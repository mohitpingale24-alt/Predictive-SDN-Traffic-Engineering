"""Loopback-only SDN dashboard with constrained, isolated experiment controls."""
import argparse
import csv
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
import re
import secrets
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))
from analyze_results import (SYSTEMS, load_run, summarize, summarize_statistics, read_csv,
                             event_timeline, active_utilization_rows, validate_controlled_campaign, PING_PATTERN)
import live_runtime

CONTROL_TOKEN = secrets.token_urlsafe(32)

CAMPAIGN = ROOT / 'final-evaluation-artifacts/campaign-02'
ASSETS = Path(__file__).resolve().parent / 'dist'
LABELS = dict(zip(SYSTEMS, ('Static', 'Reactive', 'Persistence', 'Predictive GB')))
LABELS.update({'predictive-safe': 'GB + confidence + safe',
               'predictive-confidence': 'GB + confidence', 'predictive-safe-only': 'GB + safe',
               'ecmp': 'ECMP (capacity-weighted)'})
METRICS = [('P95_Latency_ms', 'P95 RTT', 'ms'), ('Average_Throughput_Mbps', 'Goodput', 'Mbps'),
           ('Average_Packet_Loss_pct', 'Packet loss', '%'), ('Congestion_Duration_seconds', 'Congestion', 's'),
           ('Average_Latency_ms', 'Average RTT', 'ms'), ('Average_Jitter_ms', 'Jitter', 'ms'),
           ('Congestion_Events', 'Congestion events', ''), ('Successful_Reroutes', 'Successful relief actions', ''),
           ('False_Reroutes', 'False reroutes', ''), ('Recovery_Reroutes', 'Recovery actions', ''),
           ('Congestion_Avoidance_Rate', 'Episode avoidance', 'fraction'),
           ('Prediction_to_Reroute_ms', 'Prediction to decision', 'ms'),
           ('Mean_Inference_ms', 'Inference time', 'ms'), ('Mean_Controller_CPU_pct', 'Controller CPU', '%')]

def safe(value):
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, dict): return {k: safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [safe(v) for v in value]
    return value

def catalog():
    entries, loaded, issues = [], {s: [] for s in SYSTEMS}, []
    for system in SYSTEMS:
        for path in sorted((CAMPAIGN / system).glob('run-*/metadata.json')):
            try:
                meta = json.loads(path.read_text())
                status = meta.get('status', 'running')
                if status == 'complete':
                    run = load_run(path.parent)
                    loaded[system].append((path.parent.name, run))
                    status = 'verified'
                entries.append(dict(system=system, id=path.parent.name, status=status))
            except (ValueError, OSError, KeyError):
                issues.append(system + '/' + path.parent.name)
    complete = all(len(runs) == 8 for runs in loaded.values()) and not issues
    if complete:
        try: validate_controlled_campaign(loaded)
        except ValueError:
            issues.append('Unmatched experiment configuration')
            complete = False
    rows = []
    for system in SYSTEMS:
        for name, run in loaded[system]:
            reference = dict(loaded['static']).get(name) if system != 'static' else None
            rows.append(dict(Repetition=name, **summarize(run, reference)))
    stats = []
    for system in SYSTEMS:
        own = [row for row in rows if row['System'] == system]
        if own:
            # The shared aggregator expects all systems; use its primitive.
            from analyze_results import mean_confidence_interval
            for metric, _, _ in METRICS:
                n, mean, sd, low, high = mean_confidence_interval([row[metric] for row in own])
                stats.append(dict(system=system, metric=metric, n=n, mean=mean, sd=sd, low=low, high=high))
    return safe(dict(complete=complete, verified=sum(len(v) for v in loaded.values()), total=32,
                     runs=entries, issues=issues, stats=stats, metrics=METRICS,
                     labels=LABELS, excluded=len(list((CAMPAIGN / 'excluded').glob('*/metadata.json')))))

def run_data(system, name):
    if system not in SYSTEMS or name not in {f'run-{i:02d}' for i in range(1, 9)}:
        raise ValueError('Unknown run')
    directory = CAMPAIGN / system / name
    reference = load_run(CAMPAIGN / 'static' / name) if system != 'static' else None
    return capture_data(directory, system, name, reference)


def capture_data(directory, system, name, reference=None):
    meta = json.loads((directory / 'metadata.json').read_text())
    if not isinstance(meta, dict): raise ValueError('Invalid capture metadata')
    if meta.get('status') == 'failed': raise ValueError('Failed capture is excluded')
    verified = meta.get('status') == 'complete'
    summary = None
    if verified:
        if meta.get('research_schema') == 1:
            from analyze_research import summarize_research
            summary = summarize_research(directory, reference)
        else:
            run = load_run(directory)
            summary = summarize(run, reference)
    telemetry = read_csv(directory / 'telemetry.csv')
    predictions = read_csv(directory / 'predictions.csv')
    events = read_csv(directory / 'control_events.csv')
    start = meta.get('workload_started_at')
    if start is None: return dict(pending=True, system=system, id=name)
    start = float(start)
    if not math.isfinite(start): raise ValueError('Invalid workload timestamp')
    points = []
    intervals = {(row['timestamp'], row['path']): row for row in read_csv(directory / 'confidence.csv')}
    decisions = read_csv(directory / 'decisions.csv')
    for row in predictions:
        try:
            interval = intervals.get((row['timestamp'], row['path']), {})
            points.append(dict(t=round(float(row['timestamp'])-start, 3), path=row['path'],
                               active=row['active_path'], actual=float(row['current_utilization']),
                               forecast=float(row['predicted_utilization']) if row['prediction_status'] == 'predicted' else None,
                               status=row['prediction_status'],
                               lower=float(interval['lower']) if interval.get('lower') else None,
                               upper=float(interval['upper']) if interval.get('upper') else None,
                               confidence_reason=interval.get('gate_reason')))
        except (ValueError, KeyError, TypeError): continue
    reroutes = []
    for row in events:
        try:
            if row.get('event') == 'reroute' and row.get('old_path') in ('A', 'B') and row.get('new_path') in ('A', 'B'):
                reroutes.append(dict(t=round(float(row['timestamp'])-start, 2), old=row['old_path'], new=row['new_path']))
        except (ValueError, KeyError, TypeError): continue
    end = max([p['t'] for p in points] or [0])
    decision_points = []
    for row in decisions:
        try: decision_points.append(dict(t=float(row['timestamp']) - start,
                                         source=row['source'], reason=row['reason'],
                                         candidate=row.get('candidate', '')))
        except (ValueError, KeyError): continue
    transitions = []
    for row in read_csv(directory / 'transitions.csv'):
        try: transitions.append(dict(t=float(row['timestamp']) - start, id=row['transition_id'],
                                     stage=row['stage'], status=row['status'], detail=row['detail']))
        except (ValueError, KeyError): continue
    transition_metrics = None
    if verified and (directory / 'transition_metrics.json').exists():
        transition_metrics = json.loads((directory / 'transition_metrics.json').read_text())
    return safe(dict(system=system, id=name, verified=verified, live=not verified, summary=summary,
                     points=points, events=reroutes, duration=end, start=start, workload=meta['workload'],
                     research=meta.get('research_schema') == 1, scenario=meta.get('scenario', 'legacy'),
                     topology=meta.get('topology', 'diamond4'),
                     capacities=meta.get('path_capacities_mbps', {'A': 10, 'B': 20}),
                     decisions=decision_points, transitions=transitions, transition_metrics=transition_metrics,
                     confidence=meta.get('confidence')))


def _tail_csv(path, limit=50):
    if not path.exists(): return []
    try: rows = read_csv(path)
    except (OSError, ValueError): return []
    return safe(rows[-limit:])

def topnotch_data():
    results = ROOT / 'cnt-ml/results'
    return dict(explanations=_tail_csv(results / 'explanations.csv'),
                drift=_tail_csv(results / 'drift.csv'),
                failures=_tail_csv(results / 'failures.csv'))

def _read_json(path):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return None

def _latest_by(rows, key):
    latest = {}
    for row in rows:
        if row.get(key): latest[row[key]] = row
    return latest

def _capture_file(name):
    """Newest live-experiment capture file with this name, or None."""
    try:
        candidates = sorted((ROOT / 'live-experiments').glob(
            'run-*/capture/' + name), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return candidates[-1] if candidates else None


def _capture_csv(name, limit, fallback):
    """Rows from the newest capture that recorded this CSV (failed runs
    leave empty files, so keep walking back), else the local results dir.
    Returns (rows, run_id_or_None)."""
    try:
        candidates = sorted((ROOT / 'live-experiments').glob(
            'run-*/capture/' + name), key=lambda p: p.stat().st_mtime)
    except OSError:
        candidates = []
    for path in reversed(candidates):
        rows = _tail_csv(path, limit)
        if rows:
            return rows, path.parent.parent.name
    return _tail_csv(fallback, limit), None


def _capture_json(name, fallback):
    path = _capture_file(name)
    if path is not None:
        data = _read_json(path)
        if data:
            return data
    return _read_json(fallback)


def _all_capture_rows(name, per_file_limit=4000):
    """Rows from every live capture that recorded this CSV, oldest
    capture first, plus the newest capture id that contributed."""
    try:
        paths = sorted((ROOT / 'live-experiments').glob(
            'run-*/capture/' + name), key=lambda p: p.stat().st_mtime)
    except OSError:
        return [], None
    rows, newest = [], None
    for path in paths:
        part = _tail_csv(path, per_file_limit)
        if part:
            rows.extend(part)
            newest = path.parent.parent.name
    return rows, newest


def _iso_time(value):
    stamp = _num(value)
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp).isoformat(timespec='seconds')
    except (OverflowError, OSError, ValueError):
        return None


def _topnotch_status(reason):
    """Extension status exactly as the controller logged it at start.

    Newer runs log 'topnotch=enabled' (or 'topnotch=DISABLED (import
    error: ...)' after the silent-degradation fix); older recorded
    runs predate that logging and honestly report 'not reported'."""
    text = str(reason or '')
    if 'DISABLED' in text:
        return 'disabled'
    if 'topnotch=enabled' in text:
        return 'enabled'
    return 'not reported'


def _event_counts(rows):
    counts = {}
    for row in rows:
        name = row.get('event') or 'unknown'
        counts[name] = counts.get(name, 0) + 1
    return counts


def _run_event_summary(rows):
    stamps = [s for s in (_num(r.get('timestamp')) for r in rows)
              if s is not None]
    return dict(events=len(rows), counts=_event_counts(rows),
                starts=sum(1 for r in rows
                           if r.get('event') == 'controller_start'),
                reroutes=sum(1 for r in rows if r.get('event') == 'reroute'),
                first=_iso_time(min(stamps)) if stamps else None,
                last=_iso_time(max(stamps)) if stamps else None,
                duration_seconds=(round(max(stamps) - min(stamps), 2)
                                  if len(stamps) >= 2 else None))


def _platform_sources():
    """Every control_events.csv the platform can honestly claim.

    Three real layers, each read at request time: live-experiment
    captures, the recorded campaign-02 runs, and the offline results
    directory (used only when neither of the first two recorded
    anything). Returns per-run records plus the list of files read,
    so the showcase card and the platform detail page are computed
    from the same aggregation and can never disagree."""
    live, campaign, sources = [], [], []
    try:
        live_dirs = sorted((ROOT / 'live-experiments').glob('run-*'),
                           key=lambda p: p.stat().st_mtime)
    except OSError:
        live_dirs = []
    for directory in live_dirs:
        capture = directory / 'capture'
        rows = _tail_csv(capture / 'control_events.csv', 100000)
        session = _read_json(directory / 'session.json') or {}
        files = []
        if capture.is_dir():
            files = sorted(p.name for p in capture.iterdir() if p.is_file())
        if rows:
            sources.append(str((capture / 'control_events.csv')
                               .relative_to(ROOT)))
        live.append(dict(id=directory.name, source='live', rows=rows,
                         system=session.get('system'),
                         scenario=session.get('scenario'),
                         topology=session.get('topology'),
                         status=session.get('status'), files=files))
    for system in SYSTEMS:
        for directory in sorted((CAMPAIGN / system).glob('run-*')):
            rows = _tail_csv(directory / 'control_events.csv', 100000)
            meta = _read_json(directory / 'metadata.json') or {}
            if rows:
                sources.append(str((directory / 'control_events.csv')
                                   .relative_to(ROOT)))
            campaign.append(dict(id=directory.name, source='campaign',
                                 system=system, rows=rows,
                                 scenario=meta.get('scenario'),
                                 topology=meta.get('topology'),
                                 status=meta.get('status'), files=None))
    results_rows = _tail_csv(ROOT / 'cnt-ml/results/control_events.csv',
                             100000)
    if results_rows:
        sources.append('cnt-ml/results/control_events.csv')
    return live, campaign, results_rows, sources


def _platform_starts(records):
    """Every controller_start across the given records, oldest first,
    each tagged with the run it came from."""
    starts = []
    for record in records:
        for row in record['rows']:
            if row.get('event') == 'controller_start':
                starts.append(dict(
                    run=record['id'], source=record['source'],
                    system=record.get('system'),
                    timestamp=_num(row.get('timestamp')),
                    time=_iso_time(row.get('timestamp')),
                    control_mode=row.get('control_mode'),
                    initial_path=row.get('new_path'),
                    reason=row.get('reason') or '',
                    topnotch=_topnotch_status(row.get('reason'))))
    starts.sort(key=lambda s: s['timestamp'] or 0)
    return starts


RECENT_EVENT_LIMIT = 200


def platform_detail():
    """Full Extension-platform record for the detail page.

    Everything is computed at request time from the recorded
    control_events.csv files (live captures + campaign-02, with the
    offline results file as fallback): run inventories, controller
    starts with their logged extension status, per-run and per-system
    event counts, and the most recent events. No number is stored in
    this file; the payload names every file it was computed from."""
    live, campaign, results_rows, sources = _platform_sources()
    records = live + campaign
    starts = _platform_starts(records)
    totals, totals_live, totals_campaign = {}, {}, {}
    for record in records:
        bucket = totals_live if record['source'] == 'live' \
            else totals_campaign
        for name, value in _event_counts(record['rows']).items():
            totals[name] = totals.get(name, 0) + value
            bucket[name] = bucket.get(name, 0) + value
    if results_rows and not totals:
        starts = _platform_starts(
            [dict(id='cnt-ml/results', source='results', system=None,
                  rows=results_rows)])
        totals = _event_counts(results_rows)

    def public_run(record):
        entry = dict(id=record['id'], source=record['source'],
                     system=record.get('system'),
                     scenario=record.get('scenario'),
                     topology=record.get('topology'),
                     status=record.get('status'), files=record['files'])
        entry.update(_run_event_summary(record['rows']))
        own = [s for s in starts if s['run'] == record['id']
               and s['source'] == record['source']]
        entry['topnotch'] = own[-1]['topnotch'] if own else None
        return entry

    recent = []
    for record in records:
        for row in record['rows']:
            recent.append(dict(
                run=record['id'], source=record['source'],
                system=record.get('system'),
                timestamp=_num(row.get('timestamp')),
                time=_iso_time(row.get('timestamp')),
                event=row.get('event'), control_mode=row.get('control_mode'),
                old_path=row.get('old_path'), new_path=row.get('new_path'),
                reason=row.get('reason') or ''))
    recent.sort(key=lambda r: r['timestamp'] or 0)

    campaign_by_system = {}
    for record in campaign:
        group = campaign_by_system.setdefault(
            record['system'], dict(system=record['system'], runs=[]))
        group['runs'].append(public_run(record))
    for group in campaign_by_system.values():
        group['events'] = sum(r['events'] for r in group['runs'])
        group['starts'] = sum(r['starts'] for r in group['runs'])
        group['reroutes'] = sum(r['reroutes'] for r in group['runs'])

    return safe(dict(
        summary=dict(
            live_runs=len(live),
            live_runs_with_events=sum(1 for r in live if r['rows']),
            campaign_runs=len(campaign),
            campaign_runs_with_events=sum(1 for r in campaign if r['rows']),
            campaign_systems=sorted(campaign_by_system),
            starts=len(starts),
            starts_live=sum(1 for s in starts if s['source'] == 'live'),
            starts_campaign=sum(1 for s in starts
                                  if s['source'] == 'campaign'),
            reroutes=totals.get('reroute', 0),
            reroutes_live=totals_live.get('reroute', 0),
            reroutes_campaign=totals_campaign.get('reroute', 0),
            events=sum(totals.values()),
            event_counts=totals, event_counts_live=totals_live,
            event_counts_campaign=totals_campaign),
        latest_start=starts[-1] if starts else None,
        starts=starts,
        live_runs_detail=[public_run(r) for r in reversed(live)],
        campaign_by_system=[campaign_by_system[name]
                            for name in sorted(campaign_by_system)],
        recent_events=recent[-RECENT_EVENT_LIMIT:],
        recent_limit=RECENT_EVENT_LIMIT,
        sources=sources))


EXPLANATION_RECENT_LIMIT = 200


def _explanation_sources():
    """Every explanations.csv the explainability record can claim.

    Same three real layers as the platform record, each read at
    request time: live-experiment captures, the recorded campaign-02
    runs, and the offline results directory. Returns per-run records
    plus the list of files that actually contributed rows, so the
    showcase card and the explainability detail page are computed
    from the same aggregation and can never disagree."""
    live, campaign, sources = [], [], []
    try:
        live_dirs = sorted((ROOT / 'live-experiments').glob('run-*'),
                           key=lambda p: p.stat().st_mtime)
    except OSError:
        live_dirs = []
    for directory in live_dirs:
        capture = directory / 'capture'
        rows = _tail_csv(capture / 'explanations.csv', 100000)
        session = _read_json(directory / 'session.json') or {}
        files = []
        if capture.is_dir():
            files = sorted(p.name for p in capture.iterdir() if p.is_file())
        if rows:
            sources.append(str((capture / 'explanations.csv')
                               .relative_to(ROOT)))
        live.append(dict(id=directory.name, source='live', rows=rows,
                         system=session.get('system'),
                         scenario=session.get('scenario'),
                         topology=session.get('topology'),
                         status=session.get('status'), files=files))
    for system in SYSTEMS:
        for directory in sorted((CAMPAIGN / system).glob('run-*')):
            rows = _tail_csv(directory / 'explanations.csv', 100000)
            meta = _read_json(directory / 'metadata.json') or {}
            if rows:
                sources.append(str((directory / 'explanations.csv')
                                   .relative_to(ROOT)))
            campaign.append(dict(id=directory.name, source='campaign',
                                 system=system, rows=rows,
                                 scenario=meta.get('scenario'),
                                 topology=meta.get('topology'),
                                 status=meta.get('status'), files=None))
    results_rows = _tail_csv(ROOT / 'cnt-ml/results/explanations.csv',
                             100000)
    if results_rows:
        sources.append('cnt-ml/results/explanations.csv')
    return live, campaign, results_rows, sources


def _parse_top_features(value):
    """'name:weight;name:weight' -> [(name, weight)] as logged."""
    parsed = []
    for part in str(value or '').split(';'):
        if ':' in part:
            name, _, weight = part.rpartition(':')
            number = _num(weight)
            if name.strip() and number is not None:
                parsed.append((name.strip(), number))
    return parsed


def _stats_block(values):
    """n / mean / sample SD / min / max over the numeric values."""
    clean = [v for v in values if v is not None]
    if not clean:
        return dict(n=0, mean=None, sd=None, min=None, max=None)
    mean = sum(clean) / len(clean)
    sd = (math.sqrt(sum((v - mean) ** 2 for v in clean) / (len(clean) - 1))
          if len(clean) > 1 else 0.0)
    return dict(n=len(clean), mean=mean, sd=sd,
                min=min(clean), max=max(clean))


def explainability_detail():
    """Full explainable-decisions record for the detail page.

    Everything is computed at request time from the recorded
    explanations.csv files: decision counts and shares by reason,
    decision source and path; per-feature appearance counts and
    contribution totals exactly as logged in top_features; signal,
    forecast and interval statistics; per-run inventories; and the
    most recent individual explanations. No number is stored in
    this file; the payload names every file it was computed from.
    The results-directory file is used only when no live capture
    and no campaign run recorded explanations (the showcase's own
    fallback rule), so a decision is never counted twice."""
    live, campaign, results_rows, sources = _explanation_sources()
    records = live + campaign
    using_results = False
    if not any(r['rows'] for r in records) and results_rows:
        records = records + [dict(id='cnt-ml/results', source='results',
                                  system=None, scenario=None,
                                  topology=None, status=None, files=None,
                                  rows=results_rows)]
        using_results = True

    tagged = []
    for record in records:
        for row in record['rows']:
            tagged.append((record, row))
    total = len(tagged)
    stamps = [s for s in (_num(r.get('timestamp')) for _, r in tagged)
              if s is not None]

    def share(count):
        return (count / total) if total else None

    reason_rows, decision_source_rows = {}, {}
    active_rows, candidate_rows, transition_rows = {}, {}, {}
    for record, row in tagged:
        reason_rows.setdefault(row.get('reason') or '?', []).append(row)
        decision_source_rows.setdefault(row.get('source') or '?',
                                        []).append(row)
        active_rows.setdefault(row.get('active_path') or '?',
                               []).append(row)
        candidate_rows.setdefault(row.get('candidate') or '(stayed)',
                                  []).append(row)
        key = (row.get('active_path') or '?') + ' → ' + \
            (row.get('candidate') or '(stayed)')
        transition_rows.setdefault(key, []).append(row)

    def breakdown(groups):
        entries = []
        for name, rows in groups.items():
            entries.append(dict(
                name=name, count=len(rows), share=share(len(rows)),
                mean_prediction=_stats_block(
                    [_num(r.get('prediction')) for r in rows])['mean'],
                mean_projected_improvement=_stats_block(
                    [_num(r.get('projected_improvement_points'))
                     for r in rows])['mean']))
        return sorted(entries, key=lambda e: -e['count'])

    feature_appear, feature_total, feature_values = {}, {}, {}
    rows_with_features = 0
    for _, row in tagged:
        parsed = _parse_top_features(row.get('top_features'))
        if parsed:
            rows_with_features += 1
        seen = set()
        for name, weight in parsed:
            feature_total[name] = feature_total.get(name, 0.0) + weight
            feature_values.setdefault(name, []).append(weight)
            if name not in seen:
                feature_appear[name] = feature_appear.get(name, 0) + 1
                seen.add(name)
    contribution_sum = sum(feature_total.values())
    features = []
    for name, total_weight in feature_total.items():
        features.append(dict(
            feature=name, appearances=feature_appear.get(name, 0),
            appearance_share=share(feature_appear.get(name, 0)),
            total_contribution=total_weight,
            mean_contribution=(total_weight / len(feature_values[name])
                               if feature_values.get(name) else None),
            contribution_share=(total_weight / contribution_sum
                                if contribution_sum else None)))
    features.sort(key=lambda f: -f['total_contribution'])

    intervals = [(row, _num(row.get('lower')), _num(row.get('upper')))
                 for _, row in tagged]
    interval_rows = [(row, lo, hi) for row, lo, hi in intervals
                     if lo is not None and hi is not None]
    widths = [hi - lo for _, lo, hi in interval_rows]
    inside = sum(1 for row, lo, hi in interval_rows
                 if _num(row.get('prediction')) is not None
                 and lo <= _num(row.get('prediction')) <= hi)
    with_prediction = sum(1 for _, r in tagged
                          if _num(r.get('prediction')) is not None)
    with_candidate = sum(1 for _, r in tagged if r.get('candidate'))

    def public_run(record):
        rows = record['rows']
        own_stamps = [s for s in (_num(r.get('timestamp')) for r in rows)
                      if s is not None]
        reasons = {}
        for row in rows:
            name = row.get('reason') or '?'
            reasons[name] = reasons.get(name, 0) + 1
        return dict(
            id=record['id'], source=record['source'],
            system=record.get('system'), scenario=record.get('scenario'),
            topology=record.get('topology'), status=record.get('status'),
            files=record['files'], decisions=len(rows), reasons=reasons,
            with_prediction=sum(1 for r in rows
                                if _num(r.get('prediction')) is not None),
            with_interval=sum(1 for r in rows
                              if _num(r.get('lower')) is not None
                              and _num(r.get('upper')) is not None),
            with_features=sum(1 for r in rows
                              if _parse_top_features(r.get('top_features'))),
            first=_iso_time(min(own_stamps)) if own_stamps else None,
            last=_iso_time(max(own_stamps)) if own_stamps else None,
            duration_seconds=(round(max(own_stamps) - min(own_stamps), 2)
                              if len(own_stamps) >= 2 else None))

    recent = []
    for record, row in tagged:
        recent.append(dict(
            run=record['id'], source=record['source'],
            system=record.get('system'),
            timestamp=_num(row.get('timestamp')),
            time=_iso_time(row.get('timestamp')),
            active_path=row.get('active_path'),
            candidate=row.get('candidate') or '',
            decision_source=row.get('source'), reason=row.get('reason'),
            signal_a=_num(row.get('signal_a')),
            signal_b=_num(row.get('signal_b')),
            prediction=_num(row.get('prediction')),
            lower=_num(row.get('lower')), upper=_num(row.get('upper')),
            top_features=row.get('top_features') or '',
            projected_improvement_points=_num(
                row.get('projected_improvement_points'))))
    recent.sort(key=lambda r: r['timestamp'] or 0)

    return safe(dict(
        summary=dict(
            decisions=total,
            runs_contributing=sum(1 for r in records if r['rows']),
            live_runs=len(live), campaign_runs=len(campaign),
            using_results_fallback=using_results,
            distinct_reasons=len(reason_rows),
            distinct_features=len(features),
            with_prediction=with_prediction,
            with_prediction_share=share(with_prediction),
            with_interval=len(interval_rows),
            with_interval_share=share(len(interval_rows)),
            predictions_inside_interval=inside,
            with_features=rows_with_features,
            with_features_share=share(rows_with_features),
            with_candidate=with_candidate,
            first=_iso_time(min(stamps)) if stamps else None,
            last=_iso_time(max(stamps)) if stamps else None,
            duration_seconds=(round(max(stamps) - min(stamps), 2)
                              if len(stamps) >= 2 else None)),
        by_reason=breakdown(reason_rows),
        by_decision_source=breakdown(decision_source_rows),
        by_active_path=breakdown(active_rows),
        by_candidate=breakdown(candidate_rows),
        transitions=breakdown(transition_rows),
        features=features,
        signal_stats=dict(
            signal_a=_stats_block([_num(r.get('signal_a'))
                                   for _, r in tagged]),
            signal_b=_stats_block([_num(r.get('signal_b'))
                                   for _, r in tagged]),
            prediction=_stats_block([_num(r.get('prediction'))
                                     for _, r in tagged]),
            interval_width=_stats_block(widths),
            projected_improvement_points=_stats_block(
                [_num(r.get('projected_improvement_points'))
                 for _, r in tagged])),
        runs_detail=[public_run(r) for r in reversed(records)
                     if r['rows']],
        latest=recent[-1] if recent else None,
        recent=recent[-EXPLANATION_RECENT_LIMIT:],
        recent_limit=EXPLANATION_RECENT_LIMIT,
        sources=sources))


DETAIL_RECENT_LIMIT = 200


def _live_csv_sources(name):
    """Every live capture that recorded this CSV, plus the offline
    results-directory rows for the same file name.

    Same layering as _explanation_sources: live-experiment captures
    are read at request time; the results directory is returned
    separately and each detail page uses it only as a fallback when
    no live capture recorded anything, so a row is never counted
    twice. Returns (live_records, results_rows, live_source_paths)."""
    live, sources = [], []
    try:
        live_dirs = sorted((ROOT / 'live-experiments').glob('run-*'),
                           key=lambda p: p.stat().st_mtime)
    except OSError:
        live_dirs = []
    for directory in live_dirs:
        capture = directory / 'capture'
        rows = _tail_csv(capture / name, 100000)
        session = _read_json(directory / 'session.json') or {}
        files = []
        if capture.is_dir():
            files = sorted(p.name for p in capture.iterdir() if p.is_file())
        if rows:
            sources.append(str((capture / name).relative_to(ROOT)))
        live.append(dict(id=directory.name, source='live', rows=rows,
                         system=session.get('system'),
                         scenario=session.get('scenario'),
                         topology=session.get('topology'),
                         status=session.get('status'), files=files))
    results_rows = _tail_csv(ROOT / 'cnt-ml/results' / name, 100000)
    return live, results_rows, sources


def _detail_records(name):
    """Live records for a capture CSV, with the results-directory
    record appended only when no live capture recorded rows."""
    live, results_rows, sources = _live_csv_sources(name)
    records = list(live)
    using_results = False
    if not any(r['rows'] for r in records) and results_rows:
        records.append(dict(id='cnt-ml/results', source='results',
                            system=None, scenario=None, topology=None,
                            status=None, files=None, rows=results_rows))
        sources = ['cnt-ml/results/' + name]
        using_results = True
    return records, live, using_results, sources


def _time_span(rows):
    stamps = [s for s in (_num(r.get('timestamp')) for r in rows)
              if s is not None]
    return dict(first=_iso_time(min(stamps)) if stamps else None,
                last=_iso_time(max(stamps)) if stamps else None,
                duration_seconds=(round(max(stamps) - min(stamps), 2)
                                  if len(stamps) >= 2 else None))


def conformal_detail():
    """Full conformal-intervals record for the detail page.

    Everything is computed at request time: the calibration artifact
    (cnt-ml/models/conformal_calibration.json) is read as stored and
    its ACI residual scores are summarised, never copied; the live
    record is aggregated over every confidence.csv in the live
    captures (results directory only as fallback): gate pass/hold
    counts using the same 'True' predicate the showcase card uses,
    gate-reason and per-path breakdowns, and interval statistics.
    No number is stored in this file; the payload names every file
    it was computed from."""
    models = ROOT / 'cnt-ml/models'
    artifact = _read_json(models / 'conformal_calibration.json')
    records, live, using_results, sources = _detail_records('confidence.csv')
    if artifact:
        sources = ['cnt-ml/models/conformal_calibration.json'] + sources

    tagged = []
    for record in records:
        for row in record['rows']:
            tagged.append((record, row))
    total = len(tagged)
    rows_only = [r for _, r in tagged]

    def share(count):
        return (count / total) if total else None

    def gate_pass(row):
        return row.get('confidence_gate_pass') == 'True'

    passed = sum(1 for r in rows_only if gate_pass(r))

    by_path = []
    for path_name in sorted({r.get('path') or '?' for r in rows_only}):
        own = [r for r in rows_only if (r.get('path') or '?') == path_name]
        by_path.append(dict(
            path=path_name, count=len(own), share=share(len(own)),
            gate_pass=sum(1 for r in own if gate_pass(r)),
            gate_hold=sum(1 for r in own if not gate_pass(r)),
            width=_stats_block([_num(r.get('width')) for r in own]),
            prediction=_stats_block([_num(r.get('prediction'))
                                     for r in own])))

    reason_groups = {}
    for row in rows_only:
        reason_groups.setdefault(row.get('gate_reason') or '?',
                                 []).append(row)
    by_gate_reason = [dict(
        reason=name, count=len(own), share=share(len(own)),
        gate_pass=sum(1 for r in own if gate_pass(r)),
        mean_width=_stats_block([_num(r.get('width')) for r in own])['mean'])
        for name, own in reason_groups.items()]
    by_gate_reason.sort(key=lambda e: -e['count'])

    domain_groups = {}
    for row in rows_only:
        key = row.get('in_domain')
        domain_groups[key if key in ('True', 'False') else 'not recorded'] = \
            domain_groups.get(key if key in ('True', 'False')
                              else 'not recorded', 0) + 1
    by_in_domain = [dict(value=name, count=value, share=share(value))
                    for name, value in domain_groups.items()]

    artifact_block = None
    if artifact:
        aci = artifact.get('aci') or {}
        scores = [_num(v) for v in (aci.get('scores') or [])]
        heldout = artifact.get('heldout_coverage') or {}
        eval_cov = artifact.get('eval_capture_coverage') or {}
        aci_heldout = aci.get('heldout_coverage') or {}
        artifact_block = dict(
            method=artifact.get('method'),
            alpha=_num(artifact.get('alpha')),
            nominal_coverage=_num(artifact.get('nominal_coverage')),
            radius_percentage_points=_num(
                artifact.get('radius_percentage_points')),
            calibration_rows=artifact.get('calibration_rows'),
            heldout_n=heldout.get('n'),
            heldout_coverage=_num(heldout.get('coverage')),
            heldout_mean_width=_num(heldout.get('mean_width')),
            aci_heldout_coverage=_num(aci_heldout.get('coverage')),
            eval_capture_n=eval_cov.get('n'),
            eval_capture_coverage=_num(eval_cov.get('coverage')),
            aci_gamma=_num(aci.get('gamma')),
            aci_initial_radius=_num(
                aci.get('initial_radius_percentage_points')),
            aci_scores=_stats_block(scores),
            gate_diagnostics=artifact.get('gate_diagnostics') or {},
            calibration_ranges=artifact.get('calibration_ranges') or {},
            provenance=artifact.get('provenance') or {})

    def public_run(record):
        own = record['rows']
        entry = dict(
            id=record['id'], source=record['source'],
            system=record.get('system'), scenario=record.get('scenario'),
            topology=record.get('topology'), status=record.get('status'),
            files=record['files'], intervals=len(own),
            gate_pass=sum(1 for r in own if gate_pass(r)),
            gate_hold=sum(1 for r in own if not gate_pass(r)),
            mean_width=_stats_block([_num(r.get('width'))
                                     for r in own])['mean'],
            paths=sorted({r.get('path') or '?' for r in own}))
        entry.update(_time_span(own))
        return entry

    recent = []
    for record, row in tagged:
        recent.append(dict(
            run=record['id'], source=record['source'],
            system=record.get('system'),
            timestamp=_num(row.get('timestamp')),
            time=_iso_time(row.get('timestamp')),
            path=row.get('path'),
            prediction=_num(row.get('prediction')),
            lower=_num(row.get('lower')), upper=_num(row.get('upper')),
            width=_num(row.get('width')),
            nominal_coverage=_num(row.get('nominal_coverage')),
            in_domain=row.get('in_domain'),
            confidence_gate_pass=row.get('confidence_gate_pass'),
            gate_reason=row.get('gate_reason') or ''))
    recent.sort(key=lambda r: r['timestamp'] or 0)

    summary = dict(
        intervals=total,
        runs_contributing=sum(1 for r in records if r['rows']),
        live_runs=len(live), using_results_fallback=using_results,
        gate_pass=passed, gate_hold=total - passed,
        gate_pass_share=share(passed),
        distinct_gate_reasons=len(reason_groups),
        distinct_paths=len(by_path),
        nominal_coverages=sorted({v for v in
                                  (_num(r.get('nominal_coverage'))
                                   for r in rows_only) if v is not None}))
    summary.update(_time_span(rows_only))

    return safe(dict(
        summary=summary, artifact=artifact_block, by_path=by_path,
        by_gate_reason=by_gate_reason, by_in_domain=by_in_domain,
        interval_stats=dict(
            width=_stats_block([_num(r.get('width')) for r in rows_only]),
            prediction=_stats_block([_num(r.get('prediction'))
                                     for r in rows_only]),
            lower=_stats_block([_num(r.get('lower')) for r in rows_only]),
            upper=_stats_block([_num(r.get('upper')) for r in rows_only])),
        runs_detail=[public_run(r) for r in reversed(records)
                     if r['rows']],
        latest=recent[-1] if recent else None,
        recent=recent[-DETAIL_RECENT_LIMIT:],
        recent_limit=DETAIL_RECENT_LIMIT, sources=sources))


def latency_detail():
    """Full per-link latency record for the detail page.

    Computed at request time from every latency.csv in the live
    captures (results directory only as fallback): per-link sample
    counts and latency statistics, the first and latest estimate per
    link, per-run inventories, and whether the recorded per-link
    series are identical — the honest diagnostic for the documented
    equal-split path-RTT attribution. No number is stored in this
    file; the payload names every file it was computed from."""
    records, live, using_results, sources = _detail_records('latency.csv')
    tagged = []
    for record in records:
        for row in record['rows']:
            tagged.append((record, row))
    rows_only = [r for _, r in tagged]
    total = len(rows_only)

    links = sorted({r.get('link_id') for r in rows_only if r.get('link_id')})
    per_link = []
    sequences = {}
    for link in links:
        own = [r for r in rows_only if r.get('link_id') == link]
        values = [_num(r.get('latency_ms')) for r in own]
        sequences[link] = values
        entry = dict(link=link, stats=_stats_block(values),
                     first_value=values[0] if values else None,
                     latest_value=values[-1] if values else None)
        entry.update(_time_span(own))
        per_link.append(entry)
    identical = (len(links) > 1 and
                 all(sequences[link] == sequences[links[0]]
                     for link in links))

    latest_by_link = []
    for link in links:
        own = [r for r in rows_only if r.get('link_id') == link]
        last = own[-1]
        latest_by_link.append(dict(
            link=link, timestamp=_num(last.get('timestamp')),
            time=_iso_time(last.get('timestamp')),
            latency_ms=_num(last.get('latency_ms'))))

    def public_run(record):
        own = record['rows']
        entry = dict(
            id=record['id'], source=record['source'],
            system=record.get('system'), scenario=record.get('scenario'),
            topology=record.get('topology'), status=record.get('status'),
            files=record['files'], samples=len(own),
            links=sorted({r.get('link_id') for r in own
                          if r.get('link_id')}),
            mean_latency=_stats_block([_num(r.get('latency_ms'))
                                       for r in own])['mean'],
            peak_latency=_stats_block([_num(r.get('latency_ms'))
                                       for r in own])['max'])
        entry.update(_time_span(own))
        return entry

    recent = []
    for record, row in tagged:
        recent.append(dict(
            run=record['id'], source=record['source'],
            timestamp=_num(row.get('timestamp')),
            time=_iso_time(row.get('timestamp')),
            link_id=row.get('link_id'),
            latency_ms=_num(row.get('latency_ms'))))
    recent.sort(key=lambda r: r['timestamp'] or 0)

    summary = dict(
        samples=total,
        runs_contributing=sum(1 for r in records if r['rows']),
        live_runs=len(live), using_results_fallback=using_results,
        distinct_links=len(links), links_identical_series=identical,
        overall=_stats_block([_num(r.get('latency_ms'))
                              for r in rows_only]))
    summary.update(_time_span(rows_only))

    return safe(dict(
        summary=summary, per_link=per_link, latest_by_link=latest_by_link,
        runs_detail=[public_run(r) for r in reversed(records)
                     if r['rows']],
        latest=recent[-1] if recent else None,
        recent=recent[-DETAIL_RECENT_LIMIT:],
        recent_limit=DETAIL_RECENT_LIMIT, sources=sources))


def telemetry_detail():
    """Full all-link telemetry record for the detail page.

    Computed at request time from every telemetry_all_links.csv in
    the live captures (the showcase card reads the same files the
    same way; there is no results-directory version of this file):
    per-link utilization / throughput / loss / flow statistics,
    utilization bands per link (the high band starts at the
    controller's 85% congestion threshold), the latest recorded
    value per link, per-run inventories and the most recent rows.
    No number is stored in this file; the payload names every file
    it was computed from."""
    live, _results_rows, sources = _live_csv_sources(
        'telemetry_all_links.csv')
    records = live
    tagged = []
    for record in records:
        for row in record['rows']:
            tagged.append((record, row))
    rows_only = [r for _, r in tagged]
    total = len(rows_only)

    links = sorted({r.get('link_id') for r in rows_only if r.get('link_id')})
    per_link = []
    for link in links:
        own = [r for r in rows_only if r.get('link_id') == link]
        utils = [_num(r.get('utilization')) for r in own]
        peak_row = max((r for r in own
                        if _num(r.get('utilization')) is not None),
                       key=lambda r: _num(r.get('utilization')),
                       default=None)
        last = own[-1]
        bands = dict(
            idle=sum(1 for v in utils if v == 0),
            low=sum(1 for v in utils if v is not None and 0 < v < 50),
            mid=sum(1 for v in utils if v is not None and 50 <= v < 85),
            high=sum(1 for v in utils if v is not None and v >= 85))
        per_link.append(dict(
            link=link,
            utilization=_stats_block(utils),
            throughput=_stats_block([_num(r.get('throughput'))
                                     for r in own]),
            packet_loss=_stats_block([_num(r.get('packet_loss'))
                                      for r in own]),
            active_flows=_stats_block([_num(r.get('active_flows'))
                                       for r in own]),
            bands=bands,
            band_shares={k: (v / len(own) if own else None)
                         for k, v in bands.items()},
            peak_time=(_iso_time(peak_row.get('timestamp'))
                       if peak_row else None),
            latest=dict(
                timestamp=_num(last.get('timestamp')),
                time=_iso_time(last.get('timestamp')),
                utilization=_num(last.get('utilization')),
                throughput=_num(last.get('throughput')),
                packet_loss=_num(last.get('packet_loss')),
                active_flows=_num(last.get('active_flows')))))

    def public_run(record):
        own = record['rows']
        entry = dict(
            id=record['id'], source=record['source'],
            system=record.get('system'), scenario=record.get('scenario'),
            topology=record.get('topology'), status=record.get('status'),
            files=record['files'], rows=len(own),
            snapshots=len({r.get('timestamp') for r in own}),
            links=sorted({r.get('link_id') for r in own
                          if r.get('link_id')}),
            mean_utilization=_stats_block([_num(r.get('utilization'))
                                           for r in own])['mean'],
            peak_utilization=_stats_block([_num(r.get('utilization'))
                                           for r in own])['max'])
        entry.update(_time_span(own))
        return entry

    recent = []
    for record, row in tagged:
        recent.append(dict(
            run=record['id'], source=record['source'],
            timestamp=_num(row.get('timestamp')),
            time=_iso_time(row.get('timestamp')),
            link_id=row.get('link_id'),
            utilization=_num(row.get('utilization')),
            throughput=_num(row.get('throughput')),
            delay=_num(row.get('delay')),
            packet_loss=_num(row.get('packet_loss')),
            active_flows=_num(row.get('active_flows'))))
    recent.sort(key=lambda r: r['timestamp'] or 0)

    summary = dict(
        rows=total,
        runs_contributing=sum(1 for r in records if r['rows']),
        live_runs=len(live), distinct_links=len(links),
        snapshots=len({r.get('timestamp') for r in rows_only}),
        overall_utilization=_stats_block([_num(r.get('utilization'))
                                          for r in rows_only]))
    summary.update(_time_span(rows_only))

    return safe(dict(
        summary=summary, per_link=per_link,
        runs_detail=[public_run(r) for r in reversed(records)
                     if r['rows']],
        latest=recent[-1] if recent else None,
        recent=recent[-DETAIL_RECENT_LIMIT:],
        recent_limit=DETAIL_RECENT_LIMIT, sources=sources))

def _file_info(path):
    """Name, size and modification time of an artifact file, or None."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return dict(name=path.name, size_bytes=stat.st_size,
                modified=datetime.fromtimestamp(
                    stat.st_mtime).isoformat(timespec='seconds'))


def _winner(entries, key, reverse=False):
    """Name of the entry with the best numeric value for a key."""
    usable = [e for e in entries if e.get(key) is not None]
    if not usable:
        return None
    best = (max if reverse else min)(usable, key=lambda e: e[key])
    return best.get('name') or best.get('model')


def _add_ranks(entries, name_key, keys):
    """Attach 1-based ranks per metric key (ascending unless the key
    is in keys as (key, True), meaning higher is better)."""
    for spec in keys:
        key, reverse = spec if isinstance(spec, tuple) else (spec, False)
        usable = [e for e in entries if e.get(key) is not None]
        ordered = sorted(usable, key=lambda e: e[key], reverse=reverse)
        ranks = {e[name_key]: i + 1 for i, e in enumerate(ordered)}
        for entry in entries:
            entry[key + '_rank'] = ranks.get(entry[name_key])


def gnn_detail():
    """Full GNN-forecaster record for the detail page.

    Everything is computed at request time from the recorded GNN
    evaluation (cnt-ml/results/gnn_metrics.json): the three compared
    models with per-metric ranks, the GNN-vs-persistence gaps, the
    training-loss change, the Diebold-Mariano result exactly as
    recorded, and the link line-graph (links, switches, edges and
    adjacency) derived from the recorded link names — two links are
    adjacent when they share a switch token, the same rule the GNN
    was trained with. The trained-weights artifact is reported from
    the filesystem. No number is stored in this file."""
    results = ROOT / 'cnt-ml/results'
    models_dir = ROOT / 'cnt-ml/models'
    evaluation = _read_json(results / 'gnn_metrics.json') or {}
    sources = []
    if (results / 'gnn_metrics.json').exists():
        sources.append('cnt-ml/results/gnn_metrics.json')
    artifact = _file_info(models_dir / 'gnn_link_forecaster.npz')
    if artifact:
        sources.append('cnt-ml/models/gnn_link_forecaster.npz')

    entries = []
    for name, key in (('GNN', 'gnn'), ('Persistence', 'persistence'),
                      ('Ridge (same features)', 'ridge_same_features')):
        block = evaluation.get(key) or {}
        entries.append(dict(name=name, mae=_num(block.get('mae')),
                            rmse=_num(block.get('rmse'))))
    _add_ranks(entries, 'name', ['mae', 'rmse'])
    by_name = {e['name']: e for e in entries}
    gnn_entry = by_name.get('GNN', {})
    persistence = by_name.get('Persistence', {})

    def gap(key):
        if gnn_entry.get(key) is None or not persistence.get(key):
            return None, None
        diff = gnn_entry[key] - persistence[key]
        return round(diff, 4), round(100 * diff / persistence[key], 2)

    mae_diff, mae_pct = gap('mae')
    rmse_diff, rmse_pct = gap('rmse')

    links = [str(link) for link in (evaluation.get('links') or [])]
    switches = sorted({token for link in links
                       for token in link.split('-') if token})
    edges, adjacency = [], {link: [] for link in links}
    for i, first in enumerate(links):
        for second in links[i + 1:]:
            shared = sorted(set(first.split('-'))
                            & set(second.split('-')))
            if shared:
                edges.append(dict(a=first, b=second,
                                  shared_switch=shared))
                adjacency[first].append(second)
                adjacency[second].append(first)
    for neighbours in adjacency.values():
        neighbours.sort()

    train = evaluation.get('train_snapshots')
    test = evaluation.get('test_snapshots')
    total = (train or 0) + (test or 0) if train is not None \
        or test is not None else None
    initial = _num(evaluation.get('initial_loss'))
    final = _num(evaluation.get('final_loss'))
    loss_drop = round(initial - final, 4) if initial is not None \
        and final is not None else None
    dm = evaluation.get('diebold_mariano_gnn_vs_persistence') or {}

    return safe(dict(
        available=bool(evaluation),
        horizon_steps=evaluation.get('horizon_steps'),
        models=entries,
        winner_by_mae=_winner(entries, 'mae'),
        winner_by_rmse=_winner(entries, 'rmse'),
        gnn_vs_persistence=dict(mae_diff=mae_diff, mae_pct=mae_pct,
                                rmse_diff=rmse_diff, rmse_pct=rmse_pct),
        training=dict(
            initial_loss=initial, final_loss=final, drop=loss_drop,
            drop_pct=(round(100 * (initial - final) / initial, 2)
                      if loss_drop is not None and initial else None),
            train_snapshots=train, test_snapshots=test,
            total_snapshots=total,
            test_share=(round(test / total, 4)
                        if total and test is not None else None)),
        graph=dict(links=links, switches=switches, edges=edges,
                   adjacency=adjacency),
        dm=dict(dm_stat=_num(dm.get('dm_stat')),
                mean_loss_diff=_num(dm.get('mean_loss_diff')),
                n=dm.get('n'), p_value=_num(dm.get('p_value')),
                significant_5pct=dm.get('significant_5pct')),
        artifact=artifact,
        sources=sources))


def forecasters_detail():
    """Full forecaster-comparison record for the detail page.

    Everything is computed at request time from the recorded ML
    results: regression metrics per model with per-metric ranks and
    winners (cnt-ml/results/metrics_comparison.csv), congestion
    detection at the recorded threshold (congestion_metrics.csv),
    the controller's model selection recomputed from those files
    (highest congestion F1, ties by lowest RMSE — the recorded
    selection rule), the selected model's recorded configuration
    (cnt-ml/models/feature_list.json), and the model-artifact
    inventory read from the filesystem. No number is stored here."""
    results = ROOT / 'cnt-ml/results'
    models_dir = ROOT / 'cnt-ml/models'
    sources = []
    for relative in ('cnt-ml/results/metrics_comparison.csv',
                     'cnt-ml/results/congestion_metrics.csv',
                     'cnt-ml/models/feature_list.json'):
        if (ROOT / relative).exists():
            sources.append(relative)

    rows = []
    for row in read_csv(results / 'metrics_comparison.csv'):
        rows.append(dict(model=row.get('Model'),
                         mae=_num(row.get('MAE')),
                         rmse=_num(row.get('RMSE')),
                         r2=_num(row.get('R2')),
                         inference_time_ms=_num(
                             row.get('Inference_Time_ms'))))
    _add_ranks(rows, 'model', ['mae', 'rmse', ('r2', True),
                               'inference_time_ms'])
    bests = dict(mae=_winner(rows, 'mae'), rmse=_winner(rows, 'rmse'),
                 r2=_winner(rows, 'r2', reverse=True),
                 inference_time_ms=_winner(rows, 'inference_time_ms'))

    congestion = []
    for row in read_csv(results / 'congestion_metrics.csv'):
        counts = {key: _num(row.get(key))
                  for key in ('TP', 'FP', 'FN', 'TN')}
        present = [v for v in counts.values() if v is not None]
        entry = dict(model=row.get('Model'),
                     threshold=_num(row.get('Threshold')),
                     precision=_num(row.get('Precision')),
                     recall=_num(row.get('Recall')),
                     f1=_num(row.get('F1')),
                     total=int(sum(present)) if present else None)
        entry.update({key: (int(value) if value is not None else None)
                      for key, value in counts.items()})
        congestion.append(entry)

    selection = None
    candidates = [c for c in congestion if c['f1'] is not None]
    if candidates:
        rmse_by_model = {r['model']: r['rmse'] for r in rows}
        best_f1 = max(c['f1'] for c in candidates)
        tied = [c for c in candidates if c['f1'] == best_f1]
        tied.sort(key=lambda c: rmse_by_model.get(c['model'])
                  if rmse_by_model.get(c['model']) is not None
                  else float('inf'))
        selection = dict(
            model=tied[0]['model'], f1=tied[0]['f1'],
            tied_models=[c['model'] for c in tied],
            rule='Highest congestion F1 in congestion_metrics.csv; '
                 'ties broken by lowest RMSE in '
                 'metrics_comparison.csv')

    def find(fragment):
        for row in rows:
            if fragment in str(row.get('model') or ''):
                return row
        return None

    gradient, baseline = find('Gradient'), find('Persistence')
    pair = None
    if gradient and baseline:
        pair = dict(
            gradient_model=gradient['model'],
            persistence_model=baseline['model'],
            rmse_improvement_pct=(
                round(100 * (baseline['rmse'] - gradient['rmse'])
                      / baseline['rmse'], 2)
                if baseline.get('rmse') and gradient.get('rmse')
                is not None else None),
            mae_difference_pct=(
                round(100 * (gradient['mae'] - baseline['mae'])
                      / baseline['mae'], 2)
                if baseline.get('mae') and gradient.get('mae')
                is not None else None),
            r2_difference=(
                round(gradient['r2'] - baseline['r2'], 4)
                if gradient.get('r2') is not None
                and baseline.get('r2') is not None else None))

    artifacts = []
    try:
        model_files = sorted(p for p in models_dir.iterdir()
                             if p.is_file())
    except OSError:
        model_files = []
    for path in model_files:
        info = _file_info(path)
        if info:
            artifacts.append(info)

    return safe(dict(
        available=bool(rows), models=rows, bests=bests,
        congestion=congestion, selection=selection,
        selected_vs_persistence=pair,
        config=_read_json(models_dir / 'feature_list.json') or {},
        artifacts=artifacts, sources=sources))


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _series_stats(rows, key, value_key):
    """Per-key count/first/mean/peak/latest over a long-format CSV."""
    grouped = {}
    for row in rows:
        value = _num(row.get(value_key))
        if row.get(key) and value is not None:
            grouped.setdefault(row[key], []).append(value)
    return {k: dict(count=len(v), first=v[0], mean=sum(v) / len(v),
                    peak=max(v), latest=v[-1])
            for k, v in grouped.items()}


def _forecast_windows():
    """Forecast-error drift signal from the campaign-02 recordings.

    Each predictive-ml prediction is paired with the telemetry value
    exactly 5 s later on the same link (the pairing the conformal
    artifact was calibrated with), pooled over every run present.
    The error stream is split in half: first-half vs second-half
    RMSE/MAE is the drift comparison. Returns None when the
    recordings are absent or too short to split honestly.
    """
    errors = []
    for run_dir in sorted((CAMPAIGN / 'predictive-ml').glob('run-*')):
        telemetry = {}
        for row in read_csv(run_dir / 'telemetry.csv'):
            stamp = _num(row.get('timestamp'))
            if stamp is not None and row.get('link_id'):
                telemetry[(int(round(stamp)), row['link_id'])] = \
                    _num(row.get('utilization'))
        for row in read_csv(run_dir / 'predictions.csv'):
            if row.get('prediction_status') != 'predicted':
                continue
            stamp = _num(row.get('timestamp'))
            predicted = _num(row.get('predicted_utilization'))
            if stamp is None or predicted is None or not row.get('link_id'):
                continue
            actual = telemetry.get((int(round(stamp)) + 5, row['link_id']))
            if actual is not None:
                errors.append(predicted - actual)
    if len(errors) < 20:
        return None
    half = len(errors) // 2

    def rmse(part):
        return math.sqrt(sum(e * e for e in part) / len(part))

    def mae(part):
        return sum(abs(e) for e in part) / len(part)

    return dict(n=len(errors), rmse_first=rmse(errors[:half]),
                rmse_second=rmse(errors[half:]),
                mae_first=mae(errors[:half]), mae_second=mae(errors[half:]))


def _transition_stats():
    """Route-transition outcomes aggregated over all live captures."""
    rows, run = _all_capture_rows('transitions.csv')
    completed, failed, durations, starts = 0, 0, [], {}
    for row in rows:
        stamp = _num(row.get('timestamp'))
        if row.get('stage') == 'transaction' and stamp is not None:
            if row.get('status') == 'started':
                starts[row.get('transition_id')] = stamp
            elif row.get('status') == 'complete':
                completed += 1
                began = starts.get(row.get('transition_id'))
                if began is not None:
                    durations.append(stamp - began)
        if row.get('status') in ('failed', 'degraded', 'indeterminate'):
            failed += 1
    return dict(completed=completed, failed=failed,
                mean_seconds=(sum(durations) / len(durations)) if durations else None,
                latest=rows[-1] if rows else None, run=run)


def showcase_data():
    """Every extension feature, fed by its own recorded artifacts.

    Nothing here is invented. Each card layers three real sources:
    (1) live data aggregated across ALL live-experiment captures, not
    just the newest run; (2) recorded results from the verified
    campaign-02 artifacts and the calibration/model files; (3) for the
    policy features (QoS, ACL, slicing), the output of the feature's
    own module computed at request time from its real config. Every
    payload names its source so the UI can label it.
    """
    results = ROOT / 'cnt-ml/results'
    models = ROOT / 'cnt-ml/models'
    events, events_run = _all_capture_rows('control_events.csv')
    if not events:
        events = _tail_csv(results / 'control_events.csv', 4000)

    def count(name):
        return sum(1 for e in events if e.get('event') == name)

    start = next((e for e in reversed(events)
                  if e.get('event') == 'controller_start'), None)
    try:
        live_runs = len(list((ROOT / 'live-experiments').glob('run-*')))
    except OSError:
        live_runs = 0
    campaign_runs = len(list(CAMPAIGN.glob('*/run-*/metadata.json')))

    explanations, _ = _all_capture_rows('explanations.csv')
    if not explanations:
        explanations = _tail_csv(results / 'explanations.csv', 4000)
    _ex_live, _ex_campaign, _, _ = _explanation_sources()
    ex_runs_contributing = sum(1 for r in _ex_live + _ex_campaign
                               if r['rows'])
    reason_counts, feature_totals = {}, {}
    for row in explanations:
        reason = row.get('reason') or '?'
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for part in str(row.get('top_features') or '').split(';'):
            if ':' in part:
                name, _, weight = part.rpartition(':')
                value = _num(weight)
                if value is not None and name.strip():
                    feature_totals[name.strip()] = \
                        feature_totals.get(name.strip(), 0.0) + value
    top_feature = (max(feature_totals, key=feature_totals.get)
                   if feature_totals else None)

    drift_rows, _ = _capture_csv('drift.csv', 400, results / 'drift.csv')
    drift_events = {}
    for event in events:
        name = event.get('event') or ''
        if name.startswith('drift'):
            drift_events[name] = drift_events.get(name, 0) + 1
    drift_latest = next((e.get('reason') for e in reversed(events)
                         if (e.get('event') or '').startswith('drift')), None)

    failure_rows, _ = _capture_csv('failures.csv', 400,
                                   results / 'failures.csv')
    transitions = _transition_stats()
    reroutes_by_system = {}
    for system in SYSTEMS:
        total = 0
        for run_dir in sorted((CAMPAIGN / system).glob('run-*')):
            total += sum(1 for e in read_csv(run_dir / 'control_events.csv')
                         if e.get('event') == 'reroute')
        reroutes_by_system[system] = total

    anomaly_reasons = [e.get('reason') or '' for e in events
                       if e.get('event') == 'anomaly_detected']
    anomaly_kinds = {}
    for reason in anomaly_reasons:
        kind = reason.split(' on ')[0].strip() or 'detected'
        anomaly_kinds[kind] = anomaly_kinds.get(kind, 0) + 1

    confidence_rows, _ = _all_capture_rows('confidence.csv')
    if not confidence_rows:
        confidence_rows = _tail_csv(results / 'confidence.csv', 4000)
    gate_pass = sum(1 for r in confidence_rows
                    if r.get('confidence_gate_pass') == 'True')
    widths = [v for v in (_num(r.get('width')) for r in confidence_rows)
              if v is not None]
    width_by_path = {}
    for path_name in ('A', 'B'):
        own = [v for v in (_num(r.get('width')) for r in confidence_rows
                           if r.get('path') == path_name) if v is not None]
        if own:
            width_by_path[path_name] = sum(own) / len(own)

    latency_rows, _ = _all_capture_rows('latency.csv')
    if not latency_rows:
        latency_rows = _tail_csv(results / 'latency.csv', 4000)
    all_link_rows, _ = _all_capture_rows('telemetry_all_links.csv')

    qos_classes, qos_bands = [], {}
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'qos_policy', ROOT / 'qos_policy.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        policy = module.load_qos_policy()
        qos_classes = [dict(name=c['name'], dscp=c['dscp'],
                            share=c.get('share'), queue_id=c['queue_id'],
                            metered=c.get('meter_id') is not None)
                       for c in policy['classes']]
        for capacity in (10, 20):
            qos_bands[str(capacity)] = {
                band['class_name']: band['rate_kbps']
                for band in module.meter_bands(policy, capacity)}
    except Exception:
        qos_classes, qos_bands = [], {}

    acl_rules, acl_default = [], None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'acl_firewall', ROOT / 'acl_firewall.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = module.load_acl(ROOT / 'acl_rules.example.json')
        acl_default = engine.default_policy
        for rule in engine.rules:
            acl_rules.append(dict(
                action=rule['action'], priority=rule['priority'],
                protocol=module.protocol_name(rule['protocol'])
                if rule['protocol'] is not None else 'any',
                src=str(rule['src']), dst=str(rule['dst']),
                dst_ports=list(rule['dst_ports'])))
    except Exception:
        acl_rules, acl_default = [], None

    slice_records, slice_total, slice_error = [], None, None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'slicing', ROOT / 'slicing.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manager = module.load_slices(ROOT / 'slices.example.json')
        slice_records = manager.slices()
        slice_total = sum(s['share'] for s in slice_records)
    except Exception as error:
        slice_error = str(error)

    model_rows = []
    for row in read_csv(results / 'metrics_comparison.csv'):
        model_rows.append(dict(
            model=row.get('Model'), mae=_num(row.get('MAE')),
            rmse=_num(row.get('RMSE')), r2=_num(row.get('R2'))))

    # Platform card: aggregate controller starts and reroutes over the
    # live captures AND the recorded campaign (the same aggregation
    # platform_detail() serves), so the card still has real data when
    # no live run has recorded yet — the campaign runs are controller
    # runs too. The previous version counted only live-capture events
    # and showed NO DATA while 32 campaign starts sat on disk.
    plat_live, plat_campaign, plat_results, _ = _platform_sources()
    plat_starts = _platform_starts(plat_live + plat_campaign)
    if not plat_starts and plat_results:
        plat_starts = _platform_starts(
            [dict(id='cnt-ml/results', source='results', system=None,
                  rows=plat_results)])
    plat_latest = plat_starts[-1] if plat_starts else None
    plat_start_row = None
    if plat_latest is not None:
        plat_start_row = dict(
            timestamp=plat_latest['timestamp'], event='controller_start',
            control_mode=plat_latest['control_mode'],
            new_path=plat_latest['initial_path'],
            reason=plat_latest['reason'])
    elif start is not None:
        plat_start_row, plat_latest = start, None

    def _source_reroutes(records):
        return sum(1 for r in records for row in r['rows']
                   if row.get('event') == 'reroute')

    return dict(
        platform=dict(
            start=plat_start_row,
            start_status=(plat_latest['topnotch'] if plat_latest else
                          (_topnotch_status(start.get('reason'))
                           if start else None)),
            start_source=plat_latest['source'] if plat_latest else None,
            start_run=plat_latest['run'] if plat_latest else None,
            capture=events_run, live_runs=live_runs,
            live_runs_with_events=sum(1 for r in plat_live if r['rows']),
            campaign_runs=campaign_runs,
            starts=len(plat_starts),
            starts_live=sum(1 for s in plat_starts
                            if s['source'] == 'live'),
            starts_campaign=sum(1 for s in plat_starts
                                if s['source'] == 'campaign'),
            reroutes=(_source_reroutes(plat_live)
                      + _source_reroutes(plat_campaign)),
            reroutes_live=_source_reroutes(plat_live),
            reroutes_campaign=_source_reroutes(plat_campaign)),
        explanations=dict(count=len(explanations),
                          latest=explanations[-1] if explanations else None,
                          reasons=reason_counts, top_feature=top_feature,
                          runs_contributing=ex_runs_contributing,
                          with_prediction=sum(
                              1 for r in explanations
                              if _num(r.get('prediction')) is not None),
                          with_interval=sum(
                              1 for r in explanations
                              if _num(r.get('lower')) is not None
                              and _num(r.get('upper')) is not None),
                          with_features=sum(
                              1 for r in explanations
                              if _parse_top_features(
                                  r.get('top_features')))),
        drift=dict(latest=drift_rows[-1] if drift_rows else None,
                   recalibration=_capture_json('recalibration.json',
                                               results / 'recalibration.json'),
                   events=drift_events, latest_event=drift_latest,
                   forecast=_forecast_windows()),
        failures=dict(rows=failure_rows[-10:], downs=count('link_down'),
                      transitions=transitions,
                      reroutes_by_system=reroutes_by_system),
        anomalies=dict(count=len(anomaly_reasons), kinds=anomaly_kinds,
                       latest=anomaly_reasons[-1] if anomaly_reasons else None),
        acl=dict(drops=count('acl_drop'), rules=acl_rules,
                 default_policy=acl_default),
        slicing=dict(drops=count('slice_isolation_drop'),
                     slices=slice_records, total_share=slice_total,
                     error=slice_error),
        conformal=dict(artifact=_read_json(models / 'conformal_calibration.json'),
                       latest=confidence_rows[-1] if confidence_rows else None,
                       live=dict(rows=len(confidence_rows), gate_pass=gate_pass,
                                 gate_hold=len(confidence_rows) - gate_pass,
                                 mean_width=(sum(widths) / len(widths))
                                 if widths else None,
                                 width_by_path=width_by_path)),
        latency=_latest_by(latency_rows, 'link_id'),
        latency_stats=_series_stats(latency_rows, 'link_id', 'latency_ms'),
        all_links={link: row.get('utilization') for link, row in
                   _latest_by(all_link_rows, 'link_id').items()},
        all_link_stats=_series_stats(all_link_rows, 'link_id', 'utilization'),
        gnn=_read_json(results / 'gnn_metrics.json'),
        models_comparison=model_rows,
        qos=qos_classes, qos_bands=qos_bands)


DRIFT_RECENT_LIMIT = 200
RECOVERY_RECENT_LIMIT = 200
ANOMALY_RECENT_LIMIT = 200


def _capture_records(filename):
    """Per-run records for one capture CSV, live + campaign layers.

    Same layering as _platform_sources(), generalised to any capture
    file: every live-experiment run and every campaign-02 run is
    listed (even when it recorded no rows of this file), and sources
    names only the files that actually contributed rows."""
    live, campaign, sources = [], [], []
    try:
        live_dirs = sorted((ROOT / 'live-experiments').glob('run-*'),
                           key=lambda p: p.stat().st_mtime)
    except OSError:
        live_dirs = []
    for directory in live_dirs:
        capture = directory / 'capture'
        rows = _tail_csv(capture / filename, 100000)
        session = _read_json(directory / 'session.json') or {}
        if rows:
            sources.append(str((capture / filename).relative_to(ROOT)))
        live.append(dict(id=directory.name, source='live', rows=rows,
                         system=session.get('system'),
                         scenario=session.get('scenario'),
                         topology=session.get('topology'),
                         status=session.get('status')))
    for system in SYSTEMS:
        for directory in sorted((CAMPAIGN / system).glob('run-*')):
            rows = _tail_csv(directory / filename, 100000)
            meta = _read_json(directory / 'metadata.json') or {}
            if rows:
                sources.append(str((directory / filename)
                                   .relative_to(ROOT)))
            campaign.append(dict(id=directory.name, source='campaign',
                                 system=system, rows=rows,
                                 scenario=meta.get('scenario'),
                                 topology=meta.get('topology'),
                                 status=meta.get('status')))
    return live, campaign, sources


def _forecast_error_runs():
    """Per-run forecast-error streams from the campaign-02 recordings.

    The pairing is the one _forecast_windows() uses (each predictive-ml
    prediction against the telemetry value exactly 5 s later on the
    same link), but kept per run and per link so the drift page can
    show where the pooled halves came from. Errors are in percentage
    points, predicted minus actual."""
    runs, sources = [], []
    for run_dir in sorted((CAMPAIGN / 'predictive-ml').glob('run-*')):
        telemetry = {}
        for row in read_csv(run_dir / 'telemetry.csv'):
            stamp = _num(row.get('timestamp'))
            if stamp is not None and row.get('link_id'):
                telemetry[(int(round(stamp)), row['link_id'])] = \
                    _num(row.get('utilization'))
        errors, by_link = [], {}
        for row in read_csv(run_dir / 'predictions.csv'):
            if row.get('prediction_status') != 'predicted':
                continue
            stamp = _num(row.get('timestamp'))
            predicted = _num(row.get('predicted_utilization'))
            if stamp is None or predicted is None or not row.get('link_id'):
                continue
            actual = telemetry.get((int(round(stamp)) + 5, row['link_id']))
            if actual is not None:
                errors.append(predicted - actual)
                by_link.setdefault(row['link_id'], []).append(
                    predicted - actual)
        if errors:
            sources.append(str((run_dir / 'predictions.csv')
                               .relative_to(ROOT)))
            sources.append(str((run_dir / 'telemetry.csv')
                               .relative_to(ROOT)))
        half = len(errors) // 2

        def rmse(part):
            return (math.sqrt(sum(e * e for e in part) / len(part))
                    if part else None)

        def mae(part):
            return (sum(abs(e) for e in part) / len(part)) if part else None

        runs.append(dict(
            id=run_dir.name, system='predictive-ml', pairs=len(errors),
            rmse=rmse(errors),
            mae=mae(errors),
            bias=_stats_block(errors)['mean'],
            max_abs_error=max((abs(e) for e in errors), default=None),
            rmse_first=rmse(errors[:half]) if half else None,
            rmse_second=rmse(errors[half:]) if half else None,
            mae_first=mae(errors[:half]) if half else None,
            mae_second=mae(errors[half:]) if half else None,
            by_link={link: dict(pairs=len(values), rmse=rmse(values),
                                mae=mae(values),
                                bias=_stats_block(values)['mean'])
                     for link, values in sorted(by_link.items())}))
    return runs, sources


def drift_detail():
    """Full drift-monitoring record for the detail page.

    Everything is computed at request time: the detector firings
    logged as drift* control events across the live captures (the
    same events the showcase card counts), the drift.csv window rows
    and recalibration.json artifact when a run recorded them, and the
    campaign-02 forecast-error stream (predictions paired with
    telemetry at +5 s, split in half — the same pairing and split
    _forecast_windows() feeds the card, kept per run here). No number
    is stored in this file; the payload names every file used."""
    live, campaign, _event_results, _event_sources = _platform_sources()
    records = live + campaign
    firings = []
    for record in records:
        for row in record['rows']:
            name = row.get('event') or ''
            if name.startswith('drift'):
                reason = row.get('reason') or ''
                path_name = None
                if '(path ' in reason:
                    path_name = reason.split('(path ')[-1].split(')')[0] \
                        .strip() or None
                firings.append(dict(
                    run=record['id'], source=record['source'],
                    system=record.get('system'),
                    timestamp=_num(row.get('timestamp')),
                    time=_iso_time(row.get('timestamp')),
                    event=name,
                    detector=name.replace('drift_detector_', '')
                    .replace('drift_detected', 'drift_monitor')
                    .replace('drift', '').strip('_') or name,
                    control_mode=row.get('control_mode'),
                    path=path_name, reason=reason))
    firings.sort(key=lambda f: f['timestamp'] or 0)
    by_detector, by_path = {}, {}
    for firing in firings:
        by_detector[firing['detector']] = \
            by_detector.get(firing['detector'], 0) + 1
        key = firing['path'] or '(no path logged)'
        by_path[key] = by_path.get(key, 0) + 1

    drift_live, drift_campaign, drift_sources = \
        _capture_records('drift.csv')
    drift_records = drift_live + drift_campaign
    drift_rows = []
    for record in drift_records:
        for row in record['rows']:
            drift_rows.append((record, row))
    results_drift = _tail_csv(ROOT / 'cnt-ml/results/drift.csv', 100000)
    using_results_drift = False
    if not drift_rows and results_drift:
        using_results_drift = True
        drift_rows = [(dict(id='cnt-ml/results', source='results',
                            system=None), row) for row in results_drift]
        drift_sources = ['cnt-ml/results/drift.csv']
    drift_public = []
    for record, row in drift_rows:
        entry = dict(run=record['id'], source=record['source'],
                     system=record.get('system'))
        for key, value in row.items():
            entry[key] = _num(value) if _num(value) is not None else value
        entry['time'] = _iso_time(row.get('timestamp'))
        drift_public.append(entry)
    drift_public.sort(key=lambda r: _num(r.get('timestamp')) or 0)
    detected_rows = [r for r in drift_public
                     if str(r.get('drift_detected')).lower() == 'true']

    recalibration = _capture_json('recalibration.json',
                                  ROOT / 'cnt-ml/results/recalibration.json')
    recalibration_source = None
    capture_file = _capture_file('recalibration.json')
    if capture_file is not None and _read_json(capture_file):
        recalibration_source = str(capture_file.relative_to(ROOT))
    elif recalibration:
        recalibration_source = 'cnt-ml/results/recalibration.json'

    forecast_runs, forecast_sources = _forecast_error_runs()
    pooled = _forecast_windows()
    verdict = None
    if pooled:
        if pooled['rmse_second'] > pooled['rmse_first'] * 1.1:
            verdict = 'error grew over the campaign: drift is real in this data'
        elif pooled['rmse_second'] < pooled['rmse_first'] * 0.9:
            verdict = 'error fell over the campaign — the opposite of drift'
        else:
            verdict = 'error stayed flat: no drift in the recorded campaign'
    all_errors_n = sum(r['pairs'] for r in forecast_runs)

    sources = []
    for record in records:
        if any((row.get('event') or '').startswith('drift')
               for row in record['rows']):
            folder = 'live-experiments' if record['source'] == 'live' \
                else 'final-evaluation-artifacts/campaign-02'
            if record['source'] == 'live':
                sources.append('live-experiments/' + record['id']
                               + '/capture/control_events.csv')
            else:
                sources.append('final-evaluation-artifacts/campaign-02/'
                               + str(record['system']) + '/' + record['id']
                               + '/control_events.csv')
    sources.extend(drift_sources)
    if recalibration_source:
        sources.append(recalibration_source)
    sources.extend(forecast_sources)

    return safe(dict(
        summary=dict(
            firings=len(firings), by_detector=by_detector, by_path=by_path,
            live_firings=sum(1 for f in firings if f['source'] == 'live'),
            campaign_firings=sum(1 for f in firings
                                 if f['source'] == 'campaign'),
            runs_with_firings=len({(f['run'], f['source'])
                                   for f in firings}),
            drift_rows=len(drift_public),
            drift_detected_rows=len(detected_rows),
            using_results_drift_fallback=using_results_drift,
            latest_drift_row=drift_public[-1] if drift_public else None,
            forecast_pairs=pooled['n'] if pooled else all_errors_n,
            verdict=verdict,
            first=_iso_time(min((f['timestamp'] for f in firings
                                 if f['timestamp'] is not None),
                                default=None)) if firings else None,
            last=_iso_time(max((f['timestamp'] for f in firings
                                if f['timestamp'] is not None),
                               default=None)) if firings else None),
        firings=firings,
        latest_firing=firings[-1] if firings else None,
        forecast=pooled,
        forecast_runs=[r for r in forecast_runs if r['pairs']],
        drift_rows_recent=drift_public[-DRIFT_RECENT_LIMIT:],
        drift_recent_limit=DRIFT_RECENT_LIMIT,
        recalibration=recalibration,
        sources=list(dict.fromkeys(sources))))


def recovery_detail():
    """Full failure-recovery / route-transition record for the page.

    Everything is computed at request time: every transitions.csv row
    across the live captures (and campaign runs when they recorded
    one), grouped back into individual make-before-break transactions
    with their stage-by-stage record and durations; the failures.csv
    rows and link_down/link_up control events when a run recorded
    them; and the campaign-02 reroutes per system and per run (the
    same control_events aggregation the showcase card shows). No
    number is stored in this file; the payload names its files."""
    tr_live, tr_campaign, tr_sources = _capture_records('transitions.csv')
    tr_records = tr_live + tr_campaign
    grouped, stage_counts = {}, {}
    for record in tr_records:
        for row in record['rows']:
            key = (record['source'], record['id'],
                   row.get('transition_id'))
            group = grouped.setdefault(key, dict(
                run=record['id'], source=record['source'],
                system=record.get('system'),
                scenario=record.get('scenario'),
                status=record.get('status'),
                transition_id=row.get('transition_id'), stages=[]))
            group['stages'].append(dict(
                timestamp=_num(row.get('timestamp')),
                time=_iso_time(row.get('timestamp')),
                stage=row.get('stage'), status=row.get('status'),
                old_path=row.get('old_path'), new_path=row.get('new_path'),
                detail=row.get('detail') or ''))
            stage_key = (row.get('stage') or '?') + ' / ' + \
                (row.get('status') or '?')
            stage_counts[stage_key] = stage_counts.get(stage_key, 0) + 1
    transactions = []
    for group in grouped.values():
        if all(s.get('stage') == 'controller' for s in group['stages']):
            continue  # the controller-ready pseudo-entry, not a reroute
        stages = sorted(group['stages'],
                        key=lambda s: s['timestamp'] or 0)
        started = next((s for s in stages
                        if s['stage'] == 'transaction'
                        and s['status'] == 'started'), None)
        complete = next((s for s in stages
                         if s['stage'] == 'transaction'
                         and s['status'] == 'complete'), None)
        failed = [s for s in stages if s['status'] in
                  ('failed', 'degraded', 'indeterminate')]
        path_rows = [s for s in stages if s['old_path'] or s['new_path']]
        transactions.append(dict(
            run=group['run'], source=group['source'],
            system=group['system'], scenario=group['scenario'],
            run_status=group['status'],
            transition_id=group['transition_id'],
            old_path=path_rows[0]['old_path'] if path_rows else None,
            new_path=path_rows[-1]['new_path'] if path_rows else None,
            outcome=('complete' if complete else
                     'failed' if failed else 'incomplete'),
            started_at=started['time'] if started else
            (stages[0]['time'] if stages else None),
            completed_at=complete['time'] if complete else None,
            duration_seconds=(round(complete['timestamp']
                                    - started['timestamp'], 4)
                              if complete and started else None),
            stage_rows=len(stages),
            stages_confirmed=len({s['stage'] for s in stages
                                  if s['status'] in
                                  ('confirmed', 'committed', 'complete',
                                   'ready')}),
            stages=stages))
    transactions.sort(key=lambda t: t['started_at'] or '')
    durations = [t['duration_seconds'] for t in transactions
                 if t['duration_seconds'] is not None]

    fail_live, fail_campaign, fail_sources = \
        _capture_records('failures.csv')
    failure_rows = []
    for record in fail_live + fail_campaign:
        for row in record['rows']:
            entry = dict(run=record['id'], source=record['source'],
                         system=record.get('system'))
            for key, value in row.items():
                entry[key] = _num(value) if _num(value) is not None \
                    else value
            entry['time'] = _iso_time(row.get('timestamp'))
            failure_rows.append(entry)
    results_failures = _tail_csv(ROOT / 'cnt-ml/results/failures.csv',
                                 100000)
    using_results_failures = False
    if not failure_rows and results_failures:
        using_results_failures = True
        for row in results_failures:
            entry = dict(run='cnt-ml/results', source='results',
                         system=None)
            for key, value in row.items():
                entry[key] = _num(value) if _num(value) is not None \
                    else value
            entry['time'] = _iso_time(row.get('timestamp'))
            failure_rows.append(entry)
        fail_sources = ['cnt-ml/results/failures.csv']
    failure_rows.sort(key=lambda r: _num(r.get('timestamp')) or 0)
    failure_events = {}
    for row in failure_rows:
        name = row.get('event') or '?'
        failure_events[name] = failure_events.get(name, 0) + 1

    plat_live, plat_campaign, plat_results, _ = _platform_sources()
    link_events, reroute_events = [], []
    reroutes_by_system, reroutes_by_run = {}, []
    for record in plat_live + plat_campaign:
        own_reroutes = 0
        for row in record['rows']:
            name = row.get('event') or ''
            if name in ('link_down', 'link_up'):
                link_events.append(dict(
                    run=record['id'], source=record['source'],
                    system=record.get('system'),
                    timestamp=_num(row.get('timestamp')),
                    time=_iso_time(row.get('timestamp')), event=name,
                    control_mode=row.get('control_mode'),
                    old_path=row.get('old_path'),
                    new_path=row.get('new_path'),
                    reason=row.get('reason') or ''))
            if name == 'reroute':
                own_reroutes += 1
                reroute_events.append(dict(
                    run=record['id'], source=record['source'],
                    system=record.get('system'),
                    timestamp=_num(row.get('timestamp')),
                    time=_iso_time(row.get('timestamp')),
                    control_mode=row.get('control_mode'),
                    old_path=row.get('old_path'),
                    new_path=row.get('new_path'),
                    signal_a=_num(row.get('signal_a')),
                    signal_b=_num(row.get('signal_b')),
                    reason=row.get('reason') or ''))
        if record['source'] == 'campaign':
            reroutes_by_system[record['system']] = \
                reroutes_by_system.get(record['system'], 0) + own_reroutes
            reroutes_by_run.append(dict(
                run=record['id'], system=record['system'],
                scenario=record.get('scenario'),
                status=record.get('status'), reroutes=own_reroutes))
    link_events.sort(key=lambda e: e['timestamp'] or 0)
    reroute_events.sort(key=lambda e: e['timestamp'] or 0)

    metrics = []
    for record in tr_records:
        folder = (ROOT / 'live-experiments' / record['id'] / 'capture') \
            if record['source'] == 'live' else \
            (CAMPAIGN / str(record['system']) / record['id'])
        data = _read_json(folder / 'transition_metrics.json')
        if data:
            metrics.append(dict(run=record['id'],
                                source=record['source'],
                                system=record.get('system'), data=data))
            tr_sources.append(str((folder / 'transition_metrics.json')
                                  .relative_to(ROOT)))

    sources = list(tr_sources) + list(fail_sources)
    for record in plat_live + plat_campaign:
        if any((row.get('event') or '') in ('link_down', 'link_up',
                                            'reroute')
               for row in record['rows']):
            if record['source'] == 'live':
                sources.append('live-experiments/' + record['id']
                               + '/capture/control_events.csv')
            else:
                sources.append('final-evaluation-artifacts/campaign-02/'
                               + str(record['system']) + '/' + record['id']
                               + '/control_events.csv')

    return safe(dict(
        summary=dict(
            transactions=len(transactions),
            completed=sum(1 for t in transactions
                          if t['outcome'] == 'complete'),
            failed=sum(1 for t in transactions
                       if t['outcome'] == 'failed'),
            incomplete=sum(1 for t in transactions
                           if t['outcome'] == 'incomplete'),
            duration=_stats_block(durations),
            transition_rows=sum(t['stage_rows'] for t in transactions),
            runs_with_transitions=len({(t['run'], t['source'])
                                       for t in transactions}),
            link_downs=sum(1 for e in link_events
                           if e['event'] == 'link_down')
            + failure_events.get('link_down', 0),
            link_ups=sum(1 for e in link_events
                         if e['event'] == 'link_up')
            + failure_events.get('link_up', 0),
            failure_rows=len(failure_rows),
            failure_events=failure_events,
            using_results_failures_fallback=using_results_failures,
            reroutes_total=len(reroute_events),
            reroutes_live=sum(1 for e in reroute_events
                              if e['source'] == 'live'),
            reroutes_campaign=sum(1 for e in reroute_events
                                  if e['source'] == 'campaign'),
            reroutes_by_system=reroutes_by_system),
        transactions=transactions,
        stage_counts=stage_counts,
        failure_rows_recent=failure_rows[-RECOVERY_RECENT_LIMIT:],
        link_events=link_events,
        reroutes_by_run=reroutes_by_run,
        recent_reroutes=reroute_events[-RECOVERY_RECENT_LIMIT:],
        recent_limit=RECOVERY_RECENT_LIMIT,
        transition_metrics=metrics,
        sources=list(dict.fromkeys(sources))))


def _parse_anomaly_reason(reason):
    """'kind on LINK z=.. share=.. baseline=.. Mbps' as logged.

    Every field comes from the recorded reason string itself; a field
    the controller left blank (z= with no value) stays None, never
    invented."""
    import re
    text = str(reason or '')
    kind = text.split(' on ')[0].strip() or 'detected'
    link = None
    if ' on ' in text:
        link = text.split(' on ', 1)[1].split(' ')[0].strip() or None

    def grab(label):
        match = re.search(label + r'=([0-9]+(?:\.[0-9]+)?)', text)
        return float(match.group(1)) if match else None

    return dict(kind=kind, link=link, z=grab('z'), share=grab('share'),
                baseline_mbps=grab('baseline'))


def anomaly_detail():
    """Full anomaly-mitigation record for the detail page.

    Computed at request time from the same aggregation the showcase
    card uses — anomaly_detected control events across ALL live
    captures (the offline results file only when no live capture
    recorded events) — so the page and the card can never disagree.
    Kind, link, z-score, traffic share and baseline are parsed from
    each event's recorded reason string, exactly as logged. No
    number is stored in this file; the payload names its files."""
    events, events_run = _all_capture_rows('control_events.csv')
    using_results = False
    sources = []
    if events:
        try:
            paths = sorted((ROOT / 'live-experiments').glob(
                'run-*/capture/control_events.csv'),
                key=lambda p: p.stat().st_mtime)
        except OSError:
            paths = []
        for path in paths:
            if _tail_csv(path, 4000):
                sources.append(str(path.relative_to(ROOT)))
    else:
        events = _tail_csv(ROOT / 'cnt-ml/results/control_events.csv',
                           100000)
        if events:
            using_results = True
            sources = ['cnt-ml/results/control_events.csv']

    session_by_run = {}
    try:
        live_dirs = sorted((ROOT / 'live-experiments').glob('run-*'),
                           key=lambda p: p.stat().st_mtime)
    except OSError:
        live_dirs = []
    for directory in live_dirs:
        session = _read_json(directory / 'session.json') or {}
        session_by_run[directory.name] = session

    detections = []
    run_of_row = {}
    # _all_capture_rows concatenates captures oldest first but does not
    # tag rows with their run; rebuild the tagging by replaying the
    # same per-file reads so each detection names its run honestly.
    tagged_rows = []
    if not using_results:
        try:
            paths = sorted((ROOT / 'live-experiments').glob(
                'run-*/capture/control_events.csv'),
                key=lambda p: p.stat().st_mtime)
        except OSError:
            paths = []
        for path in paths:
            run_id = path.parent.parent.name
            for row in _tail_csv(path, 100000):
                tagged_rows.append((run_id, row))
    else:
        tagged_rows = [('cnt-ml/results', row) for row in events]
    for run_id, row in tagged_rows:
        if row.get('event') != 'anomaly_detected':
            continue
        parsed = _parse_anomaly_reason(row.get('reason'))
        session = session_by_run.get(run_id) or {}
        detections.append(dict(
            run=run_id, system=session.get('system'),
            scenario=session.get('scenario'),
            timestamp=_num(row.get('timestamp')),
            time=_iso_time(row.get('timestamp')),
            control_mode=row.get('control_mode'),
            active_path=row.get('old_path'),
            kind=parsed['kind'], link=parsed['link'], z=parsed['z'],
            share=parsed['share'], baseline_mbps=parsed['baseline_mbps'],
            reason=row.get('reason') or ''))
    detections.sort(key=lambda d: d['timestamp'] or 0)

    def breakdown(key):
        groups = {}
        for item in detections:
            name = item.get(key) or '(not logged)'
            groups[name] = groups.get(name, 0) + 1
        total = len(detections)
        return [dict(name=name, count=count,
                     share=(count / total) if total else None)
                for name, count in
                sorted(groups.items(), key=lambda kv: -kv[1])]

    by_run = []
    run_groups = {}
    for item in detections:
        run_groups.setdefault(item['run'], []).append(item)
    for run_id, items in run_groups.items():
        session = session_by_run.get(run_id) or {}
        kinds = {}
        for item in items:
            kinds[item['kind']] = kinds.get(item['kind'], 0) + 1
        stamps = [i['timestamp'] for i in items
                  if i['timestamp'] is not None]
        by_run.append(dict(
            run=run_id, system=session.get('system'),
            scenario=session.get('scenario'),
            status=session.get('status'), detections=len(items),
            kinds=kinds,
            links=sorted({i['link'] for i in items if i['link']}),
            first=_iso_time(min(stamps)) if stamps else None,
            last=_iso_time(max(stamps)) if stamps else None))
    by_run.sort(key=lambda r: r['first'] or '')

    return safe(dict(
        summary=dict(
            detections=len(detections),
            kinds={entry['name']: entry['count']
                   for entry in breakdown('kind')},
            links={entry['name']: entry['count']
                   for entry in breakdown('link')},
            runs_with_detections=len(by_run),
            live_runs_total=len(live_dirs),
            using_results_fallback=using_results,
            share=_stats_block([d['share'] for d in detections]),
            baseline_mbps=_stats_block([d['baseline_mbps']
                                        for d in detections]),
            z=_stats_block([d['z'] for d in detections]),
            with_z=sum(1 for d in detections if d['z'] is not None),
            with_share=sum(1 for d in detections
                           if d['share'] is not None),
            first=detections[0]['time'] if detections else None,
            last=detections[-1]['time'] if detections else None),
        by_kind=breakdown('kind'),
        by_link=breakdown('link'),
        by_run=by_run,
        latest=detections[-1] if detections else None,
        recent=detections[-ANOMALY_RECENT_LIMIT:],
        recent_limit=ANOMALY_RECENT_LIMIT,
        sources=sources))



CN_EVENT_LIMIT = 200


def _feature_module(filename):
    """Load one of the CN policy modules from the project root.

    The detail pages call the feature's own module — the same code the
    controller runs — so a number on a page is the module's output, not
    a copy of it. Returns None when the module cannot be loaded."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            filename[:-3], ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _guest_wiring():
    """Feature switches the live guest sets for every live run.

    Parsed at request time from experiments/live_guest.py: the
    environment.setdefault lines for the CN features, exactly as the
    guest will run them. An absent file or switch reports as absent,
    never as assumed."""
    path = ROOT / 'experiments/live_guest.py'
    wiring = {}
    try:
        text = path.read_text()
    except OSError:
        return wiring
    for match in re.finditer(
            r"environment\.setdefault\('(SDN_[A-Z_]+)',\s*(.+)\)\s*$",
            text, re.MULTILINE):
        value = match.group(2).strip()
        literal = re.fullmatch(r"'([^']*)'", value)
        wiring[match.group(1)] = literal.group(1) if literal else value
    return wiring


def _recorded_capacities():
    """Link capacities (Mbps) the recordings themselves declare.

    Collected at request time from campaign-02 metadata and live
    capture metadata/session files (their path_capacities_mbps), plus
    the controller's own default link capacity parsed from
    path_switch_controller.py. Returns (sorted capacities, sources)."""
    capacities, sources = set(), []

    def absorb(data, label):
        declared = (data or {}).get('path_capacities_mbps')
        if isinstance(declared, dict):
            found = False
            for value in declared.values():
                number = _num(value)
                if number is not None:
                    capacities.add(number)
                    found = True
            if found:
                sources.append(label)

    for system in SYSTEMS:
        for directory in sorted((CAMPAIGN / system).glob('run-*')):
            absorb(_read_json(directory / 'metadata.json'),
                   str((directory / 'metadata.json').relative_to(ROOT)))
    try:
        live_dirs = sorted((ROOT / 'live-experiments').glob('run-*'))
    except OSError:
        live_dirs = []
    for directory in live_dirs:
        absorb(_read_json(directory / 'capture/metadata.json'),
               str((directory / 'capture/metadata.json')
                   .relative_to(ROOT)))
        absorb(_read_json(directory / 'session.json'),
               str((directory / 'session.json').relative_to(ROOT)))
    try:
        text = (ROOT / 'path_switch_controller.py').read_text()
        match = re.search(
            r"SDN_LINK_CAPACITY_MBPS',\s*'([0-9.]+)'", text)
        if match:
            capacities.add(float(match.group(1)))
            sources.append('path_switch_controller.py (controller '
                           'default link capacity)')
    except OSError:
        pass
    return sorted(int(capacity) if float(capacity).is_integer()
                  else capacity for capacity in capacities), sources


def _cn_events(event_name):
    """Every recorded event of one CN type, live + campaign layers.

    Built on _capture_records('control_events.csv') — the same
    aggregation the drift/recovery/anomaly pages use — so a page and
    its card can never disagree about where events came from. The
    offline results file is used only when neither layer recorded an
    event of this type, so an event is never counted twice. Returns
    (tagged events oldest first, sources, using_results_fallback)."""
    live, campaign, sources = _capture_records('control_events.csv')
    tagged = []
    for record in live + campaign:
        for row in record['rows']:
            if row.get('event') == event_name:
                tagged.append((record, row))
    using_results = False
    if not tagged:
        results_rows = _tail_csv(
            ROOT / 'cnt-ml/results/control_events.csv', 100000)
        own = [row for row in results_rows
               if row.get('event') == event_name]
        if own:
            using_results = True
            sources = sources + ['cnt-ml/results/control_events.csv']
            record = dict(id='cnt-ml/results', source='results',
                          system=None)
            tagged = [(record, row) for row in own]
    events = []
    for record, row in tagged:
        events.append(dict(
            run=record['id'], source=record['source'],
            system=record.get('system'),
            timestamp=_num(row.get('timestamp')),
            time=_iso_time(row.get('timestamp')),
            control_mode=row.get('control_mode'),
            old_path=row.get('old_path'), new_path=row.get('new_path'),
            reason=row.get('reason') or ''))
    events.sort(key=lambda item: item['timestamp'] or 0)
    return events, sources, using_results


def _cn_event_summary(events):
    stamps = [event['timestamp'] for event in events
              if event['timestamp'] is not None]
    return dict(
        total=len(events),
        live=sum(1 for event in events if event['source'] == 'live'),
        campaign=sum(1 for event in events
                     if event['source'] == 'campaign'),
        results=sum(1 for event in events if event['source'] == 'results'),
        runs=sorted({event['run'] for event in events}),
        first=_iso_time(min(stamps)) if stamps else None,
        last=_iso_time(max(stamps)) if stamps else None)


def qos_detail():
    """Full QoS / DiffServ record for the detail page.

    Everything is computed at request time by qos_policy.py itself —
    the module the controller imports: the class table, the meter
    bands at every link capacity the recordings declare, the full
    DSCP-to-class map (all 64 code points resolved through
    class_for_dscp), and the traffic-class mapping. Loading the
    policy runs its validation, so a page that renders is a policy
    that validated. The live wiring is parsed from live_guest.py."""
    module = _feature_module('qos_policy.py')
    wiring = _guest_wiring()
    capacities, capacity_sources = _recorded_capacities()
    sources = ['qos_policy.py', 'experiments/live_guest.py'] \
        + capacity_sources
    if module is None:
        return safe(dict(available=False,
                         error='qos_policy.py could not be loaded',
                         wiring=wiring, capacities=capacities,
                         sources=sources))
    policy_path = os.environ.get('SDN_QOS_POLICY_PATH', '').strip() \
        or None
    try:
        if policy_path:
            policy = module.load_qos_policy(policy_path)
            policy_source = policy_path
        else:
            policy = module.load_qos_policy()
            policy_source = 'qos_policy.DEFAULT_QOS_POLICY'
    except Exception as error:
        return safe(dict(available=False, error=str(error),
                         wiring=wiring, capacities=capacities,
                         sources=sources))
    if policy_path:
        sources.append(policy_path)
    classes = []
    for entry in policy['classes']:
        classes.append(dict(
            name=entry['name'], dscp=list(entry['dscp']),
            share=entry.get('share'), queue_id=entry['queue_id'],
            meter_id=entry.get('meter_id'),
            metered=entry.get('meter_id') is not None
            and entry.get('share') is not None))
    bands = {}
    for capacity in capacities:
        bands[str(capacity)] = module.meter_bands(policy, capacity)
    dscp_map = []
    for dscp in range(64):
        entry = module.class_for_dscp(policy, dscp)
        dscp_map.append(dict(
            dscp=dscp, class_name=entry['name'],
            claimed=dscp in entry['dscp'],
            meter_id=entry.get('meter_id'), queue_id=entry['queue_id']))
    traffic_map = []
    mapping = getattr(module, '_TRAFFIC_CLASS_TO_QOS', {})
    for label in sorted(mapping):
        entry = module.class_for_traffic(policy, label)
        traffic_map.append(dict(traffic_class=label,
                                class_name=entry['name'],
                                meter_id=entry.get('meter_id')))
    metered = [entry for entry in classes if entry['metered']]
    return safe(dict(
        available=True, validated=True, policy_source=policy_source,
        default_class=policy['default_class'],
        summary=dict(
            classes=len(classes), metered_classes=len(metered),
            unmetered=[entry['name'] for entry in classes
                       if not entry['metered']],
            metered_share=sum(entry['share'] for entry in metered),
            dscps_claimed=sum(len(entry['dscp']) for entry in classes),
            queues=sorted({entry['queue_id'] for entry in classes}),
            meters=sorted({entry['meter_id'] for entry in metered}),
            capacities=capacities,
            qos_switch=wiring.get('SDN_QOS')),
        classes=classes, bands_by_capacity=bands, dscp_map=dscp_map,
        traffic_map=traffic_map, wiring=wiring, sources=sources))


def acl_detail():
    """Full ACL firewall record for the detail page.

    The rule table is the loaded engine's own state (acl_firewall.py
    over the configured rules file, rules in evaluation order). The
    probes are evaluated at request time by a second, fresh engine
    over the same file: one packet built to match each rule, and one
    just outside its destination-port range — the verdicts and the
    probe engine's hit counts are the engine's answers, not stored
    expectations. Recorded drops come from control_events.csv across
    the live and campaign layers, with the winning rule index parsed
    from each logged reason exactly as the controller wrote it."""
    module = _feature_module('acl_firewall.py')
    wiring = _guest_wiring()
    events, sources, using_results = _cn_events('acl_drop')
    sources = ['acl_firewall.py', 'experiments/live_guest.py'] + sources
    rules_path = os.environ.get('SDN_ACL_PATH', '').strip() \
        or str(ROOT / 'acl_rules.example.json')
    if module is None:
        return safe(dict(available=False,
                         error='acl_firewall.py could not be loaded',
                         events=events, summary=_cn_event_summary(events),
                         using_results_fallback=using_results,
                         wiring=wiring, sources=sources))
    try:
        engine = module.load_acl(rules_path)
    except Exception as error:
        return safe(dict(available=False, error=str(error),
                         events=events, summary=_cn_event_summary(events),
                         using_results_fallback=using_results,
                         wiring=wiring, sources=sources))
    try:
        sources.append(str(Path(rules_path).relative_to(ROOT)))
    except ValueError:
        sources.append(rules_path)
    rules = []
    for rule in engine.rules:
        rules.append(dict(
            index=rule['index'], priority=rule['priority'],
            action=rule['action'],
            protocol=(module.protocol_name(rule['protocol'])
                      if rule['protocol'] is not None else 'any'),
            protocol_number=rule['protocol'],
            src=str(rule['src']), dst=str(rule['dst']),
            src_ports=list(rule['src_ports']),
            dst_ports=list(rule['dst_ports'])))
    probes = []
    try:
        probe_engine = module.load_acl(rules_path)
        for rule in probe_engine.rules:
            def first_ip(network):
                address = network.network_address
                if network.num_addresses > 1:
                    address = address + 1
                return str(address)

            protocol = (module.protocol_name(rule['protocol'])
                        if rule['protocol'] is not None else 'tcp')
            packet = dict(src_ip=first_ip(rule['src']),
                          dst_ip=first_ip(rule['dst']),
                          protocol=protocol,
                          src_port=rule['src_ports'][0],
                          dst_port=rule['dst_ports'][0])
            action, winner = probe_engine.evaluate(**packet)
            entry = dict(rule_index=rule['index'], packet=packet,
                         action=action, winning_rule=winner,
                         outside=None)
            low, high = rule['dst_ports']
            outside_port = high + 1 if high < 65535 \
                else (low - 1 if low > 0 else None)
            if outside_port is not None:
                out_packet = dict(packet, dst_port=outside_port)
                out_action, out_winner = probe_engine.evaluate(
                    **out_packet)
                entry['outside'] = dict(packet=out_packet,
                                        action=out_action,
                                        winning_rule=out_winner)
            probes.append(entry)
        probe_hits = {str(key): value for key, value
                      in probe_engine.hit_counts().items()}
    except Exception:
        probes, probe_hits = [], {}
    by_rule, by_run = {}, {}
    for event in events:
        match = re.search(r'rule\s+(\S+)\s+denied', event['reason'])
        event['rule_index'] = match.group(1) if match else None
        key = event['rule_index'] or 'unparsed'
        by_rule[key] = by_rule.get(key, 0) + 1
        by_run[event['run']] = by_run.get(event['run'], 0) + 1
    summary = _cn_event_summary(events)
    summary.update(dict(
        rules=len(rules),
        deny_rules=sum(1 for rule in rules if rule['action'] == 'deny'),
        allow_rules=sum(1 for rule in rules if rule['action'] == 'allow'),
        default_policy=engine.default_policy,
        drops_by_rule=by_rule, drops_by_run=by_run))
    return safe(dict(
        available=True, validated=True, rules_file=rules_path,
        default_policy=engine.default_policy, rules=rules,
        probes=probes, probe_hits=probe_hits, summary=summary,
        events=events[-CN_EVENT_LIMIT:], events_total=len(events),
        recent_limit=CN_EVENT_LIMIT, using_results_fallback=using_results,
        wiring=wiring, sources=sources))


def slicing_detail():
    """Full network-slicing record for the detail page.

    The slice table, host map and pairwise isolation matrix are the
    loaded SliceManager's own answers at request time (slicing.py
    over the configured slices file): every ordered pair of known
    hosts is decided by isolation_decision(), and bandwidth uses
    slice_bandwidth_mbps() at every capacity the recordings declare.
    Loading runs the isolation validation, so a page that renders is
    a config that validated. Recorded cross-slice drops come from
    control_events.csv, with the host pair parsed from each logged
    reason and mapped back to slices through the current manager."""
    module = _feature_module('slicing.py')
    wiring = _guest_wiring()
    events, sources, using_results = _cn_events('slice_isolation_drop')
    sources = ['slicing.py', 'experiments/live_guest.py'] + sources
    capacities, capacity_sources = _recorded_capacities()
    sources = sources + capacity_sources
    slices_path = os.environ.get('SDN_SLICES_PATH', '').strip() \
        or str(ROOT / 'slices.example.json')
    if module is None:
        return safe(dict(available=False,
                         error='slicing.py could not be loaded',
                         events=events, summary=_cn_event_summary(events),
                         using_results_fallback=using_results,
                         wiring=wiring, sources=sources))
    try:
        manager = module.load_slices(slices_path)
    except Exception as error:
        return safe(dict(available=False, error=str(error),
                         events=events, summary=_cn_event_summary(events),
                         using_results_fallback=using_results,
                         wiring=wiring, sources=sources))
    try:
        sources.append(str(Path(slices_path).relative_to(ROOT)))
    except ValueError:
        sources.append(slices_path)
    slices = []
    for record in manager.slices():
        entry = dict(record)
        entry['bandwidth_by_capacity'] = {
            str(capacity): manager.slice_bandwidth_mbps(
                record['slice_id'], capacity)
            for capacity in capacities}
        slices.append(entry)
    hosts = sorted({host for record in slices for host in record['hosts']})
    host_map = [dict(host=host, slice_id=manager.slice_for_host(host),
                     vlan_id=manager.vlan_for_host(host))
                for host in hosts]
    pairs = []
    for src in hosts:
        for dst in hosts:
            pairs.append(dict(
                src=src, src_slice=manager.slice_for_host(src),
                dst=dst, dst_slice=manager.slice_for_host(dst),
                decision=manager.isolation_decision(src, dst)))
    total_share = sum(record['share'] for record in slices)
    by_pair, by_run = {}, {}
    for event in events:
        match = re.search(r'(\S+)\s*->\s*(\S+)\s+crosses', event['reason'])
        if match:
            event['src_host'], event['dst_host'] = match.groups()
            event['src_slice'] = manager.slice_for_host(event['src_host'])
            event['dst_slice'] = manager.slice_for_host(event['dst_host'])
        else:
            event['src_host'] = event['dst_host'] = None
            event['src_slice'] = event['dst_slice'] = None
        key = '%s -> %s' % (event['src_slice'] or '?',
                            event['dst_slice'] or '?')
        by_pair[key] = by_pair.get(key, 0) + 1
        by_run[event['run']] = by_run.get(event['run'], 0) + 1
    summary = _cn_event_summary(events)
    summary.update(dict(
        slices=len(slices), hosts=len(hosts), total_share=total_share,
        unallocated=1.0 - total_share, capacities=capacities,
        pairs=len(pairs),
        deny_pairs=sum(1 for pair in pairs
                       if pair['decision'] == 'deny'),
        allow_pairs=sum(1 for pair in pairs
                        if pair['decision'] == 'allow'),
        cross_pairs=sum(1 for pair in pairs
                        if pair['src_slice'] != pair['dst_slice']),
        drops_by_slice_pair=by_pair, drops_by_run=by_run))
    return safe(dict(
        available=True, validated=True, slices_file=slices_path,
        summary=summary, slices=slices, host_map=host_map, pairs=pairs,
        events=events[-CN_EVENT_LIMIT:], events_total=len(events),
        recent_limit=CN_EVENT_LIMIT, using_results_fallback=using_results,
        wiring=wiring, sources=sources))


def campaign_report():
    import generate_campaign_report
    return safe(generate_campaign_report.build_report(CAMPAIGN))

def live_data(identifier):
    directory = live_runtime.session_path(identifier)
    session = json.loads((directory / 'session.json').read_text())
    if session['status'] in ('failed', 'cancelled', 'interrupted'):
        return dict(pending=True, session=session)
    if not (directory / 'capture/metadata.json').exists():
        return dict(pending=True, session=session)
    try: payload = capture_data(directory / 'capture', session['system'], identifier)
    except json.JSONDecodeError: return dict(pending=True, session=session)
    payload['session'] = session
    return payload

class Handler(BaseHTTPRequestHandler):
    def local_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def respond(self, data, content_type='application/json', status=200):
        body = data if isinstance(data, bytes) else json.dumps(safe(data), allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.local_host():
            return self.respond(dict(error='Local access only'), status=403)
        url = urlsplit(self.path)
        try:
            if url.path == '/api/health': return self.respond(dict(application='sdn-traffic-lab', status='ready'))
            if url.path == '/api/live':
                return self.respond(dict(**live_runtime.sessions(), token=CONTROL_TOKEN))
            if url.path == '/api/live/run':
                query = parse_qs(url.query)
                return self.respond(live_data(query.get('id', [''])[0]))
            if url.path == '/api/state': return self.respond(catalog())
            if url.path == '/api/topnotch': return self.respond(topnotch_data())
            if url.path == '/api/showcase': return self.respond(showcase_data())
            if url.path == '/api/platform': return self.respond(platform_detail())
            if url.path == '/api/explainability': return self.respond(explainability_detail())
            if url.path == '/api/drift': return self.respond(drift_detail())
            if url.path == '/api/recovery': return self.respond(recovery_detail())
            if url.path == '/api/anomaly': return self.respond(anomaly_detail())
            if url.path == '/api/conformal': return self.respond(conformal_detail())
            if url.path == '/api/latency': return self.respond(latency_detail())
            if url.path == '/api/telemetry': return self.respond(telemetry_detail())
            if url.path == '/api/gnn': return self.respond(gnn_detail())
            if url.path == '/api/forecasters': return self.respond(forecasters_detail())
            if url.path == '/api/qos': return self.respond(qos_detail())
            if url.path == '/api/acl': return self.respond(acl_detail())
            if url.path == '/api/slicing': return self.respond(slicing_detail())
            if url.path == '/api/report': return self.respond(campaign_report())
            if url.path == '/api/research':
                from analyze_research import research_catalog
                return self.respond(research_catalog())
            if url.path == '/api/run':
                query = parse_qs(url.query)
                return self.respond(run_data(query.get('system', ['predictive-ml'])[0], query.get('id', ['run-01'])[0]))
            if url.path == '/api/results.csv':
                state = catalog()
                buffer = io.StringIO()
                writer = csv.DictWriter(buffer, fieldnames=['system', 'metric', 'n', 'mean', 'sd', 'low', 'high'])
                writer.writeheader(); writer.writerows(state['stats'])
                return self.respond(buffer.getvalue().encode(), 'text/csv; charset=utf-8')
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/react.min.js': ('react.min.js', 'text/javascript; charset=utf-8'), '/react-dom.min.js': ('react-dom.min.js', 'text/javascript; charset=utf-8'), '/showcase-react.js': ('showcase-react.js', 'text/javascript; charset=utf-8'), '/platform': ('platform.html', 'text/html; charset=utf-8'), '/platform.html': ('platform.html', 'text/html; charset=utf-8'), '/platform.js': ('platform.js', 'text/javascript; charset=utf-8'), '/explainability': ('explainability.html', 'text/html; charset=utf-8'), '/explainability.html': ('explainability.html', 'text/html; charset=utf-8'), '/explainability.js': ('explainability.js', 'text/javascript; charset=utf-8'), '/drift': ('drift.html', 'text/html; charset=utf-8'), '/drift.html': ('drift.html', 'text/html; charset=utf-8'), '/drift.js': ('drift.js', 'text/javascript; charset=utf-8'), '/recovery': ('recovery.html', 'text/html; charset=utf-8'), '/recovery.html': ('recovery.html', 'text/html; charset=utf-8'), '/recovery.js': ('recovery.js', 'text/javascript; charset=utf-8'), '/anomaly': ('anomaly.html', 'text/html; charset=utf-8'), '/anomaly.html': ('anomaly.html', 'text/html; charset=utf-8'), '/anomaly.js': ('anomaly.js', 'text/javascript; charset=utf-8'), '/conformal': ('conformal.html', 'text/html; charset=utf-8'), '/conformal.html': ('conformal.html', 'text/html; charset=utf-8'), '/conformal.js': ('conformal.js', 'text/javascript; charset=utf-8'), '/latency': ('latency.html', 'text/html; charset=utf-8'), '/latency.html': ('latency.html', 'text/html; charset=utf-8'), '/latency.js': ('latency.js', 'text/javascript; charset=utf-8'), '/telemetry': ('telemetry.html', 'text/html; charset=utf-8'), '/telemetry.html': ('telemetry.html', 'text/html; charset=utf-8'), '/telemetry.js': ('telemetry.js', 'text/javascript; charset=utf-8'), '/gnn': ('gnn.html', 'text/html; charset=utf-8'), '/gnn.html': ('gnn.html', 'text/html; charset=utf-8'), '/gnn.js': ('gnn.js', 'text/javascript; charset=utf-8'), '/forecasters': ('forecasters.html', 'text/html; charset=utf-8'), '/forecasters.html': ('forecasters.html', 'text/html; charset=utf-8'), '/forecasters.js': ('forecasters.js', 'text/javascript; charset=utf-8'), '/qos': ('qos.html', 'text/html; charset=utf-8'), '/qos.html': ('qos.html', 'text/html; charset=utf-8'), '/qos.js': ('qos.js', 'text/javascript; charset=utf-8'), '/acl': ('acl.html', 'text/html; charset=utf-8'), '/acl.html': ('acl.html', 'text/html; charset=utf-8'), '/acl.js': ('acl.js', 'text/javascript; charset=utf-8'), '/slicing': ('slicing.html', 'text/html; charset=utf-8'), '/slicing.html': ('slicing.html', 'text/html; charset=utf-8'), '/slicing.js': ('slicing.js', 'text/javascript; charset=utf-8')}
            if url.path in assets:
                name, kind = assets[url.path]
                return self.respond((ASSETS / name).read_bytes(), kind)
            self.respond(dict(error='Not found'), status=404)
        except (ValueError, KeyError, OSError, TypeError, AttributeError):
            self.respond(dict(error='Capture unavailable or awaiting verification'), status=409)

    def do_POST(self):
        # Browsers must come from this exact origin and obtain a per-server token.
        if (not self.local_host() or self.headers.get('Origin') != 'http://' + self.headers.get('Host', '')
                or not secrets.compare_digest(self.headers.get('X-SDN-Token', ''), CONTROL_TOKEN)):
            return self.respond(dict(error='Local dashboard authorization required'), status=403)
        if self.headers.get('Content-Type') != 'application/json':
            return self.respond(dict(error='JSON required'), status=415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024: raise ValueError('Invalid request size')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict): raise ValueError('Invalid request')
            if (self.path == '/api/live/start' and 'system' in payload
                    and set(payload).issubset({'system', 'scenario', 'topology'})):
                if set(payload) == {'system'}:
                    return self.respond(live_runtime.start(payload['system']), status=202)
                return self.respond(live_runtime.start(payload['system'],
                    payload.get('scenario', 'legacy'), payload.get('topology', 'diamond4')), status=202)
            if self.path == '/api/live/stop' and set(payload) == {'id'}:
                return self.respond(live_runtime.cancel(payload['id']), status=202)
            return self.respond(dict(error='Unknown action'), status=400)
        except (ValueError, KeyError, OSError) as error:
            self.respond(dict(error=str(error)), status=409)

    def log_message(self, *args): pass

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1',
                        help='bind address (127.0.0.1 by default; the '
                             'Docker image passes 0.0.0.0 so the published '
                             'port is reachable)')
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'SDN frontend: http://{args.host}:{args.port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
