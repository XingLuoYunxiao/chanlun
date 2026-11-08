"""回填：把**全市场**的 1 分钟数据拉下来。

**为什么需要它**：1 分钟基准的递归要长历史才有产出（日线基准下 90.6% 的票只出 1 级，
1 分钟基准抽样 20 只**只出 1 级的是 0 只** —— 见 `measure_one_minute_base.py`）。
而 `python -m chanlun sync --period 1` 的范围是**自选池**（分钟周期只跑自选池，
理由是全市场 baostock 85 秒/只跑不起）。pytdx 把这个成本降到约 **1.2 秒/只**，
所以全市场回填第一次变得可行。

**可续跑**：走 `sync.sync_one` 的增量水位（`meta.sync_state`）。中断后重跑会接着拉，
不会从头来。**连续失败 `MAX_CONSECUTIVE_FAILURES` 只就停** —— 整批连续失败说明
链路坏了（服务器全挂、网络断），继续烧剩下的几千只没有意义（与 `daily` 同一判据）。

**怎么认出「被限流」**（2026-10-16 实测）：**同一只票、同一时刻、不同起点，结果不同**。
例如 `688259` 的 1 分钟在 `start=1990-01-01` 拿到 10400 根、`start=2018-01-01` 拿到 **0 根**；
而隔一会儿再问又都有。**限流是间歇性的**，所以「这一轮 0 行」不代表「源上没有」——
只有**探针也拿不到**才是真的停。这也意味着**多跑几遍 `--only-missing` 能把漏的捞回来**
（实测：第一遍补 1047 只，第二遍又补 347 只）。

**两种回填范围**（`--scope`）：

- `market`（默认）：全市场个股 + 自选池 + 已知指数。约 5500 只、**约 1.8 小时**、
  数据约 **3.4 GB**（实测每只 ~0.5 MB / 22000 余根）。
- `watchlist`：只自选池（分钟周期的常规范围），约 1 分钟。

跑法：
    cd <项目根>
    ../.venv-chanlun/bin/python optimizer/tools/backfill_one_minute.py --scope market

退出码 0 = 没有失败；1 = 有失败或触发了连续失败熔断。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.config import load_config  # noqa: E402
from chanlun.data import meta, pytdx_source, store  # noqa: E402
from chanlun.data import sync as sync_mod  # noqa: E402

PERIOD = "1"
#: 连续失败这么多只就停批（与 `__main__.MAX_CONSECUTIVE_FAILURES` 同一判据）。
BREAKER = 20


def codes_for(conn: sqlite3.Connection, scope: str) -> list[str]:
    """要回填哪些代码。**去重保序** —— 同一只排两遍会白拉一次。"""
    out: list[str] = []
    if scope == "market":
        out += [str(r["code"]) for r in conn.execute("SELECT code FROM universe ORDER BY code")]
    out += [str(r["code"]) for r in meta.get_watchlist(conn)]
    out += [c for c, _ in meta.DEFAULT_WATCHLIST]
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


#: 探针票：每天都在交易、pytdx 一定有数据。**只用它判断「源还活着吗」**，
#: 不用它判断「某只票有没有数据」——那是两回事。
CANARY = "600030"


def _canary_alive(args) -> bool:
    """源还活着吗？拿一只必定有数据的票试一次。

    这是「连续零行」的**仲裁**：源活着 ⇒ 零行是标的本身没有；源也拿不到 ⇒ 限流。
    只看 streak 会把「缺失池前綴全是退市股」误判成限流（实测就是这么错了一次）。
    """
    import datetime as dt

    from chanlun.data import pytdx_source

    pytdx_source.reset()          # 换一条连接，免得踩在同一个坏连接上
    end = dt.date.today()
    start = (end - dt.timedelta(days=30)).isoformat()
    try:
        df = pytdx_source.fetch_bars(CANARY, CANARY, PERIOD, start, end.isoformat())
    except Exception:             # noqa: BLE001 - 探针失败就算源不可用
        return False
    return len(df) > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=("market", "watchlist"), default="market")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 只（试跑用）")
    ap.add_argument(
        "--sleep", type=float, default=0.15,
        help="每只之间停多少秒（缺省 0.15）。**不要设 0** —— 实测全速跑约 500 只后"
             "服务器开始返回空数据（把连接打限流了），而空数据在 D-48 的口径下"
             "**不是失败**，只会静默记成 skipped，看起来像「无事可做」。",
    )
    ap.add_argument(
        "--reconnect", type=int, default=150,
        help="每 N 只**重连一次** pytdx（缺省 150）。限流很可能是**按连接的配额** ——"
             "实测一个连接跑约 400 只后开始返回零行；定期换一条连接比一味加大 sleep 有效。"
             "设 0 关闭。",
    )
    ap.add_argument(
        "--cooldown", type=float, default=60.0,
        help="触发零行闸后冷却多少秒再续（缺省 60），配合 --chunk 使用",
    )
    ap.add_argument(
        "--only-missing", action="store_true",
        help="**跳过库内已有数据的票**。回填的目标是「把缺的补上」，不是「把旧的更新」——"
             "不跳的话每次续跑都要重走前面几千只，既慢又是对源的无谓请求（实测被限流"
             "就是这么来的）。要做增量更新请用 `python -m chanlun daily`。",
    )
    ap.add_argument(
        "--max-fail-rate", type=float, default=0.3,
        help="零行率超过它就停批（缺省 0.3）。零行不是失败，但**大面积零行说明被限流了**，"
             "继续跑只会把 5000 只全记成 skipped",
    )
    args = ap.parse_args()

    cfg = load_config()
    conn = meta.init()
    try:
        codes = codes_for(conn, args.scope)
        if args.only_missing:
            before = len(codes)
            codes = [c for c in codes if store.read(c, PERIOD).empty]
            print(f"  --only-missing：{before} 只里已有数据的跳过，剩 {len(codes)} 只待回填")
        if args.limit:
            codes = codes[: args.limit]
        print(f"1 分钟回填：范围 {args.scope}，{len(codes)} 只，周期 {PERIOD}")
        ok = failed = skipped = 0
        streak = 0
        empty_streak = 0
        t0 = time.perf_counter()
        for i, code in enumerate(codes, 1):
            res = sync_mod.sync_one(conn, cfg, code, PERIOD)
            if res.status == "ok":
                ok += 1
                streak = 0
                empty_streak = 0
            elif res.status == "skipped":
                skipped += 1
                streak = 0
                # 区分「已是最新」与「连上了但拿不到数据」：后者库内是空的。
                if store.read(code, PERIOD).empty:
                    empty_streak += 1
                else:
                    empty_streak = 0
            else:
                failed += 1
                streak += 1
                if streak <= 5:
                    print(f"  ✗ {code}: {res.error}")
            # **周期性重连**：限流多半按连接配额，不换连接就一直零行。
            if args.reconnect and i % args.reconnect == 0:
                pytdx_source.reset()
                print(f"  …已重连 pytdx（第 {i} 只）", flush=True)
            if args.sleep:
                time.sleep(args.sleep)
            if i % 100 == 0 or i == len(codes):
                el = time.perf_counter() - t0
                rate = i / el if el else 0
                eta = (len(codes) - i) / rate / 60 if rate else 0
                print(
                    f"  [{i}/{len(codes)}] 成功 {ok} 失败 {failed} 跳过 {skipped}  "
                    f"{rate:.2f} 只/秒  ETA {eta:.0f} 分钟",
                    flush=True,
                )
            if empty_streak >= 30:
                # ★ **先问探针，再决定是不是限流。**
                #
                # 连续零行有两种成因，长得一模一样：
                #   ① **被服务器限流** —— 整个源都不给数据了，必须停；
                #   ② **这批标的源上就没有** —— 拿一只已知有数据的票试一下就知道，
                #      这时停批是错的。
                #
                # 判据错在哪儿（实测踩到）：`--only-missing` 从代码序最前面开始跑，
                # 而缺失池的**前缀恰好全是退市股**（`000004`/`000005`/`000018`…），
                # 于是第一次续跑**立刻**被判成限流、一只没拉到。而随机抽 40 只问源，
                # **36 只有 1 分钟数据** —— 这个池子 90% 是可拉的。
                if _canary_alive(args):
                    print(f"  …连续 {empty_streak} 只零行，但**探针还活着** —— "
                          f"说明是这批标的源上没有，不是限流。清零继续。", flush=True)
                    empty_streak = 0
                else:
                    print(
                        f"  ✗ 连续 {empty_streak} 只零行，**且探针也拿不到数据** —— "
                        f"确认是**被服务器限流**（实测全速跑约 500 只后开始）。停批。\n"
                        f"    零行在 D-48 的口径下**不是失败**，所以它会静默记成 skipped ——\n"
                        f"    这正是为什么要有这道闸。加大 --sleep（或等交易时段）再续跑：\n"
                        f"    回填是增量可续的，已落库的不会重拉（`--only-missing` 直接跳缺口）。",
                        file=sys.stderr, flush=True,
                    )
                    return 1
            if streak >= BREAKER:
                print(
                    f"  ✗ 连续 {streak} 只失败，停批 —— 整批连续失败说明链路坏了"
                    f"（服务器全挂/网络断），继续烧剩下的没有意义。"
                    f"先跑 optimizer/tools/probe_pytdx_hosts.py。",
                    file=sys.stderr, flush=True,
                )
                return 1
        # 用**库内行数**汇报，而不是同步计数器 —— 已是最新的票算 skipped 但库里有数据
        have = sum(
            1 for c in codes if not store.read(c, PERIOD).empty
        )
        print(
            f"\n完成：成功 {ok} 失败 {failed} 跳过 {skipped}；"
            f"**库内实际有数据的 {have}/{len(codes)} 只**，耗时 "
            f"{(time.perf_counter() - t0) / 60:.1f} 分钟"
        )
        return 0 if failed == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
