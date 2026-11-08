"""申万行业指数清单（`data/industry.py`）—— **内置静态表，不落库、不联网**。

清单内置而不是建一张 sqlite 表的理由（D-44）：搜索与取名都是热路径，
而申万分类几年才调一次；`DEFAULT_WATCHLIST` 也是同样的写法。
代价是清单会过期，所以 `--check` / `diff` 必须能把「过期」变成一句能看懂的话。

这个文件钉三件事：

1. **数量与级别**：一级 31 / 二级 124（实测数据商，2026-10-05）。
   数量写死是故意的 —— 申万真的调了分类，这条测试红了才是对的。
2. **代码空间**：落库键是 `sw.<6 位>`，一级码末位恒为 `0`、二级码末位 `1–9`，
   两套码**不相交**（实测，不是当成契约假设的）。
3. **`diff` 只报差异、不改清单**，且四种差异各有一种说法。

测试**不联网**：`_get_json` 是唯一的取数缝合点，联网路径单列在
`test_the_live_shape_is_parsed_from_a_fixture` 里喂假 body。
"""

from __future__ import annotations

import re

import pytest

from chanlun.data import industry
from chanlun.data.types import DataSourceError

#: 实测：`board_type=hy` 恰好 31 个、`hy2` 恰好 124 个（2026-10-05）。
L1_COUNT = 31
L2_COUNT = 124

CODE_RE = re.compile(r"^sw\.801\d{3}$")


# ---------------------------------------------------------------- 清单形状
def test_counts_match_the_measured_vendor_lists():
    assert len(industry.SW_L1) == L1_COUNT
    assert len(industry.SW_L2) == L2_COUNT
    assert len(industry.ALL) == L1_COUNT + L2_COUNT
    assert len(industry.all_boards()) == L1_COUNT + L2_COUNT


def test_every_code_is_a_sw_prefixed_six_digit_shenwan_code():
    for b in industry.ALL:
        assert CODE_RE.match(b.code), b
        assert industry.vendor_code(b.code) == "pt01" + b.code[3:]


def test_levels_are_the_two_we_support_and_each_board_carries_one():
    assert industry.LEVELS == ("1", "2")
    assert industry.LEVEL_NAMES == {"1": "一级行业", "2": "二级行业"}
    assert {b.level for b in industry.ALL} == {"1", "2"}
    assert len(industry.all_boards("1")) == L1_COUNT
    assert len(industry.all_boards("2")) == L2_COUNT


def test_the_two_levels_do_not_share_a_code():
    """★ 实测两套码不相交，但这条测试要**自己算一遍**，不许把实测当契约抄下来。

    一级 `801x x0` / 二级 `801x x1-9`，看起来天然不撞；万一哪天申万发一个
    末位为 0 的二级行业，`_BY_CODE` 会静默地少一个板块（后写覆盖先写），
    页面和 CLI 都会少列一个而没有任何报错。
    """
    l1 = {b.code for b in industry.all_boards("1")}
    l2 = {b.code for b in industry.all_boards("2")}
    assert l1 & l2 == set()
    assert len(industry._BY_CODE) == len(industry.ALL), "代码有重复，_BY_CODE 被覆盖过"


def test_level_one_codes_end_in_zero_and_level_two_do_not():
    """末位就是级别的指纹 —— `801120` 是一级，`801125` 是二级。"""
    assert all(b.code.endswith("0") for b in industry.all_boards("1"))
    assert not any(b.code.endswith("0") for b in industry.all_boards("2"))


def test_names_are_non_empty_and_unique_within_a_level():
    for lv in industry.LEVELS:
        names = [b.name for b in industry.all_boards(lv)]
        assert all(names), f"级别 {lv} 有匿名板块"
        assert len(set(names)) == len(names), f"级别 {lv} 有重名板块"


def test_the_bundle_is_sorted_by_name_within_each_level():
    """清单排好序，页面和 CLI 才能直接铺出去，不必各自再排一次。"""
    for lv in industry.LEVELS:
        names = [b.name for b in industry.all_boards(lv)]
        assert names == sorted(names), f"级别 {lv} 的清单没按名称排序"


def test_known_boards_are_where_we_think_they_are():
    """两个跨级别的锚点：一级食品饮料、二级白酒Ⅱ（实测都从数据商取到过）。"""
    assert industry.name_of("sw.801120") == "食品饮料"
    assert industry.level_of("sw.801120") == "1"
    assert industry.name_of("sw.801125") == "白酒Ⅱ"
    assert industry.level_of("sw.801125") == "2"


# ---------------------------------------------------------------- 查询接口
def test_lookups_are_case_and_whitespace_tolerant():
    assert industry.get("  SW.801120 ") is not None
    assert industry.is_industry("sw.801120")
    assert industry.name_of("SW.801120") == "食品饮料"


@pytest.mark.parametrize(
    "code",
    [
        "", "801120", "sw.8011", "sw.8011200", "sw.abcdef", "sh.801120",
        "sw.", "sw", "sw.801120.1", None,
    ],
)
def test_non_industry_codes_answer_empty_instead_of_raising(code):
    """**裸 `801120` 不算行业** —— `markets.HEAD2` 把 `8` 判成北交所。

    少写 `sw.` 前缀就会和北交所板块指数抢同一个键，所以这里必须是空答案。
    """
    assert industry.get(code) is None
    assert industry.name_of(code) == ""
    assert industry.level_of(code) == ""
    assert not industry.is_industry(code)


def test_a_bad_level_is_an_empty_list_not_an_exception():
    """`all_boards` 不猜级别：页面自己决定怎么提示，后端只如实回空。"""
    assert industry.all_boards("3") == []
    assert industry.all_boards("hy") == []
    assert industry.all_boards("") == list(industry.ALL)


def test_vendor_code_does_not_validate_against_the_bundle():
    """★ 清单过期时仍然要能取数 —— `vendor_code` 只拼字符串，不查表。

    反过来说：如果它去查表，一次分类调整就会让「取不到数据」和
    「清单没更新」变成同一个症状，`--check` 也就失去了意义。
    """
    assert industry.vendor_code("sw.999999") == "pt01999999"
    assert industry.vendor_code("sw.801120") == "pt01801120"
    assert industry.vendor_code("") == ""
    # 裸码**不给**符号：`801120` 在北交所名下也有含义（`markets.HEAD2` 把 `8` 判成 bj），
    # 这里顺手拼出 `pt01801120` 就等于替调用方猜了一次市场。宁可回空。
    assert industry.vendor_code("801120") == ""


def test_search_rows_look_like_universe_rows_with_an_industry_label():
    rows = industry.search_rows()
    assert len(rows) == len(industry.ALL)
    assert set(rows[0]) == {"code", "name", "market", "kind", "source"}
    assert {r["source"] for r in rows} == {"industry"}
    # 板块是**指数**，不是个股：按 `GP` 渲染会让页面把它当成一只股票。
    assert {r["kind"] for r in rows} == {"ZS"}
    assert {r["market"] for r in rows} == {"sw"}


# ---------------------------------------------------------------- diff
def test_diff_is_empty_when_the_bundle_matches_the_vendor():
    assert industry.diff(industry.all_boards("1")) == []


def test_diff_reports_added_removed_and_renamed_separately():
    ours = industry.all_boards("1")
    live = [b for b in ours if b.code != "sw.801120"]  # 少了食品饮料
    live = live + [industry.Board("sw.801999", "新行业", "1")]  # 多了一个
    live = [
        industry.Board(b.code, "改名了" if b.code == "sw.801010" else b.name, b.level)
        for b in live
    ]
    out = industry.diff(live)
    assert any("实测多出：sw.801999 新行业" in x for x in out)
    assert any("内置多出（实测没有）：sw.801120 食品饮料" in x for x in out)
    assert any("名称不一致：sw.801010" in x and "改名了" in x for x in out)
    assert len(out) == 3


def test_diff_compares_against_the_level_of_the_live_list_not_both():
    """只比 live 的级别。拿一级实测去比全部 155 个会把 124 个二级报成「实测没有」。"""
    out = industry.diff(industry.all_boards("1"))
    assert out == []
    assert not any("二级" in x for x in out)


def test_an_empty_live_list_is_reported_not_treated_as_agreement():
    """★ 「拉不到」绝不能读成「没问题」—— 空列表必须出声。"""
    out = industry.diff([])
    assert out and "实测列表为空" in out[0]


# ---------------------------------------------------------------- 联网路径（喂假 body）
def test_a_bad_level_never_touches_the_network(monkeypatch):
    def boom(url: str, timeout: int = 25) -> dict:
        raise AssertionError("非法级别不该联网")

    monkeypatch.setattr(industry, "_get_json", boom)
    with pytest.raises(DataSourceError, match="不认得的行业级别"):
        industry.fetch_live("3")


def test_the_live_shape_is_parsed_from_a_fixture(monkeypatch):
    """把实测响应的形状喂进去：`data.rank_list[].code` 带 `pt01` 前缀。

    `fetch_live` 一页 200 条、不足 200 就停 —— 这里喂 2 条，只该请求一次。
    """
    calls: list[str] = []

    def fake(url: str, timeout: int = 25) -> dict:
        calls.append(url)
        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "rank_list": [
                    {"code": "pt01801120", "name": "食品饮料", "stock_type": "BK-HY-1"},
                    {"code": "pt01801760", "name": "传媒", "stock_type": "BK-HY-1"},
                    {"code": "not-a-board", "name": "跳过我", "stock_type": "X"},
                ]
            },
        }

    monkeypatch.setattr(industry, "_get_json", fake)
    boards = industry.fetch_live("1")
    assert [b.code for b in boards] == ["sw.801120", "sw.801760"]
    assert [b.level for b in boards] == ["1", "1"]
    assert len(calls) == 1, "不足一页就该停，不必再翻下一页"
    assert "board_type=hy" in calls[0] and "count=200" in calls[0]


def test_the_live_level_maps_to_the_right_board_type(monkeypatch):
    """★ `hy` = 一级、`hy2` = 二级。写反了会静默地拿二级去核对一级清单。"""
    seen: list[str] = []

    def fake(url: str, timeout: int = 25) -> dict:
        seen.append(url)
        return {"data": {"rank_list": []}}

    monkeypatch.setattr(industry, "_get_json", fake)
    industry.fetch_live("1")
    industry.fetch_live("2")
    assert "board_type=hy&" in seen[0]
    assert "board_type=hy2&" in seen[1]


def test_a_response_without_a_rank_list_is_an_error_not_an_empty_answer(monkeypatch):
    """接口变了要和「这个级别真的没有板块」分开：前者是错，后者是空。"""
    monkeypatch.setattr(industry, "_get_json", lambda url, timeout=25: {"data": {}})
    with pytest.raises(DataSourceError, match="rank_list"):
        industry.fetch_live("1")

    monkeypatch.setattr(industry, "_get_json", lambda url, timeout=25: {"data": []})
    with pytest.raises(DataSourceError, match="没有 data 对象"):
        industry.fetch_live("1")


def test_paging_uses_offset_and_dedupes_by_code(monkeypatch):
    """一页正好 200 条时继续翻，靠 `offset` 前进；重复码只留一条。"""
    page1 = [{"code": f"pt0180{i:04d}", "name": f"行业{i}", "stock_type": "BK-HY-2"} for i in range(200)]
    page2 = [{"code": "pt01800000", "name": "行业0", "stock_type": "BK-HY-2"}]
    urls: list[str] = []

    def fake(url: str, timeout: int = 25) -> dict:
        urls.append(url)
        return {"data": {"rank_list": page1 if "offset=0" in url else page2}}

    monkeypatch.setattr(industry, "_get_json", fake)
    boards = industry.fetch_live("2")
    assert len(urls) == 2 and "offset=200" in urls[1]
    assert len(boards) == 200, "重复码必须只留一条"


def test_checked_on_is_a_plain_iso_date():
    """页面会把它印出来（「清单核对于 …」），所以必须是能读的日期字符串。"""
    import datetime as dt

    assert dt.date.fromisoformat(industry.CHECKED_ON).isoformat() == industry.CHECKED_ON
