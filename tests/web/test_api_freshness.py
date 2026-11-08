"""`freshness` 是全项目唯一一份「要不要去补最新」的判断（D-49）。

它是**按市场选日历**的：A 股用真实交易日历，港股/美股用宽松的工作日日历
（`calendar_for`，`src/chanlun/calendar.py:111`）。所以这里的日历一律**打桩**，
不让测试结果取决于"跑测试的这一天"。
"""
from __future__ import annotations

import datetime as dt

import pytest

from chanlun.web import api as web_api


class _FakeCalendar:
    """只实现被调用到的 `last_trading_day` —— 假对象也要能一眼看出它假在哪。"""

    def __init__(self, day: dt.date) -> None:
        self.day = day

    def last_trading_day(self, _today):  # noqa: ANN001
        return self.day


@pytest.fixture()
def fake_calendar(monkeypatch):
    """`calendar_for` 一律回答"最新交易日是 2026-10-09"。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2026, 10, 9))
    )


def test_lagging_as_of_is_stale(fake_calendar):
    got = web_api.freshness("600000", "day", "2026-10-08")
    assert got["expected_as_of"] == "2026-10-09"
    assert got["stale"] is True


def test_as_of_on_the_last_trading_day_is_not_stale(fake_calendar):
    assert web_api.freshness("600000", "day", "2026-10-09")["stale"] is False


def test_minute_as_of_is_compared_by_date_only(fake_calendar):
    """实测 30 分钟线的 `as_of` 是 `"2026-09-30 15:00"`（带时间）。

    拿整串去比，它永远小于任何日期串 —— 于是每一只已经最新的票都会白发一次同步。
    """
    assert web_api.freshness("600000", "30", "2026-10-09 15:00")["stale"] is False
    assert web_api.freshness("600000", "30", "2026-10-08 15:00")["stale"] is True


def test_missing_as_of_is_not_stale(fake_calendar):
    """不知道就不猜：宁可漏补一次，也不要在没有依据时去发请求。"""
    for bad in (None, ""):
        assert web_api.freshness("600000", "day", bad)["stale"] is False
    # 但"最新交易日是哪天"照样给出来：404 页面的提示要用它说一个具体日期。
    assert web_api.freshness("600000", "day", None)["expected_as_of"] == "2026-10-09"


def test_the_calendar_is_chosen_by_the_code_market(monkeypatch):
    """日历必须按**市场**分派，不能拿一份 A 股日历套所有票。

    同一根 10-06 的 K 线，在港股算最新、在 A 股算落后 —— 这正是 2026 国庆那周的
    真实形状（A 股休市、港股开市）。
    """
    seen: list[str] = []

    def fake(code):
        seen.append(code)
        day = dt.date(2026, 10, 6) if code.startswith("hk.") else dt.date(2026, 10, 9)
        return _FakeCalendar(day)

    monkeypatch.setattr(web_api, "calendar_for", fake)
    assert web_api.freshness("hk.hsi", "day", "2026-10-06")["stale"] is False
    assert web_api.freshness("600000", "day", "2026-10-06")["stale"] is True
    assert seen == ["hk.hsi", "600000"], "日历要按传进来的代码选，不能被写死"


def test_a_period_that_can_never_be_fetched_says_so():
    """**申万 + 分钟**补不了（`sync_blocker` 的判断）：页面据此**不去发**那个必然 400 的请求。

    这里不打桩日历：只要真实日历认为最新交易日晚于 2026-09-30（永远成立），
    `stale` 就是 True，而 `syncable` 必须是 False。

    这条原先用的是「指数 + 分钟」—— 那从 2026-10-13 起**能补了**（走东财），
    所以换成真正补不了的组合（申万：腾讯/新浪/东财都没有分钟线）。
    """
    got = web_api.freshness("sw.801010", "30", "2026-09-30")
    assert got["stale"] is True
    assert got["syncable"] is False
    assert "申万" in got["sync_hint"]


def test_an_index_minute_period_is_now_syncable():
    """指数 + 分钟**反转了**：2026-10-13 起走东财，实测各 1536 根 5 分钟。

    页面据此会**给出**「同步这个周期」按钮 —— 这是对的，不是骗用户。
    """
    got = web_api.freshness("sh.000300", "5", "2026-09-30")
    assert got["stale"] is True
    assert got["syncable"] is True
    assert got["sync_hint"] == ""


def test_a_fetchable_combination_is_syncable():
    got = web_api.freshness("600000", "day", "2026-10-08")
    assert got["syncable"] is True and got["sync_hint"] == ""


# ---------------- 端点：结论必须跟着结构一起发回去 ----------------
# `client` / `cfg` 夹具来自 `tests/web/conftest.py`（4 只样本日线，末根固定 2024-12-31）。


def test_structure_carries_the_verdict(client, monkeypatch):
    """页面不许自己做日期比较：`/api/structure` 必须把结论一起给出。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2025, 1, 2))
    )
    body = client.get("/api/structure", params={"code": "600000", "period": "day"}).json()
    assert body["as_of"] == "2024-12-31", "夹具日线的末根"
    assert body["expected_as_of"] == "2025-01-02"
    assert body["stale"] is True
    assert body["syncable"] is True


def test_structure_says_fresh_when_the_file_reaches_the_last_trading_day(client, monkeypatch):
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2024, 12, 31))
    )
    body = client.get("/api/structure", params={"code": "600000", "period": "day"}).json()
    assert body["stale"] is False


def test_bars_carries_the_same_verdict(client, monkeypatch):
    """两处口径必须一致，否则「图是最新的、顶栏说不是」这种自相矛盾迟早出现。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2025, 1, 2))
    )
    body = client.get("/api/bars", params={"code": "600000", "period": "day"}).json()
    assert body["expected_as_of"] == "2025-01-02"
    assert body["stale"] is True


def test_a_404_still_tells_which_day_is_the_latest(client, monkeypatch):
    """404 时本地没有 `as_of`，但"最新交易日是哪天"照样要给 —— 提示里得有具体日期。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2025, 1, 2))
    )
    resp = client.get("/api/structure", params={"code": "600000", "period": "30"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["expected_as_of"] == "2025-01-02"
    assert body["syncable"] is True, "个股 30 分钟是可补的"
    assert body["detail"], "原有的 detail 不许丢"


def test_a_stock_one_minute_period_is_now_syncable():
    """股票 + 1 分钟**现在能补了**（pytdx），页面应当给出同步按钮。

    这条曾经断言 `syncable=False`（依据是「baostock 没有 1 分钟」）——
    2026-10-13 pytdx 实测能取到 23520 根后**反转**。两处口径同源
    （`sync_blocker` → `sources.capabilities`），所以只改这一处就够。
    """
    got = web_api.freshness("600030", "1", "2026-09-30")
    assert got["syncable"] is True
    assert got["sync_hint"] == ""
