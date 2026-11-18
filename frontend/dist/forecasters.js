'use strict';
/* Forecaster comparison detail page. Fetches /api/forecasters and
   renders only what the payload contains: every metric, rank,
   congestion count, the recomputed model selection and the recorded
   configuration are computed server-side from
   cnt-ml/results/metrics_comparison.csv,
   cnt-ml/results/congestion_metrics.csv and
   cnt-ml/models/feature_list.json. This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const fmt = (value, digits) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—';
};
const signed = (value, digits) => {
  const n = Number(value);
  return Number.isFinite(n) ? (n > 0 ? '+' : '') + n.toFixed(digits == null ? 2 : digits) : '—';
};

function metricCards(data) {
  const models = data.models || [];
  const bests = data.bests || {};
  const byName = Object.fromEntries(models.map(m => [m.model, m]));
  const best = key => byName[bests[key]] || {};
  const selection = data.selection || {};
  const cards = [
    ['Models compared', models.length, models.map(m => m.model).join(' · ')],
    ['Best RMSE', fmt(best('rmse').rmse), num(bests.rmse) + ' · lower is better'],
    ['Best R²', fmt(best('r2').r2, 3), num(bests.r2) + ' · higher is better'],
    ['Best MAE', fmt(best('mae').mae), num(bests.mae) + ' · lower is better'],
    ['Fastest inference', fmt(best('inference_time_ms').inference_time_ms, 4) + ' ms', num(bests.inference_time_ms)],
    ['Selected for the controller', num(selection.model), 'Congestion F1 ' + fmt(selection.f1, 3) + ' under the recorded selection rule'],
  ];
  $('#fc-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(value)}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function bars(element, title, items) {
  const usable = (items || []).filter(([, v]) => Number.isFinite(Number(v)));
  const max = Math.max(0.0001, ...usable.map(([, v]) => Number(v)));
  element.innerHTML = usable.length
    ? `<div class="feature-compare-title">${escapeText(title)}</div>` + usable.map(([label, value]) =>
        `<div class="bar-row"><span>${escapeText(label)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * Number(value) / max))}%"></div></div><b>${fmt(Number(value), Math.abs(Number(value)) < 10 ? 3 : 2)}</b></div>`).join('')
    : '';
}

function render(data) {
  if (!data.available) {
    $('#fc-status').textContent = 'No recorded model comparison yet';
    $('#selection').textContent = 'cnt-ml/results/metrics_comparison.csv was not found. Run the cnt-ml training pipeline and this page fills in from its results.';
    return;
  }
  metricCards(data);
  const models = data.models || [];
  const selection = data.selection || {};
  const config = data.config || {};
  $('#fc-status').textContent = selection.model
    ? 'Selected by congestion F1: ' + selection.model
    : 'Models recorded — no congestion selection could be computed';
  $('#fc-status').classList.toggle('pending', !selection.model);
  $('#selection-chip').textContent = num(selection.model);
  $('#selection').innerHTML = detailRows([
    ['Selected model (recomputed)', selection.model],
    ['Congestion F1 of the selection', fmt(selection.f1, 3)],
    ['Models tied on F1', (selection.tied_models || []).join(' · ') || '—'],
    ['Selection rule applied', selection.rule || '—'],
    ['Selection metric (as recorded in feature_list.json)', config.selection_metric || '—'],
    ['Engine (as recorded)', config.engine || '—'],
    ['Artifact status (as recorded)', config.artifact_status || '—'],
  ]);

  bars($('#mae-bars'), 'MAE comparison (lower is better)', models.map(m => [m.model, m.mae]));
  bars($('#rmse-bars'), 'RMSE comparison (lower is better)', models.map(m => [m.model, m.rmse]));
  bars($('#r2-bars'), 'R² comparison (higher is better)', models.map(m => [m.model, m.r2]));
  $('#models-table').innerHTML = `<thead><tr><th>Model</th><th>MAE</th><th>MAE rank</th><th>RMSE</th><th>RMSE rank</th><th>R²</th><th>R² rank</th><th>Inference (ms)</th><th>Inference rank</th></tr></thead><tbody>${
    models.map(m => `<tr><td>${escapeText(m.model)}</td><td>${fmt(m.mae)}</td><td>${num(m.mae_rank)}</td><td>${fmt(m.rmse)}</td><td>${num(m.rmse_rank)}</td><td>${fmt(m.r2, 3)}</td><td>${num(m.r2_rank)}</td><td>${fmt(m.inference_time_ms, 4)}</td><td>${num(m.inference_time_ms_rank)}</td></tr>`).join('')
    || '<tr><td colspan="9">No models recorded</td></tr>'}</tbody>`;

  const congestion = data.congestion || [];
  const thresholds = [...new Set(congestion.map(c => c.threshold).filter(v => v != null))];
  $('#congestion-chip').textContent = thresholds.length ? 'Congestion threshold ' + thresholds.map(t => fmt(t, 0) + '%').join(' · ') : '';
  $('#congestion-table').innerHTML = `<thead><tr><th>Model</th><th>Threshold (%)</th><th>TP</th><th>FP</th><th>FN</th><th>TN</th><th>Total</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead><tbody>${
    congestion.map(c => `<tr><td>${escapeText(c.model)}</td><td>${fmt(c.threshold, 0)}</td><td>${num(c.TP)}</td><td>${num(c.FP)}</td><td>${num(c.FN)}</td><td>${num(c.TN)}</td><td>${num(c.total)}</td><td>${fmt(c.precision, 3)}</td><td>${fmt(c.recall, 3)}</td><td>${fmt(c.f1, 3)}</td></tr>`).join('')
    || '<tr><td colspan="10">No congestion metrics recorded</td></tr>'}</tbody>`;

  const pair = data.selected_vs_persistence;
  $('#pair').innerHTML = pair ? detailRows([
    ['Gradient model', pair.gradient_model], ['Baseline', pair.persistence_model],
    ['RMSE improvement vs persistence', signed(pair.rmse_improvement_pct) + '% (lower RMSE is better)'],
    ['MAE difference vs persistence', signed(pair.mae_difference_pct) + '% (positive means worse on MAE)'],
    ['R² difference vs persistence', signed(pair.r2_difference, 3)],
  ]) : 'The recorded comparison does not contain both a Gradient Boosting and a Persistence row.';

  const features = config.features || [];
  const params = config.selected_parameters || {};
  $('#config').innerHTML = detailRows([
    ['Target', config.target], ['Target alignment', config.target_alignment],
    ['Forecast horizon (seconds)', config.horizon_seconds],
    ['Features (' + features.length + ')', features.join(' · ') || '—'],
    ['Validation RMSE (recorded)', fmt(config.validation_rmse)],
    ['Validation congestion precision', fmt(config.validation_congestion_precision, 3)],
    ['Validation congestion recall', fmt(config.validation_congestion_recall, 3)],
    ['Validation congestion F1', fmt(config.validation_congestion_f1, 3)],
  ]);
  $('#params-table').innerHTML = `<thead><tr><th>Selected parameter</th><th>Value</th></tr></thead><tbody>${
    Object.entries(params).map(([key, value]) => `<tr><td>${escapeText(key)}</td><td>${escapeText(num(value))}</td></tr>`).join('')
    || '<tr><td colspan="2">No parameters recorded</td></tr>'}</tbody>`;

  const artifacts = data.artifacts || [];
  $('#artifacts-table').innerHTML = `<thead><tr><th>File</th><th>Size (bytes)</th><th>Modified</th></tr></thead><tbody>${
    artifacts.map(a => `<tr><td>${escapeText(a.name)}</td><td>${num(a.size_bytes)}</td><td>${escapeText(a.modified || '—')}</td></tr>`).join('')
    || '<tr><td colspan="3">No model artifacts present</td></tr>'}</tbody>`;

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>`
    : 'No source files were found.';
}

async function load() {
  try {
    const response = await fetch('/api/forecasters');
    if (!response.ok) throw new Error('Forecaster record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
