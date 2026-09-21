/* 监控列表（订阅中商品）+ 尺码订阅 + 预期价/刷新/暂停/删除 */

const monitorExpanded = new Set();

async function loadMonitor(){
  $('products').innerHTML = '<div class="empty"><span class="spin"></span> 加载中…</div>';
  try{
    const rows = await api('/api/products');
    if(!rows.length){
      $('products').innerHTML = '<div class="empty">还没有关注商品。到「商城」点卡片右上角「关注降价」，'+
        '或在详情里订阅，即可开始监控价格与尺码库存。</div>';
      loadWatches(); return;
    }
    $('products').innerHTML = rows.map(p=>{
      const down = (p.origin_price && p.cur_price && p.cur_price < p.origin_price)
        ? '<span class="badge">已降 ¥'+(p.origin_price-p.cur_price).toFixed(0)+'</span>' : '';
      const hit = p.target_price
        ? (p.target_hit ? '<span class="badge ok">已达标</span>'
                        : '<span style="font-size:12px;color:var(--muted)">预期 ¥'+p.target_price+'</span>')
        : '';
      const gone = !!p.gone_ts;
      const status = gone
        ? '<span class="status off">已下架</span>'
        : '<span class="status'+(p.enabled?'':' off')+'">'+(p.enabled?'监控中':'已暂停')+'</span>';
      const goneNote = gone
        ? '<div class="meta-row" style="color:var(--muted)">连续多日未在官网出现，已暂停自动采集'+
          '（最后在售 '+fmtTime(p.last_seen_ts, false)+'）。重新上架后自动恢复；'+
          '也可点「刷新」强制核对一次。</div>'
        : '';
      const pic = p.main_pic
        ? imgTag(p.main_pic, null, 'thumb', '', 'onclick="event.stopPropagation();viewImg(this.src)"')
        : '<div class="thumb"></div>';
      return '<div class="card">'+
        '<div class="card-top">'+ pic +
          '<div class="card-main">'+
            '<div class="card-head" onclick="toggleMatrix(\''+p.code+'\')">'+
              '<span class="card-name">'+esc(p.name||p.code)+'</span>'+
              '<span class="card-code">'+p.code+'</span>'+
              '<span class="spacer" style="flex:1"></span>'+
              status+
            '</div>'+
            goneNote+
            '<div class="price-row">'+
              '<span class="price">¥'+(p.cur_price??'-')+'</span>'+
              (p.origin_price&&p.origin_price!=p.cur_price
                ? '<span class="origin">¥'+p.origin_price+'</span>' : '')+
              down+hit+
              '<span class="meta-right">'+
                (p.hist_low_price!=null?'<span class="lowv">史低 ¥'+p.hist_low_price+'</span> · ':'')+
                '在售 '+p.in_stock_count+'/'+p.sku_count+' · '+fmtTime(p.last_ts)+
              '</span>'+
            '</div>'+
            '<div class="meta-row">'+
              '<span>预期价 ¥</span>'+
              '<input type="text" class="tgin" id="tg_'+p.code+'" placeholder="未设置" '+
                'value="'+(p.target_price!=null?p.target_price:'')+'" '+
                'onkeydown="if(event.key===\'Enter\'){event.stopPropagation();saveTarget(\''+p.code+'\')}">'+
              '<button class="link" onclick="event.stopPropagation();saveTarget(\''+p.code+'\')">保存</button>'+
              (p.target_price!=null?'<button class="link" onclick="event.stopPropagation();clearTarget(\''+
                p.code+'\')">清除</button>':'')+
            '</div>'+
            '<div class="meta-row" style="border-top:1px solid var(--line);padding-top:8px">'+
              '<button class="link" onclick="event.stopPropagation();toggleMatrix(\''+p.code+'\')">查看尺码</button>'+
              '<button class="link" onclick="event.stopPropagation();openDetail(\''+p.code+'\')">价格详情</button>'+
              '<button class="link" onclick="event.stopPropagation();collectOne(\''+p.code+'\')">刷新</button>'+
              '<a class="link" href="https://h.uniqlo.cn/product?pid='+p.product_code+
                '" target="_blank" rel="noopener">官网页面 ↗</a>'+
              '<button class="link" onclick="event.stopPropagation();togglePause(\''+p.code+'\')">暂停/恢复</button>'+
              '<button class="link" data-del="'+p.code+
                '" onclick="event.stopPropagation();delProduct(\''+p.code+'\')">删除</button>'+
            '</div>'+
          '</div>'+
        '</div>'+
        '<div class="matrix" id="m_'+p.code+'" style="display:none"></div>'+
      '</div>';
    }).join('');
    monitorExpanded.forEach(c=>{ const el = $('m_'+c); if(el) renderMatrix(c, el); });
    loadWatches();
  }catch(e){ $('products').innerHTML = '<div class="empty">加载失败：'+esc(e.message)+'</div>'; }
}

async function saveTarget(code){
  const el = $('tg_'+code); if(!el) return;
  const v = el.value.trim();
  if(v && !(Number(v)>0)) return show('预期价需为正数', true);
  try{
    await postJson('/api/products/'+code+'/target', {target_price: v ? Number(v) : null});
    show(v ? ('已设置预期价 ¥'+v+'：跌到该价才推送达标通知') : '已清除预期价，恢复为「降价即推送」');
    loadMonitor();
  }catch(e){ show('保存失败：'+esc(e.message), true); }
}
function clearTarget(code){ const el = $('tg_'+code); if(el) el.value=''; saveTarget(code); }

function toggleMatrix(code){
  const el = $('m_'+code); if(!el) return;
  if(el.style.display === 'none'){ el.style.display=''; monitorExpanded.add(code); renderMatrix(code, el); }
  else { el.style.display='none'; monitorExpanded.delete(code); }
}

const refreshCd = {}, pullAllCd = {v:0};
async function collectOne(code){
  const left = Math.ceil(((refreshCd[code]||0)-Date.now())/1000);
  if(left > 0) return show(code+' 刷新冷却中，'+left+' 秒后再试', true);
  refreshCd[code] = Date.now()+60000;
  show('<span class="spin"></span> 正在采集 '+code+'…');
  try{
    const r = await api('/api/collect/'+code, {method:'POST'});
    show(r.ok ? r.message : ('采集失败：'+r.message), !r.ok);
    loadMonitor();
  }catch(e){ show('采集失败：'+esc(e.message), true); }
}

async function collectAll(){
  const left = Math.ceil((pullAllCd.v-Date.now())/1000);
  if(left > 0) return show('全部刷新冷却中，'+left+' 秒后再试', true);
  pullAllCd.v = Date.now()+60000;
  show('<span class="spin"></span> 正在采集全部关注商品…');
  try{ const r = await api('/api/collect', {method:'POST'});
    show(r.summary, !r.ok); loadMonitor(); }
  catch(e){ show('采集失败：'+esc(e.message), true); }
}

const delArm = {};
function delProduct(code){
  const btn = document.querySelector('[data-del="'+code+'"]');
  if(delArm[code]){
    clearTimeout(delArm[code]); delete delArm[code];
    if(btn){ btn.textContent='删除'; btn.style.color=''; }
    api('/api/subscribe/'+code, {method:'DELETE'})
      .then(()=>{ show('已取消关注并移除 '+code); loadMonitor(); refreshBadges(); })
      .catch(e=>show('删除失败：'+esc(e.message), true));
    return;
  }
  delArm[code] = setTimeout(()=>{
    delete delArm[code];
    const b = document.querySelector('[data-del="'+code+'"]');
    if(b){ b.textContent='删除'; b.style.color=''; }
  }, 3000);
  if(btn){ btn.textContent='确认删除？'; btn.style.color='var(--accent)'; }
  show('再次点击「确认删除？」取消关注并移除 '+code+'（3 秒内有效）');
}

async function togglePause(code){
  await api('/api/products/'+code+'/toggle', {method:'POST'});
  loadMonitor();
}

/* ---------- 尺码订阅 ---------- */
async function loadWatches(){
  const target = $('watches'); if(!target) return;
  try{
    const w = await api('/api/watches');
    target.innerHTML = w.length ? ('<div class="card"><div style="font-size:13px;margin-bottom:6px">'+
      '尺码订阅 '+w.length+' 条（仅这些颜色/尺码的断货·补货·低库存才推送）</div>' + w.map(x=>{
      const pic = x.main_pic
        ? imgTag(x.main_pic, null, 'thumb-sm', '', 'onclick="viewImg(this.src)"')
        : '<div class="thumb-sm"></div>';
      return '<div class="ev">'+pic+
        '<span style="flex:1;min-width:0">'+
          '<span style="display:block">'+esc(x.name||x.product_code)+
          (x.code?' <span class="card-code">'+esc(x.code)+'</span>':'')+'</span>'+
          '<span class="card-code">'+esc(x.color||'任意色')+' · '+esc(x.size||'任意码')+
          (x.cur_price!=null?' · 现价 ¥'+x.cur_price:'')+
          (x.hist_low_price!=null?' · 史低 ¥'+x.hist_low_price:'')+'</span>'+
        '</span>'+
        '<button class="link" onclick="delWatch('+x.id+')">取消订阅</button></div>';
    }).join('')+'</div>')
      : '<div class="empty">还没有尺码订阅。打开商品详情的「拉取尺码库存」，点击色码单元格即可订阅补货提醒。</div>';
  }catch(e){ target.innerHTML = '<div class="empty">'+esc(e.message)+'</div>'; }
}
async function delWatch(id){
  await api('/api/watches/'+id, {method:'DELETE'});
  loadWatches(); refreshBadges();
}
