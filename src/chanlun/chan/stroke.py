"""笔（第 62 课 + 第 77 课修订）。

规则（三步，顺序不可颠倒）：
1. **同类合并**：遍历分型，若与已选序列末项同类，保留更极端者（顶取更高、底取更低）。
2. **间隔校验**：顶底必须交替，且两个分型中心的合并K线下标之差 `>= 4`
   （等价于「顶底之间至少有一根独立合并K线」，即一笔至少含 5 根合并K线）。
3. **回退**：若不满足间隔则丢弃该分型继续向后找（不强行连线）；下一个同类更极端分型
   会自然顶替末项，因此不会漏掉更极端的端点。

最后一笔标记 `TENTATIVE`：其右端点尚未被后续反向分型锁定，价格仍可能延伸。
"""

from __future__ import annotations

from collections.abc import Sequence

from .types import Fractal, FractalKind, Status, Stroke

MIN_GAP = 4


def _more_extreme(new: Fractal, old: Fractal) -> bool:
    if new.kind is FractalKind.TOP:
        return new.price > old.price
    return new.price < old.price


def select_pivotal_fractals(
    fractals: Sequence[Fractal], min_gap: int = MIN_GAP
) -> list[Fractal]:
    """选出构成笔端点的分型序列（顶底交替且满足间隔要求）。"""
    seq: list[Fractal] = []
    for f in fractals:
        if not seq:
            seq.append(f)
            continue

        last = seq[-1]
        if f.kind is last.kind:
            if _more_extreme(f, last):
                seq[-1] = f
            continue

        if abs(f.midx - last.midx) >= min_gap:
            seq.append(f)
        # 间隔不足：该分型不足以构成一笔的端点，跳过继续向后寻找

    return seq


def build_strokes(
    fractals: Sequence[Fractal], min_gap: int = MIN_GAP
) -> list[Stroke]:
    """由分型序列构造笔。最后一笔为 TENTATIVE。

    `confirmed_at` 取**下一笔终点分型的时间**：第 i 笔的终点要等反向的那一笔
    走出来（下一个分型成立）才被锁定，所以在第 i 笔自己的终点时刻还不知道
    它已经结束。直接取 `self.end.ts` 是隐蔽的未来函数，会让回测用上当时
    尚未发生的信息。最后一笔没有后继，故为 None。
    """
    seq = select_pivotal_fractals(fractals, min_gap=min_gap)

    strokes: list[Stroke] = []
    for i in range(len(seq) - 1):
        a, b = seq[i], seq[i + 1]
        direction = 1 if a.kind is FractalKind.BOTTOM else -1
        is_last = i + 1 == len(seq) - 1
        # 锁定分型 = 下一笔的终点分型 `seq[i + 2]`。它「走出来」的时刻是它的
        # **可知时刻**（右侧合并K线走完），不是它的极值 bar 时刻。取后者会
        # 早 1~9 根 bar —— 实测 494 笔里 0 笔能在那个时刻复现自己的锁定分型。
        # 手工构造的分型没有可知时刻，退回事件时刻（合成数据的既有行为）。
        confirmed_at: str | None = None
        if not is_last:
            locking = seq[i + 2]
            confirmed_at = locking.confirmed_at or locking.ts
        strokes.append(
            Stroke(
                idx=i,
                direction=direction,
                start=a,
                end=b,
                high=max(a.price, b.price),
                low=min(a.price, b.price),
                src_start=a.src_idx,
                src_end=b.src_idx,
                status=Status.TENTATIVE if is_last else Status.CONFIRMED,
                confirmed_at=confirmed_at,
            )
        )
    return strokes
