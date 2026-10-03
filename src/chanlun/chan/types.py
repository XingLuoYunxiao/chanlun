"""结构引擎公共类型。

所有对象都是 frozen dataclass（不可变），这是「快照 = 纯函数输出」与
「全量重算 ≡ 增量更新」两条设计约束的基础。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any


class Status(str, Enum):
    """未确认 / 已确认 / 已失效。

    只有 CONFIRMED 且 `confirmed_at <= 当前 bar ts` 的结构才允许被回测与
    盘中扫描使用（禁止未来函数）。
    """

    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"


class FractalKind(str, Enum):
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class MergedBar:
    """包含处理后的合并K线。"""

    idx: int          # 合并后序号（0 起）
    ts: str           # 该合并K线完成时的原始 bar 时间（= src_end 对应时间）
    start_ts: str     # 该合并K线起始的原始 bar 时间
    high: float
    low: float
    direction: int    # 本次合并依据的方向：1 向上, -1 向下
    src_start: int    # 覆盖的原始 bar 索引起（含）
    src_end: int      # 覆盖的原始 bar 索引止（含）


@dataclass(frozen=True)
class Fractal:
    """分型。`midx` 是分型中间那根合并K线的下标，用于笔的间隔判定。"""

    kind: FractalKind
    midx: int         # 分型中间合并K线的 idx
    ts: str           # 极值所在原始 bar 的时间（**事件时刻**）
    high: float       # 中间合并K线的高
    low: float        # 中间合并K线的低
    price: float      # 顶=high, 底=low
    src_idx: int      # 极值所在原始 bar 索引
    # **可知时刻**：第 62 课要求右侧那根合并K线走出来，分型才成立，所以这里
    # 记 `merged[midx + 1].ts`（右侧合并K线的完成时间）。它**不是** `ts` ——
    # `ts` 是极值 bar 的事件时刻，系统性早 1~9 根 bar（实测 0/494 笔能在
    # `ts` 处复现自己的锁定分型，见 D-36）。手工构造的分型没有合并K线序列
    # 可依，留 None，由消费方显式退化处理，不在这里猜。
    confirmed_at: str | None = None


@dataclass(frozen=True)
class Stroke:
    """笔。方向 1 = 向上笔（底→顶），-1 = 向下笔（顶→底）。"""

    idx: int
    direction: int
    start: Fractal
    end: Fractal
    high: float
    low: float
    src_start: int
    src_end: int
    status: Status = Status.CONFIRMED
    # 确认时间。**必须**由状态机/构造函数显式给出，不能由 end.ts 推导：
    # 一笔要等反向的那一笔走出来才被锁定，取 end.ts 是隐蔽的未来函数。
    confirmed_at: str | None = None


@dataclass(frozen=True)
class SegmentBreak:
    """线段划分策略给出的一个「线段在此笔结束」的判定。"""

    stroke_idx: int                # 线段最后一笔的 idx
    reason: str                    # case1_top_fractal / case2_gap_confirmed / ...
    has_gap: bool = False
    confirm_stroke_idx: int | None = None   # 做出该判定时最后被消费到的笔（用于无未来函数的 confirmed_at）
    start_stroke_idx: int = 0      # 该线段的第一笔 idx（>0 表示左边有不完整的前导段）


@dataclass(frozen=True)
class Segment:
    """线段（第 67 课）。

    `status == CONFIRMED` 的线段：笔数 >= 3 且必为**单数**（线段不可能顶到顶）。
    末段为 `TENTATIVE`，笔数可能不足 3 或为双数，**不得**参与中枢计算与回测。
    """

    idx: int
    direction: int
    start: Stroke
    end: Stroke
    high: float
    low: float
    start_stroke_idx: int
    end_stroke_idx: int
    stroke_count: int
    status: Status = Status.CONFIRMED
    confirmed_at: str | None = None
    policy: str = "lesson_67_78"

    # 线段两端在**原始 bar** 上的位置。原始 bar 索引跨快照稳定，是状态机
    # 生成稳定 ID 的锚点（`idx`/`start_stroke_idx` 会随重算漂移，不能用）。
    @property
    def src_start(self) -> int:
        return self.start.src_start

    @property
    def src_end(self) -> int:
        return self.end.src_end


def to_jsonable(obj: Any) -> Any:
    """把 dataclass / Enum 结构转成可 JSON 序列化的原生对象。"""

    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


class ChanJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:  # noqa: D102
        if isinstance(o, Enum):
            return o.value
        if is_dataclass(o) and not isinstance(o, type):
            return to_jsonable(o)
        return super().default(o)
