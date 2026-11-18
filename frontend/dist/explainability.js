'use strict';
/* Explainable-decisions detail page. Fetches /api/explainability and
   renders only what the payload contains: every count, share and
   statistic is computed server-side from the recorded
   explanations.csv files (live captures + campaign-02, results dir
   as fallback). This file holds labels, no data. */
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

function metricCards(summary) {
  const cards = [
    ['Decisions explained', summary.decisions, 'Routing evaluations with a recorded explanation'],
    ['Runs contributing', summary.runs_contributing, `Live session dirs on disk ${num(summary.live_runs)} · campaign runs ${num(summary.campaign_runs)}`],
    ['With a forecast', summary.with_prediction, pct(summary.with_prediction_share) + ' of decisions carry a prediction'],
    ['With a forecast interval', summary.with_interval, pct(summary.with_interval_share) + ` carry lower/upper · ${num(summary.predictions_inside_interval)} prediction(s) inside their interval`],
    ['With feature contributions', summary.with_features, pct(summary.with_features_share) + ` of decisions · ${num(summary.distinct_features)} distinct feature(s) seen`],
    ['With a candidate path', summary.with_candidate, 'Evaluations where switching was on the table'],
  ];
  $('#explain-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

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
  metricCards(summary);
  const latest = data.latest;
  $('#explain-status').textContent = summary.decisions
    ? `${num(summary.decisions)} decision(s) explained · ${num(summary.distinct_reasons)} reason(s)`
    : 'No explanations recorded in any source';
  $('#explain-status').classList.toggle('pending', !summary.decisions);
  $('#latest-chip').textContent = latest ? shortRun(latest.run) : '—';
  $('#latest-decision').innerHTML = latest ? detailRows([
    ['Run', latest.run], ['Recorded at', latest.time], ['Active path', latest.active_path],
    ['Candidate', latest.candidate || '(stayed)'], ['Decision source', latest.decision_source],
    ['Reason', pretty(latest.reason)], ['Signal A / B', fmt(latest.signal_a) + ' / ' + fmt(latest.signal_b)],
    ['Forecast (active path)', fmt(latest.prediction)],
    ['Forecast interval', (latest.lower == null || latest.upper == null) ? '—' : fmt(latest.lower) + ' – ' + fmt(latest.upper)],
    ['Top features', latest.top_features || '—'],
    ['Projected improvement', latest.projected_improvement_points == null ? '—' : fmt(latest.projected_improvement_points) + ' pts'],
    ['Record span', (summary.first || '—') + ' → ' + (summary.last || '—') +
      (summary.duration_seconds == null ? '' : ` (${num(summary.duration_seconds)} s)`)],
  ]) : 'No explanation row exists in the live captures, the campaign recordings, or the results directory yet. Run a live experiment and this page fills in from its capture.';

  const reasons = data.by_reason || [];
  $('#reason-bars').innerHTML = bars(reasons, r => pretty(r.name), r => r.count) || 'No decisions recorded.';
  $('#reason-table').innerHTML = `<thead><tr><th>Reason</th><th>Decisions</th><th>Share</th><th>Mean forecast</th><th>Mean projected improvement</th></tr></thead><tbody>${
    reasons.map(r => `<tr><td>${escapeText(pretty(r.name))}</td><td>${num(r.count)}</td><td>${pct(r.share)}</td><td>${fmt(r.mean_prediction)}</td><td>${r.mean_projected_improvement == null ? '—' : fmt(r.mean_projected_improvement) + ' pts'}</td></tr>`).join('')
    || '<tr><td colspan="5">No decisions recorded</td></tr>'}</tbody>`;

  const features = data.features || [];
  $('#feature-bars').innerHTML = bars(features.slice(0, 8), f => f.feature, f => Number(f.total_contribution) || 0, f => fmt(f.total_contribution, 3)) || 'No feature contributions recorded.';
  $('#feature-table').innerHTML = `<thead><tr><th>Feature</th><th>Appears in</th><th>Share of decisions</th><th>Total contribution</th><th>Mean contribution</th><th>Share of all contribution</th></tr></thead><tbody>${
    features.map(f => `<tr><td>${escapeText(f.feature)}</td><td>${num(f.appearances)} decision(s)</td><td>${pct(f.appearance_share)}</td><td>${fmt(f.total_contribution, 3)}</td><td>${fmt(f.mean_contribution, 4)}</td><td>${pct(f.contribution_share)}</td></tr>`).join('')
    || '<tr><td colspan="6">No feature contributions recorded</td></tr>'}</tbody>`;

  const paths = data.by_active_path || [], candidates = data.by_candidate || [], transitions = data.transitions || [];
  $('#path-table').innerHTML = `<thead><tr><th>Active path</th><th>Decisions</th><th>Share</th><th>Candidate</th><th>Decisions</th><th>Share</th></tr></thead><tbody>${
    Array.from({ length: Math.max(paths.length, candidates.length) }).map((_, i) => {
      const p = paths[i], c = candidates[i];
      return `<tr><td>${p ? escapeText(p.name) : ''}</td><td>${p ? num(p.count) : ''}</td><td>${p ? pct(p.share) : ''}</td><td>${c ? escapeText(c.name) : ''}</td><td>${c ? num(c.count) : ''}</td><td>${c ? pct(c.share) : ''}</td></tr>`;
    }).join('') || '<tr><td colspan="6">No decisions recorded</td></tr>'}</tbody>`;
  $('#transition-table').innerHTML = `<thead><tr><th>Active → candidate</th><th>Decisions</th><th>Share</th><th>Mean projected improvement</th></tr></thead><tbody>${
    transitions.map(t => `<tr><td>${escapeText(t.name)}</td><td>${num(t.count)}</td><td>${pct(t.share)}</td><td>${t.mean_projected_improvement == null ? '—' : fmt(t.mean_projected_improvement) + ' pts'}</td></tr>`).join('')
    || '<tr><td colspan="4">No decisions recorded</td></tr>'}</tbody>`;

  const stats = data.signal_stats || {};
  const statRows = [
    ['Signal A (utilization)', stats.signal_a], ['Signal B (utilization)', stats.signal_b],
    ['Forecast for active path', stats.prediction], ['Forecast interval width', stats.interval_width],
    ['Projected improvement (pts)', stats.projected_improvement_points],
  ];
  $('#signal-table').innerHTML = `<thead><tr><th>Quantity</th><th>n</th><th>Mean</th><th>Sample SD</th><th>Min</th><th>Max</th></tr></thead><tbody>${
    statRows.map(([label, s]) => `<tr><td>${escapeText(label)}</td><td>${num(s ? s.n : 0)}</td><td>${fmt(s && s.mean)}</td><td>${fmt(s && s.sd)}</td><td>${fmt(s && s.min)}</td><td>${fmt(s && s.max)}</td></tr>`).join('')}</tbody>`;

  const runs = data.runs_detail || [];
  $('#runs-count').textContent = `${runs.length} run(s) with explanations`;
  $('#runs-table').innerHTML = `<thead><tr><th>Run</th><th>Source</th><th>System</th><th>Scenario</th><th>Decisions</th><th>Top reason</th><th>With forecast</th><th>With interval</th><th>With features</th><th>First</th><th>Last</th><th>Duration</th></tr></thead><tbody>${
    runs.map(r => {
      const top = Object.entries(r.reasons || {}).sort((a, b) => b[1] - a[1])[0];
      return `<tr><td>${escapeText(shortRun(r.id))}</td><td>${escapeText(r.source)}</td><td>${escapeText(r.system || '—')}</td><td>${escapeText(r.scenario || '—')}</td><td>${num(r.decisions)}</td><td>${top ? escapeText(pretty(top[0])) + ' × ' + num(top[1]) : '—'}</td><td>${num(r.with_prediction)}</td><td>${num(r.with_interval)}</td><td>${num(r.with_features)}</td><td>${escapeText(r.first || '—')}</td><td>${escapeText(r.last || '—')}</td><td>${r.duration_seconds == null ? '—' : num(r.duration_seconds) + ' s'}</td></tr>`;
    }).join('') || '<tr><td colspan="12">No runs recorded explanations</td></tr>'}</tbody>`;

  const recent = data.recent || [];
  $('#recent-count').textContent = `Showing the latest ${recent.length} of ${num(summary.decisions)} (limit ${num(data.recent_limit)})`;
  $('#recent-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Active</th><th>Candidate</th><th>Source</th><th>Reason</th><th>Signal A</th><th>Signal B</th><th>Forecast</th><th>Interval</th><th>Top features</th><th>Projected</th></tr></thead><tbody>${
    recent.slice().reverse().map(r => `<tr><td>${escapeText(r.time || '—')}</td><td>${escapeText(shortRun(r.run))}</td><td>${escapeText(r.active_path || '—')}</td><td>${escapeText(r.candidate || '(stayed)')}</td><td>${escapeText(r.decision_source || '—')}</td><td>${escapeText(pretty(r.reason))}</td><td>${fmt(r.signal_a)}</td><td>${fmt(r.signal_b)}</td><td>${fmt(r.prediction)}</td><td>${(r.lower == null || r.upper == null) ? '—' : fmt(r.lower) + ' – ' + fmt(r.upper)}</td><td class="event-reason">${escapeText(r.top_features || '—')}</td><td>${r.projected_improvement_points == null ? '—' : fmt(r.projected_improvement_points)}</td></tr>`).join('')
    || '<tr><td colspan="12">No explanations recorded</td></tr>'}</tbody>`;

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>`
    : 'No explanations.csv file was found in any source layer.';
}

async function load() {
  try {
    const response = await fetch('/api/explainability');
    if (!response.ok) throw new Error('Explainability record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
