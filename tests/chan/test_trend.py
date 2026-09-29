"""走势类型：趋势与盘整。"""
import pytest

from chanlun.chan.pivot import Pivot
from chanlun.chan.trend import (
    TrendType,
    classify_trends,
    validate_trends,
)
from chanlun.chan.types import Status


def _pivot(idx, zd, zg, status=Status.CONFIRMED, confirmed_at="d1",
           start_ts=None, end_ts=None):
    return Pivot(
        idx=idx, zg=zg, zd=zd, gg=zg + 1, dd=zd - 1,
        start_idx=idx * 3, end_idx=idx * 3 + 2,
        start_ts=start_ts or f"d{idx * 3:04d}", end_ts=end_ts or f"d{idx * 3 + 2:04d}",
        level="day", status=status, confirmed_at=confirmed_at,
        src_start=idx * 10, src_end=idx * 10 + 5,
    )


def test_no_pivots_no_trends():
    assert classify_trends([]) == []


def test_single_pivot_is_consolidation():
    ts = classify_trends([_pivot(0, 10, 12)])
    assert [t.kind for t in ts] == [TrendType.CONSOLIDATION]
    assert ts[0].pivot_count == 1 and ts[0].start_idx == ts[0].end_idx == 0
    assert ts[0].status is Status.CONFIRMED and ts[0].confirmed_at == "d1"


def test_two_rising_pivots_form_uptrend():
    ts = classify_trends([_pivot(0, 10, 12), _pivot(1, 13, 15)])
    assert len(ts) == 1 and ts[0].kind is TrendType.UP
    assert ts[0].pivot_count == 2
    assert (ts[0].start_idx, ts[0].end_idx) == (0, 1)
    assert ts[0].start_ts == "d0000" and ts[0].end_ts == "d0005"


def test_two_falling_pivots_form_downtrend():
    ts = classify_trends([_pivot(0, 13, 15), _pivot(1, 10, 12)])
    assert [t.kind for t in ts] == [TrendType.DOWN]


def test_three_rising_pivots_are_one_trend():
    ts = classify_trends([_pivot(0, 10, 12), _pivot(1, 13, 15), _pivot(2, 16, 18)])
    assert len(ts) == 1 and ts[0].pivot_count == 3


def test_mixed_direction_splits_into_segments():
    """升、升、降 → 一个两中枢上涨 + 一个盘整。"""
    ts = classify_trends([_pivot(0, 10, 12), _pivot(1, 13, 15), _pivot(2, 11, 14)])
    assert [t.kind for t in ts] == [TrendType.UP, TrendType.CONSOLIDATION]
    assert ts[0].pivot_count == 2


def test_two_separate_trends():
    """升、升、降、降 → 上涨趋势 + 下跌趋势。"""
    ts = classify_trends([
        _pivot(0, 10, 12), _pivot(1, 13, 15),
        _pivot(2, 11, 14), _pivot(3, 8, 11),
    ])
    assert [t.kind for t in ts] == [TrendType.UP, TrendType.DOWN]
    assert (ts[0].pivot_count, ts[1].pivot_count) == (2, 2)


def test_tentative_pivots_are_ignored():
    ts = classify_trends([
        _pivot(0, 10, 12), _pivot(1, 13, 15),
        _pivot(2, 16, 18, status=Status.TENTATIVE, confirmed_at=None),
    ])
    assert len(ts) == 1 and ts[0].pivot_count == 2


def test_trend_is_tentative_without_confirmation_time():
    ts = classify_trends([_pivot(0, 10, 12, confirmed_at=None)])
    assert ts[0].status is Status.TENTATIVE and ts[0].confirmed_at is None


def test_trend_confirmed_at_is_the_latest_pivot():
    ts = classify_trends([
        _pivot(0, 10, 12, confirmed_at="d0009"),
        _pivot(1, 13, 15, confirmed_at="d0020"),
    ])
    assert ts[0].confirmed_at == "d0020"


def test_classify_trends_is_pure_and_deterministic():
    pivots = [_pivot(0, 10, 12), _pivot(1, 13, 15), _pivot(2, 11, 14)]
    before = list(pivots)
    assert classify_trends(pivots) == classify_trends(pivots)
    assert pivots == before


def test_trends_partition_the_confirmed_pivots_in_order():
    pivots = [_pivot(i, 10 + (i % 2) * 3, 12 + (i % 3) * 3) for i in range(6)]
    ts = classify_trends(pivots)
    assert validate_trends(ts) == []
    covered = [p.idx for t in ts for p in pivots[t.start_idx:t.end_idx + 1]]
    assert covered == [p.idx for p in pivots]


@pytest.mark.parametrize("bad", [
    dict(kind=TrendType.UP, pivot_count=1, start_idx=0, end_idx=0),
    dict(kind=TrendType.CONSOLIDATION, pivot_count=2, start_idx=0, end_idx=1),
    dict(kind=TrendType.UP, pivot_count=2, start_idx=2, end_idx=1),
])
def test_validate_trends_catches_bad_shapes(bad):
    from chanlun.chan.trend import Trend

    t = Trend(idx=0, start_ts="d0", end_ts="d1", level="day", **bad)
    assert validate_trends([t]) != []
