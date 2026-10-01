"""号段 → 交易所的唯一判定表。

**为什么必须只有一处**：同一个判断在三个地方被用到，各写一份必然分叉 ——
`baostock_source.to_bs_code`（校验前缀，防 `sh.300059` 这种写错交易所的代码被
baostock 静默当成空数据）、`store.market_of`（决定落哪个目录）、以及同步/Web 层
决定「这个代码要不要保留 `sh.` 前缀才不撞车」。分叉的后果不是报错，而是**读错文件**：
`sh.000300`（沪深300指数）和 `sz.000001`（平安银行）都可能被落到别人的文件上，
页面安静地画出一条不属于这只票的曲线。

**裸码不是总能判定市场**，这是本表的重点：

- `000001` 既可能是 `sh.000001`（上证指数）也可能是 `sz.000001`（平安银行）。
  这种号段在 `HEAD1` 里记 `None` = 无法判定。指数的键**必须带前缀**（见
  `needs_prefix`），否则两种含义会抢同一个文件。
- `920xxx` 是北交所 2025 年改号后的新号段（老的 `43/83/87xxxx` 同批平移，
  实测 222 对重叠区间收盘价逐日相同）。若不特判，`9` 开头会被判成沪市，
  北交所票会写进 `data/day/sh/`。
- `88xxxx` 是沪市板块指数，`81/82xxxx` 是北交所板块指数；`88` 不能按 `8` 判成北交所。
"""

from __future__ import annotations

#: 两位前缀优先于一位前缀判定（先查 HEAD2 再查 HEAD1）。
HEAD2: dict[str, str] = {
    "92": "bj",  # 北交所改号后号段（43/83/87 → 920xxx）
    "88": "sh",  # 沪市板块指数
    "81": "bj",  # 北交所板块指数
    "82": "bj",
}

#: 一位前缀。值为 ``None`` 表示「该号段两个市场都有，无法只凭数字判定」。
HEAD1: dict[str, str | None] = {
    "6": "sh",  # 沪市 A 股 / 科创板 688
    "5": "sh",  # 沪市基金
    "9": "sh",  # 沪市 B 股 900xxx（920xxx 已在 HEAD2 拦下）
    "4": "bj",  # 北交所旧号段 43/83/87 中的 43
    "8": "bj",  # 北交所旧号段 83/87（88/81/82 已在 HEAD2 拦下）
    "0": None,  # sh.000001 上证指数 / sz.000001 平安银行
    "1": None,  # sh.113050 沪市转债 / sz.123456 深市转债
    "2": "sz",  # 深市 B 股 200xxx
    "3": "sz",  # 创业板 300/301、深证成指 399
}
# 注：「表里没有的号段」与「表里记 None 的号段」不是一回事：
# 前者是**根本判定不了**（如 7 开头的申购/配售代码），后者是**两市都有同号段**
# （0/1 开头），后者按约定解释为深市（`BARE_DEFAULT`），前者必须报错让人写前缀。


def market_of_bare(digits: str) -> str | None:
    """只看数字判市场；判定不了返回 ``None``（不要猜）。"""
    text = str(digits or "").strip().lower()
    if not text:
        return None
    if len(text) >= 2 and text[:2] in HEAD2:
        return HEAD2[text[:2]]
    return HEAD1.get(text[0])


#: 两市同号段（0/1 开头）时的约定解释。裸码 `000001` 按深市（平安银行）解释，
#: 想要上证指数必须写 `sh.000001` —— 这个约定同时决定了落库键与文件名，只有一处。
BARE_DEFAULT = "sz"


def market_of(digits: str, default: str = BARE_DEFAULT) -> str:
    """判定不了时给一个具体市场（落目录/算键用，不能是 ``None``）。"""
    return market_of_bare(digits) or default


def is_ambiguous(digits: str) -> bool:
    """两市都有同号段（0/1 开头）：裸码不能唯一指向一个市场。"""
    text = str(digits or "").strip().lower()
    if not text:
        return False
    if len(text) >= 2 and text[:2] in HEAD2:
        return False
    return text[0] in HEAD1 and HEAD1[text[0]] is None


def needs_prefix(market: str, digits: str) -> bool:
    """裸数字不足以表达这个市场时，代码键**必须**带前缀。

    `sh.600000` → False（裸码 `600000` 就指向沪市，前缀是冗余的，去掉才和一期
    已落库的数据键一致）；`sh.000300` → True（裸码 `000300` 会被判成深市，
    带上前缀才能和 `sz.000300` 区分开）；`sh.113050` → True（裸码 `113050` 按约定
    是深市转债，与 `sz.123456` 同号段，不去前缀就会和深市转债抢文件）。

    判据是「去掉前缀后落点是否还是同一个市场」——用**确定性**判定（`market_of`）
    而不是 `market_of_bare`：两者相等时，裸键与带前缀键必然落到同一个文件，
    保留前缀纯属冗余；不等时，裸键会落到别人家去。这样既不会撞车，也不会多造键。
    """
    return market_of(digits) != str(market).strip().lower()


def store_key(bs_code: str) -> str:
    """`sh.600000` → `600000`（一期键不变）；`sh.000300` → `sh.000300`（指数带前缀）。

    行情源普遍用「市场.数字」表示代码，而落库键要尽量短、又要不撞车。规则只有一条：
    裸数字能唯一指向该市场就用裸数字，否则保留前缀。
    """
    text = str(bs_code or "").strip().lower()
    if "." not in text:
        return text
    market, _, digits = text.partition(".")
    if not digits:
        return text
    return text if needs_prefix(market, digits) else digits
