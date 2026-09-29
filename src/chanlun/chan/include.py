"""包含处理（第 65 课）。

规则：
- 若后一根K线的高低区间完全包含前一根（或反之），则二者需合并。
- 合并方向由**已合并序列的最后两根**决定（`merged[-1].high > merged[-2].high` 为向上）；
  序列只有一根时，用当前原始 bar 与它比较定方向。
- 向上处理：`high = max(h1, h2)`，`low = max(l1, l2)`
- 向下处理：`high = min(h1, h2)`，`low = min(l1, l2)`
- **顺序原则**：必须「先合并第 1、2 根，再用结果与第 3 根比较」。包含关系不满足
  传递律，一次性比较全部K线会得到错误结果。
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from .types import MergedBar


def _contains(a_high: float, a_low: float, b_high: float, b_low: float) -> bool:
    """a 与 b 是否存在包含关系（含相等边界）。"""
    return (b_high <= a_high and b_low >= a_low) or (b_high >= a_high and b_low <= a_low)


def _infer_direction(prev_high: float, prev_low: float, high: float, low: float) -> int:
    """无历史趋势时的方向推断（有唯一确定性结果）。"""
    if high > prev_high:
        return 1
    if high < prev_high:
        return -1
    return 1 if low > prev_low else -1


def merge_bars(bars: pd.DataFrame | Sequence[Sequence[float]]) -> list[MergedBar]:
    """把原始K线做包含处理，返回合并K线序列。"""
    if isinstance(bars, pd.DataFrame):
        highs = [float(x) for x in bars["high"].tolist()]
        lows = [float(x) for x in bars["low"].tolist()]
        tss = [str(x) for x in bars["ts"].tolist()]
    else:
        rows = list(bars)
        highs = [float(r[1]) for r in rows]
        lows = [float(r[2]) for r in rows]
        tss = [str(r[0]) for r in rows]

    merged: list[MergedBar] = []
    for i, (high, low) in enumerate(zip(highs, lows)):
        if not merged:
            merged.append(
                MergedBar(idx=0, ts=tss[i], start_ts=tss[i], high=high, low=low,
                          direction=1, src_start=i, src_end=i)
            )
            continue

        last = merged[-1]
        if len(merged) >= 2:
            direction = 1 if merged[-1].high > merged[-2].high else -1
        else:
            direction = _infer_direction(last.high, last.low, high, low)

        if _contains(last.high, last.low, high, low):
            if direction == 1:
                new_high = max(last.high, high)
                new_low = max(last.low, low)
            else:
                new_high = min(last.high, high)
                new_low = min(last.low, low)
            merged[-1] = MergedBar(
                idx=last.idx, ts=tss[i], start_ts=last.start_ts,
                high=new_high, low=new_low, direction=direction,
                src_start=last.src_start, src_end=i,
            )
        else:
            merged.append(
                MergedBar(idx=len(merged), ts=tss[i], start_ts=tss[i],
                          high=high, low=low, direction=direction,
                          src_start=i, src_end=i)
            )

    return merged
