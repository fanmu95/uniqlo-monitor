/* 商城：官方分类导航 + 瀑布流 + 无限滚动 + 搜索/排序 */

const shop = {
  path: [],            // 已选分类路径 [{code,name}]：0=全部，1=L1，2=L1+L2 …（官方共 4 级）
  sort: 'overall',
  q: '',
  onlyNew: false,
  onlyDiscount: false,
  onlyStock: false,
  minDiscount: 0,      // 折扣力度下限（%），0=不限
  onlyDoptimal: false,
  page: 1,
  pageSize: 40,
  total: 0,
  loading: false,
  done: false,
  cats: [],            // 分类节点（含 level / parent_code）
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
  return shop.cats.filter(c => (c.parent_code || '') === (code || ''));
}
function _node(code, name){
  return shop.cats.find(c => c.code === code) || {code: code, name: name || code};
}
function _cnt(code){ return shop.counts[code] || 0; }
function catCode(){ return shop.path.length ? shop.path[shop.path.length-1].code : 'ALL'; }
function catName(){
  return shop.path.length ? (shop.path[shop.path.length-1].name || catCode()) : '全部商品';
}

/* L1 选择（'ALL' = 全部商品） */
function pickRoot(code){
  shop.path = (code === 'ALL') ? [] : [_node(code)];
  renderCatbar(); loadShop(true);
}
/* 深层选择：levelIndex 0=L1 1=L2 2=L3 3=L4，路径截断到该层后选中 */
function pickCat(code, levelIndex){
  shop.path = shop.path.slice(0, levelIndex).concat([_node(code)]);
  renderCatbar(); loadShop(true);
}
function goUp(){
  if(!shop.path.length) return;
  shop.path = shop.path.slice(0, -1);
  renderCatbar(); loadShop(true);
}

const LEVEL_LABEL = ['系列', '品类', '细分'];

/* 分类 chip：全部平铺（换行即多行，不做折叠） */
function _chips(nodes, level){
  return nodes.map(k => '<button class="fchip' + (k.sel ? ' on' : '') +
    (_cnt(k.code) ? '' : ' empty') + '" onclick="pickCat(\'' + k.code + '\',' + level +
    ')" title="' + esc(k.name) + '">' + esc(k.name) +
    '<span class="n">' + _cnt(k.code) + '</span></button>').join('');
}

function renderCatbar(){
  const bar = $('catbar');
  const roots = shop.cats.filter(c => !c.parent_code && c.code !== 'ALL');
  if(!roots.length){
    bar.innerHTML = '<div class="note">分类树尚未抓取（首次扫描时自动同步官方分类）。可先点右上角「更新商品库」。</div>';
    return;
  }
  let h = '<div class="catpanel">';

  // 面包屑
  h += '<div class="crumb">' +
    '<span class="cpart' + (shop.path.length ? '' : ' on') + '" onclick="pickRoot(\'ALL\')">全部商品</span>' +
    shop.path.map((n, i) =>
      '<span class="sep">›</span><span class="cpart' + (i === shop.path.length - 1 ? ' on' : '') +
      '" onclick="pickCat(\'' + n.code + '\',' + i + ')">' + esc(n.name) + '</span>').join('') +
    '<span class="spacer"></span>' +
    '<span class="cnum">' + shop.total + ' 件</span>' +
    (shop.path.length ? '<button class="crumb-back" onclick="goUp()" title="返回上一级">↑ 上级</button>' : '') +
  '</div>';

  // 一级
  const l1 = [{code: 'ALL', name: '全部', sel: shop.path.length === 0}].concat(
    roots.map(c => ({code: c.code, name: c.name, sel: !!(shop.path[0] && shop.path[0].code === c.code)})));
  h += '<div class="catrow"><span class="lvl">一级</span><div class="chips">' +
       _chips(l1, 0) + '</div></div>';

  // 二/三/四级：当前层级有子类才出现
  h += '<div class="catrows">';
  for(let lvl = 0; lvl < 3; lvl++){
    const parent = shop.path[lvl];
    if(!parent) break;
    const kids = _children(parent.code);
    if(!kids.length) break;
    const sel = shop.path[lvl + 1];
    const nodes = kids.map(k => ({code: k.code, name: k.name, sel: !!(sel && sel.code === k.code)}));
    h += '<div class="catrow"><span class="lvl">' + LEVEL_LABEL[lvl] + '</span><div class="chips">' +
         _chips(nodes, lvl + 1) + '</div></div>';
  }
  h += '</div></div>';
  bar.innerHTML = h;
  // 让当前选中的 chip 在行内可见（只动该行横向滚动，不滚动整页）
  const on = bar.querySelector('.fchip.on');
  const row = on && on.closest('.chips');
  if(on && row && row.scrollWidth > row.clientWidth){
    const r = on.getBoundingClientRect(), rr = row.getBoundingClientRect();
    if(r.left < rr.left || r.right > rr.right) row.scrollLeft += (r.left - rr.left) - 8;
  }
}

/* ---------- 工具条：筛选开关 + 排序 ---------- */
function toggleFilter(kind){
  if(kind === 'new'){
    shop.onlyNew = !shop.onlyNew;
    if(shop.onlyNew && shop.sort !== 'new' && shop.sort !== 'newest') shop.sort = 'new';
  }
  if(kind === 'discount') shop.onlyDiscount = !shop.onlyDiscount;
  if(kind === 'stock') shop.onlyStock = !shop.onlyStock;   // 接口保留，界面用折扣档位替代
  if(kind === 'doptimal') shop.onlyDoptimal = !shop.onlyDoptimal;
  loadShop(true);
}

function renderToolbar(){
  const tb = $('toolbar');
  const sw = (on, kind, label) =>
    '<button class="switch' + (on ? ' on' : '') + '" onclick="toggleFilter(\'' + kind + '\')">' +
    label + '</button>';
  tb.innerHTML =
    '<span class="tb-lbl">筛选</span>' +
    sw(shop.onlyNew, 'new', '只看新品') +
    sw(shop.onlyDiscount, 'discount', '只看打折') +
    sw(shop.onlyDoptimal, 'doptimal', '限时特优') +
    (shop.q ? '<span class="count-chip">搜索：' + esc(shop.q) + '</span>' : '') +
    '<label style="min-width:0;margin-left:4px">折扣 ≥</label>' +
    '<select id="discSel" onchange="shop.minDiscount=Number(this.value);loadShop(true)">' +
      [0,20,30,50].map(v=>'<option value="'+v+'"'+(shop.minDiscount===v?' selected':'')+'>'+
        (v?('≥'+v+'%'):'不限')+'</option>').join('') +
    '</select>' +
    '<span class="spacer"></span>' +
    '<label style="min-width:0">排序</label>' +
    '<select id="sortSel" onchange="shop.sort=this.value;loadShop(true)">' +
      SORTS.map(s=>'<option value="'+s[0]+'"'+(shop.sort===s[0]?' selected':'')+'>'+s[1]+'</option>').join('') +
    '</select>';
}

/* ---------- 入口 ---------- */
async function loadShop(reset){
  if(reset){ shop.page = 1; shop.done = false; shop.total = 0;
    $('grid').innerHTML = skeleton(8); }
  if(shop.loading || shop.done) return;
  shop.loading = true;
  try{
    const p = new URLSearchParams({page: shop.page, page_size: shop.pageSize,
      sort: shop.sort, category: catCode() === 'ALL' ? '' : catCode(), q: shop.q,
      only_new: shop.onlyNew ? 1 : 0, only_discount: shop.onlyDiscount ? 1 : 0,
      only_stock: shop.onlyStock ? 1 : 0,
      only_doptimal: shop.onlyDoptimal ? 1 : 0,
      min_discount: shop.minDiscount || 0});
    const d = await api('/api/catalog?' + p.toString());
    shop.total = d.total;
    renderToolbar();
    // 面包屑里的件数随结果更新（分类刚切换时先渲染的画面是上一档的数字）
    const chip = document.querySelector('#catbar .crumb .cnum');
    if(chip) chip.textContent = d.total + ' 件';
    const html = (d.items||[]).map(tileHtml).join('');
    if(shop.page === 1) $('grid').innerHTML = html;
    else $('grid').insertAdjacentHTML('beforeend', html);
    shop.page += 1;
    if(!d.items || !d.items.length || (shop.page-1)*shop.pageSize >= d.total){
      shop.done = true;
      if(!d.total){
        const hasFilter = shop.q || shop.onlyNew || shop.onlyDiscount || shop.onlyStock ||
          shop.onlyDoptimal || shop.minDiscount || shop.path.length;
        $('grid').innerHTML = '<div class="empty">' +
          (hasFilter ? '当前筛选条件下没有商品，试试取消部分筛选。'
                     : '暂无商品。点右上角「更新商品库」开始抓取官方商品（约 1–2 分钟）。') +
          '</div>';
      }
    }
  }catch(e){
    $('grid').innerHTML = '<div class="empty">加载失败：'+esc(e.message)+'</div>';
  }
  shop.loading = false;
  bindScroll();
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
