'use strict';
/* Per-link latency detail page. Fetches /api/latency and renders only
   what the payload contains: aggregates computed server-side from
   latency.csv across all live captures. This file holds labels,
   no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const fmt = (value, digits) => { const n = Number(value); return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—'; };
const shortRun = id => id ? String(id).replace('run-', '') : '';

function metricCards(data) {
  const s = data.summary || {}, o = s.overall || {};
  const cards = [
    ['Latency samples recorded', s.samples, (s.runs_contributing || 0) + ' run(s) contributing latency.csv'],
    ['Links with estimates', s.distinct_links, (data.per_link || []).map(p => p.link).join(' · ') || '—'],
    ['Mean latency (all links)', o.mean == null ? '—' : fmt(o.mean) + ' ms', 'Over ' + (o.n != null ? o.n : '—') + ' recorded samples'],
    ['Peak latency recorded', o.max == null ? '—' : fmt(o.max) + ' ms', 'Minimum recorded ' + fmt(o.min) + ' ms'],
    ['Per-link series identical', s.links_identical_series == null ? '—' : (s.links_identical_series ? 'Yes' : 'No'), s.links_identical_series ? 'Fingerprint of equal-split path-RTT attribution' : 'Links recorded distinct series'],
    ['Recording span', s.duration_seconds == null ? '—' : fmt(s.duration_seconds, 0) + ' s', (s.first || '—') + ' → ' + (s.last || '—')],
  ];
  $('#latency-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function bars(items, unit) {
  const max = Math.max(0.0001, ...items.map(([, v]) => Number(v) || 0));
  return items.map(([label, value]) =>
    `<div class="bar-row"><span>${escapeText(label)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * (Number(value) || 0) / max))}%"></div></div><b>${escapeText(fmt(Number(value)) + (unit || ''))}</b></div>`).join('');
}

function render(data) {
  const s = data.summary || {};
  metricCards(data);
  $('#latency-status').textContent = s.samples
    ? `${num(s.samples)} sample(s) across ${num(s.distinct_links)} link(s)`
    : 'No latency estimates recorded yet';
  $('#latency-status').classList.toggle('pending', !s.samples);

  const latest = data.latest_by_link || [];
  $('#latest-chip').textContent = latest.length ? (latest[0].time || '—') : '—';
  $('#latest-body').innerHTML = latest.length ? detailRows(
    latest.map(l => [l.link + ' latest', fmt(l.latency_ms) + ' ms · ' + (l.time || '—')])
      .concat([['All links share one series', s.links_identical_series ? 'Yes — equal-split attribution fingerprint' : 'No']]))
    : 'No latency.csv rows exist in any live capture yet. Start a live experiment and this page fills in from its capture.';

  const links = data.per_link || [];
  $('#link-bars').innerHTML = bars(links.map(p => [p.link + ' mean', (p.stats || {}).mean]), ' ms')
    || 'No latency estimates recorded.';
  $('#link-table').innerHTML = `<thead><tr><th>Link</th><th>Samples</th><th>Mean</th><th>SD</th><th>Min</th><th>Max</th><th>First estimate</th><th>Latest estimate</th><th>First at</th><th>Last at</th></tr></thead><tbody>${
    links.map(p => `<tr><td>${escapeText(p.link)}</td><td>${num((p.stats || {}).n)}</td><td>${fmt((p.stats || {}).mean)}</td><td>${fmt((p.stats || {}).sd)}</td><td>${fmt((p.stats || {}).min)}</td><td>${fmt((p.stats || {}).max)}</td><td>${fmt(p.first_value)}</td><td>${fmt(p.latest_value)}</td><td>${escapeText(p.first || '—')}</td><td>${escapeText(p.last || '—')}</td></tr>`).join('')
    || '<tr><td colspan="10">No latency estimates recorded</td></tr>'}</tbody>`;

  const runs = data.runs_detail || [];
  $('#runs-count').textContent = `${runs.length} run(s) with latency`;
  $('#runs-table').innerHTML = `<thead><tr><th>Run</th><th>Source</th><th>System</th><th>Status</th><th>Samples</th><th>Links</th><th>Mean latency</th><th>Peak latency</th><th>First</th><th>Last</th><th>Duration</th></tr></thead><tbody>${
    runs.map(r => `<tr><td>${escapeText(shortRun(r.id))}</td><td>${escapeText(r.source)}</td><td>${escapeText(r.system || '—')}</td><td>${escapeText(r.status || '—')}</td><td>${num(r.samples)}</td><td>${escapeText((r.links || []).join(', ') || '—')}</td><td>${fmt(r.mean_latency)}</td><td>${fmt(r.peak_latency)}</td><td>${escapeText(r.first || '—')}</td><td>${escapeText(r.last || '—')}</td><td>${r.duration_seconds == null ? '—' : num(r.duration_seconds) + ' s'}</td></tr>`).join('')
    || '<tr><td colspan="11">No runs recorded latency</td></tr>'}</tbody>`;

  const recent = data.recent || [];
  $('#recent-count').textContent = `Showing the latest ${recent.length} of ${num(s.samples)} (limit ${num(data.recent_limit)})`;
  $('#recent-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Link</th><th>Latency</th></tr></thead><tbody>${
    recent.slice().reverse().map(r => `<tr><td>${escapeText(r.time || '—')}</td><td>${escapeText(shortRun(r.run))}</td><td>${escapeText(r.link_id || '—')}</td><td>${fmt(r.latency_ms)} ms</td></tr>`).join('')
    || '<tr><td colspan="4">No latency estimates recorded</td></tr>'}</tbody>`;

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(x => `<div>${escapeText(x)}</div>`).join('')}</div>`
    : 'No latency.csv file was found in any source layer.';
}

async function load() {
  try {
    const response = await fetch('/api/latency');
    if (!response.ok) throw new Error('Latency record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
