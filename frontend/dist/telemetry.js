'use strict';
/* All-link telemetry detail page. Fetches /api/telemetry and renders
   only what the payload contains: aggregates computed server-side
   from telemetry_all_links.csv across all live captures. This file
   holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const fmt = (value, digits) => { const n = Number(value); return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—'; };
const pct1 = value => value == null ? '—' : fmt(Number(value) * 100, 1) + '%';
const shortRun = id => id ? String(id).replace('run-', '') : '';

function metricCards(data) {
  const s = data.summary || {}, o = s.overall_utilization || {};
  const cards = [
    ['Telemetry rows recorded', s.rows, (s.runs_contributing || 0) + ' live run(s) contributing'],
    ['Snapshots (distinct timestamps)', s.snapshots, 'One snapshot records every core link'],
    ['Links recorded', s.distinct_links, (data.per_link || []).map(p => p.link).join(' · ') || '—'],
    ['Mean utilization (all links)', o.mean == null ? '—' : fmt(o.mean, 1) + '%', 'Over ' + (o.n != null ? o.n : '—') + ' recorded rows'],
    ['Peak utilization recorded', o.max == null ? '—' : fmt(o.max, 1) + '%', 'Minimum recorded ' + fmt(o.min, 1) + '%'],
    ['Recording span', s.duration_seconds == null ? '—' : fmt(s.duration_seconds, 0) + ' s', (s.first || '—') + ' → ' + (s.last || '—')],
  ];
  $('#telemetry-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function bars(items, unit) {
  const max = Math.max(0.0001, ...items.map(([, v]) => Number(v) || 0));
  return items.map(([label, value]) =>
    `<div class="bar-row"><span>${escapeText(label)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * (Number(value) || 0) / max))}%"></div></div><b>${escapeText(fmt(Number(value), 1) + (unit || ''))}</b></div>`).join('');
}

function render(data) {
  const s = data.summary || {};
  metricCards(data);
  $('#telemetry-status').textContent = s.rows
    ? `${num(s.rows)} row(s) across ${num(s.distinct_links)} link(s)`
    : 'No all-link telemetry recorded yet';
  $('#telemetry-status').classList.toggle('pending', !s.rows);

  const links = data.per_link || [];
  $('#latest-chip').textContent = links.length && links[0].latest ? (links[0].latest.time || '—') : '—';
  $('#latest-bars').innerHTML = bars(links.map(p => [p.link + ' latest', (p.latest || {}).utilization]), '%')
    || 'No all-link capture yet.';
  $('#latest-body').innerHTML = links.length ? detailRows(
    links.map(p => [p.link + ' latest', fmt((p.latest || {}).utilization, 2) + '% · throughput ' + fmt((p.latest || {}).throughput, 3) + ' · ' + ((p.latest || {}).time || '—')]))
    : 'No telemetry_all_links.csv rows exist in any live capture yet. Start a live experiment with the current controller and this page fills in.';

  $('#link-bars').innerHTML = bars(links.map(p => [p.link + ' peak', (p.utilization || {}).max]), '%')
    || 'No all-link telemetry recorded.';
  $('#link-table').innerHTML = `<thead><tr><th>Link</th><th>Rows</th><th>Mean util</th><th>SD</th><th>Min</th><th>Peak util</th><th>Peak at</th><th>Latest util</th><th>Mean throughput</th><th>Max packet loss</th><th>Max active flows</th></tr></thead><tbody>${
    links.map(p => `<tr><td>${escapeText(p.link)}</td><td>${num((p.utilization || {}).n)}</td><td>${fmt((p.utilization || {}).mean, 1)}%</td><td>${fmt((p.utilization || {}).sd, 1)}</td><td>${fmt((p.utilization || {}).min, 1)}%</td><td>${fmt((p.utilization || {}).max, 1)}%</td><td>${escapeText(p.peak_time || '—')}</td><td>${fmt((p.latest || {}).utilization, 2)}%</td><td>${fmt((p.throughput || {}).mean, 3)}</td><td>${fmt((p.packet_loss || {}).max, 3)}</td><td>${fmt((p.active_flows || {}).max, 0)}</td></tr>`).join('')
    || '<tr><td colspan="11">No all-link telemetry recorded</td></tr>'}</tbody>`;

  $('#band-table').innerHTML = `<thead><tr><th>Link</th><th>Idle (0%)</th><th>Low (&gt;0–50%)</th><th>Mid (50–85%)</th><th>High (≥85%)</th><th>High share</th></tr></thead><tbody>${
    links.map(p => { const b = p.bands || {}, sh = p.band_shares || {};
      return `<tr><td>${escapeText(p.link)}</td><td>${num(b.idle)} · ${pct1(sh.idle)}</td><td>${num(b.low)} · ${pct1(sh.low)}</td><td>${num(b.mid)} · ${pct1(sh.mid)}</td><td>${num(b.high)} · ${pct1(sh.high)}</td><td>${pct1(sh.high)}</td></tr>`; }).join('')
    || '<tr><td colspan="6">No all-link telemetry recorded</td></tr>'}</tbody>`;

  const runs = data.runs_detail || [];
  $('#runs-count').textContent = `${runs.length} run(s) with telemetry`;
  $('#runs-table').innerHTML = `<thead><tr><th>Run</th><th>System</th><th>Status</th><th>Rows</th><th>Snapshots</th><th>Links</th><th>Mean util</th><th>Peak util</th><th>First</th><th>Last</th><th>Duration</th></tr></thead><tbody>${
    runs.map(r => `<tr><td>${escapeText(shortRun(r.id))}</td><td>${escapeText(r.system || '—')}</td><td>${escapeText(r.status || '—')}</td><td>${num(r.rows)}</td><td>${num(r.snapshots)}</td><td>${escapeText((r.links || []).join(', ') || '—')}</td><td>${fmt(r.mean_utilization, 1)}%</td><td>${fmt(r.peak_utilization, 1)}%</td><td>${escapeText(r.first || '—')}</td><td>${escapeText(r.last || '—')}</td><td>${r.duration_seconds == null ? '—' : num(r.duration_seconds) + ' s'}</td></tr>`).join('')
    || '<tr><td colspan="11">No runs recorded telemetry</td></tr>'}</tbody>`;

  const recent = data.recent || [];
  $('#recent-count').textContent = `Showing the latest ${recent.length} of ${num(s.rows)} (limit ${num(data.recent_limit)})`;
  $('#recent-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Link</th><th>Utilization</th><th>Throughput</th><th>Packet loss</th><th>Active flows</th></tr></thead><tbody>${
    recent.slice().reverse().map(r => `<tr><td>${escapeText(r.time || '—')}</td><td>${escapeText(shortRun(r.run))}</td><td>${escapeText(r.link_id || '—')}</td><td>${fmt(r.utilization, 2)}%</td><td>${fmt(r.throughput, 3)}</td><td>${fmt(r.packet_loss, 3)}</td><td>${fmt(r.active_flows, 0)}</td></tr>`).join('')
    || '<tr><td colspan="7">No all-link telemetry recorded</td></tr>'}</tbody>`;

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(x => `<div>${escapeText(x)}</div>`).join('')}</div>`
    : 'No telemetry_all_links.csv file was found in any live capture.';
}

async function load() {
  try {
    const response = await fetch('/api/telemetry');
    if (!response.ok) throw new Error('Telemetry record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
