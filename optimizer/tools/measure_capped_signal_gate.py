"""D-41 的证据工具：`capped` 中枢在**信号层**有没有真的被收口。

背景：第 33 课说中枢延伸不能超过 5 段（本级别最多 3+5 = 8 段），凑满 9 段就是
更大级别的中枢。`pivot.py` 早就按这条收口，并把「上限真正掐停」记成
`Pivot.capped`（`pivot.py:202`）；但 `capped` 的中枢，其 `end_idx` 那一段**不是**
离开段 —— 组内每一段（含 `end_idx`）都仍与 `[ZD, ZG]` 重叠，只是段数到顶才停下。
信号层过去一直按**位置**把 `segs[end_idx]` 当离开段，于是被上限掐停的中枢照样
产出「离开-依赖」的买卖点与趋势背驰。D-41 在 `_third_kind`、
`_entering_and_leaving`、`_leaving_legs` 三处加了 `if p.capped: continue`。

本工具量三件事，**三条都实测能失败**（下面每条都写了实测的扰动方式与结果）：

  判据一  `capped` 中枢上的「离开-依赖」信号 = **0**。
          离开-依赖 = 第三类（b3/s3，第 20 课位置口径）、第一类（b1/s1，趋势背驰）、
          以及 `find_divergences` 的趋势背驰条目。
          实测扰动：把 `signal.py::_entering_and_leaving` 与
          `divergence.py::_leaving_legs` 两处门改成 `if False and p.capped:`，
          全市场 5440 票重跑 ⇒ `capped` 列变成
          `b1 46 / s1 18 / b3 0 / s3 0 / 趋势背驰 64`，判据一 = **192**，
          退出码 **1**。
          **注意覆盖面**：这一条**量不到** `_third_kind` 那一处门 —— 单独去掉它
          全市场输出逐项不变（`capped` 列恒为 0）。原因是结构性的：`capped` 的
          定义要求 `end_idx + 1` 与 `[ZD, ZG]` 重叠，而第 20 课的位置判据要求
          回试段在区间之外，两者在**同一段**上互斥 ⇒ 那一处门永远不会改变产出。
          它由 `tests/chan/test_signal_mode.py::test_capped_pivot_emits_no_third_kind_buy`
          等三条单元测试作为**不变量**守住，不是靠本工具。
  判据二  **正对照**：非 `capped` 中枢上同样的计数必须 **> 0**。
          否则判据一在量的是「这批票根本没有中枢/没有信号」，而不是 `capped` 门。
          实测（全市场）：11924。
  判据三  严格 / 非严格两个口径在第三类上**逐项相同**（D-41 删掉了 `THIRD_TOL`）。
          实测扰动：把 `THIRD_TOL` 的旧语义加回去（`_third_kind` 收 `mode` 参数、
          只在 `LOOSE` 时用 0.1 的容忍度），`--limit 600` 重跑 ⇒
          `strict b3 327 / s3 244` vs `loose b3 330 / s3 245`，判据三 **失败**，
          退出码 **1**。

信号按 `Signal.pivot_idx` 归因到具体中枢，趋势背驰按 `Divergence.pivot_idx`
（`divergence.py::_make` 由离开段的下标反查中枢），所以量的是**被判定的那个中枢**
本身，不是从上游继承来的下标。

**小样本没有判别力**：`--limit 60` 下把三处门全部去掉，输出与未扰动**逐字节相同**
（判据一 0、判据二 169、判据三通过）。这一条的判别力来自 5440 票的全市场跑，
不要用小样本的绿色当证据。

用法：

    cd <本项目根目录>

    # 全市场日线（5440 票，D-41 记录里的数字）
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_capped_signal_gate.py

    # 小样本冒烟
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_capped_signal_gate.py --limit 60

    # 看反例清单（判据一红的时候）
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_capped_signal_gate.py --list-violations
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun.chan.divergence import DivergenceKind, find_divergences  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.signal import SignalKind, SignalMode, find_signals  # noqa: E402
from chanlun.chan.types import Status  # noqa: E402
from chanlun.data import store  # noqa: E402

#: 「离开-依赖」的买卖点类型 —— 判据都要求「有一段离开了中枢区间」。
LEAVE_DEPENDENT = (SignalKind.B1, SignalKind.S1, SignalKind.B3, SignalKind.S3)
MODES = (SignalMode.STRICT, SignalMode.LOOSE)


def _corpus(period: str, limit: int) -> list[str]:
    root = store.data_root() / period
    codes: list[str] = []
    for pref in ("sh", "sz", "bj"):
        d = root / pref
        if not d.is_dir():
            continue
        codes += [f"{pref}.{p.stem}" for p in sorted(d.glob("*.parquet"))]
    if limit:
        codes = codes[:limit]
    return codes


def _pivot_of(snap, pivot_idx: int):
    """按 `Pivot.idx` 取回中枢（`pivots[i].idx == i`，仍显式查一遍）。"""
    for p in snap.pivots:
        if p.idx == pivot_idx:
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="day")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（0 = 全部）")
    ap.add_argument("--list-violations", action="store_true",
                    help="打印每一个落在 capped 中枢上的离开-依赖信号")
    args = ap.parse_args()

    codes = _corpus(args.period, args.limit)
    print(f"语料：{args.period} {len(codes)} 只票（--min-bars {args.min_bars}，--limit {args.limit or '全部'}）")

    read_fail = skipped = 0
    n_piv = n_conf = n_capped = 0
    # 判据一 / 二：按 (中枢类别, 模式) 分桶
    on_capped: Counter = Counter()
    on_free: Counter = Counter()
    div_capped = div_free = 0
    # 判据三：两口径的第三类计数
    third = {m: Counter() for m in MODES}
    violations: list[str] = []

    for i, code in enumerate(codes, 1):
        try:
            bars = store.read(code, args.period)
        except Exception as exc:                       # noqa: BLE001
            read_fail += 1
            print(f"  ! 读取失败 {code}: {exc}")
            continue
        if len(bars) < args.min_bars:
            skipped += 1
            continue
        # 一次引擎调用即可：中枢/背驰与 mode 无关，非严格信号在同一个
        # `segments`/`pivots` 上再调一次 `find_signals`（与 web/api.py 同法）。
        snap = ChanEngine(code, args.period, signal_fn=find_signals,
                          level=args.period,
                          divergence_fn=find_divergences).full(bars)
        segs, pivots = list(snap.segments), list(snap.pivots)
        for p in pivots:
            n_piv += 1
            if p.status is Status.CONFIRMED:
                n_conf += 1
            if p.capped:
                n_capped += 1
        for mode in MODES:
            sigs = (list(snap.signals) if mode is SignalMode.STRICT
                    else find_signals(bars, segs, pivots, args.period, mode=mode))
            for s in sigs:
                if s.kind not in LEAVE_DEPENDENT:
                    continue
                p = _pivot_of(snap, s.pivot_idx)
                bucket = on_capped if (p is not None and p.capped) else on_free
                bucket[(s.kind.value, mode.value)] += 1
                if s.kind in (SignalKind.B3, SignalKind.S3):
                    third[mode][s.kind.value] += 1
                if p is not None and p.capped:
                    violations.append(
                        f"{code} {mode.value} {s.kind.value} {s.ts} "
                        f"中枢#{s.pivot_idx}(段数={p.segment_count}, "
                        f"status={p.status.value}, capped={p.capped})")
        for d in snap.divergences:
            if d.kind is not DivergenceKind.TREND:
                continue
            p = _pivot_of(snap, d.pivot_idx)
            if p is not None and p.capped:
                div_capped += 1
            else:
                div_free += 1
        if i % 500 == 0:
            print(f"  … {i}/{len(codes)}")

    print()
    print(f"读取失败 {read_fail} 只；不足 {args.min_bars} 根 K 线跳过 {skipped} 只")
    share = (n_capped / n_conf * 100) if n_conf else 0.0
    print(f"中枢 {n_piv} 个，其中 status=CONFIRMED {n_conf} 个，"
          f"capped {n_capped} 个（占确认中枢 {share:.1f}%）")
    print()
    print("判据一/二：离开-依赖信号落在哪一类中枢上")
    print(f"{'模式':<8}{'类型':<6}{'capped':>10}{'非 capped':>12}")
    for mode in MODES:
        for kind in LEAVE_DEPENDENT:
            print(f"{mode.value:<8}{kind.value:<6}{on_capped[(kind.value, mode.value)]:>10}"
                  f"{on_free[(kind.value, mode.value)]:>12}")
    print(f"{'趋势背驰':<14}{div_capped:>10}{div_free:>12}")
    print()
    print("判据三：第三类买卖点的严格 / 非严格计数")
    for mode in MODES:
        print(f"  {mode.value:<8} b3 {third[mode]['b3']} / s3 {third[mode]['s3']}")

    capped_hits = sum(on_capped.values()) + div_capped
    free_hits = sum(on_free.values()) + div_free
    print()
    if args.list_violations and violations:
        print(f"反例清单（{len(violations)} 条）：")
        for v in violations[:200]:
            print(f"  {v}")
    ok1 = capped_hits == 0
    ok2 = free_hits > 0
    ok3 = third[SignalMode.STRICT] == third[SignalMode.LOOSE]
    print(f"判据一 capped 中枢上的离开-依赖信号 = {capped_hits}（要求 0）："
          f"{'通过' if ok1 else '失败'}")
    print(f"判据二 正对照 非 capped 中枢上的同类信号 = {free_hits}（要求 > 0）："
          f"{'通过' if ok2 else '失败'}")
    print(f"判据三 严格/非严格第三类逐项相同：{'通过' if ok3 else '失败'}")
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
