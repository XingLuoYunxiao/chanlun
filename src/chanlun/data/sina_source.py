"""新浪行情源：美股（指数与个股）日线、美股 5/15/30/60 分钟。

**为什么单独一个模块**：美股走的是完全不同的数据商与响应格式 ——
腾讯对美股给不出可用数据（实测 `usAAPL` 的 `day` 是**空数组**，
`code=0` 但零行，见 spec §3.4），新浪给全。

**口径：新浪美股一律是不复权价。** 实测（2026-10-05，AAPL）：

| 日期 | 开 | 收 | 事件 |
|---|---|---|---|
| 2020-08-28 | 504.05 | 499.23 | 4:1 拆股前 |
| 2020-08-31 | 127.58 | 129.04 | **拆股当日，价格直接跳到 1/4** |
| 2014-06-06 | 649.90 | 645.57 | 7:1 拆股前 |
| 2014-06-09 | 92.70 | 93.70 | **拆股当日** |

价格在拆股日**直接跳变**，说明新浪没有做任何回溯调整。
所以 `fetch_bars` 只接受 `adjust="3"`（不复权），别的口径**报错而不是静默给原始价** ——
把不复权价标成前复权正是 spec §3.2 (a3) 拦下的那个错误模式。

指数无除权，所以「不复权」对指数就是正确的口径（`sync.adjust_for` 给指数 `"3"`）。

证据：`docs/superpowers/specs/2026-10-05-hk-us-markets-design.md` §3.1 / §3.4。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import urllib.request

import pandas as pd

from .types import DataSourceError, empty_frame, normalize

log = logging.getLogger(__name__)

#: JSONP 端点。`var%20x=` 是回调名，返回体形如
#: `/*<script>location.href='//sina.com';</script>*/\nvar x=([{…}]);`
JSONP = "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20x=/US_MinKService.{svc}"

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) chanlun/0.5"
#: 新浪要求带 `Referer` 才给行情（实测 `hq.sinajs.cn` 裸请求直接返回 `Forbidden`，
#: spec §3.4 坑 9）。这个端点裸请求目前可用，但带上更稳。
_HEADERS = {"User-Agent": _UA, "Referer": "https://finance.sina.com.cn"}

#: `getDailyK` 的 `type` 固定 240（日线）；`getMinK` 的 `type` 就是分钟数。
_MINUTE_PERIODS = {"60", "30", "15", "5"}

#: 见 `tencent_source.MIN_ROWS` 的说明 —— 拦住「返回码正常但只有几根」的静默假成功。
MIN_ROWS = {"day": 30, "60": 30, "30": 30, "15": 30, "5": 30}

#: 实测硬上限：`getMinK` 无论怎么传 `datalen`/`num`/`date`/`from`/`limit`/`p`/`page`
#: 都返回**逐字节相同**的 1023 根窗口（spec §3.4）⇒ 美股分钟历史约 3.8 个月，**不能翻页**。
#: 页面上要如实标注这个上限，不能让用户以为拉到了更长的分钟历史。
MINUTE_HARD_CAP = 1023

_PREFIX_RE = re.compile(r"^/\*.*?\*/\s*", re.S)
_CALL_RE = re.compile(r"^\s*var\s+x=\((.*)\);?\s*$", re.S)


def _get_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https 域名
            return resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 统一成 DataSourceError 交给 sync 吞
        raise DataSourceError(f"新浪取数失败 url={url}: {exc}") from exc


def _parse_jsonp(text: str, url: str) -> list:
    """剥掉 JSONP 外壳并解析成 list。

    **`var x=(null);` 是合法响应**（实测裸 `DJI` 不带点前缀就是这个，spec §3.4 坑 3），
    代表「这个符号没有数据」。它解析出来是 `None`，按**零行**处理而不是崩溃。
    """
    body = _PREFIX_RE.sub("", text.strip())
    m = _CALL_RE.match(body)
    if not m:
        raise DataSourceError(f"新浪响应不是预期的 JSONP 形状 url={url} head={body[:80]!r}")
    try:
        payload = json.loads(m.group(1))
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"新浪 JSONP 解析失败 url={url}") from exc
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise DataSourceError(f"新浪响应不是数组（{type(payload).__name__}）url={url}")
    return payload


def _rows_to_frame(rows: list) -> pd.DataFrame:
    """新浪行对象 `{d,o,h,l,c,v,a}` → BarFrame。

    **分钟线的秒要切掉**：新浪给 `"2026-06-11 12:00:00"`，而全仓库分钟线的
    `ts` 口径是 `"YYYY-MM-DD HH:MM"`（`baostock_source._rows_to_frame` 就是这么
    拼的，落库的 A 股 30 分文件实测是 `['2020-01-02 10:00', …]`）。
    留着秒不会当场出错，但 `types.truncate(df, end="2026-10-05")` 是按字符串比的，
    `"2026-10-05 16:00:00" <= "2026-10-05"` 是 False —— 最后一天的分时会整段消失。
    同一个 `(code, period)` 在不同市场必须是同一个 `ts` 口径。
    """
    if not rows:
        return empty_frame()
    records = []
    for r in rows:
        if not isinstance(r, dict) or "d" not in r:
            continue
        ts = str(r["d"])
        if len(ts) == 19 and ts[13] == ":" and ts[16] == ":":
            ts = ts[:16]
        records.append(
            {
                "ts": ts,
                "open": r.get("o"),
                "high": r.get("h"),
                "low": r.get("l"),
                "close": r.get("c"),
                "volume": r.get("v", 0.0),
                "amount": r.get("a", 0.0),
            }
        )
    if not records:
        return empty_frame()
    df = pd.DataFrame(records)
    return normalize(df[["ts", "open", "high", "low", "close", "volume", "amount"]])


def fetch_bars(
    code: str,
    symbol: str,
    period: str,
    start: str | dt.date,
    end: str | dt.date,
    adjust: str = "3",
) -> pd.DataFrame:
    """美股取数入口。`code` 是落库键，`symbol` 是新浪写法（`.DJI` / `AAPL`）。"""
    from .adjust import normalize_adjust

    mode = normalize_adjust(adjust)
    if mode != "raw":
        raise DataSourceError(
            f"新浪美股源只有不复权价（实测拆股日价格直接跳变），"
            f"无法提供 {mode} —— 拒绝把原始价标成复权价"
        )

    key = str(period)
    if key in ("week", "w", "month", "m"):
        raise DataSourceError(f"{period} 是派生周期，应由 periods.aggregate 从日线聚合")
    if key in ("day", "d"):
        svc, typ = "getDailyK", "240"
        min_rows = MIN_ROWS["day"]
    elif key in _MINUTE_PERIODS:
        svc, typ = "getMinK", key
        min_rows = MIN_ROWS[key]
    else:
        raise DataSourceError(f"新浪美股源不支持周期 {period}（可选 day/60/30/15/5）")

    url = JSONP.format(svc=svc) + f"?symbol={symbol}&type={typ}"
    df = _rows_to_frame(_parse_jsonp(_get_text(url), url))

    if len(df) < min_rows:
        raise DataSourceError(
            f"{symbol} 的 {period} 只回 {len(df)} 行（少于 {min_rows}）"
            f" —— 数据商可能改了行为，拒绝落库"
        )

    # `getDailyK` 没有区间参数（总是给全历史），`getMinK` 的区间参数被忽略。
    # 所以区间**只能本地裁**，而且不能因为裁完变少就判失败 —— 阈值判在裁之前。
    #
    # 比较只取前 10 位（日期）：分钟线的 `ts` 是 `"2026-06-11 12:00:00"`，
    # 直接跟 `"2026-10-05"` 比大小会把**结束日当天的全部分钟**判成越界
    # （`"2026-10-05 12:00:00" <= "2026-10-05"` 是 False），
    # 表现为「最后一天的分时整段消失」。
    day = df["ts"].astype(str).str[:10]
    keep = (day >= str(start)[:10]) & (day <= str(end)[:10])
    out = normalize(df[keep])
    if key in _MINUTE_PERIODS and len(df) >= MINUTE_HARD_CAP:
        log.info("新浪美股分钟 %s %s 触及硬上限 %d 根，无法翻页", symbol, period, len(df))
    return out
