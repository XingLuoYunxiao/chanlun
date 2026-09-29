import datetime as dt

from chanlun.calendar import Calendar, is_trading_day, last_trading_day


def _cal():
    days = {"2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"}  # 26/27 为周末
    return Calendar(days, source="test")


def test_is_trading_day_true_and_false():
    c = _cal()
    assert c.is_trading_day("2026-09-29")
    assert not c.is_trading_day("2026-09-27")


def test_is_trading_day_accepts_date_object():
    assert _cal().is_trading_day(dt.date(2026, 9, 25))


def test_last_trading_day_skips_weekend():
    c = _cal()
    assert c.last_trading_day("2026-09-27").isoformat() == "2026-09-25"
    assert c.last_trading_day("2026-09-29").isoformat() == "2026-09-29"


def test_next_trading_day_skips_weekend():
    assert _cal().next_trading_day("2026-09-25").isoformat() == "2026-09-28"


def test_trading_days_sorted_within_range():
    got = _cal().trading_days("2026-09-24", "2026-09-29")
    assert [d.isoformat() for d in got] == [
        "2026-09-24",
        "2026-09-25",
        "2026-09-28",
        "2026-09-29",
    ]


def test_module_level_helpers_use_cached_calendar():
    # 不依赖网络：只要求类型正确且幂等
    a = is_trading_day(dt.date(2026, 9, 29))
    b = is_trading_day(dt.date(2026, 9, 29))
    assert a == b
    assert isinstance(last_trading_day("2026-09-29"), dt.date)


def test_empty_calendar_reports_no_trading_days():
    c = Calendar(set())
    assert len(c) == 0
    assert not c.is_trading_day("2026-09-29")
