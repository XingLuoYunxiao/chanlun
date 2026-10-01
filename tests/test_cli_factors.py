"""`factors` 子命令：不联网、不碰真实 `data/`（隔离见 `tests/conftest.py`）。

命令行是这套流水线唯一的入口，所以「参数接对了」和「反推逻辑对」要分开测：
前者一旦漏接，脚本会在生产里安静地什么都不做。
"""

from __future__ import annotations

import struct

import pytest

from chanlun import __main__ as cli
from chanlun.data import factors, meta, store


def _rec(date: int, cents: int) -> bytes:
    return struct.pack("<IIIIIfII", date, cents, cents, cents, cents, 1e6, 1000, 0)


@pytest.fixture()
def ready(tmp_path, monkeypatch):
    """一只 2 拆 1 的票：原始价前 6 根 10 元、后 6 根 5 元，库内前复权恒为 5 元。"""
    src = tmp_path / "raw"
    d = src / "sh" / "lday"
    d.mkdir(parents=True)
    days = [20260105, 20260106, 20260107, 20260108, 20260109, 20260112,
            20260113, 20260114, 20260115, 20260116, 20260119, 20260120]
    cents = [1000] * 6 + [500] * 6
    (d / "sh600030.day").write_bytes(b"".join(_rec(x, c) for x, c in zip(days, cents)))
    import pandas as pd

    ts = pd.date_range("2026-01-05", periods=12, freq="B").strftime("%Y-%m-%d")
    store.write("600030", "day", pd.DataFrame({
        "ts": ts, "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0,
        "volume": 100.0, "amount": 1e5,
    }))
    conn = meta.init()
    try:
        meta.set_sync(conn, "600030", "day", None, None, 12, "2")
        meta.add_watch(conn, "600030")
    finally:
        conn.close()
    return src


def test_factors_parser_defaults():
    args = cli.build_parser().parse_args(["factors"])
    assert args.source == "infer" and args.period == "day"
    assert args.min_overlap == factors.MIN_OVERLAP and args.dry_run is False
    assert args.all is False


def test_factors_rejects_a_source_it_cannot_honour():
    """`--source baostock` 必须报错退出，不能静默按反推跑 ——
    用户以为拿到的是权威因子、实际是反推值，这种错没法从输出里看出来。"""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["factors", "--source", "baostock"])


def test_factors_cli_writes_the_watchlist_ladder(ready):
    rc = cli.main(["factors", "--src", str(ready), "--min-overlap", "6"])
    assert rc == 0
    conn = meta.init()
    try:
        got = meta.get_adjust_factors(conn, "600030")
    finally:
        conn.close()
    assert list(got["k"]) == [0.5, 1.0]


def test_factors_cli_dry_run_writes_nothing(ready):
    rc = cli.main(["factors", "--src", str(ready), "--dry-run", "--min-overlap", "6"])
    assert rc == 0
    conn = meta.init()
    try:
        assert meta.get_adjust_factors(conn, "600030").empty
    finally:
        conn.close()


def test_factors_cli_rejects_a_non_daily_period(ready):
    assert cli.main(["factors", "--src", str(ready), "--period", "5"]) == 2


def test_factors_cli_explains_an_empty_watchlist(ready, capsys):
    conn = meta.init()
    try:
        conn.execute("DELETE FROM watchlist")
        conn.commit()
    finally:
        conn.close()
    assert cli.main(["factors", "--src", str(ready), "--min-overlap", "6"]) == 2
    assert "自选池是空的" in capsys.readouterr().err


def test_factors_cli_reports_why_a_code_was_skipped(ready, capsys):
    """一只票都没有因子时退出码非 0，并把原因写在 stderr —— 否则 launchd 里静默失败。"""
    assert cli.main(["factors", "--codes", "600519", "--src", str(ready)]) == 1
    err = capsys.readouterr().err
    assert "no_raw" in err and "600519" in err
