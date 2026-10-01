"""通达信 vipdoc 解析器测试。

真包数值取自 2026-09-30 的官方 hsjday.zip（解在本机 data/tdx_raw/ 下）。
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from chanlun.data import tdx

REAL_RAW = Path(__file__).resolve().parents[2] / "data" / "tdx_raw"


def _day_record(
    date=20260930, o=1234, h=1300, low=1200, c=1250, amount=1_000_000.0, volume=1000, reserved=0
) -> bytes:
    return struct.pack("<IIIIIfII", date, o, h, low, c, amount, volume, reserved)


def test_parse_day_bytes_decodes_dates_and_yuan_prices():
    df = tdx.parse_day_bytes(_day_record())
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume", "amount"]
    row = df.iloc[0]
    assert row["ts"] == "2026-09-30"
    assert (row["open"], row["high"], row["low"], row["close"]) == (12.34, 13.00, 12.00, 12.50)
    assert row["volume"] == 1000.0
    assert row["amount"] == 1_000_000.0


def test_parse_day_bytes_rejects_truncated_file():
    with pytest.raises(tdx.TdxFormatError, match="不是 32 的整数倍"):
        tdx.parse_day_bytes(_day_record() + b"\x00")


def test_parse_day_bytes_rejects_illegal_date():
    with pytest.raises(tdx.TdxFormatError, match="日期非法"):
        tdx.parse_day_bytes(_day_record(date=0))


def test_parse_file_rejects_minute_suffix_until_verified(tmp_path):
    p = tmp_path / "600000.lc5"
    p.write_bytes(b"\x00" * 32)
    with pytest.raises(tdx.TdxFormatError, match="只支持"):
        tdx.parse_file(p)


@pytest.mark.parametrize("name", ["sh\\lday\\sh600000.day", "sh/lday/sh600000.day"])
def test_split_vipdoc_path_handles_windows_separators(name):
    assert tdx.split_vipdoc_path(name) == ("sh", "600000", "day")


def test_split_vipdoc_path_maps_minute_suffix():
    assert tdx.split_vipdoc_path("sz\\fzline\\sz000001.lc5") == ("sz", "000001", "5")


@pytest.mark.parametrize(
    "bad", ["sh600000.day", "sh/lday/sz600000.day", "sh/lday/x.txt", "sh/lday/sh60000.day"]
)
def test_split_vipdoc_path_rejects_garbage(bad):
    with pytest.raises(tdx.TdxFormatError):
        tdx.split_vipdoc_path(bad)


def test_store_key_qualifies_only_when_bare_code_would_point_elsewhere():
    """前缀只为「不撞车」而存在，不是为「它是指数」而存在。

    `sh.000001`（上证指数）必须带前缀：裸码 `000001` 按约定是深市（平安银行）。
    `sz.399001`（深证成指）不带：`39` 号段深市独有，裸码不撞车。
    """
    assert tdx.store_key("sh", "000300") == "sh.000300"
    assert tdx.store_key("sh", "000001") == "sh.000001"
    assert tdx.store_key("sz", "399001") == "399001"
    assert tdx.store_key("sh", "600000") == "600000"
    assert tdx.store_key("sz", "000001") == "000001"
    assert tdx.store_key("bj", "430017") == "430017"
    assert tdx.store_key("bj", "920017") == "920017"  # 改号后仍在北交所目录


def test_extract_package_normalizes_backslash_paths(tmp_path):
    zp = tmp_path / "pack.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("sh\\lday\\sh600000.day", _day_record())
        z.writestr("sh\\lday\\sh000300.day", _day_record(date=20260929))
        z.writestr("sh\\lday\\readme.txt", "x")
    assert len(tdx.list_package(zp)) == 3
    assert tdx.extract_package(zp, tmp_path / "out") == 2
    assert (tmp_path / "out" / "sh" / "lday" / "sh600000.day").exists()
    assert not (tmp_path / "out" / "sh" / "lday" / "readme.txt").exists()


@pytest.mark.skipif(
    not (REAL_RAW / "bj" / "lday" / "bj430017.day").exists(), reason="本机没有解开的真包"
)
def test_real_package_golden_values():
    """北交所老代码：包里的数据停在改号日 2025-09-30（新代码见 bj920017）。"""
    df = tdx.parse_file(REAL_RAW / "bj" / "lday" / "bj430017.day")
    assert len(df) == 570
    assert df.iloc[0]["ts"] == "2023-05-31"
    last = df.iloc[-1]
    assert last["ts"] == "2025-09-30" and last["close"] == 18.27
    assert last["volume"] == 1_052_312.0
    assert last["amount"] == pytest.approx(19_165_238.0, rel=1e-6)


@pytest.mark.skipif(
    not (REAL_RAW / "bj" / "lday" / "bj920017.day").exists(), reason="本机没有解开的真包"
)
def test_real_package_renumbered_code_continues():
    df = tdx.parse_file(REAL_RAW / "bj" / "lday" / "bj920017.day")
    assert (df.iloc[0]["ts"], df.iloc[-1]["ts"]) == ("2023-05-31", "2026-09-30")


@pytest.mark.skipif(
    not (REAL_RAW / "sh" / "lday" / "sh000300.day").exists(), reason="本机没有解开的真包"
)
def test_real_index_golden_values():
    df = tdx.parse_file(REAL_RAW / "sh" / "lday" / "sh000300.day")
    assert df.iloc[0]["ts"] == "2005-01-04"
    assert df.iloc[-1]["ts"] == "2026-09-30"
    assert df.iloc[-1]["close"] == 4357.62
