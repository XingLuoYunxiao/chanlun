"""未确认状态机（Task 10）测试。

核心不变量：
1. 结构对象的 ID 只由「类型 + 原始 bar 索引」决定，跨快照稳定；
2. `confirmed_at` 一旦写入永不改变（写一次永不改）；
3. 被推翻的 tentative 对象转 INVALIDATED 但保留在历史中（供回测审计）；
4. 回测只能读 `confirmed_at <= as_of` 的 CONFIRMED 对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from chanlun.chan.state import backtestable, confirm_at, object_id, reconcile
from chanlun.chan.types import (
    Fractal,
    FractalKind,
    Status,
    Stroke,
    to_jsonable,
)


# ---------------- 测试替身 ----------------
# Task 11 才定义真正的 Snapshot / Pivot / Signal，这里用 duck typing 的最小替身，
# 以验证 state.py 不依赖任何具体类。


def top(src_idx: int, price: float = 10.0, midx: int | None = None) -> Fractal:
    return Fractal(kind=FractalKind.TOP, midx=src_idx if midx is None else midx,
                   ts=f"d{src_idx}", high=price, low=price - 1, price=price,
                   src_idx=src_idx)


def bottom(src_idx: int, price: float = 5.0, midx: int | None = None) -> Fractal:
    return Fractal(kind=FractalKind.BOTTOM, midx=src_idx if midx is None else midx,
                   ts=f"d{src_idx}", high=price + 1, low=price, price=price,
                   src_idx=src_idx)


def stroke(idx: int, src_start: int, src_end: int, direction: int = 1,
           status: Status = Status.TENTATIVE, confirmed_at: str | None = None,
           midx_start: int | None = None) -> Stroke:
    """构造一笔。`midx_start` 用于制造「idx 相同但原始 bar 索引不同」的对象。"""
    return Stroke(
        idx=idx,
        direction=direction,
        start=bottom(src_start, midx=src_start if midx_start is None else midx_start),
        end=top(src_end, midx=src_end if midx_start is None else midx_start),
        high=10.0,
        low=5.0,
        src_start=src_start,
        src_end=src_end,
        status=status,
        confirmed_at=confirmed_at,
    )


@dataclass(frozen=True)
class FakeSegment:
    """线段替身：同样只用原始 bar 索引做 ID。"""

    idx: int
    src_start: int
    src_end: int
    status: Status = Status.TENTATIVE
    confirmed_at: str | None = None


@dataclass(frozen=True)
class FakePivot:
    """中枢替身：故意带一个**合成**索引 `start_stroke_idx`，ID 不得使用它。"""

    idx: int
    src_start: int
    src_end: int
    start_stroke_idx: int = 0
    status: Status = Status.TENTATIVE
    confirmed_at: str | None = None


@dataclass(frozen=True)
class FakeSignal:
    idx: int
    src_start: int
    src_end: int
    kind: str = "buy1"
    status: Status = Status.TENTATIVE
    confirmed_at: str | None = None


@dataclass(frozen=True)
class FakeSnapshot:
    as_of: str
    strokes: tuple = ()
    segments: tuple = ()
    pivots: tuple = ()
    signals: tuple = ()


def snap(as_of: str, strokes=(), segments=(), pivots=(), signals=()) -> FakeSnapshot:
    return FakeSnapshot(as_of=as_of, strokes=tuple(strokes), segments=tuple(segments),
                        pivots=tuple(pivots), signals=tuple(signals))


# ---------------- 稳定 ID ----------------
def test_object_ids_are_stable_across_steps():
    """同一对象在不同快照间 ID 必须一致；不同对象 ID 必须不同。"""
    a1 = stroke(0, 120, 180)
    a2 = stroke(0, 120, 180)
    assert object_id(a1) == object_id(a2) == "stroke@120-180"

    prev = snap("d200", strokes=[a1])
    new = snap("d210", strokes=[a2])
    out = reconcile(prev, new)
    assert object_id(out.strokes[0]) == object_id(prev.strokes[0])


def test_ids_use_raw_bar_indices_not_positional_idx():
    """两个对象 idx 相同但原始 bar 索引不同，ID 必须不同，也不能互相串味。"""
    x = stroke(idx=0, src_start=10, src_end=20)
    y = stroke(idx=0, src_start=30, src_end=40, direction=-1)
    assert object_id(x) == "stroke@10-20"
    assert object_id(y) == "stroke@30-40"
    assert object_id(x) != object_id(y)

    # 位置索引/合成索引变化都不影响 ID
    z = stroke(idx=99, src_start=10, src_end=20)
    assert object_id(z) == object_id(x)
    p = FakePivot(idx=0, src_start=50, src_end=90, start_stroke_idx=7)
    p2 = FakePivot(idx=3, src_start=50, src_end=90, start_stroke_idx=42)
    assert object_id(p) == object_id(p2) == "fakepivot@50-90"


# ---------------- confirmed_at 写一次永不改 ----------------
def test_confirm_at_never_overwrites_existing_timestamp():
    s = confirm_at(stroke(0, 120, 180), "d180")
    assert s.status is Status.CONFIRMED
    assert s.confirmed_at == "d180"

    again = confirm_at(s, "d999")
    assert again.confirmed_at == "d180"
    assert again is s or again == s


def test_confirmed_at_is_immutable_after_later_bars():
    """已确认对象的 confirmed_at 在后续 bar 上永不改变。"""
    s = confirm_at(stroke(0, 120, 180), "d180")
    snap1 = snap("d180", strokes=[s])

    for as_of in ("d190", "d260", "d400"):
        # 全量重算把该笔重算成 tentative 且 confirmed_at=None
        recalc = stroke(0, 120, 180, status=Status.TENTATIVE, confirmed_at=None)
        snap1 = reconcile(snap1, snap(as_of, strokes=[recalc]))
        assert snap1.strokes[0].confirmed_at == "d180"
        assert snap1.strokes[0].status is Status.CONFIRMED


def test_confirmed_never_regresses_to_tentative():
    """prev 已 CONFIRMED 而新快照写 TENTATIVE：不得回退，也不得清空 confirmed_at。"""
    prev = snap("d200", strokes=[stroke(0, 120, 180, status=Status.CONFIRMED,
                                        confirmed_at="d180")])
    new = snap("d210", strokes=[stroke(0, 120, 180, status=Status.TENTATIVE,
                                       confirmed_at=None)])
    out = reconcile(prev, new)
    assert out.strokes[0].status is Status.CONFIRMED
    assert out.strokes[0].confirmed_at == "d180"

    # 再串一次也不回退
    out2 = reconcile(out, snap("d220", strokes=[stroke(0, 120, 180)]))
    assert out2.strokes[0].status is Status.CONFIRMED
    assert out2.strokes[0].confirmed_at == "d180"


def test_tentative_to_confirmed_stamps_triggering_bar():
    """TENTATIVE -> CONFIRMED：写入新对象自带 confirmed_at；缺失时用 as_of。"""
    s = stroke(0, 120, 180)
    prev = snap("d180", strokes=[s])
    new = snap("d200", strokes=[stroke(0, 120, 180, status=Status.CONFIRMED, confirmed_at=None)])
    out = reconcile(prev, new)
    assert out.strokes[0].status is Status.CONFIRMED
    assert out.strokes[0].confirmed_at == "d200"   # 回退到 new.as_of


def test_tentative_to_tentative_stays_none():
    s = stroke(0, 120, 180)
    out = reconcile(snap("d180", strokes=[s]), snap("d190", strokes=[s]))
    assert out.strokes[0].status is Status.TENTATIVE
    assert out.strokes[0].confirmed_at is None


def test_first_snapshot_prev_is_none():
    out = reconcile(None, snap("d180", strokes=[stroke(0, 120, 180)]))
    assert len(out.strokes) == 1
    assert out.strokes[0].status is Status.TENTATIVE


# ---------------- 失效但留痕 ----------------
def test_invalidated_tentative_is_retained():
    """消失的 tentative 转 INVALIDATED 并保留，供回测审计。"""
    gone = stroke(0, 120, 180)
    keep = stroke(1, 200, 260)
    prev = snap("d200", strokes=[gone, keep])
    new = snap("d210", strokes=[keep])
    out = reconcile(prev, new)

    ids = [object_id(x) for x in out.strokes]
    assert ids == ["stroke@200-260", "stroke@120-180"]      # 新的在前，失效的追加
    dead = out.strokes[1]
    assert dead.status is Status.INVALIDATED
    assert dead.confirmed_at is None


def test_invalidated_confirmed_object_is_retained_with_timestamp():
    """已确认结构被推翻：同样保留为 INVALIDATED，但 confirmed_at 不得被抹掉。"""
    gone = stroke(0, 120, 180, status=Status.CONFIRMED, confirmed_at="d180")
    prev = snap("d200", strokes=[gone])
    out = reconcile(prev, snap("d210", strokes=[]))
    assert len(out.strokes) == 1
    assert out.strokes[0].status is Status.INVALIDATED
    assert out.strokes[0].confirmed_at == "d180"

    # 再往后也仍然保留
    out2 = reconcile(out, snap("d220", strokes=[]))
    assert len(out2.strokes) == 1
    assert out2.strokes[0].confirmed_at == "d180"


def test_invalidated_is_not_resurrected_as_new():
    """失效对象必须以 INVALIDATED 追加，不能变成两个对象。"""
    gone = stroke(0, 120, 180)
    out = reconcile(snap("d200", strokes=[gone]), snap("d210", strokes=[]))
    out = reconcile(out, snap("d220", strokes=[]))
    assert [x.status for x in out.strokes] == [Status.INVALIDATED]


# ---------------- 四类字段都参与 ----------------
def test_all_four_fields_are_reconciled():
    prev = snap("d200", strokes=[stroke(0, 120, 180, status=Status.CONFIRMED,
                                        confirmed_at="d180")],
                segments=[FakeSegment(0, 95, 260, Status.CONFIRMED, "d260")],
                pivots=[FakePivot(0, 300, 400, 2, Status.CONFIRMED, "d400")],
                signals=[FakeSignal(0, 410, 430, "buy1", Status.CONFIRMED, "d430")])
    new = snap("d450",
               strokes=[stroke(0, 120, 180, status=Status.TENTATIVE, confirmed_at=None)],
               segments=[FakeSegment(0, 95, 260)],
               pivots=[FakePivot(0, 300, 400, 9)],
               signals=[FakeSignal(0, 410, 430, "buy1")])
    out = reconcile(prev, new)
    assert out.strokes[0].confirmed_at == "d180"
    assert out.segments[0].confirmed_at == "d260"
    assert out.pivots[0].confirmed_at == "d400"
    assert out.signals[0].confirmed_at == "d430"
    assert all(x.status is Status.CONFIRMED
               for x in (out.strokes[0], out.segments[0], out.pivots[0], out.signals[0]))


def test_signals_field_is_also_reconciled():
    """signals 单独验证：确认要写入、消失要失效留痕。"""
    live = FakeSignal(0, 410, 430, "buy1")
    dead = FakeSignal(1, 500, 520, "sell1")
    prev = snap("d430", signals=[live, dead])
    new = snap("d520", signals=[FakeSignal(0, 410, 430, "buy1",
                                           status=Status.CONFIRMED, confirmed_at="d430")])
    out = reconcile(prev, new)
    assert object_id(out.signals[0]) == "fakesignal@410-430"
    assert out.signals[0].status is Status.CONFIRMED
    assert out.signals[0].confirmed_at == "d430"
    assert object_id(out.signals[1]) == "fakesignal@500-520"
    assert out.signals[1].status is Status.INVALIDATED


def test_only_present_fields_are_touched():
    """没有 strokes 字段的快照也必须能处理（duck typing，不假设类）。"""

    @dataclass(frozen=True)
    class Minimal:
        as_of: str
        segments: tuple = ()

    out = reconcile(None, Minimal(as_of="d1", segments=(FakeSegment(0, 1, 2),)))
    assert len(out.segments) == 1
    assert not hasattr(out, "strokes")

    out2 = reconcile(Minimal(as_of="d1", segments=(FakeSegment(0, 1, 2),)),
                     Minimal(as_of="d2", segments=()))
    assert out2.segments[0].status is Status.INVALIDATED


# ---------------- 确定性 ----------------
def test_reconcile_is_deterministic():
    """同输入两次结果完全相同（字段顺序也确定）。"""
    prev = snap("d200",
                strokes=[stroke(0, 120, 180), stroke(1, 200, 260),
                         stroke(2, 300, 360, status=Status.CONFIRMED, confirmed_at="d300")],
                segments=[FakeSegment(0, 95, 260), FakeSegment(1, 260, 400)])
    new = snap("d260", strokes=[stroke(1, 200, 260), stroke(3, 400, 460)],
               segments=[FakeSegment(0, 95, 260)])
    a = reconcile(prev, new)
    b = reconcile(prev, new)
    assert to_jsonable(a) == to_jsonable(b)
    assert [object_id(x) for x in a.strokes] == [object_id(x) for x in b.strokes]
    # 新的字段顺序在前，失效的按 prev 顺序追加
    assert [object_id(x) for x in a.strokes] == ["stroke@200-260", "stroke@400-460",
                                                 "stroke@120-180", "stroke@300-360"]
    assert [object_id(x) for x in a.segments] == ["fakesegment@95-260",
                                                  "fakesegment@260-400"]


# ---------------- 回测唯一入口 ----------------
def test_backtest_filter_excludes_unconfirmed():
    """只允许 confirmed 且 confirmed_at <= bar.ts。"""
    items = [
        stroke(0, 120, 180, status=Status.TENTATIVE, confirmed_at=None),
        stroke(1, 200, 260, status=Status.CONFIRMED, confirmed_at="d260"),
        stroke(2, 300, 360, status=Status.CONFIRMED, confirmed_at="d400"),   # 未来函数
        stroke(3, 400, 460, status=Status.INVALIDATED, confirmed_at="d300"),
        stroke(4, 500, 560, status=Status.CONFIRMED, confirmed_at=None),     # 无戳
    ]
    got = backtestable(items, "d320")
    assert [object_id(x) for x in got] == ["stroke@200-260"]

    # 边界：等于 as_of 的那一根必须算进来（<=）
    assert [object_id(x) for x in backtestable(items, "d260")] == ["stroke@200-260"]
    assert backtestable(items, "d259") == []
    assert [object_id(x) for x in backtestable(items, "d999")] == \
        ["stroke@200-260", "stroke@300-360"]
    assert backtestable([], "d999") == []


# ---------------- 与持久化/重放兼容 ----------------
def test_reconciled_snapshot_is_json_serializable():
    """reconcile 结果必须可直接序列化（跨进程重放同一个快照）。"""
    import json

    out = reconcile(None, snap("d180", strokes=[stroke(0, 120, 180)],
                               signals=[FakeSignal(0, 410, 430)]))
    payload = json.dumps(to_jsonable(out), ensure_ascii=False)
    assert "tentative" in payload


def test_later_steps_carry_previous_history_forward():
    """多步串行：每步结果可继续作为下一步 prev，历史逐层累积。"""
    s0 = snap("d100", strokes=[stroke(0, 10, 20)])
    s1 = reconcile(s0, snap("d200", strokes=[stroke(0, 10, 20), stroke(1, 30, 40)]))
    s2 = reconcile(s1, snap("d300", strokes=[stroke(1, 30, 40, status=Status.CONFIRMED,
                                                      confirmed_at="d300")]))
    assert [object_id(x) for x in s2.strokes] == ["stroke@30-40", "stroke@10-20"]
    assert s2.strokes[0].confirmed_at == "d300"
    assert s2.strokes[1].status is Status.INVALIDATED
    assert replace(s2.strokes[0]).idx == 1
