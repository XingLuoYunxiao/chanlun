"""baostock 主数据源。

实测（2026-09-29）：日线可回溯至个股上市首日；30/60/15/5 分钟自 2020-01-02 起，
30 分钟 13080 根、5 分钟 78480 根；支持前复权（adjustflag="2"）且锚定当前价。

注意：日线字段**不能包含 `time`**，否则 baostock 报
`日线指标参数传入错误:time`。
"""

from __future__ import annotations

import datetime as dt
import logging
import time as _time

import baostock as bs
import pandas as pd

from .types import DataSourceError, empty_frame, normalize

log = logging.getLogger(__name__)

DAY_FIELDS = "date,open,high,low,close,volume,amount"
MIN_FIELDS = "date,time,open,high,low,close,volume,amount"

MIN_PERIODS = {"60", "30", "15", "5"}
DAY_PERIODS = {"day", "d", "week", "w", "month", "m"}

RETRIES = 3
RETRY_SLEEP = 1.5

_LOGGED_IN = False


def _ensure_login() -> None:
    global _LOGGED_IN
    if _LOGGED_IN:
        return
    lg = bs.login()
    if lg.error_code != "0":
        raise DataSourceError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")
    _LOGGED_IN = True


def logout() -> None:
    global _LOGGED_IN
    if _LOGGED_IN:
        try:
            bs.logout()
        finally:
            _LOGGED_IN = False


# 首位数字 -> 交易所。None 表示沪深两市都存在同号段（如 sh.000001 上证指数
# 与 sz.000001 平安银行、sh.113xxx 与 sz.123xxx 可转债），无法据首位数判定。
_MARKET_BY_HEAD: dict[str, str | None] = {
    "6": "sh", "9": "sh", "5": "sh",
    "2": "sz", "3": "sz",
    "4": "bj", "8": "bj",
    "0": None, "1": None, "7": None,
}


def to_bs_code(code: str) -> str:
    """`600000` -> `sh.600000`；已带前缀则校验一致性后原样返回。

    校验很关键：`sh.300059` 这类写错交易所的代码，baostock 不会报错，
    而是以 `error_code="0"` 返回零行。若不拦截，全市场同步会静默把该股
    当成「数据为空」，下游则安静地得出「无信号」。
    """
    c = str(code).strip().lower()
    if "." in c:
        market, _, num = c.partition(".")
        if not num:
            raise ValueError(f"代码缺少数值部分: {code!r}")
        expected = _MARKET_BY_HEAD.get(num[0])
        if expected is not None and market != expected:
            raise ValueError(
                f"交易所前缀与代码不符: {code!r}，{num} 应为 {expected}.{num}"
            )
        return c
    if not c:
        raise ValueError("空代码")
    head = c[0]
    if head in ("6", "5", "9"):
        return f"sh.{c}"
    if head in ("0", "1", "2", "3"):
        return f"sz.{c}"
    if head in ("4", "8"):
        return f"bj.{c}"
    raise ValueError(f"无法判断交易所: {code}（请显式给出前缀，如 sh.000001）")


def strip_bs_code(bs_code: str) -> str:
    return bs_code.split(".", 1)[1] if "." in bs_code else bs_code


def period_to_frequency(period: str) -> str:
    p = str(period).strip().lower()
    if p in DAY_PERIODS:
        return "d" if p == "day" else p
    if p in MIN_PERIODS:
        return p
    raise ValueError(f"不支持的周期: {period!r}（支持 day/60/30/15/5）")


def fetch_bars(
    code: str,
    period: str,
    start: str | dt.date,
    end: str | dt.date,
    adjust: str = "2",
) -> pd.DataFrame:
    """拉取前复权K线。失败重试 RETRIES 次后抛 DataSourceError。"""
    global _LOGGED_IN
    bs_code = to_bs_code(code)
    freq = period_to_frequency(period)
    is_minute = freq in MIN_PERIODS
    fields = MIN_FIELDS if is_minute else DAY_FIELDS
    start_s, end_s = _as_day(start), _as_day(end)

    last_err = "unknown"
    for attempt in range(1, RETRIES + 1):
        _ensure_login()
        rs = bs.query_history_k_data_plus(
            bs_code,
            fields,
            start_date=start_s,
            end_date=end_s,
            frequency=freq,
            adjustflag=str(adjust),
        )
        if rs.error_code == "0":
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows or attempt == RETRIES:
                return _rows_to_frame(rows, is_minute)
            # 实测：baostock 对不存在的证券（如把 sz.300059 写成 sh.300059）
            # 或会话异常时，都以 error_code="0" 静默返回零行。直接采信会让
            # 下游安静地得出「该股无信号」。重登后重试，只有最后一次仍为空
            # 才接受（真空区间是合法结果，如上市前、退市后）。
            last_err = "error_code=0 但零行（疑似代码不存在或会话异常）"
            log.warning("baostock 返回空数据 code=%s period=%s 第 %d 次，重登重试",
                        code, period, attempt)
        else:
            last_err = f"{rs.error_code} {rs.error_msg}"
            log.warning("baostock 拉取失败 code=%s period=%s 第 %d 次: %s",
                        code, period, attempt, last_err)

        _LOGGED_IN = False  # 会话可能已失效，强制重登后重试
        _time.sleep(RETRY_SLEEP)

    raise DataSourceError(f"baostock 拉取失败 code={code} period={period}: {last_err}")


def _rows_to_frame(rows: list[list[str]], is_minute: bool) -> pd.DataFrame:
    if not rows:
        return empty_frame()

    if is_minute:
        # 列序: date,time,open,high,low,close,volume,amount
        # time 形如 20260929150000000 -> HH:MM
        records = []
        for r in rows:
            date_s, time_s = r[0], r[1]
            hm = f"{time_s[8:10]}:{time_s[10:12]}" if len(time_s) >= 12 else "00:00"
            records.append([f"{date_s} {hm}", *r[2:8]])
    else:
        records = [[r[0], *r[1:7]] for r in rows]

    df = pd.DataFrame(records, columns=["ts", "open", "high", "low", "close", "volume", "amount"])
    return normalize(df)


def _as_day(value: str | dt.date) -> str:
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)[:10]
