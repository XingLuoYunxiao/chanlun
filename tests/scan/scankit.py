"""扫描 / 自选池跟踪测试共用的**合成数据**构造器。

为什么要自己造 bar
------------------
真实样本（24 只 A 股日线）在当前引擎下三类买卖点命中数为 0（Task 17 的 G1），
而「按强度排序」「数据不足过滤」「相对上次快照的变化」这些行为恰恰需要**非空**
结果才能断言。与其放宽判据去凑命中（那会掩盖真实缺陷），不如用合成 bar
明确构造出中枢 / 突破 / 背驰形态，把被测逻辑钉死在已知输入上。

两套构造器：
- `synth_bars`：三角波合成 bar，保证「笔」「线段」一定出现（腿长 >= 5 根，
  且相邻 K 线不互相包含，否则会被包含处理吞掉、fractal 数为 0）。
- `bars_from_path`：给定价格路径（每个拐点 6 根 bar），可复现出**真实引擎**
  能算出中枢的形态，用于端到端验证扫描器真的调通了引擎。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from chanlun.chan.engine import Snapshot
from chanlun.chan.pivot import Pivot
from chanlun.chan.signal import Signal, SignalKind
from chanlun.chan.types import Fractal, FractalKind, Segment, Status, Stroke

DEFAULT_START = dt.date(2024, 1, 1)

# 30 分钟周期一天的 8 根 bar（A 股：上午 2 小时 + 下午 2 小时）
SLOTS_30 = ["09:30", "10:00", "10:30", "11:00", "13:00", "13:30", "14:00", "14:30"]


def _minutes(h0: int, m0: int, h1: int, m1: int, step: int = 5) -> list[str]:
    return [f"{t // 60:02d}:{t % 60:02d}"
            for t in range(h0 * 60 + m0, h1 * 60 + m1 + 1, step)]


# 5 分钟周期一天的 48 根 bar（09:35-11:30 共 24 根 + 13:05-15:00 共 24 根）
SLOTS_5 = _minutes(9, 35, 11, 30) + _minutes(13, 5, 15, 0)


def slots(per_day: int) -> list[str]:
    if per_day == 1:
        return ["15:00"]
    if per_day == 8:
        return list(SLOTS_30)
    if per_day == 48:
        return list(SLOTS_5)
    return [f"{9 + (k * 30) // 60:02d}:{(k * 30) % 60:02d}" for k in range(per_day)]


def ts_of(i: int, *, start: dt.date = DEFAULT_START, day_step: int = 1,
          per_day: int = 1) -> str:
    """第 i 根 bar 的时间戳：`(i // per_day) * day_step` 天后的第几个 intraday 槽位。"""
    day = start + dt.timedelta(days=(i // per_day) * day_step)
    return f"{day.isoformat()} {slots(per_day)[i % per_day]}:00"


def frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "amount"])


def synth_bars(
    n: int,
    *,
    base: float = 10.0,
    amplitude: float = 1.0,
    period: int = 20,
    close: float | None = None,
    half_spread: float = 0.02,
    start: dt.date = DEFAULT_START,
    day_step: int = 1,
    per_day: int = 1,
) -> pd.DataFrame:
    """三角波 bar：`period` 根一个完整来回，腿长 `period/2 >= 5` 根。

    `half_spread` 必须远小于每根 bar 的价格步长，否则相邻 K 线互相包含，
    包含处理会把整段吞成一根、fractal 归零（这是踩过的坑）。
    """
    rows = []
    leg = max(period // 2, 1)
    for i in range(n):
        if close is not None:
            px = float(close)
        else:
            frac = (i % period) / period
            tri = 2 * frac if frac < 0.5 else 2 * (1 - frac)
            px = base + amplitude * (tri - 0.25)
        rows.append({
            "ts": ts_of(i, start=start, day_step=day_step, per_day=per_day),
            "open": px - half_spread,
            "high": px + half_spread,
            "low": px - half_spread,
            "close": px,
            "volume": 1.0,
            "amount": 1.0,
        })
    return frame(rows)


def bars_from_path(
    path: list[float],
    *,
    bars_per_stroke: int = 6,
    start: dt.date = DEFAULT_START,
    day_step: int = 1,
    per_day: int = 1,
) -> pd.DataFrame:
    """按价格路径拐点生成 bar；`bars_per_stroke >= 5` 才能形成笔（MIN_GAP=4）。"""
    rows: list[dict] = []
    i = 0
    for a, b in zip(path, path[1:]):
        step = (b - a) / bars_per_stroke
        for k in range(bars_per_stroke):
            px = a + step * (k + 1)
            amp = abs(step) * 0.25 + 0.01
            rows.append({
                "ts": ts_of(i, start=start, day_step=day_step, per_day=per_day),
                "open": px - step,
                "high": px + amp,
                "low": px - amp,
                "close": px,
                "volume": 1.0,
                "amount": 1.0,
            })
            i += 1
    return frame(rows)


# 真实引擎在这条路径上会算出 1 个已确认中枢（[8.91, 13.13]，线段 1..4）。
# 长度 264 根 > 日线要求的 250 根，配合 day_step=2 覆盖 528 个自然日。
PIVOT_PATH = [
    8.0, 10.0, 8.8, 11.5, 10.2, 13.0, 11.6, 12.2, 10.0, 11.0, 9.0, 10.5,
    9.5, 11.0, 10.0, 12.0, 11.0, 13.0, 12.0, 14.0, 13.0,
    14.5, 13.2, 15.5, 14.1, 16.2, 14.8, 15.9, 13.5, 14.2, 11.8, 12.4,
    10.2, 11.0, 9.0, 9.8, 7.8, 8.6, 6.5, 7.2, 5.2,
    7.0, 5.56, 7.36, 5.92,
]


# ---------------------------------------------------------------- 合成结构

def _fr(kind: FractalKind, ts: str, price: float, src: int) -> Fractal:
    return Fractal(kind=kind, midx=src, ts=ts, high=price, low=price,
                   price=price, src_idx=src)


def seg(i: int, direction: int, low: float, high: float, s0: int, s1: int,
        *, confirmed: bool = True, ts0: str = "", ts1: str = "") -> Segment:
    """造一条线段，原始 bar 跨度写死为 `[s0, s1]`（决定 src_start/src_end）。"""
    ts0 = ts0 or f"d{s0:04d}"
    ts1 = ts1 or f"d{s1:04d}"
    if direction == 1:
        start = _fr(FractalKind.BOTTOM, ts0, low, s0)
        end = _fr(FractalKind.TOP, ts1, high, s1)
    else:
        start = _fr(FractalKind.TOP, ts0, high, s0)
        end = _fr(FractalKind.BOTTOM, ts1, low, s1)
    st = Stroke(idx=i, direction=direction, start=start, end=end, high=high, low=low,
                src_start=s0, src_end=s1)
    return Segment(
        idx=i, direction=direction, start=st, end=st, high=high, low=low,
        start_stroke_idx=i, end_stroke_idx=i, stroke_count=3,
        status=Status.CONFIRMED if confirmed else Status.TENTATIVE,
        confirmed_at=ts1 if confirmed else None,
    )


def chain(spec, *, bars_per_seg: int = 10, last_tentative: bool = False) -> list[Segment]:
    """spec: [(direction, low, high), ...]，每段占 `bars_per_seg` 根原始 bar。"""
    out = []
    for i, (d, lo, hi) in enumerate(spec):
        s0, s1 = i * bars_per_seg, i * bars_per_seg + bars_per_seg - 1
        out.append(seg(i, d, lo, hi, s0, s1,
                       confirmed=not (last_tentative and i == len(spec) - 1)))
    return out


def pivot_of(segs: list[Segment], zd: float, zg: float, start_idx: int, end_idx: int,
             *, level: str = "day", status: Status = Status.CONFIRMED) -> Pivot:
    return Pivot(
        idx=0, zg=zg, zd=zd, gg=float(max(s.high for s in segs[start_idx:end_idx + 1])),
        dd=float(min(s.low for s in segs[start_idx:end_idx + 1])),
        start_idx=start_idx, end_idx=end_idx,
        start_ts=segs[start_idx].start.start.ts, end_ts=segs[end_idx].end.end.ts,
        level=level, status=status,
        confirmed_at=segs[end_idx].confirmed_at if status is Status.CONFIRMED else None,
        src_start=segs[start_idx].src_start, src_end=segs[end_idx].src_end,
    )


def signal_of(seg: Segment, kind: str = "b1", *, status: Status | str = Status.CONFIRMED,
              level: str = "day", pivot_idx: int | None = None,
              reason: str | None = None) -> Signal:
    k = SignalKind(kind) if isinstance(kind, str) else kind
    st = Status(status) if isinstance(status, str) else status
    return Signal(
        idx=0, kind=k, ts=seg.end.end.ts,
        price=seg.low if k.is_buy else seg.high, level=level, pivot_idx=pivot_idx,
        reason=reason or f"合成{k.name_cn}",
        status=st,
        confirmed_at=seg.confirmed_at if st is Status.CONFIRMED else None,
        src_start=seg.src_start, src_end=seg.src_end,
    )


def snapshot_of(code: str, period: str, bars: pd.DataFrame, *, segs=(), pivots=(),
                signals=(), as_of: str | None = None, level: str | None = None) -> Snapshot:
    return Snapshot(
        code=code, period=period,
        as_of=as_of or str(bars["ts"].iloc[-1]),
        level=level or period,
        segments=tuple(segs), pivots=tuple(pivots), signals=tuple(signals),
    )


def macd_frame(n: int, *, areas: dict[int, float] | None = None,
               spans: dict[tuple[int, int], float] | None = None) -> pd.DataFrame:
    """假 MACD：`areas[pos]=v` 在单点上放 v；`spans[(i0,i1)]=v` 把 v 均摊到闭区间。"""
    hist = [0.0] * n
    for pos, total in (areas or {}).items():
        hist[pos] = total
    for (i0, i1), total in (spans or {}).items():
        k = i1 - i0 + 1
        for i in range(i0, i1 + 1):
            hist[i] = total / k
    return pd.DataFrame({"dif": [0.0] * n, "dea": [0.0] * n, "hist": hist})
