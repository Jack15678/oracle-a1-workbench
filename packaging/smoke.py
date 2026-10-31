"""Offline smoke test of frozen executables, including clean-home UI/worker IPC."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    folder = args.bundle.resolve()
    if folder.suffix == ".app":
        folder = folder / "Contents" / "MacOS"
    suffix = ".exe" if os.name == "nt" else ""
    cli, gui = folder / ("a1" + suffix), folder / ("A1Workbench" + suffix)
    assert cli.is_file() and gui.is_file(), "Missing packaged entry points"
    with tempfile.TemporaryDirectory(prefix="a1-clean-home-") as temporary:
        home = Path(temporary)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("OCI_", "PYTHON"))}
        env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "local"),
                   XDG_STATE_HOME=str(home / "state"), PYTHONUTF8="1")
        def command(*arguments):
            result = subprocess.run([str(cli), *arguments], cwd=home, env=env, text=True,
                                    encoding="utf-8", capture_output=True, timeout=90)
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        runtime = command("self-test")
        assert runtime["ok"] and runtime["frozen"], runtime
        config = home / "config.json"
        config.write_text(json.dumps({"config_file": str(home / "missing-config"),
                                      "ssh_key": str(home / "missing-key.pub")}), encoding="utf-8")
        inspected = command("inspect", "--config", str(config))
        assert not inspected["config"] and inspected["backend"] == "sdk", inspected
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([str(gui), "gui", "--no-open", "--port", str(port),
                                    "--data-dir", str(home / "ui")], cwd=home, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        opener = build_opener(ProxyHandler({}))
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    with opener.open(base + "/api/state", timeout=2) as response:
                        state = json.load(response)
                    break
                except OSError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        raise AssertionError("Packaged UI did not start")
                    time.sleep(0.25)
            assert state["app"] == "oracle-a1-workbench" and not state["busy"]
            for path, expected in (("/", b"<html"), ("/app.js", b"function"), ("/setup.html", b"<html"),
                                   ("/setup.js", b"function"), ("/vendor/gsap.min.js", b"GreenSock")):
                with opener.open(base + path, timeout=5) as response:
                    assert expected in response.read(), f"Missing bundled asset: {path}"
            with opener.open(base + "/api/setup", timeout=5) as response:
                assert not json.load(response)["ready"]
            request = Request(base + "/api/inspect", data=config.read_bytes(), method="POST",
                              headers={"Content-Type": "application/json", "X-Workbench-Token": state["token"]})
            with opener.open(request, timeout=90) as response:
                result = json.load(response)
                assert not result["config"] and result["backend"] == "sdk", result
            def post(path):
                request = Request(base + path, data=b"{}", method="POST",
                                  headers={"Content-Type": "application/json", "X-Workbench-Token": state["token"]})
                with opener.open(request, timeout=30) as response:
                    return json.load(response)
            key = post("/api/setup/key")
            assert key["public_key"].startswith("-----BEGIN PUBLIC KEY-----") and key["fingerprint"]
            assert "PRIVATE KEY" not in json.dumps(key)
            assert (home / "ui/credentials/setup-key/api_private_key.pem").is_file()
            assert post("/api/shutdown")["ok"]
            assert process.wait(timeout=15) == 0
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
    print(json.dumps({"packaged_smoke": "passed", "bundle": args.bundle.name}))


if __name__ == "__main__":
    main()
