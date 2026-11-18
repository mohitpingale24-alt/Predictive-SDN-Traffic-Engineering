'use strict';
/* Failure-recovery detail page. Fetches /api/recovery and renders
   only what the payload contains: transitions grouped server-side
   from every recorded transitions.csv, failures and link events
   from the recorded control events / failures.csv, and campaign-02
   reroutes. This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const fmt = (value, digits) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—';
};
const pretty = name => String(name || '').replaceAll('_', ' ');
const shortRun = id => id ? String(id).replace('run-', '') : '';
const labels = { static: 'Static', reactive: 'Reactive', 'predictive-baseline': 'Persistence', 'predictive-ml': 'Predictive GB' };

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function bars(entries, labelOf, valueOf, displayOf) {
  const max = Math.max(0.0001, ...entries.map(valueOf));
  return entries.map(entry =>
    `<div class="bar-row"><span>${escapeText(labelOf(entry))}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * valueOf(entry) / max))}%"></div></div><b>${escapeText(displayOf ? displayOf(entry) : num(valueOf(entry)))}</b></div>`).join('');
}

function render(data) {
  const summary = data.summary || {};
  const dur = summary.duration || {};
  const cards = [
    ['Transitions completed', summary.completed, 'Make-before-break transactions with a recorded completion'],
    ['Transitions failed', summary.failed, num(summary.incomplete) + ' incomplete (started, never completed or failed)'],
    ['Mean transition time', dur.mean == null ? '—' : fmt(dur.mean) + ' s', 'Start → complete per transaction · min ' + fmt(dur.min) + ' s · max ' + fmt(dur.max) + ' s · n=' + num(dur.n)],
    ['Stage rows recorded', summary.transition_rows, 'Across ' + num(summary.runs_with_transitions) + ' run(s) with transitions.csv'],
    ['Link-down events', summary.link_downs, num(summary.link_ups) + ' link-up event(s) · ' + num(summary.failure_rows) + ' failures.csv row(s)'],
    ['Reroutes recorded', summary.reroutes_total, 'Live ' + num(summary.reroutes_live) + ' · campaign ' + num(summary.reroutes_campaign)],
  ];
  $('#recovery-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');

  $('#recovery-status').textContent = summary.completed
    ? `${num(summary.completed)} transition(s) completed · ${num(summary.failed)} failed · mean ${fmt(dur.mean)} s`
    : 'No completed transition recorded yet';
  $('#recovery-status').classList.toggle('pending', !summary.completed);

  const transactions = data.transactions || [];
  const latest = transactions.filter(t => t.outcome === 'complete').at(-1) || transactions.at(-1);
  $('#latest-chip').textContent = latest ? shortRun(latest.run) : '—';
  $('#latest-transition').innerHTML = latest ? detailRows([
    ['Run', latest.run], ['Source', latest.source], ['Transition', latest.transition_id],
    ['Path change', (latest.old_path || '—') + ' → ' + (latest.new_path || '—')],
    ['Outcome', latest.outcome], ['Started', latest.started_at || '—'],
    ['Completed', latest.completed_at || '—'],
    ['Duration', latest.duration_seconds == null ? '—' : fmt(latest.duration_seconds, 3) + ' s'],
    ['Stage rows', latest.stage_rows], ['Stages confirmed', latest.stages_confirmed],
    ['Stage sequence', latest.stages.map(s => s.stage + '/' + s.status).join(' · ')],
  ]) : 'No transitions.csv row exists in any live capture or campaign run yet. Start a live experiment that reroutes and this page fills in from its capture.';

  $('#transactions-count').textContent = `${transactions.length} transaction(s)`;
  $('#transactions-table').innerHTML = `<thead><tr><th>Started</th><th>Run</th><th>Source</th><th>ID</th><th>Path</th><th>Outcome</th><th>Duration</th><th>Stage rows</th><th>Stages confirmed</th><th>Stage sequence</th></tr></thead><tbody>${
    transactions.slice().reverse().map(t => `<tr><td>${escapeText(t.started_at || '—')}</td><td>${escapeText(shortRun(t.run))}</td><td>${escapeText(t.source)}</td><td>${escapeText(num(t.transition_id))}</td><td>${escapeText((t.old_path || '—') + ' → ' + (t.new_path || '—'))}</td><td>${escapeText(t.outcome)}</td><td>${t.duration_seconds == null ? '—' : fmt(t.duration_seconds, 3) + ' s'}</td><td>${num(t.stage_rows)}</td><td>${num(t.stages_confirmed)}</td><td class="event-reason">${escapeText(t.stages.map(s => s.stage + '/' + s.status).join(' · '))}</td></tr>`).join('')
    || '<tr><td colspan="10">No transitions recorded</td></tr>'}</tbody>`;

  const stageEntries = Object.entries(data.stage_counts || {}).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
  $('#stage-bars').innerHTML = bars(stageEntries, e => e.name, e => e.count) || 'No stage rows recorded.';
  $('#stage-table').innerHTML = `<thead><tr><th>Stage / status</th><th>Rows</th></tr></thead><tbody>${
    stageEntries.map(e => `<tr><td>${escapeText(e.name)}</td><td>${num(e.count)}</td></tr>`).join('')
    || '<tr><td colspan="2">No stage rows recorded</td></tr>'}</tbody>`;

  const systems = Object.entries(summary.reroutes_by_system || {}).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
  $('#system-bars').innerHTML = bars(systems, e => labels[e.name] || e.name, e => e.count) || 'No campaign reroutes recorded.';
  $('#system-table').innerHTML = `<thead><tr><th>System</th><th>Recorded reroutes (campaign-02)</th></tr></thead><tbody>${
    systems.map(e => `<tr><td>${escapeText(labels[e.name] || e.name)}</td><td>${num(e.count)}</td></tr>`).join('')
    || '<tr><td colspan="2">No campaign reroutes recorded</td></tr>'}</tbody>`;
  const runs = data.reroutes_by_run || [];
  $('#run-table').innerHTML = `<thead><tr><th>Campaign run</th><th>System</th><th>Scenario</th><th>Status</th><th>Reroutes</th></tr></thead><tbody>${
    runs.map(r => `<tr><td>${escapeText(shortRun(r.run))}</td><td>${escapeText(labels[r.system] || r.system || '—')}</td><td>${escapeText(r.scenario || '—')}</td><td>${escapeText(r.status || '—')}</td><td>${num(r.reroutes)}</td></tr>`).join('')
    || '<tr><td colspan="5">No campaign runs recorded</td></tr>'}</tbody>`;

  const linkEvents = data.link_events || [];
  $('#link-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Source</th><th>Event</th><th>Mode</th><th>Path</th><th>Reason</th></tr></thead><tbody>${
    linkEvents.map(ev => `<tr><td>${escapeText(ev.time || '—')}</td><td>${escapeText(shortRun(ev.run))}</td><td>${escapeText(ev.source)}</td><td>${escapeText(ev.event)}</td><td>${escapeText(ev.control_mode || '—')}</td><td>${escapeText((ev.old_path || '—') + ' → ' + (ev.new_path || '—'))}</td><td class="event-reason">${escapeText(ev.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="7">No link_down or link_up event has been recorded by any run yet — congestion reroutes above are not relabelled as failures.</td></tr>'}</tbody>`;
  const failureRows = data.failure_rows_recent || [];
  if (failureRows.length) {
    const keys = [];
    failureRows.forEach(r => Object.keys(r).forEach(k => { if (!keys.includes(k)) keys.push(k); }));
    const ordered = ['time', 'run', 'source', 'system', ...keys.filter(k => !['time', 'run', 'source', 'system'].includes(k))];
    $('#failure-rows-table').innerHTML = `<thead><tr>${ordered.map(k => `<th>${escapeText(pretty(k))}</th>`).join('')}</tr></thead><tbody>${
      failureRows.slice().reverse().map(r => `<tr>${ordered.map(k => `<td>${escapeText(typeof r[k] === 'number' ? fmt(r[k], 4) : num(r[k]))}</td>`).join('')}</tr>`).join('')}</tbody>`;
  } else {
    $('#failure-rows-table').innerHTML = '<tbody><tr><td>No failures.csv row has been recorded by any run yet.</td></tr></tbody>';
  }

  const recent = data.recent_reroutes || [];
  $('#recent-count').textContent = `Showing the latest ${recent.length} of ${num(summary.reroutes_total)} (limit ${num(data.recent_limit)})`;
  $('#recent-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Source</th><th>System</th><th>Path change</th><th>Signal A</th><th>Signal B</th><th>Reason</th></tr></thead><tbody>${
    recent.slice().reverse().map(ev => `<tr><td>${escapeText(ev.time || '—')}</td><td>${escapeText(shortRun(ev.run))}</td><td>${escapeText(ev.source)}</td><td>${escapeText(labels[ev.system] || ev.system || '—')}</td><td>${escapeText((ev.old_path || '—') + ' → ' + (ev.new_path || '—'))}</td><td>${fmt(ev.signal_a)}</td><td>${fmt(ev.signal_b)}</td><td class="event-reason">${escapeText(ev.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="8">No reroutes recorded</td></tr>'}</tbody>`;

  const metrics = data.transition_metrics || [];
  $('#metrics-list').innerHTML = metrics.length
    ? metrics.map(m => `<div class="detail-body"><b>${escapeText(shortRun(m.run))}</b> (${escapeText(m.source)})<pre>${escapeText(JSON.stringify(m.data, null, 2))}</pre></div>`).join('')
    : 'No transition_metrics.json artifact was recorded with any capture.';

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>`
    : 'No transitions, failures or control-event file was found in any source layer.';
}

async function load() {
  try {
    const response = await fetch('/api/recovery');
    if (!response.ok) throw new Error('Recovery record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
