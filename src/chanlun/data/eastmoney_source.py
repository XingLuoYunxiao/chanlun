"""东方财富行情源：**分钟线**（A 股**指数**）。

为什么需要它
------------
现有三个源的分钟线缺口正好落在「指数」上：

- **baostock**：对 `sh.000001` / `sh.000300` / `sz.399001` 的分钟线一律返回
  **0 行**，只有股票有（实测 `err=0 success` 但无数据）；
- **腾讯 `mkline`**：单次上限 **320 根**（≈1.3 个交易日），要 2000 仍回 320；
- **新浪 `getKLineData`**：`datalen` 硬封顶 **1023 根**（≈1 个月）。

东财能补上 A 股指数这一段，实测 `1.000300` / `0.399001` 的 `data.name`
确实是「沪深300」/「深证成指」。

**不接受申万行业板块（`sw.*`），而且会报错。** 曾经把 `sw.801010` 映射到
`secid=90.801010` 并当作申万导入了 96 个文件 —— **那是错的**：`90.` 是东财
**自己的板块空间**，同号不同物。实测 `90.801010` 回「磷化工」、`90.801080`
回「新型城镇化」、`90.801012` 回「PVDF概念」，与申万的农林牧渔 / 电子 /
农产品加工毫无关系。**错数据比没数据危险得多**，所以这条映射被删掉并加了护栏
（`secid()` 对 `sw.*` 直接抛错），而不只是改调用方。

判断「这个 secid 是不是我要的标的」的**唯一可靠办法是核对 `data.name`** ——
号段会撞车，名字不会。上面那次事故正是靠核对名字才发现的。

两个必须记住的坑
----------------
1. **必须用 `http://`。** `https://push2his.eastmoney.com` 在本机一律
   `RemoteDisconnected`（`https://push2.eastmoney.com`、`https://63.push2his...`
   同样），换 `http://` 就正常返回 JSON。此前本项目把这条记成「东财连接被拒」，
   **那是错的** —— 被拒的只是 https。
2. **分钟历史只有约 2 个月，而且是服务端保留边界、不是翻页限制。**
   实测 `klt=5` 在 `end=20260824` 回 96 根、`end=20260821` 回 **0 根**；
   对照 `klt=101`（日线）`end=20240101` 能翻回 `2003-01-06` —— 翻页机制本身是好的。
   单次上限实测：5 分钟 ~1536 根、15 分钟 ~512、30 分钟 ~248、
   **1 分钟 240 根（=1 个交易日，且 `end` 对它无效）**。

所以这个源的定位是**每日增量累积**（每天取最近那段窗口落库，久了自然攒出长历史），
**不是回填历史**。要一次性拿到多年分钟历史，走通达信 vipdoc（`tdx.py`）。

字段顺序（2026-10-13 在真实响应上逐条核对过）
--------------------------------------------
`fields2=f51,f52,f53,f54,f55,f56,f57` 依次是
``时间, 开, 收, 高, 低, 成交量, 成交额`` —— **收在高/低之前**，不是常见的
「开高低收」。判据是 `low <= min(开,收) <= max(开,收) <= high`：按「开收高低」
代入三条实测数据全部成立，按「开高低收」全部不成立。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.request

import pandas as pd

from .types import DataSourceError, empty_frame, normalize

log = logging.getLogger(__name__)

#: **必须是 http。** https 在本机一律 RemoteDisconnected，见模块 docstring 坑 1。
BASE = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
FIELDS = "f51,f52,f53,f54,f55,f56,f57"

#: 本项目周期名 → 东财 `klt`。
KLT: dict[str, str] = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60", "day": "101"}
#: 复权口径 → 东财 `fqt`。`2` 是前复权，`raw`/`3` 都是不复权。
FQT: dict[str, str] = {"2": "1", "3": "0", "raw": "0"}
#: 单次请求上限。给足，让服务端按自己的保留边界回 —— 它不会因为我们要得多而多给。
LMT = 100000
COLUMNS = ["ts", "open", "high", "low", "close", "volume", "amount"]
#: 分钟周期（日线不在这里：东财日线走 `klt=101`，但日线的既有源都更好）。
MINUTE_PERIODS = ("1", "5", "15", "30", "60")


def secid(key: str) -> str:
    """落库键 → 东财 `secid`。**入参必须是已归一的落库键**（`sources.normalize_code`
    的产物）—— 归一在 `sources` 里、本模块不反向 import 它，否则成环。

    - `sh.600030` → `1.600030`（沪）；
    - `sz.399001` → `0.399001`（深）。

    **申万行业板块（`sw.*`）会在这里报错，这是故意的。** 曾经把
    `sw.801010` 映射成 `90.801010` 并导入了 96 个文件 —— 那是**张冠李戴**：
    `90.` 是东财**自己的板块空间**，实测 `90.801010` 回的是「磷化工」、
    `90.801080` 回「新型城镇化」、`90.801012` 回「PVDF概念」，与申万的
    农林牧渔 / 电子 / 农产品加工毫无关系。错数据比没数据危险得多，
    所以把这条映射**删掉并加护栏**，而不是只改调用方。
    """
    from . import store as _store

    mkt = _store.market_of(key)
    bare = _store.bare_code(key)
    if mkt == "sw":
        raise DataSourceError(
            f"东财源不支持申万行业板块（{key!r}）：`secid=90.…` 是东财**自己的**"
            "板块空间，同号不同物（`90.801010` 实测是「磷化工」而不是「农林牧渔」）。"
            "申万的分钟线目前没有数据源。"
        )
    if mkt == "sh":
        return f"1.{bare}"
    if mkt == "sz":
        return f"0.{bare}"
    raise DataSourceError(f"东财源不认识市场 {mkt!r}（代码 {key!r}）")


def _rows_to_frame(rows: list[str]) -> pd.DataFrame:
    out = []
    for row in rows:
        p = row.split(",")
        if len(p) < 7:
            continue
        ts, op, cl, hi, lo, vol, amount = p[:7]
        out.append(
            {
                "ts": str(ts)[:16],          # "2026-10-13 14:50"，秒与毫秒都不要
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),          # ★ 收在 f53，不是 f55
                "volume": float(vol or 0),
                "amount": float(amount or 0),
            }
        )
    if not out:
        return empty_frame()
    return normalize(pd.DataFrame(out, columns=COLUMNS))


def _get_json(url: str, timeout: int = 30) -> dict:
    """唯一的取数缝合点 —— 测试把这一处替换掉就完全离线（与 `sw_source` 同一约定）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def fetch_bars(
    code: str,
    symbol: str,
    period: str,
    start: str,
    end: str,
    adjust: str = "2",
) -> pd.DataFrame:
    """取一次东财分钟线。`symbol` 是 `secid`（由 `sources.vendor_symbol_for` 产出）。

    东财只按 `end` + `lmt` 回数据、不认 `start`，所以 `start` 在这里是**本地裁剪**：
    取回后按 `[start, end]` 过滤。这样即使源多给了，也不会把窗口外的行写进库。
    """
    p = str(period)
    if p not in KLT:
        raise DataSourceError(f"东财源不支持周期 {period!r}（支持 {'/'.join(KLT)}）")
    fqt = FQT.get(str(adjust))
    if fqt is None:
        raise DataSourceError(f"东财源不认识复权口径 {adjust!r}（支持 {', '.join(FQT)}）")

    url = (
        f"{BASE}?secid={symbol}&fields1=f1,f2,f3,f4,f5&fields2={FIELDS}"
        f"&klt={KLT[p]}&fqt={fqt}&beg=0&end={str(end)[:10].replace('-', '') or '20500101'}"
        f"&lmt={LMT}"
    )
    try:
        payload = _get_json(url)
    except Exception as exc:  # 网络/JSON 都归到取数失败，由上层记一次 failed
        raise DataSourceError(f"东财取数失败 {code} {period}: {type(exc).__name__}: {exc}") from exc

    data = payload.get("data")
    if not data:
        # `data=null` 是东财对「不认识的 secid」与「该周期无数据」的统一回答。
        # 不猜是哪种，如实返回空帧，让上层的零行口径去记账（D-48）。
        return empty_frame()

    df = _rows_to_frame(data.get("klines") or [])
    if df.empty:
        return df
    lo = str(start)[:16]
    hi = str(end)[:16]
    # ★ 不能直接用 `df["ts"] <= hi`：`end` 常常是**裸日期** `YYYY-MM-DD`，而分钟 `ts`
    # 是 `YYYY-MM-DD HH:MM`，字典序上 `"2026-10-13 09:31" > "2026-10-13"` ——
    # 那样会把**当天**的分钟线整批丢掉，而且看起来像"这个周期没数据"。
    # `data/types.py::truncate` 有同一个坑（见 ARCHITECTURE §5）。这里按 `hi` 的长度
    # 截断再比：`hi` 是裸日期就只比日期部分，是带时刻的就逐字比。
    if lo:
        df = df[df["ts"].str[: len(lo)] >= lo]
    if hi:
        df = df[df["ts"].str[: len(hi)] <= hi]
    return df.reset_index(drop=True)
