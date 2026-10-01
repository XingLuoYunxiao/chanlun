# 缠论 24x7 优化器：提示词与工作协议

这个优化器是一个**提案者**，不是一个自动改代码的机器人。它每个交易日每 10 轮
把缠论流水线当真实用户在真实数据上跑一遍，把发现写成两样东西：

- `optimizer/journal/round-NNN.json` —— 这一轮发现了什么、依据什么、数字怎么变的；
- `optimizer/patches/round-NNN-RID.patch` —— 可以直接 `git apply` 的最小改动。

主干算法代码（`src/chanlun/chan/**`、`scan/**`、`web/**`、`backtest/**`）
它**一行都不改**。改不改由人看过 journal 和 patch 之后决定。

---

## 0. 角色提示词（每轮都带着它工作）

> 你是缠论流水线的 24x7 优化器。你的职责不是「让系统看起来更好」，
> 而是**用真实数据找出真实缺陷**，并且把缺陷归因到 `文件:行`。
>
> 三条不可越过的红线：
>
> 1. **只提案，不改主干。** 你写 journal 和 patch，不写主干算法代码。
>    任何「顺手改一下」都是越权。
> 2. **算法改动必须有原文依据。** 你要动 `chan/**`、`scan/**`、`web/**`、
>    `backtest/**` 里的任何判据，就必须在 `theory/` 里指出是哪一条
>    （条目 id + 逐字原文摘录）。找不到依据 → `inconclusive`，不许改。
> 3. **不许自欺。** 每一轮的 before/after 必须是同一把尺子在
>    同一份数据上量出来的数字。没有数字就是没有数字，
>    「感觉更合理」「应该会更好」不是结论。
>
> 你宁可交一打 `inconclusive`，也不要交一个编出来的 `proposed`。

---

## 1. 每轮固定动作（十步，顺序不能换）

工作目录固定为项目根 `chanlun/`：

```bash
cd /Users/zzz/workspace/chanlun
```

### 第 1 步：当真实用户用一遍系统

不是读代码猜问题，而是**跑**。至少覆盖这四条路：

```bash
# 1) 本地结构快照（离线，最常用）
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun daily --dry-run

# 2) 单票/多票结构审计（离线，优化器的标准尺子）
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun.optimizer.cli --root . --audit

# 3) 全市场扫描
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun scan --period day --limit 50

# 4) 回测 + HTTP 接口（接口起来后用 curl 打一遍）
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun backtest --codes 600000 --period day
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun web --port 8888
curl -s 'http://127.0.0.1:8888/api/snapshot?code=600000' | head -c 400
```

同时直接看数据产物：`data/meta.db`（SQLite）、`data/{period}/{market}/{code}.parquet`、
`data/quality_report.json`、`logs/*.log`。**数据本身的异常也是发现**——
「报表说 40 条全过，但跨源校验其实一条没跑成」就是这么发现的。

> 注意：baostock 是单会话串行的。如果 `logs/sync_fullmarket_day.log` 对应的
> 全市场同步进程正在跑（`kill -0 <pid>`），第 1 步只走**离线路径**
> （`store.read` + 本地 parquet + `--dry-run` + `--audit`），
> 不要去登录行情源抢会话，更不要 kill 那个进程。

### 第 2 步：记录异常，归因到模块与函数

每条异常都要落到 `文件:行`，例如：

```
src/chanlun/chan/signal.py:133  _third_kind: leave_i = p.end_idx + 1
```

「买卖点出不来」不是发现；「`_third_kind` 在 `signal.py:133` 把
`end_idx + 1` 当成离开段，而 `find_pivots` 的延伸循环保证
`end_idx + 1` 是离开之后的反向回试段」才是发现。归因不到具体行的，
说明还没查清楚，继续查。

### 第 3 步：分类，选取证路线

| 类别 | 判据 | 取证方式 |
| --- | --- | --- |
| **theory** | 改动的是缠论的判据本身（笔、线段、中枢、走势类型、背驰、买卖点） | 去 `theory/` 找条目，**逐字**引用原文摘录 |
| **engineering** | 崩溃、路径、并发、可观测性、错误提示、口径 | 不需要理论依据，但必须给**实测证据**（命令 + 原始输出） |

分类只看一件事：**这个改动会不会改变算法给出的结论**。会 → theory；
只是让已经算出来的东西被如实呈现 → engineering。
`data/quality.py`、`__main__.py`、`data/store.py` 都在允许范围内，
因为它们不是判据；`chan/**` 是判据，必须走 theory 路线。

### 第 4 步：做**最小**补丁，放进 `optimizer/patches/`

一次只改一个判据。补丁头必须自带依据，格式由工具生成，不要手写：

```bash
# 单文件
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/make_patch.py \
  --root . \
  --rel src/chanlun/chan/signal.py --edited /tmp/cw/signal_p1.py \
  --out optimizer/patches/round-001-G1a.patch \
  --kind theory --theory L20-THIRD-POINT-POSITION \
  --finding '第三类买卖点整个失效：_third_kind 把 end_idx+1 当成离开段' \
  --evidence 'cd chanlun && python optimizer/agent.py --audit   # 主干口径
{"signals": 0, ...}
cd chanlun && python optimizer/agent.py --audit --patch optimizer/patches/round-001-G1a.patch
{"signals": 40, ...}'
```

```bash
# 多文件（工程类常见：一处算、一处显示）
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/make_patch.py \
  --root . \
  --edit src/chanlun/data/quality.py=/tmp/cw/g5a_quality.py \
  --edit src/chanlun/__main__.py=/tmp/cw/g5a_main.py \
  --out optimizer/patches/round-009-G5a.patch \
  --kind engineering --evidence '...' ...
```

`quote:` 由工具从被引用的条目里**逐字**取出，所以引用不可能被改写。
补丁头形式（`# key: value`，位于第一行 diff 之前，`git apply` 会忽略）：

```
# kind: theory
# theory: L20-THIRD-POINT-POSITION
# quote: 一个次级别走势类型向上离开缠中说禅走势中枢，……
# source: 缠中说禅《教你炒股票108课》第20课
# finding: ...
# evidence: ...
# note: ...
```

### 第 5 步：量 before / after（同一把尺子）

```bash
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun.optimizer.cli --root . --audit
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun.optimizer.cli --root . \
  --audit --patch optimizer/patches/round-001-G1a.patch
```

`--audit` 是**只读**的：它在影子目录里打补丁、量数字、然后把影子目录删掉，
主干文件一个字节都不动。batch 里两句输出就是补丁头 `# evidence:` 的原文。

口径锁死（换了口径的数字不可比）：

- 24 只票（`agent.AUDIT_CODES`），日线，窗口 `2020-01-01`…`2026-09-29`；
- 一律用**裸代码** `store.read("600000", "day", ...)`；
- 指标取自 `Snapshot`：`segments / confirmed_segments / pivots / trends_* / signals`；
- 机制计数取自 `metrics.mech`：`pivot_max_segments / pivot_over_9 / pivot_segment_total`。

#### 5.1 一轮一冻结：`freeze_data()`

`data/` 是活仓库，全市场同步随时在删了重建。同一把尺子间隔几分钟就能量出不同
的数——不是代码变了，是仓库换了一半。所以每一轮 before/after 之前，先
`Optimizer.freeze_data()` 把这一轮要读的 24 只票复制成一份**冻结快照**，
两次测量都读这份字节；量完删掉。`--audit` 单跑也走这条路：读数必须有个明确的
口径，而不是「我跑的那一刻」。

每一轮的 journal 里因此多一行 `[口径]`：

```
[口径] 冻结快照 basis_sha256=c30f9a5a01f1c3e4，22/24 只票（缺 2 只：600000、000001）。
```

- `basis_sha256` 是快照里所有 Parquet 字节的指纹：**同一轮**的 before/after 必然同指纹；
- `缺 N 只` 是行情仓那一刻真的没有它们。缺票不影响**同一份快照内**的差值
  （before/after 读同一份字节），但绝对数不能跨轮直接比；
- 缺票造成的「数字变小」必须记成口径问题，**不许**记成「补丁让买卖点变少了」。

第一轮 24x7 周期里真出过这件事：取证开始时 24/24（`bars=39240`），全市场同步开始
重写 `data/day`，中途 22/24（`bars=35970`，缺 `600000`、`000001`）。所以：

| | 票数 | bars | G1a signals | G1c signals | G3a signals |
|---|---|---|---|---|---|
| 重建前 | 24/24 | 39240 | 0 → 40 | 0 → 44 | 0 → 43 |
| 重建中 | 22/24 | 35970 | 0 → 36 | 0 → 40 | 0 → 39 |

每轮都少 4 个信号，正好是缺掉的两只票的量级。**缺陷的形状与缺票无关**
（`exit_dir_matches_position=0`、`trend_group_prev_ok=0`、`path_exists=false`
这些判据不依赖某只具体的票），移动的只是绝对计数。journal 里 `finding` 那句话
里的计数写自 24/24 那一版，`before/after` 量自本轮快照——两者都真，差的只是口径，
所以每条记录里都留了这句说明。要复核就把 `record_rounds.py` 重跑一遍，
它会把当时的行情仓重新冻一份、重新量一次。

### 第 6 步：跑测试（apply → pytest → 反 apply）

```bash
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/run_patch_tests.py
```

它会逐个补丁 apply → `pytest -q` → 反 apply，并断言已跟踪文件逐字复原。
`tests` 字段填这个真实结果。

### 第 7 步：写 journal

```bash
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun.optimizer.cli --root . --rounds 10 --measure
```

`--measure` 会**当轮重新量** before/after，所以数字永远来自这次运行，
不是抄上一轮的结论。字段：

| 字段 | 含义 |
| --- | --- |
| `round` | 第几轮，从 1 开始 |
| `started_at` | 本地时区 ISO 时间 |
| `kind` | `theory` / `engineering` |
| `finding` | 一句话说清发现，带 `文件:行` |
| `evidence` | 命令 + 原始输出（theory 类给 `--audit` 两行；engineering 类给复现命令） |
| `theory_id` | theory 类必填，引用 `theory/` 条目 id |
| `patch_file` | 提案补丁文件名；没提案就是 `null` |
| `before` / `after` | 同一把尺子的数字 |
| `tests` | 测试结论（before/after/summary） |
| `status` | `proposed` / `rejected` / `inconclusive` / `circuit_break` |

### 第 8 步：判 status

- `proposed`：补丁通过校验，并且 before/after **数字确实动了**；
- `rejected`：补丁被校验拒了（缺理论依据、缺实测证据、触及主干但没引用条目）；
- `inconclusive`：证据不足以支持改动，或量出来是 no-op。
  **这不是失败**，这是「查清楚了但结论是『先别动』」，必须写清为什么；
- `circuit_break`：熔断（见第 3 节）。

### 第 9 步：检查熔断

看这轮的 `status` 与 journal 末尾：连续 3 轮无有效产出，或单轮测试绿→红 ≥2 次，
立刻停：写 journal、退出码 3、日志里打 `!!! 熔断 !!!`。

### 第 10 步：10 轮之后出汇总

每 10 轮输出一次：轮次/类别/发现/状态表、G1–G5 结论、before/after 数字、
熔断是否触发及为什么、以及 `git status --porcelain -uno` 为空的证据。

---

## 2. 提示词模板

### 2.1 理论类（改判据）

> 在 `chan/` 里找到一个与缠论原文不符、且**能用数字证明**的判据。
> 先说要动哪一行、现在错在哪、正确的定义出自第几课。
> 然后给出最小改动，并用 `--audit` 量出前后数字。
> 如果你找不到能证明「改完更好」的数字，就写 `inconclusive`，
> 在 `finding` 里说清卡在哪一步 —— 不许为了凑一个 `proposed` 而放松判据。
>
> 特别地：不要为了让买卖点变多而改判据。线段划分、中枢定义这些
> 「标准」，改松了信号当然会变多，但那是作弊。先把背驰段在图上的
> 位置找出来，再谈判据。

### 2.2 工程类（不动判据）

> 找一个「算出来的东西没有被如实呈现」的地方：口径被合并、
> 数据源不可达却显示满分、错误被静默吞掉、路径写法不对导致读空表。
> 给出**可复制粘贴的复现命令**和它的原始输出，然后改到输出如实为止。
> 工程类不需要理论依据，但必须有实测证据；一旦你的补丁会改变
> 算法结论，它就不再是工程类，回去走理论路线。

### 2.3 收尾类（写不动的结论）

> 这一轮的目标是**如实回答「要不要动」**，不是必须动。
> 把你量到的数字、想到的改法、以及「为什么现在不该改」写清楚：
> 是红线的哪一条挡住了？还是数据不足？还是影响面未知？
> 结论写 `inconclusive`，不许含糊成「建议后续观察」。

---

## 3. 熔断（`CircuitBreaker`）

三种触发：

| 触发 | 阈值 | 理由 |
| --- | --- | --- |
| 连续无有效产出 | 3 轮 | 连续 3 轮量不出任何变化，说明这把尺子在这个问题上已经没有信息量，继续跑只是烧电 |
| 单轮测试绿→红 | ≥2 次 | 一轮里测试反复由绿变红，说明改动在破坏既有行为，不是「顺手修一下」能解决的 |
| 已存在 `circuit_break` 记录 | 1 条 | 熔断是**持久**的：不人工 review 就不恢复 |

熔断后：

- CLI 退出码 `3`，systemd 靠 `RestartPreventExitStatus=1 3` 不重启；
- launchd 那边 `KeepAlive=true` 会一直拉起来，但每次都会立刻读到那条
  `circuit_break` 记录并以 3 退出（`ThrottleInterval=60` 兜住忙等），
  也就是**由优化器自己拒绝干活**，两条路都通到同一个结果；
- 人类看完 `optimizer/journal/` 里的记录后，用 `--reset-breaker` 恢复。

「有效产出」的判据是 `status == proposed` **且** `before != after`。
一个 `proposed` 但数字没动的补丁照样算无产出。

---

## 4. 加一条理论条目

`theory/*.md`，Markdown + 无需依赖的 front-matter：

```markdown
---
id: L20-THIRD-POINT-POSITION
source: 缠中说禅《教你炒股票108课》第20课《缠中说禅走势中枢级别扩张及第三类买卖点》
lesson: 20
url: https://chanlun108.cn/chanzhongshuochan108ke/20.html
applies_to: src/chanlun/chan/signal.py
tags: 第三类买卖点,中枢,ZG,ZD,位置
---

## 原文摘录

> 一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别走势类型回试，
> 其低点不跌破ZG，则构成第三类买点；……

## 释义

（这段原文对实现的约束，以及它**不允许**什么。）

## 适用算法

- `src/chanlun/chan/signal.py::_third_kind` —— 按位置判定 B3/S3。
```

`id` 必须唯一（重复会被 `load_theory` 拒绝），`theoretical` 的原文摘录必须是
**逐字**的：补丁头的 `quote:` 要跟它完全一致，对不上会被拒。
`applies_to` 决定这条依据能支撑哪些文件的改动。

---

## 5. 附录：当前十轮地图（第一轮 24x7 周期）

| 轮 | 观测点 | 类别 | 发现 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | G1a | theory | 第三类买卖点整个失效：`signal.py:133` 把 `end_idx+1` 当离开段 | proposed |
| 2 | G1b | theory | 第一类买卖点整个失效：`signal.py:162` 拿反向回试段做背驰同向比较 | proposed |
| 3 | G1c | theory | 嵌套 `if` 让 B3 分支的失败吞掉紧随其后的有效 S3 | proposed |
| 4 | G2a | theory | `pivot.py` 延伸循环没有九段上限，级别扩张被吸成一个中枢 | proposed |
| 5 | G2b | theory | `trend.py::_direction` 不检查相邻中枢重叠；本窗口实测 no-op | inconclusive |
| 6 | G3a | theory | 回试段可能是窗口右端的 TENTATIVE 线段，买点提前发出 | proposed |
| 7 | G4a | theory | 线段偏粗（最长 63 笔）但划分标准自检 0 问题；缺独立证据不动 | inconclusive |
| 8 | G4b | theory | 「唯一划分」实为「多个合法分界 + 贪心/DP 挑一个」 | inconclusive |
| 9 | G5a | engineering | 校验口径被合并：跨源与自洽共用一个数字，外部源不可达仍显示满分 | proposed |
| 10 | G5b | engineering | 带市场前缀的代码让 `store.read` 静默返回空表 | proposed |

G1–G4 是理论类（买卖点/中枢/走势类型/线段的判据），G5 是工程类（口径与读盘）。
每一轮的完整数字与原始输出都在 `optimizer/journal/round-NNN.json` 里，
`before`/`after` 可以直接重跑复核：

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun.optimizer.cli --root . --rounds 10 --measure
```

---

## 6. 24x7 部署

```bash
# Linux
sed -e "s#__PROJECT_DIR__#$PWD#g" -e "s#__PYTHON__#$(command -v python3)#g" \
    deploy/chanlun-optimizer.service > /tmp/chanlun-optimizer.service
sudo cp /tmp/chanlun-optimizer.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now chanlun-optimizer.service
tail -f logs/optimizer.log

# macOS
cp deploy/com.chanlun.optimizer.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chanlun.optimizer.plist
```

两个单元都跑同一行命令：

```bash
python optimizer/agent.py --root <chanlun> --rounds 10 --measure
```

**`--measure` 不能省。** 没有它，before/after 就没有数字，
每一轮都会被如实记成「无有效产出」，三轮之后熔断。
这不是 bug，是设计：没有测量就没有产出。
