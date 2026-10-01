"""全市场扫描：把「数据 → 结构 → 买卖点」按强度排成一张可执行的清单（Task 15）。

三条设计约束（以及各自的理由）
------------------------------
1. **先判数据够不够，再算结构**。缠论的中枢是「至少 3 条线段重叠」、趋势背驰是
   「两个同向中枢 + 离开段」，历史太短时算出来的所谓「买卖点」只是噪声。
   所以先按 `meta.sync_state` + 本地 bar 的**实际**区间做充分性检查，
   不满足就跳过并写明理由——绝不「有多少算多少」。所需的 bar 根数与回看自然日
   都放在 `REQUIREMENTS` 里，逐条写明推导过程。
2. **单只失败不拖垮全局**。全市场 5000+ 只，坏 parquet / 停牌 / 退市必然出现；
   每只独立 `try/except`，失败进 `report.errors`（含原因），继续跑下一只。
3. **强度可解释、可复算**。`strength = 类型权重 × 级别权重 × 状态权重 × (1 + 背驰强度)`，
   每一项都在 `ScanHit.strength_parts` 里留痕，便于人工核对排序是否合理。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from ..chan.engine import ChanEngine, Snapshot
from ..chan.macd import hist_area, is_divergence, macd
from ..chan.signal import find_signals
from ..chan.types import Status
from ..data import meta, store

log = logging.getLogger(__name__)


# ================================================================= 数据充分性

@dataclass(frozen=True)
class Requirement:
    """某个周期「多少数据才算够」的量化要求与推导理由。"""

    period: str
    min_bars: int
    lookback_days: int
    reason: str


# 为什么是这些数字（推导过程写在这里，不留「凭经验」的魔法常量）：
#
# - MACD 预热：`macd()` 用 12/26/9 参数，DIF 需要 26 根才稳定，DEA 再要 9 根，
#   合计 ~35 根；这之前的柱子面积不能用来比较力度。
# - 一个中枢：至少 3 条**已确认**线段，每条线段至少 3 笔，一笔至少 5 根原始 bar
#   （分型间隔 MIN_GAP=4）→ 3×3×5 = 45 根才可能出第一个中枢。
# - 趋势背驰（第一类买卖点）：需要**两个**同向中枢 + 其间连接段 + 离开段，
#   经验上 ≈ 5 个中枢量级 ≈ 45×5 ≈ 225 根。取 250 根（≈ 一个自然年的交易日）
#   作为日线上限，既覆盖上述推导又留出安全边际。
# - 30 分钟：一个交易日 8 根 bar，250 根只相当于 31 个交易日，对 30 分钟级别
#   的「趋势」太短；取 480 根 = 60 个交易日（≈ 3 个月），对应的自然日回看
#   60×7/5 = 84 天。
# - 5 分钟：480 根 = 10 个交易日（≈ 2 周），只用于日内/短线确认，
#   自然日回看 10×7/5 = 14 天。
REQUIREMENTS: dict[str, Requirement] = {
    "day": Requirement(
        "day", 250, 365,
        "MACD 预热 35 根；一个中枢至少 3 段×3 笔×5 根 = 45 根；"
        "趋势背驰要两个同向中枢加离开段，约 5 倍中枢量 ≈ 225 根，取 250 根"
        "（≈ 一个自然年的交易日）；回看自然日按 250×7/5 ≈ 365 天。",
    ),
    "60": Requirement(
        "60", 480, 240,
        "MACD 预热 35 根；一个中枢 45 根；60 分钟一天 4 根 bar，"
        "480 根 = 120 个交易日 ≈ 半年，才够两个中枢的趋势背驰；"
        "回看自然日 120×7/5 ≈ 240 天。",
    ),
    "30": Requirement(
        "30", 480, 84,
        "MACD 预热 35 根；一个中枢 45 根；30 分钟一天 8 根 bar，"
        "480 根 = 60 个交易日 ≈ 3 个月，才够两个中枢的趋势背驰；"
        "回看自然日 60×7/5 ≈ 84 天。",
    ),
    "5": Requirement(
        "5", 480, 14,
        "MACD 预热 35 根；一个中枢 45 根；5 分钟一天 48 根 bar，"
        "480 根 = 10 个交易日 ≈ 2 周，只够日内级别的中枢与背驰；"
        "回看自然日 10×7/5 ≈ 14 天。",
    ),
}


@dataclass(frozen=True)
class Sufficiency:
    """数据充分性判定结果。`reason` 非空即代表「跳过」，且一定以「数据不足」开头。"""

    ok: bool
    reason: str = ""
    warning: str = ""
    bars: int = 0
    start_ts: str = ""
    required_start: str = ""


def _as_date(value: Any) -> dt.date | None:
    """把 `2024-01-01` / `2024-01-01 09:35:00` 解析成日期；解析不了返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def check_sufficiency(bars: pd.DataFrame, period: str, sync_start: str | None = None,
                      *, as_of: str | None = None) -> Sufficiency:
    """判断「这只票这个周期能不能算」。

    判据（全部满足才算够）：
    1. `meta.sync_state` 里有该周期记录（没有记录 = 从未同步，直接跳过）；
    2. 本地 bar 根数 >= `REQUIREMENTS[period].min_bars`；
    3. 有效起始（sync 起始与本地起始的**较晚**者）到最新 bar 的自然日跨度
       >= `lookback_days`。

    第 3 条就是为了挡住「sync_state 声称 1991 年起、本地只留了最近几个月」
    这种元数据与文件不一致的情况：宁可报「数据不足」，也不拿残缺历史算中枢。
    """
    req = REQUIREMENTS.get(str(period))
    if req is None:
        return Sufficiency(
            False,
            f"数据不足：周期 {period} 未配置最小数据量要求（不允许凭感觉猜一个阈值），已跳过；"
            f"如需支持请在 REQUIREMENTS 中写明推导理由",
        )
    if sync_start is None or str(sync_start).strip() == "":
        return Sufficiency(False, "数据不足：meta.sync_state 无该周期记录（尚未同步），已跳过")
    if bars is None or len(bars) == 0:
        return Sufficiency(False, "数据不足：本地无该周期 bar 数据（parquet 缺失），已跳过")

    bars_n = int(len(bars))
    file_start_ts = str(bars["ts"].iloc[0])
    last_ts = str(as_of or bars["ts"].iloc[-1])
    file_start, last = _as_date(file_start_ts), _as_date(last_ts)
    sync_d = _as_date(sync_start)

    warning = ""
    if sync_d is not None and file_start is not None and file_start > sync_d:
        warning = (f"meta.sync_state 起始 {sync_start} 与本地 bar 起始 {file_start_ts} 不一致"
                   f"（本地更短），按本地起始计算")
    eff_start = max([d for d in (sync_d, file_start) if d is not None], default=None)

    if bars_n < req.min_bars:
        return Sufficiency(
            False,
            f"数据不足：本地仅 {bars_n} 根 bar，{period} 级别至少需要 {req.min_bars} 根。"
            f"理由：{req.reason}",
            warning=warning, bars=bars_n, start_ts=file_start_ts,
        )

    if eff_start is None or last is None:
        return Sufficiency(
            True, "", warning=warning or "时间戳无法解析，已跳过区间校验（数据量达标）",
            bars=bars_n, start_ts=file_start_ts,
        )

    required_start = last - dt.timedelta(days=req.lookback_days)
    span_days = (last - eff_start).days
    if span_days < req.lookback_days:
        return Sufficiency(
            False,
            f"数据不足：起始 {eff_start.isoformat()} 距最新 bar {last.isoformat()} 仅 {span_days} 个自然日，"
            f"{period} 级别要求至少回看 {req.lookback_days} 个自然日。理由：{req.reason}",
            warning=warning, bars=bars_n, start_ts=eff_start.isoformat(),
            required_start=required_start.isoformat(),
        )
    return Sufficiency(True, "", warning=warning, bars=bars_n,
                       start_ts=eff_start.isoformat(),
                       required_start=required_start.isoformat())


# ================================================================= 强度定义

#: 信号类型权重：第一类（趋势背驰）最难得、最有价值；第二类（回抽不破）次之；
#: 第三类（离开中枢不回）再次。倍数关系取原文的「一类 > 二类 > 三类」顺序，
#: 具体数值只用于排序，不代表收益率预测。
KIND_WEIGHT: dict[str, float] = {
    "b1": 3.0, "s1": 3.0,
    "b2": 2.0, "s2": 2.0,
    "b3": 1.5, "s3": 1.5,
}
#: 级别权重：大级别的买卖点更可靠、更值得关注（日线 > 60/30 分钟 > 5 分钟）。
LEVEL_WEIGHT: dict[str, float] = {"day": 2.0, "60": 1.5, "30": 1.5, "15": 1.0, "5": 1.0}
#: 状态权重：只有确认过的结构才允许被当作可交易信号（未确认打折，失效归零）。
STATUS_WEIGHT: dict[str, float] = {"confirmed": 1.0, "tentative": 0.6, "invalidated": 0.0}

LEVEL_RANK: dict[str, int] = {"day": 0, "60": 1, "30": 2, "15": 3, "5": 4}

SIGNAL_LABEL: dict[str, str] = {
    "b1": "第一类买点", "b2": "第二类买点", "b3": "第三类买点",
    "s1": "第一类卖点", "s2": "第二类卖点", "s3": "第三类卖点",
}


def signal_label(kind: Any) -> str:
    return SIGNAL_LABEL.get(str(kind), str(kind))


def _status_value(status: Any) -> str:
    if isinstance(status, Status):
        return status.value
    if status is None:
        return "tentative"
    return str(getattr(status, "value", status)).lower()


def hit_strength(kind: Any, level: Any, divergence: float,
                 status: Any) -> tuple[float, tuple[tuple[str, Any], ...]]:
    """强度 = 类型权重 × 级别权重 × 状态权重 × (1 + 背驰强度)。

    为什么乘而不加：三者是**独立**的确认维度——信号类型决定「结构含义」，
    级别决定「影响范围」，背驰面积决定「力度衰竭程度」。加法会让「5 分钟
    第一类买点」和「日线第三类买点」难以区分；乘法保证任一项为 0（未确认）
    时整体归零，且每项都能在 `strength_parts` 里单独核对。
    """
    kw = KIND_WEIGHT.get(str(kind), 1.0)
    lw = LEVEL_WEIGHT.get(str(level), 1.0)
    sw = STATUS_WEIGHT.get(_status_value(status), 1.0)
    div = max(0.0, min(float(divergence or 0.0), 1.0))
    strength = kw * lw * sw * (1.0 + div)
    parts = (
        ("类型权重", kw),
        ("级别权重", lw),
        ("状态权重", sw),
        ("背驰强度", div),
        ("强度", round(strength, 4)),
    )
    return strength, parts


# ================================================================= 结果类型

class Outcome(str, Enum):
    """一行结果的三种结局：命中 / 数据不足跳过 / 失败。"""

    HIT = "hit"
    INSUFFICIENT = "insufficient"
    ERROR = "error"


@dataclass(frozen=True)
class ScanHit:
    """扫描结果行。命中、跳过、失败共用同一结构，方便并排展示与落库。"""

    code: str
    name: str = ""
    period: str = ""
    signal_kind: str = ""
    level: str = ""
    ts: str = ""
    price: float = 0.0
    pivot_range: tuple[float, ...] = ()
    divergence_strength: float = 0.0
    strength: float = 0.0
    status: str = ""
    detail: str = ""
    outcome: Outcome = Outcome.HIT
    reason: str = ""
    strength_parts: tuple[tuple[str, Any], ...] = ()
    warning: str = ""

    @property
    def signal_label(self) -> str:
        return signal_label(self.signal_kind)

    @property
    def is_hit(self) -> bool:
        return self.outcome is Outcome.HIT

    def as_detail_json(self) -> str:
        """`meta.scan_result.detail` 的载荷（中文，不丢信息）。"""
        return json.dumps({
            "name": self.name,
            "ts": self.ts,
            "price": self.price,
            "pivot_range": list(self.pivot_range),
            "divergence_strength": self.divergence_strength,
            "signal_label": self.signal_label,
            "status": self.status,
            "detail": self.detail,
            "strength_parts": [[k, v] for k, v in self.strength_parts],
            "warning": self.warning,
        }, ensure_ascii=False)


@dataclass(frozen=True)
class ScanReport:
    """一次扫描的完整结果：命中 + 跳过 + 失败 + 结构统计 + 耗时。"""

    hits: tuple[ScanHit, ...] = ()
    skipped: tuple[ScanHit, ...] = ()
    errors: tuple[ScanHit, ...] = ()
    tasks: int = 0
    codes: tuple[str, ...] = ()
    periods: tuple[str, ...] = ()
    elapsed: float = 0.0
    structure: dict[str, int] = field(default_factory=dict)
    saved: int = 0
    run_date: str = ""
    #: 数据质量提示（去重后带任务数）。**不挂在行上**：没有买卖点的票不产生任何行，
    #: 若警告只跟着行走，「元数据声称 1991 年、本地文件 2020 年起」这类问题会被静默丢掉。
    notes: tuple[str, ...] = ()

    def all_rows(self) -> list[ScanHit]:
        return [*self.hits, *self.skipped, *self.errors]

    @property
    def warnings(self) -> list[ScanHit]:
        return [r for r in self.all_rows() if r.warning]

    def summary(self) -> str:
        """中文汇总：命中/跳过/失败条数与原因分类，便于收盘后一眼看完。"""
        scanned = int(self.structure.get("codes_scanned", 0))
        parts = [
            f"扫描完成：{len(self.periods)} 个周期 × {len(self.codes)} 只 = {self.tasks} 个任务，"
            f"结构计算 {scanned} 个，用时 {self.elapsed:.2f} 秒",
            f"命中 {len(self.hits)} 条；数据不足跳过 {len(self.skipped)} 条；失败 {len(self.errors)} 条",
        ]
        if self.warnings:
            parts.append(f"警告 {len(self.warnings)} 条（数据起始与同步记录不一致等，见各行 warning）")
        if self.notes:
            parts.append(f"数据质量提示 {len(self.notes)} 条（本地数据起点与 meta.sync_state "
                         f"不一致等）：{self.notes[0]}")
        if self.saved:
            parts.append(f"已写入 scan_result {self.saved} 条（run_date={self.run_date}）")
        return "\n".join(parts)


# ================================================================= 单快照分析

def _segment_for(snap: Snapshot, sig: Any) -> Any | None:
    """按原始 bar 区间把信号对回触发它的线段（`src_*` 跨快照稳定）。"""
    for seg in snap.segments:
        if seg.src_start == getattr(sig, "src_start", None) and \
                seg.src_end == getattr(sig, "src_end", None):
            return seg
    return None


def segment_divergence(snap: Snapshot, seg: Any,
                       macd_df: pd.DataFrame | None) -> tuple[float, str]:
    """某条线段相对**前一条同向线段**的背驰强度 ∈[0,1] 与中文说明。

    定义：必须同时满足「创了新极值」且「MACD 柱面积缩小」
    （`chanlun.chan.macd.is_divergence`），强度取 `(前面积 - 后面积) / 前面积`，
    即力度衰竭的百分比。
    为什么用面积比而不是 DIF 高度：原文第 24 课看的是这波走势的「力度」，
    面积是对整段走势积分，比单点极值更能反映能量。
    """
    if macd_df is None or len(macd_df) == 0:
        return 0.0, "未提供 MACD，背驰强度按 0 计"
    prev = None
    for cand in snap.segments:
        if cand.direction != seg.direction or cand.status is Status.INVALIDATED:
            continue
        if cand.src_end < seg.src_start and (prev is None or cand.src_end > prev.src_end):
            prev = cand
    if prev is None:
        return 0.0, "没有同向的前一段可比，背驰强度按 0 计"
    try:
        area_prev = hist_area(macd_df, prev.src_start, prev.src_end)
        area_now = hist_area(macd_df, seg.src_start, seg.src_end)
    except ValueError as exc:  # 索引越界：数据与 MACD 长度不匹配
        return 0.0, f"MACD 区间越界（{exc}），背驰强度按 0 计"
    ext_prev = prev.high if seg.direction == 1 else prev.low
    ext_now = seg.high if seg.direction == 1 else seg.low
    if not is_divergence(ext_prev, ext_now, area_prev, area_now, seg.direction):
        return 0.0, f"面积 {area_prev:.2f}→{area_now:.2f}，未创新极值或力度未衰竭，不算背驰"
    strength = 0.0 if area_prev <= 0 else max(0.0, min((area_prev - area_now) / area_prev, 1.0))
    direction_cn = "顶背驰" if seg.direction == 1 else "底背驰"
    return strength, f"{direction_cn}：面积 {area_prev:.2f}→{area_now:.2f}（衰竭 {strength:.0%}）"


def divergence_of(snap: Snapshot, sig: Any, macd_df: pd.DataFrame | None) -> tuple[float, str]:
    """信号触发段的背驰强度与中文说明（先按原始 bar 区间把信号对回线段）。"""
    seg = _segment_for(snap, sig)
    if seg is None:
        return 0.0, "找不到触发段，背驰强度按 0 计"
    return segment_divergence(snap, seg, macd_df)


def analyze_snapshot(snap: Snapshot, bars: pd.DataFrame, *, name: str = "",
                     macd_df: pd.DataFrame | None = None,
                     warning: str = "") -> list[ScanHit]:
    """把一个结构化快照翻译成按强度降序排列的 `ScanHit` 列表（纯函数）。

    `macd_df` 可注入：测试用假 MACD 精确控制背驰面积，生产用真 `macd(close)`。
    """
    pivots = {p.idx: p for p in snap.pivots}
    hits: list[ScanHit] = []
    for sig in snap.signals:
        kind = str(getattr(sig, "kind", ""))
        kind = str(getattr(sig.kind, "value", sig.kind))
        level = str(getattr(sig, "level", "") or snap.level or snap.period)
        p = pivots.get(getattr(sig, "pivot_idx", None))
        pivot_range: tuple[float, ...] = ()
        pivot_text = "无对应中枢"
        if p is not None:
            pivot_range = (round(float(p.zd), 2), round(float(p.zg), 2))
            seg_count = int(p.end_idx) - int(p.start_idx) + 1
            pivot_text = f"中枢 {pivot_range[0]:.2f}-{pivot_range[1]:.2f}（{seg_count} 段）"
        div, div_text = divergence_of(snap, sig, macd_df)
        status = _status_value(getattr(sig, "status", None))
        strength, parts = hit_strength(kind, level, div, status)
        hit = ScanHit(
            code=snap.code, name=name, period=snap.period, signal_kind=kind, level=level,
            ts=str(getattr(sig, "ts", "")), price=round(float(getattr(sig, "price", 0.0)), 4),
            pivot_range=pivot_range, divergence_strength=div, strength=strength,
            status=status,
            detail=(f"{signal_label(kind)} @ {getattr(sig, 'ts', '')} 价 "
                    f"{float(getattr(sig, 'price', 0.0)):.2f}；{pivot_text}；"
                    f"{div_text}；{getattr(sig, 'reason', '') or '无补充说明'}"),
            outcome=Outcome.HIT, strength_parts=parts, warning=warning,
        )
        hits.append(hit)
    hits.sort(key=lambda h: (-h.strength, LEVEL_RANK.get(h.level, 9), h.ts, h.code, h.period))
    return hits


# ================================================================= 任务与执行

def default_snapshot_source(code: str, period: str, bars: pd.DataFrame) -> Snapshot:
    """生产路径：真引擎全量重算（含三类买卖点）。"""
    return ChanEngine(code, period, signal_fn=find_signals, level=period).full(bars)


@dataclass(frozen=True)
class ScanTask:
    """一个「股票 × 周期」任务。字段全是可 pickle 的原语，便于送进子进程。"""

    code: str
    name: str
    period: str
    sync_start: str | None
    sync_end: str | None
    data_root: str
    sync_error: str | None = None


@dataclass(frozen=True)
class TaskResult:
    rows: tuple[ScanHit, ...] = ()
    stats: dict[str, int] = field(default_factory=dict)
    #: 该任务的数据质量提示（行里已经写过的不再重复）
    notes: tuple[str, ...] = ()


def _skip_row(task: ScanTask, reason: str, warning: str = "", outcome=Outcome.INSUFFICIENT) -> ScanHit:
    return ScanHit(code=task.code, name=task.name, period=task.period, outcome=outcome,
                   reason=reason, warning=warning, level=task.period)


def _execute(task: ScanTask, snapshot_source: Callable[..., Snapshot] | None,
             macd_fn: Callable[[Any], pd.DataFrame] | None) -> TaskResult:
    """跑一个任务。**任何异常都在这里被吃住**，转成 ERROR 行而不是抛出。"""
    store.DATA_ROOT = Path(task.data_root)
    try:
        bars = store.read(task.code, task.period)
    except Exception as exc:  # 坏 parquet / 权限 / 磁盘
        return TaskResult(rows=(_skip_row(
            task, f"扫描失败：读取本地数据出错（{type(exc).__name__}: {exc}）", outcome=Outcome.ERROR),))

    suf = check_sufficiency(bars, task.period, task.sync_start)
    warning = suf.warning
    if task.sync_error:
        warning = (warning + "；" if warning else "") + f"上次同步有错：{task.sync_error}"
    if not suf.ok:
        return TaskResult(rows=(_skip_row(task, suf.reason, warning=warning),))

    try:
        snap = (snapshot_source or default_snapshot_source)(task.code, task.period, bars)
    except Exception as exc:
        return TaskResult(rows=(_skip_row(
            task, f"扫描失败：结构计算出错（{type(exc).__name__}: {exc}）",
            warning=warning, outcome=Outcome.ERROR),))

    macd_df = None
    try:
        macd_df = (macd_fn or macd)(bars["close"])
    except Exception as exc:
        warning = (warning + "；" if warning else "") + f"MACD 计算失败（{exc}），背驰强度按 0 计"

    hits = analyze_snapshot(snap, bars, name=task.name, macd_df=macd_df, warning=warning)
    stats = {
        "codes_scanned": 1,
        "bars": int(len(bars)),
        "strokes": len(snap.strokes),
        "segments": len(snap.segments),
        "pivots": len(snap.pivots),
        "signals": len(snap.signals),
    }
    notes: tuple[str, ...] = ()
    if warning and not hits:
        # 没有买卖点 → 这一任务不产生任何行；警告必须另找地方落地，否则静默丢失。
        notes = (f"{task.code}[{task.period}] {warning}",)
    return TaskResult(rows=tuple(hits), stats=stats, notes=notes)


def _scan_task(task: ScanTask) -> TaskResult:
    """`ProcessPoolExecutor` 的入口（必须是模块级函数才可 pickle）。"""
    return _execute(task, None, None)


def save_report(conn: sqlite3.Connection, report: ScanReport, run_date: str) -> int:
    """把命中写入 `scan_result`（跳过与失败不落库，避免污染结果表）。"""
    rows = [(h.code, h.period, h.signal_kind, h.level, round(float(h.strength), 4),
             h.as_detail_json()) for h in report.hits]
    return meta.save_scan_results(conn, run_date, rows) if rows else 0


# ================================================================= 对外主入口

def scan_report(
    periods: Sequence[str] = ("day", "30", "5"),
    codes: Iterable[str] | None = None,
    max_workers: int = 8,
    *,
    conn: sqlite3.Connection | None = None,
    snapshot_source: Callable[..., Snapshot] | None = None,
    macd_fn: Callable[[Any], pd.DataFrame] | None = None,
    save: bool = True,
    run_date: str | None = None,
) -> ScanReport:
    """全市场 / 指定代码的扫描，返回完整报告（命中 + 跳过 + 失败 + 统计）。

    - `codes=None`：扫 `meta.universe` 全市场（5471 只），这是「全市场扫描」的默认口径；
    - `snapshot_source` / `macd_fn` 只为测试与复用注入；一旦注入就自动**串行**执行
      （闭包无法 pickle，而且串行才能保证测试确定性）；
    - `save=True`：命中写入 `scan_result`（跳过/失败不落库）。
    """
    own_conn = conn is None
    conn = conn or meta.init()
    started = dt.datetime.now()
    period_list = tuple(str(p) for p in periods)
    run_date = run_date or dt.date.today().isoformat()

    try:
        universe = {str(r["code"]): (r["name"] or "") for r in meta.get_universe(conn)}
        if codes is None:
            if not universe:
                raise ValueError(
                    "universe 为空，无法做全市场扫描；请先执行 `python -m chanlun sync` 同步品种表")
            code_list = sorted(universe)
        else:
            code_list = [str(c).strip() for c in codes if str(c).strip()]

        sync_rows = {(str(r["code"]), str(r["period"])): r for r in meta.all_sync(conn)}
        root = str(store.data_root())
        tasks = [
            ScanTask(
                code=code, name=universe.get(code, ""), period=period,
                sync_start=(sync_rows[(code, period)]["start_ts"]
                            if (code, period) in sync_rows else None),
                sync_end=(sync_rows[(code, period)]["end_ts"]
                          if (code, period) in sync_rows else None),
                sync_error=(sync_rows[(code, period)]["error"]
                            if (code, period) in sync_rows else None),
                data_root=root,
            )
            for code in code_list for period in period_list
        ]

        results: list[TaskResult] = []
        if max_workers > 1 and snapshot_source is None and macd_fn is None and len(tasks) > 1:
            try:
                with ProcessPoolExecutor(max_workers=max_workers) as pool:
                    results = list(pool.map(_scan_task, tasks))
            except Exception as exc:  # 进程池不可用（受限环境）→ 退化为串行，不丢结果
                log.warning("进程池不可用（%s），退化为串行扫描", exc)
                results = []
        if not results:
            results = [_execute(t, snapshot_source, macd_fn) for t in tasks]

        hits: list[ScanHit] = []
        skipped: list[ScanHit] = []
        errors: list[ScanHit] = []
        stats: dict[str, int] = {}
        for res in results:
            for row in res.rows:
                if row.outcome is Outcome.HIT:
                    hits.append(row)
                elif row.outcome is Outcome.INSUFFICIENT:
                    skipped.append(row)
                else:
                    errors.append(row)
            for key, value in res.stats.items():
                stats[key] = stats.get(key, 0) + int(value)

        note_texts: dict[str, int] = {}
        note_order: list[str] = []
        for res in results:
            for note in res.notes:
                text = note.split("] ", 1)[-1] if "] " in note else note
                if text not in note_texts:
                    note_texts[text] = 0
                    note_order.append(text)
                note_texts[text] += 1
        notes = tuple(f"{text}（{note_texts[text]} 个任务）" if note_texts[text] > 1 else text
                      for text in note_order)

        hits.sort(key=lambda h: (-h.strength, LEVEL_RANK.get(h.level, 9), h.ts, h.code, h.period))
        skipped.sort(key=lambda h: (h.code, h.period))
        errors.sort(key=lambda h: (h.code, h.period))
        report = ScanReport(
            hits=tuple(hits), skipped=tuple(skipped), errors=tuple(errors),
            tasks=len(tasks), codes=tuple(code_list), periods=period_list,
            elapsed=(dt.datetime.now() - started).total_seconds(),
            structure=stats, saved=0, run_date=run_date, notes=notes,
        )
        if save:
            report = ScanReport(**{**report.__dict__, "saved": save_report(conn, report, run_date)})
        return report
    finally:
        if own_conn:
            conn.close()


def scan(
    periods: Sequence[str] = ("day", "30", "5"),
    codes: Iterable[str] | None = None,
    max_workers: int = 8,
    **kwargs: Any,
) -> list[ScanHit]:
    """契约入口：只返回命中列表（跳过与失败在 `scan_report` 里看）。"""
    return list(scan_report(periods=periods, codes=codes, max_workers=max_workers,
                            **kwargs).hits)
