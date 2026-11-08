# 行业指数选择器：从下拉框改成大弹窗

日期：2026-10-06
状态：待用户复核
关联决策：D-44（A 股一级 / 二级行业指数）

## 1. 目标

把左侧自选栏里那个 `<select id="industry">`（1 个占位项 + 两个 `optgroup`、共 155 项）
换成一个**大弹窗**，让「155 个行业里挑一个」这件事变得能用。

**非目标**（明确不做）：

- 不改行业清单的来源与内容（仍内置在 `data/industry.py`，仍由 `GET /api/industries` 供数）。
- 不做涨跌幅排行（需要新增批量行情端点，见 §3.4）。
- 不把行业指数加进自选池（D-44 第 10 条：它是**导航**，不是自选池成员）。
- 不改分钟周期与板块数据的既有约束（板块只有日线；选中时若停在分钟周期，
  自动切回日线 —— 这条行为原样保留）。

## 2. 用户已确认的三条决定

| # | 问题 | 选择 |
|---|---|---|
| 1 | 弹窗里怎么找行业 | **一级 / 二级切换 + 名称网格 + 搜索框** |
| 2 | 卡片上显示什么 | **行业名 + 代码 + 「本地有数据 / 待同步」标记** |
| 3 | 入口与流向 | **左栏按钮显示当前选中 → 点卡片即关闭并出图** |

决定 2 要求后端多返一个字段，见 §4.1。

## 3. 方案对比

### 3.1 弹窗的实现载体

| 方案 | 判断 |
|---|---|
| **原生 `<dialog>` + `showModal()`**（选中） | 遮罩、Esc 关闭、**焦点圈禁**、背景 `inert`、`::backdrop` 全部由浏览器给。Chrome 154 支持完善。 |
| 自建 `div[role="dialog"][aria-modal="true"]` + 手写遮罩 | 否决：焦点圈禁、Esc、滚动锁、背景不可点这四件事都要手写，而它们正是无障碍 bug 的高发区。零收益。 |
| `<dialog>` + `show()`（非模态） | 否决：不加遮罩、背景可点，不符合「大弹窗」的预期。 |

### 3.2 二级行业的排布

| 方案 | 判断 |
|---|---|
| **扁平网格，按名称拼音序**（选中） | API 返回的顺序本来就是拼音序（`SW_L1` 从「交通运输」到「食品饮料」，实测即拼音序），不用额外排序。124 张卡片一屏放不下，但配搜索后够用。 |
| 二级按**父级一级行业分组**，每组一个小标题 | 否决：`industry.py` 的 `SW_L1` / `SW_L2` 是两张**扁平**表，二级代码与父级代码**不同前缀**（`801103 IT服务Ⅱ` 的父级是 `801750 计算机`）。要分组就得新增 124 条父子映射，而 `python -m chanlun industry --check` 只核对数据商返回的清单，**核不了父子关系** —— 等于往系统里塞一份**无法验证**的数据。 |
| 按代码排序 | 否决：代码是数据商的内部分段，用户不靠它认行业。 |

### 3.3 「本地有数据」怎么表达

| 方案 | 判断 |
|---|---|
| **整卡明暗：有数据的亮、待同步的暗 + 虚线边**（选中） | 155 张卡片的覆盖度**一眼可见**，且复用了本项目已有的视觉语法 —— `.chip.capped` 就是用「虚线 + 半透明」表示「这个东西还没完全到位」。 |
| 每张卡片加一个「本地 / 待同步」小徽章，卡片外观不变 | 否决：155 个徽章会变成噪点，读到第三行就没人看了。 |
| 只在点击后提示「这个板块没有数据」 | 否决：**事后**才知道，等于让用户先撞墙。D-44 收尾那次事故的教训就是「悄悄留白比留白本身更坏」。 |

### 3.4 涨跌幅排行

否决。要新增一个批量板块行情端点；而且 155 个板块里**只有 1 个本地有数据**，
排序结果会缺一大块 —— 一张排不满的排行榜比没有排行榜更容易误导。

## 4. 设计

### 4.1 后端：`/api/industries` 新增逐项 `local` 布尔

```python
@router.get("/api/industries")
def industries(request: Request, level: str = "") -> dict[str, Any]:
    lv = str(level or "").strip()
    boards = industry.all_boards(lv)
    cfg = _cfg(request)                      # ← 新增：现在要读本机 meta.db
    conn = meta.init(cfg.data.meta_db)
    try:
        # 「本地有数据」= sync_state 里有这一条、rows > 0、且没有 error。
        local = {r["code"] for r in meta.all_sync(conn, "day")
                 if not r["error"] and r["rows"] > 0}
    finally:
        conn.close()
    items = [{**b.as_dict(), "local": b.code in local} for b in boards]
```

- 仍然**纯本地**：清单内置、`local` 来自本机 `meta.db`，都不联网。
- `local` 只加在 `/api/industries` 的响应里，**不进** `Board.as_dict()` ——
  后者同时供 `/api/search` 用，那边的行不该多一个只有选择器才关心的键。
- 判据用 `rows > 0` 且 `error IS NULL`：`sync_state` 里可能有「同步失败」的行
  （`error` 非空、`rows` 可能为 0），把它当「有数据」会让卡片亮起来却画出空图。

### 4.2 DOM 骨架

左栏入口（替换 `index.html:83-91` 的 `.ind-pick` 内容）：

```html
<div class="ind-pick">
  <span class="ind-label" id="ind-label">行业指数 · 申万</span>
  <button type="button" class="ind-open" id="industry"
          aria-haspopup="dialog" aria-labelledby="ind-label industry-name">
    <span class="ind-open-name" id="industry-name">选一个行业…</span>
    <span class="ind-open-caret" aria-hidden="true">▾</span>
  </button>
  <p class="ind-note" id="industry-note" hidden></p>
</div>
```

`id="industry"` **保留在入口上**（只是从 `<select>` 变成 `<button>`），
这样 `$("#industry")` 与既有的两个前端护栏测试都不用改名。

弹窗（`<body>` 的直接子元素，放在 `.work` 之后、两个 `<script>` 之前）：

```html
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

`<dialog>` 自己 `padding: 0`，全部内边距交给 `.ind-modal-inner`（`height: 100%`）。
这样**落在弹窗盒子内的点击一定命中某个后代**，`e.target === dialog` 就等价于
「点了遮罩」—— 点遮罩关闭因此不会误伤弹窗自己的边缘留白。

`.ind-tabs` 与 `.ind-grid` **由 JS 填**：tab 按钮的文案与计数、
卡片的名字与代码全部来自 `GET /api/industries` 的响应。
**HTML / JS 里不出现任何一个行业名**（D-44 的硬约束，护栏测试盯着）。

### 4.3 交互规则

| 动作 | 结果 |
|---|---|
| 点左栏按钮 | `showModal()`；焦点落在搜索框 |
| 点一级 / 二级 tab | 网格换成该级别；tab 用 `role="tab"` + `aria-selected` |
| 搜索框打字 | **跨两级**过滤（名称包含或代码包含，忽略大小写）；tab 条整条**隐藏**（不是换成计数行），结果**按级别分组**，每组一个跨列的组标题「一级行业（N）/ 二级行业（N）」—— 计数落在这两个组标题里，**没有**单独一行「跨两级搜索：N 个匹配」 |
| 清空搜索框 | 恢复 tab 条与当前级别的网格 |
| 点一张卡片 | 弹窗关闭、`setCode(code)` + `load()` + `loadWatch()`；若当前停在分钟周期先切回日线（沿用原逻辑） |
| Esc / 点 ✕ / 点遮罩 | 关闭，不改变当前选中的行业（遮罩判定见 §4.2 的 `.ind-modal-inner`） |
| ↑ ↓ ← → | 在网格里移动焦点（roving tabindex：整张网格只占一个 Tab 位） |
| Enter / Space | 选中当前焦点卡片 |

空结果时网格位置显示：「没有匹配的行业。换个词试试，或者直接输代码 801120。」
（**是邀请，不是道歉**。）
（示例**只给代码、不给行业名**：「半导体」「银行」都是 `industry.py` 里的真名，写进 HTML / JS
会踩 D-44 那条「31 + 124 个名字只活在 `industry.py` 里」的硬约束 —— 护栏会打红；
而放行它们得开一份「文案区白名单」，白名单会随着文案改动越加越多，最后把约束加死。
代码是稳定标识、不是名字，所以示例给代码。）

搜索「跨两级」而不是「在当前级别里搜」是有意的：用户打「银行」时想的是
**那个行业**，不是「一级行业里的那个银行」。若只在当前级别里搜，
停在二级 tab 上打「银行」会得到空结果 —— 这正是要避免的陷阱。

搜索结果的级别**用跨列的组标题**（`grid-column: 1 / -1`）标，不在每张卡片上加小标：
组标题是同一个网格里现成的结构位，而 155 个级别小标只会变成噪点。
「一级行业（1）/ 二级行业（2）」的文案与计数同样来自响应，不写死。

### 4.4 视觉

沿用既有 token（`styles.css:12-30`），不引入新色：

| 用途 | 取值 |
|---|---|
| 遮罩 | `color-mix(in srgb, var(--ink) 74%, transparent)`（原稿写的是 `rgba(9, 11, 14, .74)`；落地时换成 token 版，遮罩因此比原稿**略亮** —— `--ink` 是 `#0f1216`） |
| 弹窗底 / 边 | `var(--panel)` / `1px solid var(--line)`，`border-radius: var(--r)`（3px） |
| 卡片底 / 边 | `var(--panel-2)` / `1px solid var(--line-soft)` |
| 卡片名 / 代码 | `var(--rice)` / `var(--mono)` + `var(--mohui)` |
| **待同步**卡片 | 文字降到 `var(--mohui)`、`border-style: dotted`、底色 `var(--panel)` |

> 「待同步」用**降色 + 虚线**表达，**不用 `opacity`** —— `opacity` 会把
> `:focus-visible` 那圈 `outline` 一起变淡，键盘用户会看不见焦点在哪。
> 虚线边沿用 `.chip.capped`（`styles.css:452`）已有的语法：**虚线 = 还没完全到位**。

| 用途 | 取值 |
|---|---|
| 当前选中 | `border-color: var(--indigo)` + 一枚「当前」小标 |
| tab 选中态 | 复用 `.period.is-on`：`background: var(--panel-2)` + `box-shadow: inset 0 -2px 0 var(--indigo)` |

尺寸：`width: min(1080px, 92vw); height: min(760px, 88vh)`，网格
`repeat(auto-fill, minmax(168px, 1fr))`，`gap: 8px`，网格区自己滚动。
`<dialog>` 的 UA 样式表给了 `max-width/max-height: calc(100% - 6px - 2em)`，
要显式改成 `92vw` / `88vh`，否则在窄屏上会被 UA 的 `2em` 抢走宽度。

**签名元素：「有数据才亮」的网格。** 整片网格默认是暗的（154 个待同步），
本地已有的那几个是亮的。随着同步的板块变多，这片格子会自己亮起来 ——
它同时是「选行业的地方」和「本地覆盖度的一眼读数」。
页脚把它量化成 `本地已有 N / M 个板块的日线`，两个数都是**算出来的**
（N = 拿到的行里 `local` 为真的个数，M = 拿到的行数），不是写死的。

**页脚文案**（`#ind-modal-foot`）：
「本地已有 N / M 个板块的日线。标「本地」的选中就能看；其余选中后是空图，
要先同步它的日线（`python -m chanlun sync --period day --codes 代码`）。」

左栏 `#industry-note` 的成功文案保持原样 —— 注意
**「板块只有日线，周 / 月由日线聚合」这句在左栏提示里、不在页脚**（两处不重复），
其中的清单核对日期来自响应的 `as_of`，不是写死的：
「清单核对于 <as_of>；板块只有日线，周 / 月由日线聚合。」

### 4.5 无障碍

- `<dialog>` + `showModal()` 自带焦点圈禁与背景 `inert`。
- 打开时焦点落搜索框；关闭后焦点回到左栏入口按钮（`showModal` 不自动做，要手写）。
- 入口按钮 `aria-haspopup="dialog"` + `aria-labelledby`（标签 + 当前值）。
- 网格用 roving tabindex，卡片是原生 `<button>`，Enter / Space 天然可用。
- `:focus-visible` 已有全局 `outline: 2px solid var(--indigo)`，沿用。
- 标题用 `<h2>`：页面里**本来就没有 `<h1>`**（`index.html:64` 的自选栏标题也是 `<h2>`），
  这里跟着既有层级走，不顺手改全站标题结构 —— 那是另一件事。
- `prefers-reduced-motion` 下不做任何过渡（本设计的动效只有 120ms 的
  卡片 hover 边框色，本就要尊重这条）。

## 5. 测试与验证

### 5.1 后端（`tests/web/test_api_search.py`）

1. `/api/industries` 每一项都有布尔 `local`；`conftest` 的临时库里没有
   `sw.%` 的 `sync_state` 行 ⇒ 155 项全 `False`。
2. 往 `sync_state` 插一行 `('sw.801120', 'day', …, rows=21, error=NULL)` ⇒
   该项 `local is True`，其余仍 `False`。
3. 插一行 `rows=0, error='...'` 的失败行 ⇒ 该项**仍是** `False`（负对照）。

### 5.2 前端契约（`tests/web/test_frontend_industry.py`）

保留现有两条（键名 `as_of`、`res.ok` 状态码），新增：

4. `index.html` 里**没有** `<select id="industry"`，而有 `<dialog id="industry-modal"`。
5. `app.js` / `index.html` 的**代码面**里不出现任何一个内置行业名（逐个 grep
   `industry.py` 的 155 个名字，命中即红）—— D-44 的硬约束。实际扫描面比字面窄，
   是**有意收窄**的：`app.js` 先剥掉注释再扫，`index.html` 只看
   `<dialog id="industry-modal">…</dialog>` 那一段。理由是注释里为解释「搜索为什么
   跨两级」而提到行业名是正当的，顶栏 `placeholder="600000 / 浦发银行"` 里的
   「银行」则是 pre-existing 的个股示例、与行业选择器无关 —— 按字面全文件扫会假红。
6. `app.js` 读 `it.local`（后端契约键）。
7. `node --check app.js`（既有护栏，沿用）。

### 5.3 真实浏览器（headless Chrome + CDP，`Network.setCacheDisabled`）

逐字读 DOM，不看截图：

- 点入口 → `document.querySelector('#industry-modal').open === true`。
- tab 计数与卡片数：一级 **31**、二级 **124**。
- 搜索「银行」→ 卡片数 **3**（一级 `银行` + 二级 `国有大型银行Ⅱ`、`股份制银行Ⅱ`），
  结果**按级别分组**（跨列的组标题「一级行业（1）/ 二级行业（2）」），
  **不在每张卡片上加级别小标**（见 §4.3）。
- 搜索「801120」→ 卡片数 **1**，名字为「食品饮料」。
- 搜索「食品」→ 卡片数 **3**，**跨两级**（一级 `食品饮料` + 二级 `休闲食品`、`食品加工`）——
  这一条专门盯「搜索是不是跨级」。
- `sw.801120` 那张卡 `data-local="1"`，其余 `data-local="0"`。
- 点它 → `open === false`、左栏按钮文案变成「食品饮料」、
  `#stamp` 含 `sw.801120`、图上 bar 数 **21**。
- 焦点：打开后 `document.activeElement.id === "ind-search"`；
  关闭后回到 `#industry`。

### 5.4 护栏测试的连带修改

`tests/web/test_frontend_watchlist.py` 的 `_body_grid_items` 要放行 `dialog`：
本页对 `#industry-modal` **只用 `showModal()`** —— 未打开时是 `display: none`，
打开后进 top layer 且 `position: fixed`，两种状态下**都不在流内**，永远不会占
`body` 的 grid 轨道。**这个放行理由不能推广到别的 `<dialog>`**：非模态的
`<dialog open>` 是在流内的，UA 的 `display: none` 只作用于 `dialog:not([open])`。
这是修正判据的**覆盖面**，不是绕过护栏 —— 已在 `NO_BOX_TAGS` 旁边写清理由。

## 6. 文档与版本影响

- `ARCHITECTURE.md`：D-44 的「后果 / 变更历史」新增一条（下拉框 → 弹窗、
  新增 `local` 字段、被否决的分组方案）；`/api/industries` 的 API 形状说明同步更新。
- `CHANGELOG.md`：`[Unreleased]` 的 `### 新增` 记一条。
- **版本影响 `MINOR` ⇒ `0.6.0 → 0.7.0`**，在本改动自己的提交里涨
  （依据 AGENTS.md §2 的表：「向后兼容的新功能」——新增前端能力 +
  `/api/industries` 新增响应字段，两者都是向后兼容的增量）。
- 文档行号引用会再次整体位移：改完必须重跑
  `optimizer/tools/check_doc_line_refs.py --update-baseline`（非 `.py` 桶现在也进指纹了）。

## 7. 已知风险

| 风险 | 处置 |
|---|---|
| 155 个按钮 = 155 个 Tab 位 | roving tabindex：整张网格只占一个 Tab 位，↑↓←→ 在格内走。 |
| 124 张卡片一次全建 DOM | 实测 124 个 `<button>` 的建造成本可忽略；不做虚拟滚动（过早优化）。 |
| `local` 每次都查 `sync_state` | 一次 `SELECT`（本机 5497 行），且只在开页面时调一次。 |
| 弹窗是 `body` 第 5 个子元素 | 已确认 `<dialog>` 不占 grid 轨道（§5.4），并补护栏注释。 |
| 「待同步」的卡片看起来像禁用 | 卡片仍然完全可点，且页脚第一句就解释这个约定。 |
