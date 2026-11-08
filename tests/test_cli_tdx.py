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


def test_tdx_minute_period_without_download_is_rejected_when_folder_missing(fake_src, capsys):
    """分钟线没有官方整包；目录不存在时要说清「去装 iMac 版、用盘后数据下载」。"""
    rc = cli.main(argv=["tdx", "--src", str(fake_src), "--period", "5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "fzline" in err and "iMac" in err


def test_tdx_minute_period_rejects_download(fake_src, capsys):
    """`--download` 只对日线整包有意义；分钟线不能走它。"""
    rc = cli.main(argv=["tdx", "--src", str(fake_src), "--period", "5", "--download"])
    assert rc == 2
    assert "整包" in capsys.readouterr().err


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


# ---------------- 分钟线（.lc5 / .lc1）：通达信 iMac 版「盘后数据下载」的产物 ----------------
def _mrec(year, month, day, minute_of_day, o, h, l, c, amount=1e6, vol=1000):
    raw_date = (year - 2004) * 2048 + month * 100 + day
    return struct.pack("<HHfffffII", raw_date, minute_of_day, o, h, l, c, amount, vol, 0)


def _write_minute(src, market, code, blob, period="5", folder=None):
    d = src / market / (folder or tdx.FOLDERS[period])
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{market}{code}{tdx.PERIOD_SUFFIXES[period]}").write_bytes(blob)


def test_minute_ts_decodes_the_documented_encoding():
    """日期 `(年-2004)*2048+月*100+日`、时间 = 当日第几分钟（9:35 → 575）。"""
    assert tdx.minute_ts((2026 - 2004) * 2048 + 1 * 100 + 2, 575) == "2026-01-02 09:35"
    assert tdx.minute_ts((2026 - 2004) * 2048 + 10 * 100 + 13, 900) == "2026-10-13 15:00"
    assert tdx.minute_ts((1995 - 2004) * 2048 + 12 * 100 + 29, 0) == "1995-12-29 00:00"


def test_parse_minute_bytes_round_trip():
    blob = (_mrec(2026, 1, 2, 575, 10.0, 11.0, 9.5, 10.5)
            + _mrec(2026, 1, 2, 580, 10.5, 11.5, 10.0, 11.0))
    df = tdx.parse_minute_bytes(blob, "5")
    assert list(df["ts"]) == ["2026-01-02 09:35", "2026-01-02 09:40"]
    assert list(df["close"]) == [10.5, 11.0]
    assert list(df["volume"]) == [1000.0, 1000.0]


@pytest.mark.parametrize("blob, why", [
    (_mrec(2026, 13, 2, 575, 10, 11, 9, 10), "月越界"),
    (_mrec(2026, 1, 40, 575, 10, 11, 9, 10), "日越界"),
    (_mrec(2026, 1, 2, 1500, 10, 11, 9, 10), "分钟数越界"),
    (_mrec(2026, 1, 2, 575, 10, 11, 9, 10) + _mrec(2026, 1, 2, 575, 10, 11, 9, 10), "时间没前进"),
    (_mrec(2026, 1, 2, 575, 0, 11, 9, 10), "非正价格"),
    (_mrec(2026, 1, 2, 575, 10, 9, 9, 10), "高 < 收"),
])
def test_parse_minute_bytes_fails_loudly_on_any_bad_record(blob, why):
    """**任何一条**不合就炸 —— 日期编码假设错了必须当场暴露，不能静默落库。

    注意「年越界」那一支**构造不出来**：`u16` 的编码基数就是 2004，
    `raw` 可取 0..65535 ⇒ 年份只能落在 2004–2035。`YEAR_LO/YEAR_HI` 是防御性的，
    留给「位宽假设变了」的情形，不值得为它编一个做不到的用例。
    """
    with pytest.raises(tdx.TdxFormatError):
        tdx.parse_minute_bytes(blob, "5")


def test_parse_minute_bytes_rejects_truncated_file():
    with pytest.raises(tdx.TdxFormatError):
        tdx.parse_minute_bytes(b"\x00" * 33, "5")


def test_parse_file_dispatches_and_cross_checks_suffix(tmp_path):
    p = tmp_path / "sh600000.lc5"
    p.write_bytes(_mrec(2026, 1, 2, 575, 10, 11, 9, 10))
    assert len(tdx.parse_file(p, "5")) == 1
    with pytest.raises(tdx.TdxFormatError):      # 周期与扩展名不符
        tdx.parse_file(p, "1")


def test_verify_minute_against_day_catches_mismatch(tmp_path):
    """交叉验证是**唯一**能证明日期编码假设成立的检查，必须能失败。"""
    day = tdx.parse_day_bytes(_rec(20260102, 1000))          # 日线 OHLC 全 10.0 元
    ok = tdx.parse_minute_bytes(_mrec(2026, 1, 2, 575, 10.0, 10.0, 10.0, 10.0)
                                + _mrec(2026, 1, 2, 580, 10.0, 10.0, 10.0, 10.0))
    assert tdx.verify_minute_against_day(ok, day) == []
    # ① 数值对不上 → 报具体字段
    off = tdx.parse_minute_bytes(_mrec(2026, 1, 2, 575, 10.0, 10.5, 10.0, 10.0))
    problems = tdx.verify_minute_against_day(off, day)
    assert problems and "high 不一致" in problems[0]
    # ② 日期整体挪一天 → 报「没有共同交易日」
    shifted = tdx.parse_minute_bytes(_mrec(2026, 1, 3, 575, 10.0, 10.0, 10.0, 10.0))
    assert any("共同交易日" in p for p in tdx.verify_minute_against_day(shifted, day))


def _day_row(y, mo, d, o, h, l, c):
    """一条日线记录（价格单位是**分**）。"""
    return struct.pack(
        "<IIIIIfII", y * 10000 + mo * 100 + d,
        int(round(o * 100)), int(round(h * 100)), int(round(l * 100)), int(round(c * 100)),
        1e6, 1000, 0,
    )


def _write_day_rows(src, market, code, blob):
    d = src / market / tdx.FOLDERS["day"]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{market}{code}.day").write_bytes(blob)


# 2026-01-02 的 5 分钟两根：聚合 open=10 high=11.5 low=9.5 close=11
_MIN_600000 = (
    _mrec(2026, 1, 2, 575, 10, 11, 9.5, 10.5)
    + _mrec(2026, 1, 2, 580, 10.5, 11.5, 10, 11)
    + _mrec(2026, 1, 5, 575, 11, 12, 10.5, 11.5)
    + _mrec(2026, 1, 5, 580, 11.5, 12.5, 11, 12)
)
_DAY_600000 = (
    _day_row(2026, 1, 2, 10, 11.5, 9.5, 11)
    + _day_row(2026, 1, 5, 11, 12.5, 10.5, 12)
)


def test_tdx_imports_minute_vipdoc(tmp_path, capsys):
    """端到端：`--period 5` 从 `<src>/sh/fzline/*.lc5` 导入并落库，**且过了交叉验证**。"""
    src = tmp_path / "raw"
    _write_minute(src, "sh", "600000", _MIN_600000)
    _write_day_rows(src, "sh", "600000", _DAY_600000)      # 同口径参照
    assert cli.main(["tdx", "--src", str(src), "--period", "5", "--codes", "600000"]) == 0
    out = capsys.readouterr().out
    assert "成功 1" in out and "通过 1 只" in out
    df = store.read("600000", "5")
    assert list(df["ts"]) == [
        "2026-01-02 09:35", "2026-01-02 09:40", "2026-01-05 09:35", "2026-01-05 09:40",
    ]


def test_tdx_imports_index_minute_vipdoc(tmp_path, capsys):
    """**这是选通达信这条路的全部理由**：vipdoc 里指数与股票同目录，
    所以指数（baostock 对它的分钟线返回 0 行）也能拿到分钟基准。"""
    src = tmp_path / "raw"
    _write_minute(src, "sh", "000300", _mrec(2026, 1, 2, 575, 4000, 4010, 3990, 4005))
    _write_minute(src, "sz", "399001", _mrec(2026, 1, 2, 575, 10000, 10010, 9990, 10005))
    _write_day_rows(src, "sh", "000300", _day_row(2026, 1, 2, 4000, 4010, 3990, 4005))
    _write_day_rows(src, "sz", "399001", _day_row(2026, 1, 2, 10000, 10010, 9990, 10005))
    assert cli.main(["tdx", "--src", str(src), "--period", "5", "--kinds", "index"]) == 0
    out = capsys.readouterr().out
    assert "成功 2" in out and "通过 2 只" in out
    assert len(store.read("sh.000300", "5")) == 1
    assert len(store.read("399001", "5")) == 1


def test_tdx_imports_one_minute_vipdoc(tmp_path, capsys):
    """`.lc1` 走 `minline/` 目录，`--period 1`。"""
    src = tmp_path / "raw"
    blob = _mrec(2026, 1, 2, 575, 10, 10, 10, 10)
    _write_minute(src, "sh", "600000", blob, period="1")
    _write_day_rows(src, "sh", "600000", _day_row(2026, 1, 2, 10, 10, 10, 10))
    assert cli.main(["tdx", "--src", str(src), "--period", "1", "--codes", "600000"]) == 0
    assert "成功 1" in capsys.readouterr().out
    assert list(store.read("600000", "1")["ts"]) == ["2026-01-02 09:35"]


# ---------------- 交叉验证硬门：不验证就不落库 ----------------
def test_minute_import_refuses_without_day_reference(tmp_path, capsys):
    """没有 raw 口径的日线参照 → **不写入**。这是 D-09「宁可不做」的代码落点。"""
    src = tmp_path / "raw"
    _write_minute(src, "sh", "600000", _MIN_600000)      # 只有分钟，没有 .day
    rc = cli.main(["tdx", "--src", str(src), "--period", "5", "--codes", "600000"])
    out, err = capsys.readouterr().out, capsys.readouterr().err
    assert rc == 0                                    # 不是崩溃，是「如实拒绝」
    assert "成功 0" in out and "未通过（未落库）1 只" in out
    assert store.read("600000", "5").empty, "没通过验证的数据一个字都不许落库"


def test_minute_import_refuses_on_mismatch(tmp_path, capsys):
    """有参照但对不上 → 不写入，且 error 里说明对不上在哪。"""
    src = tmp_path / "raw"
    _write_minute(src, "sh", "600000", _MIN_600000)
    # 日线整体贵 2 元 → 聚合 OHLC 全对不上
    _write_day_rows(src, "sh", "600000",
                    _day_row(2026, 1, 2, 12, 13.5, 11.5, 13)
                    + _day_row(2026, 1, 5, 13, 14.5, 12.5, 14))
    err = ""
    cli.main(["tdx", "--src", str(src), "--period", "5", "--codes", "600000"])
    err = capsys.readouterr().err
    assert "未通过日线交叉验证" in err and "不一致" in err
    assert store.read("600000", "5").empty


def test_minute_import_reports_unverified_line(tmp_path, capsys):
    """汇总里必须**分开**报「通过」与「未通过」—— 两者是不同的事故。"""
    src = tmp_path / "raw"
    _write_minute(src, "sh", "600000", _MIN_600000)
    _write_day_rows(src, "sh", "600000", _DAY_600000)
    _write_minute(src, "sh", "600004", _MIN_600000)       # 这只没有日线参照
    cli.main(["tdx", "--src", str(src), "--period", "5"])
    out = capsys.readouterr().out
    assert "通过 1 只" in out and "未通过（未落库）1 只" in out


def test_allow_unverified_writes_but_still_counts(tmp_path, capsys):
    """`--allow-unverified` 是逃生门：照写，但**如实计进未通过**，不粉饰。"""
    src = tmp_path / "raw"
    _write_minute(src, "sh", "600000", _MIN_600000)
    assert cli.main(["tdx", "--src", str(src), "--period", "5",
                     "--codes", "600000", "--allow-unverified"]) == 0
    out = capsys.readouterr().out
    assert "成功 1" in out and "未通过（未落库）1 只" in out
    assert len(store.read("600000", "5")) == 4


# ---------------- 口径混用告警（D-19） ----------------
def test_minute_import_warns_on_adjust_mix(tmp_path, capsys):
    """分钟线按 **raw** 落库、日线还是 baostock **前复权** → 必须告警。

    帧内递归不受影响（只用一张 bar 帧），但两套结构建在不同的价格序列上，
    跨周期比较失真；也正踩在 D-19「跨口径拼接会在除权日长出假跳空」那条上。
    """
    src = tmp_path / "raw"
    _write_minute(src, "sh", "600000", _MIN_600000)
    _write_day_rows(src, "sh", "600000", _DAY_600000)
    conn = meta.init()
    try:                                   # 复刻「日线是前复权」的现状
        meta.set_sync(conn, "600000", "day", "2026-01-02", "2026-01-05", 2, "2")
    finally:
        conn.close()
    cli.main(["tdx", "--src", str(src), "--period", "5", "--codes", "600000"])
    err = capsys.readouterr().err
    assert "口径混用" in err
    assert "factors" in err and "--period day" in err


def test_minute_import_does_not_warn_when_day_is_raw(tmp_path, capsys):
    """负对照：日线也是 raw（或是指数那种 adjust=3）时**不该**告警 ——
    否则这条告警会变成恒响的噪音，等于没测。"""
    src = tmp_path / "raw"
    _write_minute(src, "sh", "000300", _mrec(2026, 1, 2, 575, 4000, 4010, 3990, 4005))
    _write_day_rows(src, "sh", "000300", _day_row(2026, 1, 2, 4000, 4010, 3990, 4005))
    conn = meta.init()
    try:
        meta.set_sync(conn, "sh.000300", "day", "2026-01-02", "2026-01-02", 1, "raw")
    finally:
        conn.close()
    cli.main(["tdx", "--src", str(src), "--period", "5", "--kinds", "index"])
    assert "口径混用" not in capsys.readouterr().err

