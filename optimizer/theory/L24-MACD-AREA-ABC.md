---
id: L24-MACD-AREA-ABC
lesson: 24
source: 缠中说禅《教你炒股票108课》第24课《MACD对背弛的辅助判断》
url: https://chanlun108.cn/chanzhongshuochan108ke/24.html
applies_to: src/chanlun/chan/signal.py, src/chanlun/chan/macd.py
tags: MACD,背驰,面积,A段,B段,C段,红柱子,绿柱子,盘整背驰
---

## 原文摘录

> 用MACD判断背驰，首先要有两段同向的趋势。同向趋势之间一定有一个盘整或反向趋势连接，把这三段分别称为A、B、C段。
>
> 而C段的走势类型完成时对应的MACD柱子面积（向上的看红柱子，向下看绿柱子）比A段对应的面积要小，这时候就构成标准的背弛。
>
> 13点05分，第三个红柱子，这时候，把三个红柱子的面积加起来，也没有A段两个红柱子面积和大，显然背驰了
>
> 如果C段上破中枢，但MACD柱子的面积小于A段的，这时候的原则是先出来…

## 释义

面积比较的单位是**走势类型（A 段、C 段）**，不是一根线段。
第 24 课在实例里把「三个红柱子」的面积加总去比「A 段两个红柱子」，
说明一段走势类型的面积是**它内部多根柱子面积之和**，跨若干线段。
比较结构是 A（同向前一段）— B（中间的反向/盘整）— C（同向后一段），
C 的对应面积小于 A 即背驰；C 是否创出 A 的极值不是唯一条件（盘整背驰可以不创极值）。

## 适用算法

- `src/chanlun/chan/signal.py::_first_kind` —— A/C 段的选取与面积比较。
- `src/chanlun/chan/signal.py::_area` —— 逐段面积取值口径。
- `src/chanlun/chan/macd.py::hist_area` —— 柱子面积求和口径。
