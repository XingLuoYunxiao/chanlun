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
