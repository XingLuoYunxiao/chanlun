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

def test_minute_periods_are_scoped_to_the_watchlist(env, monkeypatch, capsys):
    """没有 `--codes` 时，30/5 分钟只跑**自选池**；日线仍走全市场。

    日线有通达信整包（一次 551 MB、本地解析 83 秒），全市场跑得起；分钟没有公开整包，
    只能逐只走 baostock —— 实测 30 分 ≈30 秒/只、5 分 ≈162 秒/只，5471 只 ≈100 小时，
    会直接撞上第二天开盘。所以分钟的范围缺省是自选池。

    范围用**落库结果**观察，不看「某个函数被调用过」：给非自选票也造一份新鲜的 30 分
    数据，范围没收住的话它就会被同步、被算出结构快照。
    """
    env = replace(env, periods=["day", "30"])
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: env)
    monkeypatch.setattr(cli, "build_universe", lambda *a, **k: [
        *SECS, Security("000001", "sz.000001", "平安银行", "sz", "1991-04-03", False)])
    conn = _conn(env)
    try:
        meta.add_watch(conn, "600000", "浦发银行")          # 自选池只有 600000
    finally:
        conn.close()
    _seed(env, "600000", "day", end=TRADING_DAY)
    _seed(env, "000001", "day", end=TRADING_DAY)
    _seed(env, "600000", "30", end=TRADING_DAY, n=500)      # 自选票的分钟：该算
    _seed(env, "000001", "30", end=TRADING_DAY, n=500)      # 非自选票的分钟：不该算
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cli, "fetch_bars", _fake_fetch({"30": _bars(500)}, calls))

    rc = cli.main(["daily", "--workers", "1"])
    out = capsys.readouterr().out
    assert rc == 0

    # D-43：范围按**市场**分派 —— 港股/美股没有分钟源，A 股才用 `_period_scope`。
    # `000001` 是 A 股（深市），所以这条断言仍然拦得住「把非自选 A 股也算了分钟」。
    assert {c for c, p, _ in calls if p == "30"} == {"600000"}, "30 分只同步自选池"
    assert {c for c, p, _ in calls if p == "day"} == {"600000", "000001"}, "日线仍走全市场"
    conn = _conn(env)
    try:
        assert meta.get_structure_snapshot(conn, "600000", "30") is not None, "自选票的分钟结构要算"
        assert meta.get_structure_snapshot(conn, "000001", "30") is None, "非自选票的分钟结构不该算"
    finally:
        conn.close()
    assert "每日流水线" in out


def test_stale_minute_data_degrades_to_day_only(env, monkeypatch, capsys):
    """30 分钟数据停在 09-25、日线到 09-30：必须降级「仅日线」，并在输出里写明陈旧程度。

    分钟周期的范围是自选池，所以场景要先把票放进自选池 —— 否则它会因为「范围为空」
    而蒙对，测不到「陈旧」这件事。
    """
    env = replace(env, periods=["day", "30"])
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: env)
    conn = _conn(env)
    try:
        meta.add_watch(conn, "600000", "浦发银行")
    finally:
        conn.close()
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
    conn = _conn(env)
    try:
        meta.add_watch(conn, "600000", "浦发银行")
    finally:
        conn.close()
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


# ------------------------------------------- 8. 校验摘要必须说清「查的是什么」

def test_summary_breaks_the_check_down_by_kind(env, monkeypatch):
    """「抽样 40 条，失败 0 条」把两种哨兵合成一个数字，等于没说清查了什么。

    原始提案 round-009-G5a：`cross_source` 要去外部源取数，外部源整年不可达、
    跨源校验一条没跑成，报表依然满分通过 —— 数量没撒谎，是口径被合并掉了。
    主干采纳后这条判据常驻于此。
    """
    from chanlun.data.quality import CheckResult

    results = [
        CheckResult(code="600000", kind="cross_source", ok=True, detail=""),
        CheckResult(code="600001", kind="cross_source", ok=False, detail="东财不可达"),
        CheckResult(code="600002", kind="latest_equals_actual", ok=True, detail=""),
    ]
    monkeypatch.setattr(cli.quality, "run_all", lambda *a, **k: results)
    # 同步段必须挡掉真网络：`env` 只换了数据根，`fetch_bars` 还是真的会登 baostock
    monkeypatch.setattr(cli, "fetch_bars", _fake_fetch({}, []))
    _seed(env)
    args = cli.build_parser().parse_args(["daily", "--periods", "day", "--notify", "null"])
    text = cli._format_daily(cli._run_daily(args, env, _conn(env)))
    assert "cross_source 2 条失败 1" in text, text
    assert "latest_equals_actual 1 条失败 0" in text, text
    # 数据源那一行不能是跟实际取过哪些源无关的硬编码
    assert "校验用外部源：东财 fqt=1" in text, text


def test_quality_report_carries_the_kind_breakdown(tmp_path):
    """同一份口径也要落进 quality_report.json，页面/脚本读得到。"""
    import json

    from chanlun.data import quality
    from chanlun.data.quality import CheckResult

    path = tmp_path / "quality_report.json"
    quality._write_report(path, [
        CheckResult(code="600000", kind="cross_source", ok=True, detail=""),
        CheckResult(code="600001", kind="cross_source", ok=False, detail="x"),
    ])
    summary = json.loads(path.read_text(encoding="utf-8"))["summary"]
    assert summary["kinds"] == {"cross_source": {"total": 2, "failed": 1}}
    assert (summary["total"], summary["failed"]) == (2, 1)
