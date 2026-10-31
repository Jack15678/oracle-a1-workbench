"""Build on the target OS, collect dependency notices, and make a release archive."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def licenses():
    target = ROOT / "build" / "licenses"
    target.mkdir(parents=True, exist_ok=True)
    packages = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name, version = dist.metadata["Name"], dist.version
        packages.append({"name": name, "version": version,
                         "license": dist.metadata.get("License-Expression") or dist.metadata.get("License", ""),
                         "homepage": dist.metadata.get("Home-page", "")})
        for item in dist.files or []:
            if any(word in item.name.lower() for word in ("license", "copying", "copyright", "notice")):
                source = Path(dist.locate_file(item))
                if source.is_file() and source.suffix not in (".pyc", ".py", ".exe", ".dll"):
                    destination = target / name / str(item).replace("..", "parent")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    (target / "DEPENDENCIES.json").write_text(json.dumps(packages, indent=2), encoding="utf-8")
    py_version = platform.python_version()
    candidates = [Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"]
    python_license = next((path.read_bytes() for path in candidates if path.is_file()), None)
    if python_license is None:
        with urlopen(f"https://raw.githubusercontent.com/python/cpython/v{py_version}/LICENSE", timeout=30) as response:
            python_license = response.read()
    (target / f"Python-{py_version}-LICENSE.txt").write_bytes(python_license)
    # Wheels contain crypto's own licenses; the linked OpenSSL and CPython
    # library notices need their upstream notices as well.
    with urlopen(f"https://raw.githubusercontent.com/python/cpython/v{py_version}/Doc/license.rst", timeout=30) as response:
        (target / "Python-third-party-notices.rst").write_bytes(response.read())
    from cryptography.hazmat.backends.openssl.backend import backend
    for description in {ssl.OPENSSL_VERSION, backend.openssl_version_text()}:
        match = re.match(r"OpenSSL ([0-9.]+)", description)
        if not match:
            raise RuntimeError(f"Add license collection for this TLS library: {description}")
        version = match.group(1)
        with urlopen(f"https://raw.githubusercontent.com/openssl/openssl/openssl-{version}/LICENSE.txt", timeout=30) as response:
            (target / f"OpenSSL-{version}-LICENSE.txt").write_bytes(response.read())
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", target / "THIRD_PARTY_NOTICES.md")
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.2.0")
    parser.add_argument("--platform", required=True, choices=("windows-x64", "macos-arm64", "macos-x64", "linux-x64", "linux-arm64"))
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", args.version):
        parser.error("Version must be MAJOR.MINOR.PATCH with an optional prerelease suffix")
    system = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform)
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    if args.platform != f"{system}-{arch}":
        parser.error(f"Build on the matching native platform; this interpreter is {system}-{arch}")
    notices = licenses()
    env = dict(os.environ, A1_BUILD_VERSION=args.version.partition("-")[0])
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/workbench.spec"], cwd=ROOT, env=env, check=True)
    folder = ROOT / "dist" / "A1Workbench"
    if sys.platform == "darwin":
        folder = ROOT / "dist" / "A1Workbench.app"
    # Test the actual frozen binaries with a clean home before preparing assets.
    subprocess.run([sys.executable, "packaging/smoke.py", "--bundle", str(folder)], cwd=ROOT, check=True)
    release = ROOT / "dist" / "release"
    release.mkdir(parents=True, exist_ok=True)
    package = release / f"oracle-a1-workbench-{args.version}-{args.platform}"
    if package.resolve().parent != release.resolve():
        raise ValueError("Package path must stay inside dist/release")
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True, exist_ok=False)
    shutil.copytree(folder, package / folder.name, symlinks=True)
    shutil.copytree(notices, package / "licenses")
    shutil.copytree(ROOT / "docs", package / "docs")
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "config.example.json", "AGENTS.md"):
        shutil.copy2(ROOT / name, package / name)
    if sys.platform == "darwin":
        archive = Path(str(package) + ".zip")
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(package), str(archive)], check=True)
    elif sys.platform == "win32":
        archive = Path(shutil.make_archive(str(package), "zip", root_dir=release, base_dir=package.name))
    else:
        archive = Path(str(package) + ".tar.gz")
        with tarfile.open(archive, "w:gz") as output:
            output.add(package, arcname=package.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(str(archive) + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
    print(archive)


if __name__ == "__main__":
    main()
