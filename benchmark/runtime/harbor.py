"""Shared Harbor adapter. Native tasks, phases, feedback and scoring stay in Harbor."""

from __future__ import annotations

import json
import os
import shlex
import tempfile
import time
from pathlib import Path
from typing import Iterable

from harbor.agents.installed.base import with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import FinalMetrics, Trajectory
from harbor.utils.trajectory_utils import format_trajectory_json

from .codex_offline import CodexOffline
from .codex import Execution


_ROOT = "/opt/loopx-benchmark"
_SRC = f"{_ROOT}/source"
_PYTHON = f"{_ROOT}/python"
_NODE = f"{_ROOT}/node"
_PROFILE = f"{_ROOT}/profile"
_PROFILE_HOME = f"{_PROFILE}/home"
_SHARED_CODEX_HOME = f"{_PROFILE}/codex-home"
_SHARED_SKILLS = f"{_SHARED_CODEX_HOME}/skills"
_CLI = f"{_PROFILE}/bin/loopx"
_CONTROL = f"{_ROOT}/control"
_REGISTRY = f"{_CONTROL}/registry.json"
_LOOPX_RUNTIME = f"{_ROOT}/state/runtime"
_SCHEDULER_STATE = f"{_CONTROL}/scheduler-state.json"
_TASK_DOC = f"{_CONTROL}/task.md"
_BASH_ENV = f"{_CONTROL}/bash-env"
_CODEX_HOME = f"{_ROOT}/codex-home"
_WORKER_MODULE = "benchmark.runtime.worker"
_WAKE_LOG_DIR = "/logs/agent/wakes"
_GOAL_ID = "benchmark-goal"
_AGENT_ID = "benchmark-agent"


class BenchmarkCodex(CodexOffline):
    """One independent LoopX control plane per Harbor trial."""

    def __init__(
        self,
        *args,
        execution_mode="heartbeat",
        iteration_context="fresh",
        codex_sandbox="danger-full-access",
        validation_command=None,
        turn_timeout_sec=None,
        scheduler_timeout_sec=5080,
        replan_after_todos=3,
        task_entry="seeded-todo",
        planning_timeout_sec=300,
        **kwargs,
    ):
        if isinstance(validation_command, str):
            raise ValueError("validation_command must be an argv list, not shell text")
        self.execution = Execution(
            execution_mode,
            iteration_context,
            codex_sandbox,
            float(scheduler_timeout_sec) - 160 if turn_timeout_sec is None else float(turn_timeout_sec),
            validation_command if validation_command is not None else (),
            task_entry,
        )
        self.planning_timeout = float(planning_timeout_sec)
        if not 0 < self.planning_timeout < float("inf"):
            raise ValueError("planning timeout must be finite and positive")
        self.scheduler_timeout = int(scheduler_timeout_sec)
        if self.scheduler_timeout <= self.execution.timeout_seconds + 150:
            raise ValueError(
                "scheduler timeout must exceed turn timeout plus cleanup allowance"
            )
        self.replan_after_todos = int(replan_after_todos)
        if not 1 <= self.replan_after_todos <= 5:
            raise ValueError("replan_after_todos must be between 1 and 5")
        self._phase_number = 0
        self._seeded_todo_id: str | None = None
        super().__init__(*args, **kwargs)

    @staticmethod
    def name() -> str:
        return "benchmark-codex"

    async def _stage_source(self, environment: BaseEnvironment, source: Path) -> str:
        from .source import archive_source, source_pins

        runner = Path(os.environ.get("LOOPX_RUNNER_SRC_DIR", str(source))).resolve()
        if Path(__file__).resolve() != runner / "benchmark/runtime/harbor.py":
            raise RuntimeError("Import the adapter from the pinned runner checkout")
        head, runner_head = source_pins(
            source, runner, os.environ.get("LOOPX_EXPECTED_COMMIT"),
            os.environ.get("LOOPX_EXPECTED_RUNNER_COMMIT"),
        )
        self._runner_commit = runner_head
        with tempfile.TemporaryDirectory(prefix="benchmark-source-") as directory:
            archive = Path(directory) / "source.tar"
            paths = () if self.execution.uses_loopx else (
                "benchmark/runtime", "loopx/capabilities/benchmark_toolkit/native_codex_goal.py",
            )
            archive_source(source, head, archive, paths)
            await environment.upload_file(archive, f"{_ROOT}/source.tar")
            await self.exec_as_root(environment, command=(
                f"tar -xf {_ROOT}/source.tar -C {_SRC} && rm {_ROOT}/source.tar"
            ), timeout_sec=180)
            if runner != source:
                # Only research runtime code is overlaid; LoopX product code
                # and its installer remain exactly at the product revision.
                archive_source(runner, runner_head, archive, ("benchmark/runtime",))
                await environment.upload_file(archive, f"{_ROOT}/runner.tar")
                await self.exec_as_root(environment, command=(
                    f"tar -xf {_ROOT}/runner.tar -C {_SRC} && rm {_ROOT}/runner.tar"
                ), timeout_sec=180)
        return head

    def _profile_env(self) -> dict[str, str]:
        return {
            "HOME": _PROFILE_HOME,
            "CODEX_HOME": _SHARED_CODEX_HOME,
            "PATH": f"{_NODE}/bin:{_PROFILE}/bin:/usr/local/bin:/usr/bin:/bin",
            "LOOPX_PYTHON": f"{_PYTHON}/bin/python3",
            "LOOPX_PROMOTE_DEFAULT": "1",
            "LOOPX_INSTALL_CANARY": "0",
            "LOOPX_BIN_DIR": f"{_PROFILE}/bin",
            "LOOPX_RELEASES_DIR": f"{_PROFILE}/releases",
            "LOOPX_RELEASE_ID": "benchmark-runtime",
            "LOOPX_MAN_ROOT": f"{_PROFILE}/man",
            "LOOPX_MAN_DIR": f"{_PROFILE}/man/man1",
            "LOOPX_SHELL_PROFILE": f"{_PROFILE_HOME}/.profile",
            "LOOPX_SKILLS_DIR": _SHARED_SKILLS,
            "LOOPX_INSTALL_SLASH_COMMANDS": "0",
            "LOOPX_INSTALL_OPENCODE": "0",
            "LOOPX_INSTALL_CLAUDE": "0",
            "LOOPX_SKILL_DEDUPE_OTHER_ROOT": "0",
            # Codex tool calls use `bash -lc`, whose login profile may replace
            # PATH. BASH_ENV restores the staged Node for LoopX subprocesses.
            "BASH_ENV": _BASH_ENV,
        }

    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)

        loopx_src = Path(os.environ["LOOPX_SRC_DIR"]).resolve()
        portable_python = Path(os.environ["LOOPX_PORTABLE_PYTHON"]).resolve()
        node_root = Path(os.environ["LOOPX_NODE_DIR"]).resolve()
        await self.exec_as_root(
            environment,
            command=(
                f"mkdir -p {_SRC} {_PYTHON} {_NODE} {_PROFILE_HOME} "
                f"{_SHARED_CODEX_HOME} {_PROFILE}/bin {_PROFILE}/releases {_PROFILE}/man "
                f"{_CONTROL} "
                f"{_LOOPX_RUNTIME} {_CODEX_HOME} "
                f"{_WAKE_LOG_DIR}; chmod -R 0777 {_ROOT} {_WAKE_LOG_DIR}"
            ),
            timeout_sec=180,
        )
        actual_commit = await self._stage_source(environment, loopx_src)
        await environment.upload_dir(portable_python, _PYTHON)
        if self.execution.uses_loopx:
            await environment.upload_dir(node_root, _NODE)
        await self.exec_as_root(
            environment,
            command=(
                f"printf '%s\\n' 'export PATH={_NODE}/bin:$PATH' > {_BASH_ENV}; "
                f"chmod 0644 {_BASH_ENV}; "
                f"find {_SRC} -maxdepth 2 \\( -name '*.egg-info' -o "
                f"-name '*.dist-info' \\) -exec rm -rf {{}} +; "
                # The formal source installer builds generated frontend assets
                # as the environment user; the archive is extracted by root.
                f"chown -R {shlex.quote(str(environment.default_user or 'root'))} {_SRC}; "
                f"chmod -R a+rX {_SRC} {_PYTHON} {_NODE}; "
                f"chmod -R a+rwX {_PROFILE} {_CONTROL} {_CODEX_HOME} {_WAKE_LOG_DIR}"
            ),
            timeout_sec=300,
        )
        auth_path = self._get_env("CODEX_AUTH_JSON_PATH")
        if auth_path:
            await environment.upload_file(
                Path(auth_path).resolve(), f"{_CODEX_HOME}/auth.json"
            )
            owner = shlex.quote(str(environment.default_user or "root"))
            await self.exec_as_root(
                environment,
                command=(
                    f"chown {owner} {_CODEX_HOME}/auth.json && chmod 0600 {_CODEX_HOME}/auth.json"
                ),
            )
        if self.execution.uses_loopx:
            install = await self.exec_as_agent(
                environment,
                command=f"bash {_SRC}/scripts/install-local.sh",
                env=self._profile_env(),
                timeout_sec=1200,
            )
            if "error" in (install.stderr or "").lower():
                self.logger.debug("LoopX installer stderr: %s", install.stderr[-1000:])

            # Ownership is needed only for the trusted build. Restore the
            # staged source boundary before any solver/worker starts.
            await self.exec_as_root(
                environment, command=f"chown -R root {_SRC}", timeout_sec=180,
            )

            doctor = await self.exec_as_agent(
                environment,
                command=f"{_CLI} --format json doctor --agent-type codex-cli",
                env=self._profile_env(),
                timeout_sec=300,
            )
            try:
                doctor_payload = json.loads(doctor.stdout or "")
            except json.JSONDecodeError as exc:
                raise RuntimeError("LoopX doctor returned invalid JSON") from exc
            if doctor_payload.get("ok") is not True:
                raise RuntimeError(f"LoopX doctor failed: {doctor_payload}")

        receipt = {
            "loopx_commit": actual_commit,
            "runner_commit": self._runner_commit,
            "runtime_profile": "generic_cli",
            "execution_mode": self.execution.mode,
            "iteration_context": self.execution.context,
            "task_entry": self.execution.task_entry,
            "home_scope": "trial",
            "login_shell_node_path": _BASH_ENV,
            "scheduler_terminal_packet_compatibility": True,
            "replan_after_completed_todos": self.replan_after_todos,
        }
        await self.exec_as_agent(
            environment,
            command=(
                f"printf %s {shlex.quote(json.dumps(receipt, sort_keys=True))} "
                f"> /logs/agent/loopx-install.json"
            ),
            env=self._profile_env(),
        )

    async def _write_task_document(
        self, environment: BaseEnvironment, instruction: str
    ) -> None:
        descriptor, name = tempfile.mkstemp(prefix="benchmark-task-", suffix=".md")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write("# Current benchmark task\n\n")
                handle.write(instruction.strip())
                handle.write("\n")
            await environment.upload_file(Path(name), _TASK_DOC)
            await environment.upload_file(Path(name), self._task_document)
            await self.exec_as_root(
                environment,
                command=f"chmod 0644 {_TASK_DOC} {self._task_document}",
            )
        finally:
            Path(name).unlink(missing_ok=True)

    @property
    def _task_document(self) -> str:
        # Existing Todos keep their original input when Harbor supplies a new phase.
        return f"{_CONTROL}/task-phase-{self._phase_number:03d}.md"

    async def _loopx(
        self,
        environment: BaseEnvironment,
        args: list[str],
        *,
        cwd: str,
        require_ok: bool = True,
    ) -> dict:
        argv = [
            _CLI,
            "--format",
            "json",
            "--registry",
            _REGISTRY,
            "--runtime-root",
            _LOOPX_RUNTIME,
            *args,
        ]
        result = await self.exec_as_agent(
            environment,
            command=shlex.join(argv),
            env=self._profile_env(),
            cwd=cwd,
            timeout_sec=300,
        )
        text = (result.stdout or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"LoopX command returned invalid JSON: {text[:300]}"
            ) from exc
        if require_ok and payload.get("ok") is False:
            raise RuntimeError(f"LoopX command failed: {payload.get('error')}")
        return payload

    async def _registry_exists(self, environment: BaseEnvironment) -> bool:
        result = await environment.exec(command=f"test -s {_REGISTRY}")
        return result.return_code == 0

    async def _prepare_phase(
        self, environment: BaseEnvironment, instruction: str, *, cwd: str
    ) -> None:
        pending = await environment.exec(
            command=f"test -e {_LOOPX_RUNTIME}/benchmark-pending-turn.json"
        )
        if pending.return_code == 0:
            raise RuntimeError("Resolve the pending Turn before entering another task phase")
        await self._write_task_document(environment, instruction)
        if not await self._registry_exists(environment):
            await self._loopx(
                environment,
                [
                    "bootstrap",
                    "--project",
                    ".",
                    "--goal-id",
                    _GOAL_ID,
                    "--objective",
                    "Complete the current benchmark task through validated LoopX Todos.",
                    "--goal-doc",
                    _TASK_DOC,
                    "--adapter-kind",
                    "read_only_project_map_v0",
                    "--adapter-status",
                    "connected-read-only",
                    "--write-scope",
                    "**",
                    "--no-global-sync",
                ],
                cwd=cwd,
            )
            await self._loopx(
                environment,
                [
                    "configure-goal",
                    "--goal-id",
                    _GOAL_ID,
                    "--registered-agent",
                    _AGENT_ID,
                    "--boundary-authority-scope",
                    "**",
                    "--boundary-authority-source",
                    "harbor-task-workspace",
                    "--boundary-authority-decision-id",
                    "trial-workspace",
                    "--execution-replan-after-todos",
                    str(self.replan_after_todos),
                    "--agent-work-mode",
                    f"{_AGENT_ID}=active",
                    "--execute",
                ],
                cwd=cwd,
            )
        else:
            # New input is not evidence that an existing wait or gate was resolved.
            await self._loopx(
                environment,
                [
                    "configure-goal",
                    "--goal-id",
                    _GOAL_ID,
                    "--execution-replan-after-todos",
                    str(self.replan_after_todos),
                    "--execute",
                ],
                cwd=cwd,
            )

        if self.execution.task_entry == "seeded-todo":
            await self._seed_phase(environment, cwd=cwd)

        cadence = await self._loopx(
            environment, ["configure-goal", "--goal-id", _GOAL_ID], cwd=cwd,
        )
        configured_state = cadence.get("after") or cadence.get("before") or {}
        configured = configured_state.get("execution_profile", {}).get("replan_after_completed_todos")
        if configured != self.replan_after_todos:
            raise RuntimeError(
                f"replan cadence readback mismatch: expected {self.replan_after_todos}, got {configured!r}"
            )

    async def _seed_phase(self, environment: BaseEnvironment, *, cwd: str) -> None:
        text = (
            f"[P0] Execute benchmark phase {self._phase_number}. Read the exact "
            f"current task from {self._task_document}; inspect the workspace, implement and "
            "validate it, and create bounded successor Todos for remaining work."
        )
        if self._seeded_todo_id:
            listed = await self._loopx(environment, [
                "todo", "list", "--goal-id", _GOAL_ID, "--role", "agent",
                "--todo-id", self._seeded_todo_id,
            ], cwd=cwd)
            current = next(iter(listed["todos"]), None)
            if current and current.get("status") in {"open", "blocked"}:
                if current.get("claimed_by") != _AGENT_ID:
                    raise RuntimeError("Seeded task Todo is no longer owned by this agent")
                # New phase input revises our still-live generic task; do not
                # strand it behind an unfinished predecessor or clear a wait.
                await self._loopx(environment, [
                    "todo", "update", "--goal-id", _GOAL_ID,
                    "--todo-id", self._seeded_todo_id, "--agent-id", _AGENT_ID,
                    "--text", text, "--execute",
                ], cwd=cwd)
                return
        created = await self._loopx(
            environment,
            [
                "todo",
                "add",
                "--goal-id",
                _GOAL_ID,
                "--role",
                "agent",
                "--text",
                text,
                "--task-class",
                "advancement_task",
                "--action-kind",
                "benchmark_task",
                "--claimed-by",
                _AGENT_ID,
                "--status",
                "open",
                "--execute",
            ],
            cwd=cwd,
        )
        self._seeded_todo_id = created["todo_id"]

    def _worker_env(self, *, cwd: str) -> dict[str, str]:
        env = self._profile_env()
        if not self.execution.uses_loopx:
            env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
        env.update(
            {
                "PYTHONPATH": _SRC,
                "LOOPX_CLI": _CLI,
                "LOOPX_REGISTRY": _REGISTRY,
                "LOOPX_RUNTIME_ROOT": _LOOPX_RUNTIME,
                "LOOPX_GOAL_ID": _GOAL_ID,
                "LOOPX_AGENT_ID": _AGENT_ID,
                "LOOPX_PROJECT": cwd,
                "LOOPX_TASK_DOC": self._task_document,
                "LOOPX_WAKE_LOG_DIR": _WAKE_LOG_DIR,
                "LOOPX_CODEX_HOME": _CODEX_HOME,
                "LOOPX_SHARED_SKILLS": _SHARED_SKILLS,
                "LOOPX_EXECUTION_MODE": self.execution.mode,
                "LOOPX_TASK_ENTRY": self.execution.task_entry,
                "LOOPX_ITERATION_CONTEXT": self.execution.context,
                "LOOPX_CODEX_SANDBOX": self.execution.sandbox,
                "LOOPX_VALIDATION_COMMAND_JSON": json.dumps(
                    self.execution.validation_command
                ),
                "LOOPX_CODEX_TURN_TIMEOUT_SEC": str(self.execution.timeout_seconds),
                "CODEX_BIN": "/usr/local/bin/codex",
                "MODEL_NAME": (self.model_name or "").split("/", 1)[-1],
                "REASONING_EFFORT": str(
                    self._resolved_flags.get("reasoning_effort", "max")
                ),
                "OPENAI_BASE_URL": self._get_env("OPENAI_BASE_URL") or "",
                "OPENAI_API_KEY": self._get_env("OPENAI_API_KEY") or "",
                "CODEX_WIRE_API": self._get_env("CODEX_WIRE_API") or "responses",
            }
        )
        return env

    def _session_trajectories(self, roots: Iterable[Path]) -> list[Trajectory]:
        sessions: set[Path] = set()
        for root in roots:
            if root.is_dir():
                sessions.update(root.glob("sessions/**/*.jsonl"))
        trajectories: list[Trajectory] = []
        for session in sorted(sessions):
            # Harbor's parser takes a directory. Isolate each native rollout
            # so two sessions on the same date cannot collapse into one.
            with tempfile.TemporaryDirectory(prefix="benchmark-session-") as directory:
                (Path(directory) / session.name).symlink_to(session.resolve())
                trajectory = self._convert_events_to_trajectory(Path(directory))
                if trajectory is not None:
                    trajectories.append(trajectory)
        return trajectories

    @staticmethod
    def _totals(trajectories: Iterable[Trajectory]) -> dict[str, int | float | None]:
        prompt = completion = cached = 0
        costs: list[float] = []
        for trajectory in trajectories:
            metrics = trajectory.final_metrics
            if metrics is None:
                continue
            prompt += metrics.total_prompt_tokens or 0
            completion += metrics.total_completion_tokens or 0
            cached += metrics.total_cached_tokens or 0
            if metrics.total_cost_usd is not None:
                costs.append(metrics.total_cost_usd)
        return {
            "prompt": prompt,
            "completion": completion,
            "cached": cached,
            "cost": sum(costs) if costs else None,
        }

    def _write_aggregate_trajectory(self) -> list[Trajectory]:
        trajectories = self._session_trajectories([self.logs_dir])
        if not trajectories:
            return []
        steps = []
        for trajectory in trajectories:
            for step in trajectory.steps:
                copied = step.model_copy(deep=True)
                copied.step_id = len(steps) + 1
                steps.append(copied)
        totals = self._totals(trajectories)
        aggregate = Trajectory(
            schema_version="ATIF-v1.5",
            session_id=f"benchmark-{self.logs_dir.parent.name}",
            agent=trajectories[0].agent,
            steps=steps,
            final_metrics=FinalMetrics(
                total_prompt_tokens=totals["prompt"] or None,
                total_completion_tokens=totals["completion"] or None,
                total_cached_tokens=totals["cached"] or None,
                total_cost_usd=totals["cost"],
                total_steps=len(steps),
                extra={"native_sessions": len(trajectories)},
            ),
        )
        (self.logs_dir / "trajectory.json").write_text(
            format_trajectory_json(aggregate.to_json_dict()), encoding="utf-8"
        )
        return trajectories

    def _populate_context(
        self, context: AgentContext, before: dict | None = None
    ) -> None:
        totals = self._totals(self._session_trajectories([self.logs_dir]))
        before = before or {}
        context.n_input_tokens = int(totals["prompt"] or 0) - int(
            before.get("prompt") or 0
        )
        context.n_output_tokens = int(totals["completion"] or 0) - int(
            before.get("completion") or 0
        )
        context.n_cache_tokens = int(totals["cached"] or 0) - int(
            before.get("cached") or 0
        )
        context.cost_usd = (
            totals["cost"] - (before.get("cost") or 0)
            if totals["cost"] is not None
            else None
        )
        context.metadata = {
            "execution_mode": self.execution.mode,
            "iteration_context": self.execution.context,
            "task_entry": self.execution.task_entry,
            "home_scope": "trial",
            "replan_after_completed_todos": self.replan_after_todos,
            "benchmark_phase": self._phase_number,
        }
        self._write_aggregate_trajectory()

    def populate_context_post_run(self, context: AgentContext) -> None:
        self._populate_context(context)

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name:
            raise ValueError("model_name is required")
        self._phase_number += 1
        deadline = time.monotonic() + self.scheduler_timeout
        pwd = await self.exec_as_agent(environment, command="pwd", timeout_sec=30)
        cwd = (pwd.stdout or "").strip()
        if not cwd.startswith("/"):
            raise RuntimeError(
                f"could not resolve container working directory: {cwd!r}"
            )

        before = self._totals(self._session_trajectories([self.logs_dir]))
        try:
            if self.execution.uses_loopx:
                await self._prepare_phase(environment, instruction, cwd=cwd)
            else:
                await self._write_task_document(environment, instruction)
            wake_command = [f"{_PYTHON}/bin/python3", "-m", _WORKER_MODULE]
            env = self._worker_env(cwd=cwd)
            if self.execution.task_entry == "loopx-planned":
                result_path = f"{_CONTROL}/planning-phase-{self._phase_number:03d}.json"
                planning_timeout = min(self.planning_timeout, deadline - time.monotonic() - 30)
                if planning_timeout <= 0:
                    raise TimeoutError("Task budget exhausted before planning")
                await self.exec_as_agent(
                    environment, command=shlex.join(wake_command), cwd=cwd,
                    env=env | {
                        "LOOPX_TASK_STAGE": "plan",
                        "LOOPX_PLANNING_TIMEOUT_SEC": str(planning_timeout),
                        "LOOPX_PLANNING_RESULT": result_path,
                    },
                    timeout_sec=planning_timeout + 30,
                )
                observed = await environment.exec(command=f"cat {result_path}")
                entry = json.loads(observed.stdout or "")
                if entry.get("state_readback_verified") is not True:
                    raise RuntimeError("Planning did not return verified Todo readback")
                if entry["status"] == "blocked":
                    return
            remaining = int(deadline - time.monotonic())
            if remaining <= 160:
                raise TimeoutError("Task budget exhausted before execution handoff")
            # Planning consumes the phase budget, including when the host later resumes.
            # Keep ten seconds for scheduler startup before the worker checks
            # its execution window plus the existing 150-second settlement reserve.
            host_timeout = min(self.execution.timeout_seconds, remaining - 160)
            env["LOOPX_CODEX_TURN_TIMEOUT_SEC"] = str(host_timeout)
            from .scheduler import worker_command

            command = worker_command(
                env, python=f"{_PYTHON}/bin/python3", source=_SRC,
                state_file=_SCHEDULER_STATE, host_timeout=host_timeout,
            )
            phase_log = f"/logs/agent/worker-phase-{self._phase_number:03d}.log"
            shell = (
                "set +e; "
                # Use the task environment's clock, including remote backends.
                f"export LOOPX_PHASE_DEADLINE_EPOCH=$(( $(date +%s) + {remaining} )); "
                f"timeout --signal=TERM --kill-after=30 {remaining}s "
                f"{shlex.join(command)} >> {shlex.quote(phase_log)} 2>&1; "
                "rc=$?; "
                # Budget exhaustion retains partial artifacts for native scoring.
                'if [ "$rc" -eq 124 ]; then exit 0; fi; exit "$rc"'
            )
            await self.exec_as_agent(
                environment,
                command=shell,
                env=env,
                cwd=cwd,
                timeout_sec=remaining + 60,
            )
        finally:
            # Remote Harbor backends download logs after run(). Read them now
            # before populating a non-empty context, which Harbor will retain.
            mounted = getattr(environment, "is_mounted", None)
            if mounted is None:
                mounted = environment.capabilities.mounted
            if not mounted:
                await environment.download_dir("/logs/agent", self.logs_dir)
            self._populate_context(context, before)
