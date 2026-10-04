# D-41 证据：`capped` 中枢在信号层的收口

> 用途：ARCHITECTURE.md D-41 的**可复现测量**。本文件里每个数字都能用下面写出的
> 命令重跑出来；每个「通过」都配了一条**实测能失败**的扰动。
>
> 日期：2026-10-04。机器：macOS arm64，TZ Asia/Shanghai，Python 3.14.6。
> 语料：本机 `data/` 里的日线，全市场 **5440** 只票（不足 250 根 K 线跳过 113 只）。

---

## 0. 背景：为什么需要这个门

第 33 课《走势的多义性》：

> 中枢的延伸不能超过5段，也就是一旦出现6段的延伸，加上形成中枢本身那三段，
> 就构成更大级别的中枢了。

本级别因此最多 `3 + 5 = 8` 段。`pivot.py` 早就按这条收口，并把「上限**真的**
掐停」记成 `Pivot.capped`。但 `capped` 的中枢，其 `end_idx` 那一段**不是**离开段
—— 组内每一段（含 `end_idx`）都仍与 `[ZD, ZG]` 重叠，只是段数到顶才停下。
第 20 课中心定理一：

> 走势中枢的延伸等价于任意区间[dn，gn]与[ZD，ZG]有重叠。

信号层过去一直按**位置**把 `segs[end_idx]` 当离开段，于是被上限掐停的中枢照样
产出「离开-依赖」的买卖点与趋势背驰。D-41 在 `signal.py::_third_kind`、
`signal.py::_entering_and_leaving`、`divergence.py::_leaving_legs` 三处加了
`if p.capped: continue`。

---

## 1. 全市场基线（修复后，即当前主干）

```bash
cd <本项目根目录>
PYTHONPATH=src ../.venv-chanlun/bin/python \
    optimizer/tools/measure_capped_signal_gate.py
```

输出（退出码 **0**）：

```
读取失败 0 只；不足 250 根 K 线跳过 113 只
中枢 11642 个，其中 status=CONFIRMED 8186 个，capped 2759 个（占确认中枢 33.7%）

判据一/二：离开-依赖信号落在哪一类中枢上
模式      类型        capped    非 capped
strict  b1             0          91
strict  s1             0          25
strict  b3             0        3065
strict  s3             0        2723
loose   b1             0          91
loose   s1             0          25
loose   b3             0        3065
loose   s3             0        2723
趋势背驰                   0         116

判据三：第三类买卖点的严格 / 非严格计数
  strict   b3 3065 / s3 2723
  loose    b3 3065 / s3 2723

判据一 capped 中枢上的离开-依赖信号 = 0（要求 0）：通过
判据二 正对照 非 capped 中枢上的同类信号 = 11924（要求 > 0）：通过
判据三 严格/非严格第三类逐项相同：通过
```

**★ 判据二 是判据一的正对照**：非 `capped` 中枢上有 11924 条同类信号，说明
判据一的 0 不是「这批票根本没有中枢/没有信号」。

**★ 33.7% 这个比例本身是 D-41 的一个副产品发现。** `pivot.py:35` 记的是
「实测 24 只票日线 52 个确认中枢里 12 个，23%」—— 全市场上是 2759/8186 = 33.7%，
比那条记录高 1.46 倍。两者口径不同（24 只 vs 5440 只），**不是矛盾**，但
「23%」不该再被当成全市场比例引用。

---

## 2. 扰动一：去掉两处**有产出**的门 ⇒ 判据一红，退出码 1

`signal.py::_entering_and_leaving` 与 `divergence.py::_leaving_legs` 是真正决定
产出的两处。把它们的 `if p.capped: continue` 改成 `if False and p.capped: continue`
（**按函数名定位**，不要按行号 —— 行号会随注释漂移）：

```bash
cd <本项目根目录>
cp src/chanlun/chan/signal.py /tmp/d41_signal.bak
cp src/chanlun/chan/divergence.py /tmp/d41_divergence.bak
../.venv-chanlun/bin/python - <<'PY'
import pathlib
for path, fn in (("src/chanlun/chan/signal.py", "_entering_and_leaving"),
                 ("src/chanlun/chan/divergence.py", "_leaving_legs")):
    p = pathlib.Path(path); lines = p.read_text().splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith(f"def {fn}("))
    idx = next(i for i in range(start, len(lines)) if lines[i].strip() == "if p.capped:")
    lines[idx] = lines[idx].replace("if p.capped:", "if False and p.capped:")
    p.write_text("".join(lines)); print(f"{path}:{idx+1}")
PY
PYTHONPATH=src ../.venv-chanlun/bin/python \
    optimizer/tools/measure_capped_signal_gate.py; echo "退出码=$?"
# 还原（★ 不要用 git checkout -- ：工作区可能有未提交的改动，那条命令会连它们一起丢掉）
cp /tmp/d41_signal.bak src/chanlun/chan/signal.py
cp /tmp/d41_divergence.bak src/chanlun/chan/divergence.py
```

实测输出（退出码 **1**）：

```
strict  b1            46          91
strict  s1            18          25
strict  b3             0        3065
strict  s3             0        2723
loose   b1            46          91
loose   s1            18          25
loose   b3             0        3065
loose   s3             0        2723
趋势背驰                  64         116

判据一 capped 中枢上的离开-依赖信号 = 192（要求 0）：失败
判据二 正对照 非 capped 中枢上的同类信号 = 11924（要求 > 0）：通过
判据三 严格/非严格第三类逐项相同：通过
```

`192 = 46 + 18 + 46 + 18 + 64`。**判据一确实在测量东西。**

---

## 3. 扰动二：只去掉 `_third_kind` 的门 ⇒ 输出**逐项不变**（结构性 no-op）

```bash
cd <本项目根目录>
../.venv-chanlun/bin/python - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/chan/signal.py"); lines = p.read_text().splitlines(keepends=True)
start = next(i for i, l in enumerate(lines) if l.startswith("def _third_kind("))
idx = next(i for i in range(start, len(lines)) if lines[i].strip() == "if p.capped:")
lines[idx] = lines[idx].replace("if p.capped:", "if False and p.capped:")
p.write_text("".join(lines)); print(f"signal.py:{idx+1}")
PY
PYTHONPATH=src ../.venv-chanlun/bin/python \
    optimizer/tools/measure_capped_signal_gate.py; echo "退出码=$?"
```

实测：全市场 5440 票的输出与 §1 **逐项相同**（`capped` 列全 0、判据一 0、
判据二 11924），退出码 **0**。

**为什么必然如此（结构性的，不是样本不够）**：`capped` 的定义要求
`end_idx + 1` 与 `[ZD, ZG]` 重叠，而第 20 课第三类买点的位置判据要求回试段在
区间**之外**：

> 一个次级别走势类型向上离开缠中说禅走势中枢，然后以一个次级别走势类型回试，
> 其低点不跌破ZG，则构成第三类买点

买侧要 `back.low > p.zg`，`capped` 蕴含 `back.low <= p.zg`；卖侧同理。
两者在**同一段**上互斥 ⇒ 那一行门永远不会改变产出。

**结论（据实记录）**：`_third_kind` 的 `capped` 门是**口径不变量**，不是行为门。
留它的理由是防止将来某次「放宽第三类判据」（`THIRD_TOL` 就是这么来的）把
延伸中的中枢重新误报成第三类；它由三条单元测试守住（见 §5），**不由本工具守住**。
本工具 §1 的 `b3 0 / s3 0` 是位置判据自己的结果，**不要**把它记成「门起了作用」。

---

## 4. 扰动三：把 `mode` 相关的容忍度加回去 ⇒ 判据三红，退出码 1

判据三要求严格/非严格两个口径在第三类上逐项相同。D-41 删掉了
`THIRD_TOL = 0.1`（一个只在 `SignalMode.LOOSE` 下生效的容忍度）。把那个语义
加回去（`_third_kind` 重新收 `mode` 参数、只在 `LOOSE` 时用 0.1）：

```bash
cd <本项目根目录>
../.venv-chanlun/bin/python - <<'PY'
import pathlib
p = pathlib.Path("src/chanlun/chan/signal.py"); lines = p.read_text().splitlines(keepends=True)
i = next(i for i, l in enumerate(lines) if l.strip() == "out.extend(_third_kind(segs, pivots, level))")
lines[i] = lines[i].replace("_third_kind(segs, pivots, level)", "_third_kind(segs, pivots, level, mode)")
j = next(i for i, l in enumerate(lines) if l.startswith("def _third_kind("))
end = next(i for i in range(j, len(lines)) if lines[i].rstrip().endswith("-> list[Signal]:"))
lines[end] = lines[end].replace("level: str)", "level: str, mode: SignalMode = SignalMode.STRICT)")
k = next(i for i in range(end, len(lines)) if lines[i].strip() == "out: list[Signal] = []")
lines.insert(k, "    tol = 0.1 if mode is SignalMode.LOOSE else 0.0\n")
a = next(i for i in range(k, len(lines)) if lines[i].strip() == "if back.direction == -1 and back.low > p.zg:")
lines[a] = lines[a].replace("back.low > p.zg", "back.low > p.zg - tol")
b = next(i for i in range(k, len(lines)) if lines[i].strip() == "if back.direction == 1 and back.high < p.zd:")
lines[b] = lines[b].replace("back.high < p.zd", "back.high < p.zd + tol")
p.write_text("".join(lines))
PY
PYTHONPATH=src ../.venv-chanlun/bin/python \
    optimizer/tools/measure_capped_signal_gate.py --limit 600; echo "退出码=$?"
```

实测输出（`--limit 600`，退出码 **1**）：

```
strict  b3             0         327
strict  s3             0         244
loose   b3             0         330
loose   s3             0         245

判据三：第三类买卖点的严格 / 非严格计数
  strict   b3 327 / s3 244
  loose    b3 330 / s3 245

判据一 capped 中枢上的离开-依赖信号 = 0（要求 0）：通过
判据二 正对照 非 capped 中枢上的同类信号 = 1170（要求 > 0）：通过
判据三 严格/非严格第三类逐项相同：失败
```

**判据三确实在测量东西。** 注意：如果把容忍度**无条件**加回去（不区分 mode），
严格与非严格会一起变，判据三**仍然是绿的** —— 判据三量的是
「口径是否随 `mode` 分叉」，不是「有没有容忍度」。别用错的扰动方式去「验证」它。

---

## 5. 小样本没有判别力

`--limit 60` 下把三处门**全部**去掉，输出与未扰动逐字节相同
（判据一 0 / 判据二 169 / 判据三 通过，退出码 0）。

**不要把 `--limit 60` 的绿色当证据。** 判据一的判别力来自 §2 的全市场跑。

---

## 6. 单元测试护栏

| 护栏 | 守住什么 | 去掉对应的门时 |
|---|---|---|
| `tests/chan/test_signal_mode.py::test_capped_pivot_emits_no_third_kind_buy` | `_third_kind` 的位置判据在 `capped` 夹具上不成立（`back.low <= zg`） | **不会红** —— 这是口径不变量（§3） |
| `…::test_capped_pivot_emits_no_third_kind_sell` | 同上，卖侧 | 同上 |
| `…::test_capped_pivot_emits_no_leave_dependent_signal` | `_entering_and_leaving` 跳过 `capped` | 红（实测 `AssertionError`） |
| `tests/chan/test_divergence.py::test_capped_pivot_is_skipped_by_both_leaving_leg_implementations` | **两份实现各断言一次** | 红（两侧各实测一次） |
| `tests/chan/test_divergence.py::test_trend_divergence_matches_b1_s1` | 两份实现的**双向相等** | **不会红** —— 夹具的 4 只票上没有落在 `capped` 中枢上的趋势背驰，这条断言量不到该分支 |

最后一行是**实测**结论，`divergence.py::_leaving_legs` 的 docstring 里原来写着
「否则上面的双向相等断言会把两个实现的不一致暴露出来」，**那句是错的**，已在
D-41 一并更正。真正咬住这个分支的是上表第 4 行那条合成用例。

---

## 7. 修复前后的影响面（同一工具、同一语料）

| 指标 | 修复前（两处门去掉后测得的「无门」状态） | 修复后 |
|---|---|---|
| `capped` 中枢上的第一类买点 | 46 | **0** |
| `capped` 中枢上的第一类卖点 | 18 | **0** |
| `capped` 中枢上的趋势背驰 | 64 | **0** |
| 全部趋势背驰 | 180 | **116** |
| 全部第一类买点 | 137 | **91** |
| 全部第一类卖点 | 43 | **25** |

「修复前」一列是**同一份代码去掉两处门**测得的（§2），不是从别处的报告抄来的。
三处差值 46 / 18 / 64 全部落在 `capped` 中枢上，与 §2 的归因表一致。

---

## 8. 未覆盖 / 已知空白

- **`capped` 中枢上仍有 `b3/s3` 吗？** 没有，全市场 0 条 —— 但如 §3 所述，这是
  位置判据自己的结果，**不是** `_third_kind` 那道门的结果。
- **`capped` 与第 20 课第三类位置判据互斥**是本文件最有信息量的结论：它意味着
  「第三类买卖点」这一判据**永远不会**在 `capped` 中枢上成立。若将来要支持
  「段数到顶 ⇒ 升级到更大级别的中枢」，`capped` 中枢应该交给**级别递归**处理，
  而不是在本级别里放宽位置判据。
- **本轮不做级别递归**（`MAX_SEGMENTS = 8` 是本级别的收口）。这条空白已在
  ARCHITECTURE.md §5 记录。
- 本文件不含 `THIRD_TOL` 删除前的「99 条 LOOSE-only 分类」数字 —— 那些是在
  **已删除的代码**上测得的一次性探针结果，无法从当前主干复现，因此只写进
  D-41 的「被否决的替代方案」，并注明探针未入库。

---

## 9. 前端标记的真实浏览器复测（2026-10-04 追加）

§1–§8 量的是**信号层**。`capped` 中枢在**看盘页**上的视觉标记（点线边框 +
` · 段数到顶` + 标题行小标签 + 图例行）另用真实浏览器复测，脚本
`docs/evidence/2026-10-04-capped-marker-probe.mjs`（headless Chrome + CDP，
**禁缓存**；文案一律从 DOM dump 读，截图只作布局旁证）。

跑法（看盘页须在 `127.0.0.1:8888`，headless Chrome 须在 `9222`）：

```bash
node docs/evidence/2026-10-04-capped-marker-probe.mjs
```

被测页面：`?code=sh.600519&period=day&mode=strict&limit=1200`。
选它的理由：该票第 0 个中枢实测 `capped: true`（`status: confirmed`、`end_idx: 9`），
**且同页第 1 个中枢 `capped: false`** —— 于是同一次取样里天然带一个**反向对照**，
不需要另找票。

### 9.1 实测输出（落定主干，退出码 0）

```
=== 中枢图例行（DOM dump，逐字）===
  [0] 标题="中枢 日线" 标签=["确认","段数到顶"]
       meta="ZG 1565.26 / ZD 1370.03 · GG 2250.17 / DD 812.61 · 2020-03-19 → 2022-03-16 · 段数到顶（第33课：已构成更大级别中枢；本级别不产出买卖点）"
  [1] 标题="中枢 日线" 标签=["未确认"]
       meta="ZG 1712.06 / ZD 1376.88 · GG 1814.58 / DD 1164.58 · 2022-03-16 → 2024-09-19"

=== markArea 全部条目（4 条）===
  [0] {"name":"结构","borderType":"dotted","borderWidth":2,"opacity":0.6,"label":"中枢 日线 · ZG 1565.26 / ZD 1370.03 · 段数到顶"}
  [1] {"name":"结构","borderType":null,"borderWidth":null,"opacity":null,"label":null}
  [2] {"name":"结构","borderType":"dashed","borderWidth":1,"opacity":0.5,"label":"中枢 日线 · ZG 1712.06 / ZD 1376.88"}
  [3] {"name":"结构","borderType":null,"borderWidth":null,"opacity":null,"label":null}
```

10 条判据全过。注意第 1 个中枢是 `tentative` 所以本来就是 `dashed`，
第 0 个中枢**同时**是 `capped` 且 `confirmed` —— 点线边框（`capped`）的
优先级高于虚线（`tentative`），这一优先级写在 `app.js` 的注释里，没有隐式交换。

### 9.2 顺带发现的清单溢出（真实缺陷，已修）

复测时顺手量了清单每一行 `.row-meta` 的右边界与 `#ledger` 右边界之差。
**修前**（1600px 视口）：

| 票 | `.row-meta` 行数 | 越界行数 | 最多越界 |
|---|---|---|---|
| `sh.600519` | 14 | 14 | 554.2px |
| `sh.000001` | 12 | 12 | 560.8px |
| `sz.399006` | 15 | 14 | 560.8px |

**全中**，而且不是「差一点点」：越界量等于整条 meta 的宽度，也就是**日期与笔数那半截
画到了图表上、再被视口右边缘裁掉**，用户读不到，连省略号提示都没有。

成因是 CSS 与 DOM 不匹配：`.row-meta` 的样式声明了
`white-space: nowrap; overflow: hidden; text-overflow: ellipsis`，
但它是个 `<span>` —— **行内盒子上的 `overflow` 与 `text-overflow` 一律不生效**
（实测 `clientWidth === 0`、`scrollWidth === 0`，而 `getBoundingClientRect().width`
是 831.2px）。声明写了，等于没写。

修法：`styles.css:415` 给 `.row-meta` 加 `display: block`。块化之后省略号才真的出现，
越界行数 14/12/14 → **0/0/0**。

这直接决定了 `capped` 标记要放在哪里：同一个原因让 ` · 段数到顶（…）` 后缀在窄栏里
**必然被省略号吃掉**，所以标记**不能只放在 `.row-meta`**。现在它在三处出现：

1. 图上中枢区带 —— 点线边框 + `borderWidth 2` + `opacity 0.6`（`app.js:642`）；
2. 图上标签后缀 ` · 段数到顶`（`app.js:662`）；
3. 清单**标题行**的点线小标签「段数到顶」（`app.js:1057-1066`，样式 `styles.css:435`），
   `title` 属性带「第33课：已构成更大级别中枢；本级别不产出买卖点」——
   **这一处才是用户在窄栏里真正读得到的**。`.row-title` 同时加了 `flex-wrap: wrap`
   （`styles.css:397`），免得最后一个标签重演同样的溢出。

`.row-meta` 里的那句长文案保留：栏宽够的时候它比标签更完整，栏窄的时候被省略号吃掉
也不影响可读性 —— 标签已经把话说完了。

### 9.3 这个探针**能失败**（四条扰动，各自归因干净）

| 扰动 | 失败判据 | 退出码 |
|---|---|---|
| `app.js:642` `const cap = p.capped === true;` → `const cap = false;` | markArea 的 3 条（dotted 数 / dotted 样式 / 标签后缀） | 1 |
| `app.js:992` 图例后缀条件 → `false`（`cap` 不动） | 图例 meta 的 1 条 | 1 |
| `styles.css:415` 的 `display: block` 注掉（`row-meta` 退回行内） | 布局「不越界」的 1 条（越界 2 条、最多 554.2px） | 1 |
| `app.js:1060` `if (capped === true)` → `if (false)` | 标题行标签的 1 条 | 1 |

四次都先 `cp` 到 `/tmp/app_d41.bak` / `/tmp/app_d41b.bak` / `/tmp/styles_d41.bak`，
扰动完 `cp` 回来并 `diff -q` 确认逐字节还原（**不用 `git checkout --`**）。
还原后重跑 ⇒ 10 条全过、退出码 0。

### 9.4 `capped` 从此有生产消费方

改前 `grep -rn capped src/chanlun/web/static/` 实测 **0 命中**（字段在 API JSON 里
但前端没人读）。改后：

```
styles.css:395:/* `flex-wrap` 是为了「段数到顶」小标签（`.chip.capped`）不重演下面的溢出：
styles.css:432:/* D-41：`capped` 中枢的点线小标签。点线边框与图上中枢区带的点线边框是同一件事
styles.css:435:.chip.capped { color: var(--rice); border-style: dotted; }
app.js:637:          // D-41：段数上限掐停的中枢（`capped`）**不是**一个走完的中枢 ——
app.js:642:          const cap = p.capped === true;
app.js:989:        // D-41：`capped` 中枢在图上是点线边框 + 「段数到顶」，这里把同一件事写进图例，
app.js:992:          + (p.capped === true ? " · 段数到顶（第33课：已构成更大级别中枢；本级别不产出买卖点）" : ""),
app.js:994:        capped: p.capped === true,
app.js:1038:  function row({ idx, title, dirClass, status, confirmedAt, meta, startTs, endTs, capped }) {
app.js:1060:    if (capped === true) {
app.js:1062:      capChip.className = "chip capped";
```

**11 命中**（0 → 11）。图例、图上标记、标题行标签**同时**接上，
是为了让用户不会把「段数到顶」误读成一个独立图层。
