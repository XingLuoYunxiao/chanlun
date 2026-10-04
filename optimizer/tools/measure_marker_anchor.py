"""D-42 的证据工具：背驰 / 买卖点的 `(ts, price)` 必须落在**同一根** K 线里。

背景：`Divergence.price` / `Signal.price` 取的是**整段**的极值
（`divergence.py::_make` 的 `price = seg.low if seg.direction == -1 else seg.high`，
`signal.py::_sig` 同理），而 `ts` 过去取的是**段尾那一笔**的结束分型时刻
（`ts = seg.end.end.ts`）。两者不在同一根 bar 上 —— 标记会被画在那个价格的高度
上，x 轴却落在另一天。实测 ST洲际（`sh.600759`）日线最后一个盘整背驰卖：
`price = 2.95` 被钉在 `2026-09-29`，而那天最高只有 `2.23`；真正的极值发生在
`2026-07-14`（`seg.high = 2.95`）。

D-42 把 `ts` 改成「线段极值所在**原始 bar** 的时刻」（`divergence.py::extreme_ts`），
`price` 的口径**一个字没动** —— 所以信号集合、买卖点的成立与否、`_second_kind`
的 `back.low > first.price` 判据全都不受影响，变的只有时刻。

本工具量三件事，**三条都实测能失败**：

  判据一  现口径下，`price` 落在自己 `ts` 那根 bar 的 `[low, high]` **之外**的
          实体数 = **0**。
  判据二  **正对照**：实体总数必须 **> 0**，且与 D-42 记录的数一致
          （背驰 31821 / 买卖点 loose 52441）。否则判据一量的是「这批票根本没有
          信号」，而不是「口径正确」；也防住「越界归零是靠删信号实现的」。
  判据三  **可失败性（内置旧口径对照）**：同一次运行里再算一遍「如果 `ts` 仍是
          `seg.end.end.ts`」的越界数，必须 **> 0**。这一条是**内置的负对照** ——
          不需要改代码、不需要另一个 checkout，就能证明判据一不是恒真的。
          实测（修复前主干，全市场 5440 票）：背驰 **1076 / 31821**，
          买卖点 loose **2302 / 52441**，涉及 **1915** 只票，**100% 是
          `status=tentative`**。最坏的一条是 `sz.001331`：`price = 4.16271`
          被钉在 `2026-09-21`，那天是 `[45.49, 47.24]`。
          （本工具自己打印比值，用的是 `max(price/high, low/price)`；
          与旧探针的公式不同，**不要混引旧探针的百分比**。）

被否决的两个替代口径（都在全市场量过，都不如现在这个）：

  * **区间 `argmax` / `argmin`**（`[src_start, src_end]` 内取最高/最低 bar）：
    越界 136 / 249，但其中 **128 / 232 是 `confirmed`** —— 因为区间内可能存在
    **不是分型端点**的异常高/低 bar。实测 `sz.301439` 的 `seg10`：
    `Segment.high = 18.38`，而同一区间的原始 bar 最高是 **25.35**（包含处理把它
    并掉了）。取 argmax 会落到那根 bar 上，把背驰点钉到一个**不属于该线段**的
    价格上。
  * **取段尾同向笔的分型**（`seg.end.direction == want` 时用 `seg.end.end.ts`，
    否则用 `seg.end.start.ts`）：越界 **786 / 1761**，**比旧口径还差**。
    原因是结构性的：`Segment` 只留 `start` / `end` 两笔，而极值可以落在**中间**
    任何一笔上。实测 `sz.001331` 的 `seg6`（`direction = -1`，44 笔，TENTATIVE）
    最低点 `4.16271` 落在段的**开头**（`2024-04-17`，`start笔` 的第二个分型），
    段尾却是向上笔 ⇒ 该方案把 `4.16271` 钉到 `2026-08-20`，那天是 `[40.65, 45.28]`。

**为什么必须按「值相等」定位**：`Segment.high/low` 由 `segment.py:451-452` 的
`max/min(x.high for x in 笔)` 定义，而 `Stroke.high/low` 又是
`max/min(a.price, b.price)`（`stroke.py:82-83`）—— 极值是**某一笔端分型**的价格，
是**包含处理后**的合并 K 线值。所以定位方式是：在 `[src_start, src_end]` 内找
**值等于** `Segment.high/low` 的那根 bar，取它的 `ts`。找不到时退回
`seg.end.end.ts`（保持旧口径，不抛）；本工具把「找不到」单独计数。

**加载路径**：用 `store.read`（内部已 `normalize`，`store.py:99`），
与 `ChanEngine.full`（`engine.py:152` 再 `normalize` 一次，幂等）**同一个索引空间**。
直接读 parquet 而不 `normalize` 的探针会算出幻影失败 —— `sz.399001` 有 5 根全 0 行
（1995-02-06 ~ 1995-02-10），`normalize` 会剔除，索引因此错位 5 行。

用法：

    cd <本项目根目录>

    # 全市场日线（D-42 记录里的数字）
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_marker_anchor.py

    # 小样本冒烟
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_marker_anchor.py --limit 400

    # 看反例清单
    PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_marker_anchor.py --list-violations --limit 400
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from chanlun.chan.divergence import find_divergences  # noqa: E402
from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.chan.signal import SignalKind, SignalMode, find_signals  # noqa: E402
from chanlun.data import store  # noqa: E402

#: D-42 记录的全市场基线实体数（判据二的正对照下限用）。
BASELINE_DIV = 31821
BASELINE_SIG_LOOSE = 52441

#: 价格与 bar 区间的容差；价格是 float，用 1e-6 吸收二进制表示误差。
EPS = 1e-6


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


def _locate(bars, a: int, b: int, col: str, target: float) -> str | None:
    """在 `bars[col].iloc[a : b+1]` 里找**值等于** `target` 的那根 bar 的 `ts`。

    与 `divergence.py::extreme_ts` 同一套判据；找不到返回 `None`（调用方退回
    `seg.end.end.ts`）。
    """
    vals = bars[col].iloc[a:b + 1].to_numpy(float)
    for k, v in enumerate(vals):
        if abs(float(v) - target) <= 1e-9:
            return str(bars["ts"].iloc[a + k])
    return None


def _segment_of(segs, sig):
    """`Signal` 没有 `seg_idx`，按 `src_start` / `src_end` 反查（同 `_second_kind`）。"""
    for s in segs:
        if s.src_start == sig.src_start and s.src_end == sig.src_end:
            return s
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="day")
    ap.add_argument("--min-bars", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（0 = 全部）")
    ap.add_argument("--list-violations", action="store_true",
                    help="打印现口径下越界的实体（判据一红的时候）")
    args = ap.parse_args()

    codes = _corpus(args.period, args.limit)
    print(f"语料：{args.period} {len(codes)} 只票"
          f"（--min-bars {args.min_bars}，--limit {args.limit or '全部'}）")

    read_fail = skipped = 0
    # (现口径越界, 旧口径越界, 总数, 定位失败)
    stat = {
        "div": [0, 0, 0, 0],
        "strict": [0, 0, 0, 0],
        "loose": [0, 0, 0, 0],
    }
    bad_status: Counter = Counter()
    tickers_with_violation = 0
    worst: list[tuple[float, str]] = []
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
        snap = ChanEngine(code, args.period, signal_fn=find_signals,
                          level=args.period,
                          divergence_fn=find_divergences).full(bars)
        segs, pivots = list(snap.segments), list(snap.pivots)
        lo = bars["low"].to_numpy(float)
        hi = bars["high"].to_numpy(float)
        tss = [str(t) for t in bars["ts"]]
        pos = {t: k for k, t in enumerate(tss)}

        bad_here = 0

        def _check(kind: str, ts, price: float, legacy_ts: str, label: str,
                   status: str, found: bool) -> None:
            """把一条实体记进 `stat[kind]`；现口径越界就登记反例。"""
            nonlocal bad_here
            st = stat[kind]
            st[2] += 1
            if not found:
                st[3] += 1
            j = pos.get(str(ts))
            if j is None or not (lo[j] - EPS <= price <= hi[j] + EPS):
                st[0] += 1
                bad_status[status] += 1
                if j is not None and hi[j] > 0:
                    over = max(price / hi[j], lo[j] / price if price > 0 else 0.0)
                    worst.append((over, f"{code} {label} ts={ts} price={price:.4f} "
                                       f"bar=[{lo[j]:.2f},{hi[j]:.2f}] {status}"))
                else:
                    worst.append((float("inf"),
                                  f"{code} {label} ts={ts} price={price:.4f} "
                                  f"bar=不存在 {status}"))
                violations.append(f"{code} {label} ts={ts} price={price:.4f} "
                                  f"legacy_ts={legacy_ts} {status}")
                return
            k = pos.get(legacy_ts)
            if k is None or not (lo[k] - EPS <= price <= hi[k] + EPS):
                st[1] += 1

        for d in snap.divergences:
            seg = segs[d.seg_idx]
            want = d.direction
            col = "high" if want == 1 else "low"
            target = seg.high if want == 1 else seg.low
            anchored = _locate(bars, int(seg.src_start), int(seg.src_end), col, target)
            before = len(violations)
            _check("div", d.ts, float(d.price), seg.end.end.ts,
                   f"背驰{d.kind.value}", d.status.value, anchored is not None)
            if len(violations) > before:
                bad_here += 1

        for mode in (SignalMode.STRICT, SignalMode.LOOSE):
            sigs = (list(snap.signals) if mode is SignalMode.STRICT
                    else find_signals(bars, segs, pivots, args.period, mode=mode))
            for s in sigs:
                seg = _segment_of(segs, s)
                if seg is None:
                    continue
                want = -1 if SignalKind(s.kind).is_buy else 1
                col = "high" if want == 1 else "low"
                target = seg.high if want == 1 else seg.low
                anchored = _locate(bars, int(seg.src_start), int(seg.src_end),
                                   col, target)
                before = len(violations)
                _check(mode.value, s.ts, float(s.price), seg.end.end.ts,
                       f"买卖点{s.kind.value}", s.status.value, anchored is not None)
                if len(violations) > before:
                    bad_here += 1
        if bad_here:
            tickers_with_violation += 1
        if i % 500 == 0:
            print(f"  … {i}/{len(codes)}")

    print()
    print(f"读取失败 {read_fail} 只；不足 {args.min_bars} 根 K 线跳过 {skipped} 只")
    print()
    print(f"{'实体':<16}{'总数':>8}{'现口径越界':>12}{'旧口径越界':>12}{'定位失败':>10}")
    for kind, label in (("div", "背驰"), ("strict", "买卖点 strict"),
                        ("loose", "买卖点 loose")):
        st = stat[kind]
        print(f"{label:<16}{st[2]:>8}{st[0]:>12}{st[1]:>12}{st[3]:>10}")
    print()
    print(f"现口径越界按状态：{dict(bad_status) or '{}'}")
    print(f"有越界的票：{tickers_with_violation} / {len(codes)}")
    if worst:
        print("最坏 8 条：")
        for over, line in sorted(worst, reverse=True)[:8]:
            print(f"  {over * 100:>8.2f}%  {line}")

    now_bad = sum(stat[k][0] for k in stat)
    legacy_bad = sum(stat[k][1] for k in stat)
    total = sum(stat[k][2] for k in stat)
    ok1 = now_bad == 0
    ok2 = (stat["div"][2] > 0 and stat["loose"][2] > 0
           and (args.limit or stat["div"][2] == BASELINE_DIV)
           and (args.limit or stat["loose"][2] == BASELINE_SIG_LOOSE))
    ok3 = legacy_bad > 0
    print()
    if args.list_violations and violations:
        print(f"反例清单（{len(violations)} 条，最多打 200）：")
        for v in violations[:200]:
            print(f"  {v}")
        print()
    print(f"判据一 现口径越界 = {now_bad}（要求 0）：{'通过' if ok1 else '失败'}")
    print(f"判据二 正对照 实体总数 = {total}，背驰 {stat['div'][2]}、"
          f"买卖点 loose {stat['loose'][2]}（要求 > 0"
          f"{'' if args.limit else f' 且 == {BASELINE_DIV}/{BASELINE_SIG_LOOSE}'}）："
          f"{'通过' if ok2 else '失败'}")
    print(f"判据三 可失败性 旧口径越界 = {legacy_bad}（要求 > 0）："
          f"{'通过' if ok3 else '失败'}")
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(main())
