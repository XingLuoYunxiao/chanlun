"""SQLite 元数据库：品种表、同步状态、数据区间、质量标记、结构快照、扫描结果、自选池。"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..config import load_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (
    code       TEXT NOT NULL,
    period     TEXT NOT NULL,
    start_ts   TEXT,
    end_ts     TEXT,
    rows       INTEGER NOT NULL DEFAULT 0,
    adjust     TEXT,
    error      TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (code, period)
);

CREATE TABLE IF NOT EXISTS universe (
    code       TEXT PRIMARY KEY,
    bs_code    TEXT NOT NULL,
    name       TEXT,
    market     TEXT,
    list_date  TEXT,
    delisted   INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS dividend_event (
    code    TEXT NOT NULL,
    ex_date TEXT NOT NULL,
    PRIMARY KEY (code, ex_date)
);

CREATE TABLE IF NOT EXISTS quality_flag (
    code       TEXT NOT NULL,
    period     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    detail     TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (code, period, kind)
);

CREATE TABLE IF NOT EXISTS structure_snapshot (
    code       TEXT NOT NULL,
    period     TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    payload    TEXT NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (code, period)
);

CREATE TABLE IF NOT EXISTS scan_result (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date   TEXT NOT NULL,
    code       TEXT NOT NULL,
    period     TEXT NOT NULL,
    signal_kind TEXT,
    level      TEXT,
    strength   REAL,
    detail     TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    code     TEXT PRIMARY KEY,
    name     TEXT,
    added_at TEXT,
    note     TEXT
);
"""


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init(target: Path | str | sqlite3.Connection | None = None) -> sqlite3.Connection:
    """建库建表；返回连接（调用方负责关闭，长期进程可复用）。"""
    if isinstance(target, sqlite3.Connection):
        conn = target
    else:
        path = Path(target) if target is not None else load_config().data.meta_db
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------- 同步状态 ----------------
def set_sync(
    conn: sqlite3.Connection,
    code: str,
    period: str,
    start_ts: str | None,
    end_ts: str | None,
    rows: int,
    adjust: str,
    error: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO sync_state (code, period, start_ts, end_ts, rows, adjust, error, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(code, period) DO UPDATE SET
             start_ts=excluded.start_ts, end_ts=excluded.end_ts, rows=excluded.rows,
             adjust=excluded.adjust, error=excluded.error, updated_at=excluded.updated_at""",
        (code, period, start_ts, end_ts, rows, adjust, error, now()),
    )
    conn.commit()


def get_sync(conn: sqlite3.Connection, code: str, period: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sync_state WHERE code=? AND period=?", (code, period)
    ).fetchone()


def all_sync(conn: sqlite3.Connection, period: str | None = None) -> list[sqlite3.Row]:
    if period:
        return list(conn.execute(
            "SELECT * FROM sync_state WHERE period=? ORDER BY code", (period,)))
    return list(conn.execute("SELECT * FROM sync_state ORDER BY code, period"))


def sync_error_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM sync_state WHERE error IS NOT NULL").fetchone()
    return int(row["n"]) if row else 0


# ---------------- 品种表 ----------------
def upsert_universe(conn: sqlite3.Connection, rows: Iterable[Sequence[Any]]) -> int:
    """rows: (code, bs_code, name, market, list_date, delisted)"""
    payload = [(r[0], r[1], r[2], r[3], r[4], int(r[5]), now()) for r in rows]
    conn.executemany(
        """INSERT INTO universe (code, bs_code, name, market, list_date, delisted, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
             bs_code=excluded.bs_code, name=excluded.name, market=excluded.market,
             list_date=excluded.list_date, delisted=excluded.delisted,
             updated_at=excluded.updated_at""",
        payload,
    )
    conn.commit()
    return len(payload)


def get_universe(conn: sqlite3.Connection, include_delisted: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM universe"
    if not include_delisted:
        sql += " WHERE delisted = 0"
    return list(conn.execute(sql + " ORDER BY code"))


# ---------------- 除权事件 ----------------
def add_dividend_events(conn: sqlite3.Connection, code: str, ex_dates: Iterable[str]) -> int:
    rows = [(code, d) for d in ex_dates]
    conn.executemany(
        "INSERT OR IGNORE INTO dividend_event (code, ex_date) VALUES (?, ?)", rows)
    conn.commit()
    return len(rows)


def last_dividend_date(conn: sqlite3.Connection, code: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(ex_date) AS d FROM dividend_event WHERE code=?", (code,)).fetchone()
    return row["d"] if row and row["d"] else None


# ---------------- 质量标记 ----------------
def set_quality(
    conn: sqlite3.Connection, code: str, period: str, kind: str, ok: bool, detail: str
) -> None:
    conn.execute(
        """INSERT INTO quality_flag (code, period, kind, ok, detail, checked_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(code, period, kind) DO UPDATE SET
             ok=excluded.ok, detail=excluded.detail, checked_at=excluded.checked_at""",
        (code, period, kind, int(ok), detail, now()),
    )
    conn.commit()


def failing_quality(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM quality_flag WHERE ok = 0"))


# ---------------- 结构快照 ----------------
def save_structure_snapshot(
    conn: sqlite3.Connection, code: str, period: str, as_of: str,
    payload: dict, version: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO structure_snapshot (code, period, as_of, version, payload, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(code, period) DO UPDATE SET
             as_of=excluded.as_of, version=excluded.version,
             payload=excluded.payload, updated_at=excluded.updated_at""",
        (code, period, as_of, version, json.dumps(payload, ensure_ascii=False), now()),
    )
    conn.commit()


def get_structure_snapshot(
    conn: sqlite3.Connection, code: str, period: str
) -> dict | None:
    row = conn.execute(
        "SELECT payload FROM structure_snapshot WHERE code=? AND period=?", (code, period)
    ).fetchone()
    return json.loads(row["payload"]) if row else None


# ---------------- 扫描结果 / 自选池 ----------------
def save_scan_results(conn: sqlite3.Connection, run_date: str, hits: Iterable[Sequence[Any]]) -> int:
    """hits: (code, period, signal_kind, level, strength, detail)"""
    payload = [(run_date, h[0], h[1], h[2], h[3], h[4], h[5], now()) for h in hits]
    conn.executemany(
        """INSERT INTO scan_result
           (run_date, code, period, signal_kind, level, strength, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    conn.commit()
    return len(payload)


def get_scan_results(conn: sqlite3.Connection, run_date: str | None = None) -> list[sqlite3.Row]:
    if run_date:
        return list(conn.execute(
            "SELECT * FROM scan_result WHERE run_date=? ORDER BY strength DESC", (run_date,)))
    return list(conn.execute("SELECT * FROM scan_result ORDER BY id DESC LIMIT 500"))


def add_watch(conn: sqlite3.Connection, code: str, name: str = "", note: str = "") -> None:
    conn.execute(
        """INSERT INTO watchlist (code, name, added_at, note) VALUES (?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, note=excluded.note""",
        (code, name, now(), note),
    )
    conn.commit()


def remove_watch(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
    conn.commit()


def get_watchlist(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM watchlist ORDER BY added_at"))
