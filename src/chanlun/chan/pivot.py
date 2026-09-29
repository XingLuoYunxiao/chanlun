"""中枢（第 17/20 课）。

原文规则
--------
中枢 = **至少三个连续次级别走势类型**的重叠区间。本实现用线段近似「次级别
走势类型」，于是：

    ZG = min(前三个线段的高点)      ZD = max(前三个线段的低点)
    ZG > ZD 才成枢（相切不算）       GG = max(所有段高点)   DD = min(所有段低点)

`[ZD, ZG]` 是中枢区间，`[DD, GG]` 是中枢的完整振幅。二者不可混用：判断「离开
中枢」看的是 `[ZD, ZG]`。

三个显式取舍（都有测试锁住，不是随手简化）
------------------------------------------
1. **只有 `CONFIRMED` 线段参与**。中枢要求次级别走势类型已经完成；让未确认的
   尾段参与，中枢会随尾段来回变形，回测就会漂移。
2. **延伸不改变 `ZD`/`ZG`**。区间由最初三段确立，后续段只让 `end_idx` 后移。
   否则「离开中枢」的判据本身会动，第三类买卖点就无从定义。
3. **右端用尽可用线段 → `TENTATIVE`**。此时还不知道有没有下一段继续延伸，
   右侧未定，故不能当已成立的中枢使用。反之，若右端已被一个不重叠的**离开段**
   封闭，则中枢 `CONFIRMED`。

延伸 / 新生 / 扩展
------------------
- **延伸**：后续段仍与 `[ZD, ZG]` 重叠 → `end_idx` 后移（第 17 课）。
- **新生**：不再重叠的段即「离开段」。第一个离开段之后，再从其后寻找新的三重叠
  → 新中枢（对应第三类买卖点：离开后回抽不回中枢）。
- **扩展**：两个同级别中枢区间有重叠 → 用 `merge_pivots` 合并为高级别中枢。
  这是**单独的函数**、由调用方显式选择，因为它会改变级别语义（第 20 课）。

已知的进一步细化空间（留给 Task 17 优化师用原文重新推导）：相邻三段必须
「连续」重叠，本实现不处理「离开段后又回到中枢」的九段式中枢。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .types import Segment, Status

#: 级别升级：一个级别的中枢扩展后成为上一级别的中枢。
LEVEL_UP: dict[str, str] = {
    "5": "30",
    "30": "day",
    "day": "week",
    "week": "month",
}

MIN_SEGMENTS = 3


def next_level(level: str) -> str:
    """返回上一级别名。未知级别退化为 `"<level>+"`，不做静默猜测。"""
    return LEVEL_UP.get(level, f"{level}+")


@dataclass(frozen=True)
class Pivot:
    """中枢。`zg`/`zd` 为中枢区间，`gg`/`dd` 为完整振幅。"""

    idx: int
    zg: float
    zd: float
    gg: float
    dd: float
    start_idx: int          # 参与本中枢的第一段（线段下标）
    end_idx: int            # 参与本中枢的最后一段
    start_ts: str
    end_ts: str
    level: str
    status: Status = Status.CONFIRMED
    confirmed_at: str | None = None
    # 原始 bar 跨度，用于跨快照追踪（稳定 ID）与作图。
    src_start: int = 0
    src_end: int = 0

    @property
    def segment_count(self) -> int:
        return self.end_idx - self.start_idx + 1


def _overlaps(seg: Segment, zd: float, zg: float) -> bool:
    """线段与中枢区间 `[zd, zg]` 是否有重叠（闭区间相交）。"""
    return seg.low <= zg and seg.high >= zd


def find_pivots(segments: Sequence[Segment], level: str) -> list[Pivot]:
    """按第 17 课从线段序列中依次找出中枢。

    只使用 `CONFIRMED` 线段；`TENTATIVE` 线段被完全忽略（既不参与构造，
    也不参与延伸），因此调用方可以安全地把「含未确认尾段」的线段序列传进来。
    """
    confirmed = [s for s in segments if s.status is Status.CONFIRMED]
    n = len(confirmed)
    pivots: list[Pivot] = []

    i = 0
    while i + MIN_SEGMENTS - 1 < n:
        trio = confirmed[i : i + MIN_SEGMENTS]
        zg = min(s.high for s in trio)
        zd = max(s.low for s in trio)
        if zg <= zd:            # 前三段无重叠 → 此处不成枢，右移一段再试
            i += 1
            continue

        # 延伸：后续段只要仍与 [zd, zg] 重叠就并入（且不改变 zd/zg）
        j = i + MIN_SEGMENTS
        while j < n and _overlaps(confirmed[j], zd, zg):
            j += 1

        group = confirmed[i:j]
        end_idx = j - 1
        pivots.append(
            Pivot(
                idx=len(pivots),
                zg=zg,
                zd=zd,
                gg=max(s.high for s in group),
                dd=min(s.low for s in group),
                start_idx=i,
                end_idx=end_idx,
                start_ts=group[0].start.start.ts,
                end_ts=group[-1].end.end.ts,
                level=level,
                # 右端用尽可用线段 → 右侧还可能延伸，未定。
                status=(
                    Status.TENTATIVE
                    if end_idx == n - 1
                    else Status.CONFIRMED
                ),
                src_start=group[0].src_start,
                src_end=group[-1].src_end,
            )
        )
        # 离开段（下标 j）是下一个中枢的候选起点；若它与紧随的两段构成不了
        # 重叠，循环里的 i += 1 会继续右移，等价于「离开后回抽」的再寻找。
        i = j

    return pivots


def merge_pivots(pivots: Sequence[Pivot], level: str | None = None) -> list[Pivot]:
    """中枢扩展（第 20 课）：相邻且区间重叠的同级别中枢合并为高级别中枢。

    合并后的区间取两者的**重叠部分**：`zg = min(zg_a, zg_b)`，
    `zd = max(zd_a, zd_b)`；`gg`/`dd` 取并集的极值。只要有一个成分是
    `TENTATIVE`，合并结果就是 `TENTATIVE`（右端仍未定）。
    """
    out: list[Pivot] = []
    for p in pivots:
        prev = out[-1] if out else None
        base_level = level if level is not None else p.level
        if prev is not None and prev.level == base_level and _pivot_overlap(prev, p):
            out[-1] = replace(
                prev,
                idx=prev.idx,
                zg=min(prev.zg, p.zg),
                zd=max(prev.zd, p.zd),
                gg=max(prev.gg, p.gg),
                dd=min(prev.dd, p.dd),
                end_idx=p.end_idx,
                end_ts=p.end_ts,
                level=next_level(base_level),
                status=(
                    Status.CONFIRMED
                    if prev.status is Status.CONFIRMED
                    and p.status is Status.CONFIRMED
                    else Status.TENTATIVE
                ),
                confirmed_at=(
                    prev.confirmed_at
                    if prev.status is Status.CONFIRMED
                    and p.status is Status.CONFIRMED
                    else None
                ),
                src_end=p.src_end,
            )
        else:
            out.append(p)
    return out


def _pivot_overlap(a: Pivot, b: Pivot) -> bool:
    """两个中枢区间是否有真正的重叠（相切不算）。"""
    return min(a.zg, b.zg) > max(a.zd, b.zd)


def validate_pivots(pivots: Sequence[Pivot]) -> list[str]:
    """检查中枢的结构不变量，返回问题描述列表（空列表 = 全部通过）。

    这些不变量由 `find_pivots` 的定义直接推出，因此任何一条被违反都说明实现
    或调用方出了问题，而不是「缠论本身有歧义」：
    `dd <= zd < zg <= gg`、至少三段、线段下标连续且相邻中枢首尾相接。
    """
    problems: list[str] = []
    for i, p in enumerate(pivots):
        if p.zg <= p.zd:
            problems.append(f"pivot {i}: 中枢区间不成立（zg={p.zg} <= zd={p.zd}）")
        if p.dd > p.zd:
            problems.append(f"pivot {i}: dd={p.dd} 高于 zd={p.zd}")
        if p.zg > p.gg:
            problems.append(f"pivot {i}: zg={p.zg} 高于 gg={p.gg}")
        if p.end_idx < p.start_idx:
            problems.append(f"pivot {i}: 线段下标颠倒")
        if p.segment_count < MIN_SEGMENTS:
            problems.append(f"pivot {i}: 只有 {p.segment_count} 段，少于三段")
        if p.start_ts > p.end_ts:
            problems.append(f"pivot {i}: 时间区间颠倒")
        if p.src_end < p.src_start:
            problems.append(f"pivot {i}: 原始 bar 区间颠倒")
        if i and pivots[i - 1].end_idx + 1 != p.start_idx:
            # 中枢之间可以隔着离开段，但不得回溯或重叠
            if pivots[i - 1].end_idx >= p.start_idx:
                problems.append(f"pivot {i}: 与上一中枢的线段区间重叠")
    return problems
