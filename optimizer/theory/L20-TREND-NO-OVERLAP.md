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

## 适用算法

- `src/chanlun/chan/trend.py::_direction` —— 相邻两个中枢是否构成趋势。
- `src/chanlun/chan/pivot.py::merge_pivots` —— `[DD, GG]` 重叠时应做级别扩张。
