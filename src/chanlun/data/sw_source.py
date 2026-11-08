"""申万行业指数（板块）日线 —— 腾讯 `newfqkline`，**一律不复权**。

**为什么不复用 `tencent_source`** —— 两条路在数据商那边就不是同一个接口：

============================================  ==========  ==================================
请求                                           返回键       实测结论
============================================  ==========  ==================================
`fqkline` + `pt01801120`                       `day`       **只有 1 根**（显式区间回 0 根）
`newfqkline` + `pt01801120`（第七位留空）        `day`       **全历史，11 字段行**
`newfqkline` + `pt01801120` + `qfq`            `day`       同上（板块没有复权，词被忽略）
`mkline` + `pt01801120,m30` / `m5`             ——          `{"code":-1,"msg":"param error"}`，**没有分钟线**
============================================  ==========  ==================================

两条实测细节：

1. **`fqkline` 拿不到板块历史。** `param=pt01801120,day,,,640,qfq` 只回 1 根（当天），
   带显式区间回 0 根。所以板块必须走 `newfqkline`，而它的行是 **11 字段**
   （`fqkline` 是 6/7 字段），`_rows_to_frame` 无法复用 —— 硬塞进一个模块里，
   两个行形状的分支迟早会串味。

2. **`newfqkline` 的 `count` 上限是 2000，且只能从 `end` 往回翻。**
   实测 `count=2000` → 2000 根；`2500`/`3000`/`4000` → `{"msg":"param error"}`
   且 `data` 是一个 **`list`**（不是 dict，`.get` 会抛 `AttributeError`）。
   `start` **被完全忽略**（`2020-01-01` 与空值回同一页），`end` **被采纳且含当天**
   （`end=2018-07-05` 的一页就结束在 `2018-07-05`），而行是**升序**的，
   所以翻页把 `end` 往前挪到上一页首行的**前一天**，两页**零重叠**。
   `pt01801120` 全历史 = 3 页 = **5492 根**，`2004-03-01..2026-09-30`。

## 行形状

```
["2024-02-07","18234.74","18526.96","18526.96","18191.70","15615500.00",{},"1.80","3884437.00","0.00","0.00"]
  ts          open      close     high      low       volume        {} 涨跌幅  成交额(万元)
```

**`close` 在 `high`/`low` 之前**（与 `tencent_source` 同一个坑），按
`open,high,low,close` 读会把 high 当 low，且不报任何错。索引 6 是一个空对象 `{}`。

成交额在索引 8、单位**万元**：拿 `sh000001` 对照过 —— `newfqkline` 回
`49591987.81`，即 4959 亿元，与上证指数 2024-02 的日成交额量级吻合。
（这与 `tencent_source` 的 `_AMOUNT_IDX = 8` / `_AMOUNT_SCALE = 10_000.0`
是同一个约定，两个源各自持有一份常量，因为行形状不同、不能共享解析器。）

## 口径

**不复权**：板块是**指数**，指数没有除权除息，不存在复权口径
（与 A 股指数走 `adjust_for → "3"` 同理，见 D-19）。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.request

import pandas as pd

from .types import DataSourceError, empty_frame, normalize, truncate

log = logging.getLogger(__name__)

#: 板块日线端点。**不是** `tencent_source.HK_RAW_KLINE`（那个是 `fqkline`）。
BOARD_KLINE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"

#: 一页多少根。**2000 是实测上限**：`2500`/`3000`/`4000` 回 `param error`。
PAGE = 2000

#: 最多翻几页。实测 `pt01801120` 3 页到底（5492 根）；给 12 页余量足够任何板块。
MAX_PAGES = 12

#: 翻页地板：比数据商最早的板块数据（`2004-03-01`）早即可。
FLOOR = "1990-01-01"

#: 少于这个根数就当「没拿到」。板块最早的从 2004 年起，30 根是很松的下限；
#: 它的作用是拦住「接口变了、只回 1 根」这种静默的半截数据。
MIN_ROWS = 30

#: 与 `tencent_source` 同一个约定：成交额在索引 8、单位万元。
_AMOUNT_IDX = 8
_AMOUNT_SCALE = 10_000.0

#: 期望的返回键名。板块没有复权词，键名恒为 `day`。
EXPECTED_KEY = "day"

#: 行字段序。**close 在 high/low 之前**（见模块 docstring）。
_COLS = ("ts", "open", "close", "high", "low", "volume")

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) chanlun/0.5"


def _get_json(url: str, timeout: int = 30) -> dict:
    """取一个 JSON 端点。网络/解析失败一律转成 `DataSourceError`。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https 域名
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - 统一成 DataSourceError 交给 sync 吞
        raise DataSourceError(f"腾讯板块取数失败 url={url}: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"腾讯板块返回的不是 JSON url={url}") from exc


def _node(payload: dict, symbol: str) -> dict:
    """从 `{"data": {<symbol>: {...}}}` 里取出这个板块的节点。

    **必须判类型**：实测 `count>=2500` 时 `data` 直接是一个 JSON `list`
    （错误响应 `{"msg":"param error","data":[]}`），`data[symbol]` 会抛 `TypeError`。
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DataSourceError(
            f"腾讯板块响应 data 不是对象（{type(data).__name__}）symbol={symbol} "
            f"msg={payload.get('msg')!r}"
        )
    node = data.get(symbol)
    if not isinstance(node, dict):
        raise DataSourceError(f"腾讯板块响应缺少 {symbol} 节点（{type(node).__name__}）")
    return node


def _check_key(node: dict, symbol: str) -> list:
    """取出 K 线数组，并断言返回键名就是 `day`。

    板块没有复权，键名应当是 `day`。若哪天它开始回 `qfqday`/`hfqday`，
    说明数据商把板块也纳入了复权分派 —— 那种漂移**不会报错**，只会让库里
    混进另一种口径的价格，所以在这里拦死。
    """
    keys = [k for k in node if k.endswith("day")]
    if not keys:
        raise DataSourceError(f"腾讯板块响应里没有日线键 symbol={symbol} keys={sorted(node)[:8]}")
    key = keys[0]
    if key != EXPECTED_KEY:
        raise DataSourceError(
            f"腾讯板块日线返回键名是 {key!r}，期望 {EXPECTED_KEY!r} "
            f"—— 数据商改了口径分派，拒绝落库 symbol={symbol}"
        )
    return node[key]


def _page(symbol: str, end: str) -> list:
    """取一页：`param=<sym>,day,,<end>,2000,`。

    **第七位（复权词）必须留一个空位**：实测 `...,5`（只有 6 段）回
    `{"msg":"bad params"}`，`...,5,`（7 段、末段为空）才有数据。

    `start` 段固定留空：实测它**被完全忽略**（传 `2020-01-01` 与留空回同一页），
    所以翻页只能从 `end` 往回走。
    """
    url = f"{BOARD_KLINE}?param={symbol},day,,{end},{PAGE},"
    return _check_key(_node(_get_json(url), symbol), symbol)


def _rows_to_frame(rows: list) -> pd.DataFrame:
    """11 字段行 → BarFrame。字段错位会静默算错，所以按索引逐个取。"""
    recs: list[dict[str, float | str]] = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        try:
            rec: dict[str, float | str] = {
                "ts": str(r[0]),
                "open": float(r[1]),
                "close": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "volume": float(r[5]),
            }
        except (TypeError, ValueError):
            continue
        # 缺成交额不是错误，成交量仍在（`amount` 在 BarFrame 里允许为 0）。
        rec["amount"] = 0.0
        if len(r) > _AMOUNT_IDX:
            try:
                rec["amount"] = float(r[_AMOUNT_IDX]) * _AMOUNT_SCALE
            except (TypeError, ValueError):
                rec["amount"] = 0.0
        recs.append(rec)
    if not recs:
        return empty_frame()
    df = pd.DataFrame(recs)
    return normalize(df[list(_COLS) + ["amount"]])


def _paged_day(symbol: str, start: str, end: str) -> pd.DataFrame:
    """日线翻页：**固定 `count=2000`，把 `end` 往前挪到上一页首行的前一天**。

    `end` 含当天且**被采纳**（实测 `end=2018-07-05` 的一页就是 `…2018-07-05`），
    而行是**升序**的（`rows[0]` 是最老那根），所以下一页的 `end` 取
    `上一页首行 - 1 天`，两页**零重叠**。实测 `pt01801120`：
    `2018-07-06..2026-09-30`(2000) + `2010-04-15..2018-07-05`(2000)
    + `2004-03-01..2010-04-14`(1492) = **5492 根，去重后仍是 5492**
    （`is_unique` 为真 —— 减一天这一步就是防重叠的）。
    `normalize` 的 `drop_duplicates` 仍然留着：它是**兜底**，不是这一步的依靠。

    停止条件有五个，缺一不可：
    1. 这一页空了；
    2. 这一页不足 `PAGE` 根（到地板了）；
    3. 已经翻到请求的 `start` 之前（增量同步不必取全历史）；
    4. `end` 没有往前挪（数据商回了一个不含更早数据的页）—— 没有这条会死循环；
    5. `end` 挪到 `FLOOR` 之前（数据商最早的板块数据是 `2004-03-01`，再往前问没有意义）。
    """
    by_ts: dict[str, dict] = {}
    cursor = end
    pages = 0
    for _ in range(MAX_PAGES):
        rows = _page(symbol, cursor)
        pages += 1
        if not rows:
            break
        for rec in _rows_to_frame(rows).to_dict("records"):
            by_ts[str(rec["ts"])] = rec
        if len(rows) < PAGE:
            break
        first = str(rows[0][0]) if isinstance(rows[0], (list, tuple)) else ""
        if not first or first >= cursor:
            log.warning("腾讯板块翻页停滞 symbol=%s end=%s first=%s，已取 %d 根",
                        symbol, cursor, first, len(by_ts))
            break
        if first <= start:
            break
        nxt = (dt.date.fromisoformat(first[:10]) - dt.timedelta(days=1)).isoformat()
        if nxt < FLOOR:
            break
        cursor = nxt
    else:
        log.warning("腾讯板块翻页达到上限 %d 页 symbol=%s", MAX_PAGES, symbol)

    if not by_ts:
        raise DataSourceError(
            f"{symbol} 的日线一根都没取到（end={end}）—— 数据商可能改了行为，拒绝落库"
        )
    df = pd.DataFrame(list(by_ts.values()))
    df = normalize(df[list(_COLS) + ["amount"]])
    log.info("腾讯板块日线 symbol=%s 页数=%d 取回=%d", symbol, pages, len(df))
    return df


def fetch_bars(
    code: str,
    symbol: str,
    period: str,
    start: str,
    end: str,
    adjust: str = "3",
) -> pd.DataFrame:
    """申万行业板块日线。`code` 是落库键（`sw.801120`），`symbol` 是 `pt01801120`。

    **只提供日线**：板块在腾讯那里没有分钟线（`mkline` 回 `param error`），
    周/月由 `sources.fetch_bars` 从日线本地聚合，不走这里。

    `adjust` 参数只为与其它源同签名：板块是指数，**没有复权口径**，
    传什么都是不复权。真要一种不存在的口径由 `sources.adjust_for` 收敛，
    不在这里报错（与 `baostock` 路径对指数传 `"3"` 同理）。
    """
    if str(period) not in ("day", "d"):
        raise DataSourceError(f"申万行业板块只有日线，不提供 {period}（spec §2.1）")

    df = _paged_day(symbol, str(start)[:10], str(end)[:10])

    # ★ 行数护栏判在**裁剪之前**。腾讯只约束 `end`、不约束 `start`，所以一页
    # 通常给 2000 根，增量同步只要二十几个交易日就会被裁成二十几根。**那是正确答案**，
    # 不是数据商坏了 —— 拿裁剪后的行数去比阈值会把每一次正常的增量同步都判成失败
    # （同一个坑在 `tencent_source._paged_day` 已经踩过一次，注释在那里）。
    # 护栏真正要拦的是「返回码正常但只回 1 根」那种静默假成功，所以它必须量
    # **数据商给了多少**，而不是**我们最后用了多少**。
    if len(df) < MIN_ROWS:
        raise DataSourceError(
            f"{symbol} 的日线只回 {len(df)} 行（少于 {MIN_ROWS}）"
            f"，数据商可能改了行为 —— 拒绝落库"
        )

    # 区间在本地裁：腾讯只约束 `end`，多给的更早数据要按 `start` 裁掉，
    # 否则 `sync` 的增量起点形同虚设（每次都把全历史重取一遍再 upsert）。
    return truncate(df, start=str(start)[:10], end=str(end)[:10])
