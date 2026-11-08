"""**信号留痕**：把「当时标过、后来被走势证伪」的买卖点找出来。

为什么要它
----------
用户的原话：「它的那个盘整背驰点或者买点它是不确定的，是需要后续的走势确认的……
如果这个未确认的被证伪了，我们也不能直接去除……要有一个记录的点位，说这个是
当时的一个错误（不是错误，是当时标了、最后走势把这个点位证伪了），需要有一个
这样的标注。」

这在缠论里不是可选项，是**定义的一部分**：买卖点是**当下的判断**，不是未来的事实。
第 17 课把第二类买点建立在「第一类买点之后第一次次级别回调」上，那个回调是否成立
本来就只能等走势自己走出来。所以「标了、后来又没了」是**正常现象**，必须留痕，
否则用户看到的永远是一张「事后诸葛亮」的图 —— 图上只剩活下来的信号。

现有的机制与缺口
----------------
`chan/state.py::reconcile()` 已经实现了「**失效留痕**」：被推翻的对象（无论此前是
TENTATIVE 还是 CONFIRMED）转为 `INVALIDATED` 并保留在快照里。但它是**增量**路径
（`engine.step()`）的一部分，而 `scan` 与 `backtest` 走的都是 `engine.full()` ——
**`full()` 是从头重算「现在是什么样」，没有任何东西可被推翻**。
所以生产路径里**从来不会出现 `INVALIDATED` 信号**。

本模块补的就是这个出口，做法是**按检查点回放**：

1. 每隔 `step` 根 bar 截断一次，算一次 as-of 快照（`full(bars[:k])`）；
2. 把每次快照里的买卖点按 `(类型, src_start, src_end)` 记下来；
3. 与**最终**快照对比：
   - 还在 ⇒ **存活**；
   - 不在了 ⇒ **被证伪**，并给出它**最后出现的那次 as-of 时间**。

**为什么不用 `step()`**：`step()` 内部仍是完整重算（`_compute`）再加 reconcile，
逐根回放 2 万根就是 2 万次全算。检查点回放是同一个语义的稀疏版本，
`step` 决定分辨率。

**「被证伪」的准确含义**
------------------------
它指的是「**把后来的 bar 也纳入计算后，这个买卖点不再成立**」——
不是「价格走反了」（那是策略问题，不是结构问题）。所以文案里不写「错误」，
写「后来重算不再成立」，并给出它存在过的最后一刻。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .engine import ChanEngine
from .signal import find_signals

__all__ = ["SignalTrail", "trail"]


@dataclass(frozen=True)
class TrailEntry:
    """一条信号的完整履历。"""

    kind: str
    ts: str
    price: float
    src_start: int
    src_end: int
    #: 第一次出现在某个 as-of 快照里的时间（「当时标了」的那一刻）
    first_seen: str
    #: 最后一次还出现在 as-of 快照里的时间（「后来被证伪」前的那一刻）
    last_seen: str
    #: 第一次出现时的状态（多半是 `tentative`，也可能是 `confirmed`）
    first_status: str
    #: 最后一次出现时的状态
    last_status: str
    #: `True` = 最终快照里还在；`False` = **被证伪**
    alive: bool
    #: 被证伪时：在哪一次 as-of 检查点上它已经不在了（`None` = 存活）
    falsified_at: str | None = None
    reason: str = ""

    @property
    def label(self) -> str:
        return "存活" if self.alive else "被证伪"


@dataclass(frozen=True)
class SignalTrail:
    """一次回放的全部结果。"""

    code: str
    period: str
    bars: int
    step: int
    checkpoints: tuple[str, ...]
    entries: tuple[TrailEntry, ...]

    @property
    def alive(self) -> tuple[TrailEntry, ...]:
        return tuple(e for e in self.entries if e.alive)

    @property
    def falsified(self) -> tuple[TrailEntry, ...]:
        return tuple(e for e in self.entries if not e.alive)


def _identity(sig: Any) -> tuple[str, int, int]:
    """信号身份 = `(类型, src_start, src_end)`。

    **不能用 `state.object_id()`**：它是 `signal@起-止`，同一段上不同类型的信号
    （例如 `b1` 与 `b3`）会撞成同一个 ID，于是「一条被证伪、另一条存活」会被
    错误地合并。身份必须把类型也带上。
    """
    return (str(getattr(sig.kind, "value", sig.kind)),
            int(sig.src_start), int(sig.src_end))


def _signals_of(engine: ChanEngine, frame: pd.DataFrame) -> list[Any]:
    if len(frame) == 0:
        return []
    snap = engine.full(frame)
    return list(snap.signals)


def trail(
    code: str,
    period: str = "day",
    *,
    step: int = 200,
    bars: pd.DataFrame | None = None,
) -> SignalTrail:
    """按检查点回放，给出每条信号的存亡履历。

    `step` 是分辨率：每隔多少根 bar 算一次 as-of 快照。**越小越准、越慢**；
    取 200 根时一个 2.2 万根的 1 分钟序列是 110 次全算。
    """
    from ..data import store

    if bars is None:
        bars = store.read(code, period)
    if len(bars) == 0:
        raise ValueError(f"{code} 的 {period} 周期没有本地数据")

    if step < 1:
        raise ValueError("step 必须 >= 1")

    engine = ChanEngine(code, period, signal_fn=find_signals, level=period)
    n = len(bars)
    # 检查点必须**包含最后一根** —— 那是「最终快照」，存活的判据全在它身上。
    cuts = list(range(step, n, step)) + [n]

    seen: dict[tuple[str, int, int], dict[str, Any]] = {}
    checkpoints: list[str] = []
    for k in cuts:
        frame = bars.iloc[:k]
        as_of = str(frame["ts"].iloc[-1])
        checkpoints.append(as_of)
        for sig in _signals_of(engine, frame):
            key = _identity(sig)
            rec = seen.get(key)
            if rec is None:
                seen[key] = {
                    "sig": sig, "first_seen": as_of, "last_seen": as_of,
                    "first_status": str(getattr(sig.status, "value", sig.status)),
                    "last_status": str(getattr(sig.status, "value", sig.status)),
                    # 第一次出现在**哪一次检查点**（存亡判定要用序号，不能用时间）
                    "last_index": len(checkpoints) - 1,
                }
            else:
                rec["sig"] = sig
                rec["last_seen"] = as_of
                rec["last_status"] = str(getattr(sig.status, "value", sig.status))
                rec["last_index"] = len(checkpoints) - 1
        # 下一轮循环里，本轮是「历史」，最后一轮是「最终」

    final_as_of = checkpoints[-1]
    entries: list[TrailEntry] = []
    for key, rec in seen.items():
        sig = rec["sig"]
        alive = rec["last_index"] == len(checkpoints) - 1
        falsified_at = None
        if not alive:
            # 它最后一次出现之后的那个检查点，就是「已经不在了」的第一刻 ——
            # 那才是用户要的「点位」：从这一刻起，之前的标记不再成立。
            # 用序号取，不用时间字符串比较（时间格式不保证可比）。
            falsified_at = checkpoints[rec["last_index"] + 1]
        entries.append(TrailEntry(
            kind=key[0],
            ts=str(sig.ts),
            price=float(sig.price),
            src_start=key[1],
            src_end=key[2],
            first_seen=rec["first_seen"],
            last_seen=rec["last_seen"],
            first_status=rec["first_status"],
            last_status=rec["last_status"],
            alive=alive,
            falsified_at=falsified_at,
            reason=str(sig.reason or ""),
        ))
    entries.sort(key=lambda e: (e.first_seen, e.src_start, e.kind))
    return SignalTrail(
        code=code, period=period, bars=n, step=step,
        checkpoints=tuple(checkpoints), entries=tuple(entries),
    )
