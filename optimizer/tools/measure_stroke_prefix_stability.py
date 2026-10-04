"""笔序列的前缀稳定性测量：为 D-40 的「残留 14 条按已知空白收口」提供依据。

背景（详见 ARCHITECTURE.md D-40「后果 / 变更历史」）：

线段层的「已宣布的中间段在后续前缀里消失」残留 14 条，唯一的清零手段是
**跨次调用锁定**（把上一轮已宣布的分界点喂给下一轮）。锁定要成立，前提是
**被锁的对象本身在前缀下是稳定的** —— 否则锁一个下一秒就变形的下标，等于把
不确定引进结果里。

本工具量的就是这个前提，分两层：

  笔层（本工具主输出）：对每个前缀 k，重建笔序列，按
  `(src_start, src_end, direction, high, low)` 与上一前缀逐项比较，
  记下**首个失配位置** `same`（前 `same` 项逐项相同）。

  - `前缀对` = 参与比较的相邻前缀对数；
  - `失配对` = `same < m` 的对数（`m = min(上一轮笔数, 本轮笔数)`）；
  - `非末位失配对` = `same < m - 1` 的对数 —— **这一项是判据**：
    若它恒为 0，说明失配只发生在较短序列的**最后一个元素**上，即
    「已定的笔不会被后来的数据改写，只有最新那根笔会随数据增长而变形」；
    若它 > 0，说明笔层也有内部改写，锁定下标不安全。
  - `最差失配距离` = `max(m - same)`。

**这是一个能失败的指标**：把 `非末位失配对` 改成「失配对」它当然恒 > 0，
把阈值放成 `same < m` 它就量不出「只有末位」这件事。它也可能真的变红 ——
换更粗的 `--stride`、换更长的历史、换未复权的数据都可能让内部笔变形。

用法：

    cd <本项目根目录>

    # 复现 D-40 记录里的数字（6 只日线票）
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_stroke_prefix_stability.py

    # 换尺子：更密的 stride、更长/更短的窗口
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_stroke_prefix_stability.py --stride 20 --warmup 60

    # 指定票
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_stroke_prefix_stability.py --codes sh.600000,sz.300760

本工具只读：不改主干、不写仓库内文件。数字受数据落盘状态影响，
引用时必须连同 `--stride` / `--warmup` / 票列表一起写。
"""

from __future__ import annotations

import argparse
import sys

from chanlun.chan.fractal import find_fractals
from chanlun.chan.include import merge_bars
from chanlun.chan.stroke import build_strokes
from chanlun.web.api import _period_frame

DEFAULT_CODES = [
    "sh.600000",
    "sh.600030",
    "sh.600519",
    "sz.000001",
    "sz.300760",
    "sh.601012",
]
DEFAULT_STRIDE = 40
DEFAULT_WARMUP = 120


def stroke_key(s) -> tuple:
    """笔的身份：源 K 线区间 + 方向 + 高低点。

    用 `src_start`/`src_end`（源 K 线下标）而不是 `start`/`end` 分型对象：
    前者是笔在**原始数据**上的锚，后者随包含处理/分型口径变动。
    """
    return (
        s.src_start,
        s.src_end,
        s.direction,
        round(float(s.high), 6),
        round(float(s.low), 6),
    )


def first_mismatch(prev: list, cur: list) -> int:
    """前多少项逐项相同（= 首个失配位置）；全同则返回 min 长度。"""
    m = min(len(prev), len(cur))
    same = 0
    while same < m and stroke_key(prev[same]) == stroke_key(cur[same]):
        same += 1
    return same


def measure(code: str, stride: int, warmup: int) -> dict:
    full, *_ = _period_frame(code, "day", adjust=None, meta_db=None)
    prev: list | None = None
    pairs = bad = non_tail = worst = 0
    for k in range(warmup, len(full) + 1, stride):
        window = full.iloc[:k].reset_index(drop=True)
        strokes = build_strokes(find_fractals(merge_bars(window), bars=window))
        if prev is not None:
            pairs += 1
            same = first_mismatch(prev, strokes)
            m = min(len(prev), len(strokes))
            if same < m:
                bad += 1
                worst = max(worst, m - same)
                if same < m - 1:
                    non_tail += 1
        prev = strokes
    return {
        "code": code,
        "bars": len(full),
        "pairs": pairs,
        "bad": bad,
        "non_tail": non_tail,
        "worst": worst,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="笔序列前缀稳定性测量（D-40 已知空白的依据）",
        epilog=(
            "判据：`非末位失配对` 恒为 0 才说明失配只落在较短序列的最后一个元素上。"
            "该列 > 0 时，D-40 里「锁定笔下标是稳定的」这句话不成立，必须改记录。"
        ),
    )
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES), help="逗号分隔的票代码")
    parser.add_argument("--stride", type=int, default=DEFAULT_STRIDE, help=f"前缀步长（默认 {DEFAULT_STRIDE}）")
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help=f"起始前缀长度（默认 {DEFAULT_WARMUP}）")
    args = parser.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    print(f"stride={args.stride} warmup={args.warmup} 票数={len(codes)}")
    print("票代码         bars  前缀对  失配对  非末位失配对  最差失配距离")
    rows = []
    for code in codes:
        r = measure(code, args.stride, args.warmup)
        rows.append(r)
        print(
            f"{r['code']:<14}{r['bars']:>5}{r['pairs']:>7}{r['bad']:>8}"
            f"{r['non_tail']:>14}{r['worst']:>14}"
        )
    total_non_tail = sum(r["non_tail"] for r in rows)
    print()
    print(
        f"合计：前缀对 {sum(r['pairs'] for r in rows)} / 失配对 {sum(r['bad'] for r in rows)} / "
        f"非末位失配对 {total_non_tail} / 最大失配距离 {max((r['worst'] for r in rows), default=0)}"
    )
    if total_non_tail == 0:
        print("判据通过：所有失配都只落在较短序列的最后一个元素上 ⇒ 笔下标可锁定（D-40 据此记录）。")
    else:
        print("判据不通过：存在非末位的笔改写 ⇒ D-40 的「可锁定」结论作废，必须改记录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
