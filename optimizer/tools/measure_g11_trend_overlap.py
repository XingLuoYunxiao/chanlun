"""G11 只读审计：第 20 课「走势中枢中心定理二」在实现里的可测事实。

第 20 课把「两个中枢构成趋势」的判据写在**波动区间** `[DD, GG]` 上：

    后GG〈前DD等价于下跌及其延续；后DD〉前GG等价于上涨及其延续。

而 `src/chanlun/chan/trend.py::_direction` 只比较中枢区间 `[ZD, ZG]` 的单调。
这个脚本用同一份行情、同一把尺子，量出三种口径的差别：

- **M11a**：主干 `_direction` 判成趋势的相邻中枢对里，有多少对其实
  `[DD, GG]` 仍然重叠（即第 20 课判为「形成高级别的走势中枢」）；
- **M11b**：`[ZD, ZG]` 不重叠、但 `[DD, GG]` 重叠的对有多少（定理二第三句）；
- **M11c**：换成定理二口径后，走势类型/买卖点的数量变化。

脚本**不修改任何主干文件**：定理二口径用猴子补丁在内存里替换
`chanlun.chan.trend._direction`，跑完即恢复，所以能同时当「改之前」和
「改之后」的同一把尺子。

    cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_g11_trend_overlap.py
"""

import sys
from pathlib import Path

# 这个脚本有两种跑法：人手动 `python optimizer/tools/xxx.py`（有 __file__），
# 以及 24x7 回访时由 agent.py 在影子目录里 `python -c <脚本文本>` 跑（没有
# __file__，cwd 就是影子根）。所以这里不能只认 __file__。
try:
    ROOT = Path(__file__).resolve().parents[2]
except NameError:  # pragma: no cover - `python -c` 分支
    ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan import trend as trend_mod  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.signal import find_signals  # noqa: E402
from chanlun.chan.trend import classify_trends  # noqa: E402
from chanlun.chan.types import Status  # noqa: E402
from chanlun.data import store  # noqa: E402
from chanlun.optimizer.agent import AUDIT_CODES as CODES  # noqa: E402
from chanlun.optimizer.agent import AUDIT_END as END  # noqa: E402
from chanlun.optimizer.agent import AUDIT_START as START  # noqa: E402


def _old(a, b) -> int:
    """主干口径：`[ZD, ZG]` 同时抬高/降低。"""
    if b.zd > a.zd and b.zg > a.zg:
        return 1
    if b.zd < a.zd and b.zg < a.zg:
        return -1
    return 0


def _lesson20(a, b) -> int:
    """第 20 课定理二口径：波动区间 `[DD, GG]` 严格分离。"""
    if b.dd > a.gg:
        return 1
    if b.gg < a.dd:
        return -1
    return 0


def _zd_zg_overlap(a, b) -> bool:
    return min(a.zg, b.zg) > max(a.zd, b.zd)


def _dd_gg_overlap(a, b) -> bool:
    return min(a.gg, b.gg) >= max(a.dd, b.dd)


def _counts(pivots) -> dict:
    trends = classify_trends(pivots, "day")
    out = {"up": 0, "down": 0, "consolidation": 0, "trends": len(trends)}
    for t in trends:
        out[t.kind.value if t.kind.value != "consolidation" else "consolidation"] += 1
    return out


def main() -> None:
    pairs = 0
    old_trend_pairs = 0
    old_trend_but_overlap = 0
    zd_zg_disjoint_dd_gg_overlap = 0
    both_ok = 0
    examples: list[str] = []
    per_code: dict[str, dict] = {}

    base_pivots = {}
    for code in CODES:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            continue
        snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
        pivots = [p for p in snap.pivots if p.status is Status.CONFIRMED]
        base_pivots[code] = pivots

        for a, b in zip(pivots, pivots[1:]):
            pairs += 1
            od = _old(a, b)
            nd = _lesson20(a, b)
            if od != 0:
                old_trend_pairs += 1
                if nd == 0:
                    old_trend_but_overlap += 1
                    if len(examples) < 8:
                        examples.append(
                            f"{code} P{a.idx}->P{b.idx}: "
                            f"前[ZD,ZG]=[{a.zd:.2f},{a.zg:.2f}] [DD,GG]=[{a.dd:.2f},{a.gg:.2f}] | "
                            f"后[ZD,ZG]=[{b.zd:.2f},{b.zg:.2f}] [DD,GG]=[{b.dd:.2f},{b.gg:.2f}]"
                        )
            if nd != 0:
                both_ok += 1
            if not _zd_zg_overlap(a, b) and _dd_gg_overlap(a, b):
                zd_zg_disjoint_dd_gg_overlap += 1

        per_code[code] = {
            "pivots": len(pivots),
            "old": _counts(snap.pivots),
            "signals_old": len(snap.signals),
        }

    # ---- 换成定理二口径（猴子补丁，跑完恢复） ----
    saved = trend_mod._direction
    trend_mod._direction = _lesson20
    try:
        new_totals = {"up": 0, "down": 0, "consolidation": 0, "signals": 0, "b1": 0, "s1": 0}
        for code in CODES:
            bars = store.read(code, "day", start=START, end=END)
            if bars is None or len(bars) == 0:
                continue
            snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
            c = _counts(snap.pivots)
            per_code[code]["new"] = c
            per_code[code]["signals_new"] = len(snap.signals)
            for k in ("up", "down", "consolidation"):
                new_totals[k] += c[k]
            new_totals["signals"] += len(snap.signals)
            for sig in snap.signals:
                if getattr(sig, "kind", "") == "b1":
                    new_totals["b1"] += 1
                if getattr(sig, "kind", "") == "s1":
                    new_totals["s1"] += 1
    finally:
        trend_mod._direction = saved

    old_totals = {"up": 0, "down": 0, "consolidation": 0, "signals": 0}
    for code, row in per_code.items():
        for k in ("up", "down", "consolidation"):
            old_totals[k] += row["old"][k]
        old_totals["signals"] += row["signals_old"]

    print("== M11a/M11b 相邻中枢对（只看 CONFIRMED 中枢，24 只票日线） ==")
    print(f"相邻中枢对总数                     : {pairs}")
    print(f"主干判成趋势的对数                 : {old_trend_pairs}")
    print(f"  其中 [DD,GG] 仍重叠（定理二=级别扩张）: {old_trend_but_overlap}")
    print(f"定理二也判成趋势的对数             : {both_ok}")
    print(f"[ZD,ZG] 不重叠 但 [DD,GG] 重叠      : {zd_zg_disjoint_dd_gg_overlap}")
    print("-- 反例（主干算趋势、定理二算级别扩张） --")
    for line in examples:
        print("  " + line)
    print("== M11c 走势类型 / 买卖点（同一份行情、同一把尺子） ==")
    print(f"主干   : {old_totals}")
    print(f"定理二 : {new_totals}")
    changed = [c for c, r in per_code.items() if r["old"] != r["new"]]
    print(f"走势类型口径发生变化的票数: {len(changed)}/{len(per_code)}")
    print("__METRICS__ " + __import__("json").dumps(
        {
            "pairs": pairs,
            "old_trend_pairs": old_trend_pairs,
            "old_trend_but_overlap": old_trend_but_overlap,
            "lesson20_trend_pairs": both_ok,
            "zd_zg_disjoint_dd_gg_overlap": zd_zg_disjoint_dd_gg_overlap,
            "old": old_totals,
            "new": new_totals,
            "codes_changed": len(changed),
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
