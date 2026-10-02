"""Task 14 回测骨架：point-in-time 逐 bar 循环。

无未来函数是怎么被**物理保证**的
--------------------------------
不是靠「策略自觉」。每一根 bar 的处理都重新调用
`store.read(code, period, end=当前bar的ts)`，让存储层把 `ts > end` 的行**物理
截断掉**，再把这个截断帧交给引擎 `full()`。于是引擎、结构、信号、策略全都
看不到未来一行数据；即便引擎或策略有 bug，也变不出未来信息。
`tests/backtest/test_runner.py::test_runner_never_reads_future_bars` 用
monkeypatch 探针把这条不变量钉死：每次读取的 `end` 必须等于当时正在模拟的
bar 的 ts，且返回帧的最大 ts 不得越过 `end`。

为什么逐 bar 截断读要用 `start=None`
------------------------------------
回测第一根 bar 也需要**左侧历史**才能算出线段/中枢（否则区间起点附近的结构
全是残的，信号会凭空多出来）。所以截断读不传 `start`，让它把该票全部历史读到
`end` 为止。行情长度有限（日线 6000 行量级），逐 bar 重算的成本可接受。

另外一次「建时间轴」的读取带 `start/end`，它**只用来确定交易日序列**（哪几天
有 bar、顺序如何），绝不喂给引擎或策略。探针测试正是靠「截断读不带 start」
把这两类读取区分开的。

成交时点
--------
第 t 根 bar 收盘后策略决策 → 委托排进队列 → 第 t+1 根 bar 的**开盘价**成交
（`Broker.execute_open`）。因此 T+1 与「次日开盘成交」是同一条流水线的自然
结果，而不是额外补的规则。

触发判据：本 bar「首次可见」（D-34）
-----------------------------------
物理截断保证了「看不到未来」，但**不**保证「今天才第一次看到」：逐 bar 重算
全量时，历史上早就成立的结构每根 bar 都会再次出现。而 `Signal.confirmed_at`
是结构自身的确认完成时刻、不是首次可见时刻（实测 100% 早于首次可见），
拿它当「今日新可知」永不成立。所以主循环自己维护观测过程：用
`signal_key(code, sig)` 给每个信号记身份，`seen` 存已见过的键，`primed` 标记
首根 bar（首根只登记、不交易，否则开局会把全部历史信号一次性买满），
只把**本 bar 第一次出现**的信号交给 `_view(..., signals=fresh)` → 策略。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Mapping, Sequence

from ..chan.divergence import find_divergences
from ..chan.engine import ChanEngine
from ..chan.signal import SignalMode, find_signals
from ..chan.state import backtestable
from ..data import store
from .broker import Bar, Broker, EquityPoint, Fill, Order, Position, Rejection, RoundTrip
from .metrics import metrics
from .strategy import Context

log = logging.getLogger(__name__)

#: 默认基准：沪深300。存储里没有它的数据时优雅降级为 `benchmark=None`。
DEFAULT_BENCHMARK = "000300"


@dataclass(frozen=True)
class BacktestResult:
    """回测产物：原始数据 + 指标。原始数据一律保留，指标只是它的一个视图。"""

    codes: tuple[str, ...]
    period: str
    start: str | None
    end: str | None
    initial_cash: float
    strategy: str
    trades: tuple[Fill, ...] = ()
    round_trips: tuple[RoundTrip, ...] = ()
    equity: tuple[EquityPoint, ...] = ()
    positions: tuple[Position, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    pending: tuple[Order, ...] = ()          # 回测结束时仍未成交的委托（留痕）
    benchmark: Mapping[str, Any] | None = None
    notes: tuple[str, ...] = ()
    data_span: tuple[str, str] | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)


def _bare(code: str) -> str:
    """`sh.600000` → `600000`。存储文件名不含市场前缀，市场由目录表达。"""
    c = str(code).strip()
    return c.split(".", 1)[1] if "." in c else c


def signal_key(code: str, sig: Any) -> tuple[str, str, str, float]:
    """信号身份：代码 + 类型 + 时间 + 价格。

    不用列表下标 —— `_view` 每根 bar 都重算，`Signal.idx` 会随新信号插入而变，
    拿它做「见过没有」的键会把老信号误判成新信号。
    """
    return (code, str(sig.kind.value), str(sig.ts), round(float(sig.price), 4))


def _view(snapshot: Any, as_of: str, signals: Sequence[Any] | None = None) -> Any:
    """把全量快照裁成「站在 `as_of` 这一天才知道的样子」。

    `signals=None` 表示取该时刻**全部可见**信号（旧行为，供不关心「首次可见」的
    调用方使用）；回测主循环显式传入**本 bar 第一次可见**的那几个（D-34）。

    只有 `status is CONFIRMED and confirmed_at <= as_of` 的结构能进来，
    tentative/invalidated 一律消失。`merged`/`fractals` 原样保留：它们是纯几何
    量、没有确认时间字段，分型的「何时成立」由 `strategy.confirmed_fractals()`
    按第 62 课（右侧合并 K 线走完）另行判定。
    """
    return replace(
        snapshot,
        strokes=tuple(backtestable(snapshot.strokes, as_of)),
        segments=tuple(backtestable(snapshot.segments, as_of)),
        pivots=tuple(backtestable(snapshot.pivots, as_of)),
        signals=tuple(backtestable(snapshot.signals, as_of) if signals is None
                      else signals),
    )


def _read_benchmark(code: str, period: str, start: str | None, end: str | None):
    """返回 `(实际命中的代码, 帧)`；都没有数据则 `(None, None)`。

    这里会依次试 `000300` / `sh.000300` / `sz.000300`：指数在不同数据源里市场
    归属不一致，多试两个前缀是廉价的容错。
    """
    for cand in (code, f"sh.{code}", f"sz.{code}"):
        df = store.read(cand, period, start=start, end=end)
        if len(df) > 0:
            return cand, df
    return None, None


def run(
    strategy: Any,
    codes: Sequence[str],
    start: str | None = None,
    end: str | None = None,
    period: str = "day",
    mode: str = "strict",
    initial_cash: float = 100_000.0,
    *,
    benchmark_code: str | None = DEFAULT_BENCHMARK,
    strategy_name: str | None = None,
    engine_factory: Any = None,
) -> BacktestResult:
    """跑一次 point-in-time 回测。

    参数
    ----
    strategy: 实现 `Strategy.on_bar(ctx, bar, snapshot) -> Sequence[Order]` 的对象。
    codes: 股票代码列表（`600000` 或 `sh.600000` 都接受，多票共用一份现金）。
    start/end: 回测区间（含端点）。为 `None` 时表示不限。
    period: 行情周期，默认 `day`。
    mode: 买卖点口径，`"strict"`（默认）或 `"loose"`，只影响买卖点与背驰标注，
        不改笔/线段/中枢划分（D-32）。缺省值**不是** `None` ——
        `SignalMode(None)` 会抛 `ValueError`。
    initial_cash: 初始资金，默认 10 万。
    benchmark_code: 基准代码，默认沪深300（`000300`）；无数据则 benchmark=None。

    口径在入口处**归一化成 `SignalMode` 成员**再注入 `find_signals`：
    `SignalMode` 是 `str` 枚举，`SignalMode.LOOSE == "loose"` 为 True 而
    `SignalMode.LOOSE is "loose"` 为 False，`signal.py` 内部用的是 `is`。
    字符串直通会让 `mode="loose"` **静默等于严格模式**，所以这里必须转换；
    非法值由 `SignalMode(mode)` 抛 `ValueError`，不静默降级。
    """
    name = strategy_name or type(strategy).__name__
    notes: list[str] = []
    bare_codes = [_bare(c) for c in codes]
    m = SignalMode(mode)  # 入口归一化：绝不让字符串透传到 signal.py 的 `is` 比较
    make_engine = engine_factory or (
        lambda code: ChanEngine(
            code, period,
            signal_fn=partial(find_signals, mode=m),
            divergence_fn=find_divergences,
            level=period,
        )
    )

    # 1) 时间轴：只用 start/end 读取来确认「哪几天有 bar」，不进引擎、不进策略。
    timelines: dict[str, list[str]] = {}
    for code in bare_codes:
        df = store.read(code, period, start=start, end=end)
        if len(df) == 0:
            notes.append(f"{code}: 无 {period} 周期数据，已跳过")
            continue
        timelines[code] = [str(t) for t in df["ts"]]

    if not timelines:
        notes.append("没有任何可用行情，回测未执行（请先 sync 数据）")
        return _finish(_skeleton(bare_codes, period, start, end, initial_cash, name),
                       trades=(), round_trips=(), equity=(), positions=(),
                       rejections=(), pending=(), benchmark=None, notes=notes,
                       data_span=None)

    # 2) 逐 bar 推进
    broker = Broker(initial_cash)
    for code, ts_list in timelines.items():
        broker.register_timeline(code, ts_list)
    pos_of = {code: {ts: i for i, ts in enumerate(ts_list)}
              for code, ts_list in timelines.items()}
    engines = {code: make_engine(code) for code in timelines}
    last_close: dict[str, float] = {}
    #: 每只票已经「见过」的信号键。回测是逐 bar 重算全量，同一根 bar 上
    #: 历史上早就成立的信号会反复出现，只有第一次见到的才算「今天新知道」。
    seen: dict[str, set[tuple]] = {}
    primed: set[str] = set()

    all_ts = sorted({ts for ts_list in timelines.values() for ts in ts_list})
    equity_points: list[EquityPoint] = []

    for ts in all_ts:
        broker.begin_bar(ts)  # T+1：交易日变化时才把持仓结转为可卖
        for code, ts_list in timelines.items():
            i = pos_of[code].get(ts)
            if i is None:
                continue
            frame = store.read(code, period, end=ts)  # ★ 物理截断：只看得到 <= ts
            if len(frame) == 0:
                continue
            row = frame.iloc[-1]
            prev_close = float(frame.iloc[-2]["close"]) if len(frame) >= 2 else None
            bar = Bar(
                code=code, ts=str(row["ts"]), open=float(row["open"]),
                high=float(row["high"]), low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]), amount=float(row["amount"]),
                index=i, prev_close=prev_close,
            )
            # 上一根 bar 挂的委托，用本 bar 开盘价撮合
            broker.execute_open(bar)
            last_close[code] = bar.close  # 先更新现价，权益按本 bar 收盘 mark-to-market
            full_snap = engines[code].full(frame)
            visible = backtestable(full_snap.signals, bar.ts)
            bag = seen.setdefault(code, set())
            if code not in primed:
                # 预热：第一根 bar 上可见的都是「开仓之前就存在」的历史信号，
                # 只登记、不交易，否则开局会一次性买满。
                primed.add(code)
                bag.update(signal_key(code, s) for s in visible)
                fresh: tuple[Any, ...] = ()
            else:
                fresh = tuple(s for s in visible if signal_key(code, s) not in bag)
                bag.update(signal_key(code, s) for s in fresh)
            snapshot = _view(full_snap, bar.ts, fresh)
            ctx = Context(ts=bar.ts, cash=broker.cash,
                          equity=broker.equity(last_close),
                          positions=tuple(broker.positions.values()))
            for order in strategy.on_bar(ctx, bar, snapshot) or ():
                broker.submit(order, ts=bar.ts)
        mv = broker.market_value(last_close)
        equity_points.append(EquityPoint(ts=ts, cash=broker.cash, market_value=mv,
                                        equity=broker.cash + mv))

    # 3) 基准（缺数据则 None + 明确说明，不静默替换）
    benchmark: Mapping[str, Any] | None = None
    if benchmark_code:
        hit, bench = _read_benchmark(_bare(benchmark_code), period, start, end)
        if bench is None or len(bench) == 0:
            notes.append(f"缺基准数据：未找到 {benchmark_code} 的 {period} 行情，"
                         "基准=None，不做基准对比")
        else:
            c0, c1 = float(bench["close"].iloc[0]), float(bench["close"].iloc[-1])
            benchmark = {
                "code": hit, "period": period,
                "start": str(bench["ts"].iloc[0]), "end": str(bench["ts"].iloc[-1]),
                "close_start": c0, "close_end": c1,
                "return": (c1 / c0 - 1.0) if c0 else None,
            }

    span = (all_ts[0], all_ts[-1])
    if not broker.fills:
        notes.append("本区间 0 笔成交：买点条件未满足（例如缠论信号为 0，见引擎缺陷 G1）"
                     "或资金/涨跌停/T+1 限制导致委托被拒；详见 rejections。")
    result = _finish(_skeleton(bare_codes, period, start, end, initial_cash, name),
                     trades=broker.fills, round_trips=broker.round_trips,
                     equity=tuple(equity_points), positions=tuple(broker.positions.values()),
                     rejections=broker.rejections, pending=broker.pending,
                     benchmark=benchmark, notes=notes, data_span=span)
    log.info("回测完成 %s: %d 笔成交 / %d 个回合 / %d 笔拒单",
             ",".join(bare_codes), len(result.trades), len(result.round_trips),
             len(result.rejections))
    return result


def _skeleton(codes, period, start, end, initial_cash, name) -> BacktestResult:
    return BacktestResult(codes=tuple(codes), period=period, start=start, end=end,
                          initial_cash=float(initial_cash), strategy=name)


def _finish(base: BacktestResult, **kw) -> BacktestResult:
    """先填原始数据，再用它算指标（指标是视图，不参与循环）。"""
    provisional = replace(base, **kw)
    return replace(provisional, metrics=metrics(provisional))
