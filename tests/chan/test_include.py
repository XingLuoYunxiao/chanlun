import itertools
import random

import pandas as pd

from chanlun.chan.include import _merge_direction, merge_bars

COLS = ["ts", "open", "high", "low", "close", "volume", "amount"]


def mk(rows):
    return pd.DataFrame([(ts, o, h, l, c, v, a) for ts, o, h, l, c, v, a in rows], columns=COLS)


# ---------------- 无包含关系 ----------------
def test_no_inclusion_keeps_all_bars():
    df = mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 12, 7, 11, 100, 0),
        ("d3", 1, 15, 9, 14, 100, 0),
    ])
    assert len(merge_bars(df)) == 3


def test_empty_input_returns_empty_list():
    assert merge_bars(pd.DataFrame(columns=COLS)) == []


def test_single_bar_is_its_own_merged_bar():
    m = merge_bars(mk([("d1", 1, 10, 5, 8, 100, 0)]))
    assert len(m) == 1
    assert (m[0].high, m[0].low) == (10.0, 5.0)
    assert m[0].src_start == 0 and m[0].src_end == 0
    assert m[0].ts == "d1" and m[0].start_ts == "d1"


# ---------------- 向上包含：high=max, low=max ----------------
def test_upward_inclusion_takes_max_high_max_low():
    # d1(10,5) -> d2(15,8) 向上；d3(12,9) 被 d2 包含 -> 向上合并为 (15,9)
    df = mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 15, 8, 14, 100, 0),
        ("d3", 1, 12, 9, 10, 100, 0),
        ("d4", 1, 18, 13, 17, 100, 0),
    ])
    m = merge_bars(df)
    assert len(m) == 3
    assert (m[1].high, m[1].low) == (15.0, 9.0)
    assert m[1].direction == 1


def test_upward_merge_tracks_source_span_and_ts():
    df = mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 15, 8, 14, 100, 0),
        ("d3", 1, 12, 9, 10, 100, 0),
        ("d4", 1, 18, 13, 17, 100, 0),
    ])
    m = merge_bars(df)
    assert m[1].src_start == 1 and m[1].src_end == 2
    assert m[1].start_ts == "d2" and m[1].ts == "d3"   # ts 取跨度内最后一根原始K线


# ---------------- 向下包含：high=min, low=min ----------------
def test_downward_inclusion_takes_min_high_min_low():
    # d1(20,15) -> d2(14,8) 向下；d3(12,9) 落在 d2 内 -> 向下合并为 (12,8)
    df = mk([
        ("d1", 1, 20, 15, 16, 100, 0),
        ("d2", 1, 14, 8, 9, 100, 0),
        ("d3", 1, 12, 9, 10, 100, 0),
        ("d4", 1, 9, 4, 5, 100, 0),
    ])
    m = merge_bars(df)
    assert len(m) == 3
    assert (m[1].high, m[1].low) == (12.0, 8.0)
    assert m[1].direction == -1


def test_direction_from_second_bar_when_no_history():
    # 只有一根合并K线时，方向由当前原始 bar 与它比较得出
    up = merge_bars(mk([("d1", 1, 10, 5, 8, 100, 0), ("d2", 1, 12, 4, 6, 100, 0)]))
    assert up[0].direction == 1
    assert (up[0].high, up[0].low) == (12.0, 5.0)

    # d2(8,6) 落在 d1(10,5) 内且高点更低 -> 向下合并
    down = merge_bars(mk([("d1", 1, 10, 5, 8, 100, 0), ("d2", 1, 8, 6, 6, 100, 0)]))
    assert down[0].direction == -1
    assert (down[0].high, down[0].low) == (8.0, 5.0)


def test_merge_direction_is_sticky_within_a_span():
    """向上合并只可能抬高 low、不改变 high；向下合并只可能压低 high、不改变 low。

    由「被包含」的定义（d.high <= H 且 d.low >= L）直接推出，是包含处理的一条硬性质。
    """
    up = merge_bars(mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 15, 8, 14, 100, 0),
        ("d3", 1, 12, 9, 10, 100, 0),
        ("d4", 1, 14, 11, 12, 100, 0),
    ]))
    assert len(up) == 2
    assert up[1].high == 15.0        # high 恒等于跨度初始值
    assert up[1].low == 11.0         # low 单调抬升

    down = merge_bars(mk([
        ("d1", 1, 20, 15, 16, 100, 0),
        ("d2", 1, 14, 8, 9, 100, 0),
        ("d3", 1, 12, 9, 10, 100, 0),
        ("d4", 1, 10, 8.5, 9, 100, 0),
    ]))
    assert len(down) == 2
    assert down[1].low == 8.0        # low 恒等于跨度初始值
    assert down[1].high == 10.0      # high 单调压低


def test_a_contained_bar_never_extends_the_range_in_merge_direction():
    # 向上合并时，被包含K线的高点不允许抬高合并K线的高点
    m = merge_bars(mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 15, 8, 14, 100, 0),
        ("d3", 1, 14.5, 9, 10, 100, 0),
    ]))
    assert len(m) == 2
    assert m[1].high == 15.0


# ---------------- 顺序原则（不可传递） ----------------
def test_sequential_principle_not_transitive():
    # 三根互相包含：正确结果是先合 d1+d2 再与 d3 合，最终 1 根 (8,5)
    df = mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 9, 6, 7, 100, 0),
        ("d3", 1, 8, 7, 7.5, 100, 0),
    ])
    m = merge_bars(df)
    assert len(m) == 1
    assert (m[0].high, m[0].low) == (8.0, 5.0)
    assert m[0].src_start == 0 and m[0].src_end == 2


# ---------------- 方向判据：gn >= gn-1（第 65 课原文） ----------------
def test_merge_direction_counts_equal_high_as_upward():
    """第 65 课原文：「如果gn>=gn-1，那么称第n-1、n、n+1根K线是向上的」。

    `>=` **含相等**：高点相等必须判向上（合并取 `max/max`），不能判向下
    （取 `min/min`）。相等时两种读法的合并结果不同——例如把 (10,5)、(10,6)
    这两根相等高点的K线合并：向上得 (10,6)，向下得 (10,5)。
    """
    assert _merge_direction(10.0, 10.0) == 1     # gn == gn-1 → 向上
    assert _merge_direction(10.0, 11.0) == 1     # gn >  gn-1 → 向上
    assert _merge_direction(10.0, 9.0) == -1     # gn <  gn-1 → 向下
    assert _merge_direction(10.0, 10.0) != -1    # 严格 `>` 会误判为向下
    # 相等高点在 `merge_bars` 里只能走首两根的 `_infer_direction` 分支（见下一个
    # 测试的不可达性证明），它同样给出 `>=` 的答案：向上合并取 max/max。
    # 若按严格 `>` 判向下，这里会得到 (10, 5) —— 两种读法的结果确实不同。
    m = merge_bars(mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 10, 6, 9, 100, 0),
    ]))
    assert len(m) == 1
    assert (m[0].high, m[0].low) == (10.0, 6.0)   # 向上：max(10,10), max(5,6)
    assert (m[0].high, m[0].low) != (10.0, 5.0)   # 向下 min/min 的结果，被排除


def test_adjacent_merged_bars_never_share_a_high():
    """`_merge_direction` 的相等分支在 `merge_bars` 里**不可达**（穷举验证）。

    为什么不可达：相邻两根已合并K线必然**互不包含**（后一根只在 `_contains`
    为假时才追加），而两根互不包含的区间若高点相等就必然互相包含（`high`
    相等时 `_contains` 恒为真），所以追加时高点必不相等；此后只有
    `merged[-1]` 会被后续的包含合并改写，而改写方向由它相对 `merged[-2]`
    的高点关系决定，只会把高点推得离 `merged[-2].high` 更远（向上取 max、
    向下取 min），永远不可能取等。

    于是 `>` 与 `>=` 两种读法在 `merge_bars` 上产出**完全相同**的合并序列：
    第 65 课的这条边界是**理论边界**——真实数据（24 只 A 股日线）与全部可构造
    输入都触发不到，`>=` 的修正不改变任何既有结果，只是把原文口径写对。
    """
    pairs = [(h, low) for h in range(1, 5) for low in range(0, h)]
    for n in range(1, 5):
        for combo in itertools.product(pairs, repeat=n):
            m = merge_bars([(f"d{i}", h, low) for i, (h, low) in enumerate(combo)])
            for a, b in zip(m, m[1:]):
                assert a.high != b.high
    # 更长的随机序列同样成立（含「新 bar 包含 merged[-1]」这种会抬高/压低
    # 最后一根合并K线高点的情形）。
    rng = random.Random(20261001)
    for _ in range(2000):
        rows = []
        for i in range(rng.randint(2, 12)):
            h = rng.randint(1, 12)
            rows.append((f"d{i}", h, rng.randint(0, h - 1)))
        m = merge_bars(rows)
        for a, b in zip(m, m[1:]):
            assert a.high != b.high


def test_equality_boundaries_count_as_inclusion():
    # d2 与 d1 完全同区间 -> 视为包含
    m = merge_bars(mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 10, 5, 7, 100, 0),
    ]))
    assert len(m) == 1


def test_idx_is_dense_and_ascending():
    df = mk([
        ("d1", 1, 10, 5, 8, 100, 0),
        ("d2", 1, 12, 9, 10, 100, 0),
        ("d3", 1, 14, 11, 12, 100, 0),
    ])
    m = merge_bars(df)
    assert [x.idx for x in m] == [0, 1, 2]


def test_accepts_plain_sequences():
    # 非 DataFrame 输入：行 = (ts, high, low)
    m = merge_bars([("d1", 10, 5), ("d2", 15, 8), ("d3", 12, 9), ("d4", 18, 13)])
    assert len(m) == 3
    assert (m[1].high, m[1].low) == (15.0, 9.0)
