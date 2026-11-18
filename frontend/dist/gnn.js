'use strict';
/* GNN forecaster detail page. Fetches /api/gnn and renders only what
   the payload contains: every metric, rank, gap, graph edge and the
   Diebold-Mariano result is computed server-side from the recorded
   cnt-ml/results/gnn_metrics.json (plus the trained artifact's file
   stats). This file holds labels, no data. */
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
  const byName = Object.fromEntries(models.map(m => [m.name, m]));
  const gnn = byName['GNN'] || {}, persistence = byName['Persistence'] || {};
  const gap = data.gnn_vs_persistence || {};
  const training = data.training || {};
  const links = (data.graph || {}).links || [];
  const cards = [
    ['GNN MAE', fmt(gnn.mae), 'RMSE ' + fmt(gnn.rmse) + ' · rank ' + num(gnn.mae_rank) + ' of ' + models.length + ' on MAE'],
    ['Persistence MAE', fmt(persistence.mae), 'RMSE ' + fmt(persistence.rmse) + ' · the baseline to beat'],
    ['MAE gap (GNN − persistence)', signed(gap.mae_diff), signed(gap.mae_pct) + '% vs persistence · RMSE gap ' + signed(gap.rmse_diff)],
    ['Diebold-Mariano p-value', fmt((data.dm || {}).p_value, 3), (data.dm || {}).significant_5pct ? 'Significant at 5%' : 'Not significant at 5%'],
    ['Snapshots (train / test)', num(training.train_snapshots) + ' / ' + num(training.test_snapshots), num(training.total_snapshots) + ' total · test share ' + fmt(Number(training.test_share) * 100, 1) + '%'],
    ['Links modelled', links.length, links.join(' · ') || '—'],
  ];
  $('#gnn-metrics').innerHTML = cards.map(([label, value, caption]) =>
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
        `<div class="bar-row"><span>${escapeText(label)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * Number(value) / max))}%"></div></div><b>${fmt(Number(value))}</b></div>`).join('')
    : '';
}

function render(data) {
  if (!data.available) {
    $('#gnn-status').textContent = 'No GNN evaluation recorded yet';
    $('#gnn-metrics').innerHTML = '';
    $('#verdict').textContent = 'cnt-ml/results/gnn_metrics.json was not found. Run experiments/train_gnn.py on a recorded capture and this page fills in from its output.';
    return;
  }
  metricCards(data);
  const dm = data.dm || {};
  const gap = data.gnn_vs_persistence || {};
  const gnnWins = data.winner_by_mae === 'GNN';
  const verdict = gnnWins
    ? (dm.significant_5pct ? 'GNN wins on MAE and the DM test is significant at 5% — a real improvement on this data.'
                           : 'GNN has the lowest MAE, but the DM test is not significant at 5% — not a certified win.')
    : (dm.significant_5pct ? `${data.winner_by_mae} wins on MAE and the difference is significant at 5%.`
                           : `${data.winner_by_mae} wins on MAE; the GNN's difference from persistence is not significant at 5% (p = ${fmt(dm.p_value, 3)}), so the honest verdict is: no certified GNN improvement on this data.`);
  $('#gnn-status').textContent = verdict;
  $('#gnn-status').classList.toggle('pending', !gnnWins);
  $('#verdict-chip').textContent = 'Winner by MAE: ' + num(data.winner_by_mae);
  $('#verdict').innerHTML = detailRows([
    ['Winner by MAE', data.winner_by_mae], ['Winner by RMSE', data.winner_by_rmse],
    ['GNN MAE vs persistence', signed(gap.mae_diff) + ' (' + signed(gap.mae_pct) + '%)'],
    ['GNN RMSE vs persistence', signed(gap.rmse_diff) + ' (' + signed(gap.rmse_pct) + '%)'],
    ['Forecast horizon (steps)', data.horizon_steps],
    ['Verdict', verdict],
  ]);

  const models = data.models || [];
  bars($('#mae-bars'), 'MAE comparison (lower is better)', models.map(m => [m.name, m.mae]));
  bars($('#rmse-bars'), 'RMSE comparison (lower is better)', models.map(m => [m.name, m.rmse]));
  $('#models-table').innerHTML = `<thead><tr><th>Model</th><th>MAE</th><th>MAE rank</th><th>RMSE</th><th>RMSE rank</th></tr></thead><tbody>${
    models.map(m => `<tr><td>${escapeText(m.name)}</td><td>${fmt(m.mae)}</td><td>${num(m.mae_rank)}</td><td>${fmt(m.rmse)}</td><td>${num(m.rmse_rank)}</td></tr>`).join('')
    || '<tr><td colspan="5">No models recorded</td></tr>'}</tbody>`;

  $('#dm-chip').textContent = dm.significant_5pct ? 'Significant at 5%' : 'Not significant at 5%';
  $('#dm').innerHTML = detailRows([
    ['DM statistic', fmt(dm.dm_stat, 4)], ['Mean loss difference', fmt(dm.mean_loss_diff)],
    ['Paired test points (n)', dm.n], ['p-value', fmt(dm.p_value, 4)],
    ['Significant at 5% (as recorded)', dm.significant_5pct == null ? '—' : (dm.significant_5pct ? 'yes' : 'no')],
  ]);

  const training = data.training || {};
  $('#training').innerHTML = detailRows([
    ['Initial loss', fmt(training.initial_loss, 4)], ['Final loss', fmt(training.final_loss, 4)],
    ['Loss drop', fmt(training.drop, 4) + ' (' + fmt(training.drop_pct, 1) + '%)'],
    ['Train snapshots', training.train_snapshots], ['Test snapshots', training.test_snapshots],
    ['Total snapshots', training.total_snapshots],
  ]);

  const graph = data.graph || {};
  const adjacency = graph.adjacency || {};
  $('#graph').innerHTML = detailRows([
    ['Links (graph nodes)', (graph.links || []).join(' · ') || '—'],
    ['Switches', (graph.switches || []).join(' · ') || '—'],
    ['Edges (adjacent link pairs)', (graph.edges || []).length],
    ...Object.entries(adjacency).map(([link, neighbours]) => ['Neighbours of ' + link, neighbours.join(' · ') || 'none']),
  ]);
  $('#edges-table').innerHTML = `<thead><tr><th>Link A</th><th>Link B</th><th>Shared switch</th></tr></thead><tbody>${
    (graph.edges || []).map(edge => `<tr><td>${escapeText(edge.a)}</td><td>${escapeText(edge.b)}</td><td>${escapeText((edge.shared_switch || []).join(' · '))}</td></tr>`).join('')
    || '<tr><td colspan="3">No adjacent link pairs — the recorded links share no switch</td></tr>'}</tbody>`;

  const artifact = data.artifact;
  const sources = data.sources || [];
  $('#sources-list').innerHTML =
    (artifact ? detailRows([['Trained weights', artifact.name], ['Size (bytes)', artifact.size_bytes], ['Modified', artifact.modified]]) : '<div>No trained-weights artifact (gnn_link_forecaster.npz) is present in cnt-ml/models.</div>') +
    (sources.length ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>` : '');
}

async function load() {
  try {
    const response = await fetch('/api/gnn');
    if (!response.ok) throw new Error('GNN record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
