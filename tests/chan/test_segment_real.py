"""真实数据 golden 回归：线段划分。

`fixtures/real_strokes.json` 由真实日线（baostock 前复权，2020-01-01~2024-12-31）
经 包含处理 -> 分型 -> 笔 得到，离线固化后作为线段算法的回归基准。

合成用例无法覆盖真实走势的失效模式，目前已知三个：

1. **极值被吸收进标准特征序列的第一个元素**：早期实现会一路向后找一个假分型，在
   真实数据上曾出现大量确认段违反「极值在两端」（第78课）。
2. **标准特征序列里残留包含关系**（见 `test_real_standard_feature_sequence_is_
   containment_free`）：`_merge_feature` 原先只做单趟前向合并，而第65课明说「包含
   关系，不符合传递律」，合并出的新元素可能反过来吞掉前一个元素。残留的包含关系
   让第62课的分型判据永远判不出来，真极值被永久跳过。全历史 30 只标的上实测 8426
   处；`sz.300760` 日线因此把 6 年半的走势划成「1 笔 + 127 笔 + 4 笔」三段。
3. **左端起点**（见 `test_left_edge_takes_the_start_that_keeps_the_most_strokes_in_
   segments`）：数据文件的第一根 K 线往往只是**数据商的截断点**，不是线段起点
   （`600180` 日线从 2021-01-04 开始，该股 1998 年就上市了）。原文没有规定被截断的
   左端怎么起段（第 67/77/78 课都只说划分唯一）⇒ 只能取工程判据：让划分尽可能完整，
   前缀交给 TENTATIVE 前导段。夹具里 `sh.601088` 就分辨得出来（笔 0 起划 10 段、
   笔 3 起划 11 段），但更极端的例子只在长历史上显形：`600180` 全史 114 笔只划出
   5 段、首段 73 笔（改后 19 段、首段 16 笔）。
"""
import json
from pathlib import Path

import pytest

from chanlun.chan.segment import (
    Lesson6768Policy,
    _feature_seq,
    _first_three_overlap,
    _merge_feature,
    build_segments,
    validate_segments,
)
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
    """不变量全过；必须传 strokes，否则第 78 课标准化/次序两项检查会被跳过。"""
    assert validate_segments(_segments(code), _strokes(DATA[code])) == []


@pytest.mark.parametrize("code", CODES)
def test_real_standard_feature_sequence_is_containment_free(code):
    """第67课：标准特征序列就是「经过非包含处理的特征序列」。

    原文：

    > 关于特征序列，把每一元素看成是一K线，那么，如同一般K线图中找分型的方法，
    > 也存在所谓的包含关系，也可以对此进行非包含处理。**经过非包含处理的特征序列，
    > 成为标准特征序列。**

    第65课的顺序原则只向前推进，同时明说「包含关系，不符合传递律」，所以合并出的
    新元素可能反过来把**前一个**元素包含进去。此时序列里仍留着包含关系，而
    `_is_fractal_at` 按第62课要求「第二K线高点是相邻三K线高点中最高的，而低点也是
    相邻三K线低点中最高的」——被包含的那一对永远构不成分型，真极值被永久跳过，该
    起点一个分界点也找不到。第79课回复里对第81课图例的读法正是这条：「要先把所有的
    特征序列先包含，然后再分段，而不是只处理最近的特征序列包含关系就可以」。

    本用例断言的是定义性质本身，不是某个段数常量：修复前在真实全历史 30 只标的上
    实测 8426 处残留包含，本夹具上也会红。
    """
    strokes = _strokes(DATA[code])
    checked = 0
    for start in range(len(strokes)):
        if not _first_three_overlap(strokes, start):
            continue
        direction = strokes[start].direction
        feats = _feature_seq(strokes, start, direction)
        if len(feats) < 3:
            continue
        checked += 1
        std = _merge_feature(feats, direction)
        for a, b in zip(std, std[1:]):
            contained = (b[0] <= a[0] and b[1] >= a[1]) or (b[0] >= a[0] and b[1] <= a[1])
            assert not contained, (
                f"{code} start={start} dir={direction}: 标准特征序列仍含包含关系 "
                f"{a} / {b}"
            )
    assert checked > 0, f"{code} 一个标准特征序列都没检查到，本用例是空的"


@pytest.mark.parametrize("code", CODES)
def test_real_segment_counts_are_stable(code):
    """线段数基准。

    2026-10-01 第二次重钉（删 `_structural_ok` + 左端改回「第一个可行起点」）：
    `sh.600000` 12/10 -> 11/10，`sh.601088` 11/9 -> 9/8，`sz.300059` 11/9 -> 10/9；
    `sz.300750` 基准未动。首段不再被 TENTATIVE 前导段吃掉（除 `sz.300750` 外），
    故 `expected_first_confirmed` 的起点普遍回到笔 0。

    2026-10-01 第三次重钉（`_merge_feature` 的非包含处理做到不动点，第67课）：
    `sh.601088` 9/8 -> 11/10，`sz.300750` 11/9 -> 15/13；
    `sh.600000` 11/10 与 `sz.300059` 10/9 逐笔未变，四只票的首个确认段也都未变。

    2026-10-02 第四次重钉（左端起点取「还能确认最多线段」的起点，第67课「唯一」下
    的原文空白项）：`sh.601088` 11/10 -> 13/11，其余三只票逐笔未变。这次动的是
    **左端**：`sh.601088` 从笔0 起只能划 10 段，从笔3 起能划 11 段，于是起点改到
    笔3，笔0..2 落进 TENTATIVE 前导段 —— `expected_first_confirmed` 的
    `start_stroke_idx` 因此从 0 变成 3。判据是「同一走势只换取数窗口，落在窗口内的
    分界点不该变」：10 只票 × 3 个窗口对齐全史分界点，命中率 88.9% -> 98.4%，
    最长段 > 80 笔的样本 2 -> 0。只报段数增减不算证据。
    """
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
def test_real_confirmed_segments_are_standardized_per_lesson_78(code):
    """第 78 课：线段要「标准化为最高低点都在端点」。

    原文：

    > 如果线段中，**最高或最低点不是线段的端点**，那么……**都可以把该线段标准化为
    > 最高低点都在端点**。……经过标准化处理后，所有向上线段都是以最低点开始最高点
    > 结束，向下线段都是以最高点开始最低点结束。

    这段话是**划分完之后**的标准化动作，不是划分的判据 —— 所以 `start`/`end` 仍停在
    真实的笔端点上（线段本来就是笔的连接），而 `high`/`low` 必须等于整段笔区间的真实
    极值。次序同样是结论：向上线段的最低点不能跑到最高点后面去。
    """
    strokes = _strokes(DATA[code])
    for seg in _segments(code):
        if seg.status is not Status.CONFIRMED:
            continue
        part = strokes[seg.start_stroke_idx:seg.end_stroke_idx + 1]
        assert seg.high == max(s.high for s in part), f"{code} 段{seg.idx} high 未标准化"
        assert seg.low == min(s.low for s in part), f"{code} 段{seg.idx} low 未标准化"
        hi_i = max(range(len(part)), key=lambda k: part[k].high)
        lo_i = min(range(len(part)), key=lambda k: part[k].low)
        if seg.direction == 1:
            assert lo_i <= hi_i, f"{code} 段{seg.idx} 向上却先见最高点"
        else:
            assert hi_i <= lo_i, f"{code} 段{seg.idx} 向下却先见最低点"


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


def test_leading_tentative_segment_does_not_break_invariants():
    """sz.300750 的窗口左端被截断，前导段是 TENTATIVE，不参与确认结构。

    （「前导段与首个确认段同向」的情形本 fixture 已不存在，改由
    `tests/chan/test_segment.py::test_tentative_leading_segment_may_share_direction_with_next`
    用合成数据覆盖。）
    """
    segs = _segments("sz.300750")
    assert segs[0].status is Status.TENTATIVE
    assert segs[1].status is Status.CONFIRMED
    assert validate_segments(segs, _strokes(DATA["sz.300750"])) == []


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


# ------------------------------------------------ 窗口左端起点的选取
def _best_from(strokes, pol) -> dict[int, int]:
    """每一笔作为起点时，最多还能划分出多少条线段（只保留可行起点）。

    独立复刻 `classify` 的递推，专用于校验「左端起点怎么选」这条规则本身。
    """
    n = len(strokes)
    memo: dict[int, tuple[int, object]] = {}

    def best(s: int):
        if s + pol.min_strokes > n:
            return 0, None
        if s in memo:
            return memo[s]
        res = (0, None)
        for br in pol.candidates(strokes, s):
            cnt, _ = best(br.stroke_idx + 1)
            if cnt + 1 > res[0]:
                res = (cnt + 1, br)
        memo[s] = res
        return res

    return {s: best(s)[0] for s in range(n) if best(s)[1] is not None}


@pytest.mark.parametrize("code", CODES)
def test_left_edge_takes_the_start_that_keeps_the_most_strokes_in_segments(code):
    """左端起点取「还能确认最多线段」的起点（平局取更早的），前缀交给前导段。

    第 67 课：「一切同一级别图上的走势都可以**唯一地**划分为线段的连接，这是基础的
    基础。」「唯一」是对**手上的走势**说的 —— 而数据文件的第一根 K 线常常只是**数据商
    的截断点**：`600180` 日线从 2021-01-04 开始，可该股 1998 年就上市了。把它当成线段
    边界，等于凭空断言一条线段从这里开始。

    原文没有规定被截断的左端怎么起段（第 67/77/78 课都只说划分唯一）⇒ 这是**原文
    空白项下的口径选择**，只能取工程判据：既然无法知道第 0 笔是不是真实线段起点，就让
    划分尽可能完整。实测代价（从第 0 笔硬起）：`600180` 全史 114 笔只划出 5 段、首段
    **73 笔**（改后 19 段、首段 16 笔）；`sz.399001` 从 2020-01-01 截断 → 首段 **81 笔**
    （全史只有 47）；`603777` 同样截断 → 首段 **87 笔**（全史只有 25）。即「同一个走势，
    换个取数窗口就塌成一条线段」，与用户报告的深证成指同一机制。

    这里一度改回「第一个可行起点」，理由是 argmax 会把开头整片笔塞进前导段
    （`sz.399006` 前导 102 笔、100 只样本合计 308 笔）。**那条证据是在 D3
    （`_merge_feature` 非包含处理）修复之前取的**：旧代码里标准特征序列残留包含关系，
    `candidates(0)` 会凭空为空，argmax 才有机会把起点推到很后面。D3 之后前导段最长
    13 笔、17 只样本合计 31 笔，否决理由已消失。
    """
    strokes = _strokes(DATA[code])
    pol = Lesson6768Policy()
    feasible = _best_from(strokes, pol)
    assert feasible, f"{code} 一个可行起点都没有"

    expected = max(feasible, key=lambda s: (feasible[s], -s))
    breaks = pol.classify(strokes)
    chosen = breaks[0].start_stroke_idx if breaks else 0

    assert chosen == expected, (
        f"{code}: 左端起点选了笔{chosen}（可划 {feasible.get(chosen, 0)} 段），"
        f"而最多的是笔{expected}（可划 {feasible[expected]} 段）"
    )
    # 前缀不许丢：起点之前的笔必须由前导 TENTATIVE 段承接，一笔都不能少。
    segs = build_segments(strokes, pol)
    assert sum(s.stroke_count for s in segs) == len(strokes), f"{code}: 有笔没被任何段覆盖"
    if chosen > 0:
        assert segs[0].status is Status.TENTATIVE, f"{code}: 前缀段应当是 TENTATIVE"
        assert (segs[0].start_stroke_idx, segs[0].end_stroke_idx) == (0, chosen - 1)
