"""Linux process identity for the managed Codex operation host."""
from __future__ import annotations

import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexWorkerIdentity:
    uid: int
    gid: int
    home: Path
    setpriv: str

    @property
    def codex_home(self) -> Path:
        return self.home / ".codex"

    @property
    def process_options(self) -> dict:
        return {"user": self.uid, "group": self.gid, "extra_groups": [], "umask": 0o077}

    @property
    def environment(self) -> dict[str, str]:
        return {"HOME": str(self.home), "USERPROFILE": str(self.home),
                "CODEX_HOME": str(self.codex_home), "PATH": os.defpath}


def worker_isolation_requested() -> bool:
    return bool(os.environ.get("LOOPX_CODEX_OPERATION_WORKER_USER") or
                os.environ.get("LOOPX_CODEX_OPERATION_WORKER_HOME"))


def _require_private_directory(path: Path, uid: int) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("isolated Codex paths must be absolute physical directories")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise ValueError("isolated Codex directory must be private and owned by its account")


def load_worker_identity(runtime_root: Path, registry_path: Path) -> CodexWorkerIdentity | None:
    if not worker_isolation_requested():
        return None
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError("isolated managed Codex launch requires a Linux Core launcher with setuid authority")
    import pwd

    name = os.environ.get("LOOPX_CODEX_OPERATION_WORKER_USER", "")
    home_value = os.environ.get("LOOPX_CODEX_OPERATION_WORKER_HOME", "")
    if not name or not home_value:
        raise ValueError("isolated managed Codex requires both a Worker user and a private Worker home")
    try:
        worker = pwd.getpwnam(name)
    except KeyError as exc:
        raise ValueError("isolated managed Codex Worker user does not exist") from exc
    if worker.pw_uid == 0 or worker.pw_gid == 0:
        raise ValueError("isolated managed Codex requires an unprivileged Worker account")
    home = Path(home_value)
    _require_private_directory(home, worker.pw_uid)
    _require_private_directory(home / ".codex", worker.pw_uid)
    registry = registry_path.lstat()
    if not stat.S_ISREG(registry.st_mode) or registry.st_uid != 0 or registry.st_mode & 0o022:
        raise ValueError("isolated managed Codex requires a Core-owned physical registry file")
    for core in (runtime_root, registry_path.parent):
        _require_private_directory(core, os.geteuid())
        if home.is_relative_to(core) or core.is_relative_to(home):
            raise ValueError("Worker home must be separate from LoopX authority directories")
        for parent in core.parents:
            entry = parent.stat()
            sticky_system_directory = entry.st_uid == 0 and entry.st_mode & stat.S_ISVTX
            if entry.st_uid == worker.pw_uid or (entry.st_mode & 0o022 and not sticky_system_directory):
                raise ValueError("LoopX authority has a writable ancestor outside the Core account")
    setpriv = shutil.which("setpriv", path=os.defpath)
    if not setpriv:
        raise ValueError("isolated managed Codex requires the system setpriv command")
    return CodexWorkerIdentity(worker.pw_uid, worker.pw_gid, home, setpriv)
