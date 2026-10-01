"""复现 F2：同一只票，`limit=1200` 的旧口径 vs 新口径 vs 全史。

旧口径 = 先把行情砍到最近 1200 根**再**划分（缺陷）；新口径 = 全量划分后**裁**到窗口；
全史 = 不设窗口。三种口径的线段/中枢/买卖点数一并打出来。

用途是让 `docs/evidence/2026-10-01-optimizer-findings.md` 里那张表**可复现**：
表里每个数字都是这个脚本跑出来的，不是手抄的。

    ../.venv-chanlun/bin/python optimizer/tools/measure_f2_window_scope.py

基准池 = 优化器审计用的 24 只票（`AUDIT_CODES`），跑一遍约 1 秒。
脚本自带断言：新口径的买卖点集合必须**恰好等于**「全史买卖点里落在窗口内的那些」——
这条不成立就说明裁剪规则写错了，脚本会当场炸，而不是打出一张看起来合理的表。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.signal import find_signals  # noqa: E402
from chanlun.data import store  # noqa: E402

#: 优化器审计基准池（24 只，沪深主板 + 创业板代表）
CODES = [
    "600000", "600030", "600036", "600276", "600519", "600887", "600900", "601012",
    "601088", "601166", "601318", "601899", "000001", "000333", "000651", "000725",
    "000858", "002415", "002594", "002714", "300059", "300124", "300750", "300760",
]

LIMIT = 1200


def _snap(code: str, df):
    return ChanEngine(code, "day", signal_fn=find_signals, level="day").full(df)


def main() -> int:
    head = (f"{'代码':<8}{'根数':>6}{'段旧':>5}{'段新':>5}{'段全':>5}"
            f"{'枢旧':>5}{'枢新':>5}{'枢全':>5}{'点旧':>5}{'点新':>5}{'点全':>5}"
            f"{'幻影':>5}{'藏掉':>5}")
    print(head)
    totals = dict(old=0, new=0, full=0, phantom=0, hidden=0, seg=0, piv=0, sig=0)
    for code in CODES:
        full = store.read(code, "day")
        s_full = _snap(code, full)
        bars = full.tail(LIMIT).reset_index(drop=True)
        first, last = str(bars["ts"].iloc[0]), str(full["ts"].iloc[-1])
        s_new = s_full.clipped_to(first, last)
        s_old = _snap(code, bars)
        full_ts = {str(s.ts) for s in s_full.signals}
        old_ts = {str(s.ts) for s in s_old.signals}
        new_ts = {str(s.ts) for s in s_new.signals}
        in_window = {t for t in full_ts if first <= t <= last}
        assert new_ts == in_window, (code, sorted(new_ts ^ in_window))
        phantom = old_ts - full_ts                      # 旧口径凭空造出来的
        hidden = in_window - old_ts                     # 旧口径藏掉的
        totals["old"] += len(old_ts)
        totals["new"] += len(new_ts)
        totals["full"] += len(full_ts)
        totals["phantom"] += len(phantom)
        totals["hidden"] += len(hidden)
        totals["seg"] += len(s_old.segments) != len(s_new.segments)
        totals["piv"] += len(s_old.pivots) != len(s_new.pivots)
        totals["sig"] += old_ts != new_ts
        print(f"{code:<8}{len(full):>6}{len(s_old.segments):>5}{len(s_new.segments):>5}"
              f"{len(s_full.segments):>5}{len(s_old.pivots):>5}{len(s_new.pivots):>5}"
              f"{len(s_full.pivots):>5}{len(old_ts):>5}{len(new_ts):>5}{len(full_ts):>5}"
              f"{len(phantom):>5}{len(hidden):>5}")
    print(f"\n合计：买卖点 旧 {totals['old']} → 新 {totals['new']}（全史 {totals['full']}）"
          f"，幻影 {totals['phantom']}，藏掉 {totals['hidden']}")
    print(f"逐只不同：线段 {totals['seg']}/{len(CODES)}，中枢 {totals['piv']}/{len(CODES)}，"
          f"买卖点 {totals['sig']}/{len(CODES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
