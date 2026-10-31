"""Local-only HTTP control panel. No web framework or build step."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import urlparse
from urllib.request import urlopen
import webbrowser

from oracle_grab import DEFAULTS, settings
from runtime import data_directory, engine_command, migrate_state, process_lock, write_json
from onboarding import setup_status, generate_api_key, save_account, detect_proxies, account_tenancy

ROOT = Path(__file__).resolve().parent
ACTIVE = {"preflight", "running", "waiting", "stopping", "verifying", "provisioning"}


class ThreadingHTTPServer(_ThreadingHTTPServer):
    def server_bind(self):
        # This application only serves loopback. HTTPServer's reverse-DNS lookup
        # can stall startup for tens of seconds on otherwise healthy macOS hosts.
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def connection_error(message, config):
    text = message.lower()
    if "proxy" in text or (config["proxy_url"] and any(x in text for x in ("timed out", "refused", "timeout"))):
        return "无法连接所选代理。请确认代理软件已启动、端口正确，或返回网络步骤选择其他连接方式。"
    if "notauthenticated" in text or "status': 401" in text:
        return "Oracle 未接受这份 API 凭据。请确认公钥已添加到当前用户、配置与私钥匹配，并检查系统时间。"
    if "notauthorized" in text:
        return "账户无权读取租户或区域信息。请检查配置中的 tenancy、user 以及账户权限。"
    if "certificate verify failed" in text or "sslerror" in text:
        return "TLS 证书验证失败。请检查系统时间和代理的 HTTPS 检查设置。"
    if any(x in text for x in ("getaddrinfo", "nameresolution", "name or service not known")):
        return "网络无法解析服务地址。请检查网络连接或选择可用的代理后重试。"
    if any(x in text for x in ("timed out", "timeout", "refused")):
        return "网络连接失败或超时。请检查当前网络，或返回网络步骤设置代理后重试。"
    return message


def worker_command(c, mode):
    return engine_command(mode)


class Workbench:
    def __init__(self, data_dir=None):
        self.dir = data_dir or data_directory()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.process = None
        self.token = secrets.token_urlsafe(32)
        self.config = dict(DEFAULTS)
        self.state = dict(state="idle", message="准备就绪后，即可开始轮询", round=0, attempt=0,
                          next_at=None, current=None, egress=None, egress_error=None,
                          inventory=None, image=None, history=[], logs=[])
        try:
            saved = json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
            self.config = settings(saved["config"])
            self.state.update(saved["state"])
            migrate_state(self.state)
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def persist(self):
        write_json(self.dir / "state.json", dict(config=self.config, state=self.state))

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(dict(app="oracle-a1-workbench", config=self.config, **self.state,
                              busy=self.process is not None and self.process.poll() is None, token=self.token)))

    def event(self, event):
        with self.lock:
            kind = event["type"]
            if kind == "state":
                self.state.update(state=event["state"], message=event["message"], next_at=event.get("next_at"))
            elif kind == "round":
                self.state.update(round=event["round"], state="preflight", message="核查当前资源与网络", next_at=None)
            elif kind == "request":
                self.state.update(current=event, attempt=event["attempt"], state="running", message="创建请求进行中")
            elif kind == "result":
                self.state["history"] = [event] + self.state["history"][:4]
                self.state["current"] = event
            elif kind == "verification":
                current = self.state.get("current") or {}
                self.state["current"] = {**current, **{k: v for k, v in event.items() if k not in ("type", "at")}}
                if event.get("checked_at"):
                    self.state["current"].pop("error", None)
                # Verification does not add a fake creation request to the five-result history.
            elif kind == "region":
                self.state["region"] = event["name"]
            elif kind in ("egress", "inventory", "image"):
                self.state[kind] = event
                if kind == "egress":
                    self.state["egress_error"] = None
            elif kind == "egress_error":
                self.state["egress_error"] = event["message"]
            elif kind == "subnet":
                self.config["subnet"] = event["id"]
            self.state["logs"] = (self.state["logs"] + [event])[-60:]
            with (self.dir / "events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.persist()

    def save(self, value):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("请先停止轮询，再修改配置")
            candidate = settings(value)
            current = self.state.get("current") or {}
            changed_account = any(candidate[key] != self.config[key] for key in ("config_file", "profile"))
            if current.get("instance_id") and changed_account:
                previous = current.get("tenancy") or account_tenancy(self.config)
                if not previous or previous != account_tenancy(candidate):
                    raise ValueError("已有实例记录，不能切换到其他 tenancy；请使用独立数据目录。")
            self.config = candidate
            self.persist()

    def setup(self):
        with self.lock:
            return setup_status(self.config, self.dir)

    def configure_account(self, value):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("请先停止任务，再配置账户")
            previous_tenancy = account_tenancy(self.config)
            config = save_account(self.dir, self.config, value,
                                  protect_instance=bool((self.state.get("current") or {}).get("instance_id")))
            if previous_tenancy != account_tenancy(config):
                self.state.update(current=None, region="", inventory=None, image=None, state="idle",
                                  message="新账户已保存，请验证账户与网络", next_at=None)
            self.save(config)
            return dict(config=self.config, message="账户配置已保存到本机；请继续检测网络并验证账户。")

    def generate_key(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("请先停止任务，再配置密钥")
            return generate_api_key(self.dir)

    def authenticate(self):
        with self.lock:
            config = dict(self.config)
        result = self.inspect(config, "auth")
        with self.lock:
            if config != self.config:
                raise ValueError("配置已变更，请重新验证账户")
            self.event(dict(type="region", at=time.time(), name=result["region"]))
        return result

    def start(self, value):
        with self.lock:
            if (self.state.get("current") or {}).get("instance_id"):
                raise ValueError("已有实例编号，请使用“刷新实例状态”；不会再次创建")
            self.save(value)
            self.state.update(state="preflight", message="正在启动执行器并核查环境", round=0, attempt=0,
                              current=None, next_at=None, inventory=None, image=None, egress=None, egress_error=None)
            self.spawn("worker")

    def verify(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("执行器正在运行，请等待或先停止")
            instance_id = (self.state.get("current") or {}).get("instance_id")
            if not instance_id:
                raise ValueError("尚无已接受的实例编号")
            self.state.update(state="verifying", message="正在只读核验实例状态", next_at=None)
            self.spawn("worker-verify", "--instance-id", instance_id)

    def spawn(self, mode, *args):
        self.process = subprocess.Popen(worker_command(self.config, mode) + list(args), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                        errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        process = self.process
        process.stdin.write(json.dumps(self.config) + "\n")
        process.stdin.flush()
        self.persist()
        threading.Thread(target=self.read_events, args=(process,), daemon=True).start()
        threading.Thread(target=self.read_errors, args=(process,), daemon=True).start()

    def read_errors(self, process):
        for line in process.stderr:
            clean = line.replace("\x00", "").strip()
            if clean:
                self.event(dict(type="log", at=time.time(), message=clean[-1500:]))

    def read_events(self, process):
        for line in process.stdout:
            try:
                self.event(json.loads(line))
            except (ValueError, KeyError, TypeError):
                self.event(dict(type="log", at=time.time(), message=line.strip()[-1500:]))
        rc = process.wait()
        with self.lock:
            if self.process is process and self.state["state"] in ACTIVE:
                known = (self.state.get("current") or {}).get("instance_id")
                self.event(dict(type="state", at=time.time(), state="unverified" if known else "error" if rc else "stopped",
                                message="状态待核验：执行器已退出，已保留实例编号" if known else f"执行器已退出（{rc}），请查看运行日志", next_at=None))

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None and self.state["state"] in ACTIVE:
                try:
                    self.process.stdin.write("stop\n")
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    pass
                self.state.update(state="stopping", message="正在停止；已提交的创建请求需核查云端结果", next_at=None)
                self.persist()

    def inspect(self, value, mode):
        c = settings(value)
        result = subprocess.run(worker_command(c, mode) + ["--stdin-config"], input=json.dumps(c) + "\n", capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=150 if mode == "auth" else 30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            lines = result.stderr.replace("\x00", "").strip().splitlines()
            message = connection_error(lines[-1], c) if lines else "执行环境检测失败，请检查依赖或使用完整发行包"
            if mode == "egress":
                self.event(dict(type="egress_error", at=time.time(), message=message))
            raise ValueError(message)
        data = json.loads(result.stdout)
        if mode == "egress":
            self.event(dict(type="egress", at=time.time(), **data))
        return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, value, content_type="application/json; charset=utf-8"):
        body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, (dict, list)) else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def local_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        if not self.local_host():
            return self.reply(403, {"error": "Local access only"})
        if self.path == "/api/state":
            return self.reply(200, self.server.app.snapshot())
        if self.path == "/api/setup":
            return self.reply(200, self.server.app.setup())
        if self.path == "/api/proxies":
            return self.reply(200, detect_proxies())
        path = urlparse(self.path).path
        target = (ROOT / "web" / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if not target.is_relative_to((ROOT / "web").resolve()) or not target.is_file():
            return self.reply(404, {"error": "Not found"})
        return self.reply(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def do_POST(self):
        app = self.server.app
        origin = self.headers.get("Origin")
        if (not self.local_host() or self.headers.get("X-Workbench-Token") != app.token
                or (origin and origin not in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"))):
            return self.reply(403, {"error": "请求来源不正确，请从本地工作台操作"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                raise ValueError("请求大小不正确")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("请求必须是 JSON 对象")
            if self.path == "/api/setup/key":
                return self.reply(200, app.generate_key())
            elif self.path == "/api/setup/account":
                return self.reply(200, app.configure_account(value))
            elif self.path == "/api/setup/auth":
                return self.reply(200, app.authenticate())
            elif self.path == "/api/shutdown":
                app.stop()
                self.reply(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif self.path == "/api/save":
                app.save(value)
            elif self.path == "/api/start":
                app.start(value)
            elif self.path == "/api/verify":
                app.verify()
            elif self.path == "/api/stop":
                app.stop()
            elif self.path in ("/api/egress", "/api/inspect"):
                return self.reply(200, app.inspect(value, self.path.removeprefix("/api/")))
            else:
                return self.reply(404, {"error": "Not found"})
            self.reply(200, app.snapshot())
        except (ValueError, OSError, subprocess.SubprocessError, TypeError) as error:
            self.reply(400, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="Override the private runtime directory")
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        if args.open:
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/api/state", timeout=2) as response:
                    existing = json.load(response)
                if existing.get("app") == "oracle-a1-workbench":
                    webbrowser.open(f"http://127.0.0.1:{args.port}")
                    return
            except (OSError, ValueError):
                pass
        raise
    try:
        with process_lock((args.data_dir or data_directory()) / "workbench.lock"):
            server.app = Workbench(args.data_dir)
            url = f"http://127.0.0.1:{server.server_port}"
            print(f"A1 Workbench: {url}", flush=True)
            if args.open:
                webbrowser.open(url + ("/setup.html" if not server.app.setup()["ready"] else "/"))
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.app.stop()
    except BlockingIOError:
        raise SystemExit("此数据目录已有工作台在运行，请使用已有窗口或指定独立的 --data-dir。") from None
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
