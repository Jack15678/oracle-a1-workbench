"""Native one-folder build, with GUI and console entry points sharing assets."""
from pathlib import Path
import sys
import os
import pkgutil
import oci
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / "web"), "web"), (str(root / "build" / "licenses"), "licenses")]
datas += collect_data_files("oci") + copy_metadata("oci")
# OCI ships hundreds of unrelated cloud services. Its lazy root importer has a
# fallback import-all branch that PyInstaller would otherwise eagerly bundle.
# SDK pagination imports dns/object_storage even for identity/core list calls.
services = {"core", "identity", "auth", "pagination", "retry", "_vendor", "circuit_breaker", "dns", "object_storage", "work_requests"}
excluded_services = ["oci." + item.name for item in pkgutil.iter_modules(oci.__path__)
                     if item.ispkg and item.name not in services]
hidden = collect_submodules("oci.core") + collect_submodules("oci.identity") + ["oci.util"]
a = Analysis([str(root / "launcher.py")], pathex=[str(root)], binaries=[], datas=datas,
             hiddenimports=hidden, hookspath=[], runtime_hooks=[],
             excludes=["tkinter", "pytest", "IPython", *excluded_services])
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="A1Workbench", console=False,
          debug=False, strip=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="a1", console=True,
          debug=False, strip=False, upx=False)
bundle = COLLECT(gui, cli, a.binaries, a.datas, name="A1Workbench", strip=False, upx=False)
if sys.platform == "darwin":
    app = BUNDLE(bundle, name="A1Workbench.app", bundle_identifier="io.github.Jack15678.oracle-a1-workbench",
                 info_plist={"CFBundleShortVersionString": os.environ.get("A1_BUILD_VERSION", "0.2.0"),
                             "NSHighResolutionCapable": True})
