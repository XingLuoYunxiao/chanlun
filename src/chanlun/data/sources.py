"""市场分派层：**唯一**的取数入口。

`api.py` / `sync.py` / `__main__.py` 都从这里拿 `fetch_bars` 与 `normalize_code`，
不再直接从 `baostock_source` 拿。

分层（避免循环 import）：

```
markets.py         ← 只认号段与前缀，不 import 任何数据源
store.py           ← import markets
baostock_source.py ← import markets, types
tencent_source.py  ← import markets, types
sina_source.py     ← import markets, types
sources.py         ← import 上面全部（唯一的分派点）
```

**符号映射不猜**：落库键（小写、带市场前缀）→ 数据商要的写法，
三个坑都在 `vendor_symbol` 里标出来了。

**口径也不猜**：`adjust_for` 把调用方要的口径收敛到该市场**实际可用**的口径。
港股只有一个可用口径（不复权），见 `tencent_source` 的模块 docstring 与 D-43。
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from . import markets, periods, store, tencent_source
from .baostock_source import fetch_bars as _baostock_fetch
from .baostock_source import to_bs_code
from .sina_source import fetch_bars as _sina_fetch
from .tencent_source import fetch_bars as _tencent_fetch
from .types import DataSourceError

log = logging.getLogger(__name__)

#: 每个市场由哪个源负责。`sh/sz/bj` 是 A 股，走 baostock。
SOURCE_OF_MARKET: dict[str, str] = {
    "sh": "baostock",
    "sz": "baostock",
    "bj": "baostock",
    "hk": "tencent",
    "us": "sina",
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
    """这个落库键由哪个源负责。未知市场报错，不猜。"""
    mkt = market_of(code)
    src = SOURCE_OF_MARKET.get(mkt)
    if src is None:
        raise DataSourceError(f"未知市场 {mkt!r}（代码 {code!r}）")
    return src


def vendor_symbol(code: str) -> tuple[str, str]:
    """`(源名, 取数符号)`。落库键 → 数据商要的写法。

    坑 1：必须用 `store.market_of`，不能用 `markets.market_of`。
    坑 2：落库键是小写的，但腾讯要大写 —— `hk.hsi` → `hkHSI`。
          少了 `.upper()` 会**静默拿到空数据**（`param error`）。
    坑 3：新浪美股**指数**必须带点前缀（`.DJI`），**个股不带**（`AAPL`）。
          实测裸 `DJI` 返回 `var x=(null);`，带点才有数据。
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
    return "baostock", to_bs_code(key)


def endpoint(code: str, period: str, adjust: str) -> dict[str, str]:
    """`(控制器, 复权词, 期望键名)` —— spec §7.2 的端点/口径分派表。

    这张表存在的原因：**光有符号不够，还得选对控制器**。
    腾讯把口径写在**控制器名**上：`fqkline` 完全忽略复权词并返回**不复权**价，
    `hkfqkline` 才认 `qfq`/`hfq`。选错的后果不是报错，是把一种口径的价格当成另一种存。

    港股本期只落不复权（`tencent_source` 模块 docstring 的三条实测），
    所以这里只报 `fqkline` + 键名 `day`。实现委托给 `tencent_source.plan` ——
    **口径分派只允许有一份实现**，两份会漂移，而漂移的后果是静默的口径错误。
    """
    src = source_of(code)
    if src != "tencent":
        return {"controller": src, "adjust_word": "", "expected_key": ""}
    if str(period) not in _DAY_PERIODS and not markets.is_index(code):
        raise DataSourceError(f"腾讯港股源只提供日线，不提供 {period}（spec §3.2）")
    ctrl, word, expect = tencent_source.plan()
    return {"controller": _short(ctrl), "adjust_word": word, "expected_key": expect}


def _short(endpoint_url: str) -> str:
    """`https://ifzq.gtimg.cn/appstock/app/fqkline/get` → `fqkline`。"""
    return endpoint_url.rsplit("/", 2)[-2]


def adjust_for(code: str, adjust: str) -> str:
    """这只票实际能用哪个口径。`adjust` 是 baostock 口径码（"2" 前 / "1" 后 / "3" 不复权）。

    规则：
    - A 股：原样返回（baostock 三个口径都支持）。
    - 港股（指数与个股）：`"3"` —— 腾讯的港股复权序列**实测不可用**
      （前复权 2013-05 前为负、后复权不是因子阶梯，见 `tencent_source` docstring）。
    - 美股（指数与个股）：`"3"` —— 新浪美股源只有原始价。

    收敛而不是报错：调用方（`sync` / `api`）默认传 A 股的 `"2"`，
    让它们不必知道每个市场的口径细节。真要一种源给不出的口径，
    由源自己报错（`tencent_source.fetch_bars` / `sina_source.fetch_bars` 都会拒绝）。
    """
    mkt = market_of(code)
    if mkt in ("sh", "sz", "bj"):
        # 指数没有除权除息：落「前复权」会让 `sync_state.adjust` 写着「前复权」，
        # 页面照抄，而那个概念对指数根本不成立（D-19）。
        return "3" if markets.is_index(code) else str(adjust)
    return "3"


def normalize_code(code: str) -> str:
    """用户能输入的任何写法 → 规范落库键。

    接受的写法（spec §4.2）：`600000` / `sh.600000` / `sh600000` / `hk.00700` /
    `hk.HSI` / `us.DJI` / `us.dji`。规范形式一律**小写 + 点号 + 市场前缀**。

    A 股沿用 `to_bs_code` 的号段校验（它认得 6 位数字与歧义号段），
    港股/美股用号段正则校验。**未知写法报错，不猜市场** ——
    猜错会把一只票的数据写到另一个市场目录下。
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
    src = source_of(key)
    _src_name, symbol = vendor_symbol(key)

    if periods.is_derived(period):
        # 日线的口径与派生周期一致（聚合只改开高低收的取法，不改复权）。
        # **复权必须排在聚合前面**：周K的开盘价取的是那一周第一天的价格，
        # 先聚合再整根乘最后一个交易日的因子，除权发生在周中间时开盘价就错了。
        day = fetch_bars(key, periods.base_period(period), start, end, adjust=adjust)
        return periods.aggregate(day, period)

    eff = adjust_for(key, adjust)
    log.debug("取数 code=%s 源=%s 符号=%s 口径=%s→%s", key, src, symbol, adjust, eff)
    return _fetch_one(key, src, symbol, period, start, end, eff)


def capabilities(code: str, period: str) -> dict[str, object]:
    """这个市场/周期能不能取、深度上限是多少。给前端「灰掉 + 说明原因」用。

    **返回的 `reason` 是给用户看的文案**，所以必须说清是哪个源、什么原因，
    不能只回一个 `False` 让页面自己编。
    """
    key = normalize_code(code)
    src = source_of(key)
    mkt = market_of(key)
    p = str(period)

    if src == "baostock":
        return {"supported": True, "source": src, "reason": "", "depth_cap": None}

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
