"""背驰判定：趋势背驰（第 37 课）与盘整背驰（第 39、60 课）。

第 37 课《背驰的再分辨》：「没有趋势，没有背驰，不是任何a+A+b+B+c形式的都有
背驰的。……这样，是不存在背驰的，最多就是盘整背驰。」

第 15 课《没有趋势，没有背驰。》：「必须注意：没有趋势，没有背驰。**在盘整中是
无所谓“背驰”的**，这点是必须特别明确的。」

⇒ **「背驰」不带定语时专指趋势背驰**（第 15 / 37 课）；「盘整背驰」是**另一个被
单独命名的现象**，与「背驰」不构成强弱、包含或递进关系，两者**不可互相替代**。
第 37 课「最多就是盘整背驰」是两者的分界线。判据实现上两者并列（两个
`DivergenceKind`），**语义上不对等**。

第 39 课《同级别分解再研究》：「只理会一点，就是Ai与Ai+2之间是否盘整背驰」
⇒ 盘整背驰的判据 = 同向的两段 `Ai` 与 `Ai+2` 比较力度，后段收缩。
线段方向天然交替，所以 `segments[i]` 与 `segments[i+2]` **必然同向**，无需筛选。

第 60 课《图解分析示范五》：「力度比较的是下面所有红柱子的面积之和。」
⇒ 力度 = 同色柱面积之和，颜色取该段自身方向（`macd.hist_area` 的口径）。

第 60 课同时给出硬约束：「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」
⇒ 盘整背驰**不得**并入第一类买卖点，也**不得**因为它放宽第一类的判据
（第一类要求真趋势，第 15 / 37 课）。本系统另设独立信号类型 `pb` / `ps`
承载盘整背驰点（由后续任务加入 `signal.py`），本模块只产出 `Divergence`。

第 49 课：「中枢震荡中出现的类似盘整背驰的走势段，与中枢完成的向上移动出现的
背驰段是不同的，两者分别在第三类买点的前后……这是有严格区分的。」
⇒ `Divergence.in_pivot` 记录这个区分。

理论依据见 `optimizer/theory/L39-CONSOLIDATION-DIVERGENCE.md`。
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


class DivergenceKind(str, Enum):
    """背驰类型。两种类型**语义上不对等**，只是判据实现上并列。

    第 15 课把「盘整中有没有背驰」写死为没有，所以只有 `TREND` 才是第 15 课
    意义上的「背驰」；`CONSOLIDATION` 是被单独命名的类比物，中文名必须带
    「盘整」二字，不许简写成「背驰」。
    """

    TREND = "trend"
    CONSOLIDATION = "consolidation"

    @property
    def name_cn(self) -> str:
        return "趋势背驰" if self is DivergenceKind.TREND else "盘整背驰"


@dataclass(frozen=True)
class Divergence:
    """一次背驰。`seg_idx` 是**创出极值的那一段**（盘整背驰里的 `Ai+2`）。"""

    idx: int
    kind: DivergenceKind
    #: +1 = 顶背驰（上涨力度衰竭），-1 = 底背驰。
    direction: int
    ts: str
    price: float
    level: str
    seg_idx: int
    ref_seg_idx: int
    area_now: float
    area_prev: float
    #: 是否创出新的极值（第 27 课「不出现新低」的反面）。
    new_extreme: bool
    #: 该段是否落在某个中枢区间内（第 49 课的区分）。
    in_pivot: bool
    pivot_idx: int | None = None
    reason: str = ""
    status: Status = Status.TENTATIVE
    confirmed_at: str | None = None
    src_start: int = 0
    src_end: int = 0

    @property
    def ratio(self) -> float:
        return self.area_now / self.area_prev if self.area_prev > 0 else float("inf")

    @property
    def is_top(self) -> bool:
        return self.direction > 0

    @property
    def name_cn(self) -> str:
        return DivergenceKind(self.kind).name_cn


def _dead(seg: Segment) -> bool:
    return seg.status is Status.INVALIDATED


def _area(macd_df: pd.DataFrame, seg: Segment) -> float | None:
    """同色柱面积之和。`hist_area` 只认 ±1，方向取该段自身的。"""
    try:
        return hist_area(macd_df, seg.src_start, seg.src_end, seg.direction)
    except ValueError:
        return None


def _locate(pivots: Sequence[Pivot], i: int) -> tuple[bool, int | None]:
    for p in pivots:
        if p.start_idx <= i <= p.end_idx:
            return True, p.idx
    return False, None


def _index_of(segs: list[Segment], seg: Segment) -> int:
    """取线段在**入参列表**里的下标。

    不用 `list.index()`：`Segment` 是值相等的 frozen dataclass，一旦列表里出现
    等值段，`index()` 会返回第一个，`seg_idx` / `ref_seg_idx` 就指错了段。
    这里按对象身份定位 —— 调用方取到的段本来就来自 `segs` 本身。
    """
    for i, s in enumerate(segs):
        if s is seg:
            return i
    raise ValueError("线段不在入参列表里：segments 必须与传给 find_pivots 的是同一个列表")


def _seal(div: Divergence, seg: Segment) -> Divergence:
    if seg.status is Status.CONFIRMED and seg.confirmed_at is not None:
        return replace(div, status=Status.CONFIRMED, confirmed_at=seg.confirmed_at)
    return replace(div, status=Status.TENTATIVE, confirmed_at=None)


def _make(kind: DivergenceKind, seg: Segment, ref: Segment, level: str,
          pivots: Sequence[Pivot], seg_idx: int, ref_idx: int,
          a_now: float, a_prev: float, new_extreme: bool,
          reason: str) -> Divergence:
    in_pivot, pivot_idx = _locate(pivots, seg_idx)
    price = seg.low if seg.direction == -1 else seg.high
    return _seal(
        Divergence(
            idx=0, kind=kind, direction=seg.direction, ts=seg.end.end.ts,
            price=price, level=level, seg_idx=seg_idx, ref_seg_idx=ref_idx,
            area_now=a_now, area_prev=a_prev, new_extreme=new_extreme,
            in_pivot=in_pivot, pivot_idx=pivot_idx, reason=reason,
            src_start=seg.src_start, src_end=seg.src_end,
        ),
        seg,
    )


def _leaving_legs(segs: list[Segment], pivots: Sequence[Pivot],
                  want: int) -> list[tuple[Pivot, Segment, Segment | None]]:
    """每个中枢的「离开段」与上一个中枢的「离开段」。

    与 `signal.py::_entering_and_leaving`（L188-208）同构，**有意保留独立实现**：
    `signal.py` 会 import 本模块，反向 import 会成环。
    两者一致性由 `tests/chan/test_divergence.py::test_trend_divergence_matches_b1_s1`
    守住（**双向**相等，不是单向包含）。
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


def _trend(segs: list[Segment], pivots: Sequence[Pivot], level: str,
           macd_df: pd.DataFrame) -> list[Divergence]:
    """趋势背驰：两个同向中枢之间比较离开段力度（第 37 课「没有趋势，没有背驰」）。

    判据与 `signal.py::_first_kind` 的 `b1` / `s1` **逐条相同**（同样的走势分组、
    同样的离开段口径、同样的创新极值要求、同样的严格面积收缩），只是产出
    `Divergence` 而不是 `Signal` —— 两条实现必须一一对应，测试断言双向相等。
    """
    out: list[Divergence] = []
    trends = classify_trends(pivots, level)
    groups = {
        -1: [p for t in trends if t.kind is TrendType.DOWN
             for p in pivots[t.start_idx:t.end_idx + 1]],
        1: [p for t in trends if t.kind is TrendType.UP
            for p in pivots[t.start_idx:t.end_idx + 1]],
    }
    for want, group in groups.items():
        if len(group) < 2:
            continue
        for _p, leave, prev in _leaving_legs(segs, group, want):
            if prev is None:
                continue
            if want == -1 and not leave.low < prev.low:
                continue
            if want == 1 and not leave.high > prev.high:
                continue
            a_now, a_prev = _area(macd_df, leave), _area(macd_df, prev)
            if a_now is None or a_prev is None or not a_now < a_prev:
                continue
            i = _index_of(segs, leave)
            where = "新低" if want == -1 else "新高"
            out.append(_make(
                DivergenceKind.TREND, leave, prev, level, pivots, i,
                _index_of(segs, prev), a_now, a_prev, True,
                f"趋势背驰：{leave.end.end.ts} 创{where}，MACD 面积 "
                f"{a_now:.4f} < 前一同向走势 {a_prev:.4f}",
            ))
    return out


def _consolidation(segs: list[Segment], pivots: Sequence[Pivot], level: str,
                   macd_df: pd.DataFrame) -> list[Divergence]:
    """盘整背驰：同向的 `Ai` 与 `Ai+2` 比较力度（第 39 课）。

    第 39 课原文：「把a定义为A0，则Ai与Ai+2之间就可以不断地比较力度，用盘整
    背驰的方法决定买卖点。」所以比较对象固定隔一段（`segs[i]` vs `segs[i-2]`），
    不需要先有趋势、也不需要落在中枢里。

    注意这里**不要求创新极值**：第 39 课的判据只有「力度收缩」一条，
    `new_extreme` 只是记录下来（第 27 课「大级别里，如果不出现新低」的另一条
    入口要用它），不是触发条件。
    """
    out: list[Divergence] = []
    for i in range(2, len(segs)):
        now, prev = segs[i], segs[i - 2]
        if _dead(now) or _dead(prev):
            continue
        if now.direction != prev.direction:
            continue
        a_now, a_prev = _area(macd_df, now), _area(macd_df, prev)
        if a_now is None or a_prev is None or not a_now < a_prev:
            continue
        if now.direction == -1:
            new_extreme = now.low < prev.low
            where = "新低" if new_extreme else "未创新低"
        else:
            new_extreme = now.high > prev.high
            where = "新高" if new_extreme else "未创新高"
        out.append(_make(
            DivergenceKind.CONSOLIDATION, now, prev, level, pivots, i, i - 2,
            a_now, a_prev, new_extreme,
            f"盘整背驰：{now.end.end.ts} {where}，MACD 面积 {a_now:.4f} < "
            f"同向前段 {prev.end.end.ts} 的 {a_prev:.4f}",
        ))
    return out


def find_divergences(
    bars: pd.DataFrame,
    segments: Sequence[Segment],
    pivots: Sequence[Pivot],
    level: str = "day",
    macd_df: pd.DataFrame | None = None,
) -> tuple[Divergence, ...]:
    """找出全部背驰，按时间排序后统一编号。

    `segments` 必须与传给 `find_pivots` 的是**同一个列表**（`Pivot.end_idx`
    是那个列表的下标）。

    纯函数：不改入参、不持有状态，同一组输入必得同一组输出（「快照 = 纯函数
    输出」）。`macd_df` 缺省时内部按 `macd(bars["close"])` 现算。

    返回的两种类型**不是同一种东西**：`DivergenceKind.TREND` 是第 15 / 37 课
    意义上的背驰，`DivergenceKind.CONSOLIDATION` 是第 39 / 60 课的盘整背驰
    —— 后者「无所谓第一类买点」（第 60 课），调用方不得把它当成 `b1` / `s1`。
    """
    segs = list(segments)
    if macd_df is None and bars is not None and len(bars) > 0:
        macd_df = macd(bars["close"])
    if macd_df is None or not segs:
        return ()

    out = _trend(segs, pivots, level, macd_df)
    out.extend(_consolidation(segs, pivots, level, macd_df))
    out.sort(key=lambda d: (d.ts, d.kind.value))
    return tuple(replace(d, idx=i) for i, d in enumerate(out))
