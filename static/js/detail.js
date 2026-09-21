/* 商品详情抽屉：图集 / 价格历史 / 变价记录 / 色卡尺码 / 订阅 */

const detail = {code:null, data:null, color:null, chartDays: 0};

function chartRange(days){
  detail.chartDays = days;
  document.querySelectorAll('#dbody .sect h3 .fchip[data-days]').forEach(b => {
    b.classList.toggle('on', b.getAttribute('data-days') === String(days));
  });
  renderChartBox();
}
function renderChartBox(){
  const box = $('chartBox'); if(!box || !detail.data) return;
  const d = detail.data;
  box.innerHTML = priceChartHtml({points: d.price_series}, 
    {days: detail.chartDays, promos: d.promo_windows || []});
}

const BADGE_TEXT = {
  time_doptimal: ['限时特优', 'hot'], concessional_rate: ['超值精选', ''],
  new_product: ['新作商品', 'new'], revision: ['可修改裤长', ''],
  pickUp: ['支持门店自提', ''],
};

function openDrawer(){
  $('mask').classList.add('on');
  $('drawer').classList.add('on');
  document.body.style.overflow = 'hidden';
}
function closeDrawer(){
  $('mask').classList.remove('on');
  $('drawer').classList.remove('on');
  document.body.style.overflow = '';
  detail.code = null; detail.data = null;
}

async function openDetail(code){
  detail.code = code; detail.color = null;
  $('dbody').innerHTML = '<div class="empty"><span class="spin"></span> 加载中…</div>';
  openDrawer();
  try{
    const d = await api('/api/catalog/'+code);
    detail.data = d;
    renderDetail();
  }catch(e){
    $('dbody').innerHTML = '<div class="empty">加载失败：'+esc(e.message)+'</div>';
  }
}

function renderDetail(){
  const d = detail.data, p = d.product;
  const gone = !!p.gone_ts;
  const goneBadge = gone
    ? '<span class="tag" style="color:#fff;background:#8a8a8a;border-color:#8a8a8a">已下架</span>'
    : '';
  const disc = (p.origin_price && p.cur_price && p.cur_price < p.origin_price)
    ? Math.round((p.origin_price - p.cur_price)/p.origin_price*100) : 0;
  const badges = (p.identity||[]).map(k=>{
    const b = BADGE_TEXT[k]; if(!b) return '';
    return '<span class="tag '+(b[1]||'')+'">'+esc(b[0])+'</span>';
  }).join('') + (p.rank_new != null ? '<span class="tag new">新品</span>' : '') +
    goneBadge +
    (d.subscribed ? '<span class="tag sub">已关注</span>' : '');

  const colors = (d.colors||[]).filter(c=>c.pic);
  const colorHtml = colors.length ? '<div class="colors">' + colors.map(c=>
      '<div class="c'+(detail.color===c.no?' on':'')+'" onclick="pickColor(\''+c.no+'\')">'+
        imgTag(c.chip||c.pic, null, '', c.name||c.no) +
        '<span>'+esc(c.name||c.no)+'</span></div>').join('') + '</div>' : '';

  const sizes = p.min_size ? '<div class="sizes"><span class="s">'+
    esc(p.min_size)+(p.max_size && p.max_size!==p.min_size ? ' – '+esc(p.max_size) : '')+
    '</span></div>' : '';

  const big = p.main_pic ? imgUrl(p.main_pic, 1000) : '';

  $('dbody').innerHTML =
    '<div class="dmain">'+
      '<div class="big">'+(big ? '<img class="shot" src="'+esc(big)+'" alt="" '+
        'data-fb="'+esc(imgUrl(p.main_pic,null))+'" data-fb2="'+esc(imgProxyUrl(p.main_pic,null))+
        '" onerror="imgFallback(this)" onclick="viewImg(this.src)">' : '')+'</div>'+
      '<div class="dinfo">'+
        '<h2 class="dname">'+esc(p.name||p.code)+'</h2>'+
        '<div class="dsub">编号 '+esc(p.code)+
          (p.gender?' · '+esc(p.gender):'')+(p.season?' · '+esc(p.season):'')+'</div>'+
        '<div class="dprice"><span class="p">¥'+(p.cur_price??'-')+'</span>'+
          (p.origin_price&&p.origin_price>p.cur_price
            ? '<span class="o">¥'+p.origin_price+'</span><span class="d">-'+disc+'%</span>' : '')+
        '</div>'+
        '<div class="dsub">'+(p.hist_low_price!=null?'历史最低 ¥'+p.hist_low_price+' · ':'')+
          (p.sales!=null?'销量 '+(p.sales>10000?(p.sales/10000).toFixed(1)+'万':p.sales):'')+
          (p.evaluation_count!=null?' · 评价 '+p.evaluation_count:'')+'</div>'+
        '<div class="dbadges">'+badges+'</div>'+
        '<div class="dsub" style="'+(gone?'color:var(--accent)':'')+'">'+
          (gone
            ? '该商品已被判定下架（最后在售 '+fmtTime(p.last_seen_ts,false)+'），'
              + '不再自动采集；重新上架后自动恢复'
            : (p.last_seen_ts?('官方在售（'+fmtTime(p.last_seen_ts,false)+' 抓取）'):''))+'</div>'+
      '</div>'+
    '</div>'+
    '<div class="dactions">'+
      '<button class="primary" id="subBtn" onclick="toggleSubscribe()"'+
        ((gone && !d.subscribed) ? ' disabled' : '')+'>'+
        (d.subscribed?'取消关注':'关注降价')+'</button>'+
      '<button onclick="pullSizes()"'+(gone?' disabled':'')+'>拉取尺码库存</button>'+
      '<a class="link" style="align-self:center" href="https://h.uniqlo.cn/product?pid='+esc(p.product_code)+
        '" target="_blank" rel="noopener">官网页面 ↗</a>'+
    '</div>'+
    '<div class="dactions" style="margin-top:0">'+
      '<span style="align-self:center;font-size:12.5px;color:var(--muted)">预期价 ¥</span>'+
      '<input type="text" class="tgin" id="dTarget" placeholder="未设置" '+
        'value="'+(p.target_price!=null?p.target_price:'')+'"'+(gone?' disabled':'')+
        ' onkeydown="if(event.key===\'Enter\')saveDetailTarget()">'+
      '<button onclick="saveDetailTarget()"'+(gone?' disabled':'')+'>保存</button>'+
      (p.target_price!=null ? '<button onclick="clearDetailTarget()"'+(gone?' disabled':'')+'>清除</button>' : '')+
      '<span class="hint" style="align-self:center;margin-top:0">'+
        (p.target_price!=null
          ? '现价 ≤ 预期价时推送；价格回升越过预期会自动复位'
          : '留空保存=清除；未设置时「降价即推送」')+
        (d.subscribed ? '' : '（未关注：设了也不会推送）')+
      '</span>'+
    '</div>'+
    '<div class="sect"><h3>历史价格'+
      '<span class="hint">共 '+((d.price_series||[]).length)+' 个变化点</span>'+
      '<span class="spacer" style="flex:1"></span>'+
      [0,90,30].map(v=>'<button class="fchip'+(detail.chartDays===v?' on':'')+
        '" data-days="'+v+'" onclick="chartRange('+v+')">'+(v===0?'全部':(v+'天'))+'</button>').join('')+
      '</h3>'+
      '<div id="chartBox"></div>'+
      priceChangesList(d.price_series, detail.chartDays)+
    '</div>'+
    '<div class="sect"><h3>颜色 / 尺码</h3>'+colorHtml+sizes+
      '<div class="hint">颜色名称与色卡来自官网；尺码为官网在售区间。'+
      '点「拉取尺码库存」可获得各颜色×尺码的实时可售件数。</div>'+
    '</div>'+
    '<div class="sect" id="matrixSect" style="display:none"><h3>尺码库存</h3>'+
      '<div id="matrixBox"></div></div>';

  renderChartBox();
  if(d.has_stock_detail) renderMatrix(p.code);
}

function priceChangesList(pts, days){
  if(!pts || pts.length < 2) return '';
  if(days){ const since = pts[pts.length-1].ts - days*86400; pts = pts.filter(p=>p.ts>=since); }
  const rows = [];
  for(let i=pts.length-1;i>0 && rows.length<8;i--){
    const a = pts[i-1], b = pts[i];
    const diff = b.price - a.price;
    const cls = diff < 0 ? 'delta-down' : 'delta-up';
    rows.push('<div class="ev"><span class="k k-'+(diff<0?'PRICE_DOWN':'PRICE_UP')+'">'+
      (diff<0?'降价':'涨价')+'</span>'+
      '<span>¥'+a.price+' → <b>¥'+b.price+'</b></span>'+
      '<span class="'+cls+'">'+(diff<0?'':'+')+diff.toFixed(0)+'</span>'+
      '<span class="spacer"></span><span class="t">'+fmtTime(b.ts)+'</span></div>');
  }
  return rows.length ? '<div style="margin-top:6px">'+rows.join('')+'</div>' : '';
}

function pickColor(no){
  detail.color = (detail.color === no) ? null : no;
  const c = (detail.data.colors||[]).find(x=>x.no===detail.color);
  if(c && c.pic){
    const im = document.querySelector('#dbody .big img');
    if(im){
      im.removeAttribute('data-fb-used');
      im.removeAttribute('data-fb2-used');
      im.setAttribute('data-fb', imgUrl(c.pic, null));
      im.setAttribute('data-fb2', imgProxyUrl(c.pic, null));
      im.src = imgUrl(c.pic, 1000);
    }
    document.querySelectorAll('#dbody .colors .c').forEach(el=>el.classList.remove('on'));
    const idx = (detail.data.colors||[]).findIndex(x=>x.no===no);
    const els = document.querySelectorAll('#dbody .colors .c');
    if(els[idx]) els[idx].classList.add('on');
  }
}

async function toggleSubscribe(){
  const code = detail.code; if(!code) return;
  const btn = $('subBtn'); btn.disabled = true;
  try{
    if(detail.data.subscribed){
      await api('/api/subscribe/'+code, {method:'DELETE'});
      show('已取消关注 '+code);
    }else{
      show('<span class="spin"></span> 正在订阅并采集库存…');
      const r = await postJson('/api/subscribe', {code});
      show('已关注 '+code+'（SKU '+(r.collect&&r.collect.skus||0)+'）');
    }
    detail.data = await api('/api/catalog/'+code);
    renderDetail();
    refreshBadges();
    if($('tab-monitor').style.display !== 'none') loadMonitor();
  }catch(e){ show('操作失败：'+esc(e.message), true); }
  btn.disabled = false;
}

/* 预期价：页内输入（不用 window.prompt —— 系统对话框在预览/iframe 环境不可靠） */
async function saveDetailTarget(){
  const el = $('dTarget'); if(!el) return;
  const v = el.value.trim();
  if(v && !(Number(v) > 0)) return show('预期价需为正数', true);
  try{
    await postJson('/api/products/'+detail.code+'/target',
                   {target_price: v ? Number(v) : null});
    show(v ? ('已设置预期价 ¥'+v+'（跌到该价才推送达标通知）')
           : '已清除预期价，恢复为「降价即推送」');
    detail.data = await api('/api/catalog/'+detail.code);
    renderDetail();
  }catch(e){ show('保存失败：'+esc(e.message), true); }
}
function clearDetailTarget(){
  const el = $('dTarget'); if(el){ el.value=''; saveDetailTarget(); }
}

const pullCd = {};
async function pullSizes(){
  const code = detail.code; if(!code) return;
  const left = Math.ceil(((pullCd[code]||0)-Date.now())/1000);
  if(left > 0) return show('拉取冷却中，'+left+' 秒后再试', true);
  pullCd[code] = Date.now() + 30000;
  $('matrixSect').style.display = '';
  $('matrixBox').innerHTML = '<div class="empty"><span class="spin"></span> 正在拉取官网尺码库存…</div>';
  try{
    await postJson('/api/catalog/'+code+'/pull-sizes', {});
    await renderMatrix(code);
  }catch(e){
    $('matrixBox').innerHTML = '<div style="color:#8a2b2b;font-size:12.5px">'+esc(e.message)+'</div>';
  }
}

/* 颜色 × 尺码 矩阵（复用监控口径；单元格点击 = 订阅/取消该色码） */
async function renderMatrix(code, boxEl){
  const box = boxEl || $('matrixBox'); if(!box) return;
  box.innerHTML = '<span class="spin"></span>';
  try{
    const [d, w] = await Promise.all([api('/api/products/'+code+'/matrix'), api('/api/watches')]);
    if(!d.rows.length){ box.innerHTML =
      '<div class="hint">暂无尺码数据，点「拉取尺码库存」获取。</div>'; return; }
    const pc = d.product.product_code;
    const idx = {};
    d.rows.forEach(r=>{ idx[r.display_color+'|'+r.size] = r; });
    let h = '<table><tr><th>颜色 \\ 尺码</th>' + d.sizes.map(s=>'<th>'+esc(s)+'</th>').join('') + '</tr>';
    d.colors.forEach(c=>{
      const fr = d.rows.find(r=>r.display_color===c);
      const cp = fr && d.color_pic_map ? d.color_pic_map[fr.color_no] : '';
      const chip = cp ? imgTag(cp, null, 'chip', c,
        'onclick="event.stopPropagation();viewImg(this.src)"') : '';
      h += '<tr><th class="cname">'+chip+'<span>'+esc(c)+'</span></th>';
      d.sizes.forEach(s=>{
        const r = idx[c+'|'+s];
        if(!r){ h += '<td class="q-out">-</td>'; return; }
        const q = r.express;
        let cls = 'q-out', txt = q==null?'-':q;
        if(q>0 && q<=2) cls='q-low'; else if(q>2) cls='q-in';
        const watched = w.some(x=>x.product_code===pc && (!x.size||x.size===s) && (!x.color||x.color===c));
        h += '<td class="cell '+cls+(watched?' watched':'')+'" title="快递 '+(q??'-')+
             ' | 前置仓 '+(r.bpl??'-')+' | 大仓 '+(r.dc??'-')+' | 在途 '+(r.transit??'-')+
             '\n点击订阅/取消 '+esc(c)+' '+esc(s)+'" onclick="toggleWatch(\''+pc+'\',\''+
             esc(c).replace(/'/g,'')+'\',\''+esc(s)+'\')">'+txt+'</td>';
      });
      h += '</tr>';
    });
    h += '</table><div class="hint">数字=快递可售件数（封顶20）。灰=断货，橙=≤2件紧张，红框=已订阅（断货/补货推送），点击单元格订阅/取消。</div>';
    box.innerHTML = h;
  }catch(e){ box.innerHTML = '<span style="color:#8a2b2b">'+esc(e.message)+'</span>'; }
}

async function toggleWatch(pc, color, size){
  try{
    const w = await api('/api/watches');
    const exist = w.find(x=>x.product_code===pc && x.size===size && x.color===color);
    if(exist) await api('/api/watches/'+exist.id, {method:'DELETE'});
    else await postJson('/api/watches', {product_code:pc, size, color});
    if($('matrixBox') && $('matrixBox').closest('.sect').style.display !== 'none'){
      await renderMatrix(detail.code);
    }
    monitorExpanded.forEach(c => { const el = $('m_'+c); if(el) renderMatrix(c, el); });
    if($('watches') && $('tab-monitor').style.display !== 'none') loadWatches();
    refreshBadges();
  }catch(e){ show('订阅失败：'+esc(e.message), true); }
}
