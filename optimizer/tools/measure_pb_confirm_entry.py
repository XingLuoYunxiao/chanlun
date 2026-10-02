"""按【可实现口径】重测盘整背驰买点：入场用 `confirmed_at`（首次可见），不是 `ts`（极值日）。

## 为什么这个脚本存在

`ratio_sweep_raw.csv` 的 f20/f60/f120 是按 `Divergence.ts`（背驰**极值日**）算的。
实测 `sz.000688`：`ts=2022-04-27` 而 `confirmed_at=2024-03-28` —— **晚 1.9 年**。
在极值日你**不可能知道**那是背驰（要等结构确认），所以那批数字带前视偏差、不可实现。
**这是本项目第 6 次同类错误**，自查规则见证据文档「已作废的口径」一节。

**★ 本脚本是唯一正确的口径。** 一切前瞻收益 / 胜率数字必须以
`confirmed_at` 为基准，并写明**入场方式**（确认日收盘 / 次日开盘）与**持有根数**。

**★ 但 `confirmed_at` 口径本身仍然不是「可实现」的**：`confirmed_at` 是
「结构自身的确认完成时刻」，实测 **100% 早于引擎首次可见**（首次可见 = 回测真正
能成交的那根 bar，D-34）。所以本脚本给出的「为正比例」（如 h60 58.3%）只能写成
**上界 / 乐观上界**，**不能**写成「非严格模式的胜率」。
唯一可当作可实现胜率的数字来自回测（`measure_loose_winrate.py`）。

## 产出：四张对照表 + 去扎堆稳健性

| # | 对照组 | 用途 |
|---|---|---|
| ① | 无条件（全部 bar） | 「随便哪天买」的基线 |
| ② | 同票随机日期（`random.Random(hash(code) & 0xFFFF)`） | 排除「这段时间整个市场在涨」 |
| ③ | 全部局部低点（±10 根窗口内的最低收盘） | 选点质量参考，**带前视，仅作参考** |
| ④ | 按 `confirmed_at` 收盘 / 次日开盘入场 | **主表** |

去扎堆稳健性：按月聚合取中位、按票聚合取中位、剔掉最集中的两个月、
并按**滞后**（`ts` → `confirmed_at` 的交易日差）分桶 —— 滞后是本口径**新引入**的
扎堆维度（`confirmed_at` 可能把信号挤到同一时期）。

## 复现

    cd /Users/zzz/workspace/chanlun
    PYTHONHASHSEED=0 PYTHONPATH=src ../.venv-chanlun/bin/python \
        optimizer/tools/measure_pb_confirm_entry.py --n 120 --seed 7 \
        --out /tmp/pb_confirm_entry.json 2>&1 | grep -v '^normalize:'

**★ `PYTHONHASHSEED=0` 不能省**：对照 ② 用 `hash(code)` 做种子，而 Python 的
`hash(str)` 默认每进程随机加盐 ⇒ 不固定它，② 那行**每次重跑都不一样**，
违反 AGENTS.md §5「别人重跑应得到同一个数」。① ③ ④ 与稳健性各节不依赖它。

## 来源与改动说明

本文件由控制者的临时探针 `/tmp/exp/pb_confirm_entry.py` **移入**（Task 9，
AGENTS.md §5：数字必须能从入库脚本复现）。移入时**既有测量逻辑逐字未改**，
只做了三类改动：

1. 路径：项目根改为从 `__file__` 解析（`parents[2]`）并加守卫，不再硬写绝对路径；
   输出改为 `--out`（默认 `/tmp/pb_confirm_entry.json`），参数化 `--n/--seed/--min-bars`。
2. **零样本护栏**（Task 9 硬规则：任何测量脚本必须对零样本大声失败）：
   票池为空 / 过滤后 0 只票 / 0 个信号 / 0 条基线，一律 `SystemExit`。
3. **Task 9 追加的两节**（原有 ①②④ 的计算未动）：
   - ③ 局部低点对照 —— 旧数字出自**未入库**的第一版探针，按 §9.3 不得引用，
     必须由入库脚本重新产出。定义沿用 `progress.md` 记的「±10 根窗口内的最低收盘」。
   - 去扎堆稳健性 —— 旧的按月/按票/剔两个月那批锚在 `ts` 上，全部作废，必须重做。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# ★ 与 measure_loose_winrate.py / check_mem_read_equiv.py 同样的守卫。
if not (ROOT / "data" / "day").is_dir() or not (ROOT / "src" / "chanlun").is_dir():
    raise SystemExit(
        f"★ 项目根解析错误：ROOT={ROOT} 下找不到 data/day 或 src/chanlun。\n"
        f"  本脚本必须放在 chanlun/optimizer/tools/ 内运行（当前 {Path(__file__).resolve()}）。"
    )
sys.path.insert(0, str(ROOT / "src"))

from chanlun.data import store                      # noqa: E402

store.DATA_ROOT = ROOT / "data"

from chanlun.chan import engine as engine_mod       # noqa: E402
from chanlun.chan.signal import find_signals        # noqa: E402
from chanlun.chan.divergence import find_divergences  # noqa: E402

HORIZONS = (20, 60, 120)
LOCAL_WINDOW = 10          # ③ 局部低点：±10 根窗口内的最低收盘（沿用 progress.md 的定义）


def stat(vals):
    """中位 / 均值 / >0 比例 / 分位。空样本返回 None（**不许当 0 平均进去**）。"""
    if not vals:
        return None
    vals = sorted(vals)
    return dict(n=len(vals), mean=st.mean(vals), median=st.median(vals),
                pos=sum(1 for v in vals if v > 0) / len(vals),
                p25=vals[len(vals) // 4], p75=vals[3 * len(vals) // 4])


def row_line(tag, s):
    if s is None:
        return f"{tag:>22}  —— 无样本"
    return (f"{tag:>22} {s['n']:>6} {s['mean']:>+9.4f} {s['median']:>+9.4f} "
            f"{s['pos'] * 100:>7.1f}% {s['p25']:>+9.4f} {s['p75']:>+9.4f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120, help="票池洗牌后取前 n 只（再按 min-bars 过滤）")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--out", default="/tmp/pb_confirm_entry.json")
    args = ap.parse_args()

    codes = []
    for mk in ("sh", "sz"):
        for p in sorted((store.DATA_ROOT / "day" / mk).glob("*.parquet")):
            codes.append(f"{mk}.{p.stem}")
    if not codes:
        raise SystemExit(f"★ 票池为空：{(store.DATA_ROOT / 'day')} 下没有 parquet。测量无意义，停止。")
    random.seed(args.seed)
    random.shuffle(codes)
    codes = codes[:args.n]

    rows = []          # 盘整背驰买点，confirmed_at 入场
    lags = []          # ts -> confirmed_at 滞后（交易日）
    baseline = []      # 无条件：全部 bar 的 f20/f60/f120
    rand_rows = []     # 随机日期对照
    lows = []          # 局部低点对照（带前视）
    used = []          # 实际进入测量的票

    for code in codes:
        try:
            bars = store.read(code, "day")
        except Exception:
            continue
        if bars is None or len(bars) < args.min_bars:
            continue
        used.append(code)
        n = len(bars)
        close = bars["close"].to_numpy(dtype=float)
        openp = bars["open"].to_numpy(dtype=float)
        dstr = bars["ts"].astype(str).str.slice(0, 10).to_numpy()
        idx_of = {d: i for i, d in enumerate(dstr)}

        # ---- 无条件基线（全部 bar）----
        for h in HORIZONS:
            for i in range(0, n - h):
                baseline.append((h, close[i + h] / close[i] - 1.0))

        # ---- ③ 局部低点（±LOCAL_WINDOW 根窗口内的最低收盘）----
        # ★ 带前视：判定 close[i] 是低点用到了 i+1..i+10 的 K 线。仅作选点质量参考。
        for i in range(LOCAL_WINDOW, n - max(HORIZONS)):
            if i + LOCAL_WINDOW >= n:
                continue
            if close[i] == min(close[i - LOCAL_WINDOW:i + LOCAL_WINDOW + 1]):
                for h in HORIZONS:
                    lows.append((h, close[i + h] / close[i] - 1.0))

        # ---- 随机日期对照：每票抽与 pb 同量级的日期 ----
        snap = engine_mod.ChanEngine(code, "day", signal_fn=find_signals,
                                    divergence_fn=find_divergences, level="day").full(bars)
        pbs = [d for d in snap.divergences
               if d.kind.value == "consolidation" and d.direction == -1]

        rng = random.Random(hash(code) & 0xFFFF)
        for _ in range(max(1, len(pbs))):
            i = rng.randrange(0, max(1, n - max(HORIZONS)))
            for h in HORIZONS:
                if i + h < n:
                    rand_rows.append((h, close[i + h] / close[i] - 1.0))

        for d in pbs:
            if not d.confirmed_at:
                continue
            j = idx_of.get(d.confirmed_at)
            if j is None:
                continue
            # 滞后：交易日数
            k = idx_of.get(d.ts)
            if k is not None:
                lags.append(j - k)
            rec = dict(code=code, ts=d.ts, confirmed_at=d.confirmed_at,
                       lag_days=j - k if k is not None else None,
                       entry_close=close[j],
                       entry_next_open=openp[j + 1] if j + 1 < n else None)
            ok = True
            for h in HORIZONS:
                if j + h < n:
                    rec[f"f{h}"] = close[j + h] / close[j] - 1.0
                    if j + 1 < n and openp[j + 1] > 0:
                        rec[f"o{h}"] = close[j + h] / openp[j + 1] - 1.0
                else:
                    ok = False
            if ok:
                rows.append(rec)

    # ---- ★ 零样本护栏：任何一段空样本都必须大声失败 ----
    if not used:
        raise SystemExit(f"★ 过滤后 0 只票（>= {args.min_bars} 根日线）。测量无意义，停止。")
    if not rows:
        raise SystemExit(
            f"★ 0 个盘整背驰买点（{len(used)} 只票）。\n"
            "  0 个样本算不出任何「上界」，不要把它当成结论。先查票池/区间/口径。"
        )
    if not baseline:
        raise SystemExit("★ 基线样本为空。没有对照组的数字不许进文档。")

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"票池 {store.DATA_ROOT}（硬写）；洗牌 seed={args.seed} 后取 {len(codes)} 只，"
          f"其中 {len(used)} 只 >= {args.min_bars} 根日线进入测量")
    print(f"盘整背驰买点（confirmed_at 可定位）n={len(rows)}，"
          f"覆盖 {len(set(r['code'] for r in rows))} 只票")
    print()

    print("=== ts -> confirmed_at 滞后（交易日）===")
    if lags:
        ls = sorted(lags)
        print(f"  n={len(ls)}  中位 {st.median(ls):.0f}  均值 {st.mean(ls):.0f}  "
              f"p25 {ls[len(ls) // 4]}  p75 {ls[3 * len(ls) // 4]}  min {ls[0]}  max {ls[-1]}")
        print(f"  滞后 >250 交易日（约一年以上）的比例："
              f"{sum(1 for v in ls if v > 250) / len(ls) * 100:.1f}%")
    else:
        raise SystemExit("★ 滞后样本为空 —— 与 rows 非空矛盾，先查 confirmed_at 解析。")
    print()

    print("=== ④ 主表：按 confirmed_at 收盘入场（假设能在确认日成交 ⇒ 上界）===")
    print(f"{'h':>4} {'n':>6} {'均值':>9} {'中位':>9} {'>0 比例':>8} {'p25':>9} {'p75':>9}")
    for h in HORIZONS:
        s = stat([r[f"f{h}"] for r in rows if f"f{h}" in r])
        if s:
            print(f"{h:>4} {s['n']:>6} {s['mean']:>+9.4f} {s['median']:>+9.4f} "
                  f"{s['pos'] * 100:>7.1f}% {s['p25']:>+9.4f} {s['p75']:>+9.4f}")
    print()

    print("=== ④ 按次日开盘入场（更接近回测口径）===")
    print(f"{'h':>4} {'n':>6} {'均值':>9} {'中位':>9} {'>0 比例':>8}")
    for h in HORIZONS:
        s = stat([r[f"o{h}"] for r in rows if f"o{h}" in r])
        if s:
            print(f"{h:>4} {s['n']:>6} {s['mean']:>+9.4f} {s['median']:>+9.4f} {s['pos'] * 100:>7.1f}%")
    print()

    print("=== ① 无条件（全部 bar）===")
    for h in HORIZONS:
        s = stat([v for hh, v in baseline if hh == h])
        if s:
            print(f"{h:>4} {s['n']:>6} {s['mean']:>+9.4f} {s['median']:>+9.4f} {s['pos'] * 100:>7.1f}%")
    print()

    print("=== ② 同票随机日期（同样本量级；种子 = hash(code) & 0xFFFF）===")
    for h in HORIZONS:
        s = stat([v for hh, v in rand_rows if hh == h])
        if s:
            print(f"{h:>4} {s['n']:>6} {s['mean']:>+9.4f} {s['median']:>+9.4f} {s['pos'] * 100:>7.1f}%")
    print()

    print("=== ③ 全部局部低点（±10 根窗口最低收盘）★ 带前视，仅作选点质量参考 ===")
    for h in HORIZONS:
        s = stat([v for hh, v in lows if hh == h])
        if s:
            print(f"{h:>4} {s['n']:>6} {s['mean']:>+9.4f} {s['median']:>+9.4f} {s['pos'] * 100:>7.1f}%")
    print()

    print("=== 超额（④ 主表中位 − 对照中位）===")
    for h in HORIZONS:
        a = stat([r[f"f{h}"] for r in rows if f"f{h}" in r])
        b = stat([v for hh, v in baseline if hh == h])
        c = stat([v for hh, v in rand_rows if hh == h])
        d = stat([v for hh, v in lows if hh == h])
        if a and b and c and d:
            print(f"  h={h:>3}  对 ① 无条件 {a['median'] - b['median']:>+9.4f}   "
                  f"对 ② 随机日期 {a['median'] - c['median']:>+9.4f}   "
                  f"对 ③ 局部低点 {a['median'] - d['median']:>+9.4f}")
    print()
    print("=== 超额（④ 主表 >0 比例 − 对照 >0 比例，pp）===")
    for h in HORIZONS:
        a = stat([r[f"f{h}"] for r in rows if f"f{h}" in r])
        b = stat([v for hh, v in baseline if hh == h])
        c = stat([v for hh, v in rand_rows if hh == h])
        d = stat([v for hh, v in lows if hh == h])
        if a and b and c and d:
            print(f"  h={h:>3}  对 ① {(a['pos'] - b['pos']) * 100:>+7.1f}   "
                  f"对 ② {(a['pos'] - c['pos']) * 100:>+7.1f}   "
                  f"对 ③ {(a['pos'] - d['pos']) * 100:>+7.1f}")
    print()

    # ---- ★ 去扎堆稳健性（全部在 confirmed_at 口径下重做）----
    print("=== 去扎堆稳健性（confirmed_at 口径）===")
    months = [r["confirmed_at"][:7] for r in rows]
    years = [r["confirmed_at"][:4] for r in rows]
    cnt_m = {}
    cnt_y = {}
    for m in months:
        cnt_m[m] = cnt_m.get(m, 0) + 1
    for y in years:
        cnt_y[y] = cnt_y.get(y, 0) + 1
    top_m = sorted(cnt_m.items(), key=lambda kv: (-kv[1], kv[0]))
    top_y = sorted(cnt_y.items(), key=lambda kv: (-kv[1], kv[0]))
    print(f"  信号 {len(rows)} 个 / {len(set(r['code'] for r in rows))} 只票"
          f"（{len(rows) / len(set(r['code'] for r in rows)):.2f} 次/只），"
          f"月份跨 {min(months)} ~ {max(months)}，共 {len(cnt_m)} 个月")
    print(f"  最集中的 3 个月：{[(m, c) for m, c in top_m[:3]]} "
          f"⇒ 占比 {sum(c for _, c in top_m[:3]) / len(rows) * 100:.1f}%")
    print(f"  最集中的 1 年：{top_y[0][0]} = {top_y[0][1] / len(rows) * 100:.1f}%；"
          f"年份分布 {dict(sorted(cnt_y.items()))}")

    by_month = {}
    by_code = {}
    for r in rows:
        by_month.setdefault(r["confirmed_at"][:7], []).append(r)
        by_code.setdefault(r["code"], []).append(r)
    drop = {m for m, _ in top_m[:2]}
    kept = [r for r in rows if r["confirmed_at"][:7] not in drop]
    print(f"  剔掉最集中的两个月 {sorted(drop)} 后 n={len(kept)}")
    print(f"{'口径':>22} {'n':>6} {'均值':>9} {'中位':>9} {'>0 比例':>8} {'p25':>9} {'p75':>9}")
    for h in HORIZONS:
        print(row_line(f"按月聚合取中位 h{h}", stat(
            [st.median([x[f"f{h}"] for x in v if f"f{h}" in x])
             for v in by_month.values() if any(f"f{h}" in x for x in v)])))
        print(row_line(f"按票聚合取中位 h{h}", stat(
            [st.median([x[f"f{h}"] for x in v if f"f{h}" in x])
             for v in by_code.values() if any(f"f{h}" in x for x in v)])))
        print(row_line(f"剔两个月后 h{h}", stat([r[f"f{h}"] for r in kept if f"f{h}" in r])))
    print()

    # ---- 滞后作为新的扎堆维度 ----
    print("=== 按滞后分桶（ts -> confirmed_at 交易日差）===")
    if lags:
        ls = sorted(lags)
        q1, q2, q3 = ls[len(ls) // 4], ls[len(ls) // 2], ls[3 * len(ls) // 4]

        def bucket(v):
            if v is None:
                return "无"
            if v <= q1:
                return f"Q1 <= {q1}"
            if v <= q2:
                return f"Q2 <= {q2}"
            if v <= q3:
                return f"Q3 <= {q3}"
            return f"Q4 > {q3}"

        print(f"  分位：p25={q1}  中位={q2}  p75={q3}")
        print(f"{'桶':>14} {'n':>6} {'h60 中位':>10} {'h60 >0':>8}")
        groups = {}
        for r in rows:
            groups.setdefault(bucket(r.get("lag_days")), []).append(r)
        for tag in sorted(groups, key=lambda t: (t == "无", t)):
            v = [x["f60"] for x in groups[tag] if "f60" in x]
            s = stat(v)
            if s:
                print(f"{tag:>14} {s['n']:>6} {s['median']:>+10.4f} {s['pos'] * 100:>7.1f}%")

    print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
