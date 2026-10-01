---
id: L20-PIVOT-DEFINITION
lesson: 20
source: 缠中说禅《教你炒股票108课》第20课《缠中说禅走势中枢级别扩张及第三类买卖点》
url: https://chanlun108.cn/chanzhongshuochan108ke/20.html
applies_to: src/chanlun/chan/pivot.py
tags: 中枢,定义,重叠,区间,前三段
---

## 原文摘录

> 缠中说禅走势中枢由前三个连续次级别走势类型的重叠部分确定，其后的走势有两种情况：一、该走势中枢的延伸。二、产生新的同级别走势中枢。
>
> 中枢的区间就是[max（a2,b2,c2），min（a1,b1,c1）]

## 释义

中枢区间由**前三段**确定，并且是这三段重叠区间的交：
下沿取三段低点的**最大值**，上沿取三段高点的**最小值**。
文字里 `[max(a2,b2,c2)，min(a1,b1,c1)]` 的 a2/b2/c2 是各段低点、a1/b1/c1 是各段高点。

关键点：**区间一旦由前三段确定，就不该随延伸而改变**。
当前 `find_pivots` 在每个候选段并入后重算 `zd=max(所有段 low)`、`zg=min(所有段 high)`，
区间只会单调收缩，等于把「后续段是否还有重叠」这个判定越判越松，中枢可以无限吃段。

## 适用算法

- `src/chanlun/chan/pivot.py::find_pivots` —— 区间口径与延伸判定。
