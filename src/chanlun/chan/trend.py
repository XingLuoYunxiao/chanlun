"""走势类型：趋势与盘整（第 17 课、第 65 课）。

缠论把走势分成两类：

- **趋势**：至少两个同级别中枢**依次上升或下降**；
- **盘整**：只有一个中枢，或相邻中枢方向不一致。

判据只看中枢（`pivot.py` 的产物），不需要再回到笔/线段。本模块只吃
`CONFIRMED` 中枢：一个还在延伸的中枢随时可能吞掉下一段，拿它定走势类型
就是拿未来数据下结论。

已知的进一步细化空间（留给优化师按原文重新推导）：原文谈趋势时强调同级别
中枢**互不重叠**，重叠的中枢按第 20 课应做**扩展**合成更高级别中枢。本实现
只要求「依次上升/下降」（`zd` 与 `zg` 同时抬高/降低），没有强制不重叠，
因此重叠但整体抬高的两个中枢也会被算成上涨趋势。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .pivot import Pivot
from .types import Status


class TrendType(str, Enum):
    """走势类型。"""

    UP = "up"                       # 上涨
    DOWN = "down"                   # 下跌
    CONSOLIDATION = "consolidation"  # 盘整


@dataclass(frozen=True)
class Trend:
    """一段走势类型。`start_idx`/`end_idx` 是中枢下标（闭区间）。"""

    idx: int
    kind: TrendType
    start_idx: int
    end_idx: int
    start_ts: str
    end_ts: str
    level: str
    pivot_count: int
    status: Status = Status.CONFIRMED
    confirmed_at: str | None = None


def _direction(a: Pivot, b: Pivot) -> int:
    """两个相邻中枢的走向：1 依次上升，-1 依次下降，0 不构成趋势。"""
    if b.zd > a.zd and b.zg > a.zg:
        return 1
    if b.zd < a.zd and b.zg < a.zg:
        return -1
    return 0


def _status_of(pivots: list[Pivot]) -> tuple[Status, str | None]:
    if any(p.status is not Status.CONFIRMED for p in pivots):
        return Status.TENTATIVE, None
    # 趋势的确认时间 = 最后一个中枢的确认时间；最后一个中枢没确认就还没确认。
    stamps = [p.confirmed_at for p in pivots]
    if any(s is None for s in stamps):
        return Status.TENTATIVE, None
    return Status.CONFIRMED, max(stamps)  # type: ignore[arg-type]


def classify_trends(pivots, level: str = "day") -> list[Trend]:
    """把中枢序列切成走势类型。返回值按时间序完整覆盖入参中枢。

    只有 `CONFIRMED` 中枢参与；未被确认的中枢直接跳过（它们多半是窗口右端
    那一个，走势类型要等它定下来）。
    """
    confirmed = [p for p in pivots if p.status is Status.CONFIRMED]
    out: list[Trend] = []
    i = 0
    n = len(confirmed)
    while i < n:
        if i + 1 < n and _direction(confirmed[i], confirmed[i + 1]) != 0:
            want = _direction(confirmed[i], confirmed[i + 1])
            j = i + 1
            while j + 1 < n and _direction(confirmed[j], confirmed[j + 1]) == want:
                j += 1
            group = confirmed[i : j + 1]
            status, confirmed_at = _status_of(group)
            out.append(
                Trend(
                    idx=len(out),
                    kind=TrendType.UP if want == 1 else TrendType.DOWN,
                    start_idx=group[0].idx,
                    end_idx=group[-1].idx,
                    start_ts=group[0].start_ts,
                    end_ts=group[-1].end_ts,
                    level=level,
                    pivot_count=len(group),
                    status=status,
                    confirmed_at=confirmed_at,
                )
            )
            i = j + 1
        else:
            p = confirmed[i]
            status, confirmed_at = _status_of([p])
            out.append(
                Trend(
                    idx=len(out),
                    kind=TrendType.CONSOLIDATION,
                    start_idx=p.idx,
                    end_idx=p.idx,
                    start_ts=p.start_ts,
                    end_ts=p.end_ts,
                    level=level,
                    pivot_count=1,
                    status=status,
                    confirmed_at=confirmed_at,
                )
            )
            i += 1
    return out


def validate_trends(trends, pivots=None) -> list[str]:
    """走势类型的结构不变量校验，供测试与审计脚本调用。"""
    problems: list[str] = []
    for t in trends:
        if t.kind is not TrendType.CONSOLIDATION and t.pivot_count < 2:
            problems.append(f"trend {t.idx}: 趋势至少要有两个中枢")
        if t.kind is TrendType.CONSOLIDATION and t.pivot_count != 1:
            problems.append(f"trend {t.idx}: 盘整只能覆盖一个中枢")
        if t.end_idx < t.start_idx:
            problems.append(f"trend {t.idx}: 中枢区间颠倒")
        if t.start_ts > t.end_ts:
            problems.append(f"trend {t.idx}: 时间颠倒")
        if t.status is Status.CONFIRMED and t.confirmed_at is None:
            problems.append(f"trend {t.idx}: 已确认却没有确认时间")
        if t.status is Status.TENTATIVE and t.confirmed_at is not None:
            problems.append(f"trend {t.idx}: 未确认却有确认时间")
    for prev, nxt in zip(trends, trends[1:]):
        if nxt.start_idx <= prev.end_idx:
            problems.append(f"trend {nxt.idx}: 与上一段走势中枢区间重叠")
    return problems
