# 按需拉取（换票即补最新） —— 设计文档

- 日期：2026-10-10
- 状态：**设计已批准，实施中** —— 实施计划见同目录的
  `docs/superpowers/plans/2026-10-10-on-demand-sync.md`（7 个任务，按 TDD 逐条落地）。
- 上游诉求：用户在 2026-10-10 否掉了"全市场补跑"，要求改成"进某只票/某个指数时才去抓最新"。
- 关联记录：待写入 `ARCHITECTURE.md` 的 **D-49**；`CHANGELOG.md` 的 `[Unreleased]`。
- **本文引用的行号是 2026-10-10 改动前的状态。** 本目录不在
  `optimizer/tools/check_doc_line_refs.py` 的默认扫描范围（`DEFAULT_DOC = ARCHITECTURE.md`，
  见 `optimizer/tools/check_doc_line_refs.py` 第 66 行），所以这些行号不会被基线指纹看住，
  改动落地后需要人工回查。

---

## 1. 背景与目标

### 1.1 用户诉求（原话）

> 我觉得其实也不用全市场补，就只是在我进入某一个股票或者某一个指数的时候，
> 它要自动去抓取这个最新的一个数据。

### 1.2 定时任务连续两个交易日没有交付（实测）

| 交易日 | 定时任务实际结果 | 证据 |
|---|---|---|
| 2026-10-08 | 挂在 socket 上 **6h14m**，一根 K 线都没落库 | `docs/evidence/2026-10-08-定时任务静默挂死.md`（D-47） |
| 2026-10-09 | 被数据源拉黑（`10001011 黑名单用户`），完成 **268** 只后熔断停批 | `docs/evidence/2026-10-09-零行同步抹掉水位.md`（D-48） |

10-08 那天的数据是**人工**在 10-09 00:01 之后重跑补上的：`sync_state.updated_at` 显示
`end_ts='2026-10-08'` 的 4955 条记录里，4936 条的写入时间落在 `2026-10-09 00 时`。

到 2026-10-10（周六）为止的结果：

- 全市场 5471 只里，**4955 只的最后一根 K 线是 `2026-10-08`**，`2026-10-09` 只有 268 只。
- 最小可复现的观测方式：`/api/structure` 的 `as_of`。

### 1.3 为什么"全市场补跑"不是答案（用户的判断成立）

```
select count(*) from universe;                -- 5471
select code, name from universe where code='sh.000001';  -- 空
```

`universe` 里**只有 A 股个股，没有任何指数**。而定时任务的同步范围就是 `meta.universe`
（10-09 的汇总原文：「同步：day: 成功 268 失败 22 跳过 35（共 5471 只）」）。

于是：

1. 全市场补跑**补不到指数**——而用户明确说了"某一个指数"。
2. 用户真正会打开的集中在自选池那十几只，成本是十几只票而不是 5471 只。

### 1.4 目标

进某只票/某个指数时，**它自己把最新的数据抓回来**，不需要人等定时任务、也不需要人点按钮。

---

## 2. 现状（实测，非推测）

### 2.1 机制已经有 80%，缺的只是"触发"

| 能力 | 位置 | 现状 |
|---|---|---|
| `POST /api/sync` | `src/chanlun/web/api.py` 第 1034-1035 行 | 202 + 后台线程 + 按 `(code, period)` 幂等。**已实现** |
| `GET /api/sync/status` | `src/chanlun/web/api.py` 第 1076-1077 行 | 轮询用，`idle/running/done/skipped/error` 五态。**已实现** |
| `sync_blocker` | `src/chanlun/web/api.py` 第 947 行 | "这个周期根本补不了"的唯一口径（例如指数没有分钟线）。**已实现** |
| 前端 `syncThisPeriod` | `src/chanlun/web/static/app.js` 第 458 行 | POST → `pollSync` → 完成后 `load()`。**已实现** |
| 前端 `finishSync` | `src/chanlun/web/static/app.js` 第 511 行 | 成功即重新 `load()` 重画。**已实现** |
| **触发点** | `src/chanlun/web/static/app.js` 第 217-219 行 | **只在 404（本地一条数据都没有）时给按钮，必须手点** |
| `setCode` | `src/chanlun/web/static/app.js` 第 1828 行 | 只改 `state.code`、刷按钮与自选高亮，**不取数** |

`src/chanlun/web/static/app.js` 第 1751 行还留着当时的欠账注释：

> 真正的「换票时同步」在 Task 4 挂进 `setCode`，这里先在清单到货后刷一次。

也就是说：**这个功能当年设计过，一直没挂上去。**

### 2.2 三个"能不能复用"的实测结论

**(a) `sync_one` 自己就会短路：不落后不发网络请求**

`src/chanlun/data/sync.py` 第 190-191 行：

```python
if start is None:
    return SyncOneResult(key, period, "skipped", adjust=adjust)
```

`start_for(...)` 在本地水位已到最新交易日时返回 `None`，于是**直接返回、不调 `fetch`**。
而"最新交易日"在 `src/chanlun/data/sync.py` 第 172 行取自 `cal.last_trading_day(today)`。

⇒ 按需同步的真实网络成本**只发生在数据确实落后的那一次**。

**(b) `/api/structure` 是当场读 parquet 现算的**

`src/chanlun/web/api.py` 第 713 行调用 `snapshot_of(...)`（定义在第 385 行）。
它是**每次请求都重新读 parquet 现算**的：第 406 行 `_period_frame(...)` 现读文件，
第 407-408 行的进程内缓存键是

```python
last_ts = str(full["ts"].iloc[-1])
key = (code, period, effective, last_ts, fingerprint, mode)
```

**键里带最后一根 bar 的时间戳**，所以补上新 bar 之后 `last_ts` 变、缓存自然失效、
下一次 `load()` 一定是新结构。只要把那一只票的 parquet 补上就够了——
**不需要跑快照/扫描/推送那一整套**（这也是方案能被接受的前提，实测确认过）。

**(c) "是否落后"不能拿裸字符串比：分钟周期的 `as_of` 带时间戳**

2026-10-10 同一时刻直接 `curl /api/structure` 的实测：

| 请求 | `as_of` |
|---|---|
| `600000` period=day | `2026-10-09` |
| `600000` period=week | `2026-10-09` |
| `600000` period=month | `2026-10-09` |
| `600000` period=30 | `2026-09-30 15:00` ← **带时间** |
| `sh.000001` period=day | `2026-09-30` |
| `sh.000300` period=day | `2026-09-30` |

（`600000` 是本次人工补过的，所以是 10-09；其余未补。）

⇒ 判据必须**只比日期部分**（`as_of[:10]`）。

### 2.3 缺口的另一面：指数从来没被定时任务覆盖过

```
select w.code, w.name, s.end_ts from watchlist w
  left join sync_state s on s.code = w.code and s.period = 'day';
```

| 代码 | 名称 | `end_ts` |
|---|---|---|
| `sh.000001` | 上证指数 | 2026-09-30 |
| `399001` | 深证成指 | 2026-09-30 |
| `399006` | 创业板指 | 2026-09-30 |
| `sh.000300` | 沪深300 | 2026-09-30 |
| `sh.000016` | 上证50 | 2026-09-30 |
| `sh.000905` | 中证500 | 2026-09-30 |
| `sh.000688` | 科创50 | 2026-09-30 |
| `600759` | ST洲际 | 2026-10-08 |
| `002309` | 中利集团 | 2026-10-08 |
| `hk.hsi` | 恒生指数 | 2026-10-05 |
| `hk.hstech` | 恒生科技指数 | 2026-10-05 |
| `us.dji` | 道琼斯 | 2026-10-02 |
| `us.ixic` | 纳斯达克综合 | 2026-10-02 |

**7 个 A 股指数全部停在 `2026-09-30`**，比个股还旧两个交易日。这不是"这次事故"造成的，
是"定时任务只同步 `universe`"这个结构性缺口造成的。按需拉取天然覆盖它——
`/api/sync` 走的是 `sync_one`，**不看 `universe` 成员资格**。

---

## 3. 被否决的方案

### 3.1 方案乙：新开 `GET /api/freshness` 端点

否决：`/api/structure` 每次换票本来就要请求一次，在它的响应里多两个字段是零成本；
新端点等于把同一份日期比较搬两处、还要多一次往返。规则只能有一处。

### 3.2 方案丙：后端做"每日全量兜底"（cron 补跑 / `daily --force`）

否决：这是把"5471 只票每天拉一遍"换个入口，正是用户否掉的"全市场补"。
而且由 1.3 可知这条路**对指数无效**。

### 3.3 方案丁：每次换票无条件 POST `/api/sync`

否决：本地已最新时后端虽然会短路不联网（2.2(a)），但前端要白发一次 POST、
轮询一次，并把这个 `skipped` 当成"源没有返回数据"弹黄字警告——
**每开一只本来就最新的票都弹一次警告**，纯噪声。判据必须让前端先拿到。

### 3.4 方案戊：把"正在补最新"塞进顶栏 `#stamp` / `#stamp-detail`

否决：那两行的「恒定 2 行、窄屏不跳高度」是硬约束，
`src/chanlun/web/static/app.js` 第 236-254 行与 `styles.css` 第 35-56 行写明了原因和实测数字
（顶栏 93→110px、按钮 36→56px）。往里塞可变长度文本必然跳高度。

### 3.5 方案己：用现有 `#notice` 面板显示进度

否决：`#notice` 是 `position: absolute; inset: 56px 24px 24px`
（`src/chanlun/web/static/styles.css` 第 343 行起），**整块盖住 K 线图**。
它是为"没有数据"准备的，不是为"有数据但正在补"准备的。

### 3.6 方案庚：自动补取加一个 `config.toml` 总开关

否决（本次）：只在"确实落后"时才发请求，不落后一个包都不发；
多一个配置面就多一个"用户关了之后忘记、又以为坏了"的失败模式。

---

## 4. 设计

### 4.1 后端：唯一新增口径 `freshness`

新增于 `src/chanlun/web/api.py`，签名与语义：

```python
def freshness(code: str, period: str, as_of: str | None) -> dict[str, Any]:
    """本地数据是否落后于该市场的最新交易日，以及这个周期能不能补。
    **这是全项目唯一一份「要不要去补最新」的判断。**

    返回 {"expected_as_of": str, "stale": bool, "syncable": bool, "sync_hint": str}
    """
    expected = calendar_for(code).last_trading_day(dt.date.today()).isoformat()
    blocker = sync_blocker(code, period)
    return {"expected_as_of": expected,
            "stale": bool(as_of) and str(as_of)[:10] < expected,
            "syncable": blocker is None,
            "sync_hint": blocker or ""}
```

- 日历按市场选：`src/chanlun/calendar.py` 第 111-115 行 `calendar_for(code)`
  已经实现了"A 股用真实交易日历、港股/美股用工作日日历"，**不新造第二份**。
- 只比日期：见 2.2(c)。
- 非交易日：`last_trading_day` 自己会回退到上一个交易日，
  所以周六不会因为"今天是周六"而误判落后。
- `period` 参数不是摆设：`syncable` / `sync_hint` 复用
  `src/chanlun/web/api.py` 第 947 行 `sync_blocker(code, period)`，让页面能**跳过**
  那些必然被拒的组合（实测：`("sh.000001", "30")` 与 `("hk.hsi", "30")` 都给得出原因，
  而 `("600000", "30")`、`("sh.000001", "day")`、`("hk.hsi", "day")`、
  `("us.dji", "60")` 都是 `None`）。
- `as_of` 为空时 `stale=False`（不知道就不猜），但 `expected_as_of` **照样给出来** ——
  404 的提示里要说得出一个具体日期。

挂载点：三个地方，**都只追加字段、不改既有键**：

| 位置 | 追加 |
| --- | --- |
| `/api/structure` 成功响应（`src/chanlun/web/api.py` 第 731-735 行 `body.update`） | 4 个字段全给，`as_of` 取自 `view.snap.as_of` |
| `/api/bars`（`src/chanlun/web/api.py` 第 686-690 行的返回字典） | 同上，`as_of` 取下发数据自己的末根 |
| `/api/structure` 的 404 分支（`src/chanlun/web/api.py` 第 714-724 行） | `syncable` / `sync_hint` 改为由 `freshness` 给出，并**新增** `expected_as_of` |

**前端不做任何日期比较**，也不按号段猜指数。

### 4.2 前端：触发、队列、反馈

**触发**（两条分支，都汇到同一条同步路径）

1. `load()` 成功分支末尾 → `maybeAutoSync(body)`。
2. `load()` 的 404 分支 → `body.syncable !== false` 时**自动发起**（原来必须手点按钮）。
   按钮保留，作为失败后的手动入口。

两条分支就够：`load()` 本身同时被"换票 / 换周期 / 回车 / 直接开 URL / 自选池点击"
调用，所以这几处入口一次覆盖，不需要分别挂。

**`maybeAutoSync(body)` 的判据**

```
body.stale !== true                  → 直接返回（不落后，一个请求都不发）
autoTried.has(`${code}|${period}`)   → 直接返回（本次会话已试过）
否则 → 记入 autoTried，进入同步队列
```

**同步队列**：同时最多 **1 个在飞**，另有 **1 个"最新的"待办槽**。
必要性：首次拉 5 分钟线实测 **149 秒**（`src/chanlun/web/api.py` 第 1039 行注释记录了同一数字），
这期间用户必然换票；没有待办槽就会把中间那只票漏掉，而且会堆出一串后台线程。

**反馈控件 `#sync-chip`**（新增 DOM，见 4.4）

| 状态 | 文案 | 可点 |
|---|---|---|
| 在飞 | `正在补最新数据：本地截至 2026-10-08，最新交易日 2026-10-09…` | 否 |
| 排队 | `排队等补最新数据…` | 否 |
| 成功 | `已补到 2026-10-09`，约 3 秒后自动消失 | 否 |
| 失败/零行 | `没补到新数据：<原因> · 点这里重试` | **是**（重试 = 重新入队，并把它从 `autoTried` 移除） |

成功分支复用现有 `load()` 重画，不新写重画逻辑。

### 4.3 会话内退避

`autoTried` 是 `app.js` 里的一个 `Set`，key 为 `${code}|${period}`，
**进入即记**（无论后续成功/失败/零行），页面刷新即清空。
语义就是批准的那条："这次会话内不再自动重试，只留手动按钮"。

覆盖的真实场景：停牌股（本地最后一根永远旧于最新交易日）、
`hk`/`us` 与 A 股日历不一致导致的空拉、源临时不可用。

### 4.5 「这一只是谁」一律用落库键（实施中发现，补记）

设计时漏了一条：浮标讲的是**哪一只票**。第一版实现里所有比较都用 `state.code`，
而它只是**用户输入的原样** —— 书签可以写 `?code=sz.000858`，服务端回的 `code`
才是落库键（`000858`；指数反过来必须带前缀 `sh.000001`，否则和 `sz.000001`
平安银行撞号）。真浏览器实测出三处同根因缺陷（2026-10-10，测量过程见
`docs/evidence/2026-10-10-按需拉取验收.md`）：

| 现象 | 成因 |
| --- | --- |
| 用 `?code=sz.002898` 打开：浮标**一个字都不显示**，补完也**不重画** | `chipIsAbout` / `finishAutoSync` 拿 `sz.002898` 去比 `002898`，判成"用户已经换走了" |
| 变异对照：把 `finishAutoSync` 改回 `state.code`，顶栏停在 `截至 2026-10-08`，浮标却写着 `已补到 2026-10-09` | 同上，页面自相矛盾（数据其实已经落库） |
| 用 `?code=sh.600759` 打开：自选按钮显示 `☆ 加自选`、自选行不高亮 | `inWatchlist` / `it.code === state.code` 拿 `sh.600759` 去比 `600759` |

**规则（本设计的一部分）**：

1. `state.key` 是"屏幕上这一只"的**落库键**。`setCode` 先按用户写的形态占位，
   成功响应到了升级成 `body.code`；404/4xx 没有落库键可回，只能按请求原样记。
2. 浮标**只讲当前这一只**：换票、换周期、刷新，第一件事就是 `dropChip()` ——
   连同点击句柄一起清，否则"点这里重试"点到的是上一只的 job（把 A 的失败提示
   挂在 B 的图上，比不显示更坏）。后台补完的别人，一个字都不许写进当前这张图。
3. 自动补每会话每只只做一次，但"已经试过、现在仍然落后"的那一只**再进入时**
   要给出属于它自己的可点重试（文案：`本次会话已经自动补过一次，没有再自动重试`），
   不许静默返回 —— 静默等于把这次失败也一起藏了。
4. 全站不变量由此加了一条守卫：`app.js` 里不许出现 `=== state.code` / `!== state.code`
   形式的**身份比较**（比较一律 `state.key`）。`state.code` 只剩两个合法用途：
   发请求（`/api/structure?code=…`）和记录用户输入。

### 4.6 布局约束（不许动现有布局）

- `#sync-chip` 挂在 `<main class="stage">`（`src/chanlun/web/static/index.html` 第 101 行）
  内部，与 `#notice` 同一层。`.stage` 已是 `position: relative`
  （`src/chanlun/web/static/styles.css` 第 265 行），所以 chip 用
  `position: absolute` 定位在图表**右下角**（`right: 18px; bottom: 12px`）、
  `pointer-events: none`（只有"重试"态才开）。
- **不能放右上角**：`.stage` 顶部那约 56px 是 `.tools` 工具条，浮标会压住按钮。
- 它**不占文档流**，因此 `.chart { flex: 1 }`（`styles.css` 第 266 行）
  与顶栏那恒定 2 行都不受影响。
- 验收方式是测量而不是目测：改造前后 `.chart` 的
  `getBoundingClientRect()` 必须逐字相同（见 5.3）。

---

## 5. 验收（可失败的测量）

### 5.1 后端单测

1. `freshness` 的判据本身：
   - A 股代码 + `as_of` = 上一个交易日 → `stale=True`，`expected_as_of` = 最新交易日；
   - A 股代码 + `as_of` = 最新交易日 → `stale=False`；
   - 分钟周期的 `as_of` 带时间（`2026-09-30 15:00`）时按日期比；
   - `as_of` 为 `None` / 空串 → `stale=False`，但 `expected_as_of` **照样给出**最新交易日
     （404 的提示要说得出一个具体日期）——「不落后」与「不知道」两种成因都要有用例。
   - `syncable` / `sync_hint`：必然被拒的组合要给 `False` 且带得出原因（复用
     `sync_blocker`；实测 `("sh.000001", "30")` 与 `("hk.hsi", "30")` 都不是 `None`，
     而 `("600000", "30")`、`("sh.000001", "day")`、`("hk.hsi", "day")`、
     `("us.dji", "60")` 都返回 `None`）。
   - 港股/美股代码走工作日日历（与 A 股日历在同一天给出不同答案的用例故意构造一天，
     例如 10-01—10-07 国庆那周——A 股休市、港股开市）。**日历一律打桩**，不许让结论
     取决于"跑测试的这一天"。
2. `/api/structure` 的响应含 `stale`，且与 `freshness` 的结论一致（已最新=false / 落后=true）。
3. **判据可失败性**：把 `stale` 的比较写成 `>`（或恒 `False`）后，上述用例必须变红。

### 5.2 端到端（真浏览器 + DOM dump，不用截图）

在改之前先记录：开 `sh.000001`（实测停在 `2026-09-30`）时页面显示的 `as_of`。
改之后同一个操作应当看到：

1. `#sync-chip` 出现（"正在补最新数据…"）；
2. 图先出来（本地 09-30 的数据），**页面没有被阻塞**；
3. 补完后 chip 变"已补到 <最新交易日>"并自动消失，`/api/structure` 的 `as_of` 前进到最新交易日；
4. 再开一只本来就最新的票（如 `600000`）→ **chip 不出现、`/api/sync` 不被调用**
   （用服务端访问日志或 `_SYNC_TASKS` 状态确认）。

### 5.3 布局不许动（回归）

改造前后在同一视口宽度下测量并比对（逐字相同才算过）：

- `document.querySelector('.chart').getBoundingClientRect()`
- `document.getElementById('stamp').getBoundingClientRect()`
- `document.getElementById('stamp-detail').getBoundingClientRect()`

### 5.4 反面对照

- 把 `maybeAutoSync` 的 `body.stale !== true` 判断删掉 → 5.2 的第 4 条必须失败
  （说明"不落后不发请求"确实被这条判断守着，而不是碰巧成立）。
- 把 5 分钟线的首次拉取（149 秒那条路径）走一遍，确认 chip 期间页面仍可换票、
  待办槽只保留最新一只。

---

## 6. 改动面

### 6.1 代码

| 文件 | 改动 |
|---|---|
| `src/chanlun/web/api.py` | 新增 `freshness(...)`；`/api/structure`（含 404 分支）与 `/api/bars` 的响应各追加 4 个字段 |
| `src/chanlun/web/static/app.js` | `maybeAutoSync` / 队列 / `autoTried`；404 分支自动发起；chip 的状态机；`setCode` 的欠账注释（第 1751 行）随之作废要改 |
| `src/chanlun/web/static/index.html` | 新增 `#sync-chip` 元素 |
| `src/chanlun/web/static/styles.css` | `#sync-chip` 的样式（绝对定位，不占流） |

**不动**：`src/chanlun/data/sync.py`（`ok/skipped/failed` 三态不动）、
`deploy/`（17:30 定时任务不动）、`chan/`（判据一行不动 ⇒ 不是判据变更）。

### 6.2 测试

- `tests/web/test_api_freshness.py`（新增）：5.1 的 1、2、3。
- `tests/web/test_frontend_auto_sync.py`（新增）：chip 的 DOM/CSS 约束、判据来自后端、
  一个在飞一个待办、会话内只自动试一次、轮询问的是发起时那一只票。
- `tests/web/test_frontend_sync_button.py`：**只改 1 处断言** —— POST 搬进 `postSync` 后
  重定向该断言（契约本身不变）；该文件其余断言必须逐字仍然成立。
- 现有 web 测试不得回归（尤其 `tests/web/test_frontend_watchlist.py` 的
  `test_body_grid_rows_cover_every_body_child` —— 它是布局没被动过的证据）。

### 6.3 文档

- `ARCHITECTURE.md`：新增 **D-49**（四段式：决策 / 依据 / 被否决的替代方案 / 后果·变更历史）。
- `ARCHITECTURE.md` §5 + `CHANGELOG.md` 的「已知空白与未实现项」：登记 7.1—7.3。
- `CHANGELOG.md`：`[Unreleased]` 的 `### 新增`。
- 版本影响：**MINOR**（新增向后兼容的功能）。本次不发版：`pyproject.toml` 不动，条目留在
  `[Unreleased]`。**不顺手补版本段落** —— `0.3.0` 起的版本段分派是 `ARCHITECTURE.md` §5
  已登记的独立任务，本次再涨一次版本号而不搬条目，只会给那笔债添新的一段。

---

## 7. 未决 / 已知残留

### 7.1 定时任务只同步 `universe`，自选池的指数/港股/美股从不更新（本次不改）

实测见 2.3。按需拉取**缓解**了它（用户一打开就补），但没有根治：
没人打开的那些票仍然停在旧数据上。
要不要把 `watchlist` 一并纳入定时任务的同步范围，是独立的一次决策。

### 7.2 非交易日无法补跑

`src/chanlun/__main__.py` 第 852 行的闸门会直接跳过整条流水线：

```
非交易日：今天 2026-10-10（最近交易日 2026-10-09），跳过每日流水线
```

2026-10-10（周六）实测：`python -m chanlun daily` 被挡下并退出 0，
而 `python -m chanlun sync --period day --codes ...` 可以绕过。
也就是说**漏掉的交易日在下一个交易日之前没有自动补回来的路径**。
本次不改，登记为空白。

### 7.3 parquet 末根与 `sync_state` 水位理论上可能不一致

全市场实测只有 1 例（`300114`：parquet `2025-02-17` / 水位 `2025-02-14`）。
此时前端按 parquet 判"落后"、后端 `start_for` 按水位判"已最新"，
表现为自动补取空转一次（有 `autoTried` 兜底，不会反复）。

### 7.4 频次与拉黑风险（未证实，但要盯着）

`fetch_bars` 每次取数都会 `_ensure_login()`。按需拉取会把人打开页面的次数
变成取数次数，方向上增加了登录频次——而 10-09 那次 `10001011 黑名单用户`
**是否由登录频次引起至今未证实**（D-48 §6 已如实记录）。
本次的缓解手段是有判据的（不落后不发请求）+ 会话内退避，
但"每只票每次会话最多一次"这个上限是本次新引入的约束，需要在上线后观察真实频次。
