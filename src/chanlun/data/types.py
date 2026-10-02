"""数据层公共类型与规范化。

统一的 BarFrame 契约（全系统唯一真源）：
- 列固定为 `ts, open, high, low, close, volume, amount`
- `ts` 为字符串：日线 `YYYY-MM-DD`，分钟线 `YYYY-MM-DD HH:MM`
- 按 `ts` 升序、`ts` 唯一
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

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

    # 价格必须为正。上面 `to_numeric(errors="coerce")` 把无法解析的值变成 NaN，
    # 紧跟的 `fillna(0.0)` 又把它变成 0.0——0 价不是合法行情，却会被分型/笔当成
    # 真实极值。实测 sz.399001 日线含 5 根全 0 行（1995-02-06~1995-02-10），使
    # min(low)=0.0 成为全序列最低点，把 1991 年以来的整段走势压成一根向下的笔；
    # sh.600759 的 5 分钟线含 142 根同类行（126 段/20 中枢 → 168 段/28 中枢）。
    # 这类行直接剔除并留警告，不再静默填零。volume/amount 为 0 属正常，不在此列。
    price_cols = ["open", "high", "low", "close"]
    invalid = (out[price_cols] <= 0).any(axis=1)
    if bool(invalid.any()):
        logger.warning(
            "normalize: 剔除 %d 根非正价格 K 线（前几根 ts=%s）",
            int(invalid.sum()),
            ", ".join(out.loc[invalid, "ts"].head(5).astype(str)),
        )
        out = out[~invalid]

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
