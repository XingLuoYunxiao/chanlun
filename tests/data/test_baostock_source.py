"""baostock 数据源集成测试（需要网络）。"""

import pandas as pd
import pytest

from chanlun.data.baostock_source import (
    DataSourceError,
    fetch_bars,
    period_to_frequency,
    to_bs_code,
)
from chanlun.data.types import COLUMNS


def test_to_bs_code():
    assert to_bs_code("600000") == "sh.600000"
    assert to_bs_code("000001") == "sz.000001"
    assert to_bs_code("300750") == "sz.300750"
    assert to_bs_code("688981") == "sh.688981"
    assert to_bs_code("830799") == "bj.830799"
    assert to_bs_code("sh.600000") == "sh.600000"


def test_period_to_frequency():
    assert period_to_frequency("day") == "d"
    assert period_to_frequency("30") == "30"
    assert period_to_frequency("5") == "5"
    with pytest.raises(ValueError):
        period_to_frequency("7")


def test_fetch_day_has_expected_columns_and_order():
    df = fetch_bars("sh.600000", "day", "2024-01-01", "2024-03-01")
    assert list(df.columns) == COLUMNS
    assert df["ts"].is_monotonic_increasing and df["ts"].is_unique
    assert (df["high"] >= df["low"]).all()
    assert df["ts"].iloc[0].startswith("2024-01-")
    assert " " not in df["ts"].iloc[0]


def test_fetch_day_reaches_ipo():
    df = fetch_bars("sh.600000", "day", "1990-01-01", "2026-09-29")
    assert len(df) > 6000
    assert df["ts"].iloc[0] <= "1999-11-11"


def test_fetch_30min_has_space_in_ts_and_sufficient_depth():
    df = fetch_bars("sh.600000", "30", "2020-01-01", "2020-02-29")
    assert len(df) >= 100  # 东财同期只有 256 根/16 天，此断言确保走 baostock
    assert " " in df["ts"].iloc[0]
    assert df["ts"].iloc[0].endswith(":00") or df["ts"].iloc[0].endswith(":30")


def test_fetch_5min_depth():
    df = fetch_bars("sh.600000", "5", "2024-01-02", "2024-01-31")
    assert len(df) >= 200  # 约 48 根/天 × 约 20 天


def test_adjust_flag_changes_values_but_not_length():
    raw = fetch_bars("sh.600000", "day", "2024-01-01", "2024-06-30", adjust="3")
    adj = fetch_bars("sh.600000", "day", "2024-01-01", "2024-06-30", adjust="2")
    assert len(raw) == len(adj)
    # 前复权锚定当前价，远期价格必被改写；600000 长期分红，差异应显著
    assert not raw["close"].equals(adj["close"])


def test_empty_range_returns_empty_frame_not_error():
    df = fetch_bars("sh.600000", "day", "1990-01-01", "1990-01-05")
    assert len(df) == 0
    assert list(df.columns) == COLUMNS
