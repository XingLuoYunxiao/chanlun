"""Task 14 策略层：`Strategy` 协议 + 两个具体策略 + 注册表。

`Strategy` 协议
---------------
`on_bar(ctx, bar, snapshot) -> Sequence[Order]`：**只允许看当前 bar 及以前**。
runner 交给策略的 `snapshot` 已经过 `state.backtestable(..., as_of=bar.ts)` 过滤，
策略拿不到 tentative 结构；`merged`/`fractals` 例外（见下）。

触发判据：「本 bar 第一次可见」（D-34）
--------------------------------------
`Signal.confirmed_at` 是**结构自身的确认完成时刻**，不是「引擎第一次产出它的
时刻」。D-36 之前 `confirmed_at` 系统性早于真实可知时刻，于是实测「100% 早于首次
可见」（`sh.600000` 121/121、`sh.601088` 158/158、`sz.000001` 146/146）—— 那个
「100%」是 D-36 缺陷的观测面，不是独立事实。D-36 修好后两者**会**重合（夹具
`sz.300059` 上 221 个可见信号里已有 1 个 `confirmed_at` 恰等于当日 ts），所以
「拿 `confirmed_at == bar.ts` 当今日新可知」在旧实现下恒 0 笔成交、在新实现下
**能用但不稳**。真正判断「今天刚知道」的仍然只能是**观测过程**：`runner.py` 逐
bar 重算时跟踪已见信号键，只把**本 bar 第一次可见**的信号放进
`snapshot.signals`。于是策略既不漏新信号，也不会在回测起点把陈年历史信号一次性
补仓（首根 bar 只登记、不交易），等价于收盘后看到信号、次日开盘下单。

关于 `fractals`（重要）
-----------------------
`Fractal` 没有 `status` 字段，所以 `backtestable()` 会把它们全部丢掉。
按第 62 课，分型要等**右侧那根合并 K 线走出来**才成立；这个「可知时刻」自
D-36 起由 `Fractal.confirmed_at` 承载（= `merged[midx + 1].ts`），
`confirmed_fractals()` 直接读它，只在手工构造的分型上退回现算。
机械策略用它，缠论策略不用它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from .broker import Order, Position

# 信号类型 → 中文标签（报告展示用；键就是 Order.signal_kind）
KIND_LABELS: dict[str, str] = {
    "b1": "1类买点", "b2": "2类买点", "b3": "3类买点",
    "s1": "1类卖点", "s2": "2类卖点", "s3": "3类卖点",
    "fractal_bottom": "底分型", "fractal_top": "顶分型",
    "manual": "人工/脚本",
}


def kind_label(kind: str | None) -> str:
    if kind is None:
        return "未标注"
    return KIND_LABELS.get(kind, str(kind))


@dataclass(frozen=True)
class Context:
    """策略可见的账户状态（不可变，避免策略顺手改账）。"""

    ts: str
    cash: float
    equity: float
    positions: tuple[Position, ...] = ()

    def position(self, code: str) -> Position | None:
        for p in self.positions:
            if p.code == code:
                return p
        return None

    @property
    def n_positions(self) -> int:
        return len(self.positions)


@runtime_checkable
class Strategy(Protocol):
    """策略协议。返回的委托统一在**下一根 bar 的开盘价**撮合。"""

    def on_bar(self, ctx: Context, bar: Any, snapshot: Any) -> Sequence[Order]:
        ...


def confirmed_fractals(snapshot: Any, as_of: str) -> list[tuple[Any, str]]:
    """返回 `(fractal, 确认时间)`，只含确认时间 <= as_of 的分型。

    确认时间 = 分型中间合并 K 线右侧那根合并 K 线的 ts（第 62 课：右侧
    K 线走完，分型才成立）。合并 K 线下标就是 `MergedBar.idx`。
    """
    merged = list(getattr(snapshot, "merged", ()) or ())
    if not merged:
        return []
    by_idx = {m.idx: i for i, m in enumerate(merged)}
    out: list[tuple[Any, str]] = []
    for f in getattr(snapshot, "fractals", ()) or ():
        confirm_ts = getattr(f, "confirmed_at", None)
        if confirm_ts is None:
            # 手工构造的分型没有可知时刻 → 现算；右侧合并 K 线没出现则未成立
            i = by_idx.get(f.midx)
            if i is None or i + 1 >= len(merged):
                continue
            confirm_ts = merged[i + 1].ts
        if confirm_ts <= as_of:
            out.append((f, confirm_ts))
    return out


# ---------------- 具体策略 ----------------
@dataclass
class ChanSignalStrategy:
    """缠论三类买卖点策略（本任务的主策略）。

    规则刻意保持简单，只为了验证**管线**而不是为了跑出好看的数字：
    - 收到「本 bar 第一次可见」的信号时动作（learn today → trade next open）；
    - 买点：不重复加仓（同一票只持一份）、持仓数不超过 `max_positions`；
    - 卖点：清仓；
    - 单笔用当前现金的 `cash_pct` 比例下单。

    触发判据的契约见 `ARCHITECTURE.md` D-34：`snapshot.signals` 由
    `runner.py` 过滤成**本 bar 第一次可见**的信号，本策略不再自己判断
    「今天刚确认」—— `Signal.confirmed_at` 是结构自身的确认完成时刻，
    实测 100% 早于引擎首次产出它的时刻，拿它当「新可知」恒定不成立。
    """

    buy_kinds: tuple[str, ...] = ("b1", "b2", "b3", "pb")
    sell_kinds: tuple[str, ...] = ("s1", "s2", "s3", "ps")
    max_positions: int = 5
    cash_pct: float = 0.2
    _seen: set = field(default_factory=set, repr=False, compare=False)

    def on_bar(self, ctx: Context, bar: Any, snapshot: Any) -> list[Order]:
        orders: list[Order] = []
        for sig in snapshot.signals:
            kind = sig.kind.value
            held = ctx.position(bar.code) is not None
            if sig.is_buy and kind in self.buy_kinds:
                if held or ctx.n_positions >= self.max_positions:
                    continue
                orders.append(Order(
                    bar.code, "buy", qty=0, signal_kind=kind, cash_pct=self.cash_pct,
                    reason=f"{sig.kind.name_cn}@{sig.ts} {sig.reason}".strip(),
                ))
                break
            if (not sig.is_buy) and kind in self.sell_kinds:
                if not held:
                    continue
                orders.append(Order(
                    bar.code, "sell", qty=0, signal_kind=kind,
                    reason=f"{sig.kind.name_cn}@{sig.ts} {sig.reason}".strip(),
                ))
                break
        return orders


@dataclass
class FractalStrategy:
    """**非缠论策略**，仅用于验证账务/指标管线（不要把它当策略结论）。

    规则：底分型确认 → 次日开盘买入；顶分型确认 → 次日开盘清仓。
    它故意用最简单的几何规则，好在合成/真实数据上稳定产生成交，
    从而让佣金、印花税、T+1、回合盈亏、指标口径都能被真实数字检验。
    """

    cash_pct: float = 1.0

    def on_bar(self, ctx: Context, bar: Any, snapshot: Any) -> list[Order]:
        orders: list[Order] = []
        for frac, confirm_ts in confirmed_fractals(snapshot, bar.ts):
            if confirm_ts != bar.ts:
                continue
            kind = getattr(frac.kind, "value", str(frac.kind))
            held = ctx.position(bar.code) is not None
            if "bottom" in kind and not held:
                orders.append(Order(bar.code, "buy", qty=0, signal_kind="fractal_bottom",
                                    cash_pct=self.cash_pct,
                                    reason=f"底分型确认@{frac.ts}"))
                break
            if "top" in kind and held:
                orders.append(Order(bar.code, "sell", qty=0, signal_kind="fractal_top",
                                    reason=f"顶分型确认@{frac.ts}"))
                break
        return orders


# ---------------- 注册表 ----------------
@dataclass(frozen=True)
class StrategySpec:
    name: str
    label: str
    note: str
    factory: Callable[[], Any]

    def build(self) -> Any:
        return self.factory()


STRATEGIES: dict[str, StrategySpec] = {
    "chan": StrategySpec(
        name="chan",
        label="缠论三类买卖点",
        note="缠论策略：仅在买卖点首次可见的当根 bar 决策，次日开盘成交。",
        factory=ChanSignalStrategy,
    ),
    "fractal": StrategySpec(
        name="fractal",
        label="底/顶分型机械策略",
        note=("非缠论策略、仅用于验证账务/指标管线：底分型确认后次日开盘买入、"
              "顶分型确认后次日开盘清仓。它的收益数字不构成交易建议。"),
        factory=FractalStrategy,
    ),
}


def get_strategy(name: str) -> Any:
    spec = STRATEGIES.get(str(name).lower())
    if spec is None:
        raise KeyError(f"未知策略 {name!r}，可选：{', '.join(sorted(STRATEGIES))}")
    return spec.build()
