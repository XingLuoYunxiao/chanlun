"""通达信在线行情源（`pytdx`）—— **1 分钟数据的唯一来源**。

为什么需要它
------------
在本模块之前，**1 分钟数据没有任何来源**（详见 ARCHITECTURE §5）：

- baostock 的 `MIN_PERIODS = {60,30,15,5}`，传 `1` 直接报 `10004012`；
- 腾讯 `mkline` 上限 320 根；新浪 `datalen` 硬封顶 1023 根；
- 东财的 1 分钟只留 **1 个交易日**（240 根），且 `end` 对它无效。

**2026-10-13 实测 pytdx 全线可用**（推翻本项目此前「协议不兼容」的记录 ——
见下），而且深度足够支撑递归：

===========  ==============  ==========================  ==========
标的          周期             区间                         根数
===========  ==============  ==========================  ==========
股票 / 指数    1 分钟           ~4.5 个月                    22320–23520
股票 / 指数    5 分钟           ~2 年                        23568–23808
股票 / 指数    日线             全历史（1990/1991 起）        5670–8740
===========  ==============  ==========================  ==========

**全部到当日收盘**。相比 baostock 的 85 秒/只（全市场 5 分钟 ≈ 129 小时），
pytdx 一只 1 分钟约 1.5 秒（28 页 × 800 根）—— 快约两个数量级。

此前的错误结论
--------------
第 3 轮记的是「`pytdx 1.72` 能连上但**所有 category 的 K 线全空**，协议不兼容」。
**那个结论是错的，原因在服务器列表而不在协议**：`pytdx.config.hosts.hq_hosts`
里 104 台服务器逐台实测，**8 台可用**（国泰君安 `117.34.114.13–.27:7709` 段），
其余是死的 —— 当时试到的恰好都是死的，于是把「服务器死了」误判成「协议不兼容」。
教训：**把「试了几个都失败」写成「协议不兼容」之前，要先确认样本量够。**

两个必须记住的坑
----------------
1. **指数要用 `get_index_bars`，不能用 `get_security_bars`。** 后者对
   `market=1` 的响应**字段错位**：第一条还对（`2026-10-13 14:55`），第二条起
   变成 `2004-11-41 18:54`、`236757-22-72` 这种。而且**它不报错**，只是默默给出
   一堆垃圾时间戳 —— 所以「调用成功」在这里毫无意义。
2. **页内 `[0]` 是最早的那根**，不是最新的。翻页时 `start` 从 0（最新）往后数，
   但每一页内部是按时间**升序**排的。搞反会把「最早一根」当成「最新一根」，
   增量水位从此全是错的。

口径
----
**不复权（raw）**。通达信在线行情不提供复权序列。落库前先跑 `factors` 反推因子表
（D-19），否则同一只票的分钟线（raw）与日线（前复权）会口径不一。
"""

from __future__ import annotations

import logging
import threading

import pandas as pd

from .types import DataSourceError, empty_frame, normalize

log = logging.getLogger(__name__)

try:                                    # pytdx 是可选依赖：没装时给一句人话
    from pytdx.hq import TdxHq_API
except Exception:                       # pragma: no cover
    TdxHq_API = None                    # type: ignore[assignment]

#: 实测可用的服务器（2026-10-13，`hq_hosts` 104 台里只有这 8 台活着）。
#: 按顺序试，第一个连上就用；**换机器/换网络要重新实测**，别把这份当成永久真相。
HOSTS: tuple[tuple[str, int], ...] = (
    ("117.34.114.13", 7709),
    ("117.34.114.14", 7709),
    ("117.34.114.15", 7709),
    ("117.34.114.16", 7709),
    ("117.34.114.17", 7709),
    ("117.34.114.18", 7709),
    ("117.34.114.20", 7709),
    ("117.34.114.27", 7709),
)

#: 本项目周期名 → pytdx 的 category 码。
CATEGORY: dict[str, int] = {
    "1": 7,        # 1 分钟
    "5": 0,        # 5 分钟
    "15": 1,
    "30": 2,
    "60": 3,
    "day": 4,
    "week": 5,
    "month": 6,
}
#: **分钟**周期（`CATEGORY` 还含 day/week/month，那些不走这个源）。
MINUTE_PERIODS: tuple[str, ...] = ("1", "5", "15", "30", "60")
#: 落库市场 → pytdx market 码。**0 = 深、1 = 沪**（与常见写法相反，别凭印象写）。
MARKET: dict[str, int] = {"sz": 0, "sh": 1}
#: 单次最多取多少根，防病态输入下无休止翻页（1 分钟实测约 23500 根）。
MAX_BARS = 60000
PAGE = 800
TIMEOUT = 8.0
COLUMNS = ["ts", "open", "high", "low", "close", "volume", "amount"]

_lock = threading.Lock()
_api = None
_host: tuple[str, int] | None = None


def available() -> bool:
    """pytdx 装了没有 —— 缺依赖时要能给出人话，而不是 ImportError 堆栈。"""
    return TdxHq_API is not None


def _connect() -> "TdxHq_API":
    """连上一台可用服务器（进程内复用）。连不上就**逐个试**并报清试过谁。"""
    global _api, _host
    if _api is not None:
        return _api
    if TdxHq_API is None:
        raise DataSourceError(
            "没装 pytdx，无法取 1 分钟数据：`pip install pytdx`（见 requirements）"
        )
    tried = []
    for host, port in HOSTS:
        api = TdxHq_API(heartbeat=False)
        try:
            if api.connect(host, port, time_out=TIMEOUT):
                _api, _host = api, (host, port)
                log.info("pytdx 连上 %s:%s", host, port)
                return api
        except Exception as exc:        # 单台连不上不致命，继续试下一台
            tried.append(f"{host}:{port}({type(exc).__name__})")
            continue
        tried.append(f"{host}:{port}(拒绝)")
    raise DataSourceError(
        "pytdx 连不上任何一台已知服务器：" + "、".join(tried[:4]) + "… "
        "这些 IP 会变 —— 用 `optimizer/tools/probe_pytdx_hosts.py` 重新实测。"
    )


def reset() -> None:
    """丢掉当前连接（取数出错后下次会重连，而不是一直用一个坏 socket）。"""
    global _api, _host
    try:
        if _api is not None:
            _api.disconnect()
    except Exception:
        pass
    _api, _host = None, None


def fetch_bars(
    code: str,
    symbol: str,
    period: str,
    start: str,
    end: str,
    adjust: str = "raw",
) -> pd.DataFrame:
    """取一次 K 线。`symbol` 是落库键（本源按市场 + 裸码自己拆）。

    翻页从最新往旧走，直到覆盖 `start` 或没有更多数据；然后按 `[start, end]` 裁剪。
    """
    from . import store as _store

    p = str(period)
    if p not in CATEGORY:
        raise DataSourceError(f"pytdx 源不支持周期 {period!r}（支持 {'/'.join(CATEGORY)}）")
    if str(adjust) not in ("raw", "3", "0"):
        # 在线行情**只有不复权**。要前复权用 `factors` 反推后自己算，别在这里假装能给。
        raise DataSourceError(
            f"pytdx 只提供不复权（raw），不接受口径 {adjust!r}（D-19）"
        )
    mkt_name = _store.market_of(code)
    mkt = MARKET.get(mkt_name)
    if mkt is None:
        raise DataSourceError(f"pytdx 源不支持市场 {mkt_name!r}（只做了沪深）")
    bare = _store.bare_code(code)
    is_index = _is_index(code, mkt_name, bare)
    cat = CATEGORY[p]

    with _lock:                          # pytdx 是**单会话 socket**，并发用会串包
        fn = _connect()
        try:
            rows = _page(fn, cat, mkt, bare, str(start)[:16], is_index)
        except Exception as exc:
            reset()                      # 坏连接不要留到下一次
            raise DataSourceError(f"pytdx 取数失败 {code} {period}: {exc}") from exc
    if not rows:
        return empty_frame()

    df = normalize(pd.DataFrame(rows, columns=COLUMNS))
    lo, hi = str(start)[:16], str(end)[:16]
    if lo:
        df = df[df["ts"].str[: len(lo)] >= lo]
    if hi:
        df = df[df["ts"].str[: len(hi)] <= hi]
    return df.reset_index(drop=True)


def _is_index(code: str, mkt_name: str, bare: str) -> bool:
    """走 `markets.is_index`，但**补回市场前缀**。

    归一架后 `sz.399001`（深证成指）是**裸码**，而 `markets.is_index` 对裸码一律
    返回 `False`（裸 `000001` 是平安银行）——`sources._is_index_key` 踩过同一个坑。
    """
    from . import markets as _markets

    return _markets.is_index(f"{mkt_name}.{bare}")


def _page(fn, cat: int, mkt: int, bare: str, start: str, is_index: bool) -> list[dict]:
    """从最新往旧翻页，直到覆盖 `start`（或没数据了）。

    **页内 `[0]` 是最早的一根**（升序），所以「这一页最旧的一根」是 `bars[0]`。
    用 `bars[-1]` 判断会让循环立刻退出、只拿到一页。
    """
    method = fn.get_index_bars if is_index else fn.get_security_bars
    out: list[dict] = []
    offset = 0
    while offset < MAX_BARS:
        bars = method(cat, mkt, bare, offset, PAGE)
        if not bars:
            break
        out += [_row(b) for b in bars]
        offset += len(bars)
        oldest = str(bars[0].get("datetime") or "")[: len(start)]
        if oldest and oldest <= start:   # 已经翻到窗口起点之前，够了
            break
        if len(bars) < PAGE:             # 这是最后一页
            break
    return out


def _row(bar: dict) -> dict:
    ts = str(bar.get("datetime") or "")
    return {
        "ts": ts,
        "open": float(bar.get("open") or 0.0),
        "high": float(bar.get("high") or 0.0),
        "low": float(bar.get("low") or 0.0),
        "close": float(bar.get("close") or 0.0),
        "volume": float(bar.get("vol") or 0.0),
        "amount": float(bar.get("amount") or 0.0),
    }
