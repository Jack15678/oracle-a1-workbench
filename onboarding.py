"""Local credential setup. Private keys never appear in HTTP responses or logs."""
import configparser
import csv
from functools import lru_cache
import hashlib
import io
import os
from pathlib import Path
import re
import socket
import subprocess
import uuid
from urllib.parse import urlparse
from urllib.request import getproxies

from oracle_grab import inspect_environment, settings


def crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
        return serialization, ed25519, rsa
    except ImportError:
        raise ValueError("缺少密钥组件。请使用完整发行包，或先安装 requirements.txt 中的依赖。") from None


@lru_cache(maxsize=1)
def windows_user_sid():
    result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/whoami.exe"), "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader(result.stdout.strip().splitlines()))[1]
    if not re.fullmatch(r"S-1-(?:\d+-)*\d+", sid):
        raise ValueError("无法确认当前 Windows 用户，尚未保存私钥")
    return sid


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        grants = []
        for sid in dict.fromkeys([windows_user_sid(), "S-1-5-18"]):
            grants += ["/grant:r", f"*{sid}:(OI)(CI)F"]
        result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe"), str(path),
                                 "/inheritance:r", *grants, "/Q"], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError("无法设置私人目录权限，尚未保存私钥")
    else:
        path.chmod(0o700)
    return path


def private_write(path, value):
    with path.open("xb") as file:
        if os.name != "nt":
            os.fchmod(file.fileno(), 0o600)
        file.write(value)


def describe_key(key):
    serialization, _, _ = crypto()
    public = key.public_key()
    der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    digest = hashlib.md5(der, usedforsecurity=False).hexdigest()
    return dict(public_key=public.public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii"),
                fingerprint=":".join(digest[i:i+2] for i in range(0, len(digest), 2)))


def load_api_key(value):
    serialization, _, rsa = crypto()
    try:
        key = serialization.load_pem_private_key(value, password=None)
    except (TypeError, ValueError):
        raise ValueError("请选择未加密的 OCI API 私钥 PEM；不要使用公钥或 SSH 私钥。") from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("OCI API 密钥必须为至少 2048 位的 RSA 私钥")
    return key


def generate_api_key(directory):
    serialization, _, rsa = crypto()
    target = private_directory(directory / "credentials" / "setup-key") / "api_private_key.pem"
    if target.exists():
        key = load_api_key(target.read_bytes())
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_write(target, key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return describe_key(key)


def setup_status(config, directory):
    result = inspect_environment(config)
    response = dict(ready=all(result.get(k) for k in ("oci", "config", "api_key", "ssh_key")),
                    runtime=result, api_public_key="", fingerprint="", data_dir=str(directory))
    key = directory / "credentials" / "setup-key" / "api_private_key.pem"
    if key.is_file():
        try:
            public = describe_key(load_api_key(key.read_bytes()))
            response.update(api_public_key=public["public_key"], fingerprint=public["fingerprint"])
        except (OSError, ValueError):
            response["key_error"] = "上次生成的密钥无法读取，请导入已有 API 密钥。"
    return response


def ssh_public_key(directory, current):
    existing = Path(current["ssh_key"]).expanduser()
    if existing.is_file() and existing.read_text(encoding="utf-8").startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        return str(existing)
    serialization, ed25519, _ = crypto()
    folder = private_directory(directory / "credentials" / "ssh")
    private, public = folder / "oracle", folder / "oracle.pub"
    if private.exists():
        try:
            key = serialization.load_ssh_private_key(private.read_bytes(), password=None)
        except (TypeError, ValueError):
            raise ValueError("已有 SSH 私钥无法读取，请在高级设置指定有效公钥；不会覆盖现有私钥。") from None
    else:
        key = ed25519.Ed25519PrivateKey.generate()
        private_write(private, key.private_bytes(serialization.Encoding.PEM,
                                                serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()))
    expected = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH) + b"\n"
    if not public.exists():
        private_write(public, expected)
    elif public.read_bytes().strip() != expected.strip():
        raise ValueError("已有 SSH 公私钥不匹配，请在高级设置选择正确的公钥")
    return str(public)


def account_tenancy(config):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with Path(config["config_file"]).expanduser().open(encoding="utf-8-sig") as file:
            parser.read_file(file)
        return parser[config["profile"]].get("tenancy", "").strip()
    except (OSError, KeyError, configparser.Error, UnicodeError):
        return ""


def save_account(directory, current, value, protect_instance=False, expected_tenancy=None):
    preview, imported = value.get("config_preview", ""), value.get("private_key", "")
    if not isinstance(preview, str) or not 0 < len(preview) <= 16384:
        raise ValueError("请粘贴 Oracle 控制台的 Configuration file preview")
    if not isinstance(imported, str) or len(imported) > 16384:
        raise ValueError("API 私钥内容格式不正确或过大")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(preview.strip())
        profile = parser["DEFAULT"]
        required = {field: profile.get(field, "").strip() for field in ("user", "tenancy", "fingerprint", "region")}
    except configparser.Error:
        raise ValueError("配置预览格式不正确，请复制完整的 [DEFAULT] 配置段") from None
    for field, prefix in (("user", "ocid1.user."), ("tenancy", "ocid1.tenancy.")):
        if not required[field].startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_.-]+", required[field]):
            raise ValueError(f"配置预览中的 {field} OCID 不正确")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", required["region"]):
        raise ValueError("配置预览缺少有效的 region")
    expected = required["fingerprint"].lower()
    if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){15}", expected):
        raise ValueError("配置预览中的 fingerprint 格式不正确")
    previous_tenancy = expected_tenancy or account_tenancy(current)
    if protect_instance and previous_tenancy != required["tenancy"]:
        raise ValueError("此工作台已有实例记录，请保持原 tenancy。需要使用另一个账户时，请使用独立的数据目录。")
    if imported.strip():
        material = imported.strip().encode("utf-8")
    else:
        key_path = directory / "credentials" / "setup-key" / "api_private_key.pem"
        if not key_path.is_file():
            raise ValueError("请先生成 API 公钥，或选择已有的 API 私钥 PEM 文件")
        material = key_path.read_bytes()
    key = load_api_key(material)
    if describe_key(key)["fingerprint"] != expected:
        raise ValueError("API 密钥与配置预览的 fingerprint 不匹配。请确认上传了对应公钥，或选择正确的私钥。")
    # Each accepted import gets its own directory; never overwrite a working key/config.
    folder = private_directory(directory / "credentials" / ("account-" + uuid.uuid4().hex))
    path = folder / "api_private_key.pem"
    private_write(path, material + b"\n")
    ssh_key = ssh_public_key(directory, current)
    config = configparser.ConfigParser(interpolation=None)
    config["DEFAULT"] = {**required, "fingerprint": expected, "key_file": str(path)}
    buffer = io.StringIO()
    config.write(buffer)
    config_path = folder / "config"
    private_write(config_path, buffer.getvalue().encode("utf-8"))
    changes = {"compartment": "", "subnet": ""} if previous_tenancy != required["tenancy"] else {}
    return settings({**current, **changes, "config_file": str(config_path), "profile": "DEFAULT", "ssh_key": ssh_key})


def detect_proxies():
    candidates = {}
    for value in getproxies().values():
        if isinstance(value, str):
            try:
                parsed = urlparse(value)
                if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
                    candidates[value] = "系统代理设置"
            except ValueError:
                pass
    for port in (7890, 7897, 8080, 10809):
        candidates.setdefault(f"http://127.0.0.1:{port}", "本机常见代理端口")
    found = []
    for url, source in candidates.items():
        try:
            parsed = urlparse(url)
            with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=.2):
                found.append(dict(url=url, source=source))
        except (OSError, ValueError):
            continue
    return dict(proxies=found, message="这些地址可连接，尚未确认代理或出口；选用后请检测实际出口。")
