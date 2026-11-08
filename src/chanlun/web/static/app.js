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
  const PERIOD_CN = { day: "日线", week: "周线", month: "月线", 60: "60分", 30: "30分", 15: "15分", 5: "5分", 1: "1分" };
  // 派生周期（周/月）由**本地日线**聚合而来，不单独同步。这句话必须出现在图注里：
  // 用户看到"周线"会以为是从数据源拿的一份独立数据，实际它跟着日线的复权口径走。
  const DERIVED_CN = { week: "day", month: "day" };
  // 趋势口径（第 20 课「走势中枢中心定理二」）：比的是**围绕中枢波动的区间 [DD,GG]**，
  // 不是中枢区间 [ZD,ZG]。后DD > 前GG → 上涨；后GG < 前DD → 下跌；两者都不满足
  // → 形成高级别的走势中枢（第 20 课把它和趋势并列，本系统显示为"盘整"）。
  // GG/DD 取**中枢中所有 Zn**（第 20 课原文），再从中剔除**充当离开段的那一段**
  // （位置口径：中枢组的最后一段 `group[-1]`）。这是趋势判得出来的前提：
  // 若把离开段算进 GG/DD，两个相邻中枢的波动区间几乎总在重叠，趋势就永远判不出来。
  // 注意这个剔除**不是无条件的**（D-37 / D-38）：`group[-1]` 本身不是 Zn 时它本来
  // 就不在集合里，无需剔除；中枢被段数上限（`MAX_SEGMENTS`）掐停时，`group[-1]`
  // 只是一段仍在枢内的延伸段、根本不存在离开段，此时一段都不剔除。
  //
  // 这句话为什么必须出现：**本页没有"走势类型"分节**，趋势判据是隐形的，
  // 但它决定第一类买卖点出不出现（`chan/signal.py:205-257`：`classify_trends` 给出
  // 上涨/下跌的中枢组，组内不足两个中枢就跳过，`down_pivots`/`up_pivots` 为空时
  // B1/S1 一个都不会生成）。旧口径下趋势永远判不出来，等于第一类买卖点被静默关掉。
  // 所以图注要把口径说清楚，别让人以为买卖点是纯按中枢算的。
  const TREND_BASIS = "第一类买卖点取自趋势背驰；趋势按 [DD,GG] 波动区间判（第20课中心定理二；GG/DD 取中枢中所有 Zn 并剔除充当离开段的那一段，不是中枢区间 [ZD,ZG]）";
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
    // 屏幕上这一只的**落库键**。`state.code` 是用户输入的原样（书签里可能是
    // `sz.600759`），服务端回的 `code` 才是落库键（`600759`；指数反过来必须带前缀，
    // 否则和 `sz.000001` 平安银行撞号）。凡是"这是不是同一只"的比较一律用它 ——
    // 拿 `state.code` 比会漏：实测 `?code=sz.002898` 时浮标一个字都不显示，
    // 而且补完不重画（`finishAutoSync` 也会判成"用户已经换走了"）。
    key: "600000",
    period: "day",
    limit: null,
    adjust: readChoice(LS_ADJUST, ADJUST_ORDER, "qfq"),
    // 与 adjust 同一套两段式：localStorage 只当**默认值**，URL 有值再覆盖（见文件末尾初始化块）
    mode: readChoice(LS_MODE, MODE_ORDER, "strict"),
    ma: new Set(readMaChoice()),
    watch: [],
    data: null,
    // 信号留痕（`/api/structure/trail`）：**当时标过、后来被走势证伪**的买卖点。
    // 单独一条请求、单独一个 series —— 它慢（要按检查点回放），不能挡首屏；
    // 而且必须能单独开关：它是「历史判断的审计」，不是当下的交易信号。
    trail: null,
    layers: { strokes: true, segments: true, pivots: true, signals: true, divergence: true, margin: true, falsified: true },
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
    // 换票/换周期第一件事就是收浮标：它讲的是**上一只**补最新的结论。
    // 实测过：退市股 002898 的「点这里重试」会跟着你换到 600000，还照样可点，
    // 点下去补的是 002898。收在这里（`await` 之前）才不会留下几百毫秒的错位窗口。
    if (!chipIsAbout(chipJob)) dropChip();
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
      // 404 时"屏幕上是哪一只"只能按请求原样记：这一只没有落库键可回。
      // 下面请求携带的也是 `state.code`，两边形态一致，守卫才比得中。
      state.key = state.code;
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
      // 左栏高亮也要跟着走：404 时图表区不换，但"我正在看哪一只"已经变了。
      // 不挪的话输入框写着 399006、左栏还高亮着 sh.000001，高亮就成了一句假话。
      renderWatch();
      return showNotice(
        httpTitle(resp.status), body.detail || "接口返回了无法解析的内容。", null, action);
    }
    state.data = body;
    state.trail = null;      // 换票/换周期先清掉上一只的留痕，免得叠错
    state.key = body.code;   // 升级成落库键：后面所有"是不是同一只"都按它比
    applyAdjustUI(body);
    applyDivergenceBadge(body);
    hideNotice();
    draw();
    loadTrail(seq);   // 不 await：图先出来，留痕随后叠上去
    renderLedger();
    renderWatch(); // 高亮"图上是哪一只"：换票后自选栏要跟着动
    const c = body.counts;
    const t = body.counts_total;
    // 顶栏那一格必须**恒定 2 行**。顶栏是 flex 行，图注一变宽、一变行数就会去挤
    // `.period` 按钮（按钮是 flex:0 1 auto，被挤到内容宽度以下就折行），整行高度
    // 跟着跳 —— 实测按钮 36→56px、顶栏 93→110px。所以会变长、会时有时无的文案
    // 一律不进顶栏，走下面的 setStampDetail。
    setStamp(
      `${body.code}${body.name ? " " + body.name : ""} · ${PERIOD_CN[body.period] || body.period} · 截至 ${body.as_of}\n` +
      `线段 ${c.segments}（确认 ${c.confirmed_segments} / 未确认 ${c.tentative_segments}） · 中枢 ${c.pivots} · 买卖点 ${c.signals}`
    );
    // 结构是**全史**算出来的，窗口只决定画多少：两边数不一样时明说还有多少没画，
    // 否则用户会把"窗口里 12 段"读成"这只票只有 12 段"。
    // 详情行**恒定两行**，且第二行在任何周期下都逐字相同：
    //   第一行 = 全史计数（月线是聚合的、两边数相等，这一格为空）+ 周期来源
    //   第二行 = 口径
    // 周期来源必须并进**第一行**：留在第二行会给口径加一个随周期长短不同的前缀，
    // 窄屏下折行数就不一样（实测 800px 严格模式：日线 2 行、周/月 3 行 ⇒ 图表跳 17px）。
    // 第一行也不许空着不留位：日/周线有「全史」、月线没有，空串照样占一个行盒。
    const totals = t && (t.segments !== c.segments || t.pivots !== c.pivots || t.signals !== c.signals)
      ? `全史 ${t.segments} 段 / ${t.pivots} 中枢 / ${t.signals} 买卖点（这里只画与窗口相交的部分）`
      : "";
    const head = [totals, derivedNote(body)].filter(Boolean).join(" · ");
    setStampDetail(head + "\n" + basisLine());
    // 数据画完了，再把后端的"新不新"结论写到浮标上：**先把本地这一份给用户看**，
    // 别让页面干等网络（要补的话补完会自己回来重画，见 runAutoSync）。
    noteFreshness(body);
  }

  // 周期来源：周/月的最后一根**还没走完**时必须标出来 ——
  // 一根还在变的周K 被当成定论去数中枢，是这套系统最容易骗到自己的地方。
  // 它并进详情行的**第一行**（和全史计数同一行），不进口径那一行 —— 理由见上面 render 里。
  function derivedNote(body) {
    if (!body.derived) return "";
    return `${PERIOD_CN[body.period] || body.period}由${PERIOD_CN[body.base_period] || body.base_period}聚合`
      + (body.partial === true ? "（最后一根未走完）" : "");
  }

  // 详情行第二行：**只有口径**，不带周期来源。这一行在任何周期下逐字相同
  // ⇒ 折行数相同 ⇒ 详情行高度不随周期变，下方图表不跳。
  // 口径不同，这一句必须不同：非严格模式下第一类**还会来自盘整背驰**，
  // 照旧印「第一类买卖点取自趋势背驰」就是一句与图上结果相反的话 ——
  // 正是 §5 第 5 条那一类事故（后端改了口径、图注还印着旧口径）。
  // 严格模式必须**逐字**输出 TREND_BASIS：那句话在严格口径下是对的，不许改写它。
  function basisLine() {
    if (state.mode === "loose") {
      return "非严格口径：第一类之外另收盘整背驰（第27课「类第一类」；第60课：盘整背驰无所谓第一类买点，只是类比），第二类不要求前置第一类；第三类买卖点严格与非严格逐项相同（D-41 已删除回试容忍度，原文无此措辞）";
    }
    return TREND_BASIS;
  }

  // 工具条上的口径：按钮写"请求"的口径，实际生效口径不一致时用一句话说清原因。
  // 例：这只票没有除权记录 → 请求前复权，实际就是原始价，按钮仍显示"前复权"，
  // 旁边的说明由后端写（A 股"无除权记录（三态相同）"；港股/美股是"另两态取不到"，
  // 那是两回事，别在前端统一成一句），否则用户会以为复权算错了。
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
      ? "非严格：含盘整背驰买卖点、第二类不要求前置第一类；第三类买卖点严格与非严格逐项相同（D-41 已删除回试容忍度）。笔/线段/中枢划分不变。点击切回严格。"
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

  async function pollSync(started, job) {
    while (Date.now() - started < SYNC_MAX_MS) {
      await new Promise((r) => setTimeout(r, SYNC_POLL_MS));
      // ★ 必须问**发起时那一只**：这里原先读的是画面上的当前 code/period，
      //   用户中途换票就会去问新票的状态，后端对未知任务回 `idle`（`state !== "running"`），
      //   于是刚才那次还没跑完的同步被读成"成功、undefined 根"，还顺手 `load()` 重画。
      //   （这条注释刻意不写那两个变量名 —— 契约测试用字符串断言咬住"函数体里不许
      //   出现它们"，注释里写一遍也会把它咬红，而那种红是假红。）
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
      return syncNote(
        "行情源**连上了但没返回数据**（零行）。最常见的原因是**被行情源限流** —— "
        + "短时间连点几次同步就会这样，等一两分钟再点「重试」通常就好了。"
        + "其次是这个周期/标的真的没有数据（真空区间、或这只票没有分钟线）。",
        true,
      );
    }
    syncNote(`同步完成：${status.rows} 根（${status.start_ts || "?"} → ${status.end_ts || "?"}）。`, false);
    load(); // 数据到位了，图应该出来
  }

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

  // 顶栏那格只放恒定 2 行的短信息。顺带清掉详情行：加载中/出错时若不清，
  // 上一只票的「全史」与口径会留在下面，看起来像是这一只的。
  function setStamp(text) {
    $("#stamp").textContent = text;
    $("#stamp-detail").textContent = "";
  }

  // 顶栏之外的详情行：全史计数 + 周期来源 + 口径说明。这些文案长短不一，
  // 放在顶栏里会改顶栏高度（见 render 里的注释）。
  function setStampDetail(text) { $("#stamp-detail").textContent = text; }

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

  // 图注文案。**分辨率必须说出来**：`step` 是「每多少根检查一次」，
  // 检查得越稀、能看见的短命信号越少 —— 不说的话「只证伪了 2 条」会被读成
  // 「历史上只错了 2 次」，那是假话。
  function trailNoteText(tr) {
    if (!tr) return "";
    const list = tr.falsified || [];
    if (!list.length) {
      return `已证伪 0 条（留痕分辨率：每 ${tr.step} 根检查一次）`;
    }
    // 带上**最近一条**的时间与类型：否则用户只知道「有 6 条」，
    // 却不知道把视野拖到哪里才看得见 —— 图上没标记会被当成功能坏了。
    const last = list[list.length - 1];
    // 文案压短：工具条是单行，太长会挤掉右侧的「口径」按钮。
    // 「分辨率」仍然要说 —— 它是这张图最容易被误读的东西。
    const bars = Math.round(tr.step / 240) || 1;
    return `已证伪 ${list.length} 条（每 ${tr.step} 根/约 ${bars} 日；`
      + `最近 ${KIND_CN[last.kind] || last.kind} ${last.ts}）`;
  }

  function setTrailNote(text) {
    const el = $("#trail-note");
    if (!el) return;
    el.textContent = text;
    // CSS 里已 `nowrap + ellipsis`：长文案会被截断，`title` 让 hover 仍能看全文。
    el.title = text;
    el.hidden = !text;
  }

  // ------------------------------------------------------------- 信号留痕
  // **当时标过、后来被走势证伪**的买卖点。买卖点是需要后续走势确认的**当下判断**，
  // 被证伪是正常现象 —— 悄悄从图上消失才是错的（用户明确要求「要在图上能看到」）。
  //
  // 单独一条请求的理由：它要按检查点回放（每 step 根算一次全量结构），实测 1 分钟的
  // 2.2 万根序列要 1~5 秒。放进 `/api/structure` 会把首屏拖垮。
  async function loadTrail(seq) {
    const qs = new URLSearchParams({ code: state.code, period: state.period });
    qs.set("adjust", state.adjust);
    // 首次要回放几十遍全量结构（实测 1 分钟的 2.3 万根约 3~4 秒），
    // **必须说出来** —— 否则用户看到图上迟迟没有 ✕，会以为功能坏了。
    setTrailNote("留痕计算中…（首次较慢，之后有缓存）");
    try {
      const resp = await fetch(`/api/structure/trail?${qs}`);
      // 过期的响应不许落进 state：用户可能已经换票了（与 load() 的 seq 同一套约定）
      if (seq !== loadSeq) return;
      if (!resp.ok) { state.trail = null; setTrailNote(""); return; }
      state.trail = await resp.json();
      if (seq === loadSeq) { draw(); setTrailNote(trailNoteText(state.trail)); }
    } catch (err) {
      // 留痕是**附加信息**：拿不到就不画，不许因此弹错误、更不许挡住图。
      if (seq === loadSeq) state.trail = null;
    }
  }

  // ------------------------------------------------------- 纵轴只认当前窗口
  // 用户的要求：**窗口之外的数据不许影响当前视野**。
  //
  // 为什么必须显式做：ECharts 的纵轴 `scale: true` 会把**该 grid 上所有 series**
  // 的数据一起算进范围 —— 散点、markLine、折线都算。而图上有几类东西天然覆盖全史：
  // 买卖点、背驰、留痕（「已证伪」）、线段/中枢的 markLine。只要有**一条**落在
  // 窗口外、价格又离得远，纵轴就被它拉走，K 线被压成一条窄带。
  //
  // 实测（`sh.000001` 日线，1600×1000）：窗口 2021-11 起、价格 3,674~4,258，
  // 而留痕里有一条 `s3 @ 2002-06-25 price=1748.89` ⇒ 纵轴 1,500~4,500，
  // K 线只剩最上面一条窄带、下面全空。用户报的就是这个。
  //
  // 所以：**窗口边界从 dataZoom 取，上下界只由窗口内 bar 的 low/high 决定**。
  // 这样不管以后再加什么系列，纵轴都不会被窗口外的数据带跑。
  function visibleRange() {
    const d = state.data;
    if (!d || !Array.isArray(d.bars) || !d.bars.length) return null;
    const n = d.bars.length;
    const z = ((chart.getOption() || {}).dataZoom || [])[0] || {};
    let i0 = null;
    let i1 = null;
    if (z.startValue != null && z.endValue != null) {
      // 用 `String()` 比：ECharts 有时把类目值存成数字，`===` 会一个都匹配不上，
      // 那时若没有回退就会退成「全量范围」——纵轴被整个窗口的数据拉宽，
      // 表现就是「K 线只占上半截」。实测踩过（下界 2,537 而不是 3,674）。
      const a = d.bars.findIndex((b) => String(b.ts) === String(z.startValue));
      const b = d.bars.findIndex((x) => String(x.ts) === String(z.endValue));
      if (a >= 0 && b >= 0) { i0 = a; i1 = b; }
    }
    if (i0 === null && typeof z.start === "number" && typeof z.end === "number") {
      // **必须有这个回退**：`startValue` 拿不到时按百分比算，而不是退成全量。
      i0 = Math.floor((z.start / 100) * (n - 1));
      i1 = Math.ceil((z.end / 100) * (n - 1));
    }
    if (i0 === null) { i0 = 0; i1 = n - 1; }
    i0 = Math.max(0, Math.min(i0, n - 1));
    i1 = Math.max(i0, Math.min(i1, n - 1));
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = i0; i <= i1; i++) {
      const b = d.bars[i];
      if (b.low < lo) lo = b.low;
      if (b.high > hi) hi = b.high;
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return null;
    const pad = (hi - lo) * 0.06;
    return { min: Number((lo - pad).toFixed(2)), max: Number((hi + pad).toFixed(2)) };
  }

  let fitPending = false;
  function fitPriceAxis() {
    // 拖滑块时 `dataZoom` 每帧都触发，节流到一个动画帧，别让 setOption 拖慢拖动。
    if (fitPending) return;
    fitPending = true;
    requestAnimationFrame(() => {
      fitPending = false;
      const r = visibleRange();
      if (r) chart.setOption({ yAxis: [{ min: r.min, max: r.max }] });
    });
  }

  chart.on("dataZoom", fitPriceAxis);

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
    // 「已证伪」的标记色：用 `--mohui`（最暗的墨），**刻意不用红绿** ——
    // 红/绿在这个页面上是「涨/跌」，拿它们画证伪标记会被读成「这是个买卖点」。
    const faded = mohui;
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
      ? d.pivots.map((p) => {
          // D-41：段数上限掐停的中枢（`capped`）**不是**一个走完的中枢 ——
          // 它的组内每一段都还与 `[ZD, ZG]` 重叠（第 20 课中心定理一），
          // 只是被 `MAX_SEGMENTS` 掐停；第 33 课说凑满 9 段就构成更大级别的中枢了，
          // 而本项目不做级别递归。所以这种中枢在信号层整条跳过，图上必须能一眼认出来：
          // 实心靛青带 = 正常封口；**点线边框 + 降透明度 + 标签后缀** = 段数到顶。
          const cap = p.capped === true;
          const tint = p.status === "tentative" ? "rgba(124,156,196,0.05)" : "rgba(124,156,196,0.11)";
          return [
            {
              name: `中枢 ${f2(p.zd)}–${f2(p.zg)}`,
              xAxis: p.start_ts,
              yAxis: p.zd,
              itemStyle: {
                color: tint,
                borderColor: indigo,
                borderWidth: cap ? 2 : 1,
                borderType: cap ? "dotted" : (p.status === "tentative" ? "dashed" : "solid"),
                opacity: cap ? 0.6 : (p.status === "tentative" ? 0.5 : 1),
              },
              label: {
                show: true,
                position: "insideTopLeft",
                color: indigo,
                fontSize: 10,
                formatter: `中枢 ${PERIOD_CN[p.level] || p.level} · ZG ${f2(p.zg)} / ZD ${f2(p.zd)}`
                  + (cap ? " · 段数到顶" : ""),
              },
            },
            { xAxis: p.end_ts, yAxis: p.zg },
          ];
        })
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

    // --- 已证伪的买卖点（信号留痕）
    // **与 `tentative` 必须一眼分得开**：tentative 是「现在还没确认」，空心+实线边；
    // 已证伪是「曾经标过、后来重算不再成立」，空心+**虚线**边+去饱和。
    // 它不代表「当时标错了」——当时只能那样标。它说的是「那之后走势没有兑现它」。
    const falsified = [];
    if (state.layers.falsified && state.trail && Array.isArray(state.trail.falsified)) {
      // ★ **只画落在当前这几根 K 线上的标记**。
      //
      // 这不只是省事：ECharts 的纵轴会把该 grid 上**所有** series 的数据一起算进
      // 范围，而留痕覆盖全史。实测 `sh.000001` 日线：图上只有 2021-11 起的 1200 根
      // （价格 3,674~4,258），但留痕里有一条 `s3 @ 2002-06-25 price=1748.89` ——
      // 纵轴被它拉到 1,500~4,500，**K 线被压成最上面一条窄带，下面全空**。
      // 用户报的「图突然被压得很扁」就是这个。
      //
      // 窗口外的标记本来也看不见，滤掉之后既修好了纵轴，也没有信息损失。
      const onChart = new Set(ts);
      for (const e of state.trail.falsified) {
        if (!onChart.has(e.ts)) continue;
        const buy = isBuyKind(e.kind);
        falsified.push({
          value: [e.ts, e.price],
          label: {
            show: true,
            formatter: "✕",
            position: buy ? "bottom" : "top",
            color: faded,
            fontSize: 10,
          },
          itemStyle: {
            color: "transparent",
            borderColor: faded,
            borderWidth: 1.4,
            borderType: "dashed",
          },
          // 供 `valueFormatter` 反查（键与背驰同款：`(ts, price)` 二元组）
          kind: e.kind,
          first_seen: e.first_seen,
          falsified_at: e.falsified_at,
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
          // `hideOverlap`：窗口矮的时候（或浏览器窗口被拖矮），价格刻度会叠成一团
          // （实测 520px 高的窗口里 2800/2400/2200 挤在同一处）。让它自动少画几个，
          // 而不是让它们互相压住 —— 图上看得见的刻度必须是能读的。
          axisLabel: {
            color: dim, fontSize: 10, fontFamily: cssVar("--mono"), hideOverlap: true,
          },
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
          // **被证伪的买卖点**。与 `tentative`（空心+实线边）要一眼分得开：
          // 这里用**空心 + 虚线边 + 去饱和**，标签打 ✕。
          // 它不代表「当时标错了」——当时只能那样标；它说的是「后来重算不再成立」。
          name: "已证伪", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, data: falsified,
          symbol: "emptyCircle", symbolSize: 10, z: 8,
          tooltip: {
            // 与背驰 series 同一套：轴路径**不给数据项**，只能拿 `value` 反查。
            // 键必须是 `(ts, price)` 两元组 —— 只按 price 反查时，两个同价的点会串台。
            valueFormatter: (value) => {
              const e = falsified.find((x) => x.value[1] === value && x.value[0] === axisTs);
              const text = f2(value);
              if (!e) return text;
              return `${text}｜**已证伪** ${KIND_CN[e.kind] || e.kind}`
                + `｜标注于 ${e.first_seen}`
                + `｜证伪于 ${e.falsified_at || "—"}`;
            },
          },
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
    // `notMerge` 之后 dataZoom 已回到本次的取值，纵轴按它重定一次。
    fitPriceAxis();
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
        // D-41：`capped` 中枢在图上是点线边框 + 「段数到顶」，这里把同一件事写进图例，
        // 免得用户以为「段数到顶」是一个单独的图层。信号层已整条跳过它（不产出买卖点）。
        meta: `ZG ${f2(p.zg)} / ZD ${f2(p.zd)} · GG ${f2(p.gg)} / DD ${f2(p.dd)} · ${p.start_ts} → ${p.end_ts}`
          + (p.capped === true ? " · 段数到顶（第33课：已构成更大级别中枢；本级别不产出买卖点）" : ""),
        startTs: p.start_ts, endTs: p.end_ts,
        capped: p.capped === true,
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

  function row({ idx, title, dirClass, status, confirmedAt, meta, startTs, endTs, capped }) {
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
    // D-41：段数到顶的中枢，除了 `.row-meta` 里那句话，还要在标题行挂一个点线小标签。
    // 只写进 `.row-meta` 是不够的 —— 那一行在窄栏里会被省略号截掉（见 styles.css 里
    // `.row-meta` 的说明），用户根本读不到「段数到顶」四个字。
    if (capped === true) {
      const capChip = document.createElement("span");
      capChip.className = "chip capped";
      capChip.textContent = "段数到顶";
      capChip.title = "第33课：已构成更大级别中枢；本级别不产出买卖点";
      t.append(capChip);
    }
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
      // `body.detail` 是响应体里的文本，不是本文件的常量 —— 只能走 textContent。
      // 交给 `innerHTML` 就等于让后端决定页面解析并执行什么标记（`tests/web/test_frontend_xss.py`）。
      const fail = document.createElement("div");
      fail.className = "watch-empty";
      fail.textContent = `自选股读取失败：${body.detail || "接口返回了无法解析的内容。"}`;
      box.replaceChildren(fail);
      return;
    }
    state.watch = body.items;
    // 首屏定位：URL 没指定票时开在自选池第一只（见 boot 里的 initialCodePinned）。
    // 不做这一步，页面会停在写死的 600000 上，而自选栏里一只都没高亮 ——
    // 看起来像"高亮坏了"，其实是图上的票根本不在这份自选里。
    if (!initialCodePinned) {
      initialCodePinned = true;
      const first = (body.items || [])[0];
      if (first && first.code !== state.key) {
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
      renderFav(); // 空池也算一次"自选池变了"：按钮要退回「☆ 加自选」
      return;
    }
    for (let i = 0; i < items.length; i += 1) box.append(watchRow(items[i], i, items.length));
    // 自选池是加自选按钮的**唯一真相**：每次重画都让按钮跟着对一次。
    renderFav();
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
    if (it.code === state.key) row.classList.add("is-current");

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
    // 而点击要回答的是「我要看这只票」。自选池全是 11 个大盘指数（D-43），切到 30分/5分 时
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

  // 「代码 / 名称」两种输入要落成同一个东西：**落库键**。
  //
  // 代码形态自己就能判（`600000` / `sh.000300` / `hk.hsi` / `us.dji`）——
  // 注意前缀是**字母**、基码是**字母或数字**：自选池里本来就有 `hk.hsi`、
  // `us.ixic` 这种「字母代码」，旧正则只认 `2 个字母 + 点 + 6 位数字`，
  // 于是港美指数在页面上永远加不进自选（后端 `sources.normalize_code` 早就收它们了）。
  //
  // 名称形态只能问后端：港股/美股没有本地品种表，名称→代码只有 `/api/search` 一条路。
  const CODE_RE = /^([0-9]{6}|[a-zA-Z]{2}\.[a-zA-Z0-9]+)$/;

  function codeOf(raw) {
    // 候选列表回填的是「代码 名称」，取第一段就是代码。
    const head = String(raw || "").trim().split(/\s+/)[0] || "";
    return CODE_RE.test(head) ? head : "";
  }

  // 名称 → 代码。返回 `{code, note}`：搜不到时 `code` 为空、`note` 里是**为什么**。
  // 「搜不到」和「这类标的本期不做」必须分开说 —— 前者是输入问题，后者是范围问题。
  async function resolveCode(raw) {
    const text = String(raw || "").trim();
    if (!text) return { code: "", note: "" };
    const direct = codeOf(text);
    if (direct) return { code: direct, note: "" };
    let body = {};
    try {
      body = await (await fetch(`/api/search?q=${encodeURIComponent(text)}&limit=5`)).json();
    } catch (err) {
      return { code: "", note: "" }; // 搜索不可用不该拦住代码取数
    }
    const hit = (body.items || [])[0];
    if (hit) return { code: hit.code, note: "" };
    const skipped = (body.skipped || []).join("；");
    if (skipped) return { code: "", note: `「${text}」没有可看的标的。被排除的：${skipped}` };
    return { code: "", note: `本地品种表和数据商联想词里都没有「${text}」。A 股可以直接写 6 位代码。` };
  }

  async function addWatch() {
    const input = $("#watch-code");
    const raw = input.value.trim();
    if (!raw) return;
    // 允许"600000 浦发银行"：名称跟在代码后面一起存，自选栏里才认得出是哪只票。
    // 名称不是必填 —— 输代码也能加，名字缺了就用库里已有的。
    //
    // 也可以只写名称（用户 2026-10-05 要求）：先按代码形态试，不成再问 `/api/search`。
    // 搜出来的是**落库键**，所以港美指数、港股个股都能从这一格加进来。
    let code = codeOf(raw);
    let name = "";
    if (code) {
      name = raw.slice(raw.indexOf(code) + code.length).trim();
    } else {
      const found = await resolveCode(raw);
      if (!found.code) {
        watchError(found.note || `看不懂「${raw}」。写 6 位代码（600000）、带市场前缀（sh.000300 / hk.00700 / us.dji），或者直接写名称。`);
        return;
      }
      code = found.code;
      name = "";
    }
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
  //
  // 两个输入框（顶栏的票 + 自选栏的加自选）共用 `/api/search`：
  // 它同时覆盖**代码和名称**，也覆盖港股/美股（本地品种表里没有这两个市场）。
  // 联想词接口不通时它只返回本地结果，不报错 —— 搜索是增强，不该让页面用不了。
  let searchTimer = null;

  function fillOptions(list, items) {
    list.innerHTML = "";
    for (const it of items) {
      const opt = document.createElement("option");
      opt.value = `${it.code} ${it.name || ""}`.trim();
      list.append(opt);
    }
  }

  async function fillUniverse(q, selector) {
    let body;
    try {
      body = await (await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=20`)).json();
    } catch (err) {
      return;
    }
    fillOptions($(selector), body.items || []);
  }

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
    const items = body.items || [];
    // 第三种静默故障：后端进程**认识** `/api/industries`、却**不认识** `local`
    // （路由比字段老）。这时 `it.local === true` 恒为假，卡片会**全都暗着**、
    // 页脚会印出「本地已有 0 / N」—— 那是一句**错话**。状态码是 200，
    // `res.ok` 拦不住；后端测试测的是新代码，也拦不住。所以在这里显式认出来。
    const stale = items.length > 0
      && !items.some((it) => it && typeof it === "object" && "local" in it);
    industryRows = items.map((it) => ({
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
      if (stale) {
        // 宁可说「我不知道」，也不说一句算出来的假话：`have` 在这里恒为 0，
        // 印出去就成了「本机一个板块的日线都没有」，而真相是**这个进程不知道**。
        foot.textContent =
          "后端进程比页面旧：/api/industries 没返回 local，所以卡片会全按" +
          "「待同步」显示 —— 这不代表本机真的没有板块日线。" +
          "重启看盘页后端（改过 src 必须重启）再刷新。";
      } else {
        const have = industryRows.filter((r) => r.local).length;
        foot.textContent =
          `本地已有 ${have} / ${industryRows.length} 个板块的日线。` +
          "标「本地」的选中就能看；其余选中后是空图，要先同步它的日线" +
          "（python -m chanlun sync --period day --codes 代码）。";
      }
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
        // 必须跟着刷 tab 的选中态：只换网格不换高亮的话，点「二级行业」会看到
        // 124 张卡配着「一级行业」的靛青下划线（2026-10-06 真实浏览器实测到的）。
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
    if (row.code === state.key) b.classList.add("is-current");

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
    b.addEventListener("click", () => pickIndustry(row.code));
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

  // 入口按钮显示**当前**看的是哪个行业。真正的「换票时同步」在 Task 4 挂进 `setCode`，
  // 这里先在清单到货后刷一次。
  function syncIndustryButton() {
    const name = $("#industry-name");
    const btn = $("#industry");
    if (!name || !btn) return;
    const hit = industryRows.find((r) => r.code === state.key);
    name.textContent = hit ? hit.name : "选一个行业…";
    btn.classList.toggle("has-pick", Boolean(hit));
  }

  // ------------------------------------------------------------------ 加自选按钮
  // 用户 2026-10-05 要求：**在股票界面上**就能加自选，而不是先跑去自选栏搜一遍代码。
  // 按钮状态直接由 `state.watch` 派生（不另存一份「已加」标记）：自选栏是唯一真相，
  // 复制一份状态迟早会和它对不上。
  function favError(text) {
    const box = $("#fav-error");
    if (!text) { box.hidden = true; box.textContent = ""; return; }
    box.textContent = text;
    box.hidden = false;
  }

  function inWatchlist(code) {
    return (state.watch || []).some((it) => it.code === code);
  }

  function renderFav() {
    const btn = $("#fav-btn");
    if (!btn) return;
    const code = state.key;   // 落库键：服务端归一会把 sz.000858 收成 000858
    const has = inWatchlist(code);
    btn.textContent = has ? "★ 已在自选" : "☆ 加自选";
    btn.setAttribute("aria-pressed", has ? "true" : "false");
    btn.title = has
      ? `${code} 已在左侧自选栏；点一下把它移出去`
      : `把当前这只票（${code}）加进左侧自选栏`;
    btn.disabled = !code;
  }

  $("#fav-btn").addEventListener("click", async () => {
    const code = state.key;   // 落库键：服务端归一会把 sz.000858 收成 000858
    if (!code) return;
    favError("");
    const has = inWatchlist(code);
    let resp;
    try {
      resp = has
        ? await fetch(`/api/watchlist?code=${encodeURIComponent(code)}`, { method: "DELETE" })
        : await fetch("/api/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            // 名字从**当前图上的详情**取：`state.data` 里已有本地库认得的名称，
            // 传空串也不会丢名字（后端 `add_watch` 会回落到库里已有的）。
            body: JSON.stringify({ code, name: nameOfCurrent() }),
          });
    } catch (err) {
      favError(`操作失败：连不上本地服务（${err}）。`);
      return;
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      favError(body.detail || `操作失败：HTTP ${resp.status}`);
      return;
    }
    await loadWatch();
    renderFav();
  });

  // 当前这只票显示用的名称。`/api/structure` 的返回里带 `name`（`api.py` 用
  // `meta.name_of` 查的）。取不到就返回空串 —— 后端会去库里找，
  // 这里**不许编**一个名字出来（编错了自选栏里就是一条假记录）。
  function nameOfCurrent() {
    const d = state.data;
    return d && typeof d.name === "string" ? d.name.trim() : "";
  }

  // ------------------------------------------------------------------ 交互绑定
  function setCode(code) {
    state.code = code;
    state.key = code;   // 先按用户写的形态占位；响应到了升级成服务端回的落库键
    $("#code").value = code;
    renderFav();
    // 行业选择器的入口按钮跟着当前代码走：换票之后它不能还留着上一个行业名。
    syncIndustryButton();
  }

  $("#query").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const raw = $("#code").value.trim() || "600000";
    // 名称也要能直接回车：先试代码形态，不成再问 `/api/search`。
    // 两条路都落成**落库键**，所以「浦发银行」和「恒生指数」都能这么进去。
    const found = await resolveCode(raw);
    if (!found.code && found.note) {
      showNotice("这一条看不了", found.note);
      return;
    }
    const code = found.code || raw;
    setCode(code);
    const rawLimit = parseInt($("#limit").value, 10);
    state.limit = Number.isFinite(rawLimit) ? Math.max(60, Math.min(20000, rawLimit)) : null;
    load();
  });

  for (const btn of document.querySelectorAll(".period")) {
    btn.addEventListener("click", () => {
      state.period = btn.dataset.period;
      setPeriodUI(state.period);
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
    searchTimer = setTimeout(() => fillUniverse(q, "#universe-options"), 150);
  });
  // 顶栏的票输入框也给候选：代码和**名称**都能搜（用户 2026-10-05 要求）。
  $("#code").addEventListener("input", () => {
    const q = $("#code").value.trim();
    clearTimeout(searchTimer);
    if (!q) return;
    searchTimer = setTimeout(() => fillUniverse(q, "#code-options"), 150);
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
    setPeriodUI(state.period);
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
  wireIndustryModal();
  loadIndustries();
  const remeasure = () => chart.resize();
  requestAnimationFrame(remeasure);
  setTimeout(remeasure, 0);
  setTimeout(remeasure, 300);
})();
