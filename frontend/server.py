"""Loopback-only SDN dashboard with constrained, isolated experiment controls."""
import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
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
               'predictive-confidence': 'GB + confidence', 'predictive-safe-only': 'GB + safe'})
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
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
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
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'SDN frontend: http://127.0.0.1:{args.port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
