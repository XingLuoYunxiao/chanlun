---
id: L20-TREND-NO-OVERLAP
lesson: 20
source: 缠中说禅《教你炒股票108课》第20课《缠中说禅走势中枢级别扩张及第三类买卖点》
url: https://chanlun108.cn/chanzhongshuochan108ke/20.html
applies_to: src/chanlun/chan/trend.py, src/chanlun/chan/pivot.py
tags: 趋势,中枢,不重叠,GG,DD,ZD,ZG,级别扩张,走势类型,第20课
---

## 原文摘录

> 而在趋势里，同级别的前后缠中说禅走势中枢是不能有任何重叠的，这包括任何围绕走势中枢产生的任何瞬间波动之间的重叠。

> 缠中说禅走势中枢中心定理二：前后同级别的两个缠中说禅走势中枢，后GG〈前DD等价于下跌及其延续；后DD〉前GG等价于上涨及其延续。后ZG<前ZD且后GG〉=前DD，或后ZD〉前ZG且后DD=<前GG，则等价于形成高级别的走势中枢。

## 释义

第 20 课把「两个中枢构成趋势」的判据写死成**波动区间**的比较，而不是中枢区间
`[ZD, ZG]` 的比较：

- 上涨及其延续 ⟺ `后DD > 前GG`
- 下跌及其延续 ⟺ `后GG < 前DD`

这里 `GG=max(gn)`、`DD=min(dn)` 遍历中枢中所有 Z 走势段（与中枢形成方向一致的
次级别走势类型），所以 `[DD, GG]` 是**围绕中枢产生的全部波动区间**，比
`[ZD, ZG]` 宽。原文还特意补一句：「这包括任何围绕走势中枢产生的任何瞬间波动
之间的重叠」——**波动区间**都不许碰。

反过来，如果 `[ZD, ZG]` 已经不重叠、但 `[DD, GG]` 仍然重叠
（`后ZG<前ZD且后GG>=前DD`，或 `后ZD>前ZG且后DD<=前GG`），原文判定为
**形成高级别的走势中枢**，不是趋势。

## 当前实现（2026-10-01 校订）

`trend.py::_direction` 已按中心定理二落地：

```python
def _direction(a: Pivot, b: Pivot) -> int:
    if b.dd > a.gg:   return 1        # 后DD〉前GG → 上涨及其延续
    if b.gg < a.dd:   return -1       # 后GG〈前DD → 下跌及其延续
    return 0                          # 形成高级别的走势中枢（**不是趋势**）
```

比的是 `[DD, GG]`，不是 `[ZD, ZG]`；返回 `0` 对应原文「则等价于形成高级别的走势中枢」，
`classify_trends` 据此不再把它算作趋势。

**落地前置条件**：`GG`/`DD` 的口径必须先正确。`pivot.py` 现状是**只遍历 Zn（同向段）
且不含离开段**（`inside = zn[:-1]`）。「只遍历 Zn」是原文的直接推论（第20课
「n遍历中枢中所有Zn」）；「不含离开段」是**原文空白项下的口径选择**，依据是中心定理二的
可满足性——含离开段时 22/22 对全部落进「形成高级别中枢」，原文那两句永远无法成立。
详见 `L20-PIVOT-DEFINITION.md` 与 `docs/evidence/2026-10-01-chanlun-strictness-audit.md` §2.6.1。

实测：`trend_pairs_violating_theorem2` 18 → 0；涨/跌/盘整 10/7/11 → 8/2/26。

## 适用算法

- `src/chanlun/chan/trend.py::_direction` —— 相邻两个中枢是否构成趋势（**已落地**）。
- `src/chanlun/chan/pivot.py` —— `GG`/`DD` 只遍历 Zn 且不含离开段。
- `src/chanlun/chan/pivot.py::merge_pivots` —— `[DD, GG]` 重叠时应做级别扩张。
  **注意：这一半尚未实现**——`merge_pivots` 仍是生产链路不可达的死代码，
  原文「则等价于形成高级别的走势中枢」目前只体现为「不判成趋势」，
  **并没有真的合成高级别中枢**（见审计 §2.12）。
