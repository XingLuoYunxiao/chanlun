"""交易日历。

优先从 baostock 拉取真实交易日并缓存到 `data/calendar.csv`；
拉取失败时退化为「周一至周五」并在日志中 WARNING，绝不静默假装准确。
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path

from .config import load_config

log = logging.getLogger(__name__)

_CACHE: "Calendar | None" = None


class Calendar:
    def __init__(self, days: set[str], source: str = "baostock") -> None:
        self._days = days
        self.source = source

    # ---- 查询 ----
    def is_trading_day(self, d: dt.date | str) -> bool:
        return _as_str(d) in self._days

    def last_trading_day(self, d: dt.date | str) -> dt.date:
        """返回 <= d 的最近交易日。"""
        cur = _as_date(d)
        for _ in range(400):
            if cur.isoformat() in self._days:
                return cur
            cur -= dt.timedelta(days=1)
        raise LookupError(f"向前 400 天未找到交易日: {d}")

    def next_trading_day(self, d: dt.date | str) -> dt.date:
        cur = _as_date(d) + dt.timedelta(days=1)
        for _ in range(400):
            if cur.isoformat() in self._days:
                return cur
            cur += dt.timedelta(days=1)
        raise LookupError(f"向后 400 天未找到交易日: {d}")

    def trading_days(self, start: dt.date | str, end: dt.date | str) -> list[dt.date]:
        s, e = _as_date(start), _as_date(end)
        return sorted(
            dt.date.fromisoformat(d) for d in self._days if s <= dt.date.fromisoformat(d) <= e
        )

    def __len__(self) -> int:
        return len(self._days)

    # ---- 构造 ----
    @classmethod
    def load(cls, path: str | Path | None = None, refresh: bool = False) -> "Calendar":
        cfg = load_config()
        csv_path = Path(path) if path else cfg.data.calendar_csv

        if csv_path.exists() and not refresh:
            days = _read_csv(csv_path)
            if days:
                return cls(days, source=f"cache:{csv_path.name}")

        try:
            days = _fetch_from_baostock()
        except Exception as exc:  # noqa: BLE001 - 退化路径必须显式告警
            log.warning("交易日历拉取失败（%s），退化为周一至周五，日期准确性下降", exc)
            days = _weekday_fallback()
            return cls(days, source="weekday-fallback")

        if days:
            _write_csv(csv_path, days)
            return cls(days, source="baostock")
        log.warning("交易日历为空，退化为周一至周五")
        return cls(_weekday_fallback(), source="weekday-fallback")


def get_calendar(refresh: bool = False) -> Calendar:
    global _CACHE
    if _CACHE is None or refresh:
        _CACHE = Calendar.load(refresh=refresh)
    return _CACHE


#: 港股/美股的宽松日历缓存（与 A 股日历分开，互不污染）。
_WEEKDAY_CACHE: "Calendar | None" = None


def weekday_calendar() -> Calendar:
    """「周一至周五」日历，**不排除港美节假日**。港股/美股专用。

    为什么不复用 `get_calendar()`：那是 **A 股**日历，它把中国法定假日标成非交易日。
    港股在国庆/春节期间照常开市 —— 若拿 A 股日历给港股算增量起点，
    `next_trading_day("2026-09-30")` 会跳到 `2026-10-08`，
    **10-01~10-07 的港股行情会被整段跳过**，在 parquet 里留下一个洞，
    而缠论结构会跨着洞算。这比多取几天冗余危险得多。

    代价是不排除港美节假日（复活节、感恩节等）：那只会让增量起点落在
    一个休市日上，数据商返回空或从下一个交易日起 —— **少取为误，多取无碍**。
    见 ARCHITECTURE.md D-43 / spec §7.4 G8。
    """
    global _WEEKDAY_CACHE
    if _WEEKDAY_CACHE is None:
        _WEEKDAY_CACHE = Calendar(_weekday_fallback(), source="weekday")
    return _WEEKDAY_CACHE


def calendar_for(code: str) -> Calendar:
    """按市场选日历：A 股用真实交易日历，港股/美股用宽松日历。"""
    from .data import markets  # 局部导入，避免 data 层与 calendar 层的循环导入

    return get_calendar() if markets.is_a_share(code) else weekday_calendar()


# ---- 便捷函数 ----
def is_trading_day(d: dt.date | str) -> bool:
    return get_calendar().is_trading_day(d)


def last_trading_day(d: dt.date | str) -> dt.date:
    return get_calendar().last_trading_day(d)


# ---- 内部 ----
def _as_date(d: dt.date | str) -> dt.date:
    if isinstance(d, dt.date):
        return d
    return dt.date.fromisoformat(str(d)[:10])


def _as_str(d: dt.date | str) -> str:
    return _as_date(d).isoformat()


def _read_csv(path: Path) -> set[str]:
    days: set[str] = set()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("is_trading_day") == "1":
                days.add(row["calendar_date"])
    return days


def _write_csv(path: Path, days: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    span = sorted(days)
    start, end = span[0], span[-1]
    all_days = [start]
    cur = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    while cur < last:
        cur += dt.timedelta(days=1)
        all_days.append(cur.isoformat())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["calendar_date", "is_trading_day"])
        for d in all_days:
            writer.writerow([d, "1" if d in days else "0"])


def _fetch_from_baostock() -> set[str]:
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")
    try:
        today = dt.date.today()
        rs = bs.query_trade_dates(
            start_date=(today - dt.timedelta(days=365 * 12)).isoformat(),
            end_date=(today + dt.timedelta(days=400)).isoformat(),
        )
        days: set[str] = set()
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if len(row) >= 2 and row[1] == "1":
                days.add(row[0])
        return days
    finally:
        bs.logout()


def _weekday_fallback() -> set[str]:
    today = dt.date.today()
    start = today - dt.timedelta(days=365 * 12)
    end = today + dt.timedelta(days=400)
    days: set[str] = set()
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.add(cur.isoformat())
        cur += dt.timedelta(days=1)
    return days
