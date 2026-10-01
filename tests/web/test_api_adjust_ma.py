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
