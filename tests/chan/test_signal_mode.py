"""严格 / 非严格买卖点口径（D-32 / D-33 / D-35）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import SignalKind, SignalMode, find_signals
from chanlun.chan.types import Fractal, FractalKind, Segment, Status, Stroke

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bars.parquet"

# 夹具是**四只票的横截面**（4848 行 = 4 × 1212，`ts` 有 3636 个重复值），
# 必须先按 code 过滤再喂引擎 —— 四只票交错喂进去等于把四只票的 bar 混成一只。
# 口径同 `test_engine.py:33-35`。`ChanEngine` 的 `code` 只是标签，bars 才是输入。
_ALL = pd.read_parquet(FIXTURE)
CODE = "sh.600000"
CODES = ("sh.600000", "sh.601088", "sz.300059", "sz.300750")


def _bars_of(code: str) -> pd.DataFrame:
    return _ALL[_ALL["code"] == code].drop(columns=["code"]).reset_index(drop=True)


def _signals(code: str, mode: SignalMode | str = SignalMode.STRICT):
    """按 code 现算一份买卖点（笔/线段/中枢与 mode 无关，只算一次）。

    `mode` 故意允许传字符串：CLI / HTTP / 前端传进来的就是字符串。
    """
    bars = _bars_of(code)
    snap = ChanEngine(code, "day", signal_fn=find_signals, level="day").full(bars)
    return find_signals(bars, snap.segments, snap.pivots, "day", mode=mode)


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return _bars_of(CODE)


@pytest.fixture(scope="module")
def strict(bars):
    return ChanEngine(CODE, "day", signal_fn=find_signals, level="day").full(bars)


def test_strict_mode_matches_baseline(bars, strict):
    """D-32：严格模式的买卖点必须与不传 mode 时逐项相同。"""
    again = find_signals(bars, strict.segments, strict.pivots, "day")
    assert again == list(strict.signals)


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("text,member", [("strict", SignalMode.STRICT),
                                        ("loose", SignalMode.LOOSE)])
def test_mode_string_is_equivalent_to_member(code, text, member):
    """字符串入参与等值枚举成员逐项完全相同（含 `reason`、`idx`）。

    `SignalMode(str, Enum)` 的成员**等于**同名字符串但不是同一个对象
    （`SignalMode.LOOSE == "loose"` 为真、`is "loose"` 为假），所以内部用
    `is` 比较时必须在 `find_signals` 入口归一化 —— 否则 CLI 的 `--mode loose`、
    HTTP 的 `?mode=loose`、前端拼的 URL 都会**静默退化成严格模式**。
    """
    assert _signals(code, text) == _signals(code, member)


def test_strict_string_matches_default_call(bars, strict):
    """`mode="strict"` 与完全不传 `mode` 逐项相同。"""
    explicit = find_signals(bars, strict.segments, strict.pivots, "day", mode="strict")
    assert explicit == list(strict.signals)


def test_loose_string_actually_loosens(bars, strict):
    """反向断言：`"loose"` 必须**真的**比 `"strict"` 宽。

    没有这一条，`mode` 整体退化成严格时上面几条等价测试照样通过。
    `sh.600000` 的基线是严格 1 个 / 非严格 9 个（`task-5-report.md` §3）。
    """
    loose = find_signals(bars, strict.segments, strict.pivots, "day", mode="loose")
    tight = find_signals(bars, strict.segments, strict.pivots, "day", mode="strict")
    assert len(tight) == 1, "sh.600000 严格模式基线是 1 个信号"
    assert len(loose) == 9, "sh.600000 非严格模式基线是 9 个信号"
    assert loose != tight


def test_invalid_mode_string_raises(bars, strict):
    """非法 `mode` 直接抛 `ValueError`，不静默退化。"""
    with pytest.raises(ValueError):
        find_signals(bars, strict.segments, strict.pivots, "day", mode="loose-ish")


def test_loose_adds_but_never_removes(bars, strict):
    """非严格是**放宽**：严格模式有的买卖点一个都不能少。"""
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    key = lambda s: (s.kind.value, s.ts, round(s.price, 6))
    assert {key(s) for s in strict.signals} <= {key(s) for s in loose}


def test_loose_emits_consolidation_kinds(bars, strict):
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    assert any(s.kind is SignalKind.PB for s in loose) or \
           any(s.kind is SignalKind.PS for s in loose), \
        "夹具上应至少有一个盘整背驰买卖点"


def test_strict_never_emits_pb_ps(bars, strict):
    assert not [s for s in strict.signals if s.kind in (SignalKind.PB, SignalKind.PS)]


def test_pb_name_uses_origin_qualifier():
    """第 027 课第 7 段：「这时候就要用到这因为盘整背驰而形成的类第一类买点了。」

    第 060 课 L45 把 55（盘整背驰点）类比成第一类买点、把 57（其回抽）类比成
    第二类买点，所以 `pb`/`ps` 是**原文自己限定过的**「类第一类」，不是无定语的
    第一类买点 —— 因此仍不得并入 `b1`/`s1`（第 060 课：「盘整背驰无所谓第一类
    买点，只是这样来类比」）。「类第二类」是第 027 课第 8 段那条支路
    （`b2`/`s2`）的名字，不是 `pb`/`ps` 的。
    """
    assert "类第一类" in SignalKind.PB.name_cn
    assert "类第一类" in SignalKind.PS.name_cn
    assert "类第二类" not in SignalKind.PB.name_cn
    assert "类第二类" not in SignalKind.PS.name_cn
    assert SignalKind.PB.is_buy and not SignalKind.PS.is_buy


def test_third_tolerance_is_loose_only(bars, strict):
    """D-35：第三类的容忍度只在非严格模式生效。"""
    from chanlun.chan.signal import _third_kind

    a = _third_kind(list(strict.segments), strict.pivots, "day", SignalMode.STRICT)
    b = _third_kind(list(strict.segments), strict.pivots, "day", SignalMode.LOOSE)
    assert len(b) >= len(a)
    assert all(0.0 < x.price for x in b)


def test_tolerance_suffix_only_when_it_decided(bars, strict):
    """D-35：`（非严格：回试容忍 …）` 只在容忍度**真的翻出这个信号**时才写。

    夹具上四只票的第三类买卖点在严格模式下都有（`b3`/`s3` 计数两边相等），
    严格判据本来就通过 ⇒ 非严格模式的那条 `reason` 必须与严格模式逐字节相同，
    不许宣称用了容忍度。反过来，容忍度真的决定结论时的后缀见
    `test_third_tolerance_only_matters_on_truncated_pivot_*`。
    """
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    third = [s for s in loose if s.kind in (SignalKind.B3, SignalKind.S3)]
    assert third, "夹具上应有第三类买卖点"
    assert all("非严格" not in s.reason for s in third)

    # 两个模式共有的买卖点，`reason` 必须逐字节相同（后缀不许外溢到严格也有的信号）
    key = lambda s: (s.kind.value, s.ts, round(s.price, 6))
    strict_reason = {key(s): s.reason for s in strict.signals}
    shared = [(key(s), s.reason) for s in loose if key(s) in strict_reason]
    assert shared, "夹具上应有严格/非严格共有的买卖点"
    assert all(reason == strict_reason[k] for k, reason in shared)


# --------------------------------------------------------------------------
# 合成线段：造「被 MAX_SEGMENTS 截断的中枢」需要精确控制每一段的端点。
# 构造口径同 `test_signal.py:31-69`（首尾相连、段 i 占 bar 9i…9i+9）。
# --------------------------------------------------------------------------
def _fr(kind, ts, price, src):
    return Fractal(kind=kind, midx=src, ts=ts, high=price, low=price,
                   price=price, src_idx=src)


def _seg(i, direction, low, high, s0, s1):
    ts0, ts1 = f"d{s0:04d}", f"d{s1:04d}"
    if direction == 1:
        start, end = _fr(FractalKind.BOTTOM, ts0, low, s0), _fr(FractalKind.TOP, ts1, high, s1)
    else:
        start, end = _fr(FractalKind.TOP, ts0, high, s0), _fr(FractalKind.BOTTOM, ts1, low, s1)
    st = Stroke(idx=i, direction=direction, start=start, end=end, high=high, low=low,
                src_start=s0, src_end=s1)
    return Segment(
        idx=i, direction=direction, start=st, end=st, high=high, low=low,
        start_stroke_idx=i, end_stroke_idx=i, stroke_count=3,
        status=Status.CONFIRMED, confirmed_at=ts1,
    )


def _zigzag(points) -> list[Segment]:
    """按转折点造一串首尾相连的确认线段：第 i 段 = `points[i] → points[i+1]`。"""
    segs = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        assert a != b, "转折点不能相等"
        lo, hi = (a, b) if a < b else (b, a)
        segs.append(_seg(i, 1 if b > a else -1, float(lo), float(hi), 9 * i, 9 * i + 9))
    for a, b in zip(segs, segs[1:]):
        assert a.end.end.price == b.start.start.price, "合成线段必须首尾相连（价格）"
        assert a.end.end.ts == b.start.start.ts, "合成线段必须首尾相连（时间）"
    return segs


# 8 段封顶的中枢（段 0…7），段 7 是离开段、其终点被带出 [zd, zg]。
# 买：zg=18 / zd=11；卖：zg=19 / zd=12。第 9 段是回试段。
_CAP_HEAD_UP = [20.0, 10.0, 18.0, 11.0, 17.0, 12.0, 16.0, 13.0, 22.0]
_CAP_HEAD_DOWN = [10.0, 20.0, 12.0, 19.0, 13.0, 18.0, 14.0, 17.0, 8.0]


def _capped_pivot(head, back_end):
    """造一个「段数先到上限」的中枢，返回 (线段, 中枢, 回试段)。"""
    from chanlun.chan.pivot import find_pivots

    segs = _zigzag(head + [back_end])
    pivot = find_pivots(segs, "day")[0]
    return segs, pivot, segs[pivot.end_idx + 1]


def test_third_tolerance_only_matters_on_truncated_pivot_buy():
    """D-35：容忍度只有在**中枢组先被段数上限截断**时才可能翻结论（买点侧）。

    `find_pivots` 的延伸循环会把任何与 `[zd, zg]` 有重叠的后续段并进中枢组
    （`_overlaps`）。所以只要组没被 `MAX_SEGMENTS` 截断，真正跌回中枢里的回试
    段会先被并进去、离开段与回试段一起顺延，严格判据 `back.low > ZG` 自动成立，
    容忍度无事可做 —— 这也正是「被否决的替代方案」里 `tol` 在真实数据上极少
    命中（60 只票 1 只）的原因。只有段数上限先到时，回试段才会「有重叠却没被
    并进去」。
    """
    from chanlun.chan.pivot import MAX_SEGMENTS, _overlaps, find_pivots
    from chanlun.chan.signal import THIRD_TOL, _third_kind

    segs, pivot, back = _capped_pivot(_CAP_HEAD_UP, 19.0)
    assert (pivot.zg, pivot.zd) == (18.0, 11.0)
    tol = THIRD_TOL * (pivot.zg - pivot.zd)
    assert tol > 0

    # (a) 回试段整段在 ZG 之上：严格判据本来就通过 ⇒ 两个模式同一条 reason
    strict_a = _third_kind(segs, [pivot], "day", SignalMode.STRICT)
    loose_a = _third_kind(segs, [pivot], "day", SignalMode.LOOSE)
    assert [s.kind for s in strict_a] == [SignalKind.B3]
    assert [s.kind for s in loose_a] == [SignalKind.B3]
    assert loose_a[0].reason == strict_a[0].reason
    assert "非严格" not in loose_a[0].reason

    # (b) 回试低点恰好等于 ZG - tol：`>` 不含等号 ⇒ 两个模式都不出信号
    segs_b, pivot_b, back_b = _capped_pivot(_CAP_HEAD_UP, pivot.zg - tol)
    assert (pivot_b.zg, pivot_b.zd) == (pivot.zg, pivot.zd)
    assert _third_kind(segs_b, [pivot_b], "day", SignalMode.STRICT) == []
    assert _third_kind(segs_b, [pivot_b], "day", SignalMode.LOOSE) == []

    # (c) 再往里 0.01：只有非严格成立，且必须写明用了容忍度
    segs_c, pivot_c, back_c = _capped_pivot(_CAP_HEAD_UP, pivot.zg - tol + 0.01)
    assert _third_kind(segs_c, [pivot_c], "day", SignalMode.STRICT) == []
    loose_c = _third_kind(segs_c, [pivot_c], "day", SignalMode.LOOSE)
    assert [s.kind for s in loose_c] == [SignalKind.B3]
    assert loose_c[0].reason.endswith(f"（非严格：回试容忍 {tol:.4f}）")

    # (d) 回试低点恰好落在 ZG 上：严格判据 `> ZG` 不含等号 ⇒ 严格不出；非严格出，
    #     而这一条**正是**容忍度换来的（回试段确实回到了中枢边缘）
    segs_d, pivot_d, back_d = _capped_pivot(_CAP_HEAD_UP, pivot.zg)
    assert _third_kind(segs_d, [pivot_d], "day", SignalMode.STRICT) == []
    loose_d = _third_kind(segs_d, [pivot_d], "day", SignalMode.LOOSE)
    assert [s.kind for s in loose_d] == [SignalKind.B3]
    assert loose_d[0].reason.endswith(f"（非严格：回试容忍 {tol:.4f}）")

    # (e) 再往外 0.01：严格判据又通过了 ⇒ 后缀必须消失（与 (a) 同一件事）
    segs_e, pivot_e, _ = _capped_pivot(_CAP_HEAD_UP, pivot.zg + 0.01)
    loose_e = _third_kind(segs_e, [pivot_e], "day", SignalMode.LOOSE)
    strict_e = _third_kind(segs_e, [pivot_e], "day", SignalMode.STRICT)
    assert [s.kind for s in strict_e] == [SignalKind.B3]
    assert loose_e[0].reason == strict_e[0].reason

    # 前提：这几段确实是被段数上限截断的 —— 组已满 8 段，而回试段与中枢有重叠，
    # 没有上限时它会被并进去（也就不会再有第三类候选）。
    for pivot_x, back_x in ((pivot_b, back_b), (pivot_c, back_c), (pivot_d, back_d)):
        assert pivot_x.segment_count == MAX_SEGMENTS
        assert _overlaps(back_x, pivot_x.zd, pivot_x.zg)


def test_third_tolerance_only_matters_on_truncated_pivot_sell():
    """D-35 卖点侧：与买点侧对称（`back.high < ZD` / `ZD + tol`）。"""
    from chanlun.chan.pivot import MAX_SEGMENTS, _overlaps
    from chanlun.chan.signal import THIRD_TOL, _third_kind

    segs, pivot, back = _capped_pivot(_CAP_HEAD_DOWN, 11.0)
    assert (pivot.zg, pivot.zd) == (19.0, 12.0)
    tol = THIRD_TOL * (pivot.zg - pivot.zd)

    strict_a = _third_kind(segs, [pivot], "day", SignalMode.STRICT)
    loose_a = _third_kind(segs, [pivot], "day", SignalMode.LOOSE)
    assert [s.kind for s in strict_a] == [SignalKind.S3]
    assert loose_a[0].reason == strict_a[0].reason
    assert "非严格" not in loose_a[0].reason

    segs_b, pivot_b, _ = _capped_pivot(_CAP_HEAD_DOWN, pivot.zd + tol)
    assert _third_kind(segs_b, [pivot_b], "day", SignalMode.STRICT) == []
    assert _third_kind(segs_b, [pivot_b], "day", SignalMode.LOOSE) == []

    segs_c, pivot_c, back_c = _capped_pivot(_CAP_HEAD_DOWN, pivot.zd + tol - 0.01)
    assert _third_kind(segs_c, [pivot_c], "day", SignalMode.STRICT) == []
    loose_c = _third_kind(segs_c, [pivot_c], "day", SignalMode.LOOSE)
    assert [s.kind for s in loose_c] == [SignalKind.S3]
    assert loose_c[0].reason.endswith(f"（非严格：回试容忍 {tol:.4f}）")

    # 回抽高点恰好落在 ZD 上：严格 `high < ZD` 不含等号 ⇒ 严格不出，非严格出
    segs_d, pivot_d, back_d = _capped_pivot(_CAP_HEAD_DOWN, pivot.zd)
    assert _third_kind(segs_d, [pivot_d], "day", SignalMode.STRICT) == []
    loose_d = _third_kind(segs_d, [pivot_d], "day", SignalMode.LOOSE)
    assert [s.kind for s in loose_d] == [SignalKind.S3]
    assert loose_d[0].reason.endswith(f"（非严格：回试容忍 {tol:.4f}）")

    assert pivot_c.segment_count == MAX_SEGMENTS
    assert _overlaps(back_c, pivot_c.zd, pivot_c.zg)
    assert pivot_d.segment_count == MAX_SEGMENTS
    assert _overlaps(back_d, pivot_d.zd, pivot_d.zg)


def test_penetrating_retest_is_absorbed_when_pivot_not_capped():
    """段数没到上限时，跌回中枢内的回试段会被并进中枢组 ⇒ 容忍度无事可做。

    与上面两个测试合起来就是 D-35 的适用边界：同一个中枢，回试段整段在外时
    两个模式都出第三类；回试段跌回中枢内时它被延伸吃掉、两个模式都不出。
    """
    from chanlun.chan.pivot import MAX_SEGMENTS, _overlaps, find_pivots
    from chanlun.chan.signal import _third_kind

    segs = _zigzag([20.0, 10.0, 18.0, 11.0, 17.0, 12.0, 22.0, 19.0])
    pivot = find_pivots(segs, "day")[0]
    assert pivot.segment_count < MAX_SEGMENTS
    assert not _overlaps(segs[pivot.end_idx + 1], pivot.zd, pivot.zg)
    loose = _third_kind(segs, [pivot], "day", SignalMode.LOOSE)
    assert [s.kind for s in loose] == [SignalKind.B3]
    assert "非严格" not in loose[0].reason

    # 同一个中枢，把回试低点压到 ZG 之下：它被并进中枢组，两个模式都不出信号
    segs2 = _zigzag([20.0, 10.0, 18.0, 11.0, 17.0, 12.0, 22.0, 17.3])
    pivot2 = find_pivots(segs2, "day")[0]
    assert pivot2.segment_count == pivot.segment_count + 1
    assert _overlaps(segs2[pivot2.end_idx], pivot2.zd, pivot2.zg)
    assert _third_kind(segs2, [pivot2], "day", SignalMode.STRICT) == []
    assert _third_kind(segs2, [pivot2], "day", SignalMode.LOOSE) == []


@pytest.mark.parametrize("code, tentative_ts", [
    # 状态取自回抽段：段还没被后续段确认时，信号也是 tentative（`confirmed_at` 为 None）
    ("sh.600000", []),
    ("sh.601088", ["2024-12-27"]),
    ("sz.300059", []),
    ("sz.300750", []),
])
def test_second_kind_origin_path_is_loose_only(code, tentative_ts):
    """第 027 课第 8 段：「类似的，在大级别里，如果不出现新低，但可以构成类似
    第二类买点的买点」

    这条支路从盘整背驰出发直接给第二类买卖点（`b2`/`s2`），严格模式不出；
    它也**不**该改写成 `pb`/`ps` —— 那两个是盘整背驰点本身（第 027 课第 7 段
    「类第一类买点」），而这一条是背驰之后的回抽。
    """
    strict = _signals(code, SignalMode.STRICT)
    loose = _signals(code, SignalMode.LOOSE)
    assert not [s for s in strict if s.kind in (SignalKind.B2, SignalKind.S2)], \
        "夹具上严格模式没有 b1/s1，也就没有第一类派生的第二类买卖点"

    origin = [s for s in loose if s.kind in (SignalKind.B2, SignalKind.S2)
              and s.reason.startswith("盘整背驰 ")]
    assert origin, "夹具上应有第 27 课支路发射的第二类买卖点"
    assert [s.ts for s in origin if s.status is Status.TENTATIVE] == tentative_ts
    assert all((s.status is Status.CONFIRMED) == (s.confirmed_at is not None)
               for s in origin)
    # 回抽低点/高点不破背驰段极值 —— 这就是第 8 段「不出现新低」的口径
    for s in origin:
        prior = float(s.reason.split("不破 ")[1])
        if s.kind is SignalKind.B2:
            assert prior < s.price, s.reason
        else:
            assert prior > s.price, s.reason


def test_second_kind_origin_path_pin():
    """第 27 课支路的具体一条：`sh.600000` 2024-01-18 的 `b2`。"""
    loose = _signals(CODE, SignalMode.LOOSE)
    b2 = [s for s in loose if s.kind is SignalKind.B2]
    assert [(s.ts, round(s.price, 6), s.reason) for s in b2] == [
        ("2024-01-18", 5.685687, "盘整背驰 2022-10-31 后回抽低点 5.686 不破 5.671"),
    ]


@pytest.mark.parametrize("code, ts, price, kinds", [
    # 裁决：同一根 bar 上 `ps` 与 `s2` 并存不是重复 —— 这一根既是它自己的盘整
    # 背驰点（第 39 课：与同向前段比 MACD 面积），也是更早一个盘整背驰点之后的
    # 回抽（第 27 课第 8 段：回抽高点不破前低）。两条判据各自独立、来源不同。
    ("sh.600000", "2022-01-13", 7.172031, {"ps", "s2", "s3"}),
    ("sh.600000", "2022-09-15", 6.304453, {"ps", "s2"}),
    ("sz.300750", "2024-05-06", 201.902524, {"ps", "s2", "s3"}),
    # 同一类事实的另外三处（背驰点与第三类 / 第一类派生第二类同 bar），同样不许去重
    ("sh.601088", "2021-02-08", 10.71112, {"b2", "b3"}),
    ("sz.300059", "2021-08-30", 20.421389, {"b3", "pb"}),
    ("sz.300750", "2021-03-25", 143.6074, {"b2", "b3"}),
])
def test_same_bar_multiple_kinds_is_not_duplicated(code, ts, price, kinds):
    """同一 `(ts, price)` 上的多个类型必须都留下 —— **不得按 (ts, price) 去重**。

    去重会丢掉其中一条原文口径（`find_signals` 末尾只按 `(ts, kind.value)` 排序，
    不做任何合并；排序与编号口径也不许因此改动）。
    """
    hit = [s for s in _signals(code, SignalMode.LOOSE)
           if s.ts == ts and round(s.price, 6) == price]
    assert {s.kind.value for s in hit} == kinds
    assert len(hit) == len(kinds)
    assert len({s.kind for s in hit}) == len(hit)
