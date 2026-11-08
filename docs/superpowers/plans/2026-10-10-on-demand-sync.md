# 按需拉取（换票即补最新）实施计划

> **REQUIRED SUB-SKILL:** 用 `executing-plans` 逐任务实施本计划；实现期遇到任何测试失败先用
> `systematic-debugging`，不要凭猜改代码。

**目标：** 用户打开某只票 / 某个指数时，如果本地最后一根 K 线落在**该市场最新交易日**之前，
页面**自己**去把这个周期补到最新，补完自动重画；用户不必等 17:30 的定时任务，也不必先撞一个
404 才看见「同步这个周期」按钮。

**架构：** 判断「落不落后、能不能补」的**唯一口径在后端**（新函数 `freshness`，按市场选日历）；
前端只负责「什么时候去问、问过几次」：`load()` 拿到结构后读 `stale`，落后就入队，同时只飞一个
请求 + 一个待办槽，同一 `code|period` 本次会话只自动试一次。补完若用户还看着这一只就 `load()`
重画 —— 这一步是免费的，因为 `/api/structure` 的缓存键里含 `last_ts`（`web/api.py:408`），
parquet 一旦写进新的一根，缓存自然失效。

**技术栈：** Python 3.14 + FastAPI（`chanlun.web.api`）、原生 `app.js`（无框架、无构建）、
`pytest` + `fastapi.testclient.TestClient`、`node --check`。无浏览器依赖：前端契约用静态断言
（现有 `tests/web/test_frontend_sync_button.py` 的既有做法），端到端另用真浏览器 DOM dump 验收。

---

## 全局约束（每个任务都适用）

1. **路径与命令**
   - 仓库根是 `/Users/zzz/workspace`，项目在 `chanlun/`。所有工作目录 = `/Users/zzz/workspace/chanlun`。
   - 测试：`PYTHONPATH=src ../.venv-chanlun/bin/pytest -q`（`addopts = -m 'not live'`）。
   - 单文件：`PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_api_freshness.py`。
   - 本机**没有** `timeout` 命令；`cmd | tail` 会吃掉退出码，要看退出码请用 `${PIPESTATUS[0]}`。
2. **改完 `src/chanlun/**/*.py` 必须重启服务**（`serve` 没有 `--reload`）；只改 `web/static/*`
   只需刷新浏览器。启动方式：
   `PYTHONPATH=src PYTHONUNBUFFERED=1 nohup ../.venv-chanlun/bin/python -m chanlun serve > /tmp/serve.log 2>&1 &`
3. **判据不许有第二份实现**。日历按市场分派这件事只有 `calendar.py:111 calendar_for()` 一处；
   「这个周期能不能补」只有 `web/api.py:947 sync_blocker()` 一处。前端**不许**比日期、不许按号段
   猜指数，只读后端给的 `stale` / `syncable` / `expected_as_of`。
4. **不动布局**。`styles.css:51-58` 写明 `body` 网格行数必须等于 `body` 的直接子元素数，既有测试
   `tests/web/test_frontend_watchlist.py:327 test_body_grid_rows_cover_every_body_child` 守着它。
   chip **必须**放在 `<main class="stage">` 里、绝对定位（`.stage` 是 flex 列，
   `styles.css:265`；绝对定位子元素不占流）。顶栏「恒定 2 行」是硬约束（`app.js:236-254`），
   `#stamp` / `#stamp-detail` / `#notice` 都不能拿来放这个提示。
5. **护栏测试必须能失败**。写完每个测试先跑一次确认它是红的（在计划里写明期望的红是什么），
   再写实现。断言要咬住行为，不许写成「字符串在文件里出现过」这种自证式断言。
6. **文档纪律**（`AGENTS.md`）：`ARCHITECTURE.md` 的 `D-xx` 只增不改不重编号；新增空白要同时登记
   到 `ARCHITECTURE.md` §5 与 `CHANGELOG.md` 的 `### 已知空白与未实现项`；改了 `ARCHITECTURE.md`
   的行号引用后必须跑 `check_doc_line_refs.py --update-baseline`。
7. **本次不涨版本号**（与设计文档 §6.3 一致）：条目留在 `[Unreleased]`，`pyproject.toml` 不动。
   语义上属 **MINOR**（向后兼容的新功能），但 `0.3.0` 起的版本段落分派是已登记在
   `ARCHITECTURE.md` §5 的**独立任务**，本次不碰（详见 Task 6.5）。
   `chan/` 一行不动 ⇒ **不是判据变更**。
8. **提交方式**：用户的既有偏好是「整批做完一次性提交」（上一个提交 `48202ba` 就是这么来的）。
   因此下面每个任务末尾的 `git commit` 在**本会话批处理模式**下全部跳过，合并为 Task 7 的最后一次
   提交；若改用子代理逐任务执行，则按各任务里的步骤逐个提交。**任何情况下都不许 `git add -A`、
   不许 `git add chanlun/`**，只 `git add` 本任务明确列出的文件。

---

## 文件结构

| 文件 | 角色 |
| --- | --- |
| `src/chanlun/web/api.py` | 新增 `freshness()`（唯一判据）；把它挂到 `/api/structure`、`/api/bars`、404 分支 |
| `src/chanlun/web/static/index.html` | `<main class="stage">` 内新增 `#sync-chip`（1 行） |
| `src/chanlun/web/static/styles.css` | `.sync-chip` 样式（绝对定位、不占流、只有失败态可点） |
| `src/chanlun/web/static/app.js` | `postSync` 抽取、`pollSync` 用捕获 key、按需同步队列与 chip 状态机 |
| `tests/web/test_api_freshness.py` | 新增：后端判据单测 + 两个端点的集成测 |
| `tests/web/test_frontend_auto_sync.py` | 新增：前端契约（chip 结构、判据顺序、队列、轮询 key） |
| `tests/web/test_frontend_sync_button.py` | **改 1 处断言**：POST 搬到 `postSync` 后重定向该断言 |
| `ARCHITECTURE.md` | `D-49` + §5 空白行 |
| `CHANGELOG.md` | `### 新增` + `### 已知空白与未实现项` |
| `optimizer/tools/doc_refs_baseline.json` | 由 `--update-baseline` 重新生成 |
| `docs/evidence/2026-10-10-按需拉取验收.md` | 新增：可失败的测量记录 |

---

## Task 1：后端 `freshness` —— 全项目唯一一份「要不要补最新」的判断

**Files:**
- Create: `tests/web/test_api_freshness.py`
- Modify: `src/chanlun/web/api.py`（导入区 + `sync_blocker` 之后）

- [x] **1.1 写失败测试**

新建 `tests/web/test_api_freshness.py`：

```python
"""`freshness` 是全项目唯一一份「要不要去补最新」的判断（D-49）。

它是**按市场选日历**的：A 股用真实交易日历，港股/美股用宽松的工作日日历
（`calendar_for`，`src/chanlun/calendar.py:111`）。所以这里的日历一律**打桩**，
不让测试结果取决于"跑测试的这一天"。
"""
from __future__ import annotations

import datetime as dt

import pytest

from chanlun.web import api as web_api


class _FakeCalendar:
    """只实现被调用到的 `last_trading_day` —— 假对象也要能一眼看出它假在哪。"""

    def __init__(self, day: dt.date) -> None:
        self.day = day

    def last_trading_day(self, _today):  # noqa: ANN001
        return self.day


@pytest.fixture()
def fake_calendar(monkeypatch):
    """`calendar_for` 一律回答"最新交易日是 2026-10-09"。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2026, 10, 9))
    )


def test_lagging_as_of_is_stale(fake_calendar):
    got = web_api.freshness("600000", "day", "2026-10-08")
    assert got["expected_as_of"] == "2026-10-09"
    assert got["stale"] is True


def test_as_of_on_the_last_trading_day_is_not_stale(fake_calendar):
    assert web_api.freshness("600000", "day", "2026-10-09")["stale"] is False


def test_minute_as_of_is_compared_by_date_only(fake_calendar):
    """实测 30 分钟线的 `as_of` 是 `"2026-09-30 15:00"`（带时间）。

    拿整串去比，它永远小于任何日期串 —— 于是每一只已经最新的票都会白发一次同步。
    """
    assert web_api.freshness("600000", "30", "2026-10-09 15:00")["stale"] is False
    assert web_api.freshness("600000", "30", "2026-10-08 15:00")["stale"] is True


def test_missing_as_of_is_not_stale(fake_calendar):
    """不知道就不猜：宁可漏补一次，也不要在没有依据时去发请求。"""
    for bad in (None, ""):
        assert web_api.freshness("600000", "day", bad)["stale"] is False
    # 但"最新交易日是哪天"照样给出来：404 页面的提示要用它说一个具体日期。
    assert web_api.freshness("600000", "day", None)["expected_as_of"] == "2026-10-09"


def test_the_calendar_is_chosen_by_the_code_market(monkeypatch):
    """日历必须按**市场**分派，不能拿一份 A 股日历套所有票。

    同一根 10-06 的 K 线，在港股算最新、在 A 股算落后 —— 这正是 2026 国庆那周的
    真实形状（A 股休市、港股开市）。
    """
    seen: list[str] = []

    def fake(code):
        seen.append(code)
        day = dt.date(2026, 10, 6) if code.startswith("hk.") else dt.date(2026, 10, 9)
        return _FakeCalendar(day)

    monkeypatch.setattr(web_api, "calendar_for", fake)
    assert web_api.freshness("hk.hsi", "day", "2026-10-06")["stale"] is False
    assert web_api.freshness("600000", "day", "2026-10-06")["stale"] is True
    assert seen == ["hk.hsi", "600000"], "日历要按传进来的代码选，不能被写死"


def test_a_period_that_can_never_be_fetched_says_so():
    """指数没有分钟线（`sync_blocker` 的判断）：页面据此**不去发**那个必然 400 的请求。

    这里不打桩日历：只要真实日历认为最新交易日晚于 2026-09-30（永远成立），
    `stale` 就是 True，而 `syncable` 必须是 False。
    """
    got = web_api.freshness("sh.000001", "30", "2026-09-30")
    assert got["stale"] is True
    assert got["syncable"] is False
    assert "指数" in got["sync_hint"]


def test_a_fetchable_combination_is_syncable():
    got = web_api.freshness("600000", "day", "2026-10-08")
    assert got["syncable"] is True and got["sync_hint"] == ""
```

- [x] **1.2 跑，确认它红**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_api_freshness.py
```
期望：**5 个错 1 个过**。错的是 `AttributeError: module 'chanlun.web.api' has no attribute 'freshness'`；
唯一通过的是 `test_a_period_that_can_never_be_fetched_says_so`？不 —— 它也调 `freshness`，同样会
`AttributeError`。所以期望是 **6 个全部失败，全为 `AttributeError: ... has no attribute 'freshness'`**。
（若看到别的错因，先查测试本身。）

- [x] **1.3 实现**

`src/chanlun/web/api.py` 顶部导入区，在 `from ..data.baostock_source import to_bs_code` 之前插入：

```python
from ..calendar import calendar_for
```

> 已实测不成环：`chanlun/calendar.py` 顶层只导入标准库与 `.config`，`calendar_for` 内部才
> 局部导入 `data.markets`。这里必须是**顶层**导入，否则测试没法用 `monkeypatch` 打桩。

然后在 `sync_blocker()`（`web/api.py:947` 起）定义结束之后、`_task_view()` 之前插入：

```python
def freshness(code: str, period: str, as_of: str | None) -> dict[str, Any]:
    """本地数据是否落后于该市场的最新交易日，以及这个周期**能不能补**。

    **这是全项目唯一一份「要不要去补最新」的判断。** 前端只读结论
    （`stale` / `syncable`），不许自己比日期，也不许自己按号段猜指数：
    日历是按市场分派的（`calendar_for`：A 股真实交易日历、港/美宽松工作日历），
    前端既拿不到也不能复制；"能不能补"则复用 `sync_blocker` 那一份判断。

    `as_of` 只取**日期部分**来比：分钟周期的 `as_of` 带时间（实测 30 分钟线是
    `"2026-09-30 15:00"`），拿整串比会让它永远小于任何日期串、把每一只已最新的
    票都判成落后。

    `as_of` 为空时 `stale=False`：本地没有数据时"落后"这个判断无从谈起 ——
    宁可漏补一次，也不要让页面在没有依据时去发请求。此时仍然给出
    `expected_as_of`，好让 404 的提示能说一个具体日期。
    """
    expected = calendar_for(code).last_trading_day(dt.date.today()).isoformat()
    blocker = sync_blocker(code, period)
    return {
        "expected_as_of": expected,
        "stale": bool(as_of) and str(as_of)[:10] < expected,
        "syncable": blocker is None,
        "sync_hint": blocker or "",
    }
```

- [x] **1.4 跑，确认全绿**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_api_freshness.py
```
期望：`6 passed`。

- [ ] **1.5 提交**（批处理模式下跳过，见全局约束 8）

```bash
git add chanlun/tests/web/test_api_freshness.py chanlun/src/chanlun/web/api.py
git commit -m "feat(web): 新增按需补最新的唯一判据 freshness（D-49）"
```

---

## Task 2：把结论挂到 `/api/structure`、`/api/bars` 与 404 分支

**Files:**
- Modify: `src/chanlun/web/api.py`（`bars` handler、`structure` handler 两处）
- Modify: `tests/web/test_api_freshness.py`（追加集成测试）

- [x] **2.1 写失败测试**（追加到 `tests/web/test_api_freshness.py` 末尾）

```python
# ---------------- 端点：结论必须跟着结构一起发回去 ----------------
# `client` / `cfg` 夹具来自 `tests/web/conftest.py`（4 只样本日线，末根固定 2024-12-31）。


def test_structure_carries_the_verdict(client, monkeypatch):
    """页面不许自己做日期比较：`/api/structure` 必须把结论一起给出。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2025, 1, 2))
    )
    body = client.get("/api/structure", params={"code": "600000", "period": "day"}).json()
    assert body["as_of"] == "2024-12-31", "夹具日线的末根"
    assert body["expected_as_of"] == "2025-01-02"
    assert body["stale"] is True
    assert body["syncable"] is True


def test_structure_says_fresh_when_the_file_reaches_the_last_trading_day(client, monkeypatch):
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2024, 12, 31))
    )
    body = client.get("/api/structure", params={"code": "600000", "period": "day"}).json()
    assert body["stale"] is False


def test_bars_carries_the_same_verdict(client, monkeypatch):
    """两处口径必须一致，否则「图是最新的、顶栏说不是」这种自相矛盾迟早出现。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2025, 1, 2))
    )
    body = client.get("/api/bars", params={"code": "600000", "period": "day"}).json()
    assert body["expected_as_of"] == "2025-01-02"
    assert body["stale"] is True


def test_a_404_still_tells_which_day_is_the_latest(client, monkeypatch):
    """404 时本地没有 `as_of`，但"最新交易日是哪天"照样要给 —— 提示里得有具体日期。"""
    monkeypatch.setattr(
        web_api, "calendar_for", lambda code: _FakeCalendar(dt.date(2025, 1, 2))
    )
    resp = client.get("/api/structure", params={"code": "600000", "period": "30"})
    assert resp.status_code == 404
    body = resp.json()
    assert body["expected_as_of"] == "2025-01-02"
    assert body["syncable"] is True, "个股 30 分钟是可补的"
    assert body["detail"], "原有的 detail 不许丢"
```

- [x] **2.2 跑，确认它红**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_api_freshness.py
```
期望：**4 个错**，全是 `KeyError: 'stale'` / `KeyError: 'expected_as_of'`；Task 1 的 6 个仍然过。

- [x] **2.3 实现**

`/api/bars`（`web/api.py:686-690`）：**只追加一个键**，既有键一个不少、名字一个不改

```python
    return {
        "code": key, "period": period, "count": len(df), "bars": _bars_payload(df),
        "adjust": adj, "adjust_effective": effective, "adjust_note": note,
        "ma": ma_payload(df, periods), "ma_periods": list(periods),
        # 页面据此判断"要不要自己去补最新"（D-49）。末根取的是**读出来的这份数据**的
        # 末根（`_bars_payload` 用的也是 `r.ts`），不是磁盘上 parquet 的末根。
        **freshness(key, period, str(df["ts"].iloc[-1]) if len(df) else None),
    }
```

`/api/structure`（`web/api.py:714-724` 的 404 分支）：把 `sync_blocker` 换成 `freshness`
（后者内部就是调它），并多带一个 `expected_as_of`

```python
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        # 404 顺带回答「这个周期到底能不能补」：页面靠它决定给不给「同步这个周期」
        # 按钮，以及要不要自己去补一次。让后端判、而不是前端去猜号段。
        # `freshness(..., None)` 复用 `sync_blocker`，并额外给出最新交易日是哪天 ——
        # 本地一条都没有时，提示里至少要说得出一个具体日期。
        fresh = freshness(key, period, None)
        return JSONResponse(
            status_code=404,
            content={"detail": exc.detail, "syncable": fresh["syncable"],
                     "sync_hint": fresh["sync_hint"],
                     "expected_as_of": fresh["expected_as_of"]},
        )
```

`/api/structure` 成功分支（`web/api.py:731-735`）：同样**只追加一个键**

```python
    body.update({
        "code": key, "name": name, "period": period,
        "adjust": adj, "adjust_effective": view.effective, "adjust_note": view.note,
        "ma": ma_payload(view.bars, periods), "ma_periods": list(periods),
        # 结构已经算完了，顺便把"这份数据落不落后"告诉页面（D-49）。
        # `as_of` 来自结构快照本身（`structure_payload` 里的 `to_jsonable(view.snap)`），
        # 不是这里另造的第二个日期口径。
        **freshness(key, period, view.snap.as_of),
    })
```

> `view.snap.as_of` 是**权威来源**，不要用 `body.get("as_of")`：`body` 是
> `to_jsonable(view.snap)` 的产物，两者同源，但前者有类型、后者是 `dict`。
> 实测 `/api/structure` 的 `as_of` 日线是 `"2026-10-09"`、30 分钟线是
> `"2026-09-30 15:00"`，正是把日期部分单独比的理由。

- [x] **2.4 跑，确认全绿**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_api_freshness.py tests/web/test_api.py tests/web/test_api_sync.py tests/web/test_api_periods_watchlist.py tests/web/test_api_adjust_ma.py
```
期望：全绿。**特别确认既有 web 测试没有一个变红**（`/api/structure` 的既有键没动）。

- [ ] **2.5 提交**（批处理模式跳过）

```bash
git add chanlun/src/chanlun/web/api.py chanlun/tests/web/test_api_freshness.py
git commit -m "feat(web): /api/structure 与 /api/bars 带上 freshness 结论（D-49）"
```

---

## Task 3：`#sync-chip` 的 DOM 与样式（绝对定位、不占流、不动布局）

**Files:**
- Modify: `src/chanlun/web/static/index.html`（`<main>` 内 1 行）
- Modify: `src/chanlun/web/static/styles.css`（文件末尾追加）
- Create: `tests/web/test_frontend_auto_sync.py`

- [x] **3.1 写失败测试**

新建 `tests/web/test_frontend_auto_sync.py`：

```python
"""前端契约：换票/换指数时自动补最新（D-49）。

`app.js` 没有构建步骤、也没有前端测试框架，所以契约像
`tests/web/test_frontend_sync_button.py` 一样用**静态断言**守：咬住"规矩"而不是
"实现长相"——「判据来自后端」「同时只飞一个」「轮询要问发起时那一只」这些
断言在重构后仍然应该成立。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "chanlun" / "web" / "static"


def _html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _css() -> str:
    return (STATIC / "styles.css").read_text(encoding="utf-8")


def _fn_body(js: str, header: str) -> str:
    """抠出一个函数的函数体（按花括号配平）。找不到就报错。"""
    start = js.index(header)
    i = js.index("{", start)
    depth = 0
    for j in range(i, len(js)):
        if js[j] == "{":
            depth += 1
        elif js[j] == "}":
            depth -= 1
            if depth == 0:
                return js[i : j + 1]
    raise AssertionError(f"函数没配平: {header}")


def test_app_js_parses():
    node = shutil.which("node")
    if node is None:  # pragma: no cover - 本机有 node
        pytest.skip("没有 node")
    proc = subprocess.run(
        [node, "--check", str(STATIC / "app.js")], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_the_chip_lives_inside_main_not_in_the_body_grid():
    """`body` 网格行数必须等于 `body` 的直接子元素数（`styles.css:51-58`，
    既有测试 `test_body_grid_rows_cover_every_body_child` 守着）。chip 只能待在
    `<main class="stage">` 里 —— 那是个 flex 列，绝对定位的子元素不占格。"""
    html = _html()
    at = html.index('id="sync-chip"')
    assert html.index('<main class="stage">') < at < html.index("</main>")


def test_the_chip_does_not_take_up_flow():
    css = _css()
    at = css.index(".sync-chip {")
    block = css[at : css.index("}", at)]
    assert "position: absolute" in block, "占流的元素会把图表挤小"
    assert "pointer-events: none" in block, "平时不许挡住图上的交互"


def test_the_chip_is_a_separate_box_from_the_notice_panel():
    """`#notice` 是 `inset: 56px 24px 24px` 盖满图表的面板（`styles.css:343`），
    它表达的是"这个周期没有数据"。"有数据、正在后台补"必须另有一处，否则图被糊住。"""
    html = _html()
    assert 'id="sync-chip"' in html and 'id="notice"' in html
    assert "sync-chip" not in _fn_body(_js(), "function showNotice(")


def test_the_chip_is_hidden_by_default():
    html = _html()
    at = html.index('id="sync-chip"')
    assert "hidden" in html[max(0, at - 120) : at + 120]
```

- [x] **3.2 跑，确认它红**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_frontend_auto_sync.py
```
期望：**4 个错 1 个过**。错因 `ValueError: substring not found`（找不到 `id="sync-chip"`）。
通过的是 `test_app_js_parses`（此刻 `app.js` 还没改）。

- [x] **3.3 实现**

`index.html`：在 `<div id="notice" class="notice" hidden></div>` 与 `</main>` 之间插入

```html
      <div id="sync-chip" class="sync-chip" hidden role="status" aria-live="polite"></div>
```

`styles.css` 文件末尾追加：

```css
/* ---------------- 按需补最新的浮标（D-49） ----------------
   它和 `#notice` 是两件事：notice 是 `inset: 56px 24px 24px` 盖满图表的面板，
   说的是"这个周期没有数据"；这一条说的是"数据在这儿，我正在后台把它补到最新"，
   必须能**和图同时看见**。

   绝对定位是硬要求：`.stage` 是 flex 列，占流的子元素会把 `.chart` 挤矮，
   而顶栏"恒定 2 行 / 窄屏不跳高度"是既有硬约束（见本文件开头与 app.js 的注释）。
   `bottom` 而不是 `top`：`.stage` 顶部 56px 是工具条，浮标放下面才不会压住按钮。 */
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
  pointer-events: none;   /* 平时不吃鼠标事件：图上的十字线/缩放照常 */
}
.sync-chip.is-retry {
  border-left-color: var(--cinnabar);
  color: var(--rice);
  cursor: pointer;
  pointer-events: auto;   /* 只有"点这里重试"这一态才可点 */
}
```

- [x] **3.4 跑，确认全绿**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_frontend_auto_sync.py tests/web/test_frontend_watchlist.py tests/web/test_frontend_sync_button.py
```
期望：全绿 —— 尤其是 `test_body_grid_rows_cover_every_body_child` 仍然通过（chip 没进 body）。

- [ ] **3.5 提交**（批处理模式跳过）

```bash
git add chanlun/src/chanlun/web/static/index.html chanlun/src/chanlun/web/static/styles.css chanlun/tests/web/test_frontend_auto_sync.py
git commit -m "feat(web): 新增不占流的补数据浮标 #sync-chip（D-49）"
```

---

## Task 4：先把手动路径修好 —— `postSync` 抽取 + 轮询问「发起时那一只」

这一步先做，是因为按需同步要复用它；顺带修掉一个**既有**缺陷：`pollSync` 现在读的是
`state.code`/`state.period`（`app.js:497`），用户同步途中换票就会去问**新票**的状态，
后端对未知任务回 `idle`，`finishSync` 于是把一次没跑完的同步读成"同步完成：undefined 根"。

**Files:**
- Modify: `src/chanlun/web/static/app.js`（`syncThisPeriod` / `pollSync`）
- Modify: `tests/web/test_frontend_sync_button.py`（1 处断言重定向）
- Modify: `tests/web/test_frontend_auto_sync.py`（追加 1 个测试）

- [x] **4.1 写失败测试**

追加到 `tests/web/test_frontend_auto_sync.py`：

```python
def test_poll_asks_about_the_job_it_started_not_whatever_is_on_screen():
    """同步途中换票，轮询**不许**跟着换成新票：`/api/sync/status` 对未知任务回 `idle`，
    会被读成"同步成功、0 根"，而真正那次还在跑。"""
    body = _fn_body(_js(), "async function pollSync(")
    assert "job.code" in body and "job.period" in body
    assert "state.code" not in body and "state.period" not in body
```

同时把 `tests/web/test_frontend_sync_button.py:76-82` 那条改成（**这是本次唯一被改的既有断言**，
因为 POST 搬进了 `postSync`；契约本身一个字没变，仍然要求"POST 只排队、结果靠轮询、轮询有上限"）：

```python
def test_sync_button_posts_then_polls_instead_of_waiting():
    js = _js()
    post = _fn_body(js, "async function postSync(")
    assert '"/api/sync"' in post and 'method: "POST"' in post
    assert "rejected" in post, "要区分'后端拒绝'与'连不上' —— 后者必须留重试按钮"
    body = _fn_body(js, "async function syncThisPeriod()")
    assert "pollSync(" in body, "POST 只负责排队，结果靠轮询拿"
    assert "/api/sync/status" in js
    assert re.search(r"SYNC_POLL_MS\s*=\s*\d+", js), "轮询间隔必须是显式常量"
    assert re.search(r"SYNC_MAX_MS\s*=\s*\d+", js), "轮询必须有上限"
```

- [x] **4.2 跑，确认它红**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_frontend_auto_sync.py tests/web/test_frontend_sync_button.py
```
期望：新测试红在 `AssertionError`（`async function pollSync(` 找不到，因为当前签名是
`pollSync(started)`）；改过的那条也红在 `async function postSync(` 找不到。其余仍过。

- [x] **4.3 实现**

**(a) 在 `syncThisPeriod` 之前插入 `postSync`**（`app.js:457` 与 `:458` 之间）

```js
  // POST 只负责排队：首次拉 5 分钟线实测 149 秒，等不起。
  // **不抛异常** —— 调用方要的是"为什么没成"这个原因，不是一次栈回溯。
  // `rejected` 区分两类失败，它们的处置**不一样**，别合并：
  //   rejected=true  —— 后端明确拒绝（如"指数没有分钟线"），再点一百次结果一样 → 撤掉按钮；
  //   rejected=false —— 连不上/超时这类**暂时**失败 → 留着按钮让用户重试。
  async function postSync(code, period) {
    let resp;
    try {
      resp = await fetch("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, period }),
      });
    } catch (err) {
      return { rejected: false, detail: `连不上本地服务：${err}` };
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      return { rejected: true, detail: body.detail || `HTTP ${resp.status}` };
    }
    return { rejected: false, detail: "", body };
  }
```

**(b) `syncThisPeriod`**：把 `:458-492` 改成（POST 那段搬进 `postSync`，其余行为与文案**逐字保留**）

```js
  async function syncThisPeriod() {
    const btn = syncButton();
    const job = { code: state.code, period: state.period };
    const cn = PERIOD_CN[job.period] || job.period;
    const started = Date.now();
    const tick = () => {
      if (!btn) return;
      btn.disabled = true;
      btn.textContent = `正在同步${cn}… 已 ${Math.round((Date.now() - started) / 1000)}s`;
    };
    tick();
    syncNote(`正在向行情源拉取${cn}数据，首次可能要一两分钟，请不要关掉页面。`, false);

    const r = await postSync(job.code, job.period);
    if (r.rejected) {
      // 400 是**确定性拒绝**（例如"指数没有分钟线"）：再点一百次结果一样，
      // 所以把按钮撤掉，只留原因，不做"重试"这种假承诺。
      if (btn) btn.remove();
      return syncNote(`无法同步：${r.detail}`, true);
    }
    if (r.detail) return finishSync(null, r.detail);   // 暂时失败：沿用原有的"重试"路径

    const timer = setInterval(tick, 500);
    const status = await pollSync(started, job);
    clearInterval(timer);
    finishSync(status, null);
  }
```

**(c) `pollSync`**：`:494-509` 只改两处 —— 签名多一个 `job`、查询串改用它；
其余（`!resp.ok` 的判断、`state !== "running"` 的循环条件、超时文案）**一个字不动**

```js
  async function pollSync(started, job) {
    while (Date.now() - started < SYNC_MAX_MS) {
      await new Promise((r) => setTimeout(r, SYNC_POLL_MS));
      // ★ 必须问**发起时那一只**：这里原先读 `state.code`/`state.period`，
      //   用户中途换票就会去问新票的状态，后端对未知任务回 `idle`（`state !== "running"`），
      //   于是刚才那次还没跑完的同步被读成"成功、undefined 根"，还顺手 `load()` 重画。
      const qs = new URLSearchParams({ code: job.code, period: job.period });
      let resp;
      try {
        resp = await fetch(`/api/sync/status?${qs}`);
      } catch (err) {
        return { state: "error", error: `连不上本地服务：${err}` };
      }
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) return { state: "error", error: body.detail || `HTTP ${resp.status}` };
      if (body.state !== "running") return body;
    }
    return { state: "error", error: "等太久了。同步可能还在后台跑，刷新页面看看有没有数据。" };
  }
```

- [x] **4.4 跑，确认全绿**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/ && node --check src/chanlun/web/static/app.js
```
期望：全绿（`node --check` 无输出）。

- [ ] **4.5 提交**（批处理模式跳过）

```bash
git add chanlun/src/chanlun/web/static/app.js chanlun/tests/web/test_frontend_sync_button.py chanlun/tests/web/test_frontend_auto_sync.py
git commit -m "fix(web): 同步轮询改问发起时那一只票，POST 抽成 postSync（D-49 前置）"
```

---

## Task 5：按需同步 —— 判据、队列、会话内退避、chip 状态机

**Files:**
- Modify: `src/chanlun/web/static/app.js`（`load()` 两处挂点 + 新增一段）
- Modify: `tests/web/test_frontend_auto_sync.py`（追加）

- [x] **5.1 写失败测试**

追加到 `tests/web/test_frontend_auto_sync.py`：

```python
def test_the_verdict_comes_from_the_backend():
    """前端**不许**自己比日期，也不许自己按号段猜指数 —— 只用后端给的结论。"""
    body = _fn_body(_js(), "function maybeAutoSync(")
    assert "body.stale !== true" in body
    assert "body.syncable === false" in body
    assert body.index("stale") < body.index("requestAutoSync"), "先判落后再谈同步"
    assert "new Date" not in body and "toISOString" not in body, "前端不比日期"


def test_a_stale_structure_kicks_off_a_fetch_on_its_own():
    load = _fn_body(_js(), "async function load(")
    assert "maybeAutoSync(body)" in load, "结构到手后就该判断要不要补"


def test_an_empty_period_also_kicks_off_a_fetch():
    """「本地一条都没有」是最该自己去补的情形，不该只留给用户点按钮。"""
    load = _fn_body(_js(), "async function load(")
    at = load.index("404")
    assert "requestAutoSync(" in load[at:], "404 分支也要自动发起"


def test_auto_sync_is_attempted_at_most_once_per_session():
    """失败/零行之后不自动重试（退避），只留手动按钮；否则每次 `load()` 都去撞一次。"""
    body = _fn_body(_js(), "function runAutoSync(")
    assert "autoTried.add" in body
    assert body.index("autoTried.add") < body.index("finishAutoSync"), (
        "必须在收尾之前记：成功后 finishAutoSync 会 load() 回来，不先记就会自己触发自己"
    )
    req = _fn_body(_js(), "function requestAutoSync(")
    assert "autoTried.has" in req


def test_the_pending_slot_keeps_only_the_latest_job():
    req = _fn_body(_js(), "function requestAutoSync(")
    assert "autoBusy" in req, "同时只飞一个"
    assert "autoPending = job" in req, "待办槽是一个，不是队列：只保留最新一只"
    js = _js()
    assert "autoPending.push" not in js, "别把它做成队列：用户只想看手里这一只"
    assert "autoTried = new Set()" in js


def test_a_finished_fetch_only_redraws_if_we_are_still_looking_at_it():
    body = _fn_body(_js(), "function finishAutoSync(")
    assert "state.code === job.code" in body and "state.period === job.period" in body
    assert "load()" in body


def test_the_chip_says_what_it_is_doing_and_hides_itself_when_done():
    body = _fn_body(_js(), "function chipState(")
    assert "正在补最新数据" in body and "排队" in body and "已补到" in body
    assert "没补到新数据" in body and "重试" in body
    assert "is-retry" in body, "只有失败态可点"
    assert "setTimeout" in body, "成功提示要自己消失，别一直挂在那儿"
```

- [x] **5.2 跑，确认它红**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/test_frontend_auto_sync.py
```
期望：新增 7 条全红（`ValueError: substring not found`，找不到 `maybeAutoSync` / `runAutoSync` /
`requestAutoSync` / `finishAutoSync` / `chipState`）。Task 3、4 的仍过。

- [x] **5.3 实现**

**(a) `load()` 成功分支**：在 `app.js:252-256` 那两行统计与 `setStampDetail(...)` **之后**追加

```js
    // 数据画完了，再判断要不要去补最新：**先把本地这一份给用户看**，
    // 别让页面干等网络（补完会自己回来重画，见 runAutoSync）。
    maybeAutoSync(body);
```

**(b) `load()` 的 404 分支**：在 `showNotice(...)` **之前**插入（`app.js:217-225` 附近）

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

**(c) 新的一段**，插在 `finishSync` 之后（`app.js:530` 之后）：

```js
  // ------------------------------------------------- 按需补最新（D-49）
  // 进某只票/某个指数时，如果本地最后一根落在最新交易日之前，就自己去把它补回来 ——
  // 用户不必等 17:30 的定时任务（它只覆盖 universe 里的 A 股、也不覆盖指数），
  // 也不必先撞一个 404 才看见按钮。
  //
  // 判据**全部来自后端**（`/api/structure` 的 `stale` / `syncable` / `expected_as_of`）：
  // 日历按市场分派，前端既拿不到也不能复制。前端只做后端做不了的两件事：
  // **什么时候去问**、**问过几次**。
  const autoTried = new Set();   // key = `${code}|${period}`；进入即记，本次会话不再自动重试
  let autoBusy = null;           // 正在飞的那一个 job
  let autoPending = null;        // 待办槽：只留**最新**一只
  let chipTimer = null;

  function maybeAutoSync(body) {
    if (!body || body.stale !== true) return;   // 不落后：一个请求都不发
    if (body.syncable === false) return;        // 后端说这个周期根本补不了
    requestAutoSync({
      code: body.code,
      period: body.period,
      local: body.as_of,
      expected: body.expected_as_of,
    });
  }

  function requestAutoSync(job) {
    const key = `${job.code}|${job.period}`;
    if (autoTried.has(key)) return;             // 本次会话已经试过：只留手动
    if (autoBusy) {                             // 同时只飞一个；后来者挤掉待办槽
      autoPending = job;
      return chipState("queued", job);
    }
    runAutoSync(job);
  }

  function runAutoSync(job) {
    // ★ 先记再飞：成功后 finishAutoSync 会 load() 回来，不先记就会自己触发自己。
    autoTried.add(`${job.code}|${job.period}`);
    autoBusy = job;
    chipState("syncing", job);
    const started = Date.now();
    (async () => {
      let status = null;
      let error = null;
      try {
        const r = await postSync(job.code, job.period);
        if (r.detail) error = r.detail;              // 两类失败在这里都不继续轮询
        else status = await pollSync(started, job);
      } catch (err) {
        // 兜底：`autoBusy` 若永远卡住，后面的自动同步就全被堵死 —— 那等于把功能焊死。
        error = String(err);
      } finally {
        autoBusy = null;
      }
      finishAutoSync(job, status, error);
      const next = autoPending;
      autoPending = null;
      if (next) runAutoSync(next);
    })();
  }

  function finishAutoSync(job, status, error) {
    if (error || !status || status.state === "error") {
      return chipState("failed", job, error || status.error || "同步失败");
    }
    if (status.state === "skipped") {
      // 走到这里说明后端判定"水位已是最新"（`sync_one` 的短路，压根没联网），
      // 而前端刚才是按 parquet 末根判它落后的 —— 两者不一致只可能是水位与文件不同步。
      // **不许**照抄手动路径那句"行情源没有返回数据"：这次源根本没被问到。
      return chipState("failed", job, "本地记录已是最新，文件里还没有新数据");
    }
    chipState("done", job, null, status.end_ts);
    // 只在"还看着这一只"时才重画：后台补完的是别人，重画会把当前这只覆盖掉。
    if (state.code === job.code && state.period === job.period) load();
  }

  function chipState(mode, job, msg, endTs) {
    const el = $("#sync-chip");
    if (!el) return;
    clearTimeout(chipTimer);   // 任何新状态都取消"自动消失"，否则它会盖掉后面的状态
    el.hidden = false;
    el.classList.toggle("is-retry", mode === "failed");
    el.onclick = null;         // 只有失败态可点，别留上一次的句柄
    if (mode === "syncing") {
      el.textContent = `正在补最新数据：本地截至 ${job.local || "?"}，最新交易日 ${job.expected || "?"}…`;
    } else if (mode === "queued") {
      el.textContent = "排队等补最新数据…";
    } else if (mode === "done") {
      el.textContent = `已补到 ${endTs || job.expected || "最新交易日"}`;
      chipTimer = setTimeout(() => { el.hidden = true; }, 4000);
    } else {
      el.textContent = `没补到新数据：${msg} · 点这里重试`;
      el.onclick = () => {
        el.onclick = null;
        if (autoBusy) {
          autoPending = job;
          chipState("queued", job);
        } else {
          runAutoSync(job);
        }
      };
    }
  }
```

- [x] **5.4 跑，确认全绿**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/web/ && node --check src/chanlun/web/static/app.js
```
期望：全绿。

- [x] **5.5 手工回路自检（防死循环）**

重启服务后，用**最新**的票（`600000`，parquet 末根 2026-10-09）打开页面：
DevTools 里 `/api/sync` 的请求数必须是 **0**。再用一只落后的票（例如 `sh.000001` 指数，
末根 `2026-09-30`）刷新：应有且仅有 **1 个** `/api/sync`，补完后 `/api/structure` 重发一次，
此后不再有新的 `/api/sync`。

- [ ] **5.6 提交**（本会话改为全部任务完成后一次性提交，见 Task 7.4）

```bash
git add chanlun/src/chanlun/web/static/app.js chanlun/tests/web/test_frontend_auto_sync.py
git commit -m "feat(web): 换票/换指数自动补最新，会话内不重复自动重试（D-49）"
```

- [x] **5.7（实施中补充，原计划没有）浮标必须绑在"用户正在看的这一只"上**

原计划只写了"补完若用户仍在看这一只就重新 `load()`"，**没有定义"这一只"怎么比**，
也没写浮标在换票时要不要收。真浏览器实测（2026-10-10）暴露出这是同一根因的
**三处**缺陷，逐条按 TDD 补测、补实现、并做了变异证伪（数字见
`docs/evidence/2026-10-10-按需拉取验收.md`）：

1. `state.code` 是**用户输入原样**，服务端回的 `code` 才是**落库键**
   （`?code=sz.002898` → `002898`；指数反过来必须带前缀）。新增 `state.key`：
   `setCode` 先占位（`app.js:2016`），成功响应升级成 `body.code`（`app.js:252`），
   404/4xx 只能按请求原样记（`app.js:233`）。
2. 浮标加归属守卫：`chipIsAbout(job)`（`app.js:598`）+ `dropChip()`（`app.js:602`），
   `load()` 开头先收走不属于当前这只的浮标，`chipState` 拒绝写别人的进度。
   **连点击句柄一起清** —— 否则"点这里重试"点到的是上一只的 job。
3. `requestAutoSync` 的"本次会话已经试过"分支不许静默 `return`：正在飞 → `syncing`、
   排队中 → `queued`、否则 → 给一个**属于这一只**的可点重试浮标。
4. 顺带对齐同一根因的另外三处身份比较（自选按钮 `inWatchlist`、自选行与行业卡高亮、
   行业按钮文案），并加一条全站守卫：`app.js` 里不许有 `=== state.code` / `!== state.code`。

---

## Task 6：文档 —— D-49、§5 空白、CHANGELOG、证据

**Files:**
- Modify: `ARCHITECTURE.md`（§4 追加 `D-49`，§5 追加空白行）
- Modify: `CHANGELOG.md`（`### 新增` + `### 已知空白与未实现项`）
- Create: `docs/evidence/2026-10-10-按需拉取验收.md`
- Modify: `optimizer/tools/doc_refs_baseline.json`（由工具重算，不手改）

- [x] **6.1 `ARCHITECTURE.md` 的 `D-49`**

在 `D-48`（`ARCHITECTURE.md:921`）之后、§3 起始行（`:1032`）之前追加，**四段式，与 D-47/D-48 同构**：

````markdown
#### D-49 数据的「新不新」由后端一处判断，页面落后就自己去补

**决策。** 新增 `web/api.py` 的 `freshness(code, period, as_of)`，作为全项目唯一一份
「本地数据落不落后、这个周期能不能补」的判断，返回 `expected_as_of` / `stale` /
`syncable` / `sync_hint`。它按市场取日历（复用 `calendar.py:111` 的 `calendar_for`，
A 股真实交易日历、港美宽松工作日历），"能不能补"复用 `web/api.py:958` 的
`sync_blocker`。结论随 `/api/structure`、`/api/bars` 一起下发；`/api/structure` 的
404 分支也带上 `expected_as_of`。前端 `app.js` 读 `stale`，落后就入队补这一次
（同时只飞一个请求 + 一个待办槽，同一 `code|period` 本次会话只自动试一次），
补完若用户仍在看这一只就重新 `load()` 重画。

**"这一只"= 落库键，不是输入原样（实施中补，见 Task 5.7）。** 前端另立 `state.key`
（`app.js:121-127, 2016`）：`setCode` 先按用户写的形态占位，成功响应升级成服务端回的
`code`（`app.js:252`）。浮标的归属、补完要不要重画、自选按钮与高亮一律比 `state.key`；
比 `state.code` 实测出三处缺陷（浮标不显示、补完不重画、自选按钮永远显示"未加"）。
浮标换票即收（`dropChip`，`app.js:602`）；"本次会话已经补过一次"的那一只再进入，
给的是**它自己**的可点重试，不是静默。

**依据。** （1）定时任务只覆盖 `universe` 里的 5471 只 A 股，**从不**碰 7 个 A 股指数与
4 个港美自选，而这些恰恰是自选池里最常点开的；实测 2026-10-10（周六）7 个指数全部停在
`2026-09-30`。（2）`sync_one` 在本地水位已到最新交易日时**不联网**直接短路
（`data/sync.py:190-191`），所以"先问再补"的成本是一次本地判断，不是一次网络请求。
（3）`/api/structure` 的缓存键包含 `last_ts`（`web/api.py:408`），parquet 一旦写进新的一根，
缓存自然失效 —— "补完自动刷新"不需要任何失效逻辑。（4）日历与"能不能补"已有唯一实现，
若让前端自己比日期，会立刻分叉出第二份（港股在国庆开市、A 股休市，两者不同）。

**被否决的替代方案。** （a）新开 `/api/freshness` 接口：多一次往返，且页面在结构到手前
无法决定要不要补。（b）后端每日全量兜底：`python -m chanlun daily` 撞不上交易日就直接
退出（`__main__.py:852`），非交易日与指数都不覆盖，治不了这个病。（c）无条件
`POST /api/sync`：对已经最新的票白发请求，且会把 5 分钟线那种 149 秒的活也拖起来。
（d）把提示塞进 `#stamp`/`#stamp-detail`：那两处受"顶栏恒定 2 行"硬约束。
（e）复用 `#notice`：它是 `inset: 56px 24px 24px` 盖满图表的面板（`styles.css:343`），
用来表示"没有数据"，放"正在补"会把图糊住。（f）加 `config.toml` 总开关：这是默认该有的
行为，加开关只会让"为什么没自动补"变成一个新问题。

**后果 · 变更历史。** 页面打开落后品种时会自动产生一次 `/api/sync`，因此
`/api/sync` 的调用量从"用户手点"变成"每会话每品种至多一次"；失败的**不自动重试**，
只留可点的浮标（`autoTried` 会话内去重）。新增浮标 `#sync-chip` **不占文档流**
（`styles.css:51-58` 的 body 网格行数约束、以及顶栏恒定 2 行约束都不受影响）。
未覆盖：定时任务本身仍只同步 `universe`、仍无历史空洞检测（见 §5）。`chan/` 一行未动
⇒ **不是判据变更**。变更历史：2026-10-10 新增。
````

> 行号引用必须**改完文件后**核对一遍（插入 `D-49` 会让 `ARCHITECTURE.md:921` 之后的
> 行号全部下移 —— 上面引的 `:1032`/`:2908` 都是**改动前**的位置，请按 `ARCHITECTURE.md`
> 里既有条目的写法（引代码行，不引同文档其他段落的行）核对，宁可少引也不要引错）。

- [x] **6.2 `ARCHITECTURE.md` §5 追加空白行**

在 §5（`:2908` 起，现 `:2938-2941` 是既有空白行）之后追加三行，格式照抄既有行：

- 定时任务只同步 `universe`（5471 只 A 股），指数与港美自选从不被它刷新；按需补最新只在
  **用户打开页面时**发生，没人打开就仍然停在旧数据。
- 非交易日无法补跑：`python -m chanlun daily` 在非交易日直接退出（`__main__.py:852`），
  周末/长假期间页面只能看到假期前最后一根。
- parquet 末根与 `sync_state` 水位理论上可能不一致（实测 5638 条水位里只有 1 条不一致），
  此时按需同步会拿到 `skipped`，浮标提示"本地记录已是最新，文件里还没有新数据"。
- 按需同步的频次没有节流：同一会话内每只票至多自动一次，但**换 100 只票就是 100 次请求**，
  是否会触发 baostock 限流/拉黑未经验证（2026-10-09 有过一次 16 只票的黑名单误伤）。

- [x] **6.3 `CHANGELOG.md`**

`[Unreleased]` 的 `### 新增` 下追加：

```markdown
- **按需拉取（换票即补最新，D-49）**：打开某只票/某个指数时，若本地最后一根早于该市场的
  最新交易日，页面自动补这个周期，并在图表右下角的浮标里报进度；补完自动重画。判断
  「落不落后、能不能补」由后端 `freshness` 一处给出（按市场选日历），前端不再需要自己比日期。
  同时修掉一个既有缺陷：同步途中的轮询原先读的是当前页面的代码，换票后会把没跑完的同步
  读成「成功、0 根」。
```

`### 已知空白与未实现项` 下追加（与 §5 同文，两边必须一致）：

```markdown
- 定时任务只同步 `universe`（5471 只 A 股），指数与港美自选从不被它刷新；按需补最新只在
  用户打开页面时发生。
- 非交易日无法补跑（`daily` 撞不上交易日即退出）；周末/长假期间页面只能看到假期前最后一根。
- 按需同步没有频次节流（同会话每只票至多一次自动尝试，但换 100 只票就是 100 次请求），
  是否会触发数据源限流或拉黑未经验证。
```

- [x] **6.4 证据文档** `docs/evidence/2026-10-10-按需拉取验收.md`

必须写出**可以失败**的测量，包含三节：

1. **改动前的实测事实**（已测到的原话与数字，不许"大概是"）：5471 只 universe 里 `2026-10-08`
   收尾 4955 只、`2026-10-09` 268 只；7 个 A 股指数全部停在 `2026-09-30`；
   `hk.hsi`/`hk.hstech` `2026-10-05`、`us.dji`/`us.ixic` `2026-10-02`。
2. **判据的测量**：`freshness` 的两组打桩日历下的返回值；`/api/structure` 的 `as_of`
   在日线是 `"2026-10-09"`、30 分钟线是 `"2026-09-30 15:00"`（这就是必须只比日期部分的证据）。
3. **端到端的测量**（Task 7 填）：真浏览器 DOM dump 里，落后品种的浮标文案与
   `/api/sync` 的请求次数（**必须给出数字**，并给出"已最新的票 0 次"的对照组）。

- [x] **6.5 版本号：本次不涨**

设计文档 §6.3 写的是「本次不发版，条目留在 `[Unreleased]`」，**照它做**。`pyproject.toml`
一个字不动，`CHANGELOG.md` 的条目留在 `[Unreleased]`。

理由（不是偷懒）：`ARCHITECTURE.md:2933` 那行空白已经登记过这笔债 ——
`0.3.0` 起五个版本段全部堆在 `[Unreleased]`，**根因正是「涨了版本号却没搬条目」**。
本次再涨一次而不搬，就是给这笔债添第六段；而搬条目要把 1400 多行的 `[Unreleased]`
按版本分派，是已明确登记的**独立任务**（"补它要一次性把 `[Unreleased]` 的条目分派到
各版本号，属独立任务"），不塞进这次改动里。
语义上本次是 **MINOR**（向后兼容的新功能），将来分派版本段落时按 MINOR 归位即可 ——
提交信息里把这句话写清楚，别让下一个人以为漏了。

> 顺带自查：既有空白 F-11（`AGENTS.md` §2 还写着「当前基线是 0.2.0」）与本次无关，
> **不要**顺手改它，它有自己的登记条目。

- [x] **6.6 跑行号护栏并更新基线**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py && echo GUARD_OK
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py --update-baseline
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q tests/optimizer/test_check_doc_line_refs.py
```
期望：先 `GUARD_OK`（若报"引用行内容指纹不符"，说明引的行内容变了，先核对再更新基线）；
更新后基线条目数应从 357 增加；护栏测试全绿。

> 引用既有代码行时若其中含 `path:NNN` 形状的字符串，必须写成 `` `path` 第 NNN 行 ``（本次踩过：
> §5 里引 `tests/optimizer/test_check_doc_line_refs.py:481` 会被解析成一条活引用）。

- [ ] **6.7 提交**（批处理模式跳过）

```bash
git add chanlun/ARCHITECTURE.md chanlun/CHANGELOG.md \
        chanlun/docs/evidence/2026-10-10-按需拉取验收.md \
        chanlun/optimizer/tools/doc_refs_baseline.json
git commit -m "docs: 记录按需拉取 D-49 并登记残留空白（条目留 [Unreleased]）"
```

---

## Task 7：真浏览器验收、全量测试、一次性提交

**Files:**
- Modify: `docs/evidence/2026-10-10-按需拉取验收.md`（第 3 节填实测数字）

- [x] **7.1 全量测试**

```bash
PYTHONPATH=src ../.venv-chanlun/bin/pytest -q
```
期望：以 `0 failed` 结尾（当前基线是 `1219 passed, 12 deselected`，本次应增加约 15 条），
**把最后一行原话抄进提交信息**。

- [x] **7.2 重启服务并真浏览器验收**

```bash
pkill -f "chanlun serve" ; sleep 1
cd /Users/zzz/workspace/chanlun && PYTHONPATH=src PYTHONUNBUFFERED=1 nohup ../.venv-chanlun/bin/python -m chanlun serve > /tmp/serve.log 2>&1 &
sleep 3 ; curl -s http://127.0.0.1:8888/api/version
```
然后用**真浏览器**（不是 curl）打开 `http://127.0.0.1:8888/?code=sh.000001&period=day`，
dump DOM 并核对四件事（把结果抄进证据文档）：

1. `#sync-chip` 的 `textContent` 在补的过程中确实是「正在补最新数据：本地截至 2026-09-30，
   最新交易日 …」；
2. 补完（或跳过）之后，`#api/structure` 的 `as_of` 从 `2026-09-30` 前进（若源确实补到了）；
3. `#sync-chip` **不是** `#notice` 的子元素，且 `.notice` 的 `display` 仍是 `none`；
4. 打开一只已最新的票（`600000`），`/api/sync` 请求数为 **0**。

**布局不回归**：用 `getBoundingClientRect()` 对比改动前后
`#chart` 与顶栏（`#stamp` 所在行）的高度，两组数字必须**逐字相同**；并确认窄屏
（例如 1280px）下顶栏仍是 2 行、高度不跳。

- [x] **7.3 更新证据文档并做最终自查**

- 证据文档第 3 节填入 7.2 的实测数字与命令；
- 自查：`grep -n "TODO\|FIXME\|待填" docs/evidence/2026-10-10-按需拉取验收.md` 必须无输出；
- 回收：确认没有遗留的 `chanlun sync` 进程（`pgrep -fl "chanlun sync"` 无输出）。

- [ ] **7.4 一次性提交**

```bash
cd /Users/zzz/workspace && git status --short && git diff --stat
git add chanlun/src/chanlun/web/api.py \
        chanlun/src/chanlun/web/static/app.js \
        chanlun/src/chanlun/web/static/index.html \
        chanlun/src/chanlun/web/static/styles.css \
        chanlun/tests/web/test_api_freshness.py \
        chanlun/tests/web/test_frontend_auto_sync.py \
        chanlun/tests/web/test_frontend_sync_button.py \
        chanlun/ARCHITECTURE.md chanlun/CHANGELOG.md \
        chanlun/docs/evidence/2026-10-10-按需拉取验收.md \
        chanlun/docs/superpowers/specs/2026-10-10-on-demand-sync-design.md \
        chanlun/docs/superpowers/plans/2026-10-10-on-demand-sync.md \
        chanlun/optimizer/tools/doc_refs_baseline.json
git commit -F - <<'MSG'
feat(web): 打开某只票就自动补到最新（D-49）

问题：定时任务只覆盖 universe 里的 5471 只 A 股，指数与港美自选从不被刷新；
2026-10-10（周六）实测 7 个 A 股指数全部停在 2026-09-30，hk/us 自选停在 10-05/10-02。
用户要的不是全市场补跑，而是"我点开哪一只，哪一只就是最新的"。

做法：后端新增唯一判据 freshness(code, period, as_of)，按市场取日历
（复用 calendar_for），"能不能补"复用 sync_blocker，结论随 /api/structure 与
/api/bars 下发（404 分支也带上 expected_as_of）。前端读 stale，落后就入队补这一次：
同时只飞一个 + 一个待办槽，同一 code|period 本次会话只自动试一次（失败不自动重试，
只留可点的浮标）。补完若用户仍在看这一只就 load() —— 缓存键含 last_ts
（web/api.py:408），新的一根落盘后自然失效，不需要额外失效逻辑。

顺带修掉既有缺陷：同步途中的轮询原先读 state.code/state.period，用户换票后会去问
新票的状态，后端对未知任务回 idle，于是把没跑完的同步读成"成功、0 根"。

不动布局：浮标 #sync-chip 绝对定位挂在 .stage 内，不占流，body 网格行数不变
（既有测试 test_body_grid_rows_cover_every_body_child 仍过），顶栏恒定 2 行不变。

全量测试：<抄 7.1 的最后一行原话>
版本影响：本次不涨版本号，条目留 [Unreleased]。语义上属 MINOR（新增判据与前端行为）；
        0.3.0 起的版本段落分派是 ARCHITECTURE.md §5 已登记的独立任务，不在本次做。
chan/ 一行未动 ⇒ 不是判据变更。
MSG
```
**不许**写 `Co-Authored-By`。提交后 `git show --stat HEAD` 核对文件清单与预期一致。

---

## 自查（写完计划后逐条过）

- **规格覆盖**：设计文档 §4.1（`freshness`）→ Task 1/2；§4.2（触发、队列、反馈）→ Task 3/4/5；
  §4.3（会话内退避）→ Task 5 的 `autoTried`；§4.4（布局约束）→ Task 3 的三个断言 + 全局约束 4；
  §5（验收）→ Task 7；§6（改动面）→ 文件结构表；§7（残留）→ Task 6.2/6.3。**无遗漏。**
- **无占位符**：每个 Task 都给了可直接粘的完整代码/文案与准确命令。
- **类型一致性**：`freshness` 返回的 4 个键在 Task 1（实现）、Task 2（端点）、Task 5（前端读取）
  三处名字一致（`expected_as_of` / `stale` / `syncable` / `sync_hint`）；`postSync` 返回的
  `{rejected, detail, body}` 在 Task 4（定义）与 Task 5（`if (r.detail)`）一致；
  `pollSync(started, job)` 与 `job.code`/`job.period` 在 Task 4/5 一致。
- **被引代码逐字核对过**：Task 2.3 的三段 `api.py` 代码是 `read` 出来的原文
  （`api.py:686-690`、`:714-724`、`:731-735`），Task 4.3 的 JS 是 `app.js:458-492`、
  `:494-509` 的原文 —— 计划里没有凭记忆编的代码。
- **能失败**：每个 Task 的"跑，确认它红"都写明了期望的红与错因。
