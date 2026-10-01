"""SQLite 元数据库：品种表、同步状态、数据区间、质量标记、结构快照、扫描结果、自选池。"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

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
    note     TEXT,
    sort_order INTEGER
);

CREATE TABLE IF NOT EXISTS symbol_alias (
    old_code   TEXT PRIMARY KEY,
    new_code   TEXT NOT NULL,
    market     TEXT,
    evidence   TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS adjust_factor (
    code   TEXT NOT NULL,
    ts     TEXT NOT NULL,
    k      REAL NOT NULL,
    source TEXT,
    PRIMARY KEY (code, ts)
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
    _ensure_watch_order(conn)
    conn.commit()
    return conn


def _ensure_watch_order(conn: sqlite3.Connection) -> None:
    """给老库的自选表补 `sort_order` 列，并按**原来的显示顺序**（added_at）回填。

    不补列的话「上移/下移」在页面刷新后就会失效（顺序还是按 added_at 排）；
    回填必须按 added_at 而不是 rowid —— 两者通常一致，但一致不是契约。
    """
    cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(watchlist)")}
    if not cols or "sort_order" in cols:
        return
    conn.execute("ALTER TABLE watchlist ADD COLUMN sort_order INTEGER")
    rows = list(conn.execute("SELECT code FROM watchlist ORDER BY added_at, rowid"))
    for i, row in enumerate(rows):
        conn.execute("UPDATE watchlist SET sort_order=? WHERE code=?", (i, str(row["code"])))


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


def name_of(conn: sqlite3.Connection, code: str) -> str:
    """这只票叫什么。查不到就返回空串（页面自己决定要不要显示）。

    三处来源，优先级从高到低：品种表（股票的正式名）→ 自选池（用户加自选时存的名字，
    改名后应当以这里的为准）→ 内置指数名。指数不在 `universe` 里，没有第三处，
    `sh.000001` 在页面上就只剩一串数字。
    """
    for table in ("universe", "watchlist"):
        row = conn.execute(f"SELECT name FROM {table} WHERE code=?", (code,)).fetchone()
        if row is not None and row["name"]:
            return str(row["name"])
    return dict(DEFAULT_WATCHLIST).get(str(code), "")


def add_watch(conn: sqlite3.Connection, code: str, name: str = "", note: str = "") -> None:
    """加自选。新票落在**末尾**；已在池里的票只更新名称/备注，位置不动。

    没给名字就去 `name_of` 兜底：页面只输一个代码时，左侧栏不至于只剩一串数字。
    """
    conn.execute(
        """INSERT INTO watchlist (code, name, added_at, note, sort_order)
           VALUES (?, ?, ?, ?, COALESCE((SELECT MAX(sort_order) FROM watchlist), -1) + 1)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, note=excluded.note""",
        (code, str(name) or name_of(conn, code), now(), note),
    )
    conn.commit()


def remove_watch(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
    conn.commit()


def get_watchlist(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """按用户排好的顺序返回（`sort_order` 为空的老行排在最后，再看加入时间）。"""
    return list(conn.execute(
        "SELECT * FROM watchlist ORDER BY sort_order IS NULL, sort_order, added_at"
    ))


def move_watch(conn: sqlite3.Connection, code: str, delta: int) -> list[str]:
    """上移（-1）/下移（+1）一只票，返回移动后的完整顺序。

    换位而不是「改一个数字」：移完统一重排成 0..n-1，顺序永远稠密，也不会因为
    历史 NULL 而出现两行同号。已经在头/尾的移动是空操作（不报错）。
    """
    if delta not in (-1, 1):
        raise ValueError(f"delta 只能是 -1（上移）或 +1（下移），收到 {delta!r}")
    codes = [str(r["code"]) for r in get_watchlist(conn)]
    if code not in codes:
        return codes
    i = codes.index(code)
    j = i + delta
    if 0 <= j < len(codes):
        codes[i], codes[j] = codes[j], codes[i]
    for pos, c in enumerate(codes):
        conn.execute("UPDATE watchlist SET sort_order=? WHERE code=?", (pos, c))
    conn.commit()
    return codes


# 默认自选池 = 主要大盘指数（用户 2026-10-01 选定）。
# 指数名不在 `universe` 表里（那张表只有股票），所以名字必须和代码一起写死在这里。
# 代码用**落库键**（`markets.store_key` 的结果）：沪市指数必须带前缀（裸 `000001`
# 是平安银行），深市 `399xxx` 反而要裸写，否则和页面加进来的键不是同一个字符串。
DEFAULT_WATCHLIST: tuple[tuple[str, str], ...] = (
    ("sh.000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("sh.000300", "沪深300"),
    ("sh.000016", "上证50"),
    ("sh.000905", "中证500"),
    ("sh.000688", "科创50"),
)


def seed_watchlist(
    conn: sqlite3.Connection,
    rows: Sequence[tuple[str, str]] | None = None,
    *,
    replace: bool = False,
) -> list[str]:
    """写入默认自选池。`replace=True` 清空后重建（`--reset` 用），否则只补缺的，
    已经在池里的票**保持用户排好的位置**。返回写入后的顺序。"""
    pairs = tuple(rows) if rows is not None else DEFAULT_WATCHLIST
    if replace:
        conn.execute("DELETE FROM watchlist")
        conn.commit()
    for code, name in pairs:
        add_watch(conn, str(code), str(name))
    return [str(r["code"]) for r in get_watchlist(conn)]


# ---------------- 代码改号（北交所 43/83/87 → 920 段） ----------------
def save_alias(
    conn: sqlite3.Connection, old_code: str, new_code: str, market: str = "", evidence: str = ""
) -> None:
    conn.execute(
        """INSERT INTO symbol_alias (old_code, new_code, market, evidence, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(old_code) DO UPDATE SET
             new_code=excluded.new_code, market=excluded.market,
             evidence=excluded.evidence, created_at=excluded.created_at""",
        (old_code, new_code, market, evidence, now()),
    )
    conn.commit()


def get_alias(conn: sqlite3.Connection, old_code: str) -> str | None:
    row = conn.execute(
        "SELECT new_code FROM symbol_alias WHERE old_code=?", (old_code,)
    ).fetchone()
    return row["new_code"] if row else None


def all_aliases(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["old_code"]: r["new_code"] for r in conn.execute("SELECT * FROM symbol_alias")}


# ---------------- 除权因子阶梯 ----------------
def save_adjust_factors(
    conn: sqlite3.Connection,
    code: str,
    rows: Iterable[Sequence[Any]],
    source: str = "baostock",
) -> int:
    """写除权因子阶梯（`(ts, k)` 逐段起始日）。

    幂等：同一 `(code, ts)` 重复写入是更新而不是追加 —— 行情同步会反复跑，
    重复导入若追加出重复段，因子阶梯就会在段边界上抖动。
    """
    data = [(code, str(ts), float(k), source) for ts, k in rows]
    if not data:
        return 0
    conn.executemany(
        """INSERT INTO adjust_factor (code, ts, k, source) VALUES (?, ?, ?, ?)
           ON CONFLICT(code, ts) DO UPDATE SET k=excluded.k, source=excluded.source""",
        data,
    )
    conn.commit()
    return len(data)


def get_adjust_factors(conn: sqlite3.Connection, code: str) -> pd.DataFrame:
    """读某只票的因子阶梯，按日期升序；没有记录时返回**空表**（不是 None）。"""
    cur = conn.execute("SELECT ts, k FROM adjust_factor WHERE code=? ORDER BY ts", (code,))
    return pd.DataFrame(cur.fetchall(), columns=["ts", "k"])


def all_adjust_codes(conn: sqlite3.Connection) -> list[str]:
    return [r["code"] for r in conn.execute("SELECT DISTINCT code FROM adjust_factor ORDER BY code")]
