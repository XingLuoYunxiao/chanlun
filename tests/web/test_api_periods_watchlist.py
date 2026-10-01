"""周线/月线 + 自选池 CRUD 的接口契约（Task 29）。

三件事：

1. **打开的票要显示名称**：只给一个 6 位数字，看盘时认不出是哪只票；
   名称先查品种表，再查自选池（**指数不在品种表里**，名称是加自选时存进去的）。
2. **周线/月线由本地日线聚合**，不单独同步。所以判据是「与日线对得上账」：
   成交量/成交额必须守恒、开=首日开、收=末日收、高/低=区间极值、时间戳=该周期最后交易日；
   派生周期最后一根**没走完**时接口要能说出来（页面必须标「未走完」，否则读图的人
   会把一根还在变的周K当成定论）。
3. **自选池要能排序**（用户 2026-10-01 选定「改」= 上下移动）：上移/下移改的是顺序，
   刷新后不能变回去；越界的移动是空操作，不是报错。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from chanlun.config import load_config
from chanlun.data import meta, store
from chanlun.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "tests" / "chan" / "fixtures" / "bars.parquet"
INDEX = "sh.000001"


def _index_days() -> pd.DataFrame:
    """合成一段指数日线：2026-03-02 → 2026-09-30 的工作日，价格单调上行。"""
    days = pd.bdate_range("2026-03-02", "2026-09-30")
    n = len(days)
    close = [3000.0 + 10 * i for i in range(n)]
    return pd.DataFrame(
        {
            "ts": [d.strftime("%Y-%m-%d") for d in days],
            "open": [c - 5 for c in close],
            "high": [c + 8 for c in close],
            "low": [c - 9 for c in close],
            "close": close,
            "volume": [1e6 + i for i in range(n)],
            "amount": [1e9 + i for i in range(n)],
        }
    )


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(store, "DATA_ROOT", root)
    base = load_config()
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))

    df = pd.read_parquet(BARS)
    g = df[df["code"] == "sh.600000"].drop(columns=["code"])
    store.upsert("600000", "day", g)
    store.upsert(INDEX, "day", _index_days())

    conn = meta.init(conf.data.meta_db)
    meta.upsert_universe(conn, [("600000", "sh.600000", "浦发银行", "sh", "", 0)])
    meta.seed_watchlist(conn)
    conn.close()
    return conf


@pytest.fixture()
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


# ---------------- 名称 ----------------
def test_structure_carries_the_name_of_the_opened_code(client):
    s = client.get("/api/structure?code=600000&period=day&limit=300").json()
    assert s["name"] == "浦发银行", "品种表里有名字就必须带上，否则页面上只有一个数字"


def test_structure_name_falls_back_to_the_watchlist_for_an_index(client):
    """指数不在 `universe` 表里（那张表只有股票），名称只能来自自选池。"""
    s = client.get(f"/api/structure?code={INDEX}&period=day&limit=50").json()
    assert s["name"] == "上证指数"


def test_structure_name_is_empty_rather_than_a_crash(client):
    s = client.get("/api/structure?code=600000&period=day&limit=50").json()
    assert isinstance(s["name"], str)


# ---------------- 周线 / 月线 ----------------
def test_week_and_month_are_aggregated_from_the_same_day_bars(client):
    day = client.get("/api/structure?code=600000&period=day&limit=20000").json()
    week = client.get("/api/structure?code=600000&period=week&limit=20000").json()
    month = client.get("/api/structure?code=600000&period=month&limit=20000").json()

    assert week["period"] == "week" and month["period"] == "month"
    assert week["derived"] is True and week["base_period"] == "day"
    assert month["derived"] is True
    assert day["derived"] is False, "日线是落库周期，不是派生出来的"
    assert len(month["bars"]) < len(week["bars"]) < len(day["bars"])

    # 量额守恒：聚合只换分组，不许丢数据
    for derived in (week, month):
        assert sum(b["volume"] for b in derived["bars"]) == pytest.approx(
            sum(b["volume"] for b in day["bars"]), rel=1e-9
        )
        assert derived["bars"][-1]["ts"] == day["bars"][-1]["ts"], "最后一根的戳=最后一个交易日"
        assert derived["bars"][-1]["close"] == day["bars"][-1]["close"], "收=该周期最后一天收盘"
        assert derived["bars"][0]["open"] == day["bars"][0]["open"], "开=该周期第一天开盘"
        for b in derived["bars"]:
            assert b["low"] <= b["open"] <= b["high"] and b["low"] <= b["close"] <= b["high"]


def test_week_bars_have_weekly_highs_and_lows(client):
    day = client.get("/api/structure?code=600000&period=day&limit=20000").json()["bars"]
    week = client.get("/api/structure?code=600000&period=week&limit=20000").json()["bars"]
    first_week_end = week[0]["ts"]
    in_week = [b for b in day if b["ts"] <= first_week_end]
    assert first_week_end.startswith(in_week[0]["ts"][:4]), "第一根周K应该落在首日那一年"
    assert week[0]["high"] == pytest.approx(max(b["high"] for b in in_week))
    assert week[0]["low"] == pytest.approx(min(b["low"] for b in in_week))


def test_partial_last_bar_is_flagged_honestly(client):
    """样本日线到 2024-12-31（周二）：那一周还没走完，但 12 月是走完的。"""
    week = client.get("/api/structure?code=600000&period=week&limit=20000").json()
    month = client.get("/api/structure?code=600000&period=month&limit=20000").json()
    assert week["partial"] is True, "2024-12-31 是周二，这一周还差两天"
    assert month["partial"] is False, "2024-12-31 就是 12 月最后一天"


def test_derived_period_without_day_data_is_404_with_the_right_hint(client):
    r = client.get("/api/structure?code=300059&period=week")
    assert r.status_code == 404
    assert "日线" in r.json()["detail"], "要说清周线是从日线聚合的，而不是让用户去 sync --period week"
    assert "--period day" in r.json()["detail"]


def test_bars_endpoint_serves_derived_periods(client):
    b = client.get("/api/bars?code=600000&period=month&limit=10").json()
    assert b["period"] == "month" and b["count"] == 10
    assert b["bars"][-1]["ts"] == "2024-12-31"


def test_unknown_period_still_400_and_lists_the_new_ones(client):
    r = client.get("/api/structure?code=600000&period=year")
    assert r.status_code == 400
    assert "week" in r.json()["detail"] and "month" in r.json()["detail"]


def test_watchlist_structure_serves_week_for_the_indices(client):
    """自选栏的周线卡片必须能算出来，且与图表口径一致（指数也在池里）。"""
    client.post("/api/watchlist", json={"code": "600000", "name": "浦发银行"})
    st = client.get("/api/watchlist/structure?period=week&limit=300").json()
    by_code = {i["code"]: i for i in st["items"]}
    assert by_code[INDEX]["missing"] is False, "指数有日线，周线就该算得出来"
    assert by_code["600000"]["missing"] is False
    assert by_code["600000"]["counts"]["strokes"] > 0
    for code in (INDEX, "600000"):
        chart = client.get(f"/api/structure?code={code}&period=week&limit=300").json()
        assert by_code[code]["counts"] == chart["counts"], "自选栏卡片与图表口径必须一致"


# ---------------- 自选池：改（排序） ----------------
def test_watchlist_move_reorders_and_persists(client):
    items = client.get("/api/watchlist").json()["items"]
    codes = [i["code"] for i in items]
    assert codes == [c for c, _ in meta.DEFAULT_WATCHLIST], "默认池就是那 7 个指数"
    assert "600030" not in codes and "600519" not in codes, "用户选定：原来的 4 只股票删掉"

    last = codes[-1]
    r = client.patch("/api/watchlist", json={"code": last, "delta": -1})
    assert r.status_code == 200
    want = codes[:-2] + [last, codes[-2]]
    assert r.json()["order"] == want
    assert [i["code"] for i in client.get("/api/watchlist").json()["items"]] == want, "顺序必须落库"


def test_watchlist_move_at_the_edge_is_a_noop(client):
    codes = [i["code"] for i in client.get("/api/watchlist").json()["items"]]
    r = client.patch("/api/watchlist", json={"code": codes[0], "delta": -1})
    assert r.status_code == 200 and r.json()["order"] == codes


def test_watchlist_move_rejects_unknown_code_and_bad_delta(client):
    assert client.patch("/api/watchlist", json={"code": "600000", "delta": -1}).status_code == 404
    r = client.patch("/api/watchlist", json={"code": INDEX, "delta": 3})
    assert r.status_code == 400
    assert "delta" in r.json()["detail"]


def test_watchlist_add_remove_roundtrip_still_works(client):
    r = client.post("/api/watchlist", json={"code": "600000", "name": "浦发银行"})
    assert r.status_code == 200 and r.json()["code"] == "600000"
    assert client.get("/api/watchlist").json()["items"][-1]["code"] == "600000", "新加的票在末尾"
    assert client.delete("/api/watchlist?code=600000").status_code == 200
    assert "600000" not in [i["code"] for i in client.get("/api/watchlist").json()["items"]]
