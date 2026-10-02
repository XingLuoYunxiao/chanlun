import pytest

from chanlun.chan.segment import (
    Lesson6768Policy,
    _has_gap,
    _merge_feature,
    build_segments,
    validate_segments,
)
from chanlun.chan.types import (
    Fractal,
    FractalKind,
    Segment,
    SegmentBreak,
    Status,
    Stroke,
)


def _top(midx: int, price: float) -> Fractal:
    return Fractal(FractalKind.TOP, midx, f"t{midx}", high=price, low=price - 1,
                   price=price, src_idx=midx)


def _bottom(midx: int, price: float) -> Fractal:
    return Fractal(FractalKind.BOTTOM, midx, f"t{midx}", high=price + 1, low=price,
                   price=price, src_idx=midx)


def _stroke(idx: int, a: float, b: float, gap: int = 4) -> Stroke:
    up = b > a
    return Stroke(
        idx=idx,
        direction=1 if up else -1,
        start=_bottom(idx * gap, a) if up else _top(idx * gap, a),
        end=_top(idx * gap + gap, b) if up else _bottom(idx * gap + gap, b),
        high=max(a, b),
        low=min(a, b),
        src_start=idx * gap,
        src_end=idx * gap + gap,
        status=Status.CONFIRMED,
    )


def path(points) -> list[Stroke]:
    """转折价序列 -> 连续的笔序列（相邻笔共用端点）。"""
    return [_stroke(i, points[i], points[i + 1]) for i in range(len(points) - 1)]


# ============================ 特征序列原子操作 ============================
def test_gap_is_disjoint_ranges():
    assert _has_gap((18.0, 13.0, 0), (26.0, 20.0, 1)) is True    # 后者整体抬高 -> 向上缺口
    assert _has_gap((26.0, 20.0, 0), (19.0, 15.0, 1)) is True    # 后者整体压低 -> 向下缺口
    assert _has_gap((18.0, 13.0, 0), (24.0, 15.0, 1)) is False   # 有重合区间
    assert _has_gap((18.0, 13.0, 0), (18.0, 12.0, 1)) is False   # 边界相接不算缺口


def test_feature_merge_up_takes_max_down_takes_min():
    # (9,6) 被 (10,5) 包含
    elems = [(10.0, 5.0, 0), (9.0, 6.0, 1)]
    assert _merge_feature(elems, 1) == [(10.0, 6.0, 0)]    # 向上：取高高
    assert _merge_feature(elems, -1) == [(9.0, 5.0, 0)]    # 向下：取低低


def test_feature_merge_keeps_index_of_extreme():
    # 向上合并保留高点所在的笔，向下合并保留低点所在的笔
    elems = [(10.0, 5.0, 0), (9.0, 6.0, 1)]
    assert _merge_feature(elems, 1)[0][2] == 0     # 高点 10 来自笔 0
    assert _merge_feature(elems, -1)[0][2] == 0    # 低点 5 来自笔 0


def test_feature_merge_handles_multi_element_span():
    elems = [(10.0, 5.0, 0), (9.0, 6.0, 1), (11.0, 7.0, 2)]
    # 前两个先合并为 (10,6,0)，与 (11,7) 不再包含 -> 两个元素
    assert _merge_feature(elems, 1) == [(10.0, 6.0, 0), (11.0, 7.0, 2)]


def test_feature_merge_backtracks_when_merge_engulfs_previous_element():
    """第67课：标准特征序列必须是「经过非包含处理」的序列。

    第65课的顺序原则只向前推进，且明说「包含关系，不符合传递律」，所以合并出的新
    元素可能反过来把前一个元素包含进去。此时若就此收工，序列里仍留着包含关系，
    第62课的分型定义（第二元素高点、低点都最高）就永远判不出来。
    """
    # 向上：e2 与 e1 不包含；e3 把 e2 吞掉后，合并结果又吞掉了 e1
    elems = [(10.0, 5.0, 0), (9.0, 4.0, 1), (11.0, 3.0, 2)]
    assert _merge_feature(elems, 1) == [(11.0, 5.0, 2)]
    # 向下同理
    elems = [(10.0, 5.0, 0), (11.0, 6.0, 1), (12.0, 4.0, 2)]
    assert _merge_feature(elems, -1) == [(10.0, 4.0, 2)]


def test_feature_merge_does_not_use_transitivity():
    """第65课：「包含关系，不符合传递律」——只合并相邻元素。

    e1 与 e3 互相包含，但 e2 夹在中间且与两者都不包含，e1/e3 就都不能被并掉。
    """
    elems = [(10.0, 5.0, 0), (11.0, 6.0, 1), (10.0, 5.0, 2)]
    assert _merge_feature(elems, 1) == [(10.0, 5.0, 0), (11.0, 6.0, 1), (10.0, 5.0, 2)]


def test_feature_merge_output_has_no_adjacent_containment():
    """标准特征序列的定义性质：相邻元素之间不存在包含关系。"""
    cases = [
        [(10.0, 5.0, 0), (9.0, 4.0, 1), (11.0, 3.0, 2), (8.0, 2.0, 3), (12.0, 1.0, 4)],
        [(5.0, 10.0, 0), (6.0, 11.0, 1), (4.0, 12.0, 2), (7.0, 13.0, 3)],
        [(10.0, 5.0, 0), (11.0, 6.0, 1), (12.0, 4.0, 2), (13.0, 3.0, 3), (14.0, 2.0, 4)],
    ]
    for elems in cases:
        for direction in (1, -1):
            std = _merge_feature(elems, direction)
            for a, b in zip(std, std[1:]):
                assert not ((b[0] <= a[0] and b[1] >= a[1]) or (b[0] >= a[0] and b[1] <= a[1])), (
                    direction, std
                )


# ============================ 第一种情况：无缺口 ============================
def test_case1_no_gap_segment_ends_on_top_fractal():
    # 上-下-上-下-上-下，第二段回调的高点最高 -> 特征序列顶分型，且无缺口
    strokes = path([10, 18, 13, 24, 15, 20, 12])

    breaks = Lesson6768Policy().classify(strokes)
    assert len(breaks) == 1
    assert breaks[0].stroke_idx == 2
    assert breaks[0].reason == "case1_top_fractal"
    assert breaks[0].has_gap is False
    assert breaks[0].confirm_stroke_idx == 5

    segs = build_segments(strokes)
    assert len(segs) == 2
    assert segs[0].status is Status.CONFIRMED
    assert segs[0].direction == 1
    assert segs[0].stroke_count == 3
    assert segs[0].end_stroke_idx == 2
    assert (segs[0].high, segs[0].low) == (24.0, 10.0)
    assert segs[1].status is Status.TENTATIVE
    assert validate_segments(segs, strokes) == []


def test_case1_confirmed_at_does_not_look_ahead():
    strokes = path([10, 18, 13, 24, 15, 20, 12])
    seg = build_segments(strokes)[0]
    # 确认时刻是「判定时消费到的最后一笔」的结束时间，即 strokes[5].end.ts
    assert seg.confirmed_at == strokes[5].end.ts
    assert seg.confirmed_at != seg.end.end.ts


def test_case1_down_segment_mirrors_up():
    # 下-上-下-上-下-上，底分型成立且无缺口 -> 向下线段在 stroke 2 结束
    strokes = path([20, 12, 17, 6, 15, 10, 18])

    breaks = Lesson6768Policy().classify(strokes)
    assert len(breaks) == 1
    assert breaks[0].stroke_idx == 2
    assert breaks[0].reason == "case1_bottom_fractal"
    assert breaks[0].has_gap is False

    segs = build_segments(strokes)
    assert segs[0].status is Status.CONFIRMED
    assert segs[0].direction == -1
    assert segs[0].stroke_count == 3
    assert validate_segments(segs, strokes) == []


# ============================ 第二种情况：有缺口 ============================
def test_case2_with_gap_requires_second_feature_sequence():
    # 特征序列第 1、2 元素之间有缺口；第二特征序列（向上笔）出现底分型 -> 才确认结束
    strokes = path([10, 18, 13, 26, 20, 22, 17, 19, 18, 24])

    breaks = Lesson6768Policy().classify(strokes)
    assert len(breaks) == 1
    assert breaks[0].stroke_idx == 2
    assert breaks[0].reason == "case2_gap_confirmed"
    assert breaks[0].has_gap is True

    segs = build_segments(strokes)
    assert segs[0].status is Status.CONFIRMED
    assert segs[0].stroke_count == 3
    assert validate_segments(segs, strokes) == []


def test_case2_without_second_fractal_does_not_end_segment():
    # 同上但第二特征序列没有底分型 -> 线段不结束，整段仍是未确认
    strokes = path([10, 18, 13, 26, 20, 22, 17, 19, 15, 24])

    assert Lesson6768Policy().classify(strokes) == []
    segs = build_segments(strokes)
    assert len(segs) == 1
    assert segs[0].status is Status.TENTATIVE
    assert segs[0].stroke_count == len(strokes)


# ============================ 单段 / 策略可插拔 ============================
def test_single_up_stroke_series_is_one_segment():
    # 高点不断抬高，特征序列不构成顶分型 -> 只有一个未确认线段
    strokes = path([10, 20, 15, 25, 20, 30, 25])
    assert Lesson6768Policy().classify(strokes) == []

    segs = build_segments(strokes)
    assert len(segs) == 1
    assert segs[0].status is Status.TENTATIVE
    assert segs[0].direction == 1
    assert segs[0].stroke_count == 6


def test_min_strokes_guard_for_short_input():
    assert build_segments([]) == []
    assert len(build_segments(path([10, 20]))) == 1          # 1 笔
    assert len(build_segments(path([10, 20, 15]))) == 1      # 2 笔
    assert Lesson6768Policy().classify(path([10, 20, 15])) == []


def test_policy_is_pluggable():
    strokes = path([10, 18, 13, 24, 15, 20, 12])

    class AlwaysOne:
        name = "always_one"

        def classify(self, strokes):  # noqa: ARG002
            return []

    segs = build_segments(strokes, policy=AlwaysOne())
    assert len(segs) == 1
    assert segs[0].policy == "always_one"
    assert segs[0].status is Status.TENTATIVE

    class OneBreak:
        name = "one_break"

        def classify(self, strokes):  # noqa: ARG002
            from chanlun.chan.types import SegmentBreak
            return [SegmentBreak(stroke_idx=2, reason="test", confirm_stroke_idx=3)]

    segs2 = build_segments(strokes, policy=OneBreak())
    assert len(segs2) == 2
    assert segs2[0].policy == "one_break"
    assert segs2[0].confirmed_at == strokes[3].end.ts


def test_build_segments_does_not_hardcode_policy():
    """去掉 policy 参数后，规则必须来自可替换对象，而不是写死在 build_segments 里。"""
    strokes = path([10, 18, 13, 24, 15, 20, 12])
    default = build_segments(strokes)
    explicit = build_segments(strokes, policy=Lesson6768Policy())
    assert default == explicit


# ============================ 不变量 ============================
def test_confirmed_segments_have_odd_stroke_count_at_least_three():
    strokes = path([10, 18, 13, 24, 15, 20, 12])
    segs = build_segments(strokes)
    for seg in segs:
        if seg.status is Status.CONFIRMED:
            assert seg.stroke_count >= 3
            assert seg.stroke_count % 2 == 1


def test_segments_alternate_direction_and_are_contiguous():
    strokes = path([10, 18, 13, 24, 15, 20, 12, 22, 17, 28, 20, 19, 15])
    segs = build_segments(strokes)
    assert len(segs) >= 2
    assert validate_segments(segs, strokes) == []
    for a, b in zip(segs, segs[1:]):
        assert a.direction != b.direction
        assert a.end_stroke_idx + 1 == b.start_stroke_idx


def test_only_last_segment_is_tentative():
    strokes = path([10, 18, 13, 24, 15, 20, 12, 22, 17, 28, 20, 19, 15])
    segs = build_segments(strokes)
    assert segs[-1].status is Status.TENTATIVE
    assert all(s.status is Status.CONFIRMED for s in segs[:-1])


def test_segments_cover_all_strokes_without_gaps():
    strokes = path([10, 18, 13, 24, 15, 20, 12, 22, 17, 28, 20, 19, 15])
    segs = build_segments(strokes)
    assert segs[0].start_stroke_idx == 0
    assert segs[-1].end_stroke_idx == len(strokes) - 1
    for a, b in zip(segs, segs[1:]):
        assert a.end_stroke_idx + 1 == b.start_stroke_idx


def test_validate_segments_detects_even_stroke_count():
    from chanlun.chan.types import Segment
    strokes = path([10, 18, 13, 24])
    bad = Segment(idx=0, direction=1, start=strokes[0], end=strokes[-1],
                  high=24.0, low=10.0, start_stroke_idx=0, end_stroke_idx=2,
                  stroke_count=4, status=Status.CONFIRMED, confirmed_at="x")
    problems = validate_segments([bad], strokes)
    assert any("双数" in p or ">= 3" in p or "< 3" in p for p in problems)


def test_segment_range_matches_contained_strokes():
    strokes = path([10, 18, 13, 24, 15, 20, 12])
    seg = build_segments(strokes)[0]
    body = strokes[seg.start_stroke_idx:seg.end_stroke_idx + 1]
    assert seg.high == max(s.high for s in body)
    assert seg.low == min(s.low for s in body)
    assert seg.start is body[0] and seg.end is body[-1]


def test_build_segments_is_deterministic():
    strokes = path([10, 18, 13, 24, 15, 20, 12, 22, 17, 28, 20, 19, 15])
    assert build_segments(strokes) == build_segments(strokes)


def test_policy_protocol_is_structural():
    from chanlun.chan.segment import SegmentPolicy
    assert isinstance(Lesson6768Policy(), SegmentPolicy)


def test_first_three_overlap_is_required_for_a_new_segment():
    from chanlun.chan.segment import _first_three_overlap
    ok = path([10, 18, 13, 24, 15, 20, 12])
    assert _first_three_overlap(ok, 0) is True
    # 三笔完全分离，无重叠
    disjoint = [_stroke(0, 10, 12), _stroke(1, 12, 15), _stroke(2, 15, 18)]
    assert _first_three_overlap(disjoint, 0) is False
    assert _first_three_overlap(ok, len(ok) - 1) is False   # 不足三笔


# ==================== 线段极值在两端（结构不变量） ====================
def test_down_segment_ends_on_left_edge_extreme():
    """极值落在第一个标准特征元素上时，线段在该极值处结束。

    取自 sh.601088 的真实形态：向下段 11.62 → 8.75 之后，包含处理把
    笔1/笔3/笔5 并成标准特征元素的第一个，真正的低点 8.75 因此不在任何
    分型中间。旧实现会跳过它、跑到二十多笔之后找一个假分型。
    """
    strokes = path([11.62, 9.48, 10.33, 9.73, 10.14, 8.75, 10.27, 9.63,
                    11.50, 10.70, 13.32])
    breaks = Lesson6768Policy().classify(strokes)
    assert breaks, "应当至少划出一条线段"
    first = breaks[0]
    assert first.stroke_idx == 4, "向下段应结束在真正低点所在笔"
    assert first.reason == "case1_bottom_fractal"
    seg = build_segments(strokes)[0]
    assert seg.direction == -1
    assert (seg.start_stroke_idx, seg.end_stroke_idx) == (0, 4)
    assert seg.start.start.price == seg.high == 11.62
    assert seg.end.end.price == seg.low == 8.75


def test_single_standard_feature_element_yields_no_candidate():
    """标准特征序列只剩一个元素时无从构成分型，不得抛异常。"""
    strokes = path([11.62, 9.48, 10.33, 9.73, 10.14, 8.75])
    assert Lesson6768Policy().classify(strokes) == []


def test_confirmed_segments_are_standardized_per_lesson_78():
    """第 78 课：`high`/`low` 必须落在整段笔区间的真实极值上，且次序与方向一致。

    注意该课原文说的是**划分完之后**「把该线段标准化为最高低点都在端点」，不是划分
    判据。所以这里只要求 `high`/`low` 等于笔区间的极值，不要求 `start`/`end` 的价格
    等于极值——线段的端点是笔的端点，本来就不动。
    """
    strokes = path([10, 18, 13, 26, 20, 22, 17, 19, 18, 24, 15, 30, 22, 28])
    segs = build_segments(strokes)
    assert validate_segments(segs, strokes) == []
    for seg in segs:
        if seg.status is not Status.CONFIRMED:
            continue
        part = strokes[seg.start_stroke_idx:seg.end_stroke_idx + 1]
        assert seg.high == max(s.high for s in part)
        assert seg.low == min(s.low for s in part)
        hi_i = max(range(len(part)), key=lambda k: part[k].high)
        lo_i = min(range(len(part)), key=lambda k: part[k].low)
        assert (lo_i <= hi_i) if seg.direction == 1 else (hi_i <= lo_i)


def test_up_segment_does_not_stop_at_first_small_pullback():
    """上升段的顶必须是至今的新极值，不能被第一个小回撤误停。"""
    strokes = path([10, 18, 13, 24, 15, 20, 12, 30, 22, 28, 24])
    segs = build_segments(strokes)
    for seg in segs:
        if seg.status is Status.CONFIRMED and seg.direction == 1:
            assert seg.high == seg.end.end.price == max(
                s.high for s in strokes[seg.start_stroke_idx:seg.end_stroke_idx + 1]
            )


def _seg(idx, direction, strokes, a, b, status, confirmed_at=None):
    return Segment(
        idx=idx, direction=direction, start=strokes[a], end=strokes[b],
        high=max(s.high for s in strokes[a:b + 1]),
        low=min(s.low for s in strokes[a:b + 1]),
        start_stroke_idx=a, end_stroke_idx=b, stroke_count=b - a + 1,
        status=status, confirmed_at=confirmed_at,
    )


def test_validate_segments_flags_unstandardized_high_low():
    """`high`/`low` 与笔区间真实极值不符 -> 未按第 78 课标准化。"""
    strokes = path([10, 20, 15, 28, 18, 25])
    bad = Segment(
        idx=0, direction=1, start=strokes[0], end=strokes[4],
        high=999.0, low=1.0,
        start_stroke_idx=0, end_stroke_idx=4, stroke_count=5,
        status=Status.CONFIRMED, confirmed_at=strokes[4].end.ts,
    )
    problems = validate_segments([bad], strokes)
    assert any("未按第 78 课标准化" in p for p in problems), problems


def test_validate_segments_flags_reversed_extreme_order():
    """向上线段先见最高点、后见最低点 -> 次序与方向矛盾。

    第 78 课：经过标准化处理后「所有向上线段都是以最低点开始最高点结束」。
    """
    # 笔 0: 20->25, 笔 1: 25->22, 笔 2: 22->28, 笔 3: 28->10
    strokes = path([20, 25, 22, 28, 10])
    bad = _seg(0, 1, strokes, 0, 3, Status.CONFIRMED, strokes[3].end.ts)
    problems = validate_segments([bad], strokes)
    assert any("向上线段先见最高点" in p for p in problems), problems


def test_validate_segments_accepts_standardized_correct_order():
    """对照组：先见最低点、后见最高点的向上线段，不该报上述两条。

    （用 `_seg` 手工拼的 4 笔区间会被另外两条判据报「笔数为双数」，此处只筛掉
    第 78 课相关的两条。）
    """
    # 笔 0: 10->25, 笔 1: 25->22, 笔 2: 22->28, 笔 3: 28->20
    strokes = path([10, 25, 22, 28, 20])
    ok = _seg(0, 1, strokes, 0, 3, Status.CONFIRMED, strokes[3].end.ts)
    problems = validate_segments([ok], strokes)
    assert not [p for p in problems if "第 78 课" in p or "先见" in p], problems


def test_tentative_leading_segment_may_share_direction_with_next():
    """同向约束只作用于相邻**确认**线段。

    窗口左端的前导段与紧随其后的确认段本来就是同一条线段、只是被数据窗口截断，
    故同向是预期行为，`validate_segments` 不应报「与上一确认线段同向」。
    """
    strokes = path([10, 18, 13, 26, 20, 24, 17, 30, 22, 28, 25])
    lead = _seg(0, 1, strokes, 0, 4, Status.TENTATIVE)
    nxt = _seg(1, 1, strokes, 5, 9, Status.CONFIRMED, strokes[9].end.ts)
    assert lead.direction == nxt.direction
    assert validate_segments([lead, nxt], strokes) == []


class _HoleyPolicy(Lesson6768Policy):
    """`candidates` 被桩化，复刻真实长序列里的「可行性空洞」。

    真实数据上 `candidates(s)` 会成片为空（`_first_three_overlap` 不成立或标准特征
    序列不足 3 个元素），导致 `best(s)` 剧烈非单调：某个起点一旦选定就把后面锁死。
    随机游走造不出这种空洞（实测 40 万次随机路径 0 例），故只能用桩复刻。
    """

    #: 起点 -> 该起点唯一可用的分界终点
    TABLE = {0: 1, 1: 2, 3: 5, 6: 10}

    def candidates(self, strokes, start):        # noqa: D102
        end = self.TABLE.get(start)
        if end is None or end >= len(strokes):
            return
        yield SegmentBreak(
            stroke_idx=end, reason="stub", has_gap=False,
            confirm_stroke_idx=end, start_stroke_idx=start,
        )


class _TiePolicy(_HoleyPolicy):
    """两个起点能划出同样多的线段，用来钉「平局取更早」。"""

    TABLE = {0: 2, 3: 5}


def test_left_edge_takes_the_start_that_keeps_the_most_strokes_in_segments():
    """左端起点取「还能确认最多线段」的起点，前缀交给 TENTATIVE 前导段。

    第 67 课只说「走势可以**唯一地**划分为线段的连接」——「唯一」是对**手上的走势**
    说的；而数据文件的第一根 K 线常常只是数据商的截断点（`600180` 日线从 2021-01-04
    开始，该股 1998 年就上市了），把它当线段边界等于凭空断言一条线段从这里开始。
    原文没有规定被截断的左端怎么起段 ⇒ 原文空白项下的口径选择，取工程判据：
    让划分尽可能完整。

    桩数据（12 笔）的可达性：`candidates` 只在 {0:1, 1:2, 3:5, 6:10} 四处非空，于是
    `best(0)=1`（走 0->1 之后笔 2 接不上），而 `best(1)=3`（1->2、3->5、6->10）。
    硬从笔 0 起只能划 1 段。
    """
    strokes = path([10, 18, 13, 26, 20, 24, 17, 30, 22, 28, 25, 19, 15])
    assert len(strokes) == 12
    pol = _HoleyPolicy()
    breaks = list(pol.classify(strokes))
    assert breaks, "桩策略应当至少划出一段"
    assert breaks[0].start_stroke_idx == 1, (
        f"左端起点选了笔{breaks[0].start_stroke_idx}，"
        "而笔 1 起能划 3 段、笔 0 起只能划 1 段"
    )
    # 前缀不许丢：笔 0 必须由 TENTATIVE 前导段承接，且全部笔都要被覆盖。
    segs = build_segments(strokes, pol)
    assert segs[0].status is Status.TENTATIVE
    assert (segs[0].start_stroke_idx, segs[0].end_stroke_idx) == (0, 0)
    assert sum(s.stroke_count for s in segs) == 12


def test_left_edge_ties_break_towards_the_earlier_start():
    """计数相同时取更早的起点 —— 更早意味着更少的笔被划到划分之外。"""
    strokes = path([10, 18, 13, 26, 20, 24, 17, 30, 22, 28, 25, 19, 15])
    breaks = list(_TiePolicy().classify(strokes))
    assert breaks, "桩策略应当至少划出一段"
    assert breaks[0].start_stroke_idx == 0, (
        f"平局却选了笔{breaks[0].start_stroke_idx}，应当取更早的笔 0"
    )


def _brute_argmax_start(pol, strokes) -> int:
    """不复用 `classify` 的剪枝：每个起点都算一遍，取最大（平局取更早）。"""
    n = len(strokes)
    memo: dict[int, tuple[int, object]] = {}

    def best(s: int):
        if s + pol.min_strokes > n:
            return 0, None
        if s in memo:
            return memo[s]
        res: tuple[int, object] = (0, None)
        for br in pol.candidates(strokes, s):
            count, _ = best(br.stroke_idx + 1)
            if count + 1 > res[0]:
                res = (count + 1, br)
        memo[s] = res
        return res

    return max(range(n), key=lambda s: (best(s)[0], -s))


def test_left_edge_prune_matches_brute_force_argmax():
    """剪枝只省算，不得改变选出的起点。

    剪枝依据：`s` 之后任何起点最多划 `(n - s') // min_strokes` 条（每条至少
    `min_strokes` 笔），上界随 `s'` 单调不增；一旦当前最好成绩达到该上界，后面最多
    追平，而平局取更早。这里把「每个起点都算一遍」的结果作为对照。
    """
    strokes = path([10, 18, 13, 26, 20, 24, 17, 30, 22, 28, 25, 19, 15])
    for pol in (_HoleyPolicy(), _TiePolicy()):
        breaks = list(pol.classify(strokes))
        chosen = breaks[0].start_stroke_idx if breaks else 0
        assert chosen == _brute_argmax_start(pol, strokes), (
            f"{type(pol).__name__}: 剪枝选出笔{chosen}，"
            f"全量对照是笔{_brute_argmax_start(pol, strokes)}"
        )


def test_classify_is_deterministic_and_non_overlapping():
    strokes = path([10, 18, 13, 26, 20, 22, 17, 19, 18, 24, 15, 30, 22, 28])
    policy = Lesson6768Policy()
    first, second = policy.classify(strokes), policy.classify(strokes)
    assert first == second
    last = -1
    for br in first:
        assert br.stroke_idx > last
        last = br.stroke_idx
