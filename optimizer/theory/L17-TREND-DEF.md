---
id: L17-TREND-DEF
lesson: 17
source: 缠中说禅《教你炒股票108课》第17课《走势终完美》
url: https://chanlun108.cn/chanzhongshuochan108ke/17.html
applies_to: src/chanlun/chan/trend.py, src/chanlun/chan/pivot.py
tags: 走势类型,趋势,盘整,中枢,分解定理
---

## 原文摘录

> 缠中说禅盘整：…某完成的走势类型只包含一个缠中说禅走势中枢
>
> 缠中说禅趋势：…某走势类型至少包含两个以上依次同向的缠中说禅走势中枢
>
> 缠中说禅走势分解定理二：任何级别的任何走势类型，都至少由三段以上次级别走势类型构成。

## 释义

走势类型只有两类：**盘整**只有一个中枢，**趋势**至少两个依次同向且不重叠的中枢。
「依次同向」是方向条件，「至少两个」是数量条件，两者缺一不可——
`trend.py::_direction()` 目前只看 ZD/ZG 单调抬升或下移，没有「不重叠」这一条。

## 适用算法

- `src/chanlun/chan/trend.py::_direction` —— 判趋势必须同时满足同向与不重叠。
- `src/chanlun/chan/pivot.py::find_pivots` —— 走势类型的最小构成单位。
