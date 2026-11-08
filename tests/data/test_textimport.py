"""文本导出导入：布局要宽容，语义要严格。

**为什么这个模块存在**：通达信 macOS 版没有 vipdoc（见 D-09 变更历史），
而它有导出。所以「客户端导出 → 这里导入」是 macOS 上拿本地 K 线的现实路径。
导出格式没有样本，所以这里每一组「宽容」都配一组「必须报错」。
"""
from __future__ import annotations

import pytest

from chanlun.data import textimport as ti


# ---------------------------------------------------------------- 布局宽容
def test_chinese_daily_with_a_title_line():
    txt = (
        "600030 中信证券 日线 前复权\n"
        "日期,开盘,最高,最低,收盘,成交量,成交额\n"
        "2026-10-12,25.90,26.10,25.80,26.00,123456,1234567.00\n"
        "2026-10-13,26.00,26.20,25.95,26.15,223456,2234567.00\n"
    )
    df = ti.parse_text(txt)
    assert list(df["ts"]) == ["2026-10-12", "2026-10-13"]
    assert list(df["close"]) == [26.00, 26.15]
    assert list(df["amount"]) == [1234567.0, 2234567.0]


def test_separate_date_and_time_columns_make_a_minute_ts():
    txt = (
        "日期,时间,开盘,最高,最低,收盘,成交量\n"
        "2026-10-13,0935,25.90,26.10,25.80,26.00,1234\n"
        "2026-10-13,0940,26.00,26.20,25.95,26.15,2234\n"
    )
    df = ti.parse_text(txt)
    assert list(df["ts"]) == ["2026-10-13 09:35", "2026-10-13 09:40"]


@pytest.mark.parametrize("head", ["date,open,high,low,close", "日期\t开盘\t最高\t最低\t收盘",
                                  "DATE;OPEN;HIGH;LOW;CLOSE", "日期 开盘 最高 最低 收盘"])
def test_delimiters_and_english_headers(head):
    sep = "\t" if "\t" in head else (";" if ";" in head else (" " if " " in head else ","))
    body = sep.join(["2026-10-13", "1.0", "1.2", "0.9", "1.1"])
    df = ti.parse_text(head + "\n" + body)
    assert len(df) == 1 and float(df["close"].iloc[0]) == 1.1


def test_columns_are_taken_by_name_not_by_position():
    """**列序被打乱也必须对。** 按位置取会静默串列（把最高当收盘用，谁也不报错）。"""
    txt = "收盘,日期,最低,最高,开盘\n26.00,2026-10-13,25.80,26.10,25.90"
    df = ti.parse_text(txt)
    row = df.iloc[0]
    assert (row["open"], row["high"], row["low"], row["close"]) == (25.90, 26.10, 25.80, 26.00)


def test_compact_date_and_slash_date():
    assert list(ti.parse_text("日期,开盘,最高,最低,收盘\n20261013,1,2,0.5,1.5")["ts"]) == ["2026-10-13"]
    assert list(ti.parse_text("日期,开盘,最高,最低,收盘\n2026/10/13,1,2,0.5,1.5")["ts"]) == ["2026-10-13"]


def test_blank_lines_are_skipped():
    txt = "日期,开盘,最高,最低,收盘\n\n2026-10-13,1,2,0.5,1.5\n\n"
    assert len(ti.parse_text(txt)) == 1


def test_gb18030_encoding_is_accepted(tmp_path):
    """Windows 版通达信导出是 GBK —— 按 utf-8 读会 UnicodeDecodeError。"""
    p = tmp_path / "e.txt"
    p.write_bytes("日期,开盘,最高,最低,收盘\n2026-10-13,1,2,0.5,1.5\n".encode("gb18030"))
    assert len(ti.load(p)) == 1


# ---------------------------------------------------------------- 语义严格
def test_missing_close_column_is_an_error():
    with pytest.raises(ti.TextImportError, match="没找到表头"):
        ti.parse_text("日期,开盘,最高,最低\n2026-10-13,1,2,0.5")


def test_a_file_without_any_header_is_an_error():
    with pytest.raises(ti.TextImportError, match="没找到表头"):
        ti.parse_text("1,2,3,4,5\n6,7,8,9,10")


def test_incoherent_ohlc_is_an_error_not_a_silent_write():
    """收盘 9.9 远超最高 1.1 → 必须报错。

    这条是**列名对上但含义不对**的典型症状（比如把成交量当成了收盘）。
    放它过去就会把一份错行情写进库，而且看起来「导入成功」。
    """
    with pytest.raises(ti.TextImportError, match="OHLC 不自洽"):
        ti.parse_text("日期,开盘,最高,最低,收盘\n2026-10-13,1.0,1.1,0.9,9.9")


def test_unparsable_date_on_a_data_looking_row_is_an_error():
    """开盘点得出数字、日期却认不出 → 报错。

    不能默默跳过：那会**静默少几根 K 线**，而递归照样算得出结构。
    """
    with pytest.raises(ti.TextImportError, match="认不出日期"):
        ti.parse_text("日期,开盘,最高,最低,收盘\n2026年10月13日,1,2,0.5,1.5")


def test_a_header_followed_by_nothing_is_an_error():
    with pytest.raises(ti.TextImportError, match="没有一行能解成 K 线"):
        ti.parse_text("日期,开盘,最高,最低,收盘\n")


def test_non_numeric_price_is_an_error():
    with pytest.raises(ti.TextImportError, match="不是数字"):
        ti.parse_text("日期,开盘,最高,最低,收盘\n2026-10-13,abc,2,0.5,1.5")


def test_empty_input_is_an_error():
    with pytest.raises(ti.TextImportError):
        ti.parse_text("")


# ------------------------------------------------ 日期列没有列名（通达信导出那种）
# 空白分隔 + 日期列表头为空，是通达信导出的实际形态。两个坑叠在一起：
# ① 表头里没有日期列名；② `split()` 会吃掉那个空表头，导致后面列名**整体左移一位**。
_TDX_STYLE = (
    "600030 中信证券 日线 前复权\n"
    "        开盘    最高    最低    收盘    成交量    成交额\n"
    "2026-10-12  25.90  26.10  25.80  26.00  123456  1234567.00\n"
    "2026-10-13  26.00  26.20  25.95  26.15  223456  2234567.00\n"
)


def test_unlabeled_date_column_is_found_by_content():
    df = ti.parse_text(_TDX_STYLE)
    assert list(df["ts"]) == ["2026-10-12", "2026-10-13"]
    # ★ 列名整体左移的话，`开盘` 会落到日期那一列上、后面全部错位。
    # 这里逐列核对，因为「错位」和「对」看起来都像导入成功。
    assert list(df["open"]) == [25.90, 26.00]
    assert list(df["high"]) == [26.10, 26.20]
    assert list(df["low"]) == [25.80, 25.95]
    assert list(df["close"]) == [26.00, 26.15]
    assert list(df["volume"]) == [123456.0, 223456.0]


def test_comma_delimited_empty_date_header_needs_no_shift():
    """逗号分隔时空字段会被保留成 `""`，列号本来就对 —— **不能**再移一位。"""
    txt = ",开盘,最高,最低,收盘\n2026-10-13,1.0,1.2,0.9,1.1"
    df = ti.parse_text(txt)
    assert float(df["close"].iloc[0]) == 1.1


def test_an_extra_leading_column_that_is_not_dates_is_refused():
    """多一列但首列不是日期 → **不许**猜着移，如实报错。"""
    with pytest.raises(ti.TextImportError, match="找不到日期列"):
        ti.parse_text("开盘,最高,最低,收盘\nX,1.0,1.2,0.9\nY,1.1,1.3,1.0")


def test_a_single_date_row_is_not_enough_evidence():
    """只有一行像日期不足以确认那一列是日期列 —— 一行巧合不算证据。"""
    with pytest.raises(ti.TextImportError, match="找不到日期列"):
        ti.parse_text("开盘,最高,最低,收盘\n2026-10-13,1.0,1.2,0.9,1.1")
