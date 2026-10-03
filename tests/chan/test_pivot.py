"""中枢（第 17/20 课）。

规则来源：中枢 = **至少三个连续次级别走势类型**的重叠区间。
本实现用线段近似次级别走势类型，因此：

    ZG = min(前三个线段的高点)      ZD = max(前三个线段的低点)
    ZG > ZD 才成立                  GG = max(Zn 的高点)    DD = min(Zn 的低点)

第 20 课：「为方便起见，以后都把这些与中枢方向一致的次级别走势类型称为Z走势段，
按中枢中的时间顺序，分别记为Zn等，而相应的高、低点分别记为gn、dn，定义四个
指标,GG=max(gn),G=min(gn),D=max(dn),DD=min(dn)，n遍历中枢中所有Zn。」
即 GG/DD **只遍历同向的 Z 走势段**，反向段（B 及延伸中的回抽段）的极值不计入；
而 `group[-1]`（本实现的「离开段」，见 `pivot.py:128-130`）不算「中枢中」的 Z
走势段 —— 但**只有它本身是 Zn 时**才排除它，它是反向段时不得再砍 `Zn` 的末位
（D-37）。这条不是细节：算进离开段时 `[DD, GG]` 会被撑到下一个中枢门口，
第 20 课中心定理二里的「趋势」几乎判不出来（实测 28 对里 27 对落进「形成高级别的
走势中枢」，上涨/下跌 1/0；去掉离开段后是 上涨 9 / 下跌 4 / 高级别中枢 15，
见 `optimizer/tools/measure_l20_dd_gg_scope.py`）。

关键取舍（都写成测试锁住）：
1. **只有 `CONFIRMED` 线段能构成中枢**。中枢要求「次级别走势类型」已经完成，
   未确认的尾段不算数；否则中枢会随着尾段来回变形。
2. **延伸不改变 ZG/ZD**。中枢区间由最初三段确定，后续段只让 `end_idx` 后移。
3. **离开段成为下一个中枢的第一段**（新生：第三类买卖点之后老中枢结束）。
4. 中枢右端若已用尽可用线段 → `TENTATIVE`（右侧还可能延伸）。
"""
import pytest

from chanlun.chan.pivot import (
    LEVEL_UP,
    MAX_SEGMENTS,
    find_pivots,
    next_level,
    validate_pivots,
)
from chanlun.chan.types import Fractal, FractalKind, Segment, Status, Stroke


# ---------------------------------------------------------------- 构造工具
def _frac(ts, price, kind, idx):
    return Fractal(
        kind=kind, midx=idx, ts=ts, high=price, low=price,
        price=price, src_idx=idx,
    )


def _stroke(idx, direction, ts_a, ts_b, lo, hi):
    a = _frac(ts_a, lo if direction == 1 else hi,
              FractalKind.BOTTOM if direction == 1 else FractalKind.TOP, idx)
    b = _frac(ts_b, hi if direction == 1 else lo,
              FractalKind.TOP if direction == 1 else FractalKind.BOTTOM, idx + 1)
    return Stroke(
        idx=idx, direction=direction, start=a, end=b, high=hi, low=lo,
        src_start=a.src_idx, src_end=b.src_idx, status=Status.CONFIRMED,
        confirmed_at=ts_b,
    )


def _seg(i, low, high, direction=1, status=Status.CONFIRMED):
    """构造一个只在中枢计算上有意义的线段（high/low/时序/状态是真的）。"""
    a = _stroke(2 * i, direction, f"d{i * 10 + 1}", f"d{i * 10 + 4}", low, high)
    b = _stroke(2 * i + 1, direction, f"d{i * 10 + 6}", f"d{i * 10 + 9}", low, high)
    return Segment(
        idx=i, direction=direction, start=a, end=b, high=high, low=low,
        start_stroke_idx=2 * i, end_stroke_idx=2 * i + 1, stroke_count=3,
        status=status,
        confirmed_at=None if status is not Status.CONFIRMED else f"d{i * 10 + 9}",
    )


def _zigzag(bounds, status=Status.CONFIRMED):
    return [_seg(i, lo, hi, 1 if i % 2 == 0 else -1, status)
            for i, (lo, hi) in enumerate(bounds)]


# ---------------------------------------------------------------- 成枢
def test_three_overlapping_segments_form_pivot():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    ps = find_pivots(segs, "day")
    assert len(ps) == 1
    p = ps[0]
    assert p.zg == 18          # min(20, 22, 18)
    assert p.zd == 12          # max(10, 12, 11)
    assert (p.start_idx, p.end_idx) == (0, 2)
    assert p.level == "day"


def test_no_overlap_no_pivot():
    segs = _zigzag([(10, 20), (30, 40), (50, 60)])
    assert find_pivots(segs, "day") == []


def test_zero_width_overlap_is_not_a_pivot():
    """ZG == ZD（仅边界相切）不构成中枢：必须 ZG > ZD。"""
    segs = _zigzag([(10, 20), (5, 15), (15, 25)])
    assert find_pivots(segs, "day") == []


def test_fewer_than_three_segments_is_never_a_pivot():
    assert find_pivots(_zigzag([(10, 20), (12, 22)]), "day") == []


def test_non_contiguous_overlap_does_not_form_pivot():
    """重叠必须发生在连续的三段之间；第 1 段与第 3 段重叠但第 2 段不重叠 → 不成枢。"""
    segs = _zigzag([(10, 20), (40, 50), (12, 18)])
    assert find_pivots(segs, "day") == []


# ---------------------------------------------------------------- 延伸
def test_pivot_extends_while_overlapping():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (13, 17), (0, 5)])
    p = find_pivots(segs, "day")[0]
    assert p.end_idx == 3       # 第 4 段仍与 [12,18] 重叠 → 延伸


def test_pivot_extension_is_capped_at_eight_segments_by_lesson_33():
    """第 33 课《走势的多义性》：本级别中枢最多 8 段。

    原文把两个数分得很清楚：「中枢的延伸不能超过 5 段，也就是一旦出现 6 段的
    延伸，加上形成中枢本身那三段，就构成更大级别的中枢了」——**形成中枢的 3 段
    与延伸段是分开计数的**，所以本级别上限 = 3 + 5 = 8 段。凑满 9 段时它已经是
    上一级别的中枢，不能再当同一个中枢继续吸段（否则两个中枢会被吸成一个，
    盘整走势被压掉）。

    12 段连续重叠 → 第 1 个中枢吃满 8 段就封口，剩下的从头再成枢。
    """
    segs = _zigzag([(10.0, 20.0)] * 12)
    pivots = find_pivots(segs, "day")
    assert [p.segment_count for p in pivots] == [8, 4]
    assert (pivots[0].start_idx, pivots[0].end_idx) == (0, 7)
    assert (pivots[1].start_idx, pivots[1].end_idx) == (8, 11)
    assert MAX_SEGMENTS == 8       # 3（本身）+ 5（延伸上限）


def test_zg_zd_are_fixed_by_the_first_three_segments():
    """延伸不得改变中枢区间，否则中枢会随行情漂移。"""
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (13, 19), (0, 5)])
    p = find_pivots(segs, "day")[0]
    assert (p.zg, p.zd) == (18, 12)


def test_gg_dd_cover_only_the_z_segments():
    """GG/DD 只遍历 Z 走势段（同向段）；反向段的极值不进 [DD, GG]。

    本例中枢组 = 4 段：seg0 上 (10,25)、seg1 下 (12,20)、seg2 上 (13,30)、
    seg3 下 (14,28)。Zn 只有 seg0/seg2。`group[-1]` 是 seg3 —— **反向段**，本来
    就不在 Zn 里，所以不得再顺手砍掉 `Zn` 的末位（那会砍掉枢内的 seg2，见 D-37）。
    故 GG = max(25, 30) = 30、DD = min(10, 13) = 10。

    三个数各钉死一类干扰：
    - GG = 30：枢内的 Zn seg2 必须计入（旧实现砍掉它，得 25）；
    - GG = 30 而不是 28：排除**反向段** seg3 的 28；
    - DD = 10 而不是 12：排除**反向段** seg1 的低点。
    [DD, GG] = [10, 30] 两侧都宽于 [ZD, ZG] = [13, 20]，所以 GG/DD 确实是另一套边界。
    """
    segs = _zigzag([(10, 25), (12, 20), (13, 30), (14, 28), (0, 5)])
    p = find_pivots(segs, "day")[0]
    assert (p.zg, p.zd) == (20, 13)
    assert p.end_idx == 3
    assert p.gg == 30           # 枢内 Zn seg2 的 30 计入；反向段 seg3 的 28 不计
    assert p.dd == 10           # 不含反向段 seg1 的 12


def test_leaving_z_segment_is_excluded_from_gg_dd():
    """`group[-1]`（离开段）**是** Zn 时，它的极值才不进 GG/DD。

    与上一条互为对照，把 D-37 的两半钉死：排除的依据是「它是 `group[-1]`」，
    不是「它是最后一个同向段」。本例 `group[-1]` = seg2（上，离开段），故排除它。
    """
    segs = _zigzag([(10, 20), (12, 22), (11, 25), (0, 5)])
    p = find_pivots(segs, "day")[0]
    assert p.end_idx == 2
    assert (p.zg, p.zd) == (20, 12)
    assert p.gg == 20           # 离开段 seg2 的 25 不计
    assert p.dd == 10


def test_normally_closed_pivot_is_not_capped():
    """被「整段不碰区间」的回试段封口时 `capped is False`（D-38）。"""
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    p = find_pivots(segs, "day")[0]
    assert p.capped is False
    assert p.end_idx == 2


def test_capped_pivot_does_not_exclude_its_last_segment():
    """段数上限掐停时组里**没有**离开段，所以一段都不排除（D-38）。

    这条用例故意构造**方向不交替**的合法输入（同向段相邻），好让 D-38 的分支
    真的被走到。原因：`find_pivots` 只把「相邻确认段方向交替」写在注释里
    （`pivot.py` 里 `direction = trio[0].direction` 那一段），没有任何校验器
    强制它；而在交替输入下组长恒为 8（偶数）⇒ `group[-1]` 恒为反向段 ⇒ 该分支
    与旧口径**可证等价**（实测 24 只票 52 个确认中枢里 E 与 F 的数值差异是 0）。
    不构造反例就分不清「分支没生效」和「分支写错了」。

    本例 9 段：seg0..seg2 成枢（[ZD,ZG]=[12,18]），seg3..seg7 全部同向且仍在
    区间内，延伸被 `MAX_SEGMENTS` 掐停；seg7 的 25 是组内最高的 Zn 极值。
    """
    segs = [_seg(0, 10, 20, 1), _seg(1, 12, 22, -1), _seg(2, 11, 18, 1)]
    segs += [_seg(i, 13, 19, 1) for i in range(3, 7)]
    segs.append(_seg(7, 13, 25, 1))     # 组内最高的同向段极值
    segs.append(_seg(8, 13, 19, -1))    # 仍在枢内 ⇒ cap 才是真正掐停的那一个
    p = find_pivots(segs, "day")[0]
    assert (p.zg, p.zd) == (18, 12)
    assert p.capped is True
    assert p.segment_count == MAX_SEGMENTS
    assert p.end_idx == 7
    assert p.gg == 25           # seg7 是 Zn 且不是离开段 ⇒ 计入
    assert p.dd == 10


def test_pivot_timestamps_span_first_start_to_last_end():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    p = find_pivots(segs, "day")[0]
    assert p.start_ts == "d1"       # 第 0 段起点
    assert p.end_ts == "d29"        # 第 2 段终点


# ---------------------------------------------------------------- 新生
def test_leaving_segment_starts_the_next_pivot():
    """第三类买卖点之后老中枢结束，离开段成为新中枢的第一段。"""
    segs = _zigzag([(10, 20), (12, 22), (11, 18),       # 中枢 A：[12,18]
                    (30, 40), (32, 42), (31, 38),       # 中枢 B：[32,38]
                    (0, 5)])
    ps = find_pivots(segs, "day")
    assert [p.start_idx for p in ps] == [0, 3]
    assert ps[0].end_idx == 2
    assert ps[1].end_idx == 5
    assert (ps[1].zg, ps[1].zd) == (38, 32)


def test_pivot_indices_are_sequential_and_dense():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (30, 40), (32, 42), (31, 38), (0, 5)])
    assert [p.idx for p in find_pivots(segs, "day")] == [0, 1]


# ---------------------------------------------------------------- 未确认
def test_tentative_segments_never_form_a_pivot():
    segs = _zigzag([(10, 20), (12, 22), (11, 18)], status=Status.TENTATIVE)
    assert find_pivots(segs, "day") == []


def test_tentative_tail_does_not_join_an_existing_pivot():
    """未确认尾段不参与中枢，即使它与中枢区间重叠。"""
    segs = _zigzag([(10, 20), (12, 22), (11, 18)]) + [
        _seg(3, 8, 30, 1, Status.TENTATIVE)
    ]
    p = find_pivots(segs, "day")[0]
    assert p.end_idx == 2
    assert p.gg == 20          # 未确认尾段的 30 不得计入；反向段 seg1 的 22 也不计
    assert p.dd == 10          # 组里最后一个 Zn（seg2）是离开段，同样不计入


def test_pivot_reaching_the_window_end_is_tentative():
    segs = _zigzag([(10, 20), (12, 22), (11, 18)])
    p = find_pivots(segs, "day")[0]
    assert p.end_idx == 2 and p.status is Status.TENTATIVE


def test_pivot_closed_by_a_leaving_segment_is_confirmed():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    p = find_pivots(segs, "day")[0]
    assert p.status is Status.CONFIRMED


def test_tentative_tail_keeps_a_closed_pivot_confirmed():
    """右端已被离开段封闭的中枢，不会因为还有个未确认尾段就退回 TENTATIVE。"""
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)]) + [
        _seg(4, 8, 30, 1, Status.TENTATIVE)
    ]
    p = find_pivots(segs, "day")[0]
    assert p.status is Status.CONFIRMED


def test_pivot_indices_refer_to_the_input_list_not_the_confirmed_sublist():
    """前导段（窗口左端被截断的未确认段）不得让中枢下标整体错位。

    真实数据上 24 只里有 15 只带前导段；下标若按「确认线段子列表」计，
    调用方取 `segments[end_idx + 1]` 拿到的就不是离开段，买卖点会全错。
    """
    lead = _seg(0, 15, 25, 1, Status.TENTATIVE)
    body = [
        _seg(i + 1, lo, hi, 1 if i % 2 == 0 else -1)
        for i, (lo, hi) in enumerate([(10, 20), (12, 22), (11, 18), (0, 5)])
    ]
    segs = [lead] + body
    p = find_pivots(segs, "day")[0]
    assert (p.start_idx, p.end_idx) == (1, 3)
    assert segs[p.end_idx] is body[2]
    assert segs[p.end_idx + 1] is body[3]      # 下一段就是「离开段」
    assert p.segment_count == 3
    assert p.status is Status.CONFIRMED
    assert p.confirmed_at == body[3].confirmed_at


def test_leading_tentative_segment_does_not_shift_a_later_pivot():
    lead = _seg(0, 15, 25, 1, Status.TENTATIVE)
    body = _zigzag([(30, 40), (31, 42), (32, 41), (0, 1)])
    p = find_pivots([lead] + body, "day")[0]
    assert (p.start_idx, p.end_idx) == (1, 3)
    # Zn = body[0]/body[2]（反向段 body[1] 的 42 不计）；其中 body[2] 是离开段
    # （第 20 课：「n遍历中枢中所有Zn」，离开段不算「中枢中」的 Z 走势段），
    # 所以只剩 body[0]：GG = 40、DD = 30。
    assert p.gg == 40 and p.dd == 30


# ---------------------------------------------------------------- 扩展
def test_next_level_upgrades_periods():
    assert next_level("5") == "30"
    assert next_level("30") == "day"
    assert next_level("day") == "week"
    assert LEVEL_UP["5"] == "30"


def test_merge_overlapping_same_level_pivots():
    """扩展：两个同级别中枢有重叠 → 合并为高级别中枢。

    注意 A 与 B 之间必须夹一个「离开段」，否则 B 会被 A 的延伸吞掉。
    """
    from chanlun.chan.pivot import merge_pivots

    segs = _zigzag([(10, 20), (12, 22), (11, 18),       # A：[12,18]
                    (25, 35),                           # 离开段（封闭 A）
                    (14, 24), (15, 25), (14, 23),       # B：[15,23]（与 A 重叠）
                    (0, 5)])                            # 离开段（封闭 B）
    ps = find_pivots(segs, "30")
    assert len(ps) == 2
    assert (ps[0].zg, ps[0].zd) == (18, 12)
    assert (ps[1].zg, ps[1].zd) == (23, 15)
    merged = merge_pivots(ps, "30")
    assert len(merged) == 1
    m = merged[0]
    assert m.level == "day"
    assert (m.zg, m.zd) == (18, 15)      # 取两者重叠区间
    assert m.start_idx == 0 and m.end_idx == 6
    assert m.gg == 24 and m.dd == 10
    # A 的 Zn = seg0/seg2 → gg=20、dd=10；B 的 Zn = seg4/seg6 → gg=24、dd=14。
    # 合并取并集，故 gg = max(20,24) = 24、dd = min(10,14) = 10。


def test_merge_keeps_disjoint_pivots_apart():
    from chanlun.chan.pivot import merge_pivots

    segs = _zigzag([(10, 20), (12, 22), (11, 18),       # A：[12,18]
                    (25, 35),                           # 离开段
                    (40, 50), (42, 52), (41, 48),       # B：[42,48]（与 A 不重叠）
                    (0, 5)])
    ps = find_pivots(segs, "30")
    assert len(ps) == 2
    merged = merge_pivots(ps, "30")
    assert len(merged) == 2
    assert all(m.level == "30" for m in merged)


def test_merged_pivot_is_tentative_if_any_part_is_tentative():
    from chanlun.chan.pivot import merge_pivots

    segs = _zigzag([(10, 20), (12, 22), (11, 18),       # A：[12,18] 已封闭
                    (25, 35),                           # 离开段
                    (14, 24), (15, 25), (14, 23)])      # B：右端触窗口 → TENTATIVE
    ps = find_pivots(segs, "30")
    assert len(ps) == 2
    assert ps[0].status is Status.CONFIRMED
    assert ps[1].status is Status.TENTATIVE
    merged = merge_pivots(ps, "30")
    assert len(merged) == 1
    assert merged[0].status is Status.TENTATIVE
    assert merged[0].level == "day"


# ---------------------------------------------------------------- 不变量与确定性
def test_validate_pivots_accepts_well_formed_pivots():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (8, 25), (0, 5)])
    assert validate_pivots(find_pivots(segs, "day")) == []


def test_validate_pivots_catches_backwards_range():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    bad = find_pivots(segs, "day")
    from dataclasses import replace

    broken = [replace(bad[0], zg=5.0, zd=18.0)]
    assert validate_pivots(broken) != []


def test_find_pivots_is_deterministic_and_pure():
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (30, 40), (32, 42), (31, 38), (0, 5)])
    a = find_pivots(segs, "day")
    b = find_pivots(segs, "day")
    assert a == b
    assert [s.high for s in segs] == [20, 22, 18, 40, 42, 38, 5]   # 未被就地修改


@pytest.mark.parametrize("level", ["5", "30", "day"])
def test_level_is_recorded_verbatim(level):
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    assert find_pivots(segs, level)[0].level == level


def _one_pivot(**kw):
    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    p = find_pivots(segs, "day")[0]
    from dataclasses import replace

    return replace(p, **kw)


@pytest.mark.parametrize(
    "kw",
    [
        {"dd": 13.0},          # dd > zd
        {"gg": 17.0},          # zg > gg
        {"zd": 18.0, "zg": 12.0},   # 区间颠倒
        {"end_idx": 0},        # 段数不足 3
        {"end_idx": 20},       # 段数超过上限（第 33 课：本级别最多 8 段）
        {"start_ts": "d99"},   # 时间颠倒
        {"src_start": 500},    # 原始 bar 颠倒
    ],
)
def test_validate_pivots_catches_each_invariant(kw):
    assert validate_pivots([_one_pivot(**kw)]) != []


def test_validate_pivots_catches_overlapping_neighbours():
    a = _one_pivot()
    from dataclasses import replace

    b = replace(a, idx=1, start_idx=a.start_idx, end_idx=a.end_idx)
    assert validate_pivots([a, b]) != []


def test_segment_exposes_raw_bar_span_for_stable_ids():
    """状态机的 object_id 依赖 Segment.src_start/src_end（原始 bar 索引）。"""
    from chanlun.chan.state import object_id

    segs = _zigzag([(10, 20), (12, 22), (11, 18), (0, 5)])
    assert object_id(segs[1]) == f"segment@{segs[1].start.src_start}-{segs[1].end.src_end}"
    assert object_id(find_pivots(segs, "day")[0]).startswith("pivot@")
