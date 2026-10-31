import json
import os
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from oracle_grab import DEFAULTS
from onboarding import generate_api_key, save_account, setup_status, detect_proxies
from workbench import Workbench, Handler, ThreadingHTTPServer, worker_command


class OnboardingTests(unittest.TestCase):
    def preview(self, fingerprint):
        return ("[DEFAULT]\nuser=ocid1.user.oc1..example\ntenancy=ocid1.tenancy.oc1..example\n"
                f"region=us-ashburn-1\nfingerprint={fingerprint}\nkey_file=<ignored preview path>\n")

    def config(self, directory):
        return {**DEFAULTS, "config_file": str(directory / "missing-config"), "ssh_key": str(directory / "missing-ssh.pub")}

    def test_generate_import_and_never_return_private_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); config = self.config(root)
            public = generate_api_key(root)
            self.assertIn("BEGIN PUBLIC KEY", public['public_key'])
            self.assertNotIn("PRIVATE KEY", json.dumps(public))
            self.assertEqual(generate_api_key(root), public)
            saved = save_account(root, config, {"config_preview": self.preview(public['fingerprint'])})
            self.assertTrue(Path(saved['config_file']).is_file())
            self.assertTrue(Path(saved['ssh_key']).read_text().startswith('ssh-ed25519 '))
            status = setup_status(saved, root)
            self.assertTrue(status['runtime']['api_key'])
            self.assertTrue(status['runtime']['ssh_key'])
            self.assertNotIn('PRIVATE KEY', json.dumps(status))
            original = Path(saved['config_file']).read_bytes()
            again = save_account(root, saved, {"config_preview": self.preview(public['fingerprint'])})
            self.assertNotEqual(saved['config_file'], again['config_file'])
            self.assertEqual(Path(saved['config_file']).read_bytes(), original)
            self.assertEqual(saved['ssh_key'], again['ssh_key'])
            if os.name != 'nt':
                self.assertEqual(Path(saved['config_file']).stat().st_mode & 0o777, 0o600)
                self.assertEqual(Path(saved['ssh_key']).with_suffix('').stat().st_mode & 0o777, 0o600)

    def test_mismatch_and_untrusted_preview_leave_existing_config_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); config = self.config(root)
            public = generate_api_key(root)
            for change in (self.preview('00:'*15+'00'), self.preview(public['fingerprint']).replace('ocid1.user.oc1..example', 'bad\nvalue=x')):
                with self.assertRaises(ValueError):
                    save_account(root, config, {'config_preview': change})
            self.assertFalse(list((root/'credentials').glob('account-*')))

    def test_existing_private_key_import_does_not_need_generated_key(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            public = generate_api_key(Path(first))
            pem = (Path(first)/'credentials/setup-key/api_private_key.pem').read_text()
            target = Path(second)
            saved = save_account(target, self.config(target), {'config_preview':self.preview(public['fingerprint']), 'private_key':pem})
            self.assertTrue(Path(saved['config_file']).exists())
            with self.assertRaisesRegex(ValueError, '私钥'):
                save_account(target, saved, {'config_preview':self.preview(public['fingerprint']), 'private_key':public['public_key']})

    def test_account_switch_cannot_rebind_an_existing_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); public = generate_api_key(root)
            app = Workbench(root); app.save(self.config(root))
            app.configure_account({'config_preview':self.preview(public['fingerprint'])})
            original = dict(app.config)
            app.state['current'] = {'instance_id':'ocid1.instance.test'}
            changed = self.preview(public['fingerprint']).replace('ocid1.tenancy.oc1..example','ocid1.tenancy.oc1..another')
            with self.assertRaisesRegex(ValueError,'原 tenancy'):
                app.configure_account({'config_preview':changed})
            self.assertEqual(app.config,original)
            self.assertEqual(app.state['current']['instance_id'],'ocid1.instance.test')
            foreign = root/'foreign-config'
            foreign.write_text(changed,encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'其他 tenancy'):
                app.save({**app.config, 'config_file':str(foreign)})
            app.state['current'] = None
            app.config.update(compartment='ocid1.compartment.old',subnet='ocid1.subnet.old')
            app.configure_account({'config_preview':changed})
            self.assertEqual(app.config['compartment'],'')
            self.assertEqual(app.config['subnet'],'')

    def test_two_ui_ports_cannot_share_state_and_shutdown_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            script = str(Path(__file__).resolve().parents[1] / 'launcher.py')
            command = [sys.executable, '-u', script, 'gui', '--no-open', '--port', '0', '--data-dir', directory]
            env = {**os.environ, 'PYTHONUTF8':'1'}
            first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
            try:
                line = first.stdout.readline()
                self.assertTrue(line.startswith('A1 Workbench: '),line)
                base = line.strip().split(' ',2)[2]
                second = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=10, env=env)
                self.assertNotEqual(second.returncode,0)
                self.assertIn('已有工作台',second.stderr)
                state = json.load(urlopen(base+'/api/state'))
                result = json.load(urlopen(Request(base+'/api/shutdown', data=b'{}',
                    headers={'X-Workbench-Token':state['token'],'Content-Type':'application/json'},method='POST')))
                self.assertTrue(result['ok'])
                self.assertEqual(first.wait(timeout=10),0)
            finally:
                if first.poll() is None:first.terminate();first.wait(timeout=10)
                first.stdout.close();first.stderr.close()

    def test_proxy_detection_does_not_return_passwords_or_claim_exit(self):
        connection = Mock(); connection.__enter__ = Mock(); connection.__exit__ = Mock()
        with patch('onboarding.getproxies', return_value={'http':'http://user:secret@localhost:8080','https':'http://127.0.0.1:7890'}), \
             patch('onboarding.socket.create_connection', return_value=connection):
            result = detect_proxies()
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('country',json.dumps(result))
        self.assertIn({'url':'http://127.0.0.1:7890','source':'系统代理设置'},result['proxies'])

    def test_worker_is_native_and_setup_blocked_while_running(self):
        self.assertNotIn('wsl.exe',worker_command(DEFAULTS,'inspect'))
        with tempfile.TemporaryDirectory() as directory:
            app = Workbench(Path(directory)); app.process = Mock(); app.process.poll.return_value = None
            with self.assertRaises(ValueError):app.generate_key()
            with self.assertRaises(ValueError):app.configure_account({})

    def test_http_setup_has_csrf_guard_and_never_serves_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
            server.app = Workbench(Path(directory))
            server.app.save(self.config(Path(directory)))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                state = json.load(urlopen(base+'/api/state'))
                self.assertFalse(json.load(urlopen(base+'/api/setup'))['ready'])
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base+'/api/setup/key',data=b'{}',method='POST'))
                self.assertEqual(error.exception.code,403)
                headers = {'Content-Type':'application/json','X-Workbench-Token':state['token']}
                key = json.load(urlopen(Request(base+'/api/setup/key',data=b'{}',headers=headers,method='POST')))
                body = json.dumps({'config_preview':self.preview(key['fingerprint'])}).encode()
                result = json.load(urlopen(Request(base+'/api/setup/account',data=body,headers=headers,method='POST')))
                self.assertNotIn('PRIVATE KEY',json.dumps(result))
                with self.assertRaises(HTTPError) as error:
                    urlopen(base+'/credentials/setup-key/api_private_key.pem')
                self.assertEqual(error.exception.code,404)
                with patch.object(server.app,'inspect',return_value={'ok':True,'region':'us-ashburn-1','message':'verified'}) as auth:
                    result = json.load(urlopen(Request(base+'/api/setup/auth',data=b'{}',headers=headers,method='POST')))
                self.assertTrue(result['ok']);self.assertEqual(auth.call_args.args[1],'auth')
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':unittest.main()
