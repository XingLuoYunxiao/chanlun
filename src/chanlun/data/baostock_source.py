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


def to_bs_code(code: str) -> str:
    """`600000` -> `sh.600000`；已带前缀则原样返回。"""
    c = str(code).strip().lower()
    if "." in c:
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
    raise ValueError(f"无法判断交易所: {code}")


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
            return _rows_to_frame(rows, is_minute)

        last_err = f"{rs.error_code} {rs.error_msg}"
        log.warning("baostock 拉取失败 code=%s period=%s 第 %d 次: %s",
                    code, period, attempt, last_err)
        global _LOGGED_IN
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
