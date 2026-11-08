# 行业指数选择器改大弹窗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把左栏的 `<select id="industry">` 下拉框换成一个大弹窗（一级 / 二级切换 + 名称网格 + 跨级搜索），并用「有数据才亮」的明暗把本地覆盖度一眼标出来。

**Architecture:** 后端 `GET /api/industries` 每项多回一个布尔 `local`（来自本机 `meta.db` 的 `sync_state`），响应形状其余不变。前端用一个原生 `<dialog>` + `showModal()` 承载选择器（遮罩、Esc、焦点圈禁、背景 `inert` 都由浏览器给），左栏入口从 `<select>` 变成显示当前选中的 `<button>`；点卡片即关闭弹窗并走和顶栏输入框**同一条** `setCode` + `load` 路径。

**Tech Stack:** Python 3.14 / FastAPI / SQLite（`meta.db`）/ pytest；原生 JS（无框架）+ ECharts + 手写 CSS；headless Chrome + CDP 做真实浏览器验证。

## Global Constraints

- 设计稿：`chanlun/docs/superpowers/specs/2026-10-06-industry-picker-modal-design.md`（已提交 `41b380a`）。
- **不写死任何行业名**：31 + 124 个名字只活在 `src/chanlun/data/industry.py` 里，HTML / JS 一个字都不许有（D-44 硬约束）。同理不在 HTML / JS 里写死「31」「124」这两个计数 —— tab 的计数从响应里算。
  - **这条管的是行为，不是散文。** 代码里不许有**依赖**这两个数字的分支 / 常量 / 文案拼接；但**注释里引用一个实测过的数字当证据是允许的、而且 AGENTS.md 的证据质量规则要求把它记下来**。现存三处都属于后者，**Task 5 不许去"顺手扫掉"它们**：`index.html:77` 说清单内置在后端、顺带交代 31/124 是什么量级；`app.js:1516` 是这条规则本身的陈述（不引用数字就说不清这条规则）；`app.js:1537` 记的是 2026-10-06 真机实测到的那个破损（「点二级 tab 会看到 124 张卡配一级下划线」）—— 删了就把一次实测记录抹掉了。判断标准是「这行代码会不会因为数字变了而做错事」，不是「这行文字里有没有出现 124」。
  **这条包含 UI 文案里的示例词。** 本计划早期版本在 `placeholder` 里写了「半导体」、在空结果提示里写了「半导体」「银行」，那是**和这条约束直接冲突**的（三者都是 `industry.py` 里的真名，会被自己的护栏打红）。Task 2 先撞上了这一处。所以示例一律**只给代码**（`801120` 这类代码是稳定标识，不是名字，可以写进文案）。
  **被否决的做法**：给护栏开一个「文案区白名单」放行这些名字 —— 白名单会随着文案改动越加越多，最后把约束加死；那条护栏自己的 docstring 也写着这句话。
- 只用既有设计 token（`styles.css:12-30`）：`--ink --panel --panel-2 --line --line-soft --rice --rice-dim --mohui --cinnabar --bamboo --indigo --amber --mono --cjk --r`。**不引入新颜色**。
- 「待同步」用**降色 + 虚线边**表达，**不用 `opacity`**（`opacity` 会把 `:focus-visible` 的 `outline` 一起变淡）。
- 版本影响 **`MINOR` ⇒ `0.6.0` → `0.7.0`**，在本改动自己的提交里涨。
- 提交信息用中文 Conventional Commits（`feat(web):` / `fix(web):` / `docs:` / `test:` / `chore:`）。
- **绝不 `git add -A` / `git add chanlun/`**：每个路径单独列出。**绝不** `git checkout -- <file>`。
- 所有命令的 cwd 是 `/Users/zzz/workspace/chanlun`，Python 解释器是 `../.venv-chanlun/bin/python`，测试要 `PYTHONPATH=src`。
- 改了 `src/chanlun/**/*.py` **必须重启**看盘页后端（`web/app.py` 用的是 `uvicorn.run(...)`，没有 `--reload`）；只改 `web/static/**` 只要刷新浏览器。
- 行为变更必须同时更新 `ARCHITECTURE.md` 与 `CHANGELOG.md`（AGENTS.md §1）。
- UI 变更的验收必须**从真实浏览器的 DOM dump 里逐字读文本**，截图只用来判断布局。
- 断言要可证伪、可复现；数字一样就老实记 `inconclusive`，不许编。

---

### Task 1: 后端 `/api/industries` 每项多回一个 `local`

**Files:**
- Modify: `src/chanlun/web/api.py:634-656`（`industries` 路由）
- Test: `tests/web/test_api_search.py:280-299`（改一条既有断言 + 加两条）

**Interfaces:**
- Consumes: `chanlun.data.meta.all_sync(conn, period=None) -> list[sqlite3.Row]`（行里有 `code` / `period` / `rows` / `error`）、`chanlun.data.meta.init(path) -> sqlite3.Connection`、`chanlun.data.industry.all_boards(level) -> list[Board]`、`Board.as_dict() -> {"code","name","level"}`、`api.py` 既有的 `_cfg(request)` 帮助函数。
- Produces: `GET /api/industries` 的每个 item 变成 `{"code": str, "name": str, "level": str, "local": bool}`；`local is True` 当且仅当 `sync_state` 里有 `(code, "day")` 且 `rows > 0` 且 `error` 为空。顶层键 `count` / `level` / `levels` / `as_of` 全部不变。

- [ ] **Step 1: 改既有断言，让它先红**

`tests/web/test_api_search.py` 里 `test_industry_items_carry_what_the_picker_needs`（第 280-289 行）现在钉的是「只有三个键」。改成四个键：

```python
def test_industry_items_carry_what_the_picker_needs(client):
    """前端只认这四个键：`code` 当 value、`name` 当文案、`level` 分组、
    `local` 决定卡片亮还是暗。

    `market` 不在这里 —— `as_dict()` 只回三个键（`market` 是 `search_rows()`
    给搜索路径加的，两条路各带各的）。
    """
    for item in client.get("/api/industries?level=1").json()["items"]:
        assert set(item) == {"code", "name", "level", "local"}
        assert item["code"].startswith("sw.801")
        assert item["name"]
        assert isinstance(item["local"], bool)
```

- [ ] **Step 2: 跑它，确认它红**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_api_search.py::test_industry_items_carry_what_the_picker_needs -v`

Expected: FAIL — `assert {'code', 'level', 'name'} == {'code', 'level', 'local', 'name'}`，多出来的 `'local'` 说明后端还没回这个键。

- [ ] **Step 3: 加两条新测试（先红）**

追加到 `tests/web/test_api_search.py` 末尾。注意这两条要自己起 `TestClient`，因为它们要先往临时库里写 `sync_state`：

```python
def test_industry_items_say_whether_the_board_has_local_day_bars(cfg):
    """`local` 就是「本机 `sync_state` 里有这个板块的日线」。

    `conftest` 的临时库里一只 `sw.%` 都没有 ⇒ 155 项全 `False`；
    插一行 `sw.801120` 之后**只有它**翻成 `True`。
    """
    with TestClient(create_app(cfg)) as c:
        before = c.get("/api/industries").json()["items"]
    assert len(before) == 155
    assert not any(i["local"] for i in before), "临时库里不该有板块日线"

    conn = meta.init(cfg.data.meta_db)
    meta.set_sync(conn, "sw.801120", "day", "2026-09-01", "2026-09-30", 21, "3")
    conn.close()

    with TestClient(create_app(cfg)) as c:
        after = c.get("/api/industries").json()["items"]
    on = {i["code"] for i in after if i["local"]}
    assert on == {"sw.801120"}, "只有同步过的那一个板块该亮"


def test_a_failed_sync_does_not_count_as_local_data(cfg):
    """同步失败的行（`error` 非空 / `rows` 为 0）**不算**有数据。

    算成有数据的话，卡片会亮起来、点进去却是空图 —— 那比一直暗着更坏。
    """
    conn = meta.init(cfg.data.meta_db)
    meta.set_sync(conn, "sw.801120", "day", None, None, 0, "3", error="connect timeout")
    conn.close()

    with TestClient(create_app(cfg)) as c:
        items = c.get("/api/industries").json()["items"]
    assert not any(i["local"] for i in items), "失败的行不许把卡片点亮"
```

同一条测试里两次 `with TestClient(...)` 是必需的：`create_app(cfg)` 起停一次，确保第二次请求读到的是新写进去的 `sync_state`。若 `TestClient` 的启动开销让这里变慢，也不要把两次请求塞进同一个 `with` —— 那样会在**同一个连接**上读，测不到「重新打开数据库也读得到」。

- [ ] **Step 4: 跑它们，确认它们红**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_api_search.py -k industry -v`

Expected: `test_industry_items_carry_what_the_picker_needs` FAIL（缺 `local`）；`test_industry_items_say_whether_the_board_has_local_day_bars` FAIL（`assert not any(...)` 通过，但 `on == {"sw.801120"}` 拿到空集）；`test_a_failed_sync_does_not_count_as_local_data` PASS（因为现在压根没有 `local`，`any(...)` 恒假）。**第三条此时是假绿**，Step 6 会用改完的实现再确认它真的能拦住东西。

- [ ] **Step 5: 实现**

`src/chanlun/web/api.py` 的 `industries` 路由（第 634-656 行）整体替换成：

```python
@router.get("/api/industries")
def industries(request: Request, level: str = "") -> dict[str, Any]:
    """申万一级 / 二级行业指数清单（D-44）。

    **纯本地**：清单内置在 `industry.py`，`local` 来自本机 `meta.db` 的
    `sync_state` —— 两条路都不联网。

    `local` = 本机已经有这个板块的**日线**（`rows > 0` 且没有 `error`）。
    页面用它决定卡片是亮着还是标「待同步」；没有它就只能在点进去之后
    才发现是空图 —— 那次「悄悄留白」的教训（D-44 收尾）说的就是这件事。
    """
    lv = str(level or "").strip()
    boards = industry.all_boards(lv)
    cfg = _cfg(request)
    conn = meta.init(cfg.data.meta_db)
    try:
        # 同步失败的行也躺在 `sync_state` 里（`error` 非空、`rows` 可能为 0），
        # 把它当「有数据」会让卡片亮着却画出空图。
        local = {
            r["code"]
            for r in meta.all_sync(conn, "day")
            if not r["error"] and r["rows"] > 0
        }
    finally:
        conn.close()
    return {
        "count": len(boards),
        "level": lv,
        "levels": [{"level": k, "name": v} for k, v in industry.LEVEL_NAMES.items()],
        "as_of": industry.CHECKED_ON,
        "items": [{**b.as_dict(), "local": b.code in local} for b in boards],
    }
```

保留原有的 `return` 字典里**除 `items` 之外**的每个键与取值（`count` / `level` / `levels` / `as_of`），只把 `items` 换成上面那行。`meta` 与 `Request` 都已经 import 了（`api.py:30` / `api.py:40`），不用加 import。

- [ ] **Step 6: 跑测试，确认全绿**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_api_search.py -v`

Expected: PASS（含 `test_the_industry_route_does_not_touch_the_vendor` —— `meta.init` 走 sqlite3，不碰网络）。

- [ ] **Step 7: 反过来验一次「失败的行」那条测试真的能拦住东西**

临时把实现里的 `if not r["error"] and r["rows"] > 0` 改成 `if True`，跑：

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_api_search.py::test_a_failed_sync_does_not_count_as_local_data -v`

Expected: FAIL（`assert not any(...)` 被打破）。**然后改回来**，再跑一次确认 PASS。这一步是为了确认 Step 4 里那条假绿现在真的有了牙齿。

- [ ] **Step 8: 提交**

```bash
git add chanlun/src/chanlun/web/api.py chanlun/tests/web/test_api_search.py
git commit -m "feat(web): /api/industries 每项回一个 local，标出本地已有日线的板块"
```

---

### Task 2: HTML 骨架 —— 入口按钮 + `<dialog>` + 护栏放行 `dialog`

**Files:**
- Modify: `src/chanlun/web/static/index.html:76-87`（`.ind-pick` 块）、`:138` 之后插入 `<dialog>`
- Modify: `tests/web/test_frontend_watchlist.py:23`（`NO_BOX_TAGS`）
- Test: `tests/web/test_frontend_industry.py`（加两条）

**Interfaces:**
- Consumes: Task 1 的响应形状（`items[].local`）。
- Produces: 页面里出现 `#industry`（`<button>`，不是 `<select>`）、`#industry-name`、`#ind-label`、`#industry-modal`、`#ind-modal-title`、`#ind-close`、`#ind-search`、`#ind-tabs`、`#ind-grid`、`#ind-modal-foot`；`#industry-note` 原样保留。这些 id 是 Task 3 / 4 的唯一契约。

- [ ] **Step 1: 先让护栏放行 `dialog`，并写清理由**

`tests/web/test_frontend_watchlist.py` 第 23 行现在是：

```python
NO_BOX_TAGS = frozenset({"script", "style", "link", "template"})
```

改成：

```python
#: 不产生盒子、因此**不占** `body` 的 grid 轨道的标签。
#: `<dialog>` 在两种状态下都不在流内：未打开是 `display: none`；
#: `showModal()` 之后进 top layer 且 `position: fixed`。所以它**永远不是 grid item**
#: —— 行业指数选择器（`#industry-modal`）就是靠这一点直接挂在 `body` 上的。
NO_BOX_TAGS = frozenset({"script", "style", "link", "template", "dialog"})
```

- [ ] **Step 2: 跑护栏，确认现在还是绿的**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_watchlist.py -v`

Expected: PASS（`<dialog>` 还没加进 HTML，放行它只是提前把判据的覆盖面补齐）。同时确认 `test_body_grid_rows_cover_every_body_child` 里的 `len(tracks) == kids` 与 `"1fr" in tracks[-1]` 两条断言都在。

- [ ] **Step 3: 加两条前端契约测试（先红）**

追加到 `tests/web/test_frontend_industry.py` 末尾：

```python
def _html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _dialog_block() -> str:
    html = _html()
    start = html.index('<dialog id="industry-modal"')
    return html[start : html.index("</dialog>", start)]


def test_the_picker_is_a_dialog_not_a_select():
    """下拉框换成弹窗：155 项铺在一个网格里挑，而不是在 155 行的下拉里滚。"""
    html = _html()
    assert '<select id="industry"' not in html, "下拉框应当已经被弹窗取代"
    assert '<dialog id="industry-modal"' in html
    assert 'aria-labelledby="ind-modal-title"' in html, "弹窗要有可访问的名字"


def test_no_industry_name_is_hard_coded_in_the_frontend():
    """D-44 的硬约束：31 + 124 个名字只活在 `data/industry.py` 里。

    写死在 HTML / JS 里没人会去改，而后端 `industry --check` 才能把
    「这份清单过期了」变成一句话。

    只看 `app.js` 的代码 + 弹窗那一块 HTML，**不扫整个页面**：
    顶栏的 `placeholder="600000 / 浦发银行"` 里含「银行」，而「银行」正好是
    一个一级行业名 —— 全页扫描会稳定误报，那种测试只会被人加白名单加死。
    """
    from chanlun.data import industry

    blob = _code_only() + _dialog_block()
    # 防呆：这个测试的全部效力都押在「扫到的面够不够大」上。注释剥离或切片一旦
    # 悄悄退化成空串，下面的 `hits` 会恒为空 —— 那是最坏的情况：测试永远绿，
    # 而硬编码的名字就在里面。所以先证明扫描面真的覆盖到了该覆盖的地方。
    assert "setCode" in blob, "`_code_only()` 没扫到 app.js 的代码"
    assert 'id="ind-grid"' in blob, "`_dialog_block()` 没扫到弹窗里的网格容器"
    hits = sorted({b.name for b in industry.all_boards() if b.name in blob})
    assert not hits, f"前端写死了行业名：{hits[:5]}"


def test_the_modal_reads_the_local_flag_the_api_really_returns(client):
    """卡片亮还是暗靠 `item.local`；后端不返回它，155 张卡会全是暗的。"""
    items = client.get("/api/industries?level=1").json()["items"]
    assert all(isinstance(i["local"], bool) for i in items)
    body = _function_body("loadIndustries")
    assert "it.local" in body, "`local` 要从响应里读，不能在前端自己猜"
```

- [ ] **Step 4: 跑它们，确认它们红**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_industry.py -v`

Expected:
- `test_the_picker_is_a_dialog_not_a_select` FAIL —— `<select id="industry"` 还在。
- `test_no_industry_name_is_hard_coded_in_the_frontend` FAIL —— `_dialog_block()` 抛 `ValueError: substring not found`（弹窗还没有）。
- `test_the_modal_reads_the_local_flag_the_api_really_returns` FAIL —— `loadIndustries` 里没有 `it.local`。

- [ ] **Step 5: 换掉左栏入口**

`src/chanlun/web/static/index.html` 第 76-87 行（含那段 HTML 注释）整体替换成：

```html
    <!-- 行业指数：用户 2026-10-05 要求「A 股要有那个一级行业、二级行业的那个行业指数」。
         清单内置在后端 `data/industry.py`（一级 31 / 二级 124，`GET /api/industries`），
         所以这里**不写死任何行业名** —— 申万几年调一次分类，写死在 HTML 里没人会去改。
         2026-10-06 用户要求「可以弹出一个弹窗，就是一个比较大的弹窗，在里面进行选」，
         于是入口从 `<select>` 换成一个按钮，点它开 `#industry-modal`（见 body 末尾）。
         放在自选栏而不是顶栏：顶栏那一条是 flex 不换行的，再加一格会把整行挤爆
         （`.stamp` 的 min-width 380px 就是这么定的）。 -->
    <div class="ind-pick">
      <span class="ind-label" id="ind-label">行业指数 · 申万</span>
      <button type="button" class="ind-open" id="industry"
              aria-haspopup="dialog" aria-labelledby="ind-label industry-name"
              title="打开行业指数选择器">
        <span class="ind-open-name" id="industry-name">选一个行业…</span>
        <span class="ind-open-caret" aria-hidden="true">▾</span>
      </button>
      <p class="ind-note" id="industry-note" hidden></p>
    </div>
```

`id="industry"` **保留在入口上**（只是从 `<select>` 变成 `<button>`）：`$("#industry")` 与既有的前端护栏测试都不用改名。`<label for>` 换成 `<span>` + `aria-labelledby`，因为标签要同时指到「行业指数 · 申万」和当前值两个元素。

- [ ] **Step 6: 在 `body` 末尾插入弹窗**

`index.html` 里 `.work` 的收尾 `</div>`（原第 138 行）之后、`<script src="/static/vendor/echarts.min.js">` 之前，插入：

```html
  <!-- 行业指数选择器。用原生 <dialog> + showModal()：遮罩、Esc 关闭、焦点圈禁、
       背景 inert 全部由浏览器给 —— 手写这四件事是无障碍 bug 的高发区。
       **它不占 body 的 grid 轨道**：未打开是 display:none，showModal() 后进 top layer
       且 position:fixed，两种状态都不在流内（`test_frontend_watchlist.py` 的
       `NO_BOX_TAGS` 因此放行 dialog，理由写在那个常量的注释里）。
       tab 与卡片全部由 app.js 从 `/api/industries` 的响应里铺，这里一个行业名都没有。 -->
  <dialog id="industry-modal" aria-labelledby="ind-modal-title">
    <div class="ind-modal-inner">
      <header class="ind-modal-head">
        <div class="ind-modal-title">
          <h2 id="ind-modal-title">选行业指数</h2>
          <p class="ind-modal-sub">申万行业分类 · 一级 / 二级</p>
        </div>
        <button type="button" class="ind-close" id="ind-close" aria-label="关闭">✕</button>
      </header>
      <div class="ind-modal-controls">
        <input type="search" id="ind-search" class="ind-search"
               placeholder="搜行业名或代码，例如「801120」"
               autocomplete="off" spellcheck="false" aria-label="搜索行业">
        <div class="ind-tabs" id="ind-tabs" role="tablist" aria-label="行业级别"></div>
      </div>
      <div class="ind-grid" id="ind-grid"></div>
      <p class="ind-modal-foot" id="ind-modal-foot"></p>
    </div>
  </dialog>
```

副标题**不写**「一级 31 / 二级 124」——那两个数字是数据，tab 的计数会从响应里算出来，写死在 HTML 里就是第二份真相。

- [ ] **Step 7: 跑测试，确认该绿的绿了**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_industry.py tests/web/test_frontend_watchlist.py -v`

Expected:
- `test_the_picker_is_a_dialog_not_a_select` PASS。
- `test_no_industry_name_is_hard_coded_in_the_frontend` PASS。
- `test_the_modal_reads_the_local_flag_the_api_really_returns` **仍然 FAIL**（`it.local` 是 Task 3 的事）—— 这是预期的，别在这里改实现去凑绿。
- `test_frontend_watchlist.py` 全 PASS，特别是 `test_body_grid_rows_cover_every_body_child`（`<dialog>` 没占轨道）。

- [ ] **Step 8: 提交**

```bash
git add chanlun/src/chanlun/web/static/index.html chanlun/tests/web/test_frontend_watchlist.py chanlun/tests/web/test_frontend_industry.py
git commit -m "feat(web): 行业选择器换成 <dialog> 骨架，护栏放行 dialog（D-44）"
```

---

### Task 3: 渲染 —— tab、网格、卡片、「有数据才亮」的明暗

**Files:**
- Modify: `src/chanlun/web/static/app.js:1440-1503`（整个「行业指数」小节）
- Modify: `src/chanlun/web/static/styles.css:651-661`（删掉 `.ind-pick select` 两条，换成新的入口 + 弹窗样式）
- Modify: `src/chanlun/web/static/index.html`（**只改一个字符串**：`#ind-search` 的 `placeholder` 从 `例如「801081」` 改成 `例如「801120」`，理由见下）
- Test: `tests/web/test_frontend_industry.py`（Task 2 的第三条应当在这里转绿）

> **为什么顺手改这一个字符串**：Task 2 撞上了「文案里的示例词」与「不许写死行业名」的冲突，它把示例码换成了 `801081`，理由是「`801120` 不存在」—— **那个理由是错的**，`sw.801120 食品饮料`（一级）确实存在，而且是 155 个板块里**唯一有本地日线数据**的那个。所以 `801120` 不只是合法示例，还是唯一「输进去就能看到亮卡片 + 真图」的示例。Task 2 已经提交（`594b4aa`），这个字符串由本任务顺手改对。**除这一个字符串外不要动 `index.html`。**

> **本任务还要修掉一个 HEAD 上的可见破损。** Task 2 把 `#industry` 从 `<select>` 换成了 `<button>`，但 `loadIndustries`（`app.js:1440-1503`，正是本任务要整个换掉的那一段）还在 `sel.append(optgroup)`，于是那个按钮里现在塞着 2 个 `<optgroup>` + 155 个 `<option>`，渲染成 630 字的行业名文本块 —— Task 2 的审查者在真实浏览器的 DOM dump 里量到的。这是分任务切出来的中间态，**不是** Task 2 没照任务书做（它的任务书就是「换骨架」，`app.js` 归本任务）；修法就是本任务对 `loadIndustries` 的整体重写。
>
> **所以本任务的浏览器验收必须证明：左栏那个按钮里只剩「选一个行业…」+ 那个 ▾ 尖角，`#industry` 底下**一个 `<option>` 都没有**。** 这一条比「卡片渲染出来了」更要紧 —— 它证明那个破损真的被修掉了，而不是被新样式盖住了。

**Interfaces:**
- Consumes: Task 1 的响应、Task 2 的 DOM id。
- Produces: 模块级 `let industryRows`（`[{code, name, level, local}]`）、`let industryLevel`（`"1"` / `"2"`）、`function buildIndustryTabs(levels)`、`function syncIndustryTabs()`、`function industryHits(q) -> list | null`、`function renderIndustryGrid()`、`function industryCard(row) -> HTMLButtonElement`（`dataset.code` / `dataset.local`）、`function syncIndustryButton()`。Task 4 依赖 `industryRows` / `renderIndustryGrid` / `industryCard` 的 `dataset.local` / `syncIndustryButton`。

> **本任务结束时页面必须能跑。** 所以 `syncIndustryButton` 定义在**这里**（它属于渲染：按钮上写什么文案），Task 4 只负责把它挂进 `setCode`。`industryCard` 这时**还不绑** click —— 点卡片出图是 Task 4。

- [ ] **Step 1: 跑一次，确认那条测试还是红的**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_industry.py::test_the_modal_reads_the_local_flag_the_api_really_returns -v`

Expected: FAIL —— `loadIndustries` 里还没有 `it.local`。

- [ ] **Step 2: 用新的行业指数小节替换 app.js 的 1440-1503 行**

`src/chanlun/web/static/app.js` 第 1440 行（`// ---- 行业指数` 那条分隔注释）到第 1503 行（`loadIndustries` 的收尾 `}`）整体替换成：

```js
  // ------------------------------------------------------------------ 行业指数
  // 用户 2026-10-05 要求「A 股要有那个一级行业、二级行业的那个行业指数」，
  // 2026-10-06 要求「可以弹出一个弹窗，就是一个比较大的弹窗，在里面进行选」。
  // 清单在后端 `data/industry.py`（内置，不落库、不联网；`GET /api/industries`），
  // 这里只把它铺成弹窗里的网格。**不写死任何行业名**：申万几年调一次分类，
  // 写死在 JS 里没人会去改；后端 `--check` 才能把「清单过期了」变成一句话。
  //
  // 选中的行业走和顶栏输入框**同一条路**（`setCode` + `load`）—— 板块不是一条
  // 特殊分支，所以加自选按钮、复权口径、周期按钮、书签全都照常工作。

  //: `/api/industries` 的 items，原样存着（155 条，不值得再建一份索引）。
  let industryRows = [];
  //: 弹窗当前显示哪一级（"1" / "2"）。搜索非空时忽略它。
  let industryLevel = "1";

  async function loadIndustries() {
    const btn = $("#industry");
    if (!btn) return;
    const note = $("#industry-note");
    // 清单取不到**不能**只是静默留白：那看起来就像「这个功能不存在」。
    // 说清楚是网络断了还是后端没重启，才知道下一步做什么。
    const fail = (text) => {
      if (note) { note.textContent = text; note.hidden = false; }
      btn.disabled = true;
    };
    let res;
    try {
      res = await fetch("/api/industries");
    } catch (err) {
      fail("行业清单取不到：网络请求失败。检查看盘页后端是否在跑，然后刷新。");
      return;
    }
    // `fetch` **只在网络层失败时 reject**，404/500 都是正常 resolve。
    // 旧进程上没有 `/api/industries`，拿 `{"detail":"Not Found"}` 去读 `items`
    // 会得到「一张卡片都没有」的空弹窗 —— 这条曾经真的发生过（D-44 收尾）。
    if (!res.ok) {
      fail(`行业清单取不到：HTTP ${res.status}。看盘页后端可能没重启（改过 src 必须重启）。`);
      return;
    }
    let body;
    try {
      body = await res.json();
    } catch (err) {
      fail("行业清单取不到：返回的不是合法 JSON。");
      return;
    }
    industryRows = (body.items || []).map((it) => ({
      code: String(it.code || ""),
      name: String(it.name || ""),
      level: String(it.level || ""),
      // 本机有没有这个板块的日线，后端算好给的。前端**不猜** ——
      // 猜错就是「卡片亮着，点进去是空图」，那比一直暗着更坏。
      local: it.local === true,
    }));
    btn.disabled = false;
    buildIndustryTabs(body.levels || []);
    renderIndustryGrid();
    syncIndustryButton();
    // 核对日期必须露出来：悄悄过期比过期本身更坏（D-44）。
    // 键名是 `as_of`（`web/api.py` 的返回契约），**不是** `checked_on` ——
    // 曾经这里读错键，于是这行提示从来没显示过，而没有任何测试拦得住。
    if (note && body.as_of) {
      note.textContent = `清单核对于 ${body.as_of}；板块只有日线，周 / 月由日线聚合。`;
      note.hidden = false;
    }
    const foot = $("#ind-modal-foot");
    if (foot) {
      const have = industryRows.filter((r) => r.local).length;
      foot.textContent =
        `本地已有 ${have} / ${industryRows.length} 个板块的日线。` +
        "标「本地」的选中就能看；其余选中后是空图，要先同步它的日线" +
        "（python -m chanlun sync --period day --codes 代码）。";
    }
  }

  // tab 条：名字与计数**全部来自后端**（`body.levels` 给名字，items 给计数）。
  // 不写死「一级 31 / 二级 124」—— 申万调分类时那两个数字会变。
  function buildIndustryTabs(levels) {
    const box = $("#ind-tabs");
    if (!box) return;
    const keys = levels.map((lv) => String(lv.level || ""));
    if (!keys.includes(industryLevel)) industryLevel = keys[0] || "1";
    box.textContent = "";
    for (const lv of levels) {
      const key = String(lv.level || "");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ind-tab";
      b.dataset.level = key;
      b.setAttribute("role", "tab");
      b.textContent = `${lv.name}（${industryRows.filter((r) => r.level === key).length}）`;
      b.addEventListener("click", () => {
        industryLevel = key;
        const q = $("#ind-search");
        if (q) q.value = "";
        renderIndustryGrid();
        syncIndustryTabs();
      });
      box.append(b);
    }
    syncIndustryTabs();
  }

  function syncIndustryTabs() {
    for (const b of document.querySelectorAll(".ind-tab")) {
      const on = b.dataset.level === industryLevel;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
  }

  //: 搜索词非空时**跨两级**过滤（名称或代码包含，忽略大小写）。
  //: 打「银行」时用户想的是那个行业，不是「一级行业里的那个银行」——
  //: 只在当前级别里搜，停在二级 tab 上会得到空结果。
  function industryHits(q) {
    const text = String(q || "").trim().toLowerCase();
    if (!text) return null;
    return industryRows.filter(
      (r) => r.name.toLowerCase().includes(text) || r.code.toLowerCase().includes(text)
    );
  }

  function industryGroupTitle(level, n) {
    const p = document.createElement("p");
    p.className = "ind-group";
    p.textContent = `${level === "1" ? "一级行业" : "二级行业"}（${n}）`;
    return p;
  }

  function industryEmpty(text) {
    const p = document.createElement("p");
    p.className = "ind-empty";
    p.textContent = text;
    return p;
  }

  function industryCard(row) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ind-card";
    b.dataset.code = row.code;
    b.dataset.local = row.local ? "1" : "0";
    // roving tabindex：整张网格只占一个 Tab 位，↑↓←→ 在格内走。
    // 155 个按钮各占一个 Tab 位的话，键盘用户根本走不出去。
    b.tabIndex = -1;
    if (row.code === state.code) b.classList.add("is-current");

    const name = document.createElement("span");
    name.className = "ind-card-name";
    name.textContent = row.name;

    const meta = document.createElement("span");
    meta.className = "ind-card-meta";
    const code = document.createElement("span");
    code.className = "ind-card-code";
    code.textContent = row.code;
    const flag = document.createElement("span");
    flag.className = "ind-card-flag";
    // 明暗**同时**有文字兜底：只靠颜色区分的话，色觉障碍的人读不出来。
    flag.textContent = row.local ? "本地" : "待同步";
    meta.append(code, flag);

    b.append(name, meta);
    return b;
  }

  function renderIndustryGrid() {
    const grid = $("#ind-grid");
    if (!grid) return;
    const box = $("#ind-search");
    const hits = industryHits(box ? box.value : "");
    const tabs = $("#ind-tabs");
    if (tabs) tabs.hidden = hits !== null;

    grid.textContent = "";
    if (hits === null) {
      const rows = industryRows.filter((r) => r.level === industryLevel);
      for (const r of rows) grid.append(industryCard(r));
      if (!rows.length) grid.append(industryEmpty("这个级别暂时没有行业。"));
    } else {
      // 跨两级搜索：结果按级别分组，用**跨列的组标题**标出级别，
      // 而不是给每张卡片加小标 —— 155 个级别小标只会变成噪点。
      for (const lv of ["1", "2"]) {
        const rows = hits.filter((r) => r.level === lv);
        if (!rows.length) continue;
        grid.append(industryGroupTitle(lv, rows.length));
        for (const r of rows) grid.append(industryCard(r));
      }
      if (!hits.length) {
        grid.append(
          industryEmpty("没有匹配的行业。换个词试试，或者直接输代码 801120。")
        );
      }
    }
    const cards = grid.querySelectorAll(".ind-card");
    if (cards.length) {
      const cur = [...cards].find((c) => c.classList.contains("is-current"));
      (cur || cards[0]).tabIndex = 0;
    }
  }

  // 入口按钮显示**当前**看的是哪个行业。真正的「换票时同步」在 Task 4 挂进 `setCode`，
  // 这里先在清单到货后刷一次。
  function syncIndustryButton() {
    const name = $("#industry-name");
    const btn = $("#industry");
    if (!name || !btn) return;
    const hit = industryRows.find((r) => r.code === state.code);
    name.textContent = hit ? hit.name : "选一个行业…";
    btn.classList.toggle("has-pick", Boolean(hit));
  }
```

`industryCard` **不**在这里绑 click —— Task 4 加 `pickIndustry` 之后再绑。

- [ ] **Step 3: 跑那条测试，确认它绿了**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_industry.py -v`

Expected: 四条全 PASS（`it.local` 现在在 `loadIndustries` 里了；`checked_on` 仍然只出现在整行 `//` 注释里，被 `_code_only()` 剥掉）。

- [ ] **Step 4: 换掉 `.ind-pick select` 的样式**

`src/chanlun/web/static/styles.css` 第 651-661 行这两条：

```css
.ind-pick select {
  width: 100%;
  padding: 6px 8px;
  background: var(--ink);
  border: 1px solid var(--line);
  border-radius: var(--r);
  color: var(--rice-dim);
  font-family: inherit;
  font-size: 12px;
}
.ind-pick select:hover { border-color: var(--indigo); }
```

替换成：

```css
/* 入口是按钮而不是 <select>：点它开一个大弹窗，155 个行业在里面挑。
   长得像下拉框却不展开会一直让人困惑，所以外形也换成「按钮 + 当前值 + 角标」。 */
.ind-open {
  display: flex;
  align-items: baseline;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  background: var(--ink);
  border: 1px solid var(--line);
  border-radius: var(--r);
  color: var(--rice-dim);
  font-family: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.ind-open:hover { border-color: var(--indigo); }
.ind-open-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ind-open.has-pick .ind-open-name { color: var(--rice); }
.ind-open-caret { flex: none; color: var(--mohui); }
```

`.ind-label` 与 `.ind-note` 两条**原样不动**。

- [ ] **Step 5: 追加弹窗与卡片的样式**

在 `.ind-note` 那条规则之后（`styles.css` 第 667 行附近）、`.watch-foot` 之前，插入：

```css
/* ---------------- 行业指数选择器（弹窗） ----------------
   原生 <dialog> + showModal()：遮罩、Esc、焦点圈禁、背景 inert 都由浏览器给。
   `padding: 0` 是**有意的** —— 内边距全部交给 .ind-modal-inner，这样落在弹窗
   盒子里的点击一定命中某个后代，`e.target === dialog` 就等价于「点了遮罩」，
   点遮罩关闭因此不会误伤弹窗自己的边缘留白。 */
#industry-modal {
  width: min(1080px, 92vw);
  max-width: 92vw;
  height: min(760px, 88vh);
  max-height: 88vh;
  padding: 0;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--r);
  color: var(--rice);
  font-family: var(--cjk);
  overflow: hidden;
}
#industry-modal::backdrop { background: color-mix(in srgb, var(--ink) 74%, transparent); }
.ind-modal-inner { display: flex; flex-direction: column; height: 100%; }
.ind-modal-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 18px 12px;
  border-bottom: 1px solid var(--line-soft);
}
.ind-modal-title { flex: 1; min-width: 0; }
.ind-modal-title h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 400;
  letter-spacing: 0.1em;
}
.ind-modal-sub { margin: 4px 0 0; font-size: 11px; color: var(--mohui); }
.ind-close {
  flex: none;
  width: 26px;
  height: 26px;
  padding: 0;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--r);
  color: var(--rice-dim);
  cursor: pointer;
}
.ind-close:hover { color: var(--rice); border-color: var(--indigo); }
.ind-modal-controls { display: flex; align-items: center; gap: 10px; padding: 12px 18px; }
.ind-search {
  flex: 1;
  min-width: 0;
  padding: 8px 10px;
  background: var(--ink);
  border: 1px solid var(--line);
  border-radius: var(--r);
  color: var(--rice);
  font-family: inherit;
  font-size: 13px;
}
.ind-search::placeholder { color: var(--mohui); }
/* tab 条复用顶栏 .periods 的语法：选中态 = panel-2 底 + 靛青下划线。 */
.ind-tabs {
  display: flex;
  flex: none;
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
}
.ind-tab {
  flex: none;
  white-space: nowrap;
  padding: 7px 14px;
  background: var(--ink);
  border: 0;
  border-right: 1px solid var(--line);
  color: var(--rice-dim);
  font-family: inherit;
  font-size: 12px;
  letter-spacing: 0.06em;
  cursor: pointer;
}
.ind-tab:last-child { border-right: 0; }
.ind-tab.is-on {
  background: var(--panel-2);
  color: var(--rice);
  box-shadow: inset 0 -2px 0 var(--indigo);
}
.ind-tab:hover { color: var(--rice); }
.ind-grid {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  gap: 8px;
  align-content: start;
  padding: 4px 18px 18px;
}
.ind-group {
  grid-column: 1 / -1;
  margin: 10px 0 0;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--line-soft);
  font-size: 11px;
  color: var(--mohui);
  letter-spacing: 0.16em;
}
.ind-empty {
  grid-column: 1 / -1;
  margin: 24px 0;
  font-size: 13px;
  line-height: 1.8;
  color: var(--rice-dim);
}
/* 卡片：**有数据才亮**。待同步的降色 + 虚线边（沿用 .chip.capped 的语法：
   虚线 = 还没完全到位）。**不用 opacity** —— 它会把 :focus-visible 的
   outline 一起变淡，键盘用户就看不见焦点在哪了。 */
.ind-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 9px 10px;
  background: var(--panel-2);
  border: 1px solid var(--line-soft);
  border-radius: var(--r);
  color: var(--rice);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color .12s;
}
.ind-card:hover { border-color: var(--indigo); }
.ind-card[data-local="0"] {
  background: var(--panel);
  border-style: dotted;
  color: var(--mohui);
}
.ind-card-name { font-size: 13px; line-height: 1.35; overflow-wrap: anywhere; }
.ind-card-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}
.ind-card-code { font-family: var(--mono); font-size: 10px; color: var(--mohui); }
.ind-card-flag { flex: none; font-size: 10px; letter-spacing: 0.08em; color: var(--bamboo); }
.ind-card[data-local="0"] .ind-card-flag { color: var(--mohui); }
/* 当前看的那一个。放在 [data-local="0"] 之后：同特异性下后写的赢，
   所以即使当前选中的是个待同步的板块，边框也是实线。 */
.ind-card.is-current { border-color: var(--indigo); border-style: solid; }
.ind-card.is-current .ind-card-name::after {
  content: "当前";
  margin-left: 6px;
  font-size: 10px;
  color: var(--indigo);
}
.ind-modal-foot {
  margin: 0;
  padding: 10px 18px 12px;
  border-top: 1px solid var(--line-soft);
  font-size: 10px;
  line-height: 1.7;
  color: var(--mohui);
}
@media (prefers-reduced-motion: reduce) {
  .ind-card { transition: none; }
}
```

- [ ] **Step 6: 真实浏览器里看一眼：弹窗能开吗、卡片是 31 / 124 吗、明暗对吗**

后端没动（Task 1 动的是 `api.py`，属于 `src/chanlun/**/*.py`），所以**先重启**看盘页：

Run: `pkill -f "chanlun serve" || true; sleep 1; cd /Users/zzz/workspace/chanlun && PYTHONPATH=src PYTHONUNBUFFERED=1 nohup ../.venv-chanlun/bin/python -m chanlun serve > /tmp/serve.log 2>&1 &`

（命令照 `AGENTS.md` §4。关键是 `src/chanlun/**/*.py` 改过就必须重启，`uvicorn.run` 没有 `--reload`；只改 `web/static/**` 只要刷新浏览器。）

然后起 headless Chrome（`--remote-debugging-port=9222`）并跑一个 CDP 探针（照 `chanlun/docs/evidence/2026-10-04-capped-marker-probe.mjs` 的写法：node 内置 `WebSocket`、`Network.setCacheDisabled`、URL 带 `?_=` 破缓存），逐字读 DOM：

```js
const dump = await evaluate(`(() => {
  const d = document.querySelector('#industry-modal');
  const q = (s) => document.querySelector(s);
  const cards = [...document.querySelectorAll('#ind-grid .ind-card')];
  return {
    hasModal: !!d,
    selectGone: !document.querySelector('select#industry'),
    tabs: [...document.querySelectorAll('.ind-tab')].map(b => b.textContent),
    cards: cards.length,
    lit: cards.filter(c => c.dataset.local === '1').length,
    firstCard: cards[0] ? cards[0].textContent : null,
    foot: q('#ind-modal-foot') ? q('#ind-modal-foot').textContent : null,
  };
})()`);
```

Expected（2026-10-06 的实测值）：
- `hasModal: true`，`selectGone: true`
- `tabs: ["一级行业（31）", "二级行业（124）"]` —— **计数来自响应**，不是写死的
- 默认停在 `一级` ⇒ `cards: 31`，`lit: 1`（只有 `sw.801120` 食品饮料有日线）
- `foot` 里含 `本地已有 1 / 155 个板块的日线`

点一下 `二级行业（124）` 的 tab，再 dump：`cards: 124`、`lit: 0`（`sw.801120` 属**一级**，二级这 124 张全是「待同步」—— 早先这里写 `lit: 1` 是笔误，Task 3 实测 0 并如实记下，没有改代码去凑那个 1）。同时 tab 高亮必须跟着走：`一级行业（31）` 的 `aria-selected` 要变回 `"false"` —— 这正是上面 `syncIndustryTabs()` 那一行在管的事。

**任何一条对不上就先修，别往下走。** 把 dump 的原文贴进提交信息或 PR 说明。

- [ ] **Step 7: 提交**

```bash
git add chanlun/src/chanlun/web/static/app.js chanlun/src/chanlun/web/static/styles.css chanlun/src/chanlun/web/static/index.html
git commit -m "feat(web): 行业弹窗铺出 tab 与卡片，按 local 分「本地 / 待同步」明暗（D-44）"
```

---

### Task 4: 交互 —— 打开 / 关闭、跨级搜索、键盘导航、点卡片出图

**Files:**
- Modify: `src/chanlun/web/static/app.js`（`industryCard` 加 click；新增 `setPeriodUI` / `openIndustryModal` / `pickIndustry` / `industryGridColumns` / `moveIndustryFocus` / `wireIndustryModal`；`setCode` 里加一行 `syncIndustryButton()`；周期按钮处理器与 `?period=` 两处换用 `setPeriodUI`；第 1642-1661 行的 `change` 处理器删掉；第 1732 行的 `loadIndustries()` 之前加 `wireIndustryModal()`）
- Modify: `src/chanlun/web/static/styles.css`（**只改一行**，控制方指定：`#industry-modal::backdrop` 的 `rgba(9, 11, 14, .74)` 换成 `color-mix(in srgb, var(--ink) 74%, transparent)`。理由见下）
- Test: `tests/web/test_frontend_industry.py`（先修 `_function_body` 的收尾，再加两条：入口不是 select、`setCode` 会同步按钮文案）

> **为什么顺手改这一行 CSS**：`rgba(9, 11, 14, .74)` 是**整个 `styles.css` 里唯一一处 `rgba(` 写法** —— 实测 `grep -c 'rgba('` = 1，就是它（改完为 0）。它是计划**违反了自己写的** Global Constraint「只用既有设计 token……**不引入新颜色**」：`rgb(9,11,14)` 这个近黑色在全文件里**别处不存在**，是这一行新造出来的颜色。`color-mix(in srgb, var(--ink) 74%, transparent)` 从既有 token 推导，不新增颜色，也不碰被禁的 `opacity`（那条禁令的理由是卡片上的 `:focus-visible` 描边会被一起变淡，而 `::backdrop` 里没有可聚焦内容 —— 但既然有更干净的写法，就不去蹭这个例外）。Task 3 的审查者标了这条，控制方裁定**要修**：约束被计划自己违反一次就没人拿它当回事了。**除这一行外不要动 `styles.css`。**
>
> **控制方更正（2026-10-06，Task 5 实现者发现）**：这条理由**最初写过头了**。我原先写的是「唯一一个裸色值字面量」，依据是 `grep -c 'rgba('` = 1 —— 但那个 grep 只证明它是唯一一处 **`rgba(`**，**不证明它是唯一一处裸色值**。实测 `styles.css` 里另有 **13 处裸 hex**（`:152 #232c37`、`:194 #14181d`、`:447-448 #4a3c22 / #2c3a4a`、`:593 #4a3c22`、`:945-947 #fff / #2f6fb5 / #2a5f9e`、`:951 #3a7dc4`、`:954-955 #6b7d8f / #5f707f`、`:959 #4a5a6a`、`:962 #b23b2e`），所以「它不是既有惯例」这句是**假的** —— 内联字面量在这个文件里是一种（少数的）既有写法。**裁定不变**，但站的理由要换成那条窄的、真的：它新造了一个别处不存在的颜色，而约束的字面要求是走既有 token。这条更正本身也留在这里 —— **一个靠错的测量站住的裁定，比没有裁定更坏**。

**Interfaces:**
- Consumes: Task 3 的 `industryRows` / `industryLevel` / `renderIndustryGrid` / `industryCard` / `syncIndustryButton`。
- Produces: `function setPeriodUI(period)`（把 `.period` 那排按钮的选中态对齐到某个周期）、`function pickIndustry(code)`（卡片与任何未来的入口都调它）、`function wireIndustryModal()`（在启动时调一次）、以及 `setCode` 里的那一行 `syncIndustryButton()` 调用。

- [ ] **Step 1: 先修 `_function_body` 的收尾，再加两条契约测试（先红）**

`_function_body` 现在的收尾条件是「下一个顶层 `function`」，对 `setCode` 不成立 —— `setCode` 后面到下一个顶层 `function` 之间还隔着**整个启动尾部**。实测 `_function_body("setCode")` 返回 **4997** 字符（含 `loadIndustries();` 那一行），也就是说下面那条 `assert "syncIndustryButton()" in body` 就算把调用写在启动尾部也会通过，**它没有在验证它声称的事**。

改成按缩进收尾（IIFE 里的顶层函数都收在 2 空格缩进的 `}`），并保留「下一个顶层 function」作为兜底：

```python
def _function_body(name: str) -> str:
    """抽出一个 IIFE 顶层函数的函数体。

    收尾取 `\\n  }`（2 空格缩进的右花括号）—— IIFE 里函数体内的东西至少缩进
    4 格，所以 2 格的 `}` 只可能是这个函数自己的收尾。**不能**只用「下一个顶层
    `function`」收尾：`setCode` 之后隔着整个启动尾部才有下一个函数，
    那样抽出来的 4997 字符里混着启动代码，断言会假绿。
    """
    code = _code_only()
    m = re.search(r"(?:async\s+)?function\s+" + name + r"\s*\(", code)
    assert m, f"app.js 里没有 {name}()"
    rest = code[m.start():]
    end = re.search(r"\n  \}", rest)
    nxt = re.search(r"\n  (?:async\s+)?function\s+\w+\s*\(", rest)
    stops = [x.start() for x in (end, nxt) if x]
    return rest[: min(stops)] if stops else rest
```

改完立刻验证它没弄坏已有的两条断言（`loadIndustries` 的抽法变了，但 `body.as_of` 仍应命中）：

```bash
PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_industry.py -v
```

Expected: 现有两条 PASS。顺手确认牙口：`_function_body("setCode")` 应当从 4997 掉到 ~93 字符，且**不再**含 `loadIndustries();`。

然后追加到 `tests/web/test_frontend_industry.py` 末尾：

```python
def test_the_rail_entry_is_a_button_that_opens_the_modal():
    """入口从下拉框变成按钮；点卡片即关闭弹窗并出图。"""
    html = _html()
    assert 'id="industry"' in html
    assert 'aria-haspopup="dialog"' in html, "要告诉辅助技术它会开一个弹窗"
    assert 'id="industry-name"' in html, "按钮要能显示当前选中的行业名"
    assert "showModal" in _code_only(), "用原生模态，别自己手写遮罩与焦点圈禁"
    assert "pickIndustry" in _code_only()


def test_changing_the_current_code_keeps_the_entry_button_honest():
    """顶栏输入一只股票之后，按钮不能还留着上一个行业名 —— 那是在说反话。

    所以 `syncIndustryButton()` 必须挂在 `setCode` 上（改当前代码的唯一入口）。
    """
    body = _function_body("setCode")
    assert "syncIndustryButton()" in body
    assert "$(\"#code\").value = code" in body, "前提：这里真的是唯一的入口"
```

- [ ] **Step 2: 跑它们，确认它们红**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_industry.py -v`

Expected: 两条新测试 FAIL —— `showModal` / `pickIndustry` 还不存在（`syncIndustryButton` 在 Task 3 已经有了，这一条红是因为 `setCode` 里还没调它）。

- [ ] **Step 3: 给卡片绑 click，并加交互函数**

在 `app.js` 的 `industryCard` 里，`b.append(name, meta);` 之后加：

```js
    b.addEventListener("click", () => pickIndustry(row.code));
```

然后在 `renderIndustryGrid` 的收尾 `}` 之后追加（`syncIndustryButton` 在 Task 3 已经定义好，**不要**再定义一遍）：

```js
  function openIndustryModal() {
    const dlg = $("#industry-modal");
    if (!dlg || typeof dlg.showModal !== "function") return;
    const box = $("#ind-search");
    // 每次打开都从「浏览」开始：上次搜的词还留着，会让人以为清单变短了。
    if (box) box.value = "";
    renderIndustryGrid();
    if (!dlg.open) dlg.showModal();
    if (box) box.focus();
  }

  function pickIndustry(code) {
    const dlg = $("#industry-modal");
    if (dlg && dlg.open) dlg.close();
    // 板块只有日线（D-44）：停在 30 分 / 5 分上选行业会直接撞到「这个周期补不了」。
    // 实测腾讯 `mkline` 对 `pt01…` 回 `param error`，对照 `sh600000,m30` 正常；
    // 日 / 周 / 月都照旧。
    if (!["day", "week", "month"].includes(state.period)) {
      state.period = "day";
      setPeriodUI("day");
    }
    setCode(code);
    load();
    loadWatch();
  }

  //: 把 `.period` 那排按钮的选中态对齐到某个周期。三处都要做同一件事
  //: （周期按钮自己的 click、`?period=` 的首屏对齐、选行业时强制回日线），
  //: 抽出来免得三份各写各的 —— 少改一处就是「按钮亮着但图是别的周期」。
  function setPeriodUI(period) {
    for (const b of document.querySelectorAll(".period")) {
      const on = b.dataset.period === period;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
  }

  function industryGridColumns(grid) {
    const cards = grid.querySelectorAll(".ind-card");
    if (!cards.length) return 1;
    const top = cards[0].offsetTop;
    let n = 0;
    for (const c of cards) {
      if (c.offsetTop !== top) break;
      n += 1;
    }
    return Math.max(1, n);
  }

  function moveIndustryFocus(grid, step) {
    const cards = [...grid.querySelectorAll(".ind-card")];
    if (!cards.length) return;
    let i = cards.findIndex((c) => c.tabIndex === 0);
    if (i < 0) i = 0;
    const next = Math.min(cards.length - 1, Math.max(0, i + step));
    cards.forEach((c, k) => { c.tabIndex = k === next ? 0 : -1; });
    cards[next].focus();
  }

  //: 弹窗的所有事件只在这里绑一次。启动时调一次（见文件末尾）。
  function wireIndustryModal() {
    const dlg = $("#industry-modal");
    const btn = $("#industry");
    if (!dlg || !btn) return;
    btn.addEventListener("click", openIndustryModal);
    const close = $("#ind-close");
    if (close) close.addEventListener("click", () => dlg.close());
    // 点遮罩关闭。`<dialog>` 自己 `padding: 0`、内边距全在 .ind-modal-inner 上，
    // 所以落在弹窗盒子里的点击一定命中某个后代 —— `e.target === dlg` 就是「点了遮罩」。
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    // Esc 由浏览器关（`cancel` 事件），这里只负责把焦点还给入口按钮。
    dlg.addEventListener("close", () => btn.focus());
    const box = $("#ind-search");
    if (box) box.addEventListener("input", renderIndustryGrid);
    const grid = $("#ind-grid");
    if (grid) {
      grid.addEventListener("keydown", (e) => {
        const cols = industryGridColumns(grid);
        const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[e.key];
        if (step === undefined) return;
        e.preventDefault();
        moveIndustryFocus(grid, step);
      });
    }
  }
```

- [ ] **Step 4: 把另外两处 `.period` 同步换成 `setPeriodUI`**

现在有三份同样的循环（Step 3 抽出了 `setPeriodUI`，但另两处还没用上）。都换掉，否则「少改一处 = 按钮亮着但图是别的周期」。

`app.js` 第 1594-1604 行的周期按钮处理器改成：

```js
  for (const btn of document.querySelectorAll(".period")) {
    btn.addEventListener("click", () => {
      state.period = btn.dataset.period;
      setPeriodUI(state.period);
      load();
      loadWatch();
    });
  }
```

`app.js` 第 1702-1709 行的 `?period=` 首屏对齐改成：

```js
  if (sp.get("period") && PERIOD_CN[sp.get("period")]) {
    state.period = sp.get("period");
    setPeriodUI(state.period);
  }
```

改完之后 `grep -n "aria-selected" src/chanlun/web/static/app.js` 应当**只剩两处**：`setPeriodUI` 里那一处，以及 Task 3 的 `syncIndustryTabs` 里那一处（弹窗的级别 tab）。原来那三份手写循环一份都不该剩下。（`.rail-tab` 用的是 `aria-pressed`，不在这条 grep 里。）

- [ ] **Step 5: 把 `syncIndustryButton()` 挂进 `setCode`**

`app.js` 的 `setCode`（第 1571-1575 行）改成：

```js
  function setCode(code) {
    state.code = code;
    $("#code").value = code;
    renderFav();
    // 行业选择器的入口按钮跟着当前代码走：换票之后它不能还留着上一个行业名。
    syncIndustryButton();
  }
```

`syncIndustryButton` 是函数声明，会提升到整个 IIFE 作用域顶部，所以它定义在 `setCode` **之后**也照常可用。

- [ ] **Step 6: 删掉旧的 `change` 处理器，改在启动时 wire**

`app.js` 第 1642-1661 行（`// 行业指数：选中即跳转…` 那段注释 + `if ($("#industry")) { … }`）整体删掉 —— 那套逻辑已经搬进 `pickIndustry`。

然后在文件末尾（第 1732 行 `loadIndustries();` 之前）加一行：

```js
  wireIndustryModal();
  loadIndustries();
```

`wireIndustryModal()` 必须在 `loadIndustries()` **之前**：`loadIndustries` 里会调 `syncIndustryButton()` 与 `renderIndustryGrid()`，而这两者依赖的元素与事件在此之前就位，行为才可预期。

- [ ] **Step 7: 跑测试，确认全绿**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/web/test_frontend_industry.py -v`

Expected: 七条全 PASS（2 条原有 + Task 2 加的 3 条 + 本任务新加的 2 条）。

- [ ] **Step 8: `node --check` 语法护栏**

Run: `node --check chanlun/src/chanlun/web/static/app.js && echo OK`

Expected: `OK`（无输出即语法通过）。

- [ ] **Step 9: 真实浏览器逐字验收（DOM dump，不看截图）**

刷新页面（`web/static/**` 改动只要刷新），跑 CDP 探针。每一项都要读**文本**：

| 动作 | 期望（从 DOM 里读出来的原文） |
|---|---|
| 点 `#industry` | `document.querySelector('#industry-modal').open === true` |
| 打开后 | `document.activeElement.id === "ind-search"` |
| tab 文案 | `["一级行业（31）", "二级行业（124）"]` |
| 点「二级行业（124）」 | `#ind-grid .ind-card` 数量 **124** |
| 在 `#ind-search` 里输入 `银行` 再 dispatch `input` | 卡片 **3** 张，且 `#ind-grid .ind-group` 的文案依次为 `一级行业（1）`、`二级行业（2）`；`#ind-tabs.hidden === true` |
| 输入 `801120` | 卡片 **1** 张，`.ind-card-name` 文本为 `食品饮料` |
| 输入 `食品` | 卡片 **3** 张，**跨两级**（组标题 `一级行业（1）` + `二级行业（2）`） |
| 清空搜索框 | 卡片回到 **124**（二级 tab 仍选中），`#ind-tabs.hidden === false` |
| 点回「一级行业（31）」 | 卡片 **31** 张 —— **下面两行必须在这一级上读**：`sw.801120` 食品饮料是**一级**行业，二级那 124 张里根本没有它，停在二级会点空 |
| `sw.801120` 那张卡 | `dataset.local === "1"`；其余 `"0"`（二级那一级上 124 张**全是** `"0"`，因为本机只有这一个板块有日线） |
| 点它 | `open === false`；`#industry-name` 文本变成 `食品饮料`；`#stamp` 含 `sw.801120`；图上 bar 数 **21** |
| 关闭后 | `document.activeElement.id === "industry"` |
| 按 ↑↓←→ | 焦点在 `.ind-card` 之间移动，且任一时刻**只有一个**卡片 `tabIndex === 0` |
| 按 Esc | `open === false` |

把这份 dump 的原文存到 `chanlun/docs/evidence/2026-10-06-行业弹窗-DOM-验收.md`（照 `docs/evidence/` 里既有文件的写法：一行一条「测了什么 / 读出什么」）。**任何一条对不上就先修，别提交。**

- [ ] **Step 10: 提交**

```bash
git add chanlun/src/chanlun/web/static/app.js chanlun/tests/web/test_frontend_industry.py chanlun/docs/evidence/2026-10-06-行业弹窗-DOM-验收.md
git commit -m "feat(web): 行业弹窗的开关、跨级搜索与键盘导航，点卡片即出图（D-44）"
```

---

### Task 5: 文档、版本 0.7.0、行号基线

**Files:**
- Modify: `CHANGELOG.md`（`[Unreleased]`：第 50 行附近 `### 新增` 加一条；第 94 行起 `### 修复` 里三处 `api.py` 行号引用要修对）
- Modify: `ARCHITECTURE.md`（D-44 的「后果 · 变更历史」+ `/api/industries` 的形状说明）
- Modify: `pyproject.toml`（`[project].version`）
- Modify: `optimizer/tools/doc_refs_baseline.json`（重跑 `--update-baseline`）
- Modify: `tests/optimizer/test_check_doc_line_refs.py`（钉住的行数/处数断言）
- Modify: `tests/web/test_frontend_watchlist.py`（**只改注释，不改断言**：第 22-25 行那条理由不成立。它写「`<dialog>` 在两种状态下都不在流内……所以它**永远不是 grid item**」—— 非模态的 `<dialog open>`（只设 `open` 属性、**没**调 `showModal()`）**是在流内的**，UA 样式只有 `dialog:not([open])` 才是 `display: none`。`#industry-modal` 只用 `showModal()`，所以**放行 `dialog` 这个决定本身是对的**，错的是那句过度概括的理由。改成把「本项目的 dialog 只用 `showModal()`」写成前提，别让后来者以为 `dialog` 天然不占轨道。`NO_BOX_TAGS` 的内容与那三条 grid 断言**一个字都不许动**。）

**Interfaces:**
- Consumes: Task 1-4 的全部改动。
- Produces: 无代码接口；产出是「文档与代码一致」这件事本身。

- [ ] **Step 1: 涨版本**

`pyproject.toml` 的 `[project].version` 从 `0.6.0` 改成 `0.7.0`。

依据 AGENTS.md §2 的表：**向后兼容的新功能** ⇒ `MINOR`。这次同时新增了前端能力与 `/api/industries` 的一个响应字段，两者都是向后兼容的增量。

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -c "from chanlun import __version__; print(__version__)"`

Expected: `0.7.0`（`src/chanlun/version.py` 是**活读** `pyproject.toml` 的，所以不用改它）。

- [ ] **Step 2: 写 CHANGELOG**

`CHANGELOG.md` 的 `[Unreleased]` 段落（第 43 行起）在 `### 新增`（第 50 行）下面加一条。写清三件事：**换了什么**、**为什么**、**新增的响应字段**：

```markdown
- **行业指数选择器从下拉框换成大弹窗**（D-44）。`<select id="industry">` 里 155 项
  要在一个下拉里滚，找不到、看不清、也不知道哪些板块本地有数据。现在左栏入口是一个
  显示当前选中的按钮，点它开一个 `width: min(1080px, 92vw)` 的原生 `<dialog>`
  （`showModal()`：遮罩、Esc、焦点圈禁、背景 `inert` 都由浏览器给）：
  一级 / 二级 tab 切换、名称网格、**跨两级**搜索（打「银行」不再因为停在二级 tab 上
  而回空结果）、↑↓←→ 键盘导航（roving tabindex，整张网格只占一个 Tab 位）。
  卡片用「**有数据才亮**」标出本地覆盖度：`sync_state` 里有日线的亮着并标「本地」，
  其余降色 + 虚线边标「待同步」（复用 `.chip.capped` 的「虚线 = 还没到位」语法；
  **不用 `opacity`**，它会把 `:focus-visible` 的 outline 一起变淡）。
  配套地 `GET /api/industries` 每个 item 多回一个布尔 `local`
  （`sync_state` 里 `(code, "day")` 存在、`rows > 0`、`error` 为空）——
  仍然纯本地，不联网。
  顺带把弹窗遮罩从写死的 `rgba(9, 11, 14, .74)` 换成
  `color-mix(in srgb, var(--ink) 74%, transparent)`：前者是全文件里**唯一**一处
  `rgba(` 写法（`grep -c 'rgba('` 从 1 变 0），而且 `rgb(9,11,14)` 这个近黑色
  别处不存在、是那一行新造的颜色；后者从既有 token 推导，不新增颜色。
  **代价是遮罩比原先略亮** —— `--ink`（`#0f1216`）比那个手写值（`rgb(9,11,14)`）浅，
  这是用户可见的变化，所以记在这里。
```

**同一段 `[Unreleased]` 里还有三处 `api.py` 行号引用已经指错了**，一并修对（都在未发布区，所以按 AGENTS.md §1 的「只增不改」是**可以**改的 —— 那条纪律管的是**已发布**版本）：

- 第 101 行（`### 修复` 那条「键名读错」）引 `web/api.py:654` 说 `as_of` 在那儿。Task 1 给路由加了 `request: Request` 又补回 docstring，`as_of` 已经挪到别处 —— **自己用内容定位，别拿数字推算**。
- 第 921 行引 `web/api.py:601` 说「查询走」那儿 —— **现在那一行是空行**，是先前就漂了的，不是这次弄坏的。
- 第 907 行引 `web/api.py:202-213` 说「这只票从来没除过权」 —— 区间的起点也是空行，函数本体从下一行才开始。

上面给的三个**行号是写下这段时的**值，而你刚往 `### 新增` 插了一条，它们已经往下移了 —— 别按数字去找，按引文找：`### 修复` 那条「键名读错」、那条「查询走」、那条「这只票从来没除过权」。引文才是定位器，行号只是线索。

三处都要**先读那句引用在讲什么，再去 `api.py` 里找那段内容现在在哪**，然后按实际位置改。`CHANGELOG.md` **不在** `check_doc_line_refs.py` 的扫描范围内（它只扫 `ARCHITECTURE.md`，见 `optimizer/tools/check_doc_line_refs.py:66`），所以没有任何护栏会拦住这三处 —— 全靠你自己核对，改完在报告里逐条写明「原值 → 新值 + 你凭什么认定」。

- [ ] **Step 3: 更新 ARCHITECTURE**

在 `ARCHITECTURE.md` 里 D-44 那条记录的「后果 · 变更历史」段（第 443 行起，`依据` 在第 499 行附近）追加一条变更历史，并更新那条列举选择器入口的文件行号引用（原第 491-497 行提到 `index.html` 与 `app.js` 的旧行号）。

> **那条引用的散文本身也过期了，而且有一条「行号对、内容不对」，必须一起改。**
> 第 493-497 行现在写的是「入口是左侧自选栏里的**下拉框**……**选中即跳转**」—— Task 2 已经把它换成按钮。
> 更要紧的是 Task 4 把其中一条引用重锚到了 `app.js:1704`，而那是 `function wireIndustryModal() {`，
> 管的是**事件绑定**；「选中即跳转」实际在 `pickIndustry`（`app.js:1655-1668`）。
> **护栏只认指纹，不认语义** —— 你只要把行号指到「指纹对得上」的地方、散文一字不动，
> 它就会一路绿着骗过去。这类「行号对、内容不对」比行号错更坏，因为它连护栏都躲过了。
> 所以：把「下拉框」改成「按钮」，并让引用各自指到该指的地方（**绑定 → `wireIndustryModal`，
> 跳转 → `pickIndustry`**），或者把散文改成与 `wireIndustryModal` 相符的说法。
> **改完在报告里写明你选了哪一种、以及每条新引用当前指向的那一行原文是什么。**

要写进去的**决策与后果**：

1. 入口从 `<select>` 换成 `<button id="industry">`（**id 不变**，`$("#industry")` 与既有护栏测试都不用改名），当前选中显示在 `#industry-name` 上。
2. 弹窗是 `<body>` 的直接子元素，**不占 `body` 的 grid 轨道**（未打开 `display:none`；`showModal()` 后进 top layer 且 `position:fixed`），因此 `tests/web/test_frontend_watchlist.py` 的 `NO_BOX_TAGS` 放行 `dialog`。
3. `GET /api/industries` 的 item 多一个 `local: bool`。**`Board.as_dict()` 不动** —— 它同时供 `/api/search` 用，那边的行不该多一个只有选择器才关心的键。
4. **被否决的方案**（写清理由，免得以后重想）：
   - 二级行业按父级一级分组 → 否决：`industry.py` 的 `SW_L1` / `SW_L2` 是两张扁平表，二级代码与父级**不同前缀**（`801103 IT服务Ⅱ` 的父级是 `801750 计算机`）；要分组得新增 124 条**无法验证**的父子映射（`industry --check` 只核对数据商清单，核不了父子关系）。
   - 涨跌幅排行 → 否决：要新增批量行情端点，而 155 个板块只有 1 个本地有数据，排不满的排行榜比没有更容易误导。
   - 每张卡加「本地 / 待同步」小徽章 → 否决：155 个徽章是噪点；改成整卡明暗。
   - 自建 `div[role="dialog"]` + 手写焦点圈禁 → 否决：那四件事（圈禁、Esc、滚动锁、背景不可点）正是无障碍 bug 的高发区，原生 `<dialog>` 免费给。
5. `local` 的判据是 `rows > 0` 且 `error` 为空：`sync_state` 里躺着同步失败的行，把它算成有数据会让卡片亮着却画出空图。

**还有一处口径已经陈旧，必须一起改**：`ARCHITECTURE.md` 第 492 行（D-44 那条里列举 `/api/industries` 返回形状的地方）写着「前端只认 `code`/`name`/`level` **三个键**」。Task 1 之后 item 有**四个**键（多了 `local`）。就前端**当前**行为而言「只认三个」仍然成立（Task 3/4 才会去读 `local`），但作为**响应形状**的描述它已经错了。改成四个键，并把 `local` 的判据（`sync_state` 里 `(code, "day")` 存在、`rows > 0`、`error` 为空）写进去。

同一句话在第 526 行附近（「被否决的替代方案」段里）又出现了一次。**两处都要看**：如果那里的语境是「当初为什么不给 item 加键」，那是历史陈述，改不改由你判断并在报告里说明理由；如果它同样在描述**现在**的响应形状，就一起改对。

**行号引用务必写对**（`optimizer/tools/check_doc_line_refs.py` 会校验，未入基线就红）。历史性错值要写成**不带冒号**的形式，例如「`ARCHITECTURE.md` 的 `648` 行 → 实为 `943`」，否则解析器会把它当成一条真的引用。

- [ ] **Step 4: 重跑行号基线**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py --update-baseline`

然后：

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py; echo "EXIT=$?"`

Expected: `EXIT=0`，且 `不符 0 条 / 未入基线 0 条 / 基线残留 0 条`。记下新的两行汇总（`.py` 桶的「全文 N 处 / M 行；§3.4 …」与非 `.py` 桶的「N 处 / M 行」），Step 5 要用。

- [ ] **Step 5: 更新钉住的行数断言**

`tests/optimizer/test_check_doc_line_refs.py` 里被钉住的具体数字（`（1737 行）` 出现次数、`（785 行）` 出现次数、`.py` 桶的 `全文 … 处 / … 行；§3.4 … 处 / … 行`、非 `.py` 桶的 `… 处 / … 行`、以及那份 13 项的 re-anchor 清单）都会因为 `app.js` / `styles.css` / `index.html` 变大而位移。

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest tests/optimizer/test_check_doc_line_refs.py -v`

按**失败信息给出的实际值**逐条更新断言，**不要**用 `--update-baseline` 去掩盖它们：基线管的是「引用还在不在原位」，这些断言管的是「文档里写的行数是不是真的」。

- [ ] **Step 6: 全量测试**

Run: `PYTHONPATH=src ../.venv-chanlun/bin/python -m pytest -q`

Expected: 全绿（上次基线是 `1194 passed, 12 deselected`；这次因为新增测试，passed 数会更大，deselected 仍是 12）。把实际数字记下来。

- [ ] **Step 7: 提交**

```bash
git add chanlun/pyproject.toml chanlun/CHANGELOG.md chanlun/ARCHITECTURE.md chanlun/optimizer/tools/doc_refs_baseline.json chanlun/tests/optimizer/test_check_doc_line_refs.py chanlun/tests/web/test_frontend_watchlist.py
git commit -m "docs: 记录行业弹窗与 /api/industries 的 local 字段，版本 0.6.0 → 0.7.0（D-44）"
```

---

## 收尾检查（全部任务做完之后）

- [ ] `git status --short` 里**只有**你打算提交的路径；工作区里那些无关的未跟踪文件（`.DS_Store`、`3d-fitting-room/`、`chan-structure-engine-notes.md`）**别碰**。
- [ ] 看盘页进程是**重启过**的（Task 1 改了 `src/chanlun/**/*.py`），且 `/api/industries` 回 **200** 而不是 404 —— `fetch` 对 404 是正常 resolve，只有 `res.ok` 拦得住，这条已经吃过一次亏。
- [ ] `curl -s 'http://127.0.0.1:8888/api/industries?level=1' | python -m json.tool | head -20` 里每个 item 都有 `"local": false`（除 `sw.801120`）。
- [ ] 设计稿 `docs/superpowers/specs/2026-10-06-industry-picker-modal-design.md` 与最终实现**没有**说不通的地方；若有，改设计稿并单独提交，别让文档留在半路。
- [ ] `ARCHITECTURE.md` 与 `CHANGELOG.md` 都改了（AGENTS.md §1 是强制的，不是建议）。
- [ ] 所有提交信息是中文 Conventional Commits，且每个提交都是**单独列出路径**加的。
