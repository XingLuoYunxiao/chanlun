"""测量：级别递归的 point-in-time 语义（D-51 的已知弱点 3）。

要回答的问题
------------
递归出来的高层结构，**有没有偷看未来**？`find_pivots` / `classify_trends` 都是
纯函数，但纯函数不等于因果：一个结构的 `confirmed_at` 如果早于它真正能成立
的时刻，回测就会在历史上"提前"看到它。

做法是把「只喂前缀」与「喂全量」对比：

- `full` = `build_levels(engine.full(所有 bar).segments, bars=所有)`
- 对每个检查点 `i`：`pre` = `build_levels(engine.full(bars[:i+1]).segments, bars[:i+1])`

两个方向各测一件事，它们的含义完全不同：

1. **不得凭空造（forward）**：`pre` 里任何 `CONFIRMED` 的级别中枢，必须能在
   `full` 的同一级别里按 `(src_start, src_end)` 找到、且 `ZD/ZG` 相同。
   违反 ⇒ 前缀上看到了全量里不存在的东西（划分不收敛）。
2. **不得偷看未来（backward）**：`full` 里 `confirmed_at <= ts[i]` 的级别中枢，
   必须已经出现在 `pre` 里。违反 ⇒ 结构自称在 `ts[i]` 就确认了，但当时的
   数据还算不出它 ⇒ **未来函数**。

第 2 条是本次真正要量的那条。第 1 条是它的对照：若第 1 条也大面积失败，
说明失败来自线段层的不收敛（D-40 的残留），而不是递归层新增的前视 ——
两个数字要一起看才有意义。

跑法（本机）：
    cd <项目根>
    PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion_pit.py \
        --period 5 --checkpoints 12

判据（可失败）：退出码 1 = 第 2 条出现违反，并把前几条 violating 明细打出来。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chanlun.chan.engine import ChanEngine
from chanlun.chan.recursion import build_levels
from chanlun.chan.types import Status

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DATA = os.path.join(ROOT, "data")


def _key(p) -> tuple[str, int, int]:
    return (p.level, p.src_start, p.src_end)


def _files(period: str, sample: int | None) -> list[str]:
    out: list[str] = []
    for market in ("sh", "sz"):
        out += sorted(glob.glob(os.path.join(DATA, period, market, "*.parquet")))
    return out[:sample] if sample else out


def _levels_for(bars: pd.DataFrame, tag: str, base_level: str) -> list:
    snap = ChanEngine(tag, period=base_level).full(bars)
    return build_levels(snap.segments, bars=bars, base_level=base_level)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="5")
    ap.add_argument("--sample", type=int, default=3)
    ap.add_argument("--checkpoints", type=int, default=12)
    ap.add_argument("--min-prefix", type=int, default=2000)
    args = ap.parse_args()

    files = _files(args.period, args.sample)
    if not files:
        print(f"没有 {args.period} 数据")
        return 1

    total_fwd = total_bwd = 0
    bad_fwd = bad_bwd = 0
    samples: list[str] = []
    first_backward_violation: list[str] = []

    for path in files:
        tag = f"{os.path.basename(os.path.dirname(path))}.{os.path.basename(path)[:-8]}"
        bars = pd.read_parquet(path)
        full = _levels_for(bars, tag, args.period)
        full_by_level = {
            lv.level: {_key(p): p for p in lv.pivots if p.status is Status.CONFIRMED}
            for lv in full
        }
        n = len(bars)
        lo = args.min_prefix
        if n <= lo + 10:
            continue
        step = max(1, (n - lo) // args.checkpoints)
        for i in range(lo, n, step):
            prefix = bars.iloc[: i + 1].reset_index(drop=True)
            ts = str(prefix["ts"].iloc[-1])
            pre = _levels_for(prefix, tag, args.period)
            for lv in pre:
                pre_conf = [p for p in lv.pivots if p.status is Status.CONFIRMED]
                ref = full_by_level.get(lv.level, {})
                for p in pre_conf:
                    total_fwd += 1
                    q = ref.get(_key(p))
                    if q is None or abs(q.zd - p.zd) > 1e-9 or abs(q.zg - p.zg) > 1e-9:
                        bad_fwd += 1
                        if len(samples) < 5:
                            samples.append(
                                f"[forward] {tag} @{ts} {lv.level} "
                                f"bars[{p.src_start},{p.src_end}] ZD/ZG={p.zd}/{p.zg} "
                                f"全量里{'没有' if q is None else f'{q.zd}/{q.zg}'}"
                            )
            # backward：全量里「到 ts 为止已经确认」的，前缀必须已经有
            for lv in full:
                ref = {_key(p): p for p in lv.pivots if p.status is Status.CONFIRMED}
                pre_keys = {
                    _key(p) for q in pre if q.level == lv.level
                    for p in q.pivots if p.status is Status.CONFIRMED
                }
                for k, p in ref.items():
                    if p.confirmed_at is not None and p.confirmed_at <= ts:
                        total_bwd += 1
                        if k not in pre_keys:
                            bad_bwd += 1
                            if len(first_backward_violation) < 8:
                                first_backward_violation.append(
                                    f"[backward] {tag} @前缀到 {ts} 时，"
                                    f"{lv.level} 中枢 bars[{p.src_start},{p.src_end}] "
                                    f"自称 confirmed_at={p.confirmed_at}，但前缀上没有它"
                                )

    def rate(bad: int, tot: int) -> str:
        return f"{bad}/{tot}" + (f" ({bad / tot:.1%})" if tot else "")

    print("=== 递归结构 point-in-time ===")
    print(f"  1) 不得凭空造（forward）：违反 {rate(bad_fwd, total_fwd)}")
    print(f"  2) 不得偷看未来（backward）：违反 {rate(bad_bwd, total_bwd)}")
    for s in samples[:5]:
        print("     " + s)
    if first_backward_violation:
        print("  --- backward 明细（前几条）---")
        for s in first_backward_violation:
            print("     " + s)
    if total_fwd == 0 or total_bwd == 0:
        print("  ⚠ 样本量为 0，这个测量什么都没测到")
        return 1
    return 1 if bad_bwd else 0


if __name__ == "__main__":
    raise SystemExit(main())
