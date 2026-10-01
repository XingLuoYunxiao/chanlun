"""自选池跟踪：说清楚「相对上一次快照变了什么」（Task 15）。

为什么用「快照 + 差分」而不是每次重算后打印全量结构
--------------------------------------------------
自选池每天要看的不是「现在有几个中枢」，而是「**比昨天多了/少了一个中枢、
有没有突破、买卖点是不是今天才出现的**」。所以本模块：

1. 把当次结构压成一个**可 JSON 化、可落库**的指纹（中枢/买卖点/背驰/突破状态）；
2. 与 `meta.structure_snapshot` 里的上一份指纹逐项比较，产出 `TrackChange` 列表；
3. 没有变化时也**显式**给一条 `NO_CHANGE`——「今天没动静」本身就是有用的结论，
   静默不输出会让人怀疑是不是脚本挂了。

中枢用 `(src_start, src_end, zd, zg)` 做稳定 ID：原始 bar 区间跨快照不变，
而中枢在列表里的下标会因为新增线段而移动，不能用下标当 ID。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from ..chan.engine import Snapshot
from ..chan.macd import macd
from ..chan.types import Status
from ..data import meta, store
from .scanner import (
    check_sufficiency,
    default_snapshot_source,
    segment_divergence,
    signal_label,
)

#: 指纹格式版本。结构变化时 +1，避免拿新格式去 diff 旧数据（宁可当首次快照）。
SNAPSHOT_VERSION = 1


class TrackChangeKind(str, Enum):
    """一次跟踪里可能出现的变化类型。"""

    FIRST_SNAPSHOT = "first_snapshot"    # 没有上一份快照可比
    NEW_PIVOT = "new_pivot"              # 新中枢出现
    PIVOT_BREAKOUT = "pivot_breakout"    # 价格相对最新中枢的突破状态改变
    SIGNAL = "signal"                    # 新出现买卖点
    DIVERGENCE = "divergence"            # 新出现背驰
    NO_CHANGE = "no_change"              # 明确报告「什么都没变」


@dataclass(frozen=True)
class TrackChange:
    """一条变化：类型 + 说明 + 可核对的数字。"""

    kind: TrackChangeKind
    level: str = ""
    ts: str = ""
    detail: str = ""
    strength: float = 0.0


@dataclass(frozen=True)
class TrackRow:
    """一只票一个周期的跟踪结果（含失败/数据不足两种非正常状态）。"""

    code: str
    name: str = ""
    period: str = ""
    as_of: str = ""
    prev_as_of: str | None = None
    changes: tuple[TrackChange, ...] = ()
    status: str = "ok"          # ok / insufficient / error
    error: str = ""

    @property
    def has_change(self) -> bool:
        return any(c.kind is not TrackChangeKind.NO_CHANGE for c in self.changes)

    def summary(self) -> str:
        if self.status != "ok":
            return f"{self.code} {self.name} [{self.period}] {self.status}：{self.error}"
        detail = "；".join(c.detail for c in self.changes) or "无变化"
        return f"{self.code} {self.name} [{self.period}] {detail}"


# ------------------------------------------------------------------ 指纹

def _status_value(status: Any) -> str:
    if isinstance(status, Status):
        return status.value
    if status is None:
        return "tentative"
    return str(getattr(status, "value", status)).lower()


def _pivot_key(d: dict) -> tuple[int, int, float, float]:
    return (int(d["src_start"]), int(d["src_end"]), round(float(d["zd"]), 2), round(float(d["zg"]), 2))


def _last_segment(snap: Snapshot) -> Any | None:
    segs = [s for s in snap.segments if s.status is not Status.INVALIDATED]
    if not segs:
        return None
    return max(segs, key=lambda s: (int(s.src_end), int(s.src_start)))


def build_payload(snap: Snapshot, bars: pd.DataFrame, *,
                  macd_df: pd.DataFrame | None = None, period: str | None = None) -> dict:
    """把当前结构压成指纹（纯数据，可直接 JSON 落库）。"""
    period = period or snap.period
    close = float(bars["close"].iloc[-1]) if len(bars) else 0.0
    as_of = str(bars["ts"].iloc[-1]) if len(bars) else ""

    pivots: list[dict] = []
    for p in snap.pivots:
        if _status_value(p.status) == Status.INVALIDATED.value:
            continue    # 失效中枢不参与跟踪：它已经不代表当前结构
        pivots.append({
            "src_start": int(p.src_start), "src_end": int(p.src_end),
            "zd": round(float(p.zd), 2), "zg": round(float(p.zg), 2),
            "level": str(p.level), "status": _status_value(p.status),
        })
    pivots.sort(key=lambda d: (d["src_start"], d["src_end"]))

    signals = [{
        "ts": str(s.ts), "kind": str(getattr(s.kind, "value", s.kind)),
        "label": signal_label(getattr(s.kind, "value", s.kind)),
        "price": round(float(s.price), 4), "pivot_idx": getattr(s, "pivot_idx", None),
        "status": _status_value(getattr(s, "status", None)),
    } for s in snap.signals]

    latest = max(pivots, key=lambda d: (d["src_end"], d["src_start"])) if pivots else None
    if latest is None:
        state = "none"
    elif close > latest["zg"]:
        state = "up"
    elif close < latest["zd"]:
        state = "down"
    else:
        state = "inside"

    divergence = None
    seg = _last_segment(snap)
    if seg is not None:
        strength, text = segment_divergence(snap, seg, macd_df)
        if strength > 0:
            divergence = {"ts": str(seg.end.end.ts), "direction": int(seg.direction),
                          "strength": round(float(strength), 4), "text": text}

    return {
        "version": SNAPSHOT_VERSION,
        "period": period,
        "as_of": as_of,
        "close": round(close, 4),
        "pivots": pivots,
        "signals": signals,
        "breakout": {
            "state": state,
            "close": round(close, 4),
            "pivot_range": ([latest["zd"], latest["zg"]] if latest else []),
            "src": ([latest["src_start"], latest["src_end"]] if latest else []),
        },
        "divergence": divergence,
    }


STATE_CN: dict[str, str] = {
    "up": "上破", "down": "下破", "inside": "回到中枢内", "none": "暂无中枢",
}


def diff_payloads(prev: dict | None, cur: dict, *, period: str) -> tuple[TrackChange, ...]:
    """对比两份指纹，产出**中文可核对**的变化列表。

    `prev is None`（或版本不认识）→ 只报「首次快照」，不假装知道变化。
    """
    as_of = str(cur.get("as_of", ""))
    if prev is None or int(prev.get("version", 0)) != SNAPSHOT_VERSION:
        return (TrackChange(
            TrackChangeKind.FIRST_SNAPSHOT, level=period, ts=as_of,
            detail=(f"首次快照（无上一份可比）：中枢 {len(cur['pivots'])} 个、"
                    f"买卖点 {len(cur['signals'])} 个、收盘 {float(cur['close']):.2f}、"
                    f"中枢状态 {STATE_CN.get(cur['breakout']['state'], cur['breakout']['state'])}"),
        ),)

    changes: list[TrackChange] = []
    prev_pivots = {_pivot_key(p) for p in prev.get("pivots", [])}
    for p in cur.get("pivots", []):
        if _pivot_key(p) not in prev_pivots:
            changes.append(TrackChange(
                TrackChangeKind.NEW_PIVOT, level=period, ts=as_of,
                detail=(f"新中枢 [{float(p['zd']):.2f}, {float(p['zg']):.2f}]"
                        f"（bar {p['src_start']}-{p['src_end']}，{p['status']}）"),
            ))

    prev_brk = prev.get("breakout") or {}
    cur_brk = cur.get("breakout") or {}
    if prev_brk.get("state") != cur_brk.get("state"):
        rng = cur_brk.get("pivot_range") or []
        rng_text = f"[{float(rng[0]):.2f}, {float(rng[1]):.2f}]" if len(rng) == 2 else "无中枢"
        changes.append(TrackChange(
            TrackChangeKind.PIVOT_BREAKOUT, level=period, ts=as_of,
            detail=(f"{STATE_CN.get(cur_brk.get('state'), cur_brk.get('state'))} {rng_text}"
                    f"（收盘 {float(cur_brk.get('close', 0.0)):.2f}；"
                    f"上次 {STATE_CN.get(prev_brk.get('state'), prev_brk.get('state'))}）"),
        ))

    prev_sigs = {(str(s["ts"]), str(s["kind"])) for s in prev.get("signals", [])}
    for s in cur.get("signals", []):
        if (str(s["ts"]), str(s["kind"])) not in prev_sigs:
            changes.append(TrackChange(
                TrackChangeKind.SIGNAL, level=period, ts=str(s["ts"]),
                detail=(f"新出现{s.get('label') or signal_label(s['kind'])} @ {s['ts']}"
                        f" 价 {float(s['price']):.2f}（{s['status']}）"),
            ))

    prev_div = prev.get("divergence")
    cur_div = cur.get("divergence")
    if cur_div and (prev_div is None or
                    (prev_div.get("ts"), prev_div.get("direction")) !=
                    (cur_div.get("ts"), cur_div.get("direction"))):
        changes.append(TrackChange(
            TrackChangeKind.DIVERGENCE, level=period, ts=str(cur_div["ts"]),
            detail=f"新出现背驰：{cur_div['text']}", strength=float(cur_div["strength"]),
        ))

    if not changes:
        changes.append(TrackChange(
            TrackChangeKind.NO_CHANGE, level=period, ts=as_of,
            detail=(f"无变化（相对 {prev.get('as_of', '?')}：中枢 {len(cur['pivots'])} 个、"
                    f"买卖点 {len(cur['signals'])} 个、收盘 {float(cur['close']):.2f}、"
                    f"中枢状态 {STATE_CN.get(cur_brk.get('state'), cur_brk.get('state'))}）"),
        ))
    return tuple(changes)


# ------------------------------------------------------------------ 主入口

def track_watchlist(
    codes: Iterable[str] | None = None,
    *,
    periods: Sequence[str] = ("day",),
    conn: sqlite3.Connection | None = None,
    snapshot_source: Callable[..., Snapshot] | None = None,
    macd_fn: Callable[[Any], pd.DataFrame] | None = None,
    save: bool = True,
) -> list[TrackRow]:
    """跟踪自选池（`codes=None` 时读 `meta.watchlist`），逐只给出相对上次快照的变化。

    串行执行：自选池通常只有几十只，串行的开销远小于多进程的复杂度；
    单只失败/数据不足只影响它自己那一行，不影响其它票。
    """
    own_conn = conn is None
    conn = conn or meta.init()
    try:
        if codes is None:
            watch_rows = meta.get_watchlist(conn)
            code_list = [str(r["code"]) for r in watch_rows]
            names = {str(r["code"]): (r["name"] or "") for r in watch_rows}
        else:
            code_list = [str(c).strip() for c in codes if str(c).strip()]
            names = {str(r["code"]): (r["name"] or "") for r in meta.get_universe(conn)}
        synced = {(str(r["code"]), str(r["period"])): r for r in meta.all_sync(conn)}

        rows: list[TrackRow] = []
        for code in code_list:
            for period in (str(p) for p in periods):
                rows.append(_track_one(conn, code, names.get(code, ""), period, synced,
                                       snapshot_source, macd_fn, save))
        return rows
    finally:
        if own_conn:
            conn.close()


def _track_one(conn: sqlite3.Connection, code: str, name: str, period: str,
               synced: dict, snapshot_source: Callable[..., Snapshot] | None,
               macd_fn: Callable[[Any], pd.DataFrame] | None, save: bool) -> TrackRow:
    """单只单周期跟踪。**异常一律转成行内状态**，不向上抛。"""
    def fail(status: str, message: str) -> TrackRow:
        return TrackRow(code=code, name=name, period=period, status=status, error=message)

    try:
        bars = store.read(code, period)
    except Exception as exc:
        return fail("error", f"跟踪失败：读取本地数据出错（{type(exc).__name__}: {exc}）")

    row_sync = synced.get((code, period))
    suf = check_sufficiency(bars, period, row_sync["start_ts"] if row_sync else None)
    if not suf.ok:
        return fail("insufficient", suf.reason)

    try:
        snap = (snapshot_source or default_snapshot_source)(code, period, bars)
    except Exception as exc:
        return fail("error", f"跟踪失败：结构计算出错（{type(exc).__name__}: {exc}）")
    try:
        macd_df = (macd_fn or macd)(bars["close"])
    except Exception:
        # MACD 算不出来不影响「中枢/买卖点」的结构跟踪，只是背驰一项缺席
        # （build_payload 会把说明写成「未提供 MACD」），不能因此整只票失败。
        macd_df = None

    payload = build_payload(snap, bars, macd_df=macd_df, period=period)
    prev = meta.get_structure_snapshot(conn, code, period)
    changes = diff_payloads(prev, payload, period=period)

    if save:
        version = (int(prev.get("version", SNAPSHOT_VERSION)) if prev else SNAPSHOT_VERSION)
        meta.save_structure_snapshot(conn, code, period, payload["as_of"], payload,
                                     version=version)
    return TrackRow(
        code=code, name=name, period=period, as_of=payload["as_of"],
        prev_as_of=(str(prev.get("as_of")) if prev else None),
        changes=changes,
    )
