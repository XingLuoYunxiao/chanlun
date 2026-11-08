// D-44 终审补丁 F12 的真实浏览器验收：
//   场景 A（打补丁）：把 `/api/industries` 的每个 item 里的 `local` 键**摘掉**，
//     模拟「进程比页面旧」（路由认识、字段不认识）。期望：页脚改口说
//     「后端进程比页面旧…」，且没有一张卡是亮的。
//   场景 B（不打补丁，回归）：期望页脚报**真计数**，亮卡数等于接口说 `local` 的一级只数。
//     具体数字由 `/api/industries` **现算**（见下面 EXP），不写死 —— 理由同 EXP 处注释。
//   场景 C（打补丁：把**一个** `local: true` 改成 false）：让「亮着的数」与「总数」
//     **不相等**，这样「页脚报的是真计数、不是总数」才是可失败的判据。
//     —— 场景 B 在「本机全部同步完」的数据状态下会退化：`have` 恰好等于 `total`，
//     把 `have` 写成 `industryRows.length` 也印出同一句话，判据恒真。
//     静态护栏（`tests/web/test_frontend_industry.py` 钉 `have` 的来源）仍然管这件事，
//     但探针这一侧不能靠数据状态碰巧成立。
// 文案一律从 DOM dump 里读，不看截图。
const PAGE = "http://127.0.0.1:8888";
const CDP = "http://127.0.0.1:9222";

// 期望值现算，不写死：本机有几个板块落了日线，是**数据状态**，不是产品行为。
// 第一版把「恰好 1 张亮卡 / 本地已有 1 / 155」钉成常量；2026-10-06 用户要求把
// 一级 / 二级全部 155 个板块同步下来之后，这两条判据必然误报。
// 这两条判据真正要测的是「页脚 / 亮灯报的是不是**接口说的那个真值**」，
// 所以真值应当从接口拿 —— 写死只会让它随数据漂移而说谎。
const API = await (await fetch(`${PAGE}/api/industries`)).json();
const EXP = {
  total: API.items.length,
  l1: API.items.filter((i) => i.level === "1").length,
  l2: API.items.filter((i) => i.level === "2").length,
  litAll: API.items.filter((i) => i.local).length,
  litL1: API.items.filter((i) => i.local && i.level === "1").length,
};
const TABS = [`一级行业（${EXP.l1}）`, `二级行业（${EXP.l2}）`];
console.log(
  `数据状态：${EXP.litAll} / ${EXP.total} 个板块有本地日线` +
    `（一级 ${EXP.litL1} / ${EXP.l1}，二级 ${EXP.litAll - EXP.litL1} / ${EXP.l2}）`
);

const STRIP_LOCAL = `(() => {
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/api/industries") === -1) return orig.apply(this, arguments);
    return orig.apply(this, arguments).then((res) =>
      res.clone().json().then((body) => {
        if (body && Array.isArray(body.items)) {
          body.items = body.items.map((it) => ({ code: it.code, name: it.name, level: it.level }));
        }
        return new Response(JSON.stringify(body), {
          status: res.status,
          statusText: res.statusText,
          headers: { "Content-Type": "application/json" },
        });
      })
    );
  };
})();`;

// 场景 C 用：只把**第一个** `local: true` 的板块改成 `false`，其余原样。
// 页面据此算出的 `have` 应当比 `total` 少 1 —— 这是「报真计数」与「报总数」的分水岭。
const FLIP_ONE_LOCAL = `(() => {
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (url.indexOf("/api/industries") === -1) return orig.apply(this, arguments);
    return orig.apply(this, arguments).then((res) =>
      res.clone().json().then((body) => {
        if (body && Array.isArray(body.items)) {
          let flipped = false;
          body.items = body.items.map((it) => {
            if (!flipped && it && it.local === true) {
              flipped = true;
              return { ...it, local: false };
            }
            return it;
          });
        }
        return new Response(JSON.stringify(body), {
          status: res.status,
          statusText: res.statusText,
          headers: { "Content-Type": "application/json" },
        });
      })
    );
  };
})();`;

// 场景 C 的期望值：和页面看到的是同一份 items（同一顺序），所以「第一个亮的」就是它。
const FIRST_LIT = API.items.find((i) => i.local) || null;
const C = {
  litAll: EXP.litAll - (FIRST_LIT ? 1 : 0),
  litL1: EXP.litL1 - (FIRST_LIT && FIRST_LIT.level === "1" ? 1 : 0),
};

const DUMP = `(() => {
  const cards = [...document.querySelectorAll(".ind-card")];
  const t = (sel) => { const e = document.querySelector(sel); return e ? e.textContent : null; };
  const dlg = document.querySelector("#industry-modal");
  return JSON.stringify({
    modalOpen: dlg ? dlg.open === true : null,
    foot: t("#ind-modal-foot"),
    note: t("#industry-note"),
    noteHidden: document.querySelector("#industry-note")
      ? document.querySelector("#industry-note").hidden : null,
    entry: t("#industry"),
    tabs: [...document.querySelectorAll(".ind-tab")].map((e) => e.textContent),
    cards: cards.length,
    lit: cards.filter((c) => c.dataset.local === "1").length,
    dim: cards.filter((c) => c.dataset.local === "0").length,
    localAttrMissing: cards.filter((c) => !("local" in c.dataset)).length,
  });
})()`;

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.n = 0;
    this.pending = new Map();
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) reject(new Error(JSON.stringify(m.error)));
        else resolve(m.result);
      }
    });
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

async function scenario(name, patch) {
  const { target, cdp, ws } = await open("about:blank");
  try {
    await cdp.send("Page.enable");
    await cdp.send("Network.enable");
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    if (patch) {
      await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: patch });
    }
    await cdp.send("Page.navigate", { url: `${PAGE}/?code=sw.801120&_=${Date.now()}` });
    // 等网格长出来（`loadIndustries()` 是启动尾部最后一次取数）
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 250));
      try {
        const d = await evalJson(cdp, DUMP);
        if (d.cards > 0) break;
      } catch { /* 页面还没就绪 */ }
    }
    // 真的把弹窗打开（走入口按钮，和用户一样）
    await cdp.send("Runtime.evaluate", {
      expression: `document.querySelector("#industry").click()`,
      userGesture: true,
    });
    await new Promise((r) => setTimeout(r, 200));
    const dump = await evalJson(cdp, DUMP);
    console.log(`\n===== 场景 ${name} =====`);
    console.log(JSON.stringify(dump, null, 2));
    return dump;
  } finally {
    ws.close();
    await fetch(`${CDP}/json/close/${target.id}`).catch(() => {});
  }
}

const a = await scenario("A 打补丁：后端不认识 local", STRIP_LOCAL);
const b = await scenario("B 不打补丁：真后端", null);
const c = await scenario("C 打补丁：把一个亮的改成不亮", FLIP_ONE_LOCAL);

const checks = [
  ["前置：本机至少一个板块落了日线（否则场景 C 退化成 B）", EXP.litAll >= 1],
  ["A 弹窗确实打开了", a.modalOpen === true],
  ["A 页脚改口（含「后端进程比页面旧」）", (a.foot || "").includes("后端进程比页面旧")],
  ["A 页脚明确否认「本机没有数据」", (a.foot || "").includes("这不代表本机真的没有板块日线")],
  ["A 页脚不再报算不出的计数", !(a.foot || "").includes("本地已有")],
  ["A 一张亮卡都没有", a.lit === 0],
  // 注意：网格一次只渲染**当前一级**的卡，而页脚数的是全部 155 项 ——
  // 所以这里钉一级的只数（现算），不是 155。第一版探针写成 155，是探针错、不是产品错。
  ["A 全部卡片都落在 data-local=\"0\"（当前一级）", a.dim === a.cards && a.cards === EXP.l1],
  ["A 左栏核对日期仍在（只有页脚坏了）", (a.note || "").includes("清单核对于") && a.noteHidden === false],
  ["A tab 条仍由 body.levels 驱动", JSON.stringify(a.tabs) === JSON.stringify(TABS)],
  ["B 页脚报真计数", (b.foot || "").includes(`本地已有 ${EXP.litAll} / ${EXP.total} 个板块的日线`)],
  ["B 亮卡数 = 接口说 local 的一级只数", b.lit === EXP.litL1 && b.dim === EXP.l1 - EXP.litL1 && b.cards === EXP.l1],
  ["B 不出现「后端进程比页面旧」", !(b.foot || "").includes("后端进程比页面旧")],
  ["B tab 条同上", JSON.stringify(b.tabs) === JSON.stringify(TABS)],
  // C 是「报真计数」这条判据的可失败版本：这里 `have` ≠ `total`。
  ["C 页脚报真计数（比总数少 1）", (c.foot || "").includes(`本地已有 ${C.litAll} / ${EXP.total} 个板块的日线`)],
  ["C 亮卡数 = 接口说 local 的一级只数（少 1）", c.lit === C.litL1 && c.dim === EXP.l1 - C.litL1 && c.cards === EXP.l1],
  ["C 不出现「后端进程比页面旧」", !(c.foot || "").includes("后端进程比页面旧")],
];
console.log("\n===== 判据 =====");
let bad = 0;
for (const [name, ok] of checks) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}`);
  if (!ok) bad++;
}
console.log(`\n${checks.length - bad} / ${checks.length} PASS`);
process.exit(bad ? 1 : 0);
