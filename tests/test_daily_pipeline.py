"""Task 16 每日流水线集成测试：全程离线，不联网、不碰真库。

替换掉的边界：`fetch_bars`（行情源）、`build_universe`（品种表）、`get_calendar`（交易日历）、
`load_config`（配置与数据根目录）、`quality.run_all`（跨源哨兵校验，要走 HTTP）。
通知出口走**真实现**，落到 tmp 下的 `logs/notify.log` —— 「推没推送」这件事要有实打实的
文件证据，而不是断言某个 mock 被调用过。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pandas as pd
import pytest

from chanlun import __main__ as cli
from chanlun.config import Config, DataConfig
from chanlun.data import meta, store
from chanlun.data.universe import Security

TRADING_DAY = "2026-09-30"
STALE_DAY = "2026-09-25"
SECS = [Security("600000", "sh.600000", "浦发银行", "sh", "1999-11-10", False)]


class _Cal:
    """可控交易日历：`trading=False` 时「今天不是交易日」。"""

    def __init__(self, trading: bool = True):
        self.trading = trading

    def last_trading_day(self, d):
        return dt.date.fromisoformat(str(d)[:10]) if self.trading else dt.date(2026, 9, 25)

    def next_trading_day(self, d):
        return dt.date.fromisoformat(str(d)[:10]) + dt.timedelta(days=1)


def _bars(n: int = 300, end: str = TRADING_DAY) -> pd.DataFrame:
    ts = pd.date_range(end=end, periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [10.0 + (i % 7) * 0.1 for i in range(n)],
            "high": [10.5 + (i % 7) * 0.1 for i in range(n)],
            "low": [9.5 + (i % 7) * 0.1 for i in range(n)],
            "close": [10.2 + (i % 7) * 0.1 for i in range(n)],
            "volume": [1000.0] * n,
            "amount": [10000.0] * n,
        }
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = Config(
        data=DataConfig(root=tmp_path / "data"),
        log_path=tmp_path / "logs" / "chanlun.log",
    )
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(cli, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(cli, "get_calendar", lambda *a, **k: _Cal())
    monkeypatch.setattr(cli, "build_universe", lambda *a, **k: list(SECS))
    monkeypatch.setattr(cli.quality, "run_all", lambda *a, **k: [])
    monkeypatch.setattr(store, "DATA_ROOT", cfg.data.root)
    yield cfg


def _conn(cfg):
    return meta.init(cfg.data.meta_db)


def _seed(cfg, code: str = "600000", period: str = "day", *, end: str = TRADING_DAY,
          n: int = 300) -> None:
    """把「本地已有数据」和 sync_state 记账一起造出来（模拟历史同步过）。"""
    store.upsert(code, period, _bars(n, end))
    conn = _conn(cfg)
    try:
        meta.set_sync(conn, code, period, start_ts="2020-01-02", end_ts=end, rows=n, adjust="2")
    finally:
        conn.close()


def _fake_fetch(periods: dict[str, pd.DataFrame], calls: list[tuple[str, str, str]]):
    def fetch(bs_code: str, period: str, start, end, adjust: str = "2"):
        calls.append((str(bs_code), str(period), str(start)[:10]))
        return periods.get(str(period), _bars()).copy()

    return fetch


def _notify_log(cfg):
    p = cfg.log_path.parent / "notify.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ------------------------------------------------------------------ 1. dry-run

def test_dry_run_touches_nothing(env, monkeypatch, capsys):
    """`--dry-run` 是「彩排」：不联网同步、不落库、不推送，只把结果打在屏幕上。"""
    _seed(env)
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli, "fetch_bars", _fake_fetch({}, calls))

    rc = cli.main(["daily", "--dry-run", "--workers", "1"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "dry-run" in out
    assert calls == [], "dry-run 不该联网拉数据"
    conn = _conn(env)
    try:
        assert list(conn.execute("SELECT * FROM structure_snapshot")) == []
        assert list(conn.execute("SELECT * FROM scan_result")) == []
    finally:
        conn.close()
    assert not (env.log_path.parent / "notify.log").exists(), "dry-run 不该推送"


# ------------------------------------------------------------------ 2. 全链路

def test_daily_syncs_snapshots_and_notifies(env, monkeypatch, capsys):
    """真跑一次：K 线落库 + sync_state 记账 + 结构快照入库 + 自选池跟踪并推送。"""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli, "fetch_bars", _fake_fetch({}, calls))
    conn = _conn(env)
    try:
        meta.add_watch(conn, "600000", "浦发银行")
    finally:
        conn.close()

    rc = cli.main(["daily", "--workers", "1"])
    out = capsys.readouterr().out

    assert rc == 0
    assert calls, "真跑必须同步"
    assert calls[0][1] == "day"
    conn = _conn(env)
    try:
        row = meta.all_sync(conn, "day")[0]
        assert str(row["end_ts"]) == TRADING_DAY
        snap = meta.get_structure_snapshot(conn, "600000", "day")
    finally:
        conn.close()
    assert snap is not None, "结构快照必须入库（跟踪与回顾都靠它）"
    assert snap["as_of"] == TRADING_DAY
    assert snap["period"] == "day"
    assert len(store.read("600000", "day")) == 300
    log_text = _notify_log(env)
    assert "600000" in log_text and "自选池" in log_text
    assert "每日流水线" in out


# ------------------------------------------------------------------ 3. 非交易日

def test_non_trading_day_exits_zero_without_syncing(env, monkeypatch, capsys):
    """周末/节假日跑（launchd 补跑）不算错误，也不该去拉数据。"""
    monkeypatch.setattr(cli, "get_calendar", lambda *a, **k: _Cal(trading=False))
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli, "fetch_bars", _fake_fetch({}, calls))

    rc = cli.main(["daily", "--workers", "1"])
    out = capsys.readouterr().out

    assert rc == 0
    assert calls == []
    assert "非交易日" in out
    conn = _conn(env)
    try:
        assert list(conn.execute("SELECT * FROM structure_snapshot")) == []
    finally:
        conn.close()


# ------------------------------------------------------------------ 4. 分钟数据陈旧降级

def test_stale_minute_data_degrades_to_day_only(env, monkeypatch, capsys):
    """30 分钟数据停在 09-25、日线到 09-30：必须降级「仅日线」，并在输出里写明陈旧程度。"""
    env = replace(env, periods=["day", "30"])
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: env)
    _seed(env, "600000", "day", end=TRADING_DAY)
    _seed(env, "600000", "30", end=STALE_DAY, n=500)
    calls: list[tuple[str, str, str]] = []
    # 30 分钟这次同步拿不到更新的数据（返回的仍是停在 09-25 的那一份）
    monkeypatch.setattr(cli, "fetch_bars", _fake_fetch({"30": _bars(500, STALE_DAY)}, calls))

    rc = cli.main(["daily", "--workers", "1"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "降级" in out and "仅日线" in out
    assert "30" in out and STALE_DAY in out, "要写明是哪个周期、陈旧到什么程度"
    conn = _conn(env)
    try:
        assert meta.get_structure_snapshot(conn, "600000", "30") is None, "陈旧周期不该产出快照"
        assert meta.get_structure_snapshot(conn, "600000", "day") is not None
    finally:
        conn.close()


def test_fresh_minute_data_is_not_degraded(env, monkeypatch, capsys):
    """对照实验：30 分钟数据跟日线一样新时，不许降级（否则就是无脑只跑日线）。"""
    env = replace(env, periods=["day", "30"])
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: env)
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli, "fetch_bars", _fake_fetch({"30": _bars(500)}, calls))

    rc = cli.main(["daily", "--workers", "1"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "降级" not in out
    conn = _conn(env)
    try:
        assert meta.get_structure_snapshot(conn, "600000", "30") is not None
    finally:
        conn.close()
