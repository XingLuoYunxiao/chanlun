# Task 30–32 取证：看盘页「同步这个周期」按钮（2026-10-01）

用户原话（Request C）：

> 为什么有的这个票，股票它的这个30分钟、5分钟还是没有？比如说洲际游戏，你这个数据有没有下载完整呢？

查完的结论是：**不是下载不完整**。分钟周期只同步自选池（F5 口径），而自选池在 Request B 之后
变成了 7 个大盘指数 —— baostock 对指数不返回分钟线。所以"有的票没有分钟数据"是必然的，
不是偶发。设计口用户选定：**「页面加个「同步这个周期」按钮」**（不是自动放宽 F5 口径），
并选定实现方式 **「按这个设计开工（推荐）」**：异步 + 轮询、与命令行共用一份 `sync_one`、
**指数 + 分钟直接拒绝**。

## 0. 这一轮定的三件事

| 问题 | 用户的选择 |
| --- | --- |
| "洲际游戏"是哪只票 | **ST洲际 600759**（本地品种表里名字带"洲际"的只有它） |
| 分钟数据怎么补 | **页面加「同步这个周期」按钮**（按需，不自动扫全市场） |
| 按钮怎么实现 | **异步 + 轮询**；与 `python -m chanlun sync` 共用一份取数实现；指数+分钟直接拒绝 |

## 1. 按钮存在的理由：三条实测事实

| 事实 | 实测 |
| --- | --- |
| 600759 的分钟线**早就有**，只是很短 | 同步前 30 分 **336 根**、5 分 **2016 根**（起点晚，不是没有） |
| 手动补一次就补齐 | 30 分 **13072 根**（2020-01-02 → 2026-09-30）、5 分 **78432 根** |
| 指数拿不到分钟线 | `sh.000001` / `sh.000300` / `sh.000688` 的 30 分、5 分一律 **0 行**（重登重试后仍是 0） |

所以按钮要解决的是"**这只票这个周期本地没有，我现在就要看**"，而不是"修复一个下载 bug"。

## 2. 为什么按钮必须异步

真机实测（本机、单只票、全量）：

| 周期 | 根数 | 耗时 |
| --- | --- | --- |
| 30 分 | 13088 根 | **29.6 秒** |
| 5 分 | 78432 根 | **149.4 秒** |

同步等待会让浏览器先超时。所以 `POST /api/sync` **只排队**，立刻返回 202；真正的取数在后台线程里，
页面按 2 秒一轮询 `/api/sync/status`。

## 3. 实现

### 3.1 一份取数实现：`src/chanlun/data/sync.py`

命令行和页面共用 `sync_one(conn, cfg, code, period, *, full, since, cal, fetch)`，返回
`SyncOneResult(code, period, status, rows, start_ts, end_ts, adjust, error)`（`status ∈ ok/skipped/failed`）。
要点：

- **指数强制不复权**：`adjust_for()` 对 `markets.is_index` 返回 `"3"`，股票才用配置里的 `"2"`（前复权）。
  这条口径原先只写在 `seed-watchlist` 里，`sync` 命令是漏的 —— 现在两处合成一处。
- **整个主体包在 `try/except`**（连代码解析也进去）：后台线程抛异常会让按钮永远停在"正在同步"。
- 失败时**保留上一次的 `start_ts/end_ts/rows`**，只更新 `error`：一次断线不该把"本地有多少数据"擦掉。
- 源返回 0 行记 `skipped`（不是 failed）：真空区间是合法的。
- `src/chanlun/__main__.py` 的 `_sync_period` 改成调 `sync_mod.sync_one(...)`（`ok/failed/skipped`
  三态计数与 `first_error` 原样保留），`_start_for` 变成一行转发，原地那份重复的起点/复权判断删掉；
  `FULL_START` 收敛到 `sync_mod.FULL_START` 一处。**`_record` 保留**：它现在只服务
  `seed-watchlist`（那条命令硬编码不复权 `"3"`，且故意全量重拉），不是死代码。

### 3.2 两个接口（`src/chanlun/web/api.py`）

| 接口 | 行为 |
| --- | --- |
| `POST /api/sync` `{code, period}` | **202**，登记任务并起后台线程；同一 `(code, period)` 正在跑时**幂等**（连点不叠加） |
| `GET /api/sync/status?code&period` | `{state: idle/running/done/skipped/error, rows, elapsed, error, started_at, finished_at, start_ts, end_ts}` |

- **全局串行**：baostock 是单会话 socket 协议，两个线程同时拉会互相踩，所以取数走一把全局锁。
- **派生周期映射到日线**：`week`/`month` 是本地聚合的，没有自己的 parquet；请求 `week` → 同步 `day`，
  响应里 `requested: "week"` 保留用户请求的原周期。
- **指数 + 分钟 → 400**，且**在取数之前**拒绝：否则要等 30 秒再看 0 行。
- 周期大小写**严格**（与 `/api/bars` 一致），不认识的周期 400。

### 3.3 前端（`static/app.js` + `styles.css`）

- `/api/structure` 返回 **404**（这个周期本地真没有）时，提示框里多一个按钮「**同步这个周期**」；
  400（请求本身错）不给按钮。
- 点下去：按钮立刻禁用并显示「正在同步30分… 已 Ns」，下方一行说明"首次可能要一两分钟"。
- 轮询间隔 `SYNC_POLL_MS = 2000`，上限 `SYNC_MAX_MS = 15 分钟`（无限转圈会让人以为永远同步不完）。
- `done` → 说清拿回多少根，然后 `load()` **重画**；`skipped` → "行情源没有返回这个周期的数据"；
  `error` → 按钮变「重试」+ 失败原因；`POST` 直接 400（确定性拒绝）→ **把按钮撤掉**，只留原因。

## 4. 验收

### 4.1 测试：755 → **796 passed, 12 deselected**（17.88s）

| 文件 | 数量 | 钉住的东西 |
| --- | --- | --- |
| `tests/data/test_sync.py` | 14 | 起点/增量/`--since` 只作用于空库、指数强制不复权、失败保留旧行数、空数据 = skipped |
| `tests/web/test_api_sync.py` | 18 | 202 + 后台跑、连点幂等、跑完能重跑、周月映射到日线、指数+分钟 400、状态四态 |
| `tests/web/test_frontend_sync_button.py` | 8 | 404 才给按钮、POST 后轮询而非等待、跑时禁用、400 不给重试、成功要重画、`node --check` |
| `tests/test_cli.py` | 21（+1） | 普通 `sync` 命令拉到指数也必须写 `"3"` |

新增的 CLI 那条**做过变异验证**：把 `adjust_for` 改成永远返回配置值，会同时打红
`tests/test_cli.py::test_sync_writes_raw_adjust_for_an_index`、
`tests/data/test_sync.py::test_adjust_for_forces_raw_on_an_index`、
`tests/data/test_sync.py::test_sync_one_records_raw_adjust_for_an_index`（3 failed）——
即"合到一处"这件事真的被量具看着。

### 4.2 真机 HTTP（不是桩）

```
POST /api/sync {"code":"600000","period":"30"}            → 202 running
GET  /api/sync/status?code=600000&period=30               → done rows=13088 elapsed=29.6s
                                                            2020-01-02 10:00 → 2026-09-30 15:00
GET  /api/structure?code=600000&period=30&adjust=qfq      → 200：窗口 1200 根
                                                            全史 770 笔 / 95 段 / 18 中枢 / 21 买卖点
POST /api/sync {"code":"sh.000001","period":"30"}         → 400「…是指数，baostock 不提供指数的分钟线…」
```

落库的 `sync_state`（三条都由页面/接口写入，`adjust='2'` 与 `_stored_adjust` 口径一致）：

```
('600000','30',13088,'2','2020-01-02 10:00','2026-09-30 15:00', None)
('301716','30',   16,'2','2026-09-29 10:00','2026-09-30 15:00', None)
('301686','30',   48,'2','2026-09-22 10:00','2026-09-30 15:00', None)
```

命令行的真实路径也复跑过（重构之后）：`python -m chanlun sync --period 30 --codes 600759`
→ **跳过 1（已是最新）**，0.77 秒。

### 4.3 真实浏览器（Chrome headless，读的是 `--dump-dom` 的文本，不是截图小字）

**① 404 页面确实有按钮**（`?code=000001&period=30`，DOM 原文）：

```html
<h3>没有这只票的本地数据</h3>
<p>000001 的 30 周期没有本地数据。请先同步：python -m chanlun sync --period 30 --codes 000001</p>
<button type="button" class="notice-action">同步这个周期</button>
```

**② 正常页面没有这个按钮**（`?code=600000&period=day`）：`notice-action` 不存在、`#notice` 已隐藏、
canvas 1 个、图注 `600000 浦发银行 · 日线 · 截至 2026-09-30`。

**③ 真的点了按钮**（临时同源点击台，验证完已删除）：

- 指数 + 分钟（`sh.000001` 30 分）：
  `BTN[同步这个周期] || AFTER[… 无法同步：sh.000001 是指数，baostock 不提供指数的分钟线，同步这个周期只会空跑。…] || 按钮还在=false`
  —— 点击 → POST → 400 → 原因上屏 → **按钮撤掉**，全链路通。
- 刚上市的票（`301716` 30 分，本地无 30 分文件）：页面进入
  `正在同步30分… 已 4s` + `正在向行情源拉取30分数据…`，按钮禁用；服务端记到
  `started_at 20:21:18 → done rows=16 elapsed=0.7s`，parquet 落盘
  （`data/30/sz/301716.parquet`，5144 字节）。`301686` 同样一次点击拿到 48 根。

界面证据（重启后的截图）：
`docs/evidence/2026-10-01-sync-period-404.png`（提示框里的按钮）、
`docs/evidence/2026-10-01-sync-period-30m.png`（按钮同步出来的 600000 30 分图：
线段 4 / 中枢 1 / 买卖点 1，全史 95 段 / 18 中枢 / 21 买卖点）。

## 5. 这一轮**没有**证明的东西

1. **没有在浏览器里亲眼看到"正在同步 → 图自己画出来"那一次翻转**。headless 的
   `--virtual-time-budget` 会把 `setTimeout` 快进，2 秒一轮的轮询在墙钟时间里只跑了一两秒，
   而服务端真拉一只票要 0.7～30 秒，所以抓到的 DOM 停在"正在同步"。已验证的是：
   点击真的发起了同步并落盘（§4.3 ③）、`done → load()` 那一步由单测钉住、
   以及**同步完成之后重新打开同一页，图确实画出来了**（§4.3 ② 与 30 分截图）。
2. **没有进度条、没有断点续传**：卡在 149 秒的 5 分钟线上时，页面只能说"已 Ns"。
   这是设计口明确不做的（YAGNI）。
3. **指数分钟线的空缺没有别的来源**：本地通达信整包只有 `lday`，没有 `minline/fzline`，
   所以拒绝指数是"如实说做不到"，不是"暂时没接"。
4. **F5 口径漂移没动**：当初定"分钟周期只跑自选池"时自选池还是股票池；Request B 把它换成指数池之后，
   这条口径实际只覆盖了 baostock 服务不了的品种。要不要放宽自动范围是**新决策**，这一轮没替用户做，
   用户选的是按需按钮。
5. **1 分钟周期不在按钮范围内**：页面周期只有 日/周/月/30分/5分，接口 `PERIODS` 里虽有 `15`，
   但页面上没有入口，也没验证过。

## 6. 复现命令

```bash
cd /Users/zzz/workspace/chanlun
../.venv-chanlun/bin/pytest -q                                  # 796 passed, 12 deselected
../.venv-chanlun/bin/pytest tests/data/test_sync.py tests/web/test_api_sync.py \
    tests/web/test_frontend_sync_button.py tests/test_cli.py -q # 61 passed

# 真机：接口（需要服务在 8888 跑）
curl -s -X POST http://127.0.0.1:8888/api/sync -H 'Content-Type: application/json' \
     -d '{"code":"600000","period":"30"}'
curl -s "http://127.0.0.1:8888/api/sync/status?code=600000&period=30"
curl -s "http://127.0.0.1:8888/api/structure?code=600000&period=30&adjust=qfq&limit=1200"

# 真机：命令行（与页面共用一份实现）
PYTHONPATH=src ../.venv-chanlun/bin/python -m chanlun sync --period 30 --codes 600759
```
