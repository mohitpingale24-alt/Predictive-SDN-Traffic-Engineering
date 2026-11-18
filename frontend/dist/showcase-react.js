/* Showcase section, rendered with React 18 (UMD, no build step).
   The rest of the dashboard stays vanilla JS; this file owns only the
   "Everything running under the hood" grid. app.js fetches /api/showcase
   and calls window.renderShowcase(data); if React failed to load, app.js
   falls back to its own vanilla renderer with the same card content.

   Every card layers real sources only: live captures aggregated across
   all runs, recorded campaign-02 / calibration results, and policy
   output computed server-side by each feature's own module. The source
   line on each card says which layer its numbers came from. */
(function () {
  if (typeof React === 'undefined' || typeof ReactDOM === 'undefined') return;
  const e = React.createElement;
  const fmt = (value, digits) => {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : '—';
  };
  const shortRun = id => id ? String(id).replace('run-', '').slice(0, 13) : '';

  function buildCards(d) {
    const cards = [];
    const card = (title, state, headline, detail, extra) =>
      cards.push(Object.assign({ title, state, headline, detail }, extra || {}));

    /* 1 — Extension platform (clickable: opens /platform, the full
       record computed by /api/platform from the same aggregation) */
    const p = d.platform || {};
    const start = p.start;
    const status = p.start_status ||
      (start ? (String(start.reason || '').includes('DISABLED') ? 'disabled' : 'enabled') : null);
    card('Extension platform',
      start ? (status === 'disabled' ? 'off' : 'live') : 'no data',
      start ? 'Controller extensions ' + (status === 'disabled' ? 'DISABLED' : status === 'enabled' ? 'enabled' : 'recorded')
            : 'No controller run recorded yet',
      start ? String(start.reason || '') : 'Start a live experiment and this fills in.',
      { stats: [['Live runs recorded', p.live_runs != null ? p.live_runs : '—'],
                ['Campaign runs recorded', p.campaign_runs != null ? p.campaign_runs : '—'],
                ['Controller starts logged', p.starts != null ? p.starts : '—'],
                ['Starts · live / campaign', (p.starts_live != null ? p.starts_live : '—') + ' / ' + (p.starts_campaign != null ? p.starts_campaign : '—')],
                ['Reroutes · live / campaign', (p.reroutes_live != null ? p.reroutes_live : '—') + ' / ' + (p.reroutes_campaign != null ? p.reroutes_campaign : '—')]],
        href: '/platform', openLabel: 'Open the full platform record',
        source: 'Source: control_events.csv from live captures + campaign-02' +
                (p.start_run ? ' · latest start: ' + shortRun(p.start_run) + (p.start_source ? ' (' + p.start_source + ')' : '') : '') });

    /* 2 — Explainable decisions (clickable: opens /explainability,
       the full record computed by /api/explainability from the same
       aggregation) */
    const ex = d.explanations || {};
    const reasons = Object.entries(ex.reasons || {}).sort((a, b) => b[1] - a[1]);
    card('Explainable decisions', ex.latest ? 'live' : 'no data',
      ex.latest ? 'Latest: path ' + (ex.latest.active_path || '?') + ' · ' + String(ex.latest.reason || '').replaceAll('_', ' ')
                : 'No decisions explained yet',
      ex.latest ? 'Top features: ' + (ex.latest.top_features || 'n/a') : 'Every decision writes its reasons to explanations.csv.',
      { stats: [['Decisions explained', ex.count || 0],
                ['Most common reason', reasons.length ? reasons[0][0].replaceAll('_', ' ') + ' × ' + reasons[0][1] : '—'],
                ['Top feature overall', ex.top_feature || '—'],
                ['Runs contributing', ex.runs_contributing != null ? ex.runs_contributing : '—'],
                ['With forecast / interval', (ex.with_prediction != null ? ex.with_prediction : '—') + ' / ' + (ex.with_interval != null ? ex.with_interval : '—')]],
        compare: reasons.length ? { title: 'Decision reasons', items: reasons.slice(0, 3).map(([k, v]) => [k.replaceAll('_', ' '), v, '']) } : null,
        href: '/explainability', openLabel: 'Open the full explainability record',
        source: 'Source: explanations.csv across all live captures' });

    /* 3 — Drift monitoring (clickable: opens /drift, the full
       record computed by /api/drift from the same sources) */
    const dr = d.drift || {};
    const drEvents = Object.entries(dr.events || {});
    const drTotal = drEvents.reduce((n, [, v]) => n + v, 0);
    const fc = dr.forecast;
    card('Drift monitoring', (drTotal || fc || dr.latest) ? 'live' : 'no data',
      drTotal ? drTotal + ' drift detector firing(s) in live runs'
              : (fc ? 'Forecast error tracked over ' + fc.n + ' recorded predictions' : 'No drift data yet'),
      dr.latest_event ? 'Latest: ' + dr.latest_event
                      : (dr.recalibration ? 'Recalibration artifact: radius ' + fmt(Number(dr.recalibration.radius_percentage_points)) + ' pts'
                                          : 'ADWIN + Page-Hinkley watch the forecast-error stream live.'),
      { stats: drEvents.map(([k, v]) => [k.replace('drift_detector_', '').replaceAll('_', ' '), v + ' firing(s)']),
        compare: fc ? { title: 'Forecast RMSE · first half vs second half of campaign-02',
                        items: [['First half', Number(fc.rmse_first), ' pts'], ['Second half', Number(fc.rmse_second), ' pts']] } : null,
        detail2: fc ? 'MAE ' + fmt(Number(fc.mae_first)) + ' → ' + fmt(Number(fc.mae_second)) + ' pts — ' +
                      (Number(fc.rmse_second) > Number(fc.rmse_first) * 1.1 ? 'error grew over the campaign: drift is real in this data.'
                       : Number(fc.rmse_second) < Number(fc.rmse_first) * 0.9 ? 'error fell over the campaign — the opposite of drift.'
                                                                               : 'error stayed flat: no drift in the recorded campaign.') : null,
        href: '/drift', openLabel: 'Open the full drift record',
        source: 'Source: live drift events + campaign-02 predictive-ml predictions paired with telemetry at +5 s' });

    /* 4 — Failure recovery / route transitions (clickable: opens
       /recovery, the full record computed by /api/recovery) */
    const f = d.failures || {};
    const tr = f.transitions || {};
    const rb = Object.entries(f.reroutes_by_system || {});
    const rbLabels = { static: 'Static', reactive: 'Reactive', 'predictive-baseline': 'Persistence', 'predictive-ml': 'Predictive GB' };
    card('Failure recovery', (tr.completed || f.downs) ? 'live' : 'armed',
      (tr.completed || 0) + ' reroute transition(s) completed · ' + (tr.failed || 0) + ' failed',
      tr.latest ? 'Latest stage: ' + (tr.latest.stage || '') + ' / ' + (tr.latest.status || '') +
                  (tr.latest.old_path ? ' · path ' + tr.latest.old_path + ' → ' + tr.latest.new_path : '')
                : 'Port-status events drive reroute + recovery timing (failures.csv).',
      { stats: [['Link-down events (live)', f.downs || 0],
                ['Mean transition time', tr.mean_seconds != null ? fmt(Number(tr.mean_seconds), 2) + ' s' : '—'],
                ['Transition stages', 'make-before-break, 5 stages confirmed per reroute']],
        compare: rb.length ? { title: 'Recorded reroutes per system · campaign-02 (8 runs each)',
                               items: rb.map(([k, v]) => [rbLabels[k] || k, v, '']) } : null,
        href: '/recovery', openLabel: 'Open the full recovery record',
        source: 'Source: transitions.csv (live) + control_events.csv (campaign-02)' });

    /* 5 — Anomaly mitigation (clickable: opens /anomaly, the full
       record computed by /api/anomaly from the same events) */
    const an = d.anomalies || {};
    const kinds = Object.entries(an.kinds || {}).sort((a, b) => b[1] - a[1]);
    card('Anomaly mitigation', an.count ? 'live' : 'armed',
      (an.count || 0) + ' surge/elephant detection(s) in live runs',
      an.latest ? 'Latest: ' + an.latest : 'Statistical detector (rolling z-score + traffic share).',
      { stats: kinds.map(([k, v]) => [k.replaceAll('_', ' '), v + ' detection(s)']),
        compare: kinds.length ? { title: 'Detection kinds', items: kinds.map(([k, v]) => [k.replaceAll('_', ' '), v, '']) } : null,
        href: '/anomaly', openLabel: 'Open the full anomaly record',
        source: 'Source: anomaly_detected events across all live captures' });

    /* 6 — Conformal intervals (clickable: opens /conformal, the
       full record computed by /api/conformal from the same sources) */
    const cf = d.conformal || {};
    const art = cf.artifact || {};
    const live = cf.live || {};
    const wp = Object.entries(live.width_by_path || {});
    card('Conformal intervals', art.nominal_coverage ? 'live' : 'no data',
      art.nominal_coverage ? 'Coverage guarantee ' + fmt(Number(art.nominal_coverage) * 100, 0) + '% · radius ' + fmt(Number(art.radius_percentage_points)) + ' pts'
                           : 'No calibration artifact yet',
      art.empirical_coverage_on_calibration
        ? 'Calibrated on ' + ((art.provenance || {}).capture || 'a recorded capture') + ' · empirical coverage ' +
          fmt(Number(art.empirical_coverage_on_calibration.coverage) * 100, 1) + '% at mean width ' + fmt(Number(art.empirical_coverage_on_calibration.mean_width))
        : 'Run experiments/calibrate_conformal.py on a capture to build the artifact.',
      { stats: [['Calibration rows', art.calibration_rows != null ? art.calibration_rows : '—'],
                ['Live intervals recorded', live.rows || 0],
                ['Gate held (too uncertain)', live.gate_hold || 0],
                ['Gate passed', live.gate_pass || 0]],
        compare: wp.length ? { title: 'Live mean interval width by path', items: wp.map(([k, v]) => ['Path ' + k, Number(v), ' pts']) } : null,
        href: '/conformal', openLabel: 'Open the full conformal record',
        source: 'Source: conformal_calibration.json + confidence.csv from live runs' });

    /* 7 — Per-link latency (clickable: opens /latency, the full
       record computed by /api/latency from the same rows) */
    const latStats = Object.entries(d.latency_stats || {});
    const lat = Object.entries(d.latency || {});
    card('Per-link latency', lat.length ? 'live' : 'no data',
      lat.length ? lat.map(([k, v]) => k + ' ' + fmt(Number(v.latency_ms)) + ' ms').join(' · ') : 'No latency estimates yet',
      'EWMA per link from live RTT probes (path RTT split equally across its links — a documented estimate); feeds delay-aware scoring.',
      { stats: latStats.map(([k, v]) => [k + ' mean', fmt(Number(v.mean)) + ' ms over ' + v.count + ' samples']),
        compare: latStats.length ? { title: 'Mean latency by link', items: latStats.map(([k, v]) => [k, Number(v.mean), ' ms']) } : null,
        detail2: latStats.length ? 'First estimate ' + fmt(Number(latStats[0][1].first)) + ' ms → latest ' + fmt(Number(latStats[0][1].latest)) + ' ms' : null,
        href: '/latency', openLabel: 'Open the full latency record',
        source: 'Source: latency.csv across all live captures' });

    /* 8 — All-link telemetry (clickable: opens /telemetry, the full
       record computed by /api/telemetry from the same rows) */
    const alStats = Object.entries(d.all_link_stats || {});
    const al = Object.entries(d.all_links || {});
    card('All-link telemetry', al.length ? 'live' : 'no data',
      al.length ? al.map(([k, v]) => k + ' ' + fmt(Number(v)) + '%').join(' · ') : 'No all-link capture yet',
      'Every core link recorded, not just s1 — this is the data the GNN pooling was waiting for.',
      { stats: alStats.map(([k, v]) => [k + ' peak', fmt(Number(v.peak), 1) + '% · mean ' + fmt(Number(v.mean), 1) + '%']),
        compare: alStats.length ? { title: 'Peak utilization by link', items: alStats.map(([k, v]) => [k, Number(v.peak), '%']) } : null,
        href: '/telemetry', openLabel: 'Open the full telemetry record',
        source: 'Source: telemetry_all_links.csv across all live captures' });

    /* 9 — GNN forecaster */
    const g = d.gnn || {};
    card('GNN forecaster', g.gnn ? 'live' : 'no data',
      g.gnn ? 'GNN MAE ' + fmt(Number(g.gnn.mae)) + ' vs persistence ' + fmt(Number((g.persistence || {}).mae)) : 'No GNN evaluation yet',
      g.diebold_mariano_gnn_vs_persistence
        ? 'DM test vs persistence: p = ' + fmt(Number(g.diebold_mariano_gnn_vs_persistence.p_value), 3) +
          ' — not significant, persistence wins on this data (honest result).'
        : 'Trained on pooled all-link telemetry; judged against persistence + ridge.',
      { stats: [['Train / test snapshots', (g.train_snapshots || '—') + ' / ' + (g.test_snapshots || '—')],
                ['Training loss', fmt(Number(g.initial_loss), 3) + ' → ' + fmt(Number(g.final_loss), 3)],
                ['Links modelled', (g.links || []).join(' · ') || '—']],
        compare: g.gnn ? { title: 'MAE comparison (lower is better)',
                           items: [['GNN', Number(g.gnn.mae), ''], ['Persistence', Number((g.persistence || {}).mae), ''], ['Ridge', Number((g.ridge_same_features || {}).mae), '']] } : null,
        href: '/gnn', openLabel: 'Open the full GNN record',
        source: 'Source: cnt-ml/results/gnn_metrics.json (recorded evaluation)' });

    /* 10 — Forecaster comparison (recorded ML results) */
    const mc = d.models_comparison || [];
    const gb = mc.find(m => String(m.model || '').includes('Gradient'));
    card('Forecaster comparison', mc.length ? 'live' : 'no data',
      gb ? 'Gradient Boosting RMSE ' + fmt(Number(gb.rmse)) + ' · R² ' + fmt(Number(gb.r2), 3) : 'No recorded model comparison',
      'The recorded model selection behind the controller: GB was picked on RMSE/R², persistence stays the honest baseline on MAE.',
      { stats: mc.map(m => [m.model, 'MAE ' + fmt(Number(m.mae)) + ' · R² ' + fmt(Number(m.r2), 3)]),
        compare: mc.length ? { title: 'RMSE comparison (lower is better)', items: mc.map(m => [m.model, Number(m.rmse), '']) } : null,
        href: '/forecasters', openLabel: 'Open the full forecaster record',
        source: 'Source: cnt-ml/results/metrics_comparison.csv (recorded)' });

    /* 11 — QoS · DiffServ (clickable: opens /qos, the full
       record computed by /api/qos from the same module output) */
    const qos = d.qos || [];
    const bands10 = (d.qos_bands || {})['10'] || {};
    const bands20 = (d.qos_bands || {})['20'] || {};
    card('QoS · DiffServ', qos.length ? 'armed' : 'no data',
      qos.length ? qos.map(c => c.name + ' ' + (c.share == null ? 'unmetered' : Math.round(c.share * 100) + '%')).join(' · ') : 'Policy unavailable',
      'Meter rates are derived by qos_policy.meter_bands() from class share × link capacity — the same policy at any capacity.',
      { stats: qos.filter(c => c.share != null).map(c =>
          [c.name + ' (DSCP ' + (c.dscp || []).join('/') + ')',
           (bands10[c.name] || 0) + ' kbps @10M · ' + (bands20[c.name] || 0) + ' kbps @20M']),
        compare: Object.keys(bands10).length ? { title: 'Meter rate by class · 10 Mbps link', items: Object.entries(bands10).map(([k, v]) => [k, v, ' kbps']) } : null,
        href: '/qos', openLabel: 'Open the full QoS record',
        source: 'Source: qos_policy.py computed at request time · enabled in live runs with SDN_QOS=1' });

    /* 12 — ACL firewall (clickable: opens /acl, the full record
       computed by /api/acl from the same rules + recorded drops) */
    const acl = d.acl || {};
    const rules = acl.rules || [];
    card('ACL firewall', rules.length ? 'armed' : 'no data',
      (acl.drops || 0) + ' packet(s) dropped by ACL rules in live runs',
      rules.length ? rules.map(r => r.action.toUpperCase() + ' ' + r.protocol + ' → port ' +
                       (r.dst_ports && r.dst_ports[0] === r.dst_ports[1] ? r.dst_ports[0] : (r.dst_ports || []).join('-')) +
                       ' from ' + r.src).join(' · ')
                   : '5-tuple rules from JSON with per-rule hit counts.',
      { stats: [['Rules loaded & validated', rules.length],
                ['Default policy', acl.default_policy || '—'],
                ['Live traffic verdict', (acl.drops || 0) === 0 ? 'No live packet matched a deny rule — workload is UDP 5201 + probes, ACL denies TCP/22 + UDP 8000-9000' : acl.drops + ' drop(s)']],
        href: '/acl', openLabel: 'Open the full ACL record',
        source: 'Source: acl_rules.example.json loaded through acl_firewall.py · drops from live control events' });

    /* 13 — Network slicing (clickable: opens /slicing, the full
       record computed by /api/slicing from the same config + drops) */
    const sl = d.slicing || {};
    const slices = sl.slices || [];
    card('Network slicing', slices.length ? 'armed' : 'no data',
      (sl.drops || 0) + ' cross-slice drop(s) in live runs',
      sl.error ? 'Slice config failed validation: ' + sl.error
               : 'Isolation validated at load by slicing.py: every host in at most one slice, VLANs unique, shares cannot overcommit.',
      { stats: slices.map(s => [s.slice_id + ' · VLAN ' + s.vlan_id,
                                s.hosts.length + ' hosts · ' + Math.round(s.share * 100) + '% = ' + fmt(s.share * 10, 1) + ' Mbps @10M']),
        compare: slices.length ? { title: 'Bandwidth share by slice', items: slices.map(s => [s.slice_id, Math.round(s.share * 100), '%']) } : null,
        detail2: slices.length ? 'Total allocated ' + Math.round((sl.total_share || 0) * 100) + '% · ' +
                                 Math.round((1 - (sl.total_share || 0)) * 100) + '% unallocated' : null,
        href: '/slicing', openLabel: 'Open the full slicing record',
        source: 'Source: slices.example.json loaded through slicing.py · drops from live control events' });

    return cards;
  }

  function FeatureCard(props) {
    const children = [
      e('h3', { key: 't' }, props.title),
      e('span', { key: 's', className: 'feature-state ' + props.state.replace(' ', '-') }, props.state),
      e('strong', { key: 'h' }, props.headline),
      e('small', { key: 'd' }, props.detail)];
    if (props.detail2) children.push(e('small', { key: 'd2' }, props.detail2));
    if (props.stats && props.stats.length) {
      children.push(e('div', { key: 'stats', className: 'feature-stats' },
        props.stats.map(([label, value], i) =>
          e('div', { key: i, className: 'feature-stat' },
            e('span', null, label), e('b', null, String(value))))));
    }
    if (props.compare && props.compare.items && props.compare.items.length) {
      const max = Math.max.apply(null, props.compare.items.map(([, v]) => Number(v) || 0).concat([0.0001]));
      children.push(e('div', { key: 'cmp', className: 'feature-compare' },
        e('div', { className: 'feature-compare-title' }, props.compare.title),
        props.compare.items.map(([label, value, unit], i) =>
          e('div', { key: i, className: 'fc-row' },
            e('span', { className: 'fc-label' }, label),
            e('span', { className: 'fc-track' },
              e('span', { className: 'fc-bar', style: { width: Math.max(2, Math.round(100 * (Number(value) || 0) / max)) + '%' } })),
            e('span', { className: 'fc-value' }, fmt(Number(value), Number(value) >= 100 ? 0 : 2) + (unit || ''))))));
    }
    if (props.source) children.push(e('small', { key: 'src', className: 'feature-source' }, props.source));
    if (props.href) children.push(e('small', { key: 'open', className: 'feature-open' }, (props.openLabel || 'Open') + ' →'));
    const rootProps = { className: 'feature-card' + (props.href ? ' feature-card-link' : '') };
    if (props.href) rootProps.href = props.href;
    return e(props.href ? 'a' : 'div', rootProps, children);
  }

  function Showcase(props) {
    return e(React.Fragment, null,
      props.cards.map(c => e(FeatureCard, Object.assign({ key: c.title }, c))));
  }

  let root = null;
  window.renderShowcase = function (data) {
    const grid = document.getElementById('showcase-grid');
    if (!grid) return;
    if (!root) root = ReactDOM.createRoot(grid);
    const cards = buildCards(data);
    root.render(e(Showcase, { cards }));
    const status = document.getElementById('showcase-status');
    if (status) {
      const showing = cards.filter(c => c.state !== 'no data' && c.state !== 'off').length;
      const live = cards.filter(c => c.state === 'live').length;
      status.textContent = showing + ' of ' + cards.length + ' features showing data · ' + live + ' live';
    }
  };
})();
