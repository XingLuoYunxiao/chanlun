"""Task 13：Web 层接口契约。

关注三件事：
1. **结构对象必须自带 `status` 与 `confirmed_at`** —— 页面上未确认结构要能画成虚线，
   回测/盯盘的人必须一眼看出「这个中枢还没成立」，所以状态不能只存在于内存里。
2. **缺数据必须报错而不是返回空** —— 品种写错交易所（`sh.300059`）或该周期没同步过，
   接口要 404/400 并给出原因；返回 200 + 空数组会让人误以为「这只票没结构」。
3. **端口写死 8888**，被占用就抛错，不静默换端口。
"""

from __future__ import annotations

import dataclasses
import socket
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from chanlun.config import load_config
from chanlun.data import meta, store
from chanlun.web.app import PortInUseError, create_app, ensure_port_free

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "tests" / "chan" / "fixtures" / "bars.parquet"
CODES = ("sh.600000", "sh.601088", "sz.300059", "sz.300750")


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """把数据根指向临时目录，并写入 4 只样本的日线（代码与真实同步一致：裸数字）。"""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    # DataConfig.meta_db 是 root 下的派生属性（只读），不能当字段替换
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))
    df = pd.read_parquet(BARS)
    for bs_code, g in df.groupby("code"):
        store.upsert(str(bs_code).split(".")[1], "day", g.drop(columns=["code"]))
    conn = meta.init(conf.data.meta_db)
    meta.upsert_universe(conn, [(c.split(".")[1], c, "样本", c.split(".")[0], "", 0) for c in CODES])
    conn.close()
    return conf


@pytest.fixture()
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


# ---------------- 健康检查与端口 ----------------
def test_health_reports_fixed_port(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["port"] == 8888, "端口写死 8888，不允许静默漂移"
    assert body["data_root"]


def test_ensure_port_free_raises_when_occupied():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        with pytest.raises(PortInUseError) as ei:
            ensure_port_free("127.0.0.1", port)
        assert str(port) in str(ei.value)
        assert "不" in str(ei.value)  # 明确说明不会自动换端口
    finally:
        s.close()
    assert ensure_port_free("127.0.0.1", port) is None


# ---------------- 页面 ----------------
def test_index_html_carries_disclaimer_and_vendored_echarts(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "不构成投资建议" in html
    assert "收盘后确认" in html, "必须说明结构是收盘后确认，不是盘中实时"
    assert "/static/vendor/echarts.min.js" in html, "ECharts 必须本地内联，离线可用"
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/echarts.min.js").status_code == 200


# ---------------- 行情与结构 ----------------
def test_bars_endpoint(client):
    r = client.get("/api/bars?code=600000&period=day&limit=100")
    assert r.status_code == 200
    b = r.json()
    assert b["code"] == "600000" and b["period"] == "day"
    assert len(b["bars"]) == 100
    row = b["bars"][-1]
    assert set(row) == {"ts", "open", "high", "low", "close", "volume", "amount"}
    assert b["bars"][0]["ts"] < b["bars"][-1]["ts"]


def test_structure_has_status_and_confirmed_at_for_every_object(client):
    """字段契约用短窗，内容断言必须用**全窗**。

    窗口不是自相似的：`limit=300/500` 从结构中间切入，样本 4 只票一段中枢都没有；
    `limit=1212`（全窗）实测 sh.600000 段 12 / 确认 10 / 中枢 2。
    拿短窗去断言"应该有中枢"，测的是窗口而不是引擎。
    """
    s = client.get("/api/structure?code=600000&period=day&limit=300").json()
    assert s["segments"], "短窗里也应有线段"
    for name in ("strokes", "segments", "pivots", "signals"):
        assert name in s
        for obj in s[name]:
            assert obj["status"] in ("tentative", "confirmed", "invalidated")
            assert "confirmed_at" in obj

    full = client.get("/api/structure?code=600000&period=day&limit=2000").json()
    assert full["counts"]["confirmed_segments"] >= 5
    assert full["pivots"], "全窗下样本应有中枢（实测 2 个）"
    assert any(p["status"] == "confirmed" for p in full["pivots"])
    assert any(sg["status"] == "tentative" for sg in full["segments"]), "末段应为未确认"


def test_structure_bars_and_meta_align(client):
    s = client.get("/api/structure?code=600000&period=day&limit=200").json()
    assert s["code"] == "600000" and s["level"] == "day"
    assert len(s["bars"]) == 200
    assert s["as_of"] == s["bars"][-1]["ts"]
    assert s["disclaimer"] and s["counts"]["segments"] == len(s["segments"])


def test_tentative_objects_are_flagged_and_unstamped(client):
    s = client.get("/api/structure?code=600000&period=day&limit=300").json()
    tent = [x for x in s["segments"] if x["status"] == "tentative"]
    assert tent, "末段必然是 tentative（未确认），用于画虚线"
    for x in tent:
        assert x["confirmed_at"] is None
    for x in s["segments"]:
        if x["status"] == "confirmed":
            assert x["confirmed_at"], "确认对象必须有确认时间，否则回测无法判断当时是否可见"


def test_status_forms_are_accepted(client):
    for form in ("600000", "sh.600000", "SH600000", " sh.600000 "):
        r = client.get(f"/api/structure?code={form.strip()}&period=day&limit=50")
        assert r.status_code == 200, form


def test_mismatched_exchange_prefix_is_rejected(client):
    r = client.get("/api/structure?code=sh.300059&period=day")
    assert r.status_code == 400
    assert "交易所前缀" in r.json()["detail"]


def test_unknown_symbol_is_404_not_silent_empty(client):
    r = client.get("/api/structure?code=600001&period=day")
    assert r.status_code == 404
    assert "600001" in r.json()["detail"]


def test_unsynced_period_is_404_with_hint(client):
    r = client.get("/api/structure?code=600000&period=30")
    assert r.status_code == 404
    assert "30" in r.json()["detail"]
    assert "sync" in r.json()["detail"]


def test_scan_and_universe(client):
    u = client.get("/api/universe?limit=10").json()
    assert u["count"] == 4 and len(u["items"]) == 4
    scan = client.get("/api/scan?period=day").json()
    assert scan["run_date"] and scan["hits"] == []


# ---------------- 自选池 ----------------
def test_watchlist_roundtrip(client):
    assert client.get("/api/watchlist").json()["items"] == []
    r = client.post("/api/watchlist", json={"code": "600000", "name": "浦发银行", "note": "日线中枢"})
    assert r.status_code == 200 and r.json()["code"] == "600000"
    items = client.get("/api/watchlist").json()["items"]
    assert [i["code"] for i in items] == ["600000"]
    assert items[0]["name"] == "浦发银行"
    # 自选池跟踪：结构摘要一次给全，避免前端逐个再请求
    st = client.get("/api/watchlist/structure?period=day").json()
    assert st["items"][0]["code"] == "600000"
    assert st["items"][0]["counts"]["strokes"] > 0
    assert st["items"][0]["as_of"]

    # 卡片和图表必须描述**同一个窗口**。曾经自选池默认 300 根、图表 1200 根，
    # 同一只票在卡片上说"无中枢"、在图上画着两个中枢 —— 这种自相矛盾比数字错更难发现。
    chart = client.get("/api/structure?code=600000&period=day").json()
    card = st["items"][0]
    assert card["counts"] == chart["counts"], "自选池摘要与图表口径必须一致"
    assert card["pivots"] and len(card["pivots"]) == len(chart["pivots"])
    assert client.delete("/api/watchlist?code=600000").status_code == 200
    assert client.get("/api/watchlist").json()["items"] == []
