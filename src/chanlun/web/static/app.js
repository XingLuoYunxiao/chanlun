/* 缠论盘后结构复盘台 —— 绘图、交互、自选股
 *
 * 图上的每一层都对应一个"能不能信"的判断：
 *   笔 / 线段  —— 方向（红涨绿跌），虚线与半透明表示**未确认**，会被后面的K线改写；
 *   中枢       —— 靛青区间带（ZG/ZD）+ 虚线外沿（GG/DD），中枢按定义是重叠，不用涨跌色；
 *   买卖点     —— 一/二/三类买卖点，实心=已确认，空心=未确认；
 *   确认刻度   —— 本页的签名：结构**在哪一根K线上才可被看见**。
 *                 结构终点和确认点常常差很多根K线，回测能不能用就看后者。
 *   均线       —— 六档开关，短均线暖色/长均线冷色（颜色编码快慢，不占用涨跌色与中枢色）。
 *
 * 所有数字都来自后端 /api/structure（含 MACD、均线、复权后的价格），前端不做第二次计算：
 * 两边口径一旦分叉，页面上就会拿一条和买卖点无关的 MACD 去解释背驰，
 * 或者拿不复权的收盘价去算一条跨越除权日的均线。
 *
 * 复权是"整张图的前提"，不是某只票的属性：它和均线开关一样放在图正上方的工具条里，
 * 切换后行情、均线、结构（笔/段/中枢）一起换口径 —— 结构必须画在同一口径上。
 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // pb / ps 是**非严格口径专有**的短标签（盘整背驰形成的买卖点）。它们不许简写成
  // "一买"/"一卖"：第 60 课「严格来说，盘整背驰无所谓第一类买点，只是这样来类比」。
  // 后端 `DivergenceKind.name_cn` / `SignalKind` 是中文名的唯一实现处，这里只是图上的短标签。
  const KIND_CN = {
    b1: "一买", b2: "二买", b3: "三买", s1: "一卖", s2: "二卖", s3: "三卖",
    pb: "盘整背驰买", ps: "盘整背驰卖",
  };
  // 买/卖**不是**前缀规则：`pb`（盘整背驰买点）不以 "b" 开头，用 `startsWith("b")`
  // 会把买点画成卖点的颜色、标签挂到上方（实测 300201 日线非严格：4 个
  // 「盘整背驰买」全部是跌色 #4a9e7f / label 在 top）。
  // 这张表逐字对齐后端 `SignalKind.is_buy`（`chan/signal.py`：`("b1","b2","b3","pb")`），
  // 判据只许有一处实现，前端照抄而不是自己发明前缀规则。
  const BUY_KINDS = new Set(["b1", "b2", "b3", "pb"]);
  const isBuyKind = (kind) => BUY_KINDS.has(kind);
  const STATUS_CN = { confirmed: "确认", tentative: "未确认", invalidated: "已失效" };
  const PERIOD_CN = { day: "日线", week: "周线", month: "月线", 30: "30分", 5: "5分", 60: "60分", 15: "15分" };
  // 派生周期（周/月）由**本地日线**聚合而来，不单独同步。这句话必须出现在图注里：
  // 用户看到"周线"会以为是从数据源拿的一份独立数据，实际它跟着日线的复权口径走。
  const DERIVED_CN = { week: "day", month: "day" };
  // 趋势口径（第 20 课「走势中枢中心定理二」）：比的是**围绕中枢波动的区间 [DD,GG]**，
  // 不是中枢区间 [ZD,ZG]。后DD > 前GG → 上涨；后GG < 前DD → 下跌；两者都不满足
  // → 形成高级别的走势中枢（第 20 课把它和趋势并列，本系统显示为"盘整"）。
  // GG/DD 本身**不含离开段**（只取与中枢方向一致的 Zn），这是趋势判得出来的前提：
  // 若把离开段算进 GG/DD，两个相邻中枢的波动区间几乎总在重叠，趋势就永远判不出来。
  //
  // 这句话为什么必须出现：**本页没有"走势类型"分节**，趋势判据是隐形的，
  // 但它决定第一类买卖点出不出现（`chan/signal.py:215-224`：`classify_trends` 给出
  // 上涨/下跌的中枢组，组内不足两个中枢就跳过，`down_pivots`/`up_pivots` 为空时
  // B1/S1 一个都不会生成）。旧口径下趋势永远判不出来，等于第一类买卖点被静默关掉。
  // 所以图注要把口径说清楚，别让人以为买卖点是纯按中枢算的。
  const TREND_BASIS = "第一类买卖点取自趋势背驰；趋势按 [DD,GG] 波动区间判（第20课中心定理二；GG/DD 不含离开段，不是中枢区间 [ZD,ZG]）";
  // 价格按最小报价单位两位小数显示；原始值仍是引擎给的 float，这里只做呈现。
  const f2 = (v) => (v == null || v === "" ? "—" : Number(v).toFixed(2));
  // 成交量轴宽度只有 62px：40,000,000 会被截成 "00,000,000"，所以改成万/亿单位。
  function volLabel(v) {
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1) + "亿";
    if (a >= 1e4) return (v / 1e4).toFixed(0) + "万";
    return String(v);
  }

  // 复权三态。按钮上写的是**请求**的口径；实际生效口径与之不符时（例如这只票没有
  // 除权记录，前复权与不复权价格完全相同）由按钮旁的说明讲清楚，不让按钮替数据撒谎。
  const ADJUST_ORDER = ["qfq", "hfq", "raw"];
  const ADJUST_LABEL = { qfq: "前复权", hfq: "后复权", raw: "不复权" };
  // 均线六档：后端一次算全，前端只决定画哪几条。开关只是重画，不重新取数 ——
  // 重新取数会把用户刚放大的那段K线弹回默认窗口。
  const MA_PERIODS = [5, 10, 20, 60, 120, 250];
  const MA_COLORS = {
    5: "#e8c46a", 10: "#d2a052", 20: "#b8823f",
    60: "#8aa9a0", 120: "#6d8fa3", 250: "#5b7d94",
  };
  const MA_DEFAULT = [5, 10, 20, 60];
  const LS_MA = "chanlun.ma", LS_ADJUST = "chanlun.adjust";
  const LS_MODE = "chanlun.mode";
  const LS_WATCH = "chanlun.watch-collapsed";
  // 买卖点口径（D-32）。与复权口径一样是**整张图的前提**：它只放宽买卖点判据，
  // 笔/线段/中枢的划分一个字都不变，但图上买卖点的数量会变 —— 所以它必须出现在
  // 按钮上、图注里，并且**进请求**（服务端算买卖点，前端不自己判）。
  const MODE_ORDER = ["strict", "loose"];
  const MODE_LABEL = { strict: "严格", loose: "非严格" };
  // 背驰标注的配色：**形状与颜色都要能区分趋势背驰与盘整背驰** —— 只靠颜色的话
  // 色盲用户分不出来（第 15 课：盘整中无所谓"背驰"；两者不是同一个东西，
  // 图上不能画成一个样）。所以 kind 同时决定形状（趋势=三角 / 盘整=菱形）与色系
  // （趋势=深、盘整=浅），direction 决定冷暖（底背驰暖 / 顶背驰冷）。
  // 四个色都避开买卖点的涨跌红绿与中枢的靛青，免得"背驰"被读成"买卖点"。
  const DIV_COLOR = {
    trend: { bottom: "#e8a33d", top: "#8a6fd4" },
    consolidation: { bottom: "#f0c987", top: "#b9a8ee" },
  };

  // 存的是看图习惯，不是"上次服务端返回了什么"：换票、换级别都该保持。
  function readChoice(key, allowed, fallback) {
    let v = null;
    try { v = localStorage.getItem(key); } catch { v = null; }
    return allowed.includes(v) ? v : fallback;
  }
  function readMaChoice() {
    let raw = null;
    try { raw = localStorage.getItem(LS_MA); } catch { raw = null; }
    if (raw === null) return MA_DEFAULT.slice();
    // 空串 = 六条全关，是合法状态，必须和"没存过"区分开
    return raw.split(",").map((s) => parseInt(s, 10)).filter((p) => MA_PERIODS.includes(p));
  }
  function remember(key, value) {
    try { localStorage.setItem(key, value); } catch { /* 无痕模式：记不住就记不住 */ }
  }
  function readFlag(key) {
    try { return localStorage.getItem(key) === "1"; } catch { return false; }
  }
  // 一次要全六档：开关切换时手边就有数据，不必再等一次请求。
  const maQuery = () => MA_PERIODS.join(",");

  const state = {
    code: "600000",
    period: "day",
    limit: null,
    adjust: readChoice(LS_ADJUST, ADJUST_ORDER, "qfq"),
    // 与 adjust 同一套两段式：localStorage 只当**默认值**，URL 有值再覆盖（见文件末尾初始化块）
    mode: readChoice(LS_MODE, MODE_ORDER, "strict"),
    ma: new Set(readMaChoice()),
    watch: [],
    data: null,
    layers: { strokes: true, segments: true, pivots: true, signals: true, divergence: true, margin: true },
  };

  const chart = echarts.init($("#chart"), null, { renderer: "canvas" });
  // 容器尺寸要等样式表 + flex 布局算完才存在。首屏脚本早于布局执行时，init 量到的是
  // 一个几十像素的宽度，grid 就按那个宽度去算（实测只剩 35px 宽，整张图挤成一条竖线）。
  // 这里让容器真的拿到尺寸后再 resize 一次；之后窗口缩放、栏位折叠也都走同一条路。
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => chart.resize()).observe($("#chart"));
  }
  window.addEventListener("resize", () => chart.resize());

  // 鼠标当前悬停的**轴类目**（= 那一根K线的 `ts`）。背驰 tooltip 的反查要用它。
  //
  // 为什么不能只按价格反查：同一只票**两个不同日期恰好同价**时，两个背驰点的
  // `value[1]` 是同一个浮点数，`find` 对两者都返回数组里**靠前**的那条 ⇒ tooltip
  // 显示的是**另一个点**的判据。实测（`sh.600187` 日线严格，2022-01-04 与
  // 2024-01-12 都是 2.83）：hover `2024-01-12` 显示的是 `2022-01-04` 的文案。
  // 这不是理论风险：全史扫描 5471 只 × 2 口径 = 10942 个组合，命中 62 处同价不同日
  // （分布在 60 个组合里）；逐个真实鼠标 hover 核验 124/124 读到的都是自己的判据
  // （见 task-8-report.md §R3）。
  //
  // 为什么用 `updateAxisPointer` 取 ts：轴 tooltip 路径**不把数据项交给回调** ——
  // `tooltip.valueFormatter` 实收 `(value, dataIndex)` 两个**标量**参数，没有
  // `p.data` 可取（实测 `arg0_type: "number"`，见报告 §R3 第 0 步）。
  // 本事件实测在 `valueFormatter` **之前**触发，所以读到的就是这一帧的类目。
  // `e.axesInfo` 里 x 轴（`axisIndex: 0`）的 `value` 是**原始**类目下标 ——
  // 不是 `dataZoom` 过滤后的序号 —— 与 `state.data.bars` 一一对应。
  // 类目表**由 `draw()` 缓存**在 `axisCats` 里（就是它算出的那个 `ts` 数组）。
  // 原来是每次事件现取 `chart.getOption().xAxis[0].data`，等价但**极贵**：本机实测
  // 单次 `getOption()` 预热后中位 1.1–1.2 ms（min 0.8 / max 3.4），返回的 option
  // JSON 39.6 万–40.8 万字符（复审给的数字是 0.72 ms / 348875 字符，同一量级）；
  // 30 次真实鼠标移动 **1:1** 触发 30 次调用 ⇒ 按 30–60 次移动/秒算是单核的 3–7%，
  // 并按 0.4 MB（UTF-8 实测 39.6 万–40.8 万字节）× 30/秒 ≈ 每秒 12 MB 的速度产生短命对象。
  // 两者**逐字节等价**：`draw()` 里 `xAxes` 的 `axisBase.data` 就是 `ts` 本身
  // （四个窗格共用同一个数组），而 `state.data = body`（load）到 `draw()` 之间
  // **没有 `await`**，不存在缓存与 `divergences` 不同步的窗口。
  // 首屏 `draw()` 之前 `axisCats` 还是 null ⇒ 置 null，安全降级。
  //
  // **不要退回去用 `e.dataIndex`**：实测 12/12 次事件里 `axesInfo` 都带 x:0 项，
  // 且 `x0.value === e.dataIndex`；但 `dataIndex` 正是 R1/R2 那个错位缺陷的字段名，
  // 留一条以它兜底的支路，等于给下一个人留了"再串一次台"的入口。
  // 取不到 x 轴类目时宁可置 null —— tooltip 只显示价格、不显示**别人**的判据。
  let axisTs = null;
  let axisCats = null; // 由 draw() 写入：当前图上 x 轴类目表（= state.data.bars 的 ts）
  chart.on("updateAxisPointer", (e) => {
    const ai = (e.axesInfo || []).find((x) => x.axisDim === "x" && x.axisIndex === 0);
    axisTs = ai && axisCats && axisCats[ai.value] !== undefined ? axisCats[ai.value] : null;
  });

  // ------------------------------------------------------------------ 取数
  // 在途请求的序号。点一次周期 tab 会发**两个**请求（主图 `load()` + 自选
  // `loadWatch()`，见文件末尾 `.period` 的 click），而 `load()` 之间没有保护时
  // **后到的旧响应会覆盖 `state.data`** ⇒ 页面停在旧周期的数据上，徽标显示的是
  // **旧周期**的数字（旧周期 0 处时才为空）。
  // 复审实测这是**确定性**的、不是偶发：逐请求计时 `/api/structure?period=week`
  // 5863→6461 ms 而 `period=day` 5879→5939 ms —— 先发的旧请求后完成。
  // 每次 `load()` 领一个号，`await` 回来后号变了就把自己整段丢掉（不碰 state、
  // 不画图、不改图注）。正常单次点击时号没变，刷新路径完全不受影响。
  let loadSeq = 0;
  async function load() {
    const seq = ++loadSeq;
    const qs = new URLSearchParams({ code: state.code, period: state.period });
    if (state.limit) qs.set("limit", String(state.limit)); // 未指定时用后端默认窗口（1200）
    qs.set("adjust", state.adjust);
    // 口径必须进请求：买卖点与背驰都在服务端算，不带这个参数的话按钮变了图上不变。
    // 无条件设置（与 adjust 一致），书签里的查询串因此恒有 mode=。
    qs.set("mode", state.mode);
    qs.set("ma", maQuery());
    setStamp("加载中…");
    let resp;
    try {
      resp = await fetch(`/api/structure?${qs}`);
    } catch (err) {
      // 旧请求连不上时也不许弹提示：那是上一次点击的事，新请求可能已经成功了。
      if (seq !== loadSeq) return;
      return showNotice("连不上本地服务", `请确认服务在本机 8888 端口运行：${err}`, "python -m chanlun serve");
    }
    const body = await resp.json().catch(() => ({}));
    // ★ 从这里往下都有副作用（写 state、重绘图表/图注/自选栏、弹提示）：旧响应必须整段丢弃。
    if (seq !== loadSeq) return;
    if (!resp.ok) {
      // 404 = 这个周期本地真没有数据。命令行提示是给终端用户的，看盘的人需要能点的东西。
      // 但**不是每个 404 都能补**：指数没有分钟线（`syncable: false`），给一个必然
      // 400 的按钮比不给更糟 —— 用户点了、等了、被拒，才知道这条路是死的。
      // 能不能补由后端判（`markets.is_index` + 分钟周期），前端不自己猜号段。
      const canSync = resp.status === 404 && body.syncable !== false;
      const action = canSync
        ? { label: "同步这个周期", run: syncThisPeriod }
        : null;
      // 左栏高亮也要跟着走：404 时图表区不换，但"我正在看哪一只"已经变了。
      // 不挪的话输入框写着 399006、左栏还高亮着 sh.000001，高亮就成了一句假话。
      renderWatch();
      return showNotice(
        httpTitle(resp.status), body.detail || "接口返回了无法解析的内容。", null, action);
    }
    state.data = body;
    applyAdjustUI(body);
    applyDivergenceBadge(body);
    hideNotice();
    draw();
    renderLedger();
    renderWatch(); // 高亮"图上是哪一只"：换票后自选栏要跟着动
    const c = body.counts;
    const t = body.counts_total;
    // 结构是**全史**算出来的，窗口只决定画多少：两边数不一样时明说还有多少没画，
    // 否则用户会把"窗口里 12 段"读成"这只票只有 12 段"。
    const totals = t && (t.segments !== c.segments || t.pivots !== c.pivots || t.signals !== c.signals)
      ? `\n全史 ${t.segments} 段 / ${t.pivots} 中枢 / ${t.signals} 买卖点（这里只画与窗口相交的部分）`
      : "";
    setStamp(
      `${body.code}${body.name ? " " + body.name : ""} · ${PERIOD_CN[body.period] || body.period} · 截至 ${body.as_of}\n` +
      `线段 ${c.segments}（确认 ${c.confirmed_segments} / 未确认 ${c.tentative_segments}） · 中枢 ${c.pivots} · 买卖点 ${c.signals}` +
      totals + "\n" + basisLine(body)
    );
  }

  // 图注最后一行：周期来源 + 趋势口径。周/月的最后一根**还没走完**时必须标出来 ——
  // 一根还在变的周K 被当成定论去数中枢，是这套系统最容易骗到自己的地方。
  function basisLine(body) {
    const parts = [];
    if (body.derived) {
      parts.push(`${PERIOD_CN[body.period] || body.period}由${PERIOD_CN[body.base_period] || body.base_period}聚合`
        + (body.partial === true ? "（最后一根未走完）" : ""));
    }
    // 口径不同，这最后一句必须不同：非严格模式下第一类**还会来自盘整背驰**，
    // 照旧印「第一类买卖点取自趋势背驰」就是一句与图上结果相反的话 ——
    // 正是 §5 第 5 条那一类事故（后端改了口径、图注还印着旧口径）。
    // 严格模式必须**逐字**输出 TREND_BASIS：那句话在严格口径下是对的，不许改写它。
    if (state.mode === "loose") {
      parts.push("非严格口径：第一类之外另收盘整背驰（第27课「类第一类」；第60课：盘整背驰无所谓第一类买点，只是类比），第二类不要求前置第一类，第三类回试容忍中枢高度 10%（工程口径，无原文依据）");
    } else {
      parts.push(TREND_BASIS);
    }
    return parts.join(" · ");
  }

  // 工具条上的口径：按钮写"请求"的口径，实际生效口径不一致时用一句话说清原因。
  // 例：这只票没有除权记录 → 请求前复权，实际就是原始价，按钮仍显示"前复权"，
  // 旁边的说明写明"无除权记录（三态相同）"，否则用户会以为复权算错了。
  function applyAdjustUI(body) {
    const btn = $("#adjust-btn");
    btn.textContent = `复权 · ${ADJUST_LABEL[state.adjust] || state.adjust}`;
    btn.title = `点击切换复权口径：${ADJUST_ORDER.map((m) => ADJUST_LABEL[m]).join(" → ")}`;
    const note = $("#adjust-note");
    const eff = body && body.adjust_effective;
    const text = (body && body.adjust_note) || "";
    if (!text) {
      note.textContent = "";
      note.hidden = true;
      return;
    }
    note.textContent = text;
    note.hidden = false;
    note.classList.toggle("is-warn", Boolean(eff) && eff !== state.adjust);
  }

  // 买卖点口径按钮：两态循环。与复权按钮同一个控件形态 —— 文字写当前口径，
  // `title` 解释两种口径的差别与"点一下会切到哪"（照 applyAdjustUI 的写法，
  // **不用 aria-pressed**：那是有"按下/弹起"语义的开关才用的，循环按钮上语义是错的）。
  // 非严格模式额外挂 `is-loose`：口径会改变图上买卖点的数量，光靠按钮上的字不够醒目。
  function applyModeUI() {
    const btn = $("#mode-btn");
    if (!btn) return;
    const loose = state.mode === "loose";
    btn.textContent = `口径：${MODE_LABEL[state.mode]}`;
    btn.classList.toggle("is-loose", loose);
    btn.title = loose
      ? "非严格：含盘整背驰买卖点、第二类不要求前置第一类、第三类回试有容忍度（容忍度为工程口径，无原文依据）。笔/线段/中枢划分不变。点击切回严格。"
      : "严格：第一类只取自趋势背驰，第二类需前置第一类，第三类回试不得回到中枢内。点击切到非严格。";
  }

  // 同一 `(ts, price)` 的背驰**是同一个位置上的两种背驰**（后端同一次事件同时给了
  // 盘整背驰与趋势背驰两条，价格是同一个浮点数）。它们必须合并成**一个**数据点：
  // 不合并的话 ECharts 会在同一像素叠两个标记，而按值反查 tooltip 只能命中第一条 ——
  // 表现为「两行一模一样的文案」，且**第二条的 reason 永远读不到**。
  // 实测样本：`sh.600588` 2024-08-28 @ 8.03（盘整 + 趋势）、
  // `sz.300913` 2025-08-28 @ 69.78667905（盘整 + 趋势）。
  //
  // 分组只按**精确相等**（`===`）：同一事件的两条来自后端同一个浮点数，
  // 用近似相等反而会把相邻两笔的价位误并。
  // 合并点落在组内**第一条**的位置，所以原有的时间顺序不变。
  function mergeDivergencesByPoint(list) {
    // key 用 NUL 拼接两个字段：ts 与 price 里都不可能出现 NUL，不会串键
    const groups = new Map();
    for (const div of list) {
      const key = `${div.ts}\x00${div.price}`;
      const g = groups.get(key);
      if (g) g.push(div);
      else groups.set(key, [div]);
    }
    const out = [];
    for (const g of groups.values()) {
      const first = g[0];
      // 形状/颜色/rotate 取**趋势**那条：趋势背驰更稀有、语义更强，而且同一价位
      // 重叠时盘整的菱形本来就会被趋势的三角盖住 —— 取趋势才与视觉结果一致。
      // 组内全是盘整背驰时自然取盘整背驰。`kind`/`kind_cn` 与形状取同一条。
      const lead = g.find((d) => d.kind === "trend") || first;
      out.push({
        ts: first.ts,
        price: first.price,
        kind: lead.kind,
        kind_cn: lead.kind_cn,
        direction: lead.direction,
        // 组内**每一条**的 reason 都保留，按原有先后顺序用"；"连接 —— 一条都不许丢。
        reason: g.map((d) => d.reason || "").filter(Boolean).join("；"),
        name: g.map((d) => d.kind_cn || d.kind).join("／"),
      });
    }
    return out;
  }

  // 背驰图层按钮上的**已加载区间**计数徽标。默认视野常常一处背驰都画不到（窗口只含
  // 最近一小段K线），按钮亮着而图上空的，会被读成"这功能坏了" —— 把"已加载的这段里
  // 有多少处"写在按钮上，用户才分得清"数据里就没有"与"功能没生效"。
  // 数字取**合并后**的处数（**已加载区间内**的处数，不是窗口过滤后的数量）：
  // 它不监听 dataZoom，所以永不陈旧；也与图层开关无关 —— 它说的是数据里有多少处
  // 背驰，不是"现在画了几处"。为 0 时不显示数字，免得制造噪音。
  //
  // ★ 这个数字**不是全史**，`title` 里因此不许无条件写"全史"：
  // `divergences` 取自**被裁剪过的**快照（`api.py:416`
  // `snap.clipped_to(第一根 bar 的 ts, last_ts)`），裁剪边界就是 `limit` 送出的那批 bar，
  // 所以它**随 `limit` 变**。实测 `sz.399001` 日线：默认 1200 根 -> 3 处，
  // `limit=9000`（全史 8648 根）-> 31 处 —— 差 10 倍。默认视野下写"全史 3 处"，
  // 用户会以为这只票历史上只背驰过 3 次。
  // （对照：图注里 `counts_total` 那句"全史 N 段"**是对的**，那个数不随 limit 变。）
  // 判据：送出的 bar 数 vs 该票全史根数 —— 两个字段都在 payload 顶层
  // （`api.py:533` 的 `bars` 与 `api.py:537` 的 `bars_total`），实测都存在。
  // **取不到时不许猜**：按"已加载区间"兜底，宁可少说，不说量不出来的话。
  function applyDivergenceBadge(body) {
    const btn = document.querySelector('.rail-tab[data-layer="divergence"]');
    if (!btn) return;
    // 数字用**合并后**的处数，与图上标记数一致：用后端原始条数的话，
    // `sh.600588` 会变成「徽标 7、图上 6 个标记」，看起来像 bug。
    const n = body && body.divergences ? mergeDivergencesByPoint(body.divergences).length : 0;
    const badge = $("#divergence-badge");
    if (badge) badge.textContent = n ? String(n) : "";
    // 量词用「处」不用「条」：两处背驰落在同一时间同一价位 = 同一个**位置**上的
    // 两种背驰，那个位置只画一个标记，"处"才对得上图。
    const nBars = body && Array.isArray(body.bars) ? body.bars.length : null;
    const total = body && Number.isFinite(body.bars_total) ? body.bars_total : null;
    if (nBars !== null && total !== null && nBars === total) {
      // 已加载的就是全史（新股，或 limit 大到覆盖全史）⇒ 这里说的"全史"是真话，
      // 两句都与改动前**逐字节相同**（≤1200 根的票输出完全不变）。
      btn.title = n
        ? `背驰标注：全史 ${n} 处（这里只画与当前窗口相交的部分，默认视野可能一条都不含）。开关只决定画不画，不影响这个数字。`
        : "背驰标注：这只票的全史没有背驰（与当前窗口无关）。";
      return;
    }
    // 已加载 < 全史：四个事实都要说全 —— ① 这是已加载区间不是全史 ② 加载了多少根
    // ③ 总共多少根 ④ 怎么看到更早的。总数缺失时整段省掉 ③④，不拿猜的数填空。
    const scope = nBars !== null ? `已加载的最近 ${nBars} 根K线内` : "已加载区间内";
    const tail = nBars !== null && total !== null ? `（共 ${total} 根；加大 limit 可看更早）` : "";
    btn.title = n
      ? `背驰标注：${scope} ${n} 处${tail}。这里只画与当前窗口相交的部分，默认视野可能一处都不含。开关只决定画不画，不影响这个数字。`
      : `背驰标注：${scope}没有背驰${tail}。与当前窗口无关。`;
  }

  function httpTitle(status) {
    if (status === 404) return "没有这只票的本地数据";
    if (status === 400) return "代码或参数不对";
    return `接口出错（HTTP ${status}）`;
  }

  function showNotice(title, detail, command, action) {
    const box = $("#notice");
    box.innerHTML = "";
    const h = document.createElement("h3");
    h.textContent = title;
    const p = document.createElement("p");
    p.textContent = detail;
    box.append(h, p);
    if (action) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "notice-action";
      btn.textContent = action.label;
      btn.addEventListener("click", action.run);
      box.append(btn);
    }
    if (command) {
      const code = document.createElement("code");
      code.textContent = command;
      box.append(code);
    }
    box.hidden = false;
    chart.clear();
    setStamp("—");
  }

  function hideNotice() { $("#notice").hidden = true; }

  // ------------------------------------------------------- 同步这个周期
  // 首次拉一只票的 5 分钟线实测要 149 秒（78432 根），30 分钟线 30 秒。所以点下去
  // **不能等请求返回**：POST 只负责排队，结果靠轮询。轮询必须有上限——无限转圈
  // 会让人以为"永远同步不完"，而真相比这更简单：服务端可能已经挂了。
  const SYNC_POLL_MS = 2000;
  const SYNC_MAX_MS = 15 * 60 * 1000;

  function syncNote(text, warn) {
    const box = $("#notice");
    let note = box.querySelector(".sync-note");
    if (!note) {
      note = document.createElement("p");
      note.className = "sync-note";
      box.append(note);
    }
    note.textContent = text;
    note.classList.toggle("is-warn", Boolean(warn));
  }

  function syncButton() { return $("#notice .notice-action"); }

  async function syncThisPeriod() {
    const btn = syncButton();
    const cn = PERIOD_CN[state.period] || state.period;
    const started = Date.now();
    const tick = () => {
      if (!btn) return;
      btn.disabled = true;
      btn.textContent = `正在同步${cn}… 已 ${Math.round((Date.now() - started) / 1000)}s`;
    };
    tick();
    syncNote(`正在向行情源拉取${cn}数据，首次可能要一两分钟，请不要关掉页面。`, false);

    let resp;
    try {
      resp = await fetch("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: state.code, period: state.period }),
      });
    } catch (err) {
      return finishSync(null, `连不上本地服务：${err}`);
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      // 400 是**确定性拒绝**（例如"指数没有分钟线"）：再点一百次结果一样，
      // 所以把按钮撤掉，只留原因，不做"重试"这种假承诺。
      if (btn) btn.remove();
      return syncNote(`无法同步：${body.detail || `HTTP ${resp.status}`}`, true);
    }

    const timer = setInterval(tick, 500);
    const status = await pollSync(started);
    clearInterval(timer);
    finishSync(status, null);
  }

  async function pollSync(started) {
    while (Date.now() - started < SYNC_MAX_MS) {
      await new Promise((r) => setTimeout(r, SYNC_POLL_MS));
      const qs = new URLSearchParams({ code: state.code, period: state.period });
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

  function finishSync(status, error) {
    const btn = syncButton();
    if (error || !status || status.state === "error") {
      const msg = error || status.error || "同步失败";
      if (btn) {
        btn.disabled = false;
        btn.textContent = "重试";
      }
      return syncNote(`同步失败：${msg}`, true);
    }
    if (status.state === "skipped") {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "重试";
      }
      return syncNote("行情源没有返回这个周期的数据（可能是真空区间，也可能这只票没有分钟线）。", true);
    }
    syncNote(`同步完成：${status.rows} 根（${status.start_ts || "?"} → ${status.end_ts || "?"}）。`, false);
    load(); // 数据到位了，图应该出来
  }

  function setStamp(text) { $("#stamp").textContent = text; }

  // ------------------------------------------------------------------ 绘图
  // 重画时把当前缩放窗口带过去：开关均线、开关图层都只是重画，
  // 不该把用户刚放大的那一段K线弹回默认窗口（那是"看一眼细节"之后最恼人的事）。
  function currentZoom() {
    const z = ((chart.getOption() || {}).dataZoom || [])[0];
    if (!z) return null;
    if (z.startValue != null && z.endValue != null) {
      const ts = state.data ? state.data.bars.map((b) => b.ts) : [];
      if (ts.includes(z.startValue) && ts.includes(z.endValue)) {
        return { startValue: z.startValue, endValue: z.endValue };
      }
    }
    if (typeof z.start === "number" && typeof z.end === "number") {
      return { start: z.start, end: z.end };
    }
    return null;
  }

  function draw() {
    const d = state.data;
    const bars = d.bars;
    const ts = bars.map((b) => b.ts);
    // x 轴类目表 = 这个数组本身（下面 `axisBase.data` 用的就是它）。
    // 缓存给 `updateAxisPointer` 用，省掉每次鼠标移动一次 `chart.getOption()`（实测 0.72 ms）。
    axisCats = ts;
    const idxOf = new Map(ts.map((t, i) => [t, i]));
    const up = cssVar("--cinnabar"), down = cssVar("--bamboo");
    const indigo = cssVar("--indigo"), mohui = cssVar("--mohui"), amber = cssVar("--amber");
    const rice = cssVar("--rice"), dim = cssVar("--rice-dim"), line = cssVar("--line");
    const keepZoom = currentZoom();

    const nb = bars.length;
    const win = Math.min(nb, Math.max(120, Math.min(250, nb)));
    const startPct = nb > win ? (1 - win / nb) * 100 : 0;

    // --- 笔 / 线段 / 中枢：都挂在一个不画数据的隐藏 series 上，
    //     用 per-item lineStyle 区分确认与未确认（ECharts 的 markLine 支持逐条样式）。
    //
    //     ⚠ 每条 markLine 必须是「两个点组成的数组」：`[[{coord:[x1,y1]}, {coord:[x2,y2]}]]`。
    //     ECharts 5.5.1 的数据项只认 coord / xAxis / yAxis；写成 `{coords: [[..],[..]]}`
    //     会让整个 setOption 抛 `Cannot read properties of undefined (reading 'coord')`，
    //     而这条异常会让整张图（含 K 线）一个像素都不画——表现为「页面一直显示加载中、
    //     画布空白」。已用无头浏览器逐项对照验证：coords 全量失败，嵌套点对正常渲染。
    const markLines = [];
    const seg2 = ([x1, y1], [x2, y2], style) => [
      { coord: [x1, y1], lineStyle: style },
      { coord: [x2, y2] },
    ];
    if (state.layers.pivots) {
      for (const p of d.pivots) {
        for (const [y, tag] of [[p.gg, "GG"], [p.dd, "DD"]]) {
          markLines.push(seg2(
            [p.start_ts, y],
            [p.end_ts, y],
            { color: indigo, width: 1, type: "dashed", opacity: p.status === "tentative" ? 0.3 : 0.55 }
          ));
        }
      }
    }
    if (state.layers.strokes) {
      for (const s of d.strokes) {
        const tentative = s.status === "tentative";
        markLines.push(seg2(
          [s.start.ts, s.start.price],
          [s.end.ts, s.end.price],
          {
            color: s.direction > 0 ? up : down,
            width: 1,
            opacity: tentative ? 0.35 : 0.6,
            type: tentative ? "dashed" : "solid",
          }
        ));
      }
    }
    if (state.layers.segments) {
      for (const g of d.segments) {
        const a = g.start.start, b = g.end.end;
        const tentative = g.status === "tentative";
        markLines.push(seg2(
          [a.ts, a.price],
          [b.ts, b.price],
          {
            color: g.direction > 0 ? up : down,
            width: tentative ? 1.6 : 2.4,
            opacity: tentative ? 0.5 : 0.95,
            type: tentative ? "dashed" : "solid",
            shadowBlur: tentative ? 0 : 4,
            shadowColor: g.direction > 0 ? up : down,
          }
        ));
      }
    }

    const markAreas = state.layers.pivots
      ? d.pivots.map((p) => [
          {
            name: `中枢 ${f2(p.zd)}–${f2(p.zg)}`,
            xAxis: p.start_ts,
            yAxis: p.zd,
            itemStyle: {
              color: p.status === "tentative" ? "rgba(124,156,196,0.05)" : "rgba(124,156,196,0.11)",
              borderColor: indigo,
              borderWidth: 1,
              borderType: p.status === "tentative" ? "dashed" : "solid",
              opacity: p.status === "tentative" ? 0.5 : 1,
            },
            label: {
              show: true,
              position: "insideTopLeft",
              color: indigo,
              fontSize: 10,
              formatter: `中枢 ${PERIOD_CN[p.level] || p.level} · ZG ${f2(p.zg)} / ZD ${f2(p.zd)}`,
            },
          },
          { xAxis: p.end_ts, yAxis: p.zg },
        ])
      : [];

    // --- 分型：只标画在笔端点上的分型（其余分型不参与笔，画出来只是噪声）
    const fractals = [];
    if (state.layers.strokes) {
      const seen = new Set();
      for (const s of d.strokes) {
        for (const f of [s.start, s.end]) {
          const key = `${f.ts}|${f.price}|${f.kind}`;
          if (seen.has(key)) continue;
          seen.add(key);
          fractals.push({
            value: [f.ts, f.price],
            symbol: "triangle",
            symbolRotate: f.kind === "top" ? 180 : 0,
            itemStyle: { color: f.kind === "top" ? down : up, opacity: 0.55 },
          });
        }
      }
    }

    // --- 买卖点
    const signals = [];
    if (state.layers.signals) {
      for (const s of d.signals) {
        const buy = isBuyKind(s.kind); // 见 BUY_KINDS：pb 是买点，前缀判断会画反
        const tentative = s.status === "tentative";
        signals.push({
          value: [s.ts, s.price],
          label: {
            show: true,
            formatter: KIND_CN[s.kind] || s.kind,
            position: buy ? "bottom" : "top",
            color: buy ? up : down,
            fontSize: 11,
          },
          itemStyle: tentative
            ? { color: "transparent", borderColor: buy ? up : down, borderWidth: 1.6 }
            : { color: buy ? up : down, borderColor: rice, borderWidth: 1 },
          tooltip: {
            formatter: () =>
              `${KIND_CN[s.kind]}（${STATUS_CN[s.status]}）<br/>${s.ts} @ ${s.price}` +
              `<br/>确认时间：${s.confirmed_at || "尚未确认"}`,
          },
        });
      }
    }

    // --- 背驰标注
    // 两种口径下**都**标注背驰：口径的差别是"非严格把盘整背驰另算作买卖点"（后端算），
    // 不是"只有非严格才画背驰"。趋势/盘整**形状与颜色都不同**，见 DIV_COLOR 的注释。
    const divergences = [];
    if (state.layers.divergence) {
      for (const div of mergeDivergencesByPoint(d.divergences || [])) {
        const trend = div.kind === "trend";
        // direction：+1 = 顶背驰，-1 = 底背驰（与笔/线段/买卖点同一套符号约定）
        const top = div.direction > 0;
        divergences.push({
          value: [div.ts, div.price],
          // 中文名只许有一处实现（后端 `DivergenceKind.name_cn`）：这里只读 payload，
          // 不在前端写第二份中文名 —— 缺字段时退化成枚举名，而不是自己编一个。
          // 合并点会把组内各条的 kind_cn 用"／"连起来（同一个位置上的两种背驰）。
          name: div.name,
          symbol: trend ? "triangle" : "diamond",
          symbolRotate: trend && top ? 180 : 0, // 照分型层：三角朝下 = 顶背驰
          itemStyle: { color: DIV_COLOR[trend ? "trend" : "consolidation"][top ? "top" : "bottom"] },
          // 说明文案直接用后端给的 reason（第15/60课的判据细节都在里面），
          // 前端不拼第二份解释。合并点会把组内各条的 reason 用"；"连起来。
          reason: div.reason || "",
        });
      }
    }

    // --- 确认刻度（签名层）：结构可被看见的那一根K线
    const margin = [];
    if (state.layers.margin) {
      const push = (confirmedAt, tag, color, endTs) => {
        const i = idxOf.get(confirmedAt);
        if (i === undefined) return;
        margin.push({
          value: [confirmedAt, 0.5],
          itemStyle: { color },
          tooltip: {
            formatter: () =>
              `${tag}<br/>结构终点：${endTs}<br/>确认于：${confirmedAt}` +
              `<br/>滞后：${i - (idxOf.get(endTs) ?? i)} 根K线`,
          },
        });
      };
      for (const g of d.segments) push(g.confirmed_at, `线段 ${g.idx}（${g.direction > 0 ? "上" : "下"}）`, indigo, g.end.end.ts);
      for (const p of d.pivots) push(p.confirmed_at, `中枢 ${p.idx}`, amber, p.end_ts);
      for (const s of d.signals) push(s.confirmed_at, `${KIND_CN[s.kind]} @ ${s.ts}`, up, s.ts);
    }

    const volume = bars.map((b, i) => ({
      value: b.volume,
      itemStyle: { color: b.close >= b.open ? up : down, opacity: 0.55 },
    }));
    const hist = d.macd.hist.map((v) => ({
      value: v,
      itemStyle: { color: v >= 0 ? up : down, opacity: 0.7 },
    }));

    // --- 均线：数值由后端在**当前复权口径**下算好（/api/structure 的 ma）。
    //     前端只决定画哪几条 —— 若在这里用 bars 自己 rolling，不复权窗口下算出的
    //     MA60 会横跨除权缺口，图上就会多出一条谁也没见过的均线。
    const ma = d.ma || {};
    const maSeries = [];
    for (const p of MA_PERIODS) {
      const vals = ma[String(p)];
      if (!vals || !state.ma.has(p)) continue;
      maSeries.push({
        name: `MA${p}`,
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        // null 必须原样保留：补成 0 会让均线从坐标原点拉一条假线下来
        data: vals.map((v) => (v == null ? null : v)),
        showSymbol: false,
        connectNulls: false,
        silent: true,
        z: 4, // 压在K线之上、结构线（markLine z=5）之下
        lineStyle: { color: MA_COLORS[p], width: p <= 20 ? 1.2 : 1, opacity: p <= 20 ? 0.95 : 0.7 },
      });
    }

    const axisBase = {
      type: "category",
      data: ts,
      boundaryGap: true,
      axisLine: { lineStyle: { color: line } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      axisPointer: { label: { show: false } },
    };
    const xAxes = [0, 1, 2, 3].map((i) => ({
      ...axisBase,
      gridIndex: i,
      axisLabel: i === 3
        ? { show: true, color: dim, fontSize: 10, hideOverlap: true }
        : { show: false },
    }));

    const option = {
      animation: false,
      backgroundColor: "transparent",
      textStyle: { fontFamily: cssVar("--cjk"), color: rice },
      axisPointer: { link: [{ xAxisIndex: "all" }], lineStyle: { color: dim, type: "dashed" } },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#12161b",
        borderColor: line,
        textStyle: { color: rice, fontSize: 12 },
        axisPointer: { type: "cross" },
      },
      // 四个面板自下而上留出底部 dataZoom 的位置；K线是主工作面，占最大一块。
      // 百分比之间不要留空档：之前 K线 height 40% 而成交量 top 59%，中间空出一条
      // 近百像素的横带（实测 243→337），主图白白矮了一截。
      grid: [
        { left: 62, right: 18, top: 12, height: "52%" },
        { left: 62, right: 18, top: "56%", height: "10%" },
        { left: 62, right: 18, top: "68%", height: "12%" },
        { left: 62, right: 18, top: "82%", height: "5%" },
      ],
      xAxis: xAxes,
      yAxis: [
        {
          gridIndex: 0, scale: true, position: "left",
          axisLine: { lineStyle: { color: line } },
          splitLine: { lineStyle: { color: line, opacity: 0.35 } },
          axisLabel: { color: dim, fontSize: 10, fontFamily: cssVar("--mono") },
        },
        {
          gridIndex: 1, splitNumber: 2, min: 0, axisLine: { lineStyle: { color: line } },
          splitLine: { show: false },
          axisLabel: {
            color: dim, fontSize: 9, fontFamily: cssVar("--mono"),
            formatter: volLabel,
          },
        },
        {
          gridIndex: 2, scale: true, splitNumber: 3, axisLine: { lineStyle: { color: line } },
          splitLine: { lineStyle: { color: line, opacity: 0.25 } },
          axisLabel: { color: dim, fontSize: 9, fontFamily: cssVar("--mono") },
        },
        { gridIndex: 3, min: 0, max: 1, show: false },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1, 2, 3], ...(keepZoom || { start: startPct, end: 100 }), zoomOnMouseWheel: true },
        {
          type: "slider", xAxisIndex: [0, 1, 2, 3], ...(keepZoom || { start: startPct, end: 100 }),
          bottom: 4, height: 16, borderColor: line, fillerColor: "rgba(124,156,196,0.12)",
          handleStyle: { color: dim }, textStyle: { color: dim, fontSize: 10 },
          dataBackground: { lineStyle: { color: line }, areaStyle: { color: line, opacity: 0.2 } },
        },
      ],
      series: [
        {
          name: "K线", type: "candlestick", xAxisIndex: 0, yAxisIndex: 0, data: bars.map((b) => [b.open, b.close, b.low, b.high]),
          itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
        },
        ...maSeries,
        {
          // 结构叠加层：本身不画数据，只承载 markLine（笔/线段/GG-DD）与 markArea（中枢）
          name: "结构", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: [], silent: true,
          markLine: {
            symbol: ["none", "none"], animation: false, label: { show: false },
            silent: true, emphasis: { disabled: true }, data: markLines,
          },
          markArea: { silent: true, data: markAreas },
        },
        {
          name: "分型", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, data: fractals,
          symbolSize: 6, tooltip: { show: false }, z: 6,
        },
        {
          name: "买卖点", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, data: signals,
          symbol: "circle", symbolSize: 9, z: 10,
        },
        {
          name: "背驰", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, data: divergences,
          symbolSize: 11, z: 9,
          tooltip: {
            // 全局 tooltip 是 `trigger: "axis"`。ECharts 5.6 的 axis 路径**不调用**
            // series/item 级的 `tooltip.formatter`（它直接走 `formatTooltip()` 的默认内容），
            // 只认 `tooltip.valueFormatter` —— 实测见 task-8-report.md 的探针。
            // 所以后端的 `reason` 挂在这里，hover 才真的看得到；
            // 说明文案仍然只有后端一处实现，前端不拼第二份。
            //
            // 轴路径**不给数据项**：实测本回调收到的是 `(value, dataIndex)` 两个标量，
            // `value` 就是该点的 `value[1]`，没有 `p.data` 可读（报告 §R3 第 0 步）。
            //
            // **不要用第二个参数（那个"过滤后序号"）去索引 `divergences`**：
            // `dataZoom.filterMode` 默认是 `"filter"`，ECharts 传进来的是
            // **过滤后**的序号，而 `divergences` 是**全量**数组 —— 两者错位，
            // hover 到的是**别人**的说明。
            // 实测（sh.600000 日线严格，默认视野）：唯一可见的是 2026-09-23 那条，
            // 显示的却是 2021-11-10 那条的说明。首屏即可见。
            // 改成反查，且键必须是 **`(ts, price)` 两元组**：只按 price 反查时，
            // 两个不同日期恰好同价的点会串台（hover 谁都是靠前那条的判据）——
            // 实测 `sh.600187` 2022-01-04 与 2024-01-12 都是 2.83，报告 §R3。
            // `ts` 取 `axisTs`（由 `updateAxisPointer` 记录，见 `chart` 初始化处），
            // 与下标、与窗口、与过滤模式全都无关。
            // 合并过的点（`mergeDivergencesByPoint` 的产物）天然是唯一的 `(ts, price)`，
            // 所以这里最多命中一条，两条 reason 也已在该点的 `reason` 里连好。
            valueFormatter: (value) => {
              const div = divergences.find(
                (x) => x.value[1] === value && x.value[0] === axisTs
              );
              const text = f2(value); // 同页其它数值都走 f2，别让 tooltip 露原始浮点
              return div && div.reason ? `${text}｜${div.reason}` : text;
            },
          },
        },
        {
          name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volume, barWidth: "62%",
        },
        {
          name: "MACD柱", type: "bar", xAxisIndex: 2, yAxisIndex: 2, data: hist, barWidth: "55%",
        },
        {
          name: "DIF", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: d.macd.dif,
          showSymbol: false, lineStyle: { color: rice, width: 1 }, tooltip: { show: false },
        },
        {
          name: "DEA", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: d.macd.dea,
          showSymbol: false, lineStyle: { color: amber, width: 1 }, tooltip: { show: false },
        },
        {
          name: "确认刻度", type: "scatter", xAxisIndex: 3, yAxisIndex: 3, data: margin,
          symbol: "rect", symbolSize: [4, 11], z: 8,
        },
      ],
    };

    chart.setOption(option, { notMerge: true });
    chart.resize();
  }

  // ------------------------------------------------------------------ 结构清单
  function renderLedger() {
    const d = state.data;
    const box = $("#ledger");
    box.innerHTML = "";
    if (!d) return;

    const segBlock = block("线段", d.segments.length);
    d.segments.forEach((g) => {
      const a = g.start.start, b = g.end.end;
      segBlock.append(row({
        idx: g.idx,
        title: `${g.direction > 0 ? "向上" : "向下"}线段`,
        dirClass: g.direction > 0 ? "dir-up" : "dir-down",
        status: g.status,
        confirmedAt: g.confirmed_at,
        meta: `${a.ts} ${f2(a.price)} → ${b.ts} ${f2(b.price)} · ${g.stroke_count} 笔 · ${g.start_stroke_idx}-${g.end_stroke_idx}`,
        startTs: a.ts, endTs: b.ts,
      }));
    });
    box.append(segBlock);

    const pivBlock = block("中枢", d.pivots.length);
    if (!d.pivots.length) {
      // 中枢不是"扫一遍数据就能挑出来"的东西：它至少要 3 段确认线段出现重叠。
      // 窗口从结构中间切入时，段数不够是常态（实测 4 只样本在 500 根窗口下都是 0 中枢，
      // 全窗 1212 根才各 2 个）。这里把真实原因说出来，并给出可执行的下一步。
      const n = d.counts.confirmed_segments;
      pivBlock.append(emptyLine(
        n < 3
          ? `本窗口内没有中枢：中枢至少要 3 段确认线段重叠，当前窗口只有 ${n} 段确认线段。把「根数」调大（例如 1200）再看。`
          : `本窗口的 ${n} 段确认线段之间没有出现三段重叠，因此不构成中枢。`
      ));
    }
    d.pivots.forEach((p) => {
      pivBlock.append(row({
        idx: p.idx,
        title: `中枢 ${PERIOD_CN[p.level] || p.level}`,
        dirClass: "",
        status: p.status,
        confirmedAt: p.confirmed_at,
        meta: `ZG ${f2(p.zg)} / ZD ${f2(p.zd)} · GG ${f2(p.gg)} / DD ${f2(p.dd)} · ${p.start_ts} → ${p.end_ts}`,
        startTs: p.start_ts, endTs: p.end_ts,
      }));
    });
    box.append(pivBlock);

    const sigBlock = block("买卖点", d.signals.length);
    if (!d.signals.length) {
      sigBlock.append(emptyLine("当前级别的结构没有触发买卖点。这只说明按现有定义没出现，不等于「没有机会」。"));
    }
    d.signals.forEach((s) => {
      const buy = isBuyKind(s.kind); // 见 BUY_KINDS：pb 是买点，前缀判断会画反
      sigBlock.append(row({
        idx: s.idx,
        title: KIND_CN[s.kind] || s.kind,
        dirClass: buy ? "dir-up" : "dir-down",
        status: s.status,
        confirmedAt: s.confirmed_at,
        meta: `${s.ts} @ ${s.price}`,
        startTs: s.ts, endTs: s.ts,
      }));
    });
    box.append(sigBlock);
  }

  function block(title, n) {
    const wrap = document.createElement("section");
    wrap.className = "ledger-block";
    const h = document.createElement("h3");
    h.textContent = title;
    const span = document.createElement("span");
    span.className = "n";
    span.textContent = String(n);
    h.append(span);
    wrap.append(h);
    return wrap;
  }

  function emptyLine(text) {
    const p = document.createElement("div");
    p.className = "ledger-empty";
    p.textContent = text;
    return p;
  }

  function row({ idx, title, dirClass, status, confirmedAt, meta, startTs, endTs }) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "row";
    const i = document.createElement("span");
    i.className = "row-idx";
    i.textContent = String(idx);

    const main = document.createElement("span");
    main.className = "row-main";
    const t = document.createElement("span");
    t.className = "row-title";
    const dir = document.createElement("span");
    dir.className = `dir ${dirClass}`;
    dir.textContent = title;
    const chip = document.createElement("span");
    chip.className = `chip ${status}`;
    chip.textContent = STATUS_CN[status] || status;
    t.append(dir, chip);
    const m = document.createElement("span");
    m.className = "row-meta";
    m.textContent = meta;
    main.append(t, m);

    const when = document.createElement("span");
    when.className = "conf-time";
    when.textContent = confirmedAt ? `确认 ${confirmedAt}` : "待确认";
    btn.append(i, main, when);
    btn.addEventListener("click", () => zoomTo(startTs, endTs));
    return btn;
  }

  function zoomTo(startTs, endTs) {
    const ts = state.data.bars.map((b) => b.ts);
    const pad = 8;
    const i0 = ts.indexOf(startTs), i1 = ts.indexOf(endTs);
    if (i0 < 0 || i1 < 0) return;
    chart.dispatchAction({
      type: "dataZoom",
      startValue: ts[Math.max(0, Math.min(i0, i1) - pad)],
      endValue: ts[Math.min(ts.length - 1, Math.max(i0, i1) + pad)],
    });
  }

  // ------------------------------------------------------------------ 自选股栏
  // 一栏只回答三件事：是哪只票、现在什么价、结构走到哪一步。点一下就换图。
  // 价格与涨跌幅由后端在**和图表同一个复权口径**下算好（api._change_payload）：
  // 不复权帧在除权日有一根几十个点的缺口，拿它算涨跌幅会在自选栏里报出一根
  // 根本不存在的跌停，而图上那根K线看起来是平的。
  // 自选栏自己的在途序号。**与 `loadSeq` 分开**：周期 click 里 `load(); loadWatch();`
  // 是同步连发，共用一个计数器时后者的 `++` 会把前者**有效**的响应判成过期 ——
  // 主图丢掉自己的响应、永远停在旧周期。乱序只发生在**同一端点**的先后两次请求之间，
  // 两个端点各管各的号；一次周期点击把两个号**同时**推一格，上一周期的两个晚到响应
  // 于是各自判过期。
  let watchSeq = 0;
  async function loadWatch() {
    const seq = ++watchSeq;
    const box = $("#watch-rows");
    let url = `/api/watchlist/structure?period=${state.period}&adjust=${state.adjust}&mode=${state.mode}`;
    if (state.limit) url += `&limit=${state.limit}`;
    let body;
    try {
      body = await (await fetch(url)).json();
    } catch (err) {
      // 同 `load()`：旧请求连不上时也不许写"读取失败"，那是上一次点击的事。
      if (seq !== watchSeq) return;
      box.innerHTML = '<div class="watch-empty">自选股读取失败：连不上本地服务（8888 端口）。</div>';
      return;
    }
    // ★ 从这里往下整段都有副作用（写自选栏 DOM、写 `state.watch`、首屏 `setCode()` + `load()`）：
    // 旧响应必须整段丢弃。
    if (seq !== watchSeq) return;
    if (!body.items) {
      box.innerHTML = `<div class="watch-empty">自选股读取失败：${body.detail || "接口返回了无法解析的内容。"}</div>`;
      return;
    }
    state.watch = body.items;
    // 首屏定位：URL 没指定票时开在自选池第一只（见 boot 里的 initialCodePinned）。
    // 不做这一步，页面会停在写死的 600000 上，而自选栏里一只都没高亮 ——
    // 看起来像"高亮坏了"，其实是图上的票根本不在这份自选里。
    if (!initialCodePinned) {
      initialCodePinned = true;
      const first = (body.items || [])[0];
      if (first && first.code !== state.code) {
        setCode(first.code);
        load();
      }
    }
    renderWatch();
  }

  // ------------------------------------------------------------ 自选股栏折叠
  // 收起来不是「删掉」：整栏收成 30px 的书脊，竖排写着「自选股」，点一下就回来。
  // 状态记在 localStorage（下次打开还是你上次的样子）；URL 的 `?watch=off/on`
  // 可以覆盖它，方便把「只看K线」做成书签。
  function watchCollapsed() {
    return $("#watchlist").classList.contains("is-collapsed");
  }

  function setWatchCollapsed(collapsed, persist) {
    $(".work").classList.toggle("is-watch-collapsed", collapsed);
    $("#watchlist").classList.toggle("is-collapsed", collapsed);
    const btn = $("#watch-toggle");
    if (btn) {
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      btn.title = collapsed ? "展开自选股栏" : "收起自选股栏，把宽度让给K线";
    }
    if (persist) remember(LS_WATCH, collapsed ? "1" : "0");
    // 容器宽度变了：ResizeObserver 也会兜住，这里再显式喊一声，免得首屏量到旧宽度。
    chart.resize();
  }

  function renderWatch() {
    const box = $("#watch-rows");
    const items = state.watch || [];
    $("#watch-count").textContent = String(items.length);
    box.innerHTML = "";
    if (!items.length) {
      const p = document.createElement("div");
      p.className = "watch-empty";
      p.textContent = "自选股是空的。把常看的票加进来，点一下就能看它的笔、线段、中枢和买卖点。";
      box.append(p);
      return;
    }
    for (let i = 0; i < items.length; i += 1) box.append(watchRow(items[i], i, items.length));
  }

  function span(cls, text) {
    const s = document.createElement("span");
    s.className = cls;
    s.textContent = text;
    return s;
  }

  // 日期区间：同一个年份里省掉后半段的年，264px 的自选栏才放得下。
  // 不省年份的话「2018-01-02→2024-06-30」会被 ellipsis 吃掉后半段 —— 而"这个中枢是哪几年的"
  // 恰恰是这一行最要紧的信息（601398 那个中枢是 2018–2024 的，不写日期没人看得出来）。
  function dateRange(a, b) {
    const s = String(a || "").slice(0, 10), e = String(b || "").slice(0, 10);
    if (!s || !e) return "";
    return s.slice(0, 4) === e.slice(0, 4) ? `${s}→${e.slice(5)}` : `${s}→${e}`;
  }

  function watchRow(it, idx, total) {
    const row = document.createElement("div");
    row.className = "watch-row";
    // 代码挂在 dataset 上：无头浏览器 dump-dom 也能读到"这一栏有哪几只票"
    row.dataset.code = it.code;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    if (it.code === state.code) row.classList.add("is-current");

    const top = document.createElement("div");
    top.className = "watch-row-top";
    // 自选栏显示**不复权真实成交价**：后复权价是合成序列，摆在这里会被当成市价（和券商对不上账）
    const price = it.rail_close != null ? it.rail_close : it.close;
    top.append(span("watch-code", it.code), span("watch-price", it.missing ? "—" : f2(price)));

    const mid = document.createElement("div");
    mid.className = "watch-row-mid";
    const chg = span("watch-chg", "—");
    // 涨跌幅跟交易所口径（除权参考价做分母），与上面的真实成交价配套
    const pct = it.rail_change_pct != null ? it.rail_change_pct : it.change_pct;
    if (!it.missing && pct != null) {
      const v = Number(pct);
      chg.textContent = `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
      // A股习惯：红涨绿跌（和K线同色），平盘用灰
      chg.classList.add(v > 0 ? "up" : v < 0 ? "down" : "flat");
    } else {
      chg.classList.add("flat");
    }
    mid.append(span("watch-name", it.name || ""), chg);

    const meta = document.createElement("span");
    meta.className = "watch-meta";
    if (it.missing) {
      row.classList.add("is-missing");
      meta.textContent = it.error || "缺少本地数据";
    } else {
      // 只显示**最后一个**中枢，并且必须带上它的日期区间：光写「中枢 3.40–3.86」
      // 配现价 8.28，读的人根本不知道那是 2018–2024 年的事，会当成当下在盘整。
      const piv = (it.pivots || [])[it.pivots.length - 1];
      const seg = it.last_segment;
      const bits = [
        piv ? `中枢 ${piv.status === "tentative" ? "未确认 " : ""}${f2(piv.zd)}–${f2(piv.zg)}`
              + (dateRange(piv.start_ts, piv.end_ts) ? `（${dateRange(piv.start_ts, piv.end_ts)}）` : "")
          : "无中枢",
      ];
      if (seg) bits.push(`末段${seg.direction > 0 ? "上" : "下"}${seg.status === "tentative" ? "（未确认）" : ""}`);
      meta.textContent = bits.join(" · ");
      const sigs = it.signals || [];
      if (sigs.length) {
        row.classList.add("has-signal");
        const last = sigs[sigs.length - 1];
        // 未确认的买卖点必须标出来：它可能明天就失效（000002 的 s3 就是 tentative），
        // 不标的话读图的人会把它当成已经落定的信号。
        const tag = span("watch-sig", `${KIND_CN[last.kind] || last.kind} ${last.ts}`
          + (last.status === "tentative" ? "（未确认）" : ""));
        row.append(top, mid, meta, tag);
        return finishWatchRow(row, it, idx, total);
      }
    }
    row.append(top, mid, meta);
    return finishWatchRow(row, it, idx, total);
  }

  // 自选池的「改」= 上下移动（用户 2026-10-01 选定）：顺序本身就是信息，
  // 常看的放上面，比按代码/涨幅排序更符合盯盘的习惯。首尾两行对应方向的按钮置灰 ——
  // 后端在边界上是空操作，界面不该做出"点了会动"的样子。
  async function moveWatch(code, delta) {
    try {
      const resp = await fetch("/api/watchlist", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, delta }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        watchError(body.detail || `移动失败：HTTP ${resp.status}`);
        return;
      }
      watchError("");
    } catch (err) {
      watchError(`移动失败：连不上本地服务（${err}）。`);
      return;
    }
    loadWatch();
  }

  function moveBtn(text, title, code, delta, disabled) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "watch-mv";
    b.title = title;
    b.textContent = text;
    b.disabled = Boolean(disabled);
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!b.disabled) moveWatch(code, delta);
    });
    return b;
  }

  function finishWatchRow(row, it, idx, total) {
    const mv = document.createElement("div");
    mv.className = "watch-move";
    mv.append(
      moveBtn("▲", "上移", it.code, -1, idx === 0),
      moveBtn("▼", "下移", it.code, 1, idx === total - 1),
    );
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "watch-rm";
    rm.title = "移出自选";
    rm.textContent = "×";
    rm.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await fetch(`/api/watchlist?code=${encodeURIComponent(it.code)}`, { method: "DELETE" });
      loadWatch();
    });
    row.append(mv, rm);
    // 换票的绑定**不看 `missing`**：`missing` 说的是「当前这个周期本地没数据」，
    // 而点击要回答的是「我要看这只票」。自选池全是 7 个大盘指数，切到 30分/5分 时
    // 每一行都 missing（指数没有分钟线），一旦把点击绑在 !missing 上，整栏就点不动了
    // —— 用户看到的就是"点自选股没反应、不跳 K 线"。点过去之后再按那个周期的真实
    // 情况提示（能补就给同步按钮，补不了就说清为什么）。
    const open = () => { setCode(it.code); load(); };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); }
    });
    return row;
  }

  function watchError(text) {
    const box = $("#watch-add-error");
    if (!text) { box.hidden = true; box.textContent = ""; return; }
    box.textContent = text;
    box.hidden = false;
  }

  async function addWatch() {
    const input = $("#watch-code");
    const raw = input.value.trim();
    if (!raw) return;
    // 允许"600000 浦发银行"：名称跟在代码后面一起存，自选栏里才认得出是哪只票。
    // 名称不是必填 —— 输代码也能加，名字缺了就用库里已有的。
    const m = raw.match(/^([0-9]{6}|[a-zA-Z]{2}\.[0-9]{6})\s*(.*)$/);
    if (!m) {
      watchError(`看不懂「${raw}」。写 6 位代码（600000），或带市场前缀（sh.000300），名称可以跟在后面。`);
      return;
    }
    const code = m[1], name = m[2].trim();
    let resp;
    try {
      resp = await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(name ? { code, name } : { code }),
      });
    } catch (err) {
      watchError(`加自选失败：连不上本地服务（${err}）。`);
      return;
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      watchError(body.detail || `加自选失败：HTTP ${resp.status}`);
      return;
    }
    input.value = "";
    watchError("");
    hideNotice();
    loadWatch();
  }

  // 输入即搜：候选列表用浏览器原生 datalist，不自己造下拉框。
  // 只有"记住选择"这件事需要自己做，所以输入框里的字始终是用户写的。
  let searchTimer = null;
  async function fillUniverse(q) {
    let body;
    try {
      body = await (await fetch(`/api/universe?q=${encodeURIComponent(q)}&limit=20`)).json();
    } catch (err) {
      return;
    }
    const list = $("#universe-options");
    list.innerHTML = "";
    for (const it of body.items || []) {
      const opt = document.createElement("option");
      opt.value = `${it.code} ${it.name || ""}`.trim();
      list.append(opt);
    }
  }

  // ------------------------------------------------------------------ 交互绑定
  function setCode(code) {
    state.code = code;
    $("#code").value = code;
  }

  $("#query").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const raw = $("#code").value.trim() || "600000";
    state.code = raw;
    const rawLimit = parseInt($("#limit").value, 10);
    state.limit = Number.isFinite(rawLimit) ? Math.max(60, Math.min(20000, rawLimit)) : null;
    load();
  });

  for (const btn of document.querySelectorAll(".period")) {
    btn.addEventListener("click", () => {
      for (const b of document.querySelectorAll(".period")) {
        b.classList.toggle("is-on", b === btn);
        b.setAttribute("aria-selected", b === btn ? "true" : "false");
      }
      state.period = btn.dataset.period;
      load();
      loadWatch();
    });
  }

  for (const btn of document.querySelectorAll(".rail-tab")) {
    btn.addEventListener("click", () => {
      const key = btn.dataset.layer;
      state.layers[key] = !state.layers[key];
      btn.classList.toggle("is-on", state.layers[key]);
      btn.setAttribute("aria-pressed", state.layers[key] ? "true" : "false");
      if (state.data) draw();
    });
  }

  $("#watch-add-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    addWatch();
  });
  // 折叠/展开：箭头一个按钮；收起来之后整条书脊都可点（免得只剩 18px 的靶子）。
  $("#watch-toggle").addEventListener("click", () => {
    setWatchCollapsed(!watchCollapsed(), true);
  });
  $("#watchlist").addEventListener("click", (ev) => {
    if (!watchCollapsed() || ev.target.closest("#watch-toggle")) return;
    setWatchCollapsed(false, true);
  });
  $("#watch-code").addEventListener("input", () => {
    const q = $("#watch-code").value.trim();
    clearTimeout(searchTimer);
    if (!q) return;
    searchTimer = setTimeout(() => fillUniverse(q), 150);
  });

  // 复权：一个按钮循环三态。切换后行情、均线、结构一起换口径 ——
  // 结构必须画在同一口径上，否则"前复权的K线 + 不复权的笔"是两张图的叠加。
  $("#adjust-btn").addEventListener("click", () => {
    const i = ADJUST_ORDER.indexOf(state.adjust);
    state.adjust = ADJUST_ORDER[(i + 1) % ADJUST_ORDER.length];
    remember(LS_ADJUST, state.adjust);
    applyAdjustUI({ adjust_effective: state.adjust, adjust_note: "" });
    load();
    loadWatch();
  });

  // 口径：一个按钮两态。切换后买卖点与背驰都要重算 —— 它们都在服务端判，
  // 前端不自己判买卖点，所以这里只改 state 再重取（图上买卖点数量会变）。
  $("#mode-btn").addEventListener("click", () => {
    state.mode = state.mode === "strict" ? "loose" : "strict";
    remember(LS_MODE, state.mode);
    applyModeUI();
    load();
    loadWatch(); // 自选池摘要也是按口径算的（Task 7b），不刷新它就会与主图自相矛盾
  });

  for (const box of document.querySelectorAll(".ma-toggle")) {
    const period = parseInt(box.dataset.period, 10);
    const input = box.querySelector("input");
    input.checked = state.ma.has(period); // 勾选状态来自本地记忆，不是写死在 HTML 里
    input.addEventListener("change", () => {
      if (input.checked) state.ma.add(period); else state.ma.delete(period);
      remember(LS_MA, MA_PERIODS.filter((p) => state.ma.has(p)).join(","));
      if (state.data) draw(); // 只重画：不重新取数，缩放窗口因此保住
    });
  }

  // 初始加载：URL 可带 ?code=&period=&adjust=，方便把常看的票做成书签
  const sp = new URLSearchParams(location.search);
  if (sp.get("code")) setCode(sp.get("code"));
  if (ADJUST_ORDER.includes(sp.get("adjust"))) state.adjust = sp.get("adjust");
  // 口径与复权同一套两段式：localStorage 当默认值，URL 有合法值再覆盖（书签优先）。
  // 非法值（`?mode=xx`）不认，保持默认 —— 后端那边也会 422，前端不该把坏值送出去。
  if (MODE_ORDER.includes(sp.get("mode"))) state.mode = sp.get("mode");
  if (sp.get("period") && PERIOD_CN[sp.get("period")]) {
    state.period = sp.get("period");
    for (const b of document.querySelectorAll(".period")) {
      const on = b.dataset.period === state.period;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
  }
  // 图层按钮的 is-on 与 state.layers 对齐（HTML 里写死的 is-on 只是首屏兜底，
  // 状态以 state 为准 —— 照上面 period 按钮的写法）。
  for (const b of document.querySelectorAll(".rail-tab")) {
    const on = Boolean(state.layers[b.dataset.layer]);
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  }
  applyAdjustUI({ adjust_effective: state.adjust, adjust_note: "" });
  applyModeUI();
  // 自选股栏的收/展：URL 里写了 `?watch=off` / `?watch=on` 就听 URL（书签优先），
  // 否则沿用上次的选择。URL 只是一次性的覆盖，不写回 localStorage。
  const wq = sp.get("watch");
  setWatchCollapsed(wq === "off" ? true : wq === "on" ? false : readFlag(LS_WATCH), false);
  // 首屏开在哪只票：URL 里写了 `?code=` 就听 URL（书签优先），没写就等自选池回来
  // 开在**第一只自选票**上 —— 那是用户自己加的、他每天要盯的票。只认第一次，
  // 之后用户手动换票不再被拽回去。
  let initialCodePinned = Boolean(sp.get("code"));
  // 初始加载：URL 可带 ?code=&period=，方便把常看的票做成书签。
  // 取数不等渲染：requestAnimationFrame 在后台标签页会停摆（无头浏览器 + 虚拟时间下
  // 亦同），把它放在加载路径上会导致页面永远停在"尚未加载"。尺寸校准另走兜底回调。
  load();
  loadWatch();
  const remeasure = () => chart.resize();
  requestAnimationFrame(remeasure);
  setTimeout(remeasure, 0);
  setTimeout(remeasure, 300);
})();
