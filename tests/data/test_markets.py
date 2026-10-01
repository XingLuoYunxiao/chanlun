"""Task 23：号段 → 交易所的唯一判定表。

这张表的每一个分支都对应一次**真实踩过的坑**，而不是为了完整：
`920xxx`（北交所改号）被判成沪市 → 北交所票写进 `data/day/sh/`；
`88xxxx`（沪市板块）按 `8` 判成北交所；`000xxx` 在沪深两市都有含义，
裸码无法判定 —— 指数的键必须带前缀，否则 `sh.000001`（上证指数）和
`sz.000001`（平安银行）会抢同一个文件。
"""

from __future__ import annotations

import pytest

from chanlun.data import markets


@pytest.mark.parametrize(
    ("digits", "expected"),
    [
        ("600000", "sh"), ("688981", "sh"), ("510300", "sh"), ("900901", "sh"),
        ("000300", None), ("000001", None), ("113050", None), ("123456", None),
        ("300750", "sz"), ("200011", "sz"), ("399001", "sz"),
        ("159915", None),  # 深市 ETF，但 1 开头两市都有（沪市 11xxxx 转债）→ 不猜
        ("430047", "bj"), ("830799", "bj"), ("870508", "bj"), ("920017", "bj"),
        ("880001", "sh"), ("810001", "bj"), ("820001", "bj"),
        ("7", None), ("", None),
    ],
)
def test_market_of_bare(digits, expected):
    assert markets.market_of_bare(digits) == expected


def test_renumbered_bj_is_not_mistaken_for_shanghai():
    """北交所 2025 改号：43/83/87 → 920xxx。9 开头默认是沪市 B 股，必须特判。"""
    assert markets.market_of_bare("920017") == "bj"
    assert markets.market_of_bare("900901") == "sh"  # 沪市 B 股不受影响


def test_shanghai_board_prefix_is_not_bj():
    assert markets.market_of_bare("880001") == "sh"
    assert markets.market_of_bare("810001") == "bj"


def test_is_ambiguous_only_for_heads_shared_by_both_markets():
    assert markets.is_ambiguous("000001") is True
    assert markets.is_ambiguous("113050") is True
    assert markets.is_ambiguous("700001") is False  # 表里没有 = 判定不了，不是「两市都有」
    assert markets.is_ambiguous("600000") is False
    assert markets.is_ambiguous("920017") is False
    assert markets.is_ambiguous("") is False


def test_market_of_never_returns_none():
    assert markets.market_of("000300") == "sz"  # 判定不了时的兜底，落目录不能是 None
    assert markets.market_of("000300", default="sh") == "sh"


@pytest.mark.parametrize(
    ("market", "digits", "needed"),
    [
        ("sh", "600000", False),   # 裸码就指向沪市，前缀是冗余的
        ("sz", "000001", False),
        ("bj", "920017", False),
        ("sz", "399001", False),
        ("sh", "880001", False),
        ("sh", "000300", True),    # 裸码 000300 按约定是深市 → 必须带前缀
        ("sh", "000001", True),    # 上证指数与平安银行同号段
        ("sh", "113050", True),    # 沪市转债与深市转债同号段
        ("sz", "000300", False),   # 深市是约定解释，前缀冗余
        ("sz", "123456", False),
    ],
)
def test_needs_prefix(market, digits, needed):
    assert markets.needs_prefix(market, digits) is needed


@pytest.mark.parametrize(
    ("bs_code", "key"),
    [
        ("sh.600000", "600000"),
        ("sz.000001", "000001"),
        ("bj.920017", "920017"),
        ("sh.000300", "sh.000300"),
        ("sh.000001", "sh.000001"),
        ("sz.399001", "399001"),
        ("600000", "600000"),
        ("sh.", "sh."),
        ("", ""),
    ],
)
def test_store_key(bs_code, key):
    assert markets.store_key(bs_code) == key


@pytest.mark.parametrize(
    ("code", "is_idx"),
    [
        ("sh.000001", True),    # 上证指数
        ("sh.000300", True),    # 沪深300
        ("sh.000688", True),    # 科创50
        ("sz.399001", True),    # 深证成指
        ("sz.399006", True),    # 创业板指
        ("bj.899050", True),    # 北证50
        ("sh.600000", False),
        ("sz.000001", False),   # 平安银行：同一个号段，靠市场区分
        ("000001", False),      # 裸码判不了指数 —— 只能是股票
        ("399001", False),      # 深市裸码同理：保留前缀才认得出是指数
    ],
)
def test_is_index(code, is_idx):
    """指数靠**市场前缀 + 号段**判定：`sh.000xxx`/`sz.399xxx`/`bj.899xxx`。

    这件事关系到同步时的复权口径（指数没有除权，必须按不复权落库），
    也关系到裸码不能冒充指数（`000001` 是平安银行）。
    """
    assert markets.is_index(code) is is_idx
