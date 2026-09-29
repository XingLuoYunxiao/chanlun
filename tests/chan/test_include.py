import pandas as pd

from chanlun.chan.include import merge_bars

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
