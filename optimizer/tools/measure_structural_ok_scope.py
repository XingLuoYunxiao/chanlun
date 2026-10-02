"""量化 `_structural_ok` 过滤器在大样本全历史上的影响。

背景：2026-10-01 用户报告深证成指（sz.399001）日线全历史只剩 4 个线段、其中
一段吞掉 592 笔。定位结果是两件事叠加：

1. 数据层：1995-02-06~02-10 有 5 根全 0 的坏 K 线，造出「跌到 0 再涨回来」的两笔，
   0.0 成为全序列最小值；
2. 算法层：`_structural_ok` 要求向上线段起点必须是窗内最低、向下线段终点必须是
   窗内最低。第 78 课把「极值不在端点」明说成**划分之后**可以标准化的情况，
   不是划分判据；一旦某笔的极值超出该窗口，候选分界被否决，搜索退化成
   「整段吞掉」。

本工具在 `tests/chan/fixtures` 四只 + 指数 + 全市场抽样上对比开/关该过滤器的
段数、中枢数与最长段笔数，用来判断它到底是「口径选择」还是「破坏性前置条件」。

用法：
    PYTHONPATH=src python optimizer/tools/measure_structural_ok_scope.py [--sample N]
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun.chan import segment as S  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.data import store  # noqa: E402

INDEX_CODES = ("sz.399001", "sz.399006", "sh.000001", "sh.000905", "sh.000300")


def _stat(code: str, period: str, df) -> dict:
    snap = ChanEngine(code, period).full(df)
    segs = snap.segments
    span = max((s.end_stroke_idx - s.start_stroke_idx + 1 for s in segs), default=0)
    return {
        "strokes": len(snap.strokes),
        "segments": len(segs),
        "confirmed": sum(1 for s in segs if s.status.name == "CONFIRMED"),
        "pivots": len(snap.pivots),
        "max_span": span,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=120, help="全市场抽样只数")
    ap.add_argument("--seed", type=int, default=20261001)
    args = ap.parse_args()

    targets: list[tuple[str, str]] = [(c, "day") for c in INDEX_CODES]
    rng = random.Random(args.seed)
    day_dir = Path(store.data_root()) / "day"
    all_codes: list[str] = []
    for mk in sorted(p for p in day_dir.iterdir() if p.is_dir()):
        for f in sorted(mk.glob("*.parquet")):
            all_codes.append(f"{mk.name}.{f.stem}")
    all_codes.sort()
    pick = rng.sample(all_codes, min(args.sample, len(all_codes)))
    targets += [(c, "day") for c in pick]

    orig = S._structural_ok
    rows = []
    for code, period in targets:
        df = store.read(code, period)
        if len(df) < 100:
            continue
        S._structural_ok = orig
        on = _stat(code, period, df)
        S._structural_ok = lambda *a, **k: True  # noqa: E731
        off = _stat(code, period, df)
        S._structural_ok = orig
        rows.append((code, on, off))
        flag = ""
        if off["segments"] > on["segments"] * 1.5:
            flag = "  <<< 剧变"
        elif on["max_span"] > 100 >= off["max_span"]:
            flag = "  <<< 长段消失"
        print(
            f"{code:12s} 笔={on['strokes']:5d} | "
            f"开: 段={on['segments']:4d} 确认={on['confirmed']:4d} 中枢={on['pivots']:3d} 最长={on['max_span']:4d} | "
            f"关: 段={off['segments']:4d} 确认={off['confirmed']:4d} 中枢={off['pivots']:3d} 最长={off['max_span']:4d}{flag}",
            flush=True,
        )

    n = len(rows)
    worse = sum(1 for _, a, b in rows if b["segments"] < a["segments"])
    better = sum(1 for _, a, b in rows if b["segments"] > a["segments"])
    same = n - worse - better
    brutal = sum(1 for _, a, b in rows if b["segments"] > a["segments"] * 1.5)
    long_on = sum(1 for _, a, _ in rows if a["max_span"] > 100)
    long_off = sum(1 for _, _, b in rows if b["max_span"] > 100)
    print(f"\n-- 样本 {n} 只 --")
    print(f"关闭后段数 增加 {better} / 不变 {same} / 减少 {worse}")
    print(f"段数增幅 >50% 的: {brutal}")
    print(f"最长段 >100 笔: 开={long_on} 关={long_off}")
    print(f"总段数: 开={sum(a['segments'] for _, a, _ in rows)} 关={sum(b['segments'] for _, _, b in rows)}")
    print(f"总中枢: 开={sum(a['pivots'] for _, a, _ in rows)} 关={sum(b['pivots'] for _, _, b in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
