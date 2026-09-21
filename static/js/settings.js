/* 设置：通知 / 采集间隔 / 商品库统计与手动扫描 */

async function loadSettings(){
  try{
    const s = await api('/api/settings');
    $('s_interval').value = s.stock_interval_min ?? 30;
    $('s_sweep').value = s.catalog_sweep_min ?? 360;
    $('s_keep').value = s.catalog_keep_days ?? 90;
    $('s_hide').value = s.catalog_hide_days ?? 3;
    $('s_dropmin').value = s.price_drop_min_pct ?? 0;
    $('s_eventkeep').value = s.event_keep_days ?? 90;
    $('s_logkeep').value = s.log_keep_days ?? 30;
    const kinds = s.notify_kinds || [];
    $('s_kind_price').checked = kinds.indexOf('PRICE_DOWN') >= 0 || kinds.indexOf('TARGET_HIT') >= 0;
    $('s_kind_stock').checked = kinds.indexOf('IN') >= 0 || kinds.indexOf('OUT') >= 0 || kinds.indexOf('LOW') >= 0;
    $('s_kind_transit').checked = kinds.indexOf('TRANSIT') >= 0;
    $('s_kind_gone').checked = kinds.indexOf('GONE') >= 0;
    $('s_threshold').value = s.low_threshold ?? 2;
    $('s_enabled').value = String(s.notify_enabled ?? 0);
    $('s_channel').value = s.notify_channel || 'pushplus';
    $('s_pp_token').value = s.pp_token || '';
    $('s_ntfy_server').value = s.ntfy_server || '';
    $('s_ntfy_topic').value = s.ntfy_topic || '';
    $('s_webhook').value = s.wecom_webhook || '';
    $('s_quiet').value = s.quiet_hours || '';
  }catch(e){ show('设置加载失败：'+esc(e.message), true); }
  loadStats();
  loadLogs();
}

async function loadStats(){
  const box = $('stats'); if(!box) return;
  try{
    const st = await api('/api/catalog/stats');
    const sweep = (st.sweeps||[])[0];
    box.innerHTML =
      '<div class="meta-row" style="gap:16px">' +
        '<span>商品库 <b>'+st.total+'</b> 件</span>' +
        '<span>关注中 <b>'+st.full+'</b> 件</span>' +
        '<span>分类 <b>'+st.categories+'</b> 个</span>' +
        '<span>价格点 <b>'+st.price_points+'</b> 个</span>' +
        '<span>近24h抓到 <b>'+st.fresh_24h+'</b> 件</span>' +
        '<span class="spacer"></span>' +
        '<span>'+(st.sweep && st.sweep.running
            ? '<span class="spin"></span> 正在扫描…'
            : (st.last_sweep_ts ? '上次扫描 '+fmtTime(st.last_sweep_ts) : '尚未扫描'))+'</span>' +
      '</div>' +
      (sweep ? '<div class="note">最近一次：'+esc(sweep.message||'')+'</div>' : '');
    const btn = $('sweepBtn');
    if(btn) btn.disabled = !!(st.sweep && st.sweep.running);
  }catch(e){ box.innerHTML = '<div class="note">统计加载失败：'+esc(e.message)+'</div>'; }
}

async function saveSettings(){
  const body = {
    stock_interval_min: Number($('s_interval').value)||30,
    catalog_sweep_min: Number($('s_sweep').value)||360,
    catalog_keep_days: Number($('s_keep').value)||90,
    catalog_hide_days: Number($('s_hide').value)||3,
    price_drop_min_pct: Number($('s_dropmin').value)||0,
    event_keep_days: Number($('s_eventkeep').value)||90,
    log_keep_days: Number($('s_logkeep').value)||30,
    notify_kinds: [].concat(
      $('s_kind_price').checked ? ['PRICE_DOWN','TARGET_HIT'] : [],
      $('s_kind_stock').checked ? ['IN','OUT','LOW'] : [],
      $('s_kind_transit').checked ? ['TRANSIT'] : [],
      $('s_kind_gone').checked ? ['GONE'] : []),
    low_threshold: Number($('s_threshold').value)||2,
    notify_enabled: Number($('s_enabled').value)||0,
    notify_channel: $('s_channel').value,
    pp_token: $('s_pp_token').value.trim(),
    ntfy_server: $('s_ntfy_server').value.trim(),
    ntfy_topic: $('s_ntfy_topic').value.trim(),
    wecom_webhook: $('s_webhook').value.trim(),
    quiet_hours: $('s_quiet').value.trim(),
  };
  try{ await postJson('/api/settings', body); show('设置已保存'); }
  catch(e){ show('保存失败：'+esc(e.message), true); }
}

/* 两步确认（不用 window.confirm：预览/iframe 环境里 confirm 会直接返回 false） */
let sweepArm = 0;
function _sweepBtns(){
  return [document.getElementById('sweepBtn'), document.getElementById('btnSweepTop')]
    .filter(Boolean);
}
function _sweepReset(){
  _sweepBtns().forEach(b => { b.textContent = b.id === 'sweepBtn' ? '立即扫描商品库' : '更新商品库';
                              b.style.color = ''; });
}

async function runSweep(){
  const now = Date.now();
  if(now >= sweepArm){
    sweepArm = now + 5000;
    _sweepBtns().forEach(b => { b.textContent = '确认扫描？约 100 次请求';
                                b.style.color = 'var(--accent)'; });
    show('再次点击「确认扫描？」立即开始扫描商品库（5 秒内有效，约 1–2 分钟）');
    setTimeout(() => { if(Date.now() >= sweepArm) _sweepReset(); }, 5100);
    return;
  }
  sweepArm = 0;
  try{
    const r = await postJson('/api/catalog/sweep', {scope:'ALL'});
    show(r.message || (r.ok ? '已开始扫描' : '扫描未能启动'), !r.ok);
    if(r.ok) _sweepBtns().forEach(b => { b.disabled = true; });
    const t = setInterval(async ()=>{
      const st = await api('/api/catalog/stats').catch(()=>({sweep:{running:false}}));
      loadStats();
      if(!st.sweep || !st.sweep.running){ clearInterval(t); loadShop(true); loadCategories(); }
    }, 4000);
  }catch(e){ show('触发失败：'+esc(e.message), true); }
}

async function testNotify(){
  show('测试通知：先在设置里保存渠道并「开启通知」，然后到「监控」对任一关注商品点「刷新」产生事件即可验证推送。');
}
