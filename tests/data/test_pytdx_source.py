"""`pytdx` 源：翻页方向、指数走专用接口、口径拒收。

**不联网**：`_connect()` 是唯一的缝合点，这里替换成一个假 API。

三个坑都是实测踩出来的，各自配一条用例：
① **页内 `[0]` 是最早的一根**（升序），不是最新的 —— 用 `bars[-1]` 判断会让翻页
   立刻退出、只拿到一页；
② **指数必须用 `get_index_bars`**，`get_security_bars` 对 `market=1` 字段错位，
   给出 `2004-11-41`、`236757-22-72` 这种时间戳，**而且不报错**；
③ **在线行情只有不复权** —— 传前复权要当场拒收，不能假装能给。
"""
from __future__ import annotations

import pytest

from chanlun.data import pytdx_source as P
from chanlun.data.types import DataSourceError


class _FakeAPI:
    """按 `start` 从最新往旧切片；**页内升序**（与真服务器一致）。"""

    def __init__(self, total: int = 2000, page: int = 800):
        self.total, self.page = total, page
        self.calls: list[tuple[str, int, int, int]] = []

    def _slice(self, kind, cat, market, code, start, count):
        self.calls.append((kind, cat, market, start))
        end = min(start + count, self.total)
        idx = list(range(start, end))[::-1]      # 0 = 最新，故倒序
        return [self._bar(i) for i in idx]

    def _bar(self, i):
        n = self.total - i                        # 越大越新
        day = 1 + (n // 240) % 28
        hh, mm = 9 + (n % 240) // 60, (n % 60) // 1
        return {
            "datetime": f"2026-06-{day:02d} {hh:02d}:{mm:02d}",
            "open": 10.0, "high": 10.4, "low": 9.8, "close": 10.2,
            "vol": 100.0, "amount": 1000.0,
        }

    def get_security_bars(self, cat, market, code, start, count):
        return self._slice("security", cat, market, code, start, count)

    def get_index_bars(self, cat, market, code, start, count):
        return self._slice("index", cat, market, code, start, count)


@pytest.fixture()
def fake(monkeypatch):
    api = _FakeAPI()
    monkeypatch.setattr(P, "_connect", lambda: api)
    monkeypatch.setattr(P, "reset", lambda: None)
    return api


def test_pages_backwards_until_the_window_start(fake):
    df = P.fetch_bars("sh.600030", "sh.600030", "1", "2026-01-01", "2026-12-31")
    assert len(df) == 2000, "应当翻满（800 + 800 + 400）"
    assert list(df["ts"]) == sorted(df["ts"]), "结果必须按时间升序"
    assert len(fake.calls) >= 3, "页内 [0] 是最早一根时，翻页要能继续"


def test_index_uses_the_dedicated_endpoint(fake):
    """★ 指数走 `get_index_bars`。

    `get_security_bars` 对 `market=1` 字段错位，给出 `2004-11-41 18:54` 这种
    时间戳 —— **而且不报错**。所以「调用成功」在这里毫无意义，必须看走了哪个方法。
    """
    P.fetch_bars("sh.000300", "sh.000300", "1", "2026-01-01", "2026-12-31")
    kinds = {c[0] for c in fake.calls}
    assert kinds == {"index"}, f"指数走了 {kinds}"


def test_stock_uses_the_general_endpoint(fake):
    P.fetch_bars("sh.600030", "sh.600030", "1", "2026-01-01", "2026-12-31")
    assert {c[0] for c in fake.calls} == {"security"}


@pytest.mark.parametrize("adjust", ["2", "1"])
def test_adjust_other_than_raw_is_refused(fake, adjust):
    """在线行情只有不复权 —— **拒收**，不假装能给（D-19）。"""
    with pytest.raises(DataSourceError, match="只提供不复权"):
        P.fetch_bars("sh.600030", "sh.600030", "1", "2026-01-01", "2026-12-31",
                     adjust=adjust)


def test_unsupported_period_and_market_are_refused(fake):
    with pytest.raises(DataSourceError, match="不支持周期"):
        P.fetch_bars("sh.600030", "sh.600030", "3", "2026-01-01", "2026-12-31")
    with pytest.raises(DataSourceError, match="不支持市场"):
        P.fetch_bars("hk.hsi", "hk.hsi", "1", "2026-01-01", "2026-12-31")


def test_empty_response_yields_an_empty_frame(monkeypatch):
    monkeypatch.setattr(P, "_connect", lambda: _FakeAPI(total=0))
    df = P.fetch_bars("sh.600030", "sh.600030", "1", "2026-01-01", "2026-12-31")
    assert df.empty


def test_a_single_day_window_is_clipped(monkeypatch):
    monkeypatch.setattr(P, "_connect", lambda: _FakeAPI(total=2000))
    df = P.fetch_bars("sh.600030", "sh.600030", "1", "2026-06-01", "2026-06-01")
    assert len(df) <= 2000
    assert all(str(t).startswith("2026-06-01") for t in df["ts"])


def test_category_mapping_and_bare_index_detection():
    assert P.CATEGORY["1"] == 7 and P.CATEGORY["5"] == 0 and P.CATEGORY["day"] == 4
    assert "day" not in P.MINUTE_PERIODS, "日线不走这个源（baostock 有全历史）"
    # 深市指数的落库键是**裸码**，判指数要补回市场前缀
    assert P._is_index("399001", "sz", "399001") is True
    assert P._is_index("000001", "sz", "000001") is False, "sz.000001 是平安银行"
