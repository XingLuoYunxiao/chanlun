"""派生周期：周线/月线由**本地日线**聚合而来（Task 29）。

为什么不单独去数据源同步一份周线/月线：

1. 本地已经有 5434 只票的日线，聚合是纯本地计算，**立刻可用**、不占网络也不占磁盘；
2. 两份数据的复权口径一定会分叉 —— 日线是前复权/不复权混着落的（一期 baostock、
   二期通达信），单独同步的周线无法跟着日线的口径走，除权日附近两张图会对不上；
3. 缠论结构是**价格历史的性质**，周线中枢必须由同一份价格序列聚合出来才自洽。

口径（与同花顺/通达信一致）：

- **自然周**（ISO，周一到周日）、**自然月**：不是「每 5 根 / 每 20 根」——
  节假日少几天，按根数切会把上一周的最后一天并进这一周；
- 开=该周期第一个交易日开盘，收=最后一个交易日收盘，高/低=区间极值，量/额=区间求和；
- 时间戳=该周期**最后一个交易日**（通达信/同花顺的周K都标在那一周最后那天）；
- **当周/当月没走完也出一根**（用户 2026-10-01 选定）：不画的话盯盘时本周会凭空消失，
  代价是最后一根会随日线每天变 —— 所以 `is_complete` 必须能说出来它还没定型。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from .types import COLUMNS, normalize

# 派生周期 → 它由哪个**已落库**的周期聚合而来
DERIVED: dict[str, str] = {"week": "day", "month": "day"}
DERIVED_PERIODS: tuple[str, ...] = tuple(DERIVED)
LABELS: dict[str, str] = {
    "day": "日线", "week": "周线", "month": "月线",
    "60": "60分", "30": "30分", "15": "15分", "5": "5分",
}


def is_derived(period: str) -> bool:
    """是不是派生周期。**分钟周期的范围规则（F5）必须用它排除周/月**：
    周/月由日线算，跟自选池不沾边，按 `period != "day"` 判会把它们错圈进分钟池。"""
    return str(period) in DERIVED


def base_period(period: str) -> str:
    """派生周期的数据来源周期；非派生周期返回自身。"""
    return DERIVED.get(str(period), str(period))


def label(period: str) -> str:
    return LABELS.get(str(period), str(period))


def aggregate(df: pd.DataFrame | None, period: str) -> pd.DataFrame:
    """把日线聚合成周线/月线。返回的仍是标准 BarFrame（列序/类型/排序同 `normalize`）。"""
    key = str(period)
    if key not in DERIVED:
        raise ValueError(f"{period!r} 不是派生周期（可选 {'/'.join(DERIVED_PERIODS)}）")
    if df is None or len(df) == 0:
        return normalize(None)

    src = normalize(df)
    grouped = src.groupby(_group_keys(src["ts"], key), sort=True)
    out = pd.DataFrame(
        {
            # 时间戳取该周期最后一个交易日：`ts` 是 `YYYY-MM-DD`，字典序即时序
            "ts": grouped["ts"].max(),
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(),
            "amount": grouped["amount"].sum(),
        }
    )
    return normalize(out[COLUMNS])


def period_end(day: dt.date, period: str) -> dt.date:
    """某个交易日所属自然周/自然月的**最后一天**（周日 / 月末）。"""
    if str(period) == "week":
        return day + dt.timedelta(days=6 - day.weekday())
    if str(period) == "month":
        nxt = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return nxt - dt.timedelta(days=1)
    raise ValueError(f"{period!r} 不是派生周期")


def is_complete(last_ts: str, period: str, cal) -> bool:
    """派生周期的最后一根是否**已经走完**（该周期后面还有没有交易日）。

    没走完的那一根会随日线每天变，页面必须标出来，否则读图的人会把一根
    还在变的周K当成定论。`cal` 是 `chanlun.calendar.Calendar`（只用到
    `last_trading_day`），传进来是为了让这个判断可以在单测里脱离真实日历跑。
    """
    day = dt.date.fromisoformat(str(last_ts)[:10])
    return cal.last_trading_day(period_end(day, str(period))) == day


def _group_keys(ts: pd.Series, period: str) -> pd.Series:
    """分组键。周用 **ISO 年 + 周号**：跨年那一周（如 2025-12-29 起）必须整体落在
    同一组里，用日历年会把 12 月那三天和 1 月切成两根。

    键的名字必须**不叫 `ts`**：分组键会变成结果的索引名，和同名的列一起出现时
    `grouped["ts"]` 是「索引层还是列」的二义，pandas 直接抛错。
    """
    days = pd.to_datetime(ts.astype(str).str.slice(0, 10), format="%Y-%m-%d")
    if period == "week":
        iso = days.dt.isocalendar()
        key = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    else:
        key = days.dt.strftime("%Y-%m")
    return key.rename("bucket")
