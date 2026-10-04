# chanlun

自建的缠论股票分析系统：用 baostock 把 A 股行情落到本地 Parquet，用纯函数结构引擎算出
「包含处理 → 分型 → 笔 → 线段 → 中枢 → 走势类型 → MACD 背驰 → 三类买卖点」，
再在其上做收盘后扫描、自选池跟踪、point-in-time 回测，以及一个 FastAPI + ECharts 的
盘后看盘页。当前版本 `0.2.0`（版本号只在 `pyproject.toml` 写一次）。
这是一个**结构分析工具**，不是投资顾问。

## 能力边界

- 本地行情库：日线 + 60/30/15/5 分钟，Parquet 按 `(周期, 市场, 裸码)` 落盘；周线/月线由日线聚合。
- 结构快照：每只票每个周期一份结构指纹，带确认状态与 `confirmed_at`。分型/笔的
  `confirmed_at` 取「右侧合并K线走完」的时刻（D-36）；线段**段时钟**那一层整体偏保守
  （实测 `confirmed_at` 宁晚不早，`sh.600030` 残留 1 处、最大提前 3 根 bar）。但线段的
  **缺口确认**曾用整段序列求分型（前视），且左端起段用全序列 argmax 打分
  —— 两处已于 D-40 改为因果：
  第 71 课「已宣布分界点被后来的数据改写」的偏差从 **20 次 / 7855 段降到 14 次**
  （24 只票日线，`--sample 24 --stride 40`）。残留 14 次仍需有状态锁定才能清零，
  见下面「已知空白」。
- 全市场扫描 + 自选池跟踪：相对上次快照给出变化；时点回测：T+1、次日开盘成交、涨跌停限制、含费用。
- 看盘页：单页 SPA，查询 / 周期 / 复权 / 均线 / 图层 / 自选左栏。

不做：不提供行情数据（仓库里没有行情文件），不预测点位，不接实盘交易。

## 安装

要求 Python **>= 3.11**（`pyproject.toml`）。运行时依赖：baostock、pandas、pyarrow、
duckdb、fastapi、uvicorn[standard]、requests、pytdx、tqdm。

```bash
git clone https://github.com/XingLuoYunxiao/chanlun.git && cd chanlun
python -m venv .venv && source .venv/bin/activate
pip install -e .            # 跑测试/改代码再加开发依赖：pip install -e ".[dev]"
```

`dev` 组（`[project.optional-dependencies]`）含 `pytest` 与 `httpx2` —— 后者是
`starlette.testclient` 的运行时依赖（`tests/web/` 要用它起 ASGI 客户端），但它**不在**
starlette 的 `install_requires` 里，缺了连 `pytest` 收集 `tests/web/` 都会报错。
不安装也能直接跑：pytest 配置里已把
`src` 加进 `pythonpath`，CLI 加 `PYTHONPATH=src` 即可。

## 快速开始

```bash
# 1) 建品种表（要联网，默认 < 3 分钟；--enrich 会逐只查详情，很慢）
python -m chanlun universe
# 2) 同步日线（缺省全市场；baostock 逐只拉，配置注释记的是约 2 小时）
python -m chanlun sync --period day
# 3) 起看盘页 → http://127.0.0.1:8888
python -m chanlun serve
```

`serve` 的地址与端口缺省取 `config.toml` 的 `[web]`（`127.0.0.1:8888`），也可用
`--host` / `--port` 覆盖。**端口被占用时启动失败并明确报错，不会静默换端口** ——
换了端口，书签、自选池和外部脚本会一起失效。看盘页之外还有两处入口：
`GET /api/docs` 是 FastAPI 的 OpenAPI 交互文档，`python -m chanlun --help` 列出全部子命令。

## 数据从哪来

**仓库不带任何行情数据**：`.gitignore` 排除 `/data/`、`/logs/`、`/chanlun108/cache/`，
克隆下来是个空库，必须先同步页面才有东西可画。首次运行的顺序：

```bash
python -m chanlun universe                    # 品种表（含退市股，避免幸存者偏差）
python -m chanlun sync --period day           # 日线：缺省品种表全市场
python -m chanlun seed-watchlist              # 可选：自选池设成 7 个大盘指数并补齐日线
python -m chanlun sync --period 30            # 分钟线：缺省只跑自选池（全市场要 100 小时级）
python -m chanlun serve
```

- **数据源**：缺省 `baostock`（`[data].source`），复权口径 `bs_adjust = "2"`（前复权）。另一条
  路是 `tdx` —— 通达信 vipdoc 整包，不复权，官方只公开日线。换源是**换口径**（同一个
  `(code, "day")` 文件被原子替换），切换前必须先 `python -m chanlun factors` 把除权因子反推
  落库，否则前复权副本被覆盖后再也反推不出来。
- **存储**：`[data].root`（缺省 `data/`）下，`data/<周期>/<市场>/<裸码>.parquet` 存行情；
  `data/meta.db`（SQLite）存品种表、同步状态、结构快照、扫描结果、自选池、除权因子等；
  `data/calendar.csv` 是交易日历。
- **增量语义**：默认增量（从本地最后一根的下一个交易日起拉）；`--full` 从 1990-01-01 重拉；
  `--since` 只作用于**本地还没有数据的票**，已有数据的票仍是纯增量 —— 强行压低起点会在
  历史中间留洞，而结构会跨着洞算，比少几年历史更危险。
- **收盘后自动化**：`python -m chanlun daily` 走完同步 → 校验 → 全量结构快照入库 → 扫描 →
  自选池跟踪 → 推送；非交易日打印一行 INFO 后以 0 退出，`--dry-run` 彩排（不联网、不落库、
  不推送）。`deploy/` 下有 systemd / launchd 定时器样例。

## 命令行

`python -m chanlun [-h] [--version] {universe,sync,daily,tdx,factors,backtest,scan,serve,seed-watchlist} ...`

| 子命令 | 作用与主要参数 |
|---|---|
| `universe` | 重建品种表并打印数量；`--enrich` 逐只补全上市/退市信息（慢） |
| `sync` | 同步 K 线到 Parquet；`--period`（必填，`day/60/30/15/5`）、`--codes`、`--full`、`--since`、`--workers` |
| `daily` | 收盘后一键流水线；`--dry-run`、`--periods`、`--codes`、`--source baostock\|tdx`、`--tdx-src`、`--no-download`、`--since`、`--workers`、`--notify console\|file\|null`、`--notify-path` |
| `tdx` | 导入通达信 vipdoc 整包；`--download`、`--src`、`--period`、`--markets`、`--kinds`、`--codes`、`--dry-run` |
| `factors` | 反推除权因子并入库；`--codes`、`--all`、`--source infer`、`--src`、`--period`、`--min-overlap`、`--dry-run` |
| `backtest` | point-in-time 回测；`--codes`（必填）、`--start`、`--end`、`--period`、`--mode strict\|loose`、`--cash`、`--strategy`、`--benchmark` |
| `scan` | 全市场扫描 / 自选池跟踪；`--periods`、`--codes`、`--workers`、`--top`、`--watch`、`--notify-file`、`--no-save`、`--run-date` |
| `serve` | 启动盘后看盘页；`--host`、`--port` |
| `seed-watchlist` | 把自选池重置成 7 个大盘指数；`--keep`、`--no-sync`、`--since` |

`--version` 挂在顶层，`python -m chanlun --version` 直接答出版本号。完整参数以
`python -m chanlun <子命令> --help` 为准。

## 运行测试

```bash
cd chanlun
pytest                        # 全量（默认排除联网用例）
pytest tests/test_version.py -v
pytest -m live                # 只跑联网用例，需要真实网络
```

pytest 配置（`pyproject.toml` 的 `[tool.pytest.ini_options]`）：`testpaths = ["tests"]`、
`pythonpath = ["src"]`、`addopts = "-m 'not live'"` —— **依赖 baostock / 东财 / 腾讯的联网
用例默认被排除**，一轮要 8 分钟以上，而「跑得快」是能天天跑、能天天信的前提。

本机 checkout 的依赖装在 `../.venv-chanlun/`，所以按 `AGENTS.md` §4 的写法是
`cd <本项目根目录> && ../.venv-chanlun/bin/pytest`（在项目根目录里直接跑
`../.venv-chanlun/bin/pytest` 等价）。

## HTTP 接口

路由全部定义在 `src/chanlun/web/api.py`，`web/app.py` 负责建应用、挂静态目录与端口检查。
`mode`（买卖点口径 `strict` / `loose`，缺省 `strict`，非法值 422）与 `adjust`（复权口径）
是**两个独立维度**，别混。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/`、`/api/docs` | 看盘页 `index.html`（不进 OpenAPI）、Swagger UI |
| GET | `/api/health`、`/api/version` | 健康信息（ok / port / data_root / periods / version / disclaimer）；版本号及其来源，不碰配置与数据库 |
| GET | `/api/universe` | 品种表；`limit`（1–10000，缺省 200）、`q` 搜索 |
| GET | `/api/bars` | K 线；`code`、`period`、`limit`、`adjust`、`ma` |
| GET | `/api/structure` | 结构快照 + 行情 + MACD；`code`、`period`、`limit`、`merged`、`adjust`、`ma`、`mode` |
| GET | `/api/scan` | 扫描结果；`period`、`date` |
| GET/POST/PATCH/DELETE | `/api/watchlist` | 自选池列表 / 加自选（`code`/`name`/`note`）/ 上下移（`code`/`delta`，仅 ±1）/ 删（`code`） |
| GET | `/api/watchlist/structure` | 自选栏批量结构；`period`、`limit`、`adjust`、`mode` |
| POST | `/api/sync` | 触发同步，立刻返回 **202**，取数在后台线程 |
| GET | `/api/sync/status` | 轮询同步进度；`code`、`period` |

`/api/structure` 找不到数据时返回 404，响应体带 `syncable` / `sync_hint`，页面据此决定要不要给
「同步这个周期」按钮 —— 这个判据与 `POST /api/sync` 的 400 文案同源，只有一处。

## 目录结构

```
src/chanlun/
├── chan/          结构层：include 分型 笔 线段 中枢 趋势 MACD 买卖点 + 引擎
│                  types.py 定义 frozen dataclass 与三态 Status；state.py 管稳定 ID 与 PIT 过滤
├── data/          数据层：取数 → 复权 → 落库 → 校验；Parquet 行情 + meta.db + 因子表
├── backtest/      point-in-time 回测（broker / runner / metrics / strategy）
├── scan/          全市场扫描与自选池跟踪
├── web/           FastAPI 应用 + api.py 路由 + static/ 单页前端
├── optimizer/     只提案、不改主干的优化器（运行已暂停）
└── __main__.py    CLI 总入口（子命令参数长在各模块自己身上）
tests/             pytest 用例：chan/ data/ backtest/ scan/ web/ optimizer/
chanlun108/        《教你炒股票》108 课原文 + 评论 + 禅师回复的本地存档
docs/evidence/     实测证据（严格性审计、优化器发现、验收截图）
deploy/            systemd / launchd 定时任务
optimizer/         优化器的提案、日志、理论条目与测量脚本
```

`config.toml` 里所有相对路径都相对**项目根**解析（`config.PROJECT_ROOT`），从哪个目录调用 CLI 都行。

## 文档

| 文档 | 内容 |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 分层、不变量、判据为什么是这个值（`D-xx` 决策记录）、故障排查索引 |
| [`CHANGELOG.md`](CHANGELOG.md) | 变更记录，含回填的里程碑版本 |
| [`AGENTS.md`](AGENTS.md) | 维护规则、版本号规则、108 课存档索引 |
| [`chanlun108/README.md`](chanlun108/README.md) | 108 课存档的覆盖度、三处已知缺口、怎么重跑与检索 |
| [`docs/evidence/`](docs/evidence/) | 实测证据，含 [`2026-10-01-chanlun-strictness-audit.md`](docs/evidence/2026-10-01-chanlun-strictness-audit.md) 与 [`2026-10-03-chanlun-compliance-findings.md`](docs/evidence/2026-10-03-chanlun-compliance-findings.md) |
| [`optimizer/theory/`](optimizer/theory/) | 判据变更的理论依据（逐字摘录课号 + 原句） |
| [`deploy/README.md`](deploy/README.md) | 部署与日线换源 |

## 已知空白与口径声明

下面每一条都能在代码或证据文件里查到 —— **请按「本系统与缠论原文有已知差距」来理解它，
而不是「严格遵循缠论」**。

- **线段划分用的是全局择优，不是原文的当下程序；D-40 只修掉了「向后借用数据」那两处。**
  第 71 课讲的是「假设某转折点是分界点 → 用划分的两种情况考察 → 不满足则原线段延续」，
  是单遍左到右的局部判定；代码仍用记忆化搜索取「还能确认的线段数最多」的划分
  （`chan/segment.py` 里 `best()` 的注释自陈动机）。审计实测过原文读法，在 13 只标的上
  只剩 202 段、`sh.600759` 5 分钟 1948 笔只划出 1 段，因此 DP 被保留 ——
  但这是**显式口径选择**，不是「与原文一致」。
  D-40 修掉的是两处**各自独立**的「向后借用数据」：缺口确认曾取整段序列求分型
  （用到了当前前缀之外的笔），左端起段曾按**全序列 argmax** 打分（数据一多起点就往前跳）。
  两处都改成只看前缀后，第 71 课**单调性**违规从 **20 次 / 7855 段降到 14 次**
  （24 只票日线，`--sample 24 --stride 40`）；**残留 14 次**需要跨次调用锁定已宣布
  分界点（有状态），本轮没做 ⇒ 仍然**不是**「与原文一致」。
  **注意不要写成「前视已彻底消除」**：段时钟（`confirmed_at`）整体偏保守
  （早于 0/1、晚于 12/21、负对照缺失 0/0），但 D-40 后 `sh.600030` 仍有 **1 处、
  最大提前 3 根 bar**。
  已记为 `ARCHITECTURE.md` 的 **D-40**（并给 D-39 追加了第三轮变更历史）；
  被否决的替代方案包括「起点固定第 0 笔」（违规少但塌结构：最长段 37 → 376）、
  第 71 课字面程序（违规 20 → 28）与「已宣布即永久」（违规 0 但 36 个测试失败）。
  复现：
  `PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_segment_present_tense.py --sample 24 --stride 40`。
- **笔采用老笔口径，且第 77 课第二条判据未实现。** `MIN_GAP = 4`（`chan/stroke.py`）等价于
  「顶底之间至少一根独立合并 K 线」；禅师后来放宽的「新笔」未采用。第 77 课对笔还有第二条
  判据（顶分型中最高 K 线的区间至少要有一部分高于底分型中最低 K 线的区间），代码只查间隔、
  **全文无价格序检查**，全市场实测有数千例（见 `docs/evidence/2026-10-03-chanlun-compliance-findings.md` F-15）。
- **九段式中枢不处理。** `chan/pivot.py` 的模块 docstring 自己写着：本实现不处理
  「离开段后又回到中枢」的九段式中枢。
- **中枢扩展 / 级别扩展在生产链路里不可达。** `merge_pivots` 是独立函数，只有测试调用它；
  第 29 课的「最后一个中枢的级别扩展」从未真正执行（第 20 课中心定理二只落实了「判涨/跌」
  与「不构成趋势」，没有真的合成高级别中枢）。
- **背驰只实现了面积判据。** 第 27 课给了两个各自充分的判据，代码只实现「柱子面积明显小于
  前段」；「黄白线回抽 0 轴再次下跌不创新低」未实现（MACD 的 `dif_high` / `dif_low` /
  `dif_extreme` 有定义但 `chan/` 内无调用）。性质是**覆盖度缺口**（会漏掉「面积不符合而
  黄白线符合」的背驰），不是判据写错。
- **盘整背驰只覆盖一半。** 第 39 课口径（同向隔一段的两段相比）已在 `SignalMode.LOOSE` 下
  实现；第 24 课的 A/B/C 三段判据仍未实现。
- **第一类买卖点的区间套定位未实现**，第二类买卖点下钻次级别是近似处理。
- **走势类型不上看盘页**：结构快照里没有 `trends` 字段，`/api/structure` 不返回它。
- **回测未建模滑点，也不区分 ST 的 5% 涨跌停。**

`ARCHITECTURE.md` 的「已知空白」表与 `docs/evidence/` 下的两份审计是这些结论的一手来源，
其中也记录了若干**未确认项**（例如某些注释里的「原文空白项」判断未独立核对原文）。

## 免责声明

接口自己声明：**仅结构信号提示，不构成投资建议；结构为收盘后确认，非盘中实时。**
这是分析工具，不是投资顾问，作者不对任何使用结果负责。
