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

    # 结构不可能的行，分两档处置 —— 这个分档是刻意的，不是随手写的。
    #
    # 一档：倒挂（high < low）。价格区间是空的，而分型四条件（chan/fractal.py
    # 的 `b.high > a.high and b.high > c.high and b.low > a.low and b.low > c.low`
    # 及对偶）与包含关系判定（chan/include.py 的 `_contains`）**全部**建立在
    # high/low 的区间比较上。倒挂行不只是"脏一行"：它要么被原样 append 成一根
    # 倒挂的合并K线，要么与左邻构成包含关系后被 `max/max`（向上）或 `min/min`
    # （向下）吸收 —— 两个方向都可能产出 high < low 的合并K线。所以**剔除**，
    # 与非正价格同一处置。判据是严格小于，一字板（high == low）不受影响。
    #
    # 二档：越界（open 或 close 出了 [low, high]）。**只警告、保留**。open 不参与
    # 任何判据；close 不参与分型/笔/线段/中枢的极值比较，但会进 MACD
    # （chan/signal.py 与 chan/divergence.py 都按 `bars["close"]` 现算）⇒ 影响背驰。
    # 可剔除它会少一根 K 线，包含处理与分型跟着重排 —— 那个代价大于"一个字段偏
    # 一点"，所以在拿到实测证据之前先只观测。实测（2026-10-06，全库 5613 个
    # parquet / 9,119,671 行）倒挂 2 行、越界 62 行，见
    # docs/evidence/2026-10-06-行情结构不可能的行.md。
    upside_down = out["high"] < out["low"]
    if bool(upside_down.any()):
        logger.warning(
            "normalize: 剔除 %d 根倒挂 K 线（high < low，前几根 ts=%s）",
            int(upside_down.sum()),
            ", ".join(out.loc[upside_down, "ts"].head(5).astype(str)),
        )
        out = out[~upside_down]

    out_of_range = (out["open"] > out["high"]) | (out["open"] < out["low"]) | (
        out["close"] > out["high"]
    ) | (out["close"] < out["low"])
    if bool(out_of_range.any()):
        logger.warning(
            "normalize: 保留 %d 根字段越界 K 线（open/close 出了 [low, high]，"
            "前几根 ts=%s）—— 仅观测，不剔除",
            int(out_of_range.sum()),
            ", ".join(out.loc[out_of_range, "ts"].head(5).astype(str)),
        )

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
