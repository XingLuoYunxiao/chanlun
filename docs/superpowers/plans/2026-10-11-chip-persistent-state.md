# 浮标常驻化 + 手动补数据入口 + 静态文件 no-cache 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把右下角浮标从"有事才冒出来、4 秒后消失"改成**常驻的「这一只这一周期」数据状态读数**，落后时**自己可点**去补，并给静态文件加 `cache-control: no-cache`。

**Architecture:** 判据一条都不新增 —— `/api/structure` 已经在响应里给了 `stale` / `syncable` / `sync_hint` / `expected_as_of`，本次只把这三个后端结论**如实写进浮标**，并把"动作"从整块浮标挪进一个真 `<button>`。自动补与手动补**共用同一条** `runAutoSync → pollSync → finishAutoSync` 流水线，手动路径只多一个 `manual: true`，作用是绕过"本次会话只自动试一次"的闸门。

**Tech Stack:** Python 3.14 / FastAPI + Starlette 1.7.0（`TestClient` 做后端断言）、原生 HTML/CSS/JS（无构建步骤，契约用**静态字符串断言**咬住）、`node --check` 语法闸、CDP（`/tmp/dsh_cdp_eval.mjs`）做真浏览器行为验收。

## Global Constraints

- 项目根目录 = `/Users/zzz/workspace/chanlun`；venv = `../.venv-chanlun/`；git 根 = `/Users/zzz/workspace`。
- 跑测试一律 `cd /Users/zzz/workspace/chanlun && PYTHONPATH=src ../.venv-chanlun/bin/pytest -q`（`addopts = "-m 'not live'"`）。
- **中文输出**；每条论断带 `文件:行号` 或课号。
- **`chan/` 一行不许动** ⇒ 本次不是判据变更，不需要 `optimizer/theory/` 条目。
- **不涨版本号**：`pyproject.toml` 不动，CHANGELOG 条目留在 `[Unreleased]`。
- **UI 改动必须用真实浏览器核对**（AGENTS.md §4）；**截图只能看布局，文案必须从 DOM dump 里读**。
- **绝不 `git add -A`、绝不 `git add chanlun/`、绝不 `git checkout -- <file>`**。
- **一个批次只提交一次**（用户 2026-10-11 明确要求）。因此下面每个 Task 末尾的"提交"步骤**一律留空不勾**，真正的提交在 Task 7。
- **测量必须能失败**；实测数字逐项相同要记 `inconclusive`，不许记"验证通过"。
- 浮标整块**必须继续 `pointer-events: none`**（既有护栏 `test_the_chip_does_not_take_up_flow` 守着）；可点性放在子元素上。
- **不许 `el.textContent = …`**：往容器上写 `textContent` 会把按钮一起抹掉，状态文字一律写进 `#sync-chip-text`。
- **不许给浮标加 `white-space: nowrap` / 省略号**：`sync_hint` 实测 84 字，要能折行。
- 服务器：改 `src/chanlun/**/*.py` 后**必须重启**（`uvicorn.run` 没有 `--reload`）；改 `web/static/` 只需浏览器刷新。

## File Structure

| 文件 | 责任 | 本计划中的改动 |
|---|---|---|
| `src/chanlun/web/static/index.html` | 页面骨架 | `#sync-chip` 里加 `#sync-chip-text` + `#sync-chip-go` |
| `src/chanlun/web/static/styles.css` | 全部样式 | `.sync-chip` 注释重写；`.is-retry` → `.is-action`；新增 `.sync-chip-go` |
| `src/chanlun/web/static/app.js` | 全部前端行为 | `maybeAutoSync` → `noteFreshness`；`chipState` 七态 + 真按钮；删 `chipTimer`；404 带 `quiet` |
| `src/chanlun/web/app.py` | FastAPI 装配 | `/static` 与 `/` 补 `cache-control: no-cache` |
| `tests/web/test_frontend_auto_sync.py` | 浮标契约（静态断言） | 4 条改到新语义 + 4 条新增 |
| `tests/web/test_static_cache.py` | 缓存头契约 | **新建**，3 条 |
| `ARCHITECTURE.md` | 决策记录 | 新增 `#### D-50`；D-49 补「变更历史」 |
| `CHANGELOG.md` | 变更记录 | `[Unreleased]` 新起一条（与 D-49 那条**分开**） |
| `docs/evidence/2026-10-11-浮标常驻验收.md` | 实测证据 | **新建** |
| `optimizer/tools/doc_refs_baseline.json` | 行号指纹基线 | `--update-baseline` 重录 |
| `tests/optimizer/test_check_doc_line_refs.py` | 守卫自带镜像 | 重新推导 5 组硬编码数字 |

---

### Task 1: 浮标 DOM 与 CSS —— 状态文字与真按钮分开

**Files:**
- Modify: `src/chanlun/web/static/index.html:138-140`
- Modify: `src/chanlun/web/static/styles.css:964-993`
- Test: `tests/web/test_frontend_auto_sync.py`

**Interfaces:**
- Consumes: 无（纯静态形状）
- Produces: `#sync-chip-text`（`<span>`）、`#sync-chip-go`（`<button type="button">`）、CSS 类 `.sync-chip.is-action`、`.sync-chip-go`。Task 2 的 `chipState` 依赖这四个选择器/类名，一个字母都不能改。

- [ ] **Step 1: 写失败测试**

在 `tests/web/test_frontend_auto_sync.py` 末尾追加（文件已有 `_html()` / `_css()` / `_js()` / `_fn_code()` 帮手，直接用）：

```python
def test_the_chip_has_a_text_span_and_a_real_button():
    """状态文字与动作必须分开：动作是**真 button**（键盘可达、读屏可读），
    文字写在 span 里 —— 这样一次 `textContent` 不会把按钮一起抹掉。"""
    html = _html()
    at = html.index('id="sync-chip"')
    chip = html[at : html.index("</div>", at)]
    assert "<span" in chip and 'id="sync-chip-text"' in chip
    assert "<button" in chip and 'id="sync-chip-go"' in chip
    assert 'type="button"' in chip, "在表单里默认是 submit：必须显式写 type"
    assert "hidden" in chip, "按钮默认藏起来"


def test_the_chip_button_is_clickable_while_the_box_stays_transparent_to_the_mouse():
    """浮标整块不吃鼠标（图上的十字线/缩放照常），只有里面那个按钮吃。

    子元素在 `pointer-events: none` 的父元素下**仍然可点** —— 这正是要的：
    状态文字不挡图，按钮可点。
    """
    css = _css()
    at = css.index(".sync-chip {")
    box = css[at : css.index("}", at)]
    assert "pointer-events: none" in box, "浮标整块不许挡住图上的交互"
    assert "is-action" in css and "is-retry" not in css, (
        "可点态改名为 is-action：它现在涵盖「落后可补」与「失败重试」两种"
    )
    go = css[css.index(".sync-chip-go {") :]
    go = go[: go.index("}")]
    assert "pointer-events: auto" in go, "按钮必须可点"
    assert "cursor: pointer" in go
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k "text_span_and_a_real_button or clickable_while_the_box"
```
Expected: **2 failed** —— 第一条 `ValueError: substring not found`（`id="sync-chip-text"` 不存在），第二条 `ValueError: substring not found`（`.sync-chip-go {` 不存在）。

- [ ] **Step 3: 改 `index.html`**

把 `index.html:138-140` 这三行：

```html
    <!-- 按需补最新的浮标（D-49）：和图**同时**可见，且绝对定位不占格 ——
         它不许进 body 网格（styles.css 开头那 4 行的硬约束）。 -->
    <div id="sync-chip" class="sync-chip" hidden role="status" aria-live="polite"></div>
```

整段替换为：

```html
    <!-- 按需补最新的浮标（D-49 / D-50）：和图**同时**可见，且绝对定位不占格 ——
         它不许进 body 网格（styles.css 开头那 4 行的硬约束）。
         D-50 起它**常驻**，恒定回答「你正在看的这一只、这个周期，数据到哪儿了」；
         可点的两态（落后可补 / 失败重试）把动作放进一个**真 button**，
         而不是让整块状态文字可点 —— 状态文字与动作是两件事。 -->
    <div id="sync-chip" class="sync-chip" hidden role="status" aria-live="polite">
      <span id="sync-chip-text"></span>
      <button id="sync-chip-go" class="sync-chip-go" type="button" hidden></button>
    </div>
```

- [ ] **Step 4: 改 `styles.css`**

把 `styles.css:964-993`（从 `/* ---------------- 按需补最新的浮标（D-49）` 到文件末尾）整段替换为：

```css
/* ---------------- 按需补最新的浮标（D-49 / D-50） ----------------
   它和 `#notice` 是两件事：notice 是 `inset: 56px 24px 24px` 盖满图表的面板，
   说的是"这个周期没有数据"；这一条说的是"数据在这儿、新不新、要不要补"，
   必须能**和图同时看见**。

   绝对定位是硬要求：`.stage` 是 flex 列，占流的子元素会把 `.chart` 挤矮，
   而顶栏"恒定 2 行 / 窄屏不跳高度"是既有硬约束（见本文件开头与 app.js 的注释）。
   `bottom` 而不是 `top`：`.stage` 顶部 56px 是工具条，浮标放下面才不会压住按钮。

   D-50 起它**常驻**（不再有事才出现、4 秒后消失）：所以整块继续
   `pointer-events: none`（状态文字不许挡住图上的十字线/缩放），
   可点的动作单独放进 `.sync-chip-go` 并显式 `auto` ——
   子元素在 `pointer-events: none` 的父元素下**仍然可点**，正是这里要的。

   也**不加** `white-space: nowrap` / 省略号：`sync_hint` 实测 84 字
   （"sh.000001 是指数，baostock 不提供指数的分钟线……"），折行比截断好 ——
   截掉的那半句往往正是"为什么补不了"。 */
.sync-chip {
  position: absolute;
  right: 18px;
  bottom: 12px;
  z-index: 3;
  max-width: min(420px, 60%);
  padding: 6px 10px;
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-left: 3px solid var(--amber);
  border-radius: var(--r);
  color: var(--rice-dim);
  font-size: 12px;
  line-height: 1.5;
  pointer-events: none;   /* 状态文字不吃鼠标事件：图上的十字线/缩放照常 */
}
/* 可点的两态：本地落后（点这里补）与补失败（重试）。
   名字从 D-49 的 `is-retry` 改成 `is-action` —— 它现在涵盖两种，不只是重试。 */
.sync-chip.is-action {
  border-left-color: var(--cinnabar);
  color: var(--rice);
}
.sync-chip-go {
  pointer-events: auto;   /* 只有这个按钮吃点击 */
  cursor: pointer;
  font: inherit;
  padding: 0 6px;
  margin-left: 6px;
  background: none;
  border: 1px solid var(--cinnabar);
  border-radius: var(--r);
  color: var(--cinnabar);
}
```

- [ ] **Step 5: 跑测试确认通过，并确认既有护栏没被碰坏**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/ -q
```
Expected: **全绿**。特别是 `test_the_chip_does_not_take_up_flow`（`pointer-events: none` 仍在 `.sync-chip {` 里）、`test_the_chip_lives_inside_main_not_in_the_body_grid`、`test_body_grid_rows_cover_every_body_child`（`<span>`/`<button>` 在 depth≥3，不计入 body 直接子元素）。

- [ ] **Step 6: 提交（本批次不提交，留空）**

---

### Task 2: 浮标七态常驻 + 可点补数据（`app.js`）

**Files:**
- Modify: `src/chanlun/web/static/app.js:234-243`（404 分支）、`:282-284`（调用点）、`:580-716`（整段）
- Test: `tests/web/test_frontend_auto_sync.py`

**Interfaces:**
- Consumes: Task 1 的 `#sync-chip-text` / `#sync-chip-go` / `.is-action`；既有 `postSync(code, period)`、`pollSync(started, job)`、`SYNC_POLL_MS`、`SYNC_MAX_MS`、`state.key`。
- Produces:
  - `noteFreshness(body) -> void` —— 唯一把后端结论写进浮标的地方（取代 `maybeAutoSync`）。
  - `requestAutoSync(job, manual) -> void` —— `manual` 为真时只绕过 `autoTried` 闸门。
  - `chipState(mode, job, msg, endTs) -> void` —— `mode ∈ {fresh, stale, syncing, queued, done, blocked, failed}`。
  - `job.quiet === true` ⇒ 整条让位给 `#notice`。
  - `dropChip()` / `chipIsAbout(job)` / `chipJob` 名字不变。

**为什么要一次改完这一段**：`chipState` 的七态与"点浮标"的点击路径共用同一批局部状态（`chipJob`、`autoBusy`、`autoPending`），分两次改会留下"按钮可见但点不动"的中间态。所以状态机与可点性合成一个 Task，但下面分成 2.1 / 2.2 两组断言，一次跑红、一次改绿。

- [ ] **Step 1: 改既有测试到新语义（2.1 组：常驻 + 后端结论）**

在 `tests/web/test_frontend_auto_sync.py` 里做四处修改。

**(a)** `test_the_verdict_comes_from_the_backend`（现 `:156-162`）整段替换为：

```python
def test_the_verdict_comes_from_the_backend():
    """前端**不许**自己比日期，也不许自己按号段猜指数 —— 只用后端给的结论。"""
    body = _fn_code(_js(), "function noteFreshness(")
    assert "body.stale !== true" in body
    assert "body.syncable === false" in body
    assert body.index("stale") < body.index("requestAutoSync"), "先判落后再谈同步"
    assert "new Date" not in body and "toISOString" not in body, "前端不比日期"
    assert 'chipState("fresh"' in body, "不落后也要如实说一句「已是最新」，不许静默"
    assert 'chipState("blocked"' in body and "body.sync_hint" in body, (
        "后端说补不了时要说明为什么，而不是什么都不显示"
    )
```

**(b)** `test_a_stale_structure_kicks_off_a_fetch_on_its_own`（现 `:165-167`）里的断言改成：

```python
    load = _fn_code(_js(), "async function load(")
    assert "noteFreshness(body)" in load, "结构到手后就要把后端结论写到浮标上"
```

**(c)** `test_the_chip_says_what_it_is_doing_and_hides_itself_when_done`（现 `:234-239`）整段替换为：

```python
def test_the_chip_is_a_persistent_readout_with_a_real_button():
    """浮标是**常驻状态读数**，不是"有事才冒出来的一闪"。

    D-49 的浮标 4 秒后自己消失、失败态整块可点；D-50 改成常驻 + 里面放真 button。
    理由（用户 2026-10-11 原话）：「它也没有自动拉取，也没有手动拉取的按钮」——
    有本地数据但落后时整个 UI 没有任何可点入口；而"没有浮标"既可能是已是最新、
    也可能是功能没跑，用户分不出来。
    """
    body = _fn_code(_js(), "function chipState(")
    assert "正在补最新数据" in body and "排队" in body and "已补到" in body
    assert "已是最新" in body, "不落后时也要常驻说一句，不许静默"
    assert "补数据没成功" in body and "重试" in body
    assert "is-action" in body, "可点的两态（落后可补 / 失败重试）用同一个类"
    assert "setTimeout" not in body, "常驻：不许再挂自动消失的定时器"
    assert "el.textContent" not in body, (
        "状态文字必须写进 #sync-chip-text：往容器上写 textContent 会把按钮一起抹掉"
    )
    assert "el.onclick" not in body, (
        "动作不许挂回整块浮标：整块仍然 pointer-events: none，挂了也点不到"
    )
    assert "btn.hidden = !actionable" in body, "按钮只在可点的两态出现"
    assert "btn.textContent" in body and "btn.onclick = () =>" in body, (
        "可点态要挂真按钮的句柄与文案"
    )
```

**(d)** `test_the_chip_only_ever_talks_about_the_stock_you_are_looking_at`（现 `:242-269`）里最后三行 `drop = …` 那一段替换为：

```python
    drop = _fn_code(js, "function dropChip(")
    assert "el.hidden = true" in drop and "btn.onclick = null" in drop, (
        "收起来要连按钮句柄一起收，否则点到的是上一只票的 job"
    )
    assert "chipJob = null" in drop
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q
```
Expected: **至少 4 failed** —— `noteFreshness` 找不到（`ValueError`）、`chipState` 里没有 `已是最新`、`setTimeout` 仍在、`dropChip` 里没有 `btn.onclick = null`。

- [ ] **Step 3: 改 `app.js` 的 404 分支**

把 `app.js:234-244`：

```js
      if (canSync) {
        // 「本地一条都没有」是最该自己去补的情形，不该只留给用户点按钮。
        // 走 requestAutoSync 而不是 runAutoSync：同一只票本次会话只自动试一次，
        // 反复进出这只票不该反复发请求（按钮还在，用户想手动再试随时可以）。
        requestAutoSync({
          code: state.code,
          period: state.period,
          local: null,
          expected: body.expected_as_of,
        });
      }
```

替换为：

```js
      if (canSync) {
        // 「本地一条都没有」是最该自己去补的情形，不该只留给用户点按钮。
        // 走 requestAutoSync 而不是 runAutoSync：同一只票本次会话只自动试一次，
        // 反复进出这只票不该反复发请求（按钮还在，用户想手动再试随时可以）。
        // `quiet: true`：这一态整条让位给 `#notice` —— 它更显眼、在流内、还带命令行。
        // 显式写一个字段，而不是让下游靠"`local` 是不是 null"去猜。
        requestAutoSync({
          code: state.code,
          period: state.period,
          local: null,
          expected: body.expected_as_of,
          quiet: true,
        });
      }
```

- [ ] **Step 4: 改 `app.js` 的调用点**

把 `app.js:282-284`：

```js
    // 数据画完了，再判断要不要去补最新：**先把本地这一份给用户看**，
    // 别让页面干等网络（补完会自己回来重画，见 runAutoSync）。
    maybeAutoSync(body);
```

替换为：

```js
    // 数据画完了，再把后端的"新不新"结论写到浮标上：**先把本地这一份给用户看**，
    // 别让页面干等网络（要补的话补完会自己回来重画，见 runAutoSync）。
    noteFreshness(body);
```

- [ ] **Step 5: 整段替换 `app.js:580-716`**

把从 `  // ------------------------------------------------- 按需补最新（D-49）` 到 `chipState` 的收尾 `  }`（即 `app.js:580-716`）整段替换为下面这四块（顺序即文件顺序）：

**第一块：常量与归属**

```js
  // ------------------------------------------------- 按需补最新（D-49 / D-50）
  // 进某只票/某个指数时，如果本地最后一根落在最新交易日之前，就自己去把它补回来 ——
  // 用户不必等 17:30 的定时任务（它只覆盖 universe 里的 A 股、也不覆盖指数），
  // 也不必先撞一个 404 才看见按钮。
  //
  // 判据**全部来自后端**（`/api/structure` 的 `stale` / `syncable` / `expected_as_of`）：
  // 日历按市场分派，前端既拿不到也不能复制。前端只做后端做不了的两件事：
  // **什么时候去问**、**问过几次**。
  //
  // D-50 起浮标**常驻**：不再"有事才冒出来、4 秒后消失"，而是恒定回答
  // 「你正在看的这一只、这个周期，数据到哪儿了、能不能补」。所以
  // `noteFreshness()` 是**唯一**把后端结论写进浮标的地方 ——
  // 最新 / 落后可补 / 落后补不了 三种情形都在那里分派，判据一个都不许在前端新造。
  const autoTried = new Set();   // key = `${code}|${period}`；进入即记，本次会话不再**自动**重试
  let autoBusy = null;           // 正在飞的那一个 job
  let autoPending = null;        // 待办槽：只留**最新**一只
  let chipJob = null;            // 浮标此刻讲的是哪一只；换票/换周期即作废

  // 浮标只讲**你正在看的这一只**。这条不是洁癖：实测（2026-10-10）在退市股
  // `sz.002898` 上补不到、浮标变成可点的「点这里重试」，随后换到已经最新的
  // `600000`，那行字仍然挂在图上、**仍然可点** —— 点下去补的还是 002898。
  function chipIsAbout(job) {
    return !job || (job.code === state.key && job.period === state.period);
  }

  function dropChip() {
    const el = $("#sync-chip");
    chipJob = null;
    if (!el) return;
    el.hidden = true;
    el.classList.remove("is-action");
    const text = $("#sync-chip-text");
    if (text) text.textContent = "";
    const btn = $("#sync-chip-go");
    if (btn) {
      btn.hidden = true;
      btn.onclick = null;   // 句柄必须清：留着它点到的就是上一只票的 job
    }
  }
```

**第二块：后端结论 → 浮标**

```js
  // 后端结论 → 浮标。`body` 是 `/api/structure` 的响应体，判据全是它给的。
  function noteFreshness(body) {
    if (!body) return;
    const job = {
      code: body.code,
      period: body.period,
      local: body.as_of,
      expected: body.expected_as_of,
    };
    if (body.stale !== true) return chipState("fresh", job);       // 已是最新：一个请求都不发
    if (body.syncable === false) return chipState("blocked", job, body.sync_hint);
    requestAutoSync(job);                                          // 落后且能补：自己去补
  }

  // `manual` 只做一件事：绕过"本次会话只自动试一次"的闸门。
  // 两条路（自动 / 点浮标）共用 runAutoSync，所以轮询、超时、失败文案、重画只有一份。
  function requestAutoSync(job, manual) {
    const key = `${job.code}|${job.period}`;
    if (!manual && autoTried.has(key)) {
      // 自动路径已经试过：**不**再发请求。但也不能就这么静默走掉 ——
      // 图明明落后，画面上却什么都没有。所以按"它现在到底在干嘛"如实回一句，
      // 并且留着可点的「点这里补」（点下去补的是**这一只**，不是上一只）。
      const busy = autoBusy && `${autoBusy.code}|${autoBusy.period}` === key;
      const pend = autoPending && `${autoPending.code}|${autoPending.period}` === key;
      if (busy) return chipState("syncing", autoBusy);
      if (pend) return chipState("queued", autoPending);
      return chipState("stale", job, "本次会话已经自动补过一次");
    }
    if (autoBusy) {                             // 同时只飞一个；后来者挤掉待办槽
      autoPending = job;
      return chipState("queued", job);
    }
    runAutoSync(job);
  }
```

**第三块：`runAutoSync` 原样保留，`finishAutoSync` 只改 `skipped` 一支**

`runAutoSync`（现 `:644-668`）**一个字都不改**。`finishAutoSync`（现 `:670-685`）替换为：

```js
  function finishAutoSync(job, status, error) {
    if (error || !status || status.state === "error") {
      return chipState("failed", job, error || status.error || "同步失败");
    }
    if (status.state === "skipped") {
      // 走到这里说明后端判定"水位已是最新"（`sync_one` 的短路，压根没联网），
      // 而前端刚才是按 parquet 末根判它落后的 —— 两者不一致只可能是水位与文件不同步。
      // **不许**照抄手动路径那句"行情源没有返回数据"：这次源根本没被问到。
      // D-50 起也**不算失败**：parquet 确实落后（所以照样给可点的按钮），
      // 只是水位说源那边没有新数据。写成失败会得到
      // 「没补到新数据：本地记录已是最新，文件里还没有新数据」——一句话自己否定自己。
      return chipState("stale", job, "本地记录已是最新，文件里还没有新数据");
    }
    chipState("done", job, null, status.end_ts);
    // 只在"还看着这一只"时才重画：后台补完的是别人，重画会把当前这只覆盖掉。
    // 比的是落库键（`state.key`），不是 `state.code` —— 书签写成 `?code=sz.600759`
    // 时两者不同形态，拿 `state.code` 比会把"还看着"误判成"已经换走"，补完不重画。
    if (state.key === job.code && state.period === job.period) load();
  }
```

**第四块：`chipState` 七态**

```js
  // 浮标唯一的写入点。`mode`：
  //   fresh   —— 已是最新（常驻，不可点）
  //   stale   —— 本地落后、可以补（常驻，**可点**）
  //   syncing —— 正在补（不可点）
  //   queued  —— 排队等补（不可点）
  //   done    —— 刚补完的一句确认（紧接着 load() 会用后端的真结论覆盖它）
  //   blocked —— 本地落后、后端说补不了（常驻，不可点，附 sync_hint 说明为什么）
  //   failed  —— 补失败（常驻，**可点**重试）
  function chipState(mode, job, msg, endTs) {
    // `quiet` 的 job（404：本地一根都没有）整条让位给 `#notice`：那一处更显眼、
    // 在流内、还带完整解释与命令行。两处同时说同一件事只会更乱 ——
    // 实测 2026-10-11 `?code=000033&period=30` 两处都在说话，浮标那行还漏出一个 `?`。
    if (job && job.quiet) return dropChip();
    const el = $("#sync-chip");
    if (!el) return;
    // 讲的不是当前这只票：一个字都不写。后台补完的是别人，写到这张图上就是谎报。
    if (!chipIsAbout(job)) return;
    const text = $("#sync-chip-text");
    const btn = $("#sync-chip-go");
    if (!text || !btn) return;
    const actionable = mode === "stale" || mode === "failed";
    const label = job.local || "无本地数据";
    el.hidden = false;
    chipJob = job;
    el.classList.toggle("is-action", actionable);
    if (mode === "fresh") {
      text.textContent = `已是最新 · ${job.local || job.expected || "最新交易日"}`;
    } else if (mode === "syncing") {
      text.textContent = `正在补最新数据：本地截至 ${label} · 最新 ${job.expected || "?"}…`;
    } else if (mode === "queued") {
      text.textContent = "排队等补最新数据…";
    } else if (mode === "done") {
      text.textContent = `已补到 ${endTs || job.expected || "最新交易日"}`;
    } else if (mode === "blocked") {
      text.textContent = `本地截至 ${label} · 最新 ${job.expected || "?"}`
        + ` · 补不了：${msg || "这个周期补不了"}`;
    } else if (mode === "stale") {
      text.textContent = `本地截至 ${label} · 最新 ${job.expected || "?"}`
        + (msg ? ` · ${msg}` : "");
    } else {
      text.textContent = `补数据没成功：${msg || "未知原因"}`;
    }
    btn.hidden = !actionable;
    btn.onclick = null;   // 先清：别让上一态（或上一只票）的句柄活过这一帧
    if (!actionable) return;
    btn.textContent = mode === "failed" ? "重试" : "点这里补";
    btn.onclick = () => {
      btn.onclick = null;
      // 手动路径：只绕过"本次会话自动试过一次"这一道闸门，其余一条都不绕 ——
      // 仍然走 requestAutoSync，所以"同时只飞一个"和浮标自己的反馈通道都在。
      requestAutoSync(job, true);
    };
  }
```

- [ ] **Step 6: 跑测试确认 2.1 组通过**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q
node --check src/chanlun/web/static/app.js && echo "语法 OK"
```
Expected: **全绿**（既有 17 条里被改过的 4 条 + 未动的 13 条）。

- [ ] **Step 7: 写 2.2 组失败测试（可点路径 + 404 让位）**

在 `tests/web/test_frontend_auto_sync.py` 末尾追加：

```python
def test_the_chip_button_bypasses_only_the_once_per_session_gate():
    """点浮标 = 手动补。它只该绕过「自动只试一次」，别的（同时只飞一个、
    共用 runAutoSync、轮询问发起时那一只）一条都不许绕过。"""
    chip = _fn_code(_js(), "function chipState(")
    assert "requestAutoSync(job, true)" in chip, "手动路径要显式标出来"
    assert "runAutoSync(" not in chip, (
        "手动路径不许绕开 requestAutoSync 直接飞：那样就没有「同时只飞一个」了"
    )
    req = _fn_code(_js(), "function requestAutoSync(")
    assert "if (!manual && autoTried.has(key))" in req, "闸门必须只对自动路径生效"


def test_an_empty_period_lets_the_notice_panel_speak_alone():
    """本地一根都没有（404）时浮标整条让位给 `#notice`。

    实测（2026-10-11，`?code=000033&period=30`）两处会同时说话：notice 说
    「没有这只票的本地数据…」+ 一个「同步这个周期」按钮，浮标同时说
    「正在补最新数据：本地截至 ?，最新交易日 2026-10-09…」—— 那个 `?` 是
    `local` 为 `null` 漏出来的。两处讲同一件事只会更乱，所以 404 分支显式带 `quiet`。
    """
    load = _fn_code(_js(), "async function load(")
    at = load.index("404")
    assert "quiet: true" in load[at:], "404 的 job 要显式标 quiet，不靠 local===null 猜"
    chip = _fn_code(_js(), "function chipState(")
    assert "job.quiet" in chip and "dropChip()" in chip, "quiet 的 job 一个字都不写"


def test_a_stock_that_already_tried_keeps_a_retry_on_itself():
    """自动补只做一次，但「能不能手动再来」必须还看得见。

    收走浮标之后若只是 `return`，就等于把失败一起藏了：图是落后的，
    却一点提示都没有，也没有任何可点的东西。
    """
    req = _fn_code(_js(), "function requestAutoSync(")
    assert "autoTried.has(key)" in req
    assert "autoTried.has(key)) return;" not in req, (
        "静默 return 会把失败一起藏掉：图落后却没有任何提示，也没有可点的重试"
    )
    after = req[req.index("autoTried.has(key)") :]
    assert 'chipState("stale"' in after, "已经自动试过的那一支要给出可点的「点这里补」"
    assert 'chipState("syncing"' in after and 'chipState("queued"' in after, (
        "在跑/在排队时要显示真实状态，不许显示成失败"
    )
    assert "manual" in req, "点浮标是手动路径：只绕过「自动只试一次」这一道闸门"
```

**注意**：文件里**已经有一个**同名函数 `test_a_stock_that_already_tried_keeps_a_retry_on_itself`（现 `:272-286`）。上面这条是它的**替换版** —— 先把旧的整段删掉，再写新的，不要留下两个同名定义（pytest 会静默只收集最后一个，第一个变成死代码）。

- [ ] **Step 8: 跑测试确认 2.2 组失败**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k "bypasses_only or speak_alone or already_tried"
```
Expected: **3 failed**（`manual` 闸门、`quiet`、`chipState("stale"` 都还没写）。若 Step 5 已一次改到位，这三条会直接通过 —— 那就说明 Step 5 做多了，**不要紧，但要如实记下"2.2 组没有红过"**，并在 Task 4 的变异验证里补上对应变异（M4/M5/M6）。

- [ ] **Step 9: 跑全量 web 测试**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/ -q
```
Expected: **全绿**。

- [ ] **Step 10: 提交（本批次不提交，留空）**

---

### Task 3: 静态文件与首页 `cache-control: no-cache`

**Files:**
- Modify: `src/chanlun/web/app.py:17-20`（import）、`:52-70`（装配）
- Test: `tests/web/test_static_cache.py`（**新建**）

**Interfaces:**
- Consumes: `tests/web/conftest.py` 的 `client` 夹具（`client.get(path, headers=…)`）。
- Produces: 所有 `/static/*` 与 `/` 的响应带 `cache-control: no-cache`；带 `If-None-Match` 的第二次请求仍是 **304**。

- [ ] **Step 1: 先量现状，确认这条测试能红**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/python - <<'PY'
from fastapi.testclient import TestClient
from chanlun.config import load_config
from chanlun.web.app import create_app
with TestClient(create_app(load_config())) as c:
    for path in ("/static/app.js", "/"):
        r = c.get(path)
        print(path, r.status_code, "cache-control=", r.headers.get("cache-control"),
              "etag=", bool(r.headers.get("etag")))
PY
```
Expected: 两行都是 `cache-control= None`。**这就是"用户改了前端、你看的是旧页面"的成因之一**，把这两行原样抄进证据文档。

- [ ] **Step 2: 写失败测试**

新建 `tests/web/test_static_cache.py`：

```python
"""静态资源与首页必须带 `cache-control: no-cache`。

2026-10-11 实测（`curl -sI http://127.0.0.1:8888/static/app.js`）：只有
`last-modified` + `etag`，**没有** `Cache-Control`。Chrome 对没有显式新鲜度信息的
响应可以按启发式规则（`(now - last_modified) * 0.1`）自己算一个窗口直接拿缓存 ——
于是"前端改了、用户看的是旧页面"。这正是 D-49 上线后用户报
「也没有自动拉取，也没有手动拉取的按钮」的成因之一。

`no-cache` **不是**"不缓存"：浏览器照存，但每次带 `If-None-Match` 回来校验一句，
没变还是 304（省流量），变了立刻拿到新的。所以第三条测试必须存在 ——
它证明"加了 no-cache 之后 304 这条省钱的路还在"。
"""

from __future__ import annotations


def test_static_files_are_revalidated_every_time(client):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache", (
        "没有显式新鲜度信息时，Chrome 可以按启发式规则直接拿缓存里的旧 app.js"
    )


def test_the_index_is_revalidated_every_time(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_a_revalidated_static_file_still_gets_a_304(client):
    """`no-cache` 不许把 304 这条省钱的路堵死：没变就该只回一个头。"""
    etag = client.get("/static/app.js").headers["etag"]
    r = client.get("/static/app.js", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers.get("cache-control") == "no-cache", (
        "304 也要带 no-cache：Starlette 的 NotModifiedResponse 只白名单几个头，"
        "在 get_response 之后补才不会被它过滤掉"
    )
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_static_cache.py -q
```
Expected: **2 failed, 1 passed** —— 两条 `assert None == 'no-cache'`；第三条（304）**应该已经通过**，因为 Starlette 本来就会做 etag 校验（`starlette/staticfiles.py:216-221`）。第三条的作用是**防止修法把 304 弄丢**，它红不红取决于实现方式：如果谁把 `no-cache` 写进 `FileResponse(headers=...)` 而 304 走了 `NotModifiedResponse` 的白名单过滤，它就**会**红。

- [ ] **Step 4: 改 `app.py`**

**(a)** `app.py:19` 这一行：

```python
from fastapi.responses import FileResponse
```

替换为：

```python
from fastapi.responses import FileResponse, Response
```

> 从 `fastapi.responses` 拿 `Response`，而不是从 `starlette.responses` ——
> `pyproject.toml` 的直接依赖里只有 `fastapi`（`starlette` 是它的传递依赖，
> `pyproject.toml:6-16`）。已实测两者是**同一个对象**
> （`fastapi.responses.Response is starlette.responses.Response` → `True`），
> 所以走前者不丢任何东西，还守住了"只 import 声明过的依赖"这条线。

**(b)** 在 `PortInUseError` 类定义之后、`ensure_port_free` 之前插入：

```python
class RevalidatingStaticFiles(StaticFiles):
    """静态资源每次都要回服务端校验一次（`cache-control: no-cache`）。

    2026-10-11 实测：只带 `last-modified` + `etag` 时，Chrome 可以按
    `(now - last_modified) * 0.1` 自己算一个新鲜度窗口直接拿缓存 —— 于是
    "前端改了、用户看的是旧页面"。这正是 D-49 上线后用户报
    「也没有自动拉取，也没有手动拉取的按钮」的成因之一。

    `no-cache` 不是"不缓存"：浏览器仍存，但每次带 `If-None-Match` 回来问一句，
    没变还是 304（省流量），变了立刻拿到新的。

    **必须在这里补、不能在 `FileResponse(headers=…)` 里给**：Starlette 命中
    etag 时走的是 `NotModifiedResponse`，它只白名单保留
    `cache-control / content-location / date / etag / expires / vary`
    （`starlette/staticfiles.py:25-37`）—— 在 `FileResponse` 上给的头会被过滤掉，
    于是"200 带 no-cache、304 不带"，缓存策略变得两副面孔。
    """

    async def get_response(self, path: str, scope) -> Response:
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = "no-cache"
        return resp
```

**(c)** `app.py:64` 这一行：

```python
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
```

替换为：

```python
    app.mount("/static", RevalidatingStaticFiles(directory=str(STATIC_DIR)), name="static")
```

**(d)** `app.py:66-68`：

```python
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
```

替换为：

```python
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        # 同上：index.html 也必须每次校验，否则书签打开的还是旧页面。
        # `FileResponse` 的 304 分支由 `StaticFiles` 之外没人管，所以这一处
        # 直接把头写在响应上就够（`/` 不走 `NotModifiedResponse`）。
        return FileResponse(STATIC_DIR / "index.html", headers={"cache-control": "no-cache"})
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_static_cache.py tests/web/test_api.py -q
```
Expected: **全绿**（`test_api.py` 一起跑，确认 `create_app` 装配没被碰坏）。

- [ ] **Step 6: 重启服务并量真实响应头**

```bash
cd /Users/zzz/workspace/chanlun
pkill -f "chanlun serve" ; sleep 1
PYTHONPATH=src PYTHONUNBUFFERED=1 nohup ../.venv-chanlun/bin/python -m chanlun serve > /tmp/serve.log 2>&1 &
sleep 3
curl -sI http://127.0.0.1:8888/static/app.js | grep -i "cache-control\|etag"
curl -sI http://127.0.0.1:8888/ | grep -i "cache-control\|etag"
ETAG=$(curl -sI http://127.0.0.1:8888/static/app.js | grep -i '^etag' | tr -d '\r' | cut -d' ' -f2)
curl -sI -H "If-None-Match: $ETAG" http://127.0.0.1:8888/static/app.js | head -3
```
Expected: 两处都有 `cache-control: no-cache`；第三次请求首行是 `HTTP/1.1 304 Not Modified` 且仍带 `cache-control: no-cache`。

- [ ] **Step 7: 提交（本批次不提交，留空）**

---

### Task 4: 变异验证 —— 证明每条新断言都真的能红

**Files:**
- Modify（临时）：`src/chanlun/web/static/app.js`、`src/chanlun/web/app.py`
- Test: `tests/web/test_frontend_auto_sync.py`、`tests/web/test_static_cache.py`

**Interfaces:**
- Consumes: Task 1–3 的全部产物。
- Produces: 8 条变异的红/绿记录，逐条写进 `docs/evidence/2026-10-11-浮标常驻验收.md`（Task 5）。

**先做一件事：重新冻结变异还原源。** `/tmp/app_frozen.js` 现在是 **D-50 之前**的版本，用它还原会把 D-50 抹掉：

```bash
cd /Users/zzz/workspace/chanlun
cp src/chanlun/web/static/app.js /tmp/app_frozen_d50.js
sha256sum src/chanlun/web/static/app.js /tmp/app_frozen_d50.js
```
Expected: 两行摘要相同。

- [ ] **Step 1: 变异 M1 —— 常驻改回"4 秒后自动消失"**

```bash
cd /Users/zzz/workspace/chanlun
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/static/app.js")
s = p.read_text()
old = "    btn.hidden = !actionable;\n"
new = "    btn.hidden = !actionable;\n    setTimeout(() => { el.hidden = true; }, 4000);\n"
assert s.count(old) == 1, s.count(old)
p.write_text(s.replace(old, new))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k persistent_readout
```
Expected: **1 failed** —— `assert 'setTimeout' not in body`。然后还原：

```bash
cp /tmp/app_frozen_d50.js src/chanlun/web/static/app.js && cmp -s /tmp/app_frozen_d50.js src/chanlun/web/static/app.js && echo "还原逐字节相同"
```

- [ ] **Step 2: 变异 M2 —— 动作挂回整块浮标**

```bash
cd /Users/zzz/workspace/chanlun
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/static/app.js")
s = p.read_text()
old = "    btn.onclick = () => {\n"
new = "    el.onclick = () => {\n"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k persistent_readout
```
Expected: **1 failed** —— `assert 'el.onclick' not in body`。还原 `cp /tmp/app_frozen_d50.js …` + `cmp -s`。

- [ ] **Step 3: 变异 M3 —— 状态文字写回容器**

```bash
cd /Users/zzz/workspace/chanlun
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/static/app.js")
s = p.read_text()
old = '      text.textContent = `已是最新 · '
new = '      el.textContent = `已是最新 · '
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k persistent_readout
```
Expected: **1 failed** —— `assert 'el.textContent' not in body`（这条变异会把按钮抹掉，正是护栏要防的）。还原 + `cmp -s`。

- [ ] **Step 4: 变异 M4 —— 闸门对自动路径也放行**

```bash
cd /Users/zzz/workspace/chanlun
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/static/app.js")
s = p.read_text()
old = "    if (!manual && autoTried.has(key)) {\n"
new = "    if (autoTried.has(key)) {\n"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k "bypasses_only"
```
Expected: **1 failed** —— `assert 'if (!manual && autoTried.has(key))' in req`。还原 + `cmp -s`。

- [ ] **Step 5: 变异 M5 —— 404 让位去掉**

```bash
cd /Users/zzz/workspace/chanlun
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/static/app.js")
s = p.read_text()
old = "    if (job && job.quiet) return dropChip();\n"
assert s.count(old) == 1
p.write_text(s.replace(old, ""))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k "speak_alone"
```
Expected: **1 failed** —— `assert 'job.quiet' in chip`。还原 + `cmp -s`。

- [ ] **Step 6: 变异 M6 —— 已经试过的那一支改回静默 `return`**

```bash
cd /Users/zzz/workspace/chanlun
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/static/app.js")
s = p.read_text()
old = '      return chipState("stale", job, "本次会话已经自动补过一次");\n'
new = "      return;\n"
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k "already_tried"
```
Expected: **1 failed** —— `assert 'chipState("stale"' in after`。还原 + `cmp -s`。

> **这一条要记进证据文档的"假绿"栏**：D-49 的同名护栏只写了
> `assert "autoTried.has(key)) return;" not in req` 这一条**形状**断言。把
> `chipState("failed", …)` 换成裸 `return;` 会**被**它咬住；但 D-49 那版把这一支
> 写成 `chipState("failed", job, "本次会话已经自动补过一次，没有再自动重试")` ——
> 形状合法、也确实可点，所以旧护栏全绿。D-50 新增的正向断言
> （`'chipState("stale"' in after`）才把"必须说话、且必须可点"钉死。

- [ ] **Step 7: 变异 M7 —— `fresh` 改回静默**

```bash
cd /Users/zzz/workspace/chanlun
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/static/app.js")
s = p.read_text()
old = '    if (body.stale !== true) return chipState("fresh", job);'
new = '    if (body.stale !== true) return;'
assert s.count(old) == 1
p.write_text(s.replace(old, new))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_frontend_auto_sync.py -q \
  -k "verdict_comes_from_the_backend"
```
Expected: **1 failed** —— `assert 'chipState("fresh"' in body`。还原 + `cmp -s`。

- [ ] **Step 8: 变异 M8 —— 静态缓存头去掉**

```bash
cd /Users/zzz/workspace/chanlun
cp src/chanlun/web/app.py /tmp/app_py_frozen.py
python3 - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/web/app.py")
s = p.read_text()
old = '        resp.headers["cache-control"] = "no-cache"\n'
assert s.count(old) == 1
p.write_text(s.replace(old, ""))
PY
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/test_static_cache.py -q
```
Expected: **1 failed, 2 passed**（`/static/app.js` 那条红）。还原：

```bash
cp /tmp/app_py_frozen.py src/chanlun/web/app.py && cmp -s /tmp/app_py_frozen.py src/chanlun/web/app.py && echo "还原逐字节相同"
```

- [ ] **Step 9: 还原后跑全量 web 测试确认干净**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/web/ -q
sha256sum src/chanlun/web/static/app.js src/chanlun/web/app.py
```
Expected: **全绿**；两个摘要与 `/tmp/app_frozen_d50.js`、`/tmp/app_py_frozen.py` 相同。

- [ ] **Step 10: 提交（本批次不提交，留空）**

---

### Task 5: 真浏览器验收 + 证据文档

**Files:**
- Create: `docs/evidence/2026-10-11-浮标常驻验收.md`
- Modify（临时，不入库）：`/tmp/probe_chip_fresh.js`、`/tmp/probe_chip_stale.js`、`/tmp/probe_chip_404.js`、`/tmp/probe_chip_layout.js`

**Interfaces:**
- Consumes: Task 1–4 的产物、`/tmp/dsh_cdp_eval.mjs`、运行中的服务（`http://127.0.0.1:8888`）。
- Produces: 证据文档，含四态文案、可点性、404 让位、布局零位移、缓存头、变异表、`无实盘样本` 的显式声明。

**前置**：`app.js` 是静态文件，浏览器**必须硬刷新**。每个探针 URL 都带 `&cb=$RANDOM$RANDOM` 破缓存。**探针里不许用 `$`** —— 页面是 IIFE，`$` 不是全局的，每个探针自己写 `const q = (s) => document.querySelector(s);`。

- [ ] **Step 1: 确认服务端加载的是新 `app.js`**

```bash
cd /Users/zzz/workspace/chanlun
curl -s "http://127.0.0.1:8888/static/app.js?cb=$RANDOM" | grep -c "noteFreshness\|sync-chip-go"
sha256sum src/chanlun/web/static/app.js
curl -s "http://127.0.0.1:8888/static/app.js?cb=$RANDOM" | shasum -a 256
```
Expected: `grep -c` ≥ 6；两个摘要相同。

- [ ] **Step 2: 探针 P1 —— 「已是最新」态（`600000&day`）**

```bash
cat > /tmp/probe_chip_fresh.js <<'JS'
(async () => {
  const q = (s) => document.querySelector(s);
  await new Promise((r) => setTimeout(r, 2500));
  const chip = q("#sync-chip"), text = q("#sync-chip-text"), btn = q("#sync-chip-go");
  return {
    chipHidden: chip ? chip.hidden : null,
    chipText: text ? text.textContent : null,
    btnHidden: btn ? btn.hidden : null,
    btnText: btn ? btn.textContent : null,
    chipClass: chip ? chip.className : null,
    chipPE: chip ? getComputedStyle(chip).pointerEvents : null,
    noticeDisplay: q("#notice") ? getComputedStyle(q("#notice")).display : null,
    actions: [...document.querySelectorAll(".notice-action")].map((b) => b.textContent),
  };
})()
JS
node /tmp/dsh_cdp_eval.mjs "http://127.0.0.1:8888/?code=600000&period=day&cb=$RANDOM$RANDOM" 3000 "$(cat /tmp/probe_chip_fresh.js)"
```
Expected（**逐字**）：`chipHidden false`、`chipText "已是最新 · 2026-10-09"`、`btnHidden true`、`chipClass "sync-chip"`（**没有** `is-action`）、`chipPE "none"`、`noticeDisplay "none"`、`actions []`。

- [ ] **Step 3: 探针 P2 —— 落后可点 → 点一下 → 补完（`399001&day`）**

`399001`（深证成指）日线本地末根 `2026-09-30`、后端判 `stale=true`、日线周期 `syncable=true` ⇒ 正好是"有数据但落后"这一态。

```bash
cat > /tmp/probe_chip_stale.js <<'JS'
(async () => {
  const q = (s) => document.querySelector(s);
  const snap = () => ({
    chipHidden: q("#sync-chip") ? q("#sync-chip").hidden : null,
    chipText: q("#sync-chip-text") ? q("#sync-chip-text").textContent : null,
    btnHidden: q("#sync-chip-go") ? q("#sync-chip-go").hidden : null,
    btnText: q("#sync-chip-go") ? q("#sync-chip-go").textContent : null,
    chipClass: q("#sync-chip") ? q("#sync-chip").className : null,
  });
  await new Promise((r) => setTimeout(r, 2500));
  const before = snap();
  const btnPE = q("#sync-chip-go") ? getComputedStyle(q("#sync-chip-go")).pointerEvents : null;
  q("#sync-chip-go").click();                       // 真点击，不是直接调函数
  const frames = [];
  for (let i = 0; i < 225; i++) {                   // 最长 45s：实测单只同步可到 28.5s
    await new Promise((r) => setTimeout(r, 200));
    const s = snap();
    if (!frames.length || frames[frames.length - 1].chipText !== s.chipText) frames.push(s);
  }
  return { before, btnPE, frames, after: snap(), stamp: q("#stamp").textContent };
})()
JS
BEFORE=$(grep -c "POST /api/sync" /tmp/serve.log)
node /tmp/dsh_cdp_eval.mjs "http://127.0.0.1:8888/?code=399001&period=day&cb=$RANDOM$RANDOM" 3000 "$(cat /tmp/probe_chip_stale.js)"
AFTER=$(grep -c "POST /api/sync" /tmp/serve.log)
echo "POST /api/sync: $BEFORE -> $AFTER"
```
Expected：
- `before.chipText` = `"本地截至 2026-09-30 · 最新 2026-10-09"`，`before.btnHidden false`，`before.btnText "点这里补"`，`before.chipClass` 含 `is-action`，`btnPE "auto"`。
- `frames` 依次出现 `"正在补最新数据：本地截至 2026-09-30 · 最新 2026-10-09…"`、`"已补到 2026-10-09"`，最后落到 `"已是最新 · 2026-10-09"`。
- **`POST /api/sync` 恰好 +1**（不是 0，也不是 2）。
- `after.btnHidden true`。
- `stamp` 里 `截至 2026-10-09`。

> **副作用要如实披露**：这一次点击会真的把 `深证成指` 的日线补到 `2026-10-09`（它本来就在自选池里、也确实落后），
> 所以**重跑这条探针时 `before` 会变成「已是最新」**、`POST` 增量为 0。证据文档里要写清
> 「这条探针**只能红一次**，重跑必须换一只落后的票或先删掉 `data/day/sz/399001.parquet`」。
> 换票清单（2026-10-11 实测落后的日线，全在自选池里）：`399006`、`sh.000016`、`sh.000905`、`sh.000688`。

- [ ] **Step 4: 探针 P3 —— 404 让位（`000033&period=30`）**

```bash
cat > /tmp/probe_chip_404.js <<'JS'
(async () => {
  const q = (s) => document.querySelector(s);
  await new Promise((r) => setTimeout(r, 6000));
  const chip = q("#sync-chip");
  return {
    noticeDisplay: q("#notice") ? getComputedStyle(q("#notice")).display : null,
    noticeText: q("#notice") ? q("#notice").textContent : null,
    chipHidden: chip ? chip.hidden : null,
    chipText: q("#sync-chip-text") ? q("#sync-chip-text").textContent : null,
    actions: [...document.querySelectorAll("button")].filter((b) => !b.hidden && b.offsetParent).map((b) => b.textContent),
  };
})()
JS
node /tmp/dsh_cdp_eval.mjs "http://127.0.0.1:8888/?code=000033&period=30&cb=$RANDOM$RANDOM" 3000 "$(cat /tmp/probe_chip_404.js)"
```
Expected：`noticeDisplay "flex"`、`chipHidden true`、`chipText ""`、`actions` 里**只有** `"同步这个周期"`（不许出现 `"点这里补"`）。

> 对照：D-50 之前同一探针（`/tmp/probe_404.js`，2026-10-11 实测）是
> `noticeDisplay "flex"` + `chipHidden false` + `chipText "正在补最新数据：本地截至 ?，最新交易日 2026-10-09…"`。
> 两行都抄进证据文档 —— 这就是"让位"这条改动的可失败对照。

- [ ] **Step 5: 探针 P4 —— 布局零位移（四态 rect 全等）**

```bash
cat > /tmp/probe_chip_layout.js <<'JS'
(async () => {
  const q = (s) => document.querySelector(s);
  const rect = (s) => { const e = q(s); if (!e) return null; const r = e.getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const snap = () => ({
    chart: rect("#chart"), stage: rect(".stage"), stamp: rect("#stamp"),
    stampDetail: rect("#stamp-detail"), ledger: rect("#ledger"),
    bodyRows: getComputedStyle(document.body).gridTemplateRows,
    bodyKids: document.body.children.length,
    scrollH: document.documentElement.scrollHeight,
    chip: rect("#sync-chip"),
  });
  await new Promise((r) => setTimeout(r, 2500));
  const states = {};
  states.当前 = snap();
  const chip = q("#sync-chip"), text = q("#sync-chip-text"), btn = q("#sync-chip-go");
  const wasHidden = chip.hidden;
  chip.hidden = false; text.textContent = "本地截至 2026-09-30 · 最新 2026-10-09 · 补不了：" +
    "sh.000001 是指数，baostock 不提供指数的分钟线，同步这个周期只会空跑。指数看日线/周线/月线即可（周月线由本地日线聚合）。";
  btn.hidden = true;
  states.强制可见_长文案 = snap();
  chip.remove();
  states.移除 = snap();
  return { states, wasHidden };
})()
JS
node /tmp/dsh_cdp_eval.mjs "http://127.0.0.1:8888/?code=600000&period=day&cb=$RANDOM$RANDOM" 3000 "$(cat /tmp/probe_chip_layout.js)"
```
Expected：三个态里 `chart` / `stage` / `stamp` / `stampDetail` / `ledger` 的 rect **逐项相同**，`bodyRows`、`bodyKids`、`scrollH` 也相同；`强制可见_长文案.chip` 的宽度 ≤ 420（`max-width: min(420px, 60%)`）且**高度 > 单行高度**（证明 84 字 `sync_hint` 折了行、没有被截断）。`当前.chip` 是 `已是最新` 那一条的 rect。

> 这条探针只在"新开一个页面"时准 —— 它会把 `#sync-chip` 从 DOM 里摘掉，之后那次 `load()` 的结论无处可写。**每次跑都新开 URL**。

- [ ] **Step 6: 探针 P5 —— `blocked` 态只有静态断言，如实声明**

没有活的 `blocked` 实例：`syncable=false` 只在"指数 + 分钟周期"出现，而 13 只有 30 分数据的票**全是 A 股股票**（`data/30/sh/`：600000、600030、600519、600759、601398、601857、603178；`data/30/sz/`：000002、001696、002309、002868、301686、301716）。复核：

```bash
cd /Users/zzz/workspace/chanlun
ls data/30/sh data/30/sz | tr '\n' ' '; echo
PYTHONPATH=src ../.venv-chanlun/bin/python - <<'PY'
import json, urllib.request
for code in ("sh.000001", "sh.000300"):
    with urllib.request.urlopen(f"http://127.0.0.1:8888/api/structure?code={code}&period=30") as r:
        print(code, r.status, json.load(r))
PY
```
Expected：第二个命令对两个指数都返回 404，`"syncable": false`，`"sync_hint"` 是那段 84 字的指数说明。把 `sync_hint` 全文抄进证据文档，并显式写：

> **`blocked` 态本次**没有**在真实浏览器里见过活的实例** —— 它只能由静态断言
> （`test_the_verdict_comes_from_the_backend` 的 `chipState("blocked"` + `body.sync_hint`）
> 保证。没有活样本的原因是数据本身的形状，不是没测。

- [ ] **Step 7: 写证据文档**

新建 `docs/evidence/2026-10-11-浮标常驻验收.md`，按 D-49 那份的骨架（§0 一句话 / §1 怎么跑 / §2 背景 / §3 验收 / §4 实施中发现并修掉的缺陷 / §5 顺带改正的老引用 / §6 这次**没有**修的东西 / §7 复现命令汇总），把上面每一步的**原始输出**贴进去。硬要求：

- §2 必须写清用户原话 + 两句断言各自的实测结论（"没有按钮"成立、"没有自动拉取"在正确点击流程里不成立、但一切正常时彻底静默）。
- §3 每条都要有命令 + 原始输出 + Expected 是否命中。
- §3 必须有一小节专门写**缓存头**：`curl -sI` 的前后对照。
- §3 必须有一小节写**变异表**：M1–M8 各自的"改什么 / 哪条断言红 / 还原后逐字节相同"。
- §4 记两条：`skipped` 从"失败"改成"落后可点"（原文案自己否定自己）；`syncing` 文案里那个 `?`。
- §6 写清：全市场补数据 / 历史缺口检测 / 定时任务补跑仍然不做；`behind` 计数不做；`blocked` 无活样本。
- §7 把 P1–P4 的完整命令行列出来，每条都能重跑（并注明 P2 **只能红一次**）。

- [ ] **Step 8: 提交（本批次不提交，留空）**

---

### Task 6: ARCHITECTURE.md / CHANGELOG.md / 行号基线重锚

**Files:**
- Modify: `ARCHITECTURE.md`（新增 `#### D-50`；D-49 补「变更历史」）
- Modify: `CHANGELOG.md`（`[Unreleased]` 新起一条）
- Modify: `optimizer/tools/doc_refs_baseline.json`（`--update-baseline`）
- Modify: `tests/optimizer/test_check_doc_line_refs.py`（5 组硬编码镜像重新推导）

**Interfaces:**
- Consumes: Task 1–5 全部产物；`optimizer/tools/check_doc_line_refs.py` 的 CLI（`--doc / --root / --section / --update-baseline / --no-fingerprints`）。
- Produces: 守卫 `exit 0`、`不符 0 / 未入基线 0 / 残留 0`；全量测试全绿。

**先取实施后的真实行号**（`ARCHITECTURE.md` 的引用必须逐条对得上，不许凭记忆写）：

```bash
cd /Users/zzz/workspace/chanlun
grep -n "function noteFreshness(\|function requestAutoSync(\|function chipState(\|function dropChip(\|function chipIsAbout(\|function finishAutoSync(\|const autoTried\|state.key = body.code\|state.key = state.code" src/chanlun/web/static/app.js
grep -n "sync-chip-text\|sync-chip-go\|id=\"sync-chip\"" src/chanlun/web/static/index.html
grep -n "\.sync-chip\b\|\.sync-chip\.is-action\|\.sync-chip-go" src/chanlun/web/static/styles.css
grep -n "RevalidatingStaticFiles\|cache-control\|app.mount" src/chanlun/web/app.py
grep -n "def freshness(\|expected_as_of\|def sync_blocker(" src/chanlun/web/api.py
wc -l src/chanlun/web/static/app.js src/chanlun/web/static/styles.css src/chanlun/web/static/index.html ARCHITECTURE.md
```

- [ ] **Step 1: 在 `ARCHITECTURE.md` 里新增 `#### D-50`**

位置：紧接 D-49 那一段之后、`## 3. 结构层` 之前。四段式，逐字如下（把 `<…>` 换成 Step 0 量到的真行号）：

```markdown
#### D-50 浮标常驻：它是「这一只这一周期」的数据状态读数，落后时自己可点

**决策**：右下角浮标从"有事才冒出来、4 秒后消失"改成**常驻**，恒定回答
「你正在看的这一只、这个周期，数据到哪儿了、能不能补」，并把动作从整块浮标
挪进一个真 `<button>`。

- 后端一行未动：判据仍然全部来自 `/api/structure` 的 `stale` / `syncable` /
  `sync_hint` / `expected_as_of`（`src/chanlun/web/api.py:<freshness 行>`）。
- 前端唯一写浮标的地方是 `noteFreshness()`（`src/chanlun/web/static/app.js:<行>`），
  它按后端结论分派三种常驻态：`fresh` / `stale` / `blocked`。
- 七态：`fresh` / `stale`（可点）/ `syncing` / `queued` / `done` / `blocked` /
  `failed`（可点），实现见 `chipState()`（`app.js:<行>`）。
- 手动入口：浮标里的 `#sync-chip-go`（`index.html:<行>`、`styles.css:<行>`），
  点击走 `requestAutoSync(job, true)`（`app.js:<行>`）—— **只**绕过
  `autoTried` 的"本次会话只自动试一次"闸门，其余（同时只飞一个、共用
  `runAutoSync`）一条都不绕。
- 本地一根都没有（404）时浮标整条让位：`job.quiet === true` ⇒ `dropChip()`
  （`app.js:<行>`），把画面留给 `#notice`（它有完整解释 + 命令行 + 可点按钮）。
- 整块浮标继续 `pointer-events: none`，可点性放在子元素上
  （`styles.css:<行>`）；`no-cache` 让浏览器每次回服务端校验
  （`src/chanlun/web/app.py:<RevalidatingStaticFiles 行>`）。

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
   `body` 网格行数逐项相同；D-50 复量四态仍相同（证据见
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
- **本次不涨版本号**（`pyproject.toml` 不动），CHANGELOG 条目留在 `[Unreleased]`。
- **`chan/` 一行未动** ⇒ 不是判据变更，不需要 `optimizer/theory/` 条目。
- D-49 的浮标语义由本条取代；D-49 记录的其余部分（`freshness` 契约、
  `state.key` 落库键、`sync_blocker`、会话内退避）全部照旧有效。
```

- [ ] **Step 2: 在 D-49 里补一行「变更历史」指过来**

D-49 的「后果 / 变更历史」段落末尾（现有那一行讲 MINOR 不涨版本号 + 证据链接的后面）追加一行：

```markdown
- **变更历史（D-50，2026-10-11）**：浮标语义被 `#### D-50` 取代 ——
  从"有事才出现、4 秒后自动消失、失败态整块可点"改成"常驻 + 里面放真 button"，
  `maybeAutoSync()` 相应更名为 `noteFreshness()`。本条的其余部分
  （`freshness` 契约、`state.key` 落库键、`sync_blocker`、会话内退避）仍然有效。
```

- [ ] **Step 3: 在 `CHANGELOG.md` 的 `[Unreleased]` 里新起一条**

位置：`### 新增` 之下、D-49 那条 bullet **之前**（新的在上）。先读一次确认锚点：

```bash
cd /Users/zzz/workspace/chanlun
grep -n "^## \[Unreleased\]\|^### 新增\|^### 修复\|^### 已知空白与未实现项" CHANGELOG.md | head
sed -n '43,56p' CHANGELOG.md
```

用 `read` 工具读 `CHANGELOG.md` 的 `offset 48, limit 12` 之后，用 `edit` 把 `### 新增\n\n- **A 股一级/二级行业指数…` 这个锚点替换成 `### 新增\n\n<新条目>\n\n- **A 股一级/二级行业指数…`（**必须带上下一条 bullet 的开头**：`### 新增\n\n` 在全文件出现 14 次，单独拿它做锚点会命中错的地方）。新条目逐字如下：

```markdown
- **看盘页右下角浮标改成常驻，落后时自己可点补数据（D-50，本条尚未发版）** ——
  语义上是向后兼容的新功能（MINOR），但本次**不涨版本号**，条目留在 `[Unreleased]`。
  - **它现在恒在**：不再"有事才冒出来、4 秒后消失"，而是常驻回答
    「你正在看的这一只、这个周期，数据到哪儿了」。七态：
    `已是最新 · <日期>` / `本地截至 X · 最新 Y` / `正在补最新数据：…` /
    `排队等补最新数据…` / `已补到 X` / `本地截至 X · 最新 Y · 补不了：<原因>` /
    `补数据没成功：<原因>`。
  - **落后时浮标里有一个真按钮**（`点这里补` / `重试`）。此前全 UI 只有一个手动入口
    —— `#notice` 里的「同步这个周期」，而它**只在 404（本地一根都没有）时出现**；
    有数据但落后时一个按钮都没有。这就是用户 2026-10-11 说的
    「也没有手动拉取的按钮」。
  - **点击只绕过一道闸门**：手动路径仍然走 `requestAutoSync → runAutoSync → pollSync`，
    只跳过"本次会话只自动试一次"的 `autoTried` 检查；"同时只飞一个"和轮询
    「问发起时那一只」两条不变量都不变。
  - **本地一根都没有（404）时浮标整条让位**给 `#notice`（它有完整解释 + 命令行 +
    可点按钮）。此前两处会同时说话，浮标那行还会漏出一个 `?`。
  - **`skipped` 不再算失败**：后端说"水位已是最新、文件里还没有新数据"时，
    浮标如实显示"本地落后 + 可以再点一次"，而不是 D-49 那句自己否定自己的
    「没补到新数据：本地记录已是最新，文件里还没有新数据」。
  - **静态资源与首页加 `cache-control: no-cache`**：此前 `curl -sI /static/app.js`
    只有 `last-modified` + `etag`，Chrome 可以按启发式规则直接拿缓存里的旧 `app.js`
    —— 「我改了前端、你看的是旧页面」正是这次误判的成因之一。`no-cache` 不是
    "不缓存"：每次回服务端校验，没变仍是 304。
  - **`chan/` 一行未动** ⇒ 不是判据变更，不需要 `optimizer/theory/` 条目。
  - 设计与实测：`docs/superpowers/specs/2026-10-11-chip-persistent-state-design.md`、
    `docs/evidence/2026-10-11-浮标常驻验收.md`；决策记录见 `ARCHITECTURE.md` 的 `D-50`。
```

- [ ] **Step 4: 在 `### 已知空白与未实现项` 里补一条**

同 §1 的纪律：新发现的空白必须记。在 `### 已知空白与未实现项` 的**第一条 bullet 之前**插入（锚点同样要带上下一条 bullet 的开头）：

```markdown
- **「有数据、落后、补不了」这一态（`blocked`）没有活样本**（2026-10-11 实测）：
  `/api/structure` 判 `syncable=false` 只发生在"指数 + 分钟周期"，而本地有 30 分
  数据的 13 只票**全是 A 股股票**（`data/30/sh/` 7 只、`data/30/sz/` 6 只），
  所以这一态只能由静态断言（`tests/web/test_frontend_auto_sync.py`）保证，
  没在真实浏览器里见过活的。这不是没测，是数据形状如此。
```

- [ ] **Step 5: 跑守卫，按它报的清单重锚**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py --verbose 2>&1 | tail -40
```
Expected: 一大批 `不符`（`app.js` / `styles.css` / `index.html` / `app.py` 的引用因插入而位移，`ARCHITECTURE.md` 自身引用因新增 D-50 而整体位移）。**逐条按内容重锚，绝不用 `+N` 硬加**：

- 先导出守卫认到的每一条引用与它的期望指纹：
  ```bash
  PYTHONPATH=src ../.venv-chanlun/bin/python /tmp/dump_refs.py > /tmp/refs_before.txt
  ```
  （`/tmp/dump_refs.py` 是 D-49 那次留下的抛荒脚本，用 `REPO = Path("/Users/zzz/workspace/chanlun")`、
  逐条调 `parse_refs` / `resolve_ref(ref.filename)` / `cited_lines`；若已不在，按
  `optimizer/tools/check_doc_line_refs.py` 的 `Ref` 字段（`doc_line` / `path` / `ext` /
  `ranges` / `span` / `raw`，属性 `filename`、方法 `ranges_text()`）重写一份。）
- 对每条 `不符`：在**新**文件里按内容找到那一行（`grep -n "<该行原文的关键词>"`），
  读出它的真实行号，替换文档里的 `文件:行号`，再重算指纹确认。
- **`ARCHITECTURE.md` 自身的引用也要重锚**：新增 D-50 会让 D-49 之后的所有自引用位移。
- 若某条 `不符` 是**假红**（如 `app.js:233-233#0` 那种"基线键不含文档行号"造成的撞号，
  D-49 那次踩过），把它记进证据文档 §5，然后 `--update-baseline` 重录。

- [ ] **Step 6: 重新推导 `tests/optimizer/test_check_doc_line_refs.py` 的 5 组硬编码镜像**

那个测试文件里对**真实文档**硬编码了多组数字，加一个 `D-xx` 必须**全部**重新推导：

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py 2>&1 | grep -n "引用总数\|非 .py\|引用行无可校验符号\|覆盖度偏弱点\|硬判据"
grep -c "（$(wc -l < src/chanlun/web/static/app.js | tr -d ' ') 行）" <(PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py 2>&1)
grep -n "2156\|993\|80 处\|311 处\|172" tests/optimizer/test_check_doc_line_refs.py
```
需要重推的五组（D-49 那次的值 → 本次的新值，**以实际输出为准**）：
1. `引用总数（.py 桶）：全文 311 处 / 284 行；§3.4 79 处 / 73 行`
2. `非 .py 引用（80 处 / 62 行）`
3. `out.count("（<app.js 行数> 行）") == 53` 与 `out.count("（<styles.css 行数> 行）") == 6`
4. disclosure `引用行无可校验符号 172` 对应的 `int(m.group(1)) == 172` 与 `len(rows) == 172`
5. `test_real_architecture_doc_is_green_and_enumerates_app_js` 里那个 13 元组 ——
   每条按内容重锚（`/tmp/locate.py` 那套：在 `git show HEAD:<file>` 里按内容找行，
   再映射到新文件），**不许硬加偏移**。

改完跑：

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest tests/optimizer/test_check_doc_line_refs.py -q
```
Expected: **全绿**（D-49 后是 43 passed）。

- [ ] **Step 7: 更新基线并确认守卫彻底干净**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py --update-baseline
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py; echo "exit=$?"
PYTHONPATH=src ../.venv-chanlun/bin/python -c "import json;d=json.load(open('optimizer/tools/doc_refs_baseline.json'));print(d['version'], len(d['refs']))"
```
Expected: `exit=0`，摘要里 `不符 0 / 未入基线 0 / 残留 0`，`2 <条目数>`（D-49 后是 387）。

- [ ] **Step 8: 提交（本批次不提交，留空）**

---

### Task 7: 全量测试 + 一次性提交

**Files:**
- 无新改动；只跑测试、看 diff、提交。

- [ ] **Step 1: 全量测试**

```bash
cd /Users/zzz/workspace/chanlun
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q 2>&1 | tail -5
```
Expected: 全绿。D-49 后的基线是 `1247 passed, 12 deselected, 1 warning`（耗时 ~70 s）。本次新增 7 条测试（Task 1 两条 + Task 2 三条 + Task 3 三条 = 8 条，其中 Task 2 的 `already_tried` 是**替换**不是新增）⇒ 预期 `1254 passed` 上下。**以实际输出为准，写进提交信息**。

- [ ] **Step 2: 逐路径看清 diff，确认没有多余文件**

```bash
cd /Users/zzz/workspace
git status --short
cd chanlun && git diff --stat
```
Expected: 改动只落在本计划的 10 个路径上（`src/chanlun/web/app.py`、`src/chanlun/web/static/{index.html,styles.css,app.js}`、`tests/web/{test_frontend_auto_sync.py,test_static_cache.py}`、`ARCHITECTURE.md`、`CHANGELOG.md`、`docs/superpowers/specs/2026-10-11-chip-persistent-state-design.md`、`docs/superpowers/plans/2026-10-11-chip-persistent-state.md`、`docs/evidence/2026-10-11-浮标常驻验收.md`、`optimizer/tools/doc_refs_baseline.json`、`tests/optimizer/test_check_doc_line_refs.py`）。**仓库根上那些未跟踪的（`.DS_Store`、`3d-fitting-room/`、`chan-structure-engine-notes.md`）一个都不许加。**

- [ ] **Step 3: 提交**

```bash
cd /Users/zzz/workspace
git add chanlun/src/chanlun/web/app.py \
        chanlun/src/chanlun/web/static/index.html \
        chanlun/src/chanlun/web/static/styles.css \
        chanlun/src/chanlun/web/static/app.js \
        chanlun/tests/web/test_frontend_auto_sync.py \
        chanlun/tests/web/test_static_cache.py \
        chanlun/ARCHITECTURE.md \
        chanlun/CHANGELOG.md \
        chanlun/docs/superpowers/specs/2026-10-11-chip-persistent-state-design.md \
        chanlun/docs/superpowers/plans/2026-10-11-chip-persistent-state.md \
        chanlun/docs/evidence/2026-10-11-浮标常驻验收.md \
        chanlun/optimizer/tools/doc_refs_baseline.json \
        chanlun/tests/optimizer/test_check_doc_line_refs.py
git commit -F - <<'MSG'
feat(web): 浮标常驻化 + 手动补数据入口 + 静态文件 no-cache（D-50）

用户 2026-10-11 原话：「我看现在这个也没有那个手动拉取的那个按钮呀。就比如说是
ST洲际的30分钟K线，我点进去之后，它也没有自动拉取，也没有手动拉取的按钮」。
拆成两句可验证的断言，实测结果：

1. 「没有手动拉取的按钮」——**成立**。全 UI 只有一个手动入口 `app.js:229` 的
   「同步这个周期」，它只在 `/api/structure` 返回 404 时渲染；`600759` 30 分
   **有**本地数据、只是落后（2026-09-30 15:00），走 200 分支 —— 那个分支上
   一个按钮都没有。
2. 「也没有自动拉取」——在**正确的点击流程里不成立**。实测
   `?code=002309&period=day` 点 `.period[data-period=30]`，2 秒后浮标出现
   「已补到 2026-10-09 15:00」、`POST /api/sync` +1。真正的问题是
   **一切正常时彻底静默**：用户分不出「已是最新」和「功能没跑」。
3. 第三个成因是缓存：`curl -sI /static/app.js` 只有 last-modified + etag，
   **没有 Cache-Control** ⇒ Chrome 可按启发式规则直接拿旧 app.js ⇒
   「我改了前端、你看的是旧页面」。

改动：

- 浮标**常驻**，七态：fresh / stale（可点）/ syncing / queued / done /
  blocked / failed（可点）。判据仍然全部来自后端，前端一条都没新造。
- 动作从整块浮标挪进真 `<button id="sync-chip-go">`；整块继续
  `pointer-events: none`（不挡图上的十字线/缩放），可点性放在子元素上。
- 删掉 4 秒自动消失的定时器：`done` 那句「已补到 X」由紧接着的 `load()`
  用后端真结论覆盖。
- `maybeAutoSync()` → `noteFreshness()`（它现在总是发布一个结论）。
- 手动路径 `requestAutoSync(job, true)` **只**绕过 `autoTried` 的
  「本次会话只自动试一次」闸门，其余（同时只飞一个、共用 runAutoSync、
  轮询问发起时那一只）一条都不绕。
- 404（本地一根都没有）时 `job.quiet` ⇒ 浮标整条让位给 `#notice`。
  此前两处同时说话，浮标那行还漏出一个 `?`。
- `skipped` 从失败态改成落后可点态：D-49 那句
  「没补到新数据：本地记录已是最新，文件里还没有新数据」自己否定自己。
- `.is-retry` → `.is-action`（现在涵盖「落后可补」与「失败重试」两种）。
- `/static` 与 `/` 加 `cache-control: no-cache`（无条件）。`no-cache` 不是
  "不缓存"：每次回服务端校验，没变仍是 304。

验收（证据见 docs/evidence/2026-10-11-浮标常驻验收.md）：
- 真浏览器四态文案逐字核对；落后态点一次发且只发一次 POST；
- 404 让位（`?code=000033&period=30`：notice 可见、浮标隐藏、只有一个按钮）；
- 布局零位移（四态下 #chart/.stage/#stamp/#stamp-detail/#ledger 的 rect
  与 body 网格行数逐项相同）；
- 8 条变异（M1–M8）各自证明对应断言会红，还原后逐字节相同。

已知空白：`blocked` 态（有数据、落后、补不了）**没有活样本** ——
`syncable=false` 只发生在「指数 + 分钟周期」，而本地有 30 分数据的 13 只票
全是 A 股股票。这一态只有静态断言保证，已记进 CHANGELOG 的
「已知空白与未实现项」。

全量测试：<pytest 实际输出，逐字>
版本影响：不涨版本号（pyproject.toml 未动），条目留在 [Unreleased]
chan/ 一行未动 ⇒ 不是判据变更
MSG
git show --stat HEAD | head -30
```

- [ ] **Step 4: 提交后复核**

```bash
cd /Users/zzz/workspace
git show --stat HEAD | tail -5
git status --short chanlun/
cd chanlun
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py >/dev/null; echo "守卫 exit=$?"
curl -s http://127.0.0.1:8888/api/version
sha256sum src/chanlun/web/static/app.js
curl -s "http://127.0.0.1:8888/static/app.js?cb=$RANDOM" | shasum -a 256
```
Expected: `git status --short chanlun/` 输出为空；守卫 `exit=0`；`/api/version` 仍是 `0.7.0`；两个 sha256 相同（磁盘 = 服务端加载的）。

---

## Self-Review

**1. Spec coverage（逐节对回 `2026-10-11-chip-persistent-state-design.md`）**

| 设计文档 | 落在哪个 Task |
|---|---|
| §4.1 状态机七态 + 两处行为差异（`skipped` 不算失败、`failed` 换前缀） | Task 2 Step 5 第四块 |
| §4.2 DOM：`#sync-chip-text` + 真 `<button>` | Task 1 Step 3 |
| §4.3 可点性与不挡图表（`.is-action`、子元素 `auto`） | Task 1 Step 4 |
| §4.4 唯一代码路径：自动与手动共用 `runAutoSync` | Task 2 Step 5 第二/四块 |
| §4.5 404 让位用显式 `quiet` 字段 | Task 2 Step 3 + Step 5 第四块 |
| §4.6 布局零位移 | Task 5 Step 5（P4） |
| §5 静态文件缓存 | Task 3 |
| §6 改动面（含不涨版本号、`chan/` 不动） | Task 6 Step 3 + Task 7 Step 3 |
| §7 验收 7 条 | 1→Task 5 Step 2；2→Task 5 Step 3；3→Task 5 Step 4；4→Task 5 Step 5；5→Task 3 Step 6；6→Task 4；7→Task 7 Step 1 |
| §8 已知残留（`blocked` 无活样本） | Task 5 Step 6 + Task 6 Step 4 |

**2. Placeholder scan**：全文没有 `TBD` / `TODO` / "加适当的错误处理" / "类似 Task N"。唯一两处需要现场取值的地方都给了**可重跑的命令**：Task 6 Step 0 的 `grep -n` 取真行号、Task 6 Step 5-6 的守卫输出取真数字。

**3. Type consistency**：`noteFreshness(body)` / `requestAutoSync(job, manual)` / `chipState(mode, job, msg, endTs)` / `dropChip()` / `chipIsAbout(job)` / `chipJob` / `job.quiet` / `job.local` / `job.expected` 在 Task 1（DOM 选择器）、Task 2（实现）、Task 4（变异）、Task 5（探针）里**逐字一致**；CSS 类名只有 `.sync-chip` / `.is-action` / `.sync-chip-go` 三个，Task 1 与 Task 2、Task 4 用的完全相同。测试里引用的 `_html/_css/_js/_fn_code/_strip_comments` 都是 `tests/web/test_frontend_auto_sync.py` 里**已有**的帮手（本计划不新增帮手）。

**4. 已知的两处风险，实施时留意**

- Task 2 Step 5 的 `chipState` 里，`text.textContent` 与 `btn.textContent` 必须严格分开；
  `_strip_comments` + `_fn_code` 会把注释剥掉，所以**注释里提到 `el.textContent` 不会咬红**，
  但**代码里出现一次就会红** —— 这正是想要的。
- Task 6 Step 6 的 13 元组重锚必须**按内容**做。基线键是
  `文件名:范围文本#文档内出现序号`，**不含文档行号**，所以范围文本相同的两条引用会互相撞号，
  出现"假红"（D-49 那次的 `app.js:233-233#0`）。撞号时先确认是假红再 `--update-baseline`，
  并把判断过程记进证据文档 §5。
