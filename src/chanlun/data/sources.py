"""市场分派层：**唯一**的取数入口。

`api.py` / `sync.py` / `__main__.py` 都从这里拿 `fetch_bars` 与 `normalize_code`，
不再直接从 `baostock_source` 拿。

分层（避免循环 import）：

```
markets.py         ← 只认号段与前缀，不 import 任何数据源
industry.py        ← 申万行业清单（纯静态，不联网）
store.py           ← import markets
baostock_source.py ← import markets, types
tencent_source.py  ← import markets, types
sina_source.py     ← import markets, types
sw_source.py       ← import markets, types（申万行业板块日线）
sources.py         ← import 上面全部（唯一的分派点）
```

**符号映射不猜**：落库键（小写、带市场前缀）→ 数据商要的写法，
三个坑都在 `vendor_symbol` 里标出来了。

**口径也不猜**：`adjust_for` 把调用方要的口径收敛到该市场**实际可用**的口径。
港股只有一个可用口径（不复权），见 `tencent_source` 的模块 docstring 与 D-43；
申万行业板块是指数，没有复权口径，见 D-44。
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from . import (
    eastmoney_source,
    industry,
    markets,
    periods,
    pytdx_source,
    store,
    sw_source,
    tencent_source,
)
from .baostock_source import fetch_bars as _baostock_fetch
from .baostock_source import to_bs_code
from .eastmoney_source import fetch_bars as _eastmoney_fetch
from .pytdx_source import fetch_bars as _pytdx_fetch
from .sina_source import fetch_bars as _sina_fetch
from .sw_source import fetch_bars as _sw_fetch
from .tencent_source import fetch_bars as _tencent_fetch
from .types import DataSourceError

log = logging.getLogger(__name__)

#: 每个市场由哪个源负责。`sh/sz/bj` 是 A 股，走 baostock；
#: `sw` 是申万行业板块（一级/二级行业指数），走腾讯板块接口。
SOURCE_OF_MARKET: dict[str, str] = {
    "sh": "baostock",
    "sz": "baostock",
    "bj": "baostock",
    "hk": "tencent",
    "us": "sina",
    "sw": "sw",
}

#: 支持的市场。**落库键的第一个点之前必须是其中之一**。
MARKETS: tuple[str, ...] = tuple(SOURCE_OF_MARKET)

#: 日线（含 `d` 简写）与派生周期（周/月，本地从日线聚合）。
_DAY_PERIODS = ("day", "d")
_DERIVED_PERIODS = ("week", "w", "month", "m")


def market_of(code: str) -> str:
    """落库键 → 市场。**必须用 `store.market_of`，不能用 `markets.market_of`** ——
    后者只查号段表、不认前缀，对 `hk.00700` 会返回 `"sz"`（spec §7.2 坑 1）。"""
    return store.market_of(code)


def source_of(code: str) -> str:
    """这个落库键**按市场**由哪个源负责。未知市场报错，不猜。

    注意它**不看周期**：分钟线的分流走 `source_for(code, period)`。
    这个函数保留，是因为「这只票是谁在供数」这个问题的答案在市场层面仍然成立
    （`__main__` 的指数诊断文案用它），而且它是 `source_for` 的兜底。
    """
    mkt = market_of(code)
    src = SOURCE_OF_MARKET.get(mkt)
    if src is None:
        raise DataSourceError(f"未知市场 {mkt!r}（代码 {code!r}）")
    return src


def source_for(code: str, period: str) -> str:
    """**按时段分流的取数源** —— 同一市场里不同标的的分钟线可得性不同。

    只有分钟周期才分流（日线各市场的既有源都更好，不动）：

    - `sh`/`sz` 的**指数**：baostock 对指数分钟线一律 **0 行**，改走东财。
      判据是 `_is_index_key`（**品种**分类），不是号段猜；
    - `sh`/`sz`/`bj` 的**股票**：baostock 有 6.5 年分钟历史，而东财只有约 2 个月
      —— 仍走 baostock，不为了「统一」把好的换成差的。

    **`sw`（申万行业板块）不在这里分流，分钟线仍然没有源。** 曾经以为
    `secid=90.801010` 就是申万 —— **那是错的**：`90.` 是东财**自己的板块空间**，
    同号不同物。实测 `90.801010` 回的是「磷化工」、`90.801080` 回「新型城镇化」、
    `90.801012` 回「PVDF概念」，与申万的农林牧渔/电子/农产品加工**毫无关系**。
    按那个映射导进去的是**张冠李戴的行情**，比没有数据危险得多。
    申万的分钟线目前无解，`capabilities` 如实回 `supported=False`。
    """
    key = normalize_code(code)
    mkt = market_of(key)
    p = str(period)

    # ① **1 分钟：只有 pytdx 有。** baostock 报 10004012、腾讯 320 根、
    #    新浪 1023 根、东财只留 1 个交易日（240 根）。这是本项目此前最大的数据空白。
    if p == "1" and mkt in ("sh", "sz"):
        return "pytdx"

    # ② **指数的分钟：pytdx 比东财深得多**（5 分钟 2 年 vs 2 个月、
    #    1 分钟 4.5 个月 vs 1 天），所以优先 pytdx。
    #    只限**分钟**周期：`pytdx_source.CATEGORY` 还含 day/week/month，
    #    而日线 baostock 有全历史、pytdx 只有 8000 来根 —— 踩过：用 CATEGORY
    #    判断会把指数**日线**也切走，静默丢掉几十年历史。
    if p in pytdx_source.MINUTE_PERIODS and mkt in ("sh", "sz") and _is_index_key(key):
        return "pytdx" if pytdx_source.available() else "eastmoney"

    # ③ **股票的 5/15/30/60 分钟仍走 baostock**：它有 6.5 年，pytdx 只有 2 年。
    #    不为了「统一源」把更好的换成更差的。
    return source_of(key)


def _is_index_key(key: str) -> bool:
    """**已归一的落库键**是不是 A 股指数。

    不能直接 `markets.is_index(key)`：那个谓词要的是「用户写法」，对**裸码**一律
    返回 `False`（它的 docstring 写着「A 股必须带市场前缀才判得出」）。而归一之后
    `sz.399001`（深证成指）**会变成裸码** —— 39 号段深市独有，前缀被判为冗余
    （见 `tdx.store_key`）。漏了这一步，深市指数会被判成股票、继续走 baostock，
    然后拿到 0 行，而『指数没有分钟源』这条旧结论会被误以为仍然成立。

    所以这里按市场把前缀补回去再交给同一个谓词 —— 判据只有一处实现。
    """
    mkt = market_of(key)
    bare = store.bare_code(key)
    return markets.is_index(f"{mkt}.{bare}")


def vendor_symbol_for(code: str, src: str) -> str:
    """按**已经定好的源**产出取数符号。

    分流之后「市场 → 符号」不再一对一：`sw.801010` 走腾讯要 `pt01801010`，
    走东财却是 `90.801010`。所以符号必须跟着源走，不能再只看市场。
    """
    key = normalize_code(code)
    if src == "eastmoney":
        return eastmoney_source.secid(key)
    if src == "pytdx":
        return key          # pytdx 源按「市场 + 裸码」自己拆，不需要外部拼符号
    return vendor_symbol(key)[1]


def vendor_symbol(code: str) -> tuple[str, str]:
    """`(源名, 取数符号)`。落库键 → 数据商要的写法。

    坑 1：必须用 `store.market_of`，不能用 `markets.market_of`。
    坑 2：落库键是小写的，但腾讯要大写 —— `hk.hsi` → `hkHSI`。
          少了 `.upper()` 会**静默拿到空数据**（`param error`）。
    坑 3：新浪美股**指数**必须带点前缀（`.DJI`），**个股不带**（`AAPL`）。
          实测裸 `DJI` 返回 `var x=(null);`，带点才有数据。
    坑 4：申万行业板块要 `pt01` + 申万 6 位码（`sw.801120` → `pt01801120`）。
          漏掉 `pt01` 前缀会回 `bad params`，不会回错数据 —— 但仍算一个映射坑。
    """
    key = normalize_code(code)
    bare = store.bare_code(key)
    mkt = market_of(key)
    if mkt == "hk":
        # hk.00700 → hk00700（腾讯港股符号不带点）；hk.hsi → hkHSI
        return "tencent", "hk" + bare.upper()
    if mkt == "us":
        # 指数带点：us.dji → .DJI；个股不带：us.aapl → AAPL
        return "sina", ("." + bare.upper()) if markets.is_index(key) else bare.upper()
    if mkt == "sw":
        # sw.801120 → pt01801120
        return "sw", industry.vendor_code(key)
    return "baostock", to_bs_code(key)


def endpoint(code: str, period: str, adjust: str) -> dict[str, str]:
    """`(控制器, 复权词, 期望键名)` —— spec §7.2 的端点/口径分派表。

    这张表存在的原因：**光有符号不够，还得选对控制器**。
    腾讯把口径写在**控制器名**上：`fqkline` 完全忽略复权词并返回**不复权**价，
    `hkfqkline` 才认 `qfq`/`hfq`。选错的后果不是报错，是把一种口径的价格当成另一种存。

    港股本期只落不复权（`tencent_source` 模块 docstring 的三条实测），
    所以这里只报 `fqkline` + 键名 `day`。实现委托给 `tencent_source.plan` ——
    **口径分派只允许有一份实现**，两份会漂移，而漂移的后果是静默的口径错误。

    申万行业板块同理另有一个控制器：`newfqkline`（`fqkline` 对板块只回 1 根，
    见 `sw_source` docstring），键名同样是 `day`，复权词留空。
    """
    src = source_of(code)
    if src == "sw":
        return {
            "controller": _short(sw_source.BOARD_KLINE),
            "adjust_word": "",
            "expected_key": sw_source.EXPECTED_KEY,
        }
    if src != "tencent":
        return {"controller": src, "adjust_word": "", "expected_key": ""}
    if str(period) not in _DAY_PERIODS and not markets.is_index(code):
        raise DataSourceError(f"腾讯港股源只提供日线，不提供 {period}（spec §3.2）")
    ctrl, word, expect = tencent_source.plan()
    return {"controller": _short(ctrl), "adjust_word": word, "expected_key": expect}


def _short(endpoint_url: str) -> str:
    """`https://ifzq.gtimg.cn/appstock/app/fqkline/get` → `fqkline`。"""
    return endpoint_url.rsplit("/", 2)[-2]


def adjust_for(code: str, adjust: str, period: str | None = None) -> str:
    """这只票实际能用哪个口径。`adjust` 是 baostock 口径码（"2" 前 / "1" 后 / "3" 不复权）。

    规则：
    - A 股：原样返回（baostock 三个口径都支持）。
    - 港股（指数与个股）：`"3"` —— 腾讯的港股复权序列**实测不可用**
      （前复权 2013-05 前为负、后复权不是因子阶梯，见 `tencent_source` docstring）。
    - 美股（指数与个股）：`"3"` —— 新浪美股源只有原始价。
    - 申万行业板块：`"3"` —— 板块是**指数**，指数没有除权除息，
      与 A 股指数走同一规则（D-19）。

    收敛而不是报错：调用方（`sync` / `api`）默认传 A 股的 `"2"`，
    让它们不必知道每个市场的口径细节。真要一种源给不出的口径，
    由源自己报错（`tencent_source.fetch_bars` / `sina_source.fetch_bars` 都会拒绝）。
    """
    mkt = market_of(code)
    # pytdx 的周期**只有不复权**（在线行情不提供复权序列），所以走 pytdx 的那几个
    # 组合必须在这里收敛到 "3" —— 否则 `sync` 默认传 A 股的 "2"，源会拒收，
    # 而用户看到的只是一只票「失败」，看不出是口径问题（实测踩过）。
    if period is not None and source_for(code, period) == "pytdx":
        return "3"
    if mkt in ("sh", "sz", "bj"):
        # 指数没有除权除息：落「前复权」会让 `sync_state.adjust` 写着「前复权」，
        # 页面照抄，而那个概念对指数根本不成立（D-19）。
        return "3" if markets.is_index(code) else str(adjust)
    return "3"



def normalize_code(code: str) -> str:
    """用户能输入的任何写法 → 规范落库键。

    接受的写法（spec §4.2）：`600000` / `sh.600000` / `sh600000` / `hk.00700` /
    `hk.HSI` / `us.DJI` / `us.dji` / `sw.801120`。

    规范形式一律**小写 + 点号 + 市场前缀**。

    A 股沿用 `to_bs_code` 的号段校验（它认得 6 位数字与歧义号段），
    港股/美股/申万行业用号段正则校验。**未知写法报错，不猜市场** ——
    猜错会把一只票的数据写到另一个市场目录下。

    **裸 `801120` 不是行业板块**：它 6 位数字、走 A 股分支，
    `HEAD2` 把 `81` 判成北交所板块指数。行业板块**必须**写 `sw.801120`
    （与 `sh.000001` 必须带前缀同理，见 `industry.py` docstring）。
    """
    text = str(code).strip()
    if not text:
        raise DataSourceError("代码不能为空")

    low = text.lower()
    # 拆前缀：优先点号，其次**直接相连的两字母市场码**（`sh600000` / `hk00700`）。
    # A 股裸码全是数字，港股裸码是 5 位数字，所以两字母开头不可能是裸码，无歧义。
    if "." in low:
        mkt, _, bare = low.partition(".")
    elif len(low) > 2 and low[:2] in SOURCE_OF_MARKET:
        mkt, bare = low[:2], low[2:]
    else:
        mkt, bare = "", low
    bare = bare.strip()

    if mkt:
        if mkt not in SOURCE_OF_MARKET:
            raise DataSourceError(
                f"无法识别的市场前缀 {mkt!r}（可用: {', '.join(MARKETS)}）"
            )
        if not bare:
            raise DataSourceError(f"代码缺少证券部分: {code!r}")
        if mkt in ("sh", "sz", "bj"):
            # 交给 to_bs_code 做号段校验（它认得 6 位数字与歧义号段）。
            # 它抛的是 ValueError，这里统一收敛成 DataSourceError —— 调用方
            # （web/API、CLI）只该认一种异常类型，否则「前缀与号段矛盾」
            # 会绕过 400 直接变成 500。
            try:
                return markets.store_key(to_bs_code(f"{mkt}.{bare}"))
            except ValueError as exc:
                raise DataSourceError(str(exc)) from exc
        if mkt == "hk" and not markets.HK_SYMBOL_RE.match(bare):
            raise DataSourceError(f"{code!r} 不是合法的港股代码（5 位数字或指数字母代码）")
        if mkt == "us" and not markets.US_SYMBOL_RE.match(bare):
            raise DataSourceError(f"{code!r} 不是合法的美股代码（字母开头，可含 . 与 -）")
        if mkt == "sw" and not markets.SW_SYMBOL_RE.match(bare):
            raise DataSourceError(
                f"{code!r} 不是合法的申万行业代码（形如 sw.801120，见 `python -m chanlun industry`）"
            )
        return f"{mkt}.{bare}"

    # 无前缀：只可能是 A 股。**绝不能把 5 位数字当港股** ——
    # `markets.market_of_bare` 对它返回 None，兜底成 "sz"，会把港股数据写到深市目录。
    if not (len(low) == 6 and low.isdigit()):
        raise DataSourceError(
            f"无法判断交易所: {code!r}（A 股代码是 6 位数字；"
            f"港股 5 位数字请写 hk.00700，美股请写 us.<代码>）"
        )
    try:
        return markets.store_key(to_bs_code(low))
    except ValueError as exc:
        raise DataSourceError(str(exc)) from exc


def store_key_for(code: str) -> str:
    """规范落库键。`sync.sync_one` 用它（替代原先的 `markets.store_key(to_bs_code(code))`）。"""
    return normalize_code(code)


def _fetch_one(
    key: str, src: str, symbol: str, period: str, start, end, eff: str
) -> pd.DataFrame:
    """按源分派一次取数。**不做派生周期聚合**，那是 `fetch_bars` 的事。"""
    if src == "baostock":
        return _baostock_fetch(to_bs_code(key), period, start, end, adjust=eff)
    if src == "tencent":
        return _tencent_fetch(key, symbol, period, start, end, adjust=eff)
    if src == "sw":
        return _sw_fetch(key, symbol, period, start, end, adjust=eff)
    if src == "eastmoney":
        return _eastmoney_fetch(key, symbol, period, start, end, adjust=eff)
    if src == "pytdx":
        return _pytdx_fetch(key, symbol, period, start, end, adjust=eff)
    return _sina_fetch(key, symbol, period, start, end, adjust=eff)


def fetch_bars(
    code: str,
    period: str,
    start: str | dt.date,
    end: str | dt.date,
    adjust: str = "2",
) -> pd.DataFrame:
    """**唯一的取数入口**。`code` 是落库键（或用户写法，内部会规范化）。

    符号映射下沉到各源内部（`vendor_symbol`），调用方只传落库键。
    口径由 `adjust_for` 收敛到该市场实际可用的口径后传给源。

    **派生周期一律本地聚合**（spec §6.1 / `periods.py` 模块 docstring），
    三个市场都是：取日线再 `periods.aggregate`。看盘页本来就是这么做的
    （`web/api.py` 的 `_read_bars`：读日线 → 聚合 → 截窗口），所以库里
    再存一份原生周/月线只会变成第二个真相来源。

    > 2026-10-05 修正（D-43）：A 股原先走 baostock 的原生周/月线，但
    > `baostock_source.period_to_frequency` 把 `"week"` 原样当 `frequency` 发出去，
    > baostock 回 `10004012 请求数据类型不正确`，实测 `sync --period week`
    > 三连重试后 `failed`、零行落库。那条路径从来没有成功过（`data/` 下
    > 至今没有 `week/` 或 `month/` 目录），所以改成和港股/美股同一套聚合
    > 不改变任何已落库的数据。
    """
    key = normalize_code(code)
    src = source_for(key, period)
    symbol = vendor_symbol_for(key, src)

    if periods.is_derived(period):
        # 日线的口径与派生周期一致（聚合只改开高低收的取法，不改复权）。
        # **复权必须排在聚合前面**：周K的开盘价取的是那一周第一天的价格，
        # 先聚合再整根乘最后一个交易日的因子，除权发生在周中间时开盘价就错了。
        day = fetch_bars(key, periods.base_period(period), start, end, adjust=adjust)
        return periods.aggregate(day, period)

    eff = adjust_for(key, adjust, period)
    log.debug("取数 code=%s 源=%s 符号=%s 口径=%s→%s", key, src, symbol, adjust, eff)
    df = _fetch_one(key, src, symbol, period, start, end, eff)
    if len(df) == 0:
        alt = fallback_for(key, period, src)
        if alt:
            # ★ **主源「连得上但零行」时要回退**（2026-10-15 实测踩到）。
            #
            # `source_for` 在指数分钟上写的是 `"pytdx" if pytdx_source.available()
            # else "eastmoney"`，而 `available()` **只判连不连得上**。pytdx 的行情
            # 服务器会「接受连接但返回零行」（限流），这时不会回退 —— 页面上的
            # 同步按钮 `202 running`、跑完 `0 行`、**什么都不说**，看起来就是
            # 「点了没反应」。用户报的正是这个。
            #
            # 零行在 D-48 的口径下**不是失败**，所以这条路径必须自己补一次，
            # 而且**要把回退说出来**（`log.warning`），否则又是一次静默。
            log.warning(
                "主源 %s 对 %s %s 返回零行（连得上但没数据，多半被限流），回退到 %s",
                src, key, period, alt,
            )
            df = _fetch_one(key, alt, vendor_symbol_for(key, alt), period,
                            start, end, adjust_for(key, adjust, period))
            if len(df) == 0:
                log.warning("回退源 %s 也返回零行", alt)
    return df


def fallback_for(code: str, period: str, primary: str) -> str | None:
    """主源取到**零行**时的备选源；没有就返回 `None`。

    **只在指数分钟上回退，而且只回退到东财。** 不无条件「多方尝试」的理由：

    - **1 分钟的股票不回退**：东财的 1 分钟只留 **1 个交易日**（240 根），
      回退过去等于拿一天的数据冒充「1 分钟历史」。宁可零行、让调用方如实报错，
      也不要一份看着有、实际只有一天的行情。
    - **指数的 5/15/30/60 分钟回退到东财**：东财对指数分钟有约 **2 个月**
      （比 pytdx 的 2 年浅，但**是真的数据**），比零行有用得多。
    - **日线不回退**：各市场既有源都更好，pytdx 只有 8000 来根。
    """
    key = normalize_code(code)
    mkt = market_of(key)
    p = str(period)
    if (primary == "pytdx" and p in pytdx_source.MINUTE_PERIODS and p != "1"
            and mkt in ("sh", "sz") and _is_index_key(key)):
        return "eastmoney"
    return None


def capabilities(code: str, period: str) -> dict[str, object]:
    """这个市场/周期能不能取、深度上限是多少。给前端「灰掉 + 说明原因」用。

    **返回的 `reason` 是给用户看的文案**，所以必须说清是哪个源、什么原因，
    不能只回一个 `False` 让页面自己编。
    """
    key = normalize_code(code)
    src = source_for(key, period)
    mkt = market_of(key)
    p = str(period)

    if src == "eastmoney":
        # 走到这里说明 `source_for` 已经判定「这个标的的这个周期只有东财有」：
        # sh/sz 的**指数**分钟线。（申万不在分流之列 —— 东财根本不收录申万行业指数，
        # 见 D-52 被否决方案 5。）
        return {
            "supported": True,
            "source": src,
            "reason": "",
            "depth_cap": None,
            "note": (
                "东财分钟线（**A 股指数**；申万不在其列 —— 东财根本不收录申万行业指数，"
                "见 D-52 被否决方案 5）。约 2 个月 —— 这是**服务端保留边界**，"
                "不是翻页限制，实测 5 分钟在 `end=20260821` 回 0 根、而日线能翻回 2003 年。"
                "适合每日增量累积，**不适合回填历史**（要多年分钟历史走通达信 vipdoc）"
            ),
        }

    if src == "pytdx":
        # 走到这里说明 `source_for` 判定「这个标的的这个周期走通达信在线行情」：
        # **1 分钟**（只有它有），或**指数的分钟**（比东财深得多）。
        # ★ 这个分支必须存在：漏了它，下面的港股/美股兜底会把它当成美股，
        # 报出「美股不支持周期 1」这种驴唇不对马嘴的理由（实测踩过）。
        return {
            "supported": True,
            "source": src,
            "reason": "",
            "depth_cap": None,
            "note": (
                "通达信在线行情（pytdx）；**不复权**。实测 1 分钟 ~4.5 个月"
                "（23520 根）、5 分钟 ~2 年、日线全历史，**全部到当日收盘**。"
                "要前复权请先跑 `factors` 反推因子表（D-19）"
            ),
        }

    if src == "baostock":
        # **baostock 的分钟周期只有 60/30/15/5，没有 1 分钟。** 传 "1" 会被它以
        # `10004012 请求数据类型不正确` 拒掉（实测）。这里如实说不支持 ——
        # 原先这个分支无条件回 `supported=True`，把 `"1"` 放进 `web/api.py::PERIODS`
        # 之后就露出来了：页面会给出一个**注定失败**的「同步这个周期」按钮。
        # 这正是本文件反复强调的那件事：能力只有一处口径，且必须是实测的口径。
        if p == "1":
            return {
                "supported": False,
                "source": src,
                "reason": (
                    "A 股股票没有 1 分钟数据源：baostock 的分钟周期只有 60/30/15/5"
                    "（传 1 分钟报 `10004012 请求数据类型不正确`）；"
                    "东财的 1 分钟只留**一个交易日**，且只接指数不接个股。"
                ),
                "depth_cap": None,
            }
        return {"supported": True, "source": src, "reason": "", "depth_cap": None}

    if mkt == "sw":
        if p in _DAY_PERIODS:
            return {
                "supported": True,
                "source": src,
                "reason": "",
                "depth_cap": None,
                "note": "申万行业指数；2004-03-01 起；不复权（指数无除权除息）",
            }
        if p in _DERIVED_PERIODS:
            return {"supported": True, "source": src, "reason": "", "depth_cap": None}
        return {
            "supported": False,
            "source": src,
            "reason": (
                "申万行业板块**没有分钟线数据源**：腾讯 `mkline` 对 `pt01…` 回 "
                "`param error`、新浪对申万回 `null`；东财那个 `secid=90.801010` "
                "**不是申万** —— `90.` 是东财自己的板块空间，同号不同物"
                "（实测回的是「磷化工」而不是「农林牧渔」）。板块只有日线，"
                "周/月由日线本地聚合"
            ),
            "depth_cap": None,
        }

    if mkt == "hk":
        if p in _DAY_PERIODS:
            return {
                "supported": True,
                "source": src,
                "reason": "",
                "depth_cap": None,
                "note": "2004-06-16 起；不复权（腾讯的港股复权序列实测不可用）",
            }
        if p in _DERIVED_PERIODS:
            return {"supported": True, "source": src, "reason": "", "depth_cap": None}
        return {
            "supported": False,
            "source": src,
            "reason": (
                "港股没有可用的分钟线数据源（腾讯只提供日/周/月：`hkfqkline` 的 m30/m5 "
                "回 `bad params`，通用 `fqkline` 拿 m30 只回当天 1 根日线，"
                "`hkMinute/query` 只回当天分时、无历史不可翻页）"
            ),
            "depth_cap": None,
        }

    # 美股
    if p in _DAY_PERIODS or p in _DERIVED_PERIODS:
        return {
            "supported": True,
            "source": src,
            "reason": "",
            "depth_cap": None,
            "note": "不复权（源只提供原始价）",
        }
    if p in ("60", "30", "15", "5"):
        return {
            "supported": True,
            "source": src,
            "reason": "",
            "depth_cap": 1023,
            "note": "不复权；约 1023 根（≈3.8 个月），数据商不给翻页",
        }
    return {"supported": False, "source": src, "reason": f"美股不支持周期 {period}", "depth_cap": None}
