import pytest

from chanlun.chan.stroke import build_strokes, select_pivotal_fractals
from chanlun.chan.types import Fractal, FractalKind, Status


def top(midx: int, price: float) -> Fractal:
    return Fractal(kind=FractalKind.TOP, midx=midx, ts=f"d{midx}", high=price,
                   low=price - 1, price=price, src_idx=midx)


def bottom(midx: int, price: float) -> Fractal:
    return Fractal(kind=FractalKind.BOTTOM, midx=midx, ts=f"d{midx}", high=price + 1,
                   low=price, price=price, src_idx=midx)


# ---------------- 基本构造 ----------------
def test_alternating_extremes_make_strokes():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12), bottom(14, 7), top(18, 15)]
    s = build_strokes(fracs)
    assert len(s) == 4
    assert [x.direction for x in s] == [-1, 1, -1, 1]
    assert [(x.start.midx, x.end.midx) for x in s] == [(2, 6), (6, 10), (10, 14), (14, 18)]
    assert (s[0].high, s[0].low) == (10.0, 5.0)
    assert (s[1].high, s[1].low) == (12.0, 5.0)


def test_last_stroke_is_tentative_and_rest_confirmed():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12), bottom(14, 7)]
    s = build_strokes(fracs)
    assert [x.status for x in s] == [Status.CONFIRMED, Status.CONFIRMED, Status.TENTATIVE]


def test_single_stroke_is_tentative():
    s = build_strokes([top(2, 10), bottom(6, 5)])
    assert len(s) == 1
    assert s[0].status is Status.TENTATIVE


def test_no_strokes_when_fewer_than_two_fractals():
    assert build_strokes([]) == []
    assert build_strokes([top(2, 10)]) == []


# ---------------- 同类合并：保留更极端者 ----------------
def test_same_kind_fractals_keep_more_extreme_top():
    fracs = [top(2, 10), top(6, 13), bottom(10, 5), top(14, 16)]
    s = build_strokes(fracs)
    assert len(s) == 2
    assert s[0].start.midx == 6 and s[0].start.price == 13.0
    assert s[1].end.midx == 14 and s[1].end.price == 16.0


def test_same_kind_fractals_keep_more_extreme_bottom():
    fracs = [top(2, 10), bottom(6, 5), bottom(10, 3), top(14, 12)]
    s = build_strokes(fracs)
    assert len(s) == 2
    assert s[0].end.midx == 10 and s[0].end.price == 3.0


def test_less_extreme_same_kind_is_ignored():
    fracs = [top(2, 10), top(6, 9), bottom(10, 5), top(14, 12)]
    seq = select_pivotal_fractals(fracs)
    assert seq[0].midx == 2 and seq[0].price == 10.0


# ---------------- 间隔校验 ----------------
def test_gap_less_than_four_is_rejected():
    fracs = [top(3, 10), bottom(5, 5), top(9, 12), bottom(13, 4)]
    s = build_strokes(fracs)
    assert len(s) == 1
    assert s[0].start.midx == 9
    assert s[0].end.midx == 13


def test_gap_exactly_four_is_accepted():
    s = build_strokes([top(2, 10), bottom(6, 5)])
    assert len(s) == 1


def test_gap_three_is_rejected():
    assert build_strokes([top(2, 10), bottom(5, 5)]) == []


def test_custom_min_gap_is_respected():
    assert len(build_strokes([top(2, 10), bottom(5, 5)], min_gap=3)) == 1


# ---------------- 不变量 ----------------
def test_directions_always_alternate():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12), bottom(14, 7), top(18, 15), bottom(22, 9)]
    s = build_strokes(fracs)
    for a, b in zip(s, s[1:]):
        assert a.direction != b.direction


def test_up_stroke_starts_at_bottom_ends_at_top():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12)]
    s = build_strokes(fracs)
    up = s[1]
    assert up.direction == 1
    assert up.start.kind is FractalKind.BOTTOM
    assert up.end.kind is FractalKind.TOP
    assert up.end.price > up.start.price
    assert (up.high, up.low) == (up.end.price, up.start.price)


def test_down_stroke_starts_at_top_ends_at_bottom():
    fracs = [bottom(2, 5), top(6, 12), bottom(10, 7)]
    s = build_strokes(fracs)
    down = s[1]
    assert down.direction == -1
    assert down.start.kind is FractalKind.TOP
    assert down.end.kind is FractalKind.BOTTOM
    assert down.start.price > down.end.price


def test_high_always_greater_than_low_for_every_stroke():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12), bottom(14, 7), top(18, 15)]
    for s in build_strokes(fracs):
        assert s.high > s.low


def test_src_span_matches_endpoints():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12)]
    for s in build_strokes(fracs):
        assert s.src_start == s.start.src_idx
        assert s.src_end == s.end.src_idx


def test_idx_is_dense_and_ascending():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12), bottom(14, 7)]
    assert [x.idx for x in build_strokes(fracs)] == [0, 1, 2]


def test_confirmed_at_none_for_tentative_else_end_ts():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12)]
    s = build_strokes(fracs)
    assert s[0].confirmed_at == "d6"
    assert s[-1].confirmed_at is None


# ---------------- 确定性 ----------------
def test_build_strokes_is_pure():
    fracs = [top(2, 10), bottom(6, 5), top(10, 12), bottom(14, 7)]
    assert build_strokes(fracs) == build_strokes(fracs)


def test_streaming_prefix_is_a_prefix_of_final():
    """增量直觉：逐步喂入分型，已确认的笔不应被后续数据改写。"""
    fracs = [top(2, 10), bottom(6, 5), top(10, 12), bottom(14, 7), top(18, 15)]
    final = build_strokes(fracs)
    for n in range(2, len(fracs) + 1):
        partial = build_strokes(fracs[:n])
        for i, st in enumerate(partial[:-1]):  # 最后一笔未确认，允许变化
            assert st == final[i]
