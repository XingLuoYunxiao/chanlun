"""三类买卖点（第 17—20 课为结构，第 24—28 课为动力学）。

三类买卖点的结构定义
--------------------
- **第一类买点**：下跌趋势中出现**背驰**，即趋势的最后一个中枢之后再创新低，
  但这一波下跌的力度（MACD 面积）比前一波同向走势小。第 24 课把「趋势背驰」
  与「盘整背驰」分开，本实现的第一类买卖点只做**趋势背驰**（至少两个中枢依次
  下降）；盘整背驰另设 `pb`/`ps` —— 第 60 课：
  「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」。
- **第二类买点**：第一类买点之后，次级别回抽**不破**第一类买点的低点。
- **第三类买点**：向上**离开中枢**后，次级别回抽**不回到中枢区间**（低点 > ZG）。
  卖点全部对称。「离开」是**位置**：把价格带出区间的那一段是中枢组的最后一段
  `segs[p.end_idx]`，回抽是紧随其后的 `segs[p.end_idx + 1]`（第 20 课）。
  这个位置口径只在**中枢确实被离开段封闭**时成立，所以 `capped` 的中枢
  （段数上限掐停、根本没有离开段）一律不产出第三类（D-41）。

可靠性约定
----------
- 只吃 `CONFIRMED` 线段；中枢必须是**已封口**的（`capped is False`，D-41）。
  触发信号的那一段若还是窗口右端的未确认尾段，信号标 `TENTATIVE`
  （`confirmed_at=None`），回测会把它挡在外面。
- 所有力度比较只用 MACD（通达信口径，见 `macd.py`），不做凭观感的近似。

已知的进一步细化空间（留给优化师按原文重新推导）：
1. 第 39 课口径的**盘整背驰**（`divergence.py`，同向的 `Ai` 与 `Ai+2` 比力度）
   只在非严格模式（`SignalMode.LOOSE`）下产出 `pb`/`ps`；严格模式不产出
   （D-32 / D-33）。第 24 课按一个中枢前后两段比较力度的口径仍未单独实现；
2. 第二类买卖点原文要求次级别回抽，本实现用**本级别下一段**近似，
   严格做法是下钻到次级别（30 分钟）去看回抽内部结构；
3. 第 27、28 课讲的第一类买卖点区间套定位（多级别联立）没有实现。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Sequence

import pandas as pd

from .divergence import DivergenceKind, find_divergences
from .macd import hist_area, macd
from .pivot import Pivot
from .segment import Segment
from .trend import TrendType, classify_trends
from .types import Status


class SignalMode(str, Enum):
    """买卖点口径。

    **只影响买卖点与背驰标注**，不影响笔 / 线段 / 中枢的划分（D-32）。
    禅师对划分从未给出宽松版本（第 67/71/78/79 课），对判读则有分层 ——
    第 60 课：「站在最严格意义上」……
    同课：「当然，这是按最严格的，并没有太大操作意义的分析。」
    """

    STRICT = "strict"
    LOOSE = "loose"

    @property
    def name_cn(self) -> str:
        return "严格" if self is SignalMode.STRICT else "非严格"


# `THIRD_TOL` 已删除（2026-10-04，D-41）。
#
# 它曾是第三类买卖点的回试容忍度（中枢高度 `(ZG - ZD)` 的 10%，只在
# `SignalMode.LOOSE` 下生效），标注为「工程口径，无原文依据」。
# 删除理由不是「工程口径不好」，而是它**实际翻出来的信号与第 20 课正面冲突**：
# 全市场日线实测（5440 票）非严格独有的第三类信号 **99 条**，其中
# **44 条**挂在中枢段数被 `MAX_SEGMENTS` 掐停的中枢上（那里**根本没有离开段**，
# 由下方 `_third_kind` 的 `capped` 收口拦掉），**55 条**挂在**尚未封口**的中枢
# 尾段上（`Pivot.status is TENTATIVE`，回试段就是最后那一段、且仍与 `[ZD, ZG]`
# 重叠）—— 这 55 条在严格口径下本来就不成立，容忍度一删就没了。
# 两类都违背第 20 课「其低点不跌破ZG」「必须是第一次」。
# ★ 上面 99 / 44 / 55 这组数字测自**删除 `THIRD_TOL` 之前**的代码，探针是一次性
#   脚本、未入库，因此**无法从当前主干复现**；它只是删除决策的输入，不是可复现证据。
#   可复现的那组数字在 `docs/evidence/2026-10-04-capped-signal-gate.md`。
# 它原本的辩护是「补偿段数上限截断」——`capped` 收口后这个辩护不复存在。
# 见 `optimizer/theory/L20-THIRD-TOLERANCE.md`、
# `ARCHITECTURE.md` 的 D-35「变更历史」与 D-41。


class SignalKind(str, Enum):
    """买卖点类型。值取原文三类买卖点的序号：第 n 类 → `bn` / `sn`。"""

    B1 = "b1"
    B2 = "b2"
    B3 = "b3"
    S1 = "s1"
    S2 = "s2"
    S3 = "s3"
    #: 盘整背驰买点 = 第 027 课第 7 段的「类第一类买点」（盘整背驰**点本身**）。
    #: 第 060 课 L45 同样把 55 这个盘整背驰点类比成第一类买点、把 57 这个回抽
    #: 类比成第二类买点。但第 60 课先说了「严格来说，盘整背驰无所谓第一类买点，
    #: 只是这样来类比」⇒ 只能是**限定过的**「类第一类」，独立类型，不得并入 b1。
    PB = "pb"
    #: 盘整背驰卖点，对称。
    PS = "ps"

    @property
    def is_buy(self) -> bool:
        return self.value in ("b1", "b2", "b3", "pb")

    @property
    def name_cn(self) -> str:
        if self is SignalKind.PB:
            return "盘整背驰买点（类第一类）"
        if self is SignalKind.PS:
            return "盘整背驰卖点（类第一类）"
        n = {"1": "一", "2": "二", "3": "三"}[self.value[1]]
        return f"第{n}类买点" if self.is_buy else f"第{n}类卖点"


@dataclass(frozen=True)
class Signal:
    """一个买卖点。`price` 是触发点的价格（买点取低点，卖点取高点）。"""

    idx: int
    kind: SignalKind
    ts: str
    price: float
    level: str
    pivot_idx: int | None = None
    reason: str = ""
    status: Status = Status.TENTATIVE
    confirmed_at: str | None = None
    src_start: int = 0
    src_end: int = 0

    @property
    def is_buy(self) -> bool:
        return SignalKind(self.kind).is_buy


def _area(macd_df: pd.DataFrame | None, seg: Segment) -> float | None:
    """触发段的 MACD 面积，颜色取该段自身方向（第 24 课「向上的看红柱子，
    向下看绿柱子」）：向上段 `+1` 只算红柱、向下段 `-1` 只算绿柱。"""
    if macd_df is None:
        return None
    try:
        return hist_area(macd_df, seg.src_start, seg.src_end, seg.direction)
    except ValueError:
        return None


def _dead(seg: Segment) -> bool:
    """已作废的线段不参与信号。

    下标必须按**入参列表**对齐（`Pivot.end_idx` 是入参列表的下标），所以这里
    不能在过滤后的列表上取段，只能在取到之后判废。
    """
    return seg.status is Status.INVALIDATED


def _seal(sig: Signal, seg: Segment) -> Signal:
    """触发段确认了，信号才算确认；否则标 TENTATIVE。"""
    if seg.status is Status.CONFIRMED and seg.confirmed_at is not None:
        return replace(sig, status=Status.CONFIRMED, confirmed_at=seg.confirmed_at)
    return replace(sig, status=Status.TENTATIVE, confirmed_at=None)


def _sig(kind: SignalKind, seg: Segment, level: str, pivot_idx: int | None,
         reason: str) -> Signal:
    price = seg.low if SignalKind(kind).is_buy else seg.high
    return _seal(
        Signal(
            idx=0, kind=kind, ts=seg.end.end.ts, price=price, level=level,
            pivot_idx=pivot_idx, reason=reason,
            src_start=seg.src_start, src_end=seg.src_end,
        ),
        seg,
    )


def find_signals(
    bars: pd.DataFrame,
    segments: Sequence[Segment],
    pivots: Sequence[Pivot],
    level: str = "day",
    macd_df: pd.DataFrame | None = None,
    mode: SignalMode = SignalMode.STRICT,
) -> list[Signal]:
    """按结构 + 力度找出全部买卖点，按时间排序后统一编号。

    `segments` 必须与传给 `find_pivots` 的是**同一个列表**：`Pivot.end_idx`
    是那个列表的下标，少一段都会让离开段/回试段整体错位。

    `mode` 只放宽买卖点判据，不改结构划分（D-32）。严格模式的结果与不传
    `mode` 时逐项相同。

    `mode` 接受 `SignalMode` 成员，也接受等值字符串 `"strict"` / `"loose"`
    —— CLI 的 `--mode`、HTTP 查询参数、前端拼的 URL 传进来的都是字符串。
    入口处统一归一化，非法值抛 `ValueError`；内部私有函数拿到的必定是成员。
    """
    mode = SignalMode(mode)  # 允许传字符串（CLI / HTTP 查询参数 / 前端）
    segs = list(segments)
    if macd_df is None and bars is not None and len(bars) > 0:
        macd_df = macd(bars["close"])

    out: list[Signal] = []
    out.extend(_third_kind(segs, pivots, level))
    out.extend(_first_kind(segs, pivots, level, macd_df))
    divs: tuple[Any, ...] = ()
    if mode is SignalMode.LOOSE:
        # 引擎会另算一份给 `Snapshot.divergences` 用；这里自己算是为了让
        # `find_signals` 保持可独立调用（测试、扫描器都不经过引擎）。
        divs = find_divergences(bars, segs, pivots, level, macd_df)
        out.extend(_consolidation_kind(divs, segs, level))
    out.extend(_second_kind(out, segs, level, mode, divs))

    out.sort(key=lambda s: (s.ts, s.kind.value))
    return [replace(s, idx=i) for i, s in enumerate(out)]


def _third_kind(segs: list[Segment], pivots: Sequence[Pivot],
                level: str) -> list[Signal]:
    """第三类买卖点：离开中枢后回抽不回中枢（第 20 课，判据是**位置**）。

    第 20 课原文：「一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个
    次级别走势类型回试，其低点不跌破ZG，则构成第三类买点」——判定标准是价格
    与中枢区间的比较，不是线段自己的方向。

    离开段 = **把价格带出中枢区间的那一段** = 中枢组的最后一段
    `segs[p.end_idx]`：它的起点还在区间里（所以按「有重叠」被并进了中枢），
    终点已经在 ZG 之上。回试段 = 紧随其后的 `segs[p.end_idx + 1]`。

    为什么不能取 `segs[p.end_idx + 1]` 当离开段：真实线段首尾相连（相邻两段
    端点价格与时间戳完全重合），中枢最后一段之后的这一段**必然是反向回抽段**
    —— 它若整段在 ZG 之上，方向必然向下。于是按离开段方向向上且低点 > ZG
    写的判据在真实数据上恒不成立，本函数曾经在 148 只票 / 193 个中枢上产出 0 个信号，
    而在合成用例上通过，只因为那些合成线段是断开的（相邻段之间留了缺口）。

    **前置条件（D-41）：中枢必须 `capped is False`。**

    位置口径只在「整段不碰 `[ZD, ZG]` 的下一段封口」时成立 —— 那才是离开段。
    `find_pivots` 的延伸循环有两个退出理由，只有一个能证明离开段存在：

    - 退出理由是「`confirmed[j]` 不碰 `[ZD, ZG]`」⇒ `end_idx` 是离开段，`end_idx+1`
      是回试段。**这是唯一合法的分支**，等价于 `not capped and back is not None`。
    - 退出理由是「段数上限掐停」（`capped is True`）⇒ 组内每一段（含 `end_idx`）
      都仍与区间重叠，本级别**不存在**离开段。第 20 课定理一「走势中枢的延伸等价于
      任意区间[dn，gn]与[ZD，ZG]有重叠」；第 33 课说凑满 9 段就已经是更大级别的
      中枢了，而本项目**不做级别递归** ⇒ 正确做法是不产出，不是拿延伸段冒充离开段。
    - 退出理由是「用尽确认段」（`p.status is TENTATIVE`，`j == n`）⇒ 离开段
      **未被证伪、也未被证实**。此时回试段必然还是窗口右端的未确认尾段，`_seal`
      会把信号标成 `TENTATIVE`、回测挡住它 —— 这正是 `Status.TENTATIVE` 的用途，
      所以这里**故意不拦**（见 D-41「被否决的替代方案」）。

    **这一处的 `capped` 门是结构性 no-op（据实记录，不是「已验证」）。**
    `capped` 的定义本身就要求 `end_idx + 1` 与 `[ZD, ZG]` 重叠，所以买侧
    `back.low > ZG`（卖侧 `back.high < ZD`）在 capped 中枢上**恒不成立** ——
    把这一行改成 `if False and p.capped:` 重跑，全市场 5440 票的判据一输出逐项
    不变（实测 `--limit 600` 亦然）。留它的理由是**口径**而不是当下的产出：
    它把「capped 中枢的 `end_idx` 不是离开段」写成可执行的不变量，否则将来任何
    一次「放宽第三类判据」都会悄悄把延伸中的中枢重新误报成第三类（`THIRD_TOL`
    就是这么来的）。真正有产出差异的是 `_entering_and_leaving` / `_leaving_legs`
    两处（趋势背驰 180 → 116、第一类买点 137 → 91）。
    """
    out: list[Signal] = []
    for p in pivots:
        if p.capped:
            continue
        leave_i, back_i = p.end_idx, p.end_idx + 1
        if back_i >= len(segs):
            continue
        leave, back = segs[leave_i], segs[back_i]
        if _dead(leave) or _dead(back):
            continue
        if leave.direction == 1 and leave.high > p.zg:
            if back.direction == -1 and back.low > p.zg:
                out.append(_sig(
                    SignalKind.B3, back, level, p.idx,
                    f"向上离开中枢{p.idx}(ZG={p.zg:.3f})后回抽低点 {back.low:.3f} 不回中枢",
                ))
        elif leave.direction == -1 and leave.low < p.zd:
            if back.direction == 1 and back.high < p.zd:
                out.append(_sig(
                    SignalKind.S3, back, level, p.idx,
                    f"向下离开中枢{p.idx}(ZD={p.zd:.3f})后回抽高点 {back.high:.3f} 不回中枢",
                ))
    return out


def _entering_and_leaving(segs: list[Segment], pivots: Sequence[Pivot],
                          want: int) -> list[tuple[Pivot, Segment, Segment | None]]:
    """每个中枢的「离开段」以及上一个中枢的「离开段」，用于背驰比较。

    离开段同样取**位置**口径：中枢组的最后一段 `segs[p.end_idx]`，也就是把
    价格带出中枢区间、创出新极值的那一段 —— 第 24 课比较力度的对象正是它。
    取 `segs[p.end_idx + 1]` 会把回抽段当离开段，方向必然与趋势相反，
    于是 `leave.direction != want`，第一类买卖点永远不会触发。

    `capped` 的中枢（第 33 课段数上限掐停）**跳过**：那里 `end_idx` 只是仍在枢内
    的延伸段，拿它当离开段去比 MACD 力度就是比错了对象（D-41）。
    与 `divergence.py::_leaving_legs` 逐条相同，两处必须同步改。

    **守卫在哪**：`test_trend_divergence_matches_b1_s1` 的双向相等断言**量不到**
    这个门（夹具的 4 只票上没有落在 capped 中枢上的趋势背驰，实测去掉任一处门它
    仍然全绿）。真正咬住这个分支的是
    `test_divergence.py::test_capped_pivot_is_skipped_by_both_leaving_leg_implementations`
    —— 它对两份实现各断言一次，去掉任一处门都会在对应的那行红。
    """
    out = []
    for k, p in enumerate(pivots):
        if p.capped:
            continue
        leave = segs[p.end_idx]
        if _dead(leave) or leave.direction != want:
            continue
        prev = None
        if k > 0:
            cand = segs[pivots[k - 1].end_idx]
            if not _dead(cand) and cand.direction == want:
                prev = cand
        out.append((p, leave, prev))
    return out


def _first_kind(segs: list[Segment], pivots: Sequence[Pivot], level: str,
                macd_df: pd.DataFrame | None) -> list[Signal]:
    """第一类买卖点：趋势背驰（创新极值 + 力度衰竭）。"""
    out: list[Signal] = []
    trends = classify_trends(pivots, level)
    down_pivots = [p for t in trends if t.kind is TrendType.DOWN
                   for p in pivots[t.start_idx:t.end_idx + 1]]
    up_pivots = [p for t in trends if t.kind is TrendType.UP
                 for p in pivots[t.start_idx:t.end_idx + 1]]

    for want, kind, group in ((-1, SignalKind.B1, down_pivots),
                              (1, SignalKind.S1, up_pivots)):
        if len(group) < 2:
            continue
        for p, leave, prev in _entering_and_leaving(segs, group, want):
            if prev is None or macd_df is None:
                continue
            if want == -1 and not leave.low < prev.low:
                continue
            if want == 1 and not leave.high > prev.high:
                continue
            a_now, a_prev = _area(macd_df, leave), _area(macd_df, prev)
            if a_now is None or a_prev is None or not a_now < a_prev:
                continue
            where = "新低" if want == -1 else "新高"
            out.append(_sig(
                kind, leave, level, p.idx,
                f"趋势背驰：{leave.end.end.ts} 创{where} {leave.low if want == -1 else leave.high:.3f}，"
                f"MACD 面积 {a_now:.4f} < 前一同向走势 {a_prev:.4f}",
            ))
    return out


def _consolidation_kind(divs: Sequence[Any], segs: list[Segment],
                        level: str) -> list[Signal]:
    """盘整背驰买卖点（第 39 课）。

    第 60 课硬约束：「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」
    ⇒ 独立类型 `pb`/`ps`，绝不并入 `b1`/`s1`。

    第 39 课的判据是「只理会一点，就是Ai与Ai+2之间是否盘整背驰」——盘整背驰
    不需要先有趋势，所以它是**独立入口**，不是第一类买卖点的放宽（第 15 课
    「在盘整中是无所谓“背驰”的」：不带定语的「背驰」专指趋势背驰）。
    """
    out: list[Signal] = []
    for d in divs:
        if d.kind is not DivergenceKind.CONSOLIDATION:
            continue
        if not (0 <= d.seg_idx < len(segs)):
            continue
        kind = SignalKind.PB if d.direction == -1 else SignalKind.PS
        out.append(_sig(kind, segs[d.seg_idx], level, d.pivot_idx,
                        f"盘整背驰：{d.ts} MACD 面积 {d.area_now:.4f} < "
                        f"同向前段 {d.area_prev:.4f}"))
    return out


def _second_kind(existing: list[Signal], segs: list[Segment], level: str,
                 mode: SignalMode = SignalMode.STRICT,
                 divs: Sequence[Any] = ()) -> list[Signal]:
    """第二类买卖点：第一类买卖点之后的次级别回抽不破前极值。"""
    out: list[Signal] = []
    for first in existing:
        want_first = SignalKind(first.kind)
        if want_first not in (SignalKind.B1, SignalKind.S1):
            continue
        k = next((i for i, s in enumerate(segs)
                  if s.src_start == first.src_start and s.src_end == first.src_end), None)
        if k is None or k + 2 >= len(segs):
            continue
        bounce, back = segs[k + 1], segs[k + 2]
        if want_first is SignalKind.B1:
            if bounce.direction == 1 and back.direction == -1 and back.low > first.price:
                out.append(_sig(
                    SignalKind.B2, back, level, first.pivot_idx,
                    f"第一类买点 {first.ts} 后回抽低点 {back.low:.3f} 不破 {first.price:.3f}",
                ))
        else:
            if bounce.direction == -1 and back.direction == 1 and back.high < first.price:
                out.append(_sig(
                    SignalKind.S2, back, level, first.pivot_idx,
                    f"第一类卖点 {first.ts} 后回抽高点 {back.high:.3f} 不破 {first.price:.3f}",
                ))
    if mode is SignalMode.LOOSE:
        # 第 27 课《盘整背驰与历史性底部》：
        # 「类似的，在大级别里，如果不出现新低，但可以构成类似第二类买点的买点」
        # ⇒ 盘整背驰可以**不经过第一类**直接给出类第二类买点。这是原文自己的
        # 第二条入口，不是对第 101 课定义的重写。
        for d in divs:
            if d.kind is not DivergenceKind.CONSOLIDATION:
                continue
            k = d.seg_idx
            if k + 2 >= len(segs):
                continue
            bounce, back = segs[k + 1], segs[k + 2]
            if (d.direction == -1 and bounce.direction == 1
                    and back.direction == -1 and back.low > d.price):
                out.append(_sig(
                    SignalKind.B2, back, level, d.pivot_idx,
                    f"盘整背驰 {d.ts} 后回抽低点 {back.low:.3f} 不破 {d.price:.3f}",
                ))
            elif (d.direction == 1 and bounce.direction == -1
                    and back.direction == 1 and back.high < d.price):
                out.append(_sig(
                    SignalKind.S2, back, level, d.pivot_idx,
                    f"盘整背驰 {d.ts} 后回抽高点 {back.high:.3f} 不破 {d.price:.3f}",
                ))
    return out


def validate_signals(signals: Sequence[Signal], bars: pd.DataFrame | None = None,
                     segments: Sequence[Segment] | None = None) -> list[str]:
    """买卖点的结构不变量校验，供测试与审计脚本调用。"""
    problems: list[str] = []
    tss = set(bars["ts"]) if bars is not None else None
    for i, s in enumerate(signals):
        if i != s.idx:
            problems.append(f"signal {i}: idx 不连续({s.idx})")
        if tss is not None and s.ts not in tss:
            problems.append(f"signal {i}: 时间 {s.ts} 不是真实 bar")
        if s.status is Status.CONFIRMED and s.confirmed_at is None:
            problems.append(f"signal {i}: 已确认却没有确认时间")
        if s.status is Status.TENTATIVE and s.confirmed_at is not None:
            problems.append(f"signal {i}: 未确认却有确认时间")
        if s.confirmed_at is not None and s.confirmed_at < s.ts:
            problems.append(f"signal {i}: 确认时间早于触发时间")
        if s.price <= 0:
            problems.append(f"signal {i}: 价格非正")
        if s.src_end < s.src_start:
            problems.append(f"signal {i}: 原始 bar 跨度颠倒")
    for prev, nxt in zip(signals, signals[1:]):
        if nxt.ts < prev.ts:
            problems.append(f"signal {nxt.idx}: 时间倒序")
    return problems
