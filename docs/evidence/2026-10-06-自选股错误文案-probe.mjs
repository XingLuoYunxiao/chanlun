// F-1（D-45）的真实浏览器 A/B 验收：自选股读取失败的文案只能当**文本**渲染。
//
// 背景：`app.js` 原先在这一处把响应体里的 `body.detail` 插进模板串交给 `innerHTML`：
//     box.innerHTML = `<div class="watch-empty">自选股读取失败：${body.detail || "…"}</div>`;
// `detail` 来自 HTTP 响应体（跨进程边界），不是本文件的常量。修法改成
// `createElement` + `textContent` + `replaceChildren`。
//
// 探针打一个 fetch 补丁，让 `/api/watchlist/structure` 返回一个**不带 `items`**、
// 且 `detail` 里带 `<img onerror=…>` 的响应体 —— 正好命中那条分支。
//
// **两个场景，同一个页面，只有那一处写法不同**：
//   场景 A（不打补丁）= 磁盘上现在的 `app.js`；期望 `&lt;img`（转义文本），`onerror` 不触发。
//   场景 B（打补丁）= 用 CDP `Fetch` 拦截 `/static/app.js`，喂回**把那一处改回旧写法**
//     的源码（由当前源码做一次可见的字符串替换得到，不动磁盘上的文件）；
//     期望真正的 `<img` 标签，`onerror` 触发。
//   B 是 A 的**可失败性证明** —— 没有 B，"页面里没有 img"可能只是因为 payload
//   根本不是可解析的标记，那样判据恒真，什么都没测。
//
// **为什么不看终态**：`setCode()` 会调 `renderWatch()`（`app.js:223/233`），
// 它无条件 `box.innerHTML = ""` 再从 `state.watch` 重画。启动时 `load()` 先于
// `loadWatch()` 发出，后到的 `renderWatch()` 会把失败文案盖掉。所以终态可能是
// 「自选股是空的」，与"这条分支怎么写 DOM"无关。
// 探针改用 `MutationObserver` 记下 `#watch-rows` 的**每一次**状态，
// 判据钉在"失败文案那一次快照"上，不钉终态。
//
// 文案一律从 DOM dump 里读，不看截图。运行前需：
//   - 看盘页在 8888；
//   - 一个开了 `--remote-debugging-port=9222` 的 Chrome。
const PAGE = "http://127.0.0.1:8888";
const CDP = "http://127.0.0.1:9222";

const PAYLOAD = '<img src=x onerror="window.__xss_real=1">';
const DETAIL = `${PAYLOAD}已损坏的响应`;

// 场景 B 的改写：把修好后的那一块换回旧的一行。两段都是磁盘上源码的**逐字**内容。
const NEW_BLOCK = [
  "      // `body.detail` 是响应体里的文本，不是本文件的常量 —— 只能走 textContent。",
  "      // 交给 `innerHTML` 就等于让后端决定页面解析并执行什么标记（`tests/web/test_frontend_xss.py`）。",
  '      const fail = document.createElement("div");',
  '      fail.className = "watch-empty";',
  '      fail.textContent = `自选股读取失败：${body.detail || "接口返回了无法解析的内容。"}`;',
  "      box.replaceChildren(fail);",
].join("\n");
const OLD_BLOCK =
  '      box.innerHTML = `<div class="watch-empty">自选股读取失败：${body.detail || "接口返回了无法解析的内容。"}</div>`;';

const SRC = await (await fetch(`${PAGE}/static/app.js?_=${Date.now()}`)).text();
const OLD_SRC = SRC.replace(NEW_BLOCK, OLD_BLOCK);
// 前置：改写真的发生了。否则场景 B 就是场景 A 的复制品，A/B 什么都没测。
const AB_APPLIED = OLD_SRC !== SRC && OLD_SRC.includes(OLD_BLOCK);
console.log(`app.js ${SRC.length} 字节；场景 B 的改写生效：${AB_APPLIED}`);
if (!AB_APPLIED) {
  console.log("改写没生效 —— 源码形状变了。先修探针，别读下面的结论。");
  process.exit(2);
}

const PATCH = `(() => {
  window.__xss_real = 0;
  window.__hist = [];
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/api/watchlist/structure") === -1) return orig.apply(this, arguments);
    return Promise.resolve(new Response(
      JSON.stringify({ detail: ${JSON.stringify(DETAIL)} }),
      { status: 500, statusText: "Internal Server Error",
        headers: { "Content-Type": "application/json" } }
    ));
  };
  document.addEventListener("DOMContentLoaded", () => {
    const box = document.querySelector("#watch-rows");
    if (!box) return;
    const snap = () => window.__hist.push({
      t: Math.round(performance.now()), html: box.innerHTML,
    });
    snap();
    new MutationObserver(snap).observe(box, {
      childList: true, subtree: true, characterData: true,
    });
  });
})();`;

const DUMP = `(async () => {
  const box = document.querySelector("#watch-rows");
  if (!box) return JSON.stringify({ missing: "#watch-rows" });
  const OLD = ${JSON.stringify(`<div class="watch-empty">自选股读取失败：${DETAIL}</div>`)};
  const real = {
    html: box.innerHTML,
    text: box.textContent,
    imgCount: box.querySelectorAll("img").length,
    xssFired: window.__xss_real === 1,
    rows: box.querySelectorAll(".watch-row").length,
  };
  // 对照：同一条串走旧写法，证明 payload 确实是可解析的标记。
  const ctl = document.createElement("div");
  ctl.innerHTML = OLD.replace("__xss_real", "__xss_ctl");
  await new Promise((r) => setTimeout(r, 400));
  const control = {
    imgCount: ctl.querySelectorAll("img").length,
    text: ctl.textContent,
    xssFired: window.__xss_ctl === 1,
  };
  return JSON.stringify({ real, control, hist: window.__hist });
})()`;

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.n = 0;
    this.pending = new Map();
    this.handlers = new Map();
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) reject(new Error(JSON.stringify(m.error)));
        else resolve(m.result);
        return;
      }
      if (m.method && this.handlers.has(m.method)) this.handlers.get(m.method)(m.params);
    });
  }
  on(method, cb) {
    this.handlers.set(method, cb);
  }
  send(method, params = {}) {
    const id = ++this.n;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
}

async function open(url) {
  const r = await fetch(`${CDP}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  const t = await r.json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true });
    ws.addEventListener("error", rej, { once: true });
  });
  return { target: t, cdp: new Cdp(ws), ws };
}

async function evalJson(cdp, expr) {
  const r = await cdp.send("Runtime.evaluate", {
    expression: expr, returnByValue: true, awaitPromise: true,
  });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
  return JSON.parse(r.result.value);
}

async function scenario(oldSource) {
  const { target, cdp, ws } = await open("about:blank");
  try {
    await cdp.send("Page.enable");
    await cdp.send("Network.enable");
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    if (oldSource) {
      cdp.on("Fetch.requestPaused", async (p) => {
        if (!p.request.url.includes("/static/app.js")) {
          await cdp.send("Fetch.continueRequest", { requestId: p.requestId });
          return;
        }
        await cdp.send("Fetch.fulfillRequest", {
          requestId: p.requestId,
          responseCode: 200,
          responseHeaders: [
            { name: "Content-Type", value: "text/javascript; charset=utf-8" },
          ],
          body: Buffer.from(oldSource, "utf-8").toString("base64"),
        });
      });
      await cdp.send("Fetch.enable", {
        patterns: [{ urlPattern: "*app.js*", requestStage: "Request" }],
      });
    }
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: PATCH });
    await cdp.send("Page.navigate", { url: `${PAGE}/?code=sh.600000&_=${Date.now()}` });
    let dump = null;
    for (let i = 0; i < 80; i++) {
      await new Promise((r) => setTimeout(r, 250));
      try {
        const d = await evalJson(cdp, DUMP);
        if ((d.hist || []).some((h) => (h.html || "").includes("自选股读取失败"))) {
          dump = d;
          break;
        }
        dump = d;
      } catch { /* 页面还没就绪 */ }
    }
    return dump;
  } finally {
    ws.close();
    await fetch(`${CDP}/json/close/${target.id}`).catch(() => {});
  }
}

function report(name, dump, expectEscaped) {
  const { real, control, hist } = dump;
  const failSnaps = (hist || []).filter((h) => (h.html || "").includes("自选股读取失败"));
  const fail = failSnaps.length ? failSnaps[failSnaps.length - 1] : null;
  const last = (hist || []).length ? hist[hist.length - 1] : null;
  console.log(`\n===== 场景 ${name} =====`);
  console.log(`终态 real = ${JSON.stringify(real)}`);
  console.log(`对照 control = ${JSON.stringify(control)}`);
  console.log(`快照数 ${(hist || []).length}，含失败文案 ${failSnaps.length} 张`);
  if (fail) console.log(`失败文案快照：${fail.html}`);
  if (last) console.log(`终态快照：${last.html}`);

  const hasRealTag = fail !== null && /<img[\s>]/.test(fail.html);
  const hasEscaped = fail !== null && fail.html.includes("&lt;img");
  const checks = expectEscaped
    ? [
        ["前置：那条失败文案真的渲染过", fail !== null],
        ["前置：后端给的 detail 原样出现在文案里", (fail && fail.html.includes("已损坏的响应")) || false],
        ["对照：同一条串走旧写法会长出 1 个 img（payload 确实是可解析标记）", control.imgCount === 1],
        ["对照：旧写法的 onerror 真的触发了", control.xssFired === true],
        ["没有真正的 `<img` 标签", !hasRealTag],
        ["是转义后的 `&lt;img`（当文本写了进去）", hasEscaped],
        ["onerror 全程没被触发", real.xssFired === false],
        ["没有任何一张快照渲染成自选股行", real.rows === 0],
      ]
    : [
        ["前置：那条失败文案真的渲染过", fail !== null],
        ["**长出了真正的 `<img` 标签**（旧写法的样子）", hasRealTag],
        ["**没有 `&lt;img`**（说明它被当标记解析了，不是文本）", !hasEscaped],
        ["**onerror 触发了**（`window.__xss_real === 1`）", real.xssFired === true],
      ];
  let bad = 0;
  for (const [n, ok] of checks) {
    if (!ok) bad++;
    console.log(`${ok ? "PASS" : "FAIL"}  ${n}`);
  }
  console.log(`${checks.length - bad} / ${checks.length} 通过`);
  return { total: checks.length, bad };
}

const a = report("A 磁盘上的 app.js（修复后）", await scenario(null), true);
const b = report("B 拦截后喂回旧写法（修复前）", await scenario(OLD_SRC), false);

console.log("\n===== 结论 =====");
console.log(`场景 A（修复后）${a.total - a.bad}/${a.total}；场景 B（修复前）${b.total - b.bad}/${b.total}`);
console.log(
  a.bad === 0 && b.bad === 0
    ? "A 全过且 B 全过 —— 修复后当文本渲染，旧写法确实会执行标记：判据可失败。"
    : "有判据未通过，别当结论用。"
);
process.exit(a.bad === 0 && b.bad === 0 ? 0 : 1);
