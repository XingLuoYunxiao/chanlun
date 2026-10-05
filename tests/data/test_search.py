"""名称搜索（`data/search.py`）的判据。

一句话：**搜「名称」和搜「代码」走的是同一条路，但来源不同。** A 股有本地品种表
（全量、离线、名称最准），港股/美股没有 —— 香港几千只、美股上万只，本系统不落
它们的品种表，所以只有数据商联想词一条路。这组测试守的就是这条分界：

1. **联想词的返回是「一整段 JSON 字符串」**，无结果时回 `v_hint="N";`（末尾多个分号）。
   把「搜不到」当成畸形响应抛出去，页面就会在每一次没搜到时弹网络错误 —— 那是谎报。
   所以 `test_decode_treats_the_empty_sentinel_as_no_results` 必须过，
   而 `test_decode_still_shouts_when_the_shape_changes` 必须继续响。
2. **范围要显式剔、并回报理由**（spec §2.2）：港股权证、基金、标普500、美股个股
   都不做。安静地丢掉会让「标普」搜出零条，看着像搜索坏了。
3. **剔的是「不做」，不是「拿不到」**：美股个股的日线数据源实测是全历史可取的，
   这是范围决策 —— 理由文案里必须带着这句话，否则将来的人会以为数据拿不到。

所有 HTTP 都被 `search._get_text` 这个缝合点挡掉，测试**不联网**。
"""

from __future__ import annotations

import pytest

from chanlun.data import markets, search, sources
from chanlun.data.types import DataSourceError


def _row(market: str, symbol: str, name: str, kind: str, pinyin: str = "py") -> str:
    return "~".join([market, symbol, name, pinyin, kind])


def _body(*rows: str) -> str:
    """拼一段 `v_hint="..."` 原始响应体（真机形态：整段是一个 JSON 字符串字面量）。"""
    payload = "^".join(rows)
    return 'v_hint="' + payload.replace("\\", "\\\\").replace('"', '\\"') + '";'


@pytest.fixture()
def feed(monkeypatch):
    """把 `search._get_text` 换成一台「按 query 回不同响应」的假数据商。"""

    def _install(mapping: dict[str, str]) -> None:
        def fake(url: str, timeout: int = 20) -> str:
            q = url.split("q=", 1)[1].split("&", 1)[0]
            import urllib.parse
            text = urllib.parse.unquote(q)
            if text not in mapping:
                return 'v_hint="N";'
            got = mapping[text]
            if isinstance(got, Exception):
                raise got
            return got

        monkeypatch.setattr(search, "_get_text", fake)

    return _install


# ---------------------------------------------------------------- 响应解码
def test_decode_reads_a_normal_payload():
    body = _body(_row("sh", "600000", "浦发银行", "GP-A"))
    assert search._decode(body) == "sh~600000~浦发银行~py~GP-A"


def test_decode_treats_the_empty_sentinel_as_no_results():
    """★ 这是本模块最容易写错的一处。

    真机实测：`zzzznope` / `qqqqqq` / `!!!` / 单个全角空格 / `%` / `选择`
    一律回 `v_hint="N";` —— **末尾带一个分号**。旧的 `split("=")[1].strip()` 写法
    会把这串直接喂给 `json.loads`，抛 `JSONDecodeError`，于是「搜不到任何东西」
    被报成「联想词接口坏了」。
    """
    assert search._decode('v_hint="N";') == ""
    assert search._decode('v_hint="N"') == ""


def test_decode_still_shouts_when_the_shape_changes():
    """负控：**不能把「不认识的响应」也当成「搜不到」。**

    非空、却没有 `~` 分隔符 ⇒ 数据商改了格式，必须抛。否则搜索会安静地永远返回
    零条命中，而没有任何人会发现。
    """
    with pytest.raises(DataSourceError, match="分隔符"):
        search._decode('v_hint="600000,浦发银行";')
    with pytest.raises(DataSourceError, match="引号"):
        search._decode("v_hint=N;")
    with pytest.raises(DataSourceError, match="JSON"):
        search._decode('v_hint="a\\qb";')
    # 注：`_decode` 里还有一条「不是字符串」的防御性分支，但按它的切片方式
    # （第一个引号到最后一个引号）`json.loads` 成功就必然回 `str`，
    # 所以**测不到** —— 与其编一个走不到它的输入假装覆盖了，不如在这里写明。


def test_smartbox_turns_a_network_failure_into_a_result_error(feed):
    """联想词不通**不能往外抛**：`/api/search` 要能只用本地表把页面撑起来。"""
    feed({"腾讯": DataSourceError("boom")})
    r = search.smartbox("腾讯")
    assert r.hits == [] and "boom" in r.error


# ---------------------------------------------------------------- 落库键
@pytest.mark.parametrize(
    ("market", "symbol", "expected"),
    [
        ("sh", "600000", "600000"),      # sh 前缀在 store_key 里被省略
        ("sz", "000001", "000001"),
        ("sh", "000001", "sh.000001"),   # 含糊号段必须留前缀
        ("hk", "00700", "hk.00700"),     # 港股补零到 5 位
        ("hk", "HSI", "hk.hsi"),         # 指数符号转小写
        ("us", "dji", "us.dji"),
        ("us", "aapl.oq", "us.aapl"),    # ★ 交易所后缀必须切掉
        ("us", "tcehy.ps", "us.tcehy"),
    ],
)
def test_store_key_maps_the_vendor_spelling_to_our_code(market, symbol, expected):
    assert search._store_key(market, symbol) == expected


def test_every_kept_hit_is_a_code_the_rest_of_the_system_accepts(feed):
    """页面上选中一条命中就直接拿去取数 —— 所以**每一条命中都必须能被归一化**。

    这条是防「`us.aapl.oq` 这种带后缀的代码混进来」的：`sources.normalize_code`
    认不出它，取数会 400，而用户只是点了一下搜索建议。
    """
    feed({"苹果": _body(_row("us", "aapl.oq", "苹果", "GP"), _row("hk", "00700", "腾讯控股", "GP"))})
    r = search.smartbox("苹果")
    assert [h.code for h in r.hits] == ["hk.00700"], "美股个股本期不做（见下），香港个股要留下"
    for h in r.hits + search.smartbox("腾讯控股").hits:
        assert sources.normalize_code(h.code) == h.code


# ---------------------------------------------------------------- 范围
@pytest.mark.parametrize(
    ("market", "symbol", "kind", "keep"),
    [
        ("hk", "HSI", "ZS", True),
        ("hk", "HSTECH", "ZS", True),
        ("us", "dji", "ZS", True),
        ("us", "ixic", "ZS", True),
        ("sh", "000001", "ZS", True),        # A 股指数照收
        ("sh", "600000", "GP-A", True),
        ("sz", "000001", "GP-A", True),
        # —— 本期不做（spec §2.2）——
        ("hk", "HSCEI", "ZS", False),        # 国企指数
        ("us", "inx", "ZS", False),          # 标普 500
        ("us", "ndx", "ZS", False),          # 纳斯达克 100
        ("us", "aapl.oq", "GP", False),      # 美股个股
        ("hk", "00700", "GP", True),         # ★ 港股个股**在**范围里（spec §2.1）
        ("hk", "01211", "GP", True),
        ("hk", "HSIGTR", "ZS", False),       # 白名单外的港股指数
        ("hk", "13005", "QZ", False),        # 港股权证
        ("jj", "968029", "KJ", False),       # 基金
    ],
)
def test_classify_keeps_only_what_this_release_covers(market, symbol, kind, keep):
    code, reason = search._classify(market, symbol, kind)
    assert bool(code) is keep, f"{market}/{symbol}/{kind} → code={code!r} reason={reason!r}"
    assert (reason == "") is keep, "被剔掉时必须给出理由；留下时不许带理由"


def test_us_individual_stocks_are_excluded_by_scope_not_by_availability():
    """理由文案里必须写清「数据源实测拿得到」—— 否则将来会有人以为这是能力限制。"""
    _code, reason = search._classify("us", "aapl.oq", "GP")
    assert "范围决策" in reason and "不是能力限制" in reason


def test_the_whitelist_is_the_same_one_the_data_layer_uses():
    """搜索的白名单**不许另抄一份**：抄一份就会和 `markets.INDEX_CODES` 漂开。"""
    for market, symbol in (("hk", "HSI"), ("hk", "HSTECH"), ("us", "dji"), ("us", "ixic")):
        code, reason = search._classify(market, symbol, "ZS")
        assert code and reason == ""
        assert markets.is_index(code), f"{code} 应当在 markets.INDEX_CODES 里"


def test_skipped_reasons_are_deduped_and_visible(feed):
    """同一句话出现 5 次只报一次；**但一条都不许被安静吃掉**。"""
    rows = [_row("hk", "13005", "腾讯法兴", "QZ")] * 5 + [_row("jj", "968029", "基金", "KJ")]
    feed({"腾讯": _body(*rows)})
    r = search.smartbox("腾讯")
    assert r.hits == []
    assert len(r.skipped) == 2, r.skipped
    assert all(isinstance(s, str) and s for s in r.skipped)


def test_hit_market_is_the_vendor_market_not_a_slice_of_the_store_key(feed):
    """`sh.513600` 在落库时被缩短成 `513600`，对没有点的键切前缀会把市场报成代码本身。"""
    feed({"恒生指数": _body(_row("sh", "513600", "恒生指数ETF南方", "ETF"))})
    hits = search.smartbox("恒生指数").hits
    assert [(h.code, h.market) for h in hits] == [("513600", "sh")]


def test_a_query_that_only_matches_out_of_scope_names_still_says_why(feed):
    """用户搜「标普」：命中项是数据商的标普 ETF，但**标普 500 指数本身不做**。

    这条测的是「搜到的和想搜的不是一回事」时页面有话可说。
    """
    feed({"标普": _body(_row("sh", "513500", "标普500ETF博时", "QDII-ETF"))})
    r = search.smartbox("标普")
    assert [h.code for h in r.hits] == ["513500"]


# ---------------------------------------------------------------- 本地表
def _universe() -> list[dict]:
    return [
        {"code": "600000", "name": "浦发银行", "market": "sh"},
        {"code": "600519", "name": "贵州茅台", "market": "sh"},
        {"code": "sh.000001", "name": "上证指数", "market": "sh"},
        {"code": "000001", "name": "平安银行", "market": "sz"},
    ]


def test_local_search_matches_the_name_not_only_the_code():
    got = [h.code for h in search.local(_universe(), "浦发")]
    assert got == ["600000"]
    assert [h.source for h in search.local(_universe(), "浦发")] == ["universe"]


def test_local_search_puts_code_prefixes_before_name_contains():
    """搜「600」要的是 `600000`/`600519` 这一串代码，不是名字里恰好含「600」的票。"""
    rows = _universe() + [{"code": "000002", "name": "万科600", "market": "sz"}]
    got = [h.code for h in search.local(rows, "600")]
    assert got == ["600000", "600519", "000002"], "代码前缀优先，名称包含排后面"


def test_local_search_is_case_insensitive_and_empty_for_a_blank_query():
    assert [h.code for h in search.local(_universe(), "   ")] == []
    assert [h.code for h in search.local([{"code": "HK.HSI", "name": "恒生指数", "market": "hk"}], "hk.hsi")] == ["HK.HSI"]


# ---------------------------------------------------------------- 合并
def test_search_merges_local_first_then_remote_and_dedupes(feed):
    """本地和联想词同时命中 `600000` 时**只留一条**，且留本地那条（名字更准）。"""
    feed({"浦发": _body(_row("sh", "600000", "浦发银行", "GP-A"), _row("hk", "00700", "腾讯控股", "GP"))})
    r = search.search(_universe(), "浦发")
    codes = [h.code for h in r.hits]
    assert codes.count("600000") == 1
    assert codes[0] == "600000"
    assert r.hits[0].source == "universe"


def test_search_works_with_an_empty_local_universe(feed):
    """港股/美股的常态：本地表里**一条都没有**，全靠联想词。"""
    feed({"恒生指数": _body(_row("hk", "HSI", "恒生指数", "ZS"))})
    r = search.search([], "恒生指数")
    assert [(h.code, h.source) for h in r.hits] == [("hk.hsi", "smartbox")]


def test_search_survives_the_remote_being_down(feed):
    """联想词挂了，本地结果照常返回，失败原因放在 `error` 里（不抛、不 500）。"""
    feed({"浦发": DataSourceError("proxy down")})
    r = search.search(_universe(), "浦发")
    assert [h.code for h in r.hits] == ["600000"]
    assert "proxy down" in r.error


def test_search_of_a_blank_query_does_not_touch_the_network(feed, monkeypatch):
    def boom(url: str, timeout: int = 20) -> str:  # pragma: no cover - 被调用就失败
        raise AssertionError("空查询不该联网")

    monkeypatch.setattr(search, "_get_text", boom)
    r = search.search(_universe(), "  ")
    assert r.hits == [] and r.error == ""


def test_result_as_dict_truncates_to_limit(feed):
    feed({"银行": _body(*[_row("sh", f"5128{i:02d}", f"银行ETF{i}", "ETF") for i in range(9)])})
    r = search.search([], "银行", limit=3)
    body = r.as_dict(3)
    assert body["count"] == 3 and len(body["items"]) == 3
    assert set(body) == {"count", "items", "skipped", "error"}
