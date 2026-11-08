"""市场分派层（spec §7.1~§7.3 / D-43）。

`sources.py` 是**唯一**的取数入口：`sync` / `web` / CLI 都从这里拿 `fetch_bars` 与
`normalize_code`。这一层每一条错法都是静默的 —— 符号写错会拿到空数据、
控制器选错会把一种口径的价格当成另一种存、前缀不校验会把港股写到深市目录。
所以这里逐条钉住，而不是靠集成测试偶然覆盖。

网络一律不碰：`_fetch_one` 被换成桩，只断言**递下去的参数**与**聚合后的形状**。
"""

from __future__ import annotations

import pandas as pd
import pytest

from chanlun.data import sources
from chanlun.data.sources import fallback_for
from chanlun.data.types import DataSourceError


def _bars(ts_list: list[str], base: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": ts_list,
            "open": [base + i for i in range(len(ts_list))],
            "high": [base + i + 0.5 for i in range(len(ts_list))],
            "low": [base + i - 0.5 for i in range(len(ts_list))],
            "close": [base + i + 0.2 for i in range(len(ts_list))],
            "volume": [1000.0 + i for i in range(len(ts_list))],
            "amount": [1e5 + i for i in range(len(ts_list))],
        }
    )


# ---------------- normalize_code：用户写法 → 落库键 ----------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600000", "600000"),
        ("sh.600000", "600000"),
        ("sh600000", "600000"),
        ("SH.600000", "600000"),
        ("sh.000001", "sh.000001"),  # 上证指数：裸 `000001` 会被判成深市，必须留前缀
        ("sz.000001", "000001"),  # 平安银行：裸码本来就指向深市，前缀冗余 → 去掉
        ("sz.399001", "399001"),  # 深证成指：`39` 号段唯一指向深市，同样去掉前缀
        ("sz.301439", "301439"),
        ("hk.00700", "hk.00700"),
        ("hk.HSI", "hk.hsi"),
        ("hk00700", "hk.00700"),
        ("hk.hstech", "hk.hstech"),
        ("us.DJI", "us.dji"),
        ("us.aapl", "us.aapl"),
        ("usdji", "us.dji"),
    ],
)
def test_normalize_code_accepts_documented_spellings(raw, expected):
    assert sources.normalize_code(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "xx.600000",  # 未知前缀
        "hk.",  # 缺证券部分
        "00700",  # 港股裸码不许猜（会被 market_of_bare 兜底成深市）
        "hk.700",  # 港股必须 5 位
        "us.123",  # 美股必须字母开头
        "300",  # 既不是 6 位 A 股也不是带前缀的港股/美股
        "sh.300059",  # 前缀与号段矛盾
    ],
)
def test_normalize_code_rejects_unknown_spellings(raw):
    """**只抛 `DataSourceError`**：API 层靠它把 400 和 500 分开。

    `to_bs_code` 抛的是 `ValueError`；若让它漏出去，「前缀与号段矛盾」
    （`sh.300059`）会绕过 400 直接变成 500（spec §8）。
    """
    with pytest.raises(DataSourceError):
        sources.normalize_code(raw)


def test_store_key_for_is_the_same_implementation():
    """`store_key_for` 只是 `normalize_code` 的别名 —— 口径只允许有一份实现。"""
    assert sources.store_key_for("hk.HSI") == sources.normalize_code("hk.HSI") == "hk.hsi"


# ---------------- vendor_symbol：落库键 → 数据商写法 ----------------
@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("600000", ("baostock", "sh.600000")),
        ("sh.000001", ("baostock", "sh.000001")),
        ("hk.00700", ("tencent", "hk00700")),
        ("hk.hsi", ("tencent", "hkHSI")),  # 坑 2：腾讯要大写
        ("hk.hstech", ("tencent", "hkHSTECH")),
        ("us.dji", ("sina", ".DJI")),  # 坑 3：新浪美股**指数**必须带点
        ("us.aapl", ("sina", "AAPL")),  # 个股不带点
    ],
)
def test_vendor_symbol_maps_every_market(key, expected):
    assert sources.vendor_symbol(key) == expected


def test_market_of_uses_the_store_table_not_the_segment_table():
    """坑 1：`markets.market_of` 不认前缀，对 `hk.00700` 会返回 `"sz"`。

    用错会把港股数据写进 `data/day/sz/00700.parquet`（spec §7.2 坑 1）。
    """
    assert sources.market_of("hk.00700") == "hk"
    assert sources.market_of("us.dji") == "us"


def test_source_of_rejects_an_unknown_market():
    with pytest.raises(DataSourceError):
        sources.source_of("jp.1234")


# ---------------- adjust_for：口径收敛 ----------------
@pytest.mark.parametrize(
    ("key", "asked", "expected"),
    [
        ("600000", "2", "2"),  # A 股原样
        ("600000", "1", "1"),
        ("600000", "3", "3"),
        ("sh.000001", "2", "3"),  # 指数：除权折算对指数不成立（D-19）
        ("hk.00700", "2", "3"),  # 港股只有原始价
        ("hk.hsi", "1", "3"),
        ("us.dji", "2", "3"),  # 新浪美股只有原始价
        ("us.aapl", "2", "3"),
    ],
)
def test_adjust_for_converges_to_what_the_market_actually_has(key, asked, expected):
    """收敛而不是报错：调用方默认传 A 股的 `"2"`，不必知道每个市场的口径细节。"""
    assert sources.adjust_for(key, asked) == expected


# ---------------- endpoint：控制器 / 复权词 / 期望键名 ----------------
def test_endpoint_reports_the_tencent_raw_controller_for_hk():
    """腾讯把口径写在**控制器名**上：`fqkline` 忽略复权词并返回不复权价。

    选错的后果不是报错，是把一种口径的价格当成另一种存（spec §7.2）。
    """
    out = sources.endpoint("hk.00700", "day", "2")
    assert out["controller"] == "fqkline"
    assert out["expected_key"] == "day"


def test_endpoint_rejects_hk_minutes():
    """港股没有分钟线源（spec §3.2 实测）。"""
    with pytest.raises(DataSourceError):
        sources.endpoint("hk.00700", "30", "3")


def test_endpoint_is_a_passthrough_for_non_tencent_markets():
    out = sources.endpoint("600000", "day", "2")
    assert out == {"controller": "baostock", "adjust_word": "", "expected_key": ""}


# ---------------- capabilities：前端「灰掉 + 说明原因」 ----------------
def test_capabilities_hk_minutes_are_unsupported_with_a_user_facing_reason():
    cap = sources.capabilities("hk.00700", "30")
    assert cap["supported"] is False
    assert "分钟" in str(cap["reason"])


def test_capabilities_hk_day_carries_the_raw_price_note():
    cap = sources.capabilities("hk.00700", "day")
    assert cap["supported"] is True
    assert "不复权" in str(cap["note"])


def test_capabilities_us_minutes_report_the_depth_cap():
    cap = sources.capabilities("us.dji", "30")
    assert cap["supported"] is True
    assert cap["depth_cap"] == 1023
    assert "1023" in str(cap["note"])


def test_capabilities_baostock_is_always_supported():
    cap = sources.capabilities("600000", "30")
    assert cap["supported"] is True and cap["source"] == "baostock"


# ---------------- fetch_bars：派生周期一律本地聚合 ----------------
@pytest.mark.parametrize(
    ("key", "period", "base_period"),
    [
        ("600000", "week", "day"),  # ★ A 股也走聚合（2026-10-05 修正）
        ("sh.000001", "month", "day"),
        ("hk.hsi", "week", "day"),
        ("us.dji", "month", "day"),
    ],
)
def test_fetch_bars_aggregates_derived_periods_locally_for_every_market(
    monkeypatch, key, period, base_period
):
    """**派生周期一律先取日线再 `periods.aggregate`**（spec §6.1）。

    A 股原先走 baostock 的原生周/月线，但 `period_to_frequency("week")` 把
    `"week"` 原样当 `frequency` 发出去，baostock 回 `10004012 请求数据类型不正确`
    —— 那条路径从来没成功过（`data/` 下至今没有 `week/` 或 `month/` 目录）。
    看盘页本来也是读日线再聚合（`web/api.py` 的 `_read_bars`），
    库里再存一份原生周/月线只会变成第二个真相来源。
    """
    calls: list[dict[str, object]] = []

    def fake_fetch_one(k, src, symbol, p, start, end, eff):  # noqa: ANN001
        calls.append({"key": k, "src": src, "period": p, "eff": eff})
        return _bars(["2026-09-28", "2026-09-29", "2026-09-30"])

    monkeypatch.setattr(sources, "_fetch_one", fake_fetch_one)
    out = sources.fetch_bars(key, period, "2026-09-01", "2026-09-30")

    assert [c["period"] for c in calls] == [base_period], "派生周期必须先取日线"
    assert len(out) == 1, "同一周的三个交易日必须聚合成一根"
    assert str(out["ts"].iloc[0]) == "2026-09-30"
    # 聚合口径：开=首日开，高=最高，低=最低，收=末日收，量额=求和
    assert float(out["open"].iloc[0]) == 10.0
    assert float(out["close"].iloc[0]) == 12.2
    assert float(out["high"].iloc[0]) == 12.5
    assert float(out["low"].iloc[0]) == 9.5
    assert float(out["volume"].iloc[0]) == 1000.0 + 1001.0 + 1002.0


def test_fetch_bars_passes_the_converged_adjust_to_the_source(monkeypatch):
    """口径分派只发生在 `adjust_for`：源收到的是**收敛后**的值。"""
    seen: dict[str, object] = {}

    def fake_fetch_one(k, src, symbol, p, start, end, eff):  # noqa: ANN001
        seen.update(key=k, src=src, symbol=symbol, period=p, eff=eff)
        return _bars(["2026-09-30"])

    monkeypatch.setattr(sources, "_fetch_one", fake_fetch_one)
    sources.fetch_bars("hk.00700", "day", "2026-09-01", "2026-09-30", adjust="2")

    assert seen["src"] == "tencent"
    assert seen["symbol"] == "hk00700"
    assert seen["eff"] == "3", "港股只有原始价，调用方要前复权也要收敛成不复权"


# ---------------- 按时段分流：分钟线的源按**标的**不同（D-52） ----------------
@pytest.mark.parametrize("key, period, expected_src, expected_symbol", [
    # 股票 5 分钟：baostock 有 6.5 年，pytdx 只有 2 年 —— 不为「统一」把好的换差的
    ("sh.600030", "5", "baostock", "sh.600030"),
    ("sh.600030", "day", "baostock", "sh.600030"),
    # **1 分钟：只有 pytdx 有**（baostock 报 10004012、腾讯 320 根、新浪 1023 根、
    # 东财只留 1 个交易日）。这是本项目此前最大的数据空白。
    ("sh.600030", "1", "pytdx", "600030"),
    ("sh.000300", "1", "pytdx", "sh.000300"),
    # A 股指数分钟：baostock 一律 0 行；pytdx 比东财深得多（5 分钟 2 年 vs 2 个月）
    ("sh.000001", "5", "pytdx", "sh.000001"),
    ("sh.000300", "5", "pytdx", "sh.000300"),
    ("sz.399001", "5", "pytdx", "399001"),
    ("399001", "5", "pytdx", "399001"),              # 深市指数的落库键是裸码
    ("sh.000300", "day", "baostock", "sh.000300"),   # ★ 日线不动：baostock 有全历史
    # 申万行业板块：日线走腾讯板块接口；**分钟线没有源**，不参与分流
    # （东财的 `90.` 前缀不是申万，同号不同物 —— 见 D-52 的否决方案）
    ("sw.801010", "day", "sw", "pt01801010"),
    ("sw.801010", "5", "sw", "pt01801010"),
    # 北交所股票与港美不受影响
    ("bj.430017", "5", "baostock", "bj.430017"),
    ("hk.hsi", "5", "tencent", "hkHSI"),
    ("us.dji", "5", "sina", ".DJI"),
])
def test_source_for_routes_by_symbol_not_just_market(
    key, period, expected_src, expected_symbol
):
    assert sources.source_for(key, period) == expected_src
    # `vendor_symbol_for` 对 pytdx 返回**落库键**（`sh.600030` 归一后就是裸码 `600030`）——
    # pytdx 源内部按 `store.market_of` + `store.bare_code` 自己拆，不需要外部拼。
    assert sources.vendor_symbol_for(key, sources.source_for(key, period)) == expected_symbol


def test_bare_index_key_is_detected():
    """`sz.399001` 归一后是**裸码**，而 `markets.is_index` 对裸码一律 False ——
    漏了这一步深市指数会被判成股票、继续走 baostock 拿 0 行。"""
    from chanlun.data import markets
    assert markets.is_index("399001") is False, "前提：裸码判不出来"
    assert sources._is_index_key("399001") is True
    assert sources._is_index_key("000001") is False, "sz.000001 是平安银行，不是指数"


def test_sw_minutes_are_still_unsupported():
    """**申万的分钟线仍然没有源**，而且原因要写清「东财那个 90. 前缀不是申万」。

    这条曾经被写成 `supported=True, source="eastmoney"` —— 那是基于一个错映射
    （`sw.801010` → `90.801010`）。实测东财回的是「磷化工」而不是「农林牧渔」，
    按那个映射导进去的是张冠李戴的行情。护栏：`eastmoney_source.secid("sw.*")` 直接抛错。
    """
    cap = sources.capabilities("sw.801010", "5")
    assert cap["supported"] is False and cap["source"] == "sw"
    assert "90." in cap["reason"] and "不是申万" in cap["reason"]
    # 日线仍归腾讯板块接口
    assert sources.capabilities("sw.801010", "day")["source"] == "sw"


def test_eastmoney_never_gets_a_sw_code():
    """分流表里 `sw` 的分钟**不能**落到 eastmoney —— 落了就会去要一个错的 secid。"""
    from chanlun.data import eastmoney_source
    for period in ("1", "5", "15", "30", "60"):
        assert sources.source_for("sw.801010", period) != "eastmoney"
        with pytest.raises(Exception):
            eastmoney_source.secid("sw.801010")


def test_index_minutes_report_pytdx():
    """指数分钟从东财改判给 pytdx —— 深度差一个量级（2 年 vs 2 个月）。"""
    cap = sources.capabilities("sh.000300", "5")
    assert cap["supported"] is True and cap["source"] == "pytdx"
    assert sources.capabilities("sz.399001", "1")["source"] == "pytdx"


def test_stock_one_minute_goes_to_pytdx():
    """**1 分钟只有 pytdx 有** —— 这条曾经断言「不支持」，2026-10-13 反转。

    背景：此前 baostock 的分钟只有 60/30/15/5（传 `1` 报 `10004012`）、腾讯 320 根、
    新浪 1023 根、东财只留 1 个交易日，所以 1 分钟真的没有源。
    pytdx 实测能给股票 23520 根（约 4.5 个月）、指数 22320 根。
    """
    cap = sources.capabilities("600030", "1")
    assert cap["supported"] is True
    assert cap["source"] == "pytdx"
    assert "不复权" in cap["note"], "口径必须说清：在线行情只有不复权"


def test_capabilities_has_a_pytdx_branch():
    """★ 漏了这个分支时，港股/美股那两条兜底会把它当成美股，
    报出「美股不支持周期 1」这种驴唇不对马嘴的理由（实测踩过）。"""
    for key, period in (("sh.000300", "1"), ("sh.600030", "1"), ("sh.000300", "5")):
        cap = sources.capabilities(key, period)
        assert cap["source"] == "pytdx"
        assert "美股" not in str(cap.get("reason", ""))


@pytest.mark.parametrize("period", ["5", "15", "30", "60", "day", "week", "month"])
def test_stock_minute_periods_other_than_one_are_supported(period):
    """反向对照：别把「没有 1 分钟」误伤成「没有分钟」。"""
    assert sources.capabilities("600030", period)["supported"] is True


def test_index_one_minute_goes_to_pytdx():
    """指数 + 1 分钟**有**源，与股票同一个源（都只有 pytdx 有）。"""
    cap = sources.capabilities("sh.000300", "1")
    assert cap["supported"] is True and cap["source"] == "pytdx"


# ------------------------------------------- 主源零行时回退（2026-10-15）
def test_fallback_only_for_index_minutes_above_one_minute():
    """★ 回退**只在指数 5/15/30/60 分钟**上成立，别的一律 `None`。

    这不是保守，是三条实测事实：
    - **1 分钟的股票不回退**：东财的 1 分钟只留 1 个交易日（240 根），
      回退过去等于拿一天的数据冒充「1 分钟历史」；
    - **日线不回退**：各市场既有源都更好，pytdx 只有 8000 来根；
    - **股票分钟不回退**：它本来就走的 baostock（6.5 年 > pytdx 的 2 年）。
    """
    assert fallback_for("sh.000300", "5", "pytdx") == "eastmoney"
    assert fallback_for("sh.000300", "30", "pytdx") == "eastmoney"
    assert fallback_for("399001", "5", "pytdx") == "eastmoney"     # 深市指数（裸码）
    assert fallback_for("sh.000300", "1", "pytdx") is None
    assert fallback_for("600030", "5", "pytdx") is None
    assert fallback_for("sh.000300", "day", "pytdx") is None
    assert fallback_for("sh.000300", "5", "eastmoney") is None


def test_zero_rows_from_primary_falls_back_and_says_so(monkeypatch, caplog):
    """主源「**连得上但零行**」时必须回退，且**把回退说出来**。

    触发这个的真事：pytdx 的行情服务器会接受连接却返回零行（限流）。
    `source_for` 的判据是 `pytdx_source.available()` —— **只判连不连得上** ——
    所以不会回退；于是页面上的同步按钮 202 running、跑完 0 行、看起来「点了没反应」。
    """
    seen: list[str] = []

    def fake(key, src, symbol, period, start, end, eff):
        seen.append(src)
        return pd.DataFrame() if src == "pytdx" else pd.DataFrame(
            {"ts": ["2026-01-05"], "open": [1.0], "high": [1.0], "low": [1.0],
             "close": [1.0], "volume": [1.0], "amount": [1.0]}
        )

    monkeypatch.setattr(sources, "_fetch_one", fake)
    with caplog.at_level("WARNING"):
        df = sources.fetch_bars("sh.000300", "30", "2026-01-01", "2026-12-31")
    assert seen == ["pytdx", "eastmoney"], seen
    assert len(df) == 1
    msgs = [r.getMessage() for r in caplog.records]
    assert any("回退到 eastmoney" in m for m in msgs), msgs


def test_zero_rows_without_a_fallback_stays_zero(monkeypatch):
    """没有备选源时**保持零行**，不假装拿到了东西。"""
    monkeypatch.setattr(sources, "_fetch_one", lambda *a, **k: pd.DataFrame())
    assert len(sources.fetch_bars("600030", "1", "2026-01-01", "2026-12-31")) == 0
