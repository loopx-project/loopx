"""In-memory orchestration for lark-cli App registration sessions."""

from __future__ import annotations

import re
import subprocess
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .cli_resolution import (
    LarkCliResolution,
    LarkCliUnavailableError,
    resolve_lark_cli,
)
from .goal_channel_transport import SAFE_PROFILE_PATTERN


_OFFICIAL_SETUP_HOSTS = (
    "open.feishu.cn",
    "open.larksuite.com",
    "open." + "la" + "rk" + "office.com",
)
OFFICIAL_SETUP_URL_PATTERN = re.compile(
    r"https://(?:"
    + "|".join(re.escape(host) for host in _OFFICIAL_SETUP_HOSTS)
    + r")/page/(?:cli|launcher)\?[^\s]+"
)
TERMINAL_SETUP_STATES = {"ready", "failed", "cancelled"}


class SetupProcess(Protocol):
    stdout: Any
    returncode: int | None

    def wait(self) -> int: ...

    def terminate(self) -> None: ...


ProcessFactory = Callable[[list[str], dict[str, str]], SetupProcess]
ProfileVerifier = Callable[[str], bool]


def _default_process_factory(args: list[str], env: dict[str, str]) -> SetupProcess:
    return subprocess.Popen(
        args,
        bufsize=1,
        env=env,
        stderr=subprocess.STDOUT,
        stdout=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )


@dataclass
class _Setup:
    setup_id: str
    app_ref: str
    status: str = "starting"
    verification_url: str | None = None
    error: str | None = None
    process: SetupProcess | None = None

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "status": self.status,
            "app_ref": self.app_ref,
            "verification_url": self.verification_url,
            "error": self.error,
        }


class LarkAppSetupManager:
    def __init__(
        self,
        *,
        cli_resolution: LarkCliResolution | None = None,
        process_environ: Mapping[str, str] | None = None,
        process_factory: ProcessFactory = _default_process_factory,
        profile_verifier: ProfileVerifier,
    ) -> None:
        self._cli_resolution = cli_resolution or resolve_lark_cli()
        self._process_environ = process_environ
        self._process_factory = process_factory
        self._profile_verifier = profile_verifier
        self._lock = threading.Lock()
        self._setups: dict[str, _Setup] = {}

    def start(self, *, app_ref: str, brand: str) -> dict[str, Any]:
        profile = str(app_ref or "").strip()
        if not SAFE_PROFILE_PATTERN.fullmatch(profile):
            raise ValueError("Lark App reference must be a safe lark-cli profile name")
        if brand not in {"feishu", "lark"}:
            raise ValueError("Lark App brand must be feishu or lark")
        if not self._cli_resolution.available or not self._cli_resolution.command:
            raise LarkCliUnavailableError(
                self._cli_resolution.error_code or "lark_cli_not_installed"
            )
        setup = _Setup(setup_id=f"lark_setup_{uuid.uuid4().hex}", app_ref=profile)
        args = [
            self._cli_resolution.command,
            "config",
            "init",
            "--new",
            "--name",
            profile,
            "--brand",
            brand,
            "--lang",
            "zh_cn",
        ]
        try:
            setup.process = self._process_factory(
                args,
                self._cli_resolution.subprocess_env(self._process_environ),
            )
        except OSError as exc:
            raise LarkCliUnavailableError("lark_cli_start_failed") from exc
        with self._lock:
            self._setups[setup.setup_id] = setup
        threading.Thread(target=self._watch, args=(setup.setup_id,), daemon=True).start()
        return setup.public_snapshot()

    def snapshot(self, setup_id: str) -> dict[str, Any]:
        with self._lock:
            setup = self._setups.get(setup_id)
            if setup is None:
                raise KeyError(setup_id)
            return setup.public_snapshot()

    def cancel(self, setup_id: str) -> dict[str, Any]:
        with self._lock:
            setup = self._setups.get(setup_id)
            if setup is None:
                raise KeyError(setup_id)
            if setup.status in TERMINAL_SETUP_STATES:
                return setup.public_snapshot()
            setup.status = "cancelled"
            process = setup.process
        if process is not None:
            process.terminate()
        return self.snapshot(setup_id)

    def close(self) -> None:
        with self._lock:
            active_ids = [
                setup_id
                for setup_id, setup in self._setups.items()
                if setup.status not in TERMINAL_SETUP_STATES
            ]
        for setup_id in active_ids:
            self.cancel(setup_id)

    def _watch(self, setup_id: str) -> None:
        with self._lock:
            setup = self._setups[setup_id]
            process = setup.process
        if process is None:
            return
        for line in process.stdout or ():
            match = OFFICIAL_SETUP_URL_PATTERN.search(str(line))
            if match:
                with self._lock:
                    if setup.status not in TERMINAL_SETUP_STATES:
                        setup.verification_url = match.group(0)
                        setup.status = "waiting_for_feishu"
        returncode = process.wait()
        with self._lock:
            if setup.status == "cancelled":
                return
        verified = returncode == 0 and self._profile_verifier(setup.app_ref)
        with self._lock:
            if verified:
                setup.status = "ready"
                setup.error = None
            else:
                setup.status = "failed"
                setup.error = (
                    "Lark App registration completed but the profile could not be verified"
                    if returncode == 0
                    else "Lark App registration did not complete"
                )
