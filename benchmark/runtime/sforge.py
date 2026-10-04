"""SForge transport for the shared workers. SForge owns tasks and evaluation."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from sforge.harness.agent.codex import CodexAgent

from .codex import Execution, prepare_codex_home
from .codex_offline import CodexOffline
from .harbor import (
    BenchmarkCodex, _GOAL_ID, _PYTHON, _SCHEDULER_STATE, _SRC,
)
from .scheduler import worker_command


# Experiment profiles, local to the research runner; not new LoopX modes.
PROFILES = ("official", "single", "native-goal", "heartbeat-resume", "heartbeat-explore")
DEFAULT_TIMEOUT_SECONDS = 18 * 60 * 60


class SForgeEnvironment:
    """Adapt the existing install/bootstrap calls to a native SForge handle."""

    default_user = "agent"

    def __init__(self, backend, handle):
        self.backend, self.handle = backend, handle

    async def exec(self, command, *, user=None, env=None, cwd=None, timeout_sec=None):
        result = self.backend.exec_run_with_exit_code(
            self.handle, ["/bin/bash", "-c", command], timeout=timeout_sec or 300,
            user=user or self.default_user, workdir=cwd, environment=env,
        )
        if result.timed_out:
            raise TimeoutError("SForge transport command exceeded its deadline")
        return SimpleNamespace(stdout=result.output, stderr="", return_code=result.exit_code)

    async def upload_file(self, source, target):
        self.backend.copy_to_container(self.handle, Path(source), PurePosixPath(target))

    async def upload_dir(self, source, target):
        remote = f"/tmp/benchmark-upload-{uuid.uuid4().hex}.tar"
        with tempfile.TemporaryDirectory(prefix="benchmark-upload-") as directory:
            archive = Path(directory) / "payload.tar"
            with tarfile.open(archive, "w") as bundle:
                bundle.add(source, arcname=".")
            await self.upload_file(archive, remote)
            result = await self.exec(
                f"mkdir -p {shlex.quote(target)} && tar -xf {remote} -C "
                f"{shlex.quote(target)} && rm {remote}", user="root",
            )
            if result.return_code:
                raise RuntimeError("SForge directory upload failed")


class SForgeWorker(CodexAgent):
    """Native SForge lifecycle with an explicitly selected worker profile.

    The native hook callback is the installation seam. Only the official
    profile installs the native Stop hook; the other workers own completion.
    """

    name = "loopx-benchmark-worker"
    install_cmds = []  # Offline payload is installed through the native hook seam.
    default_api_base_url = "https://chatgpt.com"

    def __init__(self, config, *, profile: str, cwd: str,
                 timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                 turn_timeout_seconds: int = 4700):
        super().__init__(config)
        if profile not in PROFILES:
            raise ValueError("Unknown benchmark worker profile")
        if timeout_seconds <= 160:
            raise ValueError("Worker budget must exceed the 160s startup/settlement reserve")
        if not config.agent_model or not config.agent_effort:
            raise ValueError("Explicit model and reasoning effort are required")
        if not os.environ.get("CODEX_AUTH_JSON_PATH"):
            raise ValueError("Set CODEX_AUTH_JSON_PATH to the trial credential source")
        self.profile, self.cwd = profile, cwd
        self.timeout_seconds = timeout_seconds
        self.turn_timeout = min(turn_timeout_seconds, timeout_seconds - 160)
        self.runtime = None
        self.prepared = False
        # A single Codex call and a native Goal must not acquire an outer loop.
        self.resume_cmd = (CodexAgent.resume_cmd if profile == "official" else
                           "shared-scheduler" if profile.startswith("heartbeat-") else None)

    def install_stop_hook(self, backend, handle, log_dir, logger):
        self.environment = SForgeEnvironment(backend, handle)
        self.log_dir = log_dir
        effort = {"max": "xhigh"}.get(self._config.agent_effort, self._config.agent_effort)
        common = dict(logs_dir=log_dir / "worker", model_name=self._config.agent_model,
                      reasoning_effort=effort)
        if self.profile in {"official", "single"}:
            asyncio.run(CodexOffline(**common).install(self.environment))
            result = backend.exec_run(handle, "mkdir -p /home/agent/.codex", user="agent")
            if result.exit_code:
                raise RuntimeError("Could not create isolated Codex home")
            with tempfile.TemporaryDirectory(prefix="benchmark-auth-") as directory:
                home = Path(directory)
                shutil.copyfile(os.environ["CODEX_AUTH_JSON_PATH"], home / "auth.json")
                (home / "auth.json").chmod(0o600)
                prepare_codex_home(
                    home, execution=Execution(mode="plain"), workspace=Path(self.cwd),
                    model=self._config.agent_model, effort=effort,
                    base_url="", api_key="", wire_api="responses", skills=None,
                )
                for name in ("auth.json", "config.toml"):
                    backend.copy_to_container(handle, home / name,
                                              PurePosixPath(f"/home/agent/.codex/{name}"))
            result = backend.exec_run(handle, ["/bin/bash", "-c",
                "chown -R agent:agent /home/agent/.codex && chmod 0600 /home/agent/.codex/auth.json"],
                user="root")
            if result.exit_code:
                raise RuntimeError("Could not protect trial credentials")
            if self.profile == "official":
                super().install_stop_hook(backend, handle, log_dir, logger)
        else:
            mode = "native-goal" if self.profile == "native-goal" else "heartbeat"
            self.runtime = BenchmarkCodex(
                **common, execution_mode=mode,
                iteration_context="resume" if mode == "heartbeat" else "fresh",
                turn_timeout_sec=self.turn_timeout,
                scheduler_timeout_sec=self.timeout_seconds,
            )
            asyncio.run(self.runtime.install(self.environment))
        (log_dir / "worker-profile.json").write_text(json.dumps({
            "profile": self.profile, "model": self._config.agent_model,
            "reasoning_effort": effort, "timeout_seconds": self.timeout_seconds,
            "stop_hook": self.profile == "official",
            "outer_resume": self.resume_cmd is not None,
            "explore_graph": self.profile == "heartbeat-explore",
            "explore_harness": self.profile == "heartbeat-explore",
        }, indent=2))

    def format_run_cmd(self, prompt_path, *, model=None, cwd="", internet=True, resume=False):
        if self.profile in {"official", "single"}:
            return super().format_run_cmd(prompt_path, model=model, cwd=cwd,
                                          internet=internet, resume=resume)
        if self.runtime is None:
            raise RuntimeError("Run the native SForge installation hook before execution")
        if not self.prepared:
            result = asyncio.run(self.environment.exec(f"cat {shlex.quote(prompt_path)}"))
            if result.return_code:
                raise RuntimeError("Native task prompt is unavailable")
            self.runtime._phase_number = 1
            if self.runtime.execution.uses_loopx:
                asyncio.run(self.runtime._prepare_phase(self.environment, result.stdout, cwd=self.cwd))
                if self.profile == "heartbeat-explore":
                    observed = asyncio.run(self.runtime._loopx(self.environment, [
                        "configure-goal", "--goal-id", _GOAL_ID,
                        "--explore-graph-enabled", "--explore-harness-enabled",
                        "--explore-harness-profile", "adaptive-resilient", "--execute",
                    ], cwd=self.cwd))
                    (self.log_dir / "explore-activation.json").write_text(json.dumps(observed))
            else:
                asyncio.run(self.runtime._write_task_document(self.environment, result.stdout))
            self.prepared = True
        env = self.runtime._worker_env(cwd=self.cwd)
        command = worker_command(env, python=f"{_PYTHON}/bin/python3", source=_SRC,
                                 state_file=_SCHEDULER_STATE, host_timeout=self.turn_timeout)
        # Persist the phase deadline in the task environment. An abnormal outer
        # resume preserves the remaining budget instead of granting another 18h.
        deadline = "/opt/loopx-benchmark/control/phase-deadline"
        exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
        return (
            f"set -eu; test -f {deadline} || echo $(( $(date +%s) + {self.timeout_seconds} )) > {deadline}; "
            f"export LOOPX_PHASE_DEADLINE_EPOCH=$(cat {deadline}); "
            f"remaining=$(( LOOPX_PHASE_DEADLINE_EPOCH - $(date +%s) )); "
            'test "$remaining" -gt 0 || exit 0; '
            f"exec timeout --signal=TERM --kill-after=30 ${{remaining}}s env {exports} {shlex.join(command)}"
        )
