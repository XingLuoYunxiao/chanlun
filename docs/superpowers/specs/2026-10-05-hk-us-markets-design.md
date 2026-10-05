# 港股 / 美股（指数与个股）—— 设计文档

- 日期：2026-10-05
- 状态：**待用户审阅**
- 关联：
  - `ARCHITECTURE.md` D-42（前序：背驰/买卖点 marker 落点）
  - `src/chanlun/data/periods.py`（周/月本地聚合口径，本设计沿用）
  - `src/chanlun/data/markets.py`（号段 → 交易所的唯一判定表，本设计扩它）
- 版本影响：`MINOR`（新增市场、新增数据源、新增落库目录；**不触碰任何缠论判据**）

---

## 1. 背景与目标

### 1.1 用户诉求（原话）

> 「我想要新增一个，就是比如说我想要看港股或者是美股的指数，美股就不需要具体股票了，去看这个指数，主要指数就行。然后港股的话，我可能有时候会看一些具体的股票，以及它的那个恒生指数，这个能否完善一下？」

补充（第二轮）：

> 「这个30分钟还有5分钟包括A股以及港股，你尝试一下看能不能找到，然后其他的就按你说的来。」

### 1.2 需求拆解

| # | 需求 | 验收方式 |
|---|---|---|
| R1 | 能看**恒生指数**、**恒生科技指数** | 输入 `hk.HSI` / `hk.HSTECH`，日/周/月线出图出结构 |
| R2 | 能看**道琼斯**、**纳斯达克综合**指数 | 输入 `us.DJI` / `us.IXIC`，日/周/月线出图出结构 |
| R3 | 能看**港股个股**，输名字或代码都能找到 | 搜索框输「腾讯」或「00700」都能加到自选并看 |
| R4 | 港股 30分 / 5分 | **实测不可得**（见 §3.2）⇒ 灰掉并写明原因，不假装能取 |
| R5 | 美股 30分 / 5分 | Sina `US_MinKService.getMinK`，可用（1023 根上限，靠增量累积） |
| R6 | A股 30分 / 5分 | **现在就已经能用**（baostock），本次只是核实，不改 |
| R7 | 复权口径 | 前复权落库 + 前复权显示；港股个股另给「不复权」标签（§5） |

### 1.3 R6 的核实结论（先答用户的问题）

「A股 30分/5分 能不能找到」——**不用找，现在就有**。`src/chanlun/data/baostock_source.py:27`
的 `MIN_PERIODS = {"60","30","15","5"}`，`config.toml` 的 `periods = ["day","30","5"]`，
页面顶部已有「30分」「5分」两个 tab（`src/chanlun/web/static/index.html:27-28`）。
所以本次新增的只有 **港股 / 美股** 两条数据链。

---

## 2. 范围

### 2.1 做

1. 新增 `hk` / `us` 两个市场命名空间，落库到 `data/<period>/hk/`、`data/<period>/us/`。
2. 新增数据源模块：港股走**腾讯**，美股走**新浪**（依据见 §3）。
3. 看盘页：代码输入框接受 `hk.HSI` / `hk.00700` / `us.DJI` 等写法；自选池搜索框能搜到港股（中文名或代码）。
4. 单票一键同步支持新市场；周/月线照旧由本地日线聚合。
5. 港股分钟线**灰掉**并给出明确原因；美股分钟线可用但标注深度上限。

### 2.2 明确不做

| 不做的事 | 原因 |
|---|---|
| 美股个股 | 用户原话「美股就不需要具体股票了，去看这个指数」 |
| 标普500 `us.INX`、纳斯达克100 `us.NDX`、国企指数 `hk.HSCEI` | 用户 2026-10-04 明确否掉 |
| 港股 30分/5分 | 实测不可得（§3.2），不做假接口 |
| 分时（1 分钟当日线） | 用户没要；虽已探到可用（§3.3 表末），本期不接 |
| 扫描 / 回测 支持新市场 | 用户 2026-10-04 选定：只做看盘页 + 自选池 + 单票同步 |
| 港股复权因子（除权事件表） | 见 §5.3，本期用「不复权」标签替代 |

---

## 3. 数据源可用性实测矩阵（本设计的核心依据）

> 探测环境前提：本机所有出网流量走 `ALL_PROXY=socks5://127.0.0.1:7897` /
> `HTTP(S)_PROXY=http://127.0.0.1:7897`，`NO_PROXY` 只含 localhost 与 RFC1918。
> **下面每一条结论都依赖这条代理链路**；代理变化后必须重测。
> `httpx` 在这条链路上不可用（`ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`），
> 所以全部探测用 `curl` 完成。

### 3.1 结论表

| 市场 | 周期 | 源 | 端点 | 实测深度 | 复权 |
|---|---|---|---|---|---|
| A股 | 日/周/月 | baostock | 现有 | 全历史 | 前复权 |
| A股 | 60/30/15/5分 | baostock | 现有 | **已上线**（`baostock_source.py:27 MIN_PERIODS`）；深度**未逐票实测** | 不复权 |
| **港股** | **日线（前复权）** | **腾讯** | `ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk00700,day,,,640,qfq` → 键 **`qfqday`** | 单请求 **`n` 最大 1500**；`n=2000` **静默降级成 640**；`n≥3000` 报错。**区间只约束结束日**，可回溯到 **2004-06-16** | **前复权** |
| **港股** | **日线（不复权）** | **腾讯** | `ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00700,day,<start>,<end>,640,qfq` → 键 **`day`** | 尊重 `start`；`2018` 单年 246 根 | **不复权（原始价）** |
| **港股** | **日线（后复权）** | **腾讯** | `…/hkfqkline/get?param=hk00700,day,,,640,hfq` → 键 **`hfqday`** | 同 `qfqday` | 后复权 |
| **港股** | **周/月** | 本地聚合 | `data/periods.py:aggregate` | 由日线决定 | 同前复权 |
| **港股** | 30分 / 5分 | **无** | — | **不可得**（通用 `fqkline` 会回 `code=0` 但**只有 1 根**，见 §3.2 (a2)） | — |
| **港股** | 分时（1分当日） | 腾讯 | `…/appstock/app/hkMinute/query?code=hk00700` | 当日累计（**随盘中增长**，实测 152 根；不是固定值） | — |
| **美股指数** | **日线** | **新浪** | `stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20x=/US_MinKService.getDailyK?symbol=.DJI&type=240` | `.DJI` 5726 根（2004-01-02 起）/ `.IXIC` 5724 根 | **不复权**（指数无除权，等价前复权） |
| **美股指数** | **周/月** | 本地聚合 | 同上 | — | — |
| **美股** | **5/15/30/60分** | **新浪** | `…/US_MinKService.getMinK?symbol=<S>&type=<T>` | **1023 根硬上限，无翻页参数** | 不复权 |

> **端点列一律用裸 `ifzq.gtimg.cn`。** `web.ifzq.gtimg.cn` 在 `fqkline`/`hkMinute`
> 上碰巧能用（实测 200），但 `mkline` 那条路径会 301 跳到 `web3.ifzq.gtimg.cn`，
> 而那个域名**在本机 NXDOMAIN**（§3.4 坑 1）。裸域名全路径 200，所以统一用它。
> 镜像 `proxy.finance.qq.com/ifzqgtimg/appstock/…` 实测等价可用。
>
> A股分钟线深度**没测过**，所以不写数字。要写数字就按 §3.3 的方式实测一条命令贴上来。

### 3.2 港股 30分/5分 不可得 —— 证据链

**这一条是负结论，必须站得住，所以列全。** 探测分四层，每层都带同宿主/同品种的正控。

**(a) 腾讯 `mkline`（A股分钟线用的那个端点）对港股直接拒收**

```
https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=hk00700,m30,,320
  ⇒ {"code":-1,"msg":"param error","data":[]}
```
试过 `hk00700`/`hkHSI`/`hkHSTECH` × `m5`/`m15`/`m30`/`m60`，以及 `r_hk00700`、`,qfq`、`,1`、`,m30,0,320`
等变体，**全部 `param error`**。同一次运行里 `sh600519,m30` 返回 320 根（正控），
证明不是网络问题。符号必须不带点（`sh.600519` ⇒ `param error`）。

**(a2) ★ 2026-10-05 复查：通用 `fqkline` 现在会「假成功」—— 只回 1 根，不是历史**

这是**写本文件时新发现的**，也是整份规格里最容易骗过人的一条。同一个 `fqkline`
端点（日/周/月就是用它取港股日线的那个）对港股分钟请求**不再报错**，而是返回
`code=0` —— 但里面**只有 1 根**，就是当前那根还没走完的 bar：

```
hk00700,m30,,,320,qfq                ⇒ code=0  m30: n=1  first=2026-10-05 last=2026-10-05
hk00700,m30,,,2000,qfq               ⇒ code=0  m30: n=1     ← n 完全无效
hk00700,m5,,,2000,qfq                ⇒ code=0  m5:  n=1     ← 换周期也一样
hk00700,m30,2026-01-01,2026-10-05,320,qfq ⇒ code=0 m30: n=1 ← 日期区间也无效
hk00700,m30,,,320                    ⇒ code=1  "bad params" ← 去掉末尾 qfq 才报错
```

**⇒ 判据不是「返回码是不是 0」，而是「行数」。1 根 bar 建不出 30 分钟的中枢，
所以结论不变：港股分钟线不可得。**

> 一条实测 URL 的返回值会变，`bad params` 变成了「`code=0` + 1 根」。
> 所以 §12 的 M5 负控**不能**写成「期望 `bad params`」，要写成「期望行数 ≤ 1」——
> 否则数据商一改行为，负控就会以「测试挂了」的形式报警，而不是以「结论失效」的形式。
>
> **实现取数层时必须显式校验行数**（少于某个下限就抛 `DataSourceError`），
> 不能只看 `code == 0`。这条是本次复查最有价值的产出。

**(a3) ★★ 2026-10-05 复查（二）：`fqkline` **根本不看复权参数** —— 它返回的是不复权价**

这一条推翻了本文件初稿对「港股日线从哪来、是什么口径」的全部写法，**必读**。

用**同一个窗口**（`2020-01-01,2020-12-31,400`）请求 `fqkline`，只改末尾的复权词：

```
fqkline  suffix='qfq'  key=day  n=248  sha=aaaa411d6a16bcba  first_close=382.400
fqkline  suffix='hfq'  key=day  n=248  sha=aaaa411d6a16bcba  first_close=382.400
fqkline  suffix=''     key=day  n=248  sha=aaaa411d6a16bcba  first_close=382.400
fqkline  suffix='bfq'  key=day  n=248  sha=aaaa411d6a16bcba  first_close=382.400
   ⇒ 四者 sha256 完全相同，键名恒为 day —— 复权词被**完全忽略**
```

同一时刻请求 `hkfqkline`，它**认**这个参数：

```
hkfqkline suffix='qfq'  key=qfqday  n=400  first_close=273.270
hkfqkline suffix='hfq'  key=hfqday  n=400  first_close=1645.280
   ⇒ 键名与数值都不同 —— 参数生效
```

**`fqkline.day` 到底是不复权还是前复权？** 与 `hkfqkline.qfqday` 同期逐日比 `qfq/raw`：

```
2020-01-02  fqkline=382.400  qfqday=330.870  ratio=0.8652
2020-07-16  fqkline=513.000  qfqday=462.670  ratio=0.9019
2020-12-28  fqkline=519.000  qfqday=468.670  ratio=0.9030
   ⇒ ratio ≈ 0.87 且**随日期漂移**（前复权序列会随分红重标定）
   ⇒ qfq < raw，差值就是分红 ⇒ **`fqkline.day` 是不复权（原始价）**
```

**反证（最近的日期上两者必须重合）**：`2026-09-24` 起 `fqkline.day` 与 `qfqday`
逐日**完全相等**（438.400 / 436.600 / 421.200 …）—— 因为最近一次分红之后
前复权价就是原始价。这同时证明了「`fqkline.day` = 原始价」而不是另一套前复权。

> **初稿错在哪**：初稿写「港股日线 = `fqkline` + `qfq` 参数 ⇒ 前复权落库」。
> 两个错叠加：那个参数**不生效**，而且**它本来就不是前复权源**。
> 若照初稿实现，我们会把**不复权价**当成前复权存进库，
> 页面标着「前复权」、`unapply_adjust` 按前复权去还原 —— 除权日会长出假缺口
> （正是 `adjust.py:107-108` 记录的事故模式）。**这是本次复查拦下的最严重问题。**
>
> **正确取法**：前复权用 `hkfqkline`（键 `qfqday`），后复权同端点（`hfqday`），
> 不复权用 `fqkline`（键 `day`）。**三个口径数据商都直接给，不用自己算。**

**(a4) ★ 顺带的收获：港股「不复权」标签因此可以是真的**

`adjust.py:121 infer_factors(raw, qfq)` 已存在，签名正好是「不复权 + 前复权两份同区间数据」，
内部按 `ts` 合并、`k = close_qfq / close_raw` 取阶梯。既然 (a3) 证明**原始价可直接取到**，
港股就能建出真的因子表 ⇒ `unapply_adjust` 有真因子可用 ⇒
「不复权」标签显示的是**真的不复权价**，而不是「因子为空时原样返回前复权价」
（`adjust.py:112`：因子空表时 `unapply_adjust` 直接返回入参）。
**详见 §5.4。**

**(b) 腾讯港股控制器命名空间被穷举**

`…/appstock/app/<ctl>/get?param=hk00700,m30,,320&code=hk00700`，26 个候选控制器名：

- 只有 **两个存在**：`hkMinute`（`Call to undefined method: If_Controller::getAction()`）、
  `hkfqkline`（`bad params`）；
- 其余 24 个一律 `{"code":11,"data":"","msg":"Can't load controller:<X>Controller"}`。

方法名再穷举 23 个（`query, kline, mkline, minline, day, get, list, data, k, min, minute,
five, fiveday, trend, chart, mk, m30, m5, getMinK, getKline, getMkline, minuteKline, getMinuteKline`），
**只有 `hkMinute.query` 与 `hkfqkline.get` 能解析**。

而 `hkfqkline` 是**日/周/月专用**：

```
hk00700,day,,,320,qfq    ⇒ code=0，qfqday 行形如 ["2025-06-19","497.700",…,{"cqr":"2025-…"}]
hk00700,week,,,320,qfq   ⇒ code=0，qfqweek
hk00700,m30,,,320,qfq    ⇒ {"code":1,"msg":"bad params","data":[]}
hk00700,m30,,320         ⇒ {"code":1,"msg":"bad params","data":[]}
hk00700,30,,,320,qfq     ⇒ {"code":1,"msg":"bad params","data":[]}
```

**⇒ 港股专用控制器 `hkfqkline` 至今仍明确拒绝分钟线。**
但**通用控制器 `fqkline` 不拒**（回 `code=0` + 1 根，§3.2 (a2)）——
所以「用哪个控制器」会得出不同的假象，**结论要以行数为准**。

**(c) 新浪港股分钟线在四个宿主上都不存在**

| 宿主 / 路径 | 结果 |
|---|---|
| `quotes.sina.cn/hk/api/openapi.php/<NS>.getKLineData`，`symbol=00700\|hk00700`，`scale=30` | 22 个命名空间 × 全部 `{"result":{"status":{"msg":"Service not valid","code":11},"data":[]}}` |
| `stock.finance.sina.com.cn/hkstock/api/jsonp_v2.php\|jsonp.php` × 15 个服务名 | `Service not valid` / `Service not found`，**HITS=0** |
| 同上 × `HK_StockService.<38 个方法名>` | **0 命中** |
| `money.finance.sina.com.cn/quotes_service/api/json_v2.php` × 22 命名空间 × 11 方法 | **命中 []** |
| `money.finance.sina.com.cn/quotes_service/api/jsonp.php/x=/<NS>.<M>`，4 命名空间 × 3 方法 | 12/12 全 `({"__ERROR":3,"__ERRORMSG":"Service not valid"});` |

**(d) 关键正控：港股行情本身是活的，缺的是「分钟 K 线」这个接口**

```
# ★ 必须带 Referer，裸请求返回 "Forbidden"（实测）
curl -s -H 'Referer: https://finance.sina.com.cn' 'https://hq.sinajs.cn/list=hk00700'
  ⇒ var hq_str_hk00700="TENCENT,腾讯控股,420.000,421.200,423.200,416.400,422.600,…"
```
> **这个接口返回 GBK**，用 `curl` 管道给 Python 读会 `UnicodeDecodeError`
> （实测 `0xcc` 位置解码失败）—— 取数层必须 `decode("gbk")`。

腾讯侧同理：`hkMinute/query?code=hk00700` ⇒ `code=0`，**当日累计 152 根**
（`data.date='20261005'`，首 `0930` 末 `1300`；这个数**随盘中时间增长**，
本文件早先记的 77 根是当天更早时候的读数 —— 它**不是**一个固定值）；
`day/query?code=hk00700` ⇒ `code=0`，`data.data` 恰好 5 个交易日。

⇒ **港股 1 分钟与实时行情都活着，30分/5分 是数据商的接口缺口，不是网络或市场问题。**

**(e) 其余候选全部排除**

| 源 | 结果 |
|---|---|
| 网易 `img1.money.126.net/data/hk/kline/30/00700.json` | `http=000`（含 A股正控同挂 ⇒ 网络不可达，非端点缺失） |
| 同花顺 `d.10jqka.com.cn/v6/line/hk_00700/30/last.js` | `502 Bad Gateway` |
| 雪球 `/v5/stock/chart/kline.json` | `400`，且 cookie 罐里 `xq_a_token` 为 0 条 |
| Yahoo `query1/query2/finance.yahoo.com` `/v8/finance/chart/0700.HK?interval=30m` | 三宿主全部 `http=000` |

**结论**：港股 30分/5分 在本机可达的公开源里**不存在**。产品处理 = 灰掉该周期 +
在页面上写明原因（§9.2），并把这条记进 `ARCHITECTURE.md` 的已知空白。

> ⚠️ 合规附注：任何「港股分钟线再分发」的说法都要先看 HKEX 的 BMP 授权清单
> （<https://www.hkex.com.hk/-/media/HKEX-Market/Services/Market-Data-Services/Real-Time-Data-Services/Data-Licensing_/HKEX_IS_China-Lists/2025/20250228-Websites-with-BMP_c_C.pdf>）。
> 本设计只做本机自用取数，不做再分发。

### 3.3 可直接复现的探测命令

```bash
# ★ 港股日线前复权 —— 用 hkfqkline，键名 qfqday（正控：640 行）
curl -s 'https://ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk00700,day,,,640,qfq' | head -c 200

# ★ 港股日线不复权 —— 用 fqkline，键名 day（正控：246 行，首行 2018-01-02）
curl -s 'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00700,day,2018-01-01,2018-12-31,640,qfq'

# ★ 证明 fqkline 不看复权词：四条 sha 必须相同（见 §3.2 (a3)）
for s in qfq hfq '' bfq; do
  curl -s "https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00700,day,2020-01-01,2020-12-31,400,$s" | shasum
done

# 港股日线后复权（正控：键名 hfqday）
curl -s 'https://ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk00700,day,,,640,hfq' | head -c 200

# ★ hkfqkline 的 n 静默降级（n=2000 只回 640，n=3000 报错且 data 是 list）
curl -s 'https://ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk00700,day,,,2000,qfq' | head -c 120

# 港股分钟线（负结论：★ 期望「行数 ≤ 1」，不是报错 —— 见 §3.2 (a2)）
curl -s 'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00700,m30,,,2000,qfq'
curl -s 'https://ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk00700,m30,,,320,qfq'   # ⇒ bad params

# A股分钟线（正控：320 行；n 给 2000 也仍是 320）
curl -s 'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=sh600519,m30,,320' | head -c 200

# 美股指数日线（正控：n=5726，首行 2004-01-02）
curl -s 'https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20x=/US_MinKService.getDailyK?symbol=.DJI&type=240'

# 美股 30 分（正控：n=1023，首行 2026-06-11 12:00:00）
curl -s 'https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20x=/US_MinKService.getMinK?symbol=.DJI&type=30'

# 港股搜索（正控：v_hint 里有 hk~00700~腾讯控股）
curl -s 'https://smartbox.gtimg.cn/s3/?q=%E8%85%BE%E8%AE%AF&t=all'

# 港股 1 分钟与实时（正控：证明市场是活的）
curl -s 'https://ifzq.gtimg.cn/appstock/app/hkMinute/query?code=hk00700' | head -c 200
curl -s -H 'Referer: https://finance.sina.com.cn' 'https://hq.sinajs.cn/list=hk00700'   # ★ 必须带 Referer，返回 GBK
```

### 3.4 探测中查明的几个「坑」（写进代码注释，避免后来人重踩）

1. **301 是「按路径」的，不是「按域名」的 —— 这条实测很容易记错，写清楚。**
   `web3.ifzq.gtimg.cn` 确实是 NXDOMAIN（实测 `socket.gaierror`）。但
   `web.ifzq.gtimg.cn` **并非整体不可用**，只有 `mkline` 那条路径会 301 过去：

   ```
   https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=…   ⇒ 200   ✅
   https://web.ifzq.gtimg.cn/appstock/app/hkMinute/query?code=… ⇒ 200   ✅
   https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param=…  ⇒ 301 → web3.ifzq.gtimg.cn（NXDOMAIN）❌
   https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=…      ⇒ 200   ✅（裸域名同路径正常）
   ```

   **结论：一律用裸 `ifzq.gtimg.cn`**（或镜像 `proxy.finance.qq.com/ifzqgtimg`）。
   裸域名在全部路径上都返回 200，`web.` 前缀只在 `fqkline`/`hkMinute` 上碰巧能用。
   > 初稿写成「`web.ifzq` 会 301」是**以偏概全**，已按逐路径实测改正。
2. **腾讯 `mkline` 的条数上限硬编在 320**：`n=1000/2000/5000` 都只回 320 根，
   且 `first/last` 完全相同。
3. **`hkMinute/query` 与 `day/query` 的参数全是惰性的**：`&date=`/`&day=`/`&days=`/`&num=`
   逐个试过，`hkMinute` 永远只回当日，`day` 永远只回 5 个交易日。
4. **腾讯美股个股的日线不可靠**：`us.AAPL` ⇒ 空数组；`usAAPL` ⇒ 2 根（首行 2011-06-02）；
   `usAAPL.OQ` ⇒ 正常 10 根。**符号形态不统一**，所以美股个股不做（§2.2），
   美股指数走新浪（符号 `.DJI`/`.IXIC`，点前缀强制）。
5. **新浪美股分钟线无翻页**：`&datalen=`/`&num=`/`&date=`/`&from=`/`&limit=`/`&p=`/`&page=`
   全部惰性，永远 1023 根。**深度只能靠 `store.upsert` 增量累积**。
6. **新浪美股日线是「不复权」原始价**（AAPL 首行 1984-09-07 收 26.50，即当时名义价）。
7. **腾讯美股指数符号带点、港股不带点**：`us.DJI` ✓ / `hkHSI` ✓；`hk.HSI` ✗ / `usDJI` ✗。
   这个映射由我们自己的表决定（§7.2），**不依赖数据商的一致性**。
8. **★ `code=0` 不等于拿到了数据 —— 必须校验行数。** 这是本次复查最重要的发现：
   同一个 `fqkline` 端点，港股分钟请求返回 `code=0` 却**只有 1 根**当前 bar
   （`n` 和日期区间全部无效，§3.2 (a2)）。**取数层一律先看行数，再看返回码。**
9. **`hq.sinajs.cn` 要 `Referer`，且返回 GBK。** 裸请求 ⇒ 文本 `Forbidden`（不是 HTTP 403）；
   带 `Referer: https://finance.sina.com.cn` ⇒ 正常。用 Python 读必须 `decode("gbk")`，
   否则 `UnicodeDecodeError`（实测 `0xcc` 处失败）。
   > 本期港股走腾讯，不走这个接口；记下来是因为它是「港股行情是活的」的**唯一证据**，
   > 将来若要加实时报价会用到。
10. **★★ 腾讯两个「日线」控制器的复权行为完全不同 —— 别用错**（§3.2 (a3)）：

    | 控制器 | 认 `qfq`/`hfq` 吗 | 返回键 | 口径 |
    |---|---|---|---|
    | `fqkline`（通用） | **不认**（四个值 sha 相同） | 恒为 `day` | **不复权（原始价）** |
    | `hkfqkline`（港股专用） | **认** | `qfqday` / `hfqday` | 前复权 / 后复权 |

    **「传了 `qfq` 就是前复权」这个直觉是错的。** 判据是**键名**：
    看到 `day` 就是原始价，看到 `qfqday` 才是前复权。
11. **`hkfqkline` 的 `n` 在 2000 上会静默降级，且区间只约束结束日**：

    ```
    n=320  ⇒ 320 根   n=640  ⇒ 640 根   n=1000 ⇒ 1000 根   n=1500 ⇒ 1500 根
    n=2000 ⇒ 640 根 ← ★ 静默降级，不报错（`code=0`，行数却退回 640）
    n=3000 ⇒ code=1，`data` 是 list（错误形状，不是 dict）
    ```
    区间语义：`2006-01-01,2007-12-31,640,qfq` ⇒ 640 根，首行 **2005-06-02**（早于 `start`！）；
    `2018-01-01,2018-12-31,640,qfq` ⇒ 640 根，首行 **2016-05-27**。
    ⇒ **`n` 优先，`start` 只是软约束**。要翻页必须**把 `end` 往前挪**，不能靠 `start`。
    （对比：`fqkline` 尊重 `start` —— 两个控制器在这点上也不一样。）
12. **`hkfqkline` 的 `data` 在报错时是 `list` 不是 `dict`**：`n=3000` ⇒
    `{"code":1,"data":[],…}`。解析时若直接 `.get("data",{}).get(code)` 会
    `AttributeError: 'list' object has no attribute 'get'`（实测踩到过）。
    **先判类型再取键。**

---

## 4. 代码命名与落库键

### 4.1 命名空间

| 市场 | 用户可输入 | 例子 | 规范落库键 | 前缀 |
|---|---|---|---|---|
| 港股个股 | `hk.<5位数字>` | `hk.00700`、`hk.09988` | 同输入（数字无大小写） | **必须** |
| 港股指数 | `hk.<指数代码>` | `hk.HSI`、`hk.hsi`、`hk.HSTECH` | **`hk.hsi`、`hk.hstech`** | **必须** |
| 美股指数 | `us.<指数代码>` | `us.DJI`、`us.dji`、`us.IXIC` | **`us.dji`、`us.ixic`** | **必须** |
| A股 | 不变 | `sh.000300`、`399001` | 同现状 | 按号段 |

**输入大小写不敏感，落库键一律小写**（决策与理由见 §4.2 真缺口三）。
`hk.HSI` / `us.DJI` 是**用户能打的写法**，不是库里存的键。

### 4.2 前缀机制**已经能用**，但有三处真缺口（实测，2026-10-05）

> 本节先纠正一个容易想当然的判断。`markets` 的前缀机制是**市场无关**的
> （`needs_prefix` 的判据是「去掉前缀后落点是否还是同一个市场」，
> 见 `markets.py:86-90`），所以 `hk` / `us` **不需要**新增前缀规则。
> 下面每一条都是实跑出来的，不是读代码推出来的。

**已经正确、不用改**（实测输出）：

```
markets.needs_prefix("hk", "00700")  => True      # market_of("00700")=="sz" != "hk"
markets.needs_prefix("us", "DJI")    => True
markets.store_key("hk.00700")        => "hk.00700"   # 前缀被保留
store.market_of("hk.00700")          => "hk"
store.path_for("hk.00700", "day")    => …/data/day/hk/00700.parquet
store.path_for("us.DJI",   "day")    => …/data/day/us/dji.parquet
store.path_for("hk.HSI",   "day")    => …/data/day/hk/hsi.parquet
```

即 §7.5 的落库布局**不需要新增任何路径逻辑**：`store.market_of`（`store.py:48-53`）
「有前缀用前缀」，`path_for`（`:56`）把市场放父目录，`bare_code`（`:37`）剥前缀 —— 全对。

**真缺口一：`to_bs_code` 会「静默放行」`hk.00700`（比报错更危险）**

`markets.HEAD1` 对首位 `0` 的映射是 `None`（`markets.py:32`，含义是「沪深同号段」），
而 `baostock_source.to_bs_code`（`:64`）在「号段判不出交易所」时**接受调用方给的前缀**：

```
to_bs_code("hk.00700") => "hk.00700"     ← 不报错，原样放行
to_bs_code("hk.HSI")   => "hk.hsi"       ← 不报错，还被转成小写
to_bs_code("us.DJI")   => "us.dji"       ← 不报错
to_bs_code("HSI")      => ValueError: 无法判断交易所: HSI（请显式给出前缀，如 sh.000001）
```

港股 `00700` 正好是 `0` 开头 ⇒ 走「同号段」分支 ⇒ 放行。
这个字符串随后会被丢给 baostock 查询，**在取数时**才以一条看不懂的错误失败，
而不是在入口处被拦住。所以 `to_bs_code` 必须**显式拒绝**非 A 股前缀：

```python
if markets.is_hk(code) or markets.is_us(code):
    raise ValueError(f"{code} 不是 A 股代码：港股/美股请走 sources.fetch_bars 分派，不要交给 baostock")
```

**真缺口二：`is_index` 不认港股/美股指数**

`is_index("hk.HSI")` / `is_index("us.DJI")` 实测都是 `False`
（`INDEX_PREFIXES`（`:109`）只有 sh/sz/bj）。后果是 `sync.py:27 adjust_for()`
会给指数返回 `"2"`（前复权）而不是 `"3"`（不复权）。
指数没有除权，价格一样，但 `sync_state.adjust` 会替指数声称「前复权」，
这句话会一路传到页面 —— `tests/test_cli.py:302-303` 已经把这个道理写死。

**真缺口三：US 符号的大小写会被吞掉，必须定一个规范键**

`store_key`（`:99`）和 `bare_code`（`store.py:44`）都做 `.lower()`，所以
`us.DJI` 与 `us.dji` 会落到**同一个文件** `dji.parquet`，却是 `meta.sync_state` 里的**两行**。
不统一就会出现「同一只指数两条同步状态」。

**决策：落库键一律小写**（`us.dji` / `us.ixic` / `hk.hsi` / `hk.hstech` / `hk.00700`）。

- 依据：`store_key` / `bare_code` 已经小写化，且 A 股路径完全不受影响
  （A 股键全是数字）。**不跟既有约定对抗**，改动面最小。
- 被否决的替代方案：**大写规范键**（`us.DJI`）。否决理由 —— 要改 `store_key`
  与 `bare_code` 两个**已被大量测试覆盖的**函数，而它们服务于 A 股主路径；
  为了美股显示好看去动主路径，风险收益不成比例。
- 显示层不受影响：页面显示的名字来自 `meta.name_of`（`meta.py:279`，返回「道琼斯」），
  代码 chip 显示规范小写键。`DEFAULT_WATCHLIST`（`meta.py:344`）因此写成小写键。
- `normalize_code` 必须把 `us.DJI` / `us.dji` / `us.Dji` 都归到 `us.dji`（§8.1 的测试点）。

### 4.3 `markets.py` 的改动清单（实测后收敛）

| 位置 | 现状 | 改成 |
|---|---|---|
| `:24 HEAD2` / `:32 HEAD1` | A 股号段表 | **不动**（A 股判定的唯一来源） |
| `:48 market_of_bare` / `:63 market_of` / `:78 needs_prefix` | 市场无关，已正确 | **不动** |
| `:93 store_key` | 已保留 hk/us 前缀 | **不动** |
| `:109 INDEX_PREFIXES` | `{"sh":("000",), "sz":("399",), "bj":("899",)}` | **保留**，另加 `INDEX_CODES = {"hk": {"hsi","hstech"}, "us": {"dji","ixic"}}`（小写） |
| `:116 is_index` | 只看号段 | 先查 `INDEX_CODES`（小写比较），再走原号段逻辑 |
| 新增 | — | `is_hk(code)` / `is_us(code)` / `is_a_share(code)`；`US_SYMBOL_RE = ^[a-z][a-z0-9.\-]{0,9}$`（小写） |

`market_of` **不需要**「按正则校验 hk/us 符号」——校验放在 `sources.normalize_code`
（唯一的入口，§8.1）。`market_of` 保持「有前缀就用前缀」的宽松语义，
否则 `store.py` 会被迫 import 校验逻辑，而 `store` 现在只依赖 `markets`（§7.1 分层）。

### 4.4 `baostock_source` 的两处护栏

| 位置 | 改成 |
|---|---|
| `to_bs_code`（`:64`） | 见 §4.2 真缺口一：显式拒绝 `hk`/`us` 前缀 |
| `period_to_frequency`（`:96`） | **不改**（它本来就抛 `不支持的周期`）；但**不得**被 hk/us 代码路径调用到 —— 由 §7.3 的分派保证 |

---

## 5. 复权口径

### 5.1 决策（已定，2026-10-04）

> **前复权落库 + 前复权显示；港股个股另加一个「不复权」标签。**

具体到各市场：

| 品种 | 落库口径 | 页面可选 |
|---|---|---|
| A股 | 前复权（现状） | 前/后/不复权（现状） |
| 港股个股 | 前复权（**`hkfqkline` 的 `qfqday`**，见 §3.2 (a3)） | 前复权 + **不复权**（因子表由 §5.4 建） |
| 港股指数 | 不复权（`adjust="3"`，与 A 股指数一致） | 不复权（唯一） |
| 美股指数 | 不复权（新浪源本身就是原始价） | 不复权（唯一） |

> **★ 2026-10-05 修正**：初稿此处写「前复权（腾讯 `qfq`）」并把端点指向 `fqkline`。
> 实测证明那个端点的 `qfq` 参数**不生效**，它返回的是**不复权价**（§3.2 (a3)）。
> 已改为 `hkfqkline`，其前复权键名是 `qfqday`。**落库口径的决策没变，取数端点变了。**

### 5.2 依据

1. **与现有落库口径一致**。`adjust.py:99 unapply_adjust()` 的文档说得很清楚：
   「把**前复权落库**的行情还原成三态（一期 baostock 的 `day` 就是这个口径）」。
   港股个股走 `qfq` 落库，三态换算（`unapply_adjust`）**不用改一行**就能用。
   若改成不复权落库，就得换用 `apply_adjust`，两条公式拿错一条不会报错，
   只会在除权日长出一根假跳空缺口（`adjust.py:107-108` 已记录这个事故模式）。
2. **腾讯 `qfq` 参数实测生效**，且 `hkfqkline` 返回的字段名直接叫 `qfqday`。
3. **指数一律不复权**是既有约定（`sync.py:27` + `markets.is_index`），
   A 股指数已经这么跑，港股/美股指数沿用同一条规则，不引入第二套口径。
4. **美股日线源没有复权参数**（新浪 `getDailyK` 只有原始价）。美股指数无除权，
   所以不构成问题；这也是「美股只做指数」这条范围线的另一个好处。

### 5.3 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| **全部不复权落库**（用户曾倾向） | 与一期口径相反，要改用 `apply_adjust` 这条反向公式；港股个股除权跳空会污染笔/段/中枢，而缠论判据最怕假缺口。用户已放弃此方案。 |
| **网格读不复权 / 结构算前复权**（双口径，约 60 行） | 同一个页面同时存在两套价格：图上画 A、结构按 B 算，用户看到的中枢 ZG/ZD 与均线会和 K 线对不上。**由我否决**，理由已交付用户。 |
| ~~港股个股也建除权因子表~~ | ~~腾讯不提供港股除权事件，自造因子表要另找源，超出本期范围~~ **—— 此条已被 §3.2 (a3) 的实测推翻，改为采纳，见 §5.4。** 当时的判断建立在「港股拿不到原始价」这个错误前提上；实测证明 `fqkline.day` **就是**原始价，因子表可由 `infer_factors` 直接反推，不需要另找源。 |
| 港股个股用后复权 | 用户没要；且后复权锚点是因子表首段（`adjust.py:69 _k0`），港股历史起点 2004-06-16 一变，历史价全变，不利于长期对比。**但后复权数据本身可免费取到（`hfqday`），所以 §5.4 顺手把它作为因子表正确性的交叉校验，而不是作为落库口径。** |

---

### 5.4 ★ 港股因子表：由 `infer_factors` 反推（2026-10-05 新增，取代初稿的「不做」）

**背景**：§5.1 承诺港股个股有「不复权」标签。但 `unapply_adjust`（`adjust.py:98`）
在**因子表为空**时直接返回入参（`adjust.py:112`）——
也就是说没有因子表时，「不复权」标签显示的是**前复权价**，标签是**假的**。
这是初稿的一处内部矛盾：一边说「不做因子表」，一边说要给「不复权」标签。

**§3.2 (a3) 的实测解开了它**：三个口径数据商**都直接给**：

| 口径 | 端点 | 键名 |
|---|---|---|
| 不复权 | `fqkline` | `day` |
| 前复权 | `hkfqkline` + `qfq` | `qfqday` |
| 后复权 | `hkfqkline` + `hfq` | `hfqday` |

**决策：用 `adjust.infer_factors(raw, qfq)`（`adjust.py:121`）反推港股因子阶梯，写进
`adjust_factor` 表。** 该函数签名正是「不复权 + 前复权两份同区间数据」，
内部 `merged["k"] = close_qfq / close_raw`（`adjust.py:132`）取阶梯。

**两条硬要求**：

1. **两份数据必须按 `ts` 对齐后再喂**。`fqkline` 尊重 `start`、`hkfqkline` 只约束 `end`
   （§3.4 坑 11），两者返回的日期集合**形状不同**（实测同一 2018 窗口：
   `fqkline` 246 根 vs `hkfqkline` 640 根）。`infer_factors` 内部是 `merge(on="ts")`
   （`adjust.py:130`）——**交集之外的日期会被静默丢掉**，不会报错。
   所以取数时要取**同一个 `end`**、并让 `start` 取两边都覆盖得到的区间，
   取完后**断言交集行数 == 预期**，否则会得到一张稀疏的因子表。
2. **用 `hfqday` 做交叉校验**（可选但便宜）：`hfq_t = qfq_t / k_0`（`adjust.py:104`），
   所以 `hfqday / qfqday` 应当是**常数** `1/k_0`。
   > ⚠️ 但实测这个比值**会漂移**（2020-01-02 是 5.984，2020-06-28 是 5.769，约 4%）。
   > 说明腾讯的 `qfq`/`hfq` **不是**用同一个 `k_0` 锚定的，**不能用它当常数断言**。
   > 所以这条校验**只能作为「同量级」的弱检查**，不能作为强断言。
   > **若实现时发现无法构造出稳定断言，就不要写这条测试** ——
   > AGENTS.md §5：「测量必须能失败」，一条永远为真的断言没有价值。

**被否决的替代方案**：

| 方案 | 否决理由 |
|---|---|
| 不做因子表，「不复权」标签照给 | 标签名与数据不符 —— 显示的是前复权价。**这正是初稿的错误**，已推翻。 |
| 落库改存不复权，页面用 `apply_adjust` 算前复权 | 与一期口径相反，且要换用反向公式（`adjust.py:107-108` 记录的拿错公式事故模式）。用户已否决。 |
| 用 `hfqday/qfqday` 反推因子 | 实测比值非常数（见上），推不出干净阶梯。 |

**后果**：多一次取数请求（不复权那份）；因子表让「不复权」标签为真，
并且**将来要做港股除权跳空检测时可以直接用**。

---

## 6. 周期与深度

### 6.1 周/月线一律本地聚合（沿用 `periods.py`）

`periods.py:29 DERIVED = {"week": "day", "month": "day"}` 已经规定周/月由**本地日线**聚合，
口径写在模块 docstring 里（自然周 ISO / 自然月；开=首个交易日开盘、收=最后交易日收盘、
高/低=区间极值、量/额=求和；时间戳=该周期最后一个交易日；**当周/当月没走完也出一根**，
由 `is_complete` 说出来）。

**本设计不改这条**：港股/美股也只取日线，周/月本地聚合。好处是
「同一只票换个周期不会换一套笔/段/中枢」这条原则对新市场自动成立。

### 6.2 深度表（写进页面提示与 `ARCHITECTURE.md`）

| 市场 | 日线可得深度 | 30分/5分 可得深度 |
|---|---|---|
| A股 | 全历史 | **已上线**（`MIN_PERIODS`）；深度未实测，不写数字 |
| 港股 | **2004-06-16 起，实测 5498 根**（§6.3 的 end 回退翻页） | **不可得** |
| 美股指数 | **2004-01-02 起**（`.DJI` 5726 根 / `.IXIC` 5724 根） | 30分 1023 根（≈3.8 个月）/ 5分 1023 根（≈13 个交易日） |

### 6.3 港股日线翻页（首次全量同步的实现依据）

> **★ 2026-10-05 重测。** 初稿这一节是按 `fqkline` 的**日期区间**语义写的（2 年一段）。
> §3.2 (a3) 查明前复权必须走 `hkfqkline`，而**那个控制器不认 `start`、只认 `end`**
> （§3.4 坑 11）—— 按初稿的写法翻页会得到 640 根滑窗，**大部分是重复的**。
> 已按 `hkfqkline` 的真实语义重测，结果如下。

**正确做法：固定 `n=640`，把 `end` 往前退到「上一批首行的前一天」，循环到取不到新行为止。**

实测（`/tmp/probe_hkfq_page.py`，`n=640`，起点 `end=2026-10-05`）：

```
step=0  end=2026-10-05  rows=640  span=2024-02-26..2026-10-02  new=640
step=1  end=2024-02-25  rows=640  span=2021-07-23..2024-02-23  new=640
step=2  end=2021-07-22  rows=640  span=2018-12-14..2021-07-22  new=640
step=3  end=2018-12-13  rows=640  span=2016-05-13..2018-12-13  new=640
step=4  end=2016-05-12  rows=640  span=2013-10-07..2016-05-12  new=640
step=5  end=2013-10-06  rows=640  span=2011-02-28..2013-10-04  new=640
step=6  end=2011-02-27  rows=640  span=2008-07-30..2011-02-25  new=640
step=7  end=2008-07-29  rows=640  span=2005-12-21..2008-07-29  new=640
step=8  end=2005-12-20  rows=378  span=2004-06-16..2005-12-20  new=378
   ⇒ 自然终止（再往前已无数据）
合计 **5498 根不重复**，最早 2004-06-16，最新 2026-10-02
```

**每一步都恰好新增 640 行（最后一步 378），零重复、零缺口** —— 相邻两批的首尾
只差一个周末（如 step0 首行 2024-02-26，step1 末行 2024-02-23，中间 24/25 日是周六日）。

**实现要点**：

1. `n` 用 **640**。实测 `n=1500` 也可用，但 **`n=2000` 会静默降级成 640**（§3.4 坑 11），
   所以 640 是「已验证且安全」的值。
2. **终止条件 = 本批没有新行**（不是「本批 0 行」——最后一批仍有 378 行）。
   另外把「已到 2004-06-16」也作为硬停止，防止数据商行为变化时无限循环。
3. `end` 的推进量必须是**上一批首行减一天**，不能用固定的日历步长
   （节假日会让固定步长漏行或重叠）。
4. 增量同步（库里有数据）= 从 `last_ts + 1 天` 到今天**一次**请求；
   若间隔超过 640 根，退回同样的 end 回退逻辑。

> **不要照搬初稿的「2 年一段」**：那是 `fqkline` 的语义，用在 `hkfqkline` 上会重复。
> **也不要用「5 年一段」**：没有任何实测支持。

---

## 7. 数据层改动

### 7.1 新增模块

| 文件 | 职责 |
|---|---|
| `src/chanlun/data/sources.py` | **市场分派层**：`normalize_code` / `fetch_bars` / `store_key_for` / `source_of` / `capabilities` |
| `src/chanlun/data/tencent_source.py` | 腾讯：港股日线（含翻页）、港股/美股/A股 分时 1 分钟 |
| `src/chanlun/data/sina_source.py` | 新浪：美股指数日线、美股 5/15/30/60 分 |

分层理由（避免循环 import）：

```
markets.py        ← 只认号段与前缀，不 import 任何数据源
store.py          ← import markets
baostock_source.py← import markets
tencent_source.py ← import markets, types
sina_source.py    ← import markets, types
sources.py        ← import 上面全部（唯一的分派点）
```

`api.py` / `sync.py` / `__main__.py` 都从 `sources` 拿 `fetch_bars` 与 `normalize_code`，
**不再直接从 `baostock_source` 拿 `fetch_bars`**。

#### 7.1.1 ★ 取数层必须校验行数（本次复查新增的硬要求）

**判据：`code == 0` 不等于拿到了数据。** 依据是 §3.2 (a2) 那条实测 ——
腾讯通用 `fqkline` 对港股分钟请求返回 `code=0` 却只有 1 根 bar，
`n` 与日期区间全部无效。**如果取数层只看返回码，就会把 1 根 bar 当成「30 分钟数据」
写进库，页面画出 1 根 K 线，用户看到一片空白却没有任何报错。**

所以 `tencent_source.py` / `sina_source.py` 的每个 `fetch_*` 都要有：

```python
MIN_ROWS = {"day": 30, "week": 8, "month": 4, "60": 30, "30": 30, "15": 30, "5": 30}

if len(df) < MIN_ROWS[period]:
    raise DataSourceError(
        f"{vendor_symbol} 的 {period} 只回 {len(df)} 行"
        f"（少于 {MIN_ROWS[period]}），数据商可能改了行为 —— 拒绝落库"
    )
```

- 用**现有的** `data/types.py:21 DataSourceError`，不新造异常类。
- 阈值取「够不够建出结构」而不是「跟历史比」：日线 30 根足以形成笔与中枢，
  分钟线 30 根同理。**阈值只要低到不误伤正常取数即可**，
  它的作用是拦住「1 根」这类明显异常，不是做质量门禁。
- `sync.py:120` 现有 `except` 会把异常吞成「同步失败」的行，
  **这正是想要的行为** —— 失败要显示成失败，不能显示成成功。

> 这条要求**来自实测**：初稿没有它，因为初稿以为港股分钟线是 `bad params`（会抛异常）。
> 发现它变成 `code=0` + 1 根之后，才意识到「静默假成功」是真实风险。

**★ 第二道护栏（同日第二次复查新增）：校验返回的键名与请求的口径一致。**

腾讯把**口径写在返回键名里**（§3.2 (a3) / §3.4 坑 10）：

| 请求 | 港股**个股**返回 | 港股**指数**返回 |
|---|---|---|
| `hkfqkline` + `qfq` | `qfqday` ✅ | **`day`**（指数无除权，退化成原始价） |
| `hkfqkline` + `hfq` | `hfqday` ✅ | **`day`** |
| `fqkline`（任意复权词） | `day` | `day` |

实测：`hkHSI` / `hkHSTECH` 走 `hkfqkline` + `qfq` 返回的键是 **`day`**，
数值与 `fqkline` **完全相同**（n=641，首行 2024-02-26，收 16634.740）。
**指数没有复权概念，所以数据商退化成原始价 —— 这对指数是正确的**
（`sync.adjust_for` 对指数本来就返回 `"3"`）。

**但对个股就是危险的**：若某天 `hk00700` 的 `qfq` 请求也返回 `day`，
我们就会把**不复权价当成前复权**存进库 —— 正是 §3.2 (a3) 拦下的那个错误模式，
而且**行数护栏拦不住它**（行数完全正常）。

```python
expect = {"qfq": "qfqday", "hfq": "hfqday"}.get(mode)
if expect and markets.is_hk(code) and not markets.is_index(code):
    if key != expect:
        raise DataSourceError(
            f"{vendor_symbol} 请求口径 {mode} 但返回键是 {key!r}（期望 {expect!r}）"
            f" —— 口径不符，拒绝落库"
        )
```

- **只对港股个股断言**：港股指数（合法退化）与 A 股（baostock，无键名概念）不受影响。
- 这是「测量必须能失败」的一个具体应用：`key` 与 `mode` 的关系是**可证伪的**，
  不像「行数 > 30」那样对多数改动都恒真。
- **单测要能红**：喂一个 `hk00700` + `qfq` 但键名是 `day` 的 fixture ⇒ 必须抛
  `DataSourceError`（§10.1 的 `test_fetch_rejects_wrong_adjust_key`）。


> **保留打桩点的关键**：`__main__.py:54` 现在 `from .data.baostock_source import fetch_bars, …`，
> `:542` 把它当 `fetch=` 传给 `sync_one`，命令行测试靠 `monkeypatch.setattr(cli, "fetch_bars", fake)`
> 断网（`tests/test_cli.py` 里 ~20 处）。
> **改法**：把 `:54` 那一行拆成两条 —— `fetch_bars` 改从 `sources` 导入，
> `strip_bs_code` / `to_bs_code` 仍从 `baostock_source` 导入。
> 名字不变、调用点不变，**全部现有命令行测试无需修改**。

### 7.2 符号映射（落库键 → 取数符号）

写在一张显式表里，**不猜**。三个坑都在下面这段里标出来了：

```python
# sources.py
def vendor_symbol(code: str) -> tuple[str, str]:
    """(源名, 取数符号)。落库键 → 数据商要的写法。

    坑 1：必须用 store.market_of，不能用 markets.market_of ——
          markets.market_of 不认前缀（它只查号段表），会返回 "sz"。
    坑 2：落库键是小写的（§4.2 决策），但数据商要大写 ——
          hk.hsi → hkHSI、us.dji → .DJI。少了 .upper() 会静默拿到空数据。
    坑 3：新浪美股指数必须带点前缀，腾讯港股指数必须不带点。
    """
    bare = code.split(".", 1)[1]          # store.bare_code(code)，此处等价
    market = store.market_of(code)        # 坑 1
    if market == "hk":
        return "tencent", "hk" + bare.upper()     # hk.00700 → hk00700；hk.hsi → hkHSI
    if market == "us":
        return "sina", "." + bare.upper()         # us.dji → .DJI（坑 2+3）
    return "baostock", baostock_source.to_bs_code(code)   # sh.600000 → sh.600000
```

**映射表（规范键 → 取数符号），实现时按此写单测**：

| 规范键 | 源 | 取数符号 | 实测 |
|---|---|---|---|
| `hk.00700` | tencent | `hk00700` | `code=0`；**日线前复权 `hkfqkline`+`qfq` ⇒ `qfqday` 640 行** |
| `hk.hsi` | tencent | `hkHSI` | `code=0`；日线 ⇒ 键 `day`，641 行（指数无复权） |
| `hk.hstech` | tencent | `hkHSTECH` | `code=0`；日线 ⇒ 键 `day`，641 行 |
| `us.dji` | sina | `.DJI` | n=5726，首行 2004-01-02 |
| `us.ixic` | sina | `.IXIC` | n=5724 |
| `sh.600000` | baostock | `sh.600000` | 现状 |

**端点/口径分派表（★ 2026-10-05 新增 —— 光有符号不够，还得选对控制器）**：

| 品种 | 落库口径 | 控制器 | 复权词 | 期望键名 |
|---|---|---|---|---|
| 港股个股 日线 | 前复权 | `hkfqkline` | `qfq` | `qfqday` |
| 港股个股 日线（因子表用） | 不复权 | `fqkline` | （被忽略） | `day` |
| 港股指数 日线 | 不复权 | `hkfqkline` | `qfq` | `day`（合法退化，§7.1.1） |
| 美股指数 日线 | 不复权 | 新浪 `getDailyK` | — | `d/o/h/l/c/v/a` 数组 |

> **不要**给港股指数换成 `fqkline`：两个控制器对指数返回**逐字节相同**的数据，
> 统一走 `hkfqkline` 少一个分支。**但个股必须分开** —— 这正是 §3.2 (a3) 的教训。

反例（**必须写进单测，防止有人"顺手"去掉 `upper()`**）：
`hk hsi`（小写拼接）与 `.dji`（小写点）在实测中都是 `param error` / `var x=(null);`。

### 7.3 `sync.py` 分派

| 位置 | 现状 | 改成 |
|---|---|---|
| `:19` | `from .baostock_source import fetch_bars, to_bs_code` | `from .sources import fetch_bars, store_key_for` |
| `:96` | `fetch = fetch or fetch_bars` | 不变（`fetch_bars` 现在就是分派函数） |
| `:103` | `key = markets.store_key(to_bs_code(code))` | `key = store_key_for(code)`（A 股内部仍走 `to_bs_code` 校验） |
| `:108` | `df = fetch(to_bs_code(key), period, start, end, adjust=adjust)` | `df = fetch(key, period, start, end, adjust=adjust)`；**符号映射下沉到各源内部**，`sync` 只传落库键 |
| `:98` | `end = cal.last_trading_day(dt.date.today())` | `end = cal_for(market).last_trading_day(...)`（§7.4） |

`sync_one` 的**签名不变**（仍是 `code, period, *, full, since, cal, fetch`），
所以 `__main__.py:542` 的调用点、`tests/data/test_sync.py` 的桩都不用改。

### 7.4 交易日历

`calendar.py` 现在只有 A 股日历（`_fetch_from_baostock`，兜底 `_weekday_fallback`）。
港股与美股节假日与 A 股不同，**不能共用**：A 股休市而港股开市时，
`last_trading_day()` 会把 `end` 截到上一个 A 股交易日，港股当天数据就静默少一根。

改法（最小）：

```python
# calendar.py 新增
def weekday_calendar() -> Calendar:
    """周一~周五。用于港股/美股：不做节假日表，靠源返回 0 行自然跳过。"""
    return Calendar(_weekday_fallback(), source="weekday")
```

`sync.py` 里 `cal_for(market)`：`sh/sz/bj` ⇒ `get_calendar()`；`hk/us` ⇒ `weekday_calendar()`。

**为什么可以不用真节假日表**：同步是**增量**的，起点是 `last_ts + 1 天`
（`sync.py:36 start_for`）。节假日落在区间里只会让源少返回几行，`upsert` 按 `ts` 去重，
不会写脏数据；下一次同步把同一段再取一遍，成本可忽略。
真正的风险只有一个 —— **`end` 被截短**，所以 `end` 必须用「周一~周五」的宽松日历
（宁可多取一天，不可少取一天）。

### 7.5 落库布局

```
data/day/hk/00700.parquet      data/30/hk/…（不会产生：港股无分钟）
data/day/hk/hsi.parquet        data/day/us/dji.parquet
data/day/hk/hstech.parquet     data/day/us/ixic.parquet
```

**文件名是小写的**（`store.bare_code` 做 `.lower()`，`store.py:44`），
所以 `hk.hsi` → `hsi.parquet`、`us.dji` → `dji.parquet`。这是 §4.2 那条
「落库键一律小写」决策的必然结果，**读写两侧都走 `bare_code`，round-trip 一致**。

`store.path_for` 已经按 `data_root()/period/market_of(code)/f"{bare_code(code)}.parquet"`
拼路径（`store.py:56`），**只要 `markets.market_of` 认得 `hk`/`us` 前缀，路径自动正确**。
`bare_code`（`:37`）会把 `hk.` 剥掉，得到 `00700.parquet` —— 市场由**父目录**表达，
与现有 A 股布局（`data/day/sh/600000.parquet`）同构。
**`store.py` 一行都不用改**（实测见 §4.2）。

### 7.6 `meta.py` 改动

| 位置 | 改动 |
|---|---|
| `:344 DEFAULT_WATCHLIST` | 追加 4 条：`("hk.hsi","恒生指数")`、`("hk.hstech","恒生科技指数")`、`("us.dji","道琼斯")`、`("us.ixic","纳斯达克综合")`。**键用小写**（§4.2 决策）；**追加在 A 股 7 条之后**，不动已有顺序。 |
| `:279 name_of` | 不改。HK 个股名字来自 `watchlist.name`（`add_watch` 已存，`:302`），HK/US 指数名字来自 `DEFAULT_WATCHLIST` 兜底（`:290`）。 |
| `:355 seed_watchlist` | 不改。`replace=False` 只补缺的、保持用户排好的位置（`:361-362`），所以已有用户重跑 `seed-watchlist` 会把这 4 条**追加到末尾**，不会打乱他们的顺序。 |
| `:137 set_sync` | 不改。`adjust` 由 `sync.adjust_for` 决定，港股个股 `"2"`、港股/美股指数 `"3"`。 |

**必须一并改的两处测试**（AGENTS.md：口径被取代的测试要**重写，不许削弱**）：

| 测试 | 现状 | 改成 |
|---|---|---|
| `tests/test_cli.py:299` | 函数名 `test_seed_watchlist_resets_the_pool_to_the_seven_indices` | 改名 `…_to_the_default_indices`（断言本身是 `== [c for c,_ in meta.DEFAULT_WATCHLIST]`，是动态的，**不用削弱**） |
| `tests/web/test_api_periods_watchlist.py:207` | 断言消息 `"默认池就是那 7 个指数"` | 改成 `"默认池就是 DEFAULT_WATCHLIST 那几条"` |

> `tests/test_cli.py:330` 的 `assert all(adjust == "3" for _code, adjust in seen)`
> 在追加 4 条指数后会**多检查 4 次**，因此 `markets.is_index` 必须先学会 hk/us 指数（§4.3），
> 否则这条测试会红 —— 这正是它该有的行为。

---

## 8. Web API 改动

### 8.1 代码归一（`api.py:56`）

**实测的「改之前」**（这是 R1/R2/R3 全都走不通的**唯一直接原因**）：

```
_CODE_RE.match("hk.00700") => None
_CODE_RE.match("hk.HSI")   => None
_CODE_RE.match("us.DJI")   => None
normalize_code("hk.00700") => HTTPException 400: 无法识别的代码: 'hk.00700'（应为 6 位数字，可带 sh./sz./bj. 前缀）
normalize_code("us.DJI")   => HTTPException 400: 无法识别的代码: 'us.DJI'（应为 6 位数字，可带 sh./sz./bj. 前缀）
```

```python
# 现状（api.py:56）
_CODE_RE = re.compile(r"^(?:(sh|sz|bj)\.?)?(\d{6})$")

# 改成（re.I 收大小写，返回值统一小写，见 §4.2）
_CODE_RE = re.compile(
    r"^(?:(?:(sh|sz|bj)\.?(\d{6}))|(?:hk\.(\d{5}|[a-z]{2,8}))|(?:us\.([a-z][a-z0-9.\-]{0,9})))$",
    re.I,
)
```

`normalize_code`（`:83`）改为委托 `sources.normalize_code`（**校验只有一处**），
`ValueError` 仍转 400，但错误文案要提到新写法：

```
无法识别的代码: 'xx'（应为 A 股 6 位数字可带 sh./sz./bj. 前缀，
或 hk.00700 / hk.hsi / us.dji）
```

**返回值必须小写**：`normalize_code("us.DJI")`、`("us.dji")`、`("US.Dji")`
三者都要得到 `"us.dji"`，否则同一个文件会被三条 `meta.sync_state` 记录指着
（§4.2 真缺口三）。`"HK.00700"` 同理归到 `"hk.00700"`。

`normalize_adjust_or_400`（`:103`）不改 —— `raw|qfq|hfq` 三态对新市场同样有效，
只是港股/美股指数只允许 `raw`（由 `sync_state.adjust` 与页面按钮共同约束）。

### 8.2 `sync_blocker`（`api.py:856`）

现在只挡「A股指数 + 分钟周期」。新增两条：

```python
if markets.is_hk(code) and periods_mod.base_period(period) != "day":
    return (f"{code} 是港股，公开数据源不提供港股分钟线"
            "（腾讯只有日/周/月，新浪港股分钟接口不存在）。"
            "港股看日线/周线/月线即可（周月线由本地日线聚合）。")
```

美股不挡 —— 新浪有 5/15/30/60 分，但要在返回值里带上深度提示（§8.3）。
`sync_blocker` 是「这个周期根本补不了」的**唯一口径来源**（`:859-861`），
页面按钮与 `POST /api/sync` 的拒绝理由都从这里出，所以港股这条**只写一遍**。

### 8.3 新增 `GET /api/capabilities`（或并入 `/api/health`）

返回一张表，前端据此灰掉 tab 并显示提示：

```json
{
  "hk": {"day": {"ok": true,  "note": "2004-06-16 起"},
         "30":  {"ok": false, "reason": "公开源不提供港股分钟线"},
         "5":   {"ok": false, "reason": "公开源不提供港股分钟线"}},
  "us": {"day": {"ok": true,  "note": "2004-01-02 起，5726 根"},
         "30":  {"ok": true,  "note": "仅最近 1023 根，靠增量累积"},
         "5":   {"ok": true,  "note": "仅最近 1023 根（≈13 个交易日）"}}
}
```

**不新增端点也行** —— 可以让 `/api/bars` 在 `rows == 0` 时返回 `note`。
但「灰掉 tab」需要**在发请求之前**就知道，所以倾向于新增一个轻量端点，
或把它并进 `/api/health`（`api.py:547`）的返回值。

### 8.4 搜索（`api.py:571 /api/universe`）

现状只查 `universe` 表（A 股股票），`q` 只在 `code`/`name` 上做子串匹配（`:580-582`）。

新增 `GET /api/search?q=…&limit=20`：

1. 先查本地 `universe` + `watchlist`（离线可用，A 股与已加自选的港股）；
2. `q` 命中港股形态时（中文名或 5 位数字），调腾讯 `smartbox.gtimg.cn/s3/?q=<q>&t=all`；
3. 解析 `v_hint` 格式 `市场~代码~名称~拼音~类型^…`，**只保留 `hk` 的 `GP`（个股）与
   `ZS`（指数）**、`us` 的 `ZS`（本期美股只做指数）；
4. 港股指数只认白名单 `HSI`/`HSTECH`（§2.2 否掉了 `HSCEI`），其余 `ZS` 不返回；
5. **吐出的代码键必须走 `sources.normalize_code` 归成小写规范键**（§4.2）：
   腾讯返回的是 `hk~HSI~恒生指数`，我们必须转成 `hk.hsi`，
   否则「搜索加进来的」和「默认自选池里的」会变成两个键指向同一个文件；
6. 网络失败**降级**为「只返回本地结果」，不抛 500。
   `v_hint` 里的名字是 `\uXXXX` 转义，必须 `unicode_escape` 解码后再显示。

实测样例（正控）：

```
q=腾讯  ⇒ v_hint="sh~000847~腾讯济安~txja~ZS^hk~00700~腾讯控股~txkg~GP^hk~80700~…^us~tcehy.ps~…"
q=00700 ⇒ v_hint="hk~00700~腾讯控股~txkg~GP^jj~007005~…"
q=HSI   ⇒ v_hint="hk~HSI~恒生指数~hszs~ZS^hk~03136~恒指esgetf~…"
q=道琼斯 ⇒ v_hint="us~dji~道琼斯~dqs~ZS^…"
```

### 8.5 结构快照缓存键（`api.py:65-69`）

`_CACHE` 的键是 `(code, period, 复权口径, last_ts, 因子指纹, mode)`。
新市场**不需要改键**：`code` 已含市场前缀，`复权口径` 已含 `raw/qfq`，
港股个股「前复权 / 不复权」两个视图天然落在两个键上。
**唯一要确认的是「因子指纹」对无因子表的市场是否稳定** —— 港股/美股没有
`adjust_factor` 行，`get_adjust_factors` 返回空 DataFrame（`meta.py:422`），
指纹应当是一个常量；实现时要断言「同一只港股切两次前复权，命中同一个缓存键」。

---

## 9. 前端改动

### 9.1 代码输入框（`index.html:20`）

```html
<!-- 现状 -->
<input id="code" type="text" inputmode="numeric" placeholder="600000" …>

<!-- 改成 -->
<input id="code" type="text" placeholder="600000 / hk.00700 / hk.hsi / us.dji" …>
```

`inputmode="numeric"` **必须去掉**：手机上会弹纯数字键盘，`hk.` 和 `us.DJI` 打不出来。

### 9.2 周期 tab（`index.html:23-29`）

现有 5 个 tab：日线 / 周线 / 月线 / 30分 / 5分。改动：

- 当前票是港股时，把「30分」「5分」置 `disabled` 并加 `title` 说明原因（文案取自
  `sync_blocker`，§8.2，**不许前端另写一套**）；
- 当前票是美股指数时，两个 tab 可用，但加一个 `title`：「仅最近 1023 根」。

### 9.3 自选池（`index.html:59-76`）

- 加自选的输入框（`#watch-code`）已带 `list="universe-options"` 与搜索（`app.js:1366 fillUniverse`），
  把它的数据源从 `/api/universe` 换成 `/api/search`（§8.4）即可；
- 港股个股的名字在 `add_watch` 时由搜索结果带进去，存进 `watchlist.name`。

### 9.4 复权标签

港股个股在图表右上角加一个「不复权」chip，点击切 `adjust=raw`（URL 参数已支持）。
港股/美股指数**不显示**这个 chip（它们只有不复权一种口径，给了按钮反而误导）。

### 9.5 文案扫描（AGENTS.md §5 第 5 步）

`grep -rn "6 位数字\|沪深\|A股\|只支持" src/chanlun/web/static/`，
把所有「只支持 A 股」类的旧口径写死的文案找出来改掉。
**必须从 DOM dump 里读文案**，不许只看截图（§4.4）。

---

## 10. 测试计划

### 10.1 新增测试

**分两类，必须分清**（AGENTS.md §5：「测量必须能失败」）：

- **【红】** = 修复前**必须失败**的测试。它是修复的**证据**。
- **【锁】** = 修复前**已经通过**的测试。它是**回归锁**，防止后来人把已经对的东西改坏；
  **不能拿它当修复证据**。

| 测试 | 断言 | 类 | 修复前的实测行为 |
|---|---|---|---|
| `tests/data/test_markets_hk_us.py::test_is_index_knows_hk_us` | `is_index("hk.hsi") is True`、`is_index("us.dji") is True`、`is_index("hk.00700") is False` | **【红】** | 实测全 `False` |
| `…::test_to_bs_code_rejects_hk_us` | `to_bs_code("hk.00700")` **抛** `ValueError`，message 含「不是 A 股」 | **【红】** | 实测**不抛**，返回 `"hk.00700"`（§4.2 真缺口一） |
| `…::test_to_bs_code_lowercases_are_still_rejected` | `to_bs_code("us.dji")` 抛错 | **【红】** | 实测返回 `"us.dji"` |
| `…::test_needs_prefix_hk_us` | `needs_prefix("hk","00700") is True`、`needs_prefix("us","dji") is True`、`store_key("hk.00700")=="hk.00700"` | **【锁】** | **已经通过**（§4.2 实测） |
| `…::test_store_path_puts_market_in_directory` | `path_for("hk.00700","day")` 以 `day/hk/00700.parquet` 结尾；`path_for("us.dji","day")` 以 `day/us/dji.parquet` 结尾 | **【锁】** | **已经通过**（§4.2 实测） |
| `tests/data/test_tencent_source.py::test_parse_day_rows` | 用 §3.3 那条命令的真实响应（存成 fixture）解析出 `ts/open/high/low/close/volume` 六列，且 `ts` 是 `YYYY-MM-DD` | **【红】** | 模块不存在 |
| `…::test_hk_daily_paging_stops_at_2004` | 翻页循环在「某段 0 行」时停止，且总行数 == fixture 的并集 | **【红】** | 模块不存在 |
| `tests/data/test_sina_source.py::test_us_daily_parses_index_jsonp` | `.DJI` fixture → 5726 行，首行 `2004-01-02`；JSONP 前缀被剥掉 | **【红】** | 模块不存在 |
| `…::test_us_minute_type_matrix` | `type=30` fixture → 1023 行且首行 `2026-06-11 12:00:00` | **【红】** | 模块不存在 |
| `tests/data/test_sources_dispatch.py::test_vendor_symbol_table` | §7.2 那张映射表**逐行**成立：`hk.00700→("tencent","hk00700")`、`hk.hsi→("tencent","hkHSI")`、`us.dji→("sina",".DJI")` | **【红】** | 模块不存在 |
| `…::test_vendor_symbol_needs_upper` | 若去掉 `.upper()`，上一条必须变红（用一个 monkeypatch 版本断言 `"hkhsi"` ≠ 期望） | **【红】** | 模块不存在；这条是 §7.2「坑 2」的护栏 |
| `…::test_fetch_bars_dispatches_by_market` | `hk.` → tencent、`us.` → sina、A 股 → baostock（用假源计数） | **【红】** | 分派层不存在 |
| `…::test_fetch_rejects_too_few_rows` | 喂一个「`code=0` 但只有 1 根」的港股分钟 fixture ⇒ **抛 `DataSourceError`**，message 含行数 | **【红】** | §7.1.1 的护栏；模块不存在 |
| `…::test_fetch_rejects_too_few_rows_negative_control` | **同一个 fixture 换成 320 根 ⇒ 不抛** | **【负控】** | 证明上一条拦的是「行数太少」而不是「fixture 本身坏」 |
| `…::test_fetch_rejects_wrong_adjust_key` | 喂一个 `hk.00700` + `qfq` 请求但返回键为 **`day`** 的 fixture ⇒ **抛 `DataSourceError`**，message 含两个键名 | **【红】** | §7.1.1 第二道护栏；**行数护栏拦不住它**（行数正常） |
| `…::test_fetch_accepts_index_degraded_key` | **同一 fixture 换成 `hk.hsi`**（指数）⇒ **不抛**，按不复权收下 | **【负控】** | 证明上一条只对**个股**断言，指数的合法退化不被误伤 |
| `…::test_hk_day_paging_walks_end_backwards` | 假源按「`end` 优先」语义返回：断言翻页**不重复、不缺口**，且终止于无新行 | **【红】** | §6.3；防有人照初稿写成「2 年一段」 |
| `…::test_infer_factors_hk_roundtrip` | 用一对同区间 raw/qfq fixture 跑 `adjust.infer_factors`，再用 `unapply_adjust` 还原 ⇒ 与 raw 逐行相等 | **【红】** | §5.4；证明「不复权」标签**为真**（因子空表时该断言会失败） |
| `tests/web/test_api_hk_us.py::test_normalize_code_accepts_hk_us` | `normalize_code("hk.00700")=="hk.00700"`、`("HK.00700")` 同、`("us.DJI")==("us.dji")==("US.Dji")=="us.dji"` | **【红】** | 实测全 400（§8.1） |
| `…::test_sync_blocker_explains_hk_minute_gap` | 港股 + `30` 的 blocker 含「不提供港股分钟线」；美股 + `30` 返回 `None` | **【红】** | 现在港股 `to_bs_code` 放行 ⇒ 返回 `None`（假装能补） |
| `…::test_search_returns_hk_by_chinese_name` | `q=腾讯` ⇒ 结果含 `hk.00700`（用 fixture，不打真网） | **【红】** | `/api/search` 不存在 |
| `tests/data/test_watchlist_order.py::test_default_watchlist_contains_hk_us_indices` | 4 条新键（小写）都在，且**排在 A 股 7 条之后** | **【红】** | 现在没有 |

### 10.2 必须**重写**（不是削弱）的既有测试

- `tests/test_cli.py:299` 函数改名（见 §7.6 表）；
- `tests/web/test_api_periods_watchlist.py:207` 断言消息更新；
- `tests/data/test_markets.py:110 test_is_index` 的参数化表**追加** hk/us 行
  （`("hk.hsi", True)`、`("hk.HSI", True)`、`("us.dji", True)`、`("us.DJI", True)`、
  `("hk.00700", False)`），不删旧行。
  注意 `is_index`（`markets.py:123`）本来就先 `.lower()`，所以**大小写两种写法都必须过** ——
  这条同时也是 §4.2「落库键小写」的护栏。

### 10.3 全量回归

```bash
cd /Users/zzz/workspace/chanlun && ../.venv-chanlun/bin/pytest -q -p no:randomly
```

基线：`966 passed, 12 deselected, 1 warning`。**改完必须仍是 0 失败**，
且新增测试数要能从「passed 数增加」上看出来。

### 10.4 浏览器核对（AGENTS.md §4 第 4 条）

改了 `web/static/` 要**刷新浏览器**；改了 `src/chanlun/**/*.py` 要**重启看盘页**
（`web/app.py` 用 `uvicorn.run(...)` 且没有 `--reload`）。

核对项（每条都要正负对照）：

1. 输 `hk.00700` → 日线出图，**页面显示根数受 `api.py:62 DEFAULT_LIMIT = 1200` 限制**
   （即 ≤1200），最后一根是最近交易日。
   > 注意别把「库里有 5000+ 根」和「图上画了 5000+ 根」混为一谈 ——
   > 显示上限是 1200，验证落库深度要看 `store.row_count`，不是看图。
2. 输 `hk.hsi` → 日线出图；切「30分」tab → **灰掉**且 `title` 文案与 `sync_blocker` 一致；
3. 输 `us.dji` → 日线出图（同样 ≤1200 根）；切「30分」→ 出图且提示「仅最近 1023 根」；
4. 自选搜索框输「腾讯」→ 出现「腾讯控股 hk.00700」，加入自选后名字正确；
5. 港股个股切「不复权」chip → 价格变化只在除权日附近（若该票无除权记录则**应当完全一致**，
   页面要如实标「无除权记录」，见 `adjust.py:83-85`）；
6. 从 **DOM dump** 读 tab 的 `disabled` 与 `title`，不看截图。
   **输入大小写两种都要试**：`hk.HSI` 与 `hk.hsi` 必须落到同一个键、同一张图。

---

## 11. 已知空白与风险

| # | 空白 / 风险 | 处理 |
|---|---|---|
| G1 | **港股 30分/5分 不可得** | 灰掉 + 写明原因；记进 `ARCHITECTURE.md` §5 已知空白。证据链见 §3.2，**不要重复探测** |
| G2 | 美股分钟线只有 1023 根，无翻页 | 靠 `store.upsert` 增量累积；页面标注「仅最近 1023 根」 |
| G3 | 港股日线只能回溯到 2004-06-16 | 页面标注；不做假历史 |
| G4 | 腾讯/新浪都是**非官方、无 SLA** 的接口 | 全部取数走 `sources.fetch_bars` 一个口子，将来换源只改一处；失败时 `sync_one` 记 `error` 不抛异常（`sync.py:120` 现有行为） |
| ~~G5~~ | ~~港股无复权因子表~~ **→ 已解，见 §5.4** | 实测证明原始价可直接取（`fqkline.day`），因子表由 `adjust.infer_factors` 反推 ⇒ **「不复权」标签是真的**（M18 验收）。**不再是空白。** |
| G5b | 港股 `qfq`/`hfq` 的锚点不一致（比值漂移约 4%） | 不影响落库（我们只存 `qfq` + 自建因子表），但**影响交叉校验**：`hfqday/qfqday` 不能当常数断言（§5.4 硬要求 2） |
| G5c | 因子表只在**同步时**构建；首次全量同步前，「不复权」标签无因子可用 | 与 A 股现状一致（A 股因子也来自 `adjust_factor` 表）；同步后即正常 |
| G6 | 所有结论依赖本机代理链路（§3 前提） | 代理变化后重跑 §3.3 的命令；`ARCHITECTURE.md` 里注明这一条 |
| G7 | 美股个股符号形态不统一（`usAAPL` vs `us.AAPL` vs `usAAPL.OQ`） | 本期不做美股个股（§2.2）；将来要做必须重新探测 |
| G8 | 交易日历用「周一~周五」，不含港美节假日 | 只影响取数冗余，不影响正确性；`end` 用宽松日历，宁多取不少取（§7.4） |

---

## 12. 验收标准（可失败的测量）

> AGENTS.md §5：「**测量必须能失败。** 如果一个指标无论怎么改都是满分，它没有在测量什么东西。」
> 下面每条都写清**改之前**应当得到什么数。

| # | 测量 | 改之前（**实测**） | 改之后 | 类 |
|---|---|---|---|---|
| M1 | `pytest -q -p no:randomly` | `966 passed, 12 deselected` | `966 + 新增数` passed，0 失败 | 回归 |
| M2 | `python -c "from chanlun.data.markets import is_index; print(is_index('hk.hsi'))"` | **`False`** | `True` | **【红】** |
| M3 | `python -c "from chanlun.data.markets import is_index; print(is_index('us.dji'))"` | **`False`** | `True` | **【红】** |
| M4 | `python -c "from chanlun.data.baostock_source import to_bs_code; to_bs_code('hk.00700')"` | **不抛异常，返回 `'hk.00700'`**（静默放行） | 抛 `ValueError`，含「不是 A 股」 | **【红】** |
| M5 | `curl -s 'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk00700,m30,,,2000,qfq'` | ★ `code=0` 但**行数 = 1**（初稿记的 `bad params` **已过时**，见 §3.2 (a2)） | **行数仍 ≤ 1**（`n=2000` 也一样） | **负控** |
| M6 | 端到端 `POST /api/sync` `{"code":"hk.00700","period":"day"}` | 400「无法识别的代码」 | 200，`rows > 2000` | **【红】** |
| M7 | 端到端 `POST /api/sync` `{"code":"us.dji","period":"day"}` | 400「无法识别的代码」 | 200，`rows > 5000` | **【红】** |
| M8 | `POST /api/sync` `{"code":"hk.hsi","period":"30"}` | 400「无法识别的代码」（**碰巧也是 400，但理由不对**） | 400，detail 含「不提供港股分钟线」 | **【红】** |
| M9 | `GET /api/search?q=腾讯` | 0 条 | ≥1 条且含 `hk.00700` | **【红】** |
| M10 | 浏览器：港股票的「30分」tab | `disabled == false` | `disabled == true` 且 `title` 非空 | **【红】** |
| M11 | `grep -c "hk.hsi" src/chanlun/data/meta.py` | `0` | `1` | **【红】** |
| M12 | `python -c "from chanlun.web.api import normalize_code; print(normalize_code('US.Dji'))"` | `HTTPException 400` | `'us.dji'`（与 `us.DJI` 同键） | **【红】** |
| ~~M13~~ | ~~复权口径自洽：港股个股 `adjust=qfq` 与 `adjust=raw` 在**无除权记录**时逐行相等~~ **→ 已由 M16 取代** | — | — | — |
| M14 | `needs_prefix("hk","00700")` / `store_key("hk.00700")` / `path_for("hk.00700","day")` | **已经是对的**（`True` / `"hk.00700"` / `…/day/hk/00700.parquet`） | **不变** | **【锁】** |
| M15 | 取数层行数护栏：喂「`code=0` + 1 根」的港股分钟 fixture | 无此护栏（模块不存在） | 抛 `DataSourceError`，message 含行数 | **【红】** |
| M16 | **★ 口径护栏**：`hk.00700` + `qfq` 但返回键为 `day` 的 fixture | 无此护栏（模块不存在） | 抛 `DataSourceError`，message 含 `day` 与 `qfqday` | **【红】** |
| M17 | **★ 指数退化负控**：同一 fixture 换成 `hk.hsi` ⇒ 键为 `day` | — | **不抛**，按不复权收下 | **【负控】** |
| M18 | **★ 因子表往返**：`infer_factors(raw, qfq)` → `unapply_adjust(qfq, factors, "raw")` | 因子空表 ⇒ **逐行等于 qfq**（`adjust.py:112`，即标签是假的） | **逐行等于 raw** | **【红】** |
| M19 | **★ `fqkline` 忽略复权词**：同一窗口四个复权词的响应 sha256 | 四者**相同** | **仍相同**（数据商行为，非我方修复） | **负控** |
| M20 | **★ `hkfqkline` 的 `n` 静默降级**：`n=2000` | 返回 **640** 行 | 仍为 640（数据商行为） | **负控** |
| M21 | **★ 港股日线翻页**：end 回退、`n=640`，直到无新行 | 无翻页代码 | **5498 根不重复**，最早 2004-06-16 | **【红】** |

**M5 是负控**：它必须**继续失败** —— 判据是**行数 ≤ 1**，不是返回码。
如果哪天它开始返回成百上千根，说明数据商补上了港股分钟线，
G1 应当撤销，港股分钟线可以按美股分钟线那样接进来 —— 这是这条测量存在的意义。
> **不要把它写成「期望 `bad params`」**：2026-10-05 复查时它已经从
> `bad params` 变成了「`code=0` + 1 根」。写成返回码的话，下次数据商再动一下，
> 负控会以「测试挂了」的形式报警，而不是以「结论失效」的形式。**判行数。**

**M14 是锁**：它**改之前就是对的**，所以它**不能**被写成「修复验证」。
记它是因为 §4.2 那三处真缺口很容易让人误以为整个前缀机制都要重做，
后来人据此「顺手重构」`store_key` 会把已经对的东西改坏。

**M16/M17 是一对**：同一条护栏，个股要红、指数要绿。**只写 M16 会写出一个
把所有港股都拒掉的实现**（指数的键本来就是 `day`）。这对是 §7.1.1 第二道护栏的可失败证明。

**M18 是本次复查新增的最重要一条**：它把「不复权标签是真的」变成一个**能失败的断言**。
`unapply_adjust` 在因子空表时直接返回入参（`adjust.py:112`），
所以**不做因子表的话 M18 的「改之后」列根本达不到** —— 这条测试会把 §5.4 从「建议」
变成「必须做」。数学依据：`apply_adjust` 前复权用 `scale = k`（`adjust.py:92`），
`unapply_adjust` 还原用 `scale = 1/k`（`adjust.py:115`），互为逆运算。

**M19/M20 是数据商行为的负控**（不是我们的修复项）：
它们记录的是腾讯的怪癖，**必须继续失败/保持原样**。
若哪天 M19 四个 sha 不再相同、或 M20 的 `n=2000` 真回 2000 行，
说明数据商改了行为 —— 那时 §3.2 (a3) / §3.4 坑 11 的结论要重测，
`vendor_symbol` 的分派表也要重新核对。**这就是这两条存在的意义。**

**M21 是 §6.3 的可复现验收**：`5498` 这个数字来自 `/tmp/probe_hkfq_page.py`，
脚本不入库，所以**只作为「量级参考」**；实现时的单测用假源断言
「不重复、不缺口、自然终止」（§10.1 的 `test_hk_day_paging_walks_end_backwards`），
**不要把 5498 写成硬断言** —— 数据商每多一个交易日它就会变。

> **本节的自我更正（2026-10-05）**：本文件初稿把 M2/M3/M4 写成
> 「`needs_prefix` 返回 `False`」「`path_for` 抛 `ValueError`」「`is_index` 返回 `False`」，
> 其中**前两条是错的** —— 实跑发现前缀机制早已正确（见 §4.2 实测块），
> 初稿是读代码推出来的，不是跑出来的。已按实测改正，并把两条降级为 M14【锁】。
> 记在这里是因为「读代码推断」正是本项目反复栽的那个坑。

---

## 13. 交付顺序

1. **`markets.py`**：`INDEX_CODES` + `is_index` 认 hk/us 指数（M2/M3 先绿）。
   **`store.py` 一行都不用改**（§4.2 实测）。
2. **`baostock_source.to_bs_code`**：显式拒绝 hk/us 前缀（M4 先绿）。
3. **`sources.py` + `tencent_source.py` + `sina_source.py`**：取数层（配 fixture 单测）。
   含 `vendor_symbol` 映射表与 `.upper()` 护栏（§7.2），
   **端点/口径分派表**（§7.2，个股走 `hkfqkline`、指数走 `hkfqkline` 但收 `day`），
   以及**每个 `fetch_*` 的两道护栏**：行数（§7.1.1 第一道，M15 先绿）
   与**键名口径**（§7.1.1 第二道，M16/M17 先绿）。
4. **港股日线翻页**：end 回退 + `n=640`（§6.3，M21 先绿）。
   **不要照搬初稿的「2 年一段」** —— 那是 `fqkline` 的语义。
5. **`sync.py` + `calendar.py`**：分派与宽松日历（M6/M7/M8）。
6. **港股因子表**（§5.4）：同步时用 `infer_factors(raw, qfq)` 建 `adjust_factor` 行（M18 先绿）。
   **这一步是「不复权」标签为真的前提**，不能省。
7. **`meta.py`**：默认自选池（小写键）+ 两处测试重写（M11）。
8. **`api.py`**：`normalize_code` 收 hk/us 并归小写 / `sync_blocker` / `/api/search` / capabilities（M9/M12）。
9. **前端**：输入框、tab 灰化、搜索、复权 chip、文案扫描。
10. **浏览器核对**（§10.4）+ 全量回归（M1）。
11. **文档**：`ARCHITECTURE.md` 新增 `D-43`（港股/美股接入，含被否决方案与 §4.2 三处缺口）、
    `CHANGELOG.md` `[Unreleased]` 记 `MINOR`。
    > 本设计**不改任何缠论判据**，所以 AGENTS.md §5 的「先写 `optimizer/theory/` 条目」
    > 这一步**不适用**；`ARCHITECTURE.md` 的 `D-xx` 与 `CHANGELOG.md` 仍照常写。
12. 提交（逐个列路径，**绝不 `git add -A`**）。

---

## 14. 本文件未覆盖的

- 港股/美股的**除权事件表**（`dividend_event`）与复权因子（`adjust_factor`）：本期不做（§5.3）。
- 港股/美股的**扫描 / 回测**：本期不做（§2.2）。
- 港股/美股在**通知推送**里的表现：未评估，默认沿用现有路径（推送文案里的市场名可能不准，
  实现时顺手核一下 `notify.py`）。
