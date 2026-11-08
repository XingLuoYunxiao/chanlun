# 架构设计

`chanlun` —— 自建缠论股票分析系统。

本文件回答三个问题：

1. **系统长什么样**（分层、数据流、不变量）—— 让你能定位"这块代码在哪一层"。
2. **每个判据为什么是这个值**（决策记录）—— 让"为什么是 8 段而不是 9 段"这类问题
   有一个可查、可反驳、可追溯的答案，而不是只存在于某段代码注释里。
3. **出了故障该先看哪里**（排查索引）—— 从症状反查层与决策。

> **维护要求**：本文件与 [CHANGELOG.md](CHANGELOG.md) 必须随代码同步更新。
> 具体规则见 [AGENTS.md](AGENTS.md)。
> 数据层的 `data/` 与结构层的 `chan/` 是核心；外围层（web/scan/backtest/optimizer）
> 的改动如果**改变了判据或口径**，也算核心变更。

---

## 1. 系统全景

```
                    ┌─────────────────────────────────────────┐
   外部数据源        │  baostock（主源，前复权，分钟自2020起）  │
                    │  通达信 vipdoc 整包（换源，不复权日线）  │
                    │  东财 / 腾讯（仅跨源校验，不落库）       │
                    └────────────────┬────────────────────────┘
                                     │
                    ┌────────────────▼────────────────────────┐
   数据层            │ data/  取数 → 复权 → 落库 → 校验        │
   src/chanlun/data │ Parquet 行情 + SQLite meta.db + 因子表   │
                    └────────────────┬────────────────────────┘
                                     │  BarFrame
                    ┌────────────────▼────────────────────────┐
   结构层            │ chan/  包含处理→分型→笔→线段→中枢       │
   src/chanlun/chan │        →走势类型→MACD背驰→三类买卖点    │
                    └────────────────┬────────────────────────┘
                                     │  Snapshot
        ┌──────────────┬─────────────┼─────────────┬──────────────┐
        ▼              ▼             ▼             ▼              ▼
   ┌────────┐   ┌──────────┐   ┌─────────┐   ┌─────────┐   ┌──────────┐
   │ web/   │   │ scan/    │   │backtest/│   │optimizer│   │ __main__ │
   │ 看盘页 │   │ 全市场   │   │ 时点回测│   │ 提案模式│   │ CLI 总入口│
   │ :8888  │   │ 扫描+自选│   │         │   │ 24x7    │   │ daily    │
   └────────┘   └──────────┘   └─────────┘   └─────────┘   └──────────┘
```

### 分层职责

| 层 | 目录 | 单一职责 | 关键约束 |
|---|---|---|---|
| 入口 | `__main__.py`（1,041 行） | CLI 总入口与命令分发 | 命令的语义在各自子模块，`__main__` 只做分发 |
| 配置 | `config.py`（75 行） | 读 `config.toml` → 冻结 dataclass | 端口固定 8888，运行期不允许静默切换 |
| 数据 | `data/`（12 模块） | 取数、复权、落库、校验 | `(code, period)` 只能有一个写入方 |
| 结构 | `chan/`（12 模块） | 缠论结构计算 | 划分唯一性；禁止未来函数 |
| 看盘 | `web/` | HTTP API + 单页前端 | 结构全史算完再裁窗口 |
| 扫描 | `scan/` | 全市场扫描、自选池跟踪 | 强度可分解核对 |
| 回测 | `backtest/` | 逐 bar 物理时点回测 | 物理截断，不用未来数据 |
| 优化 | `optimizer/` | 只提案的自优化循环 | **绝不改主干代码** |
| 通知 | `notify.py` | 本地通知出口 | **不联网**是硬要求 |

---

## 2. 数据层（`src/chanlun/data/`）

`data/__init__.py` **是空文件（0 行）**，无 re-export。调用方必须
`from .data import store` 式直接引用子模块 —— 这是有意的：数据层没有"包级门面"，
谁依赖哪个子模块在 import 行上一眼可见。

### 2.1 模块与依赖方向（单向，无环）

| 模块 | 单一职责 | 行数 |
|---|---|---|
| `types.py` | 全系统唯一的 BarFrame 契约 + `normalize`/`truncate` | 116 |
| `store.py` | Parquet 单文件读写（原子写 + 按 ts 去重合并） | 126 |
| `markets.py` | 号段 → 交易所的**唯一**判定表 | 214 |
| `baostock_source.py` | baostock 取数：登录/重试/字段口径/前缀校验 | 247 |
| `tdx.py` | 通达信 vipdoc 解析、整包导入、北交所改号识别 | 326 |
| `adjust.py` | 复权三态（raw/qfq/hfq）的 apply / unapply / 因子反推 | 134 |
| `factors.py` | 从"TDX 不复权 + 库内前复权"反推因子阶梯 | 159 |
| `periods.py` | 周/月由**本地日线**聚合 | 125 |
| `meta.py` | SQLite schema 与全部读写（9 张表） | 447 |
| `sync.py` | 单票单周期的取数与落库编排 | 209 |
| `quality.py` | 复权哨兵校验（本地自洽 + 跨源比对） | 356 |
| `universe.py` | 全市场 A 股清单（含退市股） | 288 |

依赖方向：`types` / `config` / `calendar` 是最底层；
`markets` ← `store`、`baostock_source`、`tdx`、`sync`；
`store` / `meta` ← `sync`、`tdx`、`factors`、`quality`、`universe`；
`adjust` ← `factors`。

**`data/` 内部没有任何模块依赖 `periods` 或 `web`。**
复权与聚合的先后顺序由消费方（`web/api.py`）强制，数据层本身不强制 ——
这一点是刻意的，见决策 [D-07](#d-07-复权必须排在聚合前面)。

### 2.2 存储布局

```
data/                                    ← config.PROJECT_ROOT / "data"，可被 config.toml 覆盖
├── day/{sh,sz,bj}/{bare_code}.parquet   ← 日线
├── 30/…  5/…  60/…  15/…                ← 分钟线
├── meta.db                              ← SQLite，9 张表
├── calendar.csv                         ← 交易日历
└── quality_report.json                  ← 质量报告
```

- 路径模板：`data_root() / period / market_of(code) / f"{bare_code(code)}.parquet"`
- **文件名不含市场前缀**，市场由父目录表达。否则 `data/day/sh/sh.000300.parquet`
  既丑，且 `sh.000300` 与将来可能的 `sz.000300` 无法共存。
- **`data/week` 与 `data/month` 不存在也不应该存在** —— 周/月是派生周期，
  由日线在内存里聚合。
- 9 张 SQLite 表：`sync_state(code,period)`、`universe(code)`、
  `dividend_event(code,ex_date)`、`quality_flag(code,period,kind)`、
  `structure_snapshot(code,period)`、`scan_result(id)`、`watchlist(code)`、
  `symbol_alias(old_code)`、`adjust_factor(code,ts)`。

### 2.3 BarFrame 契约

```
列固定为 ["ts","open","high","low","close","volume","amount"]
ts 为字符串：日线 "YYYY-MM-DD"，分钟 "YYYY-MM-DD HH:MM"
按 ts 升序且唯一
```

`normalize()` 幂等：补列 → 数值化 `fillna(0.0)` → 剔除空 ts →
**剔除任一价格列 ≤ 0 的行**（留 warning）→ `drop_duplicates(keep="last")` → 稳定排序。

- `volume`/`amount` 为 0 属正常，**不剔除**（停牌、无量涨停）。
- 剔除 0 价行是必须的，见决策 [D-03](#d-03-剔除-0-价行)。

### 2.4 数据层不变量

| # | 不变量 | 位置 |
|---|---|---|
| 1 | `write` 走同目录临时文件 + `os.replace`，失败删临时文件、绝不动已有数据 | `store.py:64-85` |
| 2 | `upsert` 按 ts 合并，新数据覆盖旧数据，返回**新增**行数 | `store.py:103-117` |
| 3 | 文件缺失时 `read` 返回空表而非报错 | `store.py:96-97` |
| 4 | **复权只改价格，不改 volume/amount** | `adjust.py:10-11, 93-94, 116-117` |
| 5 | 库内只存一种口径原始价 + 一张因子阶梯 `k_t` | `adjust.py:3-4` |
| 6 | `_k0` 取**因子表首段**而非窗口首根 | `adjust.py:69-78` |
| 7 | 因子表首段之前的 K 线**沿用第一段**（外推），不留空 | `adjust.py:55-66` |
| 8 | 因子缺失时三态原样返回；`apply_adjust` 不改原表 | `adjust.py:82-90` |
| 9 | `unapply_adjust` 与 `apply_adjust` 是**两条相反公式** | `adjust.py:107-108` |
| 10 | 反推因子必须在 `day` 切到通达信整包**之前**跑 | `factors.py:12-16, 97-103` |
| 11 | 反推要求重叠 ≥ `MIN_OVERLAP = 20` 根 | `factors.py:34-35, 104-109` |
| 12 | 整段 k 恒为 1 视为"没有除权"，不写入 | `factors.py:113-119` |
| 13 | **复权必须排在聚合前面** | `web/api.py:326-340` |
| 14 | 派生周期**先聚合再截断**，聚合永远基于全量日线 | `web/api.py:282-283, 304-309` |
| 15 | 周/月分组用**自然周（ISO 年+周号）/ 自然月** | `periods.py:12-13, 112-124` |
| 16 | 当周/当月没走完也出一根，故提供 `is_complete` | `periods.py:16-17, 101-109` |
| 17 | `sync_one` **任何失败只记录不抛** | `sync.py:147-157, 212-215` |
| 18 | `since` 只作用于**本地还没有数据**的票 | `sync.py:43-59` |
| 19 | TDX `import_dir` **整份覆盖**，是"换源"不是"补充" | `tdx.py:406-413, 476-480` |
| 20 | 质量哨兵任何网络失败转 `ok=False` 但不中断流水线 | `quality.py:9-10, 83-85` |
| 21 | 指数没有除权除息，必须按 `adjustflag=3` 落库 | `markets.py:194-206` |
| 22 | 表里没有的号段是"根本判定不了"，必须报错 | `markets.py:45-47, 89-91` |

### 2.5 数据层决策记录

#### D-01 号段判断只能有一处（`markets.py`）

**背景**：早期号段逻辑散落在 `baostock_source`、`tdx`、`store` 三处。

**决策**：号段 → 交易所的判定**只保留 `markets.py` 一处**。

**理由**：分叉的后果**不是报错，而是读错文件**。`sh.000300`（沪深300）和
`sz.000001`（平安银行）都可能落到别人的文件上，页面安静地画出一条不属于这只票的
曲线 —— 没有任何异常、没有任何日志。

**后果 / 痕迹**：`baostock_source._MARKET_BY_HEAD = markets.HEAD1` 只是为兼容既有
引用保留的别名；`tdx.store_key` 的注释记录了一次已修复的 bug —— 此前把
`is_index` 与"要不要前缀"混用，导致 `sz.399001` 多背了一个没用的前缀。

#### D-02 落库键规则：能唯一就用裸码，否则保留前缀

**决策**：裸数字能唯一指向该市场就用裸数字，否则保留前缀（`markets.py:80-107`）。

- `sh.600000` → `600000`
- `sh.000300` → `sh.000300`（裸 `000300` 在深市也可能存在）
- `sz.399001` → `399001`
- 裸码 `0`/`1` 开头两市同号段，约定为**深市** `BARE_DEFAULT = "sz"`
- **上证指数必须写 `sh.000001`**（裸 `000001` 是平安银行）

**理由**：`sh.600000` 去前缀才和一期已落库的数据键一致 —— 键规则是**事后统一**的，
不是一开始就设计好的。

**特判**：`920xxx` 北交所 2025 改号新号段必须在 `HEAD2` 特判，否则 `9` 开头被判成
沪市（实测 222 对重叠区间收盘价逐日相同）；`88` 是沪市板块指数，不能按 `8` 判成
北交所。

#### D-03 剔除 0 价行

**决策**：`normalize()` 剔除任一价格列 ≤ 0 的行。

**理由**：`fillna(0.0)` 造的 0 价会被分型/笔当成**真实极值**。

**实测代价**：
- `sz.399001` 日线含 5 根全 0 行（1995-02-06 ~ 02-10），使 `min(low) = 0.0`
  把 1991 年以来整段压成一根向下的笔。
- `sh.600759` 5 分钟线含 142 根同类行：126 段 / 20 中枢 → 168 段 / 28 中枢。

#### D-04 Parquet 原子写

**决策**：`write` 走同目录临时文件 + `os.replace`。

**理由**：直接覆写会让读盘方读到半个文件，报
`Parquet magic bytes not found in footer`，页面 500 ——
"一个跟缠论逻辑毫无关系的假故障"。

#### D-05 后复权锚点取因子表首段

**决策**：`hfq_t = raw_t * k_t / k_0`，`k_0` 取**因子表首段**而非窗口首根
（`adjust.py:69-78`）。

**理由**：后复权价是**日期**的属性。拿窗口首根做锚点，会让同一天在 1200 根和
400 根的图里显示两个价格，拖动窗口整张图平移、中枢 ZG/ZD 跟着跳。

#### D-06 `PRICE_DECIMALS = 4`

**决策**：复权结果四舍五入到 4 位小数（`adjust.py:28-30`）。

**理由**：`20 * 0.95 = 18.999999999999996`。不收敛到分位，
"三态在非除权区间完全一致"这条断言无法成立。

#### D-07 复权必须排在聚合前面

**决策**：读日线 → 按日复权 → 再按周/月聚合（`web/api.py:326-340`）。

**理由**：除权发生在周中间时，先聚合后复权会让那一周的开盘价、最高、最低全错 ——
聚合把不同 `k` 的日线混在了一起。

**测试**：`tests/` 中有一条专门钉住这个顺序的用例。

#### D-08 周/月不单独同步，由本地日线聚合

**决策**：`DERIVED = {"week": "day", "month": "day"}`，`data/week`、`data/month`
不存在。

**理由**（`periods.py:3-8`）：
1. 本地已有 5,434 只票的日线，纯本地计算立刻可用。
2. 单独同步的两份数据**复权口径一定分叉**，除权日附近两张图对不上。
3. 缠论结构是价格历史的性质，周线中枢必须由**同一份**价格序列聚合才自洽。

**代价**：当周/当月没走完也出一根（用户 2026-10-01 选定），最后一根随日线变，
故提供 `is_complete`，页面如实标注。

**坑**：分组键必须改名，否则与同名 `ts` 列二义，pandas 抛错
（`periods.py:115-118`）；跨年那一周（如 2025-12-29 起）必须整体落同一组，
用日历年会切成两根。

#### D-09 分钟解析故意不实现

**决策**：`parse_file` 对非 `.day` 抛 `TdxFormatError`（`tdx.py:150-162`）。

**理由**：官方只公开日线整包，没有公开分钟整包。"宁可不做，也不写没核对过的
二进制解码。"

**额外陷阱**：`.day` 价格单位是**分**，`.lc5`/`.lc1` 是**元**。两套单位混用差 100 倍。

**变更历史（2026-10-13，D-51 期间）：这条决策被推翻，分钟解析已实现。**
推翻它的不是「等到了分钟整包」（至今仍然没有），而是多了一条**不依赖整包**的路：
通达信官方有 **iMac 版**（V3.61，支持 OSX 10.15~26.3 与 Intel/M1–M4，
`https://data.tdx.com.cn/level2/tdx_macosx.dmg`），它自带的「盘后数据下载」会把
分钟线落成本地 **vipdoc 目录**（`{market}/fzline/*.lc5`、`{market}/minline/*.lc1`），
而 `import_dir` 吃的就是目录、不是 zip。

这条路的**关键价值**不在「多一个股票分钟源」，而在 **vipdoc 里指数与股票同目录**：
本机 `data/tdx_raw/sh/lday/` 里 `sh000001.day` / `sh000300.day` 与个股并排。
而 baostock 对指数分钟线返回 **0 行**、新浪封顶 1023 根（≈1 个月）、腾讯 320 根 ——
指数与申万行业因此一直没有分钟源（见 §5）。

**★ 2026-10-13 晚更正：iMac 版这条路走不通 —— 它根本没有 vipdoc。**
把官方 `.dmg`（`data.tdx.com.cn/level2/tdx_macosx.dmg`，50.7 MB，
Last-Modified 2026-08-18）下下来**只读挂载**检查（未安装、未改系统，已卸载）：

- 包是 `com.tdx.mac2022` **v3.61**，**x86_64 only** —— 官方《安装说明》要求
  Apple Silicon 先跑 `softwareupdate --install-rosetta`；
- 形态是 **Flutter + 原生** 混合（`Frameworks/{App,FlutterMacOS}.framework`，
  `Resources/home.zip` 是 TDX home 模板：`jycfg`/`config`/`skin`/`user`/`webApp`）；
- 原生二进制里**有** `日线`/`分钟线`/`1分钟`/`5分钟`/`数据管理`/`历史数据`/
  `下载`/`导入`/`导出`/`复权`/`缓存`/`CSV`/`Excel`/`txt`；
- 原生二进制里**没有** `vipdoc` / `fzline` / `minline` / `lday` / `.lc5` / `.lc1` /
  `盘后数据` / `数据下载` —— 在**4 种编码**（utf-8 / utf-16-le / utf-16-be /
  gb18030）下、对包内**全部 >200KB 文件**逐个搜，一个都没有。

**判据**：Windows 版要写 `vipdoc/{market}/fzline/*.lc5` 就必须有 `.lc5` 这个
**扩展名字面量**。它对不上任何编码 —— 所以「盘后数据下载 → vipdoc」这套
**在 macOS 版上不存在**。（保留态度：文件名可以是运行时拼的，但**扩展名**
躲不过去；且 `home.zip` 里本就没有 `vipdoc` 目录。）

**残留的可行路径**：该客户端**有 `导出`（CSV/txt/Excel）**。所以 macOS 上要拿
本地 K 线，现实做法是**从客户端导出文本再导入**，而不是等 vipdoc。
（这条也还没验证过导出格式 —— 需要一份真实导出样本。）

原决策里那条自我约束（"宁可不做，也不写没核对过的二进制解码"）**仍然成立**，
所以实现方式是**让它容易失败**，而不是让它容易通过：

- `parse_minute_bytes` 对**每一条**记录做范围校验 —— 日期/时间能解出合法值、
  时间严格递增、OHLC 为正且 `low <= min(开,收) <= max(开,收) <= high`；
  任何一条不合就 `TdxFormatError`。位宽假设错了会当场炸，不会静默产出一份
  日期错位的行情（后者一旦落库，结构会跨着错位的 bar 算，比直接失败危险得多）。
- 配 `verify_minute_against_day()`：用**同代码的 `.day`** 交叉验证日聚合 OHLC。
  这是唯一能证伪日期编码假设的检查 —— 单看分钟文件内部自洽是不够的，
  一个整体错位的日期同样能自洽。
- **这道验证是落库的硬门，不是一句建议**：`import_dir` 在分钟周期上先跑交叉验证，
  **过不了就不写**，计进 `ImportStats.unverified` 并留一条 error。
  参照取 `{market}/lday/{market}{code}.day`（与分钟线**同口径 raw**，逐格可比），
  退而取本地库里 `adjust == "raw"` 的日线 —— **不取前复权那份**：拿它跟不复权的
  分钟线比，每个除权日都会整片对不上，那是口径差、不是解码错，会把好数据判成坏的。
  两个都拿不到就等于**无法证明**，同样不写。逃生门是 `--allow-unverified`，
  但放行的也照实计进 `unverified`，不粉饰。
- **日期编码仍是待核对假设**（`raw = (年-2004)*2048 + 月*100 + 日`、时间为当日第几分钟）。
  拿到真实 `.lc5` 后第一件事就是跑交叉验证，过了才算核对完成。

落库口径不变（raw，"换源"语义同 D-19），`.day` 与 `.lc` 的单位差仍由
`parse_day_bytes` 的 `/100` 与 `parse_minute_bytes` 的**不除**兜着。

#### D-10 反推因子而非直接问数据源

**决策**：用本地"TDX 不复权 + 库内前复权"反推 `k_t` 阶梯（`factors.py`），
而不是调 baostock `query_adjust_factor`。

**理由**：`query_adjust_factor` 权威，但逐只约 1 秒/只、全市场一个多小时，
且必须等 baostock 会话空闲。反推用本地已有两份数据、全市场几分钟、可随时重算。

**强约束**：**必须在 `day` 切到通达信整包之前跑**。切库会覆盖库内前复权数据，
之后再也反推不出来。`build_one` 查 `sync_state.adjust` 不是 `qfq` 直接拒绝。

**阶梯的有效范围**：只在被反推出来的区间内有效，更早区间是**外推**
（`factors.py:16-17`；标注实现 `web/api.py:186-200`）。

#### D-11 跨源校验只取 30 天窗口

**决策**：`CROSS_SOURCE_WINDOW_DAYS = 30`、`LATEST_WINDOW_DAYS = 45`、
`TOLERANCE = 0.005`（`quality.py:37, 54-61`）。

**理由**：实测各家前复权口径漂移随时间扩大 —— 30 天 0.07%、60 天 0.21%、
180 天 0.67%，2024 上半年可达 8%。窗口开大只会得到噪声。

**腾讯回退**：`TENCENT_URL` 回退因实测本地 HTTP 代理会突发失效
（curl/requests 同时 `Empty reply from server`），而腾讯支持直连。
`direct=True` 必须 `sess.trust_env = False`，否则 `ALL_PROXY=socks5` 会盖掉直连。

**报告口径**：`_kind_breakdown` 必须**分类**给数 —— 只给总数分不清两条哨兵各查多少
条。"外部源整年不可达、跨源校验实际一条没跑成，报表依然显示满分通过 ——
数量本身没有说谎，是口径被合并掉了。"

#### D-12 品种表含退市股

**决策**：用 2015 年至今每年最后一个交易日的**并集**建品种表
（`universe.py:3-6, 86-92, 144-171`）。

**理由**：避免幸存者偏差。`enrich=False` 默认，因为逐只 `query_stock_basic`
7,000+ 次很慢。

**防护**：`MAX_ROWS_PER_QUERY = 20000` —— 实测 baostock 服务端偶发翻页异常时
`next()` 会一直返回 `True`，必须截断避免死循环。

#### D-13 命令行与看盘页共用一份取数

**决策**：`sync.py::sync_one` 是 CLI 与页面唯一的取数入口（`sync.py:3-7`）。

**理由**：否则"迟早会分叉成两个不同的库"。

**配套**：`sync_one` 不抛异常，因为"收盘后的行情是不可再生的"。

#### D-43 港美股 + 名称搜索 + 加自选按钮：行情源按市场分派，复权口径如实收敛

**决策**（用户 2026-10-05 要求：「我想要看港股或者是美股的指数……港股的话，
我可能有时候会看一些具体的股票，以及它的那个恒生指数」；「搜索股票的时候可以不只是
搜索代码，也可以根据这个名称来」；「添加自选……在每一个股票的这个界面添加一个
加自选的按钮」）：

1. **市场从 3 个扩到 5 个**：`MARKETS = ("sh", "sz", "bj", "hk", "us")`
   （`markets.py:134`）。港股/美股**没有号段**，代码基是字母
   （`hsi` / `hstech` / `dji` / `ixic`），只能靠白名单判指数
   （`INDEX_CODES` `markets.py:128`、`is_index` `markets.py:194-206`）。
2. **取数按市场分派**（`SOURCE_OF_MARKET` `sources.py:57-64`）：A 股 → baostock、
   港股/美股 → 腾讯（`tencent_source.py`/`sina_source.py`）。`sources.py` 只是
   **分派层**，`_fetch_one`（`sources.py:327-337`）**不做任何聚合** —— 旧代码在
   分派层顺手聚合，导致"派给谁"和"怎么算周线"耦合在一处。
3. **复权口径按市场收敛，而不是报错**（`adjust_for` `sources.py:197-217`）：
   A 股沿用调用方口径（指数强制不复权）；**港股/美股一律原始价**。请求 qfq/hfq
   时返回原始价**并附一句实话**，不抛异常 —— 页面必须能把票画出来。
4. **不落库任何港股复权因子**。`factors` 表对港股留空。
5. **名称搜索走腾讯 smartbox**（`smartbox` `search.py:189`），`Result` 同时给出
   `skipped`（被范围决策挡掉的命中）与 `error` 两栏（`search.py:77-90`）。
6. **`/api/search` 是独立路由**（`web/api.py:609`），不是 `/api/universe`
   的重载：前者要能查**还没落库**的票。
7. **加自选按钮在股票界面上**（`index.html:103-106`，`.tools` 容器内），
   按压态由 `state.watch` 推出（`renderFav()` `app.js:1966-1969`），
   用户不必先搜索代码再切到自选栏。
8. **默认自选池 11 只**：7 个 A 股指数 + 恒生/恒生科技 + 道琼斯/纳斯达克
   （`DEFAULT_WATCHLIST` `meta.py:358`）。

**依据**（可复现的测量见
[docs/evidence/2026-10-05-港美股数据源与复权口径.md](docs/evidence/2026-10-05-港美股数据源与复权口径.md)；
本节**不涉及任何缠论判据**，故 §5 的第 1 步「先写 `optimizer/theory/` 条目」不适用 ——
K 线怎么来、按什么价来，与笔/段/中枢怎么画无关）：

- **"港股能反推复权因子"被自己的数字否掉**。5499 根公共日线上：
  `hfq - raw` 变化 **3009** 次、`hfq / raw` 变化 **5171** 次且跨度
  `1.000000 .. 5.812090`、`qfq` 有 **2193** 根收盘 ≤ 0（最小 `-55.111`）。
  阶梯模型与固定因子模型**两个都不成立**，`qfq` 更是连正价格都不保证。
  所以只能给原始价 —— 并且必须**说清是原始价**。
- **"口径说明"必须分两种**（`_raw_only_note` `web/api.py:210-221`）：
  A 股无除权记录 ⇒「三态相同，价格即不复权原始价」；
  港美股 ⇒「只提供原始价，另两态取不到」。**后者不许印前者那句话** ——
  「三态相同」与「另两态取不到」是两个不同命题，混用会让用户以为复权算错了。
  护栏：`tests/web/test_api_adjust_ma.py::test_hk_stock_says_the_other_two_modes_are_unavailable_not_that_none_exist`。
- **港股分钟不可得，且必须说清三种失败方式**（`capabilities` `sources.py:483-490`）：
  `hkfqkline` 的 `m30`/`m5` 回 `bad params`；通用 `fqkline` 拿 `m30` 只回
  **当天 1 根日线**（日期 `2026-10-05`、OHLC 是全天区间 —— 是日线贴了 m30 的标签）；
  `hkMinute/query` 只回当天 152 条分时。只写"没有分钟线"会让排查者去试第二个入口。
  **用户 2026-10-05 明确要求试出港股分钟，实测是负面结果，照实记。**
- **美股分钟有但很浅**：日线可翻页（`us.dji` 实测 5726 根，`2004-01-02` 起），
  30 分只有 **1023** 根且数据商不给翻页（≈3.8 个月），故 `depth_cap = 1023`
  （`sources.py:503-509`）。页面据此灰掉超范围请求。
- **派生周期改成"先落日线、再本地聚合"**（`periods.aggregate`）：
  港股/美股的周/月不再是独立取数请求，与 A 股同一套代码路径。
  `start_for`（`sync.py:43-59`）据此决定回补范围。
- **名称搜索的范围决策显式可见**：标普 500、纳斯达克 100、国企指数、美股个股
  本期**不做**，但**不是**因为取不到 —— 数据源实测有全历史日线。
  所以被挡的命中会出现在 `skipped` 里（带原因），而不是静默消失
  （`OUT_OF_SCOPE` `search.py:45-49`、`_OUT_OF_SCOPE_KIND` `search.py:53-56`）。
- **UI 改动用真实浏览器核对**（AGENTS §4.4）：CDP 探针 16 条断言
  `ALL_OK`、`EXIT=0`，含正反两向对照（含未加自选时的按压态、
  按名称回车取票、以及"不应出现"的反例）。

**被否决的替代方案**：

- **用 `adjust.infer_factors(raw, qfq)` 反推港股因子表**（规格 §5 的初版方案）。
  被 §1 的三个数字否掉：因子不是阶梯也不是常数。**若当初没测这三个数，
  就会把一条纯噪声的"因子"落库，并且页面会显示一个负的前复权价。**
- **港股请求 qfq/hfq 时直接抛异常**。用户点一下就白屏，而正确行为是
  「画原始价 + 说实话」。选收敛不选报错（与 `adjust_for` 的既有取向一致）。
- **把 `/api/search` 做成 `/api/universe` 的一个参数**。否决：`/api/universe`
  的语义是"本地品种表"，加了远程查询会让两个职责共用一条缓存与一个 404 口径。
- **港股分钟用 `hkMinute/query` 凑**。否决：只有当天、无历史、不可翻页，
  连"日线→30 分"的跨日结构都算不出，落库只会得到一根孤零零的今天。
- **把港美股的 `adjust_note` 统一成 A 股那一句**。这正是**改出来又实测否掉**的
  一个真 bug（见「变更历史」第 3 条）。
- **港股/美股也参与"跨源校验"**。否决：只有腾讯一个源，没有第二个源可校；
  硬做只能自己跟自己比。

**后果 / 变更历史**：

- 代价：**港股 `amount` 恒为 0.0**。本模块走的 `fqkline` 行只有 7 位、
  没有成交额字段（有第 8 位的是不可用的 `hkfqkline`，`tencent_source.py:96-104`）。
  唯一消费者 `web/api.py::_bars_payload` 不参与缠论计算，前端也不渲染；
  将来要显示只能另找源，**不许 量×均价 编**。
- 代价：**美股个股、标普 500、纳斯达克 100、国企指数不在此版本范围内**，
  尽管数据源有数据。这是范围决策，已在 `skipped` 里对用户可见。
- **已知空白**：`types.truncate`（`src/chanlun/data/types.py:106-116`）把 `ts` 当**纯字符串**比较，
  没有日期解析。所以 `end=<YYYY-MM-DD>` 这种只有日期的边界会丢掉当天的**分时**
  bar（`'2026-10-05 09:30' <= '2026-10-05'` 是 `False`）。
  **这对 A 股同样成立，不是本次引入的** —— 现有调用方
  （`optimizer/agent.py:98`、`backtest/runner.py:127/182/217`）全是 A 股日线口径，
  所以没有活的调用方被打破。记在这里，不修。
- **变更历史（2026-10-05，0.5.0，全部为本次）**：
  1. **港股个股被 `_classify` 全部拒掉**。原判据按 `kind` 白名单放行，
     而港股个股的 `kind` 是 `GP`、不是 A 股的 `GP-A`。修后香港个股进范围，
     只有权证类（`QZ`）被挡。**用扰动证明过**：把判据改回旧写法，
     恰好 3 条测试转红。
  2. **`Hit.market` 曾是 `code.split(".", 1)[0]`** —— 于是 `513600` 这种
     裸码 ETF 的 `market` 是 `"513600"`。改成用**数据源给的**市场字段。
  3. **`search._decode` 里的 `isinstance(decoded, str)` 分支不可达**。
     取法是「第一个 `"` 到最后一个 `"` 之间」，`json.loads` 必然返回 `str`。
     代码里注释说明并**删掉了假造该分支的用例** —— 不编覆盖率。
  4. **smartbox 零命中哨兵是 `v_hint="N";`（11 字节，带分号）**。
     旧解析把它当坏 JSON 报错。现在 `N` / `N;` 都映射成空串。
  5. **`seed-watchlist` 印出来的名字个数与 `DEFAULT_WATCHLIST` 不一致**：
     实测印了「12 只」却只列 11 个名字（多出来的是库里原有的票）。
     改为**从库里那批行**同时取个数与名字（`__main__.py:500-504`），
     并把 `--reset` 的说法改对（那个 flag 不存在）。
  6. **港美股页面印了 A 股那句「三态相同」**。这是 §5 第 5 步（查前端/文案有没有
     把旧口径写死）没做完留下的洞，由 `_raw_only_note` 修掉；`app.js` 里
     一句把 A 股口径当通用口径的注释同批改掉。
  7. **A 股派生周期曾重复落库边界 bar**：增量重算时当前周/月的第一根会被解成
     已完成周/月的最后一根，于是同一根 bar 写两次。现在用 `reopen` 门
     （`sync.py:188` 一带）+ `_drop_open_tail`（`sync.py:62-71`）在重算前删掉
     开口 bar；重算本身也**只在 `periods.is_complete` 为假时**才做，
     否则增量同步永远报不出 `skipped`。
  8. **前端 `CODE_RE` 必须接受字母基**（`hk.hsi` / `us.ixic`）。
     旧正则只认 `[a-zA-Z]{2}\.[0-9]{6}`，港股指数连提交都过不了
     （`app.js:1532`）。
  9. **页面加自选的错误提示不能复用 `showNotice`**：`#notice` 的动作槽被
     「同步这个周期」占着，且 `showNotice` 会清空图表。改为 `.tools` 里
     一个独立的 `#fav-error`（`index.html:106`、`styles.css:303-315`）。
  10. **`scan --watch --codes <指数>` 的跟踪行只剩一串代码**（收尾实测时发现，
      不是本决策的判据问题，是本决策**带出来的新用法**暴露的旧缺口）：
      `codes=None` 走自选池表、名字存在 `watchlist.name` 里，而 `--codes`
      早先只查 `meta.get_universe` —— 指数不在品种表里。于是
      `scan --watch --codes hk.hsi --notify-file …` 写出的推送文案是
      `- hk.hsi  [day] …`（名字位置两个空格）。改为按 `meta.name_of`
      的优先级取名（品种表 → 自选池 → 内置指数名，`watchlist.py:266`）；
      护栏在 `tests/scan/test_watchlist.py:144`，**在修复前的输入上实测红**
      （`assert '' == '恒生指数'`）。这一条同时把 §14 那句
      「推送文案里的市场名可能不准」改成了实测结论：**推送文案不带市场名，
      缺的是名字，已修**（`CHANGELOG.md` 同批记了一条 `PATCH`）。

#### D-44 A 股一级/二级行业指数：走腾讯板块接口，内置分类清单，不复权落库

**决策**：

1. **新增落库市场 `sw`**，代码形状 `sw.801120`（`src/chanlun/data/markets.py:134`、
   `src/chanlun/data/markets.py:149`）。`sw.` 前缀**必需**：`markets.HEAD2` 把首字符
   `8` 映到 `bj`（`src/chanlun/data/markets.py:39`），裸 `801120` 会被解析成北交所股票。
   `industry.vendor_code()`（`src/chanlun/data/industry.py:307`）对不带 `.` 的输入回 `""`，
   而不是替调用方猜一个市场。
2. **数据源是腾讯的「板块」接口，不是指数行情接口**。列表用
   `RANK_API`（`src/chanlun/data/industry.py:52`），
   `board_type=hy` ⇒ 申万一级 **31** 条、`board_type=hy2` ⇒ 二级 **124** 条
   （`src/chanlun/data/industry.py:55`），`count` 上限 200、翻页用 `offset`
   （`src/chanlun/data/industry.py:61`）。日线用
   `BOARD_KLINE`（`src/chanlun/data/sw_source.py:64`），板块符号是
   `pt01` + 申万 6 位码（`src/chanlun/data/industry.py:58`）。
3. **31 + 124 条分类清单内置在 `src/chanlun/data/industry.py`**
   （`SW_L1` `src/chanlun/data/industry.py:78`、`SW_L2` `src/chanlun/data/industry.py:113`、
   `ALL` `src/chanlun/data/industry.py:254`）。**不建 sqlite 表，页面加载时不联网。**
   清单的核对日期是 `CHECKED_ON = "2026-10-05"`
   （`src/chanlun/data/industry.py:75`），随 `/api/industries` 的 `as_of` 发到前端显示。
4. **`python -m chanlun industry` 让清单的过期可被看见**
   （`src/chanlun/__main__.py:418` 注册、`src/chanlun/__main__.py:958` 实现）：
   `--level 1|2` 列清单、`--json` 出机器可读、`--check` 联网比对。
   **`--check` 只报差异、绝不改写清单**；有差异退出码 1，**网络失败也算失败**
   （「核对失败」打到 stderr 并计入 `bad`）—— 否则「拉不到」会被读成「没问题」。
5. **落库口径是不复权**：板块是指数，没有除权除息。`sources.adjust_for`
   经 `markets.is_index`（`src/chanlun/data/markets.py:194`）把 `sw` 收敛到 `"3"`；
   `sw_source.fetch_bars`（`src/chanlun/data/sw_source.py:241`）收 `adjust` 只为对齐签名，
   传什么都不改口径（与 `baostock` 指数路径同理）。
6. **只有日线**。周/月由 `sources.fetch_bars` 从日线本地聚合
   （`src/chanlun/data/sources.py:456`、`src/chanlun/data/sources.py:480`）。
   板块在腾讯那里**没有分钟线**（`mkline` 对 `pt01…` 回 `param error`，
   对照 `sh600000,m30` 正常），新浪对申万回 `null`，`capabilities` 的分钟分支
   如实说明原因（`src/chanlun/data/sources.py:396`）。

**变更历史（2026-10-13，D-52 期间）：这条仍然成立 —— 但中间被误推翻过一次。**
当天先写成「东财有申万分钟线，`secid=90.801010`」，并把 96 个文件按那个映射导进了库。
**那是错的**：`90.` 是东财**自己的板块空间**，**同号不同物** —— 实测
`90.801010` 回的是「磷化工」（不是农林牧渔）、`90.801080` 回「新型城镇化」
（不是电子）、`90.801012` 回「PVDF概念」（不是农产品加工）。
换句话说**按那个映射导入的是张冠李戴的行情**，比没有数据危险得多。
发现方式是核对 `data.name`（号段会撞车，名字不会）—— 起因是补完数据后
发现 155 个板块里有 59 个是空的，去查为什么。
处理：① 删掉那 96 个 `data/5/sw/*.parquet` 与 155 条 `sync_state` 水位；
② 删掉 `sw → 90.` 的映射，并在 `eastmoney_source.secid()` 里对 `sw.*` 加护栏
（**直接抛错**，让这个错映射做不到，而不只是「调用方不去调」）；
③ 把这条写回「申万没有分钟线数据源」。**申万的分钟线至今无解。**
7. **行业指数接进既有的搜索与取名机制，不做第二套**：
   `industry.search_rows()`（`src/chanlun/data/industry.py:294`）产出与品种表同形的行，
   `search.local` 接受**逐行的 `kind`/`source` 覆盖**
   （`src/chanlun/data/search.py:258`、`src/chanlun/data/search.py:259`），
   于是行业命中自带 `kind="ZS"` / `source="industry"`。
   `meta.name_of` 在品种表、自选池之后插入行业清单这一源
   （`src/chanlun/data/meta.py:296`），`web/api.py` 的自选池取名改为委托给它
   （`src/chanlun/web/api.py:869`）。
8. **`/api/search` 里行业行排在最前**（`src/chanlun/web/api.py:635`）。
   `local()` 数够 `limit` 就收手，而「银行」能命中几十只同名个股 ——
   行业行排后面会被挤掉，可用户输一个行业名，要的大半就是那个行业指数。
   没有任何股票代码以 `sw` 开头，所以这个优先级不会抢走个股。
9. **新增 `GET /api/industries?level=1|2`**（`src/chanlun/web/api.py:642`），
   供前端选择器取清单，**前端不写死任何行业名**。
   item 有 `code`/`name`/`level`/`local` **四个**键（`src/chanlun/web/api.py:678`）；
   `local` 是本机有没有这个板块的**日线**（`src/chanlun/web/api.py:666-670`）。
   `market` 是搜索路径才加的。
10. **行业指数不进 `DEFAULT_WATCHLIST`**。它是**导航**，不是自选池的一员 ——
    进池会污染「自选 11 个大盘指数」这句文案，也会让 `scan --watch` 默认多跑 155 只。
    入口是左侧自选栏里的**按钮**（`src/chanlun/web/static/index.html:85-90`、
    `src/chanlun/web/static/app.js:1646`），点它开一个原生 `<dialog>`
    （`src/chanlun/web/static/index.html:160`、`src/chanlun/web/static/app.js:1851-1860`）；
    事件绑定在 `wireIndustryModal`（`src/chanlun/web/static/app.js:1911-1935`），
    **点卡片即跳转**在 `pickIndustry`（`src/chanlun/web/static/app.js:1862-1875`）。

**依据**：

用户 2026-10-05 的原话：「**还有就是 A 股的话，需要有那个一级行业、二级行业的那个行业指数。**」

**本节不涉及任何缠论判据** —— 这是数据层加一个市场、加一条 API、加一块导航，
`chan/` 下的笔/段/中枢/背驰/买卖点一行未改，所以 §5 的第 1 步（先在
`optimizer/theory/` 写理论条目）**不适用**。版本涨 `MINOR` 是因为**新增了 API 端点、
CLI 命令和落库市场**（§2 的表：向后兼容的新功能 ⇒ `MINOR`），不是因为判据变了。

实测证据逐条在 `docs/evidence/2026-10-05-申万行业指数数据源.md`：六条「申万指数取不到行情」
的否定结论、`getRank` 的 31/124 计数、11 字段行里**第 3 个是收盘、第 4 个才是最高**、
成交额在第 8 位且单位是万元、`param` 末段逗号不能省、`count ≤ 2000`、`start` 被忽略而
`end` 含当天、行是升序、板块无分钟线、`pt01801120` 全历史 3 页 5492 根零重叠。

**被否决的替代方案**：

1. **用现成的指数行情路径取 `801010` 之类**。六条路全部走不通：腾讯 smartbox 回
   `v_hint="N";`、腾讯 `fqkline` 回 `day: []`、`qt.gtimg.cn` 没有 `v_` 行、
   东财回 `{"rc":100,…,"data":null}`、新浪行情回 `var hq_str_sh801010="";`、
   新浪板块接口 `node=sw2` 回 `[]`。申万指数在行情接口那里**不是指数**。
2. **改用中证行业指数**（`sh.000928–000937`，镜像 `sz.399928–399937`；
   中证全指 `000986–000995`）。它们能走普通 A 股指数那条路，但**只有一级、没有二级**，
   而用户明确要二级 ⇒ 放弃。记在这里是为了下次不必重探。
3. **把 155 条分类建成 sqlite 表、同步时刷新**。搜索与 `meta.name_of` 都是**热路径**，
   一次网络往返会把「搜一下」变成「等一秒」；而且分类变了要有人看见，不该悄悄变。
   先例是 `meta.DEFAULT_WATCHLIST`（`src/chanlun/data/meta.py:358`）也是硬编码的。
4. **把行业名写死在 HTML/JS 里铺下拉框**。申万几年调一次分类，写死在三个文件里
   没人会去改。改为从 `/api/industries` 取（item 的 `code`/`name`/`level`，
   另有 `local` 决定卡片明暗）。
5. **把行业指数加进自选池**。会让「自选 11 个大盘指数」这句文案失真，
   也会让 `scan --watch` 默认多跑 155 只。它是导航，不是池成员。
6. **裸 `801120` 也接受**。`markets.HEAD2` 把 `8` 映到 `bj`，裸码会被解析成北交所股票 ——
   静默取错标的是最坏的一类 bug，所以前缀必需，且 `vendor_code` 对裸码回 `""`。
7. **让 `/api/search` 把行业行放在品种表之后**。`local()` 的 `limit` 早退会把
   `sw.801780` 挤到看不见，而「按名字找到那个行业指数」正是用户要的东西。
   红证明：把两段顺序对调，`tests/web/test_api_search.py` 的
   `test_industry_rows_come_before_the_local_universe` 变红（报 `+ universe`）。
8. **拿裁剪后的行数去比 `MIN_ROWS` 护栏**。腾讯只约束 `end`、不约束 `start`，
   一页通常回 2000 根，增量同步只取二十几个交易日就会被裁成二十几根 —— **那是正确答案**。
   护栏必须量「数据商给了多少」。红证明：把护栏挪到 `truncate` 之后量 `len(out)`，
   `tests/data/test_sw_source.py` 的 `test_the_row_guard_runs_before_the_trim` 与
   `test_start_and_end_are_trimmed_locally` 同时变红。

**后果 / 变更历史**：

- 版本 `0.5.0` → `0.6.0`（版本号在 `pyproject.toml`）。新增市场 `sw`、新增 API 端点
  `/api/industries`、新增 CLI 子命令 `industry`、新增模块
  `src/chanlun/data/industry.py` 与 `src/chanlun/data/sw_source.py`。
- **代价 1：清单会过期。** 申万 2021 版分类几年调一次。缓解是
  `--check` + 页面上的 `as_of`，而不是假装它不会变。**悄悄过期比过期本身更坏。**
- **代价 2：板块历史起点是逐板块的，不是统一的。** `sw.801120`（食品饮料）从
  `2004-03-01` 起 5492 根，`sw.801125`（白酒Ⅱ）从 `2000-01-04` 起 6482 根 ——
  申万基日是 1999-12-30，腾讯的板块历史只到 2004 年。**照实记，不谎报。**
- **代价 3：板块没有分钟线**，`capabilities` 只能如实说「没有数据源」，
  页面在分钟周期下点「同步这个周期」会拿到 400 而不是数据。
- **变更历史（2026-10-05，0.6.0，全部为本次）**：
  1. **`sw_source.py` 的模块注释与 `_paged_day` docstring 曾把翻页算错**：
     写成「相邻两页重叠 1 根，`2000 + 2000 + 1494 = 5494 行`，去重后 5492 根」。
     实测是 `2018-07-06..2026-09-30`(2000) + `2010-04-15..2018-07-05`(2000)
     + `2004-03-01..2010-04-14`(1492) = **5492 根、`ts.is_unique` 为真、零重叠**。
     错因是**探测脚本**把下一页的 `end` 传成「上一页首行」而**没有减一天**；
     落库代码里 `nxt = first - 1 day` 一直是对的。**代码没错、注释错了**，已改写，
     并在 `docs/evidence/2026-10-05-申万行业指数数据源.md` 留了一条日期化的更正。
     `normalize` 的 `drop_duplicates`（`src/chanlun/data/types.py:101`）仍留着，
     但它是**兜底**，不是这一步的依靠。
  2. **停止条件从「四个」补成五个**：漏写的是 `nxt < FLOOR`
     （`src/chanlun/data/sw_source.py:73`）。新增
     `tests/data/test_sw_source.py` 的
     `test_paging_stops_at_the_floor_even_if_pages_keep_coming` 钉住它；
     同一个测试也钉住 `FLOOR == "1990-01-01"`，免得它被悄悄改小。
  3. **`tests/data/test_sw_source.py` 初版的假响应是降序的**（数据商是升序）。
     于是 `rows[0]` 恰好等于 `cursor`，翻页停滞护栏在第一页就触发，
     `assert 2000 == (2000 + 10)` 之类三条断言全红 —— **源代码是对的，测试造错了**。
     假响应必须与数据商语义一致（升序、`end` 含当天、`count ≤ 2000`），
     否则测试会因为错误的理由变红。要单独测 `MAX_PAGES` 上限得把 `FLOOR` 往回改，
     不然地板先触发。
  4. **`industry.vendor_code()` 不做清单校验**。一个测试曾断言裸 `801120`
     能拿到 `pt01801120` —— 那是错的，会让「市场靠猜」。已改为钉住 `""`，
     并说明理由。
  5. **`/api/search` 的 docstring 从「两种来源」改成「三种来源」**，
     `tests/web/test_api_search.py` 的模块 docstring 同批改，并新增 8 条用例
     （行业名命中、排序契约、代码可被数据层接受、`/api/industries` 的形状 /
     过滤 / 非法级别 / 不联网）。
  6. **`index.html` 的 `.watch-foot` 文案**追加了行业指数那句，
     同时**保留**了 `默认是 11 个大盘指数` 这个被
     `tests/web/test_frontend_watchlist.py` 钉住的前缀（`七个大盘指数` 仍不许出现）。
  7. **§5 第 5 步（查前端有没有把旧口径写死）已跑**：
     `grep -rn "两种来源\|两个来源\|7 个大盘指数\|七个大盘指数" src/chanlun/web/static/ src/chanlun/web/api.py`
     ⇒ 无命中。
  8. **`PATCH` 修复：清单的核对日期从来没显示过；清单取不到时静默留白**
     （2026-10-06，D-44 收尾，**不涨版本号**）。
     `app.js` 的提示读 `body.checked_on`，而本端点的返回键是 `as_of`
     （`src/chanlun/web/api.py:678`）—— `undefined` 为假，于是这条提示
     **从来没显示过**；而 D-44 立它的理由恰恰是「悄悄过期比过期本身更坏」。
     同一次还量到：`fetch` **只在网络层失败时 reject**，404 / 500 都是正常
     resolve，旧进程上 `/api/industries` 回 `{"detail":"Not Found"}` ⇒
     `body.items || []` 得到空数组 ⇒ 下拉框里只剩占位项，
     **「后端没重启」与「这个功能不存在」长得一模一样**。
     尤其要记住：当时 `/api/version` 报的**仍是新版本号**，因为
     `version.py` 是**现读** `pyproject.toml` 的 ——
     **不能拿 `/api/version` 判断一份代码是否已被加载**。
     改法：提示改读 `as_of`；`fetch` 之后显式看 `res.ok`，失败文案里带状态码
     （404 与 500 要能分辨），断网那条路也留一句话。
     护栏放在 `tests/web/test_frontend_industry.py`：一条钉返回键名、
     一条钉 `res.ok`，断言**只看代码、不看注释**。
     真实浏览器逐字实测见 CHANGELOG 的同名条目。
9. **`MINOR`：行业指数选择器从下拉框换成大弹窗；`/api/industries` 的 item 多回
   一个 `local`**（2026-10-06，版本 `0.6.0` → `0.7.0`，`pyproject.toml:3`）。
   第 8 项里说的「下拉框」当时确实是 `<select>`；本次换成按钮 + 原生 `<dialog>`。
   - **入口 `<select id="industry">` → `<button id="industry">`**
     （`src/chanlun/web/static/index.html:85-90`）。**id 不变**，所以
     `$("#industry")`、`loadIndustries()` 与既有护栏测试都不用改名；当前选中的
     行业名显示在 `#industry-name` 上（`syncIndustryButton`
     `src/chanlun/web/static/app.js:1939-1946`，由 `setCode` 在换票时同步，
     `src/chanlun/web/static/app.js:2014-2021`）。
   - **弹窗是 `<body>` 的直接子元素，不占 `body` 的 grid 轨道**
     （`src/chanlun/web/static/index.html:160`）：未打开是 `display: none`，
     `showModal()` 之后进 top layer 且 `position: fixed`，两种状态都不在流内，
     所以 `tests/web/test_frontend_watchlist.py` 的 `NO_BOX_TAGS` 放行 `dialog`。
     **放行的理由只是「本页只用 `showModal()`」** —— 非模态的 `<dialog open>`
     是在流内的，UA 的 `display:none` 只作用于 `dialog:not([open])`；
     那个常量的注释原先把理由写成「`<dialog>` 两种状态下都不在流内」，是
     **过度概括**，2026-10-06 已改对（`NO_BOX_TAGS` 的内容与三条 grid 断言没动）。
   - **开合与键盘只在一处**：`wireIndustryModal`
     （`src/chanlun/web/static/app.js:1911-1935`）绑入口点击、关闭按钮、
     点遮罩关闭、Esc 后把焦点还给入口按钮；`openIndustryModal`
     （`src/chanlun/web/static/app.js:1851-1860`）每次打开先清空搜索词
     （上次的词还留着会让人以为清单变短）；方向键在 `moveIndustryFocus`
     （`src/chanlun/web/static/app.js:1900-1908`）移动 roving tabindex，
     整张网格只占一个 Tab 位。**点卡片即跳转**在 `pickIndustry`
     （`src/chanlun/web/static/app.js:1862-1875`）：关弹窗、非日/周/月先把周期
     扳回 `day`（板块只有日线），再 `setCode` / `load` / `loadWatch`。
     原先「选中即跳转」这条引的是 `wireIndustryModal` 那一行（`1704`），
     **行号对、内容不对** —— 那一行是事件绑定，跳转在 `pickIndustry`，本次改对。
   - **`GET /api/industries` 的 item 从三个键变四个键**
     （`src/chanlun/web/api.py:678`）：新增布尔 `local`，判据是本机 `sync_state`
     里 `(code, "day")` 存在、`rows > 0` 且 `error` 为空
     （`src/chanlun/web/api.py:666-670`）。**仍然纯本地，不联网。**
     `Board.as_dict()` 不改 —— 它同时服务 `/api/search`，而搜索路径不查
     `sync_state`，给它加一个 `local` 是另一件事。判据里 `error` 非空必须排除：
     同步失败的行也躺在 `sync_state` 里，当成「有数据」会让卡片亮着却画出空图，
     那比一直暗着更坏。
   - **`local` 键缺席 = 进程比页面旧：页脚不许报一个自己算不出的计数**
     （2026-10-06 终审补，不涨版本号）。第 8 项记了两种「后端比页面旧」的静默
     故障（键名读错、404 当正常 resolve），这是**第三种**：进程**认识**
     `/api/industries`、却**不认识** `local` —— 路由比字段老。
     状态码是 200，`res.ok` 拦不住；`it.local === true` 恒为假 ⇒ 卡片全暗，
     页脚把 `have = 0` 印成「本地已有 0 / N 个板块的日线」，
     而真相是**这个进程不知道** ——「本机真没有板块日线」与「后端比页面旧」
     长得一模一样。改法：先看后端到底给没给这个键（`"local" in it`），
     缺席时页脚改说「后端进程比页面旧……这不代表本机真的没有板块日线」。
     同批补的护栏在 `tests/web/test_frontend_industry.py`：
     ① tab 条必须由响应里的 `body.levels` 驱动（这个键缺席时
     `buildIndustryTabs(body.levels || [])` 的 `|| []` 把「没给」与「空列表」
     变成同一件事，tab 条**静默塌成一条**）；② `wireIndustryModal()` 必须
     **被调用**而不只是被定义（`tests/web/` 从不执行 `app.js`，只
     `node --check` 语法 ⇒ 删掉启动尾部那次调用，弹窗永远打不开而全部测试
     照样绿）；③ `local` 必须从响应读；④ `?level=1` 必须回非空列表
     （空列表上 `all(...)` 恒真）。四条断言都做过**变异测试**：
     把对应代码改坏，它们会红。**页脚护栏的第一版只钉了「那两句话在文件里」，拦不住 `if (stale)` 取反** —— 取反后两句话都还在，只是说反了（收尾评审实测：当时 `10 passed` 全绿）；现在文案绑在 `if (stale) { … } else { … }` 的**分支**上、`have` 钉住来源（必须来自 `.filter((r) => r.local)`），取反 / `have` 恒报总数 / 两个计数互换三种变异都会红。真实浏览器逐字实测见 `docs/evidence/2026-10-06-行业弹窗-旧后端页脚-DOM-验收.md`。
   - **本次新否决的方案**（理由留给下次）：
     - **按 `SW_L1`/`SW_L2` 把二级挂到一级下面**。两张表是**扁平**的，且前缀
       **不同**（`801103 IT服务Ⅱ` 的父级是 `801750 计算机`），要挂父子就得手写
       124 条映射，而 `industry --check` 无法校验父级 ——
       **一条不可校验的映射比没有映射更坏**。
     - **按涨跌幅排行铺网格**。要新增一个批量行情端点，而 155 个板块里当时
       只有 1 个（`sw.801120`）有本地日线，排行会是一片空。
     - **每张卡片挂徽标区分「本地 / 待同步」**。155 个徽标是噪声；改成整卡
       明暗 + 虚线边，复用 `.chip.capped` 的「虚线 = 还没到位」既有语法。
     - **手写 `div[role="dialog"]` + 自己写焦点圈禁**。焦点陷阱、Esc、滚动锁定、
       背景 `inert` 是原生 `<dialog>` 免费给的，手写这四件事是无障碍 bug 的高发区。

#### D-45 前端渲染：跨进程边界来的文本一律走 `textContent`，不进 `innerHTML`

**决策**：

1. **自选股读取失败分支改成 `createElement` + `textContent` + `replaceChildren`**
   （`src/chanlun/web/static/app.js:1307-1315`）。原先那一行是
   `` box.innerHTML = `<div class="watch-empty">自选股读取失败：${body.detail || "…"}</div>` ``。
2. **全文件 `innerHTML` 从 6 处降到 5 处**，剩下的 5 处**逐个核对过**：四处是
   清空（`app.js:439`、`app.js:1139`、`app.js:1356` 的 `= ""`、`app.js:1613`
   的 `list.innerHTML = ""`），一处是**纯常量字面量**（`app.js:1301`
   的「连不上本地服务（8888 端口）」）。**没有一处再插值变量。**
3. **新增护栏 `tests/web/test_frontend_xss.py`**，三条断言：
   ① 全文没有一行**代码**同时出现 `innerHTML` 与 `${`；
   ② 那条失败分支里没有 `.innerHTML =` **赋值**，且有 `textContent` 与
   `replaceChildren`；③ 用户看得见的文案（`自选股读取失败：` /
   `接口返回了无法解析的内容。` / `body.detail`）都还在。
4. **护栏钉机制，不钉关键词**：`_code_lines()` 只丢**整行注释**，
   分支断言用 `re.search(r"\.innerHTML\s*=", branch)`。理由：修好之后
   那句「为什么这里不许用 `innerHTML`」的注释里**必然**出现这个词，
   钉裸词会逼后来的人删掉解释。

**依据**：

- `body.detail` 来自 `/api/watchlist/structure` 的**响应体**（`body` 是
  `await res.json()` 的结果），是**跨进程边界**来的文本，不是本文件的常量。
  把它交给 `innerHTML`，等于让后端决定页面解析并执行什么标记。
- **真实浏览器 A/B 实测**（`docs/evidence/2026-10-06-自选股错误文案当文本渲染.md`，
  探针 `docs/evidence/2026-10-06-自选股错误文案-probe.mjs`）：同一份 payload、
  同一页面，**只有那一处写法不同** ——
  修复后失败快照是转义文本 `&lt;img src=x onerror=…&gt;`、`onerror` **不触发**；
  用 CDP `Fetch` 拦截 `/static/app.js` 喂回旧写法后，失败快照里长出真正的
  `<img src="x" onerror="…">` 且 `onerror` **触发**（`window.__xss_real === 1`）。
  场景 A `8 / 8`、场景 B `4 / 4`，退出码 `0`。
- **修复前该护栏变红**：把那一处临时改回旧写法 ⇒
  `test_no_line_interpolates_a_variable_into_innerhtml` 与
  `test_watch_failure_branch_renders_detail_as_text_not_html` 两条 FAILED
  （`2 failed, 1 passed`）；还原后 `sha256(app.js)` 与改动前一致。
  修复后 `tests/web/` ⇒ `175 passed`。

**被否决的替代方案**：

- **保留 `box.innerHTML = ""` 再 `append`**。`replaceChildren(fail)` 一步到位，
  不留「先清空、再追加」的中间态。
- **全局把 `innerHTML` 换成 `textContent`**。另外 5 处是清空与纯常量字面量，
  `textContent = ""` 与 `innerHTML = ""` 等价，改它们只制造无意义的 diff；
  `app.js:1301` 那条常量没有插值，不构成同一个问题。**这是逐个核对后的结论，
  不是漏掉。**
- **只钉「文件里不许出现 `innerHTML`」**。会误伤 `app.js:1301` 那条常量，
  也会逼着删掉解释性注释。判据改成「同一行同时有 `innerHTML` 与 `${`」
  +「失败分支里没有 `.innerHTML =` 赋值」。
- **在后端就把 `detail` 转义**。那是把「怎么渲染」的知识塞进数据层，
  而同一份 `detail` 还要给 CLI 与日志用；转义属渲染层的事。

**后果 / 变更历史**：

- **用户可见行为：读取失败时的文案逐字不变**（护栏第 ③ 条钉住），
  只是不再被当 HTML 解析。本条本身是 `PATCH` 级的修复。
- **本次顺带量到一件既有问题，未修**：失败文案会被**后到**的
  `renderWatch()` 盖成「自选股是空的」（探针的 `hist` 在两个场景里顺序一致
  ⇒ 与本次改动无关）。已登记为已知空白（见 §5）。修它要动
  `renderWatch()` / `setCode()` 的调用时序，超出本次范围。
- **`chan/` 下一行未改**：本条不涉及任何缠论判据，因此不需要
  `optimizer/theory/` 条目。

#### D-46 数据层：`high < low` 的行剔除，`open`/`close` 越界只警告保留

**决策**：在 `normalize` 里、非正价格剔除之后、`drop_duplicates` 之前，追加两档
处置（`src/chanlun/data/types.py:64-99`）：

1. **倒挂（`high < low`）⇒ 剔除**，并留一条点名 `ts` 的警告
   （`src/chanlun/data/types.py:81-88`）。判据是**严格小于**：一字板
   （`high == low`）不受影响 —— 涨停/跌停/无成交都会产生它，是合法行情。
2. **越界（`open` 或 `close` 出了 `[low, high]`）⇒ 保留，只警告**
   （`src/chanlun/data/types.py:90-99`）。计数在**倒挂剔除之后**的帧上算。

两档是刻意分开的，不是"顺手少写一个 `drop`"。

**依据**：

- **倒挂行的价格区间是空的，而缠论结构判据全部建立在区间比较上。**
  分型四条件（`src/chanlun/chan/fractal.py:53-58`）是
  `b.high > a.high and b.high > c.high and b.low > a.low and b.low > c.low`
  （底分型对偶）；包含关系判定 `_contains`（`src/chanlun/chan/include.py:20-24`）
  也是纯 `high`/`low` 比较。倒挂行不只是"脏一行"：`merge_bars`
  （`src/chanlun/chan/include.py:56-99`）要么把它原样 append 成一根倒挂的合并K线，
  要么在它与左邻构成包含关系后按 `max/max`（向上）或 `min/min`（向下）吸收 ——
  **两个方向都可能产出 `high < low` 的合并K线**。
  实测（2026-10-06，全库 5613 个 parquet / 9,119,671 行）：
  `sh.000001` 日线 1992-06-16 那根（`o=1172 h=1136 l=1164 c=1167`）在
  `merge_bars` 之后确实变成一根 `high=1135.61 < low=1152.69` 的合并K线
  （`idx=333`）。见 `docs/evidence/2026-10-06-行情结构不可能的行.md`。
- **越界只警告，是因为剔除的代价更大。** `open` 不参与任何判据
  （`src/chanlun/chan/` 下没有一处读 `open`）；`close` 不参与分型/笔/线段/中枢的
  极值比较，但**会进 MACD** —— `src/chanlun/chan/signal.py:209` 与
  `src/chanlun/chan/divergence.py:357` 都是按 `bars["close"]` 现算的，
  所以它确实能影响背驰。可是**剔除一行会少一根 K 线**，包含处理、分型、笔
  随之整体重排 —— 那个代价大于"一个字段偏一点"。在拿到"越界行真的改变了
  结构"的实测证据之前，只观测、不改。
  （**注**：最初那份建议书给的 rationale 是「close 出界不参与分型四条件的
  极值比较」—— 只对了一半，它没提 `close` 进 MACD 这一路。）
- **落点选 `normalize`，因为它是唯一的门。** `store.read`
  （`src/chanlun/data/store.py:88-100`）与 `ChanEngine.full`
  （`src/chanlun/chan/engine.py:170-172`）都调它；写盘路径
  `store.write` / `store.upsert` 也调它。放在这里，读、写、引擎三条路都覆盖。

**被否决的替代方案**：

- **在 `store.read` 里过滤**：`ChanEngine.full()` 也会调 `normalize`，但它同时
  接受外部直接传进来的 DataFrame（回测、探针、测试都这么干）。只改 `store.read`
  挡不住那条路。
- **把越界也一并剔除**：见上，代价是少一根 K 线导致结构重排，而越界本身
  不使区间为空。先观测。
- **只警告不剔除倒挂**：倒挂行会真的产出 `high < low` 的合并K线（已实测），
  这不是"脏一点"，是让分型四条件自相矛盾（`b.high > a.high` 与
  `b.low < a.low` 可以同时为假）。
- **在数据源解析层（`*_source.py`）过滤**：源有 5 个（baostock / tdx /
  腾讯 / 新浪 / 申万），每个都要改，且旧库里的存量行不会因此消失。
- **改 `high`/`low` 让区间成立（如取 `min`/`max`）**：那是**发明数据**。
  本项目对数据质量存疑一律照实记录、不谎报。

**后果 / 变更历史**：

- **用户可见行为：极少数票的结构会变。** 全库实测存量命中倒挂 **2 行**、
  越界 **62 行**（26 根 `open` + 36 根 `close`）。影响半径：
  - `sh.000001` 日线：少一根倒挂的合并K线（`idx=333`），
    合并K线 7004 / 分型 3018 / 笔 634 / 线段 80 / 中枢 13 **逐项不变**。
  - `us.dji` 日线：那根倒挂行**没有**变成倒挂的合并K线（被吸收了），
    但剔除它让 **分型 2026 → 2024**（−2）；合并K线 4562 / 笔 429 /
    线段 57 / 中枢 9 不变。
  ⇒ 是**真实但极小**的行为变更，不是 no-op，也不是 `inconclusive`。
- **幂等性**：全库 `normalize` 两次、行数不一致的文件 **0** 个。
- **顺带量到一件既有问题，未修**：磁盘上仍有 **165 行非正价格**（旧判据写入
  的存量：`sh.600759` 5 分钟 142 行 / 30 分钟 22 行、`sz.300114` 日线 1 行），
  它们在 `store.read` 时被静默剔除 —— 读得到的结果是对的，只是每次读都在
  重算一遍。已登记为已知空白（见 §5）。
- **两个数据源测试的探针取值改了**（`tests/data/test_sina_source.py:57-72`、
  `tests/data/test_tencent_source.py:73-91`）：它们原本拿 `high < low` 当
  "四值互异"的探针，现在会整帧被剔空。改成 `low 1 ≤ open 2 ≤ close 3 ≤ high 4`
  —— 仍四值互异、逐列断言不变，测的仍是字段映射。
- **`chan/` 下一行未改**：本条不涉及缠论判据，只涉及"什么样的行不许进引擎"，
  因此不需要 `optimizer/theory/` 条目。版本影响：`PATCH`。

---

#### D-47 baostock 会话必须带超时；连续失败必须停批

**决策**：两件事，都在 2026-10-08 那次「定时任务静默挂死 6 小时 14 分」之后立。

1. **给 baostock 那个常驻 socket 装上超时。** 新增
   `SOCKET_TIMEOUT = 30.0` 与 `bounded_login()`
   （`src/chanlun/data/baostock_source.py:34-73`）。后者在
   `socket.setdefaulttimeout(SOCKET_TIMEOUT)` 的作用域内调 `bs.login()`，
   `finally` 里恢复进程原值。**所有 baostock 会话都必须从这里登录**：
   `_ensure_login()`（`src/chanlun/data/baostock_source.py:76-83`）与
   `calendar._fetch_from_baostock()`（`src/chanlun/calendar.py:164-173`）
   是仅有的两处，两处都已改；`universe.py` 复用 `_ensure_login`，自动覆盖。
2. **连续失败停批。** 新增 `MAX_CONSECUTIVE_FAILURES = 20`
   （`src/chanlun/__main__.py:83-89`），在 `_sync_period` 里计数
   （`src/chanlun/__main__.py:720-744`）：`ok` 清零、**`skipped` 也清零**、
   `failed` 累加，到阈值就 `break` 并把原因写进 `SyncOutcome.aborted`
   （`src/chanlun/__main__.py:626-633`）。
   `_daily_exit_code` 见到 `aborted` 返回 1（`src/chanlun/__main__.py:1086-1101`），
   `_cmd_sync` 同样返回 1（`src/chanlun/__main__.py:597-600`）。

**依据**：

- **baostock 的 socket 是裸创建的，`RETRIES` 拦不住静默挂死。**
  `.venv-chanlun/.../baostock/util/socketutil.py` 的 `SocketUtil.connect()`
  直接 `socket.socket(socket.AF_INET, socket.SOCK_STREAM)` + `.connect(...)`，
  **从不 `settimeout`**；`send_msg()` 又在 `while True` 里 `recv(8192)`
  直到 `receive[-13:] == b"<![CDATA[]]>\n"`。服务端**接受连接却不回包**时，
  `recv` 永久阻塞。而 `fetch_bars` 原有的 `RETRIES` 只在**拿到响应之后**才生效
  （零行 / `error_code != "0"`），对「连响应都没有」这一路完全无感。
- **事故代价（2026-10-08 实测）**：17:35:41 起的定时任务卡在 `sz.000033`，
  到 23:54 被手动 kill 时已过 **6 小时 14 分**；`ps` 的累计 CPU 只有 **2.8 秒**
  （不是"在算"，是"在等"），主线程栈停在
  `sock_recv → sock_recv_guts → sock_call_ex → sock_recv_impl → __recvfrom`，
  `lsof` 显示 `TCP 192.168.2.97:57149->114.94.20.42:10030 (ESTABLISHED)`
  —— 对端就是 `public-api.baostock.com:10030`。落库停在 17:36:32 的第 26 只票，
  `structure_snapshot` 一直停在 2026-10-01 的 2 行（第 5~7 步从未跑完）。
  **没有任何东西会把它叫醒** —— 它会一路卡到第二天开盘，
  然后和下一次 17:30 的触发重叠。
- **超时不需要新判据，因为 baostock 自己会把它翻成普通错误。**
  `send_msg()` 的 `except Exception` 吞掉 `TimeoutError` 返回 `None`，
  `.venv-chanlun/.../baostock/security/history.py` 第 106 行 把 `None` 翻成
  `data.error_code = cons.BSERR_RECVSOCK_FAIL`（`10002007`）、
  `error_msg = "网络接收错误。"`。于是走的是 `fetch_bars` **既有**的重试分支：
  重登 → 再试 → 三次后抛 `DataSourceError`。
  `bs.login()` 每次都新建 socket（`.venv-chanlun/.../baostock/login/loginout.py` 第 70-72 行 无条件
  `SocketUtil().connect(...)`），所以「重登」天然丢掉了坏掉的 socket。
  实测复现（黑洞服务端）：`修复后 SOCKET_TIMEOUT=0.3s ⇒ DataSourceError ... 10002007
  网络接收错误。（0.3s）`；`修复前 SOCKET_TIMEOUT=None ⇒ 永不返回`。
- **30 秒这个数**：全市场实测约 1.5~2 s/只，30 秒对一次日线请求足够宽裕，
  同时把单只的最坏代价钉在 `3 × 30s + 2 × 1.5s ≈ 93s`。
- **20 这个数是按时间选的，不是拍的**：`20 × 93s ≈ 31 分钟`。
  停批的意义在于「今天这条链路是坏的」这个判断必须在**下一个交易日开盘前**
  做出来 —— 不停批的话 5471 只 × 93s ≈ **141 小时**。
- **`skipped` 必须清零**：零行意味着数据源**正常应答**了（会话是活的），
  它跟 `failed` 不是一回事。混在一起数会在「一批停牌/退市票」上误触发停批。
- **停批必须能从外部看见**：launchd 只记退出码，那是这条链路唯一能被外部
  看见的信号；摘要里也要印出来，否则「成功 20 失败 20」看起来像零星故障。

**被否决的替代方案**：

- **进程级 `socket.setdefaulttimeout(30)`**：会改掉整个进程的默认超时 ——
  看盘页的 uvicorn 跟定时任务可能同源同进程，会被一起改掉。
  `bounded_login()` 只改登录那一小段：socket 把创建时的默认值记在**自己身上**，
  所以常驻 socket 仍是带超时的，而进程默认值原样还回去。
- **工作线程 + `join(timeout)`**：阻塞的线程收不回来（每只失败票攒一个），
  而且 baostock 是**单会话 socket**（`context.default_socket`），多线程共用会串包。
- **只在 `bs.login()` 之后设超时**：登录自己的 `connect()` / `recv` 仍然无界。
- **猴子补丁 `SocketUtil.connect` / `send_msg`**：改第三方库的代码，升级即失效；
  而且没必要 —— `setdefaulttimeout` 的作用域已经够用。
- **把黑洞端到端复现放进测试套件**：它在修复前（红）状态下会**永远挂住**，
  把整个套件拖死。所以黑洞复现放在证据文档的探针里（父进程 20 秒杀掉子进程），
  套件里只放快的护栏。
- **阈值取更小的值（如 3、5）**：连续 3~5 只失败在真实全市场里会误触发
  （停牌/退市/新上市票扎堆是常见的）。20 只 × 93s ≈ 31 分钟仍在
  「开盘前能判断」的窗口内，两头的余量都比 3 大。
- **只加超时、不加停批**：超时只把单只钉在 93 秒，全市场仍是 141 小时 ——
  仍然跑不完，仍然会重叠到第二天。两道防线缺一不可。

**后果 / 变更历史**：

- **用户可见行为**：
  - 数据源静默时，单只票从「永久卡住」变成「约 93 秒后记为失败」。
    失败的票会写进 `meta.sync_state.error`，和其它失败没有区别。
  - 连续 20 只失败 ⇒ 本次同步**提前结束**，CLI 退出码 1，
    摘要里出现「**已停批** 连续 20 只失败，停批（疑似数据源不可用或会话失效）；
    最后一只 …」。`total` 仍是全量数，所以 `ok + failed + skipped < total`
    就是「没跑完」的标志。
  - 定时任务（`deploy/com.chanlun.daily.plist`）在停批时退出码为 1，
    launchd 会记下来。
- **不影响缠论判据**：`chan/` 下一行未改，只改「取数怎么失败」与「失败多少要停」。
  因此不需要 `optimizer/theory/` 条目。版本影响：`PATCH`。
- **已知空白，本次未修**（已登记进 §5）：
  - **launchd 日历触发之间没有互斥**，一个还在跑的实例会和第二天 17:30 的
    触发重叠，两个进程同时写 `meta.db` 与同一批 parquet；plist 里也没有
    `ExitTimeOut` / watchdog。本次修复把「跑不完」的概率压低了，
    但**没有消除重叠的可能**。
  - 定时链路只跑 `--periods day`，30/5 分钟周期仍只能手动补。
- **证据**：[`docs/evidence/2026-10-08-定时任务静默挂死.md`](docs/evidence/2026-10-08-定时任务静默挂死.md)；
  探针 [`optimizer/tools/measure_baostock_socket_hang.py`](optimizer/tools/measure_baostock_socket_hang.py)。

---

#### D-48 零行同步不许抹掉已有水位

**决策**：`sync_one` 在**取到零行**和**取数抛异常**这两条路径上都不再直接写
`meta.sync_state`，而是统一走新增的 `_keep_watermark()`
（`src/chanlun/data/sync.py:79-123`）：

1. **本地 parquet 有数据** ⇒ 用**它自己的首末 `ts` 与行数**回填水位
   （`store.read(key, period)`）。水位从此是「本地到底有什么」的忠实投影，
   而不是「上一次网络请求返回了什么」的流水账。
2. **本地 parquet 也没有数据**（真·新股 / 从未同步过）⇒ 保留**上一行**
   `sync_state` 的 `start_ts` / `end_ts` / `rows`，**绝不写 `None`**；
   连上一行都没有才写空。

两条路径都把 `error` 原样带进去：零行是 `error=None`（**不是失败**），
异常是 `error=str(exc)`（**是失败**）。**分类没变，变的只是水位怎么写。**

**依据**：

- **触发**：2026-10-09 00:05 那次「补回填」跑在**交易日开盘前**。
  `start_for()`（`src/chanlun/data/sync.py:43-59`）算出「本地最后一根是 10-08，
  下一个交易日是 10-09，`10-09 <= end`」⇒ 去请求**10-09 那根还不存在的 bar**，
  数据源照实返回零行。旧代码在零行分支写
  `meta.set_sync(conn, key, period, None, None, 0, ...)`
  —— **一条零行的响应，把一只已经同步到 10-08 的票的水位清零了。**
- **下游因此把它当成「从未同步」**：`scanner.check_sufficiency()`
  （`src/chanlun/scan/scanner.py:139-140`）看到 `start_ts` 是空串，返回
  `Sufficiency(False, "数据不足：meta.sync_state 无该周期记录（尚未同步），已跳过")`；
  `__main__._snapshot_all()`（`src/chanlun/__main__.py:1215-1216`）见到
  `not suf.ok` 就 `continue` ⇒ **这只票当天不进结构快照**。
- **量到的规模**：那次补回填后 `sync_state` 里 `end_ts IS NULL` 的有 **235** 只。
  逐只实拉 `2026-09-20..2026-10-09` 复核
  （`logs/probe-dropped-all-2026-10-09.log`）：

  | 判定 | 只数 | 依据（本地 parquet 的最后一根） |
  |---|---|---|
  | **其实有数据，被误判** | **23** | ≥ 2026-09-20（实为 10-08） |
  | **确实没有数据**（退市 / 长期停牌） | **212** | 落在 2018-12-25 … 2026-07-13 |

  23 只误判票的 `sync_state.updated_at` 是 `00:05:38`…`00:07:51`
  —— 正好是那批请求的**前 23 只**。清单：
  `000001 000002 000006 000007 000008 000009 000010 000011 000012 000014
  000016 000017 000019 000020 000021 000025 000026 000027 000028 000029
  000030 000031 000032`。
- **不是区间取错**：另起进程用 `2026-10-01..2026-10-08` 拉这 23 只，每只
  `rows=1`；用 `2026-09-20..2026-10-09` 拉，每只 `rows=8`。
  所以零行只来自「请求了还没有的那一天」。
- **这不是偶发，是确定性**：只要 `end` 覆盖到一个「是交易日、但 bar 还没发布」
  的日期，**任何一只已经同步到最新交易日的票都会被清零**。开盘前跑全市场 ⇒
  全市场 5471 只的水位一起归零。
- **D-47 的停批也看不见它**：`_sync_period` 在零行分支走 `skipped += 1`
  并把 `consecutive` 归零（`src/chanlun/__main__.py:739-742`），所以
  「数据源整体返回空」在停批统计里和「这些票本来就没事」长得一模一样。
  D-47 写下的「零行 ⇒ 会话是活的」这条推断**是错的**（见替代方案 1），
  那句注释已就地改掉。

**被否决的替代方案**：

1. **「水位落后于最新交易日、却拿到零行」⇒ 记 `failed`（而不是 `skipped`）。**
   这是本次最初打算采用的口径，**实测否决**：
   - 那 212 只退市/长期停牌票**每跑一次都是这个形状**（水位旧 + 零行）。
     记 `failed` 等于给 212 只票写一条**永远不会消失**的 `sync_state.error`：
     页面每次打开都在报错，每次运行的摘要都固定出现「失败 212」。
     **一个永远不清除的假警报比没有警报更糟** —— 它训练运维忽略失败计数。
   - 它**并不能**多识别出「数据源整体静默」：对那 212 只而言「零行」本来就是事实。
   - 有人会指望「至少停批能兜住」。**实测兜不住**：235 只里最长的连续号段只有
     **9 只**（`000004`…`000012`），远低于 `MAX_CONSECUTIVE_FAILURES = 20`，
     停批**根本不会触发**。也就是说这条口径**买不到任何保护**，却要付
     212 条永久假警报的代价。
     （复现：把 235 只按 `code` 排序，取「前三位相同且后三位逐 1 递增」的最长一段。）
2. **把 `end` 夹到「最后一个已发布的交易日」。** 需要一个「今天到底发布没有」的
   权威来源；baostock 没有这种查询 —— `query_trade_dates` 给的是**日历**，
   不是**可用性**。自己按时钟猜「17:30 之后就算发布」是把交易日历换成猜谜，
   而且会误伤**开盘前手动补跑**这条合法路径。
3. **让 `start_for()` 在「唯一候选日就是今天」时返回 `None`（干脆不请求今天）。**
   这会把**当天 17:30 那次增量同步**一起砍掉 —— 而拉当天收盘那根正是定时任务
   存在的理由。**修 A 坏 B。**
4. **用 `universe.delisted` 当闸门。** 实测该列**没有在维护**：`000004`（国华退）
   等已退市票的 `delisted` 仍是 `0`。它区分不出 212 与 23。
5. **只修下游：让 `check_sufficiency()` 认为「`rows=0` 但本地有 parquet」也算够。**
   这是把谎话留在 `sync_state` 里，只让一个读的人不信它。页面上的「本地 0 根」
   与库里的 1394 根仍然自相矛盾，下一个读 `sync_state` 的地方会再踩一次。
   **修数据，不修读数据的人。**
6. **零行时干脆什么都不写（保留上一行原样）。** 比旧代码好，但仍不够：
   真·新股第一次同步拿零行时 `sync_state` 会**没有这一行**，
   `check_sufficiency` 照样报「尚未同步」；而且「水位落后」这个状态永远不会自愈
   —— 它只在**下一次成功的同步**时才更新，而下一次可能同样是零行。
   从本地 parquet 回填是唯一能让水位**当次就正确**的做法。

**后果 / 变更历史**：

- **用户可见行为**：
  - 开盘前（或任何「请求了还没有的那一天」）跑同步，**不再**把票打成「尚未同步」；
    那 23 只票的结构快照会恢复。
  - `sync_state.rows` 现在等于**本地 parquet 的实际行数**，不再等于
    「上次网络请求返回的行数」。对一只已同步到最新交易日的票，跑一次零行同步后
    `rows` 从 0 变回 1394（而不是反过来）。
  - 零行仍然记为 `skipped`（**不是** `failed`），所以 212 只退市票的
    `sync_state.error` 保持为空，摘要里也不会出现「失败 212」。
- **不影响缠论判据**：`chan/` 下一行未改，`scanner.py` 也未改 —— 只改
  「同步空手而归时水位怎么写」。因此不需要 `optimizer/theory/` 条目。
  版本影响：`PATCH`。
- **已知空白，本次未修**（已登记进 §5）：
  - **「数据源整体静默」仍然看不出来。** 修完之后，一个对所有票都返回零行的
    数据源，在摘要里仍然是「跳过 N 只」——**和 212 只退市票的样子完全一样**。
    真正的护栏得是「本次零行占比」这类**批量口径**，不是逐票口径。
  - 212 只退市/长期停牌票**每次全量同步都要白跑一遍**（实测 ~5.4 s/只 ×
    212 只 ≈ 19 分钟，每只还要 2 次重登重试），本次没有引入任何「已知死票」缓存。
- **证据**：[`docs/evidence/2026-10-09-零行同步抹掉水位.md`](docs/evidence/2026-10-09-零行同步抹掉水位.md)。

---

#### D-49 数据的「新不新」由后端一处判断，页面落后就自己去补

**决策**：新增 `freshness()`（`src/chanlun/web/api.py:989`），作为全项目唯一一份
「本地数据落不落后 / 这个周期能不能补」的判断，返回四个键：`expected_as_of`
（最新交易日）、`stale`（本地是否落后）、`syncable`、`sync_hint`。日历取自
`calendar_for()`（`src/chanlun/calendar.py:111-115`，A 股走真实交易日历、港美股走
工作日日历）再取 `last_trading_day()`（`src/chanlun/calendar.py:123`）；「能不能补」
复用 `sync_blocker()`（`src/chanlun/web/api.py:964`），**不新立第二份口径**。

结论随现有响应一起下发，**不新开端点**：

- `/api/bars` 的 payload（`src/chanlun/web/api.py:699`）；
- `/api/structure` 成功分支（`src/chanlun/web/api.py:751`）；
- `/api/structure` 的 404 分支（`src/chanlun/web/api.py:727-736`）—— 本地一根都没有时
  也要答得出「最新交易日是哪天、这个周期到底能不能补」。

页面侧（`src/chanlun/web/static/app.js`）读 `stale`，为真就自动补这一次：请求走
`postSync()`（`app.js:491`），轮询走 `pollSync()`（`app.js:537`）。同一时刻**只飞一个**
（`autoBusy`）+ **只留一个待办槽**（`autoPending`），`autoTried` 以 `${code}|${period}`
记账且**进入即记**，`maybeAutoSync()`（`app.js:613`）/ `requestAutoSync()`（`app.js:624`）/
`runAutoSync()`（`app.js:644`）合起来保证「每会话每只每周期只自动试一次」。
反馈是一枚浮标（`src/chanlun/web/static/index.html:144`，样式
`src/chanlun/web/static/styles.css:972` 与 `:988`），绝对定位在图表右下角、**不占文档流**。
补完若用户**还在看这一只**（`finishAutoSync()`，`app.js:670`）就重新 `load()` 重画。

浮标挂在**哪一只**上，判据是**落库键**而不是用户输入：`state.key`（`app.js:121-127`）
是屏幕上这一只的落库键，`setCode()`（`app.js:2014`）先按用户写的形态占位，成功响应到了
升级成服务端回的 `code`（`app.js:252`），404/4xx 拿不到落库键、只能按请求原样记
（`app.js:233`）。归属由 `chipIsAbout()`（`app.js:598`）判定，换票/换周期第一件事是
`dropChip()`（`app.js:602`，连点击句柄一起清）—— 见「变更历史」第 2 条。

**依据**：

1. **定时任务从来不覆盖用户最常点开的东西。** `universe` 只有 5471 只 A 股，7 个
   A 股指数与 4 个港美自选**一天都没被它碰过**（本决策只让「打开时」能补上，
   定时覆盖仍是空白，见 §5）。实测 2026-10-10（周六）：7 个 A 股指数全部停在
   `2026-09-30`。
2. **「先问再补」的成本是一次本地判断，不是一次网络请求。** `sync_one()` 在本地水位
   已到最新交易日时**不联网**直接短路返回 `skipped`（`src/chanlun/data/sync.py:196-197`）。
   对照实测：`?code=600000`（已是最新）POST `/api/sync` **+0**、浮标不出现。
3. **补完自动重画不需要任何失效逻辑。** `/api/structure` 的缓存键里含 `last_ts`
   （`src/chanlun/web/api.py:414`），parquet 一旦落进新的一根，旧缓存自然不被命中。
4. **让前端自己比日期会立刻分叉出第二份口径。** 「最新交易日」在两个市场可以是两个
   答案 —— 2026 国庆那一周（10-01…10-07）A 股休市、港股开市。日历与 `sync_blocker`
   都已有唯一实现，前端只该读结论。
5. **落库键不是输入原样。** `?code=sz.002898` 服务端回 `code: "002898"`；指数反过来
   必须回 `"sh.000001"` —— 裸 `000001` 与平安银行 `sz.000001` 撞号。这是 D-43 定的
   口径，页面凡是要比「是不是同一只」就必须按它比。

**被否决的替代方案**：

1. **新开 `GET /api/freshness` 端点。** 页面为了一句话要多一次往返，而且它必须与
   `/api/structure` 的 `code`/`period` 解析保持一致（同一个 `normalize_code`、同一份
   404 语义）—— 两份解析就是两个分叉点。挂在已有响应上天然同源。
2. **后端做「每日全量兜底」（cron 补跑 / `daily --force`）。** 用户 2026-10-10 明确
   否掉全市场补跑（那 4955 只缺一根的票，全量补一次要 2~6 小时且还在黑名单风险里）。
   而且它**不解决**本决策要解决的问题：指数与港美股不在 `universe` 里。
3. **每次换票无条件 POST `/api/sync`。** 本地短路让它不至于联网，但它仍要建任务、轮询，
   并让页面等一个它不需要的结果；更糟的是「每次换票都发一次请求」把反面对照
   （新鲜的票 +0 请求）这根线弄丢了。
4. **把进度写进顶栏 `#stamp` / `#stamp-detail`。** 顶栏被钉成**恒定 2 行**，
   写进度会让它随文案跳动 ⇒ 图表上下抖。实测三态（隐藏 / 可见 / `remove()`）的
   `#chart`、`.stage`、`#stamp`、`#stamp-detail` 矩形与 `body` 网格行**逐字相同**，
   就是这条否决的量化证明（§5.3）。
5. **复用 `#notice` 错误面板。** `#notice` 是**错误**的归宿；把「正在补最新」混进去，
   真错误与进度会互相顶掉 —— `renderWatch` 盖掉读失败文案就是同类事故，见 §5。
6. **给自动补加一个 `config.toml` 总开关。** 一个默认关、又没有测试覆盖的开关，
   等于让「页面落后时会不会自愈」变成配置的函数，排查时先要问一句「你那个开关开了吗」。
   这次不加：它的代价（每会话每只一次请求）已被会话内退避限制住。
7. **用 `state.code`（用户输入原样）比较「是不是同一只」。** 实施中先这样写了，
   真浏览器实测出**三处同根因缺陷**（见「变更历史」第 2 条）。全部改到 `state.key`，
   并立下全站不变量：`app.js` 里不许出现 `=== state.code` / `!== state.code` 形式的
   身份比较（`state.code` 只剩「发请求」与「记录用户输入」两个合法用途）。

**后果 / 变更历史**：

- **用户可见行为**：
  - 打开一只落后的票/指数：右下角浮标写「正在补最新数据：本地截至 X，最新交易日 Y…」，
    补完自动重画并写「已补到 Y」，4 秒后自己收起。**同一只本次会话只自动补一次**，
    之后再进入给的是**属于它自己**的可点重试（`本次会话已经自动补过一次，没有再自动重试`）
    —— 不是静默，静默等于把这次失败也一起藏了。
  - **按需同步只作用于你正在看的那一只**：换票、换周期、刷新都会收掉不属于当前视图的
    浮标；后台补完别人的结果**不会**写进当前这张图。
  - 不落后的票**一个请求都不发**（对照 `?code=600000`：POST `/api/sync` +0）。
  - 本地一根都没有（404）时，除原有那句「没有数据」外还给出**具体的最新交易日**与
    「这个周期能不能补」；指数分钟线这类必然被拒的组合会说清原因。
- **布局零位移**：浮标三态下 `#chart` / `.stage` / `#stamp` / `#stamp-detail` 的矩形
  逐字相同，`body` 网格行 `77px 42px 34px 760px` 不变。浮标挂在 `<main>` 内而不是
  `<body>` 的直接子元素下 —— `tests/web/test_frontend_watchlist.py` 有一条「`body` 每个
  子元素都要有网格行」的守卫，浮标进了 `<body>` 会让它变红。
- **不影响缠论判据**：`chan/` 与 `scanner.py` 一行未改，也不涉及任何阈值 ⇒
  **不是判据变更**，不需要 `optimizer/theory/` 条目。版本影响：新增向后兼容的功能，
  语义上是 `MINOR`；但本次**不发版**，`pyproject.toml` 未动，条目留在
  `CHANGELOG.md` 的 `[Unreleased]`（§5 那条「`0.7.0` 没有对应版本段」的记账欠债仍在，
  属独立任务）。
- **变更历史**：
  1. **2026-10-10 落地**：后端 `freshness()` + 三处挂载 + 11 条后端用例；前端
     `postSync` / `pollSync` / 浮标 + 17 条前端静态守卫用例（其中
     `tests/web/test_frontend_sync_button.py` 有一条按新契约重写）。
  2. **2026-10-10 同日修正三处同根因缺陷**（真浏览器实测发现，逐条 TDD：先加不变量
     守卫看它变红，再改实现）。根因只有一个：**拿用户输入原样（`state.code`）当身份**。
     - **浮标不绑视图**：`?code=sz.002898`（本地停在 `2026-07-16` 的停牌票）自动补成
       零行后，浮标写着「没补到新数据…点这里重试」并**保持可点**；此时换到 `600000`，
       同一段文案**仍挂在 `600000` 的图上** —— 点下去重试的是 `002898` 的 job。
       **把 A 的失败挂在 B 的图上，比不显示更坏。**
       修法：`chipIsAbout()` + `dropChip()`，`load()` 开头先收走不属于当前视图的浮标。
     - **补完不重画**：`finishAutoSync` 里 `state.code === job.code` 对带前缀的写法
       **永远为假** ⇒ 以 `?code=sz.000858` 打开时页面自相矛盾（浮标「已补到 2026-10-09」，
       顶栏仍写「截至 2026-10-08」，而数据其实已经落库）。变异对照：这一句改回
       `state.code`，顶栏**停在 10-08**；改回 `state.key`，顶栏走到 **10-09**。
     - **自选按钮永远显示「未加」**：`inWatchlist` / 自选行与行业卡高亮 /
       首屏选中行判断都比了 `state.code`。实测 `?code=sh.600759`（自选里存的是裸
       `600759`）：修前按钮 `☆ 加自选`、无高亮；修后 `★ 已在自选`、高亮 `600759`。
  3. **已知空白，本次未修**（已登记进 §5）：本地**一根都没有**（404）时没有落库键可回，
     `state.key` 只能是输入原样 ⇒ 那一只的自选按钮/高亮仍可能误报；定时任务对指数与
     港美自选仍是零覆盖（本决策只让「打开时」补上）。
- **证据**：[`docs/evidence/2026-10-10-按需拉取验收.md`](docs/evidence/2026-10-10-按需拉取验收.md)。
- **变更历史（D-50，2026-10-11）**：浮标语义被 `#### D-50` 取代 ——
  从"有事才出现、4 秒后自动消失、失败态整块可点"改成"常驻 + 里面放真 button"，
  `maybeAutoSync()` 相应更名为 `noteFreshness()`。本条的其余部分
  （`freshness` 契约、`state.key` 落库键、`sync_blocker`、会话内退避）仍然有效。

---

#### D-50 浮标常驻：它是「这一只这一周期」的数据状态读数，落后时自己可点

**决策**：右下角浮标从"有事才冒出来、4 秒后消失"改成**常驻**，恒定回答
「你正在看的这一只、这个周期，数据到哪儿了、能不能补」，并把动作从整块浮标
挪进一个真 `<button>`。

- 后端一行未动：判据仍然全部来自 `/api/structure` 的 `stale` / `syncable` /
  `sync_hint` / `expected_as_of`（`src/chanlun/web/api.py:989`）。
- 前端唯一写浮标的地方是 `noteFreshness()`（`src/chanlun/web/static/app.js:624`），
  它按后端结论分派三种常驻态：`fresh` / `stale` / `blocked`。
- 七态：`fresh` / `stale`（可点）/ `syncing` / `queued` / `done` / `blocked` /
  `failed`（可点），实现见 `chipState()`（`app.js:712`）。
- 手动入口：浮标里的 `#sync-chip-go`（`index.html:151`、`styles.css:1003`），
  点击走 `requestAutoSync(job, true)`（`app.js:639`）—— **只**绕过
  `autoTried` 的"本次会话只自动试一次"闸门，其余（同时只飞一个、共用
  `runAutoSync`）一条都不绕。
- 本地一根都没有（404）时浮标整条让位：`job.quiet === true` ⇒ `dropChip()`
  （`app.js:716`，`dropChip` 本体在 `app.js:608`），把画面留给 `#notice`
  （它有完整解释 + 命令行 + 可点按钮）。
- 整块浮标继续 `pointer-events: none`，可点性放在子元素上
  （`styles.css:995`、`styles.css:1004`）；`no-cache` 让浏览器每次回服务端校验
  （`src/chanlun/web/app.py:34`）。

**依据**：

1. 用户 2026-10-11 原话：「我看现在这个也没有那个手动拉取的那个按钮呀。就比如说是
   ST洲际的30分钟K线，我点进去之后，它也没有自动拉取，也没有手动拉取的按钮」。
   实测这句话**成立**：全 UI 只有一个手动入口 `app.js:229` 的「同步这个周期」，
   它只在 `/api/structure` 返回 **404** 时渲染；`600759` 30 分**有**本地数据、只是
   落后（`2026-09-30 15:00`），走的是 200 分支 —— 那个分支上一个按钮都没有。
2. 同一句里的「也没有自动拉取」在**正确的点击流程里不成立**：实测
   `?code=002309&period=day` 进去点 `.period[data-period=30]`，2 秒后浮标出现
   「已补到 2026-10-09 15:00」、`POST /api/sync` 计数 +1。真正的问题是
   **一切正常时彻底静默**：用户分不出"已是最新"和"功能没跑"。
3. 第三个成因是缓存：实测 `curl -sI /static/app.js` 只有 `last-modified` + `etag`，
   **没有** `Cache-Control`。Chrome 对没有显式新鲜度信息的响应可以按
   `(now - last_modified) * 0.1` 自己算窗口直接拿缓存 ⇒「我改了前端、你看的是旧页面」。
4. 一次真实同步实测 2.0 s – 28.5 s（baostock 间歇 `[Errno 60] Operation timed out`
   后重试），而 D-49 的浮标是 12px、`var(--rice-dim)`、4 秒后自己消失 ⇒
   「正在补最新数据…」这条信息很容易被漏看。
5. 布局零位移已实测：浮标是 `.stage` 内的绝对定位元素（`right: 18px; bottom: 12px;
   z-index: 3`），不参与任何流。D-49 量过"隐藏 / 强制可见 / 移除"三态下
   `#chart`、`.stage`、`#stamp`、`#stamp-detail`、`#ledger` 的 rect 与
   `body` 网格行数逐项相同；D-50 复量六种配置（隐藏 / `fresh` / `stale` + 按钮 /
   `failed` + 按钮 / `blocked` / 整块移除）仍逐字相同（证据见
   `docs/evidence/2026-10-11-浮标常驻验收.md`）。

**被否决的替代方案**：

1. **把手动补数据做成顶栏按钮** —— 顶栏恒定 2 行是硬约束（D-45 /
   `docs/superpowers/specs/2026-10-03-topbar-stable-layout-design.md`），
   再加一个按钮会改顶栏高度；而且顶栏讲的是"这一只的行情口径"，按钮讲的是"动作"，
   混在一格会让那格同时承担两种语义。
2. **落后时也自动消失，只在有事时出现** —— 这正是现状，用户看不懂"没有浮标"
   到底是"已是最新"还是"功能没跑"。
3. **后端新增 `behind`（落后 N 个交易日）** —— 要在 `freshness` 的返回结构里加字段、
   用 `calendar_for` 数交易日，多一处口径多一组测试；而
   「本地截至 2026-09-30 15:00 · 最新 2026-10-09」已经把同一件事说清楚了。
4. **把整个浮标 `div` 变成按钮**（`role="button"` + `tabindex`）—— 会让一个
   `aria-live` 状态区同时是可点元素，读屏语义混在一起，键盘焦点框也会套住整块状态文字。
5. **404 时浮标与 `#notice` 都显示**（今天的实际行为）—— 实测两处同时说话，
   而且浮标那行漏出一个 `?`（`job.local` 为 `null`）；`#notice` 更显眼、在流内、
   带完整解释与命令行，让位给它更清楚。
6. **浮标点击复用 `syncThisPeriod()`** —— 它把反馈写进 `#notice`，而有本地数据时
   `#notice` 是隐藏的，点下去会像没反应。
7. **保留「补完 4 秒后自动消失」** —— 常驻之后更好的做法是让紧接着的 `load()`
   用后端真结论覆盖那句「已补到 X」，不需要定时器（定时器还会去动别人）。

**后果 / 变更历史**：

- 浮标现在**每次打开页面都在**（右下角一小块、`pointer-events: none` 不挡交互）。
  这是用户明确选的（2026-10-11：`A. 右下角浮标常驻化 + 可点`）。
- `skipped`（后端说"水位已是最新、文件里还没有新数据"）从 D-49 的**失败态**
  改成**落后可点态**：D-49 渲染出来是「没补到新数据：本地记录已是最新，文件里还没有新数据」，
  一句话自己否定自己；现在如实说"落后 + 可以再点一次"。
- `failed` 的文案前缀从「没补到新数据：」改成「补数据没成功：」——
  「没补到新数据」这个说法只留给 `skipped` 那一支。
- `syncing` 文案里的 `?` 改成「无本地数据」。
- `styles.css` 的 `.is-retry` 更名为 `.is-action`（它现在涵盖"落后可补"和"失败重试"两种）。
- 静态资源与 `/` 加了 `cache-control: no-cache`（**无条件**，用户 2026-10-11 确认）：
  `no-cache` 不是"不缓存"，是"每次回服务端校验"，没变仍是 304。
  `/` 由 `@app.get("/")` 直接返回 `FileResponse`，**绕开** `StaticFiles.is_not_modified`
  ⇒ 首页没有 304 通路，每次开页面重发 10.5 KB（换来"改了 `index.html` 立刻生效"）。
- **本次不涨版本号**（`pyproject.toml` 不动），CHANGELOG 条目留在 `[Unreleased]`。
- **`chan/` 一行未动** ⇒ 不是判据变更，不需要 `optimizer/theory/` 条目。
- D-49 的浮标语义由本条取代；D-49 记录的其余部分（`freshness` 契约、
  `state.key` 落库键、`sync_blocker`、会话内退避）全部照旧有效。

---

## 3. 结构层（`src/chanlun/chan/`）

`chan/__init__.py` **是空文件（0 字节）**，包内无导出。

### 3.1 管线

唯一装配点是 `ChanEngine._compute()`（`engine.py:176-203`）：

```
raw bars: DataFrame(ts, high, low, close)
  │ normalize()                        engine.py:152/160
  ▼
  │ merge_bars(bars)                   include.py:53       ← 包含处理（第65课）
  ▼ merged: list[MergedBar]
  │ find_fractals(merged, bars=bars)   fractal.py:41       ← 分型（第62课）
  ▼ fractals: list[Fractal]
  │ build_strokes(fractals)            stroke.py:51        ← 笔（第62课+第77课）
  ▼ strokes: list[Stroke]
  │ build_segments(strokes, policy)    segment.py:462      ← 线段（第67/78课）
  ▼ segments: list[Segment]
  │ find_pivots(segments, level)       pivot.py:126        ← 中枢（第17/20课）
  ▼ pivots: list[Pivot]
  │ signal_fn(bars, segments, pivots, level)   engine.py:185-186
  ▼ signals: list[Signal]              signal.py:117-139   ← 三类买卖点
```

- **引擎不内置买卖点规则**，`signal_fn` 由调用方注入（`engine.py:49-52, 157, 185-186`）——其中 `145` 原指向 `as_dict()` 里的一个**空行**，位移后无法按内容定位，2026-10-13 改成真正体现「注入」的那一行（构造函数里的 `signal_fn` 形参）。
  仓库内实际装配均为 `signal_fn=find_signals`：
  `web/api.py:426`、`scan/scanner.py:512`、`backtest/runner.py:173`、
  `optimizer/agent.py:102`。
- `find_signals` 内部补两步：`macd(bars["close"])`（`signal.py:208-209`）、
  `classify_trends(pivots, level)`（`signal.py:381`）。
- **`trend` 不进 `Snapshot`**：`Snapshot` 字段为
  `code, period, as_of, level, merged, fractals, strokes, segments, pivots,
  signals, version`（`engine.py:60-79`），**无 `trends` 字段**。

### 3.2 模块 ↔ 缠论概念

| 文件 | 职责 | 课号 |
|---|---|---|
| `types.py` | frozen dataclass + `Status` 三态 + JSON | — |
| `include.py` | 原始 K 线包含处理 | 第 65 课 |
| `fractal.py` | 合并 K 线上找顶/底分型 | 第 62 课 |
| `stroke.py` | 分型 → 笔（同类合并/间隔/回退） | 第 62 课 + 第 77 课修订 |
| `segment.py` | 笔 → 线段（特征序列/缺口/DP） | 第 67 课定型，71/77/78/81 修补 |
| `pivot.py` | 线段 → 中枢（延伸与扩展） | 第 17/20 课，上限第 33 课 |
| `trend.py` | 中枢序列 → 趋势/盘整 | 第 17/20 课、中心定理二 |
| `macd.py` | 通达信口径 MACD / 面积 / 背驰纯函数 | 第 24/25 课 |
| `signal.py` | 结构 + 力度 → 三类买卖点 | 第 17–20 / 24–28 课 |
| `state.py` | 稳定 ID / 确认写一次 / 失效留痕 / PIT 过滤 | 工程约束 |
| `engine.py` | 管线装配、全量/增量、窗口裁剪 | 工程约束 |

### 3.3 可调常量

| 常量 | 值 | 位置 | 依据 |
|---|---|---|---|
| `MIN_GAP` | `4` | `stroke.py:19` | 顶底间至少一根独立合并 K 线 → 一笔至少 5 根 |
| `MIN_STROKES` | `3` | `segment.py:36` | 线段至少三笔，笔数必为**单数** |
| `MIN_SEGMENTS` | `3` | `pivot.py:66` | 中枢 = 至少三个连续次级别走势类型重叠 |
| `MAX_SEGMENTS` | **`8`** | `pivot.py:85` | 第 33 课：延伸不超过 5 段 + 形成中枢的 3 段 |
| `LEVEL_UP` | `{"1":"5","5":"30","30":"day","day":"week","week":"month"}` | `pivot.py:81-88` | 未知级别退化为 `"<level>+"`；**`"1"` 是 2026-10-13 补的**，缺它 1 分钟基准的链在第一步就断（见 D-51 变更历史）|
| MACD 周期 | `fast=12, slow=26, signal=9` | `macd.py:148` | 通达信/同花顺口径 |
| 分型窗口 | 3 根合并 K 线、**严格不等式** | `fractal.py:59-66` | 严格不等式避免"平台等高"误判 |
| `Segment.policy` 默认 | `"lesson_67_78"` | `chan/types.py:107` | `Lesson6768Policy`（`segment.py:231`） |
| 左端起段选择 | 取**最早的前缀**上**第一个可行**的起点（因果；不再是全序列 argmax） | `segment.py:337-411` | **原文空白项**，工程判据（[D-30](#d-30-被截断的左端起段取还能确认最多线段的起点)，已由 [D-40](#d-40-线段划分改为前缀因果缺口确认只看已出现的笔左端起段取最早的可行起点) 取代） |
| 状态默认 | `Stroke/Segment/Pivot/Trend = CONFIRMED`；`Signal = TENTATIVE` | `chan/types.py:71,105`；`pivot.py:110`；`trend.py:54`；`signal.py:127` | 信号须经 `_seal` 升级 |

### 3.4 结构层决策记录

#### D-14 中枢延伸上限 = 8 段（第 33 课）

**决策**：`MAX_SEGMENTS = 8`（`pivot.py:85`）。

**依据**：第 33 课《走势的多义性》原文：

> 一般来说，中枢的延伸不能超过5段，也就是一旦出现6段的延伸，加上形成中枢本身
> 那三段，就构成更大级别的中枢了。

5 + 3 = 8。

**被否决的替代方案**：第 29 课的 **9**。9 是"**升级后的最小内容量**"，
不是本级别上限。round-004-G2a 取 9 是把这两个数当成了同一个数。

**不收口的代价**（`pivot.py:31-33, 183-185`）：9 段以上重叠会把两个中枢吸成一个、
盘整被压掉、`b1`/`b2` 出不来。

**变更历史**：`0.0.3` 引入时是 9 → `0.0.11`（commit `40a05d3`）收口到 8。
G2a 的 9 段上限随之作废。

#### D-15 ZG/ZD 与 GG/DD 的口径，以及"区间不可混用"

**决策**：
- `zg = min(前三段 high)`，`zd = max(前三段 low)`；`zg > zd` 才成枢，**相切不算**
  （`pivot.py:8-9, 165-169`）。
- `gg = max(Zn 的 high)`，`dd = min(Zn 的 low)`；`Zn` = 与中枢方向一致的次级别
  走势类型。
- **判"离开中枢"用 `[ZD, ZG]`；判趋势用 `[DD, GG]`**（`pivot.py:12-13, 121-123`）。

**依据**：第 20 课 Z 走势段定义原文：

> 为方便起见，以后都把这些与中枢方向一致的次级别走势类型称为Z走势段，按中枢中的
> 时间顺序，分别记为Zn等，而相应的高、低点分别记为gn、dn，定义四个指标,
> GG=max(gn), G=min(gn), D=max(dn), DD=min(dn)，n遍历中枢中所有Zn。

**关键细节**：`Zn` 只取组内**与中枢方向一致**的段（方向由 A、C 段定，
`direction = trio[0].direction`；第 20 课「A、C段，其方向与中枢形成的方向是一致的」）；
反向段（B 以及延伸中夹进来的回抽段）**不进** GG/DD。组里**最后一个 Zn 是离开段**，
不算"中枢中"的 Z 走势段，所以也不进 GG/DD（`inside = zn[:-1]`，`pivot.py:210-219`）。

**实测代价**（`optimizer/tools/measure_l20_dd_gg_scope.py`，24 只票日线，
**2026-10-01 重跑**；共 52 个确认中枢 / 28 个相邻对，其中 `[ZD,ZG]` 不重叠 25 对）：

| GG/DD 的遍历范围 | 上涨 | 下跌 | 高级别中枢 | 无关系 |
|---|---|---|---|---|
| 中枢组**全部**段（含离开段，旧主干） | 0 | 0 | 28 | 0 |
| Zn 同向段，**含**离开段 | 1 | 0 | 27 | 0 |
| **Zn 同向段，不含离开段（本决策）** | **9** | **4** | **15** | **0** |

脚本另用 `trunk_vs_C` 反向校验主干 `Pivot.gg/dd` 与脚本自己的 C 口径，结果为
**0 个不同**（必须为 0，否则脚本结论不能拿来当主干的证据）。旧口径下「趋势」永远
判不出来，而定理二那两句话本该是**完全分类**（"无关系"恒为 0 对）。

**被否决的替代方案**：

- *取中枢组全部段* —— 上表第一行，0/0，趋势判不出来。
- *取同向段但保留离开段* —— 第二行，1/0/27，仍几乎全落进"高级别中枢"。
- *先丢掉最后一段再取同向段*（脚本口径 E）—— 与"不含离开段"同分（9/4/15），
  但组内最后一段**不是**离开段时它会误丢一个真实 Zn，语义不如直接按
  「中枢中所有 Zn」的定义取。
- *`GG/DD` 用 `[DD,GG]` 判趋势但保留全部段* —— 与 D-16 冲突，见 D-16 变更历史。

**变更历史**：本决策是**未提交**的工作区改动。提交前的主干是
`gg=max(s.high for s in group)`、`dd=min(s.low for s in group)`（中枢组全部段，
含离开段）。见 [CHANGELOG.md](CHANGELOG.md) `[Unreleased]` 第 6 条。

> ⚠️ **这组数字会随引擎版本漂移。** `pivot.py` 的 docstring 里记的是更早一次的
> 「46 个确认中枢 / 22 个相邻对 / 上涨 8 · 下跌 4 · 高级别中枢 10」，那是
> D-30/D-31（线段判据）落地**之前**的数；同一脚本现在给出 52 / 28 / 9 · 4 · 15。
> 引用时必须写明**测量日期 + 引擎版本**，不能当成常量。

#### D-16 趋势判据：严格分离，三分支

**决策**：`_direction(a, b)` 只比 `[DD, GG]` 且要求**严格分离**
（`trend.py:74-92`）：

- `b.dd > a.gg` → `1`（上涨）
- `b.gg < a.dd` → `-1`（下跌）
- 否则 → `0`，**形成高级别的走势中枢**（不是趋势）

**依据**：第 20 课"走势中枢中心定理二"原文：

> 前后同级别的两个缠中说禅走势中枢，后GG〈前DD等价于下跌及其延续；
> 后DD〉前GG等价于上涨及其延续。后ZG<前ZD且后GG〉=前DD，或后ZD〉前ZG且
> 后DD=<前GG，则等价于形成高级别的走势中枢。

三分支恰好逐字对应这三句。

**只用 CONFIRMED 中枢**（`trend.py:8-10, 127`）。

**变更历史**：本决策是**未提交**的工作区改动。提交前的主干 `_direction()` 比的是
`[ZD, ZG]`（`b.zd > a.zd and b.zg > a.zg`），即只判中枢**区间**的单调，而不是定理二
说的**波动区间**。见 [CHANGELOG.md](CHANGELOG.md) `[Unreleased]` 第 7 条。

第 11 轮人工决策是「先不动，只在页面标注『趋势按 `[ZD,ZG]` 口径』」——
那条决策的**前提**是"判据按 `[ZD,ZG]` 实现"，D-15/D-16 落地后前提已不存在。
2026-10-01 已就此重新征询，用户选择「**保留 `[DD,GG]`，把页面文案改成一致**」，
故前端文案随之改写（见下）。

**前端文案已同步**：`src/chanlun/web/static/app.js:58` 的 `TREND_BASIS` 现为

```
第一类买卖点取自趋势背驰；趋势按 [DD,GG] 波动区间判（第20课中心定理二；GG/DD 取中枢中所有 Zn 并剔除充当离开段的那一段，不是中枢区间 [ZD,ZG]）
```

（2026-10-03 D-37/D-38 之后，原句「GG/DD **不含离开段**」不再无条件成立：
`group[-1]` 本身不是 Zn 时无需剔除，中枢被段数上限掐停时更是**一段都不剔除**。
原句已按新口径改写，`app.js` 总行数 1483 → 1487，本文件里那 5 处 `app.js` 行区间
按**内容**整体重锚。）

**为什么不只写"趋势按 `[DD,GG]` 判"**：本页**没有"走势类型"分节** ——
`/api/structure` 的响应里没有 `trends` 字段（只有 `fractals`/`strokes`/`segments`/
`pivots`/`signals`），ledger 也只有线段/中枢/买卖点三节。趋势判据在页面上是
**隐形**的，但它决定第一类买卖点出不出现：`signal.py:381-390` 用
`classify_trends` 取出上涨/下跌的中枢组，组内不足两个中枢就跳过 ——
`down_pivots`/`up_pivots` 为空时 **B1/S1 一个都不会生成**。
所以旧口径（趋势恒为 0）等于把第一类买卖点**静默关掉**，而页面上看不出任何异常。
图注因此要说明它影响的是买卖点，而不是一个页面上根本不显示的东西。
"走势类型不上页面"本身记为已知空白，见 §5。

此处曾短暂处于**前后端口径相反**的状态：页面印着"本系统只判 ZG/ZD 单调"，
引擎却按 `[DD,GG]` 判。同处旧注释给的理由（「实测 19/19 对相邻确认中枢在
`[DD,GG]` 上都重叠，按 `[DD,GG]` 判会让 17 组趋势全部退化成『盘整』」）
是在**旧 GG/DD 口径（含离开段）**下测的；D-15 剔除离开段后该理由不成立
（实测变成 9 上涨 / 4 下跌 / 15 形成高级别中枢）。
**教训**：口径改动必须连带检查前端有没有把它写死在文案里 ——
`grep -rn "ZD,ZG\|ZD/ZG" src/chanlun/web/static/` 应作为判据变更的收尾动作。

#### D-17 MACD 面积必须分色累加

**决策**：`hist_area(macd_df, i0, i1, color)` 的 `color` 是**必填参数**，
不许退回 `sum(abs(hist))` 混色求和（`macd.py:188-201`）。

**依据**：第 24 课原文：

> 这里的面积，指的是MACD柱子所围成的面积……向上的看红柱子，向下看绿柱子

**理由**：第 24 课要求两个被比较的走势类型**同向**；混色求和会把反方向的力度也
算进来。

**审计痕迹**：旧实现就是 `sum(abs(hist))`，属审计确认的口径偏离
（`docs/evidence/2026-10-01-chanlun-strictness-audit.md` §2.9）。

**触发段颜色**取该段自身方向：`hist_area(macd_df, seg.src_start, seg.src_end,
seg.direction)`（`signal.py:142-150`）。

**背驰判据** `is_divergence`：后段创新极值**且**面积**严格缩小**才成立；
未创新极值不算、面积相等不算（`macd.py:246-276`）。

#### D-18 MACD 用单遍 EMA 递归，不是 pandas 默认

**决策**：`ema()` 单遍 O(n) 递归，`alpha = 2/(n+1)`，以**首值**为初值
（`macd.py:134-144`）。

**理由**：通达信/同花顺口径。**不是** `ewm(adjust=True)` 的默认行为。
测试用 `ewm(span=N, adjust=False)` 交叉验证（`macd.py:19-22`）。

**未来函数防护**：EMA 单向递归，第 t 个输出只依赖 `X[:t+1]`
（`macd.py:124-125, 156`）。

#### D-19 第三类买卖点用"位置"口径

**决策**：离开段 = `segs[p.end_idx]`，回试段 = `segs[p.end_idx + 1]`
（`signal.py:226-294`）。

**依据**：第 20 课原文：

> 一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别走势类型回试，
> 其低点不跌破ZG，则构成第三类买点

**被否决的写法**：把 `segs[p.end_idx + 1]` 当离开段。真实线段首尾相连，
那**必然**是一段反向回抽段。旧写法在 148 只票 / 193 个中枢上产出 **0 信号**
（`signal.py:238-242`）。

**变更历史**：`0.0.9`（commit `271ff95`）。

#### D-20 第一类买卖点只做趋势背驰

**决策**：`_first_kind` 要求 `classify_trends` 给出下跌趋势覆盖的中枢组（≥ 2 个），
`leave = segs[p.end_idx]` 方向须 `== want == -1`，且要求 `leave.low < prev.low`
**且**面积 `a_now < a_prev`（`signal.py:394-399`）。

**依据**：第 24 课把"趋势背驰"与"盘整背驰"分开。

**已知空白**：**盘整背驰（一个中枢前后两段比较）没有实现**（`signal.py:6-7, 25-27`）。

**内联而非复用**：第一类买卖点处**内联**同一判据而非调用 `is_divergence`
（`signal.py:394-400`）。

#### D-21 第二类买卖点是近似

**决策**：以已有 B1 为锚，用 `src_start/src_end` 在 `segs` 定位下标 `k`，
取 `bounce = segs[k+1]`、`back = segs[k+2]`；要求 `bounce.direction == 1 and
back.direction == -1 and back.low > first.price`（`signal.py:456-462`）。

**代码自陈的近似**：原文要求"**次级别**回抽"，本实现用**本级别下一段**近似
（`signal.py:28-29`）。

#### D-22 禁止未来函数：逐层显式 `confirmed_at`

这是整个结构层最重要的设计约束。`state.py:3-14` 三条不变量：

1. **稳定 ID**：`object_id = "<类型名小写>@<src_start>-<src_end>"`，只用**原始 bar
   索引**，与 `idx` / `start_stroke_idx` 无关。缺字段直接报错。
2. **`confirmed_at` 写一次永不改**：唯一落点 `confirm_at`，已有值原样返回
   （`state.py:48-56`）；`_reconcile_field` 对已确认对象沿用 prev 的
   status / confirmed_at，**禁止回退**。
3. **失效留痕**：prev 有、新快照消失者转 `INVALIDATED` 保留，不静默删除。

逐层确认时间口径：

| 层 | `confirmed_at` 取什么 | 位置 |
|---|---|---|
| 笔 | **下一笔终点分型时间**（`seq[i+2].ts`），最后一笔 TENTATIVE/None | `stroke.py:54-59, 78-79` |
| 线段 | "确认它被破坏的那一笔"的 `confirmed_at`，再用 `_monotone_stamps` 取前缀最大值压实非递减 | `segment.py:435-443, 511-530` |
| 中枢 | **回试段**的确认时间 | `pivot.py:152-158, 225-232` |
| 走势 | `max(各中枢 confirmed_at)` | `trend.py:95-102` |
| 信号 | `_seal` 用触发段的 status/confirmed_at | `signal.py:162-166` |

**回测唯一入口**：`state.backtestable(items, as_of)` =
`CONFIRMED and confirmed_at is not None and confirmed_at <= as_of`
（`state.py:131-141`）。

**为什么笔的确认时间不能取 `end.ts`**：`stroke.py` 注释原话 ——
"直接取 `end.ts` 是**隐蔽的未来函数**"。

#### D-23 `step()` 是全量重算 + reconcile，不是局部重算

**决策**：`step(prev, new_bars)` = 全量重算 + `reconcile(prev, new)`，
`version = prev.version + 1`（`engine.py:154-172`）。

**理由**：线段划分用**全局 DP**（最大化确认段数），新增一根 bar 原理上就可能
改变整段划分（`engine.py:8-14`；`segment.py:337-411`）。

**不变量**：`step` 的结构字段（去掉 INVALIDATED 与 version）须与 `full(bars)`
逐字段相等。唯一例外 `confirmed_at` —— `step` 保留**首次观测到**的时间，
`full` 用当前重算值，后者只会往后挪，两者都是合法 PIT 取值（`engine.py:15-22`）。

**只许前进**：新帧终点早于 `prev.as_of` 直接 `ValueError`（`engine.py:149-153`）。

**回测不逐步调 `step()`**：一次 `full()` 后按 bar 遍历只读 `backtestable`
（`engine.py:23-26`）。

#### D-24 窗口裁剪不参与划分

**决策**：`Snapshot.clipped_to` 只决定**看得见多少**
（`engine.py:75-106`）。

- 笔 / 线段 / 中枢 / 合并 K 线："相交就留"
- 分型与信号（点）：须**严格**落在窗口内
- `idx` 保持全量编号

**理由**：反过来"先截断再算"等于让窗口参与划分。实测同一只票 `limit=300` 的
首段在 1212 根全量里**根本不存在**（`engine.py:88-93`）。

**变更历史**：`0.0.10`（commit `f9cedda`），页面必须让用户看到"窗口里 12 段"与
"这只票只有 12 段"的区别。

#### D-25 中枢扩展独立成函数

**决策**：`merge_pivots` 是独立函数，**调用方显式选择**是否调用
（`pivot.py:256-294, 45-46`）。

**理由**：扩展会**改变级别语义**。相邻、同 `level`、区间**真正重叠**
（`min(zg) > max(zd)`，相切不算）者合并：`zg=min`、`zd=max`、`gg=max`、`dd=min`、
`level=next_level(...)`。任一成分 TENTATIVE 则结果 TENTATIVE。

#### D-26 只吃 CONFIRMED 线段

**决策**：TENTATIVE 线段不参与中枢构造、也不参与延伸
（`pivot.py:24-25, 129-130, 138-140`）。

**下标口径**：`start_idx` / `end_idx` 相对**传给 `find_pivots` 的线段列表**，
不是过滤后的确认段子列表（`pivot.py:102-104, 119-121`）。
`signal.py` 必须传同一列表（`signal.py:196-197`）。

**不变量**：`dd <= zd < zg <= gg`、≥ 3 段、≤ 8 段、相邻中枢不回溯
（`pivot.py:302-333`）。

#### D-27 分型用严格不等式

**决策**：3 根合并 K 线、**严格**不等式（`fractal.py:59-66`）。

**理由**：严格不等式避免"平台等高"误判（`fractal.py:7-8`）。
首尾两根不可能是中心（`fractal.py:9`）。

#### D-28 线段标准化是"事后"判据，不是划分前置条件

**决策**：第 78 课的标准化（最高/最低点都在端点）作为 `_ordinal_ok` 的**次序判据**
在事后校验，而不是作为划分的前置条件（`segment.py:196-228`）。

**依据**：第 78 课原文：

> 如果线段中，最高或最低点不是线段的端点，那么……都可以把该线段标准化为最高低点
> 都在端点。经过标准化处理后，所有向上线段都是以最低点开始最高点结束，
> 向下线段都是以最高点开始最低点结束。

**历史函数 `_structural_ok` 曾把它提前为划分前置条件**，实测代价：
`sz.399001` 出现 **159 笔线段**。

#### D-29 线段划分唯一性不可自由设计

**决策**：划分规则外置为 `SegmentPolicy` 协议，默认 `Lesson6768Policy`。

**依据**（`segment.py:20-24`）：
- 第 67 课：「一切同一级别图上的走势都可以**唯一地**划分为线段的连接，
  这是基础的基础，请务必搞清楚」
- 第 77 课：「用线段划分的两种情况的规定，不难证明，线段的划分也是唯一的」
- 第 78 课：「根据这两种情况的完全分类来，**没有不能唯一去划分的**」
- 第 81 课：「有人提到 71 课里最后一个图，那个图显然是错的」

**原文空白项**（代码显式声明，不是发明）：`segment.py:15-18` —— 第 67 课只给出
特征序列的定义，第 71 课给的是分界点判定程序，第 78 课只说第二特征序列"必须严格
按照包含关系的处理来"，三处都**没有写到公式级的合并方向**。代码把它声明为
**显式实现约定**。

同样，`include.py:30-33` 指出第 65 课的方向判据要求"第 n 根与第 n-1 根**不是**
包含关系"，两两包含的首两根 K 线不满足这个前提，原文在此是**空白项**。

> **未独立核对**：以上两处"原文空白项"是代码注释的判断，未逐字复核原文。

#### D-30 被截断的左端起段：取"还能确认最多线段"的起点

**决策**：线段划分的起点不固定在**第 0 笔**，而是在所有可行起点中取
**能确认线段数最多**的那一个（`best(s)` 的 argmax）；平局取**更早**的起点。
起点之前的笔交给 `TENTATIVE` 前导段（`segment.py:337-411`）。
另加一条单调上界剪枝：`s` 之后的起点最多只能划 `(n - s) // min_strokes` 条，
该上界随 `s` 单调不增，所以当前最好成绩一旦达到这个上界就可以停。

**依据**：这是**原文空白项下的口径选择**。第 67 / 77 / 78 课都只宣示
「划分是唯一的」，**没有规定被截断的左端该怎么起段**。而数据文件的第一根 K 线
往往只是**数据商的截断点**，不是走势的起点：

- `600180` 日线从 2021-01-04 开始，可该股 1998 年就上市了；
- `sz.300760` 日线从 2020-01-02 开始，它 2018 年就上市了。

既然无法知道第 0 笔是不是真实线段起点，工程判据只能取"让划分尽可能完整"。
前导段本来就是为这件事存在的（`build_segments`）。

**被否决的替代方案**：

- *从第 0 笔硬起* —— 实测代价极大，且**正好是用户报告过的那类症状**：
  `600180` 日线全史 114 笔只划出 5 段、首段 **73 笔**；`sz.399001` 从 2020-01-01
  截断 → 首段 **81 笔**（全史只有 47）；`603777` 从 2020-01-01 截断 → 首段
  **87 笔**（全史只有 25）。即"同一个走势，换个取数窗口就塌成一条线段"。
- *取第一个可行起点* —— 曾被采纳过，理由是 argmax 会把开头整片笔塞进前导段
  （`sz.399006` 前导 102 笔、100 只样本合计 308 笔）。**那条证据是在 D-31 落地
  之前取的**：旧代码里标准特征序列残留包含关系，`candidates(0)` 会凭空为空，
  argmax 才有机会把起点推到很后面。D-31 之后前导段最长 13 笔、17 只样本合计
  31 笔，否决理由消失，故按用户裁决改回 argmax。
- *联网补全被截断的历史* —— 治标不治本（总会有新的上市/退市边界），
  且让划分结果依赖网络可用性。

**后果 / 变更历史**：本决策是**未提交**的工作区改动，见
[CHANGELOG.md](CHANGELOG.md) `[Unreleased]` 第 5 条。实测
（`optimizer/tools/measure_left_edge_scope.py`，17 个样本）：

| 样本 | 段数 旧→新 | 首段笔数 旧→新 |
|---|---|---|
| `600180` 日线全史 | 5 → 19 | 73 → 16 |
| `sz.399001` 从 2020-01-01 截断 | 8 → 16 | 81 → 47 |
| `603777` 从 2020-01-01 截断 | 5 → 17 | 87 → 25 |

判据要求"新口径消掉全部怪物段，**且**前导段没有反过来吞掉大段笔"，
脚本两条都过：最长段 > 80 笔的"怪物段"样本数 **2 → 0**，线段合计 1239 → 1316，
前导段藏笔合计 6 → 71。

**性能代价**（`sh.600759` 5 分钟线，1948 笔，清空 `webapi._CACHE` 后计时 `snapshot_of`，
同机三次取最快）：旧口径 **0.52 s** → argmax 无剪枝 **1.61 s** → argmax 带剪枝 **1.50 s**。
剪枝可证明安全（任一更晚的起点最多只能划 `(n-s)//min_strokes` 段，该上界随 `s` 单调不增，
故一旦最好成绩触到上界，后面的起点只能平局，而平局取更早者），但**只省下 0.11 s**：
本版比上一版慢约 **2.9 倍**，慢在 argmax 要对每个起点各做一次 DP，是口径本身的代价。

**2026-10-04 变更历史（本决策的左端口径被 D-40 推翻）**：上面的
「取**能确认线段数最多**的起点」**不再是主干行为**。D-40 改为
**取最早的前缀上第一个可行的起点**（因果），并删掉了这里的单调上界剪枝。

推翻它的不是新证据，而是**一条此前没被识别的性质**：argmax 是拿**全序列**比的，
数据一多最优起点就会往前跳 —— `sh.601012` 在 `k=250` 笔时起点是第 13 笔，
`k=330` 笔时变成第 1 笔。于是「前一次调用与后一次调用」根本不是同一件事，
第 71 课「线段的划分，都是可以当下完成的」所要求的**前缀确定性**被破坏。

上面第 2 条被否决方案（*取第一个可行起点*）当时被否掉的理由 ——
「argmax 会把开头整片笔塞进前导段」—— 现在**换了个方向**：真正把开头整片笔
塞进前导段的是「起点固定第 0 笔」那条路（`sh.000001` 最长段 37 → **376**）。
而「最早的可行起点」实测在 24 只票上是 `7787 / 52 / 14 / 38`，
argmax 是 `7855 / 64 / 20 / 44`（列＝累计确认 / 消失总 / 消失中间 / 末段）——
**违规 −30%、累计确认 −0.9%**。完整七臂表与逐条残留见
[CHANGELOG.md](CHANGELOG.md) `[Unreleased]` 与 `optimizer/theory/L71-SEGMENT-PRESENT-TENSE.md`。

**上面那张「性能代价」表也随之作废**：它的结论（「本版比上一版慢约 2.9 倍」）
是在**旧口径**下得出的，换了口径就不再适用，**不得再引用**。
按新口径在**同一入口**（`build_segments`，`sh.600519` 5 分钟线 6084 笔）重测：
**旧主干 18.622 s → 新实现 1.068 s**，且段数、确认数、最长段**逐项相同**。
原因是旧口径的 argmax 要为每个起点各做一次 DP，D-40 只做一次因果推进。
**注意口径差异**：原表测的是 `snapshot_of` 端到端、本表测的是 `build_segments`，
两者不可直接相减；D-40 本身**不主张性能收益**，这里只是撤回一条失效结论。

#### D-31 标准特征序列：合并之后必须回退

**决策**：`_merge_feature()` 在按顺序原则向前合并之后，要沿**已生成**的序列回退，
直到相邻元素之间不再有包含关系为止（`segment.py:58-128`）。结合法则抽为
`_combine()`，承载第 65 课的结合律。回退只合并**相邻**元素，**不借助传递律**。

**依据**：

- 第 65 课：「因此在K线包含关系的分析中，还要遵守顺序原则，就是先用第1、2根K线
  的包含关系确认新的K线，然后用新的K线去和第三根比，如果有包含关系，继续用
  包含关系的法则结合成新的K线，如果没有，就按正常K线去处理。」
- 同课：「**包含关系，不符合传递律**」—— 所以向前合并出的新元素可能反过来把
  **前一个**元素包含进去。
- 第 67 课：「经过非包含处理的特征序列，成为标准特征序列。」—— 残留包含关系的
  序列**不是**标准特征序列。
- 第 79 课第 24 段，**禅师自己**就是这么做的：「34、56、78，其中前两者可以进行
  包含关系处理，因此可以合并为 36（指区间），所以 78 显然和 12、36 构成底分型」
  —— 先包含，再判分型；同课第 26 段：「由于 9-10 是 78 的包含关系，所以可以认为
  线段二延伸到了 10」。
- 第 71 课给出理由：「但假设的转折点后的顶分型的元素，是可以应用包含关系的。……
  同一类的东西，当然可以考察包含关系。」
- 第 79 课评论区读者**今才觉艳**（2011-05-14）把这条读成通则：「要先把所有的特征
  序列先包含，然后再分段，而不是只处理最近的特征序列包含关系就可以」。
  **这是读者对第 79 课自身图 3 的读图笔记，不是禅师原话**，只作佐证。

**通用性验证**（2026-10-02，全市场逐只对照）：`HEAD` 干净工作树 vs 主干，
`data/day/` 下 5413 只全部跑完整 `ChanEngine.full()`。**78.4%（4243 只）段数变化**；
最长段 >200 笔的标的 **25 → 0**，>100 笔 **216 → 0**，>80 笔 **523 → 1**。
修复前病最重的 15 只里 **13 只是上海主板**（深证只占两个指数）——`sz.399001` 排第一
是因为它历史最长（8653 根），不是因为它特殊。分市场受影响比例接近（沪 79.5% / 深 77.5%）。
代价如实记录：457 只最长段变长（中位数 +5 笔、最大 +35 笔），但其中没有一只修复前
最长段 >50 笔；仅 3 只（`sh.600343` / `sh.601226` / `sh.603990`）属「修复前 ≤30 笔、
修复后 >50 笔」，留作开放项。**算法层结构上不认识标的身份**：`code` 只用于报错与快照
标签，全部判据函数的入参只有数字序列。详见审计报告 §A.8。
  > 本决策最初把它写成「第 79 课回复对第 81 课图例的读法」并当作禅师的话引用，
  > 2026-10-02 核对存档后订正。

**为什么必须回退**：`_is_fractal_at()` 按第 62 课判分型（"第二K线高点是相邻三K线
高点中最高的，而低点也是相邻三K线低点中最高的"），**被包含的那一对永远构不成
分型**，真极值被永久跳过，该起点就一个分界点也找不到。实测在 A 股各标的普遍出现
（`sz.300760` 等标的每次划分都有上百处）。

**被否决的替代方案**：

- *只向前合并*（旧实现）—— 就是上面的失效路径。
- *借传递律一次合并到底* —— 违反第 65 课"不符合传递律"，会把本不构成包含关系的
  元素也并掉。
- *不回退，改为放宽分型判定* —— 那是在动第 62 课的判据去掩盖第 67 课的实现缺陷，
  属于换判据而不是修 bug。

**变更历史**：本决策是**未提交**的工作区改动，见
[CHANGELOG.md](CHANGELOG.md) `[Unreleased]` 第 3 条。理论条目见
[optimizer/theory/L67-SEGMENT-CHAR-SEQ.md](optimizer/theory/L67-SEGMENT-CHAR-SEQ.md)。

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

> **变更历史（2026-10-02，Task 10a 补记，原文保留不改）**：
> ① **口径开关已落地到两个 HTTP 端点。** `/api/structure`（Task 7）与
> `/api/watchlist/structure`（Task 7b）都收 `mode` 查询参数
> （`Query("strict", pattern="^(strict|loose)$")`）：缺省 `"strict"` ⇒ 不传 `mode`
> 的既有调用路径逐字节不变；非法值被 pattern 挡成 **422**（不是 400、不是 500）。
> 响应顶层回显 `mode`，前端据此知道图上这套买卖点是哪个口径。注意 `mode`（买卖点
> 口径）与 `adjust`（复权口径）是**两个维度**，别混 —— 见 §4.1 的说明。
> ② **`mode` 传字符串会静默退化成严格模式（裁决 I），已修** —— 机制、修法、
> 行为变更范围见 D-35 变更历史 ⑥（同一天同一处改动，不在这里重抄）。
> 补一条可复算的计数（本任务实测，`tests/chan/fixtures/bars.parquet` 的 4 只票合计；
> 把入口归一化那行在内存里去掉即复现修复前）：
> 修复前 严格 **7** / 成员 `SignalMode.LOOSE` **36** / 字符串 `"loose"` **7**（= 严格，
> 逐字节相同）；修复后 字符串 **36** = 成员 **36**。
>
> ```bash
> cd <本项目根目录> && PYTHONPATH=src ../.venv-chanlun/bin/python - <<'PY'
> import sys, types; from functools import partial; from pathlib import Path
> import pandas as pd; import chanlun.chan.signal as real
> from chanlun.chan.engine import ChanEngine
> src = Path(real.__file__).read_text()
> pre = types.ModuleType("chanlun.chan.sig_pre"); pre.__package__ = "chanlun.chan"
> sys.modules[pre.__name__] = pre          # dataclasses 要能按 __module__ 找到它
> exec(compile(src.replace("    mode = SignalMode(mode)", "    pass"),
>              real.__file__, "exec"), pre.__dict__)
> all_ = pd.read_parquet("tests/chan/fixtures/bars.parquet")
> codes = ("sh.600000", "sh.601088", "sz.300059", "sz.300750")
> def n(mod, m):
>     t = 0
>     for c in codes:
>         b = all_[all_["code"] == c].drop(columns=["code"]).reset_index(drop=True)
>         t += len(ChanEngine(c, "day", signal_fn=partial(mod.find_signals, mode=m),
>                             level="day").full(b).signals)
>     return t
> for lab, mod in (("修复前", pre), ("修复后", real)):
>     print(lab, "严格", n(mod, mod.SignalMode.STRICT), "成员",
>           n(mod, mod.SignalMode.LOOSE), "字符串", n(mod, "loose"))
> PY
> ```
>
> 输出：`修复前 严格 7 成员 36 字符串 7` / `修复后 严格 7 成员 36 字符串 36`。

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

> **变更历史（2026-10-02，Task 3 修复轮 R1 补记，原文保留不改）**：
> ① **`Divergence.in_pivot` 的取值此前是错的。** 它按闭区间
> `start_idx <= i <= end_idx` 判定，把中枢的**离开段**（`segments[end_idx]`，
> `pivot.py:136-138`）也算进了「中枢震荡段」，于是**每一个趋势背驰都得到
> `in_pivot=True`** —— 而趋势背驰比的恒是离开段。这正好与第 049 课
> 「两者分别在第三类买点的前后……这是有严格区分的」相反，也与本条目上一段
> 「以 `Divergence.in_pivot` 保留第 049 课的区分」的说法相反。现改为
> `in_pivot = 该段落在某个中枢组的 [start_idx, end_idx) 内`：中枢震荡段 `True`、
> 离开段 `False`、趋势背驰恒 `False`。**判据本身（力度收缩 + 比较对象 + 创新极值）
> 一行未动**，变的只是这个派生字段的取值。口径由
> `tests/chan/test_divergence.py::test_in_pivot_is_the_049_oscillation_vs_leaving_leg_split`
> 与同文件末尾的合成用例钉住。
> ② **上一段被否决方案里的「窄口径 220 次 vs 全局 233 次」是旧探针口径下的数**，
> 不是本实现的口径。旧探针（`/tmp/exp/panzheng.py`）在面积收缩之外**还要求创新
> 极值**（`if new and shrink`），而本实现的盘整背驰判据只有力度收缩（第 039 课的
> 判据是「力度」；创新极值的要求属于趋势背驰，见第 037 课）。把该过滤条件加回去
> 重测：1.93 次/只（14 只 27 次），与旧探针的 1.991 次/只（117 只 233 次）同量级；
> 本实现口径实测约 5 次/只（59 只 317 次，本轮重跑复现）。**权威复测随 Task 9 的
> 测量工具落地**；今后引用 220/233 必须带上「额外要求创新极值」这个限定。
> 详见 `optimizer/theory/L39-CONSOLIDATION-DIVERGENCE.md` 的同名更正。

> **变更历史（2026-10-02，Task 10a 补记，原文保留不改）**：
> ① **`pb` / `ps` 的中文名从「（类第二类）」改为「（类第一类）」。** 依据第 027 课
> 「这时候就要用到这因为盘整背驰而形成的类第一类买点了」
> （`chanlun108/原文/027-盘整背驰与历史性底部.md`）与第 060 课「如果把55当成
> 第一类买点（严格来说，盘整背驰无所谓第一类买点，只是这样来类比）」：盘整背驰
> **点本身**是「类第一类」，「类第二类」是其后回抽不创新低 / 不创新高的那个点
> （`b2` / `s2`，第 027 课里「如果不出现新低，但可以构成类似第二类买点的买点」
> 那条路径）。判据、`SignalKind` 取值、信号数量都不变，变的只是显示名。
> 完整记录见 D-35 变更历史 ④。
> ② 本节正文的「严格 / 非严格」在 Task 5 之后才真正可用：字符串 `"loose"` 曾静默
> 等于严格模式，见 D-35 变更历史 ⑥；HTTP 端点见 D-32 变更历史 ①。

#### D-34 回测触发判据：从「结构确认时刻」改为「首次可见」

**决策**：`src/chanlun/backtest/strategy.py::ChanSignalStrategy.on_bar` 不再用
`sig.confirmed_at == bar.ts` 判断「今日新可知」。改由 `runner.py` 在逐 bar 重算时
计算**本 bar 第一次可见的信号**，显式传给 `_view(...)`。
**不改** `Segment.confirmed_at` 的语义，**不改** `segment.py::_make_segment`。

**依据**：`confirmed_at` 是**结构自身的确认完成时刻**（线段继承「确认它被破坏的那一笔」
的确认时间，`segment.py:427-459`），不是**引擎第一次产出它的时刻**。
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

> **更正（2026-10-02，Task 2 实施时补记，原文保留不改）**：
> ① **上一段对 `state.py::backtestable` 的归因是错的。**「什么时候才知道它成立」
> 这句原话**只存在于 `engine.py:24`**（当时模块 docstring 的第 4 点）；
> `state.py::backtestable` 的 docstring 里**从来没有**这句。它自己的措辞是
> 「回测/盘中扫描唯一允许读取的入口：只返回无未来函数的结构」，而这句话对**同一个
> 理由同样不成立**：`confirmed_at <= as_of` 的结构仍可能是本 bar 才第一次可见。
> 因此实际改动是：把 `state.py::backtestable` 的 docstring 改成只陈述它真正保证的
> 东西（按 `status is CONFIRMED` / `confirmed_at is not None` / `confirmed_at <= as_of`
> 过滤），并注明需要「首次可见」的调用方必须自己跟踪观测过程
> （见 `backtest/runner.py` 的 `seen`/`primed` 差分）。`engine.py` 原第 3 点
> （step ≡ full 不变量、`confirmed_at` 不倒退）本身没有不实陈述，予以保留
> （仅在开头补「快照是纯函数输出」一句）；被更正的是原第 4 点。
> ② **上一段的数字不复现。** 按同样的命令重跑，实际是 **20 次「首次可见」触发 →
> 11 笔成交 / 3 个回合 / 胜率 1/3 = 33.33%**（期末另有 5 笔持仓未平仓，故回合数
> 少于成交数的一半），不是「22 次触发 / 9 个回合 / 4/9」。旧判据 0 笔成交这一条
> 可复现。命令与逐条触发明细见
> `.superpowers/sdd/2026-10-02-strict-loose-mode/task-2-report.md`。
> ③ **② 的「数字不复现」是区间口径错配，特此更正（2026-10-02 修复轮补记）。**
> **22 次触发在设计文档自己的「全历史」区间上精确复现**：设计文档 §2.5 的表头写明
> 「8 只票，全历史」，去掉 `--start` 按同一 universe 重跑，正是 **22 次「首次可见」
> 触发**，与设计期探针的数字**逐条相同** —— 这是本次实施**最强的验证证据**：实现与
> 设计期探针一个数都不差。② 的 20 次是**同一判据**在 `--start 2018-01-01` 这个更窄
> 区间下的结果，差的 2 次全是 `sh.601398` 的 **2008-04-28** 与 **2009-10-27**
> （该票数据自 2006-10-27 起，被 2018 起点切掉），**不是判据差异**。
> **真正不复现的只有「回合 / 胜率」一列**（原记 9 个回合 / 4-9 = 44.4%）：
> 设计期探针 `/tmp/exp/fresh_trigger.py:49` 的
> `rounds.append((pos[0], float(df["close"].iloc[-1])))` 把**尚未平仓的持仓按最后一根
> 收盘价强制平仓、并计成一个回合**，胜负也按这个浮动盈亏判；真实回测只统计
> **已完成的买→卖回合**，故 9 变成 4（全历史）或 3（2018 起）。两个区间实测：
>
> | 区间 | 触发 | 成交 | 回合 | 胜率 |
> |---|---|---|---|---|
> | 全历史（2006-10-27 ~ 2026-09-30） | **22** | 13 笔（买 9 / 卖 4） | 4 | 25%（1 胜 3 负） |
> | `--start 2018-01-01`（2123 个交易日） | 20 | 11 笔（买 8 / 卖 3） | 3 | 33.33%（1 胜 2 负） |
>
> 复现命令（`chanlun/` 目录下；两条只差 `--start`）：
>
> ```bash
> # 全历史 ⇒ 22 次触发（与设计文档 §2.5 精确相同）
> PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun backtest \
>   --codes sh.600000,sh.601088,sz.300750,sz.000001,sh.600519,sz.002415,sh.601398,sz.300059 \
>   --period day
> # 2018 起 ⇒ 20 次触发
> PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun backtest \
>   --codes sh.600000,sh.601088,sz.300750,sz.000001,sh.600519,sz.002415,sh.601398,sz.300059 \
>   --start 2018-01-01 --period day
> ```

#### D-35 第三类买卖点的容忍度为工程口径，且仅在非严格模式生效

> **⚠️ 本条已作废（2026-10-04，`0.3.0`）**：`THIRD_TOL` 已**删除**，第三类买卖点的
> 判据不再随 `SignalMode` 分叉。作废理由、被否决的替代方案与可复现测量见
> **[D-41](#d-41-段数上限掐停的中枢pivotcapped在信号层必须整条跳过删除-third_tol)**。
> 下面记录的 `THIRD_TOL` 行号、`reason` 后缀格式与实测数字**描述的是引入时的状态**，
> 按「只增不改」纪律**保留原样**，不要据此去代码里找 `THIRD_TOL`。
> 唯一的例外是下面 ⑧ 那条「两个来源不同的数字」的告诫 —— 它仍然有效。

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

> **变更历史（2026-10-02，Task 5 修复轮 R1 补记，原文保留不改）**：
> ① **上一段写的判据与代码不一致。** 文档原文写
> `back.low >= p.zg - THIRD_TOL * (p.zg - p.zd)`（**含等号**），代码实际是
> `back.low > p.zg - tol`（`signal.py` 的 `_third_kind`；严格模式 `tol = 0.0`
> ⇒ `back.low > p.zg`）。**本轮只改文档、不改代码**：改成 `>=` 会动到已有信号，
> 而本轮的硬约束是「默认模式与 `mode=STRICT` 必须与 Task 5 提交（`74c579b`）
> 逐字节一致，**含 `reason`**」。等号归属记为**已知偏差** —— 字面上
> 「其低点不跌破ZG」含等号，现行代码比原文更严一点点，留待将来单独论证。
> ② **容忍度只可能在「被 `MAX_SEGMENTS` 截断」的中枢上翻结论。**
> `find_pivots` 的延伸循环会把任何仍与 `[zd, zg]` 重叠的后续段并进中枢组
> （`_overlaps` 是闭区间），只有 8 段上限先到，回试段才会「重叠却没被并进去」；
> 未被截断的中枢上，穿透型回试会被吸收成中枢延伸，`tol` 取多大都翻不了结论。
> 实测（`data/day` 全市场 5327 只里 `random.seed(7)` 抽 60 只）：
> 第三类买卖点严格 **63** / 非严格 **64**，计数不同的票 **1/60 ≈ 1.7%**
> （`sh.600850 b3 2023-08-25`，回试段进入中枢的深度是中枢高度的 0.0861
> < `THIRD_TOL = 0.1`，且该中枢 `segment_count == 8` 正好被上限截断）。
> 可复现脚本与输出见 `optimizer/theory/L20-THIRD-TOLERANCE.md` 判据三。
> ③ **`reason` 里的「（非严格：回试容忍 …）」后缀改为只在容忍度真正起作用时追加**
> （`used = bool(tol) and back.low <= p.zg`；卖点对称 `back.high >= p.zd`）。
> 此前无条件追加，于是回试段根本没进中枢、严格判据本来就通过的信号也宣称用了容忍度。
> 严格模式 `tol = 0.0` ⇒ 永不追加，`reason` 逐字节不变；受影响的只有非严格模式文案
> （fixture 上 `sh.600000 2022-01-13 s3` 的 `reason` 不再带后缀）。
> ④ **`SignalKind.PB` / `PS` 的 `name_cn` 从「（类第二类）」改为「（类第一类）」**，
> 依据第 027 课 `chanlun108/原文/027-盘整背驰与历史性底部.md:45`
> 「这时候就要用到这因为盘整背驰而形成的类第一类买点了」+ 第 060 课
> `060-图解分析示范五.md:45`（55 = 盘整背驰点 ⇒ 第一类）；「类第二类」属于
> 第 027 课 `:49` 那条路径产出的 `b2`/`s2`。判据、`SignalKind` 取值、信号数量都不变，
> 变的只是显示名。详见 `optimizer/theory/L39-CONSOLIDATION-DIVERGENCE.md`
> 与 `L27-LIKE-SECOND-POINT.md` 的同名更正。
> ⑤ 上述口径由 `tests/chan/test_signal_mode.py` 钉住：截断中枢上的 `tol` 边界用例
> （`test_third_tolerance_only_matters_on_truncated_pivot_buy` / `..._sell`，
> 把 `pivot.MAX_SEGMENTS` 从 8 改成 9 这两条就会红 ⇒ 「② 的截断依赖」确实被测量到）、
> 后缀只在生效时出现（`test_tolerance_suffix_only_when_it_decided`）、
> 同一 bar 多类型不合并（`test_same_bar_multiple_kinds_is_not_duplicated`）、
> 非严格只增不减（`test_loose_adds_but_never_removes`）。
> ⑥ **`mode` 入参归一化（裁决 I）。** `find_signals` 函数体第一行加
> `mode = SignalMode(mode)`：`SignalMode(str, Enum)` 的成员**等于**同名字符串
> 但**不是**同一对象（`SignalMode.LOOSE == "loose"` 真、`is "loose"` 假），
> 而内部三处用的是身份比较 `is SignalMode.LOOSE`。于是 CLI 的 `--mode loose`、
> HTTP 的 `?mode=loose`、前端拼的 URL 传进来的**字符串**会让非严格模式
> **静默退化成严格模式**（不报错、不警告）。归一化放在公开入口一次，
> 私有 `_third_kind`/`_second_kind` 仍只接收成员。**这是有意的行为变更**：
> `mode="loose"` 从「等于严格」变成「真的宽松」；默认与 `mode=STRICT`
> 仍与 Task 5 提交逐字节相同。回归测试
> `tests/chan/test_signal_mode.py::test_mode_string_is_equivalent_to_member`
> 等 4 条，其中 `test_loose_string_actually_loosens` 是反向断言（防「整体退化」
> 时等价测试照样通过）。
> ⑦ 行数变化：`src/chanlun/chan/signal.py` 在 Task 5 之前是 **295** 行
> （`62e1e20`）→ Task 5 提交后 **400** 行（`dc78eee`）→ 本轮 **416** 行。
> §3.5 课号引用总表里的 `signal.py:行号` 写的是 **Task 5 之前**的编号，
> 逐条实测对得上当时的代码（`:518` 的 `82-90` = 当时 `_area` 82-91；
> `:537` 的 `147-185` = 当时 `_third_kind` 147-187；`:554` 的 `211-241` =
> 当时 `_first_kind` 211-243；`:567` 的 `244-269` = 当时 `_second_kind` 244-271；
> `:341`/`:487` 的 `215` / `215-224` = 当时 `classify_trends(pivots, level)`
> 的调用点，该函数定义在 `trend.py`、`signal.py` 只调用），所以是被 Task 5
> 与本轮的行数增长推偏的，**未在本轮一并重排** —— 留作 Task 10 的文档整理项。
> 本轮只把 `optimizer/theory/L20-THIRD-TOLERANCE.md` 里的硬编码行号换成了
> 符号引用（裁决 G）。

> ⑧ **「`THIRD_TOL` 近乎 inert」有两个来源不同的数字，不许混写。** ② 的
> **63 / 64**（计数不同的票 **1/60**）是 `data/day` 全市场随机 60 只、直接看
> `b3` 的**比率**（脚本 + 跑法见 `optimizer/theory/L20-THIRD-TOLERANCE.md` 的判据三
> 那一节，脚本就写在那里；`grep -n "计数不同的票" optimizer/theory/L20-THIRD-TOLERANCE.md`
> 定位记录下来的输出行）。逐字复现的输出是：`sh.600850 b3 2023-08-25 越界比例=0.0861
> 中枢段数=8 中枢高度=2.637`，以及 `第三类：严格 63 / 非严格 64；计数不同的票 1/60`；
> 另一条是**单票例证、不是比率**：`sh.605300` 走完整 `find_signals`，`s3` 从严格
> **1** 个变非严格 **2** 个（严格 `2023-05-08`；非严格多出 `2026-09-23`）。
> 禁止写成「1/60（`sh.605300`）」这类混写 —— 样本、口径、信号类型三者都不同。
> 机制结论不变（同 ②：只在被 `MAX_SEGMENTS` 截断过的中枢上可能翻结论）。
>
> ```bash
> cd <本项目根目录> && PYTHONPATH=src ../.venv-chanlun/bin/python - <<'PY'
> from functools import partial
> import pandas as pd
> from chanlun.chan.engine import ChanEngine
> from chanlun.chan.signal import SignalMode, find_signals
> bars = pd.read_parquet("data/day/sh/605300.parquet").drop(columns=["code"], errors="ignore")
> for m in (SignalMode.STRICT, SignalMode.LOOSE):
>     snap = ChanEngine("sh.605300", "day", signal_fn=partial(find_signals, mode=m),
>                       level="day").full(bars)
>     print(m.value, [s.ts for s in snap.signals if s.kind.value == "s3"])
> PY
> ```
>
> 输出：`strict ['2023-05-08']` / `loose ['2023-05-08', '2026-09-23']`。
>
> ⑨ **「现在生效的措辞以哪一处为准」（Task 10a 实测结论）。** 上面**决策**段与 ①
> 的字面仍是 `back.low >= p.zg - THIRD_TOL * (p.zg - p.zd)`（**含等号**），代码是
> `back.low > p.zg - tol`；`optimizer/theory/L20-THIRD-TOLERANCE.md` 的
> 「被否决的替代方案」里写着「本轮只把文档从 `>=` 更正为代码实际的 `>`」，
> 但 `grep -n "back.low >=" ARCHITECTURE.md` 显示**本文件的决策段与 ① 变更历史
> 至今仍是 `>=`** —— 那次更正没落到这里。L20 现在的两处表述都与代码一致
> （判据三写 `back.low` 落在 `(zg - tol, zg]`；「被否决的替代方案」写
> 「现行代码是 `back.low > p.zg`」，定位命令见下）。⇒ **生效措辞以
> 代码 + L20 的「被否决的替代方案」为准：`>`**（比「不跌破」的字面更严一点点，
> 等号归属继续记为已知偏差，同 ①）。决策段按「D-xx 只增不改」保留原文。
> 这里**故意不写那两行的行号** —— 它们会随本文档增删而漂移，用命令定位。
>
> ```bash
> cd <本项目根目录> && grep -n "back.low >=" ARCHITECTURE.md && grep -n "tol = THIRD_TOL" src/chanlun/chan/signal.py && grep -n "back.low > p.zg\|(zg - tol, zg\]" optimizer/theory/L20-THIRD-TOLERANCE.md
> ```
>
> 输出：第一条命令命中的行里，决策段与 ① 变更历史各一行、其余是本节引用它们的
> 文字（引文自己也会被 `grep` 命中）；第二条是代码里那一行
> `tol = THIRD_TOL * (p.zg - p.zd) if mode is SignalMode.LOOSE else 0.0`；
> 第三条命中 L20 判据三的 `(zg - tol, zg]` 与「现行代码是 `back.low > p.zg`」两行。
>
> ⑩ **⑦ 记的那批偏号（§3.5 的 `signal.py:行号` 等）已在 Task 10a 逐条改指当前
> 代码。** 用新工具 `optimizer/tools/check_doc_line_refs.py` 全量复核：全文
> `182 处 / 173 行`，硬判据通过 **178 → 181**（改前 3 条路径歧义 + 1 条非仓库路径
> `/tmp/exp/fresh_trigger.py` 不判）、失败 **0** 条；符号漂移告警 **4** 条、
> 缺符号无法校验 **29** 条（其中 ±2 行内有符号的 J2 **14** 条）。
> ⑦ 的原句保留不改，此处记结果。命令见 §3.5 末尾的「行号由工具守门」。
>
> ⑪ **⑩ 的「已在 Task 10a 逐条改指当前代码」在 10a 当时并不成立。** ⑦ 记的 6 处里
> 10a 只重排了 2 处（`:554` 的 `211-241 → 300-305`、`:567` 的 `244-269 → 349-355`）；
> 另 4 处（`:518` 的 `82-90`、`:537` 的 `147-185`、`:341` 的 `215`、`:487` 的
> `215-224`）当时仍指 Task 5 之前的编号，落点已漂到别的函数上。这 4 处由
> Task 10a-R2 的独立对抗性复审发现，**按逻辑内容对齐**（不是按行数偏移加）重排：
>
> | 位置 | 旧行号 | 新行号 | 新行号处的代码 |
> |---|---|---|---|
> | §3.4 D-17（`:518`） | `82-90` | `125-133` | `def _area(...)` 全函数（`hist_area(..., seg.direction)`） |
> | §3.4 D-19（`:537`） | `147-185` | `205-257` | `def _third_kind(...)` 全函数 |
> | §3.1 管线（`:341`） | `215` | `287` | `_first_kind` 里的 `trends = classify_trends(pivots, level)` |
> | §3.4 D-16（`:487`） | `215-224` | `287-296` | 同一行 + 组内不足两个中枢就 `continue` |
>
> 同一轮还清掉**同一来源**（`signal.py` 因 Task 5 与本轮增长而漂移）的另外 9 处
> —— 它们不在 ⑦ 的清单里，是 Task 10a 的漏网：`:333` 的 `61-79 → 104-122`
> （`Signal` 类全块）、`:340` 的 `135-136 → 187-188`（`find_signals` 里的
> `macd(bars["close"])`）、`:375` 的 `72 → 115`（`Signal.status` 默认值）、
> `:561` 的 `228-234 → 300-306`（`_first_kind` 内联判据段）、`:648` 的
> `131-132 → 175-176`（`find_signals` docstring 的「同一个列表」）、`:1194` 的
> `9-11 → 11-13`（「离开」是位置）、§5 未实现表三行的 `20 → 22-24`、
> `23 → 27`、`21-22 → 25-26`（分别指文件头 docstring 的第 1/3/2 项）。
> 依据相同：按逻辑内容对齐，不看偏移量。
>
> ⑦ ⑨ ⑩ 一字未动（`AGENTS.md` 的只增不改）。**终态**：⑦ 记的 6 处行号**全部**
> 指向当前代码 —— ⑩ 的「逐条」在 10a 当时为未完成，在终态为真（⑩ 原句保留、
> 不作更正，其时间点由本条补正）。重排后重跑工具：全文 `182 处 / 173 行`、
> 硬判据通过 `181 / 182`、失败 `0`、符号漂移告警 `4`、缺符号无法校验 `29`
> （J2 `14`）、非仓库路径 `1` 全部不变；唯一变化的数字是「首末行空行」`21 → 17`
> —— 减少的 4 条正是 `:333`/`:340`/`:375` 与 §5 未实现表的盘整背驰一行，
> 它们**旧行号**落在空行上才被计入该桶，重排后自然消失。
>
> **R2 复核补正（Task 10a-R2 记，⑪ 正文保留不改）**：上面那句「首末行空行 `21 → 17`」
> 是 **10a 当时的实测值**。R2 按 §3.3(c) 逐条判定 §3.1 与 D-xx 时又清掉 3 处同源残留
> —— `:328`（segment.py 398 行 → 400 行）、`:373`（segment.py 198 行 → 200 行）、
> `:603`（engine.py 139-157 行 → 154-172 行）：三条的**旧行号**同样落在空行上，该桶再减 3，
> 终态为 **首末行空行 `21 → 14`**。另修 6 处不影响该桶的同源残留
> （`:316`/`:320`/`:332`/`:336`/`:338`/`:344`，均在 §3.1）。五个口径数字
> （全文 `182 处 / 173 行`、§3.4 `56 处 / 55 行`、硬判据 `181 / 182`、路径歧义 `0`、
> 非仓库路径 `1`）与退出码 `0` 在 R2 前后**一个都没变**。
>
> **R3 复核补正（Task 10a-R3 记，⑪ 正文与上面的 R2 补正段均保留不改）**：上面那段把
> 「首末行空行」桶第三个出桶行归给 `:603`，**归因有误**。`:603` 的旧引用是
> `engine.py` 第 139-157 行，而第 139 行是 `if not code:`、第 157 行是 `step()`
> docstring 里的散文行，**两行都不是空行**；在 R2 改前 / 改后 / 终态三个提交上，
> 该桶的成员集合里都**没有** `:603`。真正离开该桶的第三行是 `:336` —— 它的旧引用
> 是 `engine.py` 第 45-48、131、170 行，首个区间的末行（第 48 行）是 import 组
> 后面的空行，所以当时被计入该桶；重排成第 49-52、145、185-186 行后自然离开。
> 因此上面「另修 6 处不影响该桶」的名单里，`:336` 应当换成 `:603`。
> 桶的账 `21 → 17 → 14`（出桶的三条是 `:328`/`:373`/`:336`）与五个口径数字不变。
>
> 同轮还修掉一处**同类残留**：§4.4「时点保证」那条的 `runner.py` 引用原写
> `166-194`、并称 `:172` 有「★ 物理截断」标注；实测第 172 行是 `code, period,`，
> 第 166-194 行整段**不含**物理截断，真正的物理截断与「★ 物理截断」标注都在
> 第 217 行（bar 循环体第 211-217 行）。已改指 `runner.py` 第 211-217 行。
> 该桶因此由 14 再减 1 为 **13**；五个口径数字与退出码 `0` 仍然一个都没变。
>
> 本条同时记一处**已知空白**：两个工具都**不校验**裸 `:NNN`（不带文件名的续指）
> 与自指。⑪ 与两段补正里的裸 `:NNN` 记的是**当时值**（历史记录，本就不该改成
> 当前值），但 §4.4 那条里的 `:172` 是**指路**用的，会随代码漂移 ——
> 它正是这样漏过 10a 与 R2 两轮复核的。
>

#### D-36 分型（及其下游笔/线段/中枢）的确认时刻取「右侧合并K线完成时刻」

**决策**：`Fractal` 新增字段 `confirmed_at: str | None`（`chan/types.py`），
`find_fractals` 写 `merged[i + 1].ts`（`chan/fractal.py`，右侧那根合并K线的完成时间）；
一笔的 `confirmed_at` 取**锁定分型**（`seq[i + 2]`）的 `confirmed_at`（`chan/stroke.py`）。
`Fractal.ts` 语义**不变**，仍是「极值所在原始 bar 的事件时刻」。两者不是同一个东西，
不得互换。手工构造的分型没有合并K线序列可依，默认 `None`，由消费方显式退化处理。

**依据**：第 62 课《分型、笔与线段》：「像图1这种，**第二K线**高点是**相邻三K线**高点中
最高的，而低点也是相邻三K线低点中最高的，本ID给一个定义叫顶分型；图2这种叫底分型，
第二K线低点是相邻三K线低点中最低的，而高点也是相邻三K线高点中最低的。」
分型是三根相邻 K 线上的谓词：中间那根是不是极值，取决于**右边那根**的高低点 ——
右边那根走完之前，这个命题既不能判真也不能判假。所以可知时刻只能是右侧合并K线的
完成时间。

**被否决的替代方案**：
1. **直接把 `ts` 改成可知时刻** —— 否决。`ts` 是事件时刻，图上分型/笔的落点、与 bar
   的对齐都依赖它；改它等于用显示层的正确性换 point-in-time 的正确性。
2. **在各消费方（`state.py`、`strategy.py`）各自修正** —— 否决。根因只有一处，
   四处各修必然漂移，而且新消费方会继续踩同一个坑。
3. **不给 `confirmed_at` 默认值，逼所有构造点显式传入** —— 否决。13 个 `Fractal(...)`
   构造点里 12 个是测试夹具与合成用例，强制传值只会逼出一串 `confirmed_at=ts` 的
   假值，比 `None` 更难发现。

**后果 / 变更历史**：修前实测（`/tmp/probe_d1b.py`，4 只票、494 笔）在 `confirmed_at`
处只有 **0/494 强通过**（弱通过 249），改用 `merged[锁定分型.midx + 1].ts` 后 **494/494
弱+强通过**；领先量由「最小 1 / 均值 1.68 / 最大 9 根 bar」降为 **0**。负对照
（在最后一根 bar 处重算）修前修后都是 224/224。

代价与副作用：① `confirmed_at` 是 `Optional`，消费方必须处理 `None`；
② **D-34 的前提被证伪** —— D-34 记的「100% 早于首次可见」不是独立事实，而是本缺陷的
观测面，`chan/state.py` 与 `backtest/strategy.py` 的 docstring 已按此改写；
③ 回测**未**受污染（D-34 的首次可见差分本来就吸收了它）。**受影响面已核实**：
全仓库按 `confirmed_at` 做时间过滤的生产链路**只有 `backtest/runner.py:112-115`、`:232`**
（经 `state.backtestable`）；`scan/` 从不调用 `backtestable`，只把 `status` 当打分权重
并排除 `INVALIDATED`（`scan/scanner.py:392`、`:419-420`），所以扫描不受影响 ——
`backtest/__init__.py` 原先那句「回测与盘中扫描共享同一套约束」是**假的**，已就地订正。

#### D-37 GG/DD 排除的是「离开段 `group[-1]`」，不是「最后一个同向段」

**决策**：`find_pivots` 的 `GG/DD` 口径从「所有同向 Zn 去掉末位」改为「所有同向 Zn 去掉
**离开段** `group[-1]`，且只有 `group[-1]` 本身是 Zn 时才去掉」（`chan/pivot.py`）。

**依据**：第 20 课《缠中说禅走势中枢级别扩张及第三类买卖点》：「为方便起见，以后都把
这些与中枢方向一致的次级别走势类型称为Z走势段，按中枢中的时间顺序，分别记为Zn等，
而相应的高、低点分别记为gn、dn,定义四个指标,GG=max(gn),G=min(gn),D=max(dn),
DD=min(dn)，**n遍历中枢中所有Zn**。」原文只说「中枢中所有 Zn」，没说**离开段**算不算
——这是口径空白。主干取「不算」，依据是**中心定理二的可满足性**（第 20 课 `:55`）：
把离开段算进去，`[DD, GG]` 会被撑到下一个中枢门口，「趋势」几乎永远判不出来。
但「不算」的对象必须是**离开段**（`group[-1]`）。旧实现按**方向**排除「最后一个同向段」，
在 `group[-1]` 是**反向段**时（延伸停在一个回抽段上、回试段还没出现）会砍掉仍在枢内的
真 Zn，把 `[DD, GG]` **收窄** —— 那正是本决策修掉的缺陷。

**被否决的替代方案**：
1. **保持 `zn[:-1]`（按位置砍末位同向段）** —— 否决，就是上面那个收窄缺陷。
2. **改成「按方向排除最后一个同向段」** —— 同一件事的另一种写法，同样错。
3. **原文没写死，干脆算进去（口径 A）** —— 否决，实测 28 个相邻对里 27 个落进
   「形成高级别的走势中枢」、上涨/下跌 1/0，定理二里的「趋势」被静默关掉，
   连带第一类买卖点出不出现（`chan/signal.py`）。

**后果 / 变更历史**：实测（`optimizer/tools/measure_l20_dd_gg_scope.py`，24 只票日线、
52 个确认中枢、28 个相邻对）主干与采用口径 `F_adopted` **逐对相同（差异 0）**，
与旧 C 口径在 **7** 个中枢的 GG/DD 上不同；定理二分类（上涨 9 / 下跌 4 / 高级别中枢 15 /
无关系 0）在 C/D/E/F 四种口径下**完全一致** —— 也就是说**报告的「定理二影响」未能复现**，
`pair_flip_C_vs_E = 0`。该缺陷的真实影响面是 `GG`/`DD` 两个**数值**本身（会进
`trend.py`、背驰比较与页面图注），不是趋势分类的翻转。

#### D-38 段数上限掐停的中枢里没有离开段；`Pivot.capped` 记封口原因

**决策**：`Pivot` 新增字段 `capped: bool`（`chan/pivot.py`），语义是「延伸是被
`MAX_SEGMENTS` **真正**掐停的」。判定为
`capped = j - i >= MAX_SEGMENTS and j < n and _overlaps(confirmed[j], zd, zg)`。
`capped` 为真时 `GG/DD` **一段都不排除**（组里没有离开段）。

**依据**：第 33 课《走势的多义性》把延伸上限写成数字（「中枢的延伸不能超过5段，也就是
一旦出现6段的延伸，加上形成中枢本身那三段，就构成更大级别的中枢了」），但**没有规定
被上限截断时哪一段是离开段** —— 原文空白。而 `chan/signal.py`、`chan/divergence.py`
多处按位置约定「`segments[end_idx]` 是离开段」（`signal.py:234`、`divergence.py:193-195`）。
上限掐停时这个前提**不成立**：`end_idx` 那一段只是一段仍在枢内的延伸段。

判定必须带上 `_overlaps(confirmed[j], zd, zg)`：延伸循环把上限判在「有重叠」**之前**
（`while j < n and j - i < MAX_SEGMENTS and _overlaps(...)`），所以「`j - i` 等于上限」
本身不足以说明是上限掐停的 —— 若下一段本来就不碰区间，有没有上限都会停在同一组，
`group[-1]` 仍然是离开段。收紧后 `capped` 计数由 17 降到 **12**（24 只票日线 52 个
确认中枢，23%）。

**被否决的替代方案**：
1. **只加注释说明、不加字段** —— 否决。下游（`signal.py`/`divergence.py`）拿不到
   封口原因，只能继续按位置假定「`end_idx` 是离开段」。
2. **改 `signal.py`/`divergence.py` 去按 `capped` 分支** —— 本轮**未做**：实测
   capped 中枢上的第三类买卖点在 STRICT 与 LOOSE 下都是 **0**（12 只票、5 个 capped
   中枢；正常封口中枢 21/21），没有可复现的行为差异可用来验证改动，盲改无法证明
   改对了。留作已知空白。
3. **保留 `zn[:-1]` 的排除并如实标注 `capped`** —— 否决，见下。

**后果 / 变更历史**：**这一支在方向交替的输入上是 no-op，不得记成「验证通过」。**
实测：cap 掐停时中枢组长恒为 8（偶数）⇒ `group[-1]` 恒为反向段 ⇒ 它本来就不在 `Zn` 里
⇒ 排不排除它都一样；24 只票里 E 口径与 F 口径数值不同的中枢 **0/52**，
`pair_flip_E_vs_F = 0`。它只在**方向不交替**的输入上生效，而「相邻确认段方向交替」
只写在 `pivot.py` 的注释里、**没有任何校验器强制**（实测确认线段 395、相邻对 371、
同向相邻对 0；组长为奇数的确认中枢另有 26 个，但都不是 cap 封口）。保留这个分支的
价值是：让 cap 路径不再依赖一条未被强制的引理，并且把封口原因如实传给下游。
反例见 `tests/chan/test_pivot.py::test_capped_pivot_does_not_exclude_its_last_segment`
（构造同向段相邻的合法输入；把 `inside` 退回旧口径时该用例以
`AssertionError: assert 20 == 25` 失败，即守卫确实能失败）。

`capped` 当前**没有生产消费方**（只有 `Pivot`、`merge_pivots` 与测量工具），
所以本次改动对信号数量的影响为零 —— 这是如实记录，不是「无影响所以不重要」。

**2026-10-04 追加（`validate_pivots` 补上 `capped` 不变量）**：`capped` 决定 `inside`
是否剔除 `group[-1]`，因而决定 `GG/DD`，但在此之前**没有任何校验器看过这个标志**，
标错了会静默改变中枢振幅。本轮在 `validate_pivots` 里加了一条：
`capped=True` 必须正好 `MAX_SEGMENTS` 段（`capped` 只在「到了上限、且下一段仍在枢内」
时为真，此时组内段数恰好等于上限）。

**这是加固，不是修 bug** —— 先跑全量语料再下结论：24 只票审计语料实测
`pivots=72 / capped=12 / capped 段数不符 0 / validate_pivots 报错 0`，
该不变量**事实上已经处处成立**，所以本条目**不得写成「修好了某个 bug」**。
护栏可失败性已用扰动法验证：把条件改成 `if False and p.capped and ...` ⇒
`tests/chan/test_pivot.py::test_validate_pivots_catches_each_invariant[kw7]` 变红
（`assert [] != []`）；恢复后该文件 `42 passed`（原 41）。

#### D-39 线段划分仍是全局 DP，未按第 71 课「当下程序」改写（已知偏差，未修）

**决策**：本轮**保留** `chan/segment.py::Lesson6768Policy.classify` 的全局动态规划，
把「它不是原文要求的当下程序」记为**已知空白**，不在本轮改。

**依据**：第 71 课《线段划分标准的再分辨》：「其实，线段的划分，**都是可以当下完成的**，
无非是如下的程序：假设某转折点是两线段的分界点，然后对此用线段划分的两种情况去考察
是否满足，如果满足其中一种，那么这点就是真正的线段的分界点；如果不满足，那就不是，
原来的线段依然延续，就这么简单。」原文要求的是一个**逐转折点的假设-验证程序**：
已宣布的分界点不允许在更多 bar 到来后消失。主干的 `classify` 做的是两件别的事：
① 左端用 `argmax over s of best(s)[0]` 选「还能确认最多线段」的起点（原文空白项下的
工程口径，理由见 `segment.py` 的注释：数据文件首根 K 线常是数据商的截断点）；
② 前向推进时取 `best(start)[1]`，即**使未来线段总数最大**的那个分界 —— 这是上帝视角。

**实测（属实，但比本条目初版写的轻得多；指标已订正）**：5 只票、
`2024-01-01`→`2026-09-30` 日线，每 40 根 bar 切一次前缀重算。复现命令：

```bash
cd <本项目根目录>
PYTHONPATH=src ../.venv-chanlun/bin/python \
    optimizer/tools/measure_segment_present_tense.py --sample 5
```

累计确认过 **1789** 个线段分界点，其中 **12** 个在相邻两个前缀之间消失。
但「消失」必须分类：**末段消失 9 次属正常**（它的确认依赖其后仍在变动的笔，与笔层
末笔同构；笔层同样口径下 70 次消失**全部**是末笔、中间笔 0 次），
**中间段消失 3 次才是第 71 课性质 1 的违反** —— 即 **3 / 1789 = 0.17%**。
具体案例 `600036`：已确认分界点在 stroke 23/24，k=610 起被改写到 19/20 并永久保持。

**根因（M3）**：`candidates(strokes, s)` 的断点集合随新笔单调增长，且新增断点可能比
已有的**更早**（`600036` s=3：`[9,23]` → `[9,19,23]`）。断点只能被后来识别**本身是
缠论「当下」程序的特性**（第 67/78 课两种情况需要足够多的后续笔）；问题在**谁来选**：
DP 目标是「使未来线段总数最大」，于是新出现的更早候选 19 会赢过已宣布的 23。

**本条目初版的两处论断已撤回**：
① 初版写「**25** 个不在最终划分里」并把它当违规指标 —— 订正。「不在最终划分里」量的
是**与最终划分的一致性**，一个完全单调、但最终划分不同的划分会被它冤枉；正确指标是
**相邻前缀之间的消失**，且只算中间段（见上）。
② 初版写「逐根 bar 重算，`confirmed_at` **早于**首次可见，**14/14** 与 **24/24**、
最大提前 **193** 根 bar」，据此断言线段层存在「与 D-36 同类的 point-in-time 前视」——
**该测量不可复现，结论撤回，线段层没有前视**。产生它的脚本不在仓库里也不在 `/tmp`，
其定义无从恢复。用仓库内工具逐根 bar 重算整段历史（`--per-bar sh.600000,sh.600030`）
得到的是相反结论：`sh.600000` 1393 bar / 确认段 15 / 早于 **0** / 晚于 13 / 最大提前 0；
`sh.600030` 1636 bar / 确认段 24 / 早于 **1** / 晚于 21 / 最大提前 3；
负对照（最后一根 bar 处重算）缺失 **0/0**。`confirmed_at` 偏**保守**（宁晚不早），
原文要求的性质 2（结论当下可得）在线段层是**满足**的。

**被否决的替代方案**（**本轮已全部实测，不要再提议**）：
1. **把 `best(start)[1]` 换成「第一个通过验证的候选」**（= 第 71 课字面程序）—— 否决。
   实测**不降低违规数，反而更差**：真违规 3 → **4**，且**重新引入长段塌缩**
   （`sh.000001` 从 80 段/最长 37 笔变成 23 段/最长 **376** 笔）。
   这条是初版列的第一备选，当时的理由是「只改一半比不改更糟」——现在有了数字，
   结论相同但理由更硬：**改这一半本身就有害**。
2. **冻结左端（只用最前面 120 笔算一次 argmax）** —— 否决。实测与主干**逐项完全相同**
   （结构 80段/79确/37长、违规 3/12 全等），说明**左端本来就是稳定的、不是漂移源**，
   这个改动是纯 no-op。
3. **起点固定第 0 笔 + 首个满足目标**（唯一完全当下的组合）—— 否决。违规最少（1），
   但那是因为它把首段变成一个 **376 笔**的巨型前导段、确认段从 1789 掉到 1577：
   **机会变少，不是真改善**，且结构塌缩不可接受。
4. **只把 `confirmed_at` 改成「首次可见时刻」以消掉前视、不动划分** —— 否决，
   而且**前提已不存在**：实测线段层没有前视（见上），没有要消的东西。
5. **不记录** —— 否决。这是可复现的实测偏差。

**为什么无状态规则做不到 0**：四个变体的真违规数是 3 / 3 / 4 / 1，**没有一个到 0**。
真正的单调性要求**跨次调用锁定已宣布的分界点**（在已定分界点之后再跑 DP），
即 `ChanEngine` 必须承载已确认分界点列表、`classify` 必须接受锁定的前缀 —— 那是
有状态的重设计，会改动 `full()` / `step()` 的契约并重新锚定全系统所有冻结数字。

**后果 / 变更历史**：理论依据已写入 `optimizer/theory/L71-SEGMENT-PRESENT-TENSE.md`
（逐字引原文 + 订正说明）。测量尺子固化在
`optimizer/tools/measure_segment_present_tense.py`（M1 单调性 / M2 结构 / M3 根因 /
M4 逐根前视），数字可从该命令复现。**本轮未修，故不得在 CHANGELOG 里写「已修复」**
—— 只记「新增已知空白」。

> **2026-10-03 第二轮变更历史（本条目初版的两处论断被推翻）**：初版把「25 个不在最终
> 划分里」当违规指标、并声称线段层有「最大提前 193 根 bar」的 point-in-time 前视。
> 两者都不成立 —— 前者口径错（量的是与最终划分的一致性），后者不可复现（实测早于
> 0/1、晚于 13/21，方向相反）。真违规是 **3 次中间段改写（0.17%）**，根因是
> `candidates` 单调增长 + DP 全局重优化，**修法需要前缀锁定（有状态）**，
> 无状态四变体已实测全部无效。主干**判据**本轮未改动（只在 `src/chanlun/chan/segment.py`
> 末尾追加了一段实测备注，不改任何行为，也不移动被引用的行号）。

> **2026-10-04 第三轮变更历史（第二轮的两处论断被推翻，主干判据本轮真的改了）**：
> 第二轮的「3 次中间段改写（0.17%）」与「无状态四变体已实测全部无效、不要翻开关」
> 都作废。前者是**小样本 + 错口径**的产物：第二轮的尺子把「中间段消失」当违规，
> 但分母只有 5 只票、`--stride` 也粗；换成 24 只票、`--stride 40` 的同一把尺子后，
> 主干实测是 **64 次消失 / 20 次中间段消失**（累计确认 7855），不是 3 次。
> 「四变体无效」随之失效 —— 本轮把根因拆成**两个各自独立**的机制并都修掉了：
> (a) **缺口确认前视**：旧实现用「当前前缀之后」的分型去确认当前前缀里的分界点；
> (b) **左端起段漂移**：旧实现按全序列 argmax 选起点，数据一多起点就往前跳
> （实测 `601012`：k=250 时起点是第 13 笔，k=330 时变成第 1 笔）。
> 两处都改成只看前缀之后，实测 **64 → 52 次消失、20 → 14 次中间段消失**
> （累计确认 7855 → 7787，−0.9%）。第三个机制（宣布→取消→再宣布的序号复用）
> 只被部分缓解，**残留 14 条已逐条列出**。真正的 0 违规仍需前缀锁定（有状态），
> 本轮仍然没做 —— 所以本条目**依然不能**在 CHANGELOG 里写「已修复」，
> 只能写「改为前缀因果，残留 14 条」。详见下面新增的 D-40。

#### D-40 线段划分改为前缀因果：缺口确认只看已出现的笔，左端起段取最早的可行起点

**决策**：`segment.py` 的线段划分去掉两处「向后借用数据」的写法，改为**只由当前前缀
（含当前笔）决定**：

1. **缺口确认**（第 78 课第二种情况的确认元素）：`_confirm_gap` 改为按笔逐根推进，
   只用**已经走完的笔**判定分型，不再用「前缀之后的元素」去确认前缀内的候选。
2. **窗口左端**：不再对全序列做 argmax，改为**取最早的前缀上第一个可行的起点**。

行号一律见 §3.5 课号引用总表。本条目刻意不写 `文件:行号` 形式的引用 ——
往 §3.4 里新增一条 `xxx.py:NNN` 会改变量具
`optimizer/tools/check_doc_line_refs.py` 的冻结计数（全文 186 处 / 176 行，
§3.4 60 处 / 58 行），而那个计数有测试钉着。

**依据**：

- 第 71 课《线段划分标准的再分辨》：「其实，线段的划分，都是可以当下完成的，无非是
  如下的程序：假设某转折点是两线段的分界点，然后对此用线段划分的两种情况去考察是否
  满足，如果满足其中一种，那么这点就是真正的线段的分界点；如果不满足，那就不是，
  原来的线段依然延续，就这么简单。」——「当下完成」是判据**可计算性**的硬约束：
  某个分界点成不成立，只能由它**之前（含它）已经出现的笔**决定。
- 第 78 课《继续说线段的划分》：第二种情况里「笔可以远在后面」才确认。所以
  「确认元素在后面」**本身不违规** —— 违规的是把**前缀之后**的元素当成前缀内的证据。
  两条合起来正好是 `_confirm_gap` 的形状：向未来**等待**可以，向未来**借用**不可以。
- 第 79 课《继续说线段的划分》讲第二种情况的确认时给出逐字实例：「…34、56、78，
  **其中前两者可以进行包含关系处理，因此可以合并为36（指区间）**…」——包含处理
  先于分型。这是 `_merge_push` 逐根推进（先合并、再看分型）的实现依据。

**被否决的替代方案**（数字都是 24 只票日线、`--stride 40`、与主干同一把尺子；
列＝累计确认 / 消失总 / 消失中间 / 末段）：

1. **不修，只记档（L0 旧主干：argmax + 前视缺口）**：7855 / 64 / 20 / 44。
2. **只冻结左端（F1）**：7855 / 64 / 20 / 44 —— 与 L0 **逐项相同**，如实记为
   `inconclusive`：这把尺子在 stride 40 上看不见左端那一处（要 `D3+` 单因子臂才看得见）。
   仍然修它，是因为它消除的是**结构性**非因果（数据一多起点就跳），不是指标噪声。
3. **严格照第 71 课字面程序（F2：冻结左端 + 每个转折点取「第一个满足」的候选）**：
   7329 / 54 / **28** / 26 —— 中间段消失反而从 20 涨到 28。字面程序是「假设-验证」的
   **逐点**判定，主干是「使线段总数最大」的 DP；把 DP 换成字面程序会让划分对噪声更敏感。
   （这不等于第 71 课错了：原文要求的是**可计算性**，不是「逐点贪心」。）
4. **起点固定为 0（L1）**：5605 / 19 / 5 / 14 —— 违规数字好看，但累计确认掉了 29%，
   而且**结构塌陷**：`sh.000001`（634 笔）80 段 / 79 确 / 最长 37 → 4 段 / 3 确 / **最长 73**；
   `603777`（193 笔）最长 25 → **95**。**用一个错误换指标改善不算修复。**
5. **只修缺口（D3-）**：7866 / 57 / 17 / 40；**只修左端（D3+）**：7776 / 59 / 17 / 42 ——
   两个单因子臂各消掉 3 个中间段消失，**两条都修才拿到 14**。
6. **全序列第一个可行起点**（左端的非因果版本）：7582 / 58 / 15 / 43 —— 比因果版
   （7787 / 52 / 14）更差。这正是「第一个可行起点」必须是**因果**的理由。
7. **平局时取 `confirm_stroke_idx` 更小者（conf 破平局）**：7787 / 46 / **11** / 35 ——
   比已落地的 14 更好，累计确认也不掉。**未采纳**，理由有两条：(i) 已落地的两处都基于
   「结论当下可得」这个**原文性质**，而它只是**指标驱动的平局偏好**，原文没有任何依据；
   (ii) 它把划分与确认机制**耦合**起来 —— 平局判据读 `confirm_stroke_idx`，将来任何
   缺口语义改动都会静默改变划分。结构对照：7 只票里 6 只逐项相同，`601088` 最长段
   25 → 27。残留仍是 11 ≠ 0，真正的解法是有状态锁定。（此臂实测来自**临时探针，
   命令未随仓库发货**；若要采纳，必须先把该臂提升进
   `optimizer/tools/measure_segment_present_tense.py`。）
8. **「已宣布即永久」（P4）**：违规 0，但 `pytest` **36 failed / 917 passed** ——
   用破坏合法性约束换来的满分不算修复。
9. **廉价代理规则**（minconf 6807 / 12 / 2 / 10；maxconf 2668 / 323 / 182 / 141；
   `closed_only` 中间段消失 20 → **40**；`closed_candidates` 20 → **6**；
   O3 重宣布重排序 2 → **4**）：全部来自临时探针（命令未入库）。minconf 数字好看但
   累计确认掉 13%，maxconf 直接崩掉；`closed_*` 两条都是**回归**。
10. **把 `avail`（前缀过滤）加回 `candidates`**：逐前缀对照 **20,231 / 58,990 处不一致**
    （细粒度 659,688 / 674,905），即那个公式 97.7% 是错的。基于它的「精确 O(n²)
    重写」前提不成立，整条路线放弃。

**后果 / 变更历史**：

- **代价**：累计确认 7855 → 7787（**−0.9%**），即约 0.9% 的历史分界点不再被承认。
  这是**判据变更**，按 0.x 约定记为 `MINOR`。
- **残留违规 14 条**（M6，逐条）：

  ```
  600000  k=610  消失的中间段 segment@46-384
  600036  k=610  消失的中间段 segment@338-383
  600036  k=610  消失的中间段 segment@49-338
  601088  k=770  消失的中间段 segment@397-587
  000001  k=1010 消失的中间段 segment@792-817
  000333  k=1010 消失的中间段 segment@631-801
  000333  k=1010 消失的中间段 segment@801-826
  000725  k=1450 消失的中间段 segment@986-1048
  002415  k=1450 消失的中间段 segment@1036-1151
  002714  k=1450 消失的中间段 segment@994-1151
  002714  k=1530 消失的中间段 segment@1059-1144
  002714  k=1530 消失的中间段 segment@994-1059
  300760  k=650  消失的中间段 segment@445-527
  300760  k=650  消失的中间段 segment@527-552
  ```

  这些是第 71 课性质 1（单调性）的**残留违反**，需要跨次调用锁定已宣布的分界点
  （有状态）才能清掉。
- **残留 14 条按「已知空白」收口，清零目标已撤销**（2026-10-04 用户决定）：
  把「消失的中间段 = 0」当成目标**不再追求**。撤销的理由不是「做不到」，
  而是**代价与收益方向相反** —— 唯一的清零手段是**有状态锁定**（把上一次调用
  已宣布的分界点喂给下一次），它带来三个与原文明抵触的后果：

  (i) 划分结果**依赖调用顺序**。第 67 课要求「一切同一级别图上的走势都可以
  **唯一地**划分为线段的连接」，一个「取决于你从哪根 bar 开始喂」的划分不是
  唯一划分。
  (ii) 同一个 `ChanEngine` 实例之外**不可复现**。看盘页的快照缓存以
  `(code, period, effective, last_ts, fingerprint, mode)` 为键 —— 同一份数据换个
  模式或换个 `last_ts` 就是一次全新调用，锁定状态无处安放，等于给「同一输入」
  两个答案。
  (iii) 线段划分会从**纯函数**变成带隐藏状态的组件，测试、复现、`--stride`
  这类前缀测量全部要跟着改口径。

  代价侧只有 14 / 7787 ≈ **0.18%** 的历史分界点，且它们是**前缀边界效应**
  （下条已定位为「已宣布的中间段不再出现在最终划分里」，不是判据算错）。
  记录为**已知空白**，不是「待修的 bug」。
- **但「锁定」在笔层实测是可行的 —— 这是主动撤销，不是无能为力**：
  2026-10-04 用 `optimizer/tools/measure_stroke_prefix_stability.py` 实测
  笔的前缀稳定性（6 只日线票，`--stride 40 --warmup 120`）：

  ```
  票代码         bars  前缀对  失配对  非末位失配对  最差失配距离
  sh.600000      1393     31      14             0             1
  sh.600030      1636     37      13             0             1
  sh.600519      1636     37      12             0             1
  sz.000001      1393     31      11             0             1
  sz.300760      1636     37      13             0             1
  sh.601012      1636     37      15             0             1
  合计：前缀对 210 / 失配对 78 / 非末位失配对 0 / 最大失配距离 1
  ```

  **非末位失配对恒为 0** ⇒ 失配只落在较短序列的**最后一个元素**上，即
  「已定的笔不被后来的数据改写，只有最新那根笔会随数据增长而变形」。
  所以按 `(src_start, src_end, direction, high, low)` 锁定上一轮的笔是稳定的；
  线段层同理可用 `stroke_idx` 锁定。本轮**主动不采纳**，理由见上条。
  这条判据是**能失败的**：该列一旦 > 0，「笔下标可锁定」的结论即作废。
- **前视没有彻底消除**：M4 逐根 bar 实测，`sh.600000` 早于 0 处 / 最大提前 0 bar，
  但 `sh.600030` 仍有 **1 处 / 最大提前 3 bar**（末根缺失 0）。段时钟
  （`confirmed_at`）的提前量与划分的因果性是两件事，**不要**写成「已彻底消除前视」。
- **M3 断点演化（24 只票）**：断点集合**只增不减**，消失次数全 0 —— 所以上面 14 条
  不是「断点被删」，而是「已宣布的中间段不再出现在最终划分里」，性质不同。
- **不变式**：相邻确认段方向必然交替（确认线段 395 / 相邻对 371 / **同向相邻对 0**）。
  该引理目前**只在注释里断言，没有校验器强制**，是一条已知空白。
- **本条目不主张性能收益**：同一把尺子下 7 个臂的耗时同在 4 秒量级
  （L0 3.8s / 已落地 3.9s），性能既不是本轮目标、也不是采纳理由。

#### D-41 段数上限掐停的中枢（`Pivot.capped`）在信号层必须整条跳过；删除 `THIRD_TOL`

**决策**：`capped = True` 的中枢（第 33 课段数上限真的掐停，见 D-38）在信号层
**不产出任何「离开-依赖」的信号**，三处都加门。

> 这三条**故意写成带空行的独立列表，不写成表格**：表格行必然相邻，
> 而 `optimizer/tools/check_doc_line_refs.py` 的符号漂移扫描按「文档行 ±1 行」
> 取窗口，会把隔壁那行的函数名与本行的行号区间**交叉配对**，凭空报出两条
> `±1` 假告警（实测过：`L1578 _entering_and_leaving -> def@286 范围[(217,217)]`
> 与 `L1579 _third_kind -> def@217 范围[(286,286)]`，两行正好互换）。
> 加空行后窗口取不到隔壁的符号，假告警消失。工具本身没改。

- `src/chanlun/chan/signal.py:226` —— `_third_kind`（门在函数内 `:265`）：
  `if p.capped: continue`，挡第三类买卖点（第 20 课位置口径）。

- `src/chanlun/chan/signal.py:297` —— `_entering_and_leaving`（门在 `:311`）：
  `if p.capped: continue`，挡第一类买卖点 / 趋势背驰的「离开段 vs 前一同向段」比较。

- `src/chanlun/chan/divergence.py:212` —— `_leaving_legs`（门在 `:233`）：
  `if p.capped: continue`，挡趋势背驰条目。

同时**删除** `THIRD_TOL = 0.1`（原常量定义处，D-35 的产物）：
第三类买卖点的判据**不再随模式分叉**，严格与非严格在第三类上逐项相同。
`_third_kind` 因此也不再收 `mode` 参数。

**依据**：

1. **第 33 课《走势的多义性》**：「中枢的延伸不能超过5段，也就是一旦出现6段的延伸，
   加上形成中枢本身那三段，就构成更大级别的中枢了。」
   本级别最多 `3 + 5 = 8` 段，到顶就停 —— `capped` 记的正是「到顶就停」。
2. **第 20 课中心定理一**：「走势中枢的延伸等价于任意区间[dn，gn]与[ZD，ZG]有重叠。」
   `capped` 的中枢组里**每一段（含 `end_idx`）都仍与 `[ZD, ZG]` 重叠**，
   本级别**不存在**离开段。拿延伸段当离开段去比 MACD 力度，比的是错的对象。
   这不是「判据更严」，是**对象认错了**。
3. **第 24 课**：比较力度的对象是「离开中枢的那一段」；没有离开段就没有比较对象。
4. **`THIRD_TOL` 的删除依据**：它的原文依据本来就薄（D-35 自己写明
   「工程口径，无原文依据」）。第 20 课第三类买点的原文是**位置**判据：
   「一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别走势类型回试，
   其低点不跌破ZG，则构成第三类买点」—— 没有「容忍中枢高度 10%」这一说。
   而 `capped` 门落地后，容忍度**唯一还能起作用的地方**（`capped` 中枢上）
   已被结构性排除，留着一个不生效、又无原文依据的常量只会误导。
5. **实测**：见 `docs/evidence/2026-10-04-capped-signal-gate.md`。
   全市场日线 5440 票：确认中枢 **8186** 个，其中 `capped` **2759** 个（**33.7%**）。
   修复后 `capped` 中枢上的离开-依赖信号 **0** 条；去掉两处有产出的门重跑 ⇒
   `capped` 列变成 `b1 46 / s1 18 / 趋势背驰 64`，判据一 = **192**，退出码 **1**。
   即：全部趋势背驰 **180 → 116**、第一类买点 **137 → 91**、第一类卖点 **43 → 25**，
   差值 46 / 18 / 64 全部落在 `capped` 中枢上。

**被否决的替代方案**：

1. **只挡「未确认」而不挡 `capped`**（`if p.capped or p.status is not Status.CONFIRMED`）
   —— **已否决**。这是把 D-26（`ARCHITECTURE.md:1551`，「只吃 CONFIRMED 线段」）
   误读成「只吃 CONFIRMED 中枢」。D-26 约束的是**线段**，不是中枢；一个
   `TENTATIVE` 中枢是**每一只活着的股票**的常态右边缘，挡掉它等于让右边缘永远
   没有买卖点。2026-10-04 用户裁定「保持现状：不 gate TENTATIVE」。
   > 该方案当时实测能多挡下 `strict b3 243 / s3 118`、`loose b3 271 / s3 145`
   > 条信号。这些数字是在**已删除的探针脚本**（未入库）上测得的，无法从当前主干
   > 复现，故只作为「被否决方案的规模量级」留档，不作为可复现证据。
2. **把 `capped` 中枢交给「级别递归」而不是跳过** —— **本轮不做**。
   理论上更贴原文（段数到顶 = 升级成更大级别的中枢），但本级别没有级别递归，
   实现它属于新功能而非修复。已记入 §5 已知空白。
3. **保留 `THIRD_TOL`，只把它的作用域限定到 `capped=False`** —— **已否决**。
   等价于「保留一个永不生效的常量」，且 `capped` 与第三类位置判据**互斥**
   （见下条「后果」），限定作用域后 `THIRD_TOL` 恒不生效，是死代码。
4. **保留 `mode` 参数、只删容忍度** —— **已否决**。`_third_kind` 是唯一使用
   `mode` 的第三类路径；留着参数会让「第三类是否随模式分叉」这个问题
   永远悬着。删掉参数后，`find_signals` 的 `mode` 只影响
   `if mode is SignalMode.LOOSE:` 那一段追加 `find_divergences` 信号。
5. **让 G3a 补丁（`optimizer/patches/round-006-G3a.patch`）就此作废** —— **已否决**。
   G3a 提案的正是「把 `_third_kind` 的 `capped` 门加回来」，与 D-41 同向；
   2026-10-04 用户裁定「重锚 G3a 让它继续待评审」，于是把它重新锚到
   `THIRD_TOL` 删除后的主干上，`# status: pending` 保持不变。

**后果 / 变更历史**：

- **这是判据变更**（同一只票的买卖点数量会变），按 0.x 约定记为 **`MINOR`**：
  `0.2.0 → 0.3.0`。
- **★ 三处门的扰动矩阵**（全部自测，工具 `optimizer/tools/measure_capped_signal_gate.py`，
  全市场日线 5440 票；命令见 `docs/evidence/2026-10-04-capped-signal-gate.md`）。
  表里 `capped 列` 是**落在 `capped` 中枢上**的离开-依赖信号条数，
  它必须恒为 0 —— 这就是判据一：

  | 扰动 | `capped` 列 b1/s1/b3/s3 | 非-`capped` b1/s1/b3/s3 | 趋势背驰 | 判据一 | 退出码 |
  |---|---|---|---|---|---|
  | 无（落定主干） | **0/0/0/0** | 91/25/3065/2723 | 116 | 0 | 0 |
  | 只拆 `_third_kind` 的门 | **0/0/0/0** | 91/25/3065/2723 | 116 | 0 | 0 |
  | 拆 `_entering_and_leaving` + `_leaving_legs` | **46/18/0/0** | 91/25/3065/2723 | 64 | **192** | **1** |

  第二行与第一行**逐项相同**：`_third_kind` 的门是结构性 no-op（见下条）。
  第三行是**能失败**的证明：判据一从 0 跳到 192，退出码 0 → 1。
  另外单独测过「把 `mode` 相关的 `THIRD_TOL` 重新注入」（`--limit 600`，
  与上表不同规模，故不并列）：`strict b3 327 / s3 244` 对 `loose b3 330 / s3 245`
  ⇒ 判据三失败、退出码 1。**注入一个与 `mode` 无关的常数 `+0.1` 则两态相同**
  （`strict 330/245 == loose 330/245`，退出码 0）—— 所以判据三的证伪必须恢复
  `THIRD_TOL` 的**模式相关性**，不是随便加个松弛项。
- **★ 三处门里只有两处有产出 —— 第三处是结构性 no-op，据实记录。**
  `_third_kind` 的那道门**无论怎么改都不会改变输出**，实测：
  单独把它改成 `if False and p.capped:`，全市场 5440 票输出与未扰动**逐项相同**
  （`capped` 列恒为 0、判据一 0、判据二 11924），退出码仍是 0；`--limit 600`
  同样逐字节相同。原因是**结构性的**：`capped` 的定义要求 `end_idx + 1` 与
  `[ZD, ZG]` 重叠，而第三类买点的位置判据要求回试段**不跌破 ZG**
  （`back.low > p.zg`）—— 两者在**同一段**上互斥，门永远不会触发。
  **留它的理由是防止将来某次「放宽第三类判据」把延伸中的中枢重新误报成第三类**
  （`THIRD_TOL` 就是这么来的），它由三条单元测试作为**口径不变量**守住
  （`tests/chan/test_signal_mode.py::test_capped_pivot_emits_no_third_kind_buy/sell`
  与 `test_capped_pivot_emits_no_leave_dependent_signal`），
  **不由** `measure_capped_signal_gate.py` 守住。**不要把它记成「已验证的行为门」。**
- **★ 一处 docstring 的自我描述被实测证伪并更正**：
  `divergence.py::_leaving_legs` 原写「否则上面的双向相等断言会把两个实现的不一致
  暴露出来」，实测**是错的** —— 去掉该函数这一处门，
  `test_trend_divergence_matches_b1_s1` 仍然全绿（夹具的 4 只票上没有落在
  `capped` 中枢上的趋势背驰，那条断言量不到这个分支）。已更正为指向真正咬住它的
  `tests/chan/test_divergence.py::test_capped_pivot_is_skipped_by_both_leaving_leg_implementations`
  （新写的合成用例，两侧各断言一次，去掉任一处门都会在对应那行红）。
- **★ 修掉一处「看起来是护栏、其实量不到」的假护栏认知**：D-35 记录的
  `THIRD_TOL` 曾被当作「严格/非严格差异的全部来源」；实测（在删除 `THIRD_TOL`
  **之前**的一次性探针上）全市场严格 5788 / 非严格 5887，非严格独有的 99 条
  拆开是 **44 条落在已确认且 `capped` 的中枢上 / 0 条落在已确认非 `capped` 上 /
  55 条落在未确认（`TENTATIVE`）中枢上**。所以正确的表述是「在**已确认**中枢上，
  容忍度只可能在 `capped` 中枢上翻转」，而不是「容忍度只作用于 `capped`」——
  它同样翻转 55 条未确认中枢上的信号。该探针未入库，数字不可从当前主干复现，
  故只作决策输入留档。
- **前端同步**（AGENTS.md §5 第 5 条）：`src/chanlun/web/static/app.js` 里
  「第三类回试容忍中枢高度 10%（工程口径，无原文依据）」的文案已删除
  （两处：`basisLine()` 与 `applyModeUI()`），改为「第三类买卖点严格与非严格逐项相同」。
  `capped` 中枢在图上新增视觉标记：中枢区带用**点线边框 + 降透明度**，
  标签后缀 ` · 段数到顶`，图例行补
  「段数到顶（第33课：已构成更大级别中枢；本级别不产出买卖点）」
  （`app.js:829` 判定，`app.js:848` 标签，`app.js:1178` 图例）。
  此前 `capped` **已在 API JSON 里但前端零消费方**（`grep -rn capped
  src/chanlun/web/static/` 实测 0 命中）—— 这次把它接上（改后实测 11 命中，其中 `styles.css` 3 处、`app.js` 8 处）。
  实测 `GET /api/structure?code=sh.600519&period=day&mode=strict&bars=1200` 的
  `pivots[0]` 确实带 `"capped": true`（`status: confirmed`、`end_idx: 9`），
  所以标记有真实数据可画，不是空跑的装饰。
  **已用真实浏览器核对**（AGENTS.md §4.4；DOM dump 读文案，截图只作旁证）：
  `docs/evidence/2026-10-04-capped-marker-probe.mjs`（headless Chrome + CDP，禁缓存）
  在 `sh.600519` 日线 1200 根上 7 条判据全过、退出码 0 —— 图例行确实带
  「 · 段数到顶（第33课：已构成更大级别中枢；本级别不产出买卖点）」，
  markArea 那一条确实是 `borderType: "dotted"` + `borderWidth: 2` + `opacity: 0.6`，
  标签 formatter 确实带 ` · 段数到顶`。
  同页第 1 个中枢 `capped: false` 作**反向对照**，上述四项一项都不亮 ——
  这个探针**能失败**：把 `app.js:829` 的 `cap` 改成 `false` ⇒ 3 条判据红；
  只把 `app.js:1178` 的图例条件改成 `false` ⇒ 1 条判据红；两次退出码都是 1。
  细节见 `docs/evidence/2026-10-04-capped-signal-gate.md` §9。
- **顺带修掉的清单溢出**（同一次浏览器核对发现，与本条同源）：结构清单的
  `.row-meta` 是 `<span>`（**行内盒子**），而行内盒子上的 `overflow: hidden` 与
  `text-overflow: ellipsis` **一律不生效** —— CSS 里那两行声明写了等于没写。
  实测（1600px 视口）`sh.600519` / `sh.000001` / `sz.399006` 三只票的清单里
  **每一个** `.row-meta` 都冲出右栏边界（12–15 行全中），最多 **560.8px**，
  也就是日期与笔数那半截画在图表上、被视口右边缘裁掉，用户读不到，连省略号提示都没有。
  修法：`src/chanlun/web/static/styles.css` 的 `.row-meta` 加 `display: block`
  （`styles.css:432`；声明本来就想做省略号，块化之后才真的生效）。
  同一个原因让 `capped` 的 ` · 段数到顶（…）` 后缀在窄栏里**必然读不到** ——
  所以那个标记不能只放在 `.row-meta`：`.row-title` 上另挂一个点线小标签
  「段数到顶」（`app.js:1243-1252`，样式 `.chip.capped` 在 `styles.css:452`，
  `title` 属性带第33课原文依据），并给 `.row-title` 加 `flex-wrap: wrap`
  （`styles.css:414`）以免最后一个标签重演同样的溢出。
  探针相应加了 3 条判据（标题行标签数 == 1、非 capped 行为 0、`.row-meta` 不再越界），
  浏览器复测 10 条判据全过、退出码 0。**这 3 条也各自能失败**：把 `styles.css:432`
  的 `display: block` 注掉 ⇒ 「不越界」判据红（越界 2 条、最多 554.2px）；
  把 `app.js:1247` 的 `if (capped === true)` 改成 `if (false)` ⇒ 标签判据红。
- **与 D-35 的关系**：D-35（`ARCHITECTURE.md:2023`）记录了 `THIRD_TOL` 的引入，
  本条记录它的删除。D-35 里现在会读到 `THIRD_TOL` 的地方共 5 处
  （`ARCHITECTURE.md:1923-1927` 的作废提示、`ARCHITECTURE.md:1926`、
  `ARCHITECTURE.md:1931-1932` 的定义式与常量、`ARCHITECTURE.md:1946` 的勘误、
  `ARCHITECTURE.md:1959` 的护栏数字、`ARCHITECTURE.md:2003` 的「近乎 inert」注记）——
  **它们全部描述的是引入时的状态**，按「只增不改」纪律保留原样。
  **D-35 的历史数字不回填**，以本条为准。
  其中 `ARCHITECTURE.md:2003` 那条「`THIRD_TOL` 近乎 inert」的注记正好被本条**证实并推进了一步**：
  D-35 当时只知道它在**已确认非 `capped`** 中枢上 inert，
  本条把机制说清了 —— `capped` 与第三类位置判据**互斥**，
  所以它在 `capped` 上也 inert，于是**第三类买卖点根本不再随模式分叉**，
  常量也就没有存在理由了。
  同一条删除在 `CHANGELOG.md` 的 `[0.2.0]` 段（标题下那条
  「以及**第三类买卖点的回试容忍度** `THIRD_TOL = 0.1`」）也有历史记载。
  （2026-10-05 更正：这里原写的是 `[0.2.0]` 段的 931–932 **行号**，
  在 HEAD 上就已经指偏了 —— `[Unreleased]` 每加一条，`[0.2.0]` 段的
  绝对行号就整体下移（HEAD 上在 1025，加完 D-43 这批在 1153）。
  指向的正文一直没错，错的是那个数。非 `.py` 引用本来就不接退出码、
  只进「人工逐条核对」清单，所以这个偏差不会被任何测试拦住，只能靠人看；
  索性**改成按段名 + 引文定位**，不再写会腐烂的绝对行号。）
  那是**已发布版本号里的记录**，按「只增不改」纪律**不回填**；
  本条所在的 `[Unreleased]` 段落负责说明它已被删除。

#### D-42 背驰点 / 买卖点的落点：线段极值所在的那根原始 bar，不是段尾那一笔的结束时刻

**决策**：`Divergence.ts` 与 `Signal.ts` 改为「线段极值（`Segment.high` / `Segment.low`）
所在**原始 bar** 的 `ts`」；`price` 的取值口径**一个字没动**。
实现是 `src/chanlun/chan/divergence.py:115` 的新函数 `extreme_ts(bars, seg, want)`：

- 取 `col = "high" if want == 1 else "low"`、`target = seg.high if want == 1 else seg.low`；
- 在 `bars[col].iloc[seg.src_start : seg.src_end + 1]` 上按**值相等**（`abs(v - target) <= 1e-9`）找下标；
- 返回 `bars["ts"].iloc[a + k]`；找不到时回退 `seg.end.end.ts`（保持旧口径，不抛）。

`Segment.src_start` / `src_end` 是端点分型极值所在**原始 bar 的索引**（`Segment` 上的
property，见 `src/chanlun/chan/types.py:114-120`），所以它与 `bars` 的下标同一空间 ——
这一点由 `src/chanlun/chan/engine.py:174` 保证：引擎在 `full()` 里先 `normalize(bars)`，
再把**同一个** `bars` 帧交给 `divergence_fn` / `signal_fn`。

接线：`src/chanlun/chan/divergence.py:202`（背驰的 `ts`）、
`src/chanlun/chan/signal.py:176`（买卖点的 `ts`）、
`src/chanlun/chan/divergence.py:282` 与 `src/chanlun/chan/divergence.py:329-330`
（`reason` 里插值的时刻，跟着一起变）。
`_make` / `_sig` 新增的都是**带默认值的尾参** `bars=None`，
所以既有调用点不传也照样编译；`find_divergences` / `find_signals` 按位置把 `bars` 传下去。

**依据**（原文摘录逐字，课号见 `optimizer/theory/L24-DIVERGENCE-EXTREME-BAR.md`）：

- 第 24 课《MACD对背弛的辅助判断》（`chanlun108/原文/024-MACD对背弛的辅助判断.md:43`）：
  「所以，实际操作中根本不用回跌后才发现背驰，在上涨或下跌的最后阶段，判断就出来了，
  一般都可以抛到最高价位和买在最低价位附近。」—— 背驰成立的时刻**就是**极值那一刻。
- 第 60 课《图解分析示范五》（`chanlun108/原文/060-图解分析示范五.md:45`）把盘整背驰
  逐个钉在极值点上：「55跌破53后明显盘整背驰……如果把55当成第一类买点」
  「59-60的背驰足够标准……然后60新高，而柱子面积与黄白线高度都比前面不如」。
- 第 27 课《盘整背驰与历史性底部》（`chanlun108/原文/027-盘整背驰与历史性底部.md:37`）：
  「相应的转折点就在该级别背驰段确定的范围内。」—— 时刻落在**背驰段范围内**，不是段尾。
- 第 43 课《有关背驰的补习课》（`chanlun108/原文/043-有关背驰的补习课.md:51`）
  「前后两个走势类型，就以该背驰点为分界」讲的是**划分归属**，不是坐标；在极值本来就
  落在段尾时两者重合，本条全市场只移动了 3.4% 的背驰实体，见下。

**这不是新增判据，是自洽性修复。** 旧口径下系统报出 `price = 2.95` 却说「发生在
`2026-09-29`」，而那一天的最高价是 **2.23** —— 报出来的价格根本不在它报出的那根 bar 上。
触发这次修复的实例是 `sh.600759`（ST洲际）：页面上「盘整背驰卖」标记钉在
`2026-09-29 @ 2.95`，浮在那根 K 线上方；线段的极值 `2.95` 其实在 `2026-07-14`。

**被否决的替代方案**（两条都被全市场测量否决，数字由
`optimizer/tools/measure_marker_anchor.py` 复现）：

- **在 `[src_start, src_end]` 区间内取 `argmax` / `argmin`**：越界条数从 1076 降到 136，
  但其中 **128 条是 `CONFIRMED`** —— 区间内可能存在**不属于该线段**的异常高/低 bar。
  实测 `sz.301439` 的 `seg10`：`Segment.high = 18.38`，同一区间的原始 bar 最高是 **25.35**
  （被包含处理并掉了）。取 argmax 会把背驰钉到一个不属于该线段的价格上。
  **否决。**
- **取段尾同向笔的分型**（`seg.end.direction == want` 时用 `seg.end.end.ts`，否则用
  `seg.end.start.ts`）：全市场越界 **786/31821**，**比旧口径还差**。原因是结构性的：
  `Segment` 只保留 `start` / `end` 两笔（`src/chanlun/chan/types.py:95-120`），极值可以
  落在**中间**任何一笔上。实测 `sz.001331` 的 `seg6`（`direction = -1`、44 笔、TENTATIVE）：
  最低点 `4.16271` 落在 `2024-04-17`（`start笔` 的第二个分型），该方案把它钉到
  `2026-08-20`，那天的区间是 `[40.65, 45.28]`。**否决。**
- **保留旧口径、只在前端加一句提示**：治不了「价格不在它自己报出的那根 bar 上」这个
  自相矛盾，而且 `reason` 文本、自选池、扫描结果、回测成交记录里印的都是同一个错日期。
  **否决。**

**后果与变更历史**：

- **判据没变。** 全市场（`day`，5440 只票）实体数在两版**逐项相同**：
  背驰 `31821`、买卖点 loose `52441`、strict `5957`。变的只有 `ts` 与按 `ts` 的排序
  （`src/chanlun/chan/divergence.py:363`、`src/chanlun/chan/signal.py:222`）⇒ `idx` 会变。
- **纯显示层缺陷。** 越界标记按状态统计是 `{'tentative': 3378}` —— **100% 是 TENTATIVE**，
  而 `src/chanlun/chan/state.py:131-152` 的 `backtestable` 只放行 `CONFIRMED`，
  所以**胜率、净值、回测成交全部不受影响**。
- **下游三处显示消费点无需改代码**，它们读的就是 `sig.ts` / `sig.price`：
  `src/chanlun/backtest/runner.py:96`、`src/chanlun/scan/watchlist.py:129`
  （以及同函数的 `src/chanlun/scan/watchlist.py:127` 的 `ts`）、`src/chanlun/scan/scanner.py:309`。
  前端标记用 `value: [div.ts, div.price]`，日期从接口来，也没有写死口径。
- **范围边界**：`src/chanlun/web/static/app.js:944` 的**线段确认**标记仍用 `g.end.end.ts` ——
  那是「线段在哪个点确认结束」，与本条的背驰/买卖点落点不是一回事，**不改**。
- **可失败的测量**：`optimizer/tools/measure_marker_anchor.py` 自带旧口径负对照
  （同一次运行里同时算现口径与旧口径）。全市场实测输出：

```
实体                    总数       现口径越界       旧口径越界      定位失败
背驰                 31821           0        1076         0
买卖点 strict          5957           0         316         0
买卖点 loose          52441           0        2302         0

现口径越界按状态：{}
有越界的票：0 / 5440

判据一 现口径越界 = 0（要求 0）：通过
判据二 正对照 实体总数 = 90219，背驰 31821、买卖点 loose 52441（要求 > 0 且 == 31821/52441）：通过
判据三 可失败性 旧口径越界 = 3694（要求 > 0）：通过
EXIT=0
```

  把 `divergence.py` / `signal.py` 换回修复前的版本、同一条命令重跑 ⇒
  `判据一 现口径越界 = 288（要求 0）：失败`、
  `判据三 可失败性 旧口径越界 = 0（要求 > 0）：失败`、`有越界的票：143 / 400`、`EXIT=1`。
  **判据二 保持通过** —— 说明工具确实在测东西，不是恒真。
  （注意：该工具自己 `sys.path.insert` 了 `parents[2]/src`，所以**不能**用 `PYTHONPATH`
  指向另一份源码来做 A/B，必须直接扰动工作区源码。）
- **两条测试按新口径重写、并证明在修复前的输入上会失败**：
  `tests/chan/test_divergence.py` 原先直接断言 `d.ts == now.end.end.ts`（把旧口径写死），
  改为断言「`d.ts` 那根 bar 的 `high`/`low` 等于线段极值」；
  `tests/chan/test_signal_mode.py` 的 `sh.601088` 参数从 `["2024-12-27"]` 改为
  `["2023-06-01"]`，并在函数体里加了同样的「值必须落在那根 bar 上」断言。
  在修复前的源码上这两条**因预期原因失败**：
  `2024-12-27 的 low 应等于线段极值 159.080496，实际 243.1986`、
  `assert ['2024-12-27'] == ['2023-06-01']`。
- **版本**：这是「同一只票的划分结果与上一版不同」的口径变更（AGENTS.md §2 的
  「0.x 期的特别约定」），按 `MINOR` 处理 ⇒ `pyproject.toml` 的 `0.3.0` → `0.4.0`，
  `CHANGELOG.md` 记在 `[Unreleased]`。
- **已知空白**：`extreme_ts` 找不到等值 bar 时回退 `seg.end.end.ts`。
  全市场实测**回退 0 次**（`定位失败 0`）。历史上一份**直接读 parquet、没跑
  `chanlun.data.types.normalize`** 的探针曾报出 59/78931 次失败，那是探针假象 ——
  `sz.399001` 有 5 根 OHLC 全 0 的 bar（1995-02-06…1995-02-10），
  `src/chanlun/data/types.py` 的 `normalize` 会把它们剔掉，而 `engine.py:152` 内部就调它。

#### D-51 级别递归 f2 落地：中枢由**次级别走势类型**构造，级别不再等同于行情周期

**决策**：新增 `src/chanlun/chan/recursion.py`，把第 84 课的 `f2(an)=an+1` 接起来：

```
线段 ──f1──► 中枢(0) ──► 走势类型(0) ──f2──► 中枢(1) ──► 走势类型(1) ──f2──► 中枢(2) ──► …
```

`build_levels(segments, bars, base_level)` 返回按 `depth` 升序的 `LevelStructure` 列表。
三条实现要点：

1. **`find_pivots` 的入参由 `Sequence[Segment]` 放宽为 `Sequence[LevelSegment]`**
   （`types.py::LevelSegment` 是新协议）。`Segment` 与 `Trend` 都满足它，所以
   **f1 与 f2 用的是同一个 `find_pivots`**，没有第二份平行实现。第 84 课原话是
   「关于第二条的中枢过程规则，是一直没有任何改变的」—— 这是那句话的代码落点。
2. **`Trend` 补 `high`/`low`/`direction`/`src_start`/`src_end`**，使其可当「段」。
   `high`/`low` 取**原始 bar 极值**（跨度连续铺满时间轴，把两段中枢之间那段
   「离开 + 回试」的连接走势吃进来）；退化口径（不传 `bars`）取成分中枢的
   `gg`/`dd` 并集，会低估，故生产链路必须传 `bars`。
3. **`Trend.direction` 对盘整记 0**，不为它发明方向。相应地 `find_pivots` 里
   `direction = next((s.direction for s in trio if s.direction), 0)`；三段全无方向时
   整个组都算「中枢中」的段。

**依据**：第 84 课把递归拆成 f1/f2 两半，并允许 f1 随意设置、f2 不得更改；
第 17 课给中枢的递归定义（三个连续**次级别**走势类型重叠）；第 35 课给 f2 的可操作
措辞（「三个连续的最低级别走势类型……价格区间有交集，那么就形成了高一级别的
缠中说禅中枢……这个过程可以逐级上推」）；第 63 课给构造链与显微镜之辨；第 67 课
把线段定位为递归体系的起点。逐字摘录见 `optimizer/theory/L84-RECURSION-F2.md`。

**这不是判据变更。** 第 0 层与既有实现**逐字段相等**：
`build_levels(segments, bars, base_level="day")[0].pivots == find_pivots(segments, "day")`
（`tests/chan/test_recursion.py::test_level0_is_untouched_by_recursion`）。
递归是**加在**既有链路之上的一层，`engine.py` 的产出、落库键、API 形状一个字没动。
`ChanEngine.__init__` 的 `self.level = level or period` 也保持原样 —— 纠正它
（把「级别」与「周期」拆开）会改变 `Snapshot.level` 的语义与落库口径，属于另一次变更。

**被否决的替代方案**：

1. **给盘整按「区间中点相对前一段的漂移」补一个 ±1 方向，让每段都有方向** ——
   **已否决**。它的动机是迁就 `find_pivots` 里那句「相邻确认段方向必然交替」，
   但实测走势类型序列的相邻同向率是 **53.0%（L0）/ 75.0%（L1）**
   （`optimizer/tools/measure_recursion.py --period 5`），那句话对走势类型根本不成立，
   硬编出来的方向等于掷骰子。改成如实记 0、由 `find_pivots` 显式处理无方向段之后，
   递归不变量 6/6 通过。
2. **把 `find_pivots` 的 `zn`（Z 走势段）逻辑改成方向无关（GG/DD 一律取整个组）** ——
   **已否决**。那会改掉**线段**路径上已经过实测的口径（D-37/D-38，24 只票日线
   52 个确认中枢、28 个相邻对的 上涨 9 / 下跌 4 / 高级别中枢 15）。现在的写法
   对线段路径**逐字等价**（`trio[0].direction` 恒为 ±1，`next(...)` 取到的就是它），
   只在走势类型路径上生效。
3. **把每个周期的图各自递归一遍，然后把结果叫作「N 分钟级别中枢」** —— **已否决**。
   这正是旧口径。第 63 课明说「什么级别的图和什么级别的中枢没有任何必然关系」，
   第 84 课补刀「级别，本质上与时间无关……一个最低级别不到的走势类型，可以生长
   100 年不长成更高级别的」。递归只能有**一个**基准显微镜，其上的级别名
   （`LEVEL_UP` 的 `5→30→day→week→month`）只是**相对基准的递归深度**的标签
   （第 102 课：「这些级别的名字是可以随意取的」）。
4. **顺手把第 20 课的级别扩张（`merge_pivots`）也接进生产链路** —— **本轮不做**。
   它与 f2 是两条独立机制（见 theory 条目末节），接进来会改变中枢的级别语义，
   需要单独测量。`merge_pivots` 至今仍只被测试调用。
5. **让 `build_levels` 在最大深度处截断并返回** —— **已否决**，改成「遇到空级别就停」。
   一个级别连一个中枢都构不出来时，再往上没有输入，硬推只会产出空壳。
   `max_depth` 只是防病态输入的上限（缺省 6），不是正常终止条件。

**后果 / 变更历史**：

- **真递归不是 no-op**：同一只票、同一级别名，递归口径与旧口径的中枢数
  **逐项相同 0/6**（`sh.600030` 16 vs 18、`sh.600519` 17 vs 22、`sh.600759` 4 vs 12、
  `sh.601398` 5 vs 11、`sh.603178` 14 vs 22、`sz.000002` 12 vs 16）—— 递归口径
  **系统性地更少**，因为上一级中枢要求三个次级别走势类型真的重叠。
- **层级收敛自然**：5 分钟基准下 `sh.600030` 为
  L0(5) 115 中枢 / 83 走势类型 → L1(30) 16 / 13 → L2(day) 2 / 1，每级约 1/7。
  递归不变量 6/6 通过。
- **合成序列可稳定复现三级**：`tests/chan/test_recursion.py` 用确定性多尺度正弦
  （六尺度 + 固定种子噪声，24000 根）产出 L0 33–41 中枢 → L1 4–6 → L2 1。
  真实离线夹具（4 只各 1212 根日线）只够一级 —— 1212 根日线切不出三个互相重叠的
  走势类型，这也是合成夹具存在的理由。
- **已接进 `Snapshot` 与 `/api/structure`（2026-10-13）**：`Snapshot.levels` 是
  **派生字段**（与 `divergences` 同一类），由 `_compute` 里**新鲜算出的**
  segments + bars 决定，不进 `state.py::_FIELDS`、不参与 reconcile。
  **既有字段一个都没动**，`levels[0].pivots == snapshot.pivots`。
  开销实测 +2.2%（日线 6.35 → 6.49 ms/只，全市场外推 36 秒）。
  **API 是自动出口的**：`structure_payload` 走 `to_jsonable(view.snap)`，
  `asdict()` 递归，所以 `levels` 一落地就在 `/api/structure` 的响应里，
  `web/api.py` 一行没改（实测 `sh.600030?period=5` 返回 L0/5 115 中枢、
  L1/30 16、L2/day 2）。
  **`levels` 是唯一不随 `clipped_to` 裁剪的字段**：递归的高级别天然跨越数年，
  拿 300 根的显示窗口去裁必然三级全空（未加这条约定前实测 `limit=200` 时
  三级都是 0 中枢）。它的用途是回答「这个窗口里的结构处在哪个更高级别里」，
  是**上下文**问题，必须看全史；可见/全史的区分沿用 `counts` / `counts_total`
  那一对约定。
  **2026-10-13 补：`scan` 现在**看得见**递归了 —— 但只作为**上下文**，不参与判定。**
  每个 `ScanHit` 多一个 `level_context`（如「L1/30 上涨走势、L2/day 中枢内」），
  落进 `detail` 文案与 `as_detail_json()`；扫描摘要的 `stats` 多 `levels` /
  `max_level_depth` 两个数（否则「只出 L0」与「出了 6 级」在摘要上一模一样）。
  **边界是刻意的**：它**不参与命中判定、强度排序、去重** —— 有一条用例把 `levels`
  抽掉后要求命中的种类/时间/强度/顺序**逐项不变**。让递归**参与**买卖点判定属于
  判据变更（要 `optimizer/theory/` 条目 + 可失败测量 + MINOR 版本位），仍**未接**；
  自然入口还是 §5 登记的「第一类买卖点区间套」。`backtest` 同样仍未消费。
- **递归的产出高度依赖基准显微镜，这一点被实测坐实了。** 以**日线**为基准、
  **全市场 5478 只**跑一遍：**4965 只（90.6%）只出 1 级**、513 只（9.4%）出 2 级
  （`day/week`）。作为对照，以 **5 分钟**为基准的 17 只：1 级 6 只（35.3%）、
  2 级 2 只（11.8%）、**3 级 9 只（52.9%）** —— 分钟基准下过半能出三级。
  复现：`../.venv-chanlun/bin/python optimizer/tools/measure_recursion.py --invariants --period day`（约 40 秒）。
- **递归在最广的面上是「旧行为的超集」，这一点也全市场验过。** 同一次扫描校验了
  四条不变量（`--invariants`），**5478 只违规 0 只**，其中最关键的一条是
  **`levels[0].pivots` 与 `snap.pivots` 逐个相同** —— f2 只往上加级，碰最低级别
  就是错的。**它是「`scan`/`backtest` 沿用 `snap.pivots` 等价于『用 L0』」这句话的
  唯一保证**：没有它，沿用旧字段就不再等价，而且**没人会知道**（两边看起来都
  「有结果」）。用例 `test_l0_is_exactly_the_engine_pivots` 把它钉在合成序列上，
  全市场那一遍是广度证据。
  因为「上一级中枢 = 三个下级走势类型重叠」，日线级走势类型本来就少，凑不出三个
  重叠的。要让递归真的产出内容，基准必须是分钟级；而分钟数据在本机的现实是：
  **5 分钟只有 6 只票**、全市场同步约 100 小时（README 原话）。
  **其中「指数没有分钟源」这半句 2026-10-13 已不成立**：A 股指数改走东财后
  实测 `sh.000300` / `sz.399001` / `sh.000688` 各能取到 1536 根 5 分钟（D-52）；
  **申万行业板块仍然没有**（腾讯 `param error`、新浪 `null`、东财那个 `90.` 不是申万）。
- **point-in-time 已补测：递归没有引入未来函数。** 把「只喂到第 i 根 bar 的前缀」
  与「喂全量」对比：**backward**（全量里 `confirmed_at <= ts[i]` 的级别中枢，前缀上
  必须已经有了）违反 **0/999 = 0.0%**；**forward**（前缀上已确认的级别中枢，全量里
  必须存在且 ZD/ZG 相同）违反 1/1002 = 0.1%。那 1 例是**线段层的不收敛残留**
  （D-40 已登记的全局 DP 问题，L0 同样会动），不是递归层新增的。复现：
  `PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion_pit.py --period 5 --sample 2 --checkpoints 8`。
- **跨度约定是有影响的，且已量化。** 走势类型的 `high`/`low` 用什么 bar 跨度算，
  是本实现的一条工程判据（原文没规定「两段走势类型之间的连接走势归谁」）。
  实测（6 只 5 分钟票）L0 **全部相同**（L0 不吃走势类型区间），L1/L2
  **逐级相同 9/17**；退化口径（不传 `bars`，落到 `gg`/`dd` 并集）**一律产出更多**
  上级中枢（`sh.600030` L1 16→18、`sh.600519` L1 17→21、`sh.600759` L1 4→6），
  因为它低估区间（`gg`/`dd` 不含离开段）。复现：
  `PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion.py --period 5 --span-sensitivity`。
- **一个尚未解决的问题**：`find_pivots` 的 `GG/DD` 在走势类型路径上是取了
  「组内第一个有方向的段」来定 `zn`，这在方向不交替的输入上只是一个**确定的**选择，
  不是原文能直接推出的选择（原文的 A、C 同向假设以交替为前提）。这一点已如实
  登记，见 §5 已知空白。
- **版本**：本条**不涨版本号**，留在 `[Unreleased]`。理由：本轮改动**尚未提交**，
  且会话内另一个 agent 正在改 `web/` 的 D-50（同样留在 `[Unreleased]` 且明确
  「本次不涨版本号」）—— 发版时两个 agent 的改动需要一起归并、一起定版本位。

**变更历史（2026-10-13）：补上 `LEVEL_UP["1"]` —— 在此之前「1 分钟基准」根本递归不上去。**
用户明确要的是「以 1 分钟为基准往上进行递归推理」，而级别表里**没有 `"1"`**：
`next_level("1")` 走未知级别的退化分支返回 `"1+"`，`level_chain` 见到 `+` 就 `break`，
于是 `level_chain("1") == ["1"]` —— **1 分钟基准只能出 L0，一级都上不去**。
**失败是无声的**：不抛错、不警告，只是没有上一级；这正是这类表驱动设计最容易漏的地方。

补上后 `level_chain("1") == ["1", "5", "30", "day", "week", "month"]`，
每档正好是行情的下一档。合成序列实测（同一张 bar 帧，只换基准标签）：
`base=1` → L0/1 70 中枢 46 走势 | L1/5 10/10 | L2/30 1/1；
`base=5` → L0/5 70/46 | L1/30 10/10 | L2/day 1/1。

同一轮还删掉了 `web/api.py::sync_blocker` 里一条**同类**的过期护栏：它硬写
「指数是指数，baostock 不提供指数的分钟线，同步这个周期只会空跑」并据此 400 拒绝。
指数分钟线改走东财后（D-52）实测各能取到 1536 根 5 分钟，护栏却还在挡 ——
**而那段代码上方自己写着**「哪个源给不给这个周期只有 `capabilities` 知道，
在这里再写一份会让『页面说能补、接口说补不了』换个市场重演」。删掉硬编码，
统一交给 `capabilities`。

**仍未接的**：`scan` / `backtest` **判定**时不消费 `levels` —— 要用它就得改
买卖点判据（自然入口是 §5 里登记的「第一类买卖点区间套」）。
（同日稍后又补了一步：`scan` 的**产出**已经带上 `level_context` 与 `levels`/
`max_level_depth` 统计，但那是**只读上下文**，不参与判定。）

#### D-52 取数源按时段分流：同一个市场里，分钟线的源按**标的**不同

**决策**：新增 `sources.source_for(code, period)`，把「市场 → 源」从一对一改成
**按时段分流**；配套新增 `sources.vendor_symbol_for(code, src)`，因为分流之后
「市场 → 符号」也不再一对一。新增 `data/eastmoney_source.py` 承接多出来的那部分：

| 标的 | 日线 | 分钟线 |
|---|---|---|
| `sh/sz/bj` **股票** | baostock | baostock（不变） |
| `sh/sz` **指数** | baostock | **东财** |
| `sw` **申万行业板块** | 腾讯板块接口 | **无源**（见「被否决的方案 5」）|
| `hk` / `us` | 不变 | 不变 |

`source_of(code)`（按市场）**保留**：它仍是「这只票谁在供数」在市场层面的答案，
也是 `source_for` 的兜底，`__main__` 的指数诊断文案还在用它。

**依据**：这是三个数据源各自的能力边界逼出来的，不是设计偏好 ——

- **baostock**：对 `sh.000001` / `sh.000300` / `sz.399001` / `sw.801010` 的分钟线
  一律返回 **0 行**（实测 `err=0 success` 但无数据），只有股票有（6.5 年）；
- **腾讯 `mkline`**：上限 **320 根**，且对申万 `pt01…` 回 `param error`；
- **新浪 `getKLineData`**：`datalen` 硬封顶 1023，对申万回 `null`；
- **东财**：能补上 **A 股指数**这一段。判据是核对 `data.name`，**不是号段** ——
  实测 `1.000300` 回「沪深300」、`1.000688` 回「科创50」、`0.399001` 回「深证成指」，
  6/6 全对。**但 `sw` 不行**：`secid=90.801010` 回的是「磷化工」而不是「农林牧渔」
  （`90.` 是东财自己的板块空间，同号不同物），详见「被否决的方案 5」。

顺带**证伪了一条旧结论**：「东财连接被拒」——被拒的只是 `https://`，
换 `http://push2his.eastmoney.com` 就通。
**而 D-44 第 6 条「申万没有分钟线」并没有被证伪** —— 一度以为东财能补上，
**那是错的**（见「被否决的方案 5」），它现在仍然成立。

**被否决的替代方案**：

1. **按市场整体切到东财** —— **已否决**。东财的分钟历史只有约 **2 个月**
   （服务端保留边界，不是翻页限制），而 baostock 对股票有 **6.5 年**。
   为了「统一源」把好数据换成差数据，是拿能力换整洁。
2. **把「裸码指数」也交给 `markets.is_index` 直接判** —— **已否决，而且已经踩过**。
   `markets.is_index` 对**裸码**一律返回 `False`（它的 docstring 写着「A 股必须带
   市场前缀才判得出」），而归一之后 `sz.399001`（深证成指）**正是裸码** ——
   39 号段深市独有，前缀被判为冗余（见 `tdx.store_key`）。第一版就这么写的，
   实测它被判成股票、继续走 baostock 拿 0 行。改成 `_is_index_key()`：
   按市场把前缀补回去，再交给**同一个**谓词（判据只有一处实现）。
3. **让 `source_of` 也带 `period` 参数（改名、不保留旧函数）** —— **已否决**。
   有 5 处调用只关心「谁在供数」这一市场层面的问题，逼它们编一个周期出来
   只会得到为过签名而写的 `period="day"`。
4. **给东财写回填历史的翻页** —— **做不到，不是不做**。实测 `klt=5` 的
   `end=20260821` 回 **0 根**，而 `klt=101`（日线）`end=20240101` 能翻回
   `2003-01-06` —— 翻页机制本身是好的，是**服务端只保留约 2 个月分钟数据**。
5. **用 `secid=90.801010` 给申万取分钟线** —— **已否决，而且已经踩过并造成了数据污染**。
   当时以为 `90.` 是「申万板块」的市场前缀、`0.`/`1.` 都回 `data=null` 正好佐证了它。
   **那是错的**：`90.` 是东财**自己的板块空间**，**同号不同物** ——
   实测 `90.801010` 回的是「**磷化工**」（不是农林牧渔）、`90.801080` 回「新型城镇化」
   （不是电子）、`90.801012` 回「PVDF概念」（不是农产品加工）。
   按那个映射**已经把 96 个文件导进了库**，文件名却写着 `sw.801010` 之类 ——
   **张冠李戴的行情，比没有数据危险得多**。
   **更硬的证据（2026-10-13 稍后补）：东财根本不收录申万行业指数。** 用东财自己的
   搜索接口 `searchapi.eastmoney.com/api/suggest/get` 实测：输入 `801010` 回 **0 条**；
   搜「申万」只回申万宏源的**股票与基金**（`0.000166` 等），没有任何行业指数；
   搜「农林牧渔」回的是 `90.BK0433`（东财**自有**板块）与 `47.881001`（东财可转债指数）。
   也就是说 `90.` 这个市场装的是 `BKxxxx` 自有板块，**同号纯属巧合**。
   顺带解释了那个坑为什么这么滑：`BK0433 农林牧渔` 与申万 `801010 农林牧渔`
   **名字一模一样、是不同的指数**，只看名字不看代码反而更容易上当。

   **发现方式**：补完数据后核对覆盖，发现 155 个板块里 **59 个是空的**（都缺在 `8010xx`
   上），去查「为什么缺」时顺手核了 `data.name`。**号段会撞车，名字不会** ——
   要判断一个 secid 是不是想要的标的，唯一可靠的办法是核对 `data.name`。
   **处理**：① 删掉 `data/5/sw/*.parquet` 那 96 个文件与 155 条 `sync_state` 水位；
   ② 删掉 `sw → 90.` 的映射，并在 `eastmoney_source.secid()` 里对 `sw.*` 加**护栏直接抛错**
   （让这个错映射**做不到**，而不只是「调用方不去调」）；③ `capabilities` 写回
   「申万没有分钟线数据源」，并把「东财那个 `90.` 不是申万」写进 `reason`。
   **申万的分钟线至今无解。**

**后果 / 变更历史**：

- `capabilities("sh.000300", "5")` 等 A 股指数从 `supported=True, source="baostock"`
  （**但实际回 0 行**）变成 `supported=True, source="eastmoney"`，并带一句
  「约 2 个月、服务端保留边界、适合每日增量累积而非回填」的 `note`。
  **申万的分钟周期仍然 `supported=False`** —— 页面继续灰掉它，这是对的。
- **定位要写清楚**：东财这条路的用途是**每日增量累积**（每天取最近那段窗口落库，
  久了自然攒出长历史），**不是回填历史**。要一次性拿多年分钟历史仍走
  通达信 vipdoc（`tdx.py`，见 D-09 变更历史）。
- 修 bug 一处：东财源的本地裁剪原先直接 `df["ts"] <= end`，而 `end` 常常是**裸日期**
  `YYYY-MM-DD`、分钟 `ts` 是 `YYYY-MM-DD HH:MM` —— 字典序上
  `"2026-10-13 09:31" > "2026-10-13"`，**当天会被整批丢掉**，而且看起来像
  「这个周期没数据」。改成按 `end` 的长度截断再比。`data/types.py::truncate`
  有同一个坑（见 §5）。
- 字段顺序在真实响应上逐条核对过：`fields2=f51…f57` 是
  **时间、开、收、高、低、量、额**（收在高低之前）。判据是
  `low <= min(开,收) <= max(开,收) <= high`，按「开收高低」三条实测数据全部成立、
  按「开高低收」全部不成立。顺序猜错不会报错，只会静默产出高低颠倒的行情。
- **同一轮推翻了三条旧结论**：D-09（分钟解析故意不实现）、D-44 第 6 条
  （申万没有分钟线）、以及 §5 里「东财连接被拒」。三条都按 AGENTS.md §1
  在**原条目**里补了变更历史，编号与原文都保留。

#### D-53 通达信在线行情（pytdx）接入：1 分钟数据的唯一来源

**决策**：新增 `data/pytdx_source.py`，接入 `sources.source_for` 的按时段分流：

| 标的 | 周期 | 源 |
|---|---|---|
| `sh/sz` 全部 | **`1`** | **pytdx**（只有它有） |
| `sh/sz` **指数** | `5/15/30/60` | **pytdx**（2 年，远深于东财的 2 个月） |
| `sh/sz/bj` **股票** | `5/15/30/60` | baostock（6.5 年 > pytdx 2 年，不变） |
| 全部 | `day/week/month` | 不变 |
| `sw` / `hk` / `us` | 全部 | 不变 |

`adjust_for` 的签名相应加上 `period`（**这是必须的**，见「被否决的方案 2」）。

**依据**：**1 分钟在本模块之前没有任何来源**（详见 §5 各源的实测边界）。
pytdx 实测（104 台服务器里 8 台活着）能给：1 分钟 **23520 根 / 约 4.5 个月**、
5 分钟 23808 根 / 约 2 年、日线 8468 根（1991 起），**全部到当日收盘**，
一只 1 分钟约 **1.5 秒**。

**这一步把用户要的「以 1 分钟为基准往上递归」从「做不到」变成「实测跑通」**：

**基准换档的量化对照（这是关键数字）**：日线基准下全市场 5478 只里 **90.6% 只出 1 级**；
**1 分钟基准下抽样 20 只、只出 1 级的 0 只**（2 级 14 只、3 级 6 只）——
`sh.000300`/`sh.000688`/`399001` 等 7 个 A 股指数与 `600030` 全部出到 **3 级**。
复现：`../.venv-chanlun/bin/python optimizer/tools/measure_one_minute_base.py --sample 24`
（冷跑约 1.2 秒/只 ⇒ 全市场约 **1.8 小时**；约 **3.4 GB**）。

自选池 13 只里 **9 只 A 股全部成功**（7 指数 + 2 股），4 只失败正是港美
（腾讯港股只有日线、新浪美股不支持 1 分钟）—— 那是**已知边界**，不是回归。

**1 分钟基准实测产出**：

```
sh.600030 23520 根 → L0/1 25中枢 18走势 | L1/5 3/3 | L2/30 1/0
sh.000300 22320 根 → L0/1 35中枢 24走势 | L1/5 6/5 | L2/30 1/0
sz.000001 22320 根 → L0/1 11中枢  7走势 | L1/5 2/1
```

**被否决的替代方案**：

1. **继续用东财做指数分钟** —— **已否决**。东财的服务端只保留约 2 个月 5 分钟、
   1 个交易日 1 分钟（实测 `end=20260821` 回 0 根），而 pytdx 有 2 年 / 4.5 个月。
   `eastmoney_source` **保留**：pytdx 的 IP 会变、可能全线失联，那时它是唯一退路
   （`source_for` 里已有 `pytdx_source.available()` 的判断）。
2. **给 `adjust_for` 加一个「探针周期」而不是改签名** —— **已否决，而且已经踩过**。
   初版想省掉签名变更，用 `source_for(code, "1")` 去探「这只票走不走 pytdx」。
   结果 `sh.600030` 的 **5 分钟**（走 baostock、应当是前复权 `"2"`）被判成 `"3"` ——
   同一只票的不同周期口径本来就不同，**周期必须一路传下去**。
3. **把 pytdx 当成「分钟全线」的源** —— **已否决**。它 5 分钟只有 2 年而 baostock 有
   6.5 年；不为了统一把更好的换成更差的（与 D-52 同一条理由）。
4. **让 pytdx 假装能给前复权** —— **已否决**。在线行情**只有不复权**，传 `"2"` 当场
   拒收报错。猜口径比不取数危险得多（D-19）。

**后果 / 变更历史**：

- **推翻 §5 ⑤「pytdx 协议不兼容」**。那是把「试到的几台服务器都死了」误判成
  「协议不兼容」，代价是 **1 分钟数据被判为「无源」长达十轮**。已按 AGENTS.md §1
  在原条目留下变更历史。新增工具 `optimizer/tools/probe_pytdx_hosts.py` 重测存活服务器
  （104 台逐台试），并把这条教训写进它的输出。
- `web/api.py::PERIODS` 与 `__main__.py::PERIODS` 加入 `"1"`；`capabilities` 新增
  **pytdx 分支** —— 漏了它，港股/美股那两条兜底会把它当成美股，报出
  「美股不支持周期 1」这种驴唇不对马嘴的理由（实测踩过，已配用例钉住）。
- `sync.adjust_for` 同步改签名，`fetch_bars` 的调用点带上 `period`。
- 口径：pytdx 落库一律 **raw**，日线仍是 baostock 前复权 —— **同一只票两个周期口径
  不同**，导入时会有口径混用告警（见 D-19 与那套告警）。
- 实测数据质量：`sh.000300` 的 22320 根里有 **1 根** OHLC 不自洽
  （`2026-09-28 15:00` 收 4340.76 vs 高 4340.75，0.01 的舍入差），
  `normalize` 按「仅观测」保留 —— 是数据商自身的毛病，不是解析错。

#### D-54 区间套进强度判定：级别递归**参与**买卖点（第 27 课）

**决策**：新增 `chan.signal.quantao_confirm()` —— 判定第一类买卖点的**背驰段**是否落在
**上一级别背驰段**的 bar 区间内（嵌套包含）；结果记在 `Signal.quantao`
（`True`/`False`/`None`），并经 `scan.scanner.hit_strength` 的
**`QUANTAO_WEIGHT`**（`True→1.0`、`False→0.7`、`None→1.0`）进入生产强度与排序。

原文（第 27 课《盘整背驰与历史性底部》）：

> 某大级别的转折点，先找到其**背驰段**，然后在**次级别图里**，找出相应背驰段在
> 次级别里的**背驰段**，将该过程反复进行下去，直到最低级别……

> 而这背驰段，**一定在**季度线的背驰段里，**而且区间比之小**

第二句就是可执行的不变量：**小级别背驰段的 bar 区间 ⊆ 大级别背驰段的 bar 区间**。

**依据**：`chan/signal.py` 的 docstring 自己登记过这个缺口 ——
「第 27、28 课讲的第一类买卖点区间套定位（多级别联立）没有实现」。
在此之前没有「次级别」这个可下钻的对象（D-51 的 `Snapshot.levels` 才有）。
实现路径：**上一级别的「线段」＝ 下一级别的走势类型**（`Trend` 满足
`types.LevelSegment`），所以 `divergence.find_divergences` 在高一级别上**原样复用**，
不需要第二套背驰实现。

**被否决的替代方案**：

1. **把区间套做成硬门**（未确认就不出第一类买卖点）—— **已否决，而且强于原文**。
   第 27 课说区间套用于「**确认**一个十分精确的历史底部区间」，没有说「不嵌套就不是
   买点」；第一类买卖点的定义在第 17 课（趋势背驰）。实测支持这个判断：1 分钟基准
   抽样 24 只，**第一类买卖点 31 个里只有 4 个（13%）被确认** —— 硬门会砍掉 87%
   的第一类信号，并**级联**减少第二类（`_second_kind` 吃 `b1`/`s1` 作输入）。
2. **`None`（没有上一级别）也降权** —— **已否决**。`levels` 只有一级时
   根本没有「上一级别」这个东西可比（第 63 课：级别名只是相对基准的递归深度标签）。
   把「**无从确认**」当成「**确认失败**」去降权，是在惩罚一个不存在的判据。
3. **在 `SignalFn` 契约里加 `levels` 参数** —— **本轮不做**。契约是
   `(bars, segments, pivots, level)`，引擎刻意不内置买卖点规则；改它要动
   engine/scan/backtest/测试四处的签名。所以 `quantao_confirm` 内部自己算一次
   `build_levels`（实测全市场 5471 只约 33 秒）。属独立接口变更。
4. **按「次级别图」取真实的低周期数据**（原文的字面读法）—— **本轮不做**。
   原文说的是「在**次级别图里**」，字面上要用另一张周期的图。本实现用的是
   **同一张图上的递归级别**（D-51：级别与行情周期无关，第 63/84 课）。
   两者在概念上等价，但取数路径不同 —— 换成真·多周期要走 `store.read` 的另一套，
   属独立变更。

**后果 / 可失败测量**（`optimizer/tools/measure_quantao.py --period 1 --sample 24`）：

```
有「上一级背驰段」的票：6/24
第一类买卖点 31 个，其中区间套确认 4 个（13%）
强度发生变化的信号：27/360
top10 排序变化：10/10 个位次变了
```

**最后一行是关键**：区间套**真的改到了 `scan` 的产出**（`scan` 按强度取 top），
不是只加了个标注字段。若换一批数据得出「确认 0 个」或「排序无变化」，
那说明这个判据没有区分度 —— 测量脚本的退出码会把那种情况判成失败。

**校准**：`0.7` 与既有权重（`STATUS_WEIGHT` 的 `tentative=0.6`）同量级，
按「状态折扣比级别定位折扣更重」排。它与 `KIND_WEIGHT`/`LEVEL_WEIGHT` 一样是
**本项目的工程常数**，原文没有数值。

**版本**：判据变更 ⇒ **MINOR**（0.x 期约定：会让同一只票的信号强度与排序与上一版不同）。

### 3.5 课号引用总表（代码 → 原文）

格式：`文件:行号 — 对应逻辑`。行号以**当前工作区**为准。

| 位置 | 课号 | 逻辑 |
|---|---|---|
| `include.py:1` | 65 | 包含处理 |
| `include.py:41-51` | 65 | 「如果gn>=gn-1，那么称…是向上的」（`>=` 含相等，`_merge_direction`） |
| `include.py:27-39` | 65 | 两两包含的首两根 K 线走 `_infer_direction`（**原文空白项**） |
| `fractal.py:1` | 62 / 65 | 分型定义本体 / 包含处理后的方向判定 |
| `stroke.py:1` | 62 + 77 | 笔 |
| `chan/types.py:90` | 67 | 线段 |
| `segment.py:4-12` | 78 | 至少三笔、笔数为单数、特征序列、缺口、两种情况 |
| `segment.py:67-71` | 65 | 包含关系 K 线组等价区间 |
| `segment.py:58-75` | 65 | 结合法则 `_combine`（向上取 max/max，向下取 min/min） |
| `segment.py:78-128` | 67 / 65 | 标准特征序列 / 顺序原则 / **合并后回退**（不传递律，D-31） |
| `segment.py:94-103` | 65 / 79 | 包含关系不符合传递律 / 先全部包含再分段 |
| `segment.py:97-98` | 62 | 顶底分型的三 K 线定义 |
| `segment.py:174-181` | 62 | `_is_fractal_at` 的严格不等式 |
| `segment.py:196-228` | 78 | 极值**次序**判据 `_ordinal_ok`（标准化是事后步骤，D-28） |
| `segment.py:337-411` | 67 / 71 | 划分唯一性；**左端起段取最早前缀上第一个可行起点**（原文空白项，D-30 → D-40） |
| `segment.py:514-518` | 78 | 第二种情况的确认笔可以远在后面 |
| `segment.py:533-564` | 78 | `_extreme_problems`：标准化与次序的交叉校验 |
| `segment.py:567-596` | 78 | `validate_segments(..., strokes=)` 的额外校验 |
| `pivot.py:13` | 20 | 判趋势看 `[DD, GG]`（中心定理二） |
| `pivot.py:15-20` | 20 | Z 走势段与 GG/G/D/DD 定义 |
| `pivot.py:31-33, 90-96` | 33 | 延伸不超过 5 段 + 3 段 = 8 （**旧引用写的是 `67-72`，那其实是 `LEVEL_UP`，指错了**；2026-10-13 顺带改正）|
| `pivot.py:42` | 17 | 延伸：仍与 `[ZD, ZG]` 重叠 → 后移 |
| `pivot.py:46` | 20 | 扩展改变级别语义 |
| `pivot.py:173` | 20 | `direction = next((s.direction for s in trio if s.direction), 0)`（A、C 段方向与中枢形成方向一致；**盘整没有方向**，取组内第一个有方向的段，全无方向时整个组都算中枢中，见 D-51） |
| `pivot.py:175-180` | 20 | 中心定理一 |
| `pivot.py:210-219` | 20 | `Zn` 只取同向段；`inside = zn[:-1]` 剔除离开段（D-15） |
| `trend.py:12-16` | 20 | 趋势里前后中枢不能有任何重叠 |
| `trend.py:74-92` | 20 | 中心定理二逐字（`[DD, GG]` 三分支，D-16） |
| `chan/types.py:163-186` | 84 | `LevelSegment` 协议：把「段」抽象出来，让 f1 与 f2 复用同一个 `find_pivots`（「关于第二条的中枢过程规则，是一直没有任何改变的」） |
| `trend.py:60-71` | 17 / 35 | 走势类型的价格区间与方向。区间 = 「所分别经过的价格区间」；**盘整如实记无方向**（第 17 课只给趋势分上涨/下跌） |
| `trend.py:105` | 17 | `classify_trends`：盘整（1 个中枢）与趋势（≥2 个依次同向中枢） |
| `recursion.py:1` | 17 / 35 / 63 / 84 | 中枢的递归定义；`f1(a0)=a1` 与 `f2(an)=an+1`；「这个过程可以逐级上推」；显微镜之辨（D-51） |
| `recursion.py:113-146` | 35 / 84 | `build_levels`：走势类型(n) → 中枢(n+1) → 走势类型(n+1)，遇到空级别就停 |
| `recursion.py:148-225` | 35 | `validate_levels`：上级中枢的 `[ZD, ZG]` 必须**恰好**等于下级前三个走势类型区间的交集 |
| `macd.py:6-8` | 24 | 面积看红/绿柱子；两个走势类型须同向 |
| `macd.py:180-202` | 24 | `hist_area(..., color)` 分色累加（D-17） |
| `signal.py:6-7` | 24 | 趋势背驰 vs 盘整背驰 |
| `signal.py:11-13` | 20 | 「离开」是位置 |
| `signal.py:143-144` | 24 | 向上看红柱子，向下看绿柱子 |
| `signal.py:230-231` | 20 | 第三类买点定义 |
| `signal.py:302` | 24 | 比较力度的对象是离开段 |

> **本表的行号由工具守门。** 本文档里的 `.py` 引用（`文件.py:行号`）可以随时全量复核
> —— 文件是否存在、行号是否越界（硬判据，失败即 `exit 1`），外加符号漂移告警与
> 「缺符号、无法校验」的条数：
>
> ```bash
> cd <本项目根目录> && ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py
> ```
>
> 上次全量复核 2026-10-03（第二轮）：`186 处 / 176 行`、硬判据失败 **0** 条、
> 符号漂移告警 **5** 条、缺符号无法校验 **29** 条（其中 ±2 行内有符号的 J2 **14** 条）、
> 路径歧义 **0** 条、非仓库路径 **1** 条。
> `--strict-symbols` 可把符号漂移告警升级为失败。
>
> ⚠️ **它的保证比这句话听起来弱得多，实测过**：硬判据只有「文件存在 / 行号不越界 /
> 区间不倒置」三条（工具 `optimizer/tools/check_doc_line_refs.py` 的 docstring 第 23 行
> 自陈），
> **不校验被引用行上的内容是否还是那个符号**。实测对照：在 `src/chanlun/chan/segment.py`
> 开头插入 15 行注释（使其后所有行号整体平移 15，本文档十余处 `segment.py:NNN`
> 全部指错行），工具输出
> `共 186 条，通过 185 条，失败 0 条，路径歧义 0 条，符号漂移告警 5 条…`、
> 退出码 **0** —— 与平移前**逐字节相同**（完整输出 diff 只有不接入退出码的披露段变化）。
> 也就是说：**它能拦住「文件被删/区间越界/写反」，拦不住「整体平移」这种最常见的漂移**。
> 因此「本表行号由工具守门」只对**越界**成立；内容是否还对，仍要人看披露段里的
> `符号漂移扫描` 与 `±2 窗口符号` 两节。要变成真守门，得把符号比对升为硬判据
> （`--strict-symbols` 只覆盖漂移告警这一桶，且当前只有 5 条），那会立刻暴露
> 大量历史引用，属独立工作。**本轮只记档，未修。**

> **原文引用另有一个守门工具**：`optimizer/tools/check_theory_quotes.py`。
> AGENTS.md §3 要求「引用原文必须写清课号 + **原句**，不许转述」，但在此之前
> 那条规矩**只靠自觉** —— 而理论条目（`optimizer/theory/*.md`）是判据变更的
> 唯一依据（§5 第 1 步），里面的摘录一旦被顺手「润色」成通顺的转述，判据就
> 失去了锚，且**没有任何东西会报警**：文档照样渲染、测试照样绿。
>
> ```bash
> cd <本项目根目录> && ../.venv-chanlun/bin/python optimizer/tools/check_theory_quotes.py
> ```
>
> 2026-10-13 全量核对：**30 条引用全部逐字来自 `chanlun108/原文/`**，
> 另有 4 条仅**末尾标点**为节选边界（原文是「…逐级上推**，**然后就可以严格定义…」，
> 摘录截到「逐级上推」只能补句号）—— 那 4 条放行但**如实记一条 NOTE**，
> 免得「放宽末尾标点」悄悄涨成「随便改标点」。
>
> 它是**判据**（接退出码），并有 11 条反向对照钉住可失败性：改一个字、
> 转述、挂错课号、编造课号、省略号后接编造内容，**都必须报出来**。
> 一个「无论怎么改都是满分」的校验器没有在测量任何东西（§5 证据质量规则）。
>
> **它自己踩过两次坑，都写在 docstring 里**：① 初版用「最近出现过的课号」归属，
> 于是文档自己的行文被当成第 33 课的原文、而确属第 027 课的引用被算到第 060 课头上
> —— 两次都是解析器错、文档对；② 末尾标点的放宽必须**只放宽一个字符**，
> 否则改实词也会被放过去。**判据写错方向比没判据更坏。**

---

## 4. 外围层

### 4.1 Web（`web/`）

`web/app.py`（79 行）建 FastAPI 应用 + 端口占用检查；`create_app()` / `serve()`。
`web/api.py`（1041 行）承载全部路由。

**14 条路由**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | ok / port / data_root / periods / disclaimer / **version** |
| GET | `/api/version` | `{"version", "source"}`，不碰配置与数据库 |
| GET | `/api/universe` | `limit`（默认 200）、`q`；**只查本地品种表** |
| GET | `/api/search` | `q` `limit`（默认 20，上限 100）；**代码或名称**，含未落库的票（D-43） |
| GET | `/api/bars` | `code` `period` `limit` `adjust=qfq` `ma` |
| GET | `/api/structure` | 结构快照 + bars + macd + counts；`mode`（默认 `strict`，非法 422） |
| GET | `/api/scan` | `period=day` `date`，读 `meta.get_scan_results` |
| GET | `/api/watchlist` | 自选池 |
| POST | `/api/watchlist` | 增 |
| PATCH | `/api/watchlist` | `delta` 仅 ±1（上下移动） |
| DELETE | `/api/watchlist` | 删 |
| GET | `/api/watchlist/structure` | 自选栏批量结构；`mode`（默认 `strict`，非法 422） |
| POST | `/api/sync` | 触发同步，返回 **202** |
| GET | `/api/sync/status` | 同步进度 |

> **`mode` 与 `adjust` 是两个维度，别混。** `adjust`（`qfq` / `hfq` / `raw`）是
> **复权口径**：价格怎么还原。`mode`（`strict` / `loose`，D-32）是**买卖点口径**：
> 只放宽买卖点判据，不动笔 / 线段 / 中枢的划分。响应里两者分别回显在 `adjust` 与
> `mode` 字段。`mode` 缺省 `"strict"`，非法值由 `Query(pattern="^(strict|loose)$")`
> 挡成 **422**（不是 400、不是 500）。收 `mode` 的端点：`/api/structure`、
> `/api/watchlist/structure`。

**`/api/structure` 的 404 响应体形状**：

```python
JSONResponse(status_code=404, content={"detail": exc.detail,
                                       "syncable": blocker is None,
                                       "sync_hint": blocker or ""})
```

`syncable` / `sync_hint` 与 `POST /api/sync` 的 400 文案**同源于
`sync_blocker()`** —— "这个周期能不能同步"只有一处判据。

**结构缓存**：`_CACHE` / `_CACHE_MAX = 64`，key **不含 `limit`**（结构全史算完再裁）。

**同步任务**：`_SYNC_RUN_LOCK` 全局串行；`_SYNC_TASKS` 永不删除；
后台线程吞异常写回登记表。

**决策**：
- 端口固定 8888，被占用时启动失败并明确报错，不静默切换。
- **OpenAPI 的 `info.version` 与 `/api/health`、`/api/version` 同源**：
  `create_app()` 传的是 `src/chanlun/version.py` 导出的 `__version__`，不是字面量。
  原先这里写死 `"0.1.0"`，`pyproject.toml` 涨到 `0.2.0` 之后没跟着动 ⇒
  同一个进程里 `GET /openapi.json` 的 `info.version` 报 `0.1.0`、
  `/api/health` 与 `/api/version` 报 `0.2.0`。**实测三个端点**：
  修复前 `0.1.0 / 0.2.0 / 0.2.0`，修复后 `0.2.0 / 0.2.0 / 0.2.0`。
  这一条曾**只登记不修**（`[0.2.0]` 已知空白第 7 条），因为 `web/app.py` 当时
  不在任务白名单内；现已修，护栏见 `tests/test_version.py` 的两条新用例
  （一条静态拦「以数字开头的 `version=` 字面量」，一条行为比对 `app.version`
  与 `__version__`）。
- `/api/watchlist` **不做改名/改代码**：代码是这只票的身份，改名只是显示；
  顺序是用户唯一的排序诉求，就只做顺序（`web/api.py:800-801`）。
- 存在性判据用**落库周期**：周/月没有自己的 parquet，`store.exists(code, "week")`
  永远为假，整栏会被标成"未同步"—— 明明日线就在本地（`web/api.py:878-880`）。
- 不能同步等待：首次拉 5 分钟线实测 149 秒，浏览器会先超时。
- 拒绝要发生在**取数之前**：等 30 秒再看"没有数据"是浪费。

### 4.2 前端（`web/static/`）

单页 SPA：`index.html` + `app.js` + `styles.css`，ECharts 5.5.1 canvas 渲染。
（**故意不写行数**：这里曾写「`index.html`（118 行）……`styles.css`（约 620 行）」，
到 2026-10-03 实际已是 122 / 649 行 —— 和 `app.js` 的行数引用一样，
把会漂的数字抄进另一份文件就是会腐烂，见 `optimizer/tools/check_doc_line_refs.py`。）

**DOM 骨架**：查询表单（**代码 / 名称**）→ 周期按钮（日/周/月/30/5）→ 工具行
（**加自选** → 复权按钮 → 买卖点口径按钮 → 均线开关）→ 图层书脊
（笔/线段/中枢/买卖点/背驰/确认刻度）→ 自选左栏 → 图表 → 提示 → 台账。

**关键决策**：
- **搜票可以只打名称，加自选就在股票界面上**（2026-10-05，D-43）。
  两个输入框（顶栏 `#code`、自选栏 `#watch-code`）都带 `datalist`，
  提交时先试本地品种表，再问 `/api/search`；港美股**只有**远程这条路
  （本系统不落它们的品种表）。`CODE_RE` 必须接受字母基（`hk.hsi` / `us.ixic`）——
  旧正则只认 `[a-zA-Z]{2}\.[0-9]{6}`，港股指数连提交都过不了（`app.js:1532`）。
  `#fav-btn` 的按压态**从 `state.watch` 推**（`renderFav()` `app.js:1966-1969`），
  不自己存一份，否则自选栏删掉这只票后按钮还亮着。
  **错误提示不能复用 `showNotice`**：`#notice` 的动作槽被「同步这个周期」占着，
  而且 `showNotice` 会清空图表 —— 所以 `.tools` 里另有一个 `#fav-error`
  （`index.html:105`）。
- **404 的两种命运**：404 = 这个周期本地真没有数据。命令行提示是给终端用户的，
  看盘的人需要能点的东西。但**不是每个 404 都能补**：指数没有分钟线
  （`syncable: false`），给一个必然 400 的按钮比不给更糟 ——
  用户点了、等了、被拒，才知道这条路是死的（`app.js:223-226`）。
- **结构必须画在同一口径上**，否则"前复权的 K 线 + 不复权的笔"是两张图的叠加
  （`app.js:2086`）。
- **取数不等渲染**：`requestAnimationFrame` 在后台标签页会停摆，把它放在加载路径上
  会导致页面永远停在"尚未加载"（`app.js:2146-2153`）。
- **首屏定位只做一次**：`?code=` 优先；否则打开在自选池第一只，且只做一次
  （`initialCodePinned`）。
- 不复权帧在除权日有一根几十个点的缺口，拿它算涨跌幅会在自选栏里报出一根
  根本不存在的跌停，而图上那根 K 线看起来是平的（`app.js:1280-1281`）。
- **口径说明分两种，前端不要自己拼**（2026-10-05，D-43）：A 股无除权记录 ⇒
  「三态相同」，港美股只有原始价 ⇒ 「另两态取不到」。这两句都由后端
  `web/api.py::_raw_only_note`（`web/api.py:210-221`）给出，前端只负责显示。
  `app.js:309-312` 附近曾有一句把 A 股口径当**通用**口径的注释，
  与后端「另两态取不到」的说法相反 —— 已同批改掉（AGENTS §5 第 5 步）。
- **自选栏页脚不再写「七个大盘指数」**（2026-10-05，D-43）：默认池是 11 个。
  护栏 `tests/web/test_frontend_watchlist.py::test_watch_foot_no_longer_claims_only_day_bars_are_local`
  —— 改成断言新文案 + 断言旧文案**不在** html 里，在修复前的 html 上实测红。
- **顶栏那一行恒定、详情行恒定两行**（2026-10-03）。日/周/月切换时 `.topbar` 的高度、
  `.period` 的尺寸、`#stamp` 的行数**都不许变** —— 变一点下面整张图就跟着上下跳。
  于是：
  - `#stamp` 只放**恒定 2 行**的短信息（代码/名称/周期/截至 + 线段/中枢/买卖点计数）。
  - 「全史」计数与口径说明走**顶栏之外**的 `#stamp-detail`（`index.html` 里紧随
    `</header>`，全宽独立一行）。该行也恒定 2 行：**第一行** = 全史计数 + 周期来源，
    **第二行** = 口径。周期来源（「周线由日线聚合」）必须并进第一行 ——
    留在第二行会给口径加一个随周期长短不同的前缀，窄屏下折行数就不一样。
  - **顶栏里除 `.stamp` 外每一格都是 `flex: none` + `white-space: nowrap`**
    （`.brand` / `.field` / `.periods` / `.period` / `.go`）：中文一旦被压窄就**逐字竖排**，
    而不是缩排。实测（2026-10-03）`.go` 缺 `flex: none` 时被挤成 `58.3 × 78px`
    （「看结构」竖着排），`.brand` 被挤到 65.8px 时「缠论」上下叠 —— 且这在
    **≥900px 的每个宽度都存在**，不是窄屏专有。
  - **挤压力只允许落在 `.stamp`**：它是纯文本，自己折行是自然的。`.stamp` 用
    `flex: 1 1 380px; min-width: 380px`，380px 是实测阈值（低于它，
    「线段 10 (确认 9 / 未确认 1) · 中枢 2 · 买卖点 7」会折成 3 行把顶栏撑高）；
    `.topbar` **全局** `flex-wrap: wrap`，`.stamp` 自己决定何时整条掉到第二行。
    ⇒ **不再需要手算断点**：早期那个 `@media (max-width: 860px)` 补丁已删除。
  - **`body` 的行模板：弹性行必须是最后一轨**。`body` 是 grid，行数必须等于
    **会产生盒子的子元素**个数（`<script>` 是 `display: none`，不占轨道），
    且那条 `1fr` 只能落在工作区（`.work`）那一格。2026-10-03 往 `body` 插
    `#stamp-detail` 时行模板写成了 `auto auto minmax(360px, 1fr) auto` ——
    **轨道数正好也是 4，数目对得上**，但 `1fr` 落在第 3 轨（免责声明）上，
    工作区只拿到 `auto` 并被挤进隐式行，免责声明下面凭空多出 245px 空白。
    这类错位不会横向溢出（`documentElement.scrollWidth` 仍等于视口宽），
    只能靠护栏拦。
  - **≤980px 时 `.stage` 必须显式排在书脊右边那一列**：那一档 `.watch-rail` 抢了
    `grid-column: 1 / -1`（整行），auto-placement 游标被推到下一行，`.stage` 会落进
    46px 的书脊列 —— 实测 900px 时 `#chart` 只有 46px 宽、书脊右边 854px 全空。
    修法是在 `@media (max-width: 980px) and (min-width: 761px)` 里用 `order`
    把 `.stage` 提到 `.watch-rail` 之前（auto-placement 走 order-modified 文档序）。
    **不碰** 760px 那一档（那里只有一列，自带另一套排布）。
  - **不动** 760px 断点的 `.work` / `.rail` 布局。
  实测（`docs/evidence/2026-10-03-topbar-layout-probe.mjs`，无头 Chrome 真机，禁缓存）：
  9 个宽度 1600→800 × 3 个周期 × 2 个口径，`.period` 恒 `59.8×36`、`.periods` 恒 `298.6`、
  无裁切、无横向溢出，顶栏高与详情行高在**每个宽度下三个周期完全相同**。
  回归护栏：`tests/web/test_frontend_watchlist.py::test_topbar_stamp_is_pinned_to_two_short_lines`、
  `::test_topbar_controls_cannot_be_squeezed_into_vertical_text`、
  `::test_body_grid_rows_cover_every_body_child`。

**localStorage 键**：`chanlun.ma` / `chanlun.adjust` / `chanlun.watch-collapsed`。

### 4.3 扫描与自选池（`scan/`）

`scan/scanner.py`（649 行）：`Requirement` / `REQUIREMENTS`（min_bars 250/480/480/480，
lookback_days 365/240/84/14）；`check_sufficiency()` 三条判据；
`hit_strength()` = 类型权重 × 级别权重 × 状态权重 × **区间套权重** × (1 + 背驰强度)。

- **为什么乘而不加**：它们是**独立**的确认维度（类型=结构含义、级别=影响范围、
  状态=是否已确认、背驰面积=力度衰竭、**区间套=是不是大级别转折点的精确定位**，
  见 D-54）。乘法保证任一项为 0（未确认）时整体归零，且每项都能在 `strength_parts`
  里单独核对（`scanner.py:233-255`）。
- **数据质量提示不挂在行上**：没有买卖点的票不产生任何行，若警告只跟着行走，
  "元数据声称 1991 年、本地文件 2020 年起"这类问题会被静默丢掉
  （`scanner.py:337-339`）。
- **为什么用面积比而不是 DIF 高度**：原文第 24 课看的是这波走势的"力度"，
  面积是对整段走势积分，比单点极值更能反映能量（`scanner.py:381-386`）。
- `scan_report()` 默认 `periods=("day","30","5")`、`max_workers=8`；
  进程池失败退回串行。
- `save_report()` **不持久化** skip/error。

`scan/watchlist.py`（320 行）：`SNAPSHOT_VERSION = 1`；
`TrackChangeKind` = FIRST_SNAPSHOT / NEW_PIVOT / PIVOT_BREAKOUT / SIGNAL /
DIVERGENCE / NO_CHANGE；失效中枢不入指纹；**刻意串行** ——
自选池通常只有几十只，串行的开销远小于多进程的复杂度
（`watchlist.py:249-252`）。
- **`--codes` 分支的名字也要兜住指数**（2026-10-05，D-43 收尾）：
  `codes=None` 时名字取自自选池表，而 `--codes` 早先只查 `meta.get_universe`，
  指数不在品种表里 ⇒ `scan --watch --codes hk.hsi` 的跟踪行（以及
  `--notify-file` 写出的推送文案）只剩一串代码。现在该分支按 `meta.name_of`
  的优先级取名（品种表 → 自选池 → 内置指数名，`watchlist.py:266`），护栏
  `tests/scan/test_watchlist.py::test_track_codes_path_also_resolves_builtin_index_names`
  **在修复前的输入上实测红**。

### 4.4 回测（`backtest/`）

- **时点保证**：每个 bar 循环内用 `store.read(code, period, end=bar.ts)` 做
  **物理截断**（`runner.py:211-217`，`:217` 有「★ 物理截断」标注）；
  `_view()` 只保留 `confirmed_at <= as_of` 的已确认对象。
- **T+1**：`begin_bar()` 交易日变化才结转可卖；`execute_open()` 入口**再调一次**，
  使"当日买当日卖"不可能漏网（`broker.py:272-297, 283-285`）。
- **默认不允许滑点**：本项目没有支撑滑点取值的证据（`broker.py:197-201`）。
- **涨跌停**按号段：300/301/688/689 → 0.20，4/8 → 0.30，其余 0.10。
  **ST 5% 未实现**（docstring 自陈）。
- 佣金 `max(2.5e-4 * amount, 5.0)`，印花税卖出单边。
- 基准 `DEFAULT_BENCHMARK = "000300"`；缺基准只记 note，**绝不静默替代**。
- 成交为 0 时给出「引擎缺陷 G1」提示。
- 指标：赔率不可得时给 `None` 而非 `inf`。

### 4.5 优化器（`optimizer/`）

**状态说明**：代码里**没有任何"优化器已停用"的标记**。它是**活跃但只提案**：
`Optimizer.__init__` 在 `propose_only=False` 时直接 `ValueError`；
CLI `--propose-only` 默认 True 且帮助文本写"默认且唯一支持的模式"。

> **当前使用状态**：2026-10-01 用户明确指示**不跑这个优化器**。
> 即代码是活的，但**运行**被暂停。恢复需要用户明确指示。

- **绝不改主干**：只写 `patches/` 与 `journal/`，等人工确认后自己 `git apply`。
- **理论约束**：改缠论算法的唯一许可证是引用 `theory/` 条目 id 并把原文摘录
  **逐字**贴进补丁头，且机器可校验。不允许改写或编造原文摘录。
- **`kind` 由补丁碰了哪些文件决定**，不由作者自称决定。
- **冻结行情**：`data/` 是活仓库（实测同一把尺子在两条命令之间就从 24/24 只票
  掉到 22/24，bars 39240 → 35970），冻结后一轮里的两次测量读同一份字节。
- **实测数字逐项相同则强制改记 `inconclusive`**。
- **熔断**：非零退出码 3 + `circuit_break` 记录，配合 systemd
  `RestartPreventExitStatus=3`（禁止自动重启、必须人工复核）。
- 审计口径写死：`AUDIT_CODES` 24 只龙头、`AUDIT_START="2020-01-01"` /
  `AUDIT_END="2026-09-29"` —— "换股票或换窗口就等于换了口径"。
- `theory.py` 遇到**重复 id 抛 ValueError**（宁可起不来）。
- `web/static/` 是纯展示层被豁免，但服务端 `web/api.py` / `web/app.py` 仍算算法路径。

### 4.6 配置与通知

`config.toml`（18 行）全部配置项：

```toml
periods   = ["day","30","5"]
bs_adjust = "2"
[web]  host = "127.0.0.1"   port = 8888
[data] root = "data"        source = "baostock"
```

`config.py` 用 `tomllib` 加载，**校验方式仅为 dataclass 构造** ——
未知键会由 `WebConfig(**...)` 抛 `TypeError`，**没有**独立的 schema 校验或取值域
检查（端口范围、`periods` 合法性在 `load_config` 内不查，后者由
`__main__.py:1104-1109` 用 `SystemExit` 兜住）。

- `source = "tdx"` 会改变落库口径，且此后**无法再由库内数据反推除权因子** ——
  先跑 `factors` 再切。

`notify.py`：`Notifier` 协议只有一个 `send(title, body)`；
出口 `ConsoleNotifier` / `FileNotifier` / `NullNotifier` / `MultiNotifier`。
**模块内不做任何网络调用** —— "真实交易环境里扫描结果属于敏感信息，
默认不应该离开本机"。未知出口名**报错**而不是静默退回控制台。

---

## 5. 已知空白与未实现

| 项 | 状态 | 位置 |
|---|---|---|
| 盘整背驰（第 24 课，一个中枢前后两段比较） | **未实现** | `signal.py:25-27` |
| 第一类买卖点区间套定位（第 27/28 课，多级别联立） | **未实现** | `signal.py:30` |
| 第二类买卖点下钻次级别 | **近似**，用本级别下一段代替 | `signal.py:28-29` |
| 九段式中枢（离开后又回到中枢） | **未实现** | `pivot.py:48-49` |
| **段数到顶的中枢没有升级为更大级别的中枢** | **未实现**。D-41 起 `capped` 中枢在信号层整条跳过，**不再**产出买卖点/背驰；原文的做法是升级成更大级别的中枢，而 D-51 的级别递归**尚未接进信号层**（只到 `chan/recursion.py`） | [D-41](#d-41-段数上限掐停的中枢pivotcapped在信号层必须整条跳过删除-third_tol)、[D-38](#d-38-段数上限掐停的中枢里没有离开段pivotcapped-记封口原因)、[D-51](#d-51-级别递归-f2-落地中枢由次级别走势类型构造级别不再等同于行情周期) |
| **级别递归已接进 `Snapshot` 与 `/api/structure`，但 `scan`/`backtest` 还没消费它** | **2026-10-13 大部分落地**。`Snapshot.levels` 是派生字段（见 D-51）；`/api/structure` **自动出口**（`structure_payload` 走 `to_jsonable(view.snap)`、`asdict()` 递归，`web/api.py` 一行没改），终端另有 `python -m chanlun levels --codes sh.600030 --period 5`。`levels` 是**唯一不随显示窗口裁剪**的字段（裁了必然三级全空）。**仍未接**：`scan`/`backtest` **判定**时不消费 `levels` —— 要用它就得改买卖点判据（自然入口是「第一类买卖点区间套」，见本表另一行）。2026-10-13 已补的是**只读上下文**：`ScanHit.level_context`（如「L1/30 上涨走势、L2/day 中枢内」）+ 摘要里的 `levels`/`max_level_depth`；有一条边界用例把 `levels` 抽掉后要求命中逐项不变。另外 `find_pivots` 的 `GG/DD` 在**走势类型**路径上取「组内第一个有方向的段」来定 `zn` —— 这是**确定**的选择，但原文的 A、C 同向假设以「相邻次级别方向交替」为前提，而实测相邻同向率是 53%（L0）/ 75%（L1），所以它不是能从原文直接推出来的选择。走势类型的 `high`/`low` **跨度约定**同理：换约定会让 L1/L2 的中枢数变（实测逐级相同 9/17），现状选「连续 bar 跨度收录连接走势」，与第 35 课「所分别经过的价格区间」的字面读法一致，但没做过独立的择优论证 | [D-51](#d-51-级别递归-f2-落地中枢由次级别走势类型构造级别不再等同于行情周期)；实测 `optimizer/tools/measure_recursion.py` |
| **分钟基准现已可得（2026-10-13 晚由 pytdx 解决）；仅申万仍无源**（口径按当日实测分档；**1 分钟：股票 23520 根 / 指数 22320 根，约 4.5 个月、到当日收盘**）| **未解，2026-10-13 穷举实测（6 个源 / 7 个组合）**。递归要有产出基准必须是分钟级 —— 已证：把 1023 根（1 个月）5 分钟数据喂进去，递归只出 L0 的 **1–2 个中枢、出不了 L1**（实测 `sh000001` / `sz399001` / `sh600030` 三个标的都是「1 中枢 1 走势」）。逐个源：① **baostock**：`MIN_PERIODS = {60,30,15,5}`，**没有 1 分钟**（报 `10004012 请求数据类型不正确`）；5 分钟**只给股票**，对 `sh.000001` / `sh.000300` / `sz.399001` / `sw.801010` 一律返回 **0 行**。单只完整拉取（78672 根）实测 **85 秒/只** → 5471 只 ≈ **129 小时**；中途会出 `10002007 网络接收错误`（首次 3 只只成 1 只），被截断的会记成水位、下次增量自动续上，所以**可续跑但很慢**。② **腾讯 `mkline`**：`datalen` 上限 **320 根**（要 2000 仍回 320）；股票与指数一样，m1 ≈ 1.3 个交易日、m5 ≈ 6.7 个交易日。③ **新浪 `CN_MarketDataService.getKLineData`**：**唯一能给指数分钟线的源**（实测 `sh000001` / `sz399001` 的 scale=1 与 scale=5 都有数据），但 `datalen` **硬封顶 1023**（要 2000 直接回 `null`）→ 5 分钟 ≈ 1 个月、1 分钟 ≈ 1 周；对**申万行业返回 `null`**（`sw801010` / `sh801010` / `801010` 三种写法都试过）。④ **东财 `push2his`（2026-10-13 更正，并已接入）**：此前记「连接被拒」是**错的** —— 被拒的是 **`https://`**，换 **`http://push2his.eastmoney.com`** 就通（`https://` 一律 `RemoteDisconnected`，`http://` 正常返回 JSON）。它覆盖**股票与 A 股指数**。**但它并不覆盖申万** —— 一度以为 `secid=90.801010` 是申万并据此导入了 96 个文件，**那是错的**（`90.` 是东财自己的板块空间，同号不同物：`90.801010` 实测回「磷化工」而不是「农林牧渔」，见 D-52 被否决方案 5；数据已清理并加了护栏）。判断 secid 是否对得上**只能核对 `data.name`**：`1.000300`→沪深300、`1.000688`→科创50、`0.399001`→深证成指，实测 6/6 全对。但**分钟历史只有约 2 个月**，而且是**服务端保留边界、不是翻页限制**：实测 `klt=5` 在 `end=20260824` 回 96 根、`end=20260821` 回 **0 根**；对照 `klt=101`（日线）`end=20240101` 能翻回 2003-01-06，证明翻页机制本身是好的。单次返回上限实测：5 分钟 ~1536 根、15 分钟 ~512、30 分钟 ~248、1 分钟 240 根（=1 个交易日，且 `end` 对它无效）。**它已经接进取数链路**（D-52）：`sources.source_for` 把「**A 股指数** + 分钟」分流到东财，其余仍走原源 —— 因为东财只有 2 个月而 baostock 对股票有 6.5 年，不能为了统一把好的换成差的。**申万不在分流之列**（没有源；错误映射已删并加护栏）。实测接入后：`sh.000300` / `sz.399001` / `sh.000688`（科创50）等 A 股指数的 5 分钟各 **1536 根**，**此前全是 0 行** —— 科创50 此前连日线都只能绕过 baostock 另找源。⑤ **`pytdx`（2026-10-13 晚**彻底反转**，此前记「协议不兼容」是错的）**：把 `pytdx.config.hosts.hq_hosts` 的 **104 台服务器逐台实测**，**8 台活着**（国泰君安 `117.34.114.13–.27:7709` 段），其余是死的。**当时试到的恰好都是死的，于是把「服务器死了」误判成了「协议不兼容」** —— 这个误判让 1 分钟数据被判为「无源」**长达十轮**。教训：**把「试了几个都失败」写成「协议不兼容」之前，先确认样本量够**。实测深度（全部到当日收盘）：
  - **1 分钟：股票 23520 根、指数 22320 根（约 4.5 个月）** ← 此前**没有任何来源**；
  - 5 分钟：股票 23808 / 指数 23568 根（约 2 年）；
  - 日线：股票 8468（1991 起）/ 指数 8740（1990 起）根。
  一只 1 分钟约 **1.5 秒**（28 页 × 800 根），比 baostock 的 85 秒/只快约两个数量级。
  **已接入**（见 D-53）：`sources.source_for` 把「**1 分钟**（沪深全部）」与「**指数的分钟**」分给 pytdx；股票的 5/15/30/60 仍走 baostock（6.5 年 > 2 年）。
  两个坑：**指数要用 `get_index_bars`**（`get_security_bars` 对 `market=1` 字段错位，给出 `2004-11-41`、`236757-22-72` 这种时间戳，**而且不报错**）；**页内 `[0]` 是最早的一根**（升序），用 `bars[-1]` 判断会让翻页立刻退出。
  **口径只有不复权**（raw），要前复权先跑 `factors` 反推因子表（D-19）。⑥ **通达信 `.lc1`/`.lc5`**：**2026-10-13 已实现**（原 D-09「故意不实现」已被推翻，见该条变更历史）。但**取数路径同日晚上被证伪**：iMac 版**没有 vipdoc** —— 把官方 `.dmg` 只读挂载检查，包内**没有任何** `vipdoc`/`fzline`/`minline`/`.lc5`/`.lc1`/`盘后数据` 字面量（4 种编码 × 全部 >200KB 文件）。它**有 `导出`（CSV/txt）**，所以 macOS 上要拿本地 K 线只能**导出文本再导入**。**解析器本身可用（等样本），但「盘后数据下载 → vipdoc」这条路不存在。****日期编码仍是待核对假设**，但落库**已加硬门**：`import_dir` 在分钟周期上先跑日线交叉验证，过不了就不写。本地 `data/tdx_raw` 只有 12449 个 `.day`（无分钟样本），所以「对着真实样本核对」这一步仍在等数据。⑦ **雪球**（2026-10-13 试）：**不可用**。首页能拿到 `xq_a_token`（WAF 的 `acw_tc` 之外另发的），但 `stock.xueqiu.com/v5/stock/chart/kline.json` 同一组参数**时而 200 空数据、时而 400**，反爬不稳定 —— 不能进生产管线。**1 分钟的分档结论**：**股票无源**（baostock `MIN_PERIODS={60,30,15,5}`，传 `1` 报 `10004012`；腾讯 320 根；新浪 1023 根）—— `capabilities(股票,"1")` 现在如实回 `supported=False`；**指数有源但只有 1 个交易日**（东财 240 根，且 `end` 对它无效）。**后果（2026-10-13 晚更新）**：**1 分钟基准已实测跑通** —— `sh.600030` 23520 根 → L0/1 25 中枢 18 走势｜L1/5 3/3｜L2/30 1/0；`sh.000300` 22320 根 → L0/1 35/24｜L1/5 6/5；`sz.000001` 22320 根 → L0/1 11/7｜L1/5 2/1。**股票与指数都行，3 只共 5 秒。**另：以日线为基准时 **95% 的票只出 1 级**（400 只沪市：380 只 1 级 / 20 只 2 级）；自选池 13 个标的里**只有 2 只 A 股股票**（`600759` / `002309`）能拿分钟基准 —— 已补，实测 3 级（L0/5 各 38 与 64 中枢 → L1/30 各 4 与 6 → L2/day 各 1）。**7 个 A 股指数自 2026-10-13 起已可得**（改走东财，各 1536 根 5 分钟，见 ④）；**恒生/恒生科技与道指/纳指仍不可得**（腾讯港股只有日线、美股走新浪且封顶 1023 根） | [D-51](#d-51-级别递归-f2-落地中枢由次级别走势类型构造级别不再等同于行情周期)、`docs/superpowers/specs/2026-10-12-level-recursion-f2-design.md` §7 |
| **`level` 仍等于行情周期** | **未改（D-51 显式保留）**。第 63 课（「什么级别的图和什么级别的中枢没有任何必然关系」）与第 84 课（「级别，本质上与时间无关」）都否掉这个等式，但 `ChanEngine.__init__` 的 `self.level = level or period` 与 `Snapshot.level` 的落库口径绑在一起，纠正它属于独立变更 | `engine.py:143`；[D-51](#d-51-级别递归-f2-落地中枢由次级别走势类型构造级别不再等同于行情周期) |
| 通达信分钟格式（`.lc5`/`.lc1`） | **2026-10-13 已实现**（原 D-09「故意不实现」已被推翻，见该条变更历史）。数据来源是**通达信 iMac 版**的「盘后数据下载」落下的本地 vipdoc 目录（`{market}/fzline/*.lc5`、`{market}/minline/*.lc1`）—— 这条路的关键价值是 **vipdoc 里指数与股票同目录**，而指数此前没有任何分钟源。**日期编码仍是待核对假设**，而落库**已加硬门**：`import_dir` 在分钟周期上先跑 `verify_minute_against_day()`（参照取 vipdoc 同口径的 `.day`，退而取本地库里 `adjust == "raw"` 的日线），过不了就不写，计进 `ImportStats.unverified`；逃生门 `--allow-unverified`。**没有任何数据在未经验证的情况下进过库** | `tdx.py:86-107`（`minute_ts`）、`tdx.py:109-148`（`parse_minute_bytes`）、`tdx.py:163-194`（交叉验证） |
| ST 5% 涨跌停 | **未实现** | `broker.py` docstring |
| 滑点 | **未实现**（无证据支撑取值） | `broker.py:197-201` |
| `markets.py:45-47` 提到的"7 开头申购/配售代码" | 仅注释提及，**代码无显式常量** | `markets.py:45-47` |
| baostock 分钟数据 2020-01-02 起点 | 注释中的实测结论，**代码无强制校验** | `baostock_source.py:3-4` |
| `tdx.BOARD_RULES`（通达信自定义板块/指数） | 注释直写"**语义未核实**" | `tdx.py:267` |
| 特征序列包含处理的合并方向 | **原文空白项**，显式实现约定 | `segment.py:15-18` |
| 包含处理首两根 K 线的方向 | **原文空白项** | `include.py:30-33` |
| 被截断窗口的**左端起段** | **原文空白项**，工程判据；D-40 起取**最早的前缀上第一个可行的起点**（D-30 的"能确认线段最多"口径已作废） | [D-40](#d-40-线段划分改为前缀因果缺口确认只看已出现的笔左端起段取最早的可行起点)（原 [D-30](#d-30-被截断的左端起段取还能确认最多线段的起点)） |
| **行号校验器看不见整文件漂移**（2026-10-04 **已修**） | 旧硬判据只有「文件存在 / 行号在范围内 / 区间不倒置」，对整文件插入造成的行号漂移**在退出码上完全无反应**：在 `segment.py` 顶部插 15 行 ⇒ 通过 185 / 失败 0 / 退出码 0 三项全不变（只有披露桶跟着动：首末行空行 12 → 15、漂移扫描里 `def@231` → `def@246`）。一个天真的修法实测**没有任何信号**（`.py refs=186 / with_own_line_anchor=0 / drifting=0`）。现改为**记住每个区间当初那几行的内容**：`optimizer/tools/doc_refs_baseline.json` 随仓库提交、`--update-baseline` 重记，同一扰动下 **不符 26 条 / exit 1**，而「只改文档」「只在文件末尾追加」都仍是 0 条不符 | `optimizer/tools/check_doc_line_refs.py`、`optimizer/tools/doc_refs_baseline.json` |
| **非 `.py` 引用（html / css / js / md / toml）没有任何护栏**（2026-10-06 **已修**） | 指纹基线原先只覆盖 `.py` 桶（`report.py_refs()`），那个 39 处 / 31 行的非 `.py` 桶**只枚举、不判绿、不进基线** —— 被引文件一插行就集体指错而**全套测试仍全绿**。D-44 给 `index.html` / `app.js` 插行后照出 24 处（`index.html` 的 `85-87` 行 → 实为 `97-100`、`app.js` 的 `1455-1466` 行 → 实为 `1523-1526`、`ARCHITECTURE.md` 的 `648` 行 → 实为 `943`、D-35 的 7 处自引用整体漂 +297 …），其中大多数**在 HEAD 上就已经是错的**。现改为指纹覆盖**全部**引用（`FINGERPRINT_BASELINE_VERSION` `1 → 2`，265 → 305 条），反向对照见 `tests/optimizer/test_check_doc_line_refs.py::test_fingerprint_baseline_covers_non_py_refs_too` | `optimizer/tools/check_doc_line_refs.py`、`optimizer/tools/doc_refs_baseline.json` |
| **走势类型不上看盘页** | `/api/structure` 不返回 `trends`，ledger 无走势类型分节；趋势判据只通过**第一类买卖点**体现 | [D-16](#d-16-趋势判据严格分离三分支) |
| `pivot.py` / `trend.py` docstring 里的实测数字 | 记的是**旧引擎**（D-30/D-31 落地前）的数：`trend.py:84` 写「22 个相邻中枢对」，实测已 28 | [D-15](#d-15-zgzd-与-ggdd-的口径以及区间不可混用) |
| **`store.read` 的 `start` / `end` 只做字符串比较，不解析日期** | **已登记的已知限制**（D-43）。`src/chanlun/data/types.py::truncate`（`src/chanlun/data/types.py:106-116`）拿 `ts` 当纯字符串比：① 带 `:SS` 的分钟 `ts` 字典序**大于**裸 `YYYY-MM-DD` 上界，会被静默丢掉；② 即便 `ts` 只有 16 字符，用日期做 `end` 也会丢掉**当天**的分钟 bar（实测 `'2026-10-05 09:30' <= '2026-10-05'` 为 `False`）—— 这一条对 A 股同样成立，**先于 D-43 就存在**。今天 `store.read(start=/end=)` 的调用点只有 A 股日线口径（`optimizer/agent.py:98`、`backtest/runner.py:127/182/217`），没有活调用者踩到，故只登记不改 | `src/chanlun/data/types.py:106-116` |
| **行业弹窗的「空选」没有兜底** | `pickIndustry(code)` 里缺 `if (!code) return;`（D-44 收尾评审 2026-10-06 登记）。今天所有入口都会传一个真代码，**不可达**；但它是将来所有「选行业」入口的漏斗 —— 兜一句比事后查便宜 | `app.js::pickIndustry` |
| **行业搜索路径里写死了一级 / 二级** | 分组标题 `industryGroupTitle` 写死「一级行业」/「二级行业」，`renderIndustryGrid` 的分组遍历写死 `["1", "2"]`；而 tab 条已改为由 `body.levels` 驱动（D-44 收尾评审 2026-10-06 登记）。今天后端恒回这两级，**不可达** | `app.js::industryGroupTitle`、`app.js::renderIndustryGrid` |
| **`pyproject.toml` 已到 `0.7.0`，`CHANGELOG.md` 却没有 `## [0.7.0]` 段** | 最新版本段仍是 `## [0.2.0] - 2026-10-03`，`0.3.0` 起全部堆在 `[Unreleased]`；`[Unreleased]` 顶上那句「本节改动尚未提交」也因此不再是字面事实。**先于 D-44 就存在**的仓库级记账欠债（D-44 收尾评审 2026-10-06 登记）—— 补它要一次性把 `[Unreleased]` 的条目分派到各版本号，属独立任务，不在 D-44 里做 | `pyproject.toml`、`CHANGELOG.md` |
| **自选股「读取失败」的文案会被后到的 `renderWatch()` 盖成「自选股是空的」** | **既有问题，D-45 期间量到、未修**（2026-10-06）。`load()` 会调 `renderWatch()`（`app.js:247`、`app.js:258`），它无条件 `box.innerHTML = ""` 再从 `state.watch` 重画；启动时 `load()` 先于 `loadWatch()` 发出，后到的 `renderWatch()` 会把失败文案替换掉 ⇒ 用户看到的是「自选股是空的」，而真相是**读不出来**（`state.watch` 仍是 `[]`）。**「不知道」与「没有」长得一样**，与 D-44 页脚那次同类。**先于 D-45 就存在**：探针的 `hist` 在用旧写法的场景 B 里顺序完全相同。修它要动 `renderWatch()` / `setCode()` 的调用时序，超出 D-45 范围 | `app.js::renderWatch`、`app.js::setCode`；实测见 `docs/evidence/2026-10-06-自选股错误文案当文本渲染.md` §5 |
| **磁盘上仍有 165 行非正价格（`open/high/low/close` 有一项 ≤ 0）** | **既有问题，D-46 期间量到、未修**（2026-10-06）。这些行写在「非正价格 ⇒ 剔除」这条判据**落地之前**，所以它们躺在 parquet 里：`sh.600759` 5 分钟 142 行 / 30 分钟 22 行、`sz.300114` 日线 1 行。`store.read` 走 `normalize`，读出来时被静默剔除 ⇒ **用户看到的结果是对的**，只是每次读都在重算一遍，且库里的行数与实际可用行数不一致。修它要做一次存量回写（`normalize` 后 `store.write`），属于数据迁移，不是判据变更 | `data/types.py::normalize`；全库实测见 `docs/evidence/2026-10-06-行情结构不可能的行.md` §2 |
| **launchd 的日历触发之间没有互斥，也没有 watchdog** | **未修，D-47 期间登记**（2026-10-08）。`deploy/com.chanlun.daily.plist` 是纯日历触发：没有排他锁、没有 watchdog、没有 `ExitTimeOut`。一个还在跑的实例会和第二天 17:30 的触发**重叠** ⇒ 两个进程同时写 `meta.db` 与同一批 parquet。D-47 把「跑不完」的概率压低了（单只 93 秒上限 + 连续 20 只停批），但**没有消除重叠的可能**：数据源「慢而不断」、或每只都卡在超时边界上，仍可能跑过 24 小时。这一条要真修得加进程锁（`flock`）或 watchdog，属于部署层改动 | `deploy/com.chanlun.daily.plist`；实测见 `docs/evidence/2026-10-08-定时任务静默挂死.md` §7 |
| **定时链路只跑 `--periods day`** | **设计如此，D-47 期间确认并登记为覆盖范围**。baostock 是单会话串行 socket，全市场日线本身就是 2~6 小时的量级（实测约 1.5~2 s/只 × 5471 只），再叠 30/5 分钟周期必然撞上第二天开盘。30/5 分钟周期**只能手动补** | `deploy/com.chanlun.daily.plist`、`deploy/README.md` |
| **「数据源整体静默」在同步摘要里看不出来** | **未修，D-48 期间登记**（2026-10-09）。零行一律记 `skipped`（D-48 之后仍然如此，因为 212 只退市票的正常形状就是零行），所以一个**对所有票都返回零行**的数据源，摘要是「跳过 N 只」——**和「正常跑完、只是里面有一批退市票」完全一样**。要认出它必须看**批量口径**（本次零行占比 / 零行是否覆盖了本地本来有数据的票），逐票口径做不到。D-47 曾据「零行」推断「会话是活的」，该推断已被 [D-48](#d-48-零行同步不许抹掉已有水位) 证伪 | `src/chanlun/__main__.py:739-742`；实测见 `docs/evidence/2026-10-09-零行同步抹掉水位.md` |
| **212 只退市 / 长期停牌票每次全量同步都要白跑一遍** | **未修，D-48 期间量到并登记**（2026-10-09）。全市场同步时这 212 只每只都要走完「2 次重登重试 → 零行」的完整流程，实测 **~5.4 s/只 ≈ 19 分钟**（235 只补跑实测 `97/235 [07:33<12:29, 5.43s/只]`）。库里既没有「已知死票」缓存，也没法靠 `universe.delisted` 认出来（该列未维护，见 [D-48](#d-48-零行同步不许抹掉已有水位) 替代方案 4） | `src/chanlun/data/sync.py:79-123`；实测见 `docs/evidence/2026-10-09-零行同步抹掉水位.md` |
| **行号校验器只扫 `ARCHITECTURE.md`，其它文档里的引用从来不被扫描** | **未修，D-48 期间量到并登记**（2026-10-09）。`optimizer/tools/check_doc_line_refs.py` 是「`ARCHITECTURE.md` 行号引用校验器」，`--doc` 默认且**实际只传** `ARCHITECTURE.md` ⇒ `CHANGELOG.md`、`docs/evidence/*.md`、`docs/superpowers/specs/*.md` 里的 `path:N` 引用**既不进硬判据、也不进指纹基线**，被引文件一插行就静默腐烂（这比上面「非 `.py` 引用没有护栏」那条更靠前：那条至少还有**位置**落在 `ARCHITECTURE.md` 里）。**活样本**：`docs/superpowers/specs/2026-10-03-topbar-stable-layout-design.md` 里那句「`tests/optimizer/test_check_doc_line_refs.py` 第 481 行钉着 `app.js` 的行数」**在 HEAD 上就已经是错的** —— 被引测试里的注释块早被 D-41 起的改动推到第 717-718 / 756-761 行（这里**故意不写可解析的 `path:N` 形式**：那是个**反例**，不是一条真引用，写成可解析形式会被指纹基线当真引用钉住）。本次只修了这一处活样本（改成测试函数名，不再随插行腐烂），**没有扩大扫描范围** | `optimizer/tools/check_doc_line_refs.py`；实测见 `docs/evidence/2026-10-09-零行同步抹掉水位.md` §7 |
| **baostock 会封账号 / IP，封了之后当天剩下的票就都拿不到** | **未修，2026-10-09 实弹暴露**（D-47 / D-48 上线后的第一轮定时任务）。那轮跑到 **268 只**成功时源开始回 `登录失败: 10001011 黑名单用户，请与管理员联系`，连着 **20 只**失败触发了 D-47 的停批（停在 `000802`）⇒ **剩下 5203 只当天没有同步**：库里 `end_ts = 2026-10-09` 的只有 **268** 只、**4955** 只仍停在 `2026-10-08`，交叉源校验也因当天数据缺失而抽样 40 条失败 40 条（launchd 记到的退出码是 **1**，符合 D-47 的设计）。当晚 21 点 43 分用 `bounded_login()` 单次探测**仍回 `10001011`** ⇒ 补跑不能立刻做，越试越糟。库里没有退避 / 限速 / 断流识别：`RETRIES` 是固定 3 次立即重试，零行票每只还要额外重登 2 次（**既有**行为）；全市场约 5471 只 × 每只 1~3 次登录是不是就是触发黑名单的原因，**尚未确认** | `src/chanlun/data/baostock_source.py`、`deploy/com.chanlun.daily.plist`；那轮的摘要行原样见 `docs/evidence/2026-10-08-定时任务静默挂死.md` §8 |
| **定时任务对自己的覆盖率没有任何报告** | **未修，D-49 期间量到并登记**（2026-10-10）。2026-10-09 那轮因源回黑名单只跑到 268 只就停批，其余 **4955 只停在 `2026-10-08`**；而摘要当时只写了「成功 268 失败 22 跳过 35」和一句「连续 20 只失败，停批」，**没有任何一行说"全市场还差 5203 只"**（停批只报连续失败数，不报剩余量）。**注意缺口本身会自愈**，不要把它说成永久空洞：`start_for()` 的起点是 `next_trading_day(本地末根)`（`src/chanlun/data/sync.py:58-59`），所以下一次成功的增量会把 `2026-10-09` 一起补上。真正的空白是**事后确认** —— 库里没有一个口径能回答「某一天全市场到底拉全了没有」，本次只能手工数 parquet 的末根日期（`2026-10-08` 4955 / `2026-10-09` 268，共 5471） | `src/chanlun/data/sync.py:43-59`、`src/chanlun/__main__.py`；实测见 `docs/evidence/2026-10-10-按需拉取验收.md` |
| **定时任务对 7 个 A 股指数与 4 个港美自选是零覆盖** | **2026-10-13 已修**（原为 D-49 期间确认并登记的覆盖范围，2026-10-10）。旧行为：`daily` 的同步步骤只遍历 `universe`（5471 只 A 股），自选池里的 `sh.000001` / `sh.000300` / `hk.hsi` / `us.dji` 等**一天都没被定时任务碰过**。实测 2026-10-10：7 个 A 股指数全部停在 `2026-09-30`，`hk.hsi` / `hk.hstech` 停在 `2026-10-05`，`us.ixic` 停在 `2026-10-02`。**修法**：`_resolve_codes` 的全市场分支改走 `_market_scope()` = A 股全市场 **+ `DEFAULT_WATCHLIST` 的 11 个指数 + 申万全部 155 个行业板块**（去重保序），范围 5471 → 5637。**分钟周期的范围不受影响**（仍只跑自选池）。补全后：申万 155/155 与 11 个指数里的 10 个都到 `2026-10-12` | `src/chanlun/__main__.py:1338-1344`（`_resolve_codes` → `_market_scope`）、`src/chanlun/data/universe.py:62`（`universe` 只收 A 股，指数被排除）；原实测见 `docs/evidence/2026-10-10-按需拉取验收.md` |
| **`sh.000688`（科创50）没有可用的日线数据源** | **未修，2026-10-13 补全时量到**。baostock 对它返回 **0 行**（`err=0 success` 但无数据），所以它虽然已经在 `_market_scope()` 里，每次同步只是白跑；本地文件因此停在 `2026-09-30`。腾讯 `fqkline` **能给**（实测 `param=sh000688,day,,,320,qfq` 返回 320 根、末根 `2026-10-12`，可翻页 —— `tencent_source._paged_day` 已有分页实现），但那需要给 **A 股指数**加一条腾讯路由：今天 `sources.MARKET_SOURCE` 把 `sh`/`sz` 整个市场都指向 baostock，指数与股票共用一条路径。改它要引入「同市场内按标的分流」，属于独立变更 | `src/chanlun/data/sources.py`（市场→源映射）、`src/chanlun/data/baostock_source.py`；实测见 CHANGELOG 同日条目 |
| **本地一根都没有（404）时，自选按钮与高亮仍可能误报** | **未修，D-49 期间量到并登记**（2026-10-10）。前端比「是不是同一只」一律用落库键 `state.key`，而 404 分支拿不到服务端归一后的 `code`，只能退回用户输入原样（`app.js:233`）⇒ 用 `?code=sh.600759` 打开一只**本地无数据**的票时，自选栏里存的是裸 `600759`，两边形态不同，★ 仍显示「加自选」且行不高亮。触发条件是两条叠加：本地无数据 + 书签带前缀。要真修得让 404 也回一个归一后的 `code`（后端其实已经归一过，只是没把它放进 404 的响应体） | `app.js:233`、`app.js:2014`；实测见 `docs/evidence/2026-10-10-按需拉取验收.md` |

**未被使用但已实现**：`dif_high` / `dif_low` / `dif_extreme`
（`macd.py:210-240`）在 `chan/` 内未被使用，仅测试与 `scanner.py` 用
`is_divergence` / `hist_area`。

---

## 6. 故障排查索引

从**症状**反查该看哪一层、哪条决策。

| 症状 | 先看 | 相关决策 |
|---|---|---|
| 页面画出一条不属于这只票的曲线 | 号段判定 / 落库键 | [D-01](#d-01-号段判断只能有一处marketspy)、[D-02](#d-02-落库键规则能唯一就用裸码否则保留前缀) |
| 同一只票的划分结果昨天和今天不同 | 是否发生了判据变更 | [D-14](#d-14-中枢延伸上限--8-段第-33-课)、[D-19](#d-19-第三类买卖点用位置口径)、[D-28](#d-28-线段标准化是事后判据不是划分前置条件) |
| 中枢数量异常少 / `b1`、`b2` 出不来 | 中枢延伸上限 | [D-14](#d-14-中枢延伸上限--8-段第-33-课) |
| 上涨/下跌趋势数为 0，全是"形成高级别中枢" | GG/DD 是否误含离开段 | [D-15](#d-15-zgzd-与-ggdd-的口径以及区间不可混用) |
| 图注写"趋势按 `[ZD,ZG]` 口径"，但结果明显按 `[DD,GG]` 判 | 前端文案没跟上后端判据 | [D-16](#d-16-趋势判据严格分离三分支) |
| 一只票换个取数窗口就塌成一条超长线段、首段上百笔 | 左端起段是否被当成线段起点 | [D-30](#d-30-被截断的左端起段取还能确认最多线段的起点) |
| 标准特征序列里明明有分型，却一个分界点也划不出来 | 合并后是否回退（残留包含关系） | [D-31](#d-31-标准特征序列合并之后必须回退) |
| 背驰面积把红柱绿柱加在了一起 | `hist_area` 的 `color` 是否被绕过 | [D-17](#d-17-macd-面积必须分色累加) |
| 页面 500，日志 `Parquet magic bytes not found in footer` | 是否有绕过原子写的路径 | [D-04](#d-04-parquet-原子写) |
| 拖动窗口整张图平移 / 中枢 ZG/ZD 跟着跳 | 后复权锚点 | [D-05](#d-05-后复权锚点取因子表首段) |
| 除权日附近周线/月线开高低明显错 | 复权与聚合的先后 | [D-07](#d-07-复权必须排在聚合前面)、[D-08](#d-08-周月不单独同步由本地日线聚合) |
| 自选栏报出一根根本不存在的跌停，图上却是平的 | 是否用了不复权帧算涨跌幅 | [D-07](#d-07-复权必须排在聚合前面) |
| 复权价出现假跳空 / 两次复权 | `apply_adjust` 与 `unapply_adjust` 用反 | 不变量 9 |
| 反推因子报"口径不对" | `day` 是否已切到通达信整包 | [D-10](#d-10-反推因子而非直接问数据源) |
| 页面上复权说明与实际口径不符 | `sync_state.adjust` vs 实际落库口径 | [D-02](#d-02-落库键规则能唯一就用裸码否则保留前缀)、4.1 |
| 某票日线明明在本地，自选栏却标"未同步" | 存在性判据用了派生周期 | 4.1 |
| 分钟周期自选栏整栏不可点 | `missing` 守卫 / 404 提前 return | [CHANGELOG 0.0.13](CHANGELOG.md) |
| 分型/笔出现 0 价极值，整段被压成一根笔 | 0 价行是否被剔除 | [D-03](#d-03-剔除-0-价行) |
| 回测结果好得不真实 | 是否有未来函数 / 是否用了物理截断 | [D-22](#d-22-禁止未来函数逐层显式-confirmed_at) |
| 回测里出现"当日买当日卖" | T+1 结转时机 | 4.4 |
| `step()` 与 `full()` 结果不一致 | `confirmed_at` 例外是否被误解 | [D-23](#d-23-step-是全量重算--reconcile不是局部重算) |
| 窗口里 12 段，用户以为这只票只有 12 段 | 裁剪是否参与划分 | [D-24](#d-24-窗口裁剪不参与划分) |
| 线段数暴涨（如某票 159 笔线段） | 标准化是否被当成划分前置条件 | [D-28](#d-28-线段标准化是事后判据不是划分前置条件) |
| 跨源校验"满分通过"但实际一条没跑 | 报告是否分类给数 | [D-11](#d-11-跨源校验只取-30-天窗口) |
| 分钟同步点了没反应 / 一直转圈 | 指数没有分钟线（`syncable: false`） | 4.1、4.2 |
| 优化器报错说不能改主干 | 这是设计，不是 bug | 4.5 |
| `pip install` 装了一个没人用的包 | `pytdx` 是无用依赖 | [CHANGELOG 0.1.0 已知问题](CHANGELOG.md) |

---

## 7. 相关文档

| 文档 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | 维护规则、版本号规则、`chanlun108/` 存档索引 |
| [CHANGELOG.md](CHANGELOG.md) | 变更记录（含回填的里程碑版本） |
| [chanlun108/README.md](chanlun108/README.md) | 《教你炒股票》108 课存档说明 |
| [docs/evidence/](docs/evidence/) | 实测证据（审计、优化器发现、验收截图） |
| [optimizer/theory/](optimizer/theory/) | 判据变更的理论依据（12 份原文摘录） |
| [optimizer/PROMPTS.md](optimizer/PROMPTS.md) | 优化器提示词 |
| [deploy/README.md](deploy/README.md) | 部署与看行情客户端 |
| [config.toml](config.toml) | 全部配置项 |

---

## 8. 本文件的素材来源与未确认范围

本文件由三轮**逐文件精读**产出（数据层 `data/`、结构层 `chan/`、外围层
`web/scan/backtest/optimizer`），所有论断带 `文件:行号`。
精读过程中的临时笔记未入库 —— 内容已全部并入本文档，留一份副本只会造成两处
说法分叉。

**明确未确认的范围**（不要把这些当成已验证的结论）：

- `include.py:30-33` 所称"第 65 课原文在此是空白项"是**注释判断**，
  未独立核对第 65 课原文。
- `segment.py:15-18` 关于特征序列包含处理合并方向"两说并存"的判断，
  代码只声明为显式实现约定。
- `styles.css` 仅按注释分节读取，**未逐条核对选择器**。
- `web/api.py` 缓存失效 / `clear_cache` 的**调用时机未逐处追踪**。
- `optimizer/agent.py` 的 `_shadow` 内 `git apply` 与 `optimizer/tools/*.py`
  脚本正文**未逐行阅读**。
- `web/api.py::_partial_last_bar` 的判定细节**未确认**。
- 数据层"7 开头申购/配售代码"的说法仅出现在注释中，代码无对应常量。
