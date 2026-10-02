# AGENTS.md

给在这个仓库里干活的 AI agent（以及人）的作业规则。

项目：`chanlun` —— 自建缠论股票分析系统。仓库根 `/Users/zzz/workspace`，
本项目根 `/Users/zzz/workspace/chanlun`。

---

## 1. 必须维护的两份文档

**这是硬性要求，不是建议。**

| 文档 | 什么时候必须更新 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 只要改动涉及**分层、数据流、不变量、判据、常量、口径、API 形状** |
| [CHANGELOG.md](CHANGELOG.md) | **每一次**值得被记住的行为变更 |

### 判据

「值得被记住」= 满足以下任意一条：

- 用户能观察到行为变化（页面显示、CLI 输出、信号数量）。
- 某个数值、判据、口径变了（哪怕是"从 9 改成 8"）。
- 修掉了一个 bug，且这个 bug 的成因**可能再次发生**。
- 新增/移除了模块、依赖、API 端点、CLI 命令。
- 发现了新的**已知空白**或**未实现项**。

**不记**：纯重构（行为不变）、注释错别字、格式调整、测试内部调整。

### 只增不改的纪律

- **已发布的 CHANGELOG 条目不要改。** 发现写错了就在 `[Unreleased]` 里补一条
  `修复`，说明"上一版记录有误，实际是……"。历史记录的价值在于它**当时**是什么样。
- **每条条目描述的必须是「该版本提交时」的代码状态**，不是写文档那一刻的工作区状态。
  工作区里经常有未提交的改动，**把未提交的东西记进已发布版本号，等于伪造历史** ——
  而且它会让排查者去老版本里找一个根本不存在的判据。
  未提交的改动一律记在 `[Unreleased]`，并在那里写明"本节改动尚未提交"；
  发版时再原样移进新版本号。
  > 这条规则是因为真犯过：`0.0.3` / `0.0.4` 曾把工作区里**未提交**的
  > GG/DD 剔除离开段、MACD 分色面积、趋势改比 `[DD,GG]` 三处改动，
  > 记成了当时的已发布状态（提交前的主干其实是 `max/min(... for s in group)`、
  > `sum(abs(hist))`、比 `[ZD,ZG]`）。三处已在 `[Unreleased]` 更正。
- **回填的 `0.0.x` 条目描述的是该里程碑的提交状态。** 后续修复属于 `[Unreleased]`
  或新版本，**不回填进老版本**。
- **ARCHITECTURE.md 的决策记录（D-xx）编号永不复用、永不删除。** 决策被推翻时，
  在原条目里补一段「**变更历史**」，说明改成什么、为什么、哪个版本改的。
  被推翻的决策本身是重要信息 —— 它是"为什么当初那样做"的唯一答案。

### 决策记录怎么写

ARCHITECTURE.md 的决策记录用 `D-xx` 编号，四段式：

```markdown
#### D-xx <决策一句话>

**决策**：具体是什么（带 `文件:行号`）。

**依据**：为什么。有原文就引原文（缠论判据必须引《教你炒股票》课号 + 原句）。

**被否决的替代方案**：还考虑过什么、为什么不选。这一项最容易被省略，
但它是排查"为什么不是另一种写法"的唯一线索。

**后果 / 变更历史**：代价是什么、什么时候改过。
```

**判据类变更必须先有 `optimizer/theory/` 条目**（见 §5）。

---

## 2. 版本号规则

版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)：
`MAJOR.MINOR.PATCH`。

### 唯一真相来源

**版本号只在 [`pyproject.toml`](pyproject.toml) 的 `[project].version` 写一次。**

```toml
[project]
name = "chanlun"
version = "0.1.0"
```

其它地方一律**读取**，不许复制：

- `src/chanlun/version.py` —— `version_info()` 解析并返回
  `{"version", "source"}`；`__version__` 从它导出。
- `src/chanlun/__init__.py` —— 再导出 `__version__` / `version_info`。
- `python -m chanlun --version`
- `GET /api/version`、`GET /api/health` 的 `version` 字段

**禁止**在 `src/` 下出现 `__version__ = "..."` 这类硬编码。
`tests/test_version.py::test_no_hardcoded_version_assignment_in_source` 会拦住它。

### 什么时候涨哪一位

| 变化 | 涨 | 本项目中的例子 |
|---|---|---|
| 破坏性变更 | `MAJOR` | 落库键规则改变、`(code, period)` 口径改变、API 返回结构破坏 |
| 向后兼容的新功能 | `MINOR` | 新增周期、新增买卖点类型、新增 API 端点 |
| 向后兼容的修复 | `PATCH` | 判据纠正、显示错误、崩溃修复 |

### 0.x 期的特别约定

当前基线是 **`0.1.0`**，**继续留在 `0.x` 系列**（用户 2026-10-01 决定）。

按 SemVer，`0.y.z` 是初期开发，任何东西都可能随时变，破坏性变更放在 `MINOR` 位。
本项目的额外约定：

> **「判据变更」一律当破坏性变更处理。**

理由：判据变更会让**同一只票昨天的划分结果和今天不同**。对用户来说，
这比一个 API 字段改名更具破坏性 —— 它改变的是"系统认为市场是什么样"。
所以中枢延伸上限从 9 收到 8，涨的是 `MINOR`（`0.0.11`），不是 `PATCH`。

**什么时候升到 `1.0.0`**：当判据稳定、且 API 与落库格式承诺向后兼容时。
现在还没到。

### 发版时要做的事

1. 改 `pyproject.toml` 的 `version`。
2. 在 `CHANGELOG.md` 顶部把 `[Unreleased]` 的内容移进新的
   `## [x.y.z] - YYYY-MM-DD`，并补一个新的空 `[Unreleased]`。
3. 跑全量测试（见 §4）。
4. 本仓库**没有 git remote**，所以不写版本对比链接。
   将来加了 remote 再补。

---

## 3. 《教你炒股票》108 课存档索引

系统里每一个缠论判据都应该能追到原文。原文存档在
**[`chanlun108/`](chanlun108/)**（随系统源码一起放在本目录下）。

### 存档里有什么

| 路径 | 内容 |
|---|---|
| [`chanlun108/README.md`](chanlun108/README.md) | **先读这个**。覆盖度、三处已知缺口、怎么重跑、怎么检索 |
| [`chanlun108/索引.md`](chanlun108/索引.md) / [`索引.csv`](chanlun108/索引.csv) | 108 课总索引（课号/标题/日期/tid/字数/评论数/禅师回复数/文件名） |
| [`chanlun108/原文/`](chanlun108/原文/) | **108 课原文**，一课一文件，`NNN-<标题>.md` |
| [`chanlun108/回复/`](chanlun108/回复/) | 每课的全部回复（含读者提问与禅师答复） |
| [`chanlun108/问答/`](chanlun108/问答/) | 只抽出的问答对（108 课，其中 62 课非空） |
| [`chanlun108/原文合集-全108课-单文件.md`](chanlun108/原文合集-全108课-单文件.md) | 全文单文件（约 26.2 万字），适合通读 |
| [`chanlun108/禅师回复合集-全108课.md`](chanlun108/禅师回复合集-全108课.md) | 禅师回复单文件 |
| [`chanlun108/tools/crawl_chanshi.py`](chanlun108/tools/crawl_chanshi.py) | 抓取工具（见下） |
| `chanlun108/cache/` | 抓取缓存（141 MB，**不入库**） |

### 怎么查

```bash
# 按关键词全文检索原文
grep -rn "中枢的延伸" chanlun108/原文/

# 查某一课
sed -n '1,40p' "chanlun108/原文/033-走势的多义性.md"

# 查索引里某一课的元数据
grep "^33," chanlun108/索引.csv
```

**引用原文时，必须写清课号 + 原句。** 例：

> 第 33 课《走势的多义性》：「中枢的延伸不能超过5段，也就是一旦出现6段的延伸，
> 加上形成中枢本身那三段，就构成更大级别的中枢了。」

不许转述成"禅师说过中枢不能延伸太久"—— 判据要靠原句的字面量。

### 抓取工具

```bash
cd chanlun108
python3 tools/crawl_chanshi.py catalog     # 列出论坛板块/课表
python3 tools/crawl_chanshi.py articles    # 抓 108 课原文
python3 tools/crawl_chanshi.py sina        # 抓新浪原站评论（权威来源）
python3 tools/crawl_chanshi.py comments    # 抓论坛汇编评论
python3 tools/crawl_chanshi.py qa          # 生成问答
python3 tools/crawl_chanshi.py index       # 生成索引
python3 tools/crawl_chanshi.py check       # 覆盖度核对
```

**注意**：

- 抓取是**有礼貌**的：`DELAY = 1.0` 秒、无并发。**不要在别的任务跑着的时候
  并行抓新浪** —— 它会用 `A00001` 限流，而那个响应看起来像"列表到底了"。
- **`cache/` 让重跑免费。** 改解析逻辑时优先重解析缓存，不要重新联网。
- **三处已知缺口不是抓取失败**：第 34 / 72 / 80 课新浪原站**博主已关闭评论**。
- 第 2 楼及以后在 `fid=198` 需要登录权限；非门禁板块（`fid=220` 等）的回复公开。
- **只跨源去重，不删各源自己的重复。** 优先级：新浪原站 > 博客存档 > 论坛汇编。

### 引用存档里的作者名要小心

`chzhshch-108-plus` 的来源说明里提到 `CCTV` / `罗锅` 是**疑似**禅师马甲。
**没有确证之前不要称它们为禅师**，一律标注"疑似"。
禅师的权威判定是新浪 uid `1215172700`，**不是昵称**。

---

## 4. 开发与验证

### 环境

```bash
cd /Users/zzz/workspace/chanlun

# 跑测试（必须从这个目录跑）
../.venv-chanlun/bin/pytest

# 跑单个文件
../.venv-chanlun/bin/pytest tests/test_version.py -v

# 跑 CLI（需要 PYTHONPATH）
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun --version

# 起看盘页
PYTHONPATH=src PYTHONUNBUFFERED=1 nohup ../.venv-chanlun/bin/python \
  -m chanlun serve > /tmp/serve.log 2>&1 &
# → http://127.0.0.1:8888
```

- **联网测试默认不跑**（`addopts = "-m 'not live'"`）。
- 机器：macOS arm64，TZ = Asia/Shanghai，**没有 `timeout` 命令**。
- 依赖装在 `/Users/zzz/workspace/.venv-chanlun/`。

### 改完必须做的事

1. **跑全量测试。** 不许只跑自己改的那个文件就宣布完成。
2. **改了 `src/chanlun/**/*.py` 要重启看盘页** —— `web/app.py` 用
   `uvicorn.run(...)` 且**没有 `--reload`**。
   改 `web/static/` 下的静态文件只需浏览器刷新（文件从磁盘直读）。
3. **更新 ARCHITECTURE.md / CHANGELOG.md**（见 §1）。
4. **UI 相关改动必须用真实浏览器核对。** 截图只能看布局，
   **文案必须从 DOM dump 里读** —— 截图上的小字会骗人。

### 提交纪律

- **绝不 `git add -A`，绝不 `git add chanlun/`。** 逐个列路径。
  会话工作区里可能有其它 agent 的并行改动，还有未入库的 `3d-fitting-room/`。
- **绝不 `git checkout -- <file>` 去"清理"一个还带着未提交工作的文件。**
  这个事故已经发生过一次，丢掉了中枢延伸收口到 8 段的工作。
- 提交信息用中文 Conventional Commits 风格（`feat(web):` / `fix(chan):` /
  `docs:` / `chore:`），和已有 46 个提交保持一致。

---

## 5. 判据变更的特殊规则

缠论判据不是"随手调个参数"。改判据的流程：

1. **先在 [`optimizer/theory/`](optimizer/theory/) 写理论条目** ——
   引用《教你炒股票》课号 + **逐字**摘录原文。不允许改写或编造原文摘录。
2. **在 ARCHITECTURE.md 补/改 `D-xx` 决策记录**，写清被否决的替代方案。
3. **在 CHANGELOG.md 记为 `MINOR` 版本变更**，说明"这会让同一只票的划分结果
   与上一版不同"。
4. **给出可失败的测量** —— 数字要能从写下的命令复现。
   "改完看起来更合理"不是证据。
5. **收尾：查前端有没有把旧口径写死在文案里。**
   `grep -rn "ZD,ZG" src/chanlun/web/static/`（把关键词换成你改的那个判据）。
   **测试只覆盖后端判据，文案分叉不会被任何测试拦住。**
   这次就漏了 —— 后端改判 `[DD,GG]` 之后，页面图注还印着"本系统只判 ZG/ZD 单调"，
   用户看到的说明和图上结果相反。

### 证据质量规则

- **测量必须能失败。** 如果一个指标无论怎么改都是满分，它没有在测量任何东西。
- **数字必须可复现**：写下命令，别人重跑应得到同一个数。
- 实测数字**逐项相同**时，如实记成 `inconclusive`（没动就是 no-op），
  不许记成"验证通过"。

### 优化器的状态

`optimizer/` 的代码是**活的**（只提案模式，绝不改主干），
但**运行被暂停** —— 2026-10-01 用户明确指示"不用跑这个优化器了"。

**不要**运行 `run_patch_tests.py` / `record_rounds.py`，不要起新一轮，
除非用户明确要求恢复。

> **「暂停」指的是不再起新一轮，不是否认已有产物。**
> 磁盘上已经有 `optimizer/patches/round-001` … `round-020`、对应的
> `optimizer/journal/round-*.json`、`optimizer/theory/*.md`（12 篇）与
> `optimizer/tools/measure_*.py`（16 个）。
> **这些历史产物属于 CHANGELOG / ARCHITECTURE 的记录范围** ——
> 优化器过去做过的判据审计（G 系列、D 系列）该记的照记。
> 被叫停的只有"再跑一轮"这一件事。

---

## 6. 写文档的风格

- **中文输出。**
- **忠于代码与原文，不发明概念。** 每条论断带 `文件:行号` 或课号。
- **区分「已验证」与「未确认」。** 未确认的必须显式标注，
  不许用推测填空。ARCHITECTURE.md §8 就是为此存在的。
- **不写个人交易建议。** 这是分析工具，不是投资顾问。
- 数据质量存疑时**照实记录，不谎报**（`seed-watchlist` 找不到指数时就是这么做的）。

---

## 7. 相关文档

| 文档 | 内容 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构、不变量、决策记录（D-xx）、故障排查索引 |
| [CHANGELOG.md](CHANGELOG.md) | 变更记录 |
| [chanlun108/README.md](chanlun108/README.md) | 108 课存档说明 |
| [docs/evidence/](docs/evidence/) | 实测证据 |
| [optimizer/theory/](optimizer/theory/) | 判据变更的理论依据 |
| [deploy/README.md](deploy/README.md) | 部署 |
| [config.toml](config.toml) | 配置项 |
