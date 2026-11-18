'use strict';
/* Conformal-intervals detail page. Fetches /api/conformal and renders
   only what the payload contains: the calibration artifact as stored
   plus live aggregates computed server-side from confidence.csv
   across all live captures. This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const fmt = (value, digits) => { const n = Number(value); return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—'; };
const pct = value => value == null ? '—' : fmt(Number(value) * 100, 1) + '%';
const shortRun = id => id ? String(id).replace('run-', '') : '';

function metricCards(data) {
  const s = data.summary || {}, a = data.artifact || {};
  const cards = [
    ['Nominal coverage (artifact)', a.nominal_coverage == null ? '—' : pct(a.nominal_coverage), 'As stored in conformal_calibration.json'],
    ['Conformal radius', a.radius_percentage_points == null ? '—' : fmt(a.radius_percentage_points) + ' pts', 'Coverage quantile of calibration residuals'],
    ['Held-out coverage', a.heldout_coverage == null ? '—' : pct(a.heldout_coverage), (a.heldout_n != null ? a.heldout_n + ' held-out rows the radius never saw · ' : '') + 'mean width ' + fmt(a.heldout_mean_width) + ' pts'],
    ['Live intervals recorded', s.intervals, (s.runs_contributing || 0) + ' run(s) contributing confidence.csv'],
    ['Gate held (too uncertain)', s.gate_hold, 'Share ' + pct(s.gate_hold != null && s.intervals ? s.gate_hold / s.intervals : null) + ' of live intervals'],
    ['Gate passed', s.gate_pass, 'Share ' + pct(s.gate_pass_share) + ' of live intervals'],
  ];
  $('#conformal-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function bars(items, unit) {
  const max = Math.max(0.0001, ...items.map(([, v]) => Number(v) || 0));
  return items.map(([label, value]) =>
    `<div class="bar-row"><span>${escapeText(label)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * (Number(value) || 0) / max))}%"></div></div><b>${escapeText(fmt(Number(value), Number(value) >= 100 ? 0 : 2) + (unit || ''))}</b></div>`).join('');
}

function statsRows(stats) {
  return Object.entries(stats || {}).map(([name, b]) =>
    `<tr><td>${escapeText(name)}</td><td>${num(b.n)}</td><td>${fmt(b.mean)}</td><td>${fmt(b.sd)}</td><td>${fmt(b.min)}</td><td>${fmt(b.max)}</td></tr>`).join('');
}

function render(data) {
  const s = data.summary || {}, a = data.artifact;
  metricCards(data);
  $('#conformal-status').textContent = a
    ? `Artifact loaded · ${num(s.intervals)} live interval(s) recorded`
    : 'No calibration artifact found';
  $('#conformal-status').classList.toggle('pending', !a);

  $('#artifact-chip').textContent = a ? (a.method || 'artifact') : '—';
  if (a) {
    const prov = a.provenance || {}, scores = a.aci_scores || {};
    $('#artifact-body').innerHTML = detailRows([
      ['Method', a.method || '—'], ['Alpha', fmt(a.alpha, 4)],
      ['Nominal coverage', pct(a.nominal_coverage)],
      ['Radius', a.radius_percentage_points == null ? '—' : fmt(a.radius_percentage_points) + ' pts'],
      ['Calibration rows (artifact)', a.calibration_rows != null ? a.calibration_rows : '—'],
      ['Held-out coverage', pct(a.heldout_coverage)],
      ['Held-out mean width', a.heldout_mean_width == null ? '—' : fmt(a.heldout_mean_width) + ' pts'],
      ['ACI held-out coverage (sequential)', pct(a.aci_heldout_coverage)],
      ['Eval-capture coverage', pct(a.eval_capture_coverage)],
      ['Eval-capture rows', a.eval_capture_n != null ? a.eval_capture_n : '—'],
      ['ACI gamma', fmt(a.aci_gamma, 4)],
      ['ACI initial radius', a.aci_initial_radius == null ? '—' : fmt(a.aci_initial_radius) + ' pts'],
      ['ACI residual scores stored', scores.n != null ? scores.n : '—'],
      ['ACI scores mean / max', scores.n ? fmt(scores.mean) + ' / ' + fmt(scores.max) : '—'],
      ['Calibrated on capture', prov.capture || '—'],
      ['Horizon', prov.horizon_seconds != null ? prov.horizon_seconds + ' s' : '—'],
      ['Pairing', prov.pairing || '—'],
    ]);
  } else {
    $('#artifact-body').textContent = 'No conformal_calibration.json exists yet. Run experiments/calibrate_conformal.py on a recorded capture to build it.';
  }

  const reasons = data.by_gate_reason || [];
  $('#reason-bars').innerHTML = bars(reasons.map(r => [String(r.reason).replaceAll('_', ' '), r.count]), '')
    || 'No live intervals recorded.';
  $('#reason-table').innerHTML = `<thead><tr><th>Gate reason (as logged)</th><th>Intervals</th><th>Share</th><th>Gate passed</th><th>Mean width</th></tr></thead><tbody>${
    reasons.map(r => `<tr><td>${escapeText(r.reason)}</td><td>${num(r.count)}</td><td>${pct(r.share)}</td><td>${num(r.gate_pass)}</td><td>${fmt(r.mean_width)}</td></tr>`).join('')
    || '<tr><td colspan="5">No live intervals recorded</td></tr>'}</tbody>`;

  const paths = data.by_path || [];
  $('#path-bars').innerHTML = bars(paths.map(p => ['Path ' + p.path + ' mean width', (p.width || {}).mean]), ' pts')
    || 'No live intervals recorded.';
  $('#path-table').innerHTML = `<thead><tr><th>Path</th><th>Intervals</th><th>Share</th><th>Gate passed</th><th>Gate held</th><th>Mean width</th><th>Width SD</th><th>Width min</th><th>Width max</th><th>Mean prediction</th></tr></thead><tbody>${
    paths.map(p => `<tr><td>Path ${escapeText(p.path)}</td><td>${num(p.count)}</td><td>${pct(p.share)}</td><td>${num(p.gate_pass)}</td><td>${num(p.gate_hold)}</td><td>${fmt((p.width || {}).mean)}</td><td>${fmt((p.width || {}).sd)}</td><td>${fmt((p.width || {}).min)}</td><td>${fmt((p.width || {}).max)}</td><td>${fmt((p.prediction || {}).mean)}</td></tr>`).join('')
    || '<tr><td colspan="10">No live intervals recorded</td></tr>'}</tbody>`;

  $('#stats-table').innerHTML = `<thead><tr><th>Quantity</th><th>n</th><th>Mean</th><th>SD</th><th>Min</th><th>Max</th></tr></thead><tbody>${
    statsRows(data.interval_stats) || '<tr><td colspan="6">No live intervals recorded</td></tr>'}</tbody>`;

  const latest = data.latest;
  $('#latest-chip').textContent = latest ? shortRun(latest.run) : '—';
  $('#latest-body').innerHTML = latest ? detailRows([
    ['Run', latest.run], ['Source', latest.source], ['Time', latest.time || '—'],
    ['Path', latest.path || '—'], ['Prediction', fmt(latest.prediction)],
    ['Interval', (latest.lower == null ? '—' : fmt(latest.lower)) + ' → ' + (latest.upper == null ? '—' : fmt(latest.upper))],
    ['Width', fmt(latest.width)], ['Nominal coverage', pct(latest.nominal_coverage)],
    ['In domain', latest.in_domain || '—'], ['Gate pass', latest.confidence_gate_pass || '—'],
    ['Gate reason', latest.gate_reason || '—'],
  ]) : 'No confidence.csv rows exist in any live capture yet. Start a live experiment with the confidence-aware controller and this page fills in.';

  const runs = data.runs_detail || [];
  $('#runs-count').textContent = `${runs.length} run(s) with intervals`;
  $('#runs-table').innerHTML = `<thead><tr><th>Run</th><th>Source</th><th>System</th><th>Status</th><th>Intervals</th><th>Gate passed</th><th>Gate held</th><th>Mean width</th><th>Paths</th><th>First</th><th>Last</th><th>Duration</th></tr></thead><tbody>${
    runs.map(r => `<tr><td>${escapeText(shortRun(r.id))}</td><td>${escapeText(r.source)}</td><td>${escapeText(r.system || '—')}</td><td>${escapeText(r.status || '—')}</td><td>${num(r.intervals)}</td><td>${num(r.gate_pass)}</td><td>${num(r.gate_hold)}</td><td>${fmt(r.mean_width)}</td><td>${escapeText((r.paths || []).join(', ') || '—')}</td><td>${escapeText(r.first || '—')}</td><td>${escapeText(r.last || '—')}</td><td>${r.duration_seconds == null ? '—' : num(r.duration_seconds) + ' s'}</td></tr>`).join('')
    || '<tr><td colspan="12">No runs recorded intervals</td></tr>'}</tbody>`;

  const recent = data.recent || [];
  $('#recent-count').textContent = `Showing the latest ${recent.length} of ${num(s.intervals)} (limit ${num(data.recent_limit)})`;
  $('#recent-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Path</th><th>Prediction</th><th>Lower</th><th>Upper</th><th>Width</th><th>In domain</th><th>Gate</th><th>Gate reason</th></tr></thead><tbody>${
    recent.slice().reverse().map(r => `<tr><td>${escapeText(r.time || '—')}</td><td>${escapeText(shortRun(r.run))}</td><td>${escapeText(r.path || '—')}</td><td>${fmt(r.prediction)}</td><td>${fmt(r.lower)}</td><td>${fmt(r.upper)}</td><td>${fmt(r.width)}</td><td>${escapeText(r.in_domain || '—')}</td><td>${escapeText(r.confidence_gate_pass || '—')}</td><td class="event-reason">${escapeText(r.gate_reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="10">No live intervals recorded</td></tr>'}</tbody>`;

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(x => `<div>${escapeText(x)}</div>`).join('')}</div>`
    : 'No conformal artifact or confidence.csv file was found in any source layer.';
}

async function load() {
  try {
    const response = await fetch('/api/conformal');
    if (!response.ok) throw new Error('Conformal record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
