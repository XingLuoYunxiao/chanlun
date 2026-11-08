"""级别递归：`f2(an) = an+1`（第 17 / 35 / 63 / 84 / 102 课）。

为什么需要这个模块
------------------
`pivot.find_pivots` 一直在用**本级别的线段**直接构造中枢：

    engine._compute:  merged → fractals → strokes → segments → find_pivots(segments)

而原文的中枢定义是递归的。第 17 课：

    某级别走势类型中，被至少三个连续**次级别**走势类型所重叠的部分，称为
    缠中说禅走势中枢。……这里有一个递归的问题。

第 63 课把它说成一条构造链：

    有了线段，就可以定义 1 分钟的中枢，然后就是 1 分钟的走势类型，然后按照
    递归的方法，可以逐步定义 5 分钟、30 分钟、日、周、月、季度、年的中枢和
    走势类型。

也就是说，**只有最低一级的中枢才由线段构造**；再往上，本级别中枢的输入是
「次级别走势类型」，不是线段。旧代码把每个周期的线段各自构一遍中枢，等于把
递归在第一步就截断了：它算出的「日线中枢」其实是**日线图上的最低级别中枢**，
不是日线级别中枢。第 63 课对这件事有一句很硬的话：

    什么级别的图和什么级别的中枢没有任何必然关系……所以，不能把显微镜和
    显微镜观察的东西混在一起了。

第 84 课把递归拆成两半，这是本模块的骨架：

    一般的递归定义，由两部分组成，一、f1(a0)=a1；二、f2(an)=an+1；关于第二条
    的中枢过程规则，是一直没有任何改变的，而关于第一条，其实，可以随意设置
    任何的，都不会改变中枢定义的递归性。

- **f1** = `线段 → 最低级别中枢 → 最低级别走势类型`。`find_pivots` +
  `classify_trends` 已经做到了，本模块照用。
- **f2** = `走势类型(n) → 中枢(n+1) → 走势类型(n+1)`。本模块要补的就是它。

「过程规则一直没有任何改变」这句话是**可以直接落到代码上**的：`find_pivots`
吃的是 `LevelSegment` 协议而不是 `Segment` 具体类型，所以 f2 复用的就是 f1
用的同一个函数，没有第二份平行实现（见 `types.LevelSegment`）。

f2 的原文依据是第 35 课：

    三个连续的最低级别走势类型之间，如果发生重叠关系，也就是三个最低级别走势
    类型所分别经过的价格区间有交集，那么就形成了高一级别的缠中说禅中枢。……
    而这个过程可以逐级上推。

已知口径选择（不假装与原文完全一致）
------------------------------------
1. **级别名沿用 `LEVEL_UP` 的 `5→30→day→week→month`。** 第 102 课说得很清楚：
   「这些级别的名字是可以随意取的」。名字只是**相对于基准显微镜的递归深度**，
   不是「这张图对应的中枢级别」——后者正是第 63 课否掉的读法。
2. **只在 `CONFIRMED` 段上递归**（沿用 `find_pivots` 的既有门）。未确认的右端
   尾段不参与，上一级结构因此会晚一级出现，这是刻意的保守。
3. **走势类型的方向不是原文概念。** 原文只对趋势分上涨/下跌，「盘整」自身无
   方向；但 `find_pivots` 要靠 `direction` 挑 Z 走势段算 GG/DD，方向为空会崩。
   补齐规则与实测依据见 `trend.classify_trends` 与 D-51。
4. **中枢级别扩张（第 20 课）走的是另一条路。** `merge_pivots` 处理的是「两个
   同级别中枢的波动区间重叠 → 合成高级别中枢」，与本模块「三个次级别走势类型
   重叠」是两条独立机制。本模块只实现后者，前者仍是既有函数、仍未被生产链路
   调用（见 README「已知空白」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .pivot import Pivot, find_pivots, next_level
from .trend import Trend, classify_trends
from .types import LevelSegment, Status

#: 默认基准级别：本机行情库里最细的周期是 5 分钟。
DEFAULT_BASE_LEVEL = "5"

#: 单次递归最多升几级，防病态输入下无限上推。
MAX_DEPTH = 6


@dataclass(frozen=True)
class LevelStructure:
    """某一递归级别上的完整结构：中枢 + 由这些中枢切出的走势类型。

    `depth` 是**相对基准显微镜**的递归层数：`depth=0` 是线段直接构成的
    最低级别（f1 的 a1），`depth=k` 是 `f2` 迭代 k 次的结果。
    """

    level: str
    depth: int
    pivots: tuple[Pivot, ...]
    trends: tuple[Trend, ...]

    @property
    def confirmed_pivots(self) -> tuple[Pivot, ...]:
        return tuple(p for p in self.pivots if p.status is Status.CONFIRMED)

    @property
    def confirmed_trends(self) -> tuple[Trend, ...]:
        return tuple(t for t in self.trends if t.status is Status.CONFIRMED)


def level_chain(base_level: str, max_depth: int = MAX_DEPTH) -> list[str]:
    """从基准级别出发的级别名链，按递归深度升序。未知级别自动截断。"""
    chain = [base_level]
    while len(chain) < max_depth:
        nxt = next_level(chain[-1])
        if nxt.endswith("+"):       # `next_level` 对未知级别退化，到此为止
            break
        chain.append(nxt)
    return chain


def build_levels(
    segments: Sequence[LevelSegment],
    bars=None,
    base_level: str = DEFAULT_BASE_LEVEL,
    max_depth: int = MAX_DEPTH,
) -> list[LevelStructure]:
    """把最低级别线段逐级上推成各级别中枢与走势类型。

    `segments` 是最低级别（`base_level` 对应那张图）的线段；`bars` 是同一张图的
    原始 bar 帧，用来算走势类型的真实价格区间（不传会低估上一级中枢，见
    `trend.Trend.high` 的说明）。

    返回按 `depth` 升序的列表，第一个元素一定是 `depth=0`。**遇到空级别就停**：
    一个级别如果连一个中枢都构不出来，再往上没有输入，硬推只会产出空壳。
    """
    chain = level_chain(base_level, max_depth)
    out: list[LevelStructure] = []

    pivots = find_pivots(segments, chain[0])
    trends = classify_trends(pivots, chain[0], bars=bars)
    out.append(LevelStructure(chain[0], 0, tuple(pivots), tuple(trends)))

    for depth, lv in enumerate(chain[1:], start=1):
        if not trends:
            break
        # f2：输入是**上一级的走势类型**。同一个 find_pivots，没有第二份实现。
        pivots = find_pivots(trends, lv)
        if not pivots:
            break
        trends = classify_trends(pivots, lv, bars=bars)
        out.append(LevelStructure(lv, depth, tuple(pivots), tuple(trends)))

    return out


def validate_levels(levels: Sequence[LevelStructure]) -> list[str]:
    """递归结构的不变量校验，返回问题描述列表（空 = 全过）。

    这些不变量直接来自定义，任何一条被违反都说明实现或调用方出了问题，
    而不是「缠论本身有歧义」：

    1. **深度从 0 连续递增、级别名严格上升**（`level_chain` 的直接推论）。
    2. **第 35 课**：「5 分钟走势类型，必须包含也最多包含 5 分钟级别中枢」——
       本级别走势类型只能由本级别中枢切出来。代码里 `classify_trends` 只吃
       本级中枢，所以这条是结构性的；这里显式校验一遍，防止以后有人拿错列表。
    3. **走势类型的 `level` 必须等于所在级别的名字**（同上，防串级）。
    4. **上一级中枢的区间由下一级走势类型算出来**：`depth>=1` 的每个中枢都要
       落在某些下级走势类型的区间交集里。这一条是 f2 的实质，别的都只是记账。
    """
    problems: list[str] = []
    for k, lv in enumerate(levels):
        if lv.depth != k:
            problems.append(f"{lv.level}: depth={lv.depth}，应为 {k}（深度必须连续）")
        for t in lv.trends:
            if t.level != lv.level:
                problems.append(
                    f"{lv.level}: 走势类型 #{t.idx} 的 level={t.level}，与本级不符"
                )
            if t.pivot_count < 1:
                problems.append(f"{lv.level}: 走势类型 #{t.idx} 不含任何中枢")
            span = t.end_idx - t.start_idx + 1
            if t.pivot_count != span:
                problems.append(
                    f"{lv.level}: 走势类型 #{t.idx} 声明 {t.pivot_count} 个中枢，"
                    f"但下标跨度是 {span}"
                )
        for t in lv.trends:
            if t.end_idx >= len(lv.pivots) or t.start_idx < 0:
                problems.append(
                    f"{lv.level}: 走势类型 #{t.idx} 的中枢下标越界"
                    f"（{t.start_idx}..{t.end_idx}，本级共 {len(lv.pivots)} 个）"
                )
        if lv.pivots and lv.trends:
            if lv.trends[0].start_idx != 0:
                problems.append(f"{lv.level}: 首个走势类型没有从第 0 个中枢开始")

    for prev, cur in zip(levels, levels[1:]):
        if cur.depth != prev.depth + 1:
            problems.append(f"{cur.level}: 深度 {cur.depth} 不接在 {prev.level} 之后")
        if not prev.trends and cur.pivots:
            problems.append(f"{cur.level}: 上一级没有走势类型，却构出了中枢")

    # 不变量 4（f2 的实质）：上一级每个中枢的 [ZD,ZG] 必须**恰好**等于它吃进去的
    # 前三个下级走势类型区间的交集 —— `zg = min(high)`、`zd = max(low)`。
    #
    # 这是本模块最重要的一条护栏。它同时钉住了三件事：中枢确实是由下级走势类型
    # （而不是线段或别的什么）构出来的、`start_idx/end_idx` 与 `trends` 列表对齐、
    # 以及走势类型的 `high`/`low` 没有被悄悄换成一个不匹配的口径。
    tol = 1e-6
    for prev, cur in zip(levels, levels[1:]):
        for p in cur.pivots:
            if p.end_idx >= len(prev.trends):
                problems.append(
                    f"{cur.level}: 中枢 #{p.idx} 的段下标 {p.end_idx} 超出"
                    f"{prev.level} 走势类型数 {len(prev.trends)}"
                )
                continue
            members = list(prev.trends[p.start_idx : p.end_idx + 1])
            if len(members) < 3:
                problems.append(
                    f"{cur.level}: 中枢 #{p.idx} 只由 {len(members)} 个"
                    f"{prev.level} 走势类型构成，少于 3 个"
                )
                continue
            trio = members[:3]
            exp_zd = max(t.low for t in trio)
            exp_zg = min(t.high for t in trio)
            if abs(p.zd - exp_zd) > tol or abs(p.zg - exp_zg) > tol:
                problems.append(
                    f"{cur.level}: 中枢 #{p.idx} 的 [ZD,ZG]=[{p.zd},{p.zg}] "
                    f"不等于下级前三个走势类型的交集 [{exp_zd},{exp_zg}]"
                )
    return problems
