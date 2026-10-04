// D-41 `capped` 中枢视觉标记 —— 真实浏览器复测探针（2026-10-04）
//
// 量什么：`sh.600519` 日线 1200 根（该票第 0 个中枢实测 `capped: true`）页面上
//   ① 中枢图例行（`#ledger` 里的 `.row-meta`）是否带 ` · 段数到顶（第33课：…）`；
//   ② echarts 的 markArea 里那一个 `capped` 中枢是否真的画成
//      `borderType: "dotted"` + `borderWidth: 2` + `opacity: 0.6`；
//   ③ 标签 formatter 是否含 ` · 段数到顶`；
//   ④ **反向对照**：同页第 1 个中枢 `capped: false`，它的图例行与 markArea
//      必须**不带**上述任何一项 —— 否则这个标记就是「所有中枢都亮」的假指标。
//
// 怎么跑（headless Chrome 必须先起着；看盘页须已在 127.0.0.1:8888）：
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//     --headless=new --remote-debugging-port=9222 --user-data-dir=/tmp/cdp-profile \
//     http://127.0.0.1:8888/ > /tmp/chrome.log 2>&1 &
//   node docs/evidence/2026-10-04-capped-marker-probe.mjs
//
// ★ 必须禁缓存（`Network.setCacheDisabled` + `?_=` 查询串）：`app.js` 会被浏览器
//   缓存，改了静态文件却量到旧版，会得出完全相反的结论。
// 依赖：node 内置 WebSocket（无 puppeteer / playwright）。跑完约 25 秒。
const PORT = 9222;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
const page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let id = 0; const pend = new Map();
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) {
    const { res, rej } = pend.get(m.id); pend.delete(m.id);
    m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
  }
};
const send = (m, p = {}) => new Promise((res, rej) => {
  const i = ++id; pend.set(i, { res, rej });
  ws.send(JSON.stringify({ id: i, method: m, params: p }));
});
const raw = async (expr) => (await send("Runtime.evaluate", {
  expression: expr, returnByValue: true, awaitPromise: true,
})).result;

await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
await send("Network.setCacheDisabled", { cacheDisabled: true });

const URL = `http://127.0.0.1:8888/?code=sh.600519&period=day&mode=strict&limit=1200&_=${Date.now()}`;
await send("Page.navigate", { url: URL });
// 轮询到加载完（`#stamp` 不再是「尚未加载」），最多 30 秒
let ready = false;
for (let i = 0; i < 60; i++) {
  await sleep(500);
  const r = await raw(`document.querySelector('#stamp').textContent`);
  if (r.value && r.value !== "尚未加载" && !/加载中/.test(r.value)) { ready = true; break; }
}
if (!ready) { console.log("FAIL: 页面 30 秒内没加载完"); ws.close(); process.exit(2); }
await sleep(1500);

const M = `(()=>{
  const L=document.querySelector('#ledger');
  const lr=L.getBoundingClientRect();
  const rows=[...L.querySelectorAll('.row')].map(b=>({
    title:(b.querySelector('.row-title .dir')||{}).textContent||'',
    chips:[...b.querySelectorAll('.row-title .chip')].map(c=>c.textContent),
    meta:(b.querySelector('.row-meta')||{}).textContent||'',
    metaRight:+((b.querySelector('.row-meta')||{getBoundingClientRect:()=>({right:0})}).getBoundingClientRect().right).toFixed(1),
  })).filter(r=>/^中枢 /.test(r.title));
  const c=echarts.getInstanceByDom(document.querySelector('#chart'));
  if(!c) return JSON.stringify({ERR:'no echarts instance'});
  const opt=c.getOption();
  const areas=[];
  for(const s of opt.series||[]){
    for(const ma of (s.markArea&&s.markArea.data)||[]){
      for(const it of (Array.isArray(ma)?ma:[ma])){
        const st=it.itemStyle||{};
        areas.push({name:s.name||'',borderType:st.borderType||null,borderWidth:st.borderWidth||null,
          opacity:st.opacity===undefined?null:st.opacity,
          label:(it.label&&it.label.formatter)||null});
      }
    }
  }
  return JSON.stringify({ledgerRight:+lr.right.toFixed(1),pivotRows:rows,areas});
})()`;
const r = JSON.parse((await raw(M)).value);
if (r.ERR) { console.log("FAIL:", r.ERR); ws.close(); process.exit(2); }

const SUF = "段数到顶（第33课：已构成更大级别中枢；本级别不产出买卖点）";
const pivotMetas = r.pivotRows.map((x) => x.meta);
const cappedRows = pivotMetas.filter((t) => t.includes(SUF));
const plainRows = pivotMetas.filter((t) => !t.includes(SUF));
const chipRows = r.pivotRows.filter((x) => x.chips.includes("段数到顶"));
const noChipRows = r.pivotRows.filter((x) => !x.chips.includes("段数到顶"));
const over = r.pivotRows.filter((x) => x.metaRight > r.ledgerRight + 0.5);
const dotted = r.areas.filter((a) => a.borderType === "dotted");
const plainAreas = r.areas.filter((a) => a.borderType !== "dotted");
const labeled = r.areas.filter((a) => (a.label || "").includes(" · 段数到顶"));
const unlabeled = r.areas.filter((a) => !(a.label || "").includes("段数到顶"));

console.log("=== 中枢图例行（DOM dump，逐字）===");
r.pivotRows.forEach((x, i) => console.log(`  [${i}] 标题=${JSON.stringify(x.title)} 标签=${JSON.stringify(x.chips)}\n       meta=${JSON.stringify(x.meta)}`));
console.log(`\n=== markArea 全部条目（${r.areas.length} 条）===`);
r.areas.forEach((a, i) => console.log(`  [${i}] ${JSON.stringify(a)}`));

const checks = [
  ["图例 meta：带「段数到顶」的中枢行数 == 1", cappedRows.length === 1, `实测 ${cappedRows.length}`],
  ["图例 meta：不带后缀的中枢行数 >= 1（反向对照）", plainRows.length >= 1, `实测 ${plainRows.length}`],
  ["标题行标签：含「段数到顶」的中枢行数 == 1", chipRows.length === 1, `实测 ${chipRows.length}`],
  ["标题行标签：不含的中枢行数 >= 1（反向对照）", noChipRows.length >= 1, `实测 ${noChipRows.length}`],
  ["布局：没有任何中枢行 meta 越出右栏边界", over.length === 0, `越界 ${over.length} 条，最多 ${over.length ? Math.max(...over.map((x) => +(x.metaRight - r.ledgerRight).toFixed(1))) : 0}px`],
  ["markArea：dotted 边框条目数 == 1", dotted.length === 1, `实测 ${dotted.length}`],
  ["markArea：非 dotted 边框条目数 >= 1（反向对照）", plainAreas.length >= 1, `实测 ${plainAreas.length}`],
  ["markArea：dotted 那条 borderWidth==2 且 opacity==0.6",
    dotted.length === 1 && dotted[0].borderWidth === 2 && dotted[0].opacity === 0.6,
    dotted.length ? JSON.stringify(dotted[0]) : "无 dotted 条目"],
  ["标签：含「 · 段数到顶」的条目数 == 1", labeled.length === 1, `实测 ${labeled.length}`],
  ["标签：不含「段数到顶」的条目数 >= 1（反向对照）", unlabeled.length >= 1, `实测 ${unlabeled.length}`],
];
let bad = 0;
console.log("\n=== 判据 ===");
for (const [name, ok, detail] of checks) {
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${name}  —— ${detail}`);
  if (!ok) bad++;
}
console.log(bad === 0 ? "\n结论：全部通过（标记只在 capped 中枢上生效，且反向对照不亮）"
                      : `\n结论：${bad} 条判据失败`);

// 截图（只作布局旁证，文案以 DOM dump 为准）
// 先把 capped 那一行滚进视口 —— 它在清单底部，不滚的话截图里看不到那个标签。
await raw(`(()=>{const b=[...document.querySelectorAll('#ledger .row')]
  .find(x=>[...x.querySelectorAll('.chip')].some(c=>c.textContent==='段数到顶'));
  if(b) b.scrollIntoView({block:'center'}); return !!b;})()`);
await sleep(600);
const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
const fs = await import("node:fs");
fs.writeFileSync("docs/evidence/2026-10-04-capped-marker.png", Buffer.from(shot.data, "base64"));
console.log("截图：docs/evidence/2026-10-04-capped-marker.png");
ws.close();
process.exit(bad === 0 ? 0 : 1);
