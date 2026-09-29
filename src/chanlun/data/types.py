"""数据层公共类型与规范化。

统一的 BarFrame 契约（全系统唯一真源）：
- 列固定为 `ts, open, high, low, close, volume, amount`
- `ts` 为字符串：日线 `YYYY-MM-DD`，分钟线 `YYYY-MM-DD HH:MM`
- 按 `ts` 升序、`ts` 唯一
"""

from __future__ import annotations

import pandas as pd

COLUMNS: list[str] = ["ts", "open", "high", "low", "close", "volume", "amount"]
NUMERIC_COLUMNS: list[str] = ["open", "high", "low", "close", "volume", "amount"]


class DataSourceError(RuntimeError):
    """数据源不可用或返回异常时抛出。"""


def empty_frame() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in NUMERIC_COLUMNS})
    df.insert(0, "ts", pd.Series(dtype="object"))
    return df[COLUMNS]


def normalize(df: pd.DataFrame | None) -> pd.DataFrame:
    """强制列序、数值类型、唯一性与排序。幂等。"""
    if df is None or len(df) == 0:
        return empty_frame()

    out = df.copy()
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = 0.0 if col in NUMERIC_COLUMNS else ""
    out = out[COLUMNS]

    out["ts"] = out["ts"].astype(str).str.strip()
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).astype("float64")

    out = out[out["ts"] != ""]
    out = out.drop_duplicates(subset=["ts"], keep="last")
    out = out.sort_values("ts", kind="stable").reset_index(drop=True)
    return out


def truncate(df: pd.DataFrame, start: str | None = None, end: str | None = None,
             limit: int | None = None) -> pd.DataFrame:
    """按时间范围截断（point-in-time 回测的物理保障）。"""
    out = df
    if start is not None:
        out = out[out["ts"] >= start]
    if end is not None:
        out = out[out["ts"] <= end]
    if limit is not None:
        out = out.tail(limit)
    return out.reset_index(drop=True)
