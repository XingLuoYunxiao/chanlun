"""G4 只读审计：第 67 课「线段划分有标准且唯一」在实现里的可测事实。

- **M4a**：线段粗细（每段笔数分布）+ 划分不变量自检 + 缺口（第二种情况）占比；
- **M4b**：「唯一」还是「多个合法划分 + 启发式挑一个」——统计同一 start 存在
  ≥2 个可行分界、且两条分界各自都能继续划分的位置数。

它**不修改任何主干代码**，只读行情并重跑一次划分策略，所以能同时当作
「改之前」和「改之后」的同一把尺子。由 ``optimizer/agent.py`` 在影子目录里
以 ``python -c`` 运行，最后一行 ``__METRICS__`` 就是记进 journal 的数字。
"""

import json
import statistics

from chanlun.chan.engine import ChanEngine
from chanlun.chan.segment import (Lesson6768Policy, validate_segments)
from chanlun.chan.signal import find_signals
from chanlun.chan.types import Status
from chanlun.data import store
from chanlun.optimizer.agent import AUDIT_CODES as CODES
from chanlun.optimizer.agent import AUDIT_END as END
from chanlun.optimizer.agent import AUDIT_START as START


def main() -> None:
    per_code_spans: list[int] = []
    gaps = {"case1_no_gap": 0, "case2_gap": 0}
    problems: list[str] = []
    ambiguous_starts = 0
    ambiguous_codes: list[str] = []
    multi_candidate_starts = 0

    for code in CODES:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            continue
        snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
        segs = list(snap.segments)
        strokes = list(snap.strokes)

        for seg in segs:
            if seg.status is Status.CONFIRMED:
                per_code_spans.append(seg.stroke_count)
        # `Segment` 不携带 has_gap，只有中间的 `SegmentBreak` 有 —— 重新跑一次
        # 划分策略来统计两种情况的占比（只读）。
        for br in Lesson6768Policy().classify(strokes):
            gaps["case2_gap" if br.has_gap else "case1_no_gap"] += 1
        problems.extend(f"{code}: {p}" for p in validate_segments(segs))

        # ---- M4b：同一 start 的合法分界是否唯一 ----
        policy = Lesson6768Policy()
        n = len(strokes)
        for start in range(n):
            brs = list(policy.candidates(strokes, start))
            if len(brs) < 2:
                continue
            multi_candidate_starts += 1
            # 只统计「两条分界各自都能继续划分」的情形：都可行才算真两义。
            viable = sum(
                1
                for br in brs
                if list(policy.candidates(strokes, br.stroke_idx + 1))
                or br.stroke_idx + 1 + policy.min_strokes > n
            )
            if viable >= 2:
                ambiguous_starts += 1
                if code not in ambiguous_codes:
                    ambiguous_codes.append(code)

    spans = sorted(per_code_spans)
    print("__METRICS__" + json.dumps({
        "metrics": {
            "confirmed_segments": len(spans),
            "span_max": spans[-1] if spans else 0,
            "span_gt9": sum(1 for s in spans if s > 9),
            "validate_problems": len(problems),
            "ambiguous_starts": ambiguous_starts,
        },
        "mech": {
            "span_median": statistics.median(spans) if spans else 0,
            "span_gt21": sum(1 for s in spans if s > 21),
            "gaps": gaps,
            "sample_problems": problems[:5],
            "starts_with_multi_candidates": multi_candidate_starts,
            "ambiguous_codes": len(ambiguous_codes),
        },
    }, ensure_ascii=False))


main()
