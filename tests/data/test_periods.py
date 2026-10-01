"""周线/月线由日线聚合（Task 29）。

口径必须和同花顺/通达信一致，否则「周K」和别人的图对不上：

1. **自然周 / 自然月**分组（ISO 周一到周日；月按自然月），不是「每 5 根」「每 20 根」——
   节假日少了几天，按根数切会把上一周的最后一天并进这一周；
2. 开=该周期**第一个交易日**开盘，收=**最后一个交易日**收盘，高/低=区间极值，
   量/额=区间求和；时间戳=该周期**最后一个交易日**（通达信/同花顺的周K都标在周末那天）；
3. **当周/当月没走完也出一根**（用户 2026-10-01 选定）：不画的话盯盘时本周凭空消失；
4. 派生周期**不是分钟周期**：F5 的范围规则（分钟周期只跑自选池）不能把周/月也圈进去。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from chanlun.data import periods

COLS = ["ts", "open", "high", "low", "close", "volume", "amount"]

# 2026-09-28(一) 09-29(二) 09-30(三) 是国庆前最后三个交易日，第 40 周到此为止；
# 10-08(四) 10-09(五) 属第 41 周，而且是**没走完**的一周。
DAY = pd.DataFrame(
    [
        ("2026-09-28", 10.0, 11.0, 9.0, 10.5, 100.0, 1000.0),
        ("2026-09-29", 10.5, 12.0, 10.4, 11.5, 200.0, 2000.0),
        ("2026-09-30", 11.5, 11.8, 11.0, 11.2, 300.0, 3000.0),
        ("2026-10-08", 11.2, 13.0, 11.1, 12.8, 400.0, 4000.0),
        ("2026-10-09", 12.8, 12.9, 12.0, 12.1, 500.0, 5000.0),
    ],
    columns=COLS,
)


def test_week_bars_take_first_open_last_close_and_range_extremes():
    w = periods.aggregate(DAY, "week")
    assert list(w["ts"]) == ["2026-09-30", "2026-10-09"], "周K的时间戳要落在该周最后一个交易日"
    first, second = w.iloc[0], w.iloc[1]
    assert (first.open, first.high, first.low, first.close) == (10.0, 12.0, 9.0, 11.2)
    assert (first.volume, first.amount) == (600.0, 6000.0)
    assert (second.open, second.high, second.low, second.close) == (11.2, 13.0, 11.1, 12.1)
    assert (second.volume, second.amount) == (900.0, 9000.0)


def test_month_bars_group_by_calendar_month():
    m = periods.aggregate(DAY, "month")
    assert list(m["ts"]) == ["2026-09-30", "2026-10-09"]
    sept, octo = m.iloc[0], m.iloc[1]
    assert (sept.open, sept.high, sept.low, sept.close) == (10.0, 12.0, 9.0, 11.2)
    assert (octo.open, octo.high, octo.low, octo.close) == (11.2, 13.0, 11.1, 12.1)
    assert (octo.volume, octo.amount) == (900.0, 9000.0)


def test_partial_week_is_emitted_not_dropped():
    """第 41 周只有周四周五两天，照样出一根（用户选的「未走完也出一根」）。"""
    w = periods.aggregate(DAY, "week")
    assert len(w) == 2, "当周没走完也要出现在图上"
    assert str(w["ts"].iloc[-1]) == "2026-10-09"


def test_aggregate_keeps_the_frame_contract():
    for period in ("week", "month"):
        out = periods.aggregate(DAY, period)
        assert list(out.columns) == COLS, f"{period}: 列序/列名必须和 BarFrame 一致"
        assert out["ts"].is_monotonic_increasing and out["ts"].is_unique
        assert all(out[c].dtype == "float64" for c in COLS if c != "ts")


def test_aggregate_of_empty_frame_stays_empty():
    empty = periods.aggregate(pd.DataFrame(columns=COLS), "week")
    assert len(empty) == 0
    assert list(empty.columns) == COLS


def test_derived_periods_are_not_minute_periods():
    """周/月是**派生**周期，不能落进「分钟周期只跑自选池」那条规则里。"""
    assert periods.is_derived("week") and periods.is_derived("month")
    for p in ("day", "60", "30", "15", "5"):
        assert not periods.is_derived(p), f"{p} 不是派生周期"
    assert periods.base_period("week") == "day"
    assert periods.base_period("month") == "day"
    assert periods.base_period("30") == "30"


def test_aggregate_rejects_a_non_derived_period():
    with pytest.raises(ValueError):
        periods.aggregate(DAY, "day")


def test_period_end_is_the_sunday_or_the_month_end():
    assert periods.period_end(dt.date(2026, 9, 28), "week") == dt.date(2026, 10, 4)
    assert periods.period_end(dt.date(2026, 9, 30), "month") == dt.date(2026, 9, 30)
    assert periods.period_end(dt.date(2026, 2, 10), "month") == dt.date(2026, 2, 28)


class _Cal:
    """只认交易日的假日历：2026-09-28/29/30 与 10-08/09 是交易日。"""

    _DAYS = {"2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"}

    def last_trading_day(self, d):
        cur = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
        for _ in range(40):
            if cur.isoformat() in self._DAYS:
                return cur
            cur -= dt.timedelta(days=1)
        raise LookupError(d)


def test_is_complete_tells_a_finished_week_from_a_running_one():
    """第 40 周周四/周五放假，所以 09-30 就是那一周的最后交易日 → 已走完。"""
    cal = _Cal()
    assert periods.is_complete("2026-09-30", "week", cal) is True
    assert periods.is_complete("2026-10-08", "week", cal) is False, "10-09 还没到，这一周没走完"
    assert periods.is_complete("2026-09-30", "month", cal) is True, "9 月最后交易日就是 09-30"
    assert periods.is_complete("2026-09-29", "month", cal) is False

