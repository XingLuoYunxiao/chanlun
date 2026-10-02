"""走势类型：趋势与盘整（第 17 课、第 20 课）。

缠论把走势分成两类：

- **趋势**：至少两个同级别中枢**依次上升或下降**；
- **盘整**：只有一个中枢，或相邻中枢不构成趋势。

判据只看中枢（`pivot.py` 的产物），不需要再回到笔/线段。本模块只吃
`CONFIRMED` 中枢：一个还在延伸的中枢随时可能吞掉下一段，拿它定走势类型
就是拿未来数据下结论。

「依次上升/下降」用第 20 课中心定理二的原文口径（见 `_direction`）：比的是
**围绕中枢波动的区间 `[DD, GG]`**，而且必须**严格分离**。第 20 课同一课把
「在趋势里，同级别的前后走势中枢是不能有任何重叠的，这包括任何围绕走势中枢
产生的任何瞬间波动之间的重叠」写死了 —— 所以两个波动区间只要还沾着，就落到
定理二第三句「形成高级别的走势中枢」，那是**级别扩张**，不是趋势。
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
    """两个相邻同级别中枢的走向：1 上涨，-1 下跌，0 形成高级别中枢（不是趋势）。

    判据逐字来自第 20 课「走势中枢中心定理二」：

        前后同级别的两个缠中说禅走势中枢，后GG〈前DD等价于下跌及其延续；后DD〉
        前GG等价于上涨及其延续。后ZG<前ZD且后GG〉=前DD，或后ZD〉前ZG且后DD=<前GG，
        则等价于形成高级别的走势中枢。

    比的是**围绕中枢波动的区间 `[DD, GG]`**，不是中枢区间 `[ZD, ZG]`。这两句是
    完全分类：实测 24 只票日线 22 个相邻中枢对，「不满足前两句也不满足第三句」的
    是 0 对（`optimizer/tools/measure_l20_dd_gg_scope.py`），所以落在 else 分支的
    就是「形成高级别的走势中枢」——第 20 课把它和趋势并列，**不是趋势**，返回 0。
    """
    if b.dd > a.gg:
        return 1
    if b.gg < a.dd:
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
        want = _direction(confirmed[i], confirmed[i + 1]) if i + 1 < n else 0
        if want != 0:
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
