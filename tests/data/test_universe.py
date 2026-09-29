"""品种表构建测试。

真实网络测试只用 `enrich=False`（跳过逐只 `query_stock_basic` 补全），
保证单测在 3 分钟内跑完；补全逻辑用 monkeypatch 的假 baostock 覆盖。
"""

from __future__ import annotations

import datetime as dt

import pytest

from chanlun.data import meta, universe
from chanlun.data.universe import Security, build_universe


@pytest.fixture()
def conn(tmp_path):
    c = meta.init(tmp_path / "meta.db")
    yield c
    c.close()


def test_universe_includes_main_boards(conn):
    secs = build_universe(enrich=False, conn=conn, refresh=True)
    codes = {s.code for s in secs}
    for c in ("600000", "000001", "300750", "688981"):
        assert c in codes
    assert len(secs) > 5000
    assert all(isinstance(s, Security) for s in secs)
    assert all(s.bs_code.startswith(("sh.", "sz.", "bj.")) for s in secs)
    assert all(s.code == s.bs_code.split(".", 1)[1] for s in secs)


def test_universe_persisted_to_meta(conn):
    secs = build_universe(enrich=False, conn=conn, refresh=True)
    rows = meta.get_universe(conn)
    assert len(rows) == len(secs)
    assert {r["code"] for r in rows} == {s.code for s in secs}
    assert {r["market"] for r in rows} <= {"sh", "sz", "bj"}


def test_universe_excludes_indices_and_funds(conn):
    secs = build_universe(enrich=False, conn=conn, refresh=True)
    bs_codes = {s.bs_code for s in secs}
    assert "sh.000001" not in bs_codes  # 上证综合指数
    assert "sz.399001" not in bs_codes  # 深证成份指数
    assert "sh.510300" not in bs_codes  # 沪深300ETF（基金）


def test_universe_cache_avoids_network(conn, monkeypatch):
    build_universe(enrich=False, conn=conn, refresh=True)

    def _boom(day):  # pragma: no cover - 命中缓存时不应被调用
        raise AssertionError("refresh=False 且有缓存时不应再查 baostock")

    monkeypatch.setattr(universe, "_query_all_stock", _boom)
    secs = build_universe(enrich=False, conn=conn)
    assert len(secs) > 5000


def test_universe_keeps_delisted_and_filters_by_type(conn, monkeypatch):
    """退市股必须保留并标记；指数/基金/可转债（type != 1）必须剔除。"""
    fake_rows = [
        ["sh.600000", "1", "浦发银行"],
        ["sh.600001", "1", "邯郸钢铁"],
        ["sh.510300", "1", "沪深300ETF"],
        ["sh.000001", "1", "上证综合指数"],
    ]

    def fake_all_stock(day):
        return list(fake_rows)

    basics = {
        "sh.600000": dict(code="sh.600000", code_name="浦发银行", ipoDate="1999-11-10",
                          outDate="", type="1", status="1"),
        "sh.600001": dict(code="sh.600001", code_name="邯郸钢铁", ipoDate="1998-01-22",
                          outDate="2009-12-29", type="1", status="0"),
        "sh.510300": dict(code="sh.510300", code_name="沪深300ETF", ipoDate="2012-05-28",
                          outDate="", type="5", status="1"),
        "sh.000001": dict(code="sh.000001", code_name="上证综合指数", ipoDate="1991-07-15",
                          outDate="", type="2", status="1"),
    }

    def fake_basic(bs_code):
        return basics.get(bs_code)

    monkeypatch.setattr(universe, "_query_all_stock", fake_all_stock)
    monkeypatch.setattr(universe, "_query_stock_basic", fake_basic)

    secs = build_universe(enrich=True, conn=conn, refresh=True)
    by_code = {s.bs_code: s for s in secs}

    assert "sh.600001" in by_code, "退市股不能被过滤掉"
    delisted = by_code["sh.600001"]
    assert delisted.delisted is True
    assert delisted.list_date == "1998-01-22"

    assert by_code["sh.600000"].delisted is False
    assert by_code["sh.600000"].list_date == "1999-11-10"
    assert by_code["sh.600000"].name == "浦发银行"

    assert "sh.510300" not in by_code, "ETF（type=5）不是股票"
    assert "sh.000001" not in by_code, "指数（type=2）不是股票"


def test_is_a_stock_prefix_rules():
    assert universe.is_a_stock("sh.600000")
    assert universe.is_a_stock("sh.688981")
    assert universe.is_a_stock("sz.000001")
    assert universe.is_a_stock("sz.300750")
    assert universe.is_a_stock("bj.830799")
    assert not universe.is_a_stock("sh.000001")   # 指数
    assert not universe.is_a_stock("sz.399001")   # 指数
    assert not universe.is_a_stock("sh.510300")   # ETF
    assert not universe.is_a_stock("sz.159915")   # ETF
    assert not universe.is_a_stock("sz.123456")   # 可转债
    assert not universe.is_a_stock("hk.00700")    # 非沪深北


def test_runaway_resultset_is_bounded(monkeypatch):
    """服务端翻页异常时 next() 可能永不返回 False，必须截断避免死循环。"""

    class Runaway:
        error_code = "0"
        error_msg = ""

        def next(self):
            return True

        def get_row_data(self):
            return ["sh.600000", "1", "浦发银行"]

    monkeypatch.setattr(universe.bs, "query_all_stock", lambda day: Runaway())
    monkeypatch.setattr(universe.bsrc, "_ensure_login", lambda: None)

    rows = universe._query_all_stock(dt.date(2026, 9, 29))
    assert len(rows) == universe.MAX_ROWS_PER_QUERY


def test_uses_calendar_to_locate_trading_day(monkeypatch):
    """年尾恰逢周末/节假日时，日历直接给出交易日，不做逐日探测。"""
    calls: list[dt.date] = []

    class _Cal:
        def last_trading_day(self, d):
            assert str(d)[:10] == "2022-12-31"
            return dt.date(2022, 12, 30)

    monkeypatch.setattr(universe, "get_calendar", lambda *a, **k: _Cal())
    monkeypatch.setattr(universe.bsrc, "_ensure_login", lambda: None)
    monkeypatch.setattr(
        universe,
        "_query_all_stock",
        lambda day: calls.append(day) or [["sh.600000", "1", "浦发银行"]],
    )

    rows = universe._all_stock_rows(dt.date(2022, 12, 31))
    assert calls == [dt.date(2022, 12, 30)]
    assert rows == [["sh.600000", "1", "浦发银行"]]


def test_walks_back_when_calendar_day_is_empty(monkeypatch):
    """日历给出的交易日若仍无数据（数据缺失），继续逐日回退。"""
    calls: list[dt.date] = []

    class _Cal:
        def last_trading_day(self, d):
            return dt.date(2022, 12, 31)

    def fake_query(day):
        calls.append(day)
        return [["sh.600000", "1", "浦发银行"]] if day == dt.date(2022, 12, 30) else []

    monkeypatch.setattr(universe, "get_calendar", lambda *a, **k: _Cal())
    monkeypatch.setattr(universe.bsrc, "_ensure_login", lambda: None)
    monkeypatch.setattr(universe, "_query_all_stock", fake_query)

    rows = universe._all_stock_rows(dt.date(2022, 12, 31))
    assert calls == [dt.date(2022, 12, 31), dt.date(2022, 12, 30)]
    assert rows == [["sh.600000", "1", "浦发银行"]]


class _Rs:
    def __init__(self, error_code="0", rows=(), fields=("code", "tradeStatus", "code_name")):
        self.error_code = error_code
        self.error_msg = "用户未登录" if error_code != "0" else ""
        self.fields = list(fields)
        self._rows = list(rows)

    def next(self):
        return bool(self._rows)

    def get_row_data(self):
        return self._rows.pop(0)


def test_query_all_stock_relogins_after_session_loss(monkeypatch):
    """实测 baostock 中途回收会话（10001001 用户未登录），必须重登重试。"""
    calls: list[str] = []

    def fake_query(day):
        calls.append(day)
        if len(calls) == 1:
            return _Rs(error_code="10001001")
        return _Rs(rows=[["sh.600000", "1", "浦发银行"]])

    monkeypatch.setattr(universe.bs, "query_all_stock", fake_query)
    monkeypatch.setattr(universe.bsrc, "_ensure_login", lambda: None)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    rows = universe._query_all_stock(dt.date(2026, 9, 29))
    assert len(calls) == 2, "第一次会话失效后应重试"
    assert rows == [["sh.600000", "1", "浦发银行"]]


def test_query_all_stock_gives_up_after_persistent_failure(monkeypatch):
    """持续失败必须抛错（CLI 层会记 error），不能静默返回空表。"""
    calls: list[str] = []
    monkeypatch.setattr(
        universe.bs, "query_all_stock",
        lambda day: calls.append(day) or _Rs(error_code="10001001"),
    )
    monkeypatch.setattr(universe.bsrc, "_ensure_login", lambda: None)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError) as exc:
        universe._query_all_stock(dt.date(2026, 9, 29))
    assert "10001001" in str(exc.value)
    assert len(calls) == universe.QUERY_RETRIES


def test_query_stock_basic_relogins_after_session_loss(monkeypatch):
    calls: list[str] = []

    def fake_query(code):
        calls.append(code)
        if len(calls) == 1:
            return _Rs(error_code="10001001")
        return _Rs(rows=[["sh.600000", "浦发银行", "1999-11-10", "", "1", "1"]],
                   fields=("code", "code_name", "ipoDate", "outDate", "type", "status"))

    monkeypatch.setattr(universe.bs, "query_stock_basic", fake_query)
    monkeypatch.setattr(universe.bsrc, "_ensure_login", lambda: None)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    info = universe._query_stock_basic("sh.600000")
    assert len(calls) == 2
    assert info["code_name"] == "浦发银行"
    assert info["type"] == "1"
