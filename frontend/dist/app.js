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
