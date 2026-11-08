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
    """一段走势类型。`start_idx`/`end_idx` 是中枢下标（闭区间）。

    `high`/`low`/`direction`/`src_start`/`src_end` 让它满足 `LevelSegment`
    协议，从而可以作为「次级别走势类型」喂给 `pivot.find_pivots` 去构造上一
    级别的中枢 —— 这就是第 84 课的 `f2(an)=an+1`。这几个字段在 D-51 之前
    不存在；加它们是**纯增量**，没有任何既有判据读它们。
    """

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
    #: 该走势类型自身的价格区间。有 bar 帧时是**原始 bar 的高低极值**，
    #: 否则退化为成分中枢的 `gg`/`dd` 并集（会低估：`gg`/`dd` 不含离开段，
    #: 见 D-37/D-38）。上一级中枢的 ZG/ZD 直接由这个区间算，低估会让上一级
    #: 中枢被撑大，所以生产链路必须传 `bars`。
    high: float = 0.0
    low: float = 0.0
    #: 1 向上 / -1 向下 / **0 无方向**。第 17 课只给趋势分上涨与下跌，盘整
    #: 自身没有方向 —— 所以这里如实记 0，不发明一个。`find_pivots` 会显式
    #: 处理「段没有方向」的情形（D-51）；给盘整硬编一个 ±1 被实测否决：
    #: 走势类型序列的相邻同向率 51%，硬编出来的方向等于掷骰子。
    direction: int = 0
    #: 覆盖的原始 bar 区间（含）。为了让上一级的 `find_pivots` 看到一段**连续
    #: 铺满时间轴**的段序列（线段的性质），这里的 `src_end` 会延伸到下一段走势
    #: 类型的起点之前，把两段之间那段「离开 + 回试」的连接走势也吃进来。
    src_start: int = 0
    src_end: int = 0


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


def classify_trends(pivots, level: str = "day", bars=None) -> list[Trend]:
    """把中枢序列切成走势类型。返回值按时间序完整覆盖入参中枢。

    只有 `CONFIRMED` 中枢参与；未被确认的中枢直接跳过（它们多半是窗口右端
    那一个，走势类型要等它定下来）。

    `bars` 是可选的原行情帧（`high`/`low` 两列、按原始 bar 顺序）。**传了它**
    才能算出走势类型的真实价格区间；不传就退化为成分中枢的 `gg`/`dd` 并集。
    既有调用方（`signal.py` / `divergence.py`）不传，行为与本函数引入这些
    字段之前逐字段相同 —— 分类与分组逻辑一个字没改。

    第 35 课给了这个分类的原文口径：

        有一个最低级别中枢的走势，就是最低级别的盘整走势类型；有两个最低级别
        中枢的走势，就是最低级别的趋势走势类型，如果第二个中枢比第一个高，那
        就是上涨走势类型，反之就是下跌走势类型。

    实测（`optimizer/tools/explore_recursion.py`，12 只沪市日线）这个分类的
    产物**方向并不交替**：`consolidation` 34 / `up` 8 / `down` 1，相邻对里
    `0->0` 有 20 对。所以「盘整」不能留 `direction=0`，否则把它喂给
    `find_pivots` 时 `zn` 会为空。这里按下面这条规则补方向（D-51 的口径选择）：
    """
    confirmed = [p for p in pivots if p.status is Status.CONFIRMED]

    # ---- 第一遍：分组（与引入方向/区间字段之前逐字相同）----
    groups: list[tuple[TrendType, list]] = []
    i = 0
    n = len(confirmed)
    while i < n:
        want = _direction(confirmed[i], confirmed[i + 1]) if i + 1 < n else 0
        if want != 0:
            j = i + 1
            while j + 1 < n and _direction(confirmed[j], confirmed[j + 1]) == want:
                j += 1
            groups.append(
                (TrendType.UP if want == 1 else TrendType.DOWN, confirmed[i : j + 1])
            )
            i = j + 1
        else:
            groups.append((TrendType.CONSOLIDATION, [confirmed[i]]))
            i += 1

    # ---- 第二遍：原始 bar 跨度 + 价格区间 ----
    # 跨度刻意**连续**：一段走势类型铺到下一段起点之前，把两段中枢之间那段
    # 「离开 + 回试」的连接走势吃进来。中枢自身的 `src_start`/`src_end` 只盖住
    # 构成它的段，中间会留洞；上一级的 `find_pivots` 需要的是「连续铺满时间轴」
    # 的段序列（线段就具备这个性质），留洞会让重叠判断失去意义。
    last_bar = None
    if bars is not None and len(bars):
        last_bar = len(bars) - 1
    spans: list[tuple[int, int]] = []
    for k, (_, group) in enumerate(groups):
        start = group[0].src_start
        end = group[-1].src_end
        if k + 1 < len(groups):
            nxt = groups[k + 1][1][0].src_start
            end = max(end, nxt - 1)
        if last_bar is not None:
            end = min(end, last_bar)
        spans.append((start, end))

    def _range(group, span):
        start, end = span
        if bars is not None and end >= start:
            seg = bars.iloc[start : end + 1]
            return float(seg["high"].max()), float(seg["low"].min())
        # 退化口径：中枢的 gg/dd 不含离开段（D-37/D-38），会低估区间。
        return (max(p.gg for p in group), min(p.dd for p in group))

    # ---- 第三遍：价格区间与方向 ----
    # 方向**不发明**：第 17 课只把走势分成趋势（上涨/下跌）与盘整，只有趋势有
    # 方向。盘整留 0，由 `find_pivots` 显式处理「段没有方向」的情形（D-51）。
    #
    # 早先试过「给盘整按区间中点漂移补一个 ±1」，被否决：实测走势类型序列的
    # 相邻同向率只有 51%（`optimizer/tools/measure_recursion.py`），
    # `find_pivots` 那句「相邻确认段方向必然交替」对走势类型根本不成立，硬编
    # 一个方向只会让 Z 走势段的挑选变成掷骰子，还不如如实说「它没有方向」。
    highs = [0.0] * len(groups)
    lows = [0.0] * len(groups)
    for k, (_, group) in enumerate(groups):
        highs[k], lows[k] = _range(group, spans[k])

    out: list[Trend] = []
    for k, (kind, group) in enumerate(groups):
        status, confirmed_at = _status_of(group)
        out.append(
            Trend(
                idx=k,
                kind=kind,
                start_idx=group[0].idx,
                end_idx=group[-1].idx,
                start_ts=group[0].start_ts,
                end_ts=group[-1].end_ts,
                level=level,
                pivot_count=len(group),
                status=status,
                confirmed_at=confirmed_at,
                high=highs[k],
                low=lows[k],
                direction={TrendType.UP: 1, TrendType.DOWN: -1}.get(kind, 0),
                src_start=spans[k][0],
                src_end=spans[k][1],
            )
        )
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
