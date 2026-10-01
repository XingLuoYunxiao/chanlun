# 自选股点不动：30分/5分 下整栏都是「没数据」（2026-10-01）

## 0. 一句话

自选行的「换票」绑定被写在 `if (!it.missing)` **里面**。自选池现在全是 7 个大盘指数，
而 baostock 对指数不返回分钟线 —— 所以一切到 30分/5分，**每一行都是 `missing`**，
整栏的点击就被一起关掉了：点下去页面纹丝不动，看起来像「看不了自选股、不跳 K 线」。

修法：换票的绑定**不看 `missing`**；点过去之后按那个周期的真实情况提示 ——
能补就给「同步这个周期」按钮，补不了（指数 + 分钟）就**说清为什么、并且不给按钮**。

## 1. 复现（改之前，真实浏览器）

用同源探针（`index.html` + 一段脚本，`/static/__probe_click.html`）在
`http://127.0.0.1:8888/static/__probe_click.html?period=30` 上点自选栏**第 3 行**。
点第 3 行而不是第 1 行，是因为首屏本来就开在自选第 1 行（`initialCodePinned`），
点第 1 行看不出区别。

| 周期 | 点第 3 行 `399006` 之后 | 结论 |
| --- | --- | --- |
| `day` | 输入框 `sh.000001 → 399006`，印章 `399006 创业板指 · 日线 · 截至 2026-09-30` | 能跳 |
| `30` | 输入框 `sh.000001 → sh.000001`，第 3 行 `is-current=false`，通知还是 `sh.000001` 的那条 | **点了没反应** |

`30` 那一行不是「慢」，是**根本没发出请求**：`finishWatchRow` 里
`if (!it.missing) { … row.addEventListener("click", open) … }`，
而 `/api/watchlist` 在 30 分下给 7 个指数全部标了 `missing: true`。

## 2. 根因

两个决定各自都合理，叠在一起才出问题：

1. `missing` 的语义是「**这个周期**本地没数据」（`api.py` 里 `store.exists(code, base_period(period))`）；
2. 于是当初把「点一下换票」绑在 `!missing` 上，想着「没数据点开也是空图」。

但自选池从 Request B 起换成了 7 个指数，而指数**在分钟周期上永远 `missing`**
（baostock 不返回指数分钟线，本地通达信整包只有 `lday`）。结果：切到 30分/5分
就整栏点不动，而这恰恰是用户最想看的两个周期之一。

顺带暴露的第二个毛病：即使点得动，404 分支也**不更新左栏高亮**（`load()` 在
`showNotice` 处提前返回，没走到 `renderWatch()`），会出现「输入框写着 399006、
左栏还高亮着 sh.000001」—— 高亮等于一句假话。

## 3. 改了什么

| # | 位置 | 改动 |
| --- | --- | --- |
| 1 | `web/static/app.js` `finishWatchRow` | 换票绑定移出 `if (!it.missing)`；点击 + Enter/Space 一律可用 |
| 2 | `web/static/styles.css` `.watch-row.is-missing` | 去掉 `cursor: default`（没数据 ≠ 不能点） |
| 3 | `web/api.py` 新增 `sync_blocker(code, period)` | 「这个周期根本补不了」的**唯一**口径来源：`periods_mod.base_period(period) != "day"` 且 `markets.is_index(to_bs_code(code))` → 一句话原因，否则 `None` |
| 4 | `web/api.py` `/api/sync` | 原来的内联判断改成调 `sync_blocker`（行为不变，文本同源） |
| 5 | `web/api.py` `_period_frame` 404 | 「补不了」时详情写「…而且补不了：<原因>」，**不再**出现「请先同步」那条注定空跑的命令 |
| 6 | `web/api.py` `/api/structure` | 捕获 404，改回 `JSONResponse`，多带 `syncable` / `sync_hint`；`response_model=None` |
| 7 | `web/static/app.js` `load()` 404 分支 | `canSync = resp.status === 404 && body.syncable !== false`；并补 `renderWatch()` 挪高亮 |

口径只有一处：页面说「能补」和接口说「补不了」现在读的是同一个 `sync_blocker`，
有测试盯着这两句话必须一致。

## 4. 验收

### 4.1 测试

全量：**804 passed, 12 deselected, 1 warning in 17.62s**（改之前 796；+8）。

| 文件 | 新增 | 盯的是 |
| --- | --- | --- |
| `tests/web/test_api_sync.py` | 4 | 指数+分钟 404 带 `syncable=false` + 原因、且详情里没有「请先同步」；股票 404 仍然 `syncable=true`；指数**日线**也 `syncable=true`；404 的原因与 `POST /api/sync` 的 400 详情同源 |
| `tests/web/test_frontend_watchlist.py` | 3 | 换票绑定不在 `if (!it.missing)` 里面；`.watch-row.is-missing` 不再是 `cursor: default`；404 分支也要调 `renderWatch()` |
| `tests/web/test_frontend_sync_button.py` | 1 | 按钮的有无听 `body.syncable`，不听前端自己猜号段 |

新增的 8 条**先红后绿**（改之前：`KeyError: 'syncable'` ×4、`assert "syncable" in load` ×1、
`assert "setCode" not in guarded` ×1、`cursor: default` ×1、`renderWatch()` ×1）。

### 4.2 接口（真机 8888）

```
GET /api/structure?code=sh.000001&period=30
  → 404 {"detail":"sh.000001 的 30 周期没有本地数据，而且补不了：sh.000001 是指数，…",
          "syncable":false, "sync_hint":"sh.000001 是指数，…"}
GET /api/structure?code=600036&period=30
  → 404 {"detail":"600036 的 30 周期没有本地数据。请先同步：…", "syncable":true, "sync_hint":""}
```

### 4.3 真实浏览器（DOM 读出来的，不是看截图小字）

| 场景 | 输入框 | 通知 | 同步按钮 | 画布 |
| --- | --- | --- | --- | --- |
| `?period=30` 点自选第 3 行 | `399006` | 「…没有本地数据，而且补不了：399006 是指数…」 | **无** | 0 |
| `?period=day` 点自选第 3 行 | `399006` | 隐藏 | — | 1（印章 `399006 创业板指 · 日线`） |
| `?code=600036&period=30` | `600036` | 「…请先同步：…」 | **有**（「同步这个周期」） | 0 |
| `?code=sh.000001&period=30` | `sh.000001` | 「…而且补不了：…」 | **无** | 0 |
| `?code=600000&period=30` | `600000` | 隐藏 | — | 1 |

左栏高亮（点完**重新查** DOM，因为 `renderWatch()` 会换掉节点）：
`period=day` → `is-current=399006`；`period=30` → `is-current=399006`（改之前是 `sh.000001`）。

## 5. 这一轮**没有**证明的东西

1. 没在浏览器里走完「点 missing 行 → 点同步按钮 → 图自己画出来」。按钮到同步这一段
   上一轮已经端到端证过（真实点击把 `301716`/`301686` 的 30 分落了库），这一轮只证到
   「点行 → 出现按钮」，两段是拼起来的，不是一次连续点击。
2. 只验了 30分。5分 走的是同一段代码（同一个 `sync_blocker`、同一个 404 分支），
   没有单独跑一遍 5分 的真实浏览器用例。
3. 「指数分钟线拿不到」这条事实来自 baostock 实测（`sh.000001`/`sh.000300`/`sh.000688`
   的 30 分都是 0 行），不是从 baostock 文档抄的。将来它要是开始提供指数分钟线，
   `sync_blocker` 得跟着改 —— 现在页面会把这条路说死。
4. 没动键盘可达性的其余部分（Tab 顺序、`aria-*`）。

## 6. 复现命令

```bash
cd /Users/zzz/workspace/chanlun
../.venv-chanlun/bin/pytest -q                                  # 804 passed
curl -s "http://127.0.0.1:8888/api/structure?code=sh.000001&period=30" | python3 -m json.tool
# 浏览器：http://127.0.0.1:8888/?period=30 点左栏第 3 行
```

`src/chanlun/web/api.py` 改了要**重启** `python -m chanlun serve`（没有 reload）；
`app.js`/`styles.css` 是从磁盘发的，刷新即可。
