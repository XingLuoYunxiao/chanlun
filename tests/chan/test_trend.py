"""走势类型：趋势与盘整（第 17/20 课）。

第 20 课「走势中枢中心定理二」是趋势判据的原文出处：

    前后同级别的两个缠中说禅走势中枢，后GG〈前DD等价于下跌及其延续；后DD〉前GG
    等价于上涨及其延续。后ZG<前ZD且后GG〉=前DD，或后ZD〉前ZG且后DD=<前GG，则等价
    于形成高级别的走势中枢。

比的是**围绕中枢波动的区间 `[DD, GG]`**，不是中枢区间 `[ZD, ZG]`；两个波动区间
只要还沾着，就是「形成高级别的走势中枢」——级别扩张，**不是趋势**。同一课还有
一句把这条钉死的：「在趋势里，同级别的前后缠中说禅走势中枢是不能有任何重叠的，
这包括任何围绕走势中枢产生的任何瞬间波动之间的重叠」。
"""
import pytest

from chanlun.chan.pivot import Pivot
from chanlun.chan.trend import (
    TrendType,
    classify_trends,
    validate_trends,
)
from chanlun.chan.types import Status


def _pivot(idx, zd, zg, status=Status.CONFIRMED, confirmed_at="d1",
           start_ts=None, end_ts=None, gg=None, dd=None):
    """造一个中枢。`gg`/`dd` 默认贴住 `[zd, zg]`（围绕中枢的波动不外溢）。"""
    return Pivot(
        idx=idx, zg=zg, zd=zd, gg=zg if gg is None else gg, dd=zd if dd is None else dd,
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
    """升、升、降、降 → 上涨趋势 + 下跌趋势。

    相邻两对都要在 `[DD, GG]` 上严格分离（第 20 课定理二），所以第三个中枢的
    波动区间顶 11 必须低于第二个的底 13、第四个的顶 8 必须低于第三个的底 9。
    """
    ts = classify_trends([
        _pivot(0, 10, 12), _pivot(1, 13, 15),
        _pivot(2, 9, 11), _pivot(3, 6, 8),
    ])
    assert [t.kind for t in ts] == [TrendType.UP, TrendType.DOWN]
    assert (ts[0].pivot_count, ts[1].pivot_count) == (2, 2)


def test_monotone_pivots_with_overlapping_swings_are_not_a_trend():
    """`[ZD, ZG]` 单调抬高、但波动区间 `[DD, GG]` 仍重叠 → **不是**上涨趋势。

    这正是第 20 课定理二第三句「后ZG<前ZD且后GG〉=前DD … 则等价于形成高级别的
    走势中枢」要拦的情形：光看中枢区间是「依次上升」，可围绕中枢的波动还咬在
    一起，级别扩张了，不能算趋势。旧口径（比 `zd`/`zg` 单调）在这里会误判成上涨。
    """
    ts = classify_trends([
        _pivot(0, 10, 12, gg=20, dd=5),      # 波动区间 [5, 20]
        _pivot(1, 13, 15, gg=22, dd=18),     # [18, 22]：zg 抬高但 18 < 20
    ])
    assert [t.kind for t in ts] == [TrendType.CONSOLIDATION] * 2
    assert all(t.pivot_count == 1 for t in ts)


def test_swing_ranges_touching_at_a_point_are_not_a_trend():
    """波动区间相切（后DD == 前GG）不算分离 → 不是趋势（第 20 课用「〉」）。"""
    ts = classify_trends([
        _pivot(0, 10, 12, gg=20, dd=5),
        _pivot(1, 13, 15, gg=25, dd=20),     # 后DD == 前GG == 20
    ])
    assert [t.kind for t in ts] == [TrendType.CONSOLIDATION] * 2


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
