"""单只票单周期的取数与落库。

**命令行与看盘页共用这一份。** `python -m chanlun sync` / `daily` 和页面上的
「同步这个周期」按钮如果各写一套增量起点和复权口径，迟早会分叉成两个不同的库：
一边从 `--since` 起拉、另一边纯增量，同一只票在两条路径下会得到不同的 parquet。

分工：本模块只管**取一只票一个周期**；批量、进度条、报告留给调用方。
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass

from ..calendar import get_calendar
from . import markets, meta, store
from .baostock_source import fetch_bars, to_bs_code

log = logging.getLogger("chanlun.sync")

#: 首次同步的起点：1990 年早于交易所开市，用它表示「从头拉」。
FULL_START = "1990-01-01"


def adjust_for(code: str, cfg) -> str:  # noqa: ANN001 - cfg 是 Config，避免循环导入
    """这只票该按哪个复权口径落库。

    指数没有除权除息，必须 `3`（不复权）：否则 `sync_state.adjust` 会写「前复权」，
    页面在指数上显示「前复权」，而它的含义（除权参考价折算）对指数根本不成立。
    """
    return "3" if markets.is_index(to_bs_code(code)) else str(cfg.bs_adjust)


def start_for(
    code: str, period: str, full: bool, cal, end: dt.date, since: dt.date | None = None
) -> str | None:
    """返回拉取起始日；已是最新则返回 `None`（跳过）。

    `since` 只作用于**本地还没有数据的票**：首次全市场同步没必要为 5471 只票
    各拉 35 年。已有数据的票仍然纯增量 —— 若把起点强行压到 `since`，本地数据停在
    上古年份的票就会被跳过中间几年、在文件里留下空洞，而缠论结构会跨着洞算，
    这比少几年历史危险。
    """
    if full:
        return FULL_START
    last = store.last_ts(code, period)
    if not last:
        return (since or dt.date.fromisoformat(FULL_START)).isoformat()
    nxt = cal.next_trading_day(str(last)[:10])
    return None if nxt > end else nxt.isoformat()


@dataclass(frozen=True)
class SyncOneResult:
    """一只票一个周期的同步结果。

    `status` 三态与 CLI 的计数器一一对应：`ok` 落到了新数据、`skipped` 源返回零行或
    本地已是最新（**不是错误**）、`failed` 取数抛异常（原因留在 `error` 里，页面要显示它）。
    """

    code: str
    period: str
    status: str
    rows: int = 0
    start_ts: str | None = None
    end_ts: str | None = None
    adjust: str = "2"
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def sync_one(
    conn: sqlite3.Connection,
    cfg,  # noqa: ANN001 - Config
    code: str,
    period: str,
    *,
    full: bool = False,
    since: dt.date | None = None,
    cal=None,  # noqa: ANN001 - Calendar
    fetch=None,  # noqa: ANN001 - 取数函数；缺省用本模块的 fetch_bars
) -> SyncOneResult:
    """取一只票一个周期并落库。**任何失败都只记录、不抛**。

    抛出去的异常会让批量同步在第一个坏代码上停住，而收盘后的行情是**不可再生**的：
    一次网络抖动就整批跳过，代价远比多跑几分钟大。

    `fetch` 是给调用方注入自己的取数函数用的：命令行把自己的 `fetch_bars` 传进来，
    这样 `cli.fetch_bars` 仍然是那个可打桩的点（换掉它不该让 20 个命令行测试全改）。
    """
    fetch = fetch or fetch_bars
    cal = cal or get_calendar()
    end = cal.last_trading_day(dt.date.today())
    key = str(code)
    adjust = "2"

    try:
        key = markets.store_key(to_bs_code(code))
        adjust = adjust_for(key, cfg)
        start = start_for(key, period, full, cal, end, since)
        if start is None:
            return SyncOneResult(key, period, "skipped", adjust=adjust)
        df = fetch(to_bs_code(key), period, start, end, adjust=adjust)
        if len(df) == 0:
            meta.set_sync(conn, key, period, None, None, 0, adjust, error=None)
            return SyncOneResult(key, period, "skipped", adjust=adjust)
        store.upsert(key, period, df)
        stored = store.read(key, period)
        start_ts = str(stored["ts"].iloc[0]) if len(stored) else None
        end_ts = str(stored["ts"].iloc[-1]) if len(stored) else None
        meta.set_sync(conn, key, period, start_ts, end_ts, len(stored), adjust, error=None)
        return SyncOneResult(
            key, period, "ok", rows=len(stored), start_ts=start_ts, end_ts=end_ts, adjust=adjust
        )
    except Exception as exc:  # noqa: BLE001 - 单只失败不许中断整批
        log.warning("同步失败 code=%s period=%s: %s", code, period, exc)
        prev = meta.get_sync(conn, key, period)
        meta.set_sync(
            conn,
            key,
            period,
            prev["start_ts"] if prev else None,
            prev["end_ts"] if prev else None,
            int(prev["rows"]) if prev else 0,
            adjust,
            error=str(exc),
        )
        return SyncOneResult(key, period, "failed", adjust=adjust, error=str(exc))
