import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oracle_grab import Engine, OciError, parse_error, settings, inspect_environment, egress, DEFAULTS, SHAPE
from workbench import Workbench, Handler, ThreadingHTTPServer


ROOT_ID = 'ocid1.tenancy.oc1..test'
CHILD_ID = 'ocid1.compartment.oc1..child'
GOOD = dict(DEFAULTS, trusted_ip='8.8.8.8/32', compartment=ROOT_ID, max_rounds=1)


class Cloud(Engine):
    """No network or credentials. Emulates OCI responses at the command boundary."""
    def __init__(self, scenario='', **config):
        self.events, self.calls = [], []
        self.scenario = scenario
        super().__init__({**GOOD, **config}, self.events.append)

    def preflight(self, for_launch=True):
        self.region = 'test-region-1'
        self.tenancy, self.ads = ROOT_ID, ['test:REGION-1-AD-1', 'test:AD-2']

    def call(self, *args):
        self.calls.append(args)
        command = args[:3]
        def opt(k): return args[args.index(k)+1]
        if command == ('iam','compartment','list'):
            assert opt('--compartment-id-in-subtree') == 'true' and opt('--access-level') == 'ANY'
            return [{'id':CHILD_ID,'lifecycle-state':'ACTIVE'}]
        if command == ('compute','instance','list'):
            if self.scenario == 'read_error': raise OciError('permission denied','NotAuthorized',403)
            if self.scenario == 'bad_list': return {}
            if self.scenario == 'stopped_child' and opt('--compartment-id') == CHILD_ID:
                return [{'id':'existing-a1','shape':SHAPE,'lifecycle-state':'STOPPED'}]
            return []
        if args[:2] in (('bv','volume'),('bv','boot-volume')):
            if self.scenario == 'child_disk' and args[1] == 'boot-volume' and opt('--compartment-id') == CHILD_ID and opt('--availability-domain') == 'test:AD-2':
                return [{'size-in-gbs':180,'lifecycle-state':'AVAILABLE'}]
            if self.scenario == 'disk_error': raise OciError('volume read failed')
            return []
        if command == ('compute','image','list'):
            return [{'id':'ocid1.image.test','compartment-id':None,'lifecycle-state':'AVAILABLE','display-name':'Ubuntu'}]
        if command == ('network','vcn','create'):
            if self.scenario == 'network_error': raise OciError('cannot create vcn')
            return {'id':'ocid1.vcn.test','default-route-table-id':'route','default-security-list-id':'sl'}
        if command == ('network','internet-gateway','create'): return {'id':'ocid1.internetgateway.test'}
        if command in (('network','route-table','update'),('network','security-list','update')): return {}
        if command == ('network','subnet','create'): return {'id':'ocid1.subnet.test'}
        if command == ('network','subnet','get'):
            return {'lifecycle-state':'AVAILABLE','prohibit-public-ip-on-vnic':self.scenario=='private_subnet','security-list-ids':['sl']}
        if command == ('network','security-list','get'):
            if not self.c['trusted_ip'] and self.scenario != 'wide_ssh':
                return {'ingress-security-rules':[]}
            source = '0.0.0.0/0' if self.scenario == 'wide_ssh' else GOOD['trusted_ip']
            return {'ingress-security-rules':[{'protocol':'6','source':source,'source-type':'CIDR_BLOCK','tcp-options':{'destination-port-range':{'min':22,'max':22}}}]}
        if command == ('compute','instance','get'):
            return {'id':'ocid1.instance.test','lifecycle-state':'RUNNING','shape':SHAPE}
        if command == ('compute','instance','launch'):
            if self.scenario == 'capacity': raise OciError('Out of host capacity','InternalError',500)
            if self.scenario == 'quota': raise OciError('quota exceeded','LimitExceeded',400)
            if self.scenario == 'throttle': raise OciError('rate limit','TooManyRequests',429)
            if self.scenario == 'interrupt': raise InterruptedError('stop')
            return {'id':'ocid1.instance.test','lifecycle-state':'PROVISIONING'}
        raise AssertionError(f'Unexpected command: {args}')

    def launches(self):
        return [x for x in self.calls if x[:3] == ('compute','instance','launch')]


@patch('oracle_grab.egress', return_value={'ip':'8.8.8.8','country':'US','checked_at':1})
class EngineTests(unittest.TestCase):
    def test_no_ssh_ip_launches_with_ingress_closed(self, _):
        engine=Cloud(trusted_ip='');engine.run()
        updates=[c for c in engine.calls if c[:3]==('network','security-list','update')]
        self.assertEqual(json.loads(updates[0][updates[0].index('--ingress-security-rules')+1]),[])
        self.assertEqual(len(engine.launches()),1)
        self.assertEqual(engine.events[-1]['state'],'ready')

    def test_no_ssh_ip_does_not_reuse_an_open_subnet(self, _):
        engine=Cloud('wide_ssh',trusted_ip='',subnet='ocid1.subnet.test');engine.run()
        self.assertEqual(engine.events[-1]['state'],'error')
        self.assertFalse(engine.launches())

    def test_launch_payload_and_success(self, _):
        engine=Cloud();engine.run()
        launch=engine.launches()[0]
        source=json.loads(launch[launch.index('--source-details')+1])
        self.assertEqual(source,dict(sourceType='image',imageId='ocid1.image.test',bootVolumeSizeInGBs=50,bootVolumeVpusPerGB=10))
        self.assertNotIn('--boot-volume-vpus-per-gb',launch)
        self.assertEqual(launch[launch.index('--subnet-id')+1],'ocid1.subnet.test')
        self.assertEqual(engine.events[-1]['state'],'ready')
        self.assertEqual(len(engine.launches()),1)

    def test_fail_closed_before_any_network_create(self, _):
        for scenario in ('read_error','disk_error','bad_list','child_disk'):
            with self.subTest(scenario=scenario):
                engine=Cloud(scenario);engine.run()
                self.assertEqual(engine.events[-1]['state'],'error')
                self.assertFalse(engine.launches())
                self.assertFalse(any(c[:3]==('network','vcn','create') for c in engine.calls))

    def test_stopped_a1_in_child_blocks_duplicate(self, _):
        engine=Cloud('stopped_child');engine.run()
        self.assertEqual(engine.events[-1]['state'],'existing')
        self.assertFalse(engine.launches())

    def test_bad_network_never_launches(self, _):
        for scenario in ('network_error','wide_ssh','private_subnet'):
            with self.subTest(scenario=scenario):
                engine=Cloud(scenario);engine.run()
                self.assertEqual(engine.events[-1]['state'],'error')
                self.assertFalse(engine.launches())

    def test_capacity_tries_only_selected_a1(self, _):
        engine=Cloud('capacity',presets=['2/12','1/4']);engine.run()
        self.assertEqual([json.loads(x[x.index('--shape-config')+1]) for x in engine.launches()],
                         [dict(ocpus=2,memoryInGBs=12)]*2+[dict(ocpus=1,memoryInGBs=4)]*2)
        self.assertTrue(all(x[x.index('--shape')+1]==SHAPE for x in engine.launches()))
        self.assertEqual(engine.events[-1]['state'],'exhausted')

    def test_429_immediate_backoff_no_downgrade(self, _):
        engine=Cloud('throttle',max_rounds=0)
        def emit(e):
            engine.events.append(e)
            if e.get('state')=='waiting': engine.stop.set()
        engine.emit=emit;engine.run()
        waits=[e for e in engine.events if e.get('state')=='waiting']
        self.assertEqual(len(engine.launches()),1)
        self.assertGreaterEqual(waits[0]['next_at']-waits[0]['at'],1799)

    def test_quota_stops_first_attempt(self, _):
        engine=Cloud('quota');engine.run()
        self.assertEqual(len(engine.launches()),1)
        self.assertEqual(engine.events[-1]['state'],'error')

    def test_inflight_stop_is_unknown_not_capacity(self, _):
        engine=Cloud('interrupt');engine.run()
        result=[e for e in engine.events if e['type']=='result'][0]
        self.assertEqual(result['outcome'],'interrupted')
        self.assertEqual(engine.events[-1]['state'],'stopped')


class ContractTests(unittest.TestCase):
    def test_egress_reads_origin_ip_and_country_using_selected_proxy(self):
        response=Mock();response.read.return_value=b'ip=192.0.2.10\nloc=HK\ncolo=HKG\n'
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=response)
        opener.open.return_value.__exit__=Mock(return_value=False)
        proxy='http://127.0.0.1:7890'
        with patch('oracle_grab.build_opener',return_value=opener),patch('oracle_grab.ProxyHandler') as handler:
            result=egress({**GOOD,'proxy_url':proxy})
        handler.assert_called_once_with({'http':proxy,'https':proxy})
        self.assertEqual(result['ip'],'192.0.2.10');self.assertEqual(result['country'],'HK')
        self.assertEqual(result['proxy_url'],proxy);self.assertTrue(result['via_proxy'])

    def test_preview_placeholder_and_api_public_key_are_not_runtime_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'config';pub=root/'public.pem'
            config.write_text('[DEFAULT]\nuser=user\ntenancy=tenancy\nfingerprint=fingerprint\nkey_file=<path to your private keyfile> # TODO\n')
            pub.write_text('-----BEGIN PUBLIC KEY-----\ndummy\n-----END PUBLIC KEY-----\n')
            c={**GOOD,'config_file':str(config),'ssh_key':str(pub)}
            result=inspect_environment(c)
            self.assertTrue(result['config']);self.assertFalse(result['api_key']);self.assertFalse(result['ssh_key'])
            self.assertIn('key_file',result['config_error'])
            key=root/'api.pem';key.touch()
            config.write_text(f'[DEFAULT]\nuser=user\ntenancy=tenancy\nfingerprint=fingerprint\nkey_file={key.as_posix()}\n')
            pub.write_text('ssh-ed25519 dummy')
            result=inspect_environment(c)
            self.assertTrue(result['api_key']);self.assertTrue(result['ssh_key'])

    def test_reject_unsafe_settings(self):
        for change in ({'trusted_ip':'0.0.0.0/0'},{'trusted_ip':'8.8.8.8/24'},{'boot_gb':201},
                       {'presets':['4/24']},{'presets':[]},{'round_min':0},{'round_max':119},
                       {'proxy_url':'socks5://localhost:1080'},{'proxy_url':'http://user:secret@localhost:80'}):
            with self.subTest(change=change), self.assertRaises(ValueError):settings({**GOOD,**change})

    def test_error_code_not_request_id(self):
        e=parse_error('ServiceError:\n'+json.dumps(dict(code='InvalidParameter',status=400,message='bad subnet',
                                                       opc_request_id='abc429xyz401')))
        self.assertEqual(e.code,'InvalidParameter');self.assertEqual(e.status,400)

    def test_invalid_cli_json_stops(self):
        engine=Engine(GOOD,lambda e:None)
        engine.base=[sys.executable,'-c','print("not-json")']
        with self.assertRaises(OciError):engine.call()

    def test_empty_successful_paginated_cli_list_is_empty_inventory(self):
        engine=Engine(GOOD,lambda e:None)
        engine.base=[sys.executable,'-c','pass']
        self.assertEqual(engine.listing('compute','instance','list','--compartment-id',ROOT_ID),[])
        with self.assertRaises(OciError):engine.call('compute','instance','launch')
        engine.base=[sys.executable,'-c','import sys;sys.exit(1)']
        with self.assertRaises(OciError):engine.listing('compute','instance','list','--compartment-id',ROOT_ID)

    def test_stop_interrupts_active_cli(self):
        engine=Engine(GOOD,lambda e:None)
        engine.base=[sys.executable,'-c','import time;time.sleep(30)']
        timer=threading.Timer(.1,engine.stop.set);timer.start()
        started=time.monotonic()
        with self.assertRaises(InterruptedError):engine.call()
        timer.join();self.assertLess(time.monotonic()-started,5)

    def test_profile_and_proxy_applied_to_every_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);key=root/'api.pem';key.touch();pub=root/'ssh.pub';pub.write_text('ssh-ed25519 dummy')
            config=root/'config';config.write_text(f'[DEFAULT]\nregion=bootstrap-region-1\ntenancy=wrong\nkey_file={key.as_posix()}\n[chosen]\ntenancy={ROOT_ID}\n')
            engine=Engine({**GOOD,'config_file':str(config),'profile':'chosen','ssh_key':str(pub),'proxy_url':'http://127.0.0.1:7897'},lambda e:None)
            with patch('oracle_grab.shutil.which',return_value='/test/oci'),patch.object(engine,'call',side_effect=[[{'region-name':'home-region-1','is-home-region':True,'status':'READY'}],[{'name':'AD-1'}]]):
                engine.preflight()
            self.assertEqual(engine.tenancy,ROOT_ID)
            self.assertIn('--no-retry',engine.base)
            self.assertEqual(engine.base[engine.base.index('--proxy')+1],'http://127.0.0.1:7897')

    def test_history_persistence_and_active_settings_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Workbench(Path(directory));app.save(GOOD)
            for n in range(7):app.event(dict(type='result',at=n,attempt=n,outcome='capacity'))
            restored=Workbench(Path(directory))
            self.assertEqual([x['attempt'] for x in restored.state['history']],[6,5,4,3,2])
            app.process=Mock();app.process.poll.return_value=None
            with self.assertRaises(ValueError):app.save(GOOD)

    def test_worker_pipe_start_history_and_stop(self):
        program = '''import json,sys,time
json.loads(sys.stdin.readline())
for n in range(7):
 print(json.dumps(dict(type='result',at=time.time(),attempt=n,outcome='capacity')),flush=True)
print(json.dumps(dict(type='state',at=time.time(),state='waiting',message='mock wait',next_at=time.time()+120)),flush=True)
sys.stdin.readline()
print(json.dumps(dict(type='state',at=time.time(),state='stopped',message='mock stopped')),flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            app=Workbench(Path(directory))
            with patch('workbench.worker_command',return_value=[sys.executable,'-u','-c',program]):
                app.start(GOOD)
            deadline=time.monotonic()+5
            while app.state['state']!='waiting' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(app.state['state'],'waiting')
            self.assertTrue(app.snapshot()['busy'])
            self.assertEqual(len(app.state['history']),5)
            app.stop();app.process.wait(timeout=5)
            while app.state['state']!='stopped' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(app.state['state'],'stopped')
            self.assertFalse(app.snapshot()['busy'])
            app.process.stdin.close();app.process.stdout.close();app.process.stderr.close()

    def test_http_cannot_expose_workspace_or_start_cross_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.app=Workbench(Path(directory))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                data=json.load(urlopen(base+'/api/state'))
                self.assertEqual(data['state'],'idle')
                for path in ('/oracle_grab.py','/../oracle_grab.py','/Configuration%20file%20preview.txt'):
                    with self.assertRaises(HTTPError) as cm:urlopen(base+path)
                    self.assertEqual(cm.exception.code,404)
                with self.assertRaises(HTTPError) as cm:urlopen(Request(base+'/api/start',data=b'{}',method='POST'))
                self.assertEqual(cm.exception.code,403)
                request=Request(base+'/api/save',data=json.dumps(GOOD).encode(),headers={'X-Workbench-Token':data['token'],'Content-Type':'application/json'},method='POST')
                self.assertEqual(json.load(urlopen(request))['config']['trusted_ip'],GOOD['trusted_ip'])
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main(verbosity=2)
