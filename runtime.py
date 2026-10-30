"""Private runtime files live outside the source checkout."""
import json
import os
from pathlib import Path


def data_directory():
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "OracleA1Workbench"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "oracle-a1-workbench"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        temp.chmod(0o600)
    temp.replace(path)


def migrate_state(state):
    """A launch acknowledgement from older releases is not a running check."""
    for record in [state.get("current"), *state.get("history", [])]:
        if record and record.get("outcome") == "success":
            record.update(outcome="accepted", message="创建已接受；旧记录尚未核验运行状态")
    current = state.get("current") or {}
    if current.get("instance_id") and state.get("state") not in ("ready", "failed", "unverified"):
        state.update(state="unverified", message="已保留实例编号，请刷新实例状态", next_at=None)
    elif state.get("state") in ("preflight", "running", "waiting", "stopping", "verifying", "provisioning"):
        state.update(state="stopped", message="上次执行已中断；再次启动前会重新核查云端", next_at=None)
    return state
