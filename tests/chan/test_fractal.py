import pandas as pd

from chanlun.chan.fractal import find_fractals
from chanlun.chan.include import merge_bars
from chanlun.chan.types import FractalKind, MergedBar

COLS = ["ts", "open", "high", "low", "close", "volume", "amount"]


def mb(i, high, low):
    return MergedBar(idx=i, ts=f"d{i}", start_ts=f"d{i}", high=high, low=low,
                     direction=1, src_start=i, src_end=i)


def mk(rows):
    return pd.DataFrame([(ts, o, h, l, c, v, a) for ts, o, h, l, c, v, a in rows], columns=COLS)


# ---------------- 基本识别 ----------------
def test_top_fractal_detected():
    m = [mb(0, 10, 5), mb(1, 12, 7), mb(2, 11, 6)]
    f = find_fractals(m)
    assert len(f) == 1
    assert f[0].kind is FractalKind.TOP
    assert f[0].midx == 1
    assert f[0].price == 12.0
    assert f[0].high == 12.0 and f[0].low == 7.0


def test_bottom_fractal_detected():
    m = [mb(0, 12, 7), mb(1, 10, 5), mb(2, 11, 6)]
    f = find_fractals(m)
    assert len(f) == 1
    assert f[0].kind is FractalKind.BOTTOM
    assert f[0].midx == 1
    assert f[0].price == 5.0


def test_alternating_series_yields_top_then_bottom():
    m = [mb(0, 10, 5), mb(1, 12, 7), mb(2, 11, 6), mb(3, 9, 4), mb(4, 13, 8)]
    f = find_fractals(m)
    assert [(x.kind, x.midx) for x in f] == [
        (FractalKind.TOP, 1),
        (FractalKind.BOTTOM, 3),
    ]


# ---------------- 边界与严格不等式 ----------------
def test_first_and_last_bar_never_fractal_center():
    m = [mb(0, 10, 5), mb(1, 12, 7), mb(2, 11, 6)]
    assert all(0 < x.midx < len(m) - 1 for x in find_fractals(m))


def test_too_few_bars_yields_no_fractal():
    assert find_fractals([]) == []
    assert find_fractals([mb(0, 10, 5)]) == []
    assert find_fractals([mb(0, 10, 5), mb(1, 12, 7)]) == []


def test_equal_high_is_not_top_fractal():
    m = [mb(0, 10, 5), mb(1, 12, 5), mb(2, 11, 6)]
    assert not any(x.kind is FractalKind.TOP for x in find_fractals(m))


def test_equal_low_is_not_bottom_fractal():
    m = [mb(0, 12, 6), mb(1, 10, 6), mb(2, 11, 7)]
    assert not any(x.kind is FractalKind.BOTTOM for x in find_fractals(m))


def test_monotonic_series_has_no_fractal():
    m = [mb(i, 10 + i, 5 + i) for i in range(6)]
    assert find_fractals(m) == []


def test_each_bar_is_at_most_one_fractal():
    m = [mb(0, 12, 7), mb(1, 10, 5), mb(2, 11, 6), mb(3, 13, 8), mb(4, 9, 3)]
    f = find_fractals(m)
    assert len({x.midx for x in f}) == len(f)


# ---------------- 与原始K线联动 ----------------
def test_src_idx_points_to_extreme_raw_bar_inside_merged_span():
    # m[2] 由原始 r3/r4 合并而来，最高点来自跨度首根 r3
    df = mk([
        ("r0", 1, 10, 5, 8, 100, 0),
        ("r1", 1, 12, 6, 11, 100, 0),
        ("r2", 1, 11, 7, 10, 100, 0),    # 落到 r1 里 -> 向上合并 (12,7)
        ("r3", 1, 13, 9, 12, 100, 0),    # 独立成一根 (13,9)
        ("r4", 1, 12.5, 10, 11, 100, 0),  # 落到 (13,9) 里 -> 向上合并 (13,10)
        ("r5", 1, 11, 9.5, 10, 100, 0),   # 独立成一根 (11,9.5)
    ])
    m = merge_bars(df)
    assert len(m) == 4
    assert m[2].src_start == 3 and m[2].src_end == 4
    assert (m[2].high, m[2].low) == (13.0, 10.0)

    f = find_fractals(m, bars=df)
    assert len(f) == 1
    assert f[0].kind is FractalKind.TOP
    assert f[0].src_idx == 3
    assert f[0].ts == "r3"


def test_bottom_src_idx_lands_on_span_extreme():
    df = mk([
        ("r0", 1, 20, 15, 16, 100, 0),
        ("r1", 1, 14, 10, 12, 100, 0),
        ("r2", 1, 13, 11, 12, 100, 0),      # 落到 r1 里 -> 向下合并 (13,10)
        ("r3", 1, 12, 11.5, 11.6, 100, 0),  # 再落下 -> 向下合并 (12,10)
        ("r4", 1, 15, 10.5, 14, 100, 0),    # 独立成一根
    ])
    m = merge_bars(df)
    f = find_fractals(m, bars=df)
    bottoms = [x for x in f if x.kind is FractalKind.BOTTOM]
    assert len(bottoms) == 1
    assert bottoms[0].price == 10.0
    assert bottoms[0].ts == "r1" and bottoms[0].src_idx == 1


def test_src_idx_and_ts_always_point_at_the_true_span_extreme():
    """性质测试：分型的 price/ts/src_idx 必须指向其合并K线跨度内的真实极值。"""
    df = mk([
        ("r0", 1, 10, 5, 8, 100, 0),
        ("r1", 1, 12, 6, 11, 100, 0),
        ("r2", 1, 11, 7, 10, 100, 0),
        ("r3", 1, 13, 9, 12, 100, 0),
        ("r4", 1, 12.5, 10, 11, 100, 0),
        ("r5", 1, 11, 9.5, 10, 100, 0),
        ("r6", 1, 20, 9, 19, 100, 0),
        ("r7", 1, 9, 4, 5, 100, 0),
        ("r8", 1, 10, 6, 9, 100, 0),
    ])
    m = merge_bars(df)
    f = find_fractals(m, bars=df)
    assert f, "本序列应至少识别出一个分型"

    for frac in f:
        span = next(x for x in m if x.idx == frac.midx)
        col = "high" if frac.kind is FractalKind.TOP else "low"
        seg = df[col].iloc[span.src_start:span.src_end + 1]
        want = float(seg.max() if frac.kind is FractalKind.TOP else seg.min())
        assert frac.price == want
        assert frac.ts == str(df["ts"].iloc[frac.src_idx])
        assert float(df[col].iloc[frac.src_idx]) == want
        assert span.src_start <= frac.src_idx <= span.src_end


def test_without_raw_bars_ts_falls_back_to_merged_ts():
    m = [mb(0, 10, 5), mb(1, 12, 7), mb(2, 11, 6)]
    f = find_fractals(m)
    assert f[0].ts == "d1"
