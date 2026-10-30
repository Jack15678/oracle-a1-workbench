import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from test_workbench import Cloud, GOOD, ROOT_ID
from oracle_grab import Engine, OciError, main
from runtime import migrate_state
from workbench import Workbench


class VerificationTests(unittest.TestCase):
    def scenario(self, states):
        engine = Cloud()
        original = engine.call
        remaining = iter(states)
        def call(*args):
            if args[:3] == ('compute', 'instance', 'get'):
                engine.calls.append(args)
                state = next(remaining)
                if isinstance(state, Exception):
                    raise state
                return {'id': 'ocid1.instance.test', 'lifecycle-state': state}
            return original(*args)
        engine.call = call
        return engine

    @patch('oracle_grab.VERIFY_INTERVAL', 0)
    @patch('oracle_grab.egress', return_value={})
    def test_provisioning_requires_a_fresh_running_get(self, _):
        engine = self.scenario(['PROVISIONING', 'RUNNING'])
        engine.run()
        states = [e['state'] for e in engine.events if e['type'] == 'state']
        self.assertEqual(states[-3:], ['verifying', 'provisioning', 'ready'])
        results = [e for e in engine.events if e['type'] == 'result']
        self.assertEqual(results[0]['outcome'], 'accepted')
        self.assertEqual(len(engine.launches()), 1)
        self.assertEqual(len([c for c in engine.calls if c[:3] == ('compute','instance','get')]), 2)

    @patch('oracle_grab.egress', return_value={})
    def test_failure_and_unknown_never_create_again(self, _):
        for actual, expected in [('TERMINATED', 'failed'), ('TERMINATING', 'failed'),
                                 ('FAILED', 'failed'), ('STOPPED', 'unverified'),
                                 (OciError('read denied'), 'unverified')]:
            with self.subTest(actual=actual):
                engine = self.scenario([actual]); engine.run()
                self.assertEqual(engine.events[-1]['state'], expected)
                self.assertEqual(engine.instance_id, 'ocid1.instance.test')
                self.assertEqual(len(engine.launches()), 1)
                self.assertFalse(any('delete' in c or 'terminate' in c for c in engine.calls))

    @patch('oracle_grab.VERIFY_TIMEOUT', 0)
    @patch('oracle_grab.egress', return_value={})
    def test_timeout_keeps_identifier(self, _):
        engine = self.scenario(['PROVISIONING']); engine.run()
        self.assertEqual(engine.events[-1]['state'], 'unverified')
        self.assertEqual(engine.instance_id, 'ocid1.instance.test')
        self.assertEqual(len(engine.launches()), 1)

    @patch('oracle_grab.egress', return_value={})
    def test_stop_during_verification_then_resume_from_receipt(self, _):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instances.json'
            engine = self.scenario(['PROVISIONING'])
            engine.receipt_path = path
            def emit(event):
                engine.events.append(event)
                if event.get('state') == 'provisioning':
                    engine.stop.set()
            engine.emit = emit
            engine.run()
            self.assertEqual(engine.events[-1]['state'], 'unverified')
            restored = self.scenario(['RUNNING']); restored.receipt_path = path
            restored.run()
            self.assertFalse(restored.launches())
            self.assertEqual(restored.events[-1]['state'], 'ready')

    def test_verify_auth_failure_still_emits_identifier(self):
        engine = Cloud()
        with patch.object(engine, 'preflight', side_effect=OciError('auth failed')):
            engine.verify_only('ocid1.instance.test')
        self.assertEqual(engine.events[-1]['state'], 'unverified')
        self.assertEqual(engine.events[-2]['instance_id'], 'ocid1.instance.test')
        self.assertFalse(engine.launches())

    def test_old_success_and_interrupted_state_migrate_without_relaunch(self):
        for old in ('success', 'provisioning', 'verifying', 'stopping'):
            with tempfile.TemporaryDirectory() as directory:
                accepted = dict(instance_id='ocid1.instance.test', lifecycle='PROVISIONING', outcome='success')
                saved = dict(config=GOOD, state=dict(state=old, current=accepted, history=[accepted]))
                (Path(directory) / 'state.json').write_text(json.dumps(saved))
                app = Workbench(Path(directory))
                self.assertEqual(app.state['state'], 'unverified')
                self.assertEqual(app.state['history'][0]['outcome'], 'accepted')
                with self.assertRaisesRegex(ValueError, '已有实例编号'):
                    app.start(GOOD)
                with patch.object(app, 'spawn') as spawn:
                    app.verify()
                spawn.assert_called_once_with('worker-verify', '--instance-id', 'ocid1.instance.test')

    def test_readonly_refresh_preserves_creation_history(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Workbench(Path(directory))
            app.event(dict(type='result', at=1, outcome='accepted', instance_id='ocid1.instance.test', lifecycle='PROVISIONING'))
            app.event(dict(type='verification', at=2, instance_id='ocid1.instance.test', lifecycle='RUNNING', checked_at=2))
            self.assertEqual(len(app.state['history']), 1)
            self.assertEqual(app.state['history'][0]['lifecycle'], 'PROVISIONING')
            self.assertEqual(app.state['current']['lifecycle'], 'RUNNING')

    def test_home_region_selection_and_all_ad_order(self):
        for region in ('eu-frankfurt-1', 'us-ashburn-1'):
            with self.subTest(region=region), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); (root / 'key.pem').touch()
                config = root / 'config'
                config.write_text(f'[DEFAULT]\ntenancy={ROOT_ID}\nregion=bootstrap-region-1\nkey_file={root / "key.pem"}\n')
                engine = Engine({**GOOD, 'config_file': str(config)}, lambda e: None)
                def call(*args):
                    if args[:3] == ('iam','region-subscription','list'):
                        self.assertEqual(engine.base[engine.base.index('--region')+1], 'bootstrap-region-1')
                        return [{'region-name':'other-region-1','is-home-region':False,'status':'READY'},
                                {'region-name':region,'is-home-region':True,'status':'READY'}]
                    self.fail('Verification preflight should not list resources')
                with patch('oracle_grab.find_oci', return_value='oci'), patch.object(engine, 'call', side_effect=call):
                    engine.preflight(for_launch=False)
                self.assertEqual(engine.base[engine.base.index('--region')+1], region)
        engine = Cloud('capacity', presets=['2/12','1/4'])
        with patch('oracle_grab.egress', return_value={}): engine.run()
        order = [(json.loads(c[c.index('--shape-config')+1])['ocpus'], c[c.index('--availability-domain')+1]) for c in engine.launches()]
        self.assertEqual(order, [(2,engine.ads[0]), (2,engine.ads[1]), (1,engine.ads[0]), (1,engine.ads[1])])

    def test_config_file_inspect_does_not_wait_for_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'example.json'
            config.write_text(json.dumps({**GOOD, 'config_file': str(Path(directory) / 'missing')}))
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'oracle_grab.py'),
                                     'inspect', '--config', str(config)], input='', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['config'])

    def test_egress_config_overrides_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({**GOOD, 'proxy_url': 'http://127.0.0.1:7890'}))
            with patch.object(sys, 'argv', ['oracle_grab.py','egress','--config',str(path)]), \
                 patch.dict(os.environ, {'OCI_PROXY_URL': 'http://unused.example:80'}), \
                 patch('oracle_grab.egress', return_value={}) as probe, redirect_stdout(io.StringIO()):
                main()
            self.assertEqual(probe.call_args.args[0]['proxy_url'], 'http://127.0.0.1:7890')

    @unittest.skipIf(os.name == 'nt', 'Engine CLI executes in Linux/WSL')
    def test_run_and_verify_cli_use_config_and_persistent_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'config.json'
            path.write_text(json.dumps({**GOOD, 'name': 'test-from-config'}))
            for mode in ('run', 'verify'):
                args = ['oracle_grab.py', mode, '--config', str(path)]
                if mode == 'verify': args += ['--instance-id', 'ocid1.instance.test']
                with patch.object(sys, 'argv', args), patch('oracle_grab.Engine') as engine, \
                     patch('oracle_grab.data_directory', return_value=root / 'state'), \
                     patch('oracle_grab.Path.home', return_value=root), patch('oracle_grab.signal.signal'), \
                     self.assertRaises(SystemExit) as result:
                    main()
                self.assertEqual(result.exception.code, 0)
                self.assertEqual(engine.call_args.args[0]['name'], 'test-from-config')
                self.assertEqual(engine.call_args.args[3], root / 'state' / 'instances.json')
                if mode == 'verify':
                    engine.return_value.verify_only.assert_called_once_with('ocid1.instance.test')
                    engine.return_value.run.assert_not_called()
                else:
                    engine.return_value.run.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
