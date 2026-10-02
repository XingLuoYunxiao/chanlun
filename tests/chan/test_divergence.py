"""背驰判定：趋势背驰（第 37 课）与盘整背驰（第 39/60 课）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.chan.divergence import DivergenceKind, find_divergences
from chanlun.chan.engine import ChanEngine
from chanlun.chan.macd import hist_area, macd
from chanlun.chan.pivot import find_pivots
from chanlun.chan.signal import SignalKind, find_signals
from chanlun.chan.types import Fractal, FractalKind, Segment, Status, Stroke

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bars.parquet"


@pytest.fixture()
def snap():
    bars = pd.read_parquet(FIXTURE)
    return bars, ChanEngine("600000", "day", signal_fn=find_signals,
                            level="day").full(bars)


def _first_points(signals) -> set[tuple[str, float]]:
    """第一类买卖点的 `(ts, price)` 集合。"""
    return {(s.ts, round(s.price, 6)) for s in signals
            if s.kind in (SignalKind.B1, SignalKind.S1)}


def _trend_points(divs) -> set[tuple[str, float]]:
    """趋势背驰的 `(ts, price)` 集合。"""
    return {(d.ts, round(d.price, 6)) for d in divs
            if d.kind is DivergenceKind.TREND}


def test_trend_divergence_matches_b1_s1(snap):
    """趋势背驰必须与第一类买卖点**一一对应** —— 两份实现不能各自漂移。

    `divergence.py::_leaving_legs` 与 `signal.py::_entering_and_leaving` 是
    **有意保留的两份实现**（`signal.py` import 本模块，反向 import 会成环），
    这条测试就是那份重复的守卫。

    断言**双向相等**，不是单向包含：两者的判据逐条相同（同样的走势分组、同样的
    离开段口径、同样的创新极值要求、同样的严格面积收缩），所以集合必须完全一致。
    只写 `firsts <= trend` 会漏掉「`_trend` 用了更松的力度阈值、多报了一批趋势
    背驰」这种漂移 —— 多出来的那些恰恰是错的。实测 397 只票 19 个趋势背驰，
    两个方向的差集都是 0；夹具上两者同为 0（故另有合成用例兜底，见文件末尾）。
    """
    bars, s = snap
    divs = find_divergences(bars, s.segments, s.pivots, "day")
    trend = _trend_points(divs)
    firsts = _first_points(s.signals)
    assert trend == firsts, (
        f"第一类买卖点里这些没有对应的趋势背驰：{firsts - trend}；"
        f"趋势背驰里这些没有对应的第一类买卖点：{trend - firsts}"
    )


def test_consolidation_divergence_area_shrinks(snap):
    """盘整背驰的判据就是同向两段的同色面积收缩（第 39/60 课）。"""
    bars, s = snap
    macd_df = macd(bars["close"])
    segs = list(s.segments)
    divs = [d for d in find_divergences(bars, s.segments, s.pivots, "day")
            if d.kind is DivergenceKind.CONSOLIDATION]
    assert divs, "夹具上没有任何盘整背驰，本测试没有在测量任何东西"
    for d in divs:
        now, prev = segs[d.seg_idx], segs[d.ref_seg_idx]
        assert prev.direction == now.direction, "盘整背驰的两段必须同向"
        assert d.ref_seg_idx == d.seg_idx - 2, \
            "第 39 课：比较对象固定隔一段（Ai 与 Ai+2），不是相邻两段"
        assert d.area_now < d.area_prev
        assert d.area_now == pytest.approx(hist_area(macd_df, now.src_start, now.src_end,
                                                    now.direction))
        assert d.direction == now.direction
        assert d.ts == now.end.end.ts


def test_consolidation_divergence_does_not_require_a_new_extreme(snap):
    """第 39 课的判据只有「力度收缩」一条：盘整背驰**不需要**创新极值。

    `new_extreme` 只是记下来（第 27 课「大级别里，如果不出现新低」那条入口要用），
    不是触发条件 —— 夹具上就有「未创新低/新高」的盘整背驰。给盘整背驰加一条
    创新极值要求就会炸掉这条，而那正是把趋势背驰的口径偷偷搬到盘整背驰上。
    """
    bars, s = snap
    consol = [d for d in find_divergences(bars, s.segments, s.pivots, "day")
              if d.kind is DivergenceKind.CONSOLIDATION]
    # 两个方向都要有「未创新极值」的样本：只断言 `any(...)` 的话，给其中一个方向
    # 单独加一条创新极值要求仍然测不出来（夹具恰好两个方向都有这种样本，故按方向查）。
    no_extreme = {d.direction for d in consol if not d.new_extreme}
    assert no_extreme == {-1, 1}, \
        f"上下两个方向都必须存在「未创新极值」的盘整背驰样本，实际只有 {no_extreme}"


def test_divergence_ids_and_sorting_are_stable(snap):
    bars, s = snap
    a = find_divergences(bars, s.segments, s.pivots, "day")
    b = find_divergences(bars, s.segments, s.pivots, "day")
    assert a == b, "纯函数：同样的输入必须逐项相同"
    assert [d.idx for d in a] == list(range(len(a)))
    assert [d.ts for d in a] == sorted(d.ts for d in a)


def test_divergence_status_inherits_segment(snap):
    bars, s = snap
    segs = list(s.segments)
    for d in find_divergences(bars, s.segments, s.pivots, "day"):
        seg = segs[d.seg_idx]
        if seg.status is Status.CONFIRMED and seg.confirmed_at is not None:
            assert d.status is Status.CONFIRMED
            assert d.confirmed_at == seg.confirmed_at
        else:
            assert d.status is Status.TENTATIVE


def test_consolidation_is_not_called_a_divergence(snap):
    """第 15 课：「在盘整中是无所谓“背驰”的」⇒ 盘整背驰不得被叫作「背驰」。

    这条守住的是**术语**，不是算法。有人把 CONSOLIDATION 的中文名改成「背驰」
    （去掉「盘整」二字）就会炸 —— 那正是第 15 课禁止的说法。
    """
    assert DivergenceKind.TREND.name_cn == "趋势背驰"
    assert DivergenceKind.CONSOLIDATION.name_cn == "盘整背驰"
    assert DivergenceKind.TREND is not DivergenceKind.CONSOLIDATION


def test_only_trend_divergence_requires_a_trend(snap):
    """盘整背驰可以出现在没有任何中枢的位置；趋势背驰不行（第 37 课）。"""
    bars, s = snap
    divs = find_divergences(bars, s.segments, s.pivots, "day")
    trend = [d for d in divs if d.kind is DivergenceKind.TREND]
    assert all(d.pivot_idx is not None for d in trend), \
        "趋势背驰必然挂在某个中枢上（没有趋势就没有背驰）"
    consol = [d for d in divs if d.kind is DivergenceKind.CONSOLIDATION]
    assert any(d.pivot_idx is None for d in consol) or consol == [], \
        "盘整背驰不应被强制要求落在中枢里"


# ---- 合成用例：夹具上趋势背驰为 0，上面那条一致性断言在夹具上是空转的 ----
#
# 两个依次下降的中枢 A[14, 19]（离开段 seg4，src 36..45）、B[9, 10]（离开段
# seg8，src 72..81），B 整段在 A 之下 → 第 20 课中心定理二的下跌趋势。
# 与 `test_signal.py::_b1_segments` 同构（同一组转折点、同一段号），
# 这样「两份实现是否一一对应」才真的被测量到。


def _fr(kind, ts, price, src):
    return Fractal(kind=kind, midx=src, ts=ts, high=price, low=price,
                   price=price, src_idx=src)


def _seg(i, direction, low, high, s0, s1):
    ts0, ts1 = f"d{s0:04d}", f"d{s1:04d}"
    if direction == 1:
        start = _fr(FractalKind.BOTTOM, ts0, low, s0)
        end = _fr(FractalKind.TOP, ts1, high, s1)
    else:
        start = _fr(FractalKind.TOP, ts0, high, s0)
        end = _fr(FractalKind.BOTTOM, ts1, low, s1)
    st = Stroke(idx=i, direction=direction, start=start, end=end, high=high,
                low=low, src_start=s0, src_end=s1)
    return Segment(
        idx=i, direction=direction, start=st, end=st, high=high, low=low,
        start_stroke_idx=i, end_stroke_idx=i, stroke_count=3,
        status=Status.CONFIRMED, confirmed_at=ts1,
    )


def _zigzag(points) -> list[Segment]:
    """按转折点造一串**首尾相连**的线段（第 i 段占 bar `9i … 9i+9`）。"""
    segs = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        lo, hi = (a, b) if a < b else (b, a)
        segs.append(_seg(i, 1 if b > a else -1, float(lo), float(hi), 9 * i, 9 * i + 9))
    for a, b in zip(segs, segs[1:]):
        assert a.end.end.price == b.start.start.price, "合成线段必须首尾相连（价格）"
        assert a.end.end.ts == b.start.start.ts, "合成线段必须首尾相连（时间）"
    return segs


def _macd(n: int, areas: dict[int, float]) -> pd.DataFrame:
    """假 MACD：在指定 src 位置放一根绝对值等于 `total` 的**绿柱**。

    趋势背驰的两个离开段都是向下段，力度只能量绿柱（第 24 课「向上的看红柱子，
    向下看绿柱子」），所以 `color=-1`。
    """
    hist = [0.0] * n
    for pos, total in areas.items():
        hist[pos] = -total
    return pd.DataFrame({"dif": [0.0] * n, "dea": [0.0] * n, "hist": hist})


#: 下跌趋势：A 的离开段 seg4（src 36..45，低点 9）、B 的离开段 seg8
#: （src 72..81，低点 6 < 9 → 创新低）。
_TREND_POINTS = [
    22.0, 13.0, 19.0, 14.0, 17.0,   # seg0..seg3 → 中枢 A [14, 19]
    9.0,                            # seg4 ↓ 离开 A（低点 9）
    12.0, 7.0, 10.0,                # seg5..seg7 → 中枢 B [9, 10]
    6.0,                            # seg8 ↓ 离开 B（低点 6，创新低）
    8.0, 7.5,                       # seg9..seg10 回抽
]

#: 同样的下跌趋势（A[14, 19] → B[8, 11]），但 A 的离开段砸得更深（低点 5），
#: 于是 B 的离开段低点 6 **没有**创新低 —— 这是唯一能让「创新极值」那两行
#: 单独决定结果的结构（在下跌趋势里，离开段要跌到 ZD 之下才成其为离开段，
#: 所以通常「创新低」是自动成立的；只有前一个离开段砸穿了后一个中枢的 ZD，
#: 两条判据才会分岔）。
_NO_NEW_LOW_POINTS = [
    22.0, 13.0, 19.0, 14.0,         # seg0..seg2 → 中枢 A [14, 19]
    17.0,                           # seg3 ↑ 并入 A
    5.0,                            # seg4 ↓ 离开 A（低点 5，砸穿 B 的 ZD）
    12.0, 8.0, 11.0,                # seg5..seg7 → 中枢 B [8, 11]
    6.0,                            # seg8 ↓ 离开 B（低点 6 > 5 → 未创新低）
    7.5,                            # seg9 ↑ 整段在 B 之下 → B 封闭
]


def _synthetic_trend(area_a: float, area_b: float, points=None):
    """返回 (segs, pivots, macd_df)：离开段 A 面积 `area_a`、B 面积 `area_b`。"""
    segs = _zigzag(points if points is not None else _TREND_POINTS)
    pivots = find_pivots(segs, "day")
    assert [p.end_idx for p in pivots[:2]] == [4, 8], "两个离开段必须落在 seg4 / seg8"
    return segs, pivots, _macd(100, {36: area_a, 72: area_b})


def _agree(segs, pivots, macd_df):
    trend = _trend_points(
        find_divergences(pd.DataFrame(), segs, pivots, "day", macd_df=macd_df))
    firsts = _first_points(
        find_signals(pd.DataFrame(), segs, pivots, "day", macd_df=macd_df))
    return trend, firsts


def test_synthetic_trend_divergence_matches_b1_exactly():
    """夹具上趋势背驰为 0，这条合成用例让双向一致性断言真的咬得住。"""
    trend, firsts = _agree(*_synthetic_trend(area_a=10.0, area_b=1.0))
    assert firsts, "合成用例必须真的产出一个第一类买点，否则这条测试没有在测量任何东西"
    assert trend == firsts, (
        f"第一类买卖点里这些没有对应的趋势背驰：{firsts - trend}；"
        f"趋势背驰里这些没有对应的第一类买卖点：{trend - firsts}"
    )


def test_synthetic_boundary_cases_agree_with_b1():
    """口径边界必须两边同时不报：面积相等、后段更大、创新极值缺失。

    这几条是**为了能失败**才写的。夹具上趋势背驰为 0，只比「收缩」那一种的话，
    把 `_trend` 的力度阈值放松成 `a_now < a_prev * 1.5`、或者删掉创新极值那两行，
    测试仍然全绿（放松阈值已实测全绿），双向断言就白写了。
    """
    cases = [
        (1.0, 1.0, _TREND_POINTS, "面积相等（必须严格收缩）"),
        (1.0, 1.4, _TREND_POINTS, "后段面积更大"),
        (10.0, 1.0, _NO_NEW_LOW_POINTS, "面积收缩但没有创新低"),
    ]
    for area_a, area_b, points, why in cases:
        trend, firsts = _agree(*_synthetic_trend(area_a, area_b, points))
        assert trend == firsts, f"{why}：趋势背驰 {trend} != 第一类买卖点 {firsts}"
        assert trend == set(), f"{why}：不该报趋势背驰，却报了 {trend}"
