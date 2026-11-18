'use strict';
const $ = selector => document.querySelector(selector);
const systems = ['static', 'reactive', 'predictive-baseline', 'predictive-ml'];
let state, run, system = 'predictive-ml', runId = 'run-01', elapsed = 40, playing = false, generation = 0, lastFrame;
let viewingLive=false, liveState, liveId='', liveRefreshing=false, activeLiveId='', liveActionPending=false;
let researchState={groups:[]};
const liveLabels={'predictive-safe':'GB + confidence + safe','predictive-confidence':'GB + confidence','predictive-safe-only':'GB + safe','ecmp':'ECMP'};
const escapeText=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const primary = [['P95_Latency_ms','P95 RTT','ms'],['Average_Throughput_Mbps','Goodput','Mbps'],['Average_Packet_Loss_pct','Packet loss','%'],['Congestion_Duration_seconds','Congestion','s']];
const number = (value, digits=2) => Number.isFinite(value) ? value.toFixed(digits) : '—';
const timeLabel = seconds => `${String(Math.floor(Math.max(0,seconds)/60)).padStart(2,'0')}:${String(Math.floor(Math.max(0,seconds)%60)).padStart(2,'0')}`;
function error(message) { $('#error').textContent = message; $('#error').hidden = !message; }
async function api(url, options) { const response=await fetch(url,options); if(!response.ok){let message='Capture is not ready. Please choose a verified run.';try{message=(await response.json()).error||message;}catch{}throw new Error(message);} return response.json(); }

async function refreshState() {
  try {
    state=await api('/api/state');
    $('#campaign-status').textContent=state.complete ? 'Original: 32 runs verified' : `Original: ${state.verified} / 32 verified`;
    $('#campaign-status').classList.toggle('pending',!state.complete);
    $('#footer-status').textContent=viewingLive?'New sessions are separate from the original campaign':state.complete ? 'Original controlled campaign verified' : 'Campaign in progress · results provisional';
    $('#monitor').hidden=!state.runs.some(r=>r.status==='running');
    $('#comparison-note').textContent=state.complete ? '8 repetitions per system · 95% confidence intervals' : 'Verified runs only · unequal sample counts';
    if(state.issues.length) error('Some captures need review and are excluded from these results.');
    renderComparison();
    await refreshResearch();
    await refreshTopnotch();
    await refreshShowcase();
    if(!viewingLive && !run) await chooseSystem(system);
    else if(!viewingLive && run.live) await loadRun();
  } catch (e) { error('The local data service is unavailable. Reload after restarting the project.'); }
}
async function refreshTopnotch(){
  try{
    const data=await api('/api/topnotch');
    const latest=(data.explanations||[]).at(-1);
    if(latest){
      $('#explain-decision').textContent=latest.candidate?`Path ${latest.active_path} → ${latest.candidate} · ${String(latest.reason||'').replaceAll('_',' ')}`:`Stayed on path ${latest.active_path} · ${String(latest.reason||'').replaceAll('_',' ')}`;
      $('#explain-features').textContent=latest.top_features?`Top contributing features: ${latest.top_features}`:'No feature attribution for this decision';
    }
    const drift=(data.drift||[]).at(-1);
    if(drift){
      const detected=drift.drift_detected==='True'||drift.drift_detected==='true';
      $('#drift-summary').textContent=`RMSE ${number(Number(drift.rmse))} vs baseline ${number(Number(drift.baseline_rmse))}`;
      $('#drift-status').textContent=detected?'Drift detected · recalibration written':'No drift detected';
    }
    const failures=data.failures||[];
    const downs=failures.filter(f=>f.event==='link_down').length;
    $('#failure-summary').textContent=failures.length?`${downs} link-down event(s) logged`:'No failures logged';
  }catch{ /* Live insight logs appear after the first top-notch controller run. */ }
}
async function refreshShowcase(){
  try{
    const d=await api('/api/showcase');
    if(window.renderShowcase){ window.renderShowcase(d); return; }
    const cards=[];
    const card=(title,state,headline,detail,extra)=>cards.push(Object.assign({title,state,headline,detail},extra||{}));
    const num=number;
    const p=d.platform||{}; const start=p.start;
    const pStatus=p.start_status||(start?(String(start.reason||'').includes('DISABLED')?'disabled':'enabled'):null);
    card('Extension platform', start?(pStatus==='disabled'?'off':'live'):'no data',
      start?'Controller extensions '+(pStatus==='disabled'?'DISABLED':pStatus==='enabled'?'enabled':'recorded'):'No controller run recorded yet',
      start?String(start.reason||''):'Start a live experiment and this fills in.',
      {stats:[['Live runs recorded',p.live_runs],['Campaign runs recorded',p.campaign_runs],['Controller starts logged',p.starts],['Starts · live / campaign',(p.starts_live??'—')+' / '+(p.starts_campaign??'—')],['Reroutes · live / campaign',(p.reroutes_live??'—')+' / '+(p.reroutes_campaign??'—')]],
       href:'/platform',openLabel:'Open the full platform record',
       source:'Source: control_events.csv from live captures + campaign-02'+(p.start_run?' · latest start: '+String(p.start_run).replace('run-','').slice(0,13)+(p.start_source?' ('+p.start_source+')':''):'')});
    const ex=d.explanations||{}; const reasons=Object.entries(ex.reasons||{}).sort((a,b)=>b[1]-a[1]);
    card('Explainable decisions', ex.latest?'live':'no data',
      ex.latest?('Latest: path '+(ex.latest.active_path||'?')+' · '+String(ex.latest.reason||'').replaceAll('_',' ')):'No decisions explained yet',
      ex.latest?('Top features: '+(ex.latest.top_features||'n/a')):'Every decision writes its reasons to explanations.csv.',
      {stats:[['Decisions explained',ex.count||0],['Most common reason',reasons.length?reasons[0][0].replaceAll('_',' ')+' × '+reasons[0][1]:'—'],['Top feature overall',ex.top_feature||'—'],['Runs contributing',ex.runs_contributing??'—'],['With forecast / interval',(ex.with_prediction??'—')+' / '+(ex.with_interval??'—')]],
       compare:reasons.length?{title:'Decision reasons',items:reasons.slice(0,3).map(([k,v])=>[k.replaceAll('_',' '),v,''])}:null,
       href:'/explainability',openLabel:'Open the full explainability record',
       source:'Source: explanations.csv across all live captures'});
    const dr=d.drift||{}; const drEvents=Object.entries(dr.events||{}); const drTotal=drEvents.reduce((n,[,v])=>n+v,0); const fc=dr.forecast;
    card('Drift monitoring', (drTotal||fc||dr.latest)?'live':'no data',
      drTotal?drTotal+' drift detector firing(s) in live runs':(fc?'Forecast error tracked over '+fc.n+' recorded predictions':'No drift data yet'),
      dr.latest_event?('Latest: '+dr.latest_event):'ADWIN + Page-Hinkley watch the forecast-error stream live.',
      {stats:drEvents.map(([k,v])=>[k.replace('drift_detector_','').replaceAll('_',' '),v+' firing(s)']),
       compare:fc?{title:'Forecast RMSE · first half vs second half of campaign-02',items:[['First half',Number(fc.rmse_first),' pts'],['Second half',Number(fc.rmse_second),' pts']]}:null,
       href:'/drift',openLabel:'Open the full drift record',
       source:'Source: live drift events + campaign-02 predictions paired with telemetry at +5 s'});
    const f=d.failures||{}; const tr=f.transitions||{}; const rb=Object.entries(f.reroutes_by_system||{});
    const rbLabels={static:'Static',reactive:'Reactive','predictive-baseline':'Persistence','predictive-ml':'Predictive GB'};
    card('Failure recovery', (tr.completed||f.downs)?'live':'armed',
      (tr.completed||0)+' reroute transition(s) completed · '+(tr.failed||0)+' failed',
      tr.latest?('Latest stage: '+(tr.latest.stage||'')+' / '+(tr.latest.status||'')):'Port-status events drive reroute + recovery timing (failures.csv).',
      {stats:[['Link-down events (live)',f.downs||0],['Mean transition time',tr.mean_seconds!=null?num(Number(tr.mean_seconds))+' s':'—']],
       compare:rb.length?{title:'Recorded reroutes per system · campaign-02 (8 runs each)',items:rb.map(([k,v])=>[rbLabels[k]||k,v,''])}:null,
       href:'/recovery',openLabel:'Open the full recovery record',
       source:'Source: transitions.csv (live) + control_events.csv (campaign-02)'});
    const an=d.anomalies||{}; const kinds=Object.entries(an.kinds||{}).sort((a,b)=>b[1]-a[1]);
    card('Anomaly mitigation', an.count?'live':'armed',
      (an.count||0)+' surge/elephant detection(s) in live runs',
      an.latest?('Latest: '+an.latest):'Statistical detector (rolling z-score + traffic share).',
      {stats:kinds.map(([k,v])=>[k.replaceAll('_',' '),v+' detection(s)']),
       compare:kinds.length?{title:'Detection kinds',items:kinds.map(([k,v])=>[k.replaceAll('_',' '),v,''])}:null,
       href:'/anomaly',openLabel:'Open the full anomaly record',
       source:'Source: anomaly_detected events across all live captures'});
    const cf=d.conformal||{}; const art=cf.artifact||{}; const live=cf.live||{}; const wp=Object.entries(live.width_by_path||{});
    card('Conformal intervals', art.nominal_coverage?'live':'no data',
      art.nominal_coverage?('Coverage guarantee '+num(Number(art.nominal_coverage)*100,0)+'% · radius '+num(Number(art.radius_percentage_points))+' pts'):'No calibration artifact yet',
      art.empirical_coverage_on_calibration?('Calibrated on '+((art.provenance||{}).capture||'a recorded capture')+' · empirical coverage '+num(Number(art.empirical_coverage_on_calibration.coverage)*100,1)+'% at mean width '+num(Number(art.empirical_coverage_on_calibration.mean_width))):'Run calibrate_conformal.py to build the artifact.',
      {stats:[['Calibration rows',art.calibration_rows!=null?art.calibration_rows:'—'],['Live intervals recorded',live.rows||0],['Gate held (too uncertain)',live.gate_hold||0],['Gate passed',live.gate_pass||0]],
       compare:wp.length?{title:'Live mean interval width by path',items:wp.map(([k,v])=>['Path '+k,Number(v),' pts'])}:null,
       href:'/conformal',openLabel:'Open the full conformal record',
       source:'Source: conformal_calibration.json + confidence.csv from live runs'});
    const latStats=Object.entries(d.latency_stats||{}); const lat=Object.entries(d.latency||{});
    card('Per-link latency', lat.length?'live':'no data',
      lat.length?lat.map(([k,v])=>k+' '+num(Number(v.latency_ms))+' ms').join(' · '):'No latency estimates yet',
      'EWMA per link from live RTT probes; feeds delay-aware scoring (latency.csv).',
      {stats:latStats.map(([k,v])=>[k+' mean',num(Number(v.mean))+' ms over '+v.count+' samples']),
       compare:latStats.length?{title:'Mean latency by link',items:latStats.map(([k,v])=>[k,Number(v.mean),' ms'])}:null,
       href:'/latency',openLabel:'Open the full latency record',
       source:'Source: latency.csv across all live captures'});
    const alStats=Object.entries(d.all_link_stats||{}); const al=Object.entries(d.all_links||{});
    card('All-link telemetry', al.length?'live':'no data',
      al.length?al.map(([k,v])=>k+' '+num(Number(v))+'%').join(' · '):'No all-link capture yet',
      'Every core link recorded, not just s1 (telemetry_all_links.csv in each new capture).',
      {stats:alStats.map(([k,v])=>[k+' peak',num(Number(v.peak),1)+'% · mean '+num(Number(v.mean),1)+'%']),
       compare:alStats.length?{title:'Peak utilization by link',items:alStats.map(([k,v])=>[k,Number(v.peak),'%'])}:null,
       href:'/telemetry',openLabel:'Open the full telemetry record',
       source:'Source: telemetry_all_links.csv across all live captures'});
    const g=d.gnn||{};
    card('GNN forecaster', g.gnn?'live':'no data',
      g.gnn?('GNN MAE '+num(Number(g.gnn.mae))+' vs persistence '+num(Number((g.persistence||{}).mae))):'No GNN evaluation yet',
      g.diebold_mariano_gnn_vs_persistence?('DM test vs persistence: p = '+num(Number(g.diebold_mariano_gnn_vs_persistence.p_value),3)+' — not significant, persistence wins on this data (honest result).'):'Trained on pooled all-link telemetry.',
      {stats:[['Train / test snapshots',(g.train_snapshots||'—')+' / '+(g.test_snapshots||'—')],['Training loss',num(Number(g.initial_loss),3)+' → '+num(Number(g.final_loss),3)]],
       compare:g.gnn?{title:'MAE comparison (lower is better)',items:[['GNN',Number(g.gnn.mae),''],['Persistence',Number((g.persistence||{}).mae),''],['Ridge',Number((g.ridge_same_features||{}).mae),'']]}:null,
       href:'/gnn',openLabel:'Open the full GNN record',
       source:'Source: cnt-ml/results/gnn_metrics.json (recorded evaluation)'});
    const mc=d.models_comparison||[]; const gb=mc.find(m=>String(m.model||'').includes('Gradient'));
    card('Forecaster comparison', mc.length?'live':'no data',
      gb?('Gradient Boosting RMSE '+num(Number(gb.rmse))+' · R² '+num(Number(gb.r2),3)):'No recorded model comparison',
      'The recorded model selection behind the controller: GB picked on RMSE/R², persistence the honest MAE baseline.',
      {stats:mc.map(m=>[m.model,'MAE '+num(Number(m.mae))+' · R² '+num(Number(m.r2),3)]),
       compare:mc.length?{title:'RMSE comparison (lower is better)',items:mc.map(m=>[m.model,Number(m.rmse),''])}:null,
       href:'/forecasters',openLabel:'Open the full forecaster record',
       source:'Source: cnt-ml/results/metrics_comparison.csv (recorded)'});
    const qos=d.qos||[]; const bands10=(d.qos_bands||{})['10']||{}; const bands20=(d.qos_bands||{})['20']||{};
    card('QoS · DiffServ', qos.length?'armed':'no data',
      qos.length?qos.map(c=>c.name+' '+(c.share==null?'unmetered':Math.round(c.share*100)+'%')).join(' · '):'Policy unavailable',
      'Meter rates derived by qos_policy.meter_bands() from class share × link capacity.',
      {stats:qos.filter(c=>c.share!=null).map(c=>[c.name+' (DSCP '+(c.dscp||[]).join('/')+')',(bands10[c.name]||0)+' kbps @10M · '+(bands20[c.name]||0)+' kbps @20M']),
       compare:Object.keys(bands10).length?{title:'Meter rate by class · 10 Mbps link',items:Object.entries(bands10).map(([k,v])=>[k,v,' kbps'])}:null,
       href:'/qos',openLabel:'Open the full QoS record',
       source:'Source: qos_policy.py computed at request time · SDN_QOS=1 in live runs'});
    const acl=d.acl||{}; const rules=acl.rules||[];
    card('ACL firewall', rules.length?'armed':'no data',
      (acl.drops||0)+' packet(s) dropped by ACL rules in live runs',
      rules.length?rules.map(r=>r.action.toUpperCase()+' '+r.protocol+' → port '+(r.dst_ports&&r.dst_ports[0]===r.dst_ports[1]?r.dst_ports[0]:(r.dst_ports||[]).join('-'))+' from '+r.src).join(' · '):'5-tuple rules from JSON with per-rule hit counts.',
      {stats:[['Rules loaded & validated',rules.length],['Default policy',acl.default_policy||'—']],
       href:'/acl',openLabel:'Open the full ACL record',
       source:'Source: acl_rules.example.json loaded through acl_firewall.py · drops from live control events'});
    const sl=d.slicing||{}; const slices=sl.slices||[];
    card('Network slicing', slices.length?'armed':'no data',
      (sl.drops||0)+' cross-slice drop(s) in live runs',
      sl.error?('Slice config failed validation: '+sl.error):'Isolation validated at load by slicing.py: host in at most one slice, VLANs unique, shares cannot overcommit.',
      {stats:slices.map(s=>[s.slice_id+' · VLAN '+s.vlan_id,s.hosts.length+' hosts · '+Math.round(s.share*100)+'% = '+num(s.share*10,1)+' Mbps @10M']),
       compare:slices.length?{title:'Bandwidth share by slice',items:slices.map(s=>[s.slice_id,Math.round(s.share*100),'%'])}:null,
       href:'/slicing',openLabel:'Open the full slicing record',
       source:'Source: slices.example.json loaded through slicing.py · drops from live control events'});
    const grid=$('#showcase-grid');
    grid.replaceChildren(...cards.map(c=>{
      const el=document.createElement(c.href?'a':'div'); el.className='feature-card'+(c.href?' feature-card-link':'');
      if(c.href) el.href=c.href;
      const h=document.createElement('h3'); h.textContent=c.title;
      const chip=document.createElement('span'); chip.className='feature-state '+c.state.replace(' ','-'); chip.textContent=c.state;
      const head=document.createElement('strong'); head.textContent=c.headline;
      const det=document.createElement('small'); det.textContent=c.detail;
      el.append(h,chip,head,det);
      if(c.stats&&c.stats.length){
        const box=document.createElement('div'); box.className='feature-stats';
        c.stats.forEach(([label,value])=>{
          const row=document.createElement('div'); row.className='feature-stat';
          const l=document.createElement('span'); l.textContent=label;
          const b=document.createElement('b'); b.textContent=String(value);
          row.append(l,b); box.append(row);
        });
        el.append(box);
      }
      if(c.compare&&c.compare.items&&c.compare.items.length){
        const box=document.createElement('div'); box.className='feature-compare';
        const title=document.createElement('div'); title.className='feature-compare-title'; title.textContent=c.compare.title;
        box.append(title);
        const max=Math.max(...c.compare.items.map(([,v])=>Number(v)||0),0.0001);
        c.compare.items.forEach(([label,value,unit])=>{
          const row=document.createElement('div'); row.className='fc-row';
          const l=document.createElement('span'); l.className='fc-label'; l.textContent=label;
          const track=document.createElement('span'); track.className='fc-track';
          const bar=document.createElement('span'); bar.className='fc-bar'; bar.style.width=Math.max(2,Math.round(100*(Number(value)||0)/max))+'%';
          track.append(bar);
          const val=document.createElement('span'); val.className='fc-value'; val.textContent=num(Number(value),Number(value)>=100?0:2)+(unit||'');
          row.append(l,track,val); box.append(row);
        });
        el.append(box);
      }
      if(c.source){ const src=document.createElement('small'); src.className='feature-source'; src.textContent=c.source; el.append(src); }
      if(c.href){ const open=document.createElement('small'); open.className='feature-open'; open.textContent=(c.openLabel||'Open')+' →'; el.append(open); }
      return el;
    }));
    const showing=cards.filter(c=>c.state!=='no data'&&c.state!=='off').length;
    const liveN=cards.filter(c=>c.state==='live').length;
    $('#showcase-status').textContent=showing+' of '+cards.length+' features showing data · '+liveN+' live';
  }catch{ /* Showcase fills in as feature artifacts appear. */ }
}
async function refreshResearch(){
  try{
    researchState=await api('/api/research');
    const selected=$('#comparison-source').value||'original';
    $('#comparison-source').replaceChildren(new Option('Original controlled campaign','original'),
      ...researchState.groups.map(g=>new Option(`${g.scenario} / ${g.topology} · ${g.runs.length} runs${g.smoke?' · smoke':''}`,g.id)));
    $('#comparison-source').value=researchState.groups.some(g=>g.id===selected)?selected:'original';
    renderComparison();
  }catch{ /* Original captures stay available while extension data is starting. */ }
}
async function chooseSystem(next) {
  if(!systems.includes(next)) throw new Error('Unknown system');
  system=next;
  document.querySelectorAll('[data-system]').forEach(button=>{button.classList.toggle('selected',button.dataset.system===system);button.setAttribute('aria-pressed',String(button.dataset.system===system));});
  const available=state.runs.filter(r=>r.system===system && ['verified','running'].includes(r.status));
  $('#run-select').replaceChildren(...available.map(item=>new Option(item.id.replace('run-','')+(item.status==='running'?' · recording':''),item.id)));
  if(!available.some(r=>r.id===runId)) runId=available[0]?.id;
  $('#run-select').value=runId || '';
  if(!runId) {run=null;error('No verified capture is available for this system yet.'); return;}
  await loadRun();
}
async function loadRun() {
  const token=++generation;
  setPlaying(false);
  run=null;
  $('#network-data').hidden=true;
  $('#play').disabled=true;
  try {
    const result=await api(`/api/run?system=${encodeURIComponent(system)}&id=${encodeURIComponent(runId)}`);
    if(token!==generation) return;
    if(result.pending) {error('The network is starting. Telemetry will appear shortly.');return;}
    renderRun(result);
  } catch(e) {if(token===generation) error(e.message);}
}
function renderRun(result) {
    run=result;error('');
    $('#network-data').hidden=false;
    elapsed=run.live?run.duration:Math.min(elapsed,run.duration);
    $('#source-label').textContent=run.live?'Live capture · unverified':'Recorded replay';
    const latest=run.points.filter(p=>p.path===p.active).at(-1);
    const cards=run.live?[['Active link',latest?.actual,'%'],['Forecast t+5s',latest?.forecast,'%'],['Elapsed',Math.max(0,run.duration),'s'],['Path changes',run.events.length,'']]:primary.map(([key,label,unit])=>[label,run.summary?.[key],unit]);
    $('#run-metrics').innerHTML=cards.map(([label,value,unit])=>`<article class="metric"><div class="metric-label">${label}</div><div class="metric-value">${number(value,run.live?1:2)}<small>${unit}</small></div><div class="metric-caption">${run.live?'Live measurement':'Full-run measurement'}</div></article>`).join('');
    document.body.classList.toggle('playing',run.live||playing);
    renderTopology();
    $('#duration').textContent=timeLabel(run.duration);
    $('#scrubber').max=run.duration;
    $('#scrubber').disabled=run.live;
    $('#play').disabled=run.live;
    $('#events').innerHTML=run.events.length?run.events.map(event=>`<div class="event" data-time="${event.t}"><span class="event-time">${timeLabel(event.t)}</span><b>Path ${event.old}</b><span class="arrow">→</span><b>${event.new}</b></div>`).join(''):'<div class="event">No path changes</div>';
    $('#event-count').textContent=run.events.length;
    $('#safety-panel').hidden=!run.research;
    const loss=run.summary?.Transition_Probe_Loss_pct;
    $('#transition-loss').textContent=Number.isFinite(loss)?`${number(loss)}%`:'No complete transition window';
    const windowLoss=window=>{const rows=(run.transition_metrics?.rows||[]).filter(r=>r.flow_id==='all'&&r.window===window&&r.Window_Complete);const queued=rows.reduce((n,r)=>n+r.Probe_Packets_Queued,0);return queued?100*rows.reduce((n,r)=>n+r.Probe_Lost_Packets,0)/queued:null;};
    const before=windowLoss('before'),after=windowLoss('after');
    $('#transition-before-after').textContent=Number.isFinite(before)?`Before ${number(before)}% / after ${number(after)}% · ±2 s`:'Sequence probes within ±2 s';
    const stages={preparing:'Prepare',commit:'Steer',cleaning:'Retire'};
    $('#transition-stages').innerHTML=(run.transitions||[]).filter(r=>r.id!=='0'&&((stages[r.stage]&&['confirmed','committed'].includes(r.status))||['failed','degraded','indeterminate'].includes(r.status))).map(r=>`<span class="transition-stage ${r.status==='committed'?'confirmed':''}" title="${escapeText(r.detail)}">${timeLabel(r.t)} · ${stages[r.stage]||escapeText(r.stage)} / ${escapeText(r.status)}</span>`).join('');
    drawTrace();seek(elapsed);
}
function renderTopology(){
  const extended=run.topology==='access6';
  $('#topology').setAttribute('aria-label',`${extended?'Six switches and three simultaneous host pairs':'Four-switch diamond network'} showing the ${run.live?'live':'recorded'} shared active path`);
  $('.topology-foot>span').textContent=extended?'6 switches / 6 hosts / shared route':'4 switches / 2 hosts';
  const capacities=run.capacities||{A:10,B:20};
  document.querySelectorAll('.route-label').forEach((label,i)=>{const path=i===0?'A':'B';label.textContent=`${path} / ${capacities[path]} Mbps`;});
  document.querySelectorAll('.util-block small').forEach((label,i)=>label.textContent=`${capacities[i===0?'A':'B']} Mbps capacity`);
  document.querySelectorAll('#topology .host').forEach(e=>e.style.display=extended?'none':'');
  $('#topology .host-link').style.display=extended?'none':'';
  $('#access-nodes')?.remove();
  if(!extended)return;
  const group=document.createElementNS('http://www.w3.org/2000/svg','g');group.id='access-nodes';
  const switchNode=(x,label)=>`<g class="node" transform="translate(${x} 165)"><rect x="-23" y="-20" width="46" height="40" rx="8"/><path d="M-12 -6H12M-12 6H12"/><text y="48">${label}</text></g>`;
  const hosts=(x,access,labels)=>labels.map((label,i)=>{const y=65+i*100;return `<path class="host-link" d="M${x} ${y}H${access}V165"/><g class="node host" transform="translate(${x} ${y})"><rect x="-18" y="-15" width="36" height="30" rx="5"/><text y="39">${label}</text></g>`;}).join('');
  group.innerHTML=`<path class="host-link" d="M140 165H215M725 165H800"/>${hosts(35,140,['h1','h3','h5'])}${hosts(905,800,['h2','h4','h6'])}${switchNode(140,'s5')}${switchNode(800,'s6')}`;
  $('#topology').appendChild(group);
}
function pathAt(t) {let path='A';for(const event of run.events) if(event.t<=t) path=event.new;return path;}
function seek(t) {
  if(!run) return;
  elapsed=Math.max(0,Math.min(t,run.duration));
  $('#scrubber').value=elapsed;$('#elapsed').textContent=timeLabel(elapsed);
  const active=pathAt(elapsed);
  $('#active-path').textContent=`Path ${active} active`;
  let current;
  for(const path of ['A','B']) {
    const latest=run.points.filter(p=>p.path===path && p.t<=elapsed).at(-1);
    const value=latest?.actual;
    $(`#util-${path.toLowerCase()}`).textContent=Number.isFinite(value)?`${number(value,1)}%`:'—';
    $(`#meter-${path.toLowerCase()}`).style.width=`${Math.min(100,Math.max(0,value||0))}%`;
    $(`#meter-${path.toLowerCase()}`).classList.toggle('high',value>=85);
    $(`#route-${path.toLowerCase()}`).classList.toggle('active',active===path);
    $(`#flow-${path.toLowerCase()}`).classList.toggle('active',active===path);
    if(active===path) current=latest;
  }
  $('#reading').textContent=current?`Measured ${number(current.actual,1)}%  /  Forecast ${number(current.forecast,1)}${Number.isFinite(current.forecast)?'%':''}`:'Awaiting telemetry';
  if(run.research){
    $('#confidence-range').textContent=Number.isFinite(current?.lower)?`${number(current.lower,1)}–${number(current.upper,1)}%`:'Confidence gate not enabled';
    const decision=(run.decisions||[]).filter(p=>p.t<=elapsed).at(-1);
    const labels={measured_fallback:'Measured fallback',learned_confidence:'Confident forecast',blocked:'Route blocked',predictive:'Point forecast',reactive:'Measured utilization'};
    $('#decision-source').textContent=labels[decision?.source]||'Awaiting measurements';
    $('#decision-reason').textContent=(decision?.reason||current?.confidence_reason||'No decision logged').replaceAll('_',' ');
    $('#confidence-method').textContent=run.confidence?.enabled?`Nominal ${Math.round(run.confidence.nominal_coverage*100)}% · reused validation · exploratory`:'Shared A/B route · all flows move together';
  }
  const x=48+elapsed/Math.max(1,run.duration)*1028;
  $('#playhead')?.setAttribute('x1',x);$('#playhead')?.setAttribute('x2',x);
  document.querySelectorAll('.event[data-time]').forEach(e=>e.classList.toggle('future',Number(e.dataset.time)>elapsed));
  let boundary=0, stage;
  for(const item of run.workload){boundary+=item.duration_seconds;if(elapsed<boundary){stage=item;break;}}
  $('#workload-label').textContent=stage?`${stage.rate_mbps} Mbps offered`:'Workload complete';
}
function drawTrace() {
  const points=run.points.filter(p=>p.path===p.active && p.t>=0);
  const x=t=>48+t/Math.max(1,run.duration)*1028, y=value=>178-Math.min(110,Math.max(0,value))/110*158;
  let grid='';for(const value of [0,25,50,75,100])grid+=`<line class="grid-line" x1="48" y1="${y(value)}" x2="1076" y2="${y(value)}"/><text class="chart-axis" x="10" y="${y(value)+4}">${value}%</text>`;
  for(let t=0;t<=run.duration;t+=15)grid+=`<text class="chart-axis" text-anchor="middle" x="${x(t)}" y="203">${t}s</text>`;
  function line(key){let result='',connected=false;for(const p of points){if(!Number.isFinite(p[key])){connected=false;continue;}result+=`${connected?'L':'M'}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)} `;connected=true;}return result;}
  const band=points.filter(p=>Number.isFinite(p.lower)&&Number.isFinite(p.upper));
  const area=band.length?`M${band.map(p=>`${x(p.t)},${y(p.upper)}`).join(' L')} L${band.slice().reverse().map(p=>`${x(p.t)},${y(p.lower)}`).join(' L')} Z`:'';
  $('#trace').innerHTML=grid+`<path class="confidence-band" d="${area}"/><line class="threshold" x1="48" x2="1076" y1="${y(85)}" y2="${y(85)}"/><path class="trace-actual" d="${line('actual')}"/><path class="trace-forecast" d="${line('forecast')}"/><line id="playhead" class="playhead" x1="48" x2="48" y1="10" y2="180"/>`;
}
function setPlaying(value){playing=value;document.body.classList.toggle('playing',value);$('#play span').textContent=value?'Pause':'Play';$('#play').setAttribute('aria-label',value?'Pause recorded run':'Play recorded run');$('#play svg path').setAttribute('d',value?'M6 5H10V19H6ZM14 5H18V19H14Z':'M8 5L19 12L8 19Z');lastFrame=undefined;}
function frame(now){if(playing&&run){if(lastFrame!==undefined){seek(elapsed+(now-lastFrame)/1000*Number($('#speed').value));if(elapsed>=run.duration)setPlaying(false);}lastFrame=now;}requestAnimationFrame(frame);}
function renderComparison(){
  if(!state)return;
  const group=researchState.groups.find(g=>g.id===$('#comparison-source').value);
  const source=group||state, compared=group?.systems||systems;
  if(!viewingLive)$('#footer-status').textContent=group?'New protocol · results provisional':'Original controlled campaign verified';
  $('#export-results').hidden=Boolean(group);
  $('#comparison-note').textContent=group?'New protocol · matched configurations · small n is provisional':'8 repetitions per system · 95% confidence intervals';
  function stat(s,key){return source.stats.find(row=>row.system===s&&row.metric===key);}
  $('#comparison-charts').innerHTML=primary.map(([key,label,unit])=>{
    const maximum=Math.max(.01,...compared.map(s=>stat(s,key)?.mean||0));
    return `<section class="panel compare-chart"><h2>${label} <span class="secondary">${unit}</span></h2>${compared.map(s=>{const row=stat(s,key);return `<div class="bar-row ${s==='predictive-safe'||s==='predictive-ml'?'gb':''}" title="${row?`n=${row.n}; 95% CI ${number(row.low)} to ${number(row.high)}`:'Not available'}"><span>${escapeText(source.labels[s])}</span><div class="bar-track"><div class="bar-fill" data-width="${100*(row?.mean||0)/maximum}"></div></div><b>${number(row?.mean)}</b></div>`;}).join('')}<div class="chart-note">Mean across verified runs · hover for 95% interval</div></section>`;
  }).join('');
  document.querySelectorAll('[data-width]').forEach(e=>e.style.width=e.dataset.width+'%');
  $('#comparison-table').innerHTML=`<thead><tr><th>Metric</th>${compared.map(s=>`<th>${escapeText(source.labels[s])} <span class="secondary">n=${source.runs.filter(r=>r.system===s&&r.status==='verified').length}</span></th>`).join('')}</tr></thead><tbody>${source.metrics.map(([key,label,unit])=>`<tr><td>${escapeText(label)}${unit?' ('+escapeText(unit)+')':''}</td>${compared.map(s=>{const row=stat(s,key);return `<td title="95% CI ${number(row?.low)} to ${number(row?.high)}">${number(row?.mean)}${Number.isFinite(row?.sd)?' ± '+number(row.sd):''}</td>`;}).join('')}</tr>`).join('')}</tbody>`;
}
function showView(view){
  const compare=view==='comparison', previous=viewingLive;
  viewingLive=view==='live';
  $('#network-view').hidden=compare;$('#comparison-view').hidden=!compare;
  $('#network-view>.toolbar').hidden=viewingLive;$('#live-controls').hidden=!viewingLive;
  $('#view-title').textContent=compare?'System comparison':viewingLive?'Live network lab':'Network overview';
  document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  setPlaying(false);
  if(previous!==viewingLive){generation++;run=null;$('#network-data').hidden=true;error('');}
  if(viewingLive) refreshLive();
  else if(!compare && state && (previous||!run)) chooseSystem(system);
}
async function refreshLive(){
  if(liveRefreshing)return;
  liveRefreshing=true;
  const requestGeneration=generation;
  try{
    liveState=await api('/api/live');
    if(requestGeneration!==generation)return;
    const entries=liveState.sessions;
    const active=liveState.busy?entries.find(s=>['starting','running'].includes(s.status)):null;
    if(active){
      $('#live-system').value=active.system;
      $('#live-scenario').value=active.scenario||'legacy';
      $('#live-topology').value=active.topology||'diamond4';
    }
    activeLiveId=active?.id||'';
    if(!entries.some(s=>s.id===liveId))liveId=activeLiveId||entries[0]?.id||'';
    $('#live-session').replaceChildren(...(entries.length?entries.map(s=>new Option(`${new Date(s.created_at*1000).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})} · ${liveLabels[s.system]||state?.labels[s.system]||s.system} · ${s.scenario||'legacy'} · ${s.status}`,s.id)):[new Option('No experiments yet','')]));
    $('#live-session').value=liveId;
    $('#live-start').disabled=!liveState.available||liveState.busy||liveActionPending;
    $('#live-system').disabled=liveState.busy;
    $('#live-scenario').disabled=liveState.busy;$('#live-topology').disabled=liveState.busy;
    const selected=entries.find(s=>s.id===liveId);
    $('#live-source').textContent=selected?.status==='complete'?'Verified session replay':'Local Linux network';
    $('#live-stop').disabled=!activeLiveId||liveActionPending;
    $('#live-stop').textContent='Stop running experiment';
    $('#live-follow').hidden=!activeLiveId||activeLiveId===liveId;
    $('#live-status').textContent=!liveState.available?`Setup required: ${liveState.missing.join(', ')}`:selected?.message||'Ready · 90-second workload · about 3 minutes with startup';
    if(activeLiveId&&activeLiveId!==liveId)$('#live-status').textContent='An experiment is running. You are viewing an older session.';
    if(!viewingLive||!selected)return;
    if(['failed','cancelled','interrupted'].includes(selected.status)) {run=null;$('#network-data').hidden=true;return;}
    if(run?.id===liveId&&run.verified&&selected.status==='complete')return;
    const id=liveId,token=generation;
    const result=await api(`/api/live/run?id=${encodeURIComponent(id)}`);
    if(!viewingLive||token!==generation||id!==liveId)return;
    if(result.pending){$('#network-data').hidden=true;return;}
    renderRun(result);
    $('#live-source').textContent=result.live?'Live telemetry · unverified':'Verified session replay';
    if(!result.live&&selected.status==='complete')$('#live-status').textContent='Complete · quality checks passed · saved separately from campaign';
  }catch(e){if(viewingLive)error(e.message);}
  finally{liveRefreshing=false;}
}
async function liveAction(action,payload){
  if(liveActionPending)return;
  liveActionPending=true;
  $('#live-start').disabled=true;$('#live-stop').disabled=true;error('');
  try{
    if(!liveState)await refreshLive();
    const result=await api(`/api/live/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-SDN-Token':liveState.token},body:JSON.stringify(payload)});
    if(action==='start'){liveId=result.id;generation++;run=null;$('#network-data').hidden=true;}
    await refreshLive();
  }catch(e){error(e.message);}
  finally{liveActionPending=false;await refreshLive();}
}
document.querySelectorAll('[data-system]').forEach(b=>b.addEventListener('click',()=>chooseSystem(b.dataset.system)));
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.view)));
$('#run-select').addEventListener('change',()=>{runId=$('#run-select').value;loadRun();});
$('#play').addEventListener('click',()=>{if(run&&elapsed>=run.duration)seek(0);setPlaying(!playing);});
$('#scrubber').addEventListener('input',()=>{setPlaying(false);seek(Number($('#scrubber').value));});
$('#monitor').addEventListener('click',async()=>{const latest=state.runs.filter(r=>r.status==='running').at(-1);if(latest){runId=latest.id;await chooseSystem(latest.system);}});
$('#live-start').addEventListener('click',()=>liveAction('start',{system:$('#live-system').value,scenario:$('#live-scenario').value,topology:$('#live-topology').value}));
function validateControls(){
  if($('#live-scenario').value==='multi-flow')$('#live-topology').value='access6';
  if($('#live-scenario').value==='legacy'){
    $('#live-topology').value='diamond4';
    if(['predictive-safe','predictive-confidence','predictive-safe-only'].includes($('#live-system').value))$('#live-scenario').value='ramp';
  }
}
['#live-scenario','#live-system','#live-topology'].forEach(id=>$(id).addEventListener('change',validateControls));
$('#comparison-source').addEventListener('change',renderComparison);
$('#live-stop').addEventListener('click',()=>{if(activeLiveId)liveAction('stop',{id:activeLiveId});});
$('#live-follow').addEventListener('click',()=>{liveId=activeLiveId;generation++;run=null;setPlaying(false);$('#network-data').hidden=true;refreshLive();});
$('#live-session').addEventListener('change',()=>{liveId=$('#live-session').value;generation++;run=null;setPlaying(false);$('#network-data').hidden=true;refreshLive();});
showView('live');refreshState();setInterval(refreshState,10000);setInterval(()=>{if(viewingLive)refreshLive();},2000);requestAnimationFrame(frame);
// Progressive enhancement only; the dashboard does not require WebMCP.
if(document.modelContext?.registerTool){
  const lifecycle=new AbortController();
  Promise.resolve(document.modelContext.registerTool({
    name:'set_recorded_replay',
    description:'Select a captured SDN run and move its visible replay playhead. Does not modify the network.',
    inputSchema:{type:'object',properties:{system:{type:'string',enum:systems},run:{type:'integer',minimum:1,maximum:8},seconds:{type:'number',minimum:0,maximum:110}},required:['system','run','seconds'],additionalProperties:false},
    annotations:{readOnlyHint:false},
    async execute(input){
      if(!systems.includes(input.system)||!Number.isInteger(input.run)||input.run<1||input.run>8||!Number.isFinite(input.seconds)||input.seconds<0||input.seconds>110) throw new Error('Invalid replay selection');
      const id=`run-${String(input.run).padStart(2,'0')}`;
      if(!state?.runs.some(r=>r.system===input.system&&r.id===id&&r.status==='verified')) throw new Error('Run is not verified');
      showView('network');
      runId=id;
      await chooseSystem(input.system);
      if(!run || run.system!==input.system || run.id!==id || !run.verified) throw new Error('Replay could not be loaded');
      seek(input.seconds);
      return {system,run:runId,seconds:elapsed,source:'recorded replay'};
    }
  },{signal:lifecycle.signal})).catch(()=>{});
  window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
}
