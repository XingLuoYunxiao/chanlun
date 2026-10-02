"""三类买卖点（第 17—20 课为结构，第 24—28 课为动力学）。

三类买卖点的结构定义
--------------------
- **第一类买点**：下跌趋势中出现**背驰**，即趋势的最后一个中枢之后再创新低，
  但这一波下跌的力度（MACD 面积）比前一波同向走势小。第 24 课把「趋势背驰」
  与「盘整背驰」分开，本实现只做**趋势背驰**（至少两个中枢依次下降）。
- **第二类买点**：第一类买点之后，次级别回抽**不破**第一类买点的低点。
- **第三类买点**：向上**离开中枢**后，次级别回抽**不回到中枢区间**（低点 > ZG）。
  卖点全部对称。「离开」是**位置**：把价格带出区间的那一段是中枢组的最后一段
  `segs[p.end_idx]`，回抽是紧随其后的 `segs[p.end_idx + 1]`（第 20 课）。

可靠性约定
----------
- 只吃 `CONFIRMED` 线段与中枢；触发信号的那一段若还是窗口右端的未确认尾段，
  信号标 `TENTATIVE`（`confirmed_at=None`），回测会把它挡在外面。
- 所有力度比较只用 MACD（通达信口径，见 `macd.py`），不做「看起来像」的近似。

已知的进一步细化空间（留给优化师按原文重新推导）：
1. 第 24 课的**盘整背驰**（一个中枢前后两段比较）没有实现；
2. 第二类买卖点原文要求「次级别回抽」，本实现用**本级别下一段**近似，
   严格做法是下钻到次级别（30 分钟）去看回抽内部结构；
3. 第 27、28 课讲的第一类买卖点区间套定位（多级别联立）没有实现。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Sequence

import pandas as pd

from .macd import hist_area, macd
from .pivot import Pivot
from .segment import Segment
from .trend import TrendType, classify_trends
from .types import Status


class SignalKind(str, Enum):
    """买卖点类型。值用原文的「第 n 类买卖点」顺序。"""

    B1 = "b1"
    B2 = "b2"
    B3 = "b3"
    S1 = "s1"
    S2 = "s2"
    S3 = "s3"

    @property
    def is_buy(self) -> bool:
        return self.value.startswith("b")

    @property
    def name_cn(self) -> str:
        n = {"1": "一", "2": "二", "3": "三"}[self.value[1]]
        return f"第{n}类买点" if self.is_buy else f"第{n}类卖点"


@dataclass(frozen=True)
class Signal:
    """一个买卖点。`price` 是触发点的价格（买点取低点，卖点取高点）。"""

    idx: int
    kind: SignalKind
    ts: str
    price: float
    level: str
    pivot_idx: int | None = None
    reason: str = ""
    status: Status = Status.TENTATIVE
    confirmed_at: str | None = None
    src_start: int = 0
    src_end: int = 0

    @property
    def is_buy(self) -> bool:
        return SignalKind(self.kind).is_buy


def _area(macd_df: pd.DataFrame | None, seg: Segment) -> float | None:
    """触发段的 MACD 面积，颜色取该段自身方向（第 24 课「向上的看红柱子，
    向下看绿柱子」）：向上段 `+1` 只算红柱、向下段 `-1` 只算绿柱。"""
    if macd_df is None:
        return None
    try:
        return hist_area(macd_df, seg.src_start, seg.src_end, seg.direction)
    except ValueError:
        return None


def _dead(seg: Segment) -> bool:
    """已作废的线段不参与信号。

    下标必须按**入参列表**对齐（`Pivot.end_idx` 是入参列表的下标），所以这里
    不能在过滤后的列表上取段，只能在取到之后判废。
    """
    return seg.status is Status.INVALIDATED


def _seal(sig: Signal, seg: Segment) -> Signal:
    """触发段确认了，信号才算确认；否则标 TENTATIVE。"""
    if seg.status is Status.CONFIRMED and seg.confirmed_at is not None:
        return replace(sig, status=Status.CONFIRMED, confirmed_at=seg.confirmed_at)
    return replace(sig, status=Status.TENTATIVE, confirmed_at=None)


def _sig(kind: SignalKind, seg: Segment, level: str, pivot_idx: int | None,
         reason: str) -> Signal:
    price = seg.low if SignalKind(kind).is_buy else seg.high
    return _seal(
        Signal(
            idx=0, kind=kind, ts=seg.end.end.ts, price=price, level=level,
            pivot_idx=pivot_idx, reason=reason,
            src_start=seg.src_start, src_end=seg.src_end,
        ),
        seg,
    )


def find_signals(
    bars: pd.DataFrame,
    segments: Sequence[Segment],
    pivots: Sequence[Pivot],
    level: str = "day",
    macd_df: pd.DataFrame | None = None,
) -> list[Signal]:
    """按结构 + 力度找出全部三类买卖点，按时间排序后统一编号。

    `segments` 必须与传给 `find_pivots` 的是**同一个列表**：`Pivot.end_idx`
    是那个列表的下标，少一段都会让离开段/回试段整体错位。
    """
    segs = list(segments)
    if macd_df is None and bars is not None and len(bars) > 0:
        macd_df = macd(bars["close"])

    out: list[Signal] = []
    out.extend(_third_kind(segs, pivots, level))
    out.extend(_first_kind(segs, pivots, level, macd_df))
    out.extend(_second_kind(out, segs, level))

    out.sort(key=lambda s: (s.ts, s.kind.value))
    return [replace(s, idx=i) for i, s in enumerate(out)]


def _third_kind(segs: list[Segment], pivots: Sequence[Pivot],
                level: str) -> list[Signal]:
    """第三类买卖点：离开中枢后回抽不回中枢（第 20 课，判据是**位置**）。

    第 20 课原文：「一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个
    次级别走势类型回试，其低点不跌破ZG，则构成第三类买点」——判定标准是价格
    与中枢区间的比较，不是线段自己的方向。

    离开段 = **把价格带出中枢区间的那一段** = 中枢组的最后一段
    `segs[p.end_idx]`：它的起点还在区间里（所以按「有重叠」被并进了中枢），
    终点已经在 ZG 之上。回试段 = 紧随其后的 `segs[p.end_idx + 1]`。

    为什么不能取 `segs[p.end_idx + 1]` 当离开段：真实线段首尾相连（相邻两段
    端点价格与时间戳完全重合），中枢最后一段之后的这一段**必然是反向回抽段**
    —— 它若整段在 ZG 之上，方向必然向下。于是「离开段方向向上且低点 > ZG」
    在真实数据上恒不成立，本函数曾经在 148 只票 / 193 个中枢上产出 0 个信号，
    而在合成用例上通过，只因为那些合成线段是断开的（相邻段之间留了缺口）。
    """
    out: list[Signal] = []
    for p in pivots:
        leave_i, back_i = p.end_idx, p.end_idx + 1
        if back_i >= len(segs):
            continue
        leave, back = segs[leave_i], segs[back_i]
        if _dead(leave) or _dead(back):
            continue
        if leave.direction == 1 and leave.high > p.zg:
            if back.direction == -1 and back.low > p.zg:
                out.append(_sig(
                    SignalKind.B3, back, level, p.idx,
                    f"向上离开中枢{p.idx}(ZG={p.zg:.3f})后回抽低点 {back.low:.3f} 不回中枢",
                ))
        elif leave.direction == -1 and leave.low < p.zd:
            if back.direction == 1 and back.high < p.zd:
                out.append(_sig(
                    SignalKind.S3, back, level, p.idx,
                    f"向下离开中枢{p.idx}(ZD={p.zd:.3f})后回抽高点 {back.high:.3f} 不回中枢",
                ))
    return out


def _entering_and_leaving(segs: list[Segment], pivots: Sequence[Pivot],
                          want: int) -> list[tuple[Pivot, Segment, Segment | None]]:
    """每个中枢的「离开段」以及上一个中枢的「离开段」，用于背驰比较。

    离开段同样取**位置**口径：中枢组的最后一段 `segs[p.end_idx]`，也就是把
    价格带出中枢区间、创出新极值的那一段 —— 第 24 课比较力度的对象正是它。
    取 `segs[p.end_idx + 1]` 会把回抽段当离开段，方向必然与趋势相反，
    于是 `leave.direction != want`，第一类买卖点永远不会触发。
    """
    out = []
    for k, p in enumerate(pivots):
        leave = segs[p.end_idx]
        if _dead(leave) or leave.direction != want:
            continue
        prev = None
        if k > 0:
            cand = segs[pivots[k - 1].end_idx]
            if not _dead(cand) and cand.direction == want:
                prev = cand
        out.append((p, leave, prev))
    return out


def _first_kind(segs: list[Segment], pivots: Sequence[Pivot], level: str,
                macd_df: pd.DataFrame | None) -> list[Signal]:
    """第一类买卖点：趋势背驰（创新极值 + 力度衰竭）。"""
    out: list[Signal] = []
    trends = classify_trends(pivots, level)
    down_pivots = [p for t in trends if t.kind is TrendType.DOWN
                   for p in pivots[t.start_idx:t.end_idx + 1]]
    up_pivots = [p for t in trends if t.kind is TrendType.UP
                 for p in pivots[t.start_idx:t.end_idx + 1]]

    for want, kind, group in ((-1, SignalKind.B1, down_pivots),
                              (1, SignalKind.S1, up_pivots)):
        if len(group) < 2:
            continue
        for p, leave, prev in _entering_and_leaving(segs, group, want):
            if prev is None or macd_df is None:
                continue
            if want == -1 and not leave.low < prev.low:
                continue
            if want == 1 and not leave.high > prev.high:
                continue
            a_now, a_prev = _area(macd_df, leave), _area(macd_df, prev)
            if a_now is None or a_prev is None or not a_now < a_prev:
                continue
            where = "新低" if want == -1 else "新高"
            out.append(_sig(
                kind, leave, level, p.idx,
                f"趋势背驰：{leave.end.end.ts} 创{where} {leave.low if want == -1 else leave.high:.3f}，"
                f"MACD 面积 {a_now:.4f} < 前一同向走势 {a_prev:.4f}",
            ))
    return out


def _second_kind(existing: list[Signal], segs: list[Segment],
                 level: str) -> list[Signal]:
    """第二类买卖点：第一类买卖点之后的次级别回抽不破前极值。"""
    out: list[Signal] = []
    for first in existing:
        want_first = SignalKind(first.kind)
        if want_first not in (SignalKind.B1, SignalKind.S1):
            continue
        k = next((i for i, s in enumerate(segs)
                  if s.src_start == first.src_start and s.src_end == first.src_end), None)
        if k is None or k + 2 >= len(segs):
            continue
        bounce, back = segs[k + 1], segs[k + 2]
        if want_first is SignalKind.B1:
            if bounce.direction == 1 and back.direction == -1 and back.low > first.price:
                out.append(_sig(
                    SignalKind.B2, back, level, first.pivot_idx,
                    f"第一类买点 {first.ts} 后回抽低点 {back.low:.3f} 不破 {first.price:.3f}",
                ))
        else:
            if bounce.direction == -1 and back.direction == 1 and back.high < first.price:
                out.append(_sig(
                    SignalKind.S2, back, level, first.pivot_idx,
                    f"第一类卖点 {first.ts} 后回抽高点 {back.high:.3f} 不破 {first.price:.3f}",
                ))
    return out


def validate_signals(signals: Sequence[Signal], bars: pd.DataFrame | None = None,
                     segments: Sequence[Segment] | None = None) -> list[str]:
    """买卖点的结构不变量校验，供测试与审计脚本调用。"""
    problems: list[str] = []
    tss = set(bars["ts"]) if bars is not None else None
    for i, s in enumerate(signals):
        if i != s.idx:
            problems.append(f"signal {i}: idx 不连续({s.idx})")
        if tss is not None and s.ts not in tss:
            problems.append(f"signal {i}: 时间 {s.ts} 不是真实 bar")
        if s.status is Status.CONFIRMED and s.confirmed_at is None:
            problems.append(f"signal {i}: 已确认却没有确认时间")
        if s.status is Status.TENTATIVE and s.confirmed_at is not None:
            problems.append(f"signal {i}: 未确认却有确认时间")
        if s.confirmed_at is not None and s.confirmed_at < s.ts:
            problems.append(f"signal {i}: 确认时间早于触发时间")
        if s.price <= 0:
            problems.append(f"signal {i}: 价格非正")
        if s.src_end < s.src_start:
            problems.append(f"signal {i}: 原始 bar 跨度颠倒")
    for prev, nxt in zip(signals, signals[1:]):
        if nxt.ts < prev.ts:
            problems.append(f"signal {nxt.idx}: 时间倒序")
    return problems
