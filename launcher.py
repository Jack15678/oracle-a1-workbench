"""Single entry point for source use and the two packaged executables."""
import json
import os
import sys


def main():
    args = sys.argv[1:]
    mode = args[0] if args and not args[0].startswith("-") else "gui"
    if mode == "self-test":
        # Offline packaged-runtime check: no credentials, sockets or cloud calls.
        import oci
        import cryptography
        import certifi
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from oci.core.models import LaunchInstanceShapeConfigDetails
        from pathlib import Path
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode("ascii")
        config = dict(region="us-ashburn-1", tenancy="ocid1.tenancy.oc1..test", user="ocid1.user.oc1..test",
                      fingerprint=":".join(["00"] * 16), key_file="unused-self-test-key", key_content=pem)
        signer = oci.Signer(config["tenancy"], config["user"], config["fingerprint"], None, private_key_content=pem)
        from oci._vendor.requests import Request
        for factory in (oci.identity.IdentityClient, oci.core.ComputeClient,
                        oci.core.BlockstorageClient, oci.core.VirtualNetworkClient):
            client = factory(config, signer=signer, service_endpoint="https://example.invalid")
            request = Request("GET", "https://example.invalid/offline-self-test").prepare()
            signer(request)  # Prepare/sign only. Never call session.send or an API method.
            assert request.headers["authorization"].startswith("Signature ")
            client.base_client.session.close()
        assert oci.util.to_dict(LaunchInstanceShapeConfigDetails(ocpus=1))["ocpus"] == 1
        assert callable(oci.identity.IdentityClient) and callable(oci.core.VirtualNetworkClient)
        assert Path(certifi.where()).is_file()
        import oracle_grab
        import onboarding
        print(json.dumps({"ok": True, "sdk": oci.__version__, "cryptography": cryptography.__version__,
                          "frozen": bool(getattr(sys, "frozen", False))}))
        return
    if mode == "oci-api":
        sys.argv = [sys.argv[0], *args[1:]]
        from oci_api import main as entry
    elif mode in ("run", "inspect", "egress", "verify", "worker", "worker-verify", "auth"):
        from oracle_grab import main as entry
    else:
        if mode != "gui":
            raise SystemExit("Use gui, run, inspect, egress, verify or auth. See README.md.")
        args = args[1:] if args and args[0] == "gui" else args
        open_browser = "--no-open" not in args
        args = [arg for arg in args if arg != "--no-open"]
        if open_browser and "--open" not in args:
            args.append("--open")
        sys.argv = [sys.argv[0], *args]
        # A Windows windowed executable has no standard streams. Keep failures
        # in the same private data directory as the UI instead of losing them.
        if sys.stdout is None or sys.stderr is None:
            from runtime import data_directory
            directory = data_directory()
            directory.mkdir(parents=True, exist_ok=True)
            log = (directory / "launcher.log").open("a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stdout or log
            sys.stderr = sys.stderr or log
        if sys.stdin is None:
            sys.stdin = open(os.devnull, encoding="utf-8")
        from workbench import main as entry
    return entry()


if __name__ == "__main__":
    raise SystemExit(main())
