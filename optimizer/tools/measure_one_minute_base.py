"""测量：把基准从**日线**换成 **1 分钟**，递归的产出会变多少。

**要回答的问题**：用户的目标是「以 1 分钟为基准往上递归」。而递归的产出
高度依赖基准显微镜 —— 日线基准下 **90.6% 的票只出 1 级**（全市场 5478 只实测，
见 `measure_recursion.py --invariants`）。1 分钟基准是不是真的不一样？

**这是判据，不是报告**：结论落到 `ARCHITECTURE.md` D-53 与 §5。

跑法：
    cd <项目根>
    ../.venv-chanlun/bin/python optimizer/tools/measure_one_minute_base.py --sample 24

**联网**（要现场同步 1 分钟），所以不进测试。退出码 0 = 全部样本都产出 ≥2 级。
"""
from __future__ import annotations

import argparse
import glob
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.chan.engine import ChanEngine  # noqa: E402
from chanlun.config import load_config  # noqa: E402
from chanlun.data import meta, sources, store  # noqa: E402
from chanlun.data import sync as sync_mod  # noqa: E402


def _sample(n: int, seed: int) -> list[str]:
    """从**已有日线**的票里等距抽 —— 抽样框必须是「本来就有数据的票」，
    否则会把停牌/退市的空结果算成「递归没产出」。"""
    files = sorted(glob.glob(str(ROOT / "data" / "day" / "sh" / "*.parquet")))
    files += sorted(glob.glob(str(ROOT / "data" / "day" / "sz" / "*.parquet")))
    random.Random(seed).shuffle(files)
    out: list[str] = []
    for f in files:
        mkt = Path(f).parts[-2]
        bare = Path(f).stem
        # sh 的 `000xxx` 是指数（另有一批），这里只取个股，免得和市场口径混在一起
        if mkt == "sh" and bare.startswith("000"):
            continue
        out.append(sources.normalize_code(f"{mkt}.{bare}"))
        if len(out) >= n:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=24, help="抽多少只（含联网同步）")
    ap.add_argument("--seed", type=int, default=20261013)
    args = ap.parse_args()

    keys = _sample(args.sample, args.seed)
    cfg = load_config()
    conn = meta.init()
    hist: dict[int, int] = {}
    skipped = 0
    t0 = time.perf_counter()
    try:
        for key in keys:
            res = sync_mod.sync_one(conn, cfg, key, "1")
            # ★ **判断依据是「库里有没有数据」，不是「同步状态是什么」。**
            # `sync_one` 对**已经是最新**的票返回 `skipped` —— 那是正常的、
            # 库里有数据的。按状态判会把它们全算成「无数据」，
            # 于是这个测量在第二次跑时**报出 0 只**（实测踩过）。
            bars = store.read(key, "1")
            if bars.empty:
                skipped += 1                      # 停牌/退市：不算「递归没产出」
                why = res.status if res.status != "ok" else "源返回 0 行"
                print(f"  {key:12s} 无数据（{why}）")
                continue
            levels = ChanEngine(key, "1").full(bars).levels
            hist[len(levels)] = hist.get(len(levels), 0) + 1
            desc = " | ".join(
                f"L{lv.depth}/{lv.level} {len(lv.pivots)}/{len(lv.trends)}" for lv in levels
            )
            print(f"  {key:12s} {len(bars):6d}根 → {len(levels)}级  {desc}")
    finally:
        conn.close()

    n = sum(hist.values())
    elapsed = time.perf_counter() - t0
    print(f"\n=== 1 分钟基准：{n} 只（跳过 {skipped} 只无数据），耗时 {elapsed:.0f}s ===")
    print(f"  级别数分布：{dict(sorted(hist.items()))}")
    if not n:
        print("  一只都没成功 —— 先跑 optimizer/tools/probe_pytdx_hosts.py 看服务器还在不在")
        return 1
    one = hist.get(1, 0)
    print(f"  只出 1 级：{one}/{n}（{100 * one / n:.0f}%）")
    print("  对照：**日线**基准下 90.6%（4965/5478，见 measure_recursion.py --invariants）")
    per = elapsed / max(n, 1)
    print(f"  ⇒ 全市场 5471 只按每只 {per:.1f}s 估约 {5471 * per / 3600:.1f} 小时")
    if per < 0.5:
        # ★ 别让这个数被当成事实引用：数据已在库里时 `sync_one` 直接返回 skipped，
        # 这里测到的是**读盘 + 递归**的耗时，不含联网。
        print("     ⚠ 本次多数是**缓存命中**（`sync_one` 返回 skipped），"
              "这个 per-只 只含读盘与递归、**不含联网**；")
        print("        冷跑（首次全市场）实测约 **1.2s/只 ⇒ 约 1.8 小时**。")
    return 0 if one == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
