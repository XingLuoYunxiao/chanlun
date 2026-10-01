# 第 11 轮取证（2026-10-01）

第 11 轮取证。视角：一个真的在用这套系统看盘的缠论读者。本轮把注意力从「买卖点数量」
移到**走势类型与中枢自身的定义**上，因为前三轮（G1a/G1b/G5a/G5b）已经把买卖点的
口径补齐，剩下的问题更多是「级别判错」和「印给用户看的东西不对」。

冻结快照：`basis.sha256 = 044d4d17bfdc7e69`，24/24 只票，`bars = 38754`，
`frozen_at = 2026-10-01T17:05:30+08:00`。全部数字来自脚本，没有手算。

本轮基线（`--audit` 无补丁）：

```
pivots 56  trends_up 10  trends_down 7  trends_consolidation 6  signals 43
b1 0  b2 0  b3 26  s1 1  s2 0  s3 16   zero_signal_codes 3  signal_problems 0
trend_groups 17  trend_group_leave_dir_ok 13  trend_group_prev_ok 0  trend_group_both_ok 0
pivot_max_segments 14  pivot_over_9 8  pivot_segment_total 300
```

本轮新建了 2 条 theory 条目（`L20-TREND-NO-OVERLAP`、`L33-PIVOT-EXTENSION-LIMIT`，
原文均从 chanlun108.cn 逐字复制），新增 1 个只读量具
`optimizer/tools/measure_g11_dd_gg_scope.py`。**`chanlun/src/` 全程未改动**
（每步前 `git status --porcelain -- chanlun/src` 均为空）。

---

## 观测点 G11a: 趋势判据只看中枢区间 `[ZD,ZG]` 的单调，缺了第 20 课定理二的「波动区间不许重叠」，于是把 19 对「其实该升级成高级别中枢」的中枢全判成了趋势

**判据**（第 20 课《缠中说禅走势中枢级别扩张及第三类买卖点》，逐字）：

> 而在趋势里，同级别的前后缠中说禅走势中枢是不能有任何重叠的，这包括任何围绕走势中枢产生的任何瞬间波动之间的重叠。

> 缠中说禅走势中枢中心定理二：前后同级别的两个缠中说禅走势中枢，后GG〈前DD等价于下跌及其延续；后DD〉前GG等价于上涨及其延续。后ZG<前ZD且后GG〉=前DD，或后ZD〉前ZG且后DD=<前GG，则等价于形成高级别的走势中枢。

theory 条目：`optimizer/theory/L20-TREND-NO-OVERLAP.md`；另有既有条目
`L17-TREND-DEF.md`（第 17 课）在「释义」里已经自己写明「`trend.py::_direction()`
目前只看 ZD/ZG 单调抬升或下移，没有『不重叠』这一条」——即这个缺口在 theory 库里
挂了很久，但 `optimizer/journal/` 里从第 6 轮到第 10 轮没有任何一轮把它作为提案
提出来（`grep -rl "L17-TREND-DEF\|不重叠" optimizer/journal/` 为空）。

**现状**：`src/chanlun/chan/trend.py:51-57`

```python
def _direction(a: Pivot, b: Pivot) -> int:
    if b.zd > a.zd and b.zg > a.zg:
        return 1
    if b.zd < a.zd and b.zg < a.zg:
        return -1
    return 0
```

只用中枢区间 `[ZD,ZG]` 的抬升/下移判方向，完全没有 `DD/GG` 这一层。
`trend.py:12-15` 的模块 docstring 自己承认了这个缺口。上游
`pivot.py::find_pivots` 切中枢时用的是「与 `[ZD,ZG]` 有重叠就继续延伸」
（`pivot.py:131-133`，`_overlaps` 定义在 `pivot.py:86-88`，判据是
`seg.low <= zg and seg.high >= zd`），所以「`[ZD,ZG]` 不重叠」这件事在
切中枢阶段就已经被强制成立——`_direction` 加的那两个不等式几乎是同义反复。

**证据**：

```bash
cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
    optimizer/tools/measure_g11_trend_overlap.py
```

输出（脚本用 monkey-patch 在内存里换 `_direction`，不改主干）：

```
相邻 CONFIRMED 中枢对             : 19
主干判成趋势的对数                : 19
其中 [DD,GG] 仍重叠               : 19   (19/19)
按第20课定理二判成趋势的对数      : 0
[ZD,ZG] 已不重叠 但 [DD,GG] 重叠  : 19
受影响代码数                      : 17/24
反例: 600000 600030 600036 600276(×2) 600887 600900 601088
```

即：**19 对相邻确认中枢，波动区间 100% 互相重叠**，按原文全都属于「形成高级别的
走势中枢」，主干却 100% 判成趋势。

`--audit --patch optimizer/patches/round-011-G11a.patch`：

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| `trends_up` | 10 | **0** |
| `trends_down` | 7 | **0** |
| `trends_consolidation` | 6 | **40** |
| `signals` | 43 | 42 |
| `s1` | 1 | **0** |
| `trend_groups` | 17 | **0** |
| 其它（pivots/b3/s3/…） | — | 不变 |

**风险**（这一条是本轮最大的风险点）：

1. **全部趋势消失**。改后 40 个中枢全是盘整，`trends_up/down` 归零、`s1` 归零。
   直接采纳会把系统从「有趋势」变成「永远盘整」，比不改更糟。
2. **改判据救不回来**。我做了 2×2 矩阵验证：

   | 组合 | trends_up | trends_down | consolidation | signals |
   | --- | --- | --- | --- | --- |
   | A 主干（`[ZD,ZG]` 单调 + 全段 DD/GG） | 10 | 7 | 6 | 43 |
   | B 只修 G11c 的 DD/GG 范围（判据不动） | 10 | 7 | 6 | 43 |
   | C 主干 DD/GG + 定理二判据 | 0 | 0 | 40 | 42 |
   | D Z 段 DD/GG + 定理二判据 | 0 | 0 | 40 | 42 |

   D 和 C 完全一样 → **光把 `_direction` 改成定理二没有用**，因为
   `find_pivots` 是「`i = j` 跳到第一个不重叠的线段」切段的，
   `dd/gg` 又包含自己那一组的越界段，`后DD > 前GG` 在结构上不可达。
3. 真正符合原文的做法是**在中枢继续延伸时改用 `[DD,GG]` 不重叠作为收口条件**
   （即趋势继续则中枢重切），这是 `pivot.py::find_pivots` 的改动，牵动
   `pivots 56`、`signals 43` 的全部下游，不是一句谓词。
   我量了另一条路：把 `pivot.py::merge_pivots` 的 `_pivot_overlap` 从
   `[ZD,ZG]` 换成 `[DD,GG]`，中枢 40 → 23（16 只票 2→1，2 只票 3→2，
   level 变成 `week`），级别会大面积升级；但 `merge_pivots` **在主管道里根本没被
   调用**（`find_pivots` 直接返回一级中枢），所以走这条路等于新接一段管道。

**候选补丁**：`optimizer/patches/round-011-G11a.patch`（`kind: theory`，38 行，
只改 `src/chanlun/chan/trend.py`）

```
# kind: theory
# theory: L20-TREND-NO-OVERLAP,L17-TREND-DEF
# quote: 而在趋势里，同级别的前后缠中说禅走势中枢是不能有任何重叠的，这包括任何围绕走势中枢产生的任何瞬间波动之间的重叠。
# evidence: cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_g11_trend_overlap.py  # 19/19 ...
```

**处置建议：继续观察（倾向证伪当前形态的补丁）**。补丁本身通过
`validate_patch`（`ok=True`），但它把 `trends_up/down` 打成 0，**不能采纳**。
本轮把它留作「判据与原文的差距」的物证，真正的修复需要重切中枢，属于
`## 本轮无结论 / 需要人决策的点` 第 1 条。

---

## 观测点 G11b: 中枢延伸循环完全没有段数上限（实测最大 14 段），第 33 课把收口数字写成了「延伸不超过 5 段」，即本级别中枢上限 8 段

**判据**（第 33 课《走势的多义性》，逐字）：

> 例如，5分钟级别的中枢不断延伸，出现9段以上的1分钟次级别走势。站在30分钟级别的中枢角度，3个5分钟级别的走势重合就形成了，而9段以上的1分钟次级别走势，每3段构成一个5分钟的中枢，这样也就可以解释成这是一个30分钟的中枢。这种情况，只要对中枢延伸的数量进行限制，就可以消除多义性，一般来说，中枢的延伸不能超过5段，也就是一旦出现6段的延伸，加上形成中枢本身那三段，就构成更大级别的中枢了。

theory 条目：`optimizer/theory/L33-PIVOT-EXTENSION-LIMIT.md`。原文把
「形成中枢本身那三段」和「延伸段」分开计数：延伸 ≤ 5，延伸到 6 段（6+3=9）
就已经是更大级别的中枢 → **本级别中枢最多 8 段**。

**现状**：`src/chanlun/chan/pivot.py:131-133`

```python
        j = i + MIN_SEGMENTS
        while j < n and _overlaps(confirmed[j], zd, zg):
            j += 1
```

`MIN_SEGMENTS = 3`（`pivot.py:50`）。延伸循环只有「还重叠就继续吃」，
**没有任何段数上限**，`j` 可以一直涨到 `n`。第 33 课点名要限制的那个量，
主干里根本不存在。

**证据**：

```bash
cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
    -m chanlun.optimizer.cli --root . --audit --patch optimizer/patches/round-012-G11b.patch
```

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| `pivot_max_segments` | 14 | **8** |
| `pivot_over_9` | 8 | **0** |
| `pivot_segment_total` | 300 | 297 |
| `pivots` | 56 | 61 |
| `trends_consolidation` | 6 | 11 |
| `signals` | 43 | 44 |
| `b1` | 0 | **2** |
| `b2` | 0 | **2** |
| `b3` | 26 | 24 |
| `s3` | 16 | 15 |
| `signal_problems` | 0 | 0 |

两个附带结论：

1. 这是本项目**第一次**跑出 `b1`/`b2`（第 10 轮基线 `b1 0 b2 0`，
   与「走势终完美 ⇒ 背驰应产生第一类买卖点」矛盾——见末节）。原因是
   14 段的巨型中枢被切成 8 段之后，出现了段数更短、`_first_kind` 能配对的趋势组。
2. 与第 4 轮 `G2a` 冲突。G2a 的提案是**上限 9**，其 theory 依据是第 29 课
   「一个 30 分钟盘整至少有 9 个 1 分钟走势类型」，引的是「升级后的最小内容量」；
   第 33 课这句引的是「本级别的收口上限」。两处原文说的是同一件事的两面，
   但**直接给本级别中枢设上限的是第 33 课这句**，所以数字应当取 8。我在第 4 轮
   量过 cap 9（`pivots 60, signals 39, b3 23, s3 15, trend_group_both_ok 1`），
   本轮量过 cap 8（上表）。cap 9 没有产生任何 `b1/b2`，cap 8 产生了 2+2。

**风险**：

1. **与 G2a 直接冲突**，两条提案不能同时落地；若先落 G2a 的 9，再落本条就是
   改数字。需要人在 8 与 9 之间裁决一次，并把败方条目里的话说清楚。
2. 上限一旦生效，中枢数量 56 → 61（+9%），`pivots` 的 `idx` 全部位移，
   `object_id`（`state.py::object_id` = `<typename>@<src_start>-<src_end>`）随之
   变化，**已入库的历史 `confirmed_at` 记录会大面积失配**。这是最实际的风险。
3. 8 段这个数字是从「延伸不能超过 5 段」推出来的，原文没有直接写「8」；
   如果后面发现「形成中枢本身那三段」在某些走势里不是 3 段，数字要重算。

**候选补丁**：`optimizer/patches/round-012-G11b.patch`（`kind: theory`，37 行，
只改 `src/chanlun/chan/pivot.py`，加 `MAX_SEGMENTS = 8` 并让延伸循环
`while j < n and j - i < MAX_SEGMENTS and _overlaps(...)`）

**处置建议：采纳（但需先与 G2a 二选一）**。它是本轮唯一「有原文直接数字依据 +
能修好一个已承认的缺陷（无上限）+ 顺带产出第一批 `b1/b2`」的改动。

---

## 观测点 G11c: `GG`/`DD` 遍历的是中枢组里的**全部**线段，第 20 课定义的是只遍历 Z 走势段；偶数段中枢会把反向离开段的极值算进波动区间，把 `[DD,GG]` 撑宽

**判据**（第 20 课，逐字）：

> 为方便起见，以后都把这些与中枢方向一致的次级别走势类型称为Z走势段，按中枢中的时间顺序，分别记为Zn等，而相应的高、低点分别记为gn、dn，定义四个指标,GG=max(gn),G=min(gn),D=max(dn),DD=min(dn)，n遍历中枢中所有Zn。

`n` 遍历的是 **Z 走势段**，不是中枢里的全部线段。

**现状**：`src/chanlun/chan/pivot.py:142-143`

```python
            gg=max(s.high for s in group),
            dd=min(s.low for s in group),
```

`group = confirmed[i:j]`（`pivot.py:135`）是中枢组里的**全部**线段，其中
最后一段是反向的离开段。`group` 的段数奇偶决定收尾方向：奇数段以同向段收尾
（Z 段就是全部同向段），**偶数段以反向段收尾**，那一段的极值被多算进
`[DD,GG]`。

**证据**：

```bash
cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
    optimizer/tools/measure_g11_dd_gg_scope.py
```

输出：

```
确认中枢总数                     : 40
全段口径 与 Z 段口径不同的个数    : 9
差异是否只出现在偶数段中枢上      : True（偶数段中枢以反向离开段收尾）
偏差最大的一个                   : 002594 P1 n=10 全段 DD/GG=45.46/136.84 -> Z段 DD/GG=45.46/115.79
-- 反例 --
  600030 P0 n=4 全段 DD/GG=16.03/27.92 -> Z段 DD/GG=16.89/27.92
  600900 P1 n=4 全段 DD/GG=17.31/23.40 -> Z段 DD/GG=17.31/21.44
  601088 P1 n=4 全段 DD/GG=21.03/42.80 -> Z段 DD/GG=21.03/26.21
  601899 P1 n=6 全段 DD/GG=6.83/18.50 -> Z段 DD/GG=6.83/13.30
  000333 P0 n=6 全段 DD/GG=33.02/84.54 -> Z段 DD/GG=35.29/84.54
  000725 P0 n=4 全段 DD/GG=2.97/6.82 -> Z段 DD/GG=2.97/5.19
  （另 3 个：002594 P1 n=10、300750 P1 n=4、300760 P0 n=8）
== M11e 结构不变量 ==
主干 validate_pivots 问题数       : 0
Z 段口径 validate_pivots 问题数   : 0
```

40 个确认中枢里 9 个（22.5%）口径不同，全部是偶数段中枢；偏差最大的是
002594 P1 的 `GG 136.84 → 115.79`（−15.4%），601088 P1 的
`GG 42.80 → 26.21`（−38.7%）。

`--audit --patch optimizer/patches/round-013-G11c.patch`：**全部指标零变化**
（`pivots 56, trends_up 10, trends_down 7, trends_consolidation 6,
signals 43, b1 0, b2 0, b3 26, s1 1, s3 16, trend_groups 17,
pivot_max_segments 14, pivot_over_9 8, pivot_segment_total 300`）。
原因是主管道的决策路径（`_direction`、`_third_kind`、`_first_kind`）只用
`ZD/ZG/GG/DD` 里的 `ZD/ZG`，`GG/DD` 目前只被三个地方消费：

1. **网页**「中枢」行直接印给用户看。600030 会从
   `GG 34.95 / DD 22.97` 之外的那一项变化——量具打出的同一只票
   `P0` 从 `DD 16.03` 变成 `16.89`；
2. `pivot.py::validate_pivots` 的不变量 `dd <= zd < zg <= gg`（改后问题数
   仍是 0，说明不会弄坏校验）；
3. 将来的 G11a（定理二判据）——但上面 2×2 矩阵的 C/D 两列已经证明，
   就算修好 DD/GG，定理二判据依然救不回来。

**风险**：

1. 对买卖点、走势类型**零影响**，所以风险极低；反过来也意味着**收益只体现在
   显示层和校验层**，不要指望它提高信号质量。
2. `[DD,GG]` 变窄之后，如果将来 `merge_pivots` 被接进管道并用 `[DD,GG]`
   判级别扩张，合并结果会比现在少（`[ZD,ZG]` 口径下 40 → 40 不合并；
   `[DD,GG]` 口径下 40 → 23）——那是另一条提案的事，本条不改 `_pivot_overlap`。
3. 边界：如果某个中枢组的全部线段都是同向（不可能，但代码没断言），
   `zs` 会是全集，与现状一致，不会崩。

**候选补丁**：`optimizer/patches/round-013-G11c.patch`（`kind: theory`，31 行，
只改 `src/chanlun/chan/pivot.py`，把 `gg/dd` 改成
`zs = [s for s in group if s.direction == group[0].direction]` 上的极值，
并加 6 行第 20 课注释）

**处置建议：采纳**。口径与原文一致、`validate_pivots` 仍全过、对信号零影响，
是本轮最干净的一处「口径修正」。

---

## 观测点 G11d: `optimizer/theory.py::ALGO_PREFIXES` 把整个 `src/chanlun/web/` 当算法路径，连 `web/static/app.js` 这种纯展示层也算，于是任何 UI 修正都必须先伪造一条 theory 条目

**判据**：工程口径（`optimizer/theory.py::validate_patch` 的准入规则本身，
无对应理论条目）。

**现状**：`src/chanlun/optimizer/theory.py`

```python
ALGO_PREFIXES: tuple[str, ...] = (
    "src/chanlun/chan/",
    "src/chanlun/scan/",
    "src/chanlun/web/",
    "src/chanlun/backtest/",
)
```

`src/chanlun/web/` 下有 7 个文件（不计 `__pycache__`），其中 4 个是纯静态资源
（`static/app.js`、`static/index.html`、`static/styles.css`、
`static/vendor/echarts.min.js`）。`validate_patch` 对 `is_algo_path` 的路径
一律要求 `kind: theory` + 存在的 `theory:` id + 逐字 `quote:`。后果是
**改一句 UI 文案或补一个「未确认」标记，会被要求先伪造一条缠论理论条目**，
否则拒绝。本轮就撞上了（见 G11e）。

**证据**：

```bash
cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun.optimizer.cli \
    --root . --audit --patch optimizer/patches/round-014-G11d.patch     # signals 43->43, pivots 56->56
```

真正要看的是 `validate_patch` 的判决翻转（脚本在内存里换 `ALGO_PREFIXES`，
对同一份 `optimizer/patches/round-015-G11e.patch` 判两次）：

```
改前 ALGO_PREFIXES: ok=False
  reasons=('改动缠论算法（src/chanlun/web/static/app.js）必须以 `# kind: theory` 提出——工程类补丁不得触碰主干算法。',
           '改动缠论算法必须引用 theory/ 条目 id（补丁头缺 `# theory: <条目 id>`）…',
           '改动缠论算法必须附 `# quote: <原文摘录>`…')
改后 ALGO_PREFIXES: ok=True kind=engineering reasons=()
```

注意一个**不一致**：`optimizer/tools/make_patch.py` 用 `--kind engineering`
给 `web/static/app.js` 写补丁时**不报错**（照写），只有 `validate_patch`
在验收环节拒绝。也就是说这个约束在「写」和「收」两端不对称。

**风险**：

1. 放宽 `ALGO_PREFIXES` 会不会让人绕开理论约束去改真正的算法？不会——`chan/`、
   `scan/`、`backtest/` 三个前缀不动，服务端 `web/api.py`、`web/app.py`
   （决定 level / 复权 / 确认口径怎么暴露）仍保留在算法路径里。
2. 如果将来有人在 `web/static/` 里塞进缠论计算逻辑（比如前端自己算中枢），
   放宽后就不会被拦。需要一条明确的边界约定：**`static/` 只做展示**。
3. 只改常量、不动逻辑，`--audit` 全部指标零变化，回归风险为零。

**候选补丁**：`optimizer/patches/round-014-G11d.patch`（`kind: engineering`，22 行，
只改 `src/chanlun/optimizer/theory.py`，把 `"src/chanlun/web/"` 拆成
`"src/chanlun/web/api.py"` + `"src/chanlun/web/app.py"`）

**处置建议：采纳**。它是让 G11e 能合法落地的前置条件，本身零风险。

---

## 观测点 G11e: 自选股行把**未确认**的买卖点显示得和已确认的一模一样，页面免责声明只提了「笔画与中枢」

**判据**：工程口径（展示层；`app.js:4-6` 的模块注释自己写着
「买卖点 —— 一/二/三类买卖点，实心=已确认，空心=未确认」，但自选股行没有照做）。

**现状**：`src/chanlun/web/static/app.js:739-742`

```js
      const sigs = it.signals || [];
      if (sigs.length) {
        row.classList.add("has-signal");
        const last = sigs[sigs.length - 1];
        const tag = span("watch-sig", `${KIND_CN[last.kind] || last.kind} ${last.ts}`);
```

只印 `kind + ts`，`last.status` 完全没用。同一行的中枢部分却标了
（`app.js:734`：`piv.status === "tentative" ? "未确认 " : ""`），
`app.js:736` 的末段也标了。

**证据**：

```bash
cd chanlun && curl -s 'http://127.0.0.1:8888/api/watchlist/structure'
```

4 只自选股的实际载荷：

| 代码 | 印出来的中枢 | 印出来的买卖点 | 买卖点真实状态 |
| --- | --- | --- | --- |
| 600030 | 未确认 23.39–31.68 | 三买 2025-04-07 | confirmed（确认 2025-08-04） |
| 600519 | 未确认 1370.03–1565.26 | （无） | — |
| 000002 | 未确认 15.69–20.68 | 三卖 2026-09-23 | **tentative，confirmed_at=null** |
| 601398 | 3.40–3.86 | 三买 2025-01-22 | confirmed（确认 2025-04-11） |

两个具体问题：

1. **000002 的「三卖 2026-09-23」是未确认的**（`status: "tentative"`,
   `confirmed_at: null`），自选股行**没有任何标记**。同一份数据在中栏 ledger
   里是有「未确认」chip 的（`app.js:25` 的 `STATUS_CN`），两个视图不一致。
   用户会把它当成已确认的三卖。页面免责声明只说了「虚线/半透明表示尚未确认」
   并只点名笔画与中枢，**没提买卖点**。
2. **600030 行印的中枢和买卖点属于不同的中枢**：`中枢 未确认 23.39–31.68`
   是 `pivots[2]`（2024-11-08 → 2026-04-07，tentative），而
   `三买 2025-04-07 @ 22.97` 属于 `pivots[1]`（ZG=20.25，2022-04-27 →
   2024-11-08）。**22.97 < 23.39**，即印出来的买点价格低于所印中枢的下沿——
   一个缠论读者读到「中枢 23.39–31.68 + 三买」会立刻觉得自相矛盾。
   601398 同理：`三买 @ 6.02` 与所印中枢 `3.40–3.86` 相差 +56%。

**风险**：

1. 只加一个后缀，不改任何计算；`--audit` 全部指标零变化。
2. **当前状态是「写了也收不了」**：`optimizer/patches/round-015-G11e.patch` 现在被
   `validate_patch` 判 `ok=False`（3 条 reasons，见 G11d 的证据）。
   必须先落 G11d，或者给它伪造一条 theory 条目——后者是本轮明确不建议的。
3. 第 2 个问题（中枢与买卖点错配）本条补丁**不解决**，只是把它记下来：
   要修得给自选股行补上「该买卖点所属中枢」的索引，那是另一个改动。

**候选补丁**：`optimizer/patches/round-015-G11e.patch`（`kind: engineering`，20 行，
只改 `src/chanlun/web/static/app.js`，给 `watch-sig` 补
`last.status === "tentative" ? "（未确认）" : ""`）

**处置建议：采纳（依赖 G11d 先落地）**。文案级改动，风险为零，直接消除一处
会误导用户的显示。

---

## 本轮无结论 / 需要人决策的点

1. **趋势判据与原文的差距要不要动、怎么动（G11a）——本轮最大的悬空项。**
   第 20 课定理二在主干里**结构性不可达**：`find_pivots` 用 `i = j` 跳到
   第一个与 `[ZD,ZG]` 不重叠的线段，而 `dd/gg` 又包含自己那一组的越界段，
   所以 `后DD > 前GG` 永远不成立。只改 `_direction` 会让
   `trends_up/down 17 → 0`（19/19 对的中枢波动区间全部重叠）。
   要符合原文，必须在「趋势继续」时改用 `[DD,GG]` 重切中枢——这是
   `pivot.py::find_pivots` 的算法改动，会重排全部 `pivots`（`pivots 56` 起）
   与 `object_id`，进而让已入库的 `confirmed_at` 历史大面积失配。
   **建议：本轮不采纳，先由人决定「要不要接受一次全量重切」**；
   在那之前，建议至少在网页/导出里把「趋势」这个标签标成「按 `[ZD,ZG]`
   口径」而不是无条件的「趋势」。
2. **中枢延伸上限取 8 还是 9（G11b vs 第 4 轮 G2a）。** 第 33 课
   「延伸不能超过5段，也就是一旦出现6段的延伸，加上形成中枢本身那三段，
   就构成更大级别的中枢」→ 8；第 29 课「至少 9 个 1 分钟走势类型」→ 9。
   本轮按原文取 8，理由是**直接给本级别中枢设上限的是第 33 课那句**，
   而第 29 课的 9 说的是升级后的最小内容量。cap 8 还是 cap 9 影响实测结果：
   cap 8 首次产出 `b1 2 / b2 2`，cap 9 没有。**建议：采纳 8，并把 G2a 的
   条目改写为「9 是升级后的最小内容量，不是本级别上限」，避免两条提案打架。**
3. **`b1`/`b2` 长期为 0 这件事本身需要人看一眼。** 本轮基线
   `signals 43 = b3 26 + s3 16 + s1 1`，`b1 0`、`b2 0`。但系统同时报出
   17 个非盘整趋势组、`trend_group_leave_dir_ok 13`——按第 17 课
   「走势终完美」，趋势结束处应当有背驰、应当有第一类买卖点。
   本轮把根因定位到了 `signal.py::_first_kind`：基线的 17 个非盘整趋势
   **全部只有 2 个中枢**（`t.start_idx..t.end_idx` 跨度 = 2），
   于是 `_entering_and_leaving` 只为**后一个**中枢返回 `(p, leave, prev)`，
   `prev` 是**前一个**中枢的离开段，而 `leave`（后一个中枢的离开段）与
   `prev` 比较时永远创不出新极值 → 0/0（唯一的 `s1` 是 600900 2024-02-20
   @ 23.39540068）。**根本问题：2 个中枢的趋势里，把「第 2 个中枢的离开段」
   和「第 1 个中枢的离开段」比，比错了对象**——背驰应当比「趋势的最后一段」
   与「进入段/上一同向走势」。这一条本轮只做到定位，没有出补丁（它会牵动
   `s1 1` 起的所有一/二类买卖点），**留给下一轮或人来定方向**。
4. **自选股行「中枢区间」与「买卖点」错配（G11e 第 2 点）。** 两行数据来自
   不同中枢、甚至价格与中枢下沿矛盾（600030 `三买 @22.97` vs
   `中枢 23.39–31.68`），且行内**不印中枢的日期区间**（601398 印
   `中枢 3.40–3.86` 而现价 8.28，读者无法知道那是 2018–2024 年的老中枢）。
   需要人决定展示口径：是只显示最后一个中枢，还是同时显示该信号所属的中枢
   及其日期。本轮只记录，未出补丁。
5. **`make_patch.py` 与 `validate_patch` 在算法路径上的不对称（G11d 附带）。**
   前者允许写出 `kind: engineering` 的 `web/static/` 补丁，后者在验收时拒绝。
   本轮只修了 `ALGO_PREFIXES` 这一侧；是否要在 `make_patch.py` 里加同一套
   校验（让「写」的时候就报错）留给下一轮决定。

---

## 本轮产出清单

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `optimizer/patches/round-011-G11a.patch` | theory | `trend.py::_direction` 改用第 20 课定理二 `[DD,GG]` |
| `optimizer/patches/round-012-G11b.patch` | theory | `pivot.py` 中枢延伸上限 8（第 33 课） |
| `optimizer/patches/round-013-G11c.patch` | theory | `pivot.py` `GG/DD` 只遍历 Z 走势段（第 20 课） |
| `optimizer/patches/round-014-G11d.patch` | engineering | `theory.py::ALGO_PREFIXES` 拆出 `web/static/` |
| `optimizer/patches/round-015-G11e.patch` | engineering | `app.js` 自选股行给未确认买卖点加标记（依赖 G11d） |
| `optimizer/theory/L20-TREND-NO-OVERLAP.md` | theory 条目 | 新建（第 20 课，原文逐字） |
| `optimizer/theory/L33-PIVOT-EXTENSION-LIMIT.md` | theory 条目 | 新建（第 33 课，原文逐字） |
| `optimizer/tools/measure_g11_dd_gg_scope.py` | 只读量具 | G11c 的复现命令 |

`validate_patch` 判决：G11a `ok=True`、G11b `ok=True`、G11c `ok=True`、
G11d `ok=True`、G11e `ok=False`（预期，见 G11d）。

**`chanlun/src/` 未被修改**：`git status --porcelain -- chanlun/src` 输出 0 行。

---

# 附：回访入账（2026-10-01 17:30）

本节记录「取证报告 → 入账」这一步改了什么，以及**报告里被回访推翻的两处结论**。
正文保持取证当时的原样，只把 `/tmp/round-011-*.patch` 换成了最终入库路径。

## 1. 轮次与文件名

轮次号 = 观测点在 `PROBE_SPECS` 里的序号，补丁名 = `round-{轮次:03d}-{RID}.patch`：

| 轮次 | 观测点 | 补丁 | journal |
| --- | --- | --- | --- |
| 011 | G11a | `optimizer/patches/round-011-G11a.patch` | `round-011.json`（proposed） |
| 012 | G11b | `optimizer/patches/round-012-G11b.patch` | `round-012.json`（proposed） |
| 013 | G11c | `optimizer/patches/round-013-G11c.patch` | `round-013.json`（proposed） |
| 014 | G11d | `optimizer/patches/round-014-G11d.patch`（`# status: adopted`） | 无（已采纳不占 journal 轮次，但轮次号照样占位） |
| 015 | G11e | `optimizer/patches/round-015-G11e.patch` | `round-015.json`（inconclusive） |

`G11a`/`G11c` 挂上各自的只读量具（`PROBE_SCRIPTS`），`G11b`/`G11e` 用默认流水线审计。

## 2. 回访推翻了报告里的两处结论

**（1）G11c 不是「零风险」。** 报告依据 `--audit` 全指标零变化，判它「口径与原文一致、
`validate_pivots` 仍全过、对信号零影响，是本轮最干净的一处口径修正」。回访把补丁单独
apply 进主干再跑全套测试：**4 个用例失败** —— `tests/chan/test_pivot.py::test_gg_dd_are_the_full_range`、
`::test_tentative_tail_does_not_join_an_existing_pivot`、
`::test_leading_tentative_segment_does_not_shift_a_later_pivot`、
`::test_merge_overlapping_same_level_pivots`。第一个用例的名字就钉在旧口径上
（「GG/DD 是全部线段的范围」）。也就是说：**`--audit` 看不见的东西（`DD/GG` 的取值本身）
被常驻回归看见了**；采纳 G11c 必须连同这 4 个用例的判据一起改。

**（2）G11a 的 before/after 不是同名比较。** 它的量具把两个口径分别叫 `old`（主干当前口径）
与 `new`（定理二口径）；补丁打上之后 `old` 就**变成**定理二口径了。所以 journal 里
`before.old`（涨 10 / 跌 7 / 盘整 6）与 `after.old`（涨 0 / 跌 0 / 盘整 40）是**两个不同口径下的
同一个键**，不能按「键名相同就直接相减」读；真正该看的是 `before.new` vs `after.old`。
补丁单独 apply 后 **10 个用例失败**（`tests/chan/test_trend.py` 7 个 + `tests/chan/test_signal.py` 3 个，
见 `round-011.json` 的 `tests.failed_tests`），它们钉的正是「两个中枢抬高就算上涨趋势」这个旧判据。

## 3. 打补丁后主干测试的结果（本轮新增的一列证据）

| 补丁 | 单独 apply 后跑全套 | 结论 |
| --- | --- | --- |
| `round-011-G11a` | 10 failed, 685 passed | 会改判据，需人决定 |
| `round-012-G11b` | **695 passed（rc=0）** | 本轮唯一「打上就全绿」的候选 |
| `round-013-G11c` | 4 failed, 691 passed | 会改判据（4 个用例钉旧口径） |
| `round-014-G11d` | 已采纳 → 跳过 | 判据在常驻回归里 |
| `round-015-G11e` | 695 passed（rc=0） | 页面文案，审计看不见 → inconclusive |

## 4. 判据条目与量具的两处修正

- `L20-PIVOT-DEFINITION.md` 补上第 20 课 Z 走势段那段原文
  （`GG=max(gn),G=min(gn),D=max(dn),DD=min(dn)，n遍历中枢中所有Zn`），并在释义里写明
  `[DD, GG]` **不含**反向那一段。`round-013-G11c.patch` 原来引的是定理二那句
  「同级别的前后中枢不能有任何重叠」——那是**趋势**的判据，不是 `GG/DD` 的判据；引错段落
  照样能过 `validate_patch`（它只查「quote 是不是条目里的逐字子串」），已改指本条。
- 24x7 回访是 `python -c <脚本文本>` 跑量具（没有 `__file__`，cwd 是影子根），所以两个
  G11 量具的 `ROOT = Path(__file__).resolve().parents[2]` 改成 `try/except NameError`
  回退 `Path.cwd()`；人手动跑仍走 `__file__` 那条。

---

# 附二：裁决与落地（2026-10-01 18:5x）

附一记的是「取证报告 → 入账」；本节记的是**人裁决之后**的三件事：G11b 的 8 落地、
G2a 因此作废、以及 G11e 的采纳。三件都已进主干（`40a05d3`、`335b3e9`、`3aad8e7`），
本节给出落地后重跑量具的读数。

## 1. 中枢延伸上限：取 8（采纳 G11b）

第 11 轮把「8 还是 9」摆到人面前（G11b 的 8 与第 4 轮 G2a 的 9 直接冲突）。裁决：
**「采纳 8（推荐）」**。理由是原文归属：直接给**本级别**中枢设上限的是第 33 课那句
（延伸 ≤ 5，延伸到 6 段即 6+3=9 已构成更大级别中枢），而第 29 课的 9 说的是
**升级后**那个更大级别中枢的最小内容量 —— 两句话都在，但管「本级别收口」的是前一句。

落地形态（`src/chanlun/chan/pivot.py`）：

```python
MAX_SEGMENTS = 8                      # 第 33 课：延伸不超过 5 段，加形成中枢的 3 段
j = i + MIN_SEGMENTS
while (j < n and j - i < MAX_SEGMENTS
       and _overlaps(confirmed[j], zd, zg)):
    j += 1
```

**落地后**在主干上重跑审计（不挂补丁）：

```bash
cd chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun.optimizer.cli --root . --audit
```

| 指标 | 改前（第 11 轮报告） | 落地后（主干实测） |
| --- | --- | --- |
| `pivot_max_segments` | 14 | **8** |
| `pivot_over_9` | 8 | **0** |
| `pivot_segment_total` | 300 | 297 |
| `pivots` | 56 | 61 |
| `trends_consolidation` | 6 | 11 |
| `signals` | 43 | 44 |
| `b1` / `b2` | 0 / 0 | **2 / 2** |
| `b3` / `s3` | 26 / 16 | 24 / 15 |
| `trends_up` / `trends_down` | 10 / 7 | 10 / 7 |
| `signal_problems` | 0 | 0 |

`pivot_max_segments 8` + `pivot_over_9 0` 说明上限真的生效了（不是只加了个没人读的常量）；
`b1 2 / b2 2` 是项目**第一次**跑出第一/第二类买点 —— 14 段的巨型中枢被切到 8 段之后，
才出现段数足够短、`_first_kind` 能配对的趋势组。

**风险 2 的处置**：报告担心的「`pivots` 的 `idx` 全体位移 ⇒ 已入库的 `confirmed_at`
大面积失配」在本项目的现实里不成立 —— `confirmed_at` 是**当下算出来的**（引擎每次
全量/增量重算），没有一份独立的历史记录会被位移打歪。真正受影响的是页面上的编号，
那是显示层，本来就跟着划分走。

## 2. G2a 作废（不是「删掉」，是留档）

`optimizer/patches/round-004-G2a.patch` 的补丁头改成：

```
# kind: theory
# status: retired
# note: [前提被第 11 轮 G11b 证伪，2026-10-01] 原 finding 取「9 段」作本级别上限，…
#       已按 cap 8 落地（见 round-012-G11b.patch 的 adopted_note），本提案作废。
```

**retired ≠ adopted**，两者在 journal 里也不是一回事：`retired` 的 journal 状态是
`REJECTED`（提案的**前提**被实测证伪，只作证据留档），`adopted` 才是「已经是主干的一部分」。
所以 `round-004.json` 现在写的是 REJECTED，而不是「已采纳」。
改补丁**文件名**会挪动轮次号（轮次 = `PROBE_SPECS` 里的序号），所以只改头部。

## 3. 工具：证伪的补丁也要跳过复核

`run_patch_tests.py` 原来只跳过 `# status: adopted`。G2a 作废之后暴露出一个洞：
**一条前提已被证伪的补丁仍然能 apply 上去**（它改的那几行还在），于是它会被当成
「一个待评审的候选」重新跑一遍全套测试 —— 而它量的是一个已经作废的口径。

现在两条跳过理由分开打印，不混成一句：

```
round-001-G1a.patch: 已采纳 → 跳过（判据在常驻回归里，apply 会被 git 静默跳过，不算通过）
round-003-G1c.patch: 已证伪 → 跳过（只作证据留档，前提不成立，主干已往前走）
```

判据在 `run_patch_tests.py::_is_retired`，`record_rounds.py` 里也补了对应分支。
`tests/optimizer/test_optimizer.py` 加了用例钉住「retired 被跳过且理由不同」。

## 4. G11e 采纳

`round-015-G11e.patch`（自选股行里**未确认**的买卖点补「（未确认）」）在附一的表里
是 `inconclusive`（页面文案，审计看不见）。它后来被采纳：Task 29 重写 `watchRow` 时
那段 hunk 落在同一行上，已进主干，于是补丁头补上 `# status: adopted` /
`# adopted_at: 2026-10-01`。

顺带把这条**并入** Task 29 的改动一起交付（`3aad8e7`），因此它的 hunk 现在与主干
不再逐字一致 —— 再 apply 会失败，但按 adopted 的规矩它本来就不该再 apply。

## 5. 落地后重跑一遍全部补丁（本轮新增的一列证据）

```bash
cd chanlun && ../.venv-chanlun/bin/python optimizer/tools/run_patch_tests.py
```

| 补丁 | 结果 | 说明 |
| --- | --- | --- |
| `round-001-G1a` / `002-G1b` / `009-G5a` / `010-G5b` | 已采纳 → 跳过 | 判据在常驻回归里 |
| `round-003-G1c` / `004-G2a` | 已证伪 → 跳过 | 只作证据留档 |
| `round-005-G2b-candidate` | 1 failed, 752 passed | 会改判据，仍待裁决 |
| `round-006-G3a` | 2 failed, 751 passed | 会改判据，仍待裁决 |
| `round-011-G11a` | 10 failed, 743 passed | 趋势判据，仍待裁决 |
| `round-013-G11c` | 4 failed, 749 passed | `GG/DD` 口径，仍待裁决 |
| `round-012-G11b` / `014-G11d` / `015-G11e` | 已采纳 → 跳过 | 已在主干 |
| 反 apply | 触及文件逐字复原=True | 没有一个待评审补丁是「空改」 |

主干基线（同一次运行打印）：**754 passed / 12 deselected**（`3aad8e7` 之前）；
Task 29 的复权顺序钉加进来之后是 **755 passed / 12 deselected**。
`patch_test_results.json` 已按本次运行重写。

## 6. 冻结口径的 sha 变了，原因是 `meta.db`

审计的 `basis.sha256` 从第 11 轮的 `044d4d17bfdc7e69` 变成 `3b8cc6ca507d1038`，
`copied/expected` 仍是 **24/24**、`missing` 为空。这不是数据缺了：冻结快照里除了
24 只票的日线 parquet，还冻了 `quality_report.json` 与 **`meta.db`**，
而 Task 29 恰好改了 `meta.db`（自选池换成七个指数、`watchlist` 加 `sort_order`、
指数的 `sync_state`）。24 只票的行情字节没动，动的是「代码清单的来源」。
