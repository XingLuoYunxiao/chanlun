"""Task 14 账务层：Broker。

为什么规则要定成这样（A 股现实约束，不是拍脑袋）
--------------------------------------------------
1. **T+1：当日买入不可当日卖出**。这不是「保守」，是制度。回测里如果允许当日
   回转，等于凭空多出一个策略（日内高抛低吸），收益会被系统性高估。因此
   `Position` 同时记录 `qty`（总持仓）与 `available`（可卖数量），
   `begin_bar(ts)` 在**交易日变化时**才把 `qty` 结转为 `available`；
   同一 ts 内重复调用不再结算，于是「当日买、当日卖」必然被拒。
2. **次日开盘成交**。策略在收盘后（第 t 根 bar）才拿到完整 K 线并做出决定，
   现实中最早只能在第 t+1 根 bar 的开盘价成交。用收盘价成交等于用「当时还
   不知道的收盘价」下单，是最典型的未来函数。
3. **涨停不可买 / 跌停不可卖**。封板时买不到、也卖不掉。判据用「执行价触及
   涨跌停价」：涨跌停价 = 前收 ×(1±比例) 四舍五入到分。比例按板块取
   （主板 10%、创业板/科创板 20%、北交所 30%）；**ST 的 5% 未实现**，因为
   存储里没有 ST 标记，这一点在报告里如实说明，不做静默猜测。
4. **佣金万 2.5 双边、最低 5 元；印花税千 1 仅卖出**。券商实际收费口径。
   最低 5 元对小额买单影响极大（1000 元买入 → 5 元 = 0.5%），必须实现，
   否则小资金策略的收益会被高估。
5. **默认不加滑点、不做价格取整**。计划没要求，且滑点大小没有数据支撑，
   凭感觉加一个「0.1% 滑点」会让所有回测结果无法复核。若调用方确实要，
   通过 `slippage` 显式传入，并记在 `result.config` 里。买入数量按 100 股
   整数倍取整（A 股一手）是交易所规则，不属于「偷偷加的假设」。
6. **卖出所得资金当日即可用于买入**（A 股资金 T+0），只有股份受 T+1 约束。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

# ---- 费用与规则常量（集中在一处，便于审计与将来参数化）----
COMMISSION_RATE = 2.5e-4   # 佣金万 2.5
COMMISSION_MIN = 5.0       # 佣金最低 5 元
STAMP_TAX_RATE = 1e-3      # 印花税千 1，仅卖出
LOT_SIZE = 100             # A 股一手 = 100 股

SIDE_BUY = "buy"
SIDE_SELL = "sell"


@dataclass(frozen=True)
class Order:
    """策略产生的委托。`qty=0` 表示「由 Broker 决定数量」。

    - 买入 `qty=0`：用可用现金的 `cash_pct` 比例满仓买（按一手取整）；
      现金不够一手则拒单（`Rejection`），绝不出现负现金。
    - 卖出 `qty=0`：卖出全部**可卖**数量（受 T+1 约束）。
    """

    code: str
    side: str
    qty: int = 0
    signal_kind: str | None = None
    reason: str = ""
    cash_pct: float = 1.0
    created_ts: str | None = None   # 由 Broker.submit 填写，用于审计「哪根 bar 下的单」


@dataclass(frozen=True)
class Bar:
    """回测循环里的一根 bar（策略可见的全部行情）。"""

    code: str
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    index: int          # 该票在回测区间内的时间轴下标（0 起）
    prev_close: float | None = None


@dataclass(frozen=True)
class Fill:
    """一笔成交。"""

    ts: str
    code: str
    side: str
    price: float
    qty: int
    amount: float          # price * qty
    commission: float
    stamp_tax: float
    fee_total: float
    cash_after: float
    signal_kind: str | None = None
    reason: str = ""
    created_ts: str | None = None


@dataclass(frozen=True)
class Rejection:
    """被拒委托。必须留痕：静默丢弃会让「策略没成交」变成不可解释的空白。"""

    ts: str
    code: str
    side: str
    qty: int
    price: float
    reason: str
    signal_kind: str | None = None


@dataclass(frozen=True)
class Position:
    """持仓。`available` 是今天可以卖出的数量（T+1 结算后 = qty）。"""

    code: str
    qty: int
    available: int
    avg_cost: float        # 移动加权平均成本（含买入费用）

    @property
    def cost_amount(self) -> float:
        return self.avg_cost * self.qty


@dataclass(frozen=True)
class RoundTrip:
    """一次已平仓回合（FIFO 撮合，卖出可能一次拆成多笔回合）。"""

    code: str
    entry_ts: str
    exit_ts: str
    qty: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    fees: float            # 该回合分摊到的买入 + 卖出全部费用
    pnl: float             # gross_pnl - fees
    pnl_pct: float         # pnl / (entry_price*qty + 买入费用分摊)
    holding_bars: int
    signal_kind: str | None
    exit_signal_kind: str | None = None
    reason_entry: str = ""
    reason_exit: str = ""


@dataclass(frozen=True)
class EquityPoint:
    """权益快照（按全局时间轴去重后每分钟/每天一个点）。"""

    ts: str
    cash: float
    market_value: float
    equity: float


@dataclass
class _Lot:
    """FIFO 批次：记录买入价与买入费用，供已实现盈亏精确分摊。"""

    qty: int
    price: float
    fee: float
    ts: str
    signal_kind: str | None
    reason: str


def limit_pct(code: str) -> float:
    """涨跌停比例。创业板/科创板 20%，北交所 30%，其余主板 10%。"""
    bare = str(code).split(".")[-1]
    if bare.startswith(("300", "301", "688", "689")):
        return 0.20
    if bare.startswith(("4", "8")):
        return 0.30
    return 0.10


def limit_prices(prev_close: float, code: str) -> tuple[float, float]:
    """(涨停价, 跌停价)：前收 ×(1±比例)，四舍五入到分。"""
    pct = limit_pct(code)
    return round(prev_close * (1 + pct) + 1e-9, 2), round(prev_close * (1 - pct) + 1e-9, 2)


class Broker:
    """单一资金账户的撮合与账务。不含任何行情循环逻辑（那是 runner 的事）。"""

    def __init__(
        self,
        initial_cash: float = 100_000.0,
        *,
        commission_rate: float = COMMISSION_RATE,
        commission_min: float = COMMISSION_MIN,
        stamp_tax_rate: float = STAMP_TAX_RATE,
        lot_size: int = LOT_SIZE,
        slippage: float = 0.0,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash 必须为正")
        if slippage != 0.0:
            raise ValueError(
                "默认不允许滑点：本项目没有支撑滑点取值的证据。"
                "确实需要时请显式改这里的校验并在报告里说明理由"
            )
        self.initial_cash = float(initial_cash)
        self.commission_rate = float(commission_rate)
        self.commission_min = float(commission_min)
        self.stamp_tax_rate = float(stamp_tax_rate)
        self.lot_size = int(lot_size)
        self.slippage = float(slippage)

        self._cash = float(initial_cash)
        self._positions: dict[str, Position] = {}
        self._lots: dict[str, deque[_Lot]] = {}
        self._pending: list[Order] = []
        self._fills: list[Fill] = []
        self._rejections: list[Rejection] = []
        self._round_trips: list[RoundTrip] = []
        self._bar_ts: str | None = None
        self._timeline: dict[str, dict[str, int]] = {}

    # ---- 只读状态 ----
    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> Mapping[str, Position]:
        return dict(self._positions)

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    @property
    def rejections(self) -> tuple[Rejection, ...]:
        return tuple(self._rejections)

    @property
    def round_trips(self) -> tuple[RoundTrip, ...]:
        return tuple(self._round_trips)

    @property
    def pending(self) -> tuple[Order, ...]:
        return tuple(self._pending)

    def position(self, code: str) -> Position | None:
        return self._positions.get(code)

    def market_value(self, prices: Mapping[str, float]) -> float:
        return sum(
            p.qty * float(prices.get(code, p.avg_cost))
            for code, p in self._positions.items()
        )

    def equity(self, prices: Mapping[str, float]) -> float:
        return self._cash + self.market_value(prices)

    def register_timeline(self, code: str, ts_list: Sequence[str]) -> None:
        """登记该票的时间轴，用于把成交时间换算成「持仓 bar 数」。"""
        self._timeline[code] = {str(t): i for i, t in enumerate(ts_list)}

    # ---- 委托与撮合 ----
    def submit(self, order: Order, ts: str | None = None) -> Order:
        """入队，等待**该票下一根 bar 的开盘价**成交。"""
        side = str(order.side).lower()
        if side not in (SIDE_BUY, SIDE_SELL):
            raise ValueError(f"未知买卖方向: {order.side}")
        if not order.code:
            raise ValueError("委托缺少 code")
        queued = replace(order, side=side, created_ts=ts or order.created_ts)
        self._pending.append(queued)
        return queued

    def begin_bar(self, ts: str) -> None:
        """进入一根新 bar：若交易日变化，则把持仓结转为可卖（T+1）。"""
        if self._bar_ts != ts:
            self._bar_ts = ts
            for code, pos in list(self._positions.items()):
                if pos.available != pos.qty:
                    self._positions[code] = replace(pos, available=pos.qty)

    def execute_open(self, bar: Bar, prev_close: float | None = None) -> list[Fill | Rejection]:
        """以 `bar.open` 撮合该票全部的挂单，返回成交/拒单结果。

        入口处会先按 `bar.ts` 做一次 `begin_bar`：撮合发生在哪根 bar，就以哪根
        bar 的交易日结算 T+1。这样即使调用方忘了显式 `begin_bar`，也**不可能**
        出现「当日买当日卖」漏网；同一 `ts` 内不会重复结算，规则不变。
        """
        self.begin_bar(bar.ts)
        pc = bar.prev_close if prev_close is None else prev_close
        outcomes: list[Fill | Rejection] = []
        rest: list[Order] = []
        for order in self._pending:
            if order.code != bar.code:
                rest.append(order)
                continue
            outcomes.append(self._execute_one(order, bar, pc))
        self._pending = rest
        return outcomes

    # ---- 内部 ----
    def _commission(self, amount: float) -> float:
        return max(self.commission_min, amount * self.commission_rate)

    def _execute_one(self, order: Order, bar: Bar, prev_close: float | None) -> Fill | Rejection:
        if order.side == SIDE_BUY:
            return self._buy(order, bar, prev_close)
        return self._sell(order, bar, prev_close)

    def _buy(self, order: Order, bar: Bar, prev_close: float | None) -> Fill | Rejection:
        price = bar.open + self.slippage
        if price <= 0:
            return self._reject(order, bar, price, "开盘价非正，数据异常")
        if prev_close is not None:
            up, _ = limit_prices(prev_close, bar.code)
            if price >= up - 1e-9:
                return self._reject(order, bar, price,
                                    f"涨停不可买（前收 {prev_close:.2f} → 涨停 {up:.2f}）")

        budget = self._cash * max(0.0, min(1.0, order.cash_pct))
        qty = order.qty if order.qty > 0 else self._max_qty(price, budget)
        qty = qty // self.lot_size * self.lot_size
        if qty <= 0:
            return self._reject(order, bar, price,
                                f"现金不足一手（可用 {budget:.2f}，一手约 {price * self.lot_size:.2f}）")
        # 兜底：费用可能让「刚好满仓」越界，逐手回退（最多 2 次）
        while qty > 0 and qty * price + self._commission(qty * price) > self._cash:
            qty -= self.lot_size
        if qty <= 0:
            return self._reject(order, bar, price, f"现金不足（可用 {self._cash:.2f}）")

        amount = qty * price
        fee = self._commission(amount)
        self._cash -= amount + fee
        self._lots.setdefault(bar.code, deque()).append(
            _Lot(qty=qty, price=price, fee=fee, ts=bar.ts,
                 signal_kind=order.signal_kind, reason=order.reason)
        )
        self._positions[bar.code] = self._merge_position(bar.code, qty, amount + fee)
        fill = Fill(ts=bar.ts, code=bar.code, side=SIDE_BUY, price=price, qty=qty,
                    amount=amount, commission=fee, stamp_tax=0.0, fee_total=fee,
                    cash_after=self._cash, signal_kind=order.signal_kind,
                    reason=order.reason, created_ts=order.created_ts)
        self._fills.append(fill)
        return fill

    def _sell(self, order: Order, bar: Bar, prev_close: float | None) -> Fill | Rejection:
        price = bar.open - self.slippage
        pos = self._positions.get(bar.code)
        available = pos.available if pos else 0
        if available <= 0:
            reason = ("T+1：当日买入不可当日卖出（可卖 0 股）"
                      if pos is not None else "无可卖持仓")
            return self._reject(order, bar, price, reason)
        if prev_close is not None:
            _, down = limit_prices(prev_close, bar.code)
            if price <= down + 1e-9:
                return self._reject(order, bar, price,
                                    f"跌停不可卖（前收 {prev_close:.2f} → 跌停 {down:.2f}）")

        qty = available if order.qty <= 0 else min(order.qty, available)
        if qty <= 0:
            return self._reject(order, bar, price, "可卖数量为 0")

        amount = qty * price
        fee = self._commission(amount)
        tax = amount * self.stamp_tax_rate
        self._cash += amount - fee - tax
        self._consume_lots(bar, price, qty, fee + tax, order)
        remaining = pos.qty - qty
        if remaining <= 0:
            self._positions.pop(bar.code, None)
            self._lots.pop(bar.code, None)
        else:
            self._positions[bar.code] = replace(
                pos, qty=remaining, available=pos.available - qty
            )
        fill = Fill(ts=bar.ts, code=bar.code, side=SIDE_SELL, price=price, qty=qty,
                    amount=amount, commission=fee, stamp_tax=tax, fee_total=fee + tax,
                    cash_after=self._cash, signal_kind=order.signal_kind,
                    reason=order.reason, created_ts=order.created_ts)
        self._fills.append(fill)
        return fill

    def _reject(self, order: Order, bar: Bar, price: float, reason: str) -> Rejection:
        rej = Rejection(ts=bar.ts, code=bar.code, side=order.side, qty=order.qty,
                        price=price, reason=reason, signal_kind=order.signal_kind)
        self._rejections.append(rej)
        return rej

    def _max_qty(self, price: float, budget: float) -> int:
        """预算内最多能买多少股（按实际费用估算，取整到一手）。"""
        qty = int(budget // (price * self.lot_size)) * self.lot_size
        while qty > 0 and qty * price + self._commission(qty * price) > budget:
            qty -= self.lot_size
        return qty

    def _merge_position(self, code: str, qty: int, cost: float) -> Position:
        old = self._positions.get(code)
        if old is None:
            return Position(code=code, qty=qty, available=0, avg_cost=cost / qty)
        total_qty = old.qty + qty
        total_cost = old.avg_cost * old.qty + cost
        return Position(code=code, qty=total_qty, available=old.available,
                        avg_cost=total_cost / total_qty)

    def _consume_lots(self, bar: Bar, price: float, qty: int, exit_fee: float,
                      order: Order) -> None:
        """FIFO 结转已实现盈亏，生成 `RoundTrip`。"""
        lots = self._lots.setdefault(bar.code, deque())
        remaining = qty
        timeline = self._timeline.get(bar.code, {})
        while remaining > 0 and lots:
            lot = lots[0]
            take = min(lot.qty, remaining)
            entry_fee = lot.fee * take / lot.qty
            fee_share = exit_fee * take / qty
            gross = (price - lot.price) * take
            pnl = gross - entry_fee - fee_share
            basis = lot.price * take + entry_fee
            entry_idx = timeline.get(lot.ts)
            exit_idx = timeline.get(bar.ts)
            holding = 1 if entry_idx is None or exit_idx is None else max(1, exit_idx - entry_idx)
            self._round_trips.append(RoundTrip(
                code=bar.code, entry_ts=lot.ts, exit_ts=bar.ts, qty=take,
                entry_price=lot.price, exit_price=price, gross_pnl=gross,
                fees=entry_fee + fee_share, pnl=pnl,
                pnl_pct=(pnl / basis) if basis else 0.0,
                holding_bars=holding, signal_kind=lot.signal_kind,
                exit_signal_kind=order.signal_kind, reason_entry=lot.reason,
                reason_exit=order.reason,
            ))
            remaining -= take
            if take >= lot.qty:
                lots.popleft()
            else:
                lots[0] = replace(lot, qty=lot.qty - take, fee=lot.fee - entry_fee)
