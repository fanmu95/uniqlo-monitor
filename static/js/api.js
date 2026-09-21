/* 通用工具：DOM / 请求 / 格式化 / 灯箱（全局，无模块化构建） */
const $ = id => document.getElementById(id);

function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,c=>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function show(msg, isErr){
  $('msg').innerHTML = '<div class="msg' + (isErr?' err':'') + '">' + msg + '</div>';
  if(!isErr) setTimeout(()=>{ if($('msg').innerText===msg) $('msg').innerHTML=''; }, 6000);
}
function clearMsg(){ $('msg').innerHTML=''; }

async function api(url, opt){
  const r = await fetch(url, opt);
  if(!r.ok){ let d={}; try{d=await r.json()}catch(e){}
    throw new Error(d.detail || ('HTTP '+r.status)); }
  return r.json();
}
const postJson = (url, body) => api(url, {method:'POST',
  headers:{'Content-Type':'application/json'}, body:JSON.stringify(body||{})});

/* 图片 URL
   实测（2026-09-21）：
   1) 官网图片 CDN 按 TLS 指纹拦截 Python/OpenSSL（HTTP 567 AccessDeny），curl/浏览器正常
      → 前端直连 CDN，失败时逐级回退到后端 /img 代理；
   2) 分辨率档位并非都存在：/main/first/ 只有 561(1200px) 与 1000(2100px)，225 会 404；
      /sku/ 的 225(480px)/561/1000 均可用。
   因此默认用接口返回的原路径（保证存在），仅详情大图主动升到 1000 并在失败时回退。*/
const IMG_BASE = 'https://www.uniqlo.cn/';

function _imgPath(pic, w){
  let p = String(pic).replace(/^\//,'');
  if(w) p = p.replace(/\/(first|sku)\/\d+\//, '/$1/' + w + '/');
  return p;
}
function imgUrl(pic, w){
  if(!pic) return '';
  return IMG_BASE + _imgPath(pic, w);
}
function imgProxyUrl(pic, w){
  if(!pic) return '';
  const p = _imgPath(pic, w);
  return '/img/' + p + (w ? ('?w=' + w) : '');
}
/* <img>：w 为空 = 用原路径；失败链 高清档 → 原路径 → 本地代理 → 隐藏 */
function imgTag(pic, w, cls, alt, extraAttrs){
  if(!pic) return '';
  return '<img class="'+esc(cls||'')+'" loading="lazy" src="'+esc(imgUrl(pic,w))+
    '" data-fb="'+esc(imgUrl(pic,null))+'" data-fb2="'+esc(imgProxyUrl(pic,null))+'" alt="'+esc(alt||'')+
    '" onerror="imgFallback(this)" '+(extraAttrs||'')+'>';
}
function imgFallback(el){
  if(!el.getAttribute('data-fb-used')){
    el.setAttribute('data-fb-used','1');
    const fb = el.getAttribute('data-fb');
    if(fb && el.src !== fb){ el.src = fb; return; }
  }
  if(!el.getAttribute('data-fb2-used')){
    el.setAttribute('data-fb2-used','1');
    const fb2 = el.getAttribute('data-fb2');
    if(fb2){ el.src = fb2; return; }
  }
  el.style.visibility = 'hidden';
}

function fmtTime(ts, withTime){
  if(!ts) return '未采集';
  const d = new Date(ts*1000);
  const opt = withTime===false
    ? {month:'numeric',day:'numeric'}
    : {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false};
  return d.toLocaleString('zh-CN', opt);
}
function fmtDay(ts){
  if(!ts) return '';
  return new Date(ts*1000).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});
}
function kindText(k){
  return {OUT:'断货',IN:'补货',LOW:'低库存',TRANSIT:'在途',
          PRICE_DOWN:'降价',PRICE_UP:'涨价',TARGET_HIT:'达标',GONE:'下架'}[k]||k;
}

/* 价格走势 SVG
   opts.promos: [{begin_ts,end_ts}]（毫秒，来自官网 timeLimited*）→ 画限时特优时段底色
   opts.days:   只画最近 N 天（0=全部）
   X 轴按真实时间比例（不是等距序号），所以特优时段能与价格点对齐 */
function priceChartHtml(d, opts){
  opts = opts || {};
  const all = (d && d.points) || [];
  const days = opts.days || 0;
  const box = '<div style="font-size:12px;color:var(--muted);margin:2px 0 8px">价格走势' +
    '<span style="margin-left:6px">· 仅记录变化点' +
    (days ? (' · 最近 ' + days + ' 天') : '') + '</span></div>';
  if(!all.length) return box+'<div style="font-size:12px;color:var(--muted)">暂无价格记录，扫描入库后出现</div>';
  const tEnd = all[all.length-1].ts;
  const pts = days ? all.filter(p => p.ts >= tEnd - days*86400) : all;
  if(!pts.length) return box+'<div style="font-size:12px;color:var(--muted)">该时间段内没有价格记录</div>';

  const W=720, H=96, P=18, TOP=14;
  const prices = pts.map(p=>p.price);
  const min = Math.min.apply(null,prices), max = Math.max.apply(null,prices);
  const span = (max-min)||1;
  const t0 = pts[0].ts, t1 = pts[pts.length-1].ts;
  const X = t => (t1===t0) ? W/2 : P+(W-2*P)*(t-t0)/(t1-t0);
  const Y = p => TOP+(H-2*TOP)*(1-(p-min)/span);
  const f = n => n.toFixed(1);

  // 限时特优时段（毫秒 → 秒）
  let bands = '';
  (opts.promos||[]).forEach((w,i) => {
    const b = Math.round((w.begin_ts||0)/1000), e = Math.round((w.end_ts||0)/1000) || t1;
    if(!b || e < t0 || b > t1) return;
    const x1 = X(Math.max(b, t0)), x2 = X(Math.min(e, t1));
    bands += '<rect x="'+f(x1)+'" y="'+TOP+'" width="'+f(Math.max(1,x2-x1))+'" height="'+(H-2*TOP)+
      '" fill="#d0021b" opacity="0.07"/>';
    if(i===0 && (x2-x1) > 60){
      bands += '<text x="'+f((x1+x2)/2)+'" y="'+(TOP+10)+'" text-anchor="middle" font-size="9" '+
        'fill="#d0021b" opacity="0.8">限时特优</text>';
    }
  });

  let path='', dots='';
  pts.forEach((p,i)=>{
    const x=X(p.ts), y=Y(p.price);
    path += (i?' L':'M')+f(x)+' '+f(y);
    dots += '<circle cx="'+f(x)+'" cy="'+f(y)+'" r="2.5" fill="#888780"/>';
  });

  // 月度低点
  const months = {};
  pts.forEach(p => { const d2 = new Date(p.ts*1000);
    const k = d2.getFullYear() + '-' + (d2.getMonth()+1);
    if(!months[k] || p.price < months[k].price) months[k] = p; });
  let monthMarks = '';
  Object.keys(months).slice(-6).forEach(k => {
    const p = months[k];
    const x = X(p.ts);
    monthMarks += '<circle cx="'+f(x)+'" cy="'+f(Y(p.price))+'" r="3.4" fill="#fff" '+
      'stroke="#1a7f37" stroke-width="1.2"/>';
    if(x > P + 34){    // 太靠左会给「区间最低」标签让位
      monthMarks += '<text x="'+f(x)+'" y="'+f(Y(p.price)-7)+'" text-anchor="middle" font-size="9" '+
        'fill="#1a7f37">'+(Number(k.split('-')[1]))+'月 ¥'+p.price+'</text>';
    }
  });

  const last = pts[pts.length-1];
  const lowPt = pts.reduce((a,b)=>b.price<a.price?b:a, pts[0]);
  const svg = '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;display:block">'+
    bands+
    '<line x1="'+P+'" y1="'+H+'" x2="'+P+'" y2="6" stroke="#e8e8e8" stroke-width="0.5"/>'+
    '<line x1="'+P+'" y1="'+H+'" x2="'+(W-P)+'" y2="'+H+'" stroke="#e8e8e8" stroke-width="0.5"/>'+
    '<path d="'+path+'" fill="none" stroke="#888780" stroke-width="1.2"/>'+
    dots+monthMarks+
    '<circle cx="'+f(X(lowPt.ts))+'" cy="'+f(Y(lowPt.price))+'" r="3.5" fill="#1a7f37"/>'+
    '<circle cx="'+f(X(last.ts))+'" cy="'+f(Y(last.price))+'" r="3.5" fill="#d0021b"/>'+
    '<text x="'+P+'" y="'+H+'" text-anchor="start" dy="-3" font-size="11" fill="#888780">'+
      fmtTime(pts[0].ts)+'</text>'+
    '<text x="'+(W-P)+'" y="'+H+'" text-anchor="end" dy="-3" font-size="11" fill="#888780">'+
      fmtTime(last.ts)+'</text>'+
    '<text x="'+P+'" y="11" font-size="11" fill="#1a7f37">区间最低 ¥'+lowPt.price+'</text>'+
    '<text x="'+(W-P)+'" y="11" text-anchor="end" font-size="11" fill="#444441">当前 ¥'+last.price+'</text>'+
  '</svg>'+
  '<div class="hint" style="margin-top:4px">'+
    '绿圈 = 月度低点；<span style="color:#d0021b">红色区域</span> = 限时特优时段（从开始记录起积累）'+
  '</div>';
  return box+svg;
}

/* ---------- 灯箱 ---------- */
let gallery = [], gIdx = 0, gScale = 1, gX = 0, gY = 0, gDrag = null;

function collectGallery(){
  gallery = [...document.querySelectorAll('img.thumb, img.thumb-sm, img.chip, img.shot')]
    .filter(i=>i.style.display!=='none' && i.src)
    .map(i=>i.src);
}
function applyZoom(){ $('lbi').style.transform = 'translate('+gX+'px,'+gY+'px) scale('+gScale+')'; }
function updateCount(){ $('lbcount').textContent = gallery.length ? (gIdx+1)+' / '+gallery.length : ''; }
function resetView(){ gScale = 1; gX = 0; gY = 0; applyZoom(); }
function viewImg(src){
  collectGallery();
  gIdx = Math.max(0, gallery.indexOf(src));
  resetView();
  $('lbi').src = src;
  $('lb').style.display = 'flex';
  updateCount();
}
function lbNav(d){
  if(!gallery.length) return;
  gIdx = (gIdx + d + gallery.length) % gallery.length;
  resetView();
  $('lbi').src = gallery[gIdx];
  updateCount();
}
function toggleZoom(e){
  e.stopPropagation();
  gScale = gScale > 1.5 ? 1 : 2;
  if(gScale===1){ gX=0; gY=0; }
  applyZoom();
}
function hideImg(){
  $('lb').style.display = 'none';
  $('lbi').src = '';
  gallery = []; gDrag = null;
}
document.addEventListener('keydown', e=>{
  const lb = $('lb');
  if(lb && lb.style.display==='flex'){
    if(e.key==='Escape') hideImg();
    else if(e.key==='ArrowRight') lbNav(1);
    else if(e.key==='ArrowLeft') lbNav(-1);
    return;
  }
  if(e.key==='Escape' && typeof closeDrawer==='function') closeDrawer();
});
document.addEventListener('wheel', e=>{
  if($('lb').style.display!=='flex') return;
  e.preventDefault();
  gScale = Math.min(4, Math.max(0.6, gScale + (e.deltaY<0?0.2:-0.2)));
  applyZoom();
}, {passive:false});
document.addEventListener('mousedown', e=>{
  if($('lb').style.display!=='flex') return;
  if(e.target.id!=='lbi') return;
  e.preventDefault();
  gDrag = {x:e.clientX, y:e.clientY, ox:gX, oy:gY};
  $('lbi').style.cursor = 'grabbing';
});
document.addEventListener('mousemove', e=>{
  if(!gDrag) return;
  gX = gDrag.ox + (e.clientX - gDrag.x);
  gY = gDrag.oy + (e.clientY - gDrag.y);
  applyZoom();
});
document.addEventListener('mouseup', ()=>{
  if(gDrag){ gDrag = null; $('lbi').style.cursor = 'grab'; }
});

/* ---------- 页签 ---------- */
function switchTab(t){
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on', x.dataset.tab===t));
  ['shop','monitor','changes','events','settings'].forEach(x=>{
    const el = $('tab-'+x); if(el) el.style.display = (x===t?'':'none');
  });
  if(t==='monitor' && typeof loadMonitor==='function') loadMonitor();
  if(t==='changes' && typeof loadChanges==='function') loadChanges();
  if(t==='events' && typeof loadEvents==='function') loadEvents();
  if(t==='settings' && typeof loadSettings==='function') loadSettings();
  if(t==='shop' && typeof loadShop==='function') loadShop(true);
  window.scrollTo({top:0});
}
