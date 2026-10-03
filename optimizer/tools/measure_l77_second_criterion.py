"""只读审计：第 77 课对「笔」给了**两条**判据，代码只实现了第一条。

第 77 课《一些概念的再分辨》（`chanlun108/原文/077-一些概念的再分辨.md:59`）逐字：

    笔，必须是一顶一底，而且顶和底之间至少有一个K线不属于顶分型与底分型。
    当然，还有一个最显然的，就是在同一笔中，顶分型中最高那K线的区间至少要有一部分
    高于底分型中最低那K线的区间，如果这条都不满足，也就是顶都在低的范围内或顶比底
    还低，这显然是不可接受的。

第 69 课把同两条并称为「只要是两条」（`chanlun108/原文/069-月线分段与上海大走势分析、预判.md:19`）：

    这里，只要是两条：一、顶和底之间没有至少一K线；二、不满足顶必须接着底、
    或底必须接着顶。

- **第一条**（顶底间隔）由 `stroke.py:19 MIN_GAP = 4` + `stroke.py:44` 实现；
- **第二条**（顶底价格区间序）在 `stroke.py` 全文**没有任何检查**：
  `stroke.py:74-75` 的 `high=max(a.price, b.price)` / `low=min(a.price, b.price)`
  是无条件的。本脚本量它。

`Fractal.high` / `Fractal.low`（`chan/types.py:53-54`）是分型**中间那根合并 K 线自己**
的高/低，所以第二条要的是**区间**比较，不是 `price`（`types.py:55`，顶=high、底=low）
的点比较。

## 三个量

原文自己列举了两种「不可接受」的情形，逐字取并：

- `within`（含于） = `top.low >= bot.low and top.high <= bot.high` —— 「**顶都在低的范围内**」
- `below`（低于）  = `top.high < bot.low`                            —— 「**顶比底还低**」
- **严格判定量**   = `within or below`

另有两个对照量：

- **必要条件**：向上笔终点必须高于起点（`top.high > bot.low`）。它违反 ⇒ 笔的方向都错了，
  比第二条严重得多。**它必须是 0**，否则本脚本的判定量口径就有问题。
- **参考量** `top.high <= bot.high`：把「区间至少要有一部分高于底的区间」读成
  `top.high > bot.high` 时命中的集合（含合法情形，故是**上界**）。

两种读法（3231 vs 3280）都远大于 0，不影响「第二条未实现」这个结论；
**采纳哪种读法属于人类裁决项**。

## 期望输出（2026-10-03 全市场实测，5440 只票的 `data/day`）

    扫描 5426 只票 / 592824 笔
      严格判定量（顶⊆底 或 顶<底） : 3231  (0.545%)
      参考量 top.high<=bot.high     : 3280  (0.553%)
      必要条件违反                  : 0
      命中标的数                    : 2357 / 5426  (43.4%)
      方向分布                      : 向上笔 3070 / 向下笔 161
      2018 起子集                   : 3058 / 566246  (0.540%)

`2018 起` 的比率与全样本几乎相同 ⇒ 这**不是** 1990 年代老数据的假象。
命中 **43.4%** 的标的。方向 95% 是向上笔，有几何解释：向上笔里底分型在前、顶分型在后，
底那根常是恐慌大实体，顶那根小实体嵌在它区间内；向下笔则要求底（通常最大的那根）
嵌进前面顶的区间，罕见。

脚本**只读**，不修改任何主干文件。

    cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_l77_second_criterion.py [--limit N]
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[2]
except NameError:  # pragma: no cover - `python -c` 分支
    ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.types import FractalKind  # noqa: E402
from chanlun.data import store  # noqa: E402

RECENT_FROM = "2018"
MIN_BARS = 30
MAX_EXAMPLES = 3


def main(argv: list[str]) -> int:
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])

    store.DATA_ROOT = ROOT / "data"
    paths = sorted((ROOT / "data" / "day").glob("*/*.parquet"))
    if limit is not None:
        paths = paths[:limit]
    print(f"票池：{len(paths)} 只" + (f"（--limit {limit}）" if limit else ""))

    n_tickers = n_strokes = 0
    n_within = n_below = n_ref = n_necessary_violation = 0
    n_recent = n_recent_bad = 0
    tickers_bad: set[str] = set()
    strict_by_year: Counter[str] = Counter()
    by_dir: Counter[int] = Counter()
    examples: list[str] = []

    for p in paths:
        code = f"{p.parent.name}.{p.stem}"
        try:
            bars = store.read(code, "day")
        except Exception:  # noqa: BLE001 - 缺数据/坏文件不该中断全市场扫描
            continue
        if bars is None or len(bars) < MIN_BARS:
            continue
        n_tickers += 1
        for s in ChanEngine(code=code, period="day").full(bars).strokes:
            n_strokes += 1
            # 顶 = 向上笔的终点 / 向下笔的起点
            top, bot = (s.start, s.end) if s.direction == -1 else (s.end, s.start)
            if top.kind is not FractalKind.TOP or bot.kind is not FractalKind.BOTTOM:
                continue

            within = top.low >= bot.low and top.high <= bot.high
            below = top.high < bot.low
            if top.high <= bot.high:
                n_ref += 1
            if not (top.high > bot.low):
                n_necessary_violation += 1

            yr = str(s.end.ts)[:4]
            if within:
                n_within += 1
            if below:
                n_below += 1
            if within or below:
                strict_by_year[yr] += 1
                tickers_bad.add(code)
                by_dir[s.direction] += 1
                if len(examples) < MAX_EXAMPLES:
                    examples.append(
                        f"{code} 笔{s.idx} dir={s.direction} "
                        f"顶[{top.low:.4f},{top.high:.4f}] "
                        f"底[{bot.low:.4f},{bot.high:.4f}] "
                        f"{s.start.ts}->{s.end.ts}"
                    )
            if yr >= RECENT_FROM:
                n_recent += 1
                if within or below:
                    n_recent_bad += 1

    strict_total = n_within + n_below

    def pct(num: int, den: int) -> str:
        return f"{num / den * 100:.3f}%" if den else "n/a"

    print()
    print(f"扫描 {n_tickers} 只票 / {n_strokes} 笔")
    print(f"  严格判定量（顶⊆底 或 顶<底） : {strict_total}  ({pct(strict_total, n_strokes)})")
    print(f"    ├─ 含于（顶都在低的范围内） : {n_within}")
    print(f"    └─ 低于（顶比底还低）       : {n_below}")
    print(f"  参考量 top.high<=bot.high     : {n_ref}  ({pct(n_ref, n_strokes)})")
    print(f"  必要条件违反（向上笔不向上）  : {n_necessary_violation}"
          "   ← 必须是 0，否则口径有问题")
    print(f"  命中标的数                    : {len(tickers_bad)} / {n_tickers}"
          f"  ({pct(len(tickers_bad), n_tickers)})")
    print(f"  方向分布 (1=向上笔,-1=向下笔) : {dict(sorted(by_dir.items()))}")
    print()
    if n_recent:
        print(f"  {RECENT_FROM} 起子集：{n_recent_bad} / {n_recent} 笔"
              f"  ({pct(n_recent_bad, n_recent)})")
    else:
        print(f"  {RECENT_FROM} 起子集：无")
    print()
    print("  按年（仅列有命中的年份）：")
    for yr in sorted(strict_by_year):
        print(f"    {yr}: {strict_by_year[yr]:6d} 例")
    print()
    for e in examples:
        print(f"  例：{e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
