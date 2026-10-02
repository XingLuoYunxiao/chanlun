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
from chanlun.web.api import _CACHE, _CACHE_LOCK
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


def test_week_bars_are_adjusted_day_by_day_before_aggregation(client, cfg):
    """**先复权后聚合**：除权落在周中间时，周K的开盘价必须是**周一**那天的复权价。

    反着做（先把日线聚成周线、再整根乘最后一个交易日的因子）等于拿周五的因子去乘
    周一的原始开盘价。这不是理论风险：下面这段样本里周三翻倍，两种顺序的开盘价
    差一倍（10.0 vs 20.0），最低价同理（9.5 vs 19.0）。
    """
    days = pd.DataFrame(
        {
            "ts": ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"],
            "open": [10.0, 10.2, 20.4, 20.6, 20.8],
            "high": [10.5, 10.6, 21.0, 21.2, 21.4],
            "low": [9.5, 9.8, 20.0, 20.2, 20.4],
            "close": [10.2, 10.4, 20.6, 20.8, 21.0],
            "volume": [100.0] * 5,
            "amount": [1000.0] * 5,
        }
    )
    store.upsert("600000", "day", days)
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.set_sync(conn, "600000", "day", "2026-03-02", "2026-03-06", 5, "3")
        meta.save_adjust_factors(conn, "600000", [("2026-03-02", 1.0), ("2026-03-04", 2.0)])
    finally:
        conn.close()

    week = client.get("/api/structure?code=600000&period=week&adjust=qfq&limit=20000").json()
    bar = [b for b in week["bars"] if b["ts"] == "2026-03-06"][0]
    assert bar["open"] == pytest.approx(10.0), "周K开盘=周一复权开盘，不是整根乘周五的因子"
    assert bar["low"] == pytest.approx(9.5), "最低价来自周一，不能被周五的因子放大"
    assert bar["high"] == pytest.approx(42.8), "最高价来自周五（21.4 × 2.0）"
    assert bar["close"] == pytest.approx(42.0), "收盘=周五收盘 × 周五因子"


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


# ---------------- 自选池结构摘要：mode 口径（Task 7b） ----------------
def _clear_cache() -> None:
    """显式清 `_CACHE`。

    **本模块的本地 `client` fixture 遮蔽了 `tests/web/conftest.py` 里的同名 fixture**，
    所以 conftest 那份清理**不会**作用到这里。缓存键含 `mode`，理论上不会串，
    但显式清掉更省事。
    """
    with _CACHE_LOCK:
        _CACHE.clear()


def _watch_item(client, code: str, **params) -> dict:
    """按 `code` 找自选池里的那一项。

    **不许假设 `items[0]`**：默认池是 7 个指数，本模块的夹具只给其中 `sh.000001`
    落了合成日线，其余全是 `missing: True`；顺序也由用户排过。
    """
    r = client.get("/api/watchlist/structure", params={"period": "day", **params})
    assert r.status_code == 200, r.text
    hit = [i for i in r.json()["items"] if i["code"] == code]
    assert len(hit) == 1, f"{code} 在自选池里应恰好出现一次，实测 {len(hit)} 次"
    return hit[0]


def test_watchlist_structure_accepts_mode_and_loose_has_more_signals(client):
    """自选栏那一行**会打印最新一个买卖点**（`app.js:918-924`），所以它必须和主图同口径。

    Task 7 给 `/api/structure` 加了 `mode`，这里没加 ⇒ 自选栏恒按严格口径算：
    非严格模式下主图上有一只 `pb`，那一行**既不高亮也不显示它**，显示的是严格口径下的
    另一个信号（或者什么都没有）—— 同一只票两块界面互相矛盾，和用户报过的
    「界面上只显示一个向下的线段」是同一类问题。
    """
    _clear_cache()
    client.post("/api/watchlist", json={"code": "600000", "name": "浦发银行"})
    strict = _watch_item(client, "600000", mode="strict")
    loose = _watch_item(client, "600000", mode="loose")
    assert len(loose["signals"]) > len(strict["signals"]), (
        f"自选栏的 signals 必须按 mode 口径算：实测 strict={len(strict['signals'])} "
        f"loose={len(loose['signals'])}（相等 ⇒ mode 没透传给 snapshot_of，恒按严格算）"
    )
    assert strict["mode"] == "strict" and loose["mode"] == "loose", (
        "响应体必须自描述口径：前端与测试要靠它断言「这份数据是哪个口径的」，"
        "否则将来再出同类问题时没有可查的证据"
    )


def test_watchlist_structure_strict_is_byte_identical_without_the_param(client):
    """`?mode=strict` 与**完全不传** `mode` 的响应逐字节相同（默认口径不变）。

    **这是护栏，不是证明**：BASE（未改 `api.py`）上它同样通过 —— 老代码根本没有
    `mode` 参数，两次请求都走严格口径。它守的是**将来**：新参数一旦默认成 `None`
    或 `loose`，现在只拼 `period`/`adjust` 的页面（`app.js:791`）拿到的口径就悄悄换了。
    """
    _clear_cache()
    client.post("/api/watchlist", json={"code": "600000", "name": "浦发银行"})
    default = client.get("/api/watchlist/structure", params={"period": "day"})
    explicit = client.get("/api/watchlist/structure", params={"period": "day", "mode": "strict"})
    assert default.status_code == 200, default.text
    assert explicit.status_code == 200, explicit.text
    assert explicit.content == default.content, (
        "带 ?mode=strict 与不传 mode 的响应必须逐字节相同，否则默认口径被悄悄改了"
    )


def test_watchlist_structure_invalid_mode_is_422(client):
    """非法口径必须 **422**（FastAPI 的 `Query(pattern=...)`），不是 400、更不是 500。

    签名若写成裸 `str`：`mode=wild` 会被**静默忽略**（200），或者一路走到
    `SignalMode("wild")` 抛 `ValueError` —— 后者在本端点**不是 500**，而是被
    `except (DataSourceError, HTTPException, ValueError)` 兜成一行
    `{"missing": true, "error": ...}`（见 `test_watchlist_structure_degrades_...`）；
    两种都不是 422。
    """
    _clear_cache()
    r = client.get("/api/watchlist/structure", params={"period": "day", "mode": "wild"})
    assert r.status_code == 422, f"非法 mode 必须 422，实测 {r.status_code}: {r.text[:200]}"


def test_watchlist_structure_degrades_a_value_error_instead_of_500(client, monkeypatch):
    """`snapshot_of` 抛 `ValueError` 时这一行必须**降级**（200 + `missing: true`），不是 500。

    **这是护栏，不是「修复的证明」**：本用例在改 `watchlist_structure` 的 docstring
    **之前**就已通过 —— docstring 只是描述行为，改它不会让任何用例变绿。它守的是
    `api.py` 里那条 `except (DataSourceError, HTTPException, ValueError)`：
    删掉它，`SignalMode("qfq")` 这类 `ValueError` 会一路冒到 FastAPI 变成 500，
    而自选栏默认池有 7 只票 —— 用户看到的是整栏 500，不是一行「未同步」。

    历史背景：复权值原先也叫 `mode`，与口径参数同名会互相覆盖，把 `"qfq"`
    顶进 `SignalMode(...)`；这正是把复权值改叫 `adj` 的原因。
    """
    from chanlun.web import api as api_mod

    _clear_cache()
    client.post("/api/watchlist", json={"code": "600000", "name": "浦发银行"})

    def boom(*args, **kwargs):
        raise ValueError("'qfq' is not a valid SignalMode")

    monkeypatch.setattr(api_mod, "snapshot_of", boom)
    item = _watch_item(client, "600000")
    assert item["missing"] is True, item
    assert "'qfq' is not a valid SignalMode" in item["error"], item
