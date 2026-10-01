"""除权因子入库：从「通达信原始价 + 库内前复权价」反推因子阶梯，写进 `adjust_factor`。

为什么需要这一步：三态复权里只有**一个**口径是库里的原始存储，另外两个都要靠因子表换算
（`raw × k_t` / `raw × k_t ÷ k_0`，见 `adjust.apply_adjust` / `adjust.unapply_adjust`）。
因子表空着的时候，页面只能如实说「还原不了」。

为什么用反推而不是直接问数据源：baostock 的 `query_adjust_factor` 是权威来源，但它是逐只
网络请求（约 1 秒/只，全市场要一个多小时），而且必须在 baostock 会话空闲时跑。反推用的是
已经躺在本地磁盘上的两份数据（通达信整包 + 库内前复权），全市场几分钟跑完，可以随时重算。

**两条硬约束**：

1. **必须在 `day` 切到通达信整包（不复权）之前跑**。切库会覆盖掉库内那份前复权数据，
   切完之后就再也反推不出来了 —— 所以这里会先查 `sync_state.adjust`，发现落库口径已经不是
   前复权就直接拒绝，而不是拿不复权价跟不复权价相除、得到一片 `k=1` 把好因子覆盖掉。
2. **阶梯只在它被反推出来的区间内有效**。`factors.ts[0]` 之前没有观测，`adjust._k_series`
   会让更早的 K 线沿用 `k[0]`，那是外推；页面会把这个区间如实标出来（`web/api._apply_factors`）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from . import adjust, meta, store, tdx

log = logging.getLogger(__name__)

TDX_MARKETS = ("sh", "sz", "bj")
MIN_OVERLAP = 20
"""重叠少于此数就不反推：样本太少时一个停牌价、一次配股都能算出假的除权日。"""


def tdx_day_path(root: str | Path, code: str) -> Path | None:
    """把库里的 key 映射回通达信 `.day` 文件。

    只按文件名拼路径是不够的：`600030` 与 `000001` 都是六位数字，落在哪个市场由号段决定。
    所以每个候选都用 `tdx.store_key` 反查一遍，确认它确实是这个 key 的来源 —— 否则
    「sz 目录下有个同名文件」就会让我们拿另一个市场的原始价去反推因子。
    """
    digits = str(code).split(".")[-1]
    for market in TDX_MARKETS:
        if tdx.store_key(market, digits) != code:
            continue
        path = Path(root) / market / "lday" / f"{market}{digits}.day"
        if path.exists():
            return path
    return None


@dataclass(frozen=True)
class FactorResult:
    code: str
    status: str
    segments: int = 0
    overlap: int = 0
    first_ts: str | None = None
    note: str = ""


def _stored_adjust(conn, code: str, period: str) -> str:
    row = meta.get_sync(conn, code, period)
    if row is None or not row["adjust"]:
        return "raw"
    try:
        return adjust.normalize_adjust(row["adjust"])
    except ValueError:
        return "raw"


def build_one(
    conn,
    code: str,
    *,
    tdx_root: str | Path,
    period: str = "day",
    dry_run: bool = False,
    min_overlap: int = MIN_OVERLAP,
) -> FactorResult:
    """反推一只票的因子阶梯。任何「数据不够」的情况都回一个明确的 status，不抛异常。

    单只失败不该中断全市场批处理：5471 只里总会有停牌退市、文件损坏的，
    逐只记状态比整个任务崩掉有用得多。
    """
    path = tdx_day_path(tdx_root, code)
    if path is None:
        return FactorResult(code, "no_raw", note=f"通达信目录里没有 {code} 的日线文件")
    raw = tdx.parse_file(path)
    qfq = store.read(code, period)
    if len(qfq) == 0:
        return FactorResult(code, "no_qfq", note=f"库里没有 {code} 的 {period} 行情")
    # 口径检查放在「有没有数据」之后：库里什么都没有时，「没有行情」比「口径不对」更接近事实。
    stored = _stored_adjust(conn, code, period)
    if stored != "qfq":
        return FactorResult(
            code, "stored_not_qfq",
            note=f"库内 {period} 口径是「{stored}」，不是前复权：没有前复权参照就反推不出因子"
                 "（切到不复权之后这一步只能改用 baostock query_adjust_factor）",
        )
    overlap = len(raw[["ts"]].merge(qfq[["ts"]], on="ts"))
    if overlap < min_overlap:
        return FactorResult(
            code, "thin_overlap", overlap=overlap,
            note=f"两份数据只重叠 {overlap} 根（少于 {min_overlap}），反推结果不可信",
        )
    ladder = adjust.infer_factors(raw, qfq)
    if ladder.empty:
        return FactorResult(code, "no_change", overlap=overlap, note="两份数据没有共同交易日")
    # 整段 k 恒为 1 = 这段区间根本没除权。写下去虽然等价于不变换，但会让页面
    # 言之凿凿地说「按 1 段除权因子缩放」，不如明说没有。
    if len(ladder) == 1 and abs(float(ladder["k"].iloc[0]) - 1.0) < 1e-9:
        return FactorResult(
            code, "no_change", overlap=overlap,
            note=f"两份数据在重叠区间内逐笔同价（{overlap} 根），这段没有除权",
        )
    rows = list(zip(ladder["ts"], ladder["k"]))
    if not dry_run:
        meta.save_adjust_factors(conn, code, rows, source="inferred")
    return FactorResult(
        code, "dry_run" if dry_run else "written",
        segments=len(rows), overlap=overlap, first_ts=str(ladder["ts"].iloc[0]),
        note=f"{len(rows)} 段除权因子，首段 {ladder['ts'].iloc[0]}",
    )


def build_many(
    conn,
    codes: Iterable[str],
    *,
    tdx_root: str | Path,
    period: str = "day",
    dry_run: bool = False,
    min_overlap: int = MIN_OVERLAP,
    progress_every: int = 500,
) -> list[FactorResult]:
    """逐只反推。返回全部结果（含各类跳过原因），由调用方决定怎么汇报。"""
    out: list[FactorResult] = []
    for i, code in enumerate(codes, 1):
        try:
            res = build_one(conn, code, tdx_root=tdx_root, period=period,
                            dry_run=dry_run, min_overlap=min_overlap)
        except Exception as exc:  # noqa: BLE001 —— 单只出错不能拖垮全市场
            log.warning("因子反推失败 %s: %s", code, exc)
            res = FactorResult(code, "error", note=str(exc))
        out.append(res)
        if progress_every and i % progress_every == 0:
            log.info("因子反推进度 %d/%d", i, len(out))
    return out


def summarize(results: Iterable[FactorResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for res in results:
        counts[res.status] = counts.get(res.status, 0) + 1
    return dict(sorted(counts.items()))
