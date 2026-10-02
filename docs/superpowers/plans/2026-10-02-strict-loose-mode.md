# 严格/非严格口径切换 + 背驰标注 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在看盘页加一个「严格 / 非严格」口径切换按钮，非严格模式放宽买卖点判据并标注背驰（区分趋势背驰 / 盘整背驰），实测胜率目标 70%~80%。

**Architecture:** 新增正交维度 `SignalMode`，只作用于**买卖点与背驰标注**，笔/线段/中枢划分口径一律不动。背驰判定独立成 `chan/divergence.py`，结果作为 `Snapshot.divergences` 派生字段（不进 `state._FIELDS`）。同时修掉一个 P0 前置缺陷：回测触发判据把「结构自身的确认完成时刻」当成「引擎首次可见时刻」，导致 0 笔成交。

**Tech Stack:** Python 3.11+（frozen dataclass / `enum.Enum` / 纯函数）、pandas、FastAPI + uvicorn、原生 JS + ECharts、pytest。

**设计文档：** `docs/superpowers/specs/2026-10-02-strict-loose-mode-design.md`（已获用户批准）

## Global Constraints

- **作用域硬约束：严格 / 非严格只影响买卖点与背驰标注。笔、线段、中枢的划分口径完全不动** —— 不得触碰 `src/chanlun/chan/segment.py`、`pivot.py`、`include.py`、`fractal.py` 的判据。严格模式的买卖点结果必须与改动前**逐项相同**。
- **原文空白项必须显式标注。** 第三类买卖点的「容忍度」在第 020 / 024 / 032 / 033 课中一致被反对（「不跌破」「不重新回到」），**无任何原文依据**。代码注释、理论条目、UI 文案都必须写「工程口径，无原文依据」，**不得**表述为缠论判据。
- **盘整背驰不得叫第一类买卖点。** 第 060 课《图解分析示范五》：「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」。`SignalKind.PB` 的 `name_cn` 固定为「盘整背驰买点（类第一类）」—— 「类第一类」是第 027 课第 7 段原文自己的限定措辞，不是无定语的第一类买点，故不违反本禁令。
- **「背驰」不带定语时专指趋势背驰，盘整背驰不是背驰。** 第 015 课《没有趋势，没有背驰。》：「必须注意：没有趋势，没有背驰。**在盘整中是无所谓"背驰"的**，这点是必须特别明确的。」第 037 课：「这样，是不存在背驰的，**最多就是盘整背驰**。」⇒ 代码注释、理论条目、UI 文案都**不得**把盘整背驰表述为「一种背驰」或「趋势背驰的弱化版」；两者判据并列、**语义不对等**。
- **第一类买卖点两种模式行为完全一致。** 第 037 课《背驰的再分辨》：「没有趋势，没有背驰」——不得把「一个中枢 + 新极值」当趋势背驰。盘整背驰也**不得**成为放宽第一类的理由（第 015 / 037 课）。
- **每次判据变更前，先在 `optimizer/theory/` 写理论条目并逐字摘录原文**（AGENTS.md §5）。不允许改写或编造原文摘录。
- **ARCHITECTURE.md 的 `D-xx` 编号永不复用、永不删除。** 本计划占用 `D-32` / `D-33` / `D-34` / `D-35`。
- **版本号只在 `pyproject.toml` 的 `[project].version` 写一次。** 本次为 `MINOR`（判据变更按破坏性变更处理，见 AGENTS.md §2）。**本计划不执行发版**，只往 `CHANGELOG.md` 的 `[Unreleased]` 追加条目，并注明「本节改动尚未提交」。
- **绝不 `git add -A`，绝不 `git add chanlun/`。** 逐个列路径提交。**绝不 `git checkout -- <file>`。**
- 提交信息用中文 Conventional Commits（`feat(chan):` / `fix(backtest):` / `docs:`）。
- 跑测试必须 `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest`。跑 CLI 用 `PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun ...`。macOS **没有 `timeout` 命令**。
- **改了 `src/chanlun/**/*.py` 要重启看盘页**（`web/app.py` 用 `uvicorn.run(...)` 且没有 `--reload`）；只改 `web/static/` 下的文件刷新浏览器即可。
- **UI 文案必须从真实浏览器 DOM dump 里读**，不许看截图。
- **不要运行 `optimizer/tools/run_patch_tests.py` / `record_rounds.py`，不要起新一轮。**（AGENTS.md §5「优化器的状态」）

### 对设计文档的一处实测更正（执行时以本节为准）

设计文档 §4.2 把「放宽第三类『必须是第一次』」列为非严格模式的一项放宽。**实测该放宽是空操作，本计划不实现它。**

理由：`src/chanlun/chan/pivot.py:168-173` 的中枢延伸循环
`while j < n and j - i < MAX_SEGMENTS and _overlaps(confirmed[j], zd, zg)`
保证中枢**在第一个完全不碰 `[zd, zg]` 的段处封闭**，所以 `segs[p.end_idx + 1]`
（`signal.py:167` 唯一取用的回试段）**必然就是**第一次回抽 —— 结构上取不到第二次，
没有可放宽的余地。（唯一例外是 `MAX_SEGMENTS = 8` 上限触发时，属中枢扩展范畴，本轮不纳入。）

⇒ **非严格模式的第三类放宽 = 只加容忍度 `tol`。**

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `optimizer/theory/L39-CONSOLIDATION-DIVERGENCE.md` | 新建 | 盘整背驰判据的原文依据（第 39/60/37 课） |
| `optimizer/theory/L27-LIKE-SECOND-POINT.md` | 新建 | 「类似第二类买点」独立路径（第 27 课）+ 第 60 课「不得叫第一类」约束 |
| `optimizer/theory/L20-THIRD-TOLERANCE.md` | 新建 | 第三类「必须是第一次」已满足的核实 + 容忍度属工程口径的声明 |
| `ARCHITECTURE.md` | 修改 | `D-32`~`D-35` 决策记录 |
| `CHANGELOG.md` | 修改 | `[Unreleased]` 条目 |
| `src/chanlun/backtest/strategy.py` | 修改 | 触发判据改为「首次可见」；`signal_key()`；买卖点类型含 `pb`/`ps` |
| `src/chanlun/backtest/runner.py` | 修改 | 逐 bar 计算 fresh 信号并显式传给 `_view`；`make_engine` 带 `mode` |
| `src/chanlun/backtest/cli.py` | 修改 | `--mode {strict,loose}` |
| `src/chanlun/chan/divergence.py` | 新建 | 趋势背驰 / 盘整背驰判定（纯函数） |
| `src/chanlun/chan/engine.py` | 修改 | `Snapshot.divergences` + `divergence_fn` 扩展点；更正 docstring 不实陈述 |
| `src/chanlun/chan/state.py` | 修改 | 更正 `backtestable` docstring 不实陈述 |
| `src/chanlun/chan/signal.py` | 修改 | `SignalMode`、`SignalKind.PB/PS`、宽松第二类/第三类 |
| `src/chanlun/web/api.py` | 修改 | `mode` 参数进 `_CACHE` 键、`structure_payload` 增加 `mode`/`divergences` |
| `src/chanlun/web/static/index.html` | 修改 | `#mode-btn` + 背驰图层开关 |
| `src/chanlun/web/static/app.js` | 修改 | `state.mode`、URL/localStorage、背驰渲染 |
| `optimizer/tools/measure_loose_winrate.py` | 新建 | 全市场抽样胜率验收（不是优化器轮次） |
| `tests/chan/test_divergence.py` | 新建 | 背驰判定测试 |
| `tests/chan/test_signal_mode.py` | 新建 | 口径切换测试 |
| `tests/backtest/test_strategy_trigger.py` | 新建 | 触发判据回归测试 |
| `tests/web/test_structure_mode.py` | 新建 | API 口径 + 缓存隔离测试 |

---

### Task 1: 判据变更的前置文档（原文理论条目 + D-xx）

AGENTS.md §5 硬性要求：判据变更**先有理论条目**，再有代码。

**Files:**
- Create: `optimizer/theory/L39-CONSOLIDATION-DIVERGENCE.md`
- Create: `optimizer/theory/L27-LIKE-SECOND-POINT.md`
- Create: `optimizer/theory/L20-THIRD-TOLERANCE.md`
- Modify: `ARCHITECTURE.md`（在 `#### D-31` 之后追加）

**Interfaces:**
- Consumes: 无
- Produces: 后续所有任务的判据依据。`D-32`~`D-35` 编号被占用，Task 10 只追加「后果 / 变更历史」。

- [ ] **Step 1: 确认现有理论条目的格式**

Run: `cd /Users/zzz/workspace/chanlun && head -40 optimizer/theory/L78-SEGMENT-STANDARDIZATION.md`

按它的标题层级与「原文摘录 / 判据 / 被否决的替代方案」结构写下面三篇。

- [ ] **Step 2: 写 `optimizer/theory/L39-CONSOLIDATION-DIVERGENCE.md`**

必须逐字包含以下四段原文（从 `chanlun108/原文/` 摘录，不得改写）：

```markdown
# 盘整背驰（第 39、60、37 课）

## 原文摘录

第 039 课《同级别分解再研究》：

> 把a定义为A0，则Ai与Ai+2之间就可以不断地比较力度，用盘整背驰的方法决定买卖点。
> ……只理会一点，就是Ai与Ai+2之间是否盘整背驰，只要盘整背驰，就在i+2为偶数时卖出，
> 为奇数时买入。

第 039 课：

> 以上的方法，最大的特点是，就是在同级别分解的基础上将图形基本分为两类，
> 一类是"当i为偶Ai+3不跌破Ai高点"或"i为奇数Ai+3不升破Ai低点"；
> 一类是"Ai与Ai+2之间盘整背驰"。

第 060 课《图解分析示范五》：

> 站在最严格意义上，45-46线段构成43-44线段的盘整背驰（注意，力度比较的是下面所有红柱子的面积之和。）

第 037 课《背驰的再分辨》：

> 没有趋势，没有背驰，不是任何a+A+b+B+c形式的都有背驰的。……这样，是不存在背驰的，
> 最多就是盘整背驰。

第 015 课《没有趋势，没有背驰。》：

> 必须注意：没有趋势，没有背驰。**在盘整中是无所谓"背驰"的**，这点是必须特别明确的。

## 与第 15 课的表面冲突及消解（**必读，不消解就不能实现本判据**）

第 15 课的字面意思是**盘整中不存在背驰**（转述，原话见上），而第 39 / 60 课又在用
「盘整背驰」这个词并给出操作含义。**这不是禅师前后矛盾，是同一个词的两种用法**，消解如下：

| 用法 | 出处 | 含义 |
|---|---|---|
| 「背驰」（不带定语） | 第 15 课、第 37 课 | **专指趋势背驰**，必须先有趋势（两个同向中枢） |
| 「盘整背驰」 | 第 37 / 39 / 60 课 | **另一个被单独命名的现象**，不需要趋势 |

三条原文互相印证，缺口正好合上：

1. 第 15 课：「在盘整中是无所谓"背驰"的」⇒ 盘整里没有**（趋势）背驰**。
2. 第 37 课：「这样，是不存在背驰的，**最多就是盘整背驰**」⇒ 同一个位置，没有
   （趋势）背驰，退一格叫**盘整背驰**。这是禅师自己给出的、把两者区分开的措辞。
3. 第 60 课：「**严格来说，盘整背驰无所谓第一类买点**，只是这样来类比」⇒ 与第 15 课
   完全一致：正因为盘整里没有真背驰，盘整背驰才不能算第一类买点。

**由此得到本条目最重要的两条硬约束**（比「并列」这个说法精确）：

- 盘整背驰**不是**背驰，是**被单独命名的类比物**。图上标注可以写「盘整背驰」
  （禅师自己的词），但**任何文案不得暗示它是严格意义的背驰**。
- 盘整背驰**绝不能**并入 `b1`/`s1`（第 60 课），也绝不能因为它而放宽第一类
  （第一类要求真趋势，第 15 / 37 课）。

**如果哪天有人想「统一」成一种背驰，就是踩在第 15 课这句话上。**

## 判据

盘整背驰 = **同级别同向的两段** `Ai` 与 `Ai+2` 比较力度，后段收缩。
线段天然同向交替，所以 `segments[i]` 与 `segments[i+2]` 必然同向，无需额外筛选。
力度用同色柱面积之和（`macd.hist_area`，颜色取该段自身方向）。

与趋势背驰的关系：**在判据实现上并列**（两个独立的 `DivergenceKind`，各有各的
触发条件），**在语义上不对等**（趋势背驰才是第 15 课意义上的「背驰」）。
第 37 课「最多就是盘整背驰」是这两句话的分界线。

## 不得叫第一类买点

第 060 课：

> 如果把55当成第一类买点（严格来说，盘整背驰无所谓第一类买点，只是这样来类比），
> 57就是一个第二类买点。

⇒ 盘整背驰点**严格来说不是任何一类买卖点**：第 060 课把它**类比为第一类买点**，
但同一句立刻声明「盘整背驰无所谓第一类买点」；第 027 课则在「大级别里，如果不出现新低」
的情形下另给「类似第二类买点」—— 那是**另一条入口**，与第 060 课的 57 不是一回事。
因此本系统用独立信号类型 `pb` / `ps`，**既不并入** `b1` / `s1`，**也不改写第二类的定义**。
`name_cn` 取「类第二类」的**依据是第 027 课，不是第 060 课**。

## 被否决的替代方案

- **只在中枢内比较（窄口径）**：实测 220 次机会 vs 全局口径 233 次，
  差距很小；第 39 课的「同级别分解」并没有把比较限制在中枢内，故取全局口径，
  另以 `Divergence.in_pivot` 属性记录第 049 课的区分：第 049 课原话
  「中枢震荡中出现的类似盘整背驰的走势段，与中枢完成的向上移动出现的背驰段是不同的」，
  禅师用的是「类似盘整背驰的走势段」—— 第三种措辞，与「背驰」「盘整背驰」都不同。
- **把盘整背驰并入第一类买点**：直接违反第 60 课原话，否决。
```

- [ ] **Step 3: 写 `optimizer/theory/L27-LIKE-SECOND-POINT.md`**

```markdown
# 类似第二类买点（第 27 课）

## 原文摘录

第 027 课《盘整背驰与历史性底部》：

> 类似的，在大级别里，如果不出现新低，但可以构成类似第二类买点的买点，
> 在MACD上，显示出类似背驰时的表现，黄白线回拉0轴上下，而后一柱子面积小于前一柱子的。

第 101 课《答疑1》：

> 其实，所谓第二类买点，就是第一类买点的次级别回抽结束后再次探底或回试的那个
> 次级别走势的结束点。

第 021 课《缠中说禅买卖点分析的完备性》：

> 第二类买点是和第一类买点紧密相连的，因为出现第一类买点后，必然只会出现盘整与
> 上涨的走势类型，而第一买点出现后的第二段次级别走势低点就构成第二类买点。

第 101 课：

> 第二类买点跌破第一类买点，也就是第二类买点比第一类买点低，这是完全可以的，
> 这里一般都构成盘整背驰

## 判据

第二类买点有**两条**原文路径：

1. 第 101 / 021 课：前置第一类买点，次级别回抽不破第一类买点低点。（现行实现）
2. 第 027 课：**没有前置第一类**，由盘整背驰直接给出「类似第二类买点」。

第 2 条是本轮非严格模式新增的路径。它**不是**对第 101 课定义的重写，
而是原文自己给出的另一条入口 —— 第 27 课用的措辞就是「类似」。

**本路径产出的 `SignalKind` 是 `b2` / `s2`**（放宽后的第二类买卖点），**不是** `pb` / `ps`
—— `pb` / `ps` 是第 039 课盘整背驰判据的**另一个独立放宽**，见
[L39-CONSOLIDATION-DIVERGENCE.md](L39-CONSOLIDATION-DIVERGENCE.md)；
两条放宽互不依赖，不得混同。

## 被否决的替代方案

- **直接删掉第二类需前置第一类的要求**：会让第二类脱离第 101 / 021 课的定义，
  且无法说明新信号的原文来源。改为**并列增加第 27 课路径**，两条路径都保留。
```

- [ ] **Step 4: 写 `optimizer/theory/L20-THIRD-TOLERANCE.md`**

```markdown
# 第三类买卖点：位置判据、必须是第一次、容忍度（第 20、24、32、33 课）

## 原文摘录

第 020 课《缠中说禅走势中枢级别扩张及第三类买卖点》：

> 由定理一，可以得到第三类买卖点定理：一个次级别走势类型向上离开缠中说禅走势中枢，
> 然后以一个次级别走势类型回试，其低点不跌破ZG，则构成第三类买点；
> 一个次级别走势类型向下离开缠中说禅走势中枢，然后以一个次级别走势类型回抽，
> 其高点不升破ZD，则构成第三类卖点。

第 020 课：

> 但一定要注意，并不是任何回调回抽都是第三类买卖点，必须是第一次。

第 020 课中心定理一：

> 走势中枢的延伸等价于任意区间[dn，gn]与[ZD，ZG]有重叠。

第 024 课：

> 其后的次级别回跌并不重新回到前面的中枢里

第 032 课：

> 如果一个5分钟级别的回拉不回到中枢里，就意味着有第三类买点

第 033 课：

> 次级别回抽一旦不重新回到中枢里，就意味着第三类买点出现了

## 判据一：「必须是第一次」现行代码**已经满足**，不需要补过滤

`src/chanlun/chan/pivot.py:168-173` 的延伸循环
`while j < n and j - i < MAX_SEGMENTS and _overlaps(confirmed[j], zd, zg)`
让中枢**在第一个完全不碰 `[zd, zg]` 的段处封闭**，`end_idx = positions[j-1]`。
因此 `src/chanlun/chan/signal.py:167` 唯一取用的回试段 `segs[p.end_idx + 1]`
**就是**离开中枢后的第一次回抽（转述），结构上不可能取到第二次。
`pivot.py:221` 的 `i = j` 又保证相邻中枢的 `end_idx` 严格递增，同一次离开不会重复产出。

**已作废的度量**：曾统计中枢组内部在最后一段之前是否已有段越过 ZG（转述），
40 只票 / 64 个第三类信号命中 50 个（78%），一度判为偏离。
但这是**中枢延伸的正常形态** —— 由中心定理一，组内同向段只要 `dn <= ZG`
就仍在延伸，`gn` 完全可以远高于 ZG（实测 `600537` 的 ZG=5.4、组内某段最高 8.79）。
该指标测量不到「第一次」这件事。

## 判据二：容忍度 = 原文空白项，属工程口径

四课原文一致使用「不跌破」「不重新回到」「不回到」，
**没有任何允许小幅回到中枢的措辞**。
⇒ 容忍度 `tol` 无原文依据，只能在非严格模式生效，
且必须在代码注释、本条目、UI 文案三处标注为**工程口径**。

## 被否决的替代方案

- **把容忍度写进严格模式**：违反第 20/24/32/33 课，否决。
- **用中枢延伸后允许后续回抽来放宽「必须是第一次」**：实测为空操作
  （见判据一），且当 `MAX_SEGMENTS = 8` 上限触发时属中枢扩展范畴，本轮不纳入。
```

- [ ] **Step 5: 在 `ARCHITECTURE.md` 追加四条决策记录**

先找到 `#### D-31` 条目的结尾（下一个 `####` 之前），在其后追加：

```markdown
#### D-32 买卖点口径作为独立维度（严格 / 非严格），不触碰结构划分

**决策**：新增 `SignalMode`（`src/chanlun/chan/signal.py`），取值 `STRICT` / `LOOSE`，
默认 `STRICT`。它**只**影响买卖点判定与背驰标注；笔、线段、中枢的划分判据
（`segment.py` / `pivot.py` / `include.py` / `fractal.py`）一律不随口径变化。

**依据**：禅师对**划分**的要求（第 67 课标准特征序列、第 71 课包含关系、
第 78 课划分后标准化、第 79 课）没有「宽松版」；对**判读**则明确存在分层 ——
第 060 课《图解分析示范五》原话：「站在最严格意义上……当然，这是按最严格的，
并没有太大操作意义的分析。」

**被否决的替代方案**：让非严格模式同时放宽线段划分（例如放宽特征序列包含处理）。
否决理由：划分是结构，买卖点是结构上的判读；放松划分会让同一只票的「结构」
本身随开关漂移，且第 67/71/78/79 课无任何支持。

**后果 / 变更历史**：严格模式的买卖点结果必须与引入本维度之前逐项相同
（`tests/chan/test_signal_mode.py::test_strict_mode_matches_baseline` 守门）。
`web/api.py` 的 `_CACHE` 键必须包含 `mode`，否则两种口径会串数据。

#### D-33 盘整背驰作为独立信号类型，不得并入第一类买卖点

**决策**：新增 `SignalKind.PB` / `PS`（值 `"pb"` / `"ps"`），只在 `LOOSE` 模式产出。
背驰判定独立成 `src/chanlun/chan/divergence.py::find_divergences`，结果作为
`Snapshot.divergences` 派生字段（**不进** `state.py::_FIELDS`）。

**依据**：第 039 课《同级别分解再研究》「只理会一点，就是Ai与Ai+2之间是否盘整背驰」；
第 060 课「力度比较的是下面所有红柱子的面积之和」；第 037 课「没有趋势，没有背驰……
最多就是盘整背驰」。第 060 课同时给出硬约束：「严格来说，盘整背驰无所谓第一类买点，
只是这样来类比」。

**被否决的替代方案**：① 并入 `b1`/`s1` —— 违反第 60 课原话；
② 只在中枢内比较 —— 第 39 课未把比较限制在中枢内，且实测差距很小
（窄口径 220 次 vs 全局 233 次），故取全局口径，并以 `Divergence.in_pivot` 保留第 049 课的区分：
第 049 课原话「中枢震荡中出现的类似盘整背驰的走势段，与中枢完成的向上移动出现的背驰段是不同的」
—— 禅师用的是「类似盘整背驰的走势段」，第三种措辞，与「背驰」「盘整背驰」都不同。

**后果 / 变更历史**：`divergences` 是派生数据，point-in-time 语义由
`segments` + `macd` 保证，故不参与 `state.reconcile` 的调和。

#### D-34 回测触发判据：从「结构确认时刻」改为「首次可见」

**决策**：`src/chanlun/backtest/strategy.py::ChanSignalStrategy.on_bar` 不再用
`sig.confirmed_at == bar.ts` 判断「今日新可知」。改由 `runner.py` 在逐 bar 重算时
计算**本 bar 第一次可见的信号**，显式传给 `_view(...)`。
**不改** `Segment.confirmed_at` 的语义，**不改** `segment.py::_make_segment`。

**依据**：`confirmed_at` 是**结构自身的确认完成时刻**（线段继承「确认它被破坏的那一笔」
的确认时间，`segment.py:375-395`），不是**引擎第一次产出它的时刻**。
实测两者系统性不等：`sh.600000` 121/121、`sh.601088` 158/158、
`sz.000001` 146/146 —— **100%** 的结构 `confirmed_at` 早于首次可见。
故旧判据在结构上不可能成立，回测恒定 0 笔成交。

**被否决的替代方案**：① 让 `_make_segment` 产出「首次可见」时间 —— `full()`
只看得到当前帧，无法知道首次可见，且会让 `step.confirmed_at > full.confirmed_at`，
打破 `tests/chan/test_engine.py:205-208`（实测该方向 0 例外）；
② 改 `state.py::_reconcile_field` 一律记 `as_of` —— 同上，且回测走的是 `full()`
不经过 `reconcile`，修不到；③ 回测改用 `step()` —— 不解决根因且无性能收益。

**后果 / 变更历史**：8 只票实测，旧判据 0 次触发，新判据 22 次触发 / 9 个回合 /
胜率 4/9 = 44.4%（小样本）。第一根 bar 只登记不交易（预热），避免开仓日一次性买入
全部历史可见信号。`engine.py` §3/§4 与 `state.py::backtestable` 的 docstring
声称已记下「什么时候才知道它成立」，与实测 100% 反例不符，已更正。

#### D-35 第三类买卖点的容忍度为工程口径，且仅在非严格模式生效

**决策**：`LOOSE` 模式下第三类买卖点的回试判据改为
`back.low >= p.zg - THIRD_TOL * (p.zg - p.zd)`（卖点对称）。
`THIRD_TOL = 0.1` 为具名常量，注释与文档三处均标注「工程口径，无原文依据」。

**依据**：第 020 课「其低点不跌破ZG」、第 024 课「并不重新回到前面的中枢里」、
第 032 课「不回到中枢里」、第 033 课「不重新回到中枢里」—— 四课一致且严格，
**没有任何容忍度措辞**，故属**原文空白项**。

**被否决的替代方案**：把容忍度也用于严格模式（违反上述四课）；
放宽「必须是第一次」（实测为空操作，见 `optimizer/theory/L20-THIRD-TOLERANCE.md`）。

**后果 / 变更历史**：这是本系统里少数几个**明确无原文依据**的判据之一。
它的存在理由是可验收的胜率目标（用户要求 70%~80%），不是缠论推导。
```

- [ ] **Step 6: 逐字核对每一段原文摘录**

理论条目里的引文必须能在存档里 grep 到，**不许改写**：

```bash
cd /Users/zzz/workspace/chanlun/chanlun108/原文
grep -n "只理会一点" 039-*.md
grep -n "力度比较的是下面所有红柱子的面积之和" 060-*.md
grep -n "盘整背驰无所谓第一类买点" 060-*.md
grep -n "没有趋势，没有背驰" 037-*.md
grep -n "在盘整中是无所谓" 015-*.md
grep -n "类似第二类买点" 027-*.md
grep -n "必须是第一次" 020-*.md
grep -n "其低点不跌破ZG" 020-*.md
```

每条都必须命中。**任何一条没命中，就改条目里的引文去匹配存档，不许反过来改存档，也不许保留一个 grep 不到的"引文"。**

- [ ] **Step 7: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/optimizer/theory/L39-CONSOLIDATION-DIVERGENCE.md \
  chanlun/optimizer/theory/L27-LIKE-SECOND-POINT.md \
  chanlun/optimizer/theory/L20-THIRD-TOLERANCE.md \
  chanlun/ARCHITECTURE.md \
  chanlun/docs/superpowers/plans/2026-10-02-strict-loose-mode.md \
  chanlun/docs/superpowers/specs/2026-10-02-strict-loose-mode-design.md
git commit -m "docs(chan): 严格/非严格口径切换的判据依据与 D-32~D-35 决策记录"
```

---

### Task 2: 前置修复 —— 回测触发判据（P0）

**先修这个，否则 Task 9 的胜率验收跑不出任何成交。**

**Files:**
- Modify: `src/chanlun/backtest/strategy.py`（`ChanSignalStrategy` L97-136）
- Modify: `src/chanlun/backtest/runner.py`（`_view` L78-93、主循环 L166-194）
- Modify: `src/chanlun/chan/engine.py`（模块 docstring 第 3/4 点）
- Modify: `src/chanlun/chan/state.py`（`backtestable` docstring）
- Test: `tests/backtest/test_strategy_trigger.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `runner.signal_key(code: str, sig: Any) -> tuple[str, str, str, float]`
  - `runner._view(snapshot, as_of, signals=None)` —— `signals=None` 时保持旧行为（`backtestable` 全量）
  - 契约：`Strategy.on_bar(ctx, bar, snapshot)` 收到的 `snapshot.signals` 是**本 bar 第一次可见**的信号

- [ ] **Step 1: 先确认夹具的列名与索引**

```bash
cd /Users/zzz/workspace/chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python -c "
import pandas as pd
d = pd.read_parquet('tests/chan/fixtures/bars.parquet')
print(d.dtypes); print(d.head(2)); print(len(d))
"
```

记下 `ts` 是列还是索引、有没有 `amount`。下面测试里的 `_fixture_bars()` 按实测结果写。

- [ ] **Step 2: 写失败测试**

Create `tests/backtest/test_strategy_trigger.py`：

```python
"""D-34 回归：回测触发判据必须是「首次可见」，不是「结构确认时刻」。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.backtest.runner import run
from chanlun.backtest.strategy import ChanSignalStrategy
from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import find_signals
from chanlun.chan.state import backtestable

FIXTURE = Path(__file__).resolve().parents[1] / "chan" / "fixtures" / "bars.parquet"

#: 夹具是 **4 只票的面板**（`sh.600000` / `sh.601088` / `sz.300059` / `sz.300750`，
#: 各 1212 根，`ts` 重复 3636 行）。回测与 `ChanEngine` 都只认单票，而
#: `data/types.py::normalize` 的 `drop_duplicates(subset=["ts"], keep="last")`
#: 会把整个面板**静默压成最后一只票**（`sz.300750`），所以必须先切出单票。
#: 切法沿用 `tests/chan/test_engine.py:33-35`。
#: 选 `sz.300059` 而不是 `sh.600000`：真 600000 的 2 个「首次可见」信号都是 s3，
#: 空仓时卖点被跳过 ⇒ 修好后仍是 0 笔成交，测不出东西。
CODE = "sz.300059"
#: `seed_store` / `ChanEngine` 收不带市场前缀的裸代码（市场由存储目录表达）。
BARE = CODE.split(".", 1)[1]


def _fixture_bars() -> pd.DataFrame:
    """夹具里**单只票**的 K 线；`code` 列去掉（`tests/chan/test_engine.py:33-35`）。"""
    df = pd.read_parquet(FIXTURE)
    return df[df["code"] == CODE].drop(columns=["code"]).reset_index(drop=True)


def test_fresh_trigger_produces_trades(seed_store):
    """旧判据（confirmed_at == bar.ts）在结构上永不成立 ⇒ 恒 0 笔成交。"""
    seed_store(BARE, _fixture_bars())
    res = run(ChanSignalStrategy(), [BARE], period="day", benchmark_code=None)
    assert len(res.trades) > 0, (
        "首次可见触发判据必须产生成交；若为 0，说明 on_bar 仍在用 confirmed_at == bar.ts"
    )


def test_confirmed_at_never_equals_bar_ts(seed_store):
    """守住根因：confirmed_at 是结构自身的确认完成时刻，系统性早于首次可见。

    若本测试开始失败（hits > 0），说明 confirmed_at 的语义变了，
    D-34 的论证需要重新做一遍，不能默默保留旧结论。
    """
    df = _fixture_bars()
    total = hits = 0
    for k in range(250, len(df) + 1, 5):
        frame = df.iloc[:k]
        ts = str(frame.iloc[-1]["ts"])
        eng = ChanEngine(BARE, "day", signal_fn=find_signals, level="day")
        for sig in backtestable(eng.full(frame).signals, ts):
            total += 1
            if sig.confirmed_at == ts:
                hits += 1
    assert total > 0, "夹具上没有可见信号，本测试没有在测量任何东西"
    assert hits == 0, f"{hits}/{total} 个信号的 confirmed_at 等于当日 ts —— D-34 根因不再成立"
```

- [ ] **Step 3: 跑测试，确认 `test_fresh_trigger_produces_trades` 失败**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/backtest/test_strategy_trigger.py -v`

Expected: `test_fresh_trigger_produces_trades` **FAILED**（`assert 0 > 0`）；
`test_confirmed_at_never_equals_bar_ts` **PASSED**（它是根因证据，不是待修的 bug）。

- [ ] **Step 4: 改 `strategy.py` —— 去掉 `confirmed_at` 闸门**

把 `ChanSignalStrategy.on_bar`（L113-136）开头的循环改掉，并更新类 docstring：

```python
@dataclass
class ChanSignalStrategy:
    """缠论三类买卖点策略（本任务的主策略）。

    规则刻意保持简单，只为了验证**管线**而不是为了跑出好看的数字：
    - 收到「本 bar 第一次可见」的信号时动作（learn today → trade next open）；
    - 买点：不重复加仓（同一票只持一份）、持仓数不超过 `max_positions`；
    - 卖点：清仓；
    - 单笔用当前现金的 `cash_pct` 比例下单。

    触发判据的契约见 `ARCHITECTURE.md` D-34：`snapshot.signals` 由
    `runner.py` 过滤成**本 bar 第一次可见**的信号，本策略不再自己判断
    「今天刚确认」—— `Signal.confirmed_at` 是结构自身的确认完成时刻，
    实测 100% 早于引擎首次产出它的时刻，拿它当「新可知」恒定不成立。
    """

    buy_kinds: tuple[str, ...] = ("b1", "b2", "b3", "pb")
    sell_kinds: tuple[str, ...] = ("s1", "s2", "s3", "ps")
    max_positions: int = 5
    cash_pct: float = 0.2
    _seen: set = field(default_factory=set, repr=False, compare=False)

    def on_bar(self, ctx: Context, bar: Any, snapshot: Any) -> list[Order]:
        orders: list[Order] = []
        for sig in snapshot.signals:
            kind = sig.kind.value
            held = ctx.position(bar.code) is not None
            if sig.is_buy and kind in self.buy_kinds:
                if held or ctx.n_positions >= self.max_positions:
                    continue
                orders.append(Order(
                    bar.code, "buy", qty=0, signal_kind=kind, cash_pct=self.cash_pct,
                    reason=f"{sig.kind.name_cn}@{sig.ts} {sig.reason}".strip(),
                ))
                break
            if (not sig.is_buy) and kind in self.sell_kinds:
                if not held:
                    continue
                orders.append(Order(
                    bar.code, "sell", qty=0, signal_kind=kind,
                    reason=f"{sig.kind.name_cn}@{sig.ts} {sig.reason}".strip(),
                ))
                break
        return orders
```

（`pb`/`ps` 在严格模式下不会出现，所以无条件放进 `buy_kinds`/`sell_kinds` 是安全的，不必把 `mode` 穿进策略。）

- [ ] **Step 5: 改 `runner.py` —— 逐 bar 算「首次可见」**

在 `_bare`（L72）之后加：

```python
def signal_key(code: str, sig: Any) -> tuple[str, str, str, float]:
    """信号身份：代码 + 类型 + 时间 + 价格。

    不用列表下标 —— `_view` 每根 bar 都重算，`Signal.idx` 会随新信号插入而变，
    拿它做「见过没有」的键会把老信号误判成新信号。
    """
    return (code, str(sig.kind.value), str(sig.ts), round(float(sig.price), 4))
```

把 `_view`（L78-93）改成：

```python
def _view(snapshot: Any, as_of: str, signals: Sequence[Any] | None = None) -> Any:
    """把全量快照裁成「站在 `as_of` 这一天才知道的样子」。

    `signals=None` 表示取该时刻**全部可见**信号（旧行为，供不关心「首次可见」的
    调用方使用）；回测主循环显式传入**本 bar 第一次可见**的那几个（D-34）。
    """
    return replace(
        snapshot,
        strokes=tuple(backtestable(snapshot.strokes, as_of)),
        segments=tuple(backtestable(snapshot.segments, as_of)),
        pivots=tuple(backtestable(snapshot.pivots, as_of)),
        signals=tuple(backtestable(snapshot.signals, as_of) if signals is None
                      else signals),
    )
```

在主循环（L160-191）里加两个字典，并把 L186 换掉：

```python
    engines = {code: make_engine(code) for code in timelines}
    last_close: dict[str, float] = {}
    #: 每只票已经「见过」的信号键。回测是逐 bar 重算全量，同一根 bar 上
    #: 历史上早就成立的信号会反复出现，只有第一次见到的才算「今天新知道」。
    seen: dict[str, set[tuple]] = {}
    primed: set[str] = set()
```

```python
            full_snap = engines[code].full(frame)
            visible = backtestable(full_snap.signals, bar.ts)
            bag = seen.setdefault(code, set())
            if code not in primed:
                # 预热：第一根 bar 上可见的都是「开仓之前就存在」的历史信号，
                # 只登记、不交易，否则开局会一次性买满。
                primed.add(code)
                bag.update(signal_key(code, s) for s in visible)
                fresh: tuple[Any, ...] = ()
            else:
                fresh = tuple(s for s in visible if signal_key(code, s) not in bag)
                bag.update(signal_key(code, s) for s in fresh)
            snapshot = _view(full_snap, bar.ts, fresh)
```

- [ ] **Step 6: 跑测试，确认通过**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/backtest/ -v`

Expected: 全部 PASSED，含新增的两个。

- [ ] **Step 7: 用真实数据端到端复现**

```bash
cd /Users/zzz/workspace/chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun backtest \
  --codes sh.600000,sh.601088,sz.300750,sz.000001,sh.600519,sz.002415,sh.601398,sz.300059 \
  --start 2018-01-01 --period day 2>&1 | tail -30
```

Expected: **不再是 `0 笔成交 / 0 个回合`**。把实际的「笔成交 / 回合 / 胜率」抄进 Task 10 的 CHANGELOG 条目。

- [ ] **Step 8: 更正两处不实 docstring**

`src/chanlun/chan/engine.py` 模块 docstring 第 3、4 点声称已记下「什么时候才知道它成立」。实测 100% 反例。改成：

```
3. **快照是纯函数输出**：同一帧 bar + 同一份 policy，算几次都一样。
4. **`confirmed_at` 是结构自身的确认完成时刻，不是「首次可见时刻」。**
   两者实测系统性不等（`sh.600000` 121/121、`sh.601088` 158/158、
   `sz.000001` 146/146 的结构都早于首次可见）。想做 point-in-time
   回测必须用「观测过程」判断新可知，见 `backtest/runner.py` 与 D-34。
```

`src/chanlun/chan/state.py::backtestable` 的 docstring **没有**「已把什么时候才知道它成立记下来了」这句 —— 该句原话只存在于 `engine.py:24`；它自己的措辞是「只返回无未来函数的结构」，而这对同一个理由同样不成立（`confirmed_at <= as_of` 的结构仍可能是本 bar 才第一次可见）。改成陈述它真正保证的东西：「按 `status is CONFIRMED` / `confirmed_at is not None` / `confirmed_at <= as_of` 过滤；`confirmed_at` 是结构自身的确认完成时刻，调用方若需要『首次可见』必须自己跟踪观测过程（见 `backtest/runner.py` 与 D-34）」。

- [ ] **Step 9: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/src/chanlun/backtest/strategy.py \
  chanlun/src/chanlun/backtest/runner.py \
  chanlun/src/chanlun/chan/engine.py \
  chanlun/src/chanlun/chan/state.py \
  chanlun/tests/backtest/test_strategy_trigger.py
git commit -m "fix(backtest): 触发判据改为首次可见，修掉恒 0 笔成交"
```

---

### Task 3: `chan/divergence.py` —— 趋势背驰 / 盘整背驰

**Files:**
- Create: `src/chanlun/chan/divergence.py`
- Test: `tests/chan/test_divergence.py`

**Interfaces:**
- Consumes: `macd.hist_area(macd_df, i0, i1, color)`（`color` 必须是 `1` 或 `-1`，否则 `ValueError`）、`macd.macd(close)`、`trend.classify_trends(pivots, level)`、`trend.TrendType`
- Produces:
  - `DivergenceKind(str, Enum)`：`TREND = "trend"` / `CONSOLIDATION = "consolidation"`，属性 `name_cn` → `"趋势背驰"` / `"盘整背驰"`
  - `Divergence`（frozen dataclass）：`idx, kind, direction, ts, price, level, seg_idx, ref_seg_idx, area_now, area_prev, new_extreme, in_pivot, pivot_idx=None, reason="", status=Status.TENTATIVE, confirmed_at=None, src_start=0, src_end=0`，属性 `ratio` / `is_top` / `name_cn`
  - `find_divergences(bars, segments, pivots, level="day", macd_df=None) -> tuple[Divergence, ...]`（按 `(ts, kind.value)` 排序后统一编号）

- [ ] **Step 1: 写失败测试**

Create `tests/chan/test_divergence.py`：

```python
"""背驰判定：趋势背驰（第 37 课）与盘整背驰（第 39/60 课）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.chan.divergence import DivergenceKind, find_divergences
from chanlun.chan.engine import ChanEngine
from chanlun.chan.macd import hist_area, macd
from chanlun.chan.signal import SignalKind, find_signals
from chanlun.chan.types import Status

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bars.parquet"


@pytest.fixture()
def snap():
    bars = pd.read_parquet(FIXTURE)
    return bars, ChanEngine("600000", "day", signal_fn=find_signals,
                            level="day").full(bars)


def test_trend_divergence_matches_b1_s1(snap):
    """趋势背驰必须与第一类买卖点一一对应 —— 两份实现不能各自漂移。"""
    bars, s = snap
    divs = find_divergences(bars, s.segments, s.pivots, "day")
    trend = {(d.ts, round(d.price, 6)) for d in divs
             if d.kind is DivergenceKind.TREND}
    firsts = {(x.ts, round(x.price, 6)) for x in s.signals
              if x.kind in (SignalKind.B1, SignalKind.S1)}
    assert firsts <= trend, f"第一类买卖点里这些没有对应的趋势背驰：{firsts - trend}"


def test_consolidation_divergence_area_shrinks(snap):
    """盘整背驰的判据就是同向两段的同色面积收缩（第 39/60 课）。"""
    bars, s = snap
    macd_df = macd(bars["close"])
    segs = list(s.segments)
    divs = [d for d in find_divergences(bars, s.segments, s.pivots, "day")
            if d.kind is DivergenceKind.CONSOLIDATION]
    assert divs, "夹具上没有任何盘整背驰，本测试没有在测量任何东西"
    for d in divs:
        now, prev = segs[d.seg_idx], segs[d.ref_seg_idx]
        assert prev.direction == now.direction, "盘整背驰的两段必须同向"
        assert d.area_now < d.area_prev
        assert d.area_now == pytest.approx(hist_area(macd_df, now.src_start, now.src_end,
                                                    now.direction))
        assert d.direction == now.direction
        assert d.ts == now.end.end.ts


def test_divergence_ids_and_sorting_are_stable(snap):
    bars, s = snap
    a = find_divergences(bars, s.segments, s.pivots, "day")
    b = find_divergences(bars, s.segments, s.pivots, "day")
    assert a == b, "纯函数：同样的输入必须逐项相同"
    assert [d.idx for d in a] == list(range(len(a)))
    assert [d.ts for d in a] == sorted(d.ts for d in a)


def test_divergence_status_inherits_segment(snap):
    bars, s = snap
    segs = list(s.segments)
    for d in find_divergences(bars, s.segments, s.pivots, "day"):
        seg = segs[d.seg_idx]
        if seg.status is Status.CONFIRMED and seg.confirmed_at is not None:
            assert d.status is Status.CONFIRMED
            assert d.confirmed_at == seg.confirmed_at
        else:
            assert d.status is Status.TENTATIVE


def test_consolidation_is_not_called_a_divergence(snap):
    """第 15 课：「在盘整中是无所谓"背驰"的」⇒ 盘整背驰不得被表述为一种背驰。

    这条守住的是**术语**，不是算法。有人把 CONSOLIDATION 的中文名改成「背驰」
    （去掉「盘整」二字）就会炸 —— 那正是第 15 课禁止的说法。
    """
    assert DivergenceKind.TREND.name_cn == "趋势背驰"
    assert DivergenceKind.CONSOLIDATION.name_cn == "盘整背驰"
    assert DivergenceKind.TREND is not DivergenceKind.CONSOLIDATION


def test_only_trend_divergence_requires_a_trend(snap):
    """盘整背驰可以出现在没有任何中枢的位置；趋势背驰不行（第 37 课）。"""
    bars, s = snap
    divs = find_divergences(bars, s.segments, s.pivots, "day")
    trend = [d for d in divs if d.kind is DivergenceKind.TREND]
    assert all(d.pivot_idx is not None for d in trend), \
        "趋势背驰必然挂在某个中枢上（没有趋势就没有背驰）"
    consol = [d for d in divs if d.kind is DivergenceKind.CONSOLIDATION]
    assert any(d.pivot_idx is None for d in consol) or consol == [], \
        "盘整背驰不应被强制要求落在中枢里"
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/chan/test_divergence.py -v`

Expected: `ModuleNotFoundError: No module named 'chanlun.chan.divergence'`

- [ ] **Step 3: 写 `divergence.py`**

```python
"""背驰判定：趋势背驰（第 37 课）与盘整背驰（第 39、60 课）。

第 37 课《背驰的再分辨》：「没有趋势，没有背驰，不是任何a+A+b+B+c形式的都有
背驰的。……这样，是不存在背驰的，最多就是盘整背驰。」

第 15 课《没有趋势，没有背驰。》：「必须注意：没有趋势，没有背驰。**在盘整中是
无所谓"背驰"的**，这点是必须特别明确的。」

⇒ **「背驰」不带定语时专指趋势背驰**（第 15 / 37 课）；「盘整背驰」是**另一个被
单独命名的现象**，不是背驰的弱化版。第 37 课「最多就是盘整背驰」是两者的分界线。
判据实现上两者并列（两个 `DivergenceKind`），**语义上不对等**。

第 39 课《同级别分解再研究》：「只理会一点，就是Ai与Ai+2之间是否盘整背驰」
⇒ 盘整背驰的判据 = 同向的两段 `Ai` 与 `Ai+2` 比较力度，后段收缩。
线段方向天然交替，所以 `segments[i]` 与 `segments[i+2]` **必然同向**，无需筛选。

第 60 课《图解分析示范五》：「力度比较的是下面所有红柱子的面积之和。」
⇒ 力度 = 同色柱面积之和，颜色取该段自身方向（`macd.hist_area` 的口径）。

第 60 课同时给出硬约束：「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」
⇒ 盘整背驰不得并入第一类买卖点（见 `signal.py::SignalKind.PB`）。

第 49 课：「中枢震荡中出现的类似盘整背驰的走势段，与中枢完成的向上移动出现的
背驰段是不同的，两者分别在第三类买点的前后……这是有严格区分的。」
⇒ `Divergence.in_pivot` 记录这个区分。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Sequence

import pandas as pd

from .macd import hist_area, macd
from .pivot import Pivot
from .segment import Segment
from .trend import TrendType, classify_trends
from .types import Status


class DivergenceKind(str, Enum):
    TREND = "trend"
    CONSOLIDATION = "consolidation"

    @property
    def name_cn(self) -> str:
        return "趋势背驰" if self is DivergenceKind.TREND else "盘整背驰"


@dataclass(frozen=True)
class Divergence:
    """一次背驰。`seg_idx` 是**创出极值的那一段**（盘整背驰里的 `Ai+2`）。"""

    idx: int
    kind: DivergenceKind
    #: +1 = 顶背驰（上涨力度衰竭），-1 = 底背驰。
    direction: int
    ts: str
    price: float
    level: str
    seg_idx: int
    ref_seg_idx: int
    area_now: float
    area_prev: float
    #: 是否创出新的极值（第 27 课「不出现新低」的反面）。
    new_extreme: bool
    #: 该段是否落在某个中枢区间内（第 49 课的区分）。
    in_pivot: bool
    pivot_idx: int | None = None
    reason: str = ""
    status: Status = Status.TENTATIVE
    confirmed_at: str | None = None
    src_start: int = 0
    src_end: int = 0

    @property
    def ratio(self) -> float:
        return self.area_now / self.area_prev if self.area_prev > 0 else float("inf")

    @property
    def is_top(self) -> bool:
        return self.direction > 0

    @property
    def name_cn(self) -> str:
        return DivergenceKind(self.kind).name_cn


def _dead(seg: Segment) -> bool:
    return seg.status is Status.INVALIDATED


def _area(macd_df: pd.DataFrame, seg: Segment) -> float | None:
    """同色柱面积之和。`hist_area` 只认 ±1，方向取该段自身的。"""
    try:
        return hist_area(macd_df, seg.src_start, seg.src_end, seg.direction)
    except ValueError:
        return None


def _locate(pivots: Sequence[Pivot], i: int) -> tuple[bool, int | None]:
    for p in pivots:
        if p.start_idx <= i <= p.end_idx:
            return True, p.idx
    return False, None


def _seal(div: Divergence, seg: Segment) -> Divergence:
    if seg.status is Status.CONFIRMED and seg.confirmed_at is not None:
        return replace(div, status=Status.CONFIRMED, confirmed_at=seg.confirmed_at)
    return replace(div, status=Status.TENTATIVE, confirmed_at=None)


def _make(kind: DivergenceKind, seg: Segment, ref: Segment, level: str,
          pivots: Sequence[Pivot], seg_idx: int, ref_idx: int,
          a_now: float, a_prev: float, new_extreme: bool,
          reason: str) -> Divergence:
    in_pivot, pivot_idx = _locate(pivots, seg_idx)
    price = seg.low if seg.direction == -1 else seg.high
    return _seal(
        Divergence(
            idx=0, kind=kind, direction=seg.direction, ts=seg.end.end.ts,
            price=price, level=level, seg_idx=seg_idx, ref_seg_idx=ref_idx,
            area_now=a_now, area_prev=a_prev, new_extreme=new_extreme,
            in_pivot=in_pivot, pivot_idx=pivot_idx, reason=reason,
            src_start=seg.src_start, src_end=seg.src_end,
        ),
        seg,
    )


def _leaving_legs(segs: list[Segment], pivots: Sequence[Pivot],
                  want: int) -> list[tuple[Pivot, Segment, Segment | None]]:
    """每个中枢的「离开段」与上一个中枢的「离开段」。

    与 `signal.py::_entering_and_leaving`（L188-208）同构，**有意保留独立实现**：
    `signal.py` 会 import 本模块，反向 import 会成环。
    两者一致性由 `test_trend_divergence_matches_b1_s1` 守住。
    """
    out = []
    for k, p in enumerate(pivots):
        leave = segs[p.end_idx]
        if _dead(leave) or leave.direction != want:
            continue
        prev = None
        if k > 0:
            cand = segs[pivots[k - 1].end_idx]
            if not _dead(cand) and cand.direction == want:
                prev = cand
        out.append((p, leave, prev))
    return out


def _trend(segs: list[Segment], pivots: Sequence[Pivot], level: str,
           macd_df: pd.DataFrame) -> list[Divergence]:
    """趋势背驰：两个同向中枢之间比较离开段力度（第 37 课「没有趋势，没有背驰」）。"""
    out: list[Divergence] = []
    trends = classify_trends(pivots, level)
    groups = {
        -1: [p for t in trends if t.kind is TrendType.DOWN
             for p in pivots[t.start_idx:t.end_idx + 1]],
        1: [p for t in trends if t.kind is TrendType.UP
            for p in pivots[t.start_idx:t.end_idx + 1]],
    }
    for want, group in groups.items():
        if len(group) < 2:
            continue
        for _p, leave, prev in _leaving_legs(segs, group, want):
            if prev is None:
                continue
            if want == -1 and not leave.low < prev.low:
                continue
            if want == 1 and not leave.high > prev.high:
                continue
            a_now, a_prev = _area(macd_df, leave), _area(macd_df, prev)
            if a_now is None or a_prev is None or not a_now < a_prev:
                continue
            i = segs.index(leave)
            where = "新低" if want == -1 else "新高"
            out.append(_make(
                DivergenceKind.TREND, leave, prev, level, pivots, i,
                segs.index(prev), a_now, a_prev, True,
                f"趋势背驰：{leave.end.end.ts} 创{where}，MACD 面积 "
                f"{a_now:.4f} < 前一同向走势 {a_prev:.4f}",
            ))
    return out


def _consolidation(segs: list[Segment], pivots: Sequence[Pivot], level: str,
                   macd_df: pd.DataFrame) -> list[Divergence]:
    """盘整背驰：同向的 `Ai` 与 `Ai+2` 比较力度（第 39 课）。"""
    out: list[Divergence] = []
    for i in range(2, len(segs)):
        now, prev = segs[i], segs[i - 2]
        if _dead(now) or _dead(prev):
            continue
        if now.direction != prev.direction:
            continue
        a_now, a_prev = _area(macd_df, now), _area(macd_df, prev)
        if a_now is None or a_prev is None or not a_now < a_prev:
            continue
        if now.direction == -1:
            new_extreme = now.low < prev.low
            where = "新低" if new_extreme else "未创新低"
        else:
            new_extreme = now.high > prev.high
            where = "新高" if new_extreme else "未创新高"
        out.append(_make(
            DivergenceKind.CONSOLIDATION, now, prev, level, pivots, i, i - 2,
            a_now, a_prev, new_extreme,
            f"盘整背驰：{now.end.end.ts} {where}，MACD 面积 {a_now:.4f} < "
            f"同向前段 {prev.end.end.ts} 的 {a_prev:.4f}",
        ))
    return out


def find_divergences(
    bars: pd.DataFrame,
    segments: Sequence[Segment],
    pivots: Sequence[Pivot],
    level: str = "day",
    macd_df: pd.DataFrame | None = None,
) -> tuple[Divergence, ...]:
    """找出全部背驰，按时间排序后统一编号。

    `segments` 必须与传给 `find_pivots` 的是**同一个列表**（`Pivot.end_idx`
    是那个列表的下标）。
    """
    segs = list(segments)
    if macd_df is None and bars is not None and len(bars) > 0:
        macd_df = macd(bars["close"])
    if macd_df is None or not segs:
        return ()

    out = _trend(segs, pivots, level, macd_df)
    out.extend(_consolidation(segs, pivots, level, macd_df))
    out.sort(key=lambda d: (d.ts, d.kind.value))
    return tuple(replace(d, idx=i) for i, d in enumerate(out))
```

> `segs.index(leave)` 依赖 `Segment` 是可比较的 dataclass 且列表里没有重复元素。
> 若 `Segment` 定义了 `eq=True` 且存在等值段，改用
> `next(i for i, s in enumerate(segs) if s.src_start == leave.src_start and s.src_end == leave.src_end)`。

- [ ] **Step 4: 跑测试，确认通过**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/chan/test_divergence.py -v`

Expected: 4 PASSED。

- [ ] **Step 5: 全市场量级抽查（确认不是只在夹具上成立）**

```bash
cd /Users/zzz/workspace/chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python -c "
import random, pandas as pd
from pathlib import Path
from chanlun.data import store
from chanlun.chan.engine import ChanEngine
from chanlun.chan.divergence import find_divergences, DivergenceKind
from chanlun.chan.signal import find_signals
store.DATA_ROOT = Path('/Users/zzz/workspace/chanlun/data')
codes = sorted(p.stem for p in (store.DATA_ROOT/'day'/'sh').glob('*.parquet')) + \
        sorted(p.stem for p in (store.DATA_ROOT/'day'/'sz').glob('*.parquet'))
random.seed(7); random.shuffle(codes)
n_t = n_c = n_code = 0
for c in codes[:60]:
    df = store.read(c, 'day')
    if len(df) < 250: continue
    s = ChanEngine(c, 'day', signal_fn=find_signals, level='day').full(df)
    ds = find_divergences(df, s.segments, s.pivots, 'day')
    n_t += sum(1 for d in ds if d.kind is DivergenceKind.TREND)
    n_c += sum(1 for d in ds if d.kind is DivergenceKind.CONSOLIDATION)
    n_code += 1
print(f'{n_code} 只：趋势背驰 {n_t}，盘整背驰 {n_c}')
" 2>&1 | grep -v '^normalize:'
```

Expected: 盘整背驰数量显著多于趋势背驰（此前独立探针测得窄口径 220 / 全局 233 次机会，
60 只上应落在几十到一两百的量级）。**若盘整背驰为 0，说明面积口径写错了，回 Step 3 检查 `hist_area` 的 `color` 参数。**

- [ ] **Step 6: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/src/chanlun/chan/divergence.py \
  chanlun/tests/chan/test_divergence.py
git commit -m "feat(chan): 新增背驰判定（趋势背驰 / 盘整背驰）"
```

---

### Task 4: `Snapshot.divergences` + 引擎扩展点

**Files:**
- Modify: `src/chanlun/chan/engine.py`（`Snapshot` L51-111、`ChanEngine.__init__` L117-131、`_compute` L161-184）
- Test: `tests/chan/test_engine.py`（追加）

**Interfaces:**
- Consumes: `divergence.find_divergences`
- Produces:
  - `engine.DivergenceFn = Callable[[pd.DataFrame, Sequence[Segment], Sequence[Pivot], str], Sequence[Any]]`
  - `Snapshot.divergences: tuple[Any, ...] = ()`
  - `ChanEngine(code, period="day", policy=None, signal_fn=None, level=None, divergence_fn=None)`

- [ ] **Step 1: 写失败测试**

在 `tests/chan/test_engine.py` 末尾追加：

```python
def test_snapshot_carries_divergences_when_fn_given():
    from chanlun.chan.divergence import find_divergences

    bars = _fixture_bars()
    plain = ChanEngine("600000", "day", signal_fn=find_signals, level="day").full(bars)
    rich = ChanEngine("600000", "day", signal_fn=find_signals, level="day",
                      divergence_fn=find_divergences).full(bars)
    assert plain.divergences == (), "没给 divergence_fn 时不应凭空产出背驰"
    assert rich.divergences, "给了 divergence_fn 就必须有背驰"
    assert plain.signals == rich.signals, "背驰是派生数据，不得影响买卖点"


def test_clipped_to_keeps_only_visible_divergences():
    from chanlun.chan.divergence import find_divergences

    bars = _fixture_bars()
    snap = ChanEngine("600000", "day", signal_fn=find_signals, level="day",
                      divergence_fn=find_divergences).full(bars)
    lo, hi = str(bars["ts"].iloc[600]), str(bars["ts"].iloc[-1])
    cut = snap.clipped_to(lo, hi)
    assert cut.divergences, "窗口内应有背驰"
    assert all(lo <= d.ts <= hi for d in cut.divergences)
    assert len(cut.divergences) <= len(snap.divergences)
```

（`_fixture_bars()` 若 `test_engine.py` 里已有等价夹具，直接复用，不要新建。）

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/chan/test_engine.py -k divergence -v`

Expected: `AttributeError: 'Snapshot' object has no attribute 'divergences'`

- [ ] **Step 3: 改 `engine.py`**

在 `SignalFn`（L47-48）之后加：

```python
#: 背驰判定回调：`(bars, segments, pivots, level) -> Sequence[Divergence]`。
#: 与 `SignalFn` 同理，引擎不内置背驰规则。
DivergenceFn = Callable[[pd.DataFrame, Sequence[Segment], Sequence[Pivot], str],
                        Sequence[Any]]
```

`Snapshot` 在 `signals`（L66）之后加：

```python
    #: 背驰（趋势背驰 / 盘整背驰）。**派生数据**：由 `segments` + `macd` 决定，
    #: 不进 `state.py::_FIELDS`，不参与 reconcile 调和。
    divergences: tuple[Any, ...] = ()
```

`clipped_to` 的 `signals=` 之后加一行（背驰是**点**，与买卖点同规则，必须严格落在窗口内）：

```python
            divergences=tuple(d for d in self.divergences if first_ts <= d.ts <= last_ts),
```

`__init__`（L117-131）整体替换成下面这份 —— 只多一个参数、一行赋值，
**新参数放在 `level` 之后**，前五个位置参数的顺序一字不动：

```python
    def __init__(
        self,
        code: str,
        period: str = "day",
        policy: SegmentPolicy | None = None,
        signal_fn: SignalFn | None = None,
        level: str | None = None,
        divergence_fn: DivergenceFn | None = None,
    ) -> None:
        if not code:
            raise ValueError("code 不能为空")
        self.code = code
        self.period = period
        self.level = level or period
        self.policy = policy
        self.signal_fn = signal_fn
        self.divergence_fn = divergence_fn
```

`_compute` 里，在 `signals` 之后加：

```python
        divergences: tuple[Any, ...] = ()
        if self.divergence_fn is not None:
            divergences = tuple(self.divergence_fn(bars, segments, pivots, self.level))
```

并把 `divergences=divergences` 传进 `Snapshot(...)`。

- [ ] **Step 4: 跑测试，确认通过**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/chan/ -v`

Expected: 全 PASSED。`test_engine.py` 现有的 `stamp <= other`（L205-208）等不变量不受影响 —— 本任务没碰 `confirmed_at`。

- [ ] **Step 5: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/src/chanlun/chan/engine.py \
  chanlun/tests/chan/test_engine.py
git commit -m "feat(chan): Snapshot 增加背驰字段与引擎扩展点"
```

---

### Task 5: `SignalMode` + 盘整背驰买卖点 + 宽松第二/三类

**Files:**
- Modify: `src/chanlun/chan/signal.py`
- Test: `tests/chan/test_signal_mode.py`

**Interfaces:**
- Consumes: `divergence.find_divergences`、`divergence.DivergenceKind`
- Produces:
  - `SignalMode(str, Enum)`：`STRICT = "strict"` / `LOOSE = "loose"`，属性 `name_cn`
  - `THIRD_TOL: float = 0.1`（模块级具名常量）
  - `SignalKind.PB = "pb"` / `SignalKind.PS = "ps"`
  - `find_signals(bars, segments, pivots, level="day", macd_df=None, mode=SignalMode.STRICT)`

- [ ] **Step 1: 写失败测试**

Create `tests/chan/test_signal_mode.py`：

```python
"""严格 / 非严格买卖点口径（D-32 / D-33 / D-35）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import SignalKind, SignalMode, find_signals

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bars.parquet"


@pytest.fixture(scope="module")
def bars():
    return pd.read_parquet(FIXTURE)


@pytest.fixture(scope="module")
def strict(bars):
    return ChanEngine("600000", "day", signal_fn=find_signals, level="day").full(bars)


def test_strict_mode_matches_baseline(bars, strict):
    """D-32：严格模式的买卖点必须与不传 mode 时逐项相同。"""
    again = find_signals(bars, strict.segments, strict.pivots, "day")
    assert again == list(strict.signals)


def test_loose_adds_but_never_removes(bars, strict):
    """非严格是**放宽**：严格模式有的买卖点一个都不能少。"""
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    key = lambda s: (s.kind.value, s.ts, round(s.price, 6))
    assert {key(s) for s in strict.signals} <= {key(s) for s in loose}


def test_loose_emits_consolidation_kinds(bars, strict):
    loose = find_signals(bars, strict.segments, strict.pivots, "day",
                         mode=SignalMode.LOOSE)
    assert any(s.kind is SignalKind.PB for s in loose) or \
           any(s.kind is SignalKind.PS for s in loose), \
        "夹具上应至少有一个盘整背驰买卖点"


def test_strict_never_emits_pb_ps(bars, strict):
    assert not [s for s in strict.signals if s.kind in (SignalKind.PB, SignalKind.PS)]


def test_pb_name_is_not_first_kind():
    """第 60 课：「盘整背驰无所谓第一类买点，只是这样来类比」。"""
    assert "第一类" not in SignalKind.PB.name_cn
    assert "第一类" not in SignalKind.PS.name_cn
    assert SignalKind.PB.is_buy and not SignalKind.PS.is_buy


def test_third_tolerance_is_loose_only(bars, strict):
    """D-35：第三类的容忍度只在非严格模式生效。"""
    from chanlun.chan.signal import _third_kind

    a = _third_kind(list(strict.segments), strict.pivots, "day", SignalMode.STRICT)
    b = _third_kind(list(strict.segments), strict.pivots, "day", SignalMode.LOOSE)
    assert len(b) >= len(a)
    assert all(0.0 < x.price for x in b)
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/chan/test_signal_mode.py -v`

Expected: `ImportError: cannot import name 'SignalMode'`

- [ ] **Step 3: 改 `signal.py` —— 枚举与常量**

在 `SignalKind` 之前加：

```python
class SignalMode(str, Enum):
    """买卖点口径。

    **只影响买卖点与背驰标注**，不影响笔 / 线段 / 中枢的划分（D-32）。
    禅师对划分没有「宽松版」（第 67/71/78/79 课），对判读则有分层 ——
    第 60 课：「站在最严格意义上……当然，这是按最严格的，并没有太大操作意义的分析。」
    """

    STRICT = "strict"
    LOOSE = "loose"

    @property
    def name_cn(self) -> str:
        return "严格" if self is SignalMode.STRICT else "非严格"


#: 第三类买卖点的回试容忍度，取中枢高度 `(ZG - ZD)` 的比例。
#:
#: **工程口径，无原文依据。** 第 20 课「其低点不跌破ZG」、第 24 课「并不重新回到
#: 前面的中枢里」、第 32 课「不回到中枢里」、第 33 课「不重新回到中枢里」——
#: 四课一致且严格，没有任何容忍度措辞。
#: 见 `optimizer/theory/L20-THIRD-TOLERANCE.md` 与 ARCHITECTURE.md D-35。
#: 只在 `SignalMode.LOOSE` 下生效。
THIRD_TOL = 0.1
```

`SignalKind` 里加两个成员（放在 `S3` 之后）：

```python
    #: 盘整背驰买点。第 60 课：「严格来说，盘整背驰无所谓第一类买点，
    #: 只是这样来类比」⇒ 独立类型，不得并入 b1。
    PB = "pb"
    #: 盘整背驰卖点。
    PS = "ps"
```

并把 `is_buy` / `name_cn` 改成：

```python
    @property
    def is_buy(self) -> bool:
        return self.value in ("b1", "b2", "b3", "pb")

    @property
    def name_cn(self) -> str:
        if self is SignalKind.PB:
            return "盘整背驰买点（类第一类）"
        if self is SignalKind.PS:
            return "盘整背驰卖点（类第一类）"
        n = {"1": "一", "2": "二", "3": "三"}[self.value[1]]
        return f"第{n}类买点" if self.is_buy else f"第{n}类卖点"
```

（保留原有 1/2/3 的映射写法，只在前面加两个分支。）

- [ ] **Step 4: 改 `_third_kind` —— 加容忍度**

签名改为 `def _third_kind(segs: list[Segment], pivots: Sequence[Pivot], level: str,
mode: SignalMode = SignalMode.STRICT) -> list[Signal]:`，在循环体内、判 B3 之前加：

```python
        # 容忍度：允许回试段小幅回到中枢内。**工程口径，无原文依据**（D-35）。
        # 严格模式 `tol = 0.0`，`p.zg - 0.0` 与原判据逐字节等价。
        tol = THIRD_TOL * (p.zg - p.zd) if mode is SignalMode.LOOSE else 0.0
```

并把两个比较改成：

```python
            if (leave.direction == 1 and leave.high > p.zg
                    and back.direction == -1 and back.low > p.zg - tol):
```

```python
            if (leave.direction == -1 and leave.low < p.zd
                    and back.direction == 1 and back.high < p.zd + tol):
```

reason 里在非严格时追加 `（非严格：回试容忍 {tol:.4f}）`。

- [ ] **Step 5: 加盘整背驰买卖点与宽松第二类**

在 `_second_kind` 之前加：

```python
def _consolidation_kind(divs: Sequence[Any], segs: list[Segment],
                        level: str) -> list[Signal]:
    """盘整背驰买卖点（第 39 课）。

    第 60 课硬约束：「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」
    ⇒ 独立类型 `pb`/`ps`，绝不并入 `b1`/`s1`。
    """
    out: list[Signal] = []
    for d in divs:
        if d.kind is not DivergenceKind.CONSOLIDATION:
            continue
        if not (0 <= d.seg_idx < len(segs)):
            continue
        kind = SignalKind.PB if d.direction == -1 else SignalKind.PS
        out.append(_sig(kind, segs[d.seg_idx], level, d.pivot_idx,
                        f"盘整背驰：{d.ts} MACD 面积 {d.area_now:.4f} < "
                        f"同向前段 {d.area_prev:.4f}"))
    return out
```

`_second_kind` 签名改为 `(existing, segs, level, mode=SignalMode.STRICT, divs=())`，
在函数体末尾（`return out` 之前）加第 27 课那条路径：

```python
    if mode is SignalMode.LOOSE:
        # 第 27 课《盘整背驰与历史性底部》：「类似的，在大级别里，如果不出现新低，
        # 但可以构成类似第二类买点的买点」⇒ 盘整背驰可以**不经过第一类**直接给出
        # 类第二类买点。这是原文自己的第二条入口，不是对第 101 课定义的重写。
        for d in divs:
            if d.kind is not DivergenceKind.CONSOLIDATION:
                continue
            k = d.seg_idx
            if k + 2 >= len(segs):
                continue
            bounce, back = segs[k + 1], segs[k + 2]
            if (d.direction == -1 and bounce.direction == 1
                    and back.direction == -1 and back.low > d.price):
                out.append(_sig(
                    SignalKind.B2, back, level, d.pivot_idx,
                    f"盘整背驰 {d.ts}（{d.name_cn}）后回抽低点 {back.low:.3f} "
                    f"不破 {d.price:.3f}",
                ))
            elif (d.direction == 1 and bounce.direction == -1
                    and back.direction == 1 and back.high < d.price):
                out.append(_sig(
                    SignalKind.S2, back, level, d.pivot_idx,
                    f"盘整背驰 {d.ts}（{d.name_cn}）后回抽高点 {back.high:.3f} "
                    f"不破 {d.price:.3f}",
                ))
    return out
```

顶部 import 加：

```python
from .divergence import DivergenceKind, find_divergences
```

（`divergence.py` 不 import `signal.py`，不成环。）

- [ ] **Step 6: 改 `find_signals`**

```python
def find_signals(
    bars: pd.DataFrame,
    segments: Sequence[Segment],
    pivots: Sequence[Pivot],
    level: str = "day",
    macd_df: pd.DataFrame | None = None,
    mode: SignalMode = SignalMode.STRICT,
) -> list[Signal]:
    """按结构 + 力度找出全部买卖点，按时间排序后统一编号。

    `segments` 必须与传给 `find_pivots` 的是**同一个列表**：`Pivot.end_idx`
    是那个列表的下标，少一段都会让离开段/回试段整体错位。

    `mode` 只放宽买卖点判据，不改结构划分（D-32）。严格模式的结果与不传
    `mode` 时逐项相同。
    """
    segs = list(segments)
    if macd_df is None and bars is not None and len(bars) > 0:
        macd_df = macd(bars["close"])

    out: list[Signal] = []
    out.extend(_third_kind(segs, pivots, level, mode))
    out.extend(_first_kind(segs, pivots, level, macd_df))
    divs: tuple[Any, ...] = ()
    if mode is SignalMode.LOOSE:
        # 引擎会另算一份给 `Snapshot.divergences` 用；这里自己算是为了让
        # `find_signals` 保持可独立调用（测试、扫描器都不经过引擎）。
        divs = find_divergences(bars, segs, pivots, level, macd_df)
        out.extend(_consolidation_kind(divs, segs, level))
    out.extend(_second_kind(out, segs, level, mode, divs))

    out.sort(key=lambda s: (s.ts, s.kind.value))
    return [replace(s, idx=i) for i, s in enumerate(out)]
```

- [ ] **Step 7: 跑测试，确认通过**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/chan/ -v`

Expected: 全 PASSED。

- [ ] **Step 8: 查有没有别处按 kind 字面量分支**

```bash
cd /Users/zzz/workspace/chanlun && grep -rnE '"b1"|"b2"|"b3"|"s1"|"s2"|"s3"' src/chanlun/ | grep -v '\.pyc'
```

逐个确认：默认走 `STRICT` 的调用方（`scan/scanner.py`、`web/api.py` 未改前）拿不到 `pb`/`ps`，不受影响；有硬编码元组的地方按需补 `"pb"`/`"ps"`。把结论写进 Task 10 的 CHANGELOG。

- [ ] **Step 9: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/src/chanlun/chan/signal.py \
  chanlun/tests/chan/test_signal_mode.py
git commit -m "feat(chan): 新增非严格买卖点口径（盘整背驰 + 第三类容忍度）"
```

---

### Task 6: 回测 `--mode`

**Files:**
- Modify: `src/chanlun/backtest/runner.py`（`run` L108-136）
- Modify: `src/chanlun/backtest/cli.py`（`add_parser` L40、`main` L187）
- Test: `tests/backtest/test_backtest_cli.py`（追加）

**Interfaces:**
- Consumes: `SignalMode`、`find_divergences`
- Produces: `runner.run(..., mode: str = "strict")`；CLI `--mode {strict,loose}`

- [ ] **Step 1: 写失败测试**

在 `tests/backtest/test_backtest_cli.py` 末尾追加：

```python
def test_cli_accepts_mode_choice():
    from chanlun.backtest.cli import build_parser  # 按 cli.py 里实际的构造函数名

    p = build_parser()
    assert p.parse_args(["backtest", "--mode", "loose"]).mode == "loose"
    assert p.parse_args(["backtest"]).mode == "strict"
```

> 若 `cli.py` 的解析器构造函数不叫 `build_parser`，用 `grep -n "^def \|add_parser" src/chanlun/backtest/cli.py` 找到真实名字再写测试。

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/backtest/test_backtest_cli.py -v -k mode`

Expected: FAILED（`unrecognized arguments: --mode` 或 `SystemExit: 2`）

- [ ] **Step 3: 改 `runner.run`**

顶部加：

```python
from functools import partial

from ..chan.divergence import find_divergences
from ..chan.signal import SignalMode, find_signals
```

签名加 `mode: str = "strict"`（放在 `period` 之后、`initial_cash` 之前，保持关键字可用），
`make_engine` 改成：

```python
    m = SignalMode(mode)
    make_engine = engine_factory or (
        lambda code: ChanEngine(
            code, period,
            signal_fn=partial(find_signals, mode=m),
            divergence_fn=find_divergences,
            level=period,
        )
    )
```

（`partial` 绑定 `mode` 关键字，`_compute` 仍按位置传 4 个参数，签名兼容。）

- [ ] **Step 4: 改 `cli.py`**

`add_parser` 里加：

```python
    p.add_argument("--mode", choices=("strict", "loose"), default="strict",
                   help="买卖点口径：strict=严格（默认）；loose=非严格，"
                        "含盘整背驰买卖点与第三类回试容忍度")
```

`main` 里把 `mode=args.mode` 传给 `runner.run(...)`。

- [ ] **Step 5: 跑测试，确认通过**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/backtest/ -v`

Expected: 全 PASSED。

- [ ] **Step 6: 两种口径端到端对比**

```bash
cd /Users/zzz/workspace/chanlun && for m in strict loose; do echo "=== $m ==="; \
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun backtest \
  --codes sh.600000,sh.601088,sz.300750,sz.000001,sh.600519,sz.002415,sh.601398,sz.300059 \
  --start 2018-01-01 --period day --mode $m 2>&1 | tail -20; done
```

Expected: `loose` 的成交笔数与回合数 **≥** `strict`。记下两组数字。

- [ ] **Step 7: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/src/chanlun/backtest/runner.py \
  chanlun/src/chanlun/backtest/cli.py \
  chanlun/tests/backtest/test_backtest_cli.py
git commit -m "feat(backtest): 新增 --mode 口径开关"
```

---

### Task 7: Web API —— `mode` 参数 + 背驰出口

**Files:**
- Modify: `src/chanlun/web/api.py`（`_CACHE`、`snapshot_of` L362-398、`structure_payload`、`structure` 端点 L570-601）
- Test: `tests/web/test_structure_mode.py`

**Interfaces:**
- Consumes: `SignalMode`、`find_divergences`
- Produces:
  - `snapshot_of(code, period="day", limit=..., adjust=..., merged=False, ma=None, mode="strict")`
  - `GET /api/structure?...&mode=strict|loose`，payload 增加 `"mode": str` 与 `"divergences": [...]`
  - 缓存键含 `mode`

- [ ] **Step 1: 写失败测试**

Create `tests/web/test_structure_mode.py`：

```python
"""口径切换的 API 与缓存隔离（D-32）。"""

from __future__ import annotations

import pytest


def test_structure_defaults_to_strict(client):
    r = client.get("/api/structure", params={"code": "sh.600000", "period": "day",
                                             "limit": 300})
    assert r.status_code == 200
    assert r.json()["mode"] == "strict"


def test_structure_rejects_unknown_mode(client):
    r = client.get("/api/structure", params={"code": "sh.600000", "mode": "wild"})
    assert r.status_code == 422


def test_loose_mode_never_returns_fewer_signals(client):
    base = {"code": "sh.600000", "period": "day", "limit": 300}
    strict = client.get("/api/structure", params={**base, "mode": "strict"}).json()
    loose = client.get("/api/structure", params={**base, "mode": "loose"}).json()
    assert len(loose["signals"]) >= len(strict["signals"]), \
        "缓存键漏加 mode 时两个口径会串数据，这条会先炸"
    assert strict["divergences"] or loose["divergences"], "背驰必须两种口径都标注"


def test_loose_exposes_consolidation_kinds(client):
    js = client.get("/api/structure", params={"code": "sh.600000", "period": "day",
                                              "limit": 500, "mode": "loose"}).json()
    kinds = {s["kind"] for s in js["signals"]}
    if not ({"pb", "ps"} & kinds):
        pytest.skip("该票该窗口内没有盘整背驰，换个 code 再验")
```

> `client` 夹具若 `tests/web/` 里已有等价物（`test_api.py` 用的那个），直接复用同名夹具，不要新建。

- [ ] **Step 2: 跑测试，确认失败**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/web/test_structure_mode.py -v`

Expected: FAILED（`KeyError: 'mode'`）

- [ ] **Step 3: 改 `api.py`**

`snapshot_of` 签名加 `mode: str = "strict"`。缓存键改成含 `mode`：

```python
    key = (code, period, effective, last_ts, fingerprint, mode)
```

构造引擎处改成：

```python
    m = SignalMode(mode)
    snap = ChanEngine(
        code, period,
        signal_fn=partial(find_signals, mode=m),
        divergence_fn=find_divergences,
        level=period,
    ).full(full)
```

顶部 import 加 `from functools import partial`（若已有则跳过）与
`from ..chan.divergence import find_divergences` / `from ..chan.signal import SignalMode, find_signals`。

`structure_payload` 增加两个键：

```python
        "mode": mode,
        "divergences": [d.as_dict() if hasattr(d, "as_dict") else _div_dict(d)
                        for d in view.snap.divergences],
```

其中：

```python
def _div_dict(d: Any) -> dict[str, Any]:
    """背驰的出口形状。`Divergence` 是 frozen dataclass，用 `to_jsonable` 统一转。"""
    from ..chan.types import to_jsonable

    return to_jsonable(d)
```

端点签名加：

```python
    mode: str = Query("strict", pattern="^(strict|loose)$"),
```

并把它透传给 `snapshot_of(...)` 与 `structure_payload(...)`。

- [ ] **Step 4: 跑测试，确认通过**

Run: `cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest tests/web/ -v`

Expected: 全 PASSED。**特别确认 `test_structure_window.py` 的 4 个测试仍过**（窗口裁剪不受影响）。

- [ ] **Step 5: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/src/chanlun/web/api.py \
  chanlun/tests/web/test_structure_mode.py
git commit -m "feat(web): /api/structure 支持 mode 并输出背驰"
```

---

### Task 8: 前端 —— 口径按钮 + 背驰图层

**Files:**
- Modify: `src/chanlun/web/static/index.html`
- Modify: `src/chanlun/web/static/app.js`

**Interfaces:**
- Consumes: `/api/structure` 的 `mode` / `divergences`
- Produces: `?mode=strict|loose` URL 参数；`localStorage["chanlun.mode"]`；`.rail` 里新增 `divergence` 图层开关

- [ ] **Step 1: 抄 `#adjust-btn` 的既有写法**

先读三处作为模板，**照抄结构，不要发明新样式**：

```bash
cd /Users/zzz/workspace/chanlun && sed -n '75,90p' src/chanlun/web/static/index.html
sed -n '174,190p' src/chanlun/web/static/app.js
sed -n '1115,1130p' src/chanlun/web/static/app.js
sed -n '45,52p' src/chanlun/web/static/index.html
```

（分别是 `#adjust-btn` 的 DOM、`applyAdjustUI`、它的 click 监听、`.rail` 图层开关。）

- [ ] **Step 2: 改 `index.html`**

在 `#adjust-btn` 旁边加：

```html
<button id="mode-btn" class="mode" type="button" aria-pressed="false"
        title="买卖点口径：严格 / 非严格（只影响买卖点与背驰标注，不改线段与中枢划分）">口径：严格</button>
```

在 `.rail` 的图层开关里，照 `signals` 那一项的写法加一项：

```html
<button class="layer" data-layer="divergence" aria-pressed="true">背驰</button>
```

（`data-layer` 的确切属性名以 Step 1 读到的既有写法为准。）

- [ ] **Step 3: 改 `app.js` —— 状态与 URL**

`LS_MA` / `LS_ADJUST`（L64）旁边加：

```js
const LS_MODE = "chanlun.mode";
const MODE_ORDER = ["strict", "loose"];
const MODE_LABEL = { strict: "严格", loose: "非严格" };
```

`state` 里加 `mode: "strict"`，`state.layers` 里加 `divergence: true`。

URL 构造（L111-113）加：

```js
if (state.mode !== "strict") url.searchParams.set("mode", state.mode);
```

初始化（L1140-1154）里，照 `adjust` 的写法加：

```js
const savedMode = sp.get("mode") || localStorage.getItem(LS_MODE);
if (MODE_ORDER.includes(savedMode)) state.mode = savedMode;
```

- [ ] **Step 4: 改 `app.js` —— 按钮 UI**

照 `applyAdjustUI`（L174-187）写：

```js
function applyModeUI() {
  const btn = $("#mode-btn");
  if (!btn) return;
  const loose = state.mode === "loose";
  btn.textContent = `口径：${MODE_LABEL[state.mode]}`;
  btn.setAttribute("aria-pressed", loose ? "true" : "false");
  btn.classList.toggle("on", loose);
  btn.title = loose
    ? "非严格：含盘整背驰买卖点、第二类不要求前置第一类、第三类回试有容忍度（后者为工程口径，无原文依据）。笔/线段/中枢划分不变。"
    : "严格：只按趋势背驰判第一类，第二类需前置第一类，第三类回试不得回到中枢内。";
}
```

在 L1120-1124 旁边加监听：

```js
$("#mode-btn").addEventListener("click", () => {
  state.mode = state.mode === "strict" ? "loose" : "strict";
  localStorage.setItem(LS_MODE, state.mode);
  applyModeUI();
  refresh();
});
```

`refresh()` 换成 `app.js` 里真实的重新拉取函数名。

- [ ] **Step 5: 改 `app.js` —— 背驰渲染**

买卖点的 scatter（`name: "买卖点"`，L626 附近）之后，照它的写法加一组：

```js
{
  name: "背驰",
  type: "scatter",
  symbolSize: 9,
  data: (state.layers.divergence ? (payload.divergences || []) : []).map((d) => ({
    value: [d.ts, d.price],
    name: d.kind === "trend" ? "趋势背驰" : "盘整背驰",
    itemStyle: { color: d.direction < 0 ? "#e8a33d" : "#8a6fd4" },
    symbol: d.kind === "trend" ? "triangle" : "diamond",
  })),
  tooltip: { formatter: (p) => `${p.data.name}｜${p.data.value[0]}` },
},
```

tooltip 的 push（L500 附近）加一条 `背驰` 说明；图注里写清**两种口径都标注背驰，只有非严格口径把盘整背驰算作买卖点**。

**文案硬约束（第 015 / 060 课）**：图注与 tooltip 里对盘整背驰的措辞必须是
「盘整背驰（不是严格意义的背驰，第 15 课）」这类表述，**不得**写成
「一种背驰」「弱背驰」「趋势背驰的弱化版」。趋势背驰才写作「背驰」。
两种标记要在视觉上可区分（形状 + 颜色），不要只靠颜色 —— 色盲用户分不出来。

- [ ] **Step 6: 收尾 grep 旧口径文案**

```bash
cd /Users/zzz/workspace/chanlun && grep -rn "严格\|买卖点\|背驰" src/chanlun/web/static/ | grep -v '\.map'
```

逐条确认没有把旧口径写死的文案（AGENTS.md §5 第 5 条：后端改判据后页面图注必须跟着改）。

- [ ] **Step 7: 重启看盘页并用真实浏览器核对 DOM**

```bash
cd /Users/zzz/workspace/chanlun && pkill -f "chanlun serve" ; sleep 1
PYTHONPATH=src PYTHONUNBUFFERED=1 nohup ../.venv-chanlun/bin/python -m chanlun serve \
  > /tmp/serve.log 2>&1 &
sleep 3 && curl -s "http://127.0.0.1:8888/api/structure?code=sh.600000&period=day&limit=300&mode=loose" \
  | head -c 400
```

然后用真实浏览器打开 `http://127.0.0.1:8888/?code=sh.600000&mode=loose`，**dump DOM** 核对：

- `#mode-btn` 的 `textContent` 是 `口径：非严格`，`aria-pressed="true"`；
- 图注与 tooltip 文案里出现「盘整背驰」；
- 背驰图层开关能关掉背驰点。

**截图不算数 —— 文案必须从 DOM dump 里读。**

- [ ] **Step 8: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/src/chanlun/web/static/index.html \
  chanlun/src/chanlun/web/static/app.js
git commit -m "feat(web): 顶部口径切换按钮与背驰标注图层"
```

---

### Task 9: 胜率验收（全市场抽样）

**这是用户提的硬指标：「有一定程度上严谨，胜率有百分之七八十就可以」。**

**Files:**
- Create: `optimizer/tools/measure_loose_winrate.py`
- 产出：`docs/evidence/2026-10-02-strict-loose-winrate.md`

**Interfaces:**
- Consumes: `backtest.runner.run`、`backtest.metrics.metrics`、`SignalMode`
- Produces: 严格 / 非严格两种口径的 `trades / round_trips / win_rate / per_signal_type`

- [ ] **Step 1: 写测量工具**

Create `optimizer/tools/measure_loose_winrate.py`：

```python
"""口径胜率验收：严格 vs 非严格（全市场抽样）。

**这不是优化器轮次**，是用户要求的可失败测量（AGENTS.md §5「证据质量规则」）。
它不改主干、不提案、不写 journal。

复现：
    cd /Users/zzz/workspace/chanlun
    PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_loose_winrate.py \
        --n 120 --start 2018-01-01

为什么要把行情预读进内存：`runner.run` 每根 bar 都 `store.read(code, period, end=ts)`
（物理截断，见 D-34），N 只票 × M 根 bar 次 parquet 读取会把测量拖到不可接受。
这里把每只票的整帧读一次，再替换 `store.read` 为内存切片 —— **切片语义与
`store.read` 的 `start/end/limit` 必须一致**，否则测的不是同一个回测。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from chanlun.backtest import runner as runner_mod          # noqa: E402
from chanlun.backtest.metrics import metrics               # noqa: E402
from chanlun.backtest.runner import run                    # noqa: E402
from chanlun.backtest.strategy import ChanSignalStrategy   # noqa: E402
from chanlun.data import store                             # noqa: E402

FRAMES: dict[tuple[str, str], pd.DataFrame] = {}


def _mem_read(code, period, start=None, end=None, limit=None):
    df = FRAMES.get((code, period))
    if df is None:
        return pd.DataFrame()
    if start:
        df = df[df["ts"] >= start]
    if end:
        df = df[df["ts"] <= end]
    if limit:
        df = df.tail(limit)
    return df.reset_index(drop=True)


def pick(n: int, min_bars: int, seed: int) -> list[str]:
    store.DATA_ROOT = ROOT / "data"
    codes: list[str] = []
    for market in ("sh", "sz"):
        codes += [f"{market}.{p.stem}" for p in sorted((store.DATA_ROOT / "day" / market).glob("*.parquet"))]
    random.seed(seed)
    random.shuffle(codes)
    out: list[str] = []
    for c in codes:
        df = store.read(c, "day")
        if len(df) < min_bars:
            continue
        FRAMES[(c, "day")] = df.reset_index(drop=True)
        out.append(c)
        if len(out) >= n:
            break
    return out


def one(mode: str, codes: list[str], start: str | None) -> dict:
    res = run(ChanSignalStrategy(), codes, start=start, period="day",
              benchmark_code=None, mode=mode)
    m = metrics(res)
    return {
        "mode": mode,
        "trades": len(res.trades),
        "round_trips": len(res.round_trips),
        "win_rate": m.get("win_rate"),
        "payoff_ratio": m.get("payoff_ratio"),
        "expectancy_pct": m.get("expectancy_pct"),
        "per_signal_type": m.get("per_signal_type"),
        "notes": list(res.notes),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120, help="抽样标的数（验收要求 >=100）")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    codes = pick(args.n, args.min_bars, args.seed)
    print(f"抽样 {len(codes)} 只（>=250 根日线，seed={args.seed}，start={args.start}）")
    store.read = _mem_read
    runner_mod.store.read = _mem_read

    rows = [one(m, codes, args.start) for m in ("strict", "loose")]
    print(f"{'口径':<8}{'成交':>7}{'回合':>7}{'胜率':>9}{'盈亏比':>9}{'期望%':>9}")
    for r in rows:
        wr = "—" if r["win_rate"] is None else f"{r['win_rate']:.1%}"
        print(f"{r['mode']:<8}{r['trades']:>7}{r['round_trips']:>7}{wr:>9}"
              f"{(r['payoff_ratio'] or 0):>9.2f}{(r['expectancy_pct'] or 0):>9.2f}")
    for r in rows:
        print(f"\n[{r['mode']}] 分类：{json.dumps(r['per_signal_type'], ensure_ascii=False)}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> **先验证内存切片与真实 `store.read` 等价**（否则整个测量无效）：
> `runner.run` 在 `--n 3` 下，替换前后 `trades`/`round_trips` 必须完全相同。
> 不一致就先修 `_mem_read`，不要继续。

- [ ] **Step 2: 小样本冒烟**

```bash
cd /Users/zzz/workspace/chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
  optimizer/tools/measure_loose_winrate.py --n 8 2>&1 | grep -v '^normalize:'
```

Expected: 两种口径都有成交。**若 `loose` 的成交数不大于 `strict`，先回去查 Task 5/6，不要往下跑。**

- [ ] **Step 3: 正式测量**

```bash
cd /Users/zzz/workspace/chanlun && PYTHONPATH=src ../.venv-chanlun/bin/python \
  optimizer/tools/measure_loose_winrate.py --n 120 \
  --out /tmp/winrate.json 2>&1 | grep -v '^normalize:' | tee /tmp/winrate.log
```

Expected: **回合数 ≥ 100**（否则样本不足以谈胜率）。

- [ ] **Step 4: 写证据文档**

Create `docs/evidence/2026-10-02-strict-loose-winrate.md`，写清：

- 复现命令（Step 3 的整行）；
- 抽样规则（seed、min_bars、start、标的数）；
- 严格 / 非严格的成交、回合、胜率、盈亏比、期望、分类明细；
- **结论要如实**：
  - 若非严格胜率落在 **70%~80%** ⇒ 达标；
  - 若 **< 70%** ⇒ 在**已批准的判据集内**调参（只允许调 `THIRD_TOL` 与盘整背驰的力度阈值），**不得**新增无原文依据的判据，并如实记录仍未达标；
  - 若 **> 80%** ⇒ 说明过滤过严、信号太少，同样要如实记录，并报告实际信号数是否仍满足用户「买卖点太少了」的诉求。
- **样本量的局限必须写明**：单次抽样、单一区间、无交易成本之外的滑点假设。

- [ ] **Step 5: 提交**

```bash
cd /Users/zzz/workspace && git add \
  chanlun/optimizer/tools/measure_loose_winrate.py \
  chanlun/docs/evidence/2026-10-02-strict-loose-winrate.md
git commit -m "test(backtest): 口径胜率验收测量与证据"
```

---

### Task 10: 收尾文档与全量回归

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `ARCHITECTURE.md`（`D-32`~`D-35` 的「后果 / 变更历史」补实测数字）

**Interfaces:**
- Consumes: Task 2 / 6 / 9 的实测数字
- Produces: 可追溯的变更记录

- [ ] **Step 1: 全量测试**

```bash
cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest 2>&1 | tail -15
```

Expected: 基线是 **842 passed, 12 deselected**。新增测试后总数应更大，且 **0 failed**。

- [ ] **Step 2: 确认「严格模式逐项不变」这条硬约束**

```bash
cd /Users/zzz/workspace/chanlun && git stash list && \
PYTHONPATH=src ../.venv-chanlun/bin/python -c "
import pandas as pd
from chanlun.chan.engine import ChanEngine
from chanlun.chan.signal import find_signals
bars = pd.read_parquet('tests/chan/fixtures/bars.parquet')
s = ChanEngine('600000','day',signal_fn=find_signals,level='day').full(bars)
print('段', len(s.segments), '中枢', len(s.pivots), '买卖点', len(s.signals))
print([x.kind.value for x in s.signals])
" 2>&1 | grep -v '^normalize:'
```

Expected: **段 11 / 中枢 2 / 买卖点 2**（与 D5 落地后的夹具口径一致），且买卖点里**没有 `pb`/`ps`**。

- [ ] **Step 3: 更新 `ARCHITECTURE.md`**

在 `D-32`~`D-35` 四条里各补一段「**实测**」，写入 Task 2 / 6 / 9 的真实数字（成交笔数、回合数、两种口径的胜率）。**数字必须与 Task 9 的证据文档一致。**

- [ ] **Step 4: 更新 `CHANGELOG.md`**

在 `[Unreleased]` 段落里追加（**注明尚未提交**）：

```markdown
### 新增：买卖点口径切换（严格 / 非严格）

本节改动**尚未提交**。

- **口径维度**：新增 `SignalMode`（`STRICT` / `LOOSE`），看盘页顶部按钮切换，
  状态存 URL 参数 `?mode=` 与 `localStorage`，默认严格。
  **只影响买卖点与背驰标注**；笔 / 线段 / 中枢的划分判据一字未动
  （第 67 / 71 / 78 / 79 课对划分没有「宽松版」）。
- **盘整背驰买卖点**（非严格模式新增 `pb` / `ps`）：
  第 39 课《同级别分解再研究》「只理会一点，就是Ai与Ai+2之间是否盘整背驰」。
  第 60 课硬约束「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」
  ⇒ 独立类型，名字固定为「盘整背驰买点（类第一类）」（第 027 课第 7 段原文措辞），绝不并入 `b1`/`s1`。
- **第二类买卖点新增原文路径**：第 27 课「如果不出现新低，但可以构成类似第二类买点的买点」
  ⇒ 盘整背驰可以不经过第一类直接给出类第二类买点。第 101 / 21 课的原路径保留。
- **第三类买卖点容忍度**（`THIRD_TOL = 0.1`，只在中枢高度的 10% 以内）：
  **工程口径，无原文依据** —— 第 20 / 24 / 32 / 33 课一致要求「不跌破 ZG」
  「不重新回到中枢里」，没有任何容忍度措辞。只在非严格模式生效。
- **背驰标注**：新增 `chan/divergence.py`，区分**趋势背驰**（第 37 课，两个同向中枢）
  与**盘整背驰**（第 39 / 60 课，同向两段面积收缩）。**两种口径都在图上标注**；
  只有非严格口径把盘整背驰算作买卖点。
  `Divergence.in_pivot` 保留第 049 课的区分 —— 第 049 课原话「中枢震荡中出现的类似盘整背驰的走势段，与中枢完成的向上移动出现的背驰段是不同的」。
- **一处必须写进文档的语义澄清**：第 15 课《没有趋势，没有背驰。》说
  「在盘整中是无所谓"背驰"的」，第 37 课说「最多就是盘整背驰」⇒
  **「背驰」不带定语时专指趋势背驰，盘整背驰不是背驰**，只是被单独命名的类比物。
  这与第 60 课「盘整背驰无所谓第一类买点」是同一条逻辑。判据上两者并列，
  **语义上不对等** —— 页面文案已按此措辞，不得表述为「一种背驰」。

### 修复：回测恒定 0 笔成交

- **成因**：`ChanSignalStrategy.on_bar` 用 `sig.confirmed_at == bar.ts` 判断
  「今天刚确认」。但 `confirmed_at` 是**结构自身的确认完成时刻**
  （线段继承「确认它被破坏的那一笔」的确认时间），实测**系统性早于**引擎第一次
  产出该信号的时刻：`sh.600000` 121/121、`sh.601088` 158/158、`sz.000001` 146/146，
  **100%** 不等。于是该条件在结构上永不成立，回测恒定 0 笔成交。
- **修法**：触发判据改为「**首次可见**」—— 由 `runner.py` 逐 bar 跟踪已见信号键，
  只把第一次出现的信号交给策略。**不改** `Segment.confirmed_at` 的语义
  （改它会打破 `tests/chan/test_engine.py:205-208` 的 `stamp <= other` 不变量，
  实测 `step.confirmed_at > full.confirmed_at` 为 0 例）。
- **同时更正两处不实 docstring**：`chan/engine.py` 模块 docstring 第 3/4 点与
  `chan/state.py::backtestable` 曾声称已记下「什么时候才知道它成立」，与上述 100% 反例不符。
- **实测**：（填 Task 2 Step 7 与 Task 6 Step 6 的真实数字）
```

- [ ] **Step 5: 前端旧口径文案终检**

```bash
cd /Users/zzz/workspace/chanlun && grep -rn "ZD\|ZG\|背驰\|严格" src/chanlun/web/static/*.js src/chanlun/web/static/*.html | grep -v '\.map'
```

逐条确认没有与后端判据相反的文案。

- [ ] **Step 6: 提交**

```bash
cd /Users/zzz/workspace && git add chanlun/CHANGELOG.md chanlun/ARCHITECTURE.md
git commit -m "docs: 记录口径切换、背驰标注与回测触发判据修复"
```

- [ ] **Step 7: 报告用户**

最终回复里必须包含（**不许含糊**）：

1. 口径按钮在哪、怎么用、默认是什么；
2. **两种口径的实测胜率与回合数**，以及样本局限；
3. 背驰标注长什么样（趋势背驰 / 盘整背驰怎么区分）；
4. **哪些放宽有原文依据、哪些是工程口径** —— 第三类容忍度必须明说无原文依据；
5. **一个必须坦白的更正**：设计阶段曾把「放宽第三类『必须是第一次』」列为一项放宽，
   实测它是空操作（`pivot.py:168-173` 的延伸循环已保证 `segs[p.end_idx + 1]`
   就是第一次回抽），故未实现。这是测量推翻假设，不是遗漏。

---

## 自审（Self-Review）

**Spec 覆盖：**

| 设计文档章节 | 覆盖任务 |
|---|---|
| §2 回测触发判据前置修复 | Task 2 |
| §2.6 更正不实 docstring | Task 2 Step 8 |
| §3.1 盘整背驰原文依据 | Task 1 Step 2 / Task 3 |
| §3.2 不得叫第一类 | Task 1 Step 2 / Task 5 Step 5 |
| **第 15 课「在盘整中是无所谓背驰的」的消解**（计划新增，非 spec 原有） | Global Constraints / Task 1 Step 2「与第 15 课的表面冲突及消解」/ Task 3 测试 / Task 8 文案 / Task 10 CHANGELOG |
| §3.3 第二类定义依赖第一类 | Task 1 Step 3 / Task 5 Step 5 |
| §3.4 第二类的独立路径（第 27 课） | Task 1 Step 3 / Task 5 Step 5 |
| §3.5 第三类「必须是第一次」已满足 | Task 1 Step 4（含已作废度量的记录） |
| §3.6 第三类容忍度属原文空白项 | Task 1 Step 4 / Task 5 Step 3-4 |
| §4.1 口径模型与作用域硬约束 | Global Constraints / Task 5 Step 1 |
| §4.2 严格/非严格判据对照 | Task 5 Step 4-6 |
| §4.3 背驰标注（含 `in_pivot`） | Task 3 |
| §4.4 数据模型 | Task 3 / Task 4 |
| §4.5 买卖点判据 | Task 5 |
| §4.6 回测 | Task 2 / Task 6 |
| §4.7 Web / UI | Task 7 / Task 8 |
| §4.8 必须产出的文档 | Task 1 / Task 10 |
| §5.1 R1 按钮 | Task 8 |
| §5.2 R2 胜率 70%~80% | Task 9 |
| §5.3 R3 背驰标注 | Task 3 / Task 4 / Task 7 / Task 8 |
| §5.4 回归 | Task 10 Step 1-2 |
| §6 影响面（`_CACHE` 键） | Task 7 Step 1-3 |
| §7 本轮不纳入 | Global Constraints（第三类「第一次」）/ Task 9 Step 4（不得新增无依据判据） |

**占位符扫描：** 计划里所有代码步骤都给了可粘贴的完整代码。三处需要按实际代码名对齐的地方
（`cli.py` 的解析器构造函数名、`tests/web/` 的 `client` 夹具名、`app.js` 的 `refresh()` 函数名）
都给了**明确的查找命令**，不是「TODO」。`Divergence` 的 `segs.index(leave)` 给了退化写法。

**类型一致性：**

- `find_signals(bars, segments, pivots, level, macd_df, mode)` —— Task 5 定义，
  Task 3 的测试、Task 6 的 `partial`、Task 7 的 `partial` 都按这个顺序用。
- `find_divergences(bars, segments, pivots, level, macd_df)` —— Task 3 定义，
  Task 4 的 `divergence_fn`、Task 6 / Task 7 的构造都按 `(bars, segments, pivots, level)`
  位置调用（`macd_df` 留空由函数自己算）。
- `Snapshot.divergences` —— Task 4 定义，Task 7 的 `structure_payload`、Task 8 的渲染都用这个名字。
- `signal_key(code, sig)` —— Task 2 定义并在同任务内使用。
- `_view(snapshot, as_of, signals=None)` —— Task 2 定义；`signals=None` 保持旧行为，
  现有 `tests/backtest/test_runner.py` 的调用不受影响。
- `THIRD_TOL` / `SignalMode` / `SignalKind.PB|PS` —— Task 5 定义，Task 6 / 7 只透传字符串
  `"strict"|"loose"`，不直接依赖枚举。

**已知风险：**

1. **夹具上可能没有盘整背驰** —— Task 5 Step 1 的 `test_loose_emits_consolidation_kinds`
   会失败。此时**换夹具**（`tests/chan/fixtures/` 下另找一个有背驰的票的 parquet），
   不要为了让测试过而放宽判据。
2. **`segs.index(leave)` 在等值段上取错下标** —— 已给退化写法。
3. **`_mem_read` 与 `store.read` 语义不一致会让 Task 9 的测量无效** ——
   Task 9 Step 1 的注释里已要求先做 `--n 3` 的等价性验证。
4. **非严格胜率可能达不到 70%** —— Task 9 Step 4 已规定只能调 `THIRD_TOL` 与力度阈值，
   且必须如实记录未达标，不许新增无原文依据的判据。

**写计划期间发现并已消解的一处原文冲突（不是风险，是必须照做的约束）：**

第 015 课《没有趋势，没有背驰。》原文说「在盘整中是无所谓"背驰"的」，
与设计文档把盘整背驰当作「与趋势背驰并列的独立类别」**字面冲突**。
已在 Task 1 Step 2 里用第 37 课「最多就是盘整背驰」+ 第 60 课「盘整背驰无所谓第一类买点」
消解为：**判据上并列、语义上不对等；「背驰」不带定语时专指趋势背驰**。
已落成 Global Constraints 一条硬约束 + `test_consolidation_is_not_called_a_divergence`
这条可失败的测试 + Task 8 的 UI 文案约束 + Task 10 的 CHANGELOG 条目。

> 这处冲突是**写计划时逐条 grep 原文引文才发现的**，设计阶段漏了。
> 它不改变任何已批准的设计决策（`pb`/`ps` 仍是独立类型、第一类仍不放宽），
> 只收紧了**措辞**和**文档义务**。

---

## 执行方式

计划已完成并自审。两种执行方式：

1. **Subagent-Driven（推荐）** —— 每个任务派一个子 agent，任务间做审查，迭代快。
2. **Inline** —— 在本会话里按 `executing-plans` 逐任务执行，带检查点。
