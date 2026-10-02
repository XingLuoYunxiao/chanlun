"""只读审计：`[DD, GG]` 的遍历范围怎么取，第 20 课中心定理二才成立。

第 20 课对 `[DD, GG]` 只给了一句定义：

    为方便起见，以后都把这些与中枢方向一致的次级别走势类型称为Z走势段，按中枢
    中的时间顺序，分别记为Zn等，而相应的高、低点分别记为gn、dn，定义四个指标,
    GG=max(gn),G=min(gn),D=max(dn),DD=min(dn)，n遍历中枢中所有Zn。

「中枢中所有Zn」这句话没写清楚**离开段算不算中枢中的 Zn** —— 而中心定理二恰恰
靠 `[DD, GG]` 判趋势：

    前后同级别的两个缠中说禅走势中枢，后GG〈前DD等价于下跌及其延续；后DD〉前GG
    等价于上涨及其延续。后ZG<前ZD且后GG〉=前DD，或后ZD〉前ZG且后DD=<前GG，则等价
    于形成高级别的走势中枢。

若 `[DD, GG]` 把**离开段**（把价格带出中枢、朝着下一个中枢去的那一段）也算进去，
GG/DD 会被撑到下一个中枢的门口，前后两个波动区间就几乎必然重叠 —— 定理二里的
「趋势」永远判不出来。这个脚本把四种口径摆在一起量：

- **A**：中枢组**全部**段（最老的主干口径）；
- **B**：Zn（同向段），**含**离开段（2026-10-01 修 GG/DD 之后、修离开段之前的口径）；
- **C**：Zn（同向段），**不含**离开段（**当前主干口径**，见 `pivot.py` 取舍 5）；
- **D**：中枢组全部段，不含离开段；
- **E**：先丢掉中枢组的最后一段（离开段），再取同向段。

**T_trunk** 直接读主干 `Pivot.gg`/`Pivot.dd`，与 C 对照 —— 两者必须逐对相同，
否则说明主干的中枢 GG/DD 和本脚本的 C 口径已经不是一回事（本脚本的结论就不能
再拿来当主干的证据）。

判据用 `find_pivots` 已经定下的段边界：`Pivot.start_idx..end_idx` 是中枢组，
`end_idx` 那一段就是离开段（`pivot.py` 的约定：`segments[end_idx]` 是离开段、
`segments[end_idx + 1]` 是回试段）。

脚本**只读**，不修改任何主干文件。

    cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_l20_dd_gg_scope.py
"""

import json
import sys
from pathlib import Path

try:
    ROOT = Path(__file__).resolve().parents[2]
except NameError:  # pragma: no cover - `python -c` 分支
    ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.signal import find_signals  # noqa: E402
from chanlun.chan.types import Status  # noqa: E402
from chanlun.data import store  # noqa: E402
from chanlun.optimizer.agent import AUDIT_CODES as CODES  # noqa: E402
from chanlun.optimizer.agent import AUDIT_END as END  # noqa: E402
from chanlun.optimizer.agent import AUDIT_START as START  # noqa: E402

SCOPES = ("A_all", "B_zn_with_leave", "C_zn_no_leave", "D_all_no_leave", "E_zn_drop_lastseg")


def _scope_group(segments, p, scope: str) -> list:
    """按口径取出用来算 GG/DD 的那几段。"""
    group = segments[p.start_idx : p.end_idx + 1]
    if not group:
        return []
    direction = group[0].direction
    zn = [s for s in group if s.direction == direction]
    if scope == "A_all":
        return list(group)
    if scope == "B_zn_with_leave":
        return zn
    if scope == "C_zn_no_leave":
        # 丢掉「把价格带出中枢」的那一个 Zn（同向段里的最后一段）。
        return zn[:-1] if len(zn) > 1 else zn
    if scope == "D_all_no_leave":
        return list(group[:-1]) if len(group) > 1 else list(group)
    if scope == "E_zn_drop_lastseg":
        # 先丢掉中枢组的最后一段（离开段），再取同向段。
        return [s for s in group[:-1] if s.direction == direction] or zn
    raise ValueError(scope)


def _gg_dd(segments, p, scope: str) -> tuple[float, float]:
    legs = _scope_group(segments, p, scope)
    return max(s.high for s in legs), min(s.low for s in legs)


def _theorem2(a_gg: float, a_dd: float, b_gg: float, b_dd: float) -> str:
    """第 20 课中心定理二：趋势（涨/跌）还是「形成高级别的走势中枢」。"""
    if b_dd > a_gg:
        return "up"
    if b_gg < a_dd:
        return "down"
    if b_gg >= a_dd:
        return "levelup"
    return "none"


def main() -> None:
    tally = {s: {"up": 0, "down": 0, "levelup": 0, "none": 0} for s in SCOPES}
    tally["T_trunk"] = {"up": 0, "down": 0, "levelup": 0, "none": 0}
    zd_zg_disjoint = 0
    pairs = 0
    pivots_total = 0
    changed_vs_B = {s: 0 for s in SCOPES}
    trunk_vs_C = 0
    examples: list[str] = []

    for code in CODES:
        bars = store.read(code, "day", start=START, end=END)
        if bars is None or len(bars) == 0:
            continue
        snap = ChanEngine(code, "day", signal_fn=find_signals).full(bars)
        segs = list(snap.segments)
        pivots = [p for p in snap.pivots if p.status is Status.CONFIRMED]
        pivots_total += len(pivots)

        ggdd = {s: [_gg_dd(segs, p, s) for p in pivots] for s in SCOPES}
        for s in SCOPES:
            changed_vs_B[s] += sum(
                1 for i in range(len(pivots)) if ggdd[s][i] != ggdd["B_zn_with_leave"][i]
            )
        trunk_vs_C += sum(
            1 for i, p in enumerate(pivots) if (p.gg, p.dd) != ggdd["C_zn_no_leave"][i]
        )

        for k, (pa, pb) in enumerate(zip(pivots, pivots[1:])):
            pairs += 1
            if not (min(pa.zg, pb.zg) > max(pa.zd, pb.zd)):
                zd_zg_disjoint += 1
            for s in SCOPES:
                a_gg, a_dd = ggdd[s][k]
                b_gg, b_dd = ggdd[s][k + 1]
                tally[s][_theorem2(a_gg, a_dd, b_gg, b_dd)] += 1
            tally["T_trunk"][_theorem2(pa.gg, pa.dd, pb.gg, pb.dd)] += 1
            if len(examples) < 6:
                row = " | ".join(
                    f"{s[0]}=[{ggdd[s][k][1]:.2f},{ggdd[s][k][0]:.2f}]→"
                    f"[{ggdd[s][k + 1][1]:.2f},{ggdd[s][k + 1][0]:.2f}]"
                    for s in SCOPES
                )
                examples.append(
                    f"{code} P{pa.idx}->P{pb.idx} [ZD,ZG]=[{pa.zd:.2f},{pa.zg:.2f}]"
                    f"→[{pb.zd:.2f},{pb.zg:.2f}] {row}"
                )

    print("== L20 中心定理二：`[DD, GG]` 遍历范围对照（24 只票 / 日线） ==")
    print(f"确认中枢总数            : {pivots_total}")
    print(f"相邻中枢对总数          : {pairs}")
    print(f"  [ZD,ZG] 不重叠的对数  : {zd_zg_disjoint}")
    print("-- 各口径下定理二的分类 --")
    for s in list(SCOPES) + ["T_trunk"]:
        t = tally[s]
        suffix = "" if s == "T_trunk" else f"   (GG/DD 与 B 口径不同的中枢数 {changed_vs_B[s]})"
        print(
            f"  {s:18s} 上涨 {t['up']:3d}  下跌 {t['down']:3d}  "
            f"高级别中枢 {t['levelup']:3d}  无关系 {t['none']:3d}{suffix}"
        )
    print(f"-- 主干 Pivot.gg/dd 与 C 口径不同的中枢数: {trunk_vs_C}（必须是 0） --")
    print("-- 样例 --")
    for line in examples:
        print("  " + line)
    print("__METRICS__ " + json.dumps(
        {
            "pivots": pivots_total,
            "pairs": pairs,
            "zd_zg_disjoint": zd_zg_disjoint,
            "tally": tally,
            "changed_vs_B": changed_vs_B,
            "trunk_vs_C": trunk_vs_C,
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
