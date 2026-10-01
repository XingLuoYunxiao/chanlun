"""CLI 测试：fetch_bars 全部替换为假实现，不联网。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from chanlun import __main__ as cli
from chanlun.config import Config, DataConfig
from chanlun.data import meta, store
from chanlun.data.types import DataSourceError
from chanlun.data.universe import Security


def _df(n: int = 3, start: str = "2024-01-02") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [1.0 + i for i in range(n)],
            "high": [2.0 + i for i in range(n)],
            "low": [0.5 + i for i in range(n)],
            "close": [1.5 + i for i in range(n)],
            "volume": [10.0] * n,
            "amount": [100.0] * n,
        }
    )


class _FakeCal:
    """固定日历：next/last 都是「当天」，便于断言增量起点。"""

    def next_trading_day(self, d):
        return dt.date.fromisoformat(str(d)[:10]) + dt.timedelta(days=1)

    def last_trading_day(self, d):
        return dt.date.fromisoformat(str(d)[:10])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = Config(
        data=DataConfig(root=tmp_path / "data"),
        log_path=tmp_path / "logs" / "chanlun.log",
    )
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(store, "DATA_ROOT", cfg.data.root)
    monkeypatch.setattr(cli, "get_calendar", lambda *a, **k: _FakeCal())
    yield cfg


def _sync_rows(cfg):
    conn = meta.init(cfg.data.meta_db)
    try:
        return {r["code"]: r for r in meta.all_sync(conn, "day")}
    finally:
        conn.close()


def test_sync_only_requested_codes(env, monkeypatch):
    calls: list[tuple] = []

    def fake_fetch(code, period, start, end, adjust="2"):
        calls.append((code, period, str(start)[:10], str(end)[:10]))
        return _df()

    def _no_universe(*a, **k):  # --codes 存在时不应读取品种表
        raise AssertionError("指定 --codes 时不应调用 build_universe")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    monkeypatch.setattr(cli, "build_universe", _no_universe)

    rc = cli.main(["sync", "--period", "day", "--codes", "600000,000001"])
    assert rc == 0

    # 只处理指定代码
    assert {c[0] for c in calls} == {"sh.600000", "sz.000001"}
    rows = _sync_rows(env)
    assert set(rows) == {"600000", "000001"}
    assert all(r["error"] is None for r in rows.values())
    assert rows["600000"]["rows"] == 3

    # parquet 落盘
    assert (env.data.root / "day" / "sh" / "600000.parquet").exists()
    assert (env.data.root / "day" / "sz" / "000001.parquet").exists()
    assert store.row_count("600000", "day") == 3
    assert env.log_path.exists()  # 入口日志落到 logs/chanlun.log


def test_sync_failure_recorded_without_raising(env, monkeypatch):
    def fake_fetch(code, period, start, end, adjust="2"):
        if code.endswith("000001"):
            raise DataSourceError("boom")
        return _df()

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)

    rc = cli.main(["sync", "--period", "day", "--codes", "600000,000001"])
    assert rc == 0  # 单只失败不中断

    rows = _sync_rows(env)
    assert rows["000001"]["error"] is not None
    assert "boom" in rows["000001"]["error"]
    assert rows["600000"]["error"] is None
    assert (env.data.root / "day" / "sh" / "600000.parquet").exists()
    assert not (env.data.root / "day" / "sz" / "000001.parquet").exists()


def test_sync_all_failed_returns_nonzero(env, monkeypatch):
    def fake_fetch(*a, **k):
        raise DataSourceError("all dead")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    rc = cli.main(["sync", "--period", "day", "--codes", "600000,000001"])
    assert rc != 0


def test_sync_incremental_starts_after_last_ts(env, monkeypatch):
    store.upsert("600000", "day", _df(3, "2024-01-02"))  # 末行 2024-01-04
    seen: dict[str, str] = {}

    def fake_fetch(code, period, start, end, adjust="2"):
        seen["start"] = str(start)[:10]
        return _df(1, "2024-01-05")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    rc = cli.main(["sync", "--period", "day", "--codes", "600000"])
    assert rc == 0
    assert seen["start"] == "2024-01-05"  # 末行 01-04 的下一个交易日


def test_sync_full_restarts_from_1990(env, monkeypatch):
    store.upsert("600000", "day", _df(3, "2024-01-02"))
    seen: dict[str, str] = {}

    def fake_fetch(code, period, start, end, adjust="2"):
        seen["start"] = str(start)[:10]
        return _df(2, "2024-01-02")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    assert cli.main(["sync", "--period", "day", "--codes", "600000", "--full"]) == 0
    assert seen["start"] == "1990-01-01"


def test_sync_without_codes_uses_universe(env, monkeypatch):
    secs = [
        Security("600000", "sh.600000", "浦发银行", "sh", "1999-11-10", False),
        Security("000001", "sz.000001", "平安银行", "sz", "1991-04-03", False),
    ]
    monkeypatch.setattr(cli, "build_universe", lambda *a, **k: secs)
    calls: list[str] = []

    def fake_fetch(code, period, start, end, adjust="2"):
        calls.append(code)
        return _df()

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    assert cli.main(["sync", "--period", "day"]) == 0
    assert set(calls) == {"sh.600000", "sz.000001"}


def test_minute_sync_without_codes_uses_watchlist(env, monkeypatch):
    """分钟周期缺省只跑**自选池**，日线才跑全市场。

    日线有通达信整包（一次 551 MB、本地解析 83 秒），全市场跑得起；分钟没有公开整包，
    只能逐只走 baostock —— 实测 30 分 ≈30 秒/只、5 分 ≈162 秒/只，5471 只 ≈100 小时，
    会直接撞上第二天开盘。所以缺省范围是自选池（显式 `--codes` 仍照办）。
    """
    secs = [
        Security("600000", "sh.600000", "浦发银行", "sh", "1999-11-10", False),
        Security("000001", "sz.000001", "平安银行", "sz", "1991-04-03", False),
    ]
    monkeypatch.setattr(cli, "build_universe", lambda *a, **k: secs)
    conn = meta.init(env.data.meta_db)
    try:
        meta.add_watch(conn, "600000", "浦发银行")
    finally:
        conn.close()
    calls: list[str] = []

    def fake_fetch(code, period, start, end, adjust="2"):
        calls.append(str(code))
        return _df()

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    assert cli.main(["sync", "--period", "30"]) == 0
    assert calls == ["sh.600000"], "30 分只该同步自选池里的那一只"


def test_universe_command_prints_count(env, monkeypatch, capsys):
    secs = [
        Security("600000", "sh.600000", "浦发银行", "sh", "1999-11-10", False),
        Security("000001", "sz.000001", "平安银行", "sz", "1991-04-03", False),
    ]
    monkeypatch.setattr(cli, "build_universe", lambda *a, **k: secs)
    rc = cli.main(["universe"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2" in out
    # 未开 --enrich 时不能谎报「退市 0 只」
    assert "未补全" in out


def test_universe_command_reports_delisted_when_enriched(env, monkeypatch, capsys):
    secs = [
        Security("600000", "sh.600000", "浦发银行", "sh", "1999-11-10", False),
        Security("600001", "sh.600001", "邯郸钢铁", "sh", "1998-01-22", True),
    ]
    monkeypatch.setattr(cli, "build_universe", lambda *a, **k: secs)
    assert cli.main(["universe", "--enrich"]) == 0
    out = capsys.readouterr().out
    assert "2" in out
    assert "退市 1" in out


def test_sync_skips_empty_fetch(env, monkeypatch):
    def fake_fetch(code, period, start, end, adjust="2"):
        return _df(0)

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    rc = cli.main(["sync", "--period", "day", "--codes", "600000"])
    assert rc == 0  # 无数据不算失败
    rows = _sync_rows(env)
    assert rows["600000"]["rows"] == 0


def test_sync_malformed_code_does_not_abort(env, monkeypatch):
    """无法解析的代码属于「单只失败」，不能中断整批同步。"""

    monkeypatch.setattr(cli, "fetch_bars", lambda *a, **k: _df())

    rc = cli.main(["sync", "--period", "day", "--codes", "abc,600000"])
    assert rc == 0

    rows = _sync_rows(env)
    assert rows["600000"]["error"] is None
    assert rows["abc"]["error"] is not None
    assert "abc" in rows["abc"]["error"]
    assert (env.data.root / "day" / "sh" / "600000.parquet").exists()


def test_sync_since_applies_to_symbols_without_local_data(env, monkeypatch):
    """首次全市场同步：本地没数据的票从 --since 开始，别为 5471 只票各拉 35 年。"""
    seen: dict[str, str] = {}

    def fake_fetch(code, period, start, end, adjust="2"):
        seen["start"] = str(start)[:10]
        return _df(2, "2021-01-04")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    rc = cli.main(["sync", "--period", "day", "--codes", "000001", "--since", "2021-01-01"])
    assert rc == 0
    assert seen["start"] == "2021-01-01"


def test_sync_since_never_punches_a_hole_in_existing_history(env, monkeypatch):
    """已有数据的票仍然纯增量：数据停在 2024-01-04、--since 2025-01-01 时，

    起点必须是 2024-01-05（接着拉），不能是 2025-01-01 —— 否则文件里会留下
    2024-01-05～2024-12-31 的空洞，缠论结构会跨着洞算，比少几年历史危险得多。
    """
    store.upsert("600000", "day", _df(3, "2024-01-02"))  # 末行 2024-01-04
    seen: dict[str, str] = {}

    def fake_fetch(code, period, start, end, adjust="2"):
        seen["start"] = str(start)[:10]
        return _df(1, "2024-01-05")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    rc = cli.main(["sync", "--period", "day", "--codes", "600000", "--since", "2025-01-01"])
    assert rc == 0
    assert seen["start"] == "2024-01-05"


def test_sync_rejects_bad_since_format(env):
    with pytest.raises(SystemExit) as exc:
        cli.main(["sync", "--period", "day", "--codes", "600000", "--since", "2021/01/01"])
    assert exc.value.code == 2


def test_derived_periods_are_not_treated_as_minute_periods():
    """周/月是**派生周期**，不是分钟周期。

    `_is_minute_period` 曾经写成 `period != "day"`：一旦周期表里多了周线/月线，
    它们就会被当成分钟周期去圈自选池范围（`_period_scope` 拿它算同步范围），
    而它们根本不需要同步 —— 范围规则必须按「派生与否」判，不能按「是不是日线」判。
    """
    assert cli._is_minute_period("30") is True
    assert cli._is_minute_period("5") is True
    assert cli._is_minute_period("day") is False
    assert cli._is_minute_period("week") is False
    assert cli._is_minute_period("month") is False


# ---------------- 默认自选池 = 七个大盘指数 ----------------
def test_seed_watchlist_resets_the_pool_to_the_seven_indices(env, monkeypatch):
    """用户要的是「默认只放指数」，所以缺省必须**清空重建**，不是往旧池子里补。

    指数走 `adjustflag=3`（不复权）：指数没有除权，用缺省的 `2` 取数价格一样，
    但 `sync_state.adjust` 会替指数声称「前复权」，这句话会一路传到页面。
    """
    seen: list[tuple[str, str]] = []

    def fake_fetch(code, period, start, end, adjust="2"):
        seen.append((str(code), str(adjust)))
        return _df(2, "2024-01-02")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    meta.init(env.data.meta_db).close()
    conn = meta.init(env.data.meta_db)
    meta.add_watch(conn, "600000", "浦发银行")
    conn.close()

    rc = cli.main(["seed-watchlist"])
    assert rc == 0

    conn = meta.init(env.data.meta_db)
    try:
        codes = [str(r["code"]) for r in meta.get_watchlist(conn)]
        names = {str(r["code"]): str(r["name"]) for r in meta.get_watchlist(conn)}
        sync = {str(r["code"]): str(r["adjust"]) for r in meta.all_sync(conn, "day")}
    finally:
        conn.close()
    assert codes == [code for code, _ in meta.DEFAULT_WATCHLIST]
    assert "600000" not in codes, "旧的股票自选要清掉"
    assert names["399006"] == "创业板指"
    assert all(adjust == "3" for _code, adjust in seen), seen
    assert set(sync.values()) == {"3"}
    assert store.exists("sh.000001", "day")


def test_seed_watchlist_keep_does_not_clear_the_pool(env, monkeypatch):
    monkeypatch.setattr(cli, "fetch_bars", lambda *a, **k: _df(2, "2024-01-02"))
    meta.init(env.data.meta_db).close()
    conn = meta.init(env.data.meta_db)
    meta.add_watch(conn, "600000", "浦发银行")
    conn.close()

    assert cli.main(["seed-watchlist", "--keep", "--no-sync"]) == 0
    conn = meta.init(env.data.meta_db)
    try:
        codes = [str(r["code"]) for r in meta.get_watchlist(conn)]
    finally:
        conn.close()
    assert codes[0] == "600000", "用户排在第一位的票不该被搬走"
    assert "sh.000001" in codes


def test_seed_watchlist_reports_zero_row_indices_instead_of_pretending(env, monkeypatch):
    """数据源没有这只指数时（实测 sh.000688 科创50 返回 0 行），

    不许静默当成成功：`error` 里要写明原因，`rows` 要等于库里真实行数，
    否则「自选栏有 7 只、其中一只永远空白」会变成一个没人解释得清的现象。
    """
    def fake_fetch(code, period, start, end, adjust="2"):
        if str(code).endswith("000688"):
            return _df(0, "2024-01-02")
        return _df(2, "2024-01-02")

    monkeypatch.setattr(cli, "fetch_bars", fake_fetch)
    meta.init(env.data.meta_db).close()
    assert cli.main(["seed-watchlist"]) == 0

    conn = meta.init(env.data.meta_db)
    try:
        rows = {r["code"]: r for r in meta.all_sync(conn, "day")}
    finally:
        conn.close()
    assert rows["sh.000688"]["rows"] == 0
    assert rows["sh.000688"]["error"] and "0 行" in rows["sh.000688"]["error"]
    assert rows["sh.000001"]["error"] is None


def _write_day(path: Path, rows: list[tuple[int, float, float, float, float]]) -> None:
    """写一个通达信 `.day` 文件：32 字节/条，`<IIIIIfII`。"""
    import struct

    path.parent.mkdir(parents=True, exist_ok=True)
    blob = b"".join(
        struct.pack("<IIIIIfII", date, int(o * 100), int(h * 100), int(l * 100),
                    int(c * 100), amt, vol, 0)
        for date, o, h, l, c in rows
        for amt, vol in [(0.0, 0)]
    )
    path.write_bytes(blob)


def test_seed_watchlist_falls_back_to_the_local_tdx_package(env, monkeypatch):
    """baostock 没有科创50（实测 0 行），本地通达信整包里有 —— 用它补上，而不是留个空行。

    整包写的是不复权价，指数也没有除权，所以这条兜底不改口径；但**必须验号段**：
    `sz000688` 是另一只个股（收 24.24），拿它顶替指数就是换了一只票。
    """
    monkeypatch.setattr(cli, "fetch_bars", lambda *a, **k: _df(0, "2024-01-02"))
    _write_day(
        env.data.root / "tdx_raw" / "sh" / "lday" / "sh000688.day",
        [(20240102, 1000.0, 1010.0, 990.0, 1005.0), (20240103, 1005.0, 1020.0, 1000.0, 1015.0)],
    )
    meta.init(env.data.meta_db).close()
    assert cli.main(["seed-watchlist"]) == 0

    conn = meta.init(env.data.meta_db)
    try:
        rows = {r["code"]: r for r in meta.all_sync(conn, "day")}
    finally:
        conn.close()
    assert rows["sh.000688"]["rows"] == 2
    assert rows["sh.000688"]["error"] is None
    assert store.exists("sh.000688", "day")
    df = store.read("sh.000688", "day")
    assert list(df["ts"]) == ["2024-01-02", "2024-01-03"]
    assert df["close"].iloc[-1] == 1015.0


def test_tdx_fallback_refuses_a_stock_number_segment(env):
    """同一串数字在别的号段是个股：`sh600000` 不是指数，兜底不许把它当指数收下。"""
    _write_day(
        env.data.root / "tdx_raw" / "sh" / "lday" / "sh600000.day",
        [(20240102, 10.0, 10.5, 9.9, 10.2)],
    )
    df, why = cli._tdx_index_frame(env, "sh.600000")
    assert df is None
    assert "不是指数" in why


def test_sync_writes_raw_adjust_for_an_index(env, monkeypatch):
    """普通 `sync` 命令拉到指数时，落库也必须写「不复权」。

    这条口径原先只存在于 `seed-watchlist` 里（硬编码 `"3"`），`sync` 走的是配置里的
    前复权；Task 30 把两处合到 `data/sync.py`，这里钉住合完之后 `sync` 也是对的。
    """
    monkeypatch.setattr(cli, "fetch_bars", lambda *a, **k: _df(2, "2024-01-02"))
    rc = cli.main(["sync", "--period", "day", "--codes", "sh.000001,600000"])
    assert rc == 0
    rows = _sync_rows(env)
    assert rows["sh.000001"]["adjust"] == "3", "指数没有除权除息"
    assert rows["600000"]["adjust"] == "2", "股票仍按配置的前复权"
