"""品种表：全市场 A 股清单（含退市股，避免幸存者偏差）。

策略（针对 baostock 免费源的限制）：
1. `query_all_stock(day=...)` 传入历史日期会返回「那一天在市」的证券，
   因此对 2015 年至今每年最后一个交易日各查一次并取**并集**，
   这样 2015 年后退市的股票也会进表。
2. 该接口同时返回指数/基金/可转债，用代码前缀规则先过滤（`is_a_stock`）。
3. `list_date` / `delisted` 需要逐只 `query_stock_basic`，7000+ 次调用很慢，
   因此默认 `enrich=False`（默认路径 < 3 分钟）；需要精确退市标记时
   显式 `enrich=True`，或按需调用 `fetch_stock_basic` 惰性补全。

登录/登出复用 `baostock_source` 的会话状态（`_ensure_login` / `logout`），
避免直接 `bs.logout()` 后 `baostock_source._LOGGED_IN` 仍为 True 导致后续
`fetch_bars` 使用失效会话。
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import time
from dataclasses import dataclass

import baostock as bs

from ..calendar import get_calendar
from . import baostock_source as bsrc
from . import meta
from .baostock_source import strip_bs_code, to_bs_code

log = logging.getLogger(__name__)

# 覆盖 2015 年之后退市的股票；再早的退市股不在本表范围
UNIVERSE_START_YEAR = 2015
# 一年最后一个交易日最多向前回退的天数（覆盖长假）
MAX_BACKFILL_DAYS = 15
# 少于该行数视为缓存不可用，需要重新拉取
MIN_CACHE_ROWS = 1000
# 单次查询最多收集的行数：实测 baostock 服务端偶发翻页异常时
# `ResultData.next()` 会一直返回 True（同一页重复返回），必须截断避免死循环
MAX_ROWS_PER_QUERY = 20000
# query_stock_basic 单只最多行数（正常 0 或 1 行）
MAX_BASIC_ROWS = 50
# 会话可能被服务端中途回收（实测 error_code=10001001 用户未登录），重登重试
QUERY_RETRIES = 3
QUERY_RETRY_SLEEP = 1.0


@dataclass(frozen=True)
class Security:
    """品种表条目（frozen dataclass）。"""

    code: str
    bs_code: str
    name: str
    market: str
    list_date: str | None = None
    delisted: bool = False


def is_a_stock(bs_code: str) -> bool:
    """按代码前缀判断是否 A 股（含退市/ST/B 股），排除指数、基金、可转债。

    规则依据实测（2026-09-29 query_all_stock 全量 7406 条）：
    - sh.6xxxxx 主板/科创板，sh.9xxxxx B 股；sh.0xxxxx 指数、sh.5xxxxx 基金
    - sz.00xxxx/30xxxx 主板/创业板，sz.20xxxx B 股；sz.1xxxxx 基金/债券、
      sz.39xxxx 指数
    - bj.4xxxxx/8xxxxx/92xxxx 北交所
    """
    raw = str(bs_code).strip().lower()
    if "." not in raw:
        return False
    market, _, num = raw.partition(".")
    if not num.isdigit():
        return False
    if market == "sh":
        return num[0] in ("6", "9")
    if market == "sz":
        return num[0] in ("0", "2") or (num[0] == "3" and not num.startswith("39"))
    if market == "bj":
        return num[0] in ("4", "8", "9")
    return False


def year_end_days(today: dt.date | None = None) -> list[dt.date]:
    """2015 至今每年最后一个自然日（可能非交易日，由回退逻辑处理）。"""
    today = today or dt.date.today()
    days: list[dt.date] = []
    for year in range(UNIVERSE_START_YEAR, today.year + 1):
        days.append(min(dt.date(year, 12, 31), today))
    return days


def build_universe(
    refresh: bool = False,
    enrich: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[Security]:
    """构建并落库品种表。

    - `refresh=False` 且库内已有足够行数时直接读缓存（不联网）。
    - `enrich=True` 逐只 `query_stock_basic` 补全 `list_date`/`delisted` 并
      按 `type == 1` 精确过滤；很慢，默认关闭。
    """
    own_conn = conn is None
    conn = conn if conn is not None else meta.init()
    try:
        if not refresh:
            cached = meta.get_universe(conn)
            if len(cached) >= MIN_CACHE_ROWS:
                log.info("品种表命中缓存：%d 只", len(cached))
                return [_row_to_security(r) for r in cached]

        try:
            secs = _fetch_union()
            if enrich:
                secs = _enrich(secs)
        finally:
            # 只关闭 baostock 会话；socket 会话状态与 baostock_source 同步
            bsrc.logout()

        if not secs:
            log.warning("品种表为空：baostock 可能不可用")
            return []

        meta.upsert_universe(
            conn,
            [(s.code, s.bs_code, s.name, s.market, s.list_date, s.delisted) for s in secs],
        )
        log.info("品种表已更新：%d 只（enrich=%s）", len(secs), enrich)
        return secs
    finally:
        if own_conn:
            conn.close()


def fetch_stock_basic(bs_code: str) -> dict | None:
    """单只证券基础信息（惰性补全用）；查不到返回 None。"""
    return _query_stock_basic(to_bs_code(bs_code))


# ---------------- 内部：抓取 ----------------
def _fetch_union() -> list[Security]:
    merged: dict[str, Security] = {}
    for day in year_end_days():
        rows = _all_stock_rows(day)
        log.info("query_all_stock(%s) -> %d 行", day.isoformat(), len(rows))
        for row in rows:
            if len(row) < 3:
                continue
            raw = str(row[0]).strip().lower()
            if not raw:
                continue
            try:
                bs_code = to_bs_code(raw)
            except ValueError:
                continue
            if not is_a_stock(bs_code):
                continue
            name = str(row[2]).strip()
            prev = merged.get(bs_code)
            if prev is not None and not name:
                name = prev.name
            merged[bs_code] = Security(
                code=strip_bs_code(bs_code),
                bs_code=bs_code,
                name=name,
                market=bs_code.split(".", 1)[0],
            )
    return sorted(merged.values(), key=lambda s: s.code)


def _candidate_day(day: dt.date) -> dt.date:
    """把自然日校正为 <= day 的交易日。

    年尾（12-31）常落在周末或节假日，逐日探测每个年份要多花十几次请求
    （实测每次约 10s），因此优先用项目日历直接定位；日历不可用时退回原日期，
    由后续逐日回退逻辑兜底。
    """
    try:
        return get_calendar().last_trading_day(day)
    except Exception as exc:  # noqa: BLE001 - 日历不可用不应影响品种表构建
        log.debug("交易日历不可用（%s），直接查询 %s", exc, day.isoformat())
        return day


def _all_stock_rows(day: dt.date) -> list[list[str]]:
    """查某日在市证券；先用日历定位交易日，仍为空则逐日回退。"""
    cur = _candidate_day(day)
    if cur != day:
        log.debug("query_all_stock: %s 非交易日，改用 %s", day.isoformat(), cur.isoformat())
    for _ in range(MAX_BACKFILL_DAYS):
        rows = _query_all_stock(cur)
        if rows:
            return rows
        log.debug("query_all_stock(%s) 为空，向前回退", cur.isoformat())
        cur -= dt.timedelta(days=1)
    log.warning("query_all_stock(%s) 连续 %d 天为空", day.isoformat(), MAX_BACKFILL_DAYS)
    return []


def _enrich(secs: list[Security]) -> list[Security]:
    out: list[Security] = []
    for s in secs:
        info = _query_stock_basic(s.bs_code)
        if info is None:
            # 查不到记录（多为北交所/极老退市股）仍然保留，避免丢品种
            out.append(s)
            continue
        if str(info.get("type", "1")).strip() != "1":
            continue  # 指数/基金/可转债
        out_date = str(info.get("outDate") or "").strip()
        status = str(info.get("status") or "1").strip()
        name = str(info.get("code_name") or s.name).strip()
        out.append(
            Security(
                code=s.code,
                bs_code=s.bs_code,
                name=name,
                market=s.market,
                list_date=str(info.get("ipoDate") or "").strip() or None,
                delisted=bool(out_date) or status == "0",
            )
        )
    return out


# ---------------- 内部：baostock 调用 ----------------
def _collect_rows(rs, limit: int, what: str) -> list[list[str]]:
    """收集结果集，最多 `limit` 行。

    baostock 的 `next()` 在服务端翻页异常时会永不返回 False，
    因此这里用上限兜底：超限说明响应异常，记警告并截断（已收集的行仍有效）。
    """
    rows: list[list[str]] = []
    while len(rows) < limit and rs.next():
        rows.append(rs.get_row_data())
    if len(rows) >= limit:
        log.warning("%s 返回行数达到上限 %d，疑似服务端翻页异常，已截断", what, limit)
    return rows


def _query_with_relogin(call, what: str):
    """执行 baostock 查询；会话被服务端回收时强制重登并重试。

    实测一次品种表构建（12 次 query_all_stock，约 2 分钟）中偶发
    `error_code=10001001 用户未登录`：`baostock_source._LOGGED_IN` 仍为 True，
    导致后续查询全部失败。这里沿用 `fetch_bars` 的做法，把标记置 False 后重试。
    """
    last = "unknown"
    for attempt in range(1, QUERY_RETRIES + 1):
        bsrc._ensure_login()  # noqa: SLF001 - 复用会话状态
        rs = call()
        if rs.error_code == "0":
            return rs
        last = f"{rs.error_code} {rs.error_msg}"
        if attempt < QUERY_RETRIES:
            log.warning("%s 第 %d 次失败（%s），重登后重试", what, attempt, last)
            bsrc._LOGGED_IN = False  # noqa: SLF001 - 会话可能已失效，强制重登
            time.sleep(QUERY_RETRY_SLEEP)
    raise RuntimeError(f"{what} 失败: {last}")


def _query_all_stock(day: dt.date) -> list[list[str]]:
    what = f"query_all_stock({day.isoformat()})"
    rs = _query_with_relogin(lambda: bs.query_all_stock(day=day.isoformat()), what)
    return _collect_rows(rs, MAX_ROWS_PER_QUERY, what)


def _query_stock_basic(bs_code: str) -> dict | None:
    what = f"query_stock_basic({bs_code})"
    rs = _query_with_relogin(lambda: bs.query_stock_basic(code=bs_code), what)
    rows = _collect_rows(rs, MAX_BASIC_ROWS, what)
    if not rows:
        return None
    return dict(zip(rs.fields, rows[0]))


def _row_to_security(row: sqlite3.Row) -> Security:
    return Security(
        code=row["code"],
        bs_code=row["bs_code"],
        name=row["name"] or "",
        market=row["market"] or row["bs_code"].split(".", 1)[0],
        list_date=row["list_date"] or None,
        delisted=bool(row["delisted"]),
    )
