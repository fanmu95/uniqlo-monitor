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

/* 价格走势 SVG（仅价格变化点） */
function priceChartHtml(d, opts){
  opts = opts || {};
  const pts = (d && d.points) || [];
  const box = '<div style="font-size:12px;color:var(--muted);margin:2px 0 8px">价格走势（仅记录变化点）</div>';
  if(!pts.length) return box+'<div style="font-size:12px;color:var(--muted)">暂无价格记录，扫描入库后出现</div>';
  const W=720,H=88,P=16;
  const prices = pts.map(p=>p.price);
  const min = Math.min.apply(null,prices), max = Math.max.apply(null,prices);
  const span = (max-min)||1;
  const X = i => pts.length===1 ? W/2 : P+(W-2*P)*i/(pts.length-1);
  const Y = p => P+(H-2*P)*(1-(p-min)/span);
  let path='', dots='';
  pts.forEach((p,i)=>{
    const x=X(i), y=Y(p.price);
    path += (i?' L':'M')+x.toFixed(1)+' '+y.toFixed(1);
    dots += '<circle cx="'+x.toFixed(1)+'" cy="'+y.toFixed(1)+'" r="2.5" fill="#888780"/>';
  });
  const last = pts[pts.length-1];
  const lowPt = pts.reduce((a,b)=>b.price<a.price?b:a, pts[0]);
  const svg = '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;display:block">'+
    '<line x1="'+P+'" y1="'+H+'" x2="'+P+'" y2="6" stroke="#e8e8e8" stroke-width="0.5"/>'+
    '<line x1="'+P+'" y1="'+H+'" x2="'+(W-P)+'" y2="'+H+'" stroke="#e8e8e8" stroke-width="0.5"/>'+
    '<path d="'+path+'" fill="none" stroke="#888780" stroke-width="1.2"/>'+
    dots+
    '<circle cx="'+X(pts.indexOf(lowPt)).toFixed(1)+'" cy="'+Y(lowPt.price).toFixed(1)+'" r="3.5" fill="#1a7f37"/>'+
    '<circle cx="'+X(pts.length-1).toFixed(1)+'" cy="'+Y(last.price).toFixed(1)+'" r="3.5" fill="#d0021b"/>'+
    '<text x="'+P+'" y="'+H+'" text-anchor="start" dy="-3" font-size="11" fill="#888780">'+fmtTime(pts[0].ts)+'</text>'+
    '<text x="'+(W-P)+'" y="'+H+'" text-anchor="end" dy="-3" font-size="11" fill="#888780">'+fmtTime(last.ts)+'</text>'+
    '<text x="'+P+'" y="12" font-size="11" fill="#1a7f37">史低 ¥'+lowPt.price+'</text>'+
    '<text x="'+(W-P)+'" y="12" text-anchor="end" font-size="11" fill="#444441">当前 ¥'+last.price+'</text>'+
    (min<max?'<text x="'+(W-P)+'" y="'+(Y(max)+8).toFixed(1)+'" text-anchor="end" font-size="11" fill="#888780">¥'+max+'</text>':'')+
  '</svg>';
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
