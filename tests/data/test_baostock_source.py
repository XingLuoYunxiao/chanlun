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
    assert to_bs_code("SZ.300059") == "sz.300059"


def test_to_bs_code_rejects_wrong_exchange_prefix():
    """写错交易所的代码 baostock 不报错、只返回零行，必须在入口拦住。"""
    with pytest.raises(ValueError, match="应为 sz.300059"):
        to_bs_code("sh.300059")
    with pytest.raises(ValueError, match="应为 sh.600000"):
        to_bs_code("sz.600000")
    with pytest.raises(ValueError, match="应为 bj.830799"):
        to_bs_code("sh.830799")
    with pytest.raises(ValueError, match="缺少数值部分"):
        to_bs_code("sh.")


def test_to_bs_code_allows_ambiguous_index_and_bond_numbers():
    """sh.000001 是上证指数、sz.000001 是平安银行，两者都合法，不得误杀。"""
    assert to_bs_code("sh.000001") == "sh.000001"
    assert to_bs_code("sz.000001") == "sz.000001"
    assert to_bs_code("sh.113050") == "sh.113050"
    assert to_bs_code("sz.123456") == "sz.123456"


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


# ---------------------------------------------------------------- 静默空数据防护
# 实测 baostock 会话异常时会以 error_code="0" 返回零行。若直接采信，
# 下游会安静地认为「该股无信号」。以下用假客户端离线验证重试行为。


class _FakeRS:
    def __init__(self, rows, error_code="0", error_msg=""):
        self._rows = list(rows)
        self.error_code = error_code
        self.error_msg = error_msg
        self._i = -1

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def get_row_data(self):
        return self._rows[self._i]


class _FakeBS:
    def __init__(self, results):
        self._results = list(results)
        self.queries = 0
        self.logins = 0

    def login(self):
        self.logins += 1
        return _FakeRS([], "0", "")

    def logout(self):
        pass

    def query_history_k_data_plus(self, *args, **kwargs):
        rs = self._results[min(self.queries, len(self._results) - 1)]
        self.queries += 1
        return rs


@pytest.fixture
def fake_bs(monkeypatch):
    from chanlun.data import baostock_source as bst

    monkeypatch.setattr(bst, "RETRY_SLEEP", 0)
    monkeypatch.setattr(bst, "_LOGGED_IN", False)
    return bst


def _wire(monkeypatch, bst, fake):
    monkeypatch.setattr(bst, "bs", fake)
    return fake


def test_empty_result_is_retried_and_relogged(fake_bs, monkeypatch):
    fake = _wire(monkeypatch, fake_bs, _FakeBS([
        _FakeRS([]),                                                    # 静默空
        _FakeRS([["2024-01-02", "1", "2", "0.5", "1.5", "10", "100"]]),  # 重登后正常
    ]))
    df = fake_bs.fetch_bars("sh.600000", "day", "2024-01-01", "2024-01-05")
    assert len(df) == 1
    assert fake.queries == 2
    assert fake.logins == 2   # 空结果触发了重登


def test_all_attempts_empty_returns_empty_frame_not_error(fake_bs, monkeypatch):
    fake = _wire(monkeypatch, fake_bs, _FakeBS([_FakeRS([])]))
    df = fake_bs.fetch_bars("sh.600000", "day", "1990-01-01", "1990-01-05")
    assert len(df) == 0
    assert list(df.columns) == COLUMNS
    assert fake.queries == fake_bs.RETRIES


def test_nonzero_error_code_still_raises_after_retries(fake_bs, monkeypatch):
    _wire(monkeypatch, fake_bs, _FakeBS([_FakeRS([], "10001001", "用户未登录")]))
    with pytest.raises(DataSourceError):
        fake_bs.fetch_bars("sh.600000", "day", "2024-01-01", "2024-01-05")
