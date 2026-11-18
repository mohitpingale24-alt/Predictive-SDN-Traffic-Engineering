'use strict';
/* Anomaly-mitigation detail page. Fetches /api/anomaly and renders
   only what the payload contains: every detection is an
   anomaly_detected control event from the live captures, with kind,
   link, share, baseline and z parsed server-side from the recorded
   reason string. This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const fmt = (value, digits) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—';
};
const pct = value => value == null ? '—' : fmt(Number(value) * 100, 1) + '%';
const pretty = name => String(name || '').replaceAll('_', ' ');
const shortRun = id => id ? String(id).replace('run-', '') : '';

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function bars(entries, labelOf, valueOf, displayOf) {
  const max = Math.max(0.0001, ...entries.map(valueOf));
  return entries.map(entry =>
    `<div class="bar-row"><span>${escapeText(labelOf(entry))}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * valueOf(entry) / max))}%"></div></div><b>${escapeText(displayOf ? displayOf(entry) : num(valueOf(entry)))}</b></div>`).join('');
}

function breakdownTable(entries) {
  return `<thead><tr><th>Name</th><th>Detections</th><th>Share of detections</th></tr></thead><tbody>${
    entries.map(e => `<tr><td>${escapeText(pretty(e.name))}</td><td>${num(e.count)}</td><td>${pct(e.share)}</td></tr>`).join('')
    || '<tr><td colspan="3">No detections recorded</td></tr>'}</tbody>`;
}

function render(data) {
  const summary = data.summary || {};
  const kinds = Object.entries(summary.kinds || {}).sort((a, b) => b[1] - a[1]);
  const cards = [
    ['Detections in live runs', summary.detections, kinds.map(([k, v]) => pretty(k) + ' × ' + v).join(' · ') || 'None yet'],
    ['Runs with detections', summary.runs_with_detections, 'Of ' + num(summary.live_runs_total) + ' live session(s) on disk' + (summary.using_results_fallback ? ' · results-directory fallback in use' : '')],
    ['Links flagged', Object.keys(summary.links || {}).length, Object.entries(summary.links || {}).map(([k, v]) => k + ' × ' + v).join(' · ') || 'None'],
    ['Traffic share (mean)', summary.share ? fmt(summary.share.mean) : '—', 'Parsed from share= in each logged reason · n=' + num(summary.share ? summary.share.n : 0)],
    ['Baseline (mean)', summary.baseline_mbps ? fmt(summary.baseline_mbps.mean) + ' Mbps' : '—', 'The source baseline each verdict compared against'],
    ['Detections with a z-score', summary.with_z, 'Surges log z=; elephant flows log share instead — blanks stay blank'],
  ];
  $('#anomaly-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');

  $('#anomaly-status').textContent = summary.detections
    ? `${num(summary.detections)} detection(s) · ${num(summary.first)} → ${num(summary.last)}`
    : 'No anomaly detected in any live capture yet';
  $('#anomaly-status').classList.toggle('pending', !summary.detections);

  const latest = data.latest;
  $('#latest-chip').textContent = latest ? shortRun(latest.run) : '—';
  $('#latest-detection').innerHTML = latest ? detailRows([
    ['Run', latest.run], ['Recorded at', latest.time], ['Kind', pretty(latest.kind)],
    ['Link', latest.link || '—'], ['Active path', latest.active_path || '—'],
    ['Traffic share', latest.share == null ? '—' : fmt(latest.share)],
    ['Baseline', latest.baseline_mbps == null ? '—' : fmt(latest.baseline_mbps) + ' Mbps'],
    ['z-score', latest.z == null ? '(not logged for this kind)' : fmt(latest.z)],
    ['Logged reason', latest.reason || '—'],
  ]) : 'No anomaly_detected event exists in the live captures or the results directory yet. Run a live experiment with a surge or elephant workload and this page fills in from its capture.';

  const byKind = data.by_kind || [], byLink = data.by_link || [];
  $('#kind-bars').innerHTML = bars(byKind, e => pretty(e.name), e => e.count) || 'No detections recorded.';
  $('#kind-table').innerHTML = breakdownTable(byKind);
  $('#link-bars').innerHTML = bars(byLink, e => e.name, e => e.count) || 'No detections recorded.';
  $('#link-table').innerHTML = breakdownTable(byLink);

  const statRows = [['Traffic share', summary.share, ''], ['Baseline (Mbps)', summary.baseline_mbps, ''], ['z-score', summary.z, '']];
  $('#stats-table').innerHTML = `<thead><tr><th>Measurement (as logged)</th><th>n</th><th>Mean</th><th>Sample SD</th><th>Min</th><th>Max</th></tr></thead><tbody>${
    statRows.map(([label, s]) => `<tr><td>${escapeText(label)}</td><td>${num(s ? s.n : 0)}</td><td>${fmt(s && s.mean, 3)}</td><td>${fmt(s && s.sd, 3)}</td><td>${fmt(s && s.min, 3)}</td><td>${fmt(s && s.max, 3)}</td></tr>`).join('')}</tbody>`;

  const runs = data.by_run || [];
  $('#runs-count').textContent = `${runs.length} run(s) with detections`;
  $('#runs-table').innerHTML = `<thead><tr><th>Run</th><th>System</th><th>Scenario</th><th>Status</th><th>Detections</th><th>Kinds</th><th>Links</th><th>First</th><th>Last</th></tr></thead><tbody>${
    runs.map(r => `<tr><td>${escapeText(shortRun(r.run))}</td><td>${escapeText(r.system || '—')}</td><td>${escapeText(r.scenario || '—')}</td><td>${escapeText(r.status || '—')}</td><td>${num(r.detections)}</td><td>${escapeText(Object.entries(r.kinds || {}).map(([k, v]) => pretty(k) + ' × ' + v).join(' · ') || '—')}</td><td>${escapeText((r.links || []).join(' · ') || '—')}</td><td>${escapeText(r.first || '—')}</td><td>${escapeText(r.last || '—')}</td></tr>`).join('')
    || '<tr><td colspan="9">No runs recorded detections</td></tr>'}</tbody>`;

  const recent = data.recent || [];
  $('#recent-count').textContent = `Showing the latest ${recent.length} of ${num(summary.detections)} (limit ${num(data.recent_limit)})`;
  $('#recent-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Kind</th><th>Link</th><th>Share</th><th>Baseline</th><th>z</th><th>Logged reason</th></tr></thead><tbody>${
    recent.slice().reverse().map(d => `<tr><td>${escapeText(d.time || '—')}</td><td>${escapeText(shortRun(d.run))}</td><td>${escapeText(pretty(d.kind))}</td><td>${escapeText(d.link || '—')}</td><td>${d.share == null ? '—' : fmt(d.share)}</td><td>${d.baseline_mbps == null ? '—' : fmt(d.baseline_mbps) + ' Mbps'}</td><td>${d.z == null ? '—' : fmt(d.z)}</td><td class="event-reason">${escapeText(d.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="8">No detections recorded</td></tr>'}</tbody>`;

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>`
    : 'No control_events.csv file was found in any source layer.';
}

async function load() {
  try {
    const response = await fetch('/api/anomaly');
    if (!response.ok) throw new Error('Anomaly record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
