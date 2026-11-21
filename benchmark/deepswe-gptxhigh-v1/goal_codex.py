"""Codex driven through its native Goal API, as a Pier agent.

Pier's stock Codex agent runs `codex exec` once.  That is enough to *create* a
Goal — with `features.goals` on, the model will call `create_goal` when asked —
but not to exercise one: the process exits after the first turn, so the
automatic continuation loop that is the entire point of Goal mode never runs.
Measured that way, Goal mode looks like a no-op, and the experiment would
conclude the wrong thing for a purely mechanical reason.

So this subclass keeps everything Pier does to stand Codex up — npm install
through the CN mirror, CODEX_HOME, auth.json, config.toml, skills, MCP, session
capture — and swaps only the final invocation for the app-server transaction:

    initialize(experimentalApi=true) -> thread/start -> thread/goal/set(active)
      -> turn/start -> observe continuation turns while the Goal stays active

That transaction is not reimplemented here.  `native_codex_goal.py` from LoopX
already owns it, is stdlib-only, and is the same code path the LoopX arm will
use later — sharing it is what keeps the two arms differing in LoopX alone
rather than in how each one talks to Codex.  It is copied into the container at
run time rather than baked into the image so that the two arms cannot drift.

The swap is done by intercepting `exec_as_agent` rather than by reimplementing
`run()`.  Pier's `run()` is one long method whose setup (auth resolution,
ownership fixes, config blocks) would have to be duplicated and kept in step
with upstream; intercepting the one command that matters leaves that setup
untouched.  If Pier ever changes how it invokes Codex, the marker below stops
matching and this fails loudly instead of silently reverting to plain
`codex exec` — which would look like a successful Goal run with no Goal in it.

Usage:

    MR_AGENT=goal_codex:GoalCodex MR_MODEL=openai/gpt-5.5 ./run.sh --all -i <task>

Environment:

    MR_GOAL_PREFLIGHT=1        prove Goal attachment and stop before any model
                               turn — costs nothing, use it first on a new box
    MR_GOAL_TIMEOUT_SEC        ceiling for the continuation loop (default 3600,
                               under the 5400 s task budget so the loop stops
                               itself instead of being killed mid-turn)
    MR_GOAL_TOKEN_BUDGET       optional Goal token budget
    MR_NATIVE_GOAL_MODULE      path to LoopX's native_codex_goal.py on the host
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from pier.agents.installed.codex import Codex
from pier.models.trial.paths import EnvironmentPaths

# Pier builds exactly one command containing this; see
# pier/agents/installed/codex.py, Codex.run().
_CODEX_EXEC_MARKER = "codex exec "

_REMOTE_DIR = "/tmp/loopx-goal"
_LOOPX_MOUNT = "/opt/loopx"
_DEFAULT_LOOPX_ROOT = str(
    Path(__file__).resolve().parents[1] / "wen" / "loopx"
)
_DEFAULT_MODULE_RELATIVE = (
    "loopx/capabilities/benchmark_toolkit/native_codex_goal.py"
)

# The Goal objective is fixed rather than derived from the task text.  A Goal is
# meant to state the durable intent that survives across continuation turns,
# while the task file already carries the specifics; restating the task as the
# objective gave the model two copies of the same thing and nothing to hold on
# to between turns.  DeepSWE grades a committed patch, so committing belongs in
# the objective — a run that solves the task and never commits scores zero.
_OBJECTIVE = (
    "Complete the software engineering task described in the task file. "
    "Work in the repository, keep existing behaviour intact, verify the change "
    "against the repository's own tests, and commit the finished work to a new "
    "branch off main. The goal is complete only once the change is committed."
)

# A Goal only continues while it is still active, so an objective that one turn
# can satisfy never exercises the continuation loop — and the objective above
# says outright that committing completes it.  Across 53 runs the continuation
# count was zero every time, which makes the measured "Goal API has no effect"
# a statement about an objective that never needed the API, not about the API.
#
# This variant withholds completion until work that cannot plausibly finish in
# one turn is done: pass, then re-derive from the tests, then hunt regressions,
# then edge cases. Whether that actually keeps the Goal active is the thing
# being tested — if the continuation count is still zero, single-turn
# termination is Codex's behaviour here rather than an artefact of the wording.
_OBJECTIVE_STAGED = (
    "Complete the software engineering task described in the task file, in "
    "stages, and do not consider the goal complete until every stage is done.\n"
    "Stage 1: make the target behaviour work and commit it.\n"
    "Stage 2: re-read the task description and check your implementation "
    "against every requirement it states, including ones you did not address "
    "in stage 1. Fix what is missing and commit.\n"
    "Stage 3: look for behaviour you may have broken elsewhere in the "
    "repository, run the wider test suite, and fix any regression you find.\n"
    "Stage 4: consider edge cases the tests may not cover — empty inputs, "
    "concurrent use, error paths — and handle the ones the task implies.\n"
    "The goal is complete only after stage 4."
)


def _objective() -> str:
    return _OBJECTIVE_STAGED if os.environ.get("MR_GOAL_OBJECTIVE") == "staged" else _OBJECTIVE


# All three arms keep Pier's own agent name.  Overriding name() per arm looked
# tidy but fed straight into AgentInstallSpec.fingerprint(), whose first input is
# agent_name — so each arm produced a different PIER_AGENT_INSTALL_FINGERPRINT,
# invalidated the Docker layer cache, and rebuilt `nvm install 22` plus the npm
# install of Codex for every task in every arm: 162 builds where 54 would do.
# It also removed the only fallback for a network outage, since a cached layer
# needs no proxy.  The arm is selected by pier_cn.py rebinding AgentName.CODEX,
# which needs no distinct name.

_WEB_SEARCH_OFF = 'printf "\\nweb_search = \\"disabled\\"\\n" >> "$CODEX_HOME/config.toml"'


class PlainCodex(Codex):
    """The control arm: same everything, no Goal attached.

    Exists so that Goal vs no-Goal differs in the Goal API and nothing else.
    Two things have to be carried over from GoalCodex or the comparison measures
    the wrong difference:

    * ``web_search`` off.  Stock Codex leaves it at its default, and it is a
      hosted tool the container's egress allowlist cannot block, so one arm
      could look answers up.
    * the objective text.  A Goal cannot exist without an objective, so the Goal
      arm is necessarily prompted with those three sentences.  Withholding them
      here would fold "the effect of that wording" into the measured difference.
      Appending them leaves the API as the only variable.

    What remains different is intrinsic: `codex exec` runs one turn and exits,
    while the app-server keeps serving continuations while the Goal stays
    active.  That *is* the treatment.
    """

    async def run(self, instruction, environment, context):  # type: ignore[override]
        return await super().run(
            f"{instruction}\n\n{_objective()}", environment, context
        )

    async def exec_as_agent(self, environment, command: str = "", env=None, **kwargs):  # type: ignore[override]
        if _CODEX_EXEC_MARKER in command:
            await super().exec_as_agent(environment, command=_WEB_SEARCH_OFF, env=env)
        return await super().exec_as_agent(
            environment, command=command, env=env, **kwargs
        )


class LoopxCodex(Codex):
    """The third arm: Codex driven by LoopX's governed Turn loop.

    The other two arms both stop when the model says it is finished — `codex
    exec` exits, and the Goal API marked every one of 53 runs complete on the
    first turn.  LoopX is the only arm where something other than the model
    decides: it runs one Turn, requires an independent validator to prove the
    postcondition, and only then commits and spends quota.  Turn two happens
    because the controller asks for it.

    Everything else is held to the other arms: same model, same disabled
    web_search, same bypassed sandbox, same task set.  The staged Todo text
    mirrors the four-stage objective already tested on the Goal arm, where it
    did not produce a single continuation — so any multi-turn behaviour here is
    attributable to the loop rather than to the wording.

    LoopX is bind-mounted rather than installed: it declares no runtime
    dependencies, and a read-only mount cannot drift between tasks the way 54
    separate installs could.

    Sandbox is ``workspace-write`` rather than the bypass the other two arms
    use, because LoopX rejects anything else in two places — the argparse
    choices and again in the driver ("Codex CLI sandbox must be read-only or
    workspace-write").  Whether Codex can actually execute under it inside
    these containers is the thing this arm has to establish first: the app-
    server path could not, but that is a different code path from
    ``codex exec --sandbox``, and assuming they behave alike is what a smoke
    test is for.  If it works, the other two arms should move to the same value
    so permissions stop being a second difference between the arms.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loopx_instruction: str | None = None
        self._loopx_swapped = False

    async def run(self, instruction, environment, context):  # type: ignore[override]
        self._loopx_instruction = instruction
        self._loopx_swapped = False
        try:
            return await super().run(instruction, environment, context)
        finally:
            if not self._loopx_swapped:
                raise RuntimeError(
                    "LoopxCodex never intercepted a `codex exec` command — this "
                    "run would have been plain Codex with no LoopX loop."
                )

    async def exec_as_agent(self, environment, command: str = "", env=None, **kwargs):  # type: ignore[override]
        if _CODEX_EXEC_MARKER not in command:
            return await super().exec_as_agent(
                environment, command=command, env=env, **kwargs
            )

        self._loopx_swapped = True
        model = self._command_model_name or (self.model_name or "").split("/")[-1]

        loopx_root = Path(os.environ.get("MR_LOOPX_ROOT", _DEFAULT_LOOPX_ROOT))
        if not (loopx_root / "loopx" / "__init__.py").is_file():
            raise FileNotFoundError(f"LoopX package not found under {loopx_root}")
        runner_src = Path(__file__).resolve().parent / "loopx_turn_runner.py"

        await super().exec_as_agent(
            environment, command=f"mkdir -p {shlex.quote(_REMOTE_DIR)}", env=env
        )
        await super().exec_as_agent(environment, command=_WEB_SEARCH_OFF, env=env)

        # LoopX arrives as one tarball rather than a bind mount: the container is
        # created by Pier from its own compose file, so an agent cannot add a
        # mount to it, and uploading 710 files one at a time is not a serious
        # option.  Packed once per run rather than once per task would be nicer
        # still, but the tar is ~13 MB and building it is far cheaper than the
        # model turn that follows.
        runner_source = getattr(self, "_goal_runner_source", None)
        with tempfile.TemporaryDirectory() as tmp:
            tarball = Path(tmp) / "loopx.tar.gz"
            subprocess.run(
                ["tar", "czf", str(tarball), "-C", str(loopx_root), "loopx"],
                check=True,
            )
            task_path = Path(tmp) / "task.txt"
            task_path.write_text(self._loopx_instruction or "", encoding="utf-8")
            for local, remote in (
                (tarball, f"{_REMOTE_DIR}/loopx.tar.gz"),
                (task_path, f"{_REMOTE_DIR}/task.txt"),
                (runner_src, f"{_REMOTE_DIR}/loopx_turn_runner.py"),
                (runner_src.parent / "codex_nosandbox_wrapper.py",
                 f"{_REMOTE_DIR}/codex_nosandbox_wrapper.py"),
            ):
                await environment.upload_file(str(local), remote)

        if environment.default_user is not None:
            await self.exec_as_root(
                environment,
                command=f"chown -R {environment.default_user} {shlex.quote(_REMOTE_DIR)} && chmod +x {shlex.quote(_REMOTE_DIR)}/codex_nosandbox_wrapper.py",
            )
        await super().exec_as_agent(
            environment,
            command=(
                f"mkdir -p {shlex.quote(_LOOPX_MOUNT)} && "
                f"tar xzf {shlex.quote(_REMOTE_DIR)}/loopx.tar.gz "
                f"-C {shlex.quote(_LOOPX_MOUNT)} && "
                f"python3 -c 'import sys; sys.path.insert(0, \"{_LOOPX_MOUNT}\"); "
                "import loopx.cli_commands.turn'"
            ),
            env=env,
        )

        args = [
            "python3",
            f"{_REMOTE_DIR}/loopx_turn_runner.py",
            "--project", "__PWD__",
            "--task-file", f"{_REMOTE_DIR}/task.txt",
            "--runtime-root", f"{_REMOTE_DIR}/runtime",
            "--codex-bin", "codex",
            "--model", model,
            "--sandbox", os.environ.get("MR_LOOPX_SANDBOX", "workspace-write"),
            "--quota", os.environ.get("MR_LOOPX_QUOTA", "4"),
        ]
        rendered = shlex.join(args).replace("'__PWD__'", '"$(pwd)"').replace(
            "__PWD__", '"$(pwd)"'
        )
        output = (EnvironmentPaths.agent_dir / "loopx-turns.json").as_posix()
        return await super().exec_as_agent(
            environment,
            command=(
                "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                f"PYTHONPATH={shlex.quote(_LOOPX_MOUNT)} {rendered} "
                f"2>&1 </dev/null | tee {shlex.quote(output)}"
            ),
            env=env,
        )


class GoalCodex(Codex):
    """Codex with its native Goal loop actually running."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._goal_instruction: str | None = None
        self._goal_swapped = False

    async def run(self, instruction, environment, context):  # type: ignore[override]
        self._goal_instruction = instruction
        self._goal_swapped = False
        try:
            return await super().run(instruction, environment, context)
        finally:
            if not self._goal_swapped:
                raise RuntimeError(
                    "GoalCodex never intercepted a `codex exec` command — Pier's "
                    "Codex.run() no longer matches the expected shape, and this "
                    "run would have been plain Codex with no Goal attached."
                )

    async def exec_as_agent(self, environment, command: str = "", env=None, **kwargs):  # type: ignore[override]
        if _CODEX_EXEC_MARKER not in command:
            return await super().exec_as_agent(
                environment, command=command, env=env, **kwargs
            )

        self._goal_swapped = True
        instruction = self._goal_instruction or ""
        model = self._command_model_name or (self.model_name or "").split("/")[-1]

        loopx_root = Path(
            os.environ.get("MR_LOOPX_ROOT", _DEFAULT_LOOPX_ROOT)
        ).expanduser()
        module_src = Path(
            os.environ.get(
                "MR_NATIVE_GOAL_MODULE",
                str(loopx_root / _DEFAULT_MODULE_RELATIVE),
            )
        )
        if not module_src.is_file():
            raise FileNotFoundError(
                f"native_codex_goal.py not found at {module_src}; set "
                "MR_NATIVE_GOAL_MODULE to LoopX's copy"
            )
        runner_source = getattr(self, "_goal_runner_source", None)

        await super().exec_as_agent(
            environment, command=f"mkdir -p {shlex.quote(_REMOTE_DIR)}", env=env
        )

        # Upload rather than heredoc: the instruction is arbitrary user text and
        # routinely contains quotes, backticks and $ — shell-quoting it into a
        # container command is how the objective silently loses characters.
        with tempfile.TemporaryDirectory() as tmp:
            objective_path = Path(tmp) / "objective.txt"
            task_path = Path(tmp) / "task.txt"
            objective_path.write_text(_objective(), encoding="utf-8")
            task_path.write_text(instruction, encoding="utf-8")

            uploads = [
                (module_src, f"{_REMOTE_DIR}/native_codex_goal.py"),
                (objective_path, f"{_REMOTE_DIR}/objective.txt"),
                (task_path, f"{_REMOTE_DIR}/task.txt"),
            ]
            if runner_source is not None:
                uploads.append((Path(runner_source), f"{_REMOTE_DIR}/run_goal.py"))
            for local, remote in uploads:
                await environment.upload_file(str(local), remote)

        # upload_file writes as root; the agent user has to be able to read them.
        if environment.default_user is not None:
            await self.exec_as_root(
                environment,
                command=f"chown -R {environment.default_user} {shlex.quote(_REMOTE_DIR)}",
            )

        # Codex ships a `web_search` tool.  It is a hosted tool — the provider
        # runs the search, so it does not traverse the container's network and
        # the egress allowlist that blocks apt, pip and the open internet does
        # not block it.  Left at its default, one arm of this experiment could
        # look things up while the 113-task mini-swe-agent baseline could not,
        # and no amount of network isolation would show it.  Disabling it in
        # config.toml covers both `codex exec` and `codex app-server`; the key
        # and its values come from the binary's own override message ("live",
        # "cached", "disabled").  The no-Goal arm needs the same line for the
        # comparison to hold.
        await super().exec_as_agent(
            environment,
            command=_WEB_SEARCH_OFF,
            env=env,
        )

        if runner_source is None:
            runner = _RUNNER.format(remote_dir=_REMOTE_DIR)
            await super().exec_as_agent(
                environment,
                command=f"cat > {shlex.quote(_REMOTE_DIR)}/run_goal.py <<'LOOPX_EOF'\n{runner}\nLOOPX_EOF",
                env=env,
            )

        timeout = os.environ.get("MR_GOAL_TIMEOUT_SEC", "3600")
        budget = os.environ.get("MR_GOAL_TOKEN_BUDGET", "")
        effort = os.environ.get("MR_REASONING_EFFORT", "").strip()
        preflight = os.environ.get("MR_GOAL_PREFLIGHT", "") not in ("", "0")

        args = [
            "python3",
            f"{_REMOTE_DIR}/run_goal.py",
            "--cwd",
            "__PWD__",
            "--objective-file",
            # The LoopX arm renders its objective with LoopX's own CLI and
            # points here; a subclass cannot substitute it by rewriting the
            # command, because this method calls `super().exec_as_agent` rather
            # than `self.`, so an override never sees the built command at all.
            os.environ.get("MR_GOAL_OBJECTIVE_FILE", f"{_REMOTE_DIR}/objective.txt"),
            "--task-file",
            f"{_REMOTE_DIR}/task.txt",
            "--codex-bin",
            "codex",
            "--model",
            model,
            "--goal-timeout-seconds",
            timeout,
            # NativeGoalConfig defaults to sandbox="workspace-write", which on
            # Linux is enforced with landlock/seccomp and cannot be set up
            # inside these task containers.  Codex then declines to run
            # commands: the first attempt produced 431 assistant-message deltas,
            # zero command_execution events, zero file_change events and an
            # empty model.patch, which the verifier scored 0/24 — a harness
            # failure that reads exactly like the model failing the task.
            # `codex exec` avoids it with --dangerously-bypass-approvals-and-
            # sandbox; this is the app-server equivalent, so both arms execute
            # under the same permissions.
            "--sandbox",
            os.environ.get("MR_GOAL_SANDBOX", "danger-full-access"),
        ]
        if effort:
            args += ["--effort", effort]
        if budget:
            args += ["--token-budget", budget]
        # Empty on this arm.  The LoopX arm sets it to the skill ids its
        # installed profile materialized, which arms `skills/list` as a
        # precondition of thread creation: without it a run in which Codex never
        # discovered LoopX still finishes and still scores, and is then filed as
        # a LoopX result.  One such run has already happened.
        skills = os.environ.get("MR_GOAL_REQUIRED_SKILL_IDS", "").strip()
        if skills:
            args += ["--required-skill-ids", skills]
        runner_env = getattr(self, "_goal_runner_env", {})
        if runner_env:
            args += [
                "--loopx-cli", runner_env["LOOPX_CLI"],
                "--registry", runner_env["LOOPX_REGISTRY"],
                "--runtime-root", runner_env["LOOPX_RUNTIME_ROOT"],
                "--goal-id", runner_env["LOOPX_GOAL_ID"],
                "--agent-id", runner_env["LOOPX_AGENT_ID"],
            ]
            if runner_env.get("LOOPX_MODE"):
                args += ["--mode", runner_env["LOOPX_MODE"]]
            if runner_env.get("LOOPX_RECOVER_BLOCKED") == "1":
                args.append("--recover-blocked")
            if runner_env.get("LOOPX_MODE") in {"codex-cli", "heartbeat"}:
                args += [
                    "--segment-timeout-seconds",
                    os.environ.get("MR_HEARTBEAT_SEGMENT_TIMEOUT_SEC", "7200"),
                ]
            if runner_env.get("LOOPX_MODE") == "ssh-goal":
                args += [
                    "--turn-idle-timeout-seconds",
                    os.environ.get("MR_LOOPX_TURN_IDLE_TIMEOUT_SEC", "7200"),
                ]
        if preflight:
            args.append("--preflight-only")

        # No task declares a working directory, so `codex exec` would have run
        # in whatever WORKDIR the image sets — different per repository.  Resolve
        # it in the container instead of guessing.  Both spellings are replaced
        # because shlex.join only quotes a token that needs it, and this one
        # (letters and underscores) comes back bare: matching only the quoted
        # form silently leaves the placeholder in the command, which surfaces as
        # FileNotFoundError('__PWD__') from inside the runner.
        rendered = shlex.join(args)
        rendered = rendered.replace("'__PWD__'", '"$(pwd)"').replace(
            "__PWD__", '"$(pwd)"'
        )

        output = (EnvironmentPaths.agent_dir / "codex-goal.json").as_posix()
        # The LoopX arm points CODEX_HOME at its installed profile so app-server
        # discovers the LoopX skills; this arm leaves it as Pier set it up.
        codex_home = os.environ.get("MR_GOAL_CODEX_HOME", "").strip()
        prefix = f"export CODEX_HOME={shlex.quote(codex_home)}; " if codex_home else ""
        for key, value in runner_env.items():
            prefix += f"export {key}={shlex.quote(str(value))}; "
        # The profile's `loopx` launcher was built on the host and records the
        # host's own Python path, which does not exist in the container.  This
        # was fixed for the one-shot bootstrap script by exporting the variable
        # in that command's own shell -- but bootstrap and this long-lived
        # app-server process are separate `docker exec` invocations, each with
        # its own shell, so the export did not carry over. Any `loopx` command
        # Codex itself runs *during* a turn -- todo claim, quota should-run,
        # heartbeat-prompt -- inherits app-server's process environment, not
        # bootstrap's, and failed with the same "configured Python executable
        # not found" every time until this export is repeated here. A session
        # transcript showed exactly that: two failed `loopx` calls mid-turn.
        if os.environ.get("MR_GOAL_ARM_LOOPX_PYTHON", "") not in ("", "0"):
            prefix += 'export LOOPX_PYTHON="$(command -v python3)"; '
        return await super().exec_as_agent(
            environment,
            command=(
                "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                f"{prefix}{rendered} 2>&1 </dev/null | tee {shlex.quote(output)}"
            ),
            env=env,
        )


# Wen-aligned plain control uses the same GoalCodex setup but uploads a runner
# that skips the Goal attachment transaction.
class PlainAppServerCodex(GoalCodex):
    """App-server transport with no Goal and no LoopX control logic."""

    _plain_runner_source = Path(__file__).with_name("plain_appserver_runner.py")

    async def exec_as_agent(self, environment, command: str = "", env=None, **kwargs):  # type: ignore[override]
        if _CODEX_EXEC_MARKER in command:
            self._goal_runner_source = self._plain_runner_source
        return await super().exec_as_agent(
            environment, command=command, env=env, **kwargs
        )


# Written into the container next to the uploaded module.  LoopX's own
# run_native_codex_goal.py imports through the `loopx` package, which is not
# installed in a task image; this is the same entry point with the package
# import removed.
_RUNNER = '''\
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, "{remote_dir}")
from native_codex_goal import (
    NativeGoalConfig,
    compact_native_goal_receipt,
    probe_native_goal_process,
    run_native_goal_process_until_terminal,
)

p = argparse.ArgumentParser()
p.add_argument("--cwd", required=True)
p.add_argument("--objective-file", required=True)
p.add_argument("--task-file", required=True)
p.add_argument("--codex-bin", default="codex")
p.add_argument("--model")
p.add_argument("--effort")
p.add_argument("--token-budget", type=int)
p.add_argument("--response-timeout-seconds", type=float, default=30)
p.add_argument("--goal-timeout-seconds", type=float, default=21600)
p.add_argument("--sandbox", default="danger-full-access")
p.add_argument("--required-skill-ids", default="")
p.add_argument("--preflight-only", action="store_true")
a = p.parse_args()

config = NativeGoalConfig(
    cwd=a.cwd,
    objective=Path(a.objective_file).read_text(encoding="utf-8").strip(),
    task_instruction=Path(a.task_file).read_text(encoding="utf-8").strip(),
    model=a.model,
    effort=a.effort,
    token_budget=a.token_budget,
    sandbox=a.sandbox,
    # Empty on the Goal arm, which has no skills to require.  The LoopX arm
    # passes the installed skill ids, which makes `skills/list` a precondition
    # of thread creation: a run where Codex never discovered LoopX then fails
    # before any model work rather than scoring as a LoopX result.
    required_skill_ids=tuple(
        s for s in (x.strip() for x in a.required_skill_ids.split(",")) if s
    ),
)
if a.preflight_only:
    turn = probe_native_goal_process(
        config, codex_bin=a.codex_bin, response_timeout_sec=a.response_timeout_seconds
    )
    mode = "goal_attachment_preflight"
else:
    turn = run_native_goal_process_until_terminal(
        config,
        codex_bin=a.codex_bin,
        response_timeout_sec=a.response_timeout_seconds,
        goal_timeout_sec=a.goal_timeout_seconds,
    )
    mode = "goal_until_terminal"

receipt = compact_native_goal_receipt(turn)
receipt["execution_mode"] = mode
print(json.dumps(receipt, indent=2, sort_keys=True))
'''
