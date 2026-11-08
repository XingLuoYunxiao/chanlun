# 级别递归 f2：设计与隔夜进度（D-51）

- 日期：2026-10-12 夜
- 任务来源：用户要求「先去完整理解禅师原文和这个算法，然后把**真递归**实现一下」
- 结论：**核心递归已实现、已验证、已测量**；上层接入（快照 / API / 扫描 / 回测）
  与「级别≠周期」的语义纠正**明确留给下一步**，理由见 §7。
- 决策记录：[`ARCHITECTURE.md` 的 `D-51`](../../ARCHITECTURE.md)
- 理论条目：[`optimizer/theory/L84-RECURSION-F2.md`](../../optimizer/theory/L84-RECURSION-F2.md)

---

## 1. 一句话

旧代码用**本周期线段**直接构中枢，每个行情周期各构一遍，等于把原文的递归
在第一步就截断了。本次把第 84 课的 `f2(an)=an+1` 接上：**上一级中枢由
「三个连续次级别走势类型」构造**，逐级上推。第 0 层与旧实现**逐字段相等**，
所以这不是判据变更，而是**加了一层**。

```
线段 ──f1(a0)=a1──► 中枢(0) ──► 走势类型(0)
                       │
                       └──f2(an)=an+1──► 中枢(1) ──► 走势类型(1) ──f2──► 中枢(2) ──► …
```

## 2. 原文依据（逐字，全文见 theory 条目）

| 课 | 原句（节选） | 作用 |
|---|---|---|
| 17 | 「某级别走势类型中，被至少三个连续**次级别**走势类型所重叠的部分，称为缠中说禅走势中枢。……这里有一个**递归的问题**」 | 中枢定义的递归性 |
| 84 | 「一、**f1(a0)=a1**；二、**f2(an)=an+1**；关于第二条的中枢过程规则，是**一直没有任何改变的**，而关于第一条，其实，可以随意设置任何的」 | 递归拆成两半；f2 是硬约束 |
| 84 | 「可以用分型、线段这样的函数关系去构造最低级别的中枢……而在二中，也就是**最低级别以上**，可以用另一套规则去定义」 | 只有最低级别才用线段 |
| 35 | 「三个连续的最低级别走势类型之间，如果发生重叠关系，也就是三个最低级别走势类型所分别经过的**价格区间有交集**，那么就形成了高一级别的缠中说禅中枢……这个过程可以**逐级上推**」 | f2 的可操作定义 |
| 35 | 「**5分钟走势类型，必须包含也最多包含5分钟级别中枢**」 | 可校验不变量 |
| 63 | 「有了线段，就可以定义1分钟的中枢……然后**按照递归的方法**，可以逐步定义5分钟、30分钟、日、周……」 | 构造链 |
| 63 | 「**什么级别的图和什么级别的中枢没有任何必然关系**……不能把显微镜和显微镜观察的东西混在一起了」 | 否掉「级别=周期」 |
| 84 | 「**级别，本质上与时间无关**……一个最低级别不到的走势类型，可以生长100年不长成更高级别的」 | 同上，更硬 |
| 67 | 「有了这两个基础，那么整个**中枢与走势类型的递归体系**就可以建立起来」 | 线段是起点 |

**关键分辨**：第 84 课明说 f1「可以随意设置」，第 101 课补「分型等于递归函数的
a0，这完全可以随意设计」。所以「用线段起步」**本身不违反原文**——缺的是 f2。

## 3. 改动前的取证（都留在库里，可复现）

`optimizer/tools/explore_recursion.py` + 直接读代码：

- `pivot.py:129`（旧）`find_pivots(segments: Sequence[Segment], ...)` —— 入参类型
  写死成线段，所以**结构上不可能**吃「次级别走势类型」。
- `engine.py:183` `pivots = find_pivots(segments, self.level)`，而
  `engine.py:143` `self.level = level or period` —— **级别 = 行情周期**。
- `LEVEL_UP` / `merge_pivots`（中枢级别扩张，第 20 课）在 `src/` 下**零调用**，
  只被 `tests/chan/test_pivot.py` 用到。
- `signal.py:28-29` 自陈：「第二类买卖点原文要求次级别回抽，本实现用**本级别下一段**
  近似」—— 系统里根本没有「次级别」这个对象可以下钻。
- 全文 `grep 次级别|sub_level|lower_period|child` 在 `src/` 下**零命中**（除注释）。

## 4. 设计（三条要点）

**4.1 让「段」抽象出来，f1/f2 复用同一个 `find_pivots`**

新增 `types.py::LevelSegment` 协议（`direction` / `high` / `low` / `src_start` /
`src_end` / `start_ts` / `end_ts` / `status` / `confirmed_at`）。`Segment` 与 `Trend`
都满足它。第 84 课「过程规则一直没有任何改变」是这句话的代码落点 ——
**抄一份平行实现就迟早分叉**。

**4.2 `Trend` 补足成为「段」所需的字段**

`high` / `low` / `direction` / `src_start` / `src_end`。其中：

- `high`/`low` 取**原始 bar 极值**，且 bar 跨度**连续铺满时间轴**（延伸到下一段
  起点之前），把两段中枢之间那段「离开 + 回试」的连接走势吃进来 ——
  上一级的 `find_pivots` 需要的是像线段那样连续的段序列。
  不传 `bars` 时退化为成分中枢 `gg`/`dd` 的并集，**会低估**，故生产链路必须传 `bars`。
- `direction` 对**盘整记 0**（第 17 课只给趋势分上涨/下跌）。见 §6 的第一条。

**4.3 递归主循环**

```python
pivots = find_pivots(segments, chain[0]);  trends = classify_trends(pivots, chain[0], bars)
for lv in chain[1:]:
    if not trends: break
    pivots = find_pivots(trends, lv)          # ← f2：输入是上一级的走势类型
    if not pivots: break
    trends = classify_trends(pivots, lv, bars)
```

遇到空级别就停（不是撞 `max_depth` 才停）—— 一个级别连一个中枢都构不出来时，
再往上没有输入，硬推只会产出空壳。

## 5. 交付物清单

| 文件 | 性质 | 说明 |
|---|---|---|
| `src/chanlun/chan/recursion.py` | **新增** | `build_levels` / `level_chain` / `LevelStructure` / `validate_levels` |
| `src/chanlun/chan/types.py` | 改 | 新增 `LevelSegment` 协议；`Segment` 补 `start_ts`/`end_ts` 属性 |
| `src/chanlun/chan/pivot.py` | 改 | `find_pivots` 入参放宽为 `LevelSegment`；无方向段的 `zn` 分支 |
| `src/chanlun/chan/trend.py` | 改 | `Trend` 补 5 个字段；`classify_trends` 增可选 `bars` |
| `tests/chan/test_recursion.py` | **新增** | 27 条用例（含 4 条校验器可失败的反向对照） |
| `optimizer/tools/measure_recursion.py` | **新增** | 正式测量脚本，退出码 0/1 |
| `optimizer/tools/measure_recursion_pit.py` | **新增** | 递归结构的 point-in-time 测量（forward / backward 两向） |
| `optimizer/tools/explore_recursion.py` | **新增** | 设计前的取证脚本（走势类型序列形状） |
| `optimizer/tools/reanchor_report.py` | **新增** | 行号引用漂移的只读诊断（复用性工具） |
| `src/chanlun/__main__.py` | 改 | 新增 `levels` 子命令（命令行出口，只读） |
| `tests/test_cli_levels.py` | **新增** | `levels` 子命令的 7 条用例 |
| `README.md` | 改 | 子命令清单补 `levels` / `industry`；`levels` 用法与示例；已知空白补一条 |
| `optimizer/theory/L84-RECURSION-F2.md` | **新增** | 理论条目（第 17/35/63/67/84/102 课逐字摘录） |
| `ARCHITECTURE.md` | 改 | 新增 `D-51`；§5 已知空白补 3 行；重锚 15 条行号引用 |
| `CHANGELOG.md` | 改 | `[Unreleased] / 新增` 新增一条 |

## 6. 测量与证据（都可复现）

```bash
cd <项目根>

# 主测量：真递归 vs 旧口径、层级收敛、方向交替率、不变量
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion.py --period 5

# point-in-time：递归结构有没有偷看未来（较慢，5 分钟数据约几分钟）
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion_pit.py \
    --period 5 --sample 2 --checkpoints 8

# 递归用例
../.venv-chanlun/bin/pytest tests/chan/test_recursion.py -q
```

**实测（本机，5 分钟基准，6 只）**

```
L0(5)     sh.600030 115 中枢 / 83 走势类型
L1(30)                  16 / 13
L2(day)                  2 /  1        ← 每级约 1/7
```

| 级别 | 真递归中枢数 / 旧口径中枢数 |
|---|---|
| `sh.600030` 30 | 16 / 18 |
| `sh.600519` 30 | 17 / 22 |
| `sh.600759` 30 | 4 / 12 |
| `sh.601398` 30 | 5 / 11 |
| `sh.603178` 30 | 14 / 22 |
| `sz.000002` 30 | 12 / 16 |
| | **逐项相同 0/6**（⇒ 不是 no-op） |

**方向交替率**（`find_pivots` 那句「相邻确认段方向必然交替」的检验）：
L0 相邻对 362、同向 **53.0%**；L1 相邻对 48、同向 **75.0%**。
⇒ 该前提**对线段成立、对走势类型不成立**。

**递归不变量 6/6 通过**（`validate_levels`，含「上级中枢 ZD/ZG 必须**恰好**等于
下级前三个走势类型区间的交集」这条实质判据）。

**point-in-time（本次补测，见 `measure_recursion_pit.py`）**

把「只喂到第 i 根 bar 的前缀」与「喂全量」对比，两个方向分开量：

| 方向 | 含义 | 实测 |
|---|---|---|
| forward | 前缀上已确认的级别中枢，全量里必须存在且 ZD/ZG 相同 | 违反 **1/1002（0.1%）** |
| **backward** | 全量里 `confirmed_at <= ts[i]` 的级别中枢，前缀上必须已经有了 | 违反 **0/999（0.0%）** |

**backward = 0 是本次要的结论**：递归**没有引入未来函数** —— 任何自称在
`ts[i]` 已确认的级别中枢，用截至 `ts[i]` 的数据确实算得出来。

那 1 例 forward 违反（`sh.600519 @2023-06-19 14:25` 的 30 分钟级别中枢，全量里
不存在）是**线段层的不收敛残留**（D-40 已登记：线段划分用全局 DP，第 71 课的
单调性违规残留 14 次），不是递归层新增的：同一检查下 L0 也会跟着动。
0.1% 的量级与 D-40 的残留比例一致。

**用例的反向对照**（否则「全绿」什么都不证明）：
`test_validator_flags_wrong_depth` / `_trend_of_wrong_level` /
`_widened_parent_pivot` / `_missing_child_trends` —— 各自把结构弄坏一处，
校验器必须变红。

## 7. 本轮**没有**做的（明确边界，不是遗漏）

1. **递归没接进上层**：`Snapshot` 无 `levels` 字段、`/api/structure` 不返回、
   `scan`/`backtest` 仍走旧口径。目前有两个出口：Python 直接调
   `chanlun.chan.recursion.build_levels`，或者在命令行跑
   `python -m chanlun levels --codes <code> --period <p> [--verbose|--json]`
   （只读本地数据）。**没有**任何自动消费者 —— 这是刻意的：一个消费者都没有时
   就把它塞进 `Snapshot`，等于凭空发明裁剪语义；而它的自然消费者是
   `signal.py` 的区间套（见下）。
2. **`self.level = level or period` 保持原样**（级别仍等于周期）。第 63/84 课都否掉
   这个等式，但 `Snapshot.level` 与落库口径绑在一起，纠正它属于独立变更。
3. **第 20 课的级别扩张（`merge_pivots`）仍未接进生产链路**。它与 f2 是两条独立
   机制（f2 是定义，级别扩张是附加定理），接进来需要单独测量。
4. **`find_pivots` 在走势类型路径上的 `GG/DD` 是一个"确定但非原文直推"的选择** ——
   见 §8 第一条，已如实登记进 `ARCHITECTURE.md` §5。

**建议的下一步顺序**：先做 1（把 `levels` 接进 `Snapshot` 与 `/api/structure`，
只增不改），再做 2（级别与周期解耦），最后才碰 3。

## 8. 未确认 / 已知弱点（不粉饰）

1. **`zn`（Z 走势段）在走势类型路径上没有原文支撑。** 第 20 课「A、C 段方向一致」
   以「相邻次级别方向交替」为前提，而实测不交替（53%/75%）。当前取「组内第一个
   有方向的段」，是**确定的**选择但不是**可推导的**选择。已进 §5。
2. **`build_levels` 的 `bars` 跨度约定（连续铺满时间轴）是本实现的工程判据**，
   原文没有规定「两段走势类型之间的连接走势归谁」。**已补测敏感性**
   （`--span-sensitivity`，A = 连续 bar 跨度收录连接走势 / C = 不传 `bars` 的
   退化口径即 `gg`/`dd` 并集）：

   ```
   PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion.py \
       --period 5 --span-sensitivity
   ```

   实测（6 只 5 分钟票）：**L0 全部相同**（L0 不吃走势类型的区间，符合预期），
   **L1/L2 逐级相同 9/17** —— 换约定确实会改上一级的中枢数，而且方向是系统的：
   退化口径 C **一律产出更多**上级中枢（`sh.600600` 类：`600030` L1 16→18、
   `600519` L1 17→21、`600759` L1 4→6）。原因是 C 低估走势类型区间（`gg`/`dd`
   不含离开段，见 D-37/D-38），区间一被低估，三个走势类型的交集就更容易成立。

   ⇒ 结论：**这条约定不是无关紧要的实现细节**，它和 D-37/D-38 是同一类口径问题。
   现状选 A（收录连接走势，区间取原始 bar 极值），因为它的区间是「这段走势真实
   经过的价格范围」，与第 35 课「走势类型所分别经过的价格区间」的字面读法一致。
   但**没有**做过「哪个约定更贴原文」的独立论证 —— 这一点如实留在这里。
3. ~~**递归的确认时间（`confirmed_at`）语义未单独验证。**~~
   **已补测（2026-10-12 夜）**：backward（不得偷看未来）**0/999 = 0.0%**、
   forward（不得凭空造）1/1002 = 0.1%，后者来自线段层 D-40 的已知残留。
   脚本 `optimizer/tools/measure_recursion_pit.py`，退出码 1 = 出现 backward 违反。
   这一条现在**不再是空白**；剩下的只是样本规模（2 只票 × 8 个检查点）。
4. **合成夹具的三级结构是"调到能复现"的**，不是从真实市场提取的。
   真实离线夹具（1212 根日线）只够一级。若要求更强的回归保障，
   应把某只真实 5 分钟票的 `build_levels` 输出固化成 golden 文件。

## 9. 与并行 agent 的冲突规避（重要交接）

会话内**另有 agent 正在改 `web/`**（D-49/D-50 浮标常驻），其未提交改动在
`web/app.py`、`web/static/*`、`tests/web/*`、`ARCHITECTURE.md`、`CHANGELOG.md`。

- **代码零重叠**：我的全部代码改动在 `chan/`、`tests/chan/`、`optimizer/` 下。
- **`ARCHITECTURE.md`**：对方在 `L1149` 插入 94 行（D-50）；我新增 `D-51`（§3.4 末尾）
  与 §5 三行，并把被我改动的 `chan/` 源文件所影响的 **15 条行号引用按内容重锚**。
  两部分不重叠。
- **`CHANGELOG.md`**：对方条目在 `[Unreleased]/新增` 顶部；我的条目插在
  `### 新增` 之后、对方条目之前。不重叠。
- **`optimizer/tools/doc_refs_baseline.json`（共享文件）**：我重记了它。
  该文件是**派生工件**、`--update-baseline` 幂等，**必然同时包含对方 web 改动的
  指纹**。谁最后跑一次都会得到同样结果，不构成丢失。
- **未触碰**：`tests/optimizer/test_check_doc_line_refs.py` 里那 3 条计数断言。
  它们**因对方的文档新增而变红**，已用隔离 worktree 证明（见 §10）。
  应由对方在其 D-50 收尾时一并更新。

### 9.1 提交策略（为什么有三个文件**故意不提交**）

`ARCHITECTURE.md`、`CHANGELOG.md`、`optimizer/tools/doc_refs_baseline.json`
这三个文件**同时含对方未提交的改动**，因此本次**只提交我自己的隔离文件**：

| 提交 | 不提交 |
|---|---|
| `src/chanlun/chan/recursion.py`（新） | `ARCHITECTURE.md` |
| `src/chanlun/chan/types.py`、`pivot.py`、`trend.py` | `CHANGELOG.md` |
| `tests/chan/test_recursion.py`（新） | `optimizer/tools/doc_refs_baseline.json` |
| `optimizer/tools/measure_recursion.py`、`explore_recursion.py`、`reanchor_report.py`（新） | （`web/**` 属对方，一行未动） |
| `optimizer/theory/L84-RECURSION-F2.md`（新） | |
| `docs/superpowers/specs/2026-10-12-level-recursion-f2-design.md`（新） | |

**为什么不能一起提交**：`doc_refs_baseline.json` 记的是**全仓库**被引行的内容指纹，
其中包含 `web/static/app.js` 等**对方未提交**的文件。只把基线和文档入库、不入库
对方的 web 代码，会让**已提交状态**自相矛盾（基线指纹对不上 HEAD 的 `app.js`）。
反过来，把对方的 web 代码也一起提交，就越过了「不动对方的改动」这条约束。

**对方的状态（本次会话实测）**：`web/app.py`、`web/static/*`、`tests/web/*` 的
最后修改时间是 **2026-10-11 12:48–14:38**，今天（10-12）除了我自己的文件之外
**没有任何文件被改动** —— 即对方目前是**静止**的，不是在写。

**收尾命令**（等两个 agent 都停了之后，由用户或对方执行）：

```bash
cd /Users/zzz/workspace/chanlun
# 1) 重记基线（幂等；把双方改动的指纹一起收编）
../.venv-chanlun/bin/python optimizer/tools/check_doc_line_refs.py --update-baseline
# 2) 更新 tests/optimizer/test_check_doc_line_refs.py 里那 3 条计数断言（见 §10 的实际输出）
# 3) 逐个列路径提交（**绝不** git add -A）
git add ARCHITECTURE.md CHANGELOG.md optimizer/tools/doc_refs_baseline.json \
        src/chanlun/web/app.py src/chanlun/web/static/app.js \
        src/chanlun/web/static/index.html src/chanlun/web/static/styles.css \
        tests/web/test_frontend_auto_sync.py tests/web/test_static_cache.py \
        tests/optimizer/test_check_doc_line_refs.py \
        docs/evidence docs/superpowers/plans docs/superpowers/specs
```

## 10. 明早验收：怎么看这套东西是否真的成了

```bash
cd <项目根>

# 1) 递归用例（应 27 passed）
../.venv-chanlun/bin/pytest tests/chan/test_recursion.py -q

# 2) 既有链路没被动过（chan 全套应 370 passed）
../.venv-chanlun/bin/pytest tests/chan/ -q

# 3) 主测量（退出码 0；三个可失败判据都打印出来）
PYTHONPATH=src ../.venv-chanlun/bin/python optimizer/tools/measure_recursion.py --period 5

# 4) 全量
../.venv-chanlun/bin/pytest -q
```

**第 4 条的预期**：`1277 passed` + **3 failed**，全部是
`tests/optimizer/test_check_doc_line_refs.py` 里的**计数断言**。这 3 条
**不是**本次递归改动造成的，判据是**隔离 worktree 反向对照**：

```bash
git worktree add /tmp/chanlun-other HEAD
# 只把对方 agent 改过的文件拷进去（ARCHITECTURE.md / CHANGELOG.md / web/*）
# 重记基线后跑：
#   → test_real_architecture_doc_is_green_and_enumerates_app_js      FAILED
#   → test_real_architecture_doc_reports_py_totals                   FAILED
#   → test_real_architecture_doc_discloses_unverifiable_lines        FAILED
```

即：**在完全没有我的 chan 改动的树上，同样这 3 条失败**。
（反过来，在 HEAD 文档 + 我的 chan 代码的树上重记基线后，43/43 全绿。）

---

## 附：本次会话里我自己拿的主意（用户说「自行决定即可」）

| 决定 | 取值 | 理由 |
|---|---|---|
| 递归的基准显微镜 | 调用方传入 `base_level`，缺省 `"5"` | 本机最细周期是 5 分钟；原文允许 a0 随意设置 |
| 级别名 | 沿用 `LEVEL_UP`（`5→30→day→week→month`） | 第 102 课「级别名字可以随意取」；与既有配置口径一致 |
| 盘整的方向 | `0`（无方向） | 第 17 课不给盘整分方向；硬编 ±1 实测等于掷骰子 |
| 空级别 | 停止上推 | 没有输入就没有输出，硬推是空壳 |
| `max_depth` | 6 | 只防病态输入，不是正常终止条件 |
| 版本号 | **不涨**，留在 `[Unreleased]` | 改动未提交 + 另一 agent 也在 `[Unreleased]`，发版时一起归并 |
| 基线文件 | 重记并提交 | 派生工件、幂等；不重记则整库行号漂移不可见 |
| 那 3 条计数断言 | **不动** | 属对方 D-50 的记账；动它有冲突风险 |
