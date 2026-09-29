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
这一条在原文中未展开到公式级，是**策略可插拔点**，由 Task 17 的优化师依据原文继续校准。

注意：线段划分是全书修订最多、分歧最大的一层（第 63、84、101 课明确说分型/线段只是
递归定义里的「初始项 a0」，**可以随便设计**，只要不破坏后续唯一分解）。因此本模块通过
`SegmentPolicy` 协议把实现完全外置，`build_segments` 内**不含任何硬编码的划分规则**。
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


def _merge_feature(
    elems: Sequence[tuple[float, float, int]], direction: int
) -> list[tuple[float, float, int]]:
    """对特征序列做包含处理，得到标准特征序列。

    `direction` 为线段方向；元素记为 `(high, low, stroke_idx)`，
    合并后保留决定极值的那根笔的索引。
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
            if direction == 1:
                nh, nl = max(h, high), max(l, low)
                nsi = si if high >= h else prev_si
            else:
                nh, nl = min(h, high), min(l, low)
                nsi = si if low <= l else prev_si
            out[-1] = [nh, nl, nsi]
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


def _structural_ok(strokes: Sequence[Stroke], start: int, end: int, direction: int) -> bool:
    """线段极值必须在两端。

    向上线段的低点在起点、高点在终点；向下线段的高点在起点、低点在终点。
    由「线段不可能顶到顶/底到底」推出：极值落在中间即意味着该段被更大的
    反向走势穿过，不构成线段。
    """
    seg = strokes[start:end + 1]
    if not seg:
        return False
    highs = [x.high for x in seg]
    lows = [x.low for x in seg]
    if direction == 1:
        return seg[0].start.price == min(lows) and seg[-1].end.price == max(highs)
    return seg[0].start.price == max(highs) and seg[-1].end.price == min(lows)


class Lesson6768Policy:
    """第 67 课 + 第 78 课的默认线段划分策略。

    终点取「标准特征序列中确立新极值、且构成分型」的元素；缺口（第二种情况）
    另需反向的第二特征序列出现分型来确认。分界还必须满足两条结构约束：
    极值在两端、开始三笔有重叠。

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
            if not _structural_ok(strokes, start, end_idx, direction):
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

        breaks: list[SegmentBreak] = []
        # 窗口左端：数据被截断，第 0 笔可能只是一个不完整线段的尾巴。
        # 若从它起无法划分出线段（常见于首笔方向与随后长期走势相反），
        # 就把这段前缀交给 TENTATIVE 前导段，从第一个可划分处开始。
        start = 0
        while start < n and best(start)[1] is None:
            start += 1
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


def validate_segments(segments: Sequence[Segment]) -> list[str]:
    """返回线段序列违反的不变量列表（空列表 = 全部满足）。"""
    problems: list[str] = []
    for i, seg in enumerate(segments):
        if seg.start is not seg.end and seg.start_stroke_idx > seg.end_stroke_idx:
            problems.append(f"segment {i}: 笔区间颠倒")
        if seg.status is Status.CONFIRMED:
            if seg.stroke_count < MIN_STROKES:
                problems.append(f"segment {i}: 已确认线段笔数 {seg.stroke_count} < 3")
            if seg.stroke_count % 2 == 0:
                problems.append(f"segment {i}: 已确认线段笔数为双数 {seg.stroke_count}")
            if seg.direction == 1:
                if seg.low != seg.start.start.price or seg.high != seg.end.end.price:
                    problems.append(
                        f"segment {i}: 向上线段极值不在两端"
                        f"（起点 {seg.start.start.price} 低点 {seg.low}，"
                        f"终点 {seg.end.end.price} 高点 {seg.high}）"
                    )
            elif seg.high != seg.start.start.price or seg.low != seg.end.end.price:
                problems.append(
                    f"segment {i}: 向下线段极值不在两端"
                    f"（起点 {seg.start.start.price} 高点 {seg.high}，"
                    f"终点 {seg.end.end.price} 低点 {seg.low}）"
                )
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
