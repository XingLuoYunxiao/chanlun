#!/usr/bin/env python
"""缠论严格性审计探针（只读，不改主干）。

对每个可疑口径同时给出「主干怎么算」与「按原文该怎么算」两组数字，
让偏差可以被量化，而不是靠读代码猜。

用法::

    PYTHONPATH=src .venv-chanlun/bin/python optimizer/tools/audit_chanlun_strictness.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import numpy as np  # noqa: E402

from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.macd import macd  # noqa: E402
from chanlun.chan.signal import find_signals  # noqa: E402
from chanlun.chan.trend import TrendType, classify_trends  # noqa: E402
from chanlun.chan.types import Status  # noqa: E402
from chanlun.data import store  # noqa: E402

CODES = (
    "600000", "600030", "600036", "600276", "600519", "600887",
    "600900", "601012", "601088", "601166", "601318", "601899",
    "000001", "000333", "000651", "000725", "000858", "002415",
    "002594", "002714", "300059", "300124", "300750", "300760",
)
START, END = "2020-01-01", "2026-09-29"

R = {k: 0 for k in (
    # A. 笔
    "strokes", "stroke_merged_gap_lt4", "stroke_src_gap_ge4_but_merged_lt4",
    "stroke_merged_gap_ge4_but_src_lt4", "stroke_src_gap_min",
    # B. 线段
    "segments", "seg_confirmed", "seg_even_strokes", "seg_max_strokes",
    "seg_over_9_strokes", "seg_over_15_strokes", "seg_parity_odd_confirmed",
    # C. 中枢
    "pivots", "pivots_confirmed", "pivot_ggdd_differs_from_zn",
    "pivot_ggdd_trunk_differs", "pivot_ggdd_trunk_differs_from_all",
    "pivot_even_group", "pivot_ggdd_differs_and_even",
    # D. 走势类型 / 定理二
    "pivot_pairs_zdzg_disjoint", "pairs_zdzg_disjoint_ddgg_overlap",
    "pairs_zdzg_disjoint_ddgg_disjoint", "trends_up", "trends_down",
    "trends_consolidation", "trend_pairs_violating_theorem2",
    # E. 背驰面积口径
    "b1", "s1", "b1_area_flip_if_sign_only", "s1_area_flip_if_sign_only",
    "b1_uses_nonfinal_pivot", "s1_uses_nonfinal_pivot",
    # F. 三类买卖点
    "b3", "s3", "b3_back_overlaps_pivot", "s3_back_overlaps_pivot",
    "b3_leave_not_first_breakout", "s3_leave_not_first_breakout",
)}
EXAMPLES: dict[str, list[str]] = {}


def note(key: str, msg: str, limit: int = 6) -> None:
    EXAMPLES.setdefault(key, [])
    if len(EXAMPLES[key]) < limit:
        EXAMPLES[key].append(msg)


def main() -> int:
    per_code = {}
    for code in CODES:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            per_code[code] = {"missing": True}
            continue
        snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
        merged = list(snap.merged)
        strokes = list(snap.strokes)
        segs = list(snap.segments)
        pivots = list(snap.pivots)
        signals = list(snap.signals)
        mdf = macd(bars["close"])
        hist = mdf["hist"].to_numpy(dtype=float)
        trends = classify_trends(pivots, "day")

        # ---------------- A. 笔：合并后计数 vs 原始K线计数 ----------------
        for st in strokes:
            R["strokes"] += 1
            mgap = abs(st.end.midx - st.start.midx)
            sgap = abs(st.end.src_idx - st.start.src_idx)
            if mgap < 4:
                R["stroke_merged_gap_lt4"] += 1
            if sgap >= 4 and mgap < 4:
                R["stroke_src_gap_ge4_but_merged_lt4"] += 1
                note("笔_原始够4根但合并后不够",
                     f"{code} {st.start.ts}->{st.end.ts} 合并间隔{mgap} 原始间隔{sgap}")
            if mgap >= 4 and sgap < 4:
                R["stroke_merged_gap_ge4_but_src_lt4"] += 1
                note("笔_合并够4根但原始不够(不可能)",
                     f"{code} {st.start.ts}->{st.end.ts} 合并间隔{mgap} 原始间隔{sgap}")

        # ---------------- B. 线段 ----------------
        for sg in segs:
            R["segments"] += 1
            if sg.status is Status.CONFIRMED:
                R["seg_confirmed"] += 1
                if sg.stroke_count % 2 == 0:
                    R["seg_even_strokes"] += 1
                    note("已确认线段笔数为偶数",
                         f"{code} seg{sg.idx} 笔数={sg.stroke_count}")
            R["seg_max_strokes"] = max(R["seg_max_strokes"], sg.stroke_count)
            if sg.stroke_count > 9:
                R["seg_over_9_strokes"] += 1
            if sg.stroke_count > 15:
                R["seg_over_15_strokes"] += 1
                note("线段笔数>15", f"{code} seg{sg.idx} 笔数={sg.stroke_count}")

        # ---------------- C. 中枢 GG/DD 口径 ----------------
        # 两种口径对照（**不是**主干缺陷计数器，主干现状看 trunk_* 那几个）：
        #   all = 中枢组里全部线段；zn = 只取同向 Z 走势段（第20课「n遍历中枢中所有Zn」）。
        # 2026-10-01 修复后主干 = zn 再去掉**离开段**（`inside = zn[:-1]`，见 pivot.py 取舍 5、
        # optimizer/journal/round-016.json），所以这里额外直接读 `Pivot.gg/dd` 做不变量。
        for p in pivots:
            R["pivots"] += 1
            if p.status is Status.CONFIRMED:
                R["pivots_confirmed"] += 1
            group = segs[p.start_idx:p.end_idx + 1]
            if not group:
                continue
            # 「Z 走势段」= 与中枢第一段同向的段（第20课：与中枢方向一致）
            want = group[0].direction
            zn = [s for s in group if s.direction == want]
            inside = zn[:-1] if len(zn) > 1 else zn
            gg_all = max(s.high for s in group)
            dd_all = min(s.low for s in group)
            gg_zn = max(s.high for s in zn) if zn else gg_all
            dd_zn = min(s.low for s in zn) if zn else dd_all
            gg_in = max(s.high for s in inside) if inside else gg_zn
            dd_in = min(s.low for s in inside) if inside else dd_zn
            differs = (abs(gg_all - gg_zn) > 1e-9) or (abs(dd_all - dd_zn) > 1e-9)
            if differs:
                R["pivot_ggdd_differs_from_zn"] += 1
                note("GG/DD 全段口径与Zn口径不同（口径对照，非缺陷）",
                     f"{code} P{p.idx} 段数{len(group)} GG {gg_all:.2f}->{gg_zn:.2f} "
                     f"DD {dd_all:.2f}->{dd_zn:.2f}")
            # 不变量：主干 Pivot.gg/dd 必须等于「Zn 不含离开段」口径。
            if (abs(p.gg - gg_in) > 1e-9) or (abs(p.dd - dd_in) > 1e-9):
                R["pivot_ggdd_trunk_differs"] += 1
                note("主干 Pivot.gg/dd 与 Zn去离开段 口径不同（必须为 0）",
                     f"{code} P{p.idx} 主干 GG {p.gg:.2f} DD {p.dd:.2f} "
                     f"vs 口径 GG {gg_in:.2f} DD {dd_in:.2f}")
            if (abs(p.gg - gg_all) > 1e-9) or (abs(p.dd - dd_all) > 1e-9):
                R["pivot_ggdd_trunk_differs_from_all"] += 1
            if len(group) % 2 == 0:
                R["pivot_even_group"] += 1
                if differs:
                    R["pivot_ggdd_differs_and_even"] += 1

        # ---------------- D. 定理二：相邻中枢 [ZD,ZG] 不重叠但 [DD,GG] 重叠 ----------------
        conf_pivots = [p for p in pivots if p.status is Status.CONFIRMED]
        for a, b in zip(conf_pivots, conf_pivots[1:]):
            zdzg_disjoint = b.zd > a.zg or b.zg < a.zd
            ddgg_disjoint = b.dd > a.gg or b.gg < a.dd
            if zdzg_disjoint:
                R["pivot_pairs_zdzg_disjoint"] += 1
                if not ddgg_disjoint:
                    R["pairs_zdzg_disjoint_ddgg_overlap"] += 1
                    note("定理二：ZD/ZG不重叠但DD/GG重叠→应为高级别中枢",
                         f"{code} P{a.idx}->P{b.idx} "
                         f"a[ZD{a.zd:.2f},ZG{a.zg:.2f} DD{a.dd:.2f},GG{a.gg:.2f}] "
                         f"b[ZD{b.zd:.2f},ZG{b.zg:.2f} DD{b.dd:.2f},GG{b.gg:.2f}]")
                else:
                    R["pairs_zdzg_disjoint_ddgg_disjoint"] += 1

        for t in trends:
            if t.kind is TrendType.UP:
                R["trends_up"] += 1
            elif t.kind is TrendType.DOWN:
                R["trends_down"] += 1
            else:
                R["trends_consolidation"] += 1
        # 主干判成趋势、但按定理二应判为「高级别中枢」的相邻对
        for t in trends:
            if t.kind is TrendType.CONSOLIDATION:
                continue
            grp = pivots[t.start_idx:t.end_idx + 1]
            for a, b in zip(grp, grp[1:]):
                if not (b.dd > a.gg or b.gg < a.dd):
                    R["trend_pairs_violating_theorem2"] += 1

        # ---------------- E. 背驰面积口径（红/绿柱 vs 绝对值） ----------------
        area_abs = lambda s: float(np.sum(np.abs(hist[s.src_start:s.src_end + 1])))
        for want, kind in ((-1, "b1"), (1, "s1")):
            grp = [p for t in trends if t.kind is (TrendType.DOWN if want == -1 else TrendType.UP)
                   for p in pivots[t.start_idx:t.end_idx + 1]]
            if len(grp) < 2:
                continue
            for k, p in enumerate(grp):
                leave = segs[p.end_idx]
                if leave.direction != want or k == 0:
                    continue
                prev = segs[grp[k - 1].end_idx]
                if prev.direction != want:
                    continue
                if want == -1 and not leave.low < prev.low:
                    continue
                if want == 1 and not leave.high > prev.high:
                    continue
                a_now, a_prev = area_abs(leave), area_abs(prev)
                if not a_now < a_prev:
                    continue
                R[kind] += 1
                # 只取同号柱子（向上取红柱正面积、向下取绿柱负面积）
                def signed(s):
                    w = hist[s.src_start:s.src_end + 1]
                    return float(np.sum(w[w > 0])) if want == 1 else float(np.sum(-w[w < 0]))
                if not signed(leave) < signed(prev):
                    R[f"{kind}_area_flip_if_sign_only"] += 1
                    note("面积口径翻转(只取红/绿柱)",
                         f"{code} P{p.idx} abs {a_now:.2f}<{a_prev:.2f} 但同号柱 "
                         f"{signed(leave):.2f} >= {signed(prev):.2f}")
                # 是否发生在趋势的**非最后一个**中枢上
                if k != len(grp) - 1:
                    R[f"{kind}_uses_nonfinal_pivot"] += 1
                    note("B1/S1 落在趋势的中间中枢（趋势仍在延续）",
                         f"{code} P{p.idx} 是 {len(grp)} 个中枢中的第 {k + 1} 个")

        # ---------------- F. 三类买卖点 ----------------
        for p in pivots:
            if p.end_idx + 1 >= len(segs):
                continue
            leave, back = segs[p.end_idx], segs[p.end_idx + 1]
            if leave.direction == 1 and leave.high > p.zg and back.direction == -1 and back.low > p.zg:
                R["b3"] += 1
                if back.low <= p.zg:
                    R["b3_back_overlaps_pivot"] += 1
            if leave.direction == -1 and leave.low < p.zd and back.direction == 1 and back.high < p.zd:
                R["s3"] += 1
                if back.high >= p.zd:
                    R["s3_back_overlaps_pivot"] += 1

        per_code[code] = {
            "bars": len(bars), "strokes": len(strokes), "segments": len(segs),
            "pivots": len(pivots), "signals": len(signals),
            "seg_stroke_counts": sorted(s.stroke_count for s in segs)[-5:],
        }

    print("__RESULT__ " + json.dumps(
        {"metrics": R, "examples": EXAMPLES, "per_code": per_code},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
