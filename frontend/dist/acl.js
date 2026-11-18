'use strict';
/* ACL firewall detail page. Fetches /api/acl and renders only what
   the payload contains: the rule table is the loaded engine's state,
   the probes are a fresh engine's answers at request time, and the
   recorded drops are aggregated server-side from control_events.csv
   (live captures + campaign-02). This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const shortRun = id => id ? String(id).replace('run-', '') : '';
const portRange = range => {
  if (!range || range.length !== 2) return '—';
  if (range[0] === 0 && range[1] === 65535) return 'any';
  return range[0] === range[1] ? String(range[0]) : range[0] + '–' + range[1];
};
const packetText = p => p ? `${p.src_ip} → ${p.dst_ip} · ${p.protocol} · ${p.src_port} → ${p.dst_port}` : '—';
const verdictText = (action, winner) => action == null ? '—' : action + (winner == null ? ' (default policy)' : ' (rule ' + winner + ')');

function metricCards(summary) {
  const cards = [
    ['Rules loaded & validated', summary.rules, 'From the configured rules file, through acl_firewall.py'],
    ['Deny rules', summary.deny_rules, 'Rules whose action is deny'],
    ['Allow rules', summary.allow_rules, 'Rules whose action is allow'],
    ['Default policy', summary.default_policy, 'Applies when no rule matches'],
    ['Recorded drops', summary.total, 'acl_drop events in the recorded control events'],
    ['Drops · live / campaign', `${num(summary.live)} / ${num(summary.campaign)}`, summary.using_results ? 'Includes the offline results file (fallback)' : 'Live captures + campaign-02 recordings'],
  ];
  $('#acl-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function render(data) {
  const summary = data.summary || {};
  summary.using_results = data.using_results_fallback;
  $('#acl-status').textContent = data.available
    ? `${num(summary.rules)} rule(s) loaded · default ${num(summary.default_policy)} · ${num(summary.total)} recorded drop(s)`
    : 'ACL rules unavailable';
  $('#acl-status').classList.toggle('pending', !data.available);
  metricCards(summary);
  $('#rules-chip').textContent = data.available ? `default · ${num(data.default_policy)}` : '—';

  const rules = data.rules || [];
  $('#rules-table').innerHTML = `<thead><tr><th>#</th><th>Rule</th><th>Priority</th><th>Action</th><th>Protocol</th><th>Source</th><th>Destination</th><th>Src ports</th><th>Dst ports</th></tr></thead><tbody>${
    rules.map((r, i) => `<tr><td>${i + 1}</td><td>${num(r.index)}</td><td>${num(r.priority)}</td><td>${escapeText(r.action)}</td><td>${escapeText(r.protocol)}</td><td>${escapeText(r.src)}</td><td>${escapeText(r.dst)}</td><td>${escapeText(portRange(r.src_ports))}</td><td>${escapeText(portRange(r.dst_ports))}</td></tr>`).join('')
    || `<tr><td colspan="9">${data.available ? 'No rules in the loaded file' : 'Rules file could not be loaded: ' + escapeText(data.error || 'unknown error')}</td></tr>`}</tbody>`;
  $('#acl-policy').innerHTML = detailRows([
    ['Rules file', data.rules_file || '—'],
    ['Default policy', data.default_policy || '—'],
    ['Validated at load', data.available ? 'yes — acl_firewall.py accepted the file' : 'no'],
    ['Recorded drops used the results fallback', data.using_results_fallback ? 'yes — neither live nor campaign recorded an acl_drop' : 'no'],
  ]);

  const probes = data.probes || [];
  $('#probe-table').innerHTML = `<thead><tr><th>Rule</th><th>Matching packet</th><th>Verdict</th><th>Just-outside packet</th><th>Verdict</th></tr></thead><tbody>${
    probes.map(p => `<tr><td>${num(p.rule_index)}</td><td class="event-reason">${escapeText(packetText(p.packet))}</td><td>${escapeText(verdictText(p.action, p.winning_rule))}</td><td class="event-reason">${p.outside ? escapeText(packetText(p.outside.packet)) : '—'}</td><td>${p.outside ? escapeText(verdictText(p.outside.action, p.outside.winning_rule)) : '—'}</td></tr>`).join('')
    || '<tr><td colspan="5">No probes evaluated</td></tr>'}</tbody>`;
  const hits = Object.entries(data.probe_hits || {});
  $('#probe-hits').innerHTML = hits.length
    ? detailRows(hits.map(([k, v]) => ['Probe hits on rule ' + k, v]))
    : '';

  const events = data.events || [];
  $('#drops-count').textContent = `Showing the latest ${events.length} of ${num(data.events_total)} recorded drop(s) (limit ${num(data.recent_limit)})`;
  const byRule = Object.entries(summary.drops_by_rule || {}).sort((a, b) => b[1] - a[1]);
  const maxDrop = Math.max(0.0001, ...byRule.map(([, v]) => v));
  $('#drop-bars').innerHTML = byRule.length
    ? byRule.map(([k, v]) => `<div class="bar-row"><span>rule ${escapeText(k)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * v / maxDrop))}%"></div></div><b>${num(v)}</b></div>`).join('')
    : 'No acl_drop event has been recorded in any live capture, campaign run, or the results file. The workload (UDP iperf + probes) matches no deny rule, so zero is the honest count — run traffic to TCP/22 or UDP 8000-9000 in a live experiment and the drops appear here from its capture.';
  $('#drops-table').innerHTML = `<thead><tr><th>Time</th><th>Run</th><th>Source</th><th>System</th><th>Rule</th><th>Reason</th></tr></thead><tbody>${
    events.slice().reverse().map(ev => `<tr><td>${escapeText(ev.time || '—')}</td><td>${escapeText(shortRun(ev.run))}</td><td>${escapeText(ev.source)}</td><td>${escapeText(ev.system || '—')}</td><td>${escapeText(ev.rule_index || '—')}</td><td class="event-reason">${escapeText(ev.reason || '—')}</td></tr>`).join('')
    || '<tr><td colspan="6">No recorded drops</td></tr>'}</tbody>`;

  const wiring = data.wiring || {};
  const wiringRows = Object.entries(wiring).sort().map(([k, v]) => [k, v]);
  $('#acl-wiring').innerHTML = wiringRows.length
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
    const response = await fetch('/api/acl');
    if (!response.ok) throw new Error('ACL record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
