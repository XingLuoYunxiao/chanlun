"""baostock 数据源集成测试（需要网络）。"""

import socket

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


def test_to_bs_code_accepts_renumbered_bj_and_shanghai_board():
    """北交所改号 920xxx 与沪市板块 88xxxx 都是真实存在的号段，不得误杀。"""
    assert to_bs_code("920017") == "bj.920017"
    assert to_bs_code("bj.920017") == "bj.920017"
    assert to_bs_code("sh.880001") == "sh.880001"
    with pytest.raises(ValueError, match="应为 bj.920017"):
        to_bs_code("sh.920017")


def test_period_to_frequency():
    assert period_to_frequency("day") == "d"
    assert period_to_frequency("30") == "30"
    assert period_to_frequency("5") == "5"
    with pytest.raises(ValueError):
        period_to_frequency("7")


@pytest.mark.live
def test_fetch_day_has_expected_columns_and_order():
    df = fetch_bars("sh.600000", "day", "2024-01-01", "2024-03-01")
    assert list(df.columns) == COLUMNS
    assert df["ts"].is_monotonic_increasing and df["ts"].is_unique
    assert (df["high"] >= df["low"]).all()
    assert df["ts"].iloc[0].startswith("2024-01-")
    assert " " not in df["ts"].iloc[0]


@pytest.mark.live
def test_fetch_day_reaches_ipo():
    df = fetch_bars("sh.600000", "day", "1990-01-01", "2026-09-29")
    assert len(df) > 6000
    assert df["ts"].iloc[0] <= "1999-11-11"


@pytest.mark.live
def test_fetch_30min_has_space_in_ts_and_sufficient_depth():
    df = fetch_bars("sh.600000", "30", "2020-01-01", "2020-02-29")
    assert len(df) >= 100  # 东财同期只有 256 根/16 天，此断言确保走 baostock
    assert " " in df["ts"].iloc[0]
    assert df["ts"].iloc[0].endswith(":00") or df["ts"].iloc[0].endswith(":30")


@pytest.mark.live
def test_fetch_5min_depth():
    df = fetch_bars("sh.600000", "5", "2024-01-02", "2024-01-31")
    assert len(df) >= 200  # 约 48 根/天 × 约 20 天


@pytest.mark.live
def test_adjust_flag_changes_values_but_not_length():
    raw = fetch_bars("sh.600000", "day", "2024-01-01", "2024-06-30", adjust="3")
    adj = fetch_bars("sh.600000", "day", "2024-01-01", "2024-06-30", adjust="2")
    assert len(raw) == len(adj)
    # 前复权锚定当前价，远期价格必被改写；600000 长期分红，差异应显著
    assert not raw["close"].equals(adj["close"])


@pytest.mark.live
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


# ---------------------------------------------------------------- socket 超时防护
# baostock 的 socket 是**裸创建**的（`baostock/util/socketutil.py` 里直接
# `socket.socket(...)`，从不 `settimeout`）。服务端接受连接后不回包时，
# `send_msg` 里的 `recv` 会**永久**阻塞 —— 上面的重试只在「拿到响应之后」才生效。
# 2026-10-08 的定时任务就是这样卡了 6 小时 14 分：主线程栈停在 `sock_recv`，
# 累计 CPU 只有 2.8 秒，落库停在 17:36:32 的第 26 只票。


class _SocketSpyBS(_FakeBS):
    """在 `login()` 里裸建一个 socket，量「baostock 建出来的 socket 有没有超时」。

    这里必须真的建 socket：断言 `socket.getdefaulttimeout()` 只能证明我们设过，
    证明不了 baostock 那个**常驻 socket 自己**带上了超时。
    """

    def __init__(self, results):
        super().__init__(results)
        self.probe = None
        self.default_during_login = None

    def login(self):
        self.probe = socket.socket()  # 与 baostock 内部同样的裸创建
        self.default_during_login = socket.getdefaulttimeout()
        return _FakeRS([], "0", "")


def test_login_socket_carries_timeout_and_does_not_leak_process_default(fake_bs, monkeypatch):
    fake = _wire(monkeypatch, fake_bs, _SocketSpyBS([_FakeRS([])]))
    before = socket.getdefaulttimeout()
    try:
        fake_bs.fetch_bars("sh.600000", "day", "1990-01-01", "1990-01-05")
        # 先钉「真的设过」，再钉「设成了 SOCKET_TIMEOUT」。
        # 少了前一条，这个测试会**自证**：把 `SOCKET_TIMEOUT` 改成 `None`
        # 之后 `None == None` 照样绿（2026-10-09 用变异实测确认过）。
        assert fake.default_during_login is not None, "登录期间必须真的设过超时"
        assert fake.default_during_login == fake_bs.SOCKET_TIMEOUT
        # 常驻 socket 自己带着超时：恢复进程默认值**不会**把它改回阻塞
        assert fake.probe.gettimeout() is not None, "常驻 socket 必须自己带着超时"
        assert fake.probe.gettimeout() == fake_bs.SOCKET_TIMEOUT
        # 不污染进程默认值 —— 看盘页进程里 uvicorn 等其它 socket 不受影响
        assert socket.getdefaulttimeout() == before
    finally:
        if fake.probe is not None:
            fake.probe.close()


def test_socket_timeout_is_a_finite_budget_not_a_hang():
    """超时必须是个**有限正数**；`None` 就是本次事故的形状。"""
    from chanlun.data import baostock_source as bst

    assert bst.SOCKET_TIMEOUT is not None
    assert 0 < bst.SOCKET_TIMEOUT <= 120
