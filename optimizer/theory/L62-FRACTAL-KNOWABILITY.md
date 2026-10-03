---
id: L62-FRACTAL-KNOWABILITY
lesson: 62
source: 缠中说禅《教你炒股票108课》第62课《分型、笔与线段》
url: https://chanlun108.cn/chanzhongshuochan108ke/62.html
applies_to: src/chanlun/chan/fractal.py
tags: 分型,可知时刻,确认时刻,右侧K线,未来函数,point-in-time,D-36
---

## 原文摘录

第 062 课《分型、笔与线段》：

> 像图1这种，第二K线高点是相邻三K线高点中最高的，而低点也是相邻三K线低点中最高的，
> 本ID给一个定义叫顶分型；图2这种叫底分型，第二K线低点是相邻三K线低点中最低的，
> 而高点也是相邻三K线高点中最低的。

第 062 课（同一课的「结合律」要求）：

> 但这里有一个细微的地方要分清楚，因为结合律是必须遵守的，像图3这种，顶和底之间
> 必须共用一个K线，这就违反结合律了，所以这不算一笔。

## 释义

分型是**三根相邻 K 线**上的谓词（原文：「相邻三K线」）。中间那根（第二 K 线）是不是
极值，取决于**右边那根**的高低点 —— 在右边那根 K 线走完之前，这个命题既不能判真也
不能判假。

所以：**分型的「可知时刻」是右侧合并 K 线完成的时间，不是极值那根 K 线的完成时间。**
两者系统性相差 1~9 根原始 bar（实测 494 笔：最小 1、均值 1.68、最大 9）。

这不是措辞问题，它决定 point-in-time 的正确性：任何拿「极值 bar 的时间」当分型确认
时间的下游（盘中扫描、`confirmed_at <= D` 之类的闸门）都会**提前知道未来**。

主干用 `Fractal.confirmed_at` 显式记这个时刻（`merged[midx + 1].ts`），而 `Fractal.ts`
仍保留「极值 bar 的事件时刻」的语义 —— 两者**不是**同一个东西，不得互换使用。
`Stroke.confirmed_at`（取锁定分型的 `confirmed_at`）、`Segment.confirmed_at`、
`Pivot.confirmed_at` 全部继承这个时钟，所以根因只有一处。

## 适用算法

- `src/chanlun/chan/fractal.py::find_fractals` —— 写入 `Fractal.confirmed_at = merged[i + 1].ts`。
- `src/chanlun/chan/stroke.py::build_strokes` —— 一笔的确认时刻取**锁定分型**
  （`seq[i + 2]`）的 `confirmed_at`，取不到才退回 `ts`。
- `src/chanlun/chan/state.py::backtestable`、`src/chanlun/backtest/strategy.py::confirmed_fractals`
  —— 消费方，按 `confirmed_at` 过滤。
