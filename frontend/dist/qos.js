'use strict';
/* QoS / DiffServ detail page. Fetches /api/qos and renders only what
   the payload contains: every class, meter band, DSCP resolution and
   mapping is computed server-side by qos_policy.py at request time.
   This file holds labels, no data. */
const $ = selector => document.querySelector(selector);
const escapeText = value => String(value ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
const num = value => value == null ? '—' : String(value);
const pct = value => value == null ? '—' : Math.round(value * 100) + '%';

function metricCards(summary) {
  const cards = [
    ['Traffic classes', summary.classes, 'Loaded and validated by qos_policy.py'],
    ['Metered classes', summary.metered_classes, 'Classes with a meter and a capacity share'],
    ['Metered share of capacity', pct(summary.metered_share), 'Sum of the metered class shares'],
    ['DSCPs claimed', summary.dscps_claimed, 'Of the 64 DiffServ code points'],
    ['Capacities recorded', (summary.capacities || []).length, (summary.capacities || []).map(c => c + ' Mbps').join(' · ') || 'None declared'],
    ['QoS in live runs', summary.qos_switch ? 'SDN_QOS=' + summary.qos_switch : '—', 'As set by experiments/live_guest.py'],
  ];
  $('#qos-metrics').innerHTML = cards.map(([label, value, caption]) =>
    `<article class="metric"><div class="metric-label">${escapeText(label)}</div><div class="metric-value">${escapeText(num(value))}</div><div class="metric-caption">${escapeText(caption)}</div></article>`).join('');
}

function detailRows(rows) {
  return `<div class="detail-rows">${rows.map(([label, value]) =>
    `<div class="feature-stat"><span>${escapeText(label)}</span><b>${escapeText(num(value))}</b></div>`).join('')}</div>`;
}

function render(data) {
  const summary = data.summary || {};
  $('#qos-status').textContent = data.available
    ? `Policy validated · ${num(summary.classes)} classes · ${num(summary.metered_classes)} metered`
    : 'QoS policy unavailable';
  $('#qos-status').classList.toggle('pending', !data.available);
  if (!data.available) {
    $('#qos-metrics').innerHTML = '';
    $('#qos-policy').textContent = 'qos_policy.py could not load the policy: ' + (data.error || 'unknown error') + '. Nothing on this page is estimated — fix the policy file and reload.';
    $('#error').hidden = true;
    renderSources(data);
    return;
  }
  metricCards(summary);
  $('#policy-chip').textContent = data.default_class ? 'default · ' + data.default_class : '—';
  $('#qos-policy').innerHTML = detailRows([
    ['Policy source', data.policy_source],
    ['Default class', data.default_class],
    ['Metered share', pct(summary.metered_share)],
    ['Unmetered classes', (summary.unmetered || []).join(', ') || '—'],
    ['Enforcement', summary.enforcement || 'OpenFlow meters (rate caps) only'],
    ['Meters used', (summary.meters || []).join(', ') || '—'],
  ]);

  const bands = data.bands_by_capacity || {};
  const capacities = Object.keys(bands).sort((a, b) => Number(a) - Number(b));
  const first = capacities.length ? bands[capacities[0]] : [];
  const maxRate = Math.max(0.0001, ...first.map(b => b.rate_kbps || 0));
  $('#band-bars').innerHTML = first.length
    ? first.map(b => `<div class="bar-row"><span>${escapeText(b.class_name)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, Math.round(100 * (b.rate_kbps || 0) / maxRate))}%"></div></div><b>${num(b.rate_kbps)} kbps</b></div>`).join('') +
      `<div class="secondary">Meter rates at the smallest recorded capacity (${escapeText(capacities[0])} Mbps) — the full table covers every recorded capacity.</div>`
    : 'No link capacity is declared by any recording or by the controller default, so no meter band can be derived.';
  const bandRows = [];
  capacities.forEach(capacity => (bands[capacity] || []).forEach(b =>
    bandRows.push(`<tr><td>${escapeText(capacity)} Mbps</td><td>${escapeText(b.class_name)}</td><td>${num(b.meter_id)}</td><td>${num(b.rate_kbps)} kbps</td><td>${b.rate_kbps == null ? '—' : (b.rate_kbps / 1000).toFixed(1)} Mbps</td><td>${num(b.burst_kbit)} kbit</td></tr>`)));
  $('#band-table').innerHTML = `<thead><tr><th>Link capacity</th><th>Class</th><th>Meter</th><th>Rate</th><th>Rate</th><th>Burst</th></tr></thead><tbody>${bandRows.join('') || '<tr><td colspan="6">No meter bands derivable</td></tr>'}</tbody>`;

  const classes = data.classes || [];
  $('#class-table').innerHTML = `<thead><tr><th>Class</th><th>DSCP</th><th>Share</th><th>Queue ID (metadata)</th><th>Meter</th><th>Metered</th></tr></thead><tbody>${
    classes.map(c => `<tr><td>${escapeText(c.name)}</td><td>${escapeText((c.dscp || []).join(', '))}</td><td>${c.share == null ? 'unmetered' : pct(c.share)}</td><td>${num(c.queue_id)}</td><td>${num(c.meter_id)}</td><td>${c.metered ? 'yes' : 'no'}</td></tr>`).join('')
    || '<tr><td colspan="6">No classes loaded</td></tr>'}</tbody>`;

  const dscpMap = data.dscp_map || [];
  const claimed = dscpMap.filter(d => d.claimed).length;
  $('#dscp-count').textContent = `${claimed} of ${dscpMap.length} code points are claimed by a class; the rest resolve to the default class`;
  $('#dscp-table').innerHTML = `<thead><tr><th>DSCP</th><th>Resolves to class</th><th>Claimed by policy</th><th>Queue ID (metadata)</th><th>Meter</th></tr></thead><tbody>${
    dscpMap.map(d => `<tr><td>${num(d.dscp)}</td><td>${escapeText(d.class_name)}</td><td>${d.claimed ? 'yes' : 'no — default'}</td><td>${num(d.queue_id)}</td><td>${num(d.meter_id)}</td></tr>`).join('')
    || '<tr><td colspan="5">No DSCP map computed</td></tr>'}</tbody>`;

  const traffic = data.traffic_map || [];
  $('#traffic-table').innerHTML = `<thead><tr><th>Traffic class</th><th>QoS class</th><th>Meter</th></tr></thead><tbody>${
    traffic.map(t => `<tr><td>${escapeText(t.traffic_class)}</td><td>${escapeText(t.class_name)}</td><td>${num(t.meter_id)}</td></tr>`).join('')
    || '<tr><td colspan="3">No traffic-class mapping in qos_policy.py</td></tr>'}</tbody>`;

  const wiring = data.wiring || {};
  const wiringRows = Object.entries(wiring).sort().map(([k, v]) => [k, v]);
  $('#qos-wiring').innerHTML = wiringRows.length
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
    const response = await fetch('/api/qos');
    if (!response.ok) throw new Error('QoS record unavailable');
    render(await response.json());
    $('#error').hidden = true;
  } catch (e) {
    $('#error').textContent = 'The local data service is unavailable. Reload after restarting the project.';
    $('#error').hidden = false;
  }
}
load();
setInterval(load, 10000);
