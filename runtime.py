"""Private runtime files live outside the source checkout."""
import json
import os
from pathlib import Path
import sys
from contextlib import contextmanager


def data_directory():
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "OracleA1Workbench"
    xdg = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "oracle-a1-workbench"
    if sys.platform == "darwin" and not xdg.exists():
        return Path.home() / "Library/Application Support/OracleA1Workbench"
    return xdg


def engine_command(*args):
    """Use the console helper even when called from a frozen GUI executable."""
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).with_name("a1.exe" if os.name == "nt" else "a1")
        return [str(executable), *args]
    return [sys.executable, "-u", str(Path(__file__).resolve().with_name("launcher.py")), *args]


@contextmanager
def process_lock(path=None):
    # Preserve the old POSIX lock location so old/new executors cannot overlap.
    path = path or (data_directory() if os.name == "nt" else Path.home() / ".cache") / "oracle-a1-grab.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if os.name == "nt":
            import msvcrt
            if file.seek(0, 2) == 0:
                file.write(b"\0")
                file.flush()
            file.seek(0)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise BlockingIOError("另一个 A1 执行器正在运行") from error
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


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
