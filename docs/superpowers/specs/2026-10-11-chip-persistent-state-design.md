# 设计：右下角浮标变成「这一只这一周期」的常驻数据状态，并兼作手动补数据入口

- 日期：2026-10-11
- 状态：已与用户逐段确认，待实施
- 上游：D-49（`docs/superpowers/specs/2026-10-10-on-demand-sync-design.md`）
- 前置事实：D-49 已落地并提交（`e3a6548`），本条改的是它的浮标语义

## 1. 背景：用户的两句原话，两句都对

用户看过 D-49 之后的原话：

> 不是吧？我看现在这个也没有那个手动拉取的那个按钮呀。就比如说是 ST洲际的30分钟K线，我点进去之后，它也没有自动拉取，也没有手动拉取的按钮，这肯定不对吧？

拆成两个可验证的断言，实测结果：

**断言一「没有手动拉取的按钮」——成立。** 全 UI 只有一个手动入口：
`app.js:229` 的 `{ label: "同步这个周期", run: syncThisPeriod }`，它只在
`/api/structure` 返回 **404** 时才渲染（`load()` 的 `!resp.ok` 分支）。而 ST洲际
（`600759`）30 分**有**本地数据、只是落后，所以走的是 200 分支 —— 那个分支上
**一个按钮都没有**，只有一块 12px、`var(--rice-dim)`、4 秒后自己消失的浮标。

**断言二「没有自动拉取」——在正确的点击流程里不成立。** 实测：`?code=002309&period=day`
进去点 `.period[data-period=30]`，2 秒后浮标出现「已补到 2026-10-09 15:00」，
服务端 `POST /api/sync` 计数 +1。同一支探针在已经补好的 `600759` 上则
`chipHidden: true`、`#notice` 不显示、没有任何动作 —— 也就是**一切正常时彻底静默**。

所以真正的问题不是"没自动补"，而是三件事叠在一起：

1. **有数据但落后时没有可点的入口**（断言一的字面事实）；
2. **一切正常时完全静默**，用户无法区分"已是最新"和"这功能没工作"；
3. **一次真实同步要 2.0 秒到 28.5 秒**（baostock 间歇 `[Errno 60] Operation timed out`
   后重试），而浮标「正在补最新数据…」只有 12px 且藏在右下角，很容易被漏看。

另有第四个成因，用户自己也提过：**我改了前端、你看的是旧页面**。实测
`curl -sI /static/app.js` 只有 `last-modified` + `etag`，**没有 `Cache-Control`**，
Chrome 可以按启发式规则直接拿缓存里的旧 `app.js`，那份旧代码既没有自动补、
也没有非 404 的按钮。这一条与用户选的第二项一起修。

## 2. 目标

- 右下角浮标**常驻**，恒定回答一个问题：**你正在看的这一只、这个周期，数据到哪儿了、能不能补、补没补上**。
- 落后时浮标**本身可点**，点下去补的就是**这一只**。
- 把"静默"从设计里去掉：四种情形（最新 / 落后 / 补不了 / 正在补）各自有明确文案。
- 静态文件不再被浏览器按启发式规则缓存住旧版本。

## 3. 被否决的替代方案

### 3.1 把「手动补数据」做成顶栏按钮

顶栏恒定 2 行是硬约束（D-45 / `2026-10-03-topbar-stable-layout-design.md`），
再加一个按钮会改顶栏高度。而且顶栏讲的是"这一只的行情口径"，按钮讲的是"动作"，
两者混在一格会让那一格同时承担两种语义。**否决**：用户已选 A（浮标常驻化 + 可点）。

### 3.2 让浮标在"落后"时也自动消失，只在有事时出现

这正是现状。用户看不懂"没有浮标"到底是"已是最新"还是"功能没跑"。**否决**。

### 3.3 后端新增「落后 N 个交易日」字段

更直观，但要在 `freshness`（`web/api.py:993`）的返回结构里加 `behind`，
用 `calendar_for` 数交易日 —— 多一处口径、多一组测试，而
「本地截至 2026-09-30 15:00 · 最新 2026-10-09」已经把同一件事说清楚了。
**否决**（用户已选"不显示"）。

### 3.4 把整个浮标 `div` 变成按钮

改动更小（`role="button"` + `tabindex`），但会让一个 `aria-live` 状态区同时是
可点元素，读屏语义混在一起，键盘焦点框也会套住整块状态文字。**否决**：
用户已选"里面放真 button"。

### 3.5 404 时浮标与提示行都显示

这是**今天的实际行为**，实测（`?code=000033&period=30`，一个退市代码，
本地无 30 分数据、后端说可补）：

```
noticeDisplay: "flex"
noticeText: "没有这只票的本地数据000033 的 30 周期没有本地数据。请先同步：
             python -m chanlun sync --period 30 --codes 000033同步这个周期"
chipHidden: false
chipText:   "正在补最新数据：本地截至 ?，最新交易日 2026-10-09…"
actions:    ["同步这个周期"]
```

两个入口讲同一件事，而且浮标那行有个丑陋的 `?`（`job.local` 为 `null`）。
**否决**：用户已选"让位：浮标隐藏"。404 时 `#notice` 更显眼、在流内、
有完整解释和命令，浮标整条收起来。

### 3.6 浮标点击复用 `syncThisPeriod()`

`syncThisPeriod()`（`app.js:509`）把反馈写进 `#notice`（`syncNote`）。而有本地数据时
`#notice` 是隐藏的 —— 点下去会像没反应。**否决**：浮标点击走浮标自己的反馈通道。

### 3.7 保留「补完 4 秒后自动消失」

「已补到 X」是有用的确认，但它 4 秒后消失、并且可能挂到别的票上
（D-49 已修归属问题）。常驻之后更好的做法是：**「已补到 X」由紧接着的
`load()` 用后端真结论覆盖掉**，不需要定时器。**否决**：不留定时器。

## 4. 设计

### 4.1 状态机（判据全部来自后端，前端不新增任何判据）

`noteFreshness(body)`（由 D-49 的 `maybeAutoSync` 更名而来）是**唯一**决定浮标
说什么的地方，它拿 `/api/structure` 的响应体，按后端已经算好的四个字段分派：

| 情形 | 后端给的 | 浮标 | 可点 |
|---|---|---|---|
| 请求中 | — | 隐藏（`dropChip()`，不许对上一只票下结论） | — |
| 有数据、已最新 | 200 `stale=false` | `已是最新 · 2026-10-09 15:00` | 否 |
| 有数据、落后、能补 | 200 `stale=true` `syncable=true` | `本地截至 2026-09-30 15:00 · 最新 2026-10-09` | **是**，「点这里补」 |
| 同一次会话已经自动试过 | 同上 | 同上 + `· 本次会话已经自动补过一次` | **是**，「点这里补」 |
| 正在补 | 本地状态 | `正在补最新数据：本地截至 2026-09-30 15:00 · 最新 2026-10-09…` | 否 |
| 在排队 | 本地状态 | `排队等补最新数据…` | 否 |
| 补完 | 本地状态 | `已补到 2026-10-09 15:00`（随即被 `load()` 的真结论覆盖） | 否 |
| 补了但源没给新数据 | `state=skipped` | `本地截至 2026-09-30 15:00 · 最新 2026-10-09 · 本地记录已是最新，文件里还没有新数据` | **是**，「点这里补」 |
| 补失败 | `state=error` / 网络错 | `补数据没成功：<原因>` | **是**，「重试」 |
| 有数据、落后、补不了 | 200 `stale=true` `syncable=false` | `本地截至 X · 最新 Y · 补不了：<sync_hint>` | 否 |
| **本地一根都没有** | 404 | **隐藏**，让位给 `#notice` | — |
| 连不上服务 | 网络错 | 隐藏，让位给 `#notice` | — |

两处与 D-49 不同的行为，都是有意的：

- **`skipped` 不再算失败。** D-49 把它写成 `chipState("failed", job,
  "本地记录已是最新，文件里还没有新数据")`，渲染出来是
  「没补到新数据：本地记录已是最新，文件里还没有新数据」—— 一句话自己否定自己。
  改成"落后 + 可点重试"更诚实：parquet 确实落后，只是水位说没有新数据。
- **「没补到新数据」前缀只留给真失败**，改成「补数据没成功：<原因>」。

### 4.2 DOM：状态文字与动作分开

`index.html:140` 从空 `div` 变成：

```html
<div id="sync-chip" class="sync-chip" hidden role="status" aria-live="polite">
  <span id="sync-chip-text"></span>
  <button id="sync-chip-go" class="sync-chip-go" type="button" hidden></button>
</div>
```

- 外层保留 `role="status" aria-live="polite"`，读屏读到的是状态文字。
- 按钮是真 `<button type="button">`，键盘可达、可读屏。
- **代码里所有 `el.textContent = …` 必须改成 `text.textContent = …`** ——
  继续往容器上写 `textContent` 会把按钮一起抹掉。这一条要用静态断言咬住。
- 按钮文案由状态决定：可补时「点这里补」，失败时「重试」。

### 4.3 可点性与不挡图表

`styles.css` 的 `.sync-chip` **保持 `pointer-events: none`**（浮标整块不挡图表交互，
既有测试 `test_the_chip_does_not_take_up_flow` 继续成立），把 `pointer-events: auto`
放到 `.sync-chip-go` 上 —— 子元素在 `pointer-events: none` 的父元素下**仍然可点**，
这样只有那个小按钮吃点击，状态文字不吃。

`.is-retry`（`styles.css:988`）更名为 `.is-action`：它现在涵盖"落后可补"和
"失败重试"两种可点状态，"retry"这个名字已经不准。它的
`border-left-color: var(--cinnabar); color: var(--rice)` 保留。

### 4.4 唯一代码路径：自动与手动共用 `runAutoSync`

- 自动：`noteFreshness(body)` → `requestAutoSync(job)`。
- 手动（点浮标）：`requestAutoSync(job, { manual: true })`，
  **只多做一件事 —— 绕过 `autoTried` 的"本次会话只自动试一次"闸门**。
- 两条路共用 `runAutoSync` / `pollSync` / `finishAutoSync`，因此轮询、超时、
  失败文案、`load()` 重画只有一份。
- `chipState` 里不再有 `el.onclick`；点击句柄挂在按钮上，`dropChip()` 收浮标时
  连按钮句柄一起清（**归属**这条不变量不能丢：点到的必须是当前这一只）。
- `chipTimer` 与 4 秒自动消失一起删除（`dropChip` 里的 `clearTimeout` 随之删除）。

### 4.5 404 让位用显式字段，不靠 `local === null` 猜

404 分支给 job 加一个显式字段：

```js
requestAutoSync({ code: state.code, period: state.period,
                  local: null, expected: body.expected_as_of, quiet: true });
```

`chipState` 开头：`if (job && job.quiet) return dropChip();`。
用显式 `quiet` 而不是"`local` 是不是 `null`"，是因为后者靠一个巧合
（有数据时 `as_of` 一定是字符串）成立，加一个字段更不容易在将来被改坏。

### 4.6 布局：常驻不新增位移

浮标是 `.stage` 内的绝对定位元素（`right: 18px; bottom: 12px; z-index: 3`），
不参与任何流。D-49 已经量过"隐藏 / 强制可见 / 移除"三态下 `#chart`、`.stage`、
`#stamp`、`body` 网格行数逐字节相同。常驻只是把"可见"从例外变成默认，**不新增位移**。

新增验收（必须量，不能靠看）：四种常驻态（最新 / 落后可点 / 正在补 / 补不了）下
`#chart`、`.stage`、`#stamp`、`#stamp-detail` 的 `getBoundingClientRect()`
以及 `body` 网格行数、`document.documentElement.scrollHeight` 全部与基线相同。

## 5. 静态文件缓存

`src/chanlun/web/app.py`：

- `/static` 挂载点：用一个 `StaticFiles` 子类在 `get_response` 之后补
  `cache-control: no-cache`。
- `/`（`index()` 的 `FileResponse`）：同样加 `cache-control: no-cache`。

`no-cache` 的语义是"每次用 etag 回服务端校验"，不是"禁用缓存" —— 没变就还是 304
（服务端日志里今天已经在返回 `304 Not Modified`），变了立刻拿到新的。
这一条是**无条件**的（用户已确认），理由写进 D-50 的「依据」：
本次误判的直接成因之一就是"我改了前端、你看的是旧页面"。

## 6. 改动面

| 文件 | 改什么 |
|---|---|
| `src/chanlun/web/static/index.html` | 浮标里加 `#sync-chip-text` + `#sync-chip-go` |
| `src/chanlun/web/static/styles.css` | `.sync-chip` 加文字/按钮排布；`.is-retry` → `.is-action`；新增 `.sync-chip-go` |
| `src/chanlun/web/static/app.js` | `maybeAutoSync` → `noteFreshness`；`chipState` 七态 + `quiet` 让位；删 `chipTimer`；按钮挂句柄；手动入口绕 `autoTried` |
| `src/chanlun/web/app.py` | 静态与 `/` 的 `cache-control: no-cache` |
| `tests/web/test_frontend_auto_sync.py` | 改到新契约（常驻、真按钮、`is-action`、`noteFreshness`、`skipped` 不算失败） |
| `tests/web/test_static_cache.py` | 新增：响应头 + `If-None-Match` 得 304 |
| `ARCHITECTURE.md` | 新增 `#### D-50`；D-49 补一行「变更历史」指过去 |
| `CHANGELOG.md` | `[Unreleased]` 新起一条（与 D-49 那条分开） |
| `docs/evidence/2026-10-11-浮标常驻验收.md` | 新增 |

**版本影响**：新增向后兼容的前端行为 + 一个响应头 ⇒ 语义上是 MINOR，但本次
**不涨版本号**（`pyproject.toml` 不动），条目留在 `[Unreleased]`，与 D-49 一致。

**判据影响**：`chan/` 一行不动 ⇒ 不是判据变更，不需要 `optimizer/theory/` 条目。

## 7. 验收（每条都要能失败）

1. **文案四态**：真浏览器里对四种情形各取一次 `#sync-chip-text` 文本 +
   `#sync-chip-go.hidden`，与 4.1 表逐字对上。
2. **可点性**：落后态下按钮 `pointer-events` 为 `auto`、容器为 `none`；
   点一次发且只发一次 `POST /api/sync`（用 `grep -c "POST /api/sync" /tmp/serve.log` 数），
   补完文案落到「已补到 …」再落到「已是最新 · …」。
3. **404 让位**：`?code=000033&period=30` 下 `#notice` 可见、
   `#sync-chip.hidden === true`、页面上只有「同步这个周期」一个按钮。
4. **布局零位移**：4.6 列的那些 rect 与网格行数在四态下全等。
5. **缓存头**：`curl -sI /static/app.js` 与 `curl -sI /` 都含 `cache-control: no-cache`；
   带 `If-None-Match` 第二次得 304。
6. **变异能红**：把每一处新断言对应的代码改回去（常驻改回自动消失、
   按钮改回 `el.onclick`、`quiet` 让位去掉、`noteFreshness` 改回不写浮标），
   证明静态断言确实会失败，再逐字节还原。
7. **全量测试**：`PYTHONPATH=src ../.venv-chanlun/bin/pytest -q` 全绿。

## 8. 已知残留（不在本次范围）

- 「有数据、落后、补不了」（`blocked` 态）**今天没有活实例**：`syncable=false`
  只在"指数 + 分钟周期"出现，而 13 只有 30 分数据的票全是 A 股股票。
  这个态仍然实现（它是"落后但沉默"的解药），但验收只能到静态断言一级，
  证据文档里要如实写「未在浏览器里见过活的」。
- 全市场补数据、历史缺口检测、定时任务补跑：仍然不做（D-49 已否决）。
- `behind` 计数：不做（3.3）。
