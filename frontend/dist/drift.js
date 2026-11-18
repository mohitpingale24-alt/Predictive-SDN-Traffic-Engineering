'use strict';
/* Drift-monitoring detail page. Fetches /api/drift and renders only
   what the payload contains: detector firings from the recorded
   control events, drift.csv window rows, the recalibration artifact,
   and the campaign-02 forecast-error halves — all computed
   server-side at request time. This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const fmt = (value, digits) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—';
};
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

function render(data) {
  const summary = data.summary || {};
  const fc = data.forecast;
  const detectors = Object.entries(summary.by_detector || {});
  const cards = [
    ['Detector firings (live runs)', summary.firings, `Live ${num(summary.live_firings)} · campaign ${num(summary.campaign_firings)} · across ${num(summary.runs_with_firings)} run(s)`],
    ['Detectors that fired', detectors.length, detectors.map(([k, v]) => pretty(k) + ' × ' + v).join(' · ') || 'None yet'],
    ['Forecast pairs checked', summary.forecast_pairs, 'Campaign-02 predictions paired with telemetry at +5 s'],
    ['Forecast RMSE · 1st → 2nd half', fc ? fmt(fc.rmse_first) + ' → ' + fmt(fc.rmse_second) + ' pts' : '—', fc ? 'MAE ' + fmt(fc.mae_first) + ' → ' + fmt(fc.mae_second) + ' pts over ' + num(fc.n) + ' pairs' : 'Campaign recordings too short to split'],
    ['Drift-monitor window rows', summary.drift_rows, num(summary.drift_detected_rows) + ' row(s) flagged drift_detected' + (summary.using_results_drift_fallback ? ' · from the results directory' : '')],
    ['Recalibration artifact', data.recalibration ? 'Written' : 'Not written', data.recalibration ? 'Radius ' + fmt(Number(data.recalibration.radius_percentage_points)) + ' pts' : 'Written only when the monitor declares drift'],
  ];
  $('#drift-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');

  $('#drift-status').textContent = summary.verdict
    ? summary.verdict.charAt(0).toUpperCase() + summary.verdict.slice(1)
    : (summary.firings ? num(summary.firings) + ' detector firing(s) recorded' : 'No drift data recorded in any source');
  $('#drift-status').classList.toggle('pending', !summary.firings && !fc);

  const latest = data.latest_firing;
  $('#latest-chip').textContent = latest ? shortRun(latest.run) : '—';
  $('#latest-firing').innerHTML = latest ? detailRows([
    ['Run', latest.run], ['Source', latest.source], ['Recorded at', latest.time],
    ['Detector', pretty(latest.detector)], ['Event as logged', latest.event],
    ['Path', latest.path || '—'], ['Control mode', latest.control_mode || '—'],
    ['Logged reason', latest.reason || '—'],
    ['Firing span', (summary.first || '—') + ' → ' + (summary.last || '—')],
  ]) : 'No drift detector has fired in any recorded run yet. The forecast-error halves below are still computed from the campaign recordings.';

  if (fc) {
    $('#forecast-bars').innerHTML = bars(
      [{ name: 'First half RMSE', value: Number(fc.rmse_first) }, { name: 'Second half RMSE', value: Number(fc.rmse_second) }],
      e => e.name, e => e.value, e => fmt(e.value) + ' pts');
    $('#forecast-verdict').innerHTML = detailRows([
      ['Pairs', fc.n], ['MAE first half', fmt(fc.mae_first) + ' pts'], ['MAE second half', fmt(fc.mae_second) + ' pts'],
      ['RMSE first half', fmt(fc.rmse_first) + ' pts'], ['RMSE second half', fmt(fc.rmse_second) + ' pts'],
      ['Verdict (same rule as the dashboard card)', summary.verdict || '—'],
    ]);
  } else {
    $('#forecast-bars').innerHTML = 'Campaign-02 predictive-ml recordings are absent or too short to split honestly.';
    $('#forecast-verdict').innerHTML = '';
  }
  const runs = data.forecast_runs || [];
  $('#forecast-table').innerHTML = `<thead><tr><th>Campaign run</th><th>Pairs</th><th>RMSE</th><th>MAE</th><th>Bias</th><th>Max abs error</th><th>RMSE 1st half</th><th>RMSE 2nd half</th><th>MAE 1st half</th><th>MAE 2nd half</th></tr></thead><tbody>${
    runs.map(r => `<tr><td>${escapeText(shortRun(r.id))}</td><td>${num(r.pairs)}</td><td>${fmt(r.rmse)}</td><td>${fmt(r.mae)}</td><td>${fmt(r.bias)}</td><td>${fmt(r.max_abs_error)}</td><td>${fmt(r.rmse_first)}</td><td>${fmt(r.rmse_second)}</td><td>${fmt(r.mae_first)}</td><td>${fmt(r.mae_second)}</td></tr>`).join('')
    || '<tr><td colspan="10">No campaign forecast pairs recorded</td></tr>'}</tbody>`;
  const linkRows = [];
  runs.forEach(r => Object.entries(r.by_link || {}).forEach(([link, s]) =>
    linkRows.push({ run: r.id, link, pairs: s.pairs, rmse: s.rmse, mae: s.mae, bias: s.bias })));
  $('#link-table').innerHTML = `<thead><tr><th>Campaign run</th><th>Link</th><th>Pairs</th><th>RMSE</th><th>MAE</th><th>Bias</th></tr></thead><tbody>${
    linkRows.map(r => `<tr><td>${escapeText(shortRun(r.run))}</td><td>${escapeText(r.link)}</td><td>${num(r.pairs)}</td><td>${fmt(r.rmse)}</td><td>${fmt(r.mae)}</td><td>${fmt(r.bias)}</td></tr>`).join('')
    || '<tr><td colspan="6">No per-link forecast pairs recorded</td></tr>'}</tbody>`;

  const firings = data.firings || [];
  $('#firings-count').textContent = `${firings.length} firing(s)`;
  $('#detector-bars').innerHTML = bars(detectors.map(([name, count]) => ({ name, count })), e => pretty(e.name), e => e.count) || 'No detector has fired yet.';
  $('#firings-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Source</th><th>Detector</th><th>Path</th><th>Mode</th><th>Logged reason</th></tr></thead><tbody>${
    firings.slice().reverse().map(f => `<tr><td>${escapeText(f.time || '—')}</td><td>${escapeText(shortRun(f.run))}</td><td>${escapeText(f.source)}</td><td>${escapeText(pretty(f.detector))}</td><td>${escapeText(f.path || '—')}</td><td>${escapeText(f.control_mode || '—')}</td><td class="event-reason">${escapeText(f.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="7">No detector firings recorded</td></tr>'}</tbody>`;

  const driftRows = data.drift_rows_recent || [];
  if (driftRows.length) {
    const keys = [];
    driftRows.forEach(r => Object.keys(r).forEach(k => { if (!keys.includes(k)) keys.push(k); }));
    const ordered = ['time', 'run', 'source', 'system', ...keys.filter(k => !['time', 'run', 'source', 'system'].includes(k))];
    $('#drift-rows-table').innerHTML = `<thead><tr>${ordered.map(k => `<th>${escapeText(pretty(k))}</th>`).join('')}</tr></thead><tbody>${
      driftRows.slice().reverse().map(r => `<tr>${ordered.map(k => `<td>${escapeText(typeof r[k] === 'number' ? fmt(r[k], 4) : num(r[k]))}</td>`).join('')}</tr>`).join('')}</tbody>`;
  } else {
    $('#drift-rows-table').innerHTML = '<tbody><tr><td>No drift.csv row has been recorded by any run yet — the windowed monitor writes this file only when it is armed with a baseline RMSE.</td></tr></tbody>';
  }

  const rec = data.recalibration;
  $('#recalibration').innerHTML = rec ? detailRows(
    Object.entries(rec).filter(([, v]) => typeof v !== 'object').map(([k, v]) => [pretty(k), typeof v === 'number' ? fmt(v, 4) : String(v)])
  ) : 'No recalibration.json artifact exists in the live captures or the results directory. It is written only when the drift monitor declares drift after consecutive drifting windows.';

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>`
    : 'No drift source file was found in any layer.';
}

async function load() {
  try {
    const response = await fetch('/api/drift');
    if (!response.ok) throw new Error('Drift record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
