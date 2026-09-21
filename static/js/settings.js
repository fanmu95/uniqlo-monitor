/* 设置：通知 / 采集间隔 / 商品库统计与手动扫描 */

async function loadSettings(){
  try{
    const s = await api('/api/settings');
    $('s_interval').value = s.stock_interval_min ?? 30;
    $('s_sweep').value = s.catalog_sweep_min ?? 360;
    $('s_keep').value = s.catalog_keep_days ?? 90;
    $('s_hide').value = s.catalog_hide_days ?? 3;
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

async function runSweep(){
  if(!window.confirm('立即扫描官方商品库？\n\n会对官网发起约 100 次低频请求（1 次/秒），'+
                     '耗时 1–2 分钟，期间可正常使用界面。')) return;
  try{
    const r = await postJson('/api/catalog/sweep', {scope:'ALL'});
    show(r.message || (r.ok ? '已开始扫描' : '扫描未能启动'), !r.ok);
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
