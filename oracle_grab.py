"""A1 creation engine shared by the CLI and local workbench. Stdlib only."""
import argparse
import configparser
import ipaddress
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener
from runtime import data_directory, write_json

VERIFY_INTERVAL = 15
VERIFY_TIMEOUT = 600
SHAPE = "VM.Standard.A1.Flex"
PRESETS = {"2/12": (2, 12), "1/6": (1, 6), "1/4": (1, 4)}
DEFAULTS = dict(distro="Ubuntu-24.04", compartment="", profile="DEFAULT",
                config_file="~/.oci/config", ssh_key="~/.ssh/oracle.pub",
                trusted_ip="", proxy_url="", subnet="", name="oracle-a1",
                presets=["2/12", "1/6", "1/4"], round_min=120, round_max=300,
                max_rounds=0, boot_gb=50)


def settings(value):
    if not isinstance(value, dict):
        raise ValueError("配置必须是 JSON 对象")
    c = {**DEFAULTS, **{k: v for k, v in value.items() if k in DEFAULTS}}
    for key in ("round_min", "round_max", "max_rounds", "boot_gb"):
        if isinstance(c[key], bool) or not str(c[key]).isdigit():
            raise ValueError(f"{key} 必须是整数")
        c[key] = int(c[key])
    if not 120 <= c["round_min"] <= c["round_max"] <= 86400:
        raise ValueError("轮询间隔应在 120–86400 秒之间，最大值不能小于最小值")
    if not 0 <= c["max_rounds"] <= 1000000 or not 50 <= c["boot_gb"] <= 200:
        raise ValueError("轮数应为 0–1000000；启动盘应为 50–200 GB")
    if not isinstance(c["presets"], list) or not c["presets"] or any(p not in PRESETS for p in c["presets"]):
        raise ValueError("至少选择一种 A1 规格")
    c["presets"] = [p for p in PRESETS if p in c["presets"]]
    for key in ("distro", "compartment", "profile", "config_file", "ssh_key", "trusted_ip", "proxy_url", "subnet", "name"):
        if not isinstance(c[key], str) or len(c[key]) > 2048 or any(x in c[key] for x in "\r\n\x00"):
            raise ValueError(f"{key} 格式不正确")
        c[key] = c[key].strip()
    if any(not c[k] for k in ("profile", "distro", "name", "config_file", "ssh_key")):
        raise ValueError("发行版、配置路径、Profile、公钥路径和实例名称不能为空")
    if c["proxy_url"]:
        u = urlparse(c["proxy_url"])
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise ValueError("代理请填写 http://主机:端口；此工作台不保存带账号密码的代理 URL")
        _ = u.port
    if c["trusted_ip"]:
        try:
            net = ipaddress.ip_network(c["trusted_ip"], strict=True)
            if net.version != 4 or net.prefixlen != 32 or not net.network_address.is_global or net.network_address.is_multicast:
                raise ValueError()
        except ValueError:
            raise ValueError("SSH 放行地址必须是一个公网 IPv4/32") from None
    if c["compartment"] and not c["compartment"].startswith(("ocid1.tenancy.", "ocid1.compartment.")):
        raise ValueError("Compartment 应是 compartment 或 tenancy OCID；留空使用 root")
    if c["subnet"] and not c["subnet"].startswith("ocid1.subnet."):
        raise ValueError("子网 OCID 格式不正确")
    return c


def egress(c):
    proxy = c["proxy_url"]
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    with opener.open("https://www.cloudflare.com/cdn-cgi/trace", timeout=12) as response:
        data = dict(line.split("=", 1) for line in response.read().decode("utf-8").splitlines() if "=" in line)
    ipaddress.ip_address(data["ip"])
    return {"ip": data["ip"], "country": data.get("loc", ""), "city": "", "org": "",
            "provider": "Cloudflare Trace", "checked_at": time.time(), "via_proxy": bool(proxy), "proxy_url": proxy,
            "note": "与 Oracle 请求共用代理配置的出口探测；OCI 不回显源 IP。"}


def find_oci():
    return shutil.which("oci") or next((str(Path(p).expanduser()) for p in
        ("~/.local/bin/oci", "~/bin/oci", "~/.local/share/oracle-a1/venv/bin/oci")
        if Path(p).expanduser().is_file()), None)


def inspect_environment(c):
    config_path, ssh_path = Path(c["config_file"]).expanduser(), Path(c["ssh_key"]).expanduser()
    result = dict(python=sys.version.split()[0], oci=find_oci(), config=config_path.is_file(),
                  api_key=False, ssh_key=False, config_path=str(config_path), ssh_key_path=str(ssh_path))
    if result["config"]:
        try:
            parser = configparser.ConfigParser(interpolation=None)
            with config_path.open(encoding="utf-8-sig") as file:
                parser.read_file(file)
            profile = parser[c["profile"]]
            for field in ("user", "tenancy", "fingerprint", "key_file"):
                if not profile.get(field, "").strip():
                    raise ValueError(f"配置缺少 {field}")
            result["api_key"] = Path(profile["key_file"]).expanduser().is_file()
            if not result["api_key"]:
                result["config_error"] = "key_file 未指向存在的 API 私钥"
        except (OSError, ValueError, KeyError, configparser.Error) as error:
            result["config_error"] = str(error)
    if ssh_path.is_file():
        try:
            result["ssh_key"] = ssh_path.read_text().startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-"))
        except (OSError, UnicodeError):
            pass
    return result


class OciError(RuntimeError):
    def __init__(self, message, code="", status=0):
        super().__init__(message)
        self.code, self.status = code, status


def parse_error(text):
    # OCI prefixes its JSON with ServiceError. Never match digits in request IDs.
    try:
        data = json.JSONDecoder().raw_decode(text[text.index("{"):])[0]
        return OciError(data.get("message", text), data.get("code", ""), data.get("status", 0))
    except (ValueError, TypeError, AttributeError):
        return OciError(text.strip()[-3000:] or "OCI CLI 未返回结果")


class Engine:
    def __init__(self, c, emit, stop=None, receipt_path=None):
        self.c, self.emit = settings(c), emit
        self.stop = stop or threading.Event()
        self.base, self.tenancy, self.ads = [], "", []
        self.region, self.instance_id, self.lifecycle = "", "", ""
        self.receipt_path = receipt_path
        self.verify_deadline = None
        self.subnet, self.image, self.round, self.attempt = self.c["subnet"], "", 0, 0

    def event(self, kind, **data):
        self.emit({"type": kind, "at": time.time(), **data})

    def call(self, *args):
        if self.stop.is_set():
            raise InterruptedError("用户已停止")
        env = {k: v for k, v in os.environ.items() if k.lower() not in
               ("http_proxy", "https_proxy", "all_proxy", "no_proxy") and not k.startswith("OCI_CLI_")}
        process = subprocess.Popen(self.base + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", env=env)
        deadline = min(time.monotonic() + 120, self.verify_deadline or float("inf"))
        while True:
            try:
                out, err = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if self.stop.is_set() or time.monotonic() > deadline:
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    if self.stop.is_set():
                        raise InterruptedError("用户已停止；已提交的创建请求需以云端状态为准")
                    raise OciError("OCI 请求超时；停止重试，下次启动将先核查已有实例")
        if process.returncode:
            raise parse_error(err or out)
        # OCI CLI 3.95 emits no stdout for a successful empty paginated list.
        if not out.strip() and len(args) >= 3 and args[2] == "list" and "--all" in args:
            return []
        try:
            result = json.loads(out)["data"]
            if result is None:
                raise ValueError()
            return result
        except (ValueError, KeyError, TypeError):
            raise OciError("OCI 返回了无效或缺失的数据，已停止") from None

    def listing(self, *args):
        data = self.call(*args, "--all")
        if not isinstance(data, list):
            raise OciError("资源列表格式错误，已停止")
        return data

    def preflight(self, for_launch=True):
        cli = find_oci()
        if not cli:
            raise ValueError("Ubuntu 中尚未安装 OCI CLI，见 README 中的安装命令")
        config_file = Path(self.c["config_file"]).expanduser()
        config = configparser.ConfigParser(interpolation=None)
        with config_file.open(encoding="utf-8-sig") as file:
            config.read_file(file)
        profile = config[self.c["profile"]]
        self.tenancy = profile["tenancy"].strip()
        if not self.tenancy.startswith("ocid1.tenancy."):
            raise ValueError("配置中的 tenancy OCID 不正确")
        if not Path(profile["key_file"]).expanduser().is_file():
            raise ValueError("配置中的 API 私钥路径不存在")
        if for_launch:
            key = Path(self.c["ssh_key"]).expanduser()
            if not key.read_text().startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
                raise ValueError("SSH 公钥文件格式不正确；不要使用 OCI API 公钥")
            self.c["ssh_key"] = str(key)
        self.c["compartment"] = self.c["compartment"] or self.tenancy
        bootstrap = profile.get("region", "").strip()
        if not bootstrap:
            raise ValueError("OCI 配置缺少 region，无法查询区域订阅")
        self.base = [cli, "--config-file", str(config_file), "--profile", self.c["profile"], "--region", bootstrap,
                     "--output", "json", "--no-retry", "--connection-timeout", "15", "--read-timeout", "60"]
        if self.c["proxy_url"]:
            self.base += ["--proxy", self.c["proxy_url"]]
        subscriptions = self.listing("iam", "region-subscription", "list", "--tenancy-id", self.tenancy)
        homes = [x["region-name"] for x in subscriptions if x.get("is-home-region") is True and x.get("status") == "READY"]
        if len(homes) != 1:
            raise ValueError("无法唯一确认可用的 Home Region，已停止")
        self.region = homes[0]
        self.base[self.base.index("--region") + 1] = self.region
        self.event("region", name=self.region)
        if for_launch:
            self.ads = [x["name"] for x in self.listing("iam", "availability-domain", "list", "--compartment-id", self.tenancy)]
            if not self.ads:
                raise ValueError("Home Region 没有返回可用域")
        self.event("log", message=f"认证通过，Home Region 为 {self.region}。")

    def receipt(self, save=False):
        if self.receipt_path is None:
            return ""
        records = json.loads(self.receipt_path.read_text(encoding="utf-8")) if self.receipt_path.exists() else {}
        key = self.tenancy + "/" + self.region
        if save:
            records[key] = dict(instance_id=self.instance_id, region=self.region, lifecycle=self.lifecycle, at=time.time())
            write_json(self.receipt_path, records)
        return records.get(key, {}).get("instance_id", "")

    def verify(self, instance_id, wait=False):
        """Only GET requests; a failed/unknown result never enables another launch."""
        self.instance_id = instance_id
        self.verify_deadline = time.monotonic() + VERIFY_TIMEOUT
        try:
            self.receipt(save=True)
            self.event("state", state="verifying", message="正在只读核验实例状态")
            while True:
                result = self.call("compute", "instance", "get", "--instance-id", instance_id)
                if result.get("id") != instance_id or not result.get("lifecycle-state"):
                    raise OciError("实例查询返回的数据不完整")
                self.lifecycle = result["lifecycle-state"]
                self.receipt(save=True)
                self.event("verification", instance_id=instance_id, lifecycle=self.lifecycle,
                           region=self.region, shape=result.get("shape", ""),
                           shape_config=result.get("shape-config", {}), name=result.get("display-name", ""),
                           checked_at=time.time())
                if self.lifecycle == "RUNNING":
                    self.event("state", state="ready", message="已运行：只读查询已确认 RUNNING")
                    return
                if self.lifecycle in ("TERMINATING", "TERMINATED", "FAILED", "FAULTY"):
                    self.event("state", state="failed", message=f"实例状态为 {self.lifecycle}，任务已结束；未删除资源")
                    return
                if self.lifecycle not in ("PROVISIONING", "STARTING") or not wait:
                    self.event("state", state="unverified", message=f"状态待核验：当前为 {self.lifecycle}")
                    return
                remaining = self.verify_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("核验已等待 10 分钟")
                self.event("state", state="provisioning", message="创建已接受，正在部署；每 15 秒核验一次")
                if self.stop.wait(min(VERIFY_INTERVAL, remaining)):
                    raise InterruptedError("用户停止核验")
                if time.monotonic() >= self.verify_deadline:
                    raise TimeoutError("核验已等待 10 分钟")
        except Exception as error:
            self.event("verification", instance_id=instance_id, lifecycle=self.lifecycle,
                       region=self.region, error=str(error))
            self.event("state", state="unverified", message=f"状态待核验：{error}；已保留实例编号，不会重新创建")
        finally:
            self.verify_deadline = None

    def verify_only(self, instance_id):
        self.instance_id = instance_id
        try:
            self.preflight(for_launch=False)
            self.verify(instance_id)
        except Exception as error:
            self.event("verification", instance_id=instance_id, error=str(error))
            self.event("state", state="unverified", message=f"状态待核验：{error}；已保留实例编号")

    def inventory(self):
        children = self.listing("iam", "compartment", "list", "--compartment-id", self.tenancy,
                                "--compartment-id-in-subtree", "true", "--access-level", "ANY")
        compartments = {self.tenancy} | {x["id"] for x in children if x["lifecycle-state"] != "DELETED"}
        if self.c["compartment"] not in compartments:
            raise ValueError("目标 compartment 不属于当前 tenancy")
        total, existing = 0, []
        for cid in sorted(compartments):
            instances = self.listing("compute", "instance", "list", "--compartment-id", cid)
            existing += [i for i in instances if i["shape"] == SHAPE and i["lifecycle-state"] != "TERMINATED"]
            volumes = self.listing("bv", "volume", "list", "--compartment-id", cid)
            for ad in self.ads:
                volumes += self.listing("bv", "boot-volume", "list", "--compartment-id", cid, "--availability-domain", ad)
            for volume in volumes:
                if volume["lifecycle-state"] == "TERMINATED":
                    continue
                size = volume["size-in-gbs"]
                if not isinstance(size, (int, float)) or isinstance(size, bool) or size < 0:
                    raise ValueError("磁盘容量无法确认，已停止")
                total += size
        self.event("inventory", storage_gb=total, compartment_count=len(compartments), existing=len(existing))
        if existing:
            self.event("existing", instances=[{"id": i["id"], "state": i["lifecycle-state"], "name": i.get("display-name", "")} for i in existing])
            return False
        if total + self.c["boot_gb"] > 200:
            raise ValueError(f"已有磁盘 {total:g} GB，加 {self.c['boot_gb']} GB 将超过 200 GB")
        return True

    def find_image(self):
        for system, version in (("Canonical Ubuntu", "24.04"), ("Canonical Ubuntu", "22.04"), ("Oracle Linux", "9")):
            images = self.listing("compute", "image", "list", "--compartment-id", self.c["compartment"],
                                  "--operating-system", system, "--operating-system-version", version,
                                  "--shape", SHAPE, "--sort-by", "TIMECREATED", "--sort-order", "DESC")
            for image in images:
                if ("compartment-id" in image and image["compartment-id"] is None
                        and image["lifecycle-state"] == "AVAILABLE" and image.get("listing-type") != "COMMUNITY"):
                    self.event("image", name=image.get("display-name", system), id=image["id"])
                    return image["id"]
        raise ValueError("没有找到兼容 A1 的官方 Ubuntu / Oracle Linux 镜像")

    def ensure_subnet(self):
        if self.subnet:
            self.validate_subnet()
            return
        self.event("log", message="创建 VCN、互联网网关和专用子网。" +
                   ("SSH 仅放行指定 IP。" if self.c["trusted_ip"] else "SSH 入站保持关闭。"))
        cid = self.c["compartment"]
        vcn = self.call("network", "vcn", "create", "--compartment-id", cid, "--cidr-block", "10.0.0.0/16",
                        "--display-name", "vcn-oracle-a1", "--dns-label", "vcna1")
        gateway = self.call("network", "internet-gateway", "create", "--compartment-id", cid,
                            "--vcn-id", vcn["id"], "--is-enabled", "true", "--display-name", "igw-oracle-a1")
        routes = [{"destination": "0.0.0.0/0", "destinationType": "CIDR_BLOCK", "networkEntityId": gateway["id"]}]
        self.call("network", "route-table", "update", "--rt-id", vcn["default-route-table-id"], "--force", "--route-rules", json.dumps(routes))
        ingress = [{"source": self.c["trusted_ip"], "sourceType": "CIDR_BLOCK", "protocol": "6", "isStateless": False,
                    "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}}}] if self.c["trusted_ip"] else []
        self.call("network", "security-list", "update", "--security-list-id", vcn["default-security-list-id"], "--force",
                  "--ingress-security-rules", json.dumps(ingress), "--egress-security-rules",
                  '[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]')
        subnet = self.call("network", "subnet", "create", "--compartment-id", cid, "--vcn-id", vcn["id"],
                           "--cidr-block", "10.0.0.0/24", "--display-name", "subnet-public-a1", "--dns-label", "puba1",
                           "--route-table-id", vcn["default-route-table-id"], "--security-list-ids", json.dumps([vcn["default-security-list-id"]]),
                           "--prohibit-public-ip-on-vnic", "false", "--wait-for-state", "AVAILABLE")
        self.subnet = subnet["id"]
        self.event("subnet", id=self.subnet)
        self.validate_subnet()

    def validate_subnet(self):
        subnet = self.call("network", "subnet", "get", "--subnet-id", self.subnet)
        if subnet["lifecycle-state"] != "AVAILABLE" or subnet["prohibit-public-ip-on-vnic"]:
            raise ValueError("子网必须是 AVAILABLE 状态的公共子网")
        allows_ssh = False
        for sid in subnet["security-list-ids"]:
            rules = self.call("network", "security-list", "get", "--security-list-id", sid)["ingress-security-rules"]
            for rule in rules:
                ports = (rule.get("tcp-options") or {}).get("destination-port-range")
                opens_ssh = rule["protocol"] == "all" or (rule["protocol"] == "6" and (not ports or ports["min"] <= 22 <= ports["max"]))
                if opens_ssh:
                    if not self.c["trusted_ip"]:
                        raise ValueError("当前未启用 SSH，但已有子网开放了 SSH；请使用没有 SSH 入站规则的子网")
                    if rule.get("source-type", "CIDR_BLOCK") != "CIDR_BLOCK" or rule["source"] != self.c["trusted_ip"]:
                        raise ValueError("子网存在其他来源的 SSH 放行规则；请使用专用子网或收紧规则")
                    allows_ssh = True
        if self.c["trusted_ip"] and not allows_ssh:
            raise ValueError("子网尚未放行填写的 SSH IP")

    def run(self):
        self.event("state", state="preflight", message="正在核查环境与账户")
        try:
            self.preflight()
            known = self.receipt()
            if known:
                self.verify(known, wait=True)
                return
            while not self.stop.is_set():
                self.round += 1
                self.event("round", round=self.round)
                try:
                    self.event("egress", **egress(self.c))
                except Exception as error:
                    self.event("egress_error", message=str(error))
                throttled = False
                for preset, ad in ((p, ad) for p in self.c["presets"] for ad in self.ads):
                    if not self.inventory():
                        self.event("state", state="existing", message="已有 A1（含停止中的实例），未重复创建")
                        return
                    self.image = self.image or self.find_image()
                    self.ensure_subnet()
                    cpu, ram = PRESETS[preset]
                    self.attempt += 1
                    request = dict(attempt=self.attempt, round=self.round, preset=preset, ocpu=cpu, ram_gb=ram,
                                   boot_gb=self.c["boot_gb"], shape=SHAPE, ad=ad, region=self.region, name=self.c["name"])
                    self.event("request", **request)
                    started = time.monotonic()
                    source = dict(sourceType="image", imageId=self.image, bootVolumeSizeInGBs=self.c["boot_gb"], bootVolumeVpusPerGB=10)
                    try:
                        result = self.call("compute", "instance", "launch", "--availability-domain", ad,
                                           "--compartment-id", self.c["compartment"], "--shape", SHAPE,
                                           "--shape-config", json.dumps({"ocpus": cpu, "memoryInGBs": ram}),
                                           "--source-details", json.dumps(source), "--subnet-id", self.subnet,
                                           "--assign-public-ip", "true", "--ssh-authorized-keys-file", self.c["ssh_key"],
                                           "--display-name", self.c["name"])
                        if not result.get("id"):
                            raise OciError("创建响应状态无法确认，停止重试；请检查 OCI 控制台")
                    except OciError as error:
                        capacity = error.code in ("OutOfHostCapacity", "OutOfCapacity") or "out of host capacity" in str(error).lower()
                        throttled = error.status == 429 or error.code == "TooManyRequests"
                        outcome = "throttled" if throttled else "capacity" if capacity else "error"
                        self.event("result", **request, outcome=outcome, message=str(error), code=error.code,
                                   duration=round(time.monotonic() - started, 2))
                        if throttled:
                            break
                        if capacity:
                            continue
                        raise
                    except InterruptedError:
                        self.event("result", **request, outcome="interrupted", message="本地请求已停止；云端结果待核查",
                                   duration=round(time.monotonic() - started, 2))
                        raise
                    self.instance_id, self.lifecycle = result["id"], result.get("lifecycle-state", "")
                    self.receipt(save=True)
                    self.event("result", **request, outcome="accepted", message="创建已接受，正在部署", instance_id=self.instance_id,
                               lifecycle=self.lifecycle, duration=round(time.monotonic() - started, 2))
                    self.verify(self.instance_id, wait=True)
                    return
                if self.c["max_rounds"] and self.round >= self.c["max_rounds"]:
                    self.event("state", state="exhausted", message="已达到最大轮数，尚未抢到 A1")
                    return
                delay = random.randint(self.c["round_min"], self.c["round_max"])
                if throttled:
                    delay = max(1800, delay)
                self.event("state", state="waiting", message="限流退避" if throttled else "等待下一轮", next_at=time.time() + delay)
                self.stop.wait(delay)
            raise InterruptedError("轮询已停止")
        except InterruptedError as error:
            self.event("state", state="stopped", message=str(error))
        except Exception as error:
            if self.instance_id:
                self.event("verification", instance_id=self.instance_id, lifecycle=self.lifecycle, error=str(error))
            self.event("state", state="unverified" if self.instance_id else "error", message=str(error))


def environment_config():
    mapping = dict(COMPARTMENT_OCID="compartment", SSH_PUBLIC_KEY_FILE="ssh_key", TRUSTED_IP_CIDR="trusted_ip",
                   OCI_PROFILE="profile", OCI_CLI_CONFIG_FILE="config_file", OCI_PROXY_URL="proxy_url",
                   SUBNET_OCID="subnet", ROUND_MIN_SEC="round_min", ROUND_MAX_SEC="round_max", MAX_ROUNDS="max_rounds")
    return {key: os.environ[env] for env, key in mapping.items() if env in os.environ}


def main():
    parser = argparse.ArgumentParser(description="Oracle A1: run / inspect / egress / verify")
    parser.add_argument("mode", choices=("run", "worker", "worker-verify", "egress", "inspect", "verify"))
    parser.add_argument("--config", help="JSON configuration; otherwise use environment variables")
    parser.add_argument("--stdin-config", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--instance-id")
    args = parser.parse_args()
    worker = args.mode in ("worker", "worker-verify")
    if args.config:
        value = json.loads(Path(args.config).expanduser().read_text(encoding="utf-8-sig"))
    elif worker or args.stdin_config:
        value = json.loads(sys.stdin.readline())
    else:
        value = environment_config()
    c = settings(value)
    if args.mode == "inspect":
        print(json.dumps(inspect_environment(c), ensure_ascii=False))
        return
    if args.mode == "egress":
        print(json.dumps(egress(c), ensure_ascii=False), flush=True)
        return
    if args.mode in ("verify", "worker-verify") and not (args.instance_id or "").startswith("ocid1.instance."):
        parser.error("verify requires --instance-id ocid1.instance.…")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if worker:
        def listen():
            sys.stdin.readline()  # stop command or parent exit both stop the worker
            stop.set()
        threading.Thread(target=listen, daemon=True).start()
    last_state = ["idle"]
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = Path(os.environ.get("LOG_FILE", str(directory / "engine-events.jsonl"))).expanduser()
    def emit(event):
        if event["type"] == "state":
            last_state[0] = event["state"]
        line = json.dumps(event, ensure_ascii=False)
        # Persist before stdout so the parent exiting cannot lose the accepted OCID.
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        print(line, flush=True)
    # One worker across the UI and CLI in this Linux user account.
    import fcntl
    lock_path = Path.home() / ".cache" / "oracle-a1-grab.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit(dict(type="state", at=time.time(), state="error", message="已有 A1 执行器运行，请先停止另一个副本"))
            raise SystemExit(1)
        engine = Engine(c, emit, stop, directory / "instances.json")
        if args.mode in ("verify", "worker-verify"):
            engine.verify_only(args.instance_id)
        else:
            engine.run()
    raise SystemExit({"error": 1, "failed": 1, "exhausted": 3, "unverified": 4, "stopped": 130}.get(last_state[0], 0))


if __name__ == "__main__":
    main()
