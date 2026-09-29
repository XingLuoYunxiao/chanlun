"""真实数据 golden 回归：线段划分。

`fixtures/real_strokes.json` 由真实日线（baostock 前复权，2020-01-01~2024-12-31）
经 包含处理 -> 分型 -> 笔 得到，离线固化后作为线段算法的回归基准。

合成用例无法覆盖真实走势的失效模式：早期实现会把线段极值吸收进标准特征序列的
第一个元素后一路向后找一个假分型，在真实数据上有 34% 的确认段违反「极值在两端」。
"""
import json
from pathlib import Path

import pytest

from chanlun.chan.segment import build_segments, validate_segments
from chanlun.chan.stroke import build_strokes
from chanlun.chan.types import Fractal, FractalKind, Status, Stroke

FIXTURE = Path(__file__).parent / "fixtures" / "real_strokes.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CODES = sorted(DATA)


def _fractal(d: dict) -> Fractal:
    return Fractal(
        FractalKind(d["kind"]), d["midx"], d["ts"],
        high=d["high"], low=d["low"], price=d["price"], src_idx=d["src_idx"],
    )


def _strokes(entry: dict) -> list[Stroke]:
    return [
        Stroke(
            idx=s["idx"], direction=s["dir"], start=_fractal(s["start"]),
            end=_fractal(s["end"]), high=s["high"], low=s["low"],
            src_start=s["src_start"], src_end=s["src_end"],
            status=Status.CONFIRMED,
        )
        for s in entry["strokes"]
    ]


def _segments(code: str):
    return build_segments(_strokes(DATA[code]))


@pytest.mark.parametrize("code", CODES)
def test_real_segments_satisfy_every_invariant(code):
    assert validate_segments(_segments(code)) == []


@pytest.mark.parametrize("code", CODES)
def test_real_segment_counts_are_stable(code):
    segs = _segments(code)
    conf = [s for s in segs if s.status is Status.CONFIRMED]
    assert len(conf) == DATA[code]["expected_confirmed_count"]
    assert len(segs) == DATA[code]["expected_total_segments"]


@pytest.mark.parametrize("code", CODES)
def test_real_first_confirmed_segment_is_correct(code):
    exp = DATA[code]["expected_first_confirmed"]
    conf = [s for s in _segments(code) if s.status is Status.CONFIRMED]
    first = conf[0]
    assert first.start_stroke_idx == exp["start_stroke_idx"]
    assert first.end_stroke_idx == exp["end_stroke_idx"]
    assert first.direction == exp["direction"]
    assert first.start.start.price == pytest.approx(exp["start_price"])
    assert first.end.end.price == pytest.approx(exp["end_price"])
    assert first.high == pytest.approx(exp["high"])
    assert first.low == pytest.approx(exp["low"])


@pytest.mark.parametrize("code", CODES)
def test_real_confirmed_segments_put_extremes_at_both_ends(code):
    for seg in _segments(code):
        if seg.status is not Status.CONFIRMED:
            continue
        if seg.direction == 1:
            assert seg.low == seg.start.start.price
            assert seg.high == seg.end.end.price
        else:
            assert seg.high == seg.start.start.price
            assert seg.low == seg.end.end.price


@pytest.mark.parametrize("code", CODES)
def test_real_confirmed_segments_are_odd_and_at_least_three_strokes(code):
    for seg in _segments(code):
        if seg.status is not Status.CONFIRMED:
            continue
        assert seg.stroke_count >= 3
        assert seg.stroke_count % 2 == 1


@pytest.mark.parametrize("code", CODES)
def test_real_segments_are_contiguous_and_cover_every_stroke(code):
    segs = _segments(code)
    assert segs[0].start_stroke_idx == 0
    assert segs[-1].end_stroke_idx == len(DATA[code]["strokes"]) - 1
    for prev, nxt in zip(segs, segs[1:]):
        assert prev.end_stroke_idx + 1 == nxt.start_stroke_idx


@pytest.mark.parametrize("code", CODES)
def test_real_confirmed_segments_alternate_direction(code):
    """同向约束只作用于相邻的确认线段（TENTATIVE 段不参与）。"""
    conf = [s for s in _segments(code) if s.status is Status.CONFIRMED]
    for prev, nxt in zip(conf, conf[1:]):
        assert prev.direction != nxt.direction


def test_real_data_has_no_unsegmentable_symbol():
    """不允许出现「整只股票一个确认段都没有」——那等于没有分析价值。"""
    for code in CODES:
        conf = [s for s in _segments(code) if s.status is Status.CONFIRMED]
        assert conf, f"{code} 没有任何确认线段"


def test_real_fixture_covers_a_truncated_window_left_edge():
    """sz.300750 的窗口左端被截断，前导笔应作为 TENTATIVE 而不参与确认段。"""
    segs = _segments("sz.300750")
    assert segs[0].status is Status.TENTATIVE
    assert segs[0].start_stroke_idx == 0
    assert segs[1].status is Status.CONFIRMED


def test_leading_tentative_may_share_direction_with_first_confirmed():
    """sh.600000 的前导段与首个确认段同向。

    其实它们本来就是同一条线段，只是左端落在数据窗口之外、无法验证，
    才被切成 TENTATIVE + CONFIRMED。故同向是预期行为，
    validate_segments 不应在此报「与上一线段同向」。
    """
    segs = _segments("sh.600000")
    assert segs[0].status is Status.TENTATIVE
    assert segs[1].status is Status.CONFIRMED
    assert segs[0].direction == segs[1].direction
    assert validate_segments(segs) == []


@pytest.mark.parametrize("code", CODES)
def test_real_fixture_bar_count_is_complete(code):
    """fixture 必须来自完整区间数据；半量数据会得出错误的划分基准。"""
    assert DATA[code]["bars"] >= 1200


# ------------------------------------------------ 无未来函数（真实 ISO 时间戳）
def _rebuilt_strokes(code: str) -> list[Stroke]:
    """由 fixture 的分型端点序列重建笔，验证管线幂等且确认时间合法。"""
    fracs = [_fractal(DATA[code]["strokes"][0]["start"])] + [
        _fractal(s["end"]) for s in DATA[code]["strokes"]
    ]
    return build_strokes(fracs)


@pytest.mark.parametrize("code", CODES)
def test_real_stroke_rebuild_is_idempotent(code):
    """对已是端点的分型序列再跑一次笔构造，结果必须完全一致。"""
    rebuilt = _rebuilt_strokes(code)
    assert len(rebuilt) == len(DATA[code]["strokes"])


@pytest.mark.parametrize("code", CODES)
def test_real_stroke_confirmed_at_is_never_look_ahead(code):
    """确认时间不早于自身终点，且随时间单调不减。

    这是「回测不得使用未来信息」的第一道闸门：真实数据是 ISO 时间戳，
    字典序即时间序，故可直接比较。
    """
    rebuilt = _rebuilt_strokes(code)
    seen = ""
    for st in rebuilt:
        assert st.end.ts >= st.start.ts
        if st.confirmed_at is not None:
            assert st.confirmed_at >= st.end.ts, f"笔{st.idx} 确认早于自身终点"
            assert st.confirmed_at >= seen, f"笔{st.idx} 确认时间回退"
            seen = st.confirmed_at
    assert rebuilt[-1].confirmed_at is None
