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


def test_third_kind_matches_find_signals_and_is_mode_independent(bars, strict):
    """D-41：第三类买卖点的判据不再随模式变化 —— `THIRD_TOL` 已整体删除。

    第 20 课《缠中说禅走势中枢》的第三类判据是**纯位置**判据：「其低点不跌破
    ZG」；原文没有任何容忍量。本项目曾用 `THIRD_TOL = 0.1 × (ZG − ZD)`
    （工程口径，无原文依据）放宽它，2026-10-04 随 `capped` 收口一并删除：
    容忍度唯一的正当理由（补偿被段数上限掐停的中枢）在 `capped` 中枢不再产出
    信号之后消失了，而它唯一还能翻动的信号，其回试段恰恰是**已经跌回中枢里**
    的那种 —— 与原文「不跌破 ZG」直接冲突。
    """
    from chanlun.chan.signal import _third_kind

    a = _third_kind(list(strict.segments), strict.pivots, "day")
    assert [s.kind.value for s in a] == [
        s.kind.value for s in strict.signals
        if s.kind in (SignalKind.B3, SignalKind.S3)]
    assert a, "夹具上应有第三类买卖点"
    assert all(0.0 < x.price for x in a)


def test_third_kind_reason_has_no_tolerance_suffix(bars, strict):
    """`（非严格：回试容忍 …）` 这个后缀必须彻底消失。

    `THIRD_TOL` 与 `_third_kind` 的 `mode` 形参都已删除（D-41），所以不可能再有
    任何 `reason` 宣称用了容忍度；同时两个模式共有的买卖点 `reason` 仍必须逐字节
    相同（这条口径从 D-35 起就没变）。
    """
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    third = [s for s in loose if s.kind in (SignalKind.B3, SignalKind.S3)]
    assert third, "夹具上应有第三类买卖点"
    assert all("非严格" not in s.reason and "容忍" not in s.reason for s in third)

    # 两个模式共有的买卖点，`reason` 必须逐字节相同
    key = lambda s: (s.kind.value, s.ts, round(s.price, 6))
    strict_reason = {key(s): s.reason for s in strict.signals}
    shared = [(key(s), s.reason) for s in loose if key(s) in strict_reason]
    assert shared, "夹具上应有严格/非严格共有的买卖点"
    assert all(reason == strict_reason[k] for k, reason in shared)


def test_third_kind_and_entering_have_no_mode_parameter():
    """结构护栏：`mode` 形参删掉了，就不该再有人按模式给它传参（D-41）。"""
    import inspect

    from chanlun.chan.signal import _entering_and_leaving, _third_kind

    assert "mode" not in inspect.signature(_third_kind).parameters
    assert "mode" not in inspect.signature(_entering_and_leaving).parameters


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


# 8 段封顶的中枢（段 0…7），段 7 的终点被带出 [zd, zg]，但**段 8 又跌回区间内**，
# 所以延伸是被 `MAX_SEGMENTS` 掐停的（`capped is True`）。
# 买：zg=18 / zd=11，段 8 是 22→19（在 ZG 之上）；卖：zg=19 / zd=12，段 8 是 8→11
# （在 ZD 之下）。也就是说：位置判据「回试不跌破 ZG / 不升破 ZD」**满足**，所以
# 「不产出信号」只可能来自 `capped` 门（D-41），不是位置不满足。
_CAP_HEAD_UP = [20.0, 10.0, 18.0, 11.0, 17.0, 12.0, 16.0, 13.0, 22.0]
_CAP_HEAD_DOWN = [10.0, 20.0, 12.0, 19.0, 13.0, 18.0, 14.0, 17.0, 8.0]

# 同形状、但**没到上限**（6 段）的中枢，用于正对照：它必须照常产出第三类。
_OK_HEAD_UP = [20.0, 10.0, 18.0, 11.0, 17.0, 12.0, 22.0, 19.0]
_OK_HEAD_DOWN = [10.0, 20.0, 12.0, 19.0, 13.0, 18.0, 8.0, 11.0]


def _capped_pivot(head, back_end):
    """造一个「段数先到上限」的中枢，返回 (线段, 中枢, 回试段)。"""
    from chanlun.chan.pivot import find_pivots

    segs = _zigzag(head + [back_end])
    pivot = find_pivots(segs, "day")[0]
    return segs, pivot, segs[pivot.end_idx + 1]


def _uncapped_pivot(head):
    """造一个同形状、未到段数上限的中枢，返回 (线段, 中枢)。"""
    from chanlun.chan.pivot import find_pivots

    segs = _zigzag(head)
    return segs, find_pivots(segs, "day")[0]


def test_capped_pivot_emits_no_third_kind_buy():
    """D-41：被段数上限掐停的中枢**不产出**第三类买点。

    第 33 课《走势的多义性》：「中枢的延伸不能超过5段，也就是一旦出现6段的延伸，
    加上形成中枢本身那三段，就构成更大级别的中枢了。」本级别最多 8 段。
    `capped` 的定义是「到了上限、而下一段仍与 `[ZD, ZG]` 重叠」——也就是说组内
    每一段（含 `end_idx`）都**没有**离开过区间（第 20 课中心定理一：「走势中枢的
    延伸等价于任意区间[dn，gn]与[ZD，ZG]有重叠。」）。而第 20 课的第三类买点要求
    「一个次级别走势类型**向上离开**缠中说禅走势中枢，然后以一个次级别走势类型
    回试，其低点不跌破ZG」——离开这个前提在这里不成立，拿 `segs[end_idx]` 冒充
    离开段没有原文依据。

    正对照在同一条测试里：同形状、**没到上限**的中枢必须照常产出 B3。
    没有这个正对照，这条护栏量到的可能只是「这批线段压根没构成中枢」。

    **这条门在 `THIRD_TOL` 删除之后是结构性 no-op**（据实记录）：capped 的定义
    本身就要求回试段与 `[ZD, ZG]` 重叠，所以第 20 课的位置判据 `back.low > ZG`
    在 capped 中枢上**恒不成立**，删掉门也一样不产出。它的价值是把「capped 中枢
    的 `end_idx` 不是离开段」这条口径写成可执行的不变量 —— 否则将来任何一次
    「放宽判据」都会悄悄把延伸中的中枢重新误报成第三类。
    """
    from chanlun.chan.pivot import MAX_SEGMENTS
    from chanlun.chan.signal import _third_kind

    segs, pivot, back = _capped_pivot(_CAP_HEAD_UP, 18.0)
    assert (pivot.zg, pivot.zd) == (18.0, 11.0)
    assert pivot.capped is True
    assert pivot.segment_count == MAX_SEGMENTS
    assert back.low <= pivot.zg, "回试段仍在中枢边缘内 ⇒ 这正是 capped 的定义"
    assert _third_kind(segs, [pivot], "day") == []

    segs_ok, pivot_ok = _uncapped_pivot(_OK_HEAD_UP)
    assert pivot_ok.capped is False
    assert pivot_ok.segment_count < MAX_SEGMENTS
    assert [s.kind for s in _third_kind(segs_ok, [pivot_ok], "day")] == [SignalKind.B3]


def test_capped_pivot_emits_no_third_kind_sell():
    """卖点侧与买点侧对称（第 20 课：「其高点不升破ZD」）。"""
    from chanlun.chan.pivot import MAX_SEGMENTS
    from chanlun.chan.signal import _third_kind

    segs, pivot, back = _capped_pivot(_CAP_HEAD_DOWN, 12.0)
    assert (pivot.zg, pivot.zd) == (19.0, 12.0)
    assert pivot.capped is True
    assert pivot.segment_count == MAX_SEGMENTS
    assert back.high >= pivot.zd, "回抽段仍在中枢边缘内 ⇒ 这正是 capped 的定义"
    assert _third_kind(segs, [pivot], "day") == []

    segs_ok, pivot_ok = _uncapped_pivot(_OK_HEAD_DOWN)
    assert pivot_ok.capped is False
    assert [s.kind for s in _third_kind(segs_ok, [pivot_ok], "day")] == [SignalKind.S3]


def test_capped_pivot_emits_no_leave_dependent_signal():
    """`_entering_and_leaving` 必须跳过 `capped` 中枢（第一类/趋势背驰的来源）。

    这一处**不是 no-op**：`segs[p.end_idx]` 的方向完全可能与趋势同向（下面断言了
    它同向、且未作废），所以没有门的话这里会照常返回一对 (中枢, 离开段)，交给
    `_first_kind` 去比 MACD 力度 —— 比的是一个仍在延伸的中枢里的段，比出来的
    力度没有意义。全市场实测：趋势背驰 180 条里 64 条落在 capped 中枢上。

    它与 `divergence.py::_leaving_legs` 是同一判据的两份实现，必须同步（D-41）；
    两份实现的一致性由 `tests/chan/test_divergence.py::test_trend_divergence_matches_b1_s1`
    守住（双向相等）。
    """
    from chanlun.chan.signal import _dead, _entering_and_leaving

    segs, pivot, _ = _capped_pivot(_CAP_HEAD_UP, 18.0)
    assert pivot.capped is True
    assert segs[pivot.end_idx].direction == 1
    assert not _dead(segs[pivot.end_idx])
    assert _entering_and_leaving(segs, [pivot], 1) == []

    segs_ok, pivot_ok = _uncapped_pivot(_OK_HEAD_UP)
    pairs = _entering_and_leaving(segs_ok, [pivot_ok], 1)
    assert [p.idx for p, _, _ in pairs] == [0]
    assert pairs[0][1] is segs_ok[pivot_ok.end_idx]


def test_tentative_pivot_still_emits_third_kind():
    """裁决（2026-10-04）：只 gate `capped`，**不** gate 中枢 `status`。

    `ARCHITECTURE.md` D-26 的「TENTATIVE 线段不参与中枢构造、也不参与延伸」约束的
    是**线段**，不是**中枢**：TENTATIVE 中枢是每一只在交易中的票的正常右端形态
    （`find_pivots` 在 `back_of` 取不到下一确认段时就这么标），连它一起 gate 会把
    大量合法的实时信号静默删掉。这类信号本身是 `Status.TENTATIVE`，回测已经把它
    挡在外面，所以它是**显示口径**问题，不是判据缺陷。

    合成夹具：7 段，前 6 段成枢、第 7 段（回试段）标成 TENTATIVE ⇒ 中枢 TENTATIVE
    但**未** capped，第三类买点照常产出。
    """
    from dataclasses import replace

    from chanlun.chan.pivot import find_pivots
    from chanlun.chan.signal import _third_kind

    segs = _zigzag(_OK_HEAD_UP)
    segs[6] = replace(segs[6], status=Status.TENTATIVE, confirmed_at=None)
    pivot = find_pivots(segs, "day")[0]
    assert pivot.status is Status.TENTATIVE
    assert pivot.capped is False

    got = _third_kind(segs, [pivot], "day")
    assert [s.kind for s in got] == [SignalKind.B3]
    assert got[0].status is Status.TENTATIVE


def test_penetrating_retest_is_absorbed_when_pivot_not_capped():
    """段数没到上限时，跌回中枢内的回试段会被并进中枢组 ⇒ 根本不出第三类。

    这条是 `capped` 门（D-41）的**适用边界**：同一个中枢，回试段整段在外时出
    第三类；回试段跌回中枢内时它被延伸吃掉、连第三类候选都不存在。也就是说
    `capped` 门收掉的不是「位置不满足」的信号，而是「位置满足但离开段不存在」
    的信号 —— 前者本来就不产出。
    """
    from chanlun.chan.pivot import MAX_SEGMENTS, _overlaps, find_pivots
    from chanlun.chan.signal import _third_kind

    segs, pivot = _uncapped_pivot(_OK_HEAD_UP)
    assert pivot.segment_count < MAX_SEGMENTS
    assert not _overlaps(segs[pivot.end_idx + 1], pivot.zd, pivot.zg)
    assert [s.kind for s in _third_kind(segs, [pivot], "day")] == [SignalKind.B3]

    # 同一个中枢，把回试低点压到 ZG 之下：它被并进中枢组，两个模式都不出信号
    segs2 = _zigzag([20.0, 10.0, 18.0, 11.0, 17.0, 12.0, 22.0, 17.3])
    pivot2 = find_pivots(segs2, "day")[0]
    assert pivot2.capped is False
    assert pivot2.segment_count == pivot.segment_count + 1
    assert _overlaps(segs2[pivot2.end_idx], pivot2.zd, pivot2.zg)
    assert _third_kind(segs2, [pivot2], "day") == []


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
