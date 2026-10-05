"""`GET /api/search` 的接口契约（用户 2026-10-05 要的「按名称搜」）。

页面上的「搜索」有**两种来源**，这个端点把它们合成一个答案：

- `universe` —— 本地品种表。A 股的**全量**名单在这里，离线可用。
- `smartbox` —— 腾讯联想词。港股/美股**只有**这条路 —— 本系统不落它们的品种表，
  香港几千只、美股上万只，落不起。

三条契约值得单独钉住：

1. **返回的是落库键**（`hk.00700` / `us.dji` / `600000`），页面拿它直接取数 ——
   所以每一条都必须能过 `sources.normalize_code`。
2. **`skipped` 是给用户看的话，不是日志。** 数据商那里有、但本期不做的标的必须
   说出来；安静地丢掉会让用户以为搜索坏了。
3. **联想词不通不许让端点挂掉。** 本地结果照常返回，失败原因放 `error`。
   搜索是增强功能，不该让整个页面用不了。

测试**不联网**：`search._get_text` 是唯一的取数缝合点，全部挡在那里。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chanlun.config import load_config
from chanlun.data import meta, search, sources
from chanlun.data.types import DataSourceError
from chanlun.web.app import create_app


def _row(market: str, symbol: str, name: str, kind: str) -> str:
    return "~".join([market, symbol, name, "py", kind])


def _body(*rows: str) -> str:
    return 'v_hint="' + "^".join(rows) + '";'


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    base = load_config()
    conf = dataclasses.replace(base, data=dataclasses.replace(base.data, root=root))
    conn = meta.init(conf.data.meta_db)
    meta.upsert_universe(conn, [
        ("600000", "sh.600000", "浦发银行", "sh", "", 0),
        ("600519", "sh.600519", "贵州茅台", "sh", "", 0),
        ("000001", "sz.000001", "平安银行", "sz", "", 0),
    ])
    conn.close()
    return conf


@pytest.fixture()
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


@pytest.fixture()
def feed(monkeypatch):
    def _install(mapping: dict[str, str]):
        def fake(url: str, timeout: int = 20) -> str:
            import urllib.parse
            q = urllib.parse.unquote(url.split("q=", 1)[1].split("&", 1)[0])
            got = mapping.get(q, 'v_hint="N";')
            if isinstance(got, Exception):
                raise got
            return got

        monkeypatch.setattr(search, "_get_text", fake)

    return _install


# ---------------------------------------------------------------- 本地表
def test_search_by_name_hits_the_local_universe(client, feed):
    feed({})
    body = client.get("/api/search?q=浦发").json()
    assert body["query"] == "浦发"
    assert [i["code"] for i in body["items"]] == ["600000"]
    assert body["items"][0]["name"] == "浦发银行"
    assert body["items"][0]["source"] == "universe"
    assert body["error"] == "" and body["skipped"] == []


def test_search_by_code_prefix_still_works(client, feed):
    feed({})
    assert [i["code"] for i in client.get("/api/search?q=600").json()["items"]] == ["600000", "600519"]


def test_a_blank_query_is_an_empty_answer_not_a_500(client, feed):
    feed({})
    for url in ("/api/search", "/api/search?q=", "/api/search?q=%20%20"):
        body = client.get(url).json()
        assert body["count"] == 0 and body["items"] == []


def test_a_blank_query_does_not_touch_the_data_vendor(client, monkeypatch):
    def boom(url: str, timeout: int = 20) -> str:
        raise AssertionError("空查询不该联网")

    monkeypatch.setattr(search, "_get_text", boom)
    assert client.get("/api/search?q=").json()["count"] == 0


# ---------------------------------------------------------------- 港股 / 美股
def test_hk_and_us_names_come_from_the_vendor(client, feed):
    """★ 这条是这次新增功能的**主用例**：按中文名搜港股个股。

    港股个股在范围里（spec §2.1）。曾经 `_classify` 把**所有**非指数港股都当权证
    剔掉，于是搜「腾讯控股」得到零命中、理由还写着「港股权证」—— 假的。
    """
    feed({"腾讯": _body(_row("hk", "00700", "腾讯控股", "GP"))})
    items = client.get("/api/search?q=腾讯").json()["items"]
    assert [i["code"] for i in items] == ["hk.00700"]
    assert items[0]["source"] == "smartbox" and items[0]["market"] == "hk"


def test_every_returned_code_is_one_the_data_layer_accepts(client, feed):
    """页面点一下建议就直接取数 —— 带交易所后缀的 `us.aapl.oq` 混进来会 400。"""
    feed({
        "道琼斯": _body(_row("us", "dji", "道琼斯", "ZS"), _row("us", "ndaq.oq", "纳斯达克", "GP")),
        "恒生指数": _body(_row("hk", "HSI", "恒生指数", "ZS"), _row("hk", "13005", "腾讯法兴", "QZ")),
    })
    for q in ("道琼斯", "恒生指数"):
        for item in client.get(f"/api/search?q={q}").json()["items"]:
            assert sources.normalize_code(item["code"]) == item["code"]


def test_out_of_scope_hits_are_reported_not_swallowed(client, feed):
    """搜「苹果」：数据商有，本期不做 —— 必须**说出来**，否则用户以为搜索坏了。"""
    feed({"苹果": _body(_row("us", "aapl.oq", "苹果", "GP"))})
    body = client.get("/api/search?q=苹果").json()
    assert body["items"] == []
    assert body["skipped"] and "美股个股本期不做" in body["skipped"][0]
    assert "范围决策" in body["skipped"][0], "别把范围决策写成能力限制"


def test_a_no_result_query_is_an_empty_answer_not_an_api_error(client, feed):
    """★ 数据商搜不到时回 `v_hint="N";`（末尾多一个分号）。

    把它当畸形响应抛出去 ⇒ 每一次「搜不到」都被报成「接口坏了」。必须回空。
    """
    feed({"zzzznope": 'v_hint="N";'})
    body = client.get("/api/search?q=zzzznope").json()
    assert body["count"] == 0 and body["items"] == [] and body["error"] == ""


# ---------------------------------------------------------------- 降级
def test_the_vendor_being_down_does_not_break_the_endpoint(client, feed):
    feed({"浦发": DataSourceError("proxy down"), "腾讯": DataSourceError("proxy down")})
    body = client.get("/api/search?q=浦发").json()
    assert [i["code"] for i in body["items"]] == ["600000"], "本地结果必须还在"
    assert "proxy down" in body["error"]

    only_remote = client.get("/api/search?q=腾讯").json()
    assert only_remote["count"] == 0
    assert "proxy down" in only_remote["error"], "只靠联想词的查询也要把原因带出来，而不是 500"


def test_the_vendor_being_down_is_not_a_5xx(client, feed):
    feed({"腾讯": DataSourceError("proxy down")})
    assert client.get("/api/search?q=腾讯").status_code == 200


# ---------------------------------------------------------------- 形状
def test_limit_is_clamped_and_applied(client, feed):
    feed({})
    body = client.get("/api/search?q=银行&limit=2").json()
    assert body["count"] <= 2 and len(body["items"]) <= 2
    assert client.get("/api/search?q=浦发&limit=0").status_code == 422
    assert client.get("/api/search?q=浦发&limit=9999").status_code == 422


def test_response_keys_are_stable(client, feed):
    feed({})
    assert set(client.get("/api/search?q=浦发").json()) == {"query", "count", "items", "skipped", "error"}


def test_the_route_does_not_disturb_the_universe_route(client, feed):
    """`/api/universe` 是离线快路径，检索不许把它改了形状。"""
    feed({})
    uni = client.get("/api/universe?q=浦发").json()
    assert set(uni) == {"count", "items"}
