/* 缠论盘后结构复盘台 —— 绘图、交互、自选池
 *
 * 图上的每一层都对应一个"能不能信"的判断：
 *   笔 / 线段  —— 方向（红涨绿跌），虚线与半透明表示**未确认**，会被后面的K线改写；
 *   中枢       —— 靛青区间带（ZG/ZD）+ 虚线外沿（GG/DD），中枢按定义是重叠，不用涨跌色；
 *   买卖点     —— 一/二/三类买卖点，实心=已确认，空心=未确认；
 *   确认刻度   —— 本页的签名：结构**在哪一根K线上才可被看见**。
 *                 结构终点和确认点常常差很多根K线，回测能不能用就看后者。
 *
 * 所有数字都来自后端 /api/structure（含 MACD），前端不做第二次计算：两边口径一旦
 * 分叉，页面上就会拿一条和买卖点无关的 MACD 去解释背驰。
 */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const KIND_CN = { b1: "一买", b2: "二买", b3: "三买", s1: "一卖", s2: "二卖", s3: "三卖" };
  const STATUS_CN = { confirmed: "确认", tentative: "未确认", invalidated: "已失效" };
  const PERIOD_CN = { day: "日线", 30: "30分", 5: "5分", 60: "60分", 15: "15分" };
  // 价格按最小报价单位两位小数显示；原始值仍是引擎给的 float，这里只做呈现。
  const f2 = (v) => (v == null || v === "" ? "—" : Number(v).toFixed(2));
  // 成交量轴宽度只有 62px：40,000,000 会被截成 "00,000,000"，所以改成万/亿单位。
  function volLabel(v) {
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1) + "亿";
    if (a >= 1e4) return (v / 1e4).toFixed(0) + "万";
    return String(v);
  }

  const state = {
    code: "600000",
    period: "day",
    limit: null,
    data: null,
    layers: { strokes: true, segments: true, pivots: true, signals: true, margin: true },
  };

  const chart = echarts.init($("#chart"), null, { renderer: "canvas" });
  // 容器尺寸要等样式表 + flex 布局算完才存在。首屏脚本早于布局执行时，init 量到的是
  // 一个几十像素的宽度，grid 就按那个宽度去算（实测只剩 35px 宽，整张图挤成一条竖线）。
  // 这里让容器真的拿到尺寸后再 resize 一次；之后窗口缩放、栏位折叠也都走同一条路。
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => chart.resize()).observe($("#chart"));
  }
  window.addEventListener("resize", () => chart.resize());

  // ------------------------------------------------------------------ 取数
  async function load() {
    const qs = new URLSearchParams({ code: state.code, period: state.period });
    if (state.limit) qs.set("limit", String(state.limit)); // 未指定时用后端默认窗口（1200）
    setStamp("加载中…");
    let resp;
    try {
      resp = await fetch(`/api/structure?${qs}`);
    } catch (err) {
      return showNotice("连不上本地服务", `请确认服务在本机 8888 端口运行：${err}`, "python -m chanlun serve");
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      return showNotice(httpTitle(resp.status), body.detail || "接口返回了无法解析的内容。", null);
    }
    state.data = body;
    hideNotice();
    draw();
    renderLedger();
    const c = body.counts;
    setStamp(
      `${body.code} · ${PERIOD_CN[body.period] || body.period} · 截至 ${body.as_of}\n` +
      `线段 ${c.segments}（确认 ${c.confirmed_segments} / 未确认 ${c.tentative_segments}） · 中枢 ${c.pivots} · 买卖点 ${c.signals}`
    );
  }

  function httpTitle(status) {
    if (status === 404) return "没有这只票的本地数据";
    if (status === 400) return "代码或参数不对";
    return `接口出错（HTTP ${status}）`;
  }

  function showNotice(title, detail, command) {
    const box = $("#notice");
    box.innerHTML = "";
    const h = document.createElement("h3");
    h.textContent = title;
    const p = document.createElement("p");
    p.textContent = detail;
    box.append(h, p);
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

  function setStamp(text) { $("#stamp").textContent = text; }

  // ------------------------------------------------------------------ 绘图
  function draw() {
    const d = state.data;
    const bars = d.bars;
    const ts = bars.map((b) => b.ts);
    const idxOf = new Map(ts.map((t, i) => [t, i]));
    const up = cssVar("--cinnabar"), down = cssVar("--bamboo");
    const indigo = cssVar("--indigo"), mohui = cssVar("--mohui"), amber = cssVar("--amber");
    const rice = cssVar("--rice"), dim = cssVar("--rice-dim"), line = cssVar("--line");

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
        const buy = s.kind.startsWith("b");
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
        { type: "inside", xAxisIndex: [0, 1, 2, 3], start: startPct, end: 100, zoomOnMouseWheel: true },
        {
          type: "slider", xAxisIndex: [0, 1, 2, 3], start: startPct, end: 100,
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
      const buy = s.kind.startsWith("b");
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

  // ------------------------------------------------------------------ 自选池
  async function loadWatch() {
    const box = $("#watch-cards");
    let body;
    try {
      body = await (await fetch(`/api/watchlist/structure?period=${state.period}`)).json();
    } catch {
      box.innerHTML = '<div class="watch-empty">自选池读取失败。</div>';
      return;
    }
    if (!body.items || !body.items.length) {
      box.innerHTML = '<div class="watch-empty">自选池是空的。把常看的票加进来，每次打开页面都能看到它们的线段方向、中枢区间和买卖点。</div>';
      return;
    }
    box.innerHTML = "";
    for (const it of body.items) {
      const card = document.createElement("div");
      const cls = ["watch-card"];
      if (it.missing) cls.push("is-missing");
      if (!it.missing && it.signals && it.signals.length) cls.push("has-signal");
      card.className = cls.join(" ");

      const head = document.createElement("div");
      head.className = "watch-card-head";
      const code = document.createElement("span");
      code.className = "watch-code";
      code.textContent = it.code;
      const name = document.createElement("span");
      name.className = "watch-name";
      name.textContent = it.name || "";
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "watch-rm";
      rm.title = "移出自选";
      rm.textContent = "×";
      rm.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        await fetch(`/api/watchlist?code=${it.code}`, { method: "DELETE" });
        loadWatch();
      });
      head.append(code, name, rm);
      card.append(head);

      if (it.missing) {
        const tag = document.createElement("span");
        tag.className = "watch-tag";
        tag.textContent = it.error || "缺少数据";
        card.append(tag);
      } else {
        const line1 = document.createElement("div");
        line1.className = "watch-line";
        line1.textContent = `截至 ${it.as_of} · 线段 ${it.counts.segments}（未确认 ${it.counts.tentative_segments}）`;
        const line2 = document.createElement("div");
        line2.className = "watch-line";
        const p = it.pivots[it.pivots.length - 1];
        line2.textContent = p ? `末中枢 ${p.status === "tentative" ? "未确认 " : ""}${f2(p.zd)}–${f2(p.zg)}` : "无中枢";
        const line3 = document.createElement("div");
        line3.className = "watch-line";
        const seg = it.last_segment;
        line3.textContent = seg
          ? `末段 ${seg.direction > 0 ? "向上" : "向下"} ${seg.low}–${seg.high}${seg.status === "tentative" ? "（未确认）" : ""}`
          : "无线段";
        card.append(line1, line2, line3);
        if (it.signals && it.signals.length) {
          const tag = document.createElement("span");
          tag.className = "watch-tag";
          tag.textContent = it.signals.map((s) => `${KIND_CN[s.kind] || s.kind} ${s.ts}`).join(" · ");
          card.append(tag);
        }
      }

      card.addEventListener("click", () => {
        if (it.missing) return;
        setCode(it.code);
        load();
      });
      box.append(card);
    }
  }

  async function addWatch() {
    const input = $("#watch-code");
    const raw = input.value.trim();
    if (!raw) return;
    const resp = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: raw }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      showNotice("加自选失败", body.detail || `HTTP ${resp.status}`, null);
      return;
    }
    input.value = "";
    hideNotice();
    loadWatch();
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

  $("#watch-add").addEventListener("click", addWatch);
  $("#watch-code").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); addWatch(); }
  });

  // 初始加载：URL 可带 ?code=&period=，方便把常看的票做成书签
  const sp = new URLSearchParams(location.search);
  if (sp.get("code")) setCode(sp.get("code"));
  if (sp.get("period") && PERIOD_CN[sp.get("period")]) {
    state.period = sp.get("period");
    for (const b of document.querySelectorAll(".period")) {
      const on = b.dataset.period === state.period;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
  }
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
