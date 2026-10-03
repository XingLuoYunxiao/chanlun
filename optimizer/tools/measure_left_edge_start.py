"""⚠️ **已失效，不要再用它下结论**（2026-10-03 标注）。

**它测不出任何东西**：本文件 `main` 里
`cur = snap.segments` 是主干 `classify` 的产出，而 `arg = _classify_argmax(...)`
是**主干当前逻辑的逐字副本**（`_classify_argmax` 就是照抄现状写的）—— 两列恒等。
按 AGENTS.md §5「测量必须能失败」：**一个无论怎么改都是满分的指标没有在测量任何东西**。

**它的前提也是错的**：本文件初版以为主干左端是「第一个可行起点」，
实测主干是 **`argmax over s of best(s)[0]`**（见 `src/chanlun/chan/segment.py`
`classify` 的注释块）。所以标题里那句「现状：第一个可行起点」是错的标注。

**替代尺子**：`optimizer/tools/measure_segment_present_tense.py`。
它用**仓库内可复现**的四个变体（主干 / 热身冻结左端 / 首个满足目标 / 起点0）
量第 71 课单调性违规与结构，并已给出结论：**冻结左端与主干逐项相同**
⇒ 左端本来就是稳定的，**不是漂移源**，换左端口径这条路是死的。

本文件**保留不删**：它是历史产物，删掉会丢掉「当初为什么这样比」的线索
（AGENTS.md §5 对优化器历史产物的态度）。

--- 以下为原文，前提已证伪，仅作历史留存 ---

左端起点口径复测（D3 非包含处理修复之后）。

**为什么必须重测**：主干左端一直是「第一个可行起点」（`classify` 从第 0 笔起扫，
扫到第一个 `best(start)[1] is not None` 的起点就定下来）。一次中间态曾改成
「确认线段数最多的起点」（argmax），当时的否决证据是 `sz.399006` 被塞进
**102 笔**的不分类前导段、100 只样本合计 308 笔被隐藏。

**那条证据是在 `_merge_feature` 非包含处理不彻底的旧代码上取的** ——
旧代码里标准特征序列会残留包含关系，`candidates(0)` 会**凭空为空**，
于是 `best(0)[1] is None`，argmax 才有机会把起点推到很后面。
D3 修掉之后「前导段藏笔」这个否决理由**可能已经失效**，所以不能沿用旧结论。

**判据（第67/78课）**：
- 第67课「一切同一级别图上的走势都可以唯一地划分为线段的连接，这是基础的基础」
  + 第77课「线段的划分也是唯一的」⇒ 同一段数据只能有一种划分。
- 第78课「经过标准化处理后，所有向上线段都是以最低点开始最高点结束」⇒
  前导 TENTATIVE 段是**数据窗口截断的工程补丁**，不是缠论概念。
  **原文没有规定被截断的左端该怎么起段** ⇒ 这是原文空白项下的口径选择。
- 因此只能取工程判据：**谁把笔藏进不分类前导段更少、谁的段数不是异常少**。

用法：`python optimizer/tools/measure_left_edge_start.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun.chan import segment as S  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.data import store  # noqa: E402

CODES = [
    ("sz.399001", "day"), ("sz.399006", "day"), ("sh.000001", "day"),
    ("sh.000905", "day"), ("sh.000300", "day"),
    ("sh.600000", "day"), ("sh.601088", "day"), ("sz.300059", "day"),
    ("sz.300750", "day"), ("sh.600519", "day"), ("sz.002415", "day"),
    ("sh.600887", "day"), ("sz.300760", "day"), ("603777", "day"),
    ("600180", "day"), ("sh.600759", "5"), ("sh.600519", "30"),
]


def _classify_argmax(self, strokes):
    """左端取「确认线段数最多」的起点，其余与 `Lesson6768Policy.classify` 相同。"""
    n = len(strokes)
    if n < self.min_strokes:
        return []
    memo: dict[int, tuple[int, S.SegmentBreak | None]] = {}

    def best(start: int):
        if start + self.min_strokes > n:
            return 0, None
        if start in memo:
            return memo[start]
        result = (0, None)
        for br in self.candidates(strokes, start):
            count, _ = best(br.stroke_idx + 1)
            if count + 1 > result[0]:
                result = (count + 1, br)
        memo[start] = result
        return result

    feasible = [st for st in range(n) if next(self.candidates(strokes, st), None) is not None]
    if not feasible:
        return []
    start = max(feasible, key=lambda st: (best(st)[0], -st))

    breaks: list[S.SegmentBreak] = []
    while start < n:
        _, br = best(start)
        if br is None:
            break
        breaks.append(br)
        start = br.stroke_idx + 1
    return breaks


def _stats(segs, n_strokes: int) -> str:
    if not segs:
        return "无段"
    conf = sum(1 for s in segs if s.status.name == "CONFIRMED")
    lead = segs[0].stroke_count if segs[0].status.name != "CONFIRMED" else 0
    return (f"段{len(segs):3d} 确认{conf:3d} 前导{lead:4d} "
            f"最长{max(s.stroke_count for s in segs):4d}")


def main() -> int:
    orig = S.Lesson6768Policy.classify
    print(f"{'code':12s}{'周期':5s}{'笔':>6s} | {'主干 classify':>26s} | "
          f"{'_classify_argmax（主干副本）':>26s}")
    tot = {"cur": [0, 0], "arg": [0, 0]}
    for code, period in CODES:
        try:
            df = pd.read_parquet(store.path_for(code, period))
        except FileNotFoundError:
            print(f"{code:12s}{period:5s}  —— 无数据")
            continue
        snap = ChanEngine(code, period).full(df)
        n = len(snap.strokes)
        cur = snap.segments
        S.Lesson6768Policy.classify = _classify_argmax
        try:
            arg = S.build_segments(snap.strokes)
        finally:
            S.Lesson6768Policy.classify = orig
        print(f"{code:12s}{period:5s}{n:6d} | {_stats(cur, n):>26s} | {_stats(arg, n):>26s}")
        for key, segs in (("cur", cur), ("arg", arg)):
            lead = segs[0].stroke_count if segs and segs[0].status.name != "CONFIRMED" else 0
            tot[key][0] += lead
            tot[key][1] += len(segs)
    print(f"\n合计：主干 段{tot['cur'][1]} 前导藏笔 {tot['cur'][0]} 笔"
          f" | 主干副本 段{tot['arg'][1]} 前导藏笔 {tot['arg'][0]} 笔")
    print("⚠️ 两列应当恒等（后者是前者的逐字副本）—— 恒等即证明本工具无测量能力。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
