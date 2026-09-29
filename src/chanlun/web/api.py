"""Web API：行情、缠论结构、扫描与自选池。

设计约定
--------
1. **品种代码在接口边界就归一化并校验**。Store 落盘用的是**裸数字**代码
   （`data/day/sh/600000.parquet`），而 baostock 用 `sh.600000`；两边混用会把
   文件找错地方。更要紧的是交易所前缀：`sh.300059` 在 baostock 那里不报错、
   只返回零行，若放过去，页面会安静地显示「这只票没有结构」。所以这里用
   `to_bs_code()` 校验，前缀不符直接 400。
2. **没有数据就是 404，不返回空数组**。同一类错误（代码写错、周期没同步、
   真的没上市）在页面上必须给出不同的话，否则用户没法自救。
3. **结构对象一律带 `status` 与 `confirmed_at`**。页面要把未确认的笔画虚线、
   回测要按 `confirmed_at` 判可见性，这两个字段不许在序列化时丢掉。
4. `Snapshot` 的字段全是 frozen dataclass / tuple，直接 `to_jsonable()` 即可，
   不需要手写映射——手写映射最容易在加字段时漏掉。
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ..chan.engine import ChanEngine, Snapshot
from ..chan.macd import macd as compute_macd
from ..chan.signal import find_signals
from ..chan.types import Status, to_jsonable
from ..data import meta, store
from ..data.baostock_source import strip_bs_code, to_bs_code
from ..data.types import DataSourceError

router = APIRouter()

#: 页面固定免责声明（原文也用于接口返回值，前端与推送共用一句话）。
DISCLAIMER = "仅结构信号提示，不构成投资建议；结构为收盘后确认，非盘中实时。"

PERIODS = ("day", "60", "30", "15", "5")
_CODE_RE = re.compile(r"^(?:(sh|sz|bj)\.?)?(\d{6})$")

#: 默认窗口。窗口不是自相似的：**结构随窗口起点变化**，500 根时实测 4 只样本
#: 一段中枢都凑不出（中枢至少要 3 段确认线段重叠），1200 根才各 2 个。
#: 所有"给结构用"的接口共用这一个值，避免页面（1200）和自选池卡片（曾用 300）
#: 描述两个不同窗口、看起来像自相矛盾。
DEFAULT_LIMIT = 1200

#: 结构快照缓存：同一只票同一段行情只算一次。键为 (code, period, limit, last_ts)。
_CACHE: dict[tuple, Snapshot] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 64


# ---------------- 工具 ----------------
def normalize_code(raw: str) -> str:
    """把各种写法归一成裸数字代码；前缀与号段不符时抛 400。"""
    text = str(raw or "").strip().lower().replace(" ", "")
    m = _CODE_RE.match(text)
    if not m:
        raise HTTPException(status_code=400, detail=f"无法识别的代码: {raw!r}（应为 6 位数字，可带 sh./sz./bj. 前缀）")
    market, digits = m.group(1), m.group(2)
    try:
        bs = to_bs_code(f"{market}.{digits}" if market else digits)
    except ValueError as exc:  # 号段与前缀矛盾（例如 sh.300059）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return strip_bs_code(bs)


def _cfg(request: Request):
    return request.app.state.cfg


def _read_bars(code: str, period: str, limit: int) -> pd.DataFrame:
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"不支持的周期: {period!r}（可选 {'/'.join(PERIODS)}）")
    if not store.exists(code, period):
        raise HTTPException(
            status_code=404,
            detail=(
                f"{code} 的 {period} 周期没有本地数据。"
                f"请先同步：python -m chanlun sync --period {period} --codes {code}"
            ),
        )
    df = store.read(code, period, limit=limit)
    if len(df) == 0:
        raise HTTPException(status_code=404, detail=f"{code} 的 {period} 周期数据为空文件")
    return df


def _bars_payload(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "ts": str(r.ts),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
            "amount": float(r.amount),
        }
        for r in df.itertuples()
    ]


def _macd_payload(bars: pd.DataFrame) -> dict[str, list[float]]:
    """MACD 由**服务端**算：页面必须和引擎看到同一条 DIF/DEA/柱子。

    前端自己再算一遍 EMA 看着更省事，但两边一旦口径分叉（种子、hist 是否乘 2），
    页面上就会拿一条和买卖点无关的 MACD 去解释背驰 —— 那是自欺。
    """
    df = compute_macd(bars["close"].to_numpy(dtype=float))
    return {c: [round(float(v), 4) for v in df[c]] for c in ("dif", "dea", "hist")}


def snapshot_of(code: str, period: str, limit: int) -> tuple[Snapshot, pd.DataFrame]:
    """算（或取缓存）某只票的结构快照。缓存键含最后时间戳，数据一更新就自动失效。

    取数一律走 `_read_bars`：缺数据的 404 与周期校验必须只有一处实现，
    否则某条路径会绕过检查、拿着空 DataFrame 往下跑到 500。
    """
    df = _read_bars(code, period, limit)
    last_ts = str(df["ts"].iloc[-1])
    key = (code, period, limit, last_ts)
    with _CACHE_LOCK:
        snap = _CACHE.get(key)
    if snap is None:
        snap = ChanEngine(code, period, signal_fn=find_signals, level=period).full(df)
        with _CACHE_LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = snap
    return snap, df


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _counts(snap: Snapshot) -> dict[str, int]:
    return {
        "strokes": len(snap.strokes),
        "segments": len(snap.segments),
        "pivots": len(snap.pivots),
        "signals": len(snap.signals),
        "confirmed_segments": sum(1 for s in snap.segments if s.status is Status.CONFIRMED),
        "tentative_segments": sum(1 for s in snap.segments if s.status is Status.TENTATIVE),
    }


def structure_payload(snap: Snapshot, bars: pd.DataFrame, *,
                      include_merged: bool = False) -> dict[str, Any]:
    """结构 + 行情一起返回：一次请求就能画图，且两边必然对齐。

    `merged`（包含处理后的 K 线）默认不传 —— 它是内部中间量，条数与 bars 同量级，
    传了只会让页面变慢；需要核对包含处理时用 `?merged=true` 单独取。
    """
    body = to_jsonable(snap)
    if not include_merged:
        body.pop("merged", None)
    body["bars"] = _bars_payload(bars)
    body["macd"] = _macd_payload(bars)
    body["counts"] = _counts(snap)
    body["disclaimer"] = DISCLAIMER
    return body


# ---------------- 接口 ----------------
@router.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    return {
        "ok": True,
        "port": getattr(request.app.state, "port", cfg.web.port),
        "data_root": str(cfg.data.root),
        "periods": list(cfg.periods),
        "disclaimer": DISCLAIMER,
    }


@router.get("/api/universe")
def universe(request: Request, limit: int = Query(200, ge=1, le=10000), q: str = "") -> dict[str, Any]:
    cfg = _cfg(request)
    conn = meta.init(cfg.data.meta_db)
    try:
        rows = meta.get_universe(conn)
    finally:
        conn.close()
    items = [dict(r) for r in rows]
    if q:
        needle = q.strip().lower()
        items = [i for i in items if needle in str(i["code"]) or needle in str(i["name"]).lower()]
    return {"count": len(items), "items": items[:limit]}


@router.get("/api/bars")
def bars(request: Request, code: str, period: str = "day", limit: int = Query(DEFAULT_LIMIT, ge=10, le=20000)) -> dict[str, Any]:
    _cfg(request)
    bare = normalize_code(code)
    df = _read_bars(bare, period, limit)
    return {"code": bare, "period": period, "count": len(df), "bars": _bars_payload(df)}


@router.get("/api/structure")
def structure(request: Request, code: str, period: str = "day",
              limit: int = Query(DEFAULT_LIMIT, ge=10, le=20000), merged: bool = False) -> dict[str, Any]:
    cfg = _cfg(request)
    bare = normalize_code(code)
    snap, df = snapshot_of(bare, period, limit)
    return structure_payload(snap, df, include_merged=merged)


@router.get("/api/scan")
def scan(request: Request, period: str = "day", date: str | None = None) -> dict[str, Any]:
    cfg = _cfg(request)
    run_date = date or dt.date.today().isoformat()
    conn = meta.init(cfg.data.meta_db)
    try:
        hits = meta.get_scan_results(conn, run_date)
    finally:
        conn.close()
    return {"run_date": run_date, "period": period, "count": len(hits), "hits": [dict(h) for h in hits]}


# ---------------- 自选池 ----------------
@router.get("/api/watchlist")
def watchlist(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    conn = meta.init(cfg.data.meta_db)
    try:
        rows = meta.get_watchlist(conn)
    finally:
        conn.close()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


@router.post("/api/watchlist")
async def watchlist_add(request: Request) -> dict[str, Any]:
    cfg = _cfg(request)
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 - 空 body / 坏 JSON 都按参数错误处理
        raise HTTPException(status_code=400, detail=f"请求体不是合法 JSON: {exc}") from exc
    code = normalize_code(str(body.get("code", "")))  # 自选池可先记代码后同步，但格式必须合法
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.add_watch(conn, code, str(body.get("name", "")), str(body.get("note", "")))
    finally:
        conn.close()
    return {"ok": True, "code": code}


@router.delete("/api/watchlist")
def watchlist_remove(request: Request, code: str) -> dict[str, Any]:
    cfg = _cfg(request)
    bare = normalize_code(code)
    conn = meta.init(cfg.data.meta_db)
    try:
        meta.remove_watch(conn, bare)
    finally:
        conn.close()
    return {"ok": True, "code": bare}


@router.get("/api/watchlist/structure")
def watchlist_structure(request: Request, period: str = "day", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """自选池结构摘要：一次请求给全，字段足以做「结构跟踪表」。"""
    cfg = _cfg(request)
    conn = meta.init(cfg.data.meta_db)
    try:
        rows = [dict(r) for r in meta.get_watchlist(conn)]
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        code = str(row["code"])
        base = {"code": code, "name": row.get("name") or "", "period": period,
                "note": row.get("note") or ""}
        if not store.exists(code, period):
            items.append({**base, "missing": True, "error": f"{period} 周期未同步"})
            continue
        try:
            snap, df = snapshot_of(code, period, limit)
        except (DataSourceError, HTTPException, ValueError) as exc:
            items.append({**base, "missing": True, "error": str(exc)})
            continue
        last = snap.segments[-1] if snap.segments else None
        items.append({
            **base,
            "missing": False,
            "as_of": snap.as_of,
            "counts": _counts(snap),
            "pivots": [
                {"zg": p.zg, "zd": p.zd, "level": p.level, "status": p.status.value,
                 "start_ts": p.start_ts, "end_ts": p.end_ts, "confirmed_at": p.confirmed_at}
                for p in snap.pivots
            ],
            "last_segment": None if last is None else {
                "direction": last.direction, "status": last.status.value,
                "low": last.low, "high": last.high, "confirmed_at": last.confirmed_at,
                "stroke_count": last.stroke_count,
            },
            "signals": [
                {"kind": s.kind.value, "kind_cn": s.kind.name_cn, "ts": s.ts, "price": s.price,
                 "status": s.status.value, "confirmed_at": s.confirmed_at}
                for s in snap.signals
            ],
        })
    return {"period": period, "count": len(items), "items": items}
