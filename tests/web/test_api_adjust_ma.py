"""Task 21：Web API 的三态复权与均线。

关注四件事：

1. **均线只算一次、且必须和 pandas 口径一致** —— 页面自己再算一遍均线，迟早和
   服务端/引擎的窗口错开一根，于是「金叉」画在两根不同的 K 线上。
2. **复权口径必须能看出来** —— 请求 `qfq` 而这只票没有除权记录时，三态价格完全相同，
   接口要如实回答 `adjust_effective="raw"` 并给出说明，而不是假装做了复权。
3. **结构必须画在同一个口径上** —— 请求 `hfq` 却拿 `raw` 的笔/段/中枢去画，
   中枢的 ZG/ZD 会和 K 线对不上，那是最难发现也最误导人的错。
4. **带市场前缀的指数键要能读到** —— `sh.000300` 不能退化成裸码 `000300`
   （那会指向深市同号段文件）。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from chanlun.config import load_config
from chanlun.data import meta, store
from chanlun.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "tests" / "chan" / "fixtures" / "bars.parquet"


def _bars(n=800, start=10.0, wave=1.0):
    """造一段**有结构**的行情：单调线画不出笔，没有笔就没有中枢，断言会空转。"""
    i = np.arange(n, dtype="float64")
    close = pd.Series(start + 3.0 * i / (n - 1) + wave * np.sin(i / 7.0))
    ts = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame({"ts": ts, "open": close, "high": close + 0.1, "low": close - 0.1,
                         "close": close, "volume": [1000.0] * n, "amount": [1e6] * n})


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))
    store.write("600000", "day", _bars())
    store.write("sh.000300", "day", _bars(start=4000.0))
    return conf


@pytest.fixture()
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


def _add_factors(cfg, code="600000", rows=(("2024-01-02", 0.5), ("2024-07-01", 1.0))):
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.save_adjust_factors(conn, code, rows)
    finally:
        conn.close()


# ---------------- 均线 ----------------
def test_ma_matches_pandas_rolling(client):
    body = client.get("/api/bars", params={"code": "600000", "period": "day",
                                           "ma": "5,10,20,60,120,250"}).json()
    close = _bars()["close"]
    ma = body["ma"]
    assert set(ma) == {"5", "10", "20", "60", "120", "250"}
    assert body["ma_periods"] == [5, 10, 20, 60, 120, 250]
    for period in (5, 10, 20, 60, 120, 250):
        expected = close.rolling(period).mean()
        got = pd.Series(ma[str(period)], dtype="float64")
        assert got.iloc[:period - 1].isna().all(), f"MA{period} 前 {period - 1} 根必须是空"
        assert got.iloc[-1] == pytest.approx(expected.iloc[-1], abs=1e-3)
        assert got.iloc[period - 1] == pytest.approx(expected.iloc[period - 1], abs=1e-3)


def test_ma_values_are_rounded_to_4_decimals(client):
    """保留 4 位小数是接口契约（前端不再二次格式化），要能被测到。"""
    body = client.get("/api/bars", params={"code": "600000", "period": "day", "ma": "5"}).json()
    expected = _bars()["close"].rolling(5).mean()
    got = body["ma"]["5"]
    assert got[4] == round(float(expected.iloc[4]), 4)
    assert got[-1] == round(float(expected.iloc[-1]), 4)


def test_ma_default_is_the_six_configured_periods(client):
    body = client.get("/api/bars", params={"code": "600000", "period": "day"}).json()
    assert body["ma_periods"] == [5, 10, 20, 60, 120, 250]


def test_ma_can_be_turned_off(client):
    body = client.get("/api/bars", params={"code": "600000", "period": "day", "ma": ""}).json()
    assert body["ma"] == {} and body["ma_periods"] == []


def test_ma_rejects_nonsense(client):
    r = client.get("/api/bars", params={"code": "600000", "period": "day", "ma": "5,abc"})
    assert r.status_code == 400
    assert "均线" in r.json()["detail"]


# ---------------- 复权 ----------------
def test_adjust_default_is_qfq_and_reports_effective(client):
    body = client.get("/api/bars", params={"code": "600000", "period": "day"}).json()
    assert body["adjust"] == "qfq" and body["adjust_effective"] == "raw"
    assert "无除权记录" in body["adjust_note"]


def test_adjust_modes_change_prices_when_factors_exist(client, cfg):
    _add_factors(cfg)
    raw = client.get("/api/bars", params={"code": "600000", "period": "day",
                                          "adjust": "raw"}).json()
    qfq = client.get("/api/bars", params={"code": "600000", "period": "day",
                                          "adjust": "qfq"}).json()
    hfq = client.get("/api/bars", params={"code": "600000", "period": "day",
                                          "adjust": "hfq"}).json()
    assert raw["bars"][0]["close"] > qfq["bars"][0]["close"]
    assert hfq["bars"][0]["close"] == pytest.approx(raw["bars"][0]["close"])
    assert qfq["bars"][-1]["close"] == pytest.approx(raw["bars"][-1]["close"])
    assert hfq["bars"][-1]["close"] == pytest.approx(qfq["bars"][-1]["close"] * 2)
    assert qfq["adjust_effective"] == "qfq" and hfq["adjust_effective"] == "hfq"
    assert raw["adjust_effective"] == "raw"
    assert "除权" in qfq["adjust_note"]


def test_ma_follows_requested_adjust_mode(client, cfg):
    """均线要用**当前口径**的收盘价算，不能永远算不复权的。"""
    _add_factors(cfg)
    raw = client.get("/api/bars", params={"code": "600000", "period": "day",
                                          "adjust": "raw", "ma": "5"}).json()["ma"]["5"]
    qfq = client.get("/api/bars", params={"code": "600000", "period": "day",
                                          "adjust": "qfq", "ma": "5"}).json()["ma"]["5"]
    assert qfq[4] == pytest.approx(raw[4] * 0.5, abs=1e-3)


def test_adjust_accepts_source_numeric_vocabulary(client):
    """行情源用的是数字口径（baostock/通达信：1=后复权 2=前复权 3=不复权），照收。"""
    for raw_value, expect in (("2", "qfq"), ("1", "hfq"), ("3", "raw")):
        body = client.get("/api/bars", params={"code": "600000", "period": "day",
                                               "adjust": raw_value}).json()
        assert body["adjust"] == expect, f"adjust={raw_value} 应归一成 {expect}"


def test_unknown_adjust_mode_is_400(client):
    r = client.get("/api/bars", params={"code": "600000", "period": "day", "adjust": "bogus"})
    assert r.status_code == 400
    assert "复权" in r.json()["detail"]


def test_structure_is_computed_on_the_requested_adjust_mode(tmp_path, monkeypatch):
    """结构、K 线、中枢必须同一个口径 —— 缓存键漏了口径就会串味。

    用**真实行情样本**（`tests/chan/fixtures/bars.parquet`，1212 根日线，实测 88 笔 / 12 段 / 2 中枢）：
    自己造的直线或正弦行情画不出中枢，断言会空转，等于没测。
    """
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))
    real = pd.read_parquet(BARS)
    bars = real[real["code"] == "sh.600000"].drop(columns=["code"]).reset_index(drop=True)
    store.write("600000", "day", bars)
    # 单段因子：整段行情都乘 0.5，于是「复权后中枢价位 = 不复权 × 0.5」可断言
    _add_factors(conf, rows=(("2020-01-02", 0.5),))

    with TestClient(create_app(conf)) as client:
        raw = client.get("/api/structure", params={"code": "600000", "period": "day",
                                                   "adjust": "raw"}).json()
        qfq = client.get("/api/structure", params={"code": "600000", "period": "day",
                                                   "adjust": "qfq"}).json()

    assert qfq["adjust"] == "qfq" and qfq["adjust_effective"] == "qfq"
    # 复权价保留 4 位小数（`adjust.PRICE_DECIMALS`），容差取 1e-3，远小于一个价位跳动
    assert qfq["bars"][0]["close"] == pytest.approx(raw["bars"][0]["close"] * 0.5, abs=1e-3)
    assert qfq["bars"][-1]["close"] == pytest.approx(raw["bars"][-1]["close"] * 0.5, abs=1e-3)
    # 样本必须真的造出中枢，否则下面这条断言是空转的（等于没测）
    assert raw["pivots"], "样本行情没造出中枢，这条测试失去了判别力"
    assert qfq["pivots"], "复权后中枢不该消失"
    # 价格保留 4 位小数，中枢由这些价格算出，容差取 1e-3（远小于一个价位跳动）
    assert max(p["zg"] for p in qfq["pivots"]) == pytest.approx(
        max(p["zg"] for p in raw["pivots"]) * 0.5, abs=1e-3)


def test_structure_without_factors_reports_raw_effective(client):
    body = client.get("/api/structure", params={"code": "600000", "period": "day"}).json()
    assert body["adjust"] == "qfq" and body["adjust_effective"] == "raw"
    assert "无除权记录" in body["adjust_note"]
    assert set(body["ma"]) == {"5", "10", "20", "60", "120", "250"}


def test_cached_structure_invalidates_when_factors_are_corrected(tmp_path, monkeypatch):
    """行情一根没动、只把因子表改对：缓存必须失效。

    除权数据是**后补**的（同步当天可能还没有因子，几天后才补上或被纠正），
    只按「行情最后时间戳」做缓存键会一直拿着旧复权价算结构。
    """
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))
    real = pd.read_parquet(BARS)
    bars = real[real["code"] == "sh.600000"].drop(columns=["code"]).reset_index(drop=True)
    store.write("600000", "day", bars)
    _add_factors(conf, rows=(("2020-01-02", 0.5),))

    with TestClient(create_app(conf)) as client:
        first = client.get("/api/structure", params={"code": "600000", "period": "day",
                                                     "adjust": "qfq"}).json()
        _add_factors(conf, rows=(("2020-01-02", 0.25),))
        second = client.get("/api/structure", params={"code": "600000", "period": "day",
                                                      "adjust": "qfq"}).json()

    assert first["pivots"] and second["pivots"]
    assert max(p["zg"] for p in second["pivots"]) == pytest.approx(
        max(p["zg"] for p in first["pivots"]) * 0.5, abs=1e-3)


# ---------------- 代码键 ----------------
def test_index_code_with_market_prefix_is_readable(client):
    r = client.get("/api/bars", params={"code": "sh.000300", "period": "day"})
    assert r.status_code == 200, r.json()  # 退化成裸码就会去读深市同号段文件，404
    body = r.json()
    assert body["code"] == "sh.000300"
    assert body["bars"][-1]["close"] == pytest.approx(float(_bars(start=4000.0)["close"].iloc[-1]))


def test_normalize_code_keeps_prefix_only_when_needed():
    from chanlun.web.api import normalize_code

    assert normalize_code("sh.600000") == "600000"
    assert normalize_code("sh.000300") == "sh.000300"
    assert normalize_code("000300") == "000300"  # 裸码按约定是深市
    assert normalize_code("920017") == "920017"  # 北交所新号段
    with pytest.raises(Exception):
        normalize_code("sh.300059")  # 前缀与号段矛盾


# ---------------- 自选栏摘要 ----------------
def _watch(cfg, code="600000"):
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.upsert_universe(conn, [("600000", "sh.600000", "浦发银行", "sh", "1999-11-10", 0)])
        meta.add_watch(conn, code)
    finally:
        conn.close()


def test_watchlist_summary_carries_price_for_the_rail(cfg):
    """左侧自选栏要显示最新价与涨跌幅，否则「自选股」就只是六个数字的清单。

    价格必须取自**同一个复权帧**（和图表同口径），涨跌幅由该帧相邻两根收盘价算出：
    横跨除权日时，不复权口径会凭空多出一根假跌停。
    """
    _watch(cfg)
    _add_factors(cfg, rows=(("2024-01-02", 0.5),))  # 整段行情同一个因子
    with TestClient(create_app(cfg)) as client:
        body = client.get("/api/watchlist/structure", params={"period": "day"}).json()
        raw = client.get("/api/watchlist/structure",
                         params={"period": "day", "adjust": "raw"}).json()

    it = body["items"][0]
    close = _bars()["close"]
    assert it["name"] == "浦发银行", "名称要能从 universe 表兜底取到"
    assert it["ts"] == _bars()["ts"].iloc[-1]
    assert it["close"] == pytest.approx(float(close.iloc[-1]) * 0.5, abs=1e-3)
    assert it["adjust"] == "qfq" and it["adjust_effective"] == "qfq"
    # 单一因子整段不变时，涨跌幅与不复权一致（因子在比值里约掉）—— 复权不该把涨跌幅也缩放
    assert it["change_pct"] == pytest.approx(raw["items"][0]["change_pct"], abs=1e-6)
    assert raw["items"][0]["close"] == pytest.approx(float(close.iloc[-1]), abs=1e-3)


def test_watchlist_change_pct_survives_a_factor_change_inside_the_window(cfg):
    """除权日就在最新一根上：不复权会算出一根假跌，前复权不会。

    自选栏报的是**最新一根**的涨跌幅，所以除权日必须落在最后一根上才谈得上这件事
    （除权日在窗口中间时，最新涨跌幅与它无关）。
    真实数据里**不复权才是带缺口的那一条**（前复权就是把这个缺口补平的结果），
    所以这里把除权日之前的价格抬成 2 倍，再让因子表把它乘回 0.5：
    前复权帧连续，不复权帧在最新一根上有一根 -50% 的假跌。
    """
    bars = _bars()
    ex = str(bars["ts"].iloc[-1])
    bars.loc[bars["ts"] < ex, ["open", "high", "low", "close"]] *= 2
    store.write("600000", "day", bars)
    _watch(cfg)
    _add_factors(cfg, rows=((str(bars["ts"].iloc[0]), 0.5), (ex, 1.0)))
    with TestClient(create_app(cfg)) as client:
        raw = client.get("/api/watchlist/structure",
                         params={"period": "day", "adjust": "raw"}).json()["items"][0]
        qfq = client.get("/api/watchlist/structure",
                         params={"period": "day", "adjust": "qfq"}).json()["items"][0]

    assert raw["change_pct"] < -20, "不复权口径在除权日会算出一根假跌（本用例的前提）"
    assert abs(qfq["change_pct"]) < 10, f"前复权后的涨跌幅不该被除权砸出坑：{qfq['change_pct']}"


# ---------------- 落库口径（一期 day 是 baostock 前复权） ----------------
def _set_stored(cfg, adjust, code="600000", period="day"):
    """把该票该周期的落库口径写进 sync_state —— 真实同步流水线每次都写这一行。"""
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.set_sync(conn, code, period, None, None, 0, adjust)
    finally:
        conn.close()


def test_baostock_stored_prices_are_not_reported_as_raw(client, cfg):
    """一期库里的 day 是 baostock **前复权**（`sync_state.adjust="2"`）。

    没有因子表时三态确实切不动，但生效口径是前复权，不是"不复权原始价"：
    把前复权价标成不复权，用户会以为除权日的跳空是行情本身，而库里那份早就补平了。
    """
    _set_stored(cfg, "2")
    body = client.get("/api/bars", params={"code": "600000", "period": "day",
                                           "adjust": "raw"}).json()
    assert body["adjust"] == "raw" and body["adjust_effective"] == "qfq"
    assert "前复权" in body["adjust_note"]
    assert "原始价" not in body["adjust_note"]
    assert body["bars"][0]["close"] == pytest.approx(float(_bars()["close"].iloc[0]))


def test_stored_qfq_answers_qfq_and_names_the_stored_mode(client, cfg):
    _set_stored(cfg, "2")
    body = client.get("/api/bars", params={"code": "600000", "period": "day",
                                           "adjust": "qfq"}).json()
    assert body["adjust_effective"] == "qfq"
    assert "前复权" in body["adjust_note"]


def test_stored_raw_keeps_the_raw_formula(client, cfg):
    """`sync_state.adjust="3"`（通达信落库）走原来的公式：qfq = raw × k_t。"""
    _set_stored(cfg, "3")
    _add_factors(cfg)
    qfq = client.get("/api/bars", params={"code": "600000", "period": "day",
                                          "adjust": "qfq"}).json()
    assert qfq["adjust_effective"] == "qfq"
    assert qfq["bars"][0]["close"] == pytest.approx(float(_bars()["close"].iloc[0]) * 0.5)


def test_stored_qfq_with_factors_derives_raw_and_hfq(client, cfg):
    """库内是前复权 `q_t` 且因子表已补齐时：`raw = q_t / k_t`，`hfq = q_t / k_0`。

    这是 baostock 落库 + 因子补全之后的真实状态。照不复权的公式再乘一遍 `k_t`，
    前复权价会被**二次复权**，除权日反而长出一根假跳空 —— 方向恰好反了。
    """
    _set_stored(cfg, "2")
    _add_factors(cfg)  # k: 2024-01-02 → 0.5，2024-07-01 → 1.0
    first = float(_bars()["close"].iloc[0])
    raw = client.get("/api/bars", params={"code": "600000", "period": "day", "adjust": "raw"}).json()
    qfq = client.get("/api/bars", params={"code": "600000", "period": "day", "adjust": "qfq"}).json()
    hfq = client.get("/api/bars", params={"code": "600000", "period": "day", "adjust": "hfq"}).json()

    assert qfq["bars"][0]["close"] == pytest.approx(first)
    assert raw["bars"][0]["close"] == pytest.approx(first / 0.5)
    assert raw["bars"][-1]["close"] == pytest.approx(qfq["bars"][-1]["close"])
    assert hfq["bars"][0]["close"] == pytest.approx(first / 0.5)
    assert hfq["bars"][-1]["close"] == pytest.approx(qfq["bars"][-1]["close"] / 0.5)
    assert raw["adjust_effective"] == "raw" and hfq["adjust_effective"] == "hfq"
    assert "除权" in hfq["adjust_note"]


def test_watchlist_summary_reports_the_stored_mode(cfg):
    """自选栏与图表必须报同一个生效口径，否则两处价格看起来"对不上"。"""
    _set_stored(cfg, "2")
    _watch(cfg)
    with TestClient(create_app(cfg)) as client:
        it = client.get("/api/watchlist/structure",
                        params={"period": "day", "adjust": "hfq"}).json()["items"][0]
    assert it["adjust"] == "hfq" and it["adjust_effective"] == "qfq"
    assert it["close"] == pytest.approx(float(_bars()["close"].iloc[-1]))
