"""SForge transport for the shared workers. SForge owns tasks and evaluation."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from sforge.harness.agent.codex import CodexAgent
from loopx.control_plane.goals.goal_vision_policy import DEFAULT_PLANNING_HORIZON_GOAL

from benchmark.edgebench.feedback import FEEDBACK_MODES
from benchmark.edgebench.feedback_hook import FEEDBACK_PAYLOAD

from .codex import DEFAULT_REPLAN_AFTER_TURNS, Execution, prepare_codex_home
from .codex_offline import CodexOffline, MODEL_CATALOG_REMOTE, stage_portable_python
from .harbor import (
    BenchmarkCodex, _CONTROL, _CODEX_HOME, _PYTHON, _SCHEDULER_STATE, _SRC,
)
from .scheduler import worker_command
from .worker import PHASE_WORK_RESERVE_SECONDS


# Experiment profiles, local to the research runner; not new LoopX modes.
PROFILES = ("official", "single", "native-goal", "heartbeat-resume", "heartbeat-explore")
DEFAULT_TIMEOUT_SECONDS = 18 * 60 * 60
_RUNTIME_CREDENTIAL_PLACEHOLDER = "runtime-managed-credential"


class SForgeEnvironment:
    """Adapt the existing install/bootstrap calls to a native SForge handle."""

    default_user = "agent"

    def __init__(self, backend, handle, *, setup_proxy=None):
        self.backend, self.handle = backend, handle
        self.setup_env = ({key: setup_proxy for key in
                           ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
                          if setup_proxy else {})

    async def exec(self, command, *, user=None, env=None, cwd=None, timeout_sec=None):
        result = self.backend.exec_run_with_exit_code(
            self.handle, ["/bin/bash", "-c", command], timeout=timeout_sec or 300,
            user=user or self.default_user, workdir=cwd,
            environment={**self.setup_env, **(env or {})},
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
                 feedback_prompt: str | None = None,
                 feedback: str = "native",
                 workspace_instructions: str | None = None,
                 task_objective: str | None = None,
                 task_entry: str | None = None,
                 turn_envelope: bool = False,
                 context_delivery: str = "off",
                 native_goal_continue: bool = False,
                 replan_after_turns: int | None = None,
                 replan_after_todos: int | None = None,
                 stride_strategy: str | None = None,
                 planning_horizon_goal: bool | None = None,
                 checkpoint_request_path: str | None = None,
                 checkpoint_request: bytes | None = None,
                 warm_start=None):
        super().__init__(config)
        if profile not in PROFILES:
            raise ValueError("Unknown benchmark worker profile")
        if not isinstance(turn_envelope, bool) or (
            turn_envelope and profile not in {"heartbeat-resume", "heartbeat-explore"}
        ):
            raise ValueError("turn_envelope requires a heartbeat worker")
        self.turn_envelope = turn_envelope
        self.context_delivery = Execution(
            mode="heartbeat" if profile.startswith("heartbeat-") else "plain",
            context_delivery=context_delivery,
        ).context_delivery
        if timeout_seconds <= PHASE_WORK_RESERVE_SECONDS:
            raise ValueError("Worker budget must exceed the 160s startup/settlement reserve")
        if not config.agent_model or not config.agent_effort:
            raise ValueError("Explicit model and reasoning effort are required")
        if config.agent_api_base_url and not config.agent_api_key:
            raise ValueError("Custom model provider requires an API credential")
        if not config.agent_api_base_url and not os.environ.get("CODEX_AUTH_JSON_PATH"):
            raise ValueError("Set CODEX_AUTH_JSON_PATH to the trial credential source")
        if task_entry == "loopx-planned" and not profile.startswith("heartbeat-"):
            raise ValueError("loopx-planned requires a heartbeat profile")
        self.task_entry = Execution(
            mode="heartbeat" if profile.startswith("heartbeat-") else "plain",
            task_entry=task_entry,
        ).task_entry
        if replan_after_turns is not None and replan_after_todos is not None:
            raise ValueError("Choose replan_after_turns or replan_after_todos, not both")
        if replan_after_todos is not None:
            if type(replan_after_todos) is not int or not 1 <= replan_after_todos <= 5:
                raise ValueError("replan_after_todos must be an integer between 1 and 5")
            if not profile.startswith("heartbeat-"):
                raise ValueError("replan_after_todos requires a heartbeat profile")
        if replan_after_turns is not None:
            if (type(replan_after_turns) is not int or
                    not 1 <= replan_after_turns <= 6):
                raise ValueError("replan_after_turns must be an integer between 1 and 6")
            if not profile.startswith("heartbeat-"):
                raise ValueError("replan_after_turns requires a heartbeat profile")
        if (replan_after_turns is None and replan_after_todos is None
                and profile.startswith("heartbeat-")):
            replan_after_turns = DEFAULT_REPLAN_AFTER_TURNS
        self.replan_after_turns = replan_after_turns
        self.replan_after_todos = replan_after_todos
        if stride_strategy is not None and not profile.startswith("heartbeat-"):
            raise ValueError("stride_strategy requires a heartbeat profile")
        if stride_strategy is not None:
            from loopx.control_plane.work_items.stride_policy_codec import normalize_stride_strategy
            stride_strategy = normalize_stride_strategy(stride_strategy)
        if planning_horizon_goal is not None:
            if not isinstance(planning_horizon_goal, bool):
                raise ValueError("planning_horizon_goal must be a boolean")
            if not profile.startswith("heartbeat-"):
                raise ValueError("planning_horizon_goal requires a heartbeat profile")
        self.planning_horizon_goal = (
            DEFAULT_PLANNING_HORIZON_GOAL if planning_horizon_goal is None and profile.startswith("heartbeat-")
            else planning_horizon_goal
        )
        self.stride_strategy = stride_strategy
        self.checkpoint_request_path = None
        self.checkpoint_request = checkpoint_request
        if checkpoint_request is not None:
            if checkpoint_request_path is not None or not profile.startswith("heartbeat-"):
                raise ValueError("checkpoint_request requires a heartbeat profile and no guest-path override")
            from .checkpoint import parse_checkpoint_request
            parse_checkpoint_request(checkpoint_request)
            checkpoint_request_path = f"{_CONTROL}/checkpoint-request.json"
        if checkpoint_request_path is not None:
            if not profile.startswith("heartbeat-"):
                raise ValueError("checkpoint_request_path requires a heartbeat profile")
            from .checkpoint import normalize_checkpoint_request_path
            self.checkpoint_request_path = normalize_checkpoint_request_path(checkpoint_request_path)
        self.warm_start = warm_start
        if warm_start is not None:
            if not profile.startswith("heartbeat-") or feedback != "native" or self.task_entry != "loopx-planned":
                raise ValueError("Warm checkpoint continuation requires a planned native heartbeat worker")
            warm_start.verify(cwd)
        self.profile, self.cwd = profile, cwd
        if feedback not in FEEDBACK_MODES:
            raise ValueError("Unknown feedback mode")
        if feedback != "native" and feedback_prompt is None:
            raise ValueError("Restricted feedback requires its task wrapper")
        if not isinstance(native_goal_continue, bool):
            raise ValueError("native_goal_continue must be a boolean opt-in")
        if native_goal_continue and profile != "native-goal":
            raise ValueError("native_goal_continue requires the native-goal profile")
        self.native_goal_continue = native_goal_continue
        if workspace_instructions is not None and (
            feedback != "native" or not workspace_instructions.strip()
            or feedback_prompt is None or not feedback_prompt.startswith(workspace_instructions)
        ):
            raise ValueError("Workspace feedback instructions must match the native task wrapper")
        if task_objective is not None and (
            not isinstance(task_objective, str) or not task_objective.strip()
            or workspace_instructions is None or feedback_prompt is None
            or task_objective not in feedback_prompt
        ):
            raise ValueError("Task objective must occur in the native task prompt")
        self.feedback = feedback
        self.feedback_prompt = feedback_prompt
        self.workspace_instructions = workspace_instructions
        self.task_objective = task_objective
        self.instructions_installed = False
        self.prompt_installed = False
        self.timeout_seconds = timeout_seconds
        # Let one call use the trial budget. The shared worker recomputes the
        # remaining deadline on every continuation, retaining cleanup time.
        self.turn_timeout = timeout_seconds - PHASE_WORK_RESERVE_SECONDS
        self.runtime = None
        self.prepared = False
        # Only the official profile delegates continuation to SForge. Heartbeat
        # workers already run a scheduler: restarting it after terminal/quiescent
        # exit duplicates that owner and can exhaust the native resume limit.
        self.resume_cmd = CodexAgent.resume_cmd if profile == "official" else None
        # SForge signs this declaration into the runtime protocol.  The setup
        # callback below is shared by every profile, but only the official
        # profile installs the Codex stop hook inside that callback.
        self.stop_hook = CodexAgent.stop_hook if profile == "official" else None

    def install_stop_hook(self, backend, handle, log_dir, logger):
        self.environment = SForgeEnvironment(
            backend, handle, setup_proxy=os.environ.get("LOOPX_INSTALL_HTTPS_PROXY"),
        )
        try:
            self._install_worker(backend, handle, log_dir, logger)
        finally:
            # Setup may build pinned frontend assets before native isolation.
            # Solver execution must never inherit this setup-only proxy.
            self.environment.setup_env = {}

    def _install_worker(self, backend, handle, log_dir, logger):
        self.backend, self.handle = backend, handle
        self.log_dir = log_dir
        effort = self._config.agent_effort
        common = dict(logs_dir=log_dir / "worker", model_name=self._config.agent_model,
                      reasoning_effort=effort)
        runtime_base_url = self._config.agent_api_base_url
        runtime_api_key = self._config.agent_api_key
        if self._config.agent_api_base_url:
            route = backend.exec_run(
                handle,
                ["/bin/bash", "-c", 'printf "%s" "${OPENAI_BASE_URL:-}"'],
                user="agent",
            )
            runtime_base_url = (getattr(route, "output", "") or "").strip()
            if route.exit_code or not runtime_base_url:
                raise RuntimeError("Could not read the effective model route from the trial")
            if runtime_base_url != self._config.agent_api_base_url:
                # SForge may replace a provider route with a host credential
                # gateway after constructing this Agent. Keep the provider
                # credential on the host and give child runtimes a non-secret
                # value; SForge supplies the same effective route to the outer
                # solver process.
                runtime_api_key = _RUNTIME_CREDENTIAL_PLACEHOLDER
            common["extra_env"] = {
                "OPENAI_BASE_URL": runtime_base_url,
                "OPENAI_API_KEY": runtime_api_key,
                "CODEX_AUTH_JSON_PATH": "",
            }
        if self.profile in {"official", "single"}:
            offline = CodexOffline(**common)
            asyncio.run(offline.install(self.environment))
            result = backend.exec_run(handle, "mkdir -p /home/agent/.codex", user="agent")
            if result.exit_code:
                raise RuntimeError("Could not create isolated Codex home")
            with tempfile.TemporaryDirectory(prefix="benchmark-auth-") as directory:
                home = Path(directory)
                if not self._config.agent_api_base_url:
                    shutil.copyfile(os.environ["CODEX_AUTH_JSON_PATH"], home / "auth.json")
                    (home / "auth.json").chmod(0o600)
                prepare_codex_home(
                    home, execution=Execution(mode="plain"), workspace=Path(self.cwd),
                    model=self._config.agent_model, effort=effort,
                    base_url=runtime_base_url or "",
                    api_key=runtime_api_key or "", wire_api="responses", skills=None,
                    model_catalog=offline._model_catalog_source,
                    model_catalog_destination=(Path(MODEL_CATALOG_REMOTE)
                                               if offline._model_catalog_staged else None),
                )
                for name in ("auth.json", "config.toml"):
                    if not (home / name).exists():
                        continue
                    backend.copy_to_container(handle, home / name,
                                              PurePosixPath(f"/home/agent/.codex/{name}"))
            result = backend.exec_run(handle, ["/bin/bash", "-c",
                "chown -R agent:agent /home/agent/.codex && "
                "{ test ! -f /home/agent/.codex/auth.json || chmod 0600 /home/agent/.codex/auth.json; }"],
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
                task_entry=self.task_entry,
                turn_envelope=self.turn_envelope,
                context_delivery=self.context_delivery,
                native_goal_continue=self.native_goal_continue,
                replan_after_turns=self.replan_after_turns,
                replan_after_todos=self.replan_after_todos,
                stride_strategy=self.stride_strategy,
                planning_horizon_goal=self.planning_horizon_goal,
                checkpoint_request_path=self.checkpoint_request_path,
                checkpoint_request=self.checkpoint_request,
                task_objective=self.task_objective,
                **({"goal_id": self.warm_start.goal_id, "agent_id": self.warm_start.agent_id,
                    "defer_codex_home": True} if self.warm_start is not None else {}),
            )
            asyncio.run(self.runtime.install(self.environment))
            if self.warm_start is not None:
                research_paths = asyncio.run(self.warm_start.stage(self.environment, self.cwd))
                self._warm_command("workspace", user="root")
                result = backend.exec_run(handle,
                    ["chown", "-R", "-P", "agent:agent", self.cwd, *research_paths], user="root")
                if result.exit_code:
                    raise RuntimeError("Restored research state is not writable by its receiver"
                        if research_paths else "Restored workspace is not writable by its receiver")
            if self.checkpoint_request is not None:
                with tempfile.TemporaryDirectory(prefix="benchmark-checkpoint-") as directory:
                    declaration = Path(directory) / "request.json"
                    declaration.write_bytes(self.checkpoint_request)
                    asyncio.run(self.environment.upload_file(declaration, self.checkpoint_request_path))
                result = backend.exec_run(handle, ["/bin/bash", "-c",
                    f"chown agent:agent {shlex.quote(self.checkpoint_request_path)} && "
                    f"chmod 0600 {shlex.quote(self.checkpoint_request_path)}"], user="root")
                if result.exit_code:
                    raise RuntimeError("Could not protect the explicit checkpoint declaration")
        # Prompt replacement describes the policy; the feedback mode owns access.
        if self.feedback != "native":
            result = backend.exec_run(handle, ["rm", "-f", "/usr/local/bin/sforge-submit"], user="root")
            if result.exit_code:
                raise RuntimeError("Could not remove unavailable submission entrypoint")
        if self.feedback == "best-only":
            from benchmark.edgebench import feedback_hook
            from benchmark.edgebench.feedback import prepare_feedback_root
            # Plain workers stage only Codex; their task image need not ship Python.
            # Heartbeat/native-goal workers already staged this same interpreter.
            if self.runtime is None:
                result = backend.exec_run(handle, ["mkdir", "-p", _PYTHON], user="root")
                if result.exit_code:
                    raise RuntimeError("Could not create best-only interpreter directory")
                asyncio.run(stage_portable_python(self.environment, _PYTHON))
                result = backend.exec_run(handle, ["chmod", "-R", "a+rX", _PYTHON], user="root")
                if result.exit_code:
                    raise RuntimeError("Best-only interpreter is unreadable by worker")
            hook_python = f"{_PYTHON}/bin/python3"
            hook = PurePosixPath("/opt/edgebench-feedback/hook.py")
            prepare_feedback_root(backend, handle)
            backend.copy_to_container(handle, Path(feedback_hook.__file__), hook)
            result = backend.exec_run(handle, [hook_python, str(hook), "--install"], user="root")
            if result.exit_code:
                raise RuntimeError("Could not install best-only Codex delivery hooks")
            result = backend.exec_run(handle, [hook_python, "-c",
                "from pathlib import Path; "
                f"Path({str(hook)!r}).read_bytes(); Path('/etc/codex/hooks.json').read_bytes()"], user="agent")
            if result.exit_code:
                raise RuntimeError("Best-only Codex delivery hooks are unreadable by worker")
        (log_dir / "worker-profile.json").write_text(json.dumps({
            "profile": self.profile, "model": self._config.agent_model,
            "task_entry": self.task_entry,
            "reasoning_effort": effort, "timeout_seconds": self.timeout_seconds,
            "loopx_usage_ping_enabled": False,
            **({"checkpoint_capture_enabled": True}
               if self.checkpoint_request_path is not None else {}),
            **({"planning_timeout_seconds": None}
               if self.task_entry == "loopx-planned" else {}),
            "stop_hook": self.profile == "official",
            **(self.runtime._turn_envelope_receipt() if self.runtime is not None else {}),
            **({"native_goal_continue": True} if self.native_goal_continue else {}),
            "outer_resume": self.resume_cmd is not None,
            "explore_graph": self.profile == "heartbeat-explore",
            "explore_harness": self.profile == "heartbeat-explore",
            "feedback": self.feedback,
            **({"feedback_delivery": "codex_hooks", "feedback_payload": FEEDBACK_PAYLOAD}
               if self.feedback == "best-only" else {}),
            **(self.runtime._replan_receipt() if self.runtime and
               self.profile.startswith("heartbeat-") else {}),
        }, indent=2))

    def format_run_cmd(self, prompt_path, *, model=None, cwd="", internet=True, resume=False):
        if self.workspace_instructions is not None and not self.instructions_installed and self.warm_start is None:
            self._install_workspace_instructions()
        if self.feedback_prompt is not None and not self.prompt_installed:
            with tempfile.TemporaryDirectory(prefix="benchmark-prompt-") as directory:
                prompt = Path(directory) / "task.md"
                prompt.write_text(self.feedback_prompt)
                # The native copy preserves the host mode and lands root-owned.
                # The container's agent user must read the prompt whatever
                # umask the launcher runs under.
                prompt.chmod(0o644)
                asyncio.run(self.environment.upload_file(prompt, prompt_path))
            # Native run_agent saved the pre-override wrapper. Keep its visible
            # prompt artifact aligned with what all five workers actually read.
            (self.log_dir / "agent_prompt.md").write_text(self.feedback_prompt)
            self.prompt_installed = True
        if self.profile in {"official", "single"}:
            command = super().format_run_cmd(prompt_path, model=model, cwd=cwd,
                                            internet=internet, resume=resume)
            # Native SForge predates clients/providers with a distinct max
            # effort. Undo its legacy alias instead of silently downgrading
            # the explicit trial input; all other native command behavior stays.
            if self._config.agent_effort == "max":
                command = command.replace('model_reasoning_effort="xhigh"',
                                          'model_reasoning_effort="max"', 1)
            return self._execution_command(command)
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
                        "configure-goal", "--goal-id", self.runtime.goal_id,
                        "--explore-graph-enabled", "--explore-harness-enabled",
                        "--explore-harness-profile", "adaptive-resilient", "--execute",
                    ], cwd=self.cwd))
                    (self.log_dir / "explore-activation.json").write_text(json.dumps(observed))
            else:
                asyncio.run(self.runtime._write_task_document(self.environment, result.stdout))
            if self.warm_start is not None:
                self._warm_command("context")
            if self.warm_start is not None and not self._config.agent_api_base_url:
                auth = Path(os.environ["CODEX_AUTH_JSON_PATH"]).resolve()
                asyncio.run(self.environment.upload_file(auth, f"{_CODEX_HOME}/auth.json"))
                protected = self.backend.exec_run(self.handle, ["/bin/bash", "-c",
                    f"chown agent:agent {_CODEX_HOME}/auth.json && chmod 0600 {_CODEX_HOME}/auth.json"], user="root")
                if protected.exit_code:
                    raise RuntimeError("Could not protect fresh receiver credentials")
            self.prepared = True
        env = self.runtime._worker_env(cwd=self.cwd)
        if self.warm_start is not None:
            env["LOOPX_WARM_START"] = f"{_CONTROL}/warm-start.json"
        command = worker_command(env, python=f"{_PYTHON}/bin/python3", source=_SRC,
                                 state_file=_SCHEDULER_STATE, host_timeout=self.turn_timeout)
        if self.task_entry == "loopx-planned" and self.warm_start is None:
            env["LOOPX_PLANNING_RESULT"] = "/opt/loopx-benchmark/control/planning-phase-001.json"
            command = [f"{_PYTHON}/bin/python3", "-m", "benchmark.runtime.sforge_entry", *command]
        # Persist the phase deadline for repeated command preparation. Reusing
        # an entry command must never grant another full trial budget.
        deadline = "/opt/loopx-benchmark/control/phase-deadline"
        started = "/opt/loopx-benchmark/control/execution-started"
        if self.warm_start is not None:
            if self.warm_start.deadline_epoch <= time.time():
                raise ValueError("Original checkpoint allowance expired during receiver setup")
            clock = (f"test ! -e {deadline}; printf '%s\\n' {self.warm_start.deadline_epoch} > {deadline}; ")
        else:
            clock = f"test -f {deadline} || echo $(( $(date +%s) + {self.timeout_seconds} )) > {deadline}; "
        if self._config.agent_api_base_url:
            # SForge injects the credential through the container execution
            # environment. Do not duplicate it in the logged shell command.
            env.pop("OPENAI_API_KEY", None)
        exports = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
        return self._execution_command(
            f"set -eu; {clock}"
            f"export LOOPX_PHASE_DEADLINE_EPOCH=$(cat {deadline}); "
            f"remaining=$(( LOOPX_PHASE_DEADLINE_EPOCH - $(date +%s) )); "
            f'if test "$remaining" -le 0; then test -f {started}; exit $?; fi; '
            + (f"touch {started}; " if self.task_entry != "loopx-planned" or self.warm_start is not None else "")
            + f"set +e; timeout --signal=TERM --kill-after=30 ${{remaining}}s env {exports} {shlex.join(command)}; "
            f"phase_rc=$?; if test $phase_rc -eq 124 && test -f {started} "
            '&& test "$(date +%s)" -ge "$LOOPX_PHASE_DEADLINE_EPOCH"; then exit 0; fi; exit $phase_rc',
            execution_start_marker=started,
        )

    def _warm_command(self, action, *, user="agent"):
        env = self.runtime._worker_env(cwd=self.cwd)
        env["CODEX_HOME"] = _CODEX_HOME
        result = asyncio.run(self.environment.exec(
            f"{_PYTHON}/bin/python3 -m benchmark.runtime.sforge_warm_start {action} "
            f"--declaration {_CONTROL}/warm-start.json"
            + (f" > {_CONTROL}/checkpoint-context.json" if action == "context" else ""),
            env=env, user=user))
        if result.return_code:
            raise RuntimeError("Checkpoint receiver " + action + " failed: " + result.stdout[-2000:])
        if action == "context":
            # The transport combines stderr with stdout; native CLI diagnostics
            # must not corrupt the machine-readable receipt.
            receipt = asyncio.run(self.environment.exec(
                f"cat {_CONTROL}/checkpoint-context.json", env=env, user=user))
            if receipt.return_code:
                raise RuntimeError("Checkpoint context receipt is unavailable")
            value = json.loads(receipt.stdout)
            (self.log_dir / "checkpoint-context.json").write_text(json.dumps(value))

    def _install_workspace_instructions(self):
        """Preserve task instructions and verify the policy before model entry."""
        path = shlex.quote(str(PurePosixPath(self.cwd) / "AGENTS.md"))
        marker = "<!-- EdgeBench native feedback policy -->"
        block = f"\n\n{marker}\n\n{self.workspace_instructions}"
        check = f"test ! -L {path} && {{ test ! -e {path} || test -f {path}; }}"
        read = asyncio.run(self.environment.exec(f"{check} && {{ test ! -f {path} || cat {path}; }}"))
        if read.return_code:
            raise RuntimeError("Workspace AGENTS.md must be a readable regular file or absent")
        if marker in read.stdout and not read.stdout.endswith(block):
            raise RuntimeError("Workspace AGENTS.md has conflicting or partial feedback instructions")
        expected = read.stdout if read.stdout.endswith(block) else read.stdout + block
        if expected != read.stdout:
            written = asyncio.run(self.environment.exec(
                f"{check} && printf '%s' {shlex.quote(block)} >> {path}"))
            if written.return_code:
                raise RuntimeError("Could not append workspace feedback instructions")
        observed = asyncio.run(self.environment.exec(f"cat {path}"))
        if observed.return_code or observed.stdout != expected:
            raise RuntimeError("Workspace feedback instruction readback failed")
        (self.log_dir / "workspace-AGENTS.md").write_text(observed.stdout)
        self.instructions_installed = True

    def _execution_command(self, command, *, execution_start_marker=None):
        if getattr(self, "backend", None) is not None:
            self.backend.execution_command = command
            self.backend.execution_start_marker = execution_start_marker
        if self.feedback == "best-only":
            self.backend.start_feedback(self.handle)
        return command
