/* 变价信息（全站价格变动流） + 变更日志（关注商品的库存/价格事件） */

const changes = {days: 3, direction: '', loading: false};

async function loadChanges(){
  const box = $('changes'); if(!box) return;
  box.innerHTML = '<div class="empty"><span class="spin"></span> 加载中…</div>';
  try{
    const p = new URLSearchParams({days: changes.days, limit: 200,
      direction: changes.direction});
    const d = await api('/api/price-changes?' + p.toString());
    const bar = $('changesBar');
    if(bar) bar.innerHTML =
      '<span>时间范围</span>' +
      [1,3,7,30].map(n=>'<button class="chipbtn'+(changes.days===n?' on':'')+
        '" onclick="setChangesDays('+n+')">'+n+' 天</button>').join('') +
      '<span class="spacer"></span>' +
      [['','全部'],['down','只看降价'],['up','只看涨价']].map(x=>
        '<button class="chipbtn'+(changes.direction===x[0]?' on':'')+
        '" onclick="setChangesDir(\''+x[0]+'\')">'+x[1]+'</button>').join('') +
      '<span class="count-chip">'+d.total+' 条</span>';
    if(!d.items.length){
      box.innerHTML = '<div class="empty">该时间段内没有价格变动。'+
        '（商品库每 6 小时扫描一次，价格变化才会记录）</div>'; return;
    }
    box.innerHTML = '<div class="card">' + d.items.map(c=>{
      const diff = (c.old_price!=null && c.new_price!=null) ? (c.new_price - c.old_price) : null;
      const cls = diff < 0 ? 'delta-down' : 'delta-up';
      const pic = c.main_pic
        ? imgTag(c.main_pic, null, 'thumb-sm', '', 'onclick="viewImg(this.src)"')
        : '<div class="thumb-sm"></div>';
      return '<div class="ev" style="cursor:pointer" onclick="openDetail(\''+c.code+'\')">'+
        '<span class="k k-'+(diff<0?'PRICE_DOWN':'PRICE_UP')+'">'+(diff<0?'降价':'涨价')+'</span>'+
        pic +
        '<span style="flex:1;min-width:0">'+
          '<span style="display:block">'+esc(c.name||c.code)+
          ' <span class="card-code">'+esc(c.code)+'</span>'+
          (c.track_level==='full'?' <span class="tag sub">已关注</span>':'')+'</span>'+
          '<span class="card-code">¥'+c.old_price+' → ¥'+c.new_price+
          (c.hist_low_price!=null?' · 史低 ¥'+c.hist_low_price:'')+'</span>'+
        '</span>'+
        '<span class="'+cls+'">'+(diff>0?'+':'')+diff.toFixed(0)+'</span>'+
        '<span class="t">'+fmtTime(c.ts)+'</span></div>';
    }).join('') + '</div>';
  }catch(e){ box.innerHTML = '<div class="empty">加载失败：'+esc(e.message)+'</div>'; }
}

function setChangesDays(n){ changes.days = n; loadChanges(); }
function setChangesDir(d){ changes.direction = d; loadChanges(); }

/* ---------- 变更日志（关注商品的细粒度事件） ---------- */
async function loadEvents(){
  const box = $('events'); if(!box) return;
  box.innerHTML = '<div class="empty"><span class="spin"></span></div>';
  try{
    const evs = await api('/api/events?limit=200');
    box.innerHTML = evs.length ? ('<div class="card">'+evs.map(e=>
      '<div class="ev"><span class="k k-'+e.kind+'">'+kindText(e.kind)+'</span>'+
      '<span>'+esc(e.title)+'</span>'+
      (e.detail?'<span style="color:var(--muted)">'+esc(e.detail)+'</span>':'')+
      '<span class="spacer" style="flex:1"></span>'+
      '<span class="t">'+fmtTime(e.ts)+'</span></div>').join('')+'</div>')
      : '<div class="empty">暂无变更记录。关注商品后，库存或价格变化会出现在这里。</div>';
  }catch(e){ box.innerHTML = '<div class="empty">'+esc(e.message)+'</div>'; }
}

async function loadLogs(){
  const box = $('logs'); if(!box) return;
  try{
    const rows = await api('/api/logs?limit=30');
    box.innerHTML = rows.length ? ('<div class="card">'+rows.map(r=>
      '<div class="ev"><span class="k">'+(r.ok?'OK':'失败')+'</span>'+
      '<span style="flex:1;min-width:0">'+esc(r.message)+'</span>'+
      '<span class="t">'+fmtTime(r.ts)+'</span></div>').join('')+'</div>')
      : '<div class="empty">暂无日志</div>';
  }catch(e){ box.innerHTML = '<div class="empty">'+esc(e.message)+'</div>'; }
}
