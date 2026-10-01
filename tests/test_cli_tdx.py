"""`tdx` 子命令测试：不起网络，下载那一段用本地 HTTP 服务真跑一遍。

`__main__.TdxConfig` 以外的隔离由 `tests/conftest.py` 的自动夹具负责（store 与 meta.db）。
"""

from __future__ import annotations

import http.server
import struct
import threading

import pytest

from chanlun import __main__ as cli
from chanlun.data import meta, store, tdx


def _rec(date: int, cents: int) -> bytes:
    return struct.pack("<IIIIIfII", date, cents, cents, cents, cents, 1e6, 1000, 0)


def _write_day(src, market: str, code: str, dates) -> None:
    d = src / market / "lday"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{market}{code}.day").write_bytes(
        b"".join(_rec(x, 1000 + i) for i, x in enumerate(dates))
    )


@pytest.fixture()
def fake_src(tmp_path):
    """4 只票 × 2 根 K 线：沪 600000 / 沪指数 000300 / 深 000001 / 北 430017。"""
    src = tmp_path / "raw"
    for market, code in (("sh", "600000"), ("sh", "000300"), ("sz", "000001"), ("bj", "430017")):
        _write_day(src, market, code, (20260102, 20260105))
    return src


def test_tdx_parser_defaults():
    args = cli.build_parser().parse_args(["tdx", "--src", "data/tdx_raw"])
    assert args.command == "tdx" and args.period == "day" and args.dry_run is False
    assert args.markets == "sh,sz,bj"
    assert args.kinds == "stock,index"


def test_tdx_dry_run_prints_summary_and_touches_nothing(fake_src, capsys):
    rc = cli.main(argv=["tdx", "--src", str(fake_src), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "通达信" in out and "成功 4" in out
    assert "共 8 行" in out and "口径 raw" in out
    assert not store.data_root().exists() or not store.exists("600000", "day")


def test_tdx_import_writes_store_and_meta(fake_src, capsys):
    assert cli.main(argv=["tdx", "--src", str(fake_src)]) == 0
    assert len(store.read("600000", "day")) == 2
    assert len(store.read("sh.000300", "day")) == 2
    assert len(store.read("430017", "day")) == 2
    conn = meta.init()
    try:
        row = meta.get_sync(conn, "600000", "day")
        assert (row["start_ts"], row["end_ts"], row["rows"], row["adjust"]) == (
            "2026-01-02", "2026-01-05", 2, "raw")
    finally:
        conn.close()
    assert "成功 4" in capsys.readouterr().out


def test_tdx_prints_newest_date_per_market(fake_src, capsys):
    _write_day(fake_src, "bj", "430017", (20250102, 20250105))  # 北交所明显陈旧
    assert cli.main(argv=["tdx", "--src", str(fake_src), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "沪 2026-01-05" in out and "深 2026-01-05" in out
    assert "北 2025-01-05（陈旧）" in out          # 比最新市场落后 10 天以上要标出来
    assert "沪 2026-01-05（陈旧）" not in out


def test_tdx_rejects_minute_period(fake_src, capsys):
    rc = cli.main(argv=["tdx", "--src", str(fake_src), "--period", "5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "分钟" in err and "整包" in err


def test_tdx_can_limit_kinds_and_codes(fake_src, capsys):
    assert cli.main(argv=["tdx", "--src", str(fake_src), "--codes", "600000", "--dry-run"]) == 0
    assert "成功 1" in capsys.readouterr().out


def test_daily_accepts_source_choice():
    args = cli.build_parser().parse_args(["daily", "--source", "tdx", "--dry-run"])
    assert args.source == "tdx"


# ---------------- fetch_package：本地 HTTP 服务，真跑 HEAD + 下载 ----------------
class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # 别把请求日志打到 stdout
        pass


@pytest.fixture()
def http_dir(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: _Quiet(*a, directory=root, **k))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_fetch_package_downloads_then_skips_when_size_matches(http_dir, tmp_path):
    root, base = http_dir
    (root / "hsjday.zip").write_bytes(b"A" * 4096)
    dest = tmp_path / "downloads"
    path = tdx.fetch_package(dest, f"{base}/hsjday.zip")
    assert path.read_bytes() == b"A" * 4096
    assert not path.with_suffix(".zip.part").exists()

    (root / "hsjday.zip").write_bytes(b"B" * 4096)   # 同样大小、内容不同
    assert tdx.fetch_package(dest, f"{base}/hsjday.zip") == path
    assert path.read_bytes() == b"A" * 4096          # 大小一致就跳过，不重复下载
