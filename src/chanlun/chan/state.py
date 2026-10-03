"""未确认状态机与稳定 ID（Task 10）。

三条不变量，全部由本模块负责守护：

1. **稳定 ID**：结构对象的 ID 只由「类型名 + 原始 bar 索引(src_start/src_end)」决定，
   与位置索引 `idx`、合成索引 `start_stroke_idx` 无关。因此同一对象在不同快照间
   的 ID 恒等，可以跨快照追踪、也可以跨进程重放。
2. **confirmed_at 写一次永不改**：一旦某个对象被确认，其确认时间在所有后续快照中
   保持不变；全量重算把已确认结构算回 TENTATIVE 时**不得回退**。
3. **失效留痕**：被推翻（在新快照中消失）的对象，无论此前是 TENTATIVE 还是
   CONFIRMED，都转为 `INVALIDATED` 并保留在快照里，供回测审计。

本模块只做 duck typing + `dataclasses.replace`，不 import 任何具体快照类，
这样 Task 11 的 `Snapshot` / `Pivot` / `Signal` 可以后置定义。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from typing import Any

from .types import Status

__all__ = ["Status", "backtestable", "confirm_at", "object_id", "reconcile"]

# reconcile 需要接管的四类结构字段（按此顺序处理，保证结果可复现）。
_FIELDS: tuple[str, ...] = ("strokes", "segments", "pivots", "signals")


def object_id(obj: Any) -> str:
    """稳定 ID：`<类型名小写>@<src_start>-<src_end>`，例如 `stroke@120-180`。

    只使用原始 bar 索引。若对象缺少 `src_start`/`src_end`，说明它无法被稳定追踪，
    直接报错而不是退化成不稳定的位置索引。
    """
    name = type(obj).__name__.lower()
    try:
        src_start = obj.src_start
        src_end = obj.src_end
    except AttributeError as exc:  # pragma: no cover - 防御性分支
        raise TypeError(
            f"{type(obj).__name__} 缺少 src_start/src_end，无法生成稳定 ID"
        ) from exc
    return f"{name}@{src_start}-{src_end}"


def confirm_at(item: Any, ts: str) -> Any:
    """把 item 标记为 CONFIRMED，并写入确认时间。

    **关键不变量**：若 item 已有 `confirmed_at`，原样返回，绝不覆盖。
    这是「写一次永不改」的唯一落点，所有确认路径都必须经过这里。
    """
    if getattr(item, "confirmed_at", None) is not None:
        return item
    return dataclasses.replace(item, status=Status.CONFIRMED, confirmed_at=ts)


def _field_items(snapshot: Any, name: str) -> Sequence[Any] | None:
    """取出快照的某个结构字段；字段不存在返回 None（duck typing）。"""
    if not hasattr(snapshot, name):
        return None
    value = getattr(snapshot, name)
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError(f"快照字段 {name} 应是可迭代的结构序列，实际为 {type(value).__name__}")
    return list(value)


def _reconcile_field(prev_items: Sequence[Any], new_items: Sequence[Any],
                     as_of: str | None) -> list[Any]:
    """对单个字段做状态收敛，返回顺序确定的列表。"""
    prev_by_id = {object_id(x): x for x in prev_items}
    result: list[Any] = []
    seen: set[str] = set()

    for new_item in new_items:
        oid = object_id(new_item)
        seen.add(oid)
        prev_item = prev_by_id.get(oid)

        if prev_item is not None and getattr(prev_item, "status", None) is Status.CONFIRMED:
            # 已经确认过：沿用 prev 的 confirmed_at 与 status，禁止回退。
            result.append(dataclasses.replace(
                new_item,
                status=Status.CONFIRMED,
                confirmed_at=prev_item.confirmed_at,
            ))
            continue

        if getattr(new_item, "status", None) is Status.CONFIRMED:
            # 本次由 TENTATIVE -> CONFIRMED：写入自带的确认时间，
            # 缺失时用快照的 as_of（触发确认的那根 bar 的时间）。
            ts = new_item.confirmed_at
            result.append(confirm_at(new_item, as_of if ts is None else ts))
            continue

        # TENTATIVE -> TENTATIVE（或首次出现）：保持未确认。
        result.append(new_item)

    # 在 prev 中出现、但新快照里消失的结构：结构被推翻，但历史必须留痕。
    for prev_item in prev_items:
        oid = object_id(prev_item)
        if oid in seen:
            continue
        seen.add(oid)
        result.append(dataclasses.replace(prev_item, status=Status.INVALIDATED))

    return result


def reconcile(prev_snapshot: Any, new_snapshot: Any) -> Any:
    """把新快照与上一快照对齐，返回状态收敛后的新快照。

    - `prev_snapshot` 可以是 None（首个快照）。
    - 只处理新快照**存在**的字段（strokes/segments/pivots/signals）。
    - 每个字段的输出顺序：先按新快照的顺序，再按 prev 顺序追加被 invalidated 的对象。
    """
    as_of = getattr(new_snapshot, "as_of", None)
    updates: dict[str, list[Any]] = {}

    for name in _FIELDS:
        new_items = _field_items(new_snapshot, name)
        if new_items is None:
            continue
        prev_items = [] if prev_snapshot is None else _field_items(prev_snapshot, name)
        updates[name] = _reconcile_field(prev_items or [], new_items, as_of)

    return dataclasses.replace(new_snapshot, **updates)


def backtestable(items: Iterable[Any], as_of: str) -> list[Any]:
    """回测/盘中扫描的按时间过滤入口：只保留 `confirmed_at <= as_of` 的结构。

    条件：`status is CONFIRMED` 且 `confirmed_at is not None` 且 `confirmed_at <= as_of`。

    注意 `confirmed_at` 是**结构自身的确认完成时刻**，不是「首次可见时刻」——
    两者**可能重合**，不保证不等。D-36 之前 `confirmed_at` 取锁定分型的极值 bar
    时刻，系统性早 1~9 根 bar，于是实测「100% 早于首次可见」（`sh.600000`
    121/121、`sh.601088` 158/158、`sz.000001` 146/146）—— 那个「100%」是 D-36
    缺陷的观测面，不是独立事实。D-36 改取真实可知时刻后，夹具 `sz.300059` 上
    221 个可见信号里已有 1 个的 `confirmed_at` 恰好等于当日 ts。所以本函数保证的
    是「该结构自己已经确认完成」，**不保证**它在本 bar 之前就被引擎产出过：
    一个 `confirmed_at <= as_of` 的结构仍可能今天才第一次出现。调用方若需要
    真正的「首次可见」，必须自己跟踪观测过程（见 `backtest/runner.py` 的
    `seen`/`primed` 差分与 D-34）。
    """
    return [
        item for item in items
        if getattr(item, "status", None) is Status.CONFIRMED
        and getattr(item, "confirmed_at", None) is not None
        and item.confirmed_at <= as_of
    ]
