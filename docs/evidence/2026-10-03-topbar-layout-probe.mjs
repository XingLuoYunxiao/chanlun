// 顶栏布局稳定化 —— 真实浏览器复测探针（2026-10-03）
//
// 量什么：9 个视口宽 × 3 个周期（日/周/月）× 2 个口径（严格/非严格）下，
//         .topbar 高、#stamp 行数、#stamp-detail 高与逻辑行数、.period 尺寸、
//         .periods 宽与裁切、横向溢出，并 dump 三个周期的详情行全文。
// 判据：同一视口宽下三个周期的「顶栏高」与「详情行高」必须相等（恒定），
//        按钮恒 59.8×36、.periods 恒 298.6、裁切无、溢出否。
//
// 怎么跑（headless Chrome 必须先起着；看盘页须已在 127.0.0.1:8888）：
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
//     --headless=new --remote-debugging-port=9222 --user-data-dir=/tmp/cdp-profile \
//     http://127.0.0.1:8888/ > /tmp/chrome.log 2>&1 &
//   node docs/evidence/2026-10-03-topbar-layout-probe.mjs
//
// ★ 必须禁缓存（Network.setCacheDisabled + `?_=` 查询串）：本页的 index.html / app.js
//   会被浏览器缓存，改了静态文件却量到旧版，会得出完全相反的结论（本次真踩过）。
// 依赖：node 内置 WebSocket（无 puppeteer / playwright）。跑完约 7 分钟。
const PORT=9222;
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const list=await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
const page=list.find(t=>t.type==='page'&&t.webSocketDebuggerUrl);
const ws=new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej;});
let id=0; const pend=new Map();
ws.onmessage=(e)=>{const m=JSON.parse(e.data); if(m.id&&pend.has(m.id)){const{res,rej}=pend.get(m.id);pend.delete(m.id);m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);}};
const send=(m,p={})=>new Promise((res,rej)=>{const i=++id;pend.set(i,{res,rej});ws.send(JSON.stringify({id:i,method:m,params:p}));});
const raw=async(expr)=>(await send('Runtime.evaluate',{expression:expr,returnByValue:true})).result;
await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
await send('Network.setCacheDisabled',{cacheDisabled:true});

const M=`(()=>{const P=document.querySelector('.periods'),b=P&&P.children[0],H=document.querySelector('.topbar'),
S=document.querySelector('#stamp'),D=document.querySelector('#stamp-detail');
if(!P||!b||!H||!S||!D) return JSON.stringify({ERR:1});
return JSON.stringify({btnW:+b.getBoundingClientRect().width.toFixed(1),btnH:+b.getBoundingClientRect().height.toFixed(1),
periodsW:+P.getBoundingClientRect().width.toFixed(1),
periodsClip:P.scrollWidth>P.clientWidth?P.scrollWidth-P.clientWidth:0,
topbarH:+H.getBoundingClientRect().height.toFixed(1),
stampH:+S.getBoundingClientRect().height.toFixed(1),
stampLines:(S.textContent||'').split('\\n').length,
detailH:+D.getBoundingClientRect().height.toFixed(1),
detailLines:(D.textContent||'').split('\\n').length,
docScroll:document.documentElement.scrollWidth,vw:innerWidth,
stampText:S.textContent,detailText:D.textContent});})()`;

const widths=[1600,1440,1366,1280,1200,1024,900,860,800];
for(const mode of ['strict','loose']){
  console.log(`\n########## 口径 = ${mode} ##########`);
  await send('Page.navigate',{url:`http://127.0.0.1:8888/?mode=${mode}&period=day&_=${Date.now()}`});
  await sleep(9000);
  for(const w of widths){
    await send('Emulation.setDeviceMetricsOverride',{width:w,height:1000,deviceScaleFactor:1,mobile:false});
    await sleep(900);
    const rows=[],texts=[];
    for(const p of ['day','week','month']){
      await send('Runtime.evaluate',{expression:`document.querySelector('.period[data-period="${p}"]').click()`});
      await sleep(4000);
      const r=JSON.parse((await raw(M)).value); rows.push(r); texts.push(r.detailText);
    }
    if(rows.some(r=>r.ERR)){console.log(`${String(w).padStart(6)} | 取数失败`);continue;}
    const dh=rows.map(r=>r.detailH), th=rows.map(r=>r.topbarH);
    const ov=rows.some(r=>r.docScroll>r.vw), clip=rows.some(r=>r.periodsClip>0);
    // 第二行（口径）是否三个周期逐字相同
    const l2=new Set(texts.map(t=>t.split('\n')[1]));
    console.log(`${String(w).padStart(6)} | 详情高 ${dh.join('/')} | 第二行同文=${l2.size===1} | 顶栏高 ${th.join('/')} | 按钮 ${rows.map(r=>r.btnW+'x'+r.btnH).join(' ')} | .periods ${rows.map(r=>r.periodsW).join('/')} 裁切${clip?'有':'无'} | 图注行 ${rows.map(r=>r.stampLines).join('/')} | 溢出 ${ov?'是':'否'} | 详情恒定=${new Set(dh).size===1} 顶栏恒定=${new Set(th).size===1}`);
    if(w===1600){for(const [i,p] of ['日','周','月'].entries()){
      console.log(`   [DOM 1600] ${p}线详情 = ${JSON.stringify(texts[i])}`);}}
  }
}
await send('Emulation.clearDeviceMetricsOverride');
ws.close();
