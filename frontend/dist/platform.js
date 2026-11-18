'use strict';
/* Extension-platform detail page. Fetches /api/platform and renders
   only what the payload contains: every count, start, run and event
   is computed server-side from the recorded control_events.csv files
   (live captures + campaign-02). This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const shortRun = id => id ? String(id).replace('run-', '') : '';

function metricCards(summary) {
  const types = Object.keys(summary.event_counts || {}).length;
  const cards = [
    ['Live runs recorded', summary.live_runs, 'Experiment sessions on disk'],
    ['Live runs with control events', summary.live_runs_with_events, 'Captures that recorded control_events.csv'],
    ['Campaign runs recorded', summary.campaign_runs, (summary.campaign_systems || []).join(' · ') || 'campaign-02'],
    ['Controller starts logged', summary.starts, `Live ${num(summary.starts_live)} · campaign ${num(summary.starts_campaign)}`],
    ['Reroutes recorded', summary.reroutes, `Live ${num(summary.reroutes_live)} · campaign ${num(summary.reroutes_campaign)}`],
    ['Control events total', summary.events, `${types} event type(s) seen`],
  ];
  $('#platform-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function render(data) {
  const summary = data.summary || {};
  metricCards(summary);
  const latest = data.latest_start;
  $('#platform-status').textContent = latest
    ? `Latest controller start · extensions ${latest.topnotch}`
    : 'No controller start recorded in any source';
  $('#platform-status').classList.toggle('pending', !latest || latest.topnotch !== 'enabled');
  $('#latest-start-chip').textContent = latest ? shortRun(latest.run) : '—';
  $('#latest-start').innerHTML = latest ? detailRows([
    ['Run', latest.run], ['Source', latest.source], ['System', latest.system || '—'],
    ['Started at', latest.time], ['Control mode', latest.control_mode],
    ['Initial path', latest.initial_path], ['Extensions (as logged)', latest.topnotch],
    ['Logged reason', latest.reason || '—'],
  ]) : 'No controller_start event exists in the live captures, the campaign recordings, or the results directory yet. Start a live experiment and this page fills in from its capture.';

  const starts = data.starts || [];
  $('#starts-count').textContent = `${starts.length} start(s)`;
  $('#starts-table').innerHTML = `<thead><tr><th>Started at</th><th>Run</th><th>Source</th><th>System</th><th>Mode</th><th>Path</th><th>Extensions</th><th>Reason</th></tr></thead><tbody>${
    starts.slice().reverse().map(s => `<tr><td>${escapeText(s.time || '—')}</td><td>${escapeText(shortRun(s.run))}</td><td>${escapeText(s.source)}</td><td>${escapeText(s.system || '—')}</td><td>${escapeText(s.control_mode || '—')}</td><td>${escapeText(s.initial_path || '—')}</td><td>${escapeText(s.topnotch)}</td><td class="event-reason">${escapeText(s.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="8">No controller starts recorded</td></tr>'}</tbody>`;

  const liveRuns = data.live_runs_detail || [];
  $('#live-count').textContent = `${liveRuns.length} session(s) on disk`;
  $('#live-table').innerHTML = `<thead><tr><th>Run</th><th>System</th><th>Scenario</th><th>Status</th><th>Events</th><th>Starts</th><th>Reroutes</th><th>Anomalies</th><th>Drift firings</th><th>First event</th><th>Last event</th><th>Duration</th><th>Capture files</th></tr></thead><tbody>${
    liveRuns.map(r => {
      const counts = r.counts || {};
      const drift = Object.entries(counts).filter(([k]) => k.startsWith('drift')).reduce((n, [, v]) => n + v, 0);
      return `<tr><td>${escapeText(shortRun(r.id))}</td><td>${escapeText(r.system || '—')}</td><td>${escapeText(r.scenario || '—')}</td><td>${escapeText(r.status || '—')}</td><td>${num(r.events)}</td><td>${num(r.starts)}</td><td>${num(r.reroutes)}</td><td>${num(counts.anomaly_detected || 0)}</td><td>${num(drift)}</td><td>${escapeText(r.first || '—')}</td><td>${escapeText(r.last || '—')}</td><td>${r.duration_seconds == null ? '—' : num(r.duration_seconds) + ' s'}</td><td class="event-reason">${escapeText((r.files || []).join(', ') || '—')}</td></tr>`;
    }).join('') || '<tr><td colspan="13">No live sessions recorded</td></tr>'}</tbody>`;

  const groups = data.campaign_by_system || [];
  const maxReroutes = Math.max(0.0001, ...groups.map(g => g.reroutes || 0));
  $('#campaign-bars').innerHTML = groups.map(g =>
    `<div class="bar-row"><span>${escapeText(g.system)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * (g.reroutes || 0) / maxReroutes))}%"></div></div><b>${num(g.reroutes)}</b></div>`).join('')
    || 'No campaign recordings found.';
  $('#campaign-table').innerHTML = `<thead><tr><th>System</th><th>Runs</th><th>Runs with events</th><th>Events</th><th>Starts</th><th>Reroutes</th></tr></thead><tbody>${
    groups.map(g => `<tr><td>${escapeText(g.system)}</td><td>${num(g.runs.length)}</td><td>${num(g.runs.filter(r => r.events > 0).length)}</td><td>${num(g.events)}</td><td>${num(g.starts)}</td><td>${num(g.reroutes)}</td></tr>`).join('')
    || '<tr><td colspan="6">No campaign recordings found</td></tr>'}</tbody>`;

  const totals = Object.entries(summary.event_counts || {}).sort((a, b) => b[1] - a[1]);
  const maxEvent = Math.max(0.0001, ...totals.map(([, v]) => v));
  $('#event-bars').innerHTML = totals.map(([name, value]) =>
    `<div class="bar-row"><span>${escapeText(name)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * value / maxEvent))}%"></div></div><b>${num(value)}</b></div>`).join('')
    || 'No control events recorded.';
  const liveCounts = summary.event_counts_live || {}, campaignCounts = summary.event_counts_campaign || {};
  const allTypes = Array.from(new Set([...Object.keys(liveCounts), ...Object.keys(campaignCounts)])).sort();
  $('#event-table').innerHTML = `<thead><tr><th>Event type</th><th>Live</th><th>Campaign</th><th>Total</th></tr></thead><tbody>${
    allTypes.map(name => `<tr><td>${escapeText(name)}</td><td>${num(liveCounts[name] || 0)}</td><td>${num(campaignCounts[name] || 0)}</td><td>${num((liveCounts[name] || 0) + (campaignCounts[name] || 0))}</td></tr>`).join('')
    || '<tr><td colspan="4">No control events recorded</td></tr>'}</tbody>`;

  const recent = data.recent_events || [];
  $('#recent-count').textContent = `Showing the latest ${recent.length} of ${num(summary.events)} (limit ${num(data.recent_limit)})`;
  $('#recent-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Source</th><th>Event</th><th>Mode</th><th>Path change</th><th>Reason</th></tr></thead><tbody>${
    recent.slice().reverse().map(ev => `<tr><td>${escapeText(ev.time || '—')}</td><td>${escapeText(shortRun(ev.run))}</td><td>${escapeText(ev.source)}</td><td>${escapeText(ev.event || '—')}</td><td>${escapeText(ev.control_mode || '—')}</td><td>${escapeText(ev.old_path || ev.new_path ? `${ev.old_path || '—'} → ${ev.new_path || '—'}` : '—')}</td><td class="event-reason">${escapeText(ev.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="7">No control events recorded</td></tr>'}</tbody>`;

  const sources = data.sources || [];
  $('#sources-list').innerHTML = sources.length
    ? `<div class="source-list">${sources.map(s => `<div>${escapeText(s)}</div>`).join('')}</div>`
    : 'No control_events.csv file was found in any source layer.';
}

async function load() {
  try {
    const response = await fetch('/api/platform');
    if (!response.ok) throw new Error('Platform record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
