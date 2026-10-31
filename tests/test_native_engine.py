import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oci_api
import oracle_grab
import runtime


def arguments(*command):
    return oci_api.parser().parse_args(["--config-file", "test-config", "--region", "test-region-1", *command])


class RuntimeTests(unittest.TestCase):
    def test_native_command_prefers_sdk_and_retains_cli_fallback(self):
        with patch("oracle_grab.sdk_available", return_value=True), patch("oracle_grab.find_oci") as cli:
            native = oracle_grab.oci_command()
            self.assertEqual(native[-1], "oci-api")
            self.assertTrue(native[-2].endswith("launcher.py"))
            cli.assert_not_called()
        with patch("oracle_grab.sdk_available", return_value=False), patch("oracle_grab.find_oci", return_value="external-oci"):
            self.assertEqual(oracle_grab.oci_command(), ["external-oci"])

    def test_frozen_gui_uses_console_sibling(self):
        fake_exe = str(Path(tempfile.gettempdir()) / "OracleA1Workbench.exe")
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", fake_exe):
            command = runtime.engine_command("inspect")
        self.assertEqual(Path(command[0]).name, "a1.exe" if os.name == "nt" else "a1")
        self.assertEqual(command[1:], ["inspect"])

    def test_process_lock_blocks_other_process_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.lock"
            program = """from pathlib import Path
import sys
from runtime import process_lock
try:
    with process_lock(Path(sys.argv[1])): pass
except BlockingIOError:
    raise SystemExit(3)
"""
            def check():
                return subprocess.run([sys.executable, "-c", program, str(path)],
                    cwd=Path(runtime.__file__).parent, capture_output=True, timeout=10).returncode
            with runtime.process_lock(path):
                self.assertEqual(check(), 3)
            self.assertEqual(check(), 0)

    def test_mac_new_directory_and_existing_xdg_preserved(self):
        # Fix the concrete Path class while simulating a different OS in Windows CI.
        path_class = type(Path())
        with tempfile.TemporaryDirectory() as directory, patch("runtime.Path", path_class), \
                patch.object(path_class, "home", return_value=path_class(directory)), \
                patch("runtime.os.name", "posix"), patch("runtime.sys.platform", "darwin"), \
                patch.dict(os.environ, {"XDG_STATE_HOME": str(path_class(directory) / "state")}):
            self.assertEqual(runtime.data_directory(), path_class(directory) / "Library/Application Support/OracleA1Workbench")
            old = path_class(directory) / "state/oracle-a1-workbench"
            old.mkdir(parents=True)
            self.assertEqual(runtime.data_directory(), old)

    def test_auth_outputs_one_json_without_launch_validation(self):
        def preflight(engine, for_launch=True):
            self.assertFalse(for_launch)
            engine.region = "test-region-1"
            engine.event("region", name=engine.region)
        out = io.StringIO()
        with patch.object(sys, "argv", ["oracle_grab.py", "auth"]), \
                patch("oracle_grab.environment_config", return_value={}), \
                patch.object(oracle_grab.Engine, "preflight", preflight), contextlib.redirect_stdout(out):
            oracle_grab.main()
        self.assertEqual(json.loads(out.getvalue())["region"], "test-region-1")
        self.assertEqual(len(out.getvalue().splitlines()), 1)

    def test_auth_can_prove_existing_instance_access_without_receipt_write(self):
        instance_id = "ocid1.instance.existing"
        def preflight(engine, for_launch=True):
            self.assertFalse(for_launch)
            engine.region, engine.tenancy = "test-region-1", "ocid1.tenancy.oc1..example"
        cases = [({"id": instance_id, "lifecycle-state": "RUNNING"}, True),
                 ({"id": "ocid1.instance.someone-else"}, False),
                 (oracle_grab.OciError("not authorized", "NotAuthorizedOrNotFound", 404), False)]
        for response, succeeds in cases:
            with self.subTest(response=response):
                out, err = io.StringIO(), io.StringIO()
                with patch.object(sys, "argv", ["oracle_grab.py", "auth", "--instance-id", instance_id]), \
                        patch("oracle_grab.environment_config", return_value={}), \
                        patch.object(oracle_grab.Engine, "preflight", preflight), \
                        patch.object(oracle_grab.Engine, "call", side_effect=response if isinstance(response, Exception) else None,
                                     return_value=response) as call, \
                        patch.object(oracle_grab.Engine, "receipt") as receipt, \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    if succeeds:
                        oracle_grab.main()
                    else:
                        with self.assertRaises(SystemExit) as failure:
                            oracle_grab.main()
                        self.assertEqual(failure.exception.code, 1)
                call.assert_called_once_with("compute", "instance", "get", "--instance-id", instance_id)
                receipt.assert_not_called()
                if succeeds:
                    result = json.loads(out.getvalue())
                    self.assertEqual(result["tenancy"], "ocid1.tenancy.oc1..example")
                    self.assertEqual(result["instance_id"], instance_id)
                    self.assertEqual(result["lifecycle"], "RUNNING")
                    self.assertEqual(len(out.getvalue().splitlines()), 1)
                else:
                    self.assertEqual(out.getvalue(), "")
                    self.assertTrue(err.getvalue().strip())

    def test_wrong_account_verify_does_not_create_or_overwrite_receipt(self):
        for saved in (None, {"ocid1.tenancy.oc1..new/test-region-1": {"instance_id": "ocid1.instance.known"}}):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "instances.json"
                if saved is not None:
                    path.write_text(json.dumps(saved), encoding="utf-8")
                before = path.read_bytes() if path.exists() else None
                events = []
                engine = oracle_grab.Engine({}, events.append, receipt_path=path)
                def preflight(for_launch=True):
                    self.assertFalse(for_launch)
                    engine.tenancy, engine.region = "ocid1.tenancy.oc1..new", "test-region-1"
                with patch.object(engine, "preflight", side_effect=preflight), \
                        patch.object(engine, "call", side_effect=oracle_grab.OciError("not authorized", "NotAuthorizedOrNotFound", 404)) as call:
                    engine.verify_only("ocid1.instance.wrong-account")
                call.assert_called_once_with("compute", "instance", "get", "--instance-id", "ocid1.instance.wrong-account")
                self.assertEqual(path.read_bytes() if path.exists() else None, before)
                self.assertEqual(events[-1]["state"], "unverified")
                self.assertEqual(events[-2]["instance_id"], "ocid1.instance.wrong-account")

    def test_accepted_launch_keeps_receipt_when_first_verification_fails(self):
        from test_workbench import Cloud, ROOT_ID
        with tempfile.TemporaryDirectory() as directory:
            engine = Cloud()
            engine.receipt_path = Path(directory) / "instances.json"
            original = engine.call
            def call(*args):
                if args[:3] == ("compute", "instance", "get"):
                    raise oracle_grab.OciError("read temporarily denied", "NotAuthorizedOrNotFound", 404)
                return original(*args)
            with patch.object(engine, "call", side_effect=call), patch("oracle_grab.egress", return_value={}):
                engine.run()
            receipt = json.loads(engine.receipt_path.read_text())[ROOT_ID + "/test-region-1"]
            self.assertEqual(receipt["instance_id"], "ocid1.instance.test")
            self.assertEqual(receipt["lifecycle"], "PROVISIONING")
            self.assertEqual(engine.events[-1]["state"], "unverified")
            accepted = next(event for event in engine.events if event["type"] == "result")
            self.assertEqual(accepted["tenancy"], ROOT_ID)
            self.assertEqual(len(engine.launches()), 1)
            engine.verify("ocid1.instance.test")
            verified = next(event for event in reversed(engine.events) if event["type"] == "verification")
            self.assertEqual(verified["tenancy"], ROOT_ID)
            self.assertEqual(verified["lifecycle"], "RUNNING")


class AdapterTests(unittest.TestCase):
    def sdk(self):
        # Real models when installed; the same network-free tests remain runnable
        # with only stdlib for existing source users who use external OCI CLI.
        try:
            import oci
            return oci
        except ImportError:
            models = SimpleNamespace(**{name: lambda **values: SimpleNamespace(**values) for name in (
                "LaunchInstanceDetails", "LaunchInstanceShapeConfigDetails", "InstanceSourceViaImageDetails", "CreateVnicDetails",
                "CreateVcnDetails", "CreateInternetGatewayDetails", "RouteRule", "UpdateRouteTableDetails", "IngressSecurityRule",
                "TcpOptions", "PortRange", "EgressSecurityRule", "UpdateSecurityListDetails", "CreateSubnetDetails")})
            return SimpleNamespace(core=SimpleNamespace(models=models), retry=SimpleNamespace(NoneRetryStrategy=lambda: "no-retry"))

    def test_launch_maps_actual_shape_source_vnic_and_ssh_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "ssh.pub"
            key.write_text("ssh-ed25519 TEST_ONLY", encoding="utf-8")
            args = arguments("compute", "instance", "launch", "--availability-domain", "AD-1", "--compartment-id", "test-compartment",
                "--shape", oracle_grab.SHAPE, "--shape-config", '{"ocpus":2,"memoryInGBs":12}',
                "--source-details", '{"sourceType":"image","imageId":"test-image","bootVolumeSizeInGBs":50,"bootVolumeVpusPerGB":10}',
                "--subnet-id", "test-subnet", "--assign-public-ip", "true", "--ssh-authorized-keys-file", str(key), "--display-name", "test-a1")
            client = Mock()
            method, options = oci_api.command(client, args, self.sdk())
            details = options["launch_instance_details"]
            self.assertEqual(method, client.launch_instance)
            self.assertEqual(details.shape_config.memory_in_gbs, 12)
            self.assertEqual(details.source_details.boot_volume_vpus_per_gb, 10)
            self.assertEqual(details.create_vnic_details.subnet_id, "test-subnet")
            self.assertTrue(details.create_vnic_details.assign_public_ip)
            self.assertEqual(details.metadata, {"ssh_authorized_keys": "ssh-ed25519 TEST_ONLY"})

    def test_paginated_inventory_keeps_all_pages_and_no_retry(self):
        client = Mock()
        client.list_instances.side_effect = [SimpleNamespace(data=[{"id": "first"}], headers={"opc-next-page": "next"}),
                                            SimpleNamespace(data=[{"id": "second"}], headers={})]
        result = oci_api.execute(arguments("compute", "instance", "list", "--compartment-id", "test", "--all"), self.sdk(), client)
        self.assertEqual(result, [{"id": "first"}, {"id": "second"}])
        self.assertEqual(client.list_instances.call_count, 2)
        self.assertEqual(client.list_instances.call_args.kwargs["page"], "next")
        self.assertIn("retry_strategy", client.list_instances.call_args.kwargs)

    def test_sdk_service_error_is_not_retried(self):
        client = Mock()
        client.list_instances.side_effect = RuntimeError("permission denied")
        with self.assertRaisesRegex(RuntimeError, "permission denied"):
            oci_api.execute(arguments("compute", "instance", "list", "--compartment-id", "test", "--all"), self.sdk(), client)
        self.assertEqual(client.list_instances.call_count, 1)

    def test_explicit_proxy_and_timeouts_disable_ambient_proxy(self):
        for proxy in ("", "http://127.0.0.1:7890"):
            sdk = Mock()
            sdk.config.from_file.return_value = {"region": "wrong-region"}
            args = arguments("compute", "instance", "get", "--instance-id", "test", "--proxy", proxy)
            client = oci_api.make_client(args, sdk)
            self.assertFalse(client.base_client.session.trust_env)
            self.assertEqual(client.base_client.session.proxies, {"http": proxy, "https": proxy} if proxy else {})
            self.assertEqual(sdk.core.ComputeClient.call_args.args[0]["region"], "test-region-1")
            self.assertEqual(sdk.core.ComputeClient.call_args.kwargs["timeout"], (15, 60))

    def test_security_rule_models_preserve_ssh_closed_and_narrow_open(self):
        for rules in ([], [{"source": "192.0.2.1/32", "sourceType": "CIDR_BLOCK", "protocol": "6", "isStateless": False,
                            "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}}]):
            args = arguments("network", "security-list", "update", "--security-list-id", "test", "--force",
                "--ingress-security-rules", json.dumps(rules), "--egress-security-rules", '[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]')
            _, options = oci_api.command(Mock(), args, self.sdk())
            details = options["update_security_list_details"]
            self.assertEqual(len(details.ingress_security_rules), len(rules))
            if rules:
                self.assertEqual(details.ingress_security_rules[0].tcp_options.destination_port_range.max, 22)
                self.assertEqual(details.ingress_security_rules[0].source, "192.0.2.1/32")

    def test_subnet_wait_is_readonly_after_one_create(self):
        client = Mock()
        client.create_subnet.return_value = SimpleNamespace(data=SimpleNamespace(id="test-subnet", lifecycle_state="PROVISIONING"))
        client.get_subnet.return_value = SimpleNamespace(data=SimpleNamespace(id="test-subnet", lifecycle_state="AVAILABLE"))
        args = arguments("network", "subnet", "create", "--compartment-id", "test", "--vcn-id", "test-vcn", "--cidr-block", "10.0.0.0/24",
            "--route-table-id", "test-route", "--security-list-ids", '["test-list"]', "--prohibit-public-ip-on-vnic", "false", "--wait-for-state", "AVAILABLE")
        with patch("oci_api.time.sleep"):
            result = oci_api.execute(args, self.sdk(), client)
        self.assertEqual(result.lifecycle_state, "AVAILABLE")
        self.assertEqual(client.create_subnet.call_count, 1)
        self.assertEqual(client.get_subnet.call_count, 1)

    def test_sdk_output_matches_existing_hyphenated_contract(self):
        source = {"compartment_id": None, "shape_config": {"memory_in_gbs": 12}, "items": [{"lifecycle_state": "RUNNING"}]}
        self.assertEqual(oci_api.hyphenated(source), {"compartment-id": None, "shape-config": {"memory-in-gbs": 12}, "items": [{"lifecycle-state": "RUNNING"}]})

    def test_adapter_cannot_delete_or_terminate_resources(self):
        with self.assertRaises(ValueError):
            oci_api.command(Mock(), arguments("compute", "instance", "update"), self.sdk())

    @unittest.skipUnless(oracle_grab.sdk_available(), "OCI SDK is optional for legacy source installs")
    def test_real_sdk_generated_clients_accept_every_adapter_operation(self):
        import oci
        factories = {"iam": oci.identity.IdentityClient, "compute": oci.core.ComputeClient,
                     "bv": oci.core.BlockstorageClient, "network": oci.core.VirtualNetworkClient}
        operations = [
            ("iam", "region-subscription", "list", "--tenancy-id", "test"),
            ("iam", "availability-domain", "list", "--compartment-id", "test"),
            ("iam", "compartment", "list", "--compartment-id", "test", "--compartment-id-in-subtree", "true", "--access-level", "ANY"),
            ("compute", "instance", "list", "--compartment-id", "test"),
            ("compute", "instance", "get", "--instance-id", "test"),
            ("compute", "image", "list", "--compartment-id", "test", "--shape", oracle_grab.SHAPE, "--operating-system", "Canonical Ubuntu", "--operating-system-version", "24.04", "--sort-by", "TIMECREATED", "--sort-order", "DESC"),
            ("bv", "volume", "list", "--compartment-id", "test"),
            ("bv", "boot-volume", "list", "--compartment-id", "test", "--availability-domain", "AD-1"),
            ("network", "subnet", "get", "--subnet-id", "test"),
            ("network", "security-list", "get", "--security-list-id", "test"),
            ("network", "vcn", "create", "--compartment-id", "test", "--cidr-block", "10.0.0.0/16"),
            ("network", "internet-gateway", "create", "--compartment-id", "test", "--vcn-id", "test", "--is-enabled", "true"),
            ("network", "route-table", "update", "--rt-id", "test", "--route-rules", '[{"destination":"0.0.0.0/0","destinationType":"CIDR_BLOCK","networkEntityId":"test"}]'),
            ("network", "security-list", "update", "--security-list-id", "test", "--ingress-security-rules", "[]", "--egress-security-rules", '[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]'),
            ("network", "subnet", "create", "--compartment-id", "test", "--vcn-id", "test", "--cidr-block", "10.0.0.0/24", "--security-list-ids", '["test"]', "--prohibit-public-ip-on-vnic", "false"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "test.pub"
            key.write_text("ssh-ed25519 TEST_ONLY", encoding="utf-8")
            operations.append(("compute", "instance", "launch", "--availability-domain", "AD-1", "--compartment-id", "test",
                "--shape", oracle_grab.SHAPE, "--shape-config", '{"ocpus":2,"memoryInGBs":12}',
                "--source-details", '{"sourceType":"image","imageId":"test","bootVolumeSizeInGBs":50,"bootVolumeVpusPerGB":10}',
                "--subnet-id", "test", "--assign-public-ip", "true", "--ssh-authorized-keys-file", str(key)))
            for operation in operations:
                with self.subTest(operation=operation[:3]):
                    client = factories[operation[0]]({"region": "us-ashburn-1"}, signer=Mock(spec=oci.auth.signers.KeyPairSigner))
                    client.base_client.call_api = Mock(return_value=oci.response.Response(200, {}, [] if operation[2] == "list" else {}, None))
                    oci_api.execute(arguments(*operation), oci, client)
                    client.base_client.call_api.assert_called_once()


if __name__ == "__main__":
    unittest.main()
