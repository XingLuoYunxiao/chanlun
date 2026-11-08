"""东方财富分钟源：解析与本地裁剪。

**不联网**：`_get_json` 是唯一的取数缝合点（与 `sw_source` 同一约定）。

重点钉三件事，它们各自都对应一次真实的错：

1. **字段顺序是「开、收、高、低」**，不是常见的「开高低收」。这个在真实响应上
   逐条核过：按「开收高低」代入三条实测数据全部满足
   `low <= min(开,收) <= max(开,收) <= high`，按「开高低收」全部不满足。
   顺序搞错不会报错，只会静默产出一份高低颠倒的行情。
2. **`secid` 的前缀**：沪 `1.`、深 `0.`、申万 `90.` —— 板块用 `0.`/`1.` 都回
   `data=null`，所以这条不能靠猜。
3. **本地裁剪的日期边界**：东财只认 `end`、不认 `start`，`start`/`end` 全靠本地过滤；
   而 `end` 常常是**裸日期** `YYYY-MM-DD`，字典序上 `"2026-10-13 09:31" > "2026-10-13"`,
   直接比会把**当天**整批丢掉。
"""
from __future__ import annotations

import pytest

from chanlun.data import eastmoney_source as em
from chanlun.data.types import DataSourceError


def _payload(rows: list[str]) -> dict:
    return {"data": {"code": "600030", "market": 1, "name": "中信证券", "klines": rows}}


# 真实响应原样抄下来的三条（2026-10-13 实测），顺序：时间,开,收,高,低,量,额
_REAL = [
    "2026-10-13 14:50,25.93,25.91,25.94,25.88,17689,45818323.00",
    "2026-10-13 14:55,25.90,25.94,25.95,25.88,19890,51541926.00",
    "2026-10-13 15:00,25.95,25.99,25.99,25.93,33463,86906560.00",
]


@pytest.fixture()
def fake(monkeypatch):
    def _install(rows):
        monkeypatch.setattr(em, "_get_json", lambda url, timeout=30: _payload(rows))
    return _install


def test_field_order_is_open_close_high_low(fake):
    """按「开收高低」解出来的三条必须逐条满足 OHLC 自洽。"""
    fake(_REAL)
    df = em.fetch_bars("sh.600030", "1.600030", "5", "2026-10-13", "2026-10-13")
    assert list(df["ts"]) == ["2026-10-13 14:50", "2026-10-13 14:55", "2026-10-13 15:00"]
    assert list(df["open"]) == [25.93, 25.90, 25.95]
    assert list(df["close"]) == [25.91, 25.94, 25.99]     # 收在 f53
    assert list(df["high"]) == [25.94, 25.95, 25.99]      # 高在 f54
    assert list(df["low"]) == [25.88, 25.88, 25.93]       # 低在 f55
    assert list(df["volume"]) == [17689.0, 19890.0, 33463.0]
    assert list(df["amount"]) == [45818323.0, 51541926.0, 86906560.0]
    ok = ((df["low"] <= df[["open", "close"]].min(axis=1))
          & (df[["open", "close"]].max(axis=1) <= df["high"])).all()
    assert ok


def test_bare_date_end_keeps_the_same_day(fake):
    """`end` 是裸日期时，**当天**的分钟线必须留下。

    字典序上 `"2026-10-13 09:31" > "2026-10-13"`，直接 `<=` 会把当天整批丢掉，
    而且看起来像「这个周期没数据」。`data/types.py::truncate` 有同一个坑。
    """
    fake(_REAL)
    df = em.fetch_bars("sh.600030", "1.600030", "5", "2026-10-13", "2026-10-13")
    assert len(df) == 3, "裸日期 end 把当天的行丢掉了"


def test_date_window_filters_outside_rows(fake):
    fake(_REAL)
    df = em.fetch_bars("sh.600030", "1.600030", "5", "2026-10-14", "2026-10-20")
    assert df.empty, "窗口外的行没有被裁掉"
    df2 = em.fetch_bars("sh.600030", "1.600030", "5", "2026-10-13 14:52", "2026-10-13 15:00")
    assert list(df2["ts"]) == ["2026-10-13 14:55", "2026-10-13 15:00"]


def test_null_data_returns_empty_frame(fake):
    """东财对「不认识的 secid」与「该周期无数据」都回 `data=null` —— 不猜是哪种。"""
    fake([])
    assert em.fetch_bars("sh.000999", "1.000999", "5", "2026-10-01", "2026-10-13").empty


def test_secid_prefixes():
    """沪 `1.`、深 `0.`。"""
    assert em.secid("sh.600030") == "1.600030"
    assert em.secid("sh.000001") == "1.000001"
    assert em.secid("sz.000001") == "0.000001"
    assert em.secid("399001") == "0.399001"          # 深市指数的落库键是**裸码**


def test_secid_refuses_sw_boards():
    """**申万不能用东财的 `90.` 前缀** —— 那是东财自己的板块空间，同号不同物。

    曾经把 `sw.801010` 映射成 `90.801010` 并当作申万导入了 96 个文件；
    实测 `90.801010` 回的是「磷化工」而不是「农林牧渔」。护栏钉在这里：
    这个映射必须**做不到**，而不只是「调用方不去调」。
    """
    with pytest.raises(DataSourceError, match="不支持申万"):
        em.secid("sw.801010")


def test_secid_rejects_unknown_market():
    with pytest.raises(DataSourceError):
        em.secid("hk.hsi")


def test_unknown_period_and_adjust_are_rejected(fake):
    fake(_REAL)
    with pytest.raises(DataSourceError):
        em.fetch_bars("sh.600030", "1.600030", "week", "2026-10-13", "2026-10-13")
    with pytest.raises(DataSourceError):
        em.fetch_bars("sh.600030", "1.600030", "5", "2026-10-13", "2026-10-13", adjust="9")


def test_network_failure_becomes_datasourceerror(monkeypatch):
    def boom(url, timeout=30):
        raise OSError("connection reset")
    monkeypatch.setattr(em, "_get_json", boom)
    with pytest.raises(DataSourceError, match="东财取数失败"):
        em.fetch_bars("sh.600030", "1.600030", "5", "2026-10-13", "2026-10-13")


def test_klt_mapping_covers_documented_periods():
    assert em.KLT == {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60", "day": "101"}
    assert em.FQT["2"] == "1" and em.FQT["raw"] == "0" and em.FQT["3"] == "0"
