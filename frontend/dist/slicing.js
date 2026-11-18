'use strict';
/* Network-slicing detail page. Fetches /api/slicing and renders only
   what the payload contains: the slice table, host map and isolation
   matrix are the loaded SliceManager's answers at request time, and
   the recorded drops are aggregated server-side from
   control_events.csv (live captures + campaign-02). Labels only. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const shortRun = id => id ? String(id).replace('run-', '') : '';
const pct = value => value == null ? '—' : Math.round(value * 100) + '%';

function metricCards(summary) {
  const cards = [
    ['Slices loaded & validated', summary.slices, 'Isolation invariant checked by slicing.py at load'],
    ['Hosts assigned', summary.hosts, 'Each in at most one slice'],
    ['Capacity allocated', pct(summary.total_share), 'Sum of the slice bandwidth shares'],
    ['Unallocated', pct(summary.unallocated), 'Share no slice claims'],
    ['Host pairs decided', summary.pairs, 'Every ordered pair, via isolation_decision()'],
    ['Recorded cross-slice drops', summary.total, `Live ${num(summary.live)} · campaign ${num(summary.campaign)}`],
  ];
  $('#slicing-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function render(data) {
  const summary = data.summary || {};
  $('#slicing-status').textContent = data.available
    ? `${num(summary.slices)} slice(s) validated · ${num(summary.hosts)} host(s) · ${num(summary.total)} recorded drop(s)`
    : 'Slice config unavailable';
  $('#slicing-status').classList.toggle('pending', !data.available);
  metricCards(summary);
  $('#slices-chip').textContent = data.available ? `${pct(summary.total_share)} allocated` : '—';

  const slices = data.slices || [];
  const capacities = summary.capacities || [];
  const maxShare = Math.max(0.0001, ...slices.map(s => s.share || 0));
  $('#share-bars').innerHTML = slices.length
    ? slices.map(s => `<div class="bar-row"><span>${escapeText(s.slice_id)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * (s.share || 0) / maxShare))}%"></div></div><b>${pct(s.share)}</b></div>`).join('')
    : (data.available ? 'No slices in the loaded file.' : 'Slice config failed validation: ' + (data.error || 'unknown error') + ' — slicing.py refused the file, so no slice data is shown.');
  $('#slice-table').innerHTML = `<thead><tr><th>Slice</th><th>VLAN</th><th>Share</th><th>Hosts</th>${capacities.map(c => `<th>Bandwidth @${escapeText(c)} Mbps</th>`).join('')}</tr></thead><tbody>${
    slices.map(s => `<tr><td>${escapeText(s.slice_id)}</td><td>${num(s.vlan_id)}</td><td>${pct(s.share)}</td><td class="event-reason">${escapeText((s.hosts || []).join(', '))}</td>${capacities.map(c => `<td>${s.bandwidth_by_capacity && s.bandwidth_by_capacity[String(c)] != null ? Number(s.bandwidth_by_capacity[String(c)]).toFixed(1) + ' Mbps' : '—'}</td>`).join('')}</tr>`).join('')
    || `<tr><td colspan="${5 + capacities.length}">No slices loaded</td></tr>`}</tbody>`;

  const hostMap = data.host_map || [];
  $('#host-table').innerHTML = `<thead><tr><th>Host</th><th>Slice</th><th>VLAN</th></tr></thead><tbody>${
    hostMap.map(h => `<tr><td>${escapeText(h.host)}</td><td>${escapeText(h.slice_id || '—')}</td><td>${num(h.vlan_id)}</td></tr>`).join('')
    || '<tr><td colspan="3">No hosts assigned</td></tr>'}</tbody>`;

  const pairs = data.pairs || [];
  $('#pair-count').textContent = `${pairs.length} ordered pair(s) · ${num(summary.cross_pairs)} cross-slice · ${num(summary.deny_pairs)} denied`;
  $('#pair-table').innerHTML = `<thead><tr><th>Source host</th><th>Source slice</th><th>Destination host</th><th>Destination slice</th><th>Decision</th></tr></thead><tbody>${
    pairs.map(p => `<tr><td>${escapeText(p.src)}</td><td>${escapeText(p.src_slice || '—')}</td><td>${escapeText(p.dst)}</td><td>${escapeText(p.dst_slice || '—')}</td><td>${escapeText(p.decision)}</td></tr>`).join('')
    || '<tr><td colspan="5">No host pairs to decide</td></tr>'}</tbody>`;

  const events = data.events || [];
  $('#drops-count').textContent = `Showing the latest ${events.length} of ${num(data.events_total)} recorded drop(s) (limit ${num(data.recent_limit)})`;
  const byPair = Object.entries(summary.drops_by_slice_pair || {}).sort((a, b) => b[1] - a[1]);
  const maxDrop = Math.max(0.0001, ...byPair.map(([, v]) => v));
  $('#drop-bars').innerHTML = byPair.length
    ? byPair.map(([k, v]) => `<div class="bar-row"><span>${escapeText(k)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * v / maxDrop))}%"></div></div><b>${num(v)}</b></div>`).join('')
    : 'No slice_isolation_drop event has been recorded in any live capture, campaign run, or the results file. The workload flows are same-slice or involve unassigned hosts, so zero is the honest count — send traffic between an iot host and an office host in a live experiment and the drops appear here from its capture.';
  $('#drops-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Source</th><th>System</th><th>Hosts</th><th>Slices</th><th>Reason</th></tr></thead><tbody>${
    events.slice().reverse().map(ev => `<tr><td>${escapeText(ev.time || '—')}</td><td>${escapeText(shortRun(ev.run))}</td><td>${escapeText(ev.source)}</td><td>${escapeText(ev.system || '—')}</td><td>${escapeText(ev.src_host || '—')} → ${escapeText(ev.dst_host || '—')}</td><td>${escapeText(ev.src_slice || '?')} → ${escapeText(ev.dst_slice || '?')}</td><td class="event-reason">${escapeText(ev.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="7">No recorded drops</td></tr>'}</tbody>`;

  const wiring = data.wiring || {};
  const wiringRows = Object.entries(wiring).sort().map(([k, v]) => [k, v]);
  $('#slicing-wiring').innerHTML = wiringRows.length
    ? detailRows(wiringRows)
    : 'experiments/live_guest.py sets no SDN feature switches, or the file was not found.';
  renderSources(data);
}

function renderSources(data) {
  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>`
    : 'No source files were read.';
}

async function load() {
  try {
    const response = await fetch('/api/slicing');
    if (!response.ok) throw new Error('Slicing record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
