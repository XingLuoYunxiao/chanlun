# 部署：每天收盘后自动跑一次

这个目录只放「让流水线自己按时跑起来」的东西，不含业务代码。

| 文件 | 平台 | 作用 |
| --- | --- | --- |
| `chanlun-daily.service` / `chanlun-daily.timer` | Linux (systemd) | 工作日 17:30 Asia/Shanghai 触发 `python -m chanlun daily` |
| `com.chanlun.daily.plist` | macOS (launchd) | 同上；本机是 macOS，所以实际装的是这个 |

## 流水线实际做了什么

`python -m chanlun daily [--dry-run] [--periods day,30,5] [--codes ...] [--source baostock|tdx]` 按顺序做七件事，
**每一步都尽力而为**：某一步失败只写进摘要，不吞掉后面的步骤（收盘后的数据不可再生，
因为一次网络抖动就整批跳过，代价比多跑几分钟大得多）。

1. **判断是不是交易日**：`last_trading_day(今天) != 今天` 就打印一行 INFO 并退出 0。
   这一条放在最前面，是为了让假期里被 launchd/systemd 触发的任务**一个网络请求都不发**。
2. **同步**（`--dry-run` 跳过）：逐周期增量拉取，单只失败不中断整批。
   `day` 周期在 `--source tdx` 下改走通达信整包（见下一节），`30/5` 永远走 baostock。
3. **校验**：抽 20 只票与外部行情源交叉比对，失败降级为一行说明，不阻断流水线。
4. **判周期可用性**：以日线 `sync_state.end_ts` 的中位数为基准，分钟数据落后于日线就
   把它从本次结构计算里剔除，并在输出里写明「落后 N 个自然日」。
   宁可只算日线，也不能拿几周前的分钟结构冒充当下 —— 级别不同，中枢和买卖点完全不同。
5. **全量结构快照入库**：每只票每个可用周期冻结一份结构指纹到 `meta.structure_snapshot`，
   数据不足的票直接跳过。
6. **全市场扫描 + 自选池跟踪**：写 `meta.scan_result`，自选池相对上次快照给出变化。
7. **推送**：默认追加写 `logs/notify.log`（`--notify console|file|null` 可选）。

`--dry-run` 是彩排：**不联网、不落库、不推送**，但走的是同一套代码，只在写入口收手。
所以它能测出「真跑时结构算不算得出来」，而不会在库里留下任何痕迹。

### 日线数据源：`--source {baostock,tdx}`

```bash
python -m chanlun daily --periods day --source tdx            # 下整包（551 MB）→ 解压 → 全市场导入
python -m chanlun daily --periods day --source tdx --no-download   # 复用 data/tdx_raw 里已解开的那份
python -m chanlun daily --periods day --source tdx --tdx-src /path/to/vipdoc
```

- **速度**：通达信官方整包 `hsjday.zip`（约 551 MB，12,449 个 `.day`）一次拉完，本地解析几十秒；
  baostock 是逐只 5471 次请求，2–6 小时。日线改由整包供数据后，17:30 那一轮从小时级降到分钟级。
- **口径会变，这是切源的全部风险**：整包写的是**不复权**价，baostock 写的是**前复权**。
  两者写的是同一个 `(code, "day")` 文件，`store.write` 是原子替换（不是追加），
  所以是**换源**而不是「多一个数据源」。切换后 `sync_state.adjust` 从 `"2"` 变成 `raw`，
  页面右上角与自选栏据此标注；**回测仍强制前复权**（一期约束不变）。
- **不可逆的一半**：除权因子现在是由「库内前复权 ÷ 通达信不复权」反推出来的
  （`python -m chanlun factors`）。日线切到整包后，前复权那份副本被覆盖，**就再也反推不出来了**。
  因此顺序必须是：**先跑 `factors` 落库 → 再切 `--source tdx`**。此后权威来源只剩
  baostock `query_adjust_factor`（尚未实现，见 spec「已知空白」）。
- `daily --source tdx` 会自己打印一行备注，说明本次是口径切换、多少只票受影响。
- 分钟周期不受影响：通达信没有公开的分钟整包，`30/5` 仍逐只 baostock（只对自选池）。

实测开销（本机，2026-10-01）：

| 步骤 | 开销 |
| --- | --- |
| 全市场日线一轮同步（baostock） | 5471 只 × 1.0–4.3 秒 ≈ **2–6 小时**（单会话串行） |
| 全市场日线一轮同步（`--source tdx`） | 下载 551 MB + 本地解析 12,449 个文件，**分钟级** |
| 全市场结构快照（串行） | 约 0.024 秒/票·周期 ≈ **5 分钟/周期** |
| 全市场扫描（`--workers 4`） | 3.3 秒（结构不足的票被跳过；真正有历史的票才花时间） |
| 单只票彩排（1 只 × 1 周期） | 0.1 秒 |

**因此定时任务只跑日线**（`--periods day`）。分钟数据目前只有 baostock 一个源，
全市场一轮 30 分钟同样要 2–6 小时，三个周期叠起来会撞上第二天开盘；
分钟数据改由手动 `sync --period 30 --codes ...`（只同步自选池，几十只票几十秒）或
二期盘中增量源提供。流水线本身支持多周期：把 `--periods day,30,5` 加回去即可，
它会在分钟数据陈旧时自动降级为仅日线，而不是拿旧数据硬算。

## 前置条件：数据得先同步过

流水线只处理**库里已有的数据**，它不会替你决定买多少磁盘。先看清现状：

```bash
find data/day -name '*.parquet' | wc -l   # 已同步的日线只数（品种表全市场 5471 只）
du -sh data/day                           # 磁盘占用
```

首次全市场同步用 `--since` 限定起点，别为 5471 只票各拉 35 年
（`--since` 只作用于**本地还没有数据的票**，已有数据的票仍走纯增量，不会在历史中间留洞）：

```bash
cd <项目目录>
PYTHONPATH=src python -m chanlun sync --period day --since 2021-01-01   # 约 300 MB / 2–6 小时
PYTHONPATH=src python -m chanlun sync --period 30 --codes 600000,000001 # 分钟数据按需
```

实测磁盘：`data/day/sh/600000.parquet` = 320 KB / 6517 行 ≈ 49 字节/行。
五年日线（约 1150 行/只）全市场约 **300 MB**；全历史约 2 GB。这个取舍由使用者定，
流水线不擅自决定。

**同步和看盘页可以同时开着**：写 Parquet 是「写临时文件 + `os.replace`」的原子替换，
读者要么看到旧文件、要么看到新文件，不会读到写了一半的半个 parquet。

## 装（macOS，本机）

```bash
cp chanlun/deploy/com.chanlun.daily.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chanlun.daily.plist
launchctl print gui/$(id -u)/com.chanlun.daily | head -20    # 确认已加载 + 下次触发
```

手动触发一次、卸载：

```bash
launchctl kickstart -p gui/$(id -u)/com.chanlun.daily
launchctl bootout gui/$(id -u)/com.chanlun.daily
```

## 装（Linux，systemd）

```bash
sed -e "s#__PROJECT_DIR__#$PWD#g" -e "s#__PYTHON__#$(command -v python3)#g" \
    chanlun/deploy/chanlun-daily.service > /tmp/chanlun-daily.service
sudo cp /tmp/chanlun-daily.service chanlun/deploy/chanlun-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now chanlun-daily.timer
systemctl list-timers chanlun-daily.timer
```

## 时间口径（容易踩）

- **17:30 是 Asia/Shanghai 的 17:30**。systemd 那份写了 `Timezone=Asia/Shanghai`；
  launchd 用**本机时区**解释 `StartCalendarInterval`，本机 `/etc/localtime` 是
  `Asia/Shanghai`（CST +0800）所以正好一致。换机器时区必须回来改 plist 里的
  `Hour`/`Minute`，launchd 不会替你换算。
- 为什么是 17:30：A 股 15:00 收盘，baostock 日线约 15:30–16:30 落定，17:30 留了余量。
  再早会拿到半截数据，再晚会让人打开看盘页时看不到当天结构。
- 非交易日：流水线自己判断，打印一行 INFO 后以 0 退出（不是失败）。
- 错过的触发点：systemd 用 `Persistent=true`、launchd 在唤醒后补跑一次，
  都只补最近一次。漏一天就少一天的结构快照，所以补跑是必须的。

## 日志

- 流水线：`logs/daily.log`（两个平台都指到这里）；systemd 上还可 `journalctl -u chanlun-daily.service`。
- 看盘页：`logs/chanlun.log`（`python -m chanlun serve`）。
