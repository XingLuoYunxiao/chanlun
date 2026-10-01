---
id: L20-PIVOT-EXTENSION
lesson: 20
source: 缠中说禅《教你炒股票108课》第20课《缠中说禅走势中枢级别扩张及第三类买卖点》
url: https://chanlun108.cn/chanzhongshuochan108ke/20.html
applies_to: src/chanlun/chan/pivot.py, src/chanlun/chan/trend.py
tags: 中枢,延伸,重叠,级别扩张,ZD,ZG,定理
---

## 原文摘录

> 而在趋势里，同级别的前后缠中说禅走势中枢是不能有任何重叠的
>
> 走势中枢的延伸等价于任意区间[dn，gn]与[ZD，ZG]有重叠。换言之，若有Zn，使得dn>ZG或gn<ZD，则必然产生高级别的走势中枢或趋势及延续。

## 释义

中枢中心定理一给出了**延伸的判据**：只要每一个后续的走势段区间 `[dn, gn]`
与 `[ZD, ZG]` 有重叠，中枢就继续延伸；一旦出现某个段完全在 ZG 之上（`dn>ZG`）
或完全在 ZD 之下（`gn<ZD`），中枢就结束，并「必然产生高级别的走势中枢或趋势及延续」。

两个直接推论：

1. 判据作用在**走势段区间**上，不是作用在单根 bar 或线段方向上。
2. 延伸出来的中枢，级别是**扩张**了的——所以延伸不能无上限地吃下去，
   否则会把「高级别盘整」和「本级别趋势」混成一个中枢。

## 适用算法

- `src/chanlun/chan/pivot.py::find_pivots::_overlaps` —— 判据实现（`seg.low <= zg and seg.high >= zd`）。
- `src/chanlun/chan/pivot.py::find_pivots` —— 延伸循环缺少上限。
- `src/chanlun/chan/trend.py::classify_trends` —— 前后中枢重叠时不应判为趋势。
