"""Parquet 存储层：`data/{period}/{market}/{code}.parquet`。

约定：
- 一个品种一个周期一个文件，文件名不含市场前缀，市场由父目录表达。
- `upsert` 按 `ts` 去重合并（新数据覆盖旧数据），返回**新增**行数。
- 检测到除权事件时必须调用方自行全量重拉后 `write`（前复权全历史会重算）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ..config import load_config
from .types import COLUMNS, normalize, truncate

log = logging.getLogger(__name__)

# 测试可 monkeypatch；为 None 时按配置解析
DATA_ROOT: Path | None = None


def data_root() -> Path:
    if DATA_ROOT is not None:
        return DATA_ROOT
    root = load_config().data.root
    root.mkdir(parents=True, exist_ok=True)
    return root


def market_of(code: str) -> str:
    c = str(code).strip().lower()
    if "." in c:
        return c.split(".", 1)[0]
    head = c[0]
    if head in ("6", "5", "9"):
        return "sh"
    if head in ("4", "8"):
        return "bj"
    return "sz"


def path_for(code: str, period: str) -> Path:
    return data_root() / str(period) / market_of(code) / f"{code}.parquet"


def exists(code: str, period: str) -> bool:
    return path_for(code, period).exists()


def write(code: str, period: str, df: pd.DataFrame) -> Path:
    path = path_for(code, period)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = normalize(df)
    clean.to_parquet(path, index=False, engine="pyarrow")
    log.debug("写入 %s (%d 行)", path, len(clean))
    return path


def read(
    code: str,
    period: str,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    path = path_for(code, period)
    if not path.exists():
        return normalize(None)
    df = pd.read_parquet(path, engine="pyarrow")
    df = normalize(df)
    return truncate(df, start=start, end=end, limit=limit)


def upsert(code: str, period: str, df: pd.DataFrame) -> int:
    """按 ts 合并，返回新增行数（不改变已有行的值，除非同 ts 覆盖）。"""
    new = normalize(df)
    if len(new) == 0:
        return 0

    old = read(code, period)
    if len(old) == 0:
        write(code, period, new)
        return len(new)

    old_ts = set(old["ts"])
    merged = normalize(pd.concat([old, new], ignore_index=True))
    write(code, period, merged)
    return int(sum(1 for ts in merged["ts"] if ts not in old_ts))


def last_ts(code: str, period: str) -> str | None:
    df = read(code, period, limit=1)
    return None if len(df) == 0 else str(df["ts"].iloc[-1])


def row_count(code: str, period: str) -> int:
    return len(read(code, period))
