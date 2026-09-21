/* 商城：官方分类导航 + 瀑布流 + 无限滚动 + 搜索/排序 */

const shop = {
  cat: 'ALL',          // 当前分类
  catName: '全部商品',
  l1: 'ALL',           // 当前一级分类
  sort: 'overall',
  q: '',
  onlyNew: false,
  page: 1,
  pageSize: 40,
  total: 0,
  loading: false,
  done: false,
  cats: [],            // 分类节点
  counts: {},
  scrollBound: false,
};

const SORTS = [
  ['overall', '官方推荐'], ['newest', '最新上架'], ['new', '新品优先'],
  ['priceAsc', '价格从低到高'], ['priceDesc', '价格从高到低'], ['discount', '折扣力度'],
];

/* ---------- 分类导航 ---------- */
async function loadCategories(){
  try{
    const d = await api('/api/categories');
    shop.cats = d.nodes || [];
    shop.counts = d.counts || {};
  }catch(e){ shop.cats = []; shop.counts = {}; }
  renderCatbar();
}

function _children(code){
  return shop.cats.filter(c => (c.parent_code || '') === (code === 'ALL' ? '' : code));
}

function renderCatbar(){
  const roots = shop.cats.filter(c => !c.parent_code);
  const bar = $('catbar');
  if(!roots.length){
    bar.innerHTML = '<div class="note">分类树尚未抓取（首次扫描时自动同步官方分类）。可先点右上角「更新商品库」。</div>';
    return;
  }
  const l1 = [ {code:'ALL', name:'全部', count: shop.counts['ALL']||0} ].concat(roots);
  let h = '<div class="catline"><span class="lbl">分类</span>' + l1.map(c =>
      '<button class="chipbtn'+(shop.l1===c.code?' on':'')+'" onclick="pickL1(\''+c.code+'\')">'+
      esc(c.name||c.code)+'<span class="n">'+(c.count||shop.counts[c.code]||0)+'</span></button>').join('') +
    '</div>';
  const subs = (shop.l1!=='ALL') ? _children(shop.l1) : [];
  if(subs.length){
    h += '<div class="catline"><span class="lbl">系列</span></div><div class="catsub">' +
      [{code:shop.l1, name:'全部'}].concat(subs).map(c =>
        '<button class="chipbtn'+(shop.cat===c.code?' on':'')+'" onclick="pickL2(\''+c.code+'\',\''+
        esc(c.name||'').replace(/'/g,'')+'\')">'+esc(c.name||c.code)+
        '<span class="n">'+(c.count||shop.counts[c.code]||0)+'</span></button>').join('') + '</div>';
    const subs2 = _children(shop.cat);
    if(subs2.length){
      h += '<div class="catsub" style="margin-top:7px">' + subs2.map(c =>
        '<button class="chipbtn" onclick="pickL2(\''+c.code+'\',\''+esc(c.name||'').replace(/'/g,'')+
        '\')">'+esc(c.name||c.code)+'<span class="n">'+(c.count||shop.counts[c.code]||0)+
        '</span></button>').join('') + '</div>';
    }
  }
  bar.innerHTML = h;
}

function pickL1(code){
  shop.l1 = code;
  shop.cat = (code === 'ALL') ? 'ALL' : code;
  shop.catName = (code === 'ALL') ? '全部商品'
    : (shop.cats.find(c=>c.code===code)||{}).name || code;
  renderCatbar(); loadShop(true);
}
function pickL2(code, name){
  shop.cat = code;
  shop.catName = name || (shop.cats.find(c=>c.code===code)||{}).name || code;
  renderCatbar(); loadShop(true);
}

/* ---------- 工具条 ---------- */
function renderToolbar(){
  const tb = $('toolbar');
  tb.innerHTML =
    '<span>'+esc(shop.catName)+'</span>' +
    '<span class="count-chip">'+shop.total+' 件</span>' +
    '<span class="spacer"></span>' +
    '<label style="min-width:0">排序</label>' +
    '<select id="sortSel" onchange="shop.sort=this.value;loadShop(true)">' +
      SORTS.map(s=>'<option value="'+s[0]+'"'+(shop.sort===s[0]?' selected':'')+'>'+s[1]+'</option>').join('') +
    '</select>' +
    '<button class="chipbtn'+(shop.onlyNew?' on':'')+'" onclick="toggleOnlyNew()">只看新品</button>' +
    (shop.q ? '<span class="count-chip">搜索：'+esc(shop.q)+'</span>' : '');
}

function toggleOnlyNew(){
  shop.onlyNew = !shop.onlyNew;
  if(shop.onlyNew && shop.sort !== 'new' && shop.sort !== 'newest'){ shop.sort = 'new'; }
  loadShop(true);
}

/* ---------- 入口 ---------- */
async function loadShop(reset){
  if(reset){ shop.page = 1; shop.done = false; shop.total = 0;
    $('grid').innerHTML = skeleton(8); }
  if(shop.loading || shop.done) return;
  shop.loading = true;
  if(shop.onlyNew){
    await loadNewGrid();
    shop.loading = false;
    return;
  }
  try{
    const p = new URLSearchParams({page: shop.page, page_size: shop.pageSize,
      sort: shop.sort, category: shop.cat === 'ALL' ? '' : shop.cat, q: shop.q});
    const d = await api('/api/catalog?' + p.toString());
    shop.total = d.total;
    renderToolbar();
    const html = (d.items||[]).map(tileHtml).join('');
    if(shop.page === 1) $('grid').innerHTML = html;
    else $('grid').insertAdjacentHTML('beforeend', html);
    shop.page += 1;
    if(!d.items || !d.items.length || (shop.page-1)*shop.pageSize >= d.total){
      shop.done = true;
      if(!d.total) $('grid').innerHTML =
        '<div class="empty">暂无商品。点右上角「更新商品库」开始抓取官方商品（约 1–2 分钟）。</div>';
    }
  }catch(e){
    $('grid').innerHTML = '<div class="empty">加载失败：'+esc(e.message)+'</div>';
  }
  shop.loading = false;
  bindScroll();
}

async function loadNewGrid(){
  try{
    const d = await api('/api/new-arrivals?days=60&limit=120');
    shop.total = (d.items||[]).length;
    renderToolbar();
    $('grid').innerHTML = shop.total
      ? d.items.map(tileHtml).join('')
      : '<div class="empty">还没有新品数据。点「更新商品库」抓取官方「新作商品」榜。</div>';
    shop.done = true;
  }catch(e){ $('grid').innerHTML = '<div class="empty">加载失败：'+esc(e.message)+'</div>'; }
}

/* 无限滚动 */
function bindScroll(){
  if(shop.scrollBound) return;
  shop.scrollBound = true;
  const sent = $('sentinel');
  if(!window.IntersectionObserver){ return; }
  new IntersectionObserver(entries=>{
    if(entries[0].isIntersecting && !$('tab-shop').style.display) loadShop(false);
  }, {rootMargin:'600px'}).observe(sent);
}

/* ---------- 卡片 ---------- */
function tagsHtml(it){
  const t = [];
  if(it.is_new) t.push('<span class="tag new">新品</span>');
  const idn = it.identity || [];
  if(idn.indexOf('time_doptimal') >= 0) t.push('<span class="tag hot">限时特优</span>');
  if(idn.indexOf('concessional_rate') >= 0) t.push('<span class="tag">超值精选</span>');
  if(idn.indexOf('revision') >= 0) t.push('<span class="tag">可改裤长</span>');
  if(it.subscribed) t.push('<span class="tag sub">已关注</span>');
  return t.join('');
}

function tileHtml(it){
  const disc = it.discount_pct > 0
    ? '<span class="d">-'+it.discount_pct+'%</span>' : '';
  const origin = (it.origin_price && it.origin_price > it.cur_price)
    ? '<span class="o">¥'+it.origin_price+'</span>' : '';
  const pic = it.main_pic
    ? imgTag(it.main_pic, null, 'pic', it.name)
    : '<div class="pic"></div>';
  return '<div class="tile" data-code="'+it.code+'" onclick="openDetail(\''+it.code+'\')">'+
      pic +
      '<button class="subbtn'+(it.subscribed?' on':'')+'" onclick="event.stopPropagation();'+
        'quickSubscribe(\''+it.code+'\',this)">'+(it.subscribed?'已关注':'关注降价')+'</button>'+
      '<div class="nm">'+esc(it.name||it.code)+'</div>'+
      '<div class="pr"><span class="p">¥'+(it.cur_price??'-')+'</span>'+origin+disc+'</div>'+
      '<div class="tags">'+tagsHtml(it)+'</div>'+
    '</div>';
}

function skeleton(n){
  let h = '';
  for(let i=0;i<n;i++){
    const w = 150 + Math.round(Math.random()*70);
    h += '<div class="tile"><div class="skel" style="height:'+w*1.33+'px"></div>'+
      '<div class="skel" style="height:14px;margin:9px 0 6px"></div>'+
      '<div class="skel" style="height:14px;width:40%"></div></div>';
  }
  return h;
}

/* ---------- 顶栏动作 ---------- */
function doSearch(){
  const q = $('searchInput').value.trim();
  shop.q = q; loadShop(true);
}
function clearSearch(){
  $('searchInput').value=''; shop.q=''; loadShop(true);
}

async function quickSubscribe(code, btn){
  const on = btn.classList.contains('on');
  btn.disabled = true;
  try{
    if(on){
      await api('/api/subscribe/'+code, {method:'DELETE'});
      btn.classList.remove('on'); btn.textContent='关注降价';
      show('已取消关注 '+code);
    }else{
      show('<span class="spin"></span> 正在订阅并采集库存…');
      const r = await postJson('/api/subscribe', {code});
      btn.classList.add('on'); btn.textContent='已关注';
      show('已关注 '+code+'：降价/补货将推送'+(r.collect && r.collect.ok ? '（SKU '+(r.collect.skus||0)+'）' : ''));
      if(typeof loadMonitor === 'function' && $('tab-monitor').style.display !== 'none') loadMonitor();
    }
  }catch(e){ show('操作失败：'+esc(e.message), true); }
  btn.disabled = false;
  refreshBadges();
}

/* 关注数角标 */
async function refreshBadges(){
  try{
    const w = await api('/api/watches');
    const el = $('badge-monitor');
    if(el) el.textContent = w.length ? String(w.length) : '';
  }catch(e){}
}
