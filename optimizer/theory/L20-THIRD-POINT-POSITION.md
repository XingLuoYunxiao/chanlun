---
id: L20-THIRD-POINT-POSITION
lesson: 20
source: 缠中说禅《教你炒股票108课》第20课《缠中说禅走势中枢级别扩张及第三类买卖点》
url: https://chanlun108.cn/chanzhongshuochan108ke/20.html
applies_to: src/chanlun/chan/signal.py
tags: 第三类买卖点,离开,回试,回抽,ZG,ZD,位置,第一次
---

## 原文摘录

> 一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别走势类型回试，其低点不跌破ZG，则构成第三类买点；一个次级别走势类型向下离开缠中说禅走势中枢，然后以一个次级别走势类型回抽，其高点不升破ZD，则构成第三类卖点。
>
> 并不是任何回调回抽都是第三类买卖点，必须是第一次

## 释义

这条定义里有三个必须照字面落实的点：

1. **判定标准是位置，不是方向**。「低点不跌破 ZG」「高点不升破 ZD」是价格与
   中枢区间的比较；而离开与回试各自都是「一个次级别走势类型」，不是一根线段。
2. **离开段与回试段是先后两段走势类型**。回试段是**离开之后**的那一段，
   不是「第一段完全在区间外的那一段」。
3. **必须是第一次**。离开之后若已经回试过、或价格已经回到区间内，
   后面的回调就不再是三类买卖点。

## 当前实现（2026-10-01 校订）

> **本节此前写的是**「当前 `signal.py::_third_kind` 把 `segs[p.end_idx+1]` 当离开段、
> `segs[p.end_idx+2]` 当回试段」——**该描述已失效**，留此一行以免旧结论被当成现状。

现状（`signal.py::_third_kind`）取的是**位置口径**：

```python
leave_i, back_i = p.end_idx, p.end_idx + 1
leave = segs[leave_i]        # 中枢组的最后一段 = 离开段
back  = segs[back_i]         # 紧接其后的那一段 = 回试段
```

即 **离开段 = `segs[p.end_idx]`**（中枢组自身的最后一段），回试段是它的下一段。
这与第20课「一个次级别走势类型向上离开……然后以一个次级别走势类型回试」的字面顺序一致：
离开是**中枢的最后一段**，回试是**离开之后的那一段**。

判据本体（与原文逐字对应）：

- B3：`leave.direction == 1 and leave.high > p.zg`，且 `back.direction == -1 and back.low > p.zg`
- S3：`leave.direction == -1 and leave.low < p.zd`，且 `back.direction == 1 and back.high < p.zd`
- 「**必须是第一次**」由 `if/elif` 结构保证：一旦本中枢已判出 B3 就不再判 S3，反之亦然。

`_entering_and_leaving` 里另有一处同源口径，docstring 已写明为什么不能用
`segs[p.end_idx + 1]`：那会把**回抽段**当成离开段。

## 适用算法

- `src/chanlun/chan/signal.py::_third_kind` —— 离开段/回试段的识别与位置判据。
- `src/chanlun/chan/signal.py::_entering_and_leaving` —— 进入段/离开段口径（同为位置口径）。
