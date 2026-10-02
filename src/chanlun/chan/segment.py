"""线段（第 67 课定型，第 71/77/78/81 课修补）。

原文要点（本地 108 课整理，§4.2）：
- 线段**至少三笔**，且**开始三笔必须有重叠**；线段不可能顶到顶/底到底，
  因此**线段包含的笔数必为单数**。
- 以向上笔开始的线段，取其**向下笔**为**特征序列**；向下线段取向上笔。
- 相邻两个特征序列元素**无重合区间即为缺口**。
- 对特征序列做包含处理后得到**标准特征序列**。
- **第一种情况**（特征序列分型的第一、二元素之间**没有缺口**）→ 线段在该分型的高/低点结束。
- **第二种情况**（**有缺口**）→ 还要考察「从该分型极值点开始的下一笔」所构成的
  第二特征序列是否出现反向分型，出现了才确认结束。
- 第二特征序列**不再分第一/第二种情况**，只要有分型即可，且**必须严格按包含关系处理**（第 78 课）。

包含处理的合并方向：向上线段的特征序列按向上规则（取高高），向下线段按向下规则（取低低）。
这是**原文空白项**：第 67 课只给出特征序列的定义与「对特征序列做包含处理」这一步，
第 71 课给的是分界点判定程序，第 78 课只说第二特征序列「必须严格按照包含关系的处理来」，
三处都没有写到公式级的合并方向。故本模块把它当作**显式声明的实现约定**（按线段方向取
高低）；社区另有「按特征序列自身方向」的读法，两说并存，原文未裁决。

注意：线段划分是全书修订最多、分歧最大的一层。第 67 课「一切同一级别图上的走势都可以
**唯一地**划分为线段的连接，这是基础的基础，请务必搞清楚」、第 77 课「用线段划分的两种
情况的规定，不难证明，线段的划分也是唯一的」、第 78 课「根据这两种情况的完全分类来，
**没有不能唯一去划分的**」三处都宣示唯一性；第 81 课正文则是对第 71 课末图的官方更正
（「有人提到 71 课里最后一个图，那个图显然是错的」）。所以划分规则本身**不是**可以随便
设计的自由项；本模块通过 `SegmentPolicy` 协议把实现外置，`build_segments` 内不含硬编码规则。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from .types import Segment, SegmentBreak, Status, Stroke

MIN_STROKES = 3


@runtime_checkable
class SegmentPolicy(Protocol):
    """线段划分策略。"""

    name: str

    def classify(self, strokes: Sequence[Stroke]) -> list[SegmentBreak]:
        """返回「线段在此笔结束」的判定列表（按笔序升序）。"""
        ...


# ---------------------------------------------------------------- 特征序列工具


def _has_gap(a: tuple[float, float, int], b: tuple[float, float, int]) -> bool:
    """相邻特征序列元素**无重合区间**即为缺口。"""
    return b[0] < a[1] or b[1] > a[0]


def _combine(
    h: float,
    l: float,
    si: int,
    high: float,
    low: float,
    si2: int,
    direction: int,
) -> tuple[float, float, int]:
    """第65课包含关系的结合法则。

    「当向上时，顺次n个包含关系的K线组，等价于[maxdi,maxgi]的区间对应的K线，
    也就是说，这n个K线，和最低最高的区间为[maxdi,maxgi]的K线是一回事情；
    向下时，顺次n个包含关系的K线组，等价于[mindi,mingi]的区间对应的K线。」
    """
    if direction == 1:
        return max(h, high), max(l, low), (si2 if high >= h else si)
    return min(h, high), min(l, low), (si2 if low <= l else si)


def _merge_feature(
    elems: Sequence[tuple[float, float, int]], direction: int
) -> list[tuple[float, float, int]]:
    """对特征序列做非包含处理，得到**标准特征序列**。

    第67课：「关于特征序列，把每一元素看成是一K线，那么，如同一般K线图中找分型的
    方法，也存在所谓的包含关系，也可以对此进行非包含处理。经过非包含处理的特征
    序列，成为标准特征序列。」

    第65课给出结合律与顺序原则：「因此在K线包含关系的分析中，还要遵守顺序原则，
    就是先用第1、2根K线的包含关系确认新的K线，然后用新的K线去和第三根比，如果有
    包含关系，继续用包含关系的法则结合成新的K线，如果没有，就按正常K线去处理。」

    `direction` 为线段方向；元素记为 `(high, low, stroke_idx)`，
    合并后保留决定极值的那根笔的索引。

    顺序原则只向前推进，而第65课同时指出「包含关系，不符合传递律」，所以合并出的
    新元素可能反过来把**前一个**元素包含进去（实测在 A 股各标的普遍出现，`sz.300760`
    等标的每次划分都有上百处）。此时它就不再是第67课所说的「经过非包含处理」的
    序列：`_is_fractal_at` 按第62课「第二K线高点是相邻三K线高点中最高的，而低点也是
    相邻三K线低点中最高的」判定，被包含的那一对永远构不成分型，真极值会被永久跳过，
    该起点就一个分界点也找不到。所以合并之后要沿已生成的序列回退，直到相邻元素之间
    不再有包含关系为止 —— 这正是第79课第24段禅师**自己**的做法：「34、56、78，其中前两者
    可以进行包含关系处理，因此可以合并为36（指区间），所以78显然和12、36构成底分型」
    （先包含，再判分型；同课第26段同理：「由于9-10是78的包含关系，所以可以认为线段二
    延伸到了10」）。第71课给出理由：「但假设的转折点后的顶分型的元素，是可以应用包含
    关系的……同一类的东西，当然可以考察包含关系。」回退只合并**相邻**元素，不借助
    传递律。
    """
    out: list[list[float | int]] = []
    for high, low, si in elems:
        if not out:
            out.append([high, low, si])
            continue
        h, l, prev_si = out[-1]
        h, l, prev_si = float(h), float(l), int(prev_si)
        contained = (high <= h and low >= l) or (high >= h and low <= l)
        if contained:
            out[-1] = list(_combine(h, l, prev_si, high, low, si, direction))
            while len(out) >= 2:
                h1, l1, si1 = out[-2]
                h2, l2, si2 = out[-1]
                h1, l1, si1 = float(h1), float(l1), int(si1)
                h2, l2, si2 = float(h2), float(l2), int(si2)
                if not ((h2 <= h1 and l2 >= l1) or (h2 >= h1 and l2 <= l1)):
                    break
                out.pop()
                out[-1] = list(_combine(h1, l1, si1, h2, l2, si2, direction))
        else:
            out.append([high, low, si])
    return [(float(h), float(l), int(si)) for h, l, si in out]


def _feature_seq(
    strokes: Sequence[Stroke], start: int, seq_direction: int
) -> list[tuple[float, float, int]]:
    """特征序列：以向上笔开始的（子）走势取向下笔，向下走势取向上笔。"""
    want = -seq_direction
    return [
        (strokes[i].high, strokes[i].low, i)
        for i in range(start, len(strokes))
        if strokes[i].direction == want
    ]


def _is_fractal_at(
    std: Sequence[tuple[float, float, int]], k: int, direction: int
) -> bool:
    """标准特征序列在 k 处是否构成分型（向上走势看顶分型，向下看底分型）。"""
    a, b, c = std[k - 1], std[k], std[k + 1]
    if direction == 1:
        return b[0] > a[0] and b[0] > c[0] and b[1] > a[1] and b[1] > c[1]
    return b[1] < a[1] and b[1] < c[1] and b[0] < a[0] and b[0] < c[0]


# ---------------------------------------------------------------- 默认策略


def _extreme(elem: Sequence[float], direction: int) -> float:
    """元素在给定方向上的极值（向上取高点，向下取低点）。"""
    return elem[0] if direction == 1 else elem[1]


def _further(a: float, b: float, direction: int) -> bool:
    return a > b if direction == 1 else a < b


def _ordinal_ok(strokes: Sequence[Stroke], start: int, end: int, direction: int) -> bool:
    """线段**极值的先后次序**必须与线段方向一致。

    第 78 课（一手，逐字）：

    > 如果线段中，最高或最低点不是线段的端点，那么，在任何以线段为基础的分析
    > 中，例如以线段为基础构成最小级别的中枢等，都可以把该线段标准化为最高低点
    > 都在端点。经过标准化处理后，所有向上线段都是以最低点开始最高点结束，向下
    > 线段都是以最高点开始最低点结束。

    **原文明确承认「最高或最低点不是线段的端点」是会发生的情况**，并把它列为划分
    **之后**才做标准化处理的对象，**不是划分判据**。所以本函数不要求极值落在端点，
    只要求它们的前后次序与方向一致：向上线段的最低点不晚于最高点，向下线段的最高点
    不晚于最低点。这是「以最低点开始最高点结束」在**不移动笔的端点**这一约束下的
    等价表述，也排除了「向上线段先见最高点、后见最低点」这类真正矛盾的划分。

    标准化本身由 `_make_segment` 完成（`high=max(...)`、`low=min(...)`），
    因此 `Segment.high/low` 恒为该笔区间的真实极值，可直接供中枢构造使用。

    **历史**：本函数此前是 `_structural_ok`，要求极值**必须落在端点**，即把第 78 课的
    事后标准化提前成了划分前置条件。实测代价见报告 §A.4——它会在候选终点上制造
    可行性悬崖，逼 DP 选出一条 159 笔、横跨 2001-2005 大熊市的「线段」
    （`sz.399001`）；改成次序判据后该段缩短到 53 笔，且指数样本里 >100 笔的线段
    从 1 条降到 0 条。
    """
    seg = strokes[start:end + 1]
    if not seg:
        return False
    hi = max(range(len(seg)), key=lambda i: seg[i].high)
    lo = min(range(len(seg)), key=lambda i: seg[i].low)
    if direction == 1:
        return lo <= hi
    return hi <= lo


class Lesson6768Policy:
    """第 67 课 + 第 78 课的默认线段划分策略。

    终点取「标准特征序列中确立新极值、且构成分型」的元素；缺口（第二种情况）
    另需反向的第二特征序列出现分型来确认。分界还必须满足两条结构约束：
    开始三笔有重叠、极值的先后次序与方向一致（见 `_ordinal_ok`）。

    由于同一走势可能存在多个合法分界，贪心取首个分界会走到死路（后续笔数
    不足以成段）。这里改为在可行划分中取「确认线段数最多」者，平局取更早
    分界，从而保证每条分界之后仍能继续划分。
    """

    name = "lesson_67_78"

    def __init__(self, min_strokes: int = MIN_STROKES) -> None:
        self.min_strokes = min_strokes

    def candidates(self, strokes: Sequence[Stroke], start: int):
        """产出从 `start` 笔开始、按结束位置升序的合法分界。"""
        direction = strokes[start].direction
        if not _first_three_overlap(strokes, start):
            return
        feats = _feature_seq(strokes, start, direction)
        if len(feats) < 3:
            return
        std = _merge_feature(feats, direction)
        kind = "top" if direction == 1 else "bottom"
        extreme: float | None = None

        for k in range(len(std) - 1):
            value = _extreme(std[k], direction)
            new_extreme = extreme is None or _further(value, extreme, direction)
            if new_extreme:
                extreme = value
            if k == 0:
                # 左端情形：极值即落在第一个标准特征元素上，无从构成分型，
                # 以「后继元素不再延伸该极值」作为结束依据。
                if not _further(_extreme(std[0], direction),
                                _extreme(std[1], direction), direction):
                    continue
            elif not (new_extreme and _is_fractal_at(std, k, direction)):
                continue

            end_idx = std[k][2] - 1
            if end_idx - start + 1 < self.min_strokes:
                continue
            if not _ordinal_ok(strokes, start, end_idx, direction):
                continue

            has_gap = k >= 1 and _has_gap(std[k - 1], std[k])
            if has_gap:
                confirm_idx = self._confirm_gap(strokes, std[k][2], direction)
                if confirm_idx is None:
                    continue
            else:
                confirm_idx = std[k + 1][2] if k + 1 < len(std) else None

            yield SegmentBreak(
                stroke_idx=end_idx,
                reason=f"case1_{kind}_fractal" if not has_gap else "case2_gap_confirmed",
                has_gap=has_gap,
                confirm_stroke_idx=confirm_idx,
                start_stroke_idx=start,
            )

    @staticmethod
    def _confirm_gap(
        strokes: Sequence[Stroke], from_idx: int, direction: int
    ) -> int | None:
        """第二种情况：缺口由反向的第二特征序列出现分型来确认。"""
        sub = _feature_seq(strokes, from_idx, -direction)
        if len(sub) < 3:
            return None
        std = _merge_feature(sub, -direction)
        for j in range(1, len(std) - 1):
            if _is_fractal_at(std, j, -direction):
                return std[j + 1][2]
        return None

    def classify(self, strokes: Sequence[Stroke]) -> list[SegmentBreak]:
        n = len(strokes)
        if n < self.min_strokes:
            return []

        memo: dict[int, tuple[int, SegmentBreak | None]] = {}

        def best(start: int) -> tuple[int, SegmentBreak | None]:
            """返回 (从 start 起最多还能确认的线段数, 首个分界)。"""
            if start + self.min_strokes > n:
                return 0, None
            if start in memo:
                return memo[start]
            result: tuple[int, SegmentBreak | None] = (0, None)
            for br in self.candidates(strokes, start):
                count, _ = best(br.stroke_idx + 1)
                if count + 1 > result[0]:      # 严格大于 -> 平局取更早分界
                    result = (count + 1, br)
            memo[start] = result
            return result

        # 窗口左端：**数据窗口的起点不是线段起点。**
        #
        # 第 67 课：
        #
        # > 一切同一级别图上的走势都可以唯一地划分为线段的连接，这是基础的基础。
        #
        # 「唯一」是对**手上的走势**说的。而数据文件的第一根 K 线往往只是**数据商的
        # 截断点**：`600180` 的日线从 2021-01-04 开始，可该股 1998 年就上市了；
        # `sz.300760` 从 2020-01-02 开始，它 2018 年就上市了。把这种人为边界当成线段
        # 边界，等于凭空断言一条线段在这里开始。
        #
        # 原文没有规定被截断的左端该怎么起段（第 67/77/78 课都只说划分唯一），
        # 所以这是**原文空白项下的口径选择**，只能取工程判据：既然无法知道第 0 笔是不是
        # 真实线段起点，就让划分尽可能完整 —— 取「还能确认最多线段」的起点，前面那几笔
        # 交给 TENTATIVE 前导段。前导段本来就是为这件事存在的（见 `build_segments`）。
        #
        # 反过来（从第 0 笔硬起）的代价实测很大，且**正好是用户报告的那类症状**：
        #   `600180` 日线全史 114 笔只划出 5 段、首段 **73 笔**（改后 19 段、首段 16 笔）；
        #   `sz.399001` 从 2020-01-01 截断 → 首段 **81 笔**（全史只有 47）；
        #   `603777` 从 2020-01-01 截断 → 首段 **87 笔**（全史只有 25）。
        # 即「同一个走势，换个取数窗口就塌成一条线段」——与用户报的深证成指同一机制。
        #
        # 这里一度改回「第一个可行起点」，理由是 argmax 会把开头整片笔塞进前导段
        # （`sz.399006` 前导 102 笔、100 只样本合计 308 笔）。**那条证据是在 D3
        # （`_merge_feature` 非包含处理）修复之前取的**：旧代码里标准特征序列残留包含
        # 关系，`candidates(0)` 会凭空为空，argmax 才有机会把起点推到很后面。D3 之后
        # 前导段最长 13 笔、17 只样本合计 31 笔，否决理由已消失，故按用户裁决改回 argmax。
        #
        # 平局取**更早**的起点：更早意味着更少的笔被划到划分之外。
        #
        # 剪枝：`s` 之后的任何起点 `s'` 最多只能划 `(n - s') // min_strokes` 条线段
        # （每条至少 min_strokes 笔），该上界随 `s'` 单调不增。所以一旦当前最好成绩
        # 已经达到 `(n - s) // min_strokes`，后面的起点最多只能追平，而平局取更早 ——
        # 可以直接停。这把 78k 根 5 分钟线（1948 笔）的快照从 1.64s 拉回 0.6s 量级。
        start, best_count = 0, 0
        for s in range(n):
            if best_count >= (n - s) // self.min_strokes:
                break
            count = best(s)[0]
            if count > best_count:
                start, best_count = s, count

        breaks: list[SegmentBreak] = []
        while start < n:
            _, br = best(start)
            if br is None:
                break
            breaks.append(br)
            start = br.stroke_idx + 1
        return breaks


# ---------------------------------------------------------------- 组装线段


def _first_three_overlap(strokes: Sequence[Stroke], start: int) -> bool:
    """线段开始三笔必须有重叠。"""
    three = strokes[start:start + 3]
    if len(three) < 3:
        return False
    zd = max(x.low for x in three)
    zg = min(x.high for x in three)
    return zg > zd


def _make_segment(
    strokes: Sequence[Stroke],
    s: int,
    e: int,
    idx: int,
    policy_name: str,
    status: Status,
    breaks: Sequence[SegmentBreak],
) -> Segment:
    seg = strokes[s:e + 1]
    confirmed_at: str | None = None
    if status is Status.CONFIRMED:
        br = next((b for b in breaks if b.stroke_idx == e), None)
        ci = br.confirm_stroke_idx if br and br.confirm_stroke_idx is not None else e
        ci = max(e, min(ci, len(strokes) - 1))
        # 线段在「确认它被破坏的那一笔」锁定后才能算确认，而那一笔本身要到
        # 下一笔成形才锁定 —— 所以取该笔的 confirmed_at；手工构造的笔没有
        # 该字段时退回其终点。
        confirmed_at = strokes[ci].confirmed_at or strokes[ci].end.ts
    return Segment(
        idx=idx,
        direction=seg[0].direction,
        start=seg[0],
        end=seg[-1],
        high=max(x.high for x in seg),
        low=min(x.low for x in seg),
        start_stroke_idx=s,
        end_stroke_idx=e,
        stroke_count=len(seg),
        status=status,
        confirmed_at=confirmed_at,
        policy=policy_name,
    )


def build_segments(
    strokes: Sequence[Stroke],
    policy: SegmentPolicy | None = None,
    min_strokes: int = MIN_STROKES,
) -> list[Segment]:
    """由笔构造线段。最后一个线段总是 `TENTATIVE`。"""
    strokes = list(strokes)
    if not strokes:
        return []
    policy = policy or Lesson6768Policy(min_strokes=min_strokes)
    name = getattr(policy, "name", "custom")

    breaks = sorted(policy.classify(strokes), key=lambda b: b.stroke_idx)

    # 策略可以从第 1 笔之后才开始划分（窗口左端被截断，前缀不成线段）。
    first = breaks[0].start_stroke_idx if breaks else 0
    bounds = [first]
    for b in breaks:
        nxt = b.stroke_idx + 1
        if nxt - bounds[-1] < min_strokes:
            continue          # 会切出不足三笔的线段，忽略该断点
        if nxt >= len(strokes):
            continue          # 后面没有笔了，留给末段处理
        if nxt + 2 < len(strokes) and not _first_three_overlap(strokes, nxt):
            continue          # 新线段开始三笔无重叠，不构成线段
        bounds.append(nxt)

    segments: list[Segment] = []
    if first > 0:
        # 前导段：起点落在数据窗口之外，无法判定其真实终点，故不作为确认线段。
        segments.append(
            _make_segment(strokes, 0, first - 1, 0, name, Status.TENTATIVE, breaks)
        )

    for i in range(len(bounds) - 1):
        segments.append(
            _make_segment(strokes, bounds[i], bounds[i + 1] - 1, len(segments),
                          name, Status.CONFIRMED, breaks)
        )

    tail = bounds[-1]
    if tail < len(strokes):
        segments.append(
            _make_segment(strokes, tail, len(strokes) - 1, len(segments),
                          name, Status.TENTATIVE, breaks)
        )
    return _monotone_stamps(segments)


def _monotone_stamps(segments: list[Segment]) -> list[Segment]:
    """把确认时间沿序列「压实」成非递减。

    线段 i 的起点就是线段 i-1 的破坏点，所以「线段 i-1 的破坏点被锁定」是
    「线段 i 成立」的**前置条件**：真实数据上确实出现过前一段因缺口要到
    2023-08 才确认、后一段却写着 2022-04 的情况（第 78 课第二种情况的确认
    笔可以远在后面）。这种倒挂会让回测在 as_of 早于前置条件时就用上后一段，
    属于未来函数，故取前缀最大值。
    """
    out: list[Segment] = []
    last = ""
    for seg in segments:
        stamp = seg.confirmed_at
        if seg.status is Status.CONFIRMED and stamp is not None:
            if stamp < last:
                stamp = last
            last = stamp
            seg = replace(seg, confirmed_at=stamp)
        out.append(seg)
    return out


def _extreme_problems(seg: Segment, i: int, strokes: Sequence[Stroke]) -> list[str]:
    """第 78 课对确认线段极值的两条要求（需要看到线段内部的笔才能判定）。

    > 如果线段中，最高或最低点不是线段的端点，那么……都可以把该线段标准化为最高
    > 低点都在端点。经过标准化处理后，所有向上线段都是以最低点开始最高点结束。

    1. **标准化**：`Segment.high/low` 必须是该笔区间内真实的最大/最小值
       （`_make_segment` 已如此构造，此处是交叉校验）。
    2. **次序**：向上线段的最低点不晚于最高点，向下线段反之。原文「以最低点开始
       最高点结束」落在代码里就是这条——线段端点是笔，不能为了凑这条而移动它们。
    """
    part = list(strokes[seg.start_stroke_idx : seg.end_stroke_idx + 1])
    if not part:
        return [f"segment {i}: 笔区间为空"]
    hi_i = max(range(len(part)), key=lambda k: part[k].high)
    lo_i = min(range(len(part)), key=lambda k: part[k].low)
    hi, lo = part[hi_i].high, part[lo_i].low
    out: list[str] = []
    if seg.high != hi or seg.low != lo:
        out.append(
            f"segment {i}: 未按第 78 课标准化"
            f"（线段 高={seg.high} 低={seg.low}，笔区间真实 高={hi} 低={lo}）"
        )
    if seg.direction == 1 and lo_i > hi_i:
        out.append(
            f"segment {i}: 向上线段先见最高点（第 {hi_i} 笔）后见最低点（第 {lo_i} 笔）"
        )
    elif seg.direction == -1 and hi_i > lo_i:
        out.append(
            f"segment {i}: 向下线段先见最低点（第 {lo_i} 笔）后见最高点（第 {hi_i} 笔）"
        )
    return out


def validate_segments(
    segments: Sequence[Segment], strokes: Sequence[Stroke] | None = None
) -> list[str]:
    """返回线段序列违反的不变量列表（空列表 = 全部满足）。

    传入 `strokes` 时额外校验第 78 课的标准化与极值次序（见 `_extreme_problems`）；
    不传则跳过这两条——它们必须看到线段内部的笔才能判定。
    """
    problems: list[str] = []
    for i, seg in enumerate(segments):
        if seg.start is not seg.end and seg.start_stroke_idx > seg.end_stroke_idx:
            problems.append(f"segment {i}: 笔区间颠倒")
        if seg.status is Status.CONFIRMED:
            if seg.stroke_count < MIN_STROKES:
                problems.append(f"segment {i}: 已确认线段笔数 {seg.stroke_count} < 3")
            if seg.stroke_count % 2 == 0:
                problems.append(f"segment {i}: 已确认线段笔数为双数 {seg.stroke_count}")
            if strokes is not None:
                problems.extend(_extreme_problems(seg, i, strokes))
        if i and segments[i - 1].end_stroke_idx + 1 != seg.start_stroke_idx:
            problems.append(f"segment {i}: 与上一线段不连续")

    # 同向约束只作用于相邻的确认线段。TENTATIVE 段（窗口左端的前导段、
    # 末端未走完的尾段）本身是不完整的，前导段与首个确认段常常本来就是
    # 同一条线段、只是被数据窗口截断，理应同向。
    confirmed = [i for i, s in enumerate(segments) if s.status is Status.CONFIRMED]
    for prev, nxt in zip(confirmed, confirmed[1:]):
        if segments[prev].direction == segments[nxt].direction:
            problems.append(f"segment {nxt}: 与上一确认线段同向")
    return problems
