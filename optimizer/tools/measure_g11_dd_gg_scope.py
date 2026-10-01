"""G11 只读审计：第 20 课 GG/DD 的遍历范围（Z 走势段 vs 全部线段）。

第 20 课定义中枢的四个指标时写得很死：

> 为方便起见，以后都把这些与中枢方向一致的次级别走势类型称为Z走势段，
> 按中枢中的时间顺序，分别记为Zn等，而相应的高、低点分别记为gn、dn，
> 定义四个指标,GG=max(gn),G=min(gn),D=max(dn),DD=min(dn)，n遍历中枢中所有Zn。

`n` 遍历的是 **Z 走势段**（与中枢方向一致的那些），不是中枢里的全部线段。
`src/chanlun/chan/pivot.py::find_pivots` 却是
`gg=max(s.high for s in group)` / `dd=min(s.low for s in group)` —— 遍历全部线段，
于是中枢尾部那段**反向的离开段**的极值也被算进波动区间，把 `[DD, GG]` 撑宽。

这个脚本用同一份行情量出两件事：

- **M11d**：40 个确认中枢里，有多少个 `DD/GG` 与 Z 段口径不同、偏差多大；
- **M11e**：换成 Z 段口径后 `validate_pivots` 的结构不变量（`dd <= zd < zg <= gg`）
  是否仍然成立 —— 不成立就说明这个修法会把不变量弄坏。

脚本**不修改任何主干文件**，只在内存里重建 pivots，所以同一把尺子既能量
「改之前」也能量「改之后」。

    cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_g11_dd_gg_scope.py
"""

import json
import sys
from dataclasses import replace
from pathlib import Path

# 两种跑法：人手动 `python optimizer/tools/xxx.py`（有 __file__），以及 24x7 回访时
# 由 agent.py 在影子目录里 `python -c <脚本文本>` 跑（没有 __file__，cwd 是影子根）。
try:
    ROOT = Path(__file__).resolve().parents[2]
except NameError:  # pragma: no cover - `python -c` 分支
    ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.pivot import validate_pivots  # noqa: E402
from chanlun.chan.signal import find_signals  # noqa: E402
from chanlun.chan.types import Status  # noqa: E402
from chanlun.data import store  # noqa: E402
from chanlun.optimizer.agent import AUDIT_CODES as CODES  # noqa: E402
from chanlun.optimizer.agent import AUDIT_END as END  # noqa: E402
from chanlun.optimizer.agent import AUDIT_START as START  # noqa: E402


def z_scope(segments, pivots):
    """按第 20 课的 Z 走势段口径重算 gg/dd（只读，返回新列表）。

    中枢组只由 **CONFIRMED** 线段构成（`find_pivots` 是在 `confirmed` 子列表上
    切段的），所以这里必须先把未确认/失效线段剔掉，否则会多算进别的段。
    """
    confirmed = [s for s in segments if s.status is Status.CONFIRMED]
    out = []
    for p in pivots:
        grp = [s for s in confirmed if p.start_idx <= s.idx <= p.end_idx]
        if not grp:
            out.append(p)
            continue
        zs = [s for s in grp if s.direction == grp[0].direction]
        out.append(replace(p, gg=max(s.high for s in zs), dd=min(s.low for s in zs)))
    return out


def main() -> None:
    total = 0
    differ = 0
    even_only = True
    worst: tuple[float, str] = (0.0, "")
    examples: list[str] = []
    bad_after: list[str] = []
    bad_before: list[str] = []
    web_before: dict[str, str] = {}
    web_after: dict[str, str] = {}

    for code in CODES:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            continue
        snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
        segs = list(snap.segments)
        fixed = z_scope(segs, list(snap.pivots))

        confirmed_before = [p for p in snap.pivots if p.status is Status.CONFIRMED]
        bad_before.extend(f"{code}: {m}" for m in validate_pivots(list(snap.pivots)))
        bad_after.extend(f"{code}: {m}" for m in validate_pivots(fixed))

        for p_before, p_after in zip(snap.pivots, fixed):
            if p_before.status is not Status.CONFIRMED:
                continue
            total += 1
            grp = [s for s in segs
                   if s.status is Status.CONFIRMED
                   and p_before.start_idx <= s.idx <= p_before.end_idx]
            if abs(p_before.gg - p_after.gg) > 1e-9 or abs(p_before.dd - p_after.dd) > 1e-9:
                differ += 1
                # 中枢组的线段方向首尾交替，只有**偶数段**的中枢才会以一段
                # 反向段收尾，那一段的极值才会被全段口径多算进来。
                if len(grp) % 2 != 0:
                    even_only = False
                span = abs(p_before.gg - p_after.gg) + abs(p_before.dd - p_after.dd)
                label = (f"{code} P{p_before.idx} n={len(grp)} "
                         f"全段 DD/GG={p_before.dd:.2f}/{p_before.gg:.2f} -> "
                         f"Z段 DD/GG={p_after.dd:.2f}/{p_after.gg:.2f}")
                if span > worst[0]:
                    worst = (span, label)
                if len(examples) < 6:
                    examples.append(label)
            if code == "600030" and p_before.status is Status.CONFIRMED:
                web_before[f"P{p_before.idx}"] = f"{p_before.dd:.2f}/{p_before.gg:.2f}"
                web_after[f"P{p_before.idx}"] = f"{p_after.dd:.2f}/{p_after.gg:.2f}"

    print("== M11d 确认中枢的 DD/GG 遍历范围 ==")
    print(f"确认中枢总数                     : {total}")
    print(f"全段口径 与 Z 段口径不同的个数    : {differ}")
    print(f"差异是否只出现在偶数段中枢上      : {even_only}（偶数段中枢以反向离开段收尾）")
    print(f"偏差最大的一个                   : {worst[1]}")
    print("-- 反例 --")
    for line in examples:
        print("  " + line)
    print("== M11e 结构不变量 ==")
    print(f"主干 validate_pivots 问题数       : {len(bad_before)}")
    print(f"Z 段口径 validate_pivots 问题数   : {len(bad_after)}")
    for line in bad_after[:5]:
        print("  " + line)
    print("== 网页「中枢」行会印出来的 DD/GG（600030） ==")
    print(f"  全段口径: {web_before}")
    print(f"  Z 段口径: {web_after}")
    print("__METRICS__ " + json.dumps(
        {
            "confirmed_pivots": total,
            "dd_gg_scope_differs": differ,
            "diff_only_even_groups": even_only,
            "validate_problems_before": len(bad_before),
            "validate_problems_after": len(bad_after),
            "web_600030_before": web_before,
            "web_600030_after": web_after,
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
