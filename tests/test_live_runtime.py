import fcntl
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments'))
import live_runtime as runtime
from frontend import server


class LiveRuntimeTests(unittest.TestCase):
    def test_only_known_systems_can_start(self):
        for system in ('arbitrary-command', '../static', ['static']):
            with self.assertRaises(ValueError): runtime.start(system)

    def test_session_identifiers_cannot_escape_storage(self):
        for identifier in ('../campaign-02', '/tmp/test', 'run-01', None):
            with self.assertRaises(ValueError): runtime.session_path(identifier)

    def test_research_choices_cannot_inject_commands(self):
        for scenario, topology in (('../ramp','diamond4'), ('ramp','unknown'), ('multi-flow','diamond4')):
            with self.assertRaises(ValueError): runtime.start('predictive-safe', scenario, topology)
        for repetition in (0, True, 1001, '1; exit'):
            with self.assertRaises(ValueError): runtime.start('predictive-safe', repetition=repetition)
        with self.assertRaises(ValueError): runtime.start('predictive-safe', campaign_id='../capture')

    def test_manual_repetitions_do_not_mix_sources(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(runtime, 'LIVE', Path(temporary)):
            for index, hashes in ((1, {'controller':'new'}), (2, {'controller':'new'}), (8, {'controller':'old'})):
                folder=Path(temporary)/('run-%s' % index)/'capture'
                folder.mkdir(parents=True)
                (folder/'metadata.json').write_text(json.dumps(dict(system='predictive-safe',
                    scenario='ramp',topology='diamond4',repetition=index,repository={'source_sha256':hashes})))
            self.assertEqual(runtime.next_repetition('predictive-safe','ramp','diamond4',{'controller':'new'}),3)
            self.assertEqual(runtime.next_repetition('static','ramp','diamond4',{'controller':'new'}),1)

    def test_lock_prevents_overlapping_experiments(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(runtime, 'LIVE', Path(temporary)):
            self.assertFalse(runtime.is_busy())
            with (Path(temporary) / '.lock').open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(runtime.is_busy())
                with patch.object(runtime, 'requirements', return_value={'available': True}):
                    with self.assertRaisesRegex(ValueError, 'already running'): runtime.start('static')
            self.assertFalse(runtime.is_busy())

    def test_missing_tools_return_setup_state(self):
        with patch.object(runtime.shutil, 'which', return_value=None), patch.object(Path, 'is_file', return_value=False):
            result = runtime.requirements()
        self.assertFalse(result['available'])
        self.assertIn('prepared Ubuntu VM disk', result['missing'])

    def test_stopped_worker_is_not_reported_as_live(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(runtime, 'LIVE', Path(temporary)):
            identifier = 'run-20260921T120000-1234abcd'
            directory = runtime.session_path(identifier)
            directory.mkdir()
            runtime.atomic_json(directory / 'session.json', dict(id=identifier, system='static', status='running'))
            self.assertEqual(runtime.sessions()['sessions'][0]['status'], 'interrupted')

    def test_old_session_is_not_revived_by_another_worker(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(runtime, 'LIVE', Path(temporary)):
            identifier = 'run-20260921T120000-1234abcd'
            directory = runtime.session_path(identifier)
            directory.mkdir()
            runtime.atomic_json(directory / 'session.json', dict(id=identifier, system='static', status='running'))
            (Path(temporary) / '.lock').write_text('run-20260921T130000-1234abcd')
            with patch.object(runtime, 'is_busy', return_value=True):
                self.assertEqual(runtime.sessions()['sessions'][0]['status'], 'interrupted')
                with self.assertRaisesRegex(ValueError, 'no longer running'): runtime.cancel(identifier)

    def test_launcher_reuses_healthy_server(self):
        from frontend import launch
        with patch.object(launch, 'ready', return_value=True), patch.object(launch.webbrowser, 'open') as browser, patch.object(launch.subprocess, 'Popen') as process:
            launch.main()
            browser.assert_called_once_with(launch.URL)
            process.assert_not_called()


class ControlAPITests(unittest.TestCase):
    def request(self, payload, **headers):
        handler = server.Handler.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=8765)
        handler.path = '/api/live/start'
        data = json.dumps(payload).encode()
        handler.headers = {'Host':'127.0.0.1:8765', 'Origin':'http://127.0.0.1:8765',
                           'X-SDN-Token':server.CONTROL_TOKEN, 'Content-Type':'application/json',
                           'Content-Length':str(len(data)), **headers}
        handler.rfile = io.BytesIO(data)
        handler.respond = Mock()
        handler.do_POST()
        return handler.respond.call_args

    def test_cross_origin_and_missing_token_are_rejected(self):
        for headers in ({'Origin':'https://untrusted.example'}, {'X-SDN-Token':''}, {'Host':'untrusted.example'}):
            with patch.object(runtime, 'start') as start:
                result = self.request({'system':'static'}, **headers)
                self.assertEqual(result.kwargs['status'], 403)
                start.assert_not_called()

    def test_valid_request_has_only_a_system_choice(self):
        with patch.object(runtime, 'start', return_value={'status':'starting'}) as start:
            self.assertEqual(self.request({'system':'static'}).kwargs['status'], 202)
            start.assert_called_once_with('static')
            start.reset_mock()
            self.assertEqual(self.request({'system':'static','command':'anything'}).kwargs['status'], 400)
            start.assert_not_called()

    def test_research_request_only_accepts_bounded_choices(self):
        with patch.object(runtime, 'start', return_value={'status':'starting'}) as start:
            payload={'system':'predictive-safe', 'scenario':'multi-flow', 'topology':'access6'}
            self.assertEqual(self.request(payload).kwargs['status'], 202)
            start.assert_called_once_with('predictive-safe', 'multi-flow', 'access6')
            start.reset_mock()
            self.assertEqual(self.request({**payload, 'repetition':100}).kwargs['status'], 400)
            start.assert_not_called()
