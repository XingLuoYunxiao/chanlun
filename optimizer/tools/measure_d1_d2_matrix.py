"""量出「数据层 0 价」与「线段 _structural_ok」两个缺陷的分离贡献。

四个组合 = {含 0 价, 剔除 0 价} × {_structural_ok 在, 不在}，左端固定在主干口径
（第一个可行起点）。做法是把 HEAD 版的 `data/types.py` 与 `chan/segment.py`
作为独立模块加载进来，所以两边的代码都能在同一个进程里跑。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan import fractal, include, stroke  # noqa: E402
from chanlun.data import store  # noqa: E402
from chanlun.data import types as types_new  # noqa: E402

PRE = Path("/tmp/pre018")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


types_old = load("chanlun.data.types_head", PRE / "src/chanlun/data/types.py")
seg_old = load("chanlun.chan.segment_head", PRE / "src/chanlun/chan/segment.py")
from chanlun.chan import segment as seg_new  # noqa: E402


def strokes_for(code: str, period: str, limit: int | None):
    # 直接读 parquet，绕开 store.read 里的 normalize，否则 0 价行在进本脚本之前
    # 就已经被主干剔掉了，两组数据会逐位相同（第一次量的时候就是这样被骗过去的）。
    raw = pd.read_parquet(store.path_for(code, period), engine="pyarrow")
    out = {}
    for tag, norm in (("含0价", types_old.normalize), ("剔0价", types_new.normalize)):
        df = norm(raw.copy())
        if limit is not None:
            df = store.truncate(df, limit=limit)
        merged = include.merge_bars(df)
        fr = fractal.find_fractals(merged, df)
        out[tag] = stroke.build_strokes(fr)
    return out


def seg_stats(strokes, mod):
    segs = mod.build_segments(strokes)
    longest = max((s.stroke_count for s in segs), default=0)
    return len(segs), sum(1 for s in segs if s.status.name == "CONFIRMED"), longest


def main() -> int:
    cases = [("sz.399001", "day", None), ("sz.399001", "day", 4000), ("sz.399006", "day", None)]
    for code, period, limit in cases:
        strokes = strokes_for(code, period, limit)
        tag = f"{code} {period}" + (f" 末{limit}根" if limit else " 全历史")
        print(f"\n### {tag}")
        for dtag, ss in strokes.items():
            row = [f"  笔={len(ss):>5}"]
            for ptag, mod in (("滤波器开", seg_old), ("滤波器关", seg_new)):
                total, conf, longest = seg_stats(ss, mod)
                row.append(f"{ptag}: 段={total:>3} 确认={conf:>3} 最长={longest:>3}")
            print(f"  {dtag} " + " | ".join(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
