"""缠论计算引擎：全量 `full()` 与增量 `step()`，以及「全量 ≡ 增量」不变量。

设计要点
--------
1. **快照不可变**。`Snapshot` 是 frozen dataclass，字段全是 tuple；任何一次
   计算都产出新快照，旧快照继续可用。回测要「回到某一根 bar 当时能看到
   什么」，靠的就是旧快照没被就地改写。
2. **`step()` 不做尾部局部重算**，而是「全量重算 + 状态机对齐」。原因不是
   偷懒：线段划分用的是**全局 DP**（最大化确认段数，见 `segment.py`），
   新增一根 bar 在原理上就可能改变整段划分，所以局部重算在数学上不成立。
   增量的语义体现在 **对象身份与确认时间的延续**上：`reconcile()` 让同一个
   结构（按原始 bar 跨度定 ID）保持 `confirmed_at` 不变、被推翻的旧结构
   留痕为 `INVALIDATED`。日线全市场 5400 只按 `full()` 重算约 1 分钟，
   本来就在每日 5 分钟预算内，没必要为省这点时间引入不可验证的局部性。
3. **不变量有测试兜底**：`step(prev, bars)` 的结构字段（去掉 INVALIDATED
   留痕、去掉 version）必须与 `full(bars)` **逐字段相等**，真实数据上逐根
   bar 走一遍验证。唯一的例外是 `confirmed_at`：`step()` 保留的是**首次观测
   到**的确认时间，而 `full()` 用当前重算值。两者不等不是 bug —— 落在末端的
   确认分型会被「更极端的同类后续分型」顶替（例如确认某笔的底分型被更低的
   新低顶到后面几根 bar 上），于是 `full()` 的值只会往后挪，`step()` 的旧值
   必然 `<=` 它，两者都是合法的 point-in-time 取值。测试按「结构相等 + 时间
   戳不倒退」两条分别校验。
4. **point-in-time 回测不逐步调 `step()`**。每个结构的 `confirmed_at` 已把
   「什么时候才知道它成立」记下来了，一次 `full()` 就够：回测按 bar 遍历，
   只读 `state.backtestable(items, as_of)`。逐步 step 只是为了对齐快照状态，
   不是回测的正确性来源。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

import pandas as pd

from ..data.types import normalize
from .fractal import find_fractals
from .include import merge_bars
from .pivot import Pivot, find_pivots
from .segment import SegmentPolicy, build_segments
from .state import reconcile
from .stroke import build_strokes
from .types import Fractal, MergedBar, Segment, Stroke

#: 买卖点判定回调：`(bars, segments, pivots, level) -> Sequence[Signal]`。
#: 引擎不内置买卖点规则，避免动力学规则与结构计算互相绑架。
SignalFn = Callable[[pd.DataFrame, Sequence[Segment], Sequence[Pivot], str],
                    Sequence[Any]]


@dataclass(frozen=True)
class Snapshot:
    """某一时刻、某一级别的完整缠论结构。字段全部不可变。"""

    code: str
    period: str
    as_of: str
    #: 缠论级别。多数情况下等于 `period`，但级别与行情周期是两个概念，
    #: 分开存以免下层（中枢、买卖点）的级别信息被周期名覆盖。
    level: str = ""
    merged: tuple[MergedBar, ...] = ()
    fractals: tuple[Fractal, ...] = ()
    strokes: tuple[Stroke, ...] = ()
    segments: tuple[Segment, ...] = ()
    pivots: tuple[Pivot, ...] = ()
    signals: tuple[Any, ...] = ()
    version: int = 1

    @property
    def confirmed_segments(self) -> tuple[Segment, ...]:
        from .types import Status

        return tuple(s for s in self.segments if s.status is Status.CONFIRMED)

    def as_dict(self) -> dict[str, Any]:
        from .types import to_jsonable

        return to_jsonable(self)


class ChanEngine:
    """把 bar 帧算成 `Snapshot`。纯计算，不碰数据库、不碰网络。"""

    def __init__(
        self,
        code: str,
        period: str = "day",
        policy: SegmentPolicy | None = None,
        signal_fn: SignalFn | None = None,
        level: str | None = None,
    ) -> None:
        if not code:
            raise ValueError("code 不能为空")
        self.code = code
        self.period = period
        self.level = level or period
        self.policy = policy
        self.signal_fn = signal_fn

    # ---- 对外 ----

    def full(self, bars: pd.DataFrame | None) -> Snapshot:
        """从零重算。首算、每日收盘后跑批、全市场扫描都走这里。"""
        return self._compute(normalize(bars), version=1)

    def step(self, prev: Snapshot | None, new_bars: pd.DataFrame | None) -> Snapshot:
        """在 `prev` 之上推进到 `new_bars` 的终点。

        `new_bars` 必须是**含新增 bar 在内的完整 bar 帧**（快照不持有行情，
        否则内存会被全市场行情放大到不可接受）。`prev is None` 等价于 `full()`。
        """
        bars = normalize(new_bars)
        if len(bars) == 0:
            raise ValueError(f"{self.code} 收到空 bar 帧")
        last = str(bars["ts"].iloc[-1])
        if prev is not None and last < prev.as_of:
            raise ValueError(
                f"{self.code} 新 bar 帧终点 {last} 早于上一快照 {prev.as_of}；"
                "step() 只能向前推进，缩短的帧会静默算错结构"
            )
        if prev is None:
            return self._compute(bars, version=1)
        new = self._compute(bars, version=1)
        return replace(reconcile(prev, new), version=prev.version + 1)

    # ---- 内部 ----

    def _compute(self, bars: pd.DataFrame, version: int) -> Snapshot:
        if len(bars) == 0:
            raise ValueError(f"{self.code} 收到空 bar 帧")
        merged = merge_bars(bars)
        fractals = find_fractals(merged, bars=bars)
        strokes = build_strokes(fractals)
        segments = build_segments(strokes, self.policy)
        pivots = find_pivots(segments, self.level)
        signals: Sequence[Any] = ()
        if self.signal_fn is not None:
            signals = self.signal_fn(bars, segments, pivots, self.level)
        return Snapshot(
            code=self.code,
            period=self.period,
            as_of=str(bars["ts"].iloc[-1]),
            level=self.level,
            merged=tuple(merged),
            fractals=tuple(fractals),
            strokes=tuple(strokes),
            segments=tuple(segments),
            pivots=tuple(pivots),
            signals=tuple(signals),
            version=version,
        )
