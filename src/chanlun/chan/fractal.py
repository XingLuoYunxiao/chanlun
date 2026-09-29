"""分型（第 65 课）。

在**合并后**K线上，连续三根 a、b、c：
- 顶分型：`b.high > a.high and b.high > c.high and b.low > a.low and b.low > c.low`
- 底分型：对偶（b 的 high、low 都最低）

由于已做包含处理，相邻两根不会互相包含，因此可以放心使用严格不等式；
严格不等式同时避免了「平台等高」被误判为分型。
首尾两根不可能成为分型中心。
"""

from __future__ import annotations

from collections.abc import Sequence

from .types import Fractal, FractalKind, MergedBar


def _extreme_src(mb: MergedBar, kind: FractalKind, bars_high: Sequence[float] | None,
                 bars_low: Sequence[float] | None) -> int:
    """返回分型极值所在的原始 bar 索引。

    未提供原始K线数据时退化为合并K线的极值端（`src_start`/`src_end`）。
    """
    if bars_high is None or bars_low is None:
        return mb.src_start if kind is FractalKind.BOTTOM else mb.src_end
    lo, hi = mb.src_start, mb.src_end
    if kind is FractalKind.TOP:
        best = lo
        for i in range(lo, hi + 1):
            if bars_high[i] >= bars_high[best]:
                best = i
        return best
    best = lo
    for i in range(lo, hi + 1):
        if bars_low[i] <= bars_low[best]:
            best = i
    return best


def find_fractals(
    merged: Sequence[MergedBar],
    bars: object | None = None,
) -> list[Fractal]:
    """在合并K线序列上识别全部分型。

    `bars` 可选，传入原始 BarFrame 可让 `ts`/`src_idx` 精确指向极值所在的原始K线。
    """
    bars_high: Sequence[float] | None = None
    bars_low: Sequence[float] | None = None
    if bars is not None:
        bars_high = [float(x) for x in bars["high"].tolist()]  # type: ignore[index]
        bars_low = [float(x) for x in bars["low"].tolist()]    # type: ignore[index]
        bars_ts = [str(x) for x in bars["ts"].tolist()]        # type: ignore[index]
    else:
        bars_ts = None

    out: list[Fractal] = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]

        kind: FractalKind | None = None
        if b.high > a.high and b.high > c.high and b.low > a.low and b.low > c.low:
            kind = FractalKind.TOP
        elif b.low < a.low and b.low < c.low and b.high < a.high and b.high < c.high:
            kind = FractalKind.BOTTOM
        if kind is None:
            continue

        src = _extreme_src(b, kind, bars_high, bars_low)
        ts = bars_ts[src] if bars_ts is not None and 0 <= src < len(bars_ts) else b.ts
        out.append(
            Fractal(
                kind=kind,
                midx=b.idx,
                ts=ts,
                high=b.high,
                low=b.low,
                price=b.high if kind is FractalKind.TOP else b.low,
                src_idx=src,
            )
        )
    return out
