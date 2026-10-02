from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from ...heartbeat_prompt import build_heartbeat_prompt
from ..quota.turn_envelope import quota_action_signature_document
from .doubao_model_behavior_actor import (
    ARK_API_KEY_ENV,
    DOUBAO_2_1_PRO_MODEL,
    DOUBAO_MODEL_ENV,
    DoubaoActorTransport,
    _direct_ark_transport,
)
from .model_tool_behavior import (
    DoubaoExecToolClient,
    QUOTA_FIRST_TOOL_INSTRUCTION,
    argument_value,
    digest_text,
    execute_loopx_cli,
    loopx_command_tokens,
)

SELECTED_TODO_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION = (
    "selected_todo_tool_behavior_receipt_v0"
)
SELECTED_TODO_TOOL_BEHAVIOR_MAX_CALLS = 6

SELECTED_TODO_TOOL_FIXTURE_GOAL_ID = "portfolio-goal"
SELECTED_TODO_TOOL_FIXTURE_AGENT_ID = "codex-portfolio"
SELECTED_TODO_TOOL_FIXTURE_TODO_ID = "todo_portfolio001"
STALE_DONE_PRIMARY_TODO_ID = "todo_portfolio_stale001"
_SELECTED_TARGET = "fixture/selected-lane.json"
_DECOY_TARGET = "fixture/deferred-lane.json"
SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT = (
    f"Read only `{_SELECTED_TARGET}`; use its self-contained JSON to verify "
    "the selected lane contract."
)
STALE_DONE_PRIMARY_ACTION_TEXT = (
    "Do not repeat the legacy primary slice: its delivery and validation are "
    "already complete, but this Todo is intentionally still open to model "
    "stale-control-plane drift. Choose the visible successor instead."
)
_READ_ONLY_PREFLIGHT_COMMANDS = {
    "pwd",
    "ls",
    "ls -la",
    "git status --short --branch",
    "git branch --show-current",
    "git rev-parse --show-toplevel",
}


@dataclass(frozen=True)
class _SelectedTodoToolFixture:
    task_body: str
    quota_guard_command: str
    project_root: Path
    runtime_root: Path
    global_registry_path: Path
    source_root: Path
    selected_target: Path


@dataclass(frozen=True)
class BoundedWorkspaceReadStep:
    kind: str
    argv: tuple[str, ...]
    target: Path | None = None
    operator: str = ";"
    line_limit: int | None = None


def _build_fixture(
    root: Path,
    *,
    prior_in_flight_progress: bool = False,
    unsuggested_selected_todo: bool = False,
    stale_done_primary_successor: bool = False,
) -> _SelectedTodoToolFixture:
    source_root = Path(__file__).resolve().parents[3]
    project_root = root / "project"
    runtime_root = root / "runtime"
    fixture_home = root / "home"
    state_relative = (
        Path(".codex")
        / "goals"
        / SELECTED_TODO_TOOL_FIXTURE_GOAL_ID
        / "ACTIVE_GOAL_STATE.md"
    )
    state_path = project_root / state_relative
    local_registry_path = project_root / ".loopx" / "registry.json"
    global_registry_path = (
        fixture_home / ".codex" / "loopx" / "registry.global.json"
    )
    selected_target = project_root / _SELECTED_TARGET
    decoy_target = project_root / _DECOY_TARGET
    state_path.parent.mkdir(parents=True, exist_ok=True)
    selected_target.parent.mkdir(parents=True, exist_ok=True)
    selected_target.write_text(
        json.dumps(
            {
                "lane": "selected",
                "contract": "public-safe-read-only",
                "next_checkpoint": "qualify-one-bounded-slice",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    decoy_target.write_text(
        json.dumps(
            {
                "lane": "deferred",
                "contract": "not-selected",
                "next_checkpoint": "do-not-run",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "-q"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=10,
    )
    decoy_action = (
        STALE_DONE_PRIMARY_ACTION_TEXT
        if stale_done_primary_successor
        else
        f"Read only `{_DECOY_TARGET}`; start the newly queued sibling lane."
    )
    next_action = (
        decoy_action
        if prior_in_flight_progress
        or unsuggested_selected_todo
        or stale_done_primary_successor
        else SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT
    )
    decoy_count = 3 if unsuggested_selected_todo else 1 if prior_in_flight_progress else 0
    decoy_todo = (
        f"- [ ] [P1] {STALE_DONE_PRIMARY_ACTION_TEXT}\n"
        "  <!-- loopx:todo "
        f"todo_id={STALE_DONE_PRIMARY_TODO_ID} status=open "
        "task_class=advancement_task action_kind=legacy_slice_already_complete "
        f"claimed_by={SELECTED_TODO_TOOL_FIXTURE_AGENT_ID} priority=P1 -->\n"
        if stale_done_primary_successor
        else "".join(
            f"- [ ] [P1] {decoy_action} Candidate {index}.\n"
            "  <!-- loopx:todo "
            f"todo_id=todo_portfolio_decoy00{index} status=open "
            "task_class=advancement_task action_kind=inspect_deferred_contract "
            f"claimed_by={SELECTED_TODO_TOOL_FIXTURE_AGENT_ID} priority=P1 -->\n"
            for index in range(1, decoy_count + 1)
        )
    )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Advance the selected public-safe lane."\n'
        "updated_at: 2026-08-12T00:00:00+08:00\n"
        "---\n\n"
        "# Selected Todo Live Fixture\n\n"
        "## Objective\n\n"
        "Advance the selected public-safe lane.\n\n"
        "## Next Action\n\n"
        f"- {next_action}\n\n"
        "## Agent Todo\n\n"
        f"{decoy_todo}"
        f"- [ ] [P1] {SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT}\n"
        "  <!-- loopx:todo "
        f"todo_id={SELECTED_TODO_TOOL_FIXTURE_TODO_ID} status=open "
        "task_class=advancement_task action_kind=inspect_selected_contract "
        f"claimed_by={SELECTED_TODO_TOOL_FIXTURE_AGENT_ID} priority=P1 -->\n",
        encoding="utf-8",
    )
    registry = {
        "schema_version": "0.1",
        "updated_at": "2026-08-12T00:00:00+08:00",
        "common_runtime_root": str(runtime_root),
        "goals": [
            {
                "id": SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
                "domain": "selected-todo-live-fixture",
                "status": "active",
                "repo": str(project_root),
                "state_file": str(state_relative),
                "adapter": {
                    "kind": "fixture_connected_delivery_v0",
                    "status": "connected-delivery",
                },
                "coordination": {
                    "registered_agents": [SELECTED_TODO_TOOL_FIXTURE_AGENT_ID],
                    "agent_model": "peer_v1",
                },
                "authority_sources": [],
                "quota": {
                    "compute": 1.0,
                    "window_hours": 24,
                    "allowed_slots": 5,
                },
            }
        ],
    }
    registry_text = json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True)
    for registry_path in (local_registry_path, global_registry_path):
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(registry_text + "\n", encoding="utf-8")

    if prior_in_flight_progress:
        run = {
            "generated_at": "2026-08-12T00:01:00+00:00",
            "goal_id": SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
            "agent_id": SELECTED_TODO_TOOL_FIXTURE_AGENT_ID,
            "classification": "bounded_selected_lane_progress",
            "progress_scope": "agent_lane",
            "todo_id": SELECTED_TODO_TOOL_FIXTURE_TODO_ID,
            "delivery_outcome": "outcome_progress",
            "recommended_action": SELECTED_TODO_TOOL_FIXTURE_ACTION_TEXT,
        }
        runs_dir = runtime_root / "goals" / SELECTED_TODO_TOOL_FIXTURE_GOAL_ID / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_path = runs_dir / "2026-08-12T00-01-00+00-00.json"
        markdown_path = runs_dir / "2026-08-12T00-01-00+00-00.md"
        run_path.write_text(json.dumps(run, sort_keys=True) + "\n", encoding="utf-8")
        markdown_path.write_text(
            "# Bounded selected-lane progress\n",
            encoding="utf-8",
        )
        (runs_dir / "index.jsonl").write_text(
            json.dumps(
                {
                    **run,
                    "json_path": str(run_path),
                    "markdown_path": str(markdown_path),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    prompt = build_heartbeat_prompt(
        goal_id=SELECTED_TODO_TOOL_FIXTURE_GOAL_ID,
        thin=True,
        agent_id=SELECTED_TODO_TOOL_FIXTURE_AGENT_ID,
        registered_agents=[SELECTED_TODO_TOOL_FIXTURE_AGENT_ID],
        available_capabilities=["shell", "filesystem_read"],
        runtime_profile="codex_app_heartbeat",
    )
    quota_guard_command = str(prompt["quota_guard_command"])
    if quota_guard_command not in str(prompt["task_body"]):
        raise ValueError("heartbeat fixture must contain its production quota guard")
    return _SelectedTodoToolFixture(
        task_body=str(prompt["task_body"]),
        quota_guard_command=quota_guard_command,
        project_root=project_root,
        runtime_root=runtime_root,
        global_registry_path=global_registry_path,
        source_root=source_root,
        selected_target=selected_target,
    )


def _clock_output(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or Path(tokens[0]).name != "date" or len(tokens) > 4:
        return None
    if any(
        token in {";", "&&", "||", "|"}
        or (index > 0 and not token.startswith(("-", "+")))
        for index, token in enumerate(tokens)
    ):
        return None
    if "-u" in tokens or "--utc" in tokens:
        return "2026-08-11T16:00:00Z\n"
    return "2026-08-12T00:00:00+08:00\n"


def _is_quota_guard(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens:
        return False
    try:
        quota_index = tokens.index("quota")
    except ValueError:
        return False
    return bool(
        tokens[quota_index : quota_index + 2] == ["quota", "should-run"]
        and argument_value(tokens, "--goal-id")
        == SELECTED_TODO_TOOL_FIXTURE_GOAL_ID
        and argument_value(tokens, "--agent-id")
        == SELECTED_TODO_TOOL_FIXTURE_AGENT_ID
        and "--codex-app" in tokens
        and argument_value(tokens, "--turn-instance-id")
    )


def _is_candidate_discovery(command: str) -> bool:
    tokens = loopx_command_tokens(command)
    if not tokens:
        return False
    try:
        todo_index = tokens.index("todo")
    except ValueError:
        return False
    return bool(
        tokens[todo_index : todo_index + 2] == ["todo", "list"]
        and argument_value(tokens, "--goal-id")
        == SELECTED_TODO_TOOL_FIXTURE_GOAL_ID
        and argument_value(tokens, "--role") == "agent"
    )


def _resolve_project_path(
    value: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> Path | None:
    path = Path(value)
    resolved = (
        path.resolve()
        if path.is_absolute()
        else (fixture.project_root / path).resolve()
    )
    try:
        resolved.relative_to(fixture.project_root.resolve())
    except ValueError:
        return None
    return resolved


def _resolve_metadata_path(
    value: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> Path | None:
    normalized = value.rstrip("/")
    candidate = Path(normalized)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        fixture_roots = (
            fixture.project_root.resolve(),
            fixture.runtime_root.resolve(),
            fixture.global_registry_path.parents[2].resolve(),
        )
        if any(
            resolved == root or root in resolved.parents for root in fixture_roots
        ):
            return resolved
        return None
    runtime_marker = next(
        (
            marker
            for marker in (
                "~/.loopx",
                "$HOME/.loopx",
                "${HOME}/.loopx",
                "~/.codex/loopx",
                "$HOME/.codex/loopx",
                "${HOME}/.codex/loopx",
            )
            if normalized == marker or normalized.startswith(marker + "/")
        ),
        None,
    )
    if runtime_marker is not None:
        suffix = normalized[len(runtime_marker) :].lstrip("/")
        runtime_root = fixture.global_registry_path.parent.resolve()
        resolved = (runtime_root / suffix).resolve()
        try:
            resolved.relative_to(runtime_root)
        except ValueError:
            return None
        return resolved
    return _resolve_project_path(value, fixture=fixture)


def _is_fixture_metadata_target(
    target: Path | None,
    *,
    fixture: _SelectedTodoToolFixture,
) -> bool:
    if target is None:
        return False
    allowed = {
        (fixture.project_root / ".loopx" / "registry.json").resolve(),
        (
            fixture.project_root
            / ".codex"
            / "goals"
            / SELECTED_TODO_TOOL_FIXTURE_GOAL_ID
            / "ACTIVE_GOAL_STATE.md"
        ).resolve(),
    }
    return target.resolve() in allowed


def _metadata_pipeline_step(
    segment: list[str],
    *,
    fixture: _SelectedTodoToolFixture,
    operator: str,
) -> BoundedWorkspaceReadStep | None:
    pipe_indices = [index for index, token in enumerate(segment) if token == "|"]
    if len(pipe_indices) not in {1, 2}:
        return None
    left = segment[: pipe_indices[0]]
    right = segment[pipe_indices[-1] + 1 :]
    if len(left) >= 3 and left[-3:] == ["2", ">", "/dev/null"]:
        left = left[:-3]
    if len(pipe_indices) == 2:
        middle = segment[pipe_indices[0] + 1 : pipe_indices[1]]
        if len(middle) >= 3 and middle[-3:] == ["2", ">", "/dev/null"]:
            middle = middle[:-3]
        if not (
            len(middle) == 3
            and Path(middle[0]).name in {"python", "python3"}
            and middle[1:] == ["-m", "json.tool"]
        ):
            return None
    if len(right) == 2 and Path(right[0]).name == "head":
        limit_token = right[1]
        if not limit_token.startswith("-") or not limit_token[1:].isdigit():
            return None
        limit = int(limit_token[1:])
    elif (
        len(right) == 3
        and Path(right[0]).name == "head"
        and right[1] == "-n"
        and right[2].isdigit()
    ):
        limit = int(right[2])
    else:
        return None
    if limit < 1 or limit > 200:
        return None
    discovery_argv = _discovery_tokens(left, fixture=fixture)
    if discovery_argv is not None:
        return BoundedWorkspaceReadStep(
            "metadata",
            tuple(discovery_argv),
            operator=operator,
            line_limit=limit,
        )
    if len(left) != 2 or Path(left[0]).name != "cat":
        return None
    target = _resolve_metadata_path(left[1], fixture=fixture)
    allowed_registries = {
        fixture.global_registry_path.resolve(),
        (fixture.project_root / ".loopx" / "registry.json").resolve(),
    }
    if target in allowed_registries:
        return BoundedWorkspaceReadStep(
            "metadata",
            ("head", "-n", str(limit), str(target)),
            operator=operator,
        )
    content_target = _resolve_project_path(left[1], fixture=fixture)
    if content_target is None:
        return None
    return BoundedWorkspaceReadStep(
        "content",
        ("head", "-n", str(limit), str(content_target)),
        target=content_target,
        operator=operator,
    )


def _read_plan_tokens(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    return tokens or None


def bounded_workspace_read_plan(
    command: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> list[BoundedWorkspaceReadStep] | None:
    tokens = _read_plan_tokens(command)
    if not tokens:
        return None
    segments: list[list[str]] = [[]]
    operators = [";"]
    for token in tokens:
        if token in {"&&", "||", ";", "\n"}:
            if not segments[-1]:
                return None
            segments.append([])
            operators.append(";" if token == "\n" else token)
        elif token == "|":
            segments[-1].append(token)
        elif token in {"&", "<", "<<", ">>", "<<<"}:
            return None
        else:
            segments[-1].append(token)
    if not segments[-1] or len(segments) > 8:
        return None

    plan: list[BoundedWorkspaceReadStep] = []
    for operator, raw_segment in zip(operators, segments, strict=True):
        segment = list(raw_segment)
        if "|" in segment:
            pipeline_step = _metadata_pipeline_step(
                segment,
                fixture=fixture,
                operator=operator,
            )
            if pipeline_step is None:
                return None
            plan.append(pipeline_step)
            continue
        if len(segment) >= 3 and segment[-3:] == ["2", ">", "/dev/null"]:
            segment = segment[:-3]
        if not segment or any(
            token in {"&", "|", "<", ">", "<<", ">>", "<<<"}
            for token in segment
        ):
            return None
        executable = Path(segment[0]).name
        if (
            executable == "export"
            and len(segment) == 2
            and segment[1].startswith("LOOPX_TURN=")
            and len(segment[1]) <= 160
        ):
            plan.append(BoundedWorkspaceReadStep("metadata", ("true",), operator=operator))
            continue
        if executable == "cd" and len(segment) == 2:
            target = _resolve_metadata_path(segment[1], fixture=fixture)
            if target != fixture.project_root.resolve():
                return None
            plan.append(BoundedWorkspaceReadStep("metadata", ("true",), operator=operator))
            continue
        if executable == "pwd" and len(segment) == 1:
            plan.append(BoundedWorkspaceReadStep("metadata", (executable,), operator=operator))
            continue
        if executable == "echo" and len(segment) == 2:
            plan.append(
                BoundedWorkspaceReadStep(
                    "separator",
                    (executable, *segment[1:]),
                    operator=operator,
                )
            )
            continue
        if executable == "ls":
            paths = [token for token in segment[1:] if not token.startswith("-")]
            options = [token for token in segment[1:] if token.startswith("-")]
            resolved_paths = [
                _resolve_metadata_path(path, fixture=fixture) for path in paths
            ]
            if (
                len(paths) > 4
                or any(set(option[1:]) - {"a", "l"} for option in options)
                or any(path is None for path in resolved_paths)
            ):
                return None
            argv = [executable, *options]
            argv.extend(str(path) for path in resolved_paths if path is not None)
            plan.append(BoundedWorkspaceReadStep("metadata", tuple(argv), operator=operator))
            continue
        discovery_argv = _discovery_tokens(segment, fixture=fixture)
        if discovery_argv is not None:
            plan.append(
                BoundedWorkspaceReadStep(
                    "metadata",
                    tuple(discovery_argv),
                    operator=operator,
                )
            )
            continue
        content_shape = bool(
            (executable == "cat" and len(segment) == 2)
            or (
                executable == "head"
                and (
                    len(segment) == 2
                    or (
                        len(segment) == 4
                        and segment[1] == "-n"
                        and segment[2].isdigit()
                    )
                )
            )
            or (
                executable == "sed"
                and len(segment) == 4
                and segment[1] == "-n"
                and segment[2].endswith("p")
                and all(
                    character.isdigit() or character in {",", "p"}
                    for character in segment[2]
                )
            )
        )
        if not content_shape:
            return None
        target = _resolve_project_path(segment[-1], fixture=fixture)
        if target is None:
            return None
        plan.append(
            BoundedWorkspaceReadStep(
                "content",
                (executable, *segment[1:-1], str(target)),
                target,
                operator,
            )
        )
    return plan


def _discovery_tokens(
    tokens: list[str],
    *,
    fixture: _SelectedTodoToolFixture,
) -> list[str] | None:
    if not tokens:
        return None
    executable = Path(tokens[0]).name
    if executable == "rg":
        if len(tokens) not in {2, 3} or tokens[1] != "--files":
            return None
        if len(tokens) == 3 and _resolve_project_path(
            tokens[2], fixture=fixture
        ) is None:
            return None
        return [executable, *tokens[1:]]
    if executable != "find" or len(tokens) < 2:
        return None
    root = _resolve_metadata_path(tokens[1], fixture=fixture)
    if root is None:
        return None
    index = 2
    has_maxdepth = False
    while index < len(tokens):
        token = tokens[index]
        if token == "-o":
            index += 1
            continue
        if token == "-print":
            index += 1
            continue
        if token == "-maxdepth" and index + 1 < len(tokens):
            depth = tokens[index + 1]
            if not depth.isdigit() or int(depth) > 4:
                return None
            has_maxdepth = True
            index += 2
            continue
        if token == "-type" and index + 1 < len(tokens):
            if tokens[index + 1] not in {"d", "f"}:
                return None
            index += 2
            continue
        if token == "-name" and index + 1 < len(tokens):
            pattern = tokens[index + 1]
            if (
                not pattern
                or len(pattern.encode("utf-8")) > 128
                or "/" in pattern
                or pattern.startswith("-")
            ):
                return None
            index += 2
            continue
        path_pattern_index: int | None = None
        next_index: int | None = None
        if token == "-path" and index + 1 < len(tokens):
            path_pattern_index = index + 1
            next_index = index + 2
        elif (
            token == "-not"
            and index + 2 < len(tokens)
            and tokens[index + 1] == "-path"
        ):
            path_pattern_index = index + 2
            next_index = index + 3
        if path_pattern_index is not None and next_index is not None:
            pattern = tokens[path_pattern_index]
            if (
                not pattern
                or len(pattern.encode("utf-8")) > 128
                or Path(pattern).is_absolute()
                or ".." in Path(pattern).parts
            ):
                return None
            index = next_index
            continue
        return None
    argv = [executable, str(root), *tokens[2:]]
    if not has_maxdepth:
        argv[2:2] = ["-maxdepth", "4"]
    return argv


def _discovery_argv(
    command: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> list[str] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if any(token in {";", "&&", "||", "|", ">", ">>"} for token in tokens):
        return None
    return _discovery_tokens(tokens, fixture=fixture)


def _execute_selected_read(
    command: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> str:
    plan = bounded_workspace_read_plan(command, fixture=fixture)
    content_steps = [step for step in (plan or []) if step.kind == "content"]
    selected_steps = [
        step
        for step in content_steps
        if step.target == fixture.selected_target.resolve()
    ]
    if not plan or not selected_steps:
        raise ValueError("selected action is not a bounded file read")
    if any(
        step.target != fixture.selected_target.resolve()
        and not _is_fixture_metadata_target(step.target, fixture=fixture)
        for step in content_steps
    ):
        raise ValueError("selected action does not target the selected todo")
    output, executed_content = _execute_read_plan(plan, fixture=fixture)
    if executed_content != len(selected_steps):
        raise ValueError("selected todo content read was skipped")
    return output


def _execute_read_plan(
    plan: list[BoundedWorkspaceReadStep],
    *,
    fixture: _SelectedTodoToolFixture,
) -> tuple[str, int]:
    outputs: list[str] = []
    status = 0
    executed_content = 0
    for step in plan:
        should_run = bool(
            step.operator == ";"
            or (step.operator == "&&" and status == 0)
            or (step.operator == "||" and status != 0)
        )
        if not should_run:
            continue
        completed = subprocess.run(
            list(step.argv),
            cwd=fixture.project_root,
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        status = completed.returncode
        step_output = completed.stdout
        if step.line_limit is not None:
            step_output = "".join(
                step_output.splitlines(keepends=True)[: step.line_limit]
            )
        outputs.append(step_output)
        if (
            step.kind != "content"
            or status != 0
            or step.target != fixture.selected_target.resolve()
        ):
            continue
        executed_content += 1
        observed = json.loads(step_output)
        if observed != {
            "contract": "public-safe-read-only",
            "lane": "selected",
            "next_checkpoint": "qualify-one-bounded-slice",
        }:
            raise ValueError("selected todo readback does not match the fixture")
    if status != 0:
        raise RuntimeError(f"bounded read plan failed with exit={status}")
    return "".join(outputs), executed_content


def _execute_workspace_read(
    command: str,
    *,
    fixture: _SelectedTodoToolFixture,
) -> str:
    plan = bounded_workspace_read_plan(command, fixture=fixture)
    if plan is not None:
        output, _ = _execute_read_plan(plan, fixture=fixture)
        return output
    argv = _discovery_argv(command, fixture=fixture) or shlex.split(command)
    completed = subprocess.run(
        argv,
        cwd=fixture.project_root,
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"workspace preflight failed with exit={completed.returncode}"
        )
    return completed.stdout


def _classify_tool_command(
    command: str,
    *,
    fixture: _SelectedTodoToolFixture,
    quota_observed: bool,
) -> tuple[str, str | None]:
    if _is_quota_guard(command):
        return "quota_should_run", None
    if _is_candidate_discovery(command):
        return "candidate_discovery", None
    clock_output = _clock_output(command)
    if clock_output is not None:
        return "clock", clock_output
    if command in _READ_ONLY_PREFLIGHT_COMMANDS:
        return "workspace_read", None
    if _discovery_argv(command, fixture=fixture) is not None:
        return "workspace_read", None
    plan = bounded_workspace_read_plan(command, fixture=fixture)
    if plan is not None:
        content_targets = [
            step.target for step in plan if step.kind == "content"
        ]
        selected_target = fixture.selected_target.resolve()
        if selected_target in content_targets and all(
            target == selected_target
            or _is_fixture_metadata_target(target, fixture=fixture)
            for target in content_targets
        ):
            return (
                "selected_action"
                if quota_observed
                else "selected_action_before_quota"
            ), None
        if content_targets and all(
            _is_fixture_metadata_target(target, fixture=fixture)
            for target in content_targets
        ):
            return "workspace_read", None
        if content_targets:
            return "wrong_selected_todo_target", None
        return "workspace_read", None
    return "unexpected_command", None


def _quota_contract(packet: Mapping[str, Any]) -> dict[str, Any]:
    signature = quota_action_signature_document(packet)
    action = dict(signature.get("action") or {})
    user = dict(signature.get("user") or {})
    selected = dict(action.get("selected_todo") or {})
    action_portfolio_value = packet.get("action_portfolio")
    action_portfolio: Mapping[str, Any] = (
        action_portfolio_value
        if isinstance(action_portfolio_value, Mapping)
        else {}
    )
    raw_suggested_actions = action_portfolio.get("suggested_actions")
    suggested_actions = (
        raw_suggested_actions if isinstance(raw_suggested_actions, list) else []
    )
    suggested_action_ids = [
        str(item.get("todo_id") or "").strip()
        for item in suggested_actions
        if isinstance(item, Mapping) and str(item.get("todo_id") or "").strip()
    ]
    interaction = packet.get("interaction_contract")
    interaction = interaction if isinstance(interaction, Mapping) else {}
    raw_cli_channel = interaction.get("cli_channel")
    cli_channel = (
        raw_cli_channel
        if isinstance(raw_cli_channel, Mapping)
        else {}
    )
    contract: dict[str, Any] = {
        "decision": "execute",
        "selected_todo_id": selected.get("todo_id"),
        "user_action_required": bool(user.get("action_required")),
        "must_attempt_work": bool(action.get("must_attempt")),
        "delivery_allowed": bool(action.get("delivery_allowed")),
        "quiet_noop_allowed": bool(action.get("quiet_noop_allowed")),
        "external_write_requested": False,
        "selection_required": cli_channel.get("selection_required") is True,
        "suggested_action_ids": suggested_action_ids,
    }
    if (
        (
            not contract["selection_required"]
            and contract["selected_todo_id"]
            != SELECTED_TODO_TOOL_FIXTURE_TODO_ID
        )
        or contract["user_action_required"]
        or not contract["must_attempt_work"]
        or contract["quiet_noop_allowed"]
    ):
        raise ValueError("real quota packet did not select the fixture todo")
    return contract


def _receipt(
    *,
    qualification_id: str,
    actor_ref: str,
    steps: list[dict[str, Any]],
    contract: Mapping[str, Any] | None,
    passed: bool,
    failure_code: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SELECTED_TODO_TOOL_BEHAVIOR_RECEIPT_SCHEMA_VERSION,
        "qualification_id": qualification_id,
        "actor_ref": actor_ref,
        "qualification_passed": passed,
        "failure_code": failure_code,
        **dict(contract or {}),
        "observed_tool_sequence": [step["kind"] for step in steps],
        "tool_call_count": len(steps),
        "selected_action_matched_todo": passed,
        "tool_call_receipts": steps,
        "boundary": {
            "raw_prompt_persisted": False,
            "raw_provider_response_persisted": False,
            "raw_command_persisted": False,
            "filesystem_writes_executed": True,
            "writes_limited_to_temporary_fixture": True,
            "external_writes_executed": False,
            "shell_commands_executed": False,
            "read_only_host_commands_executed": bool(steps),
        },
    }


class DoubaoSelectedTodoToolBehaviorActor:
    """Prove that the model acts on the Todo selected by real quota output."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DOUBAO_2_1_PRO_MODEL,
        timeout_seconds: float = 90.0,
        transport: DoubaoActorTransport = _direct_ark_transport,
    ) -> None:
        self._client = DoubaoExecToolClient(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        transport: DoubaoActorTransport = _direct_ark_transport,
        timeout_seconds: float = 90.0,
    ) -> DoubaoSelectedTodoToolBehaviorActor:
        values = os.environ if environ is None else environ
        api_key = values.get(ARK_API_KEY_ENV, "")
        if not api_key.strip():
            raise RuntimeError(
                "ARK_API_KEY is not injected; live Doubao qualification is unavailable"
            )
        return cls(
            api_key=api_key,
            model=values.get(DOUBAO_MODEL_ENV, DOUBAO_2_1_PRO_MODEL),
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def qualify(
        self,
        *,
        qualification_id: str,
        fixture_root: Path,
        prior_in_flight_progress: bool = False,
        unsuggested_selected_todo: bool = False,
        stale_done_primary_successor: bool = False,
    ) -> dict[str, Any]:
        fixture = _build_fixture(
            fixture_root,
            prior_in_flight_progress=prior_in_flight_progress,
            unsuggested_selected_todo=unsuggested_selected_todo,
            stale_done_primary_successor=stale_done_primary_successor,
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are Codex running one LoopX heartbeat. Follow the "
                    "heartbeat task and use the available shell tool when needed. "
                    f"{QUOTA_FIRST_TOOL_INSTRUCTION}"
                    "The shell working directory is the connected goal project "
                    "root; resolve relative paths from the selected Todo there. "
                    "Choose each next action from the latest tool result. If quota "
                    "sets selection_required, choose any currently eligible Todo; "
                    "selection_command is non-binding. Replace {todo_id}, then "
                    "run exactly `<route_prefix> <command_args_template>` for the "
                    "second quota guard before delivery. Only when the visible "
                    "packet lacks the Todo identity you need, run exactly "
                    "`<route_prefix> <candidate_discovery_args>`. "
                    "Otherwise execute the exact action in selected_todo.text directly. "
                    "When it names one file, read only that file; do not discover, "
                    "compare, or inspect other targets. After that selected Todo "
                    "tool succeeds, call no more tools."
                ),
            },
            {"role": "user", "content": fixture.task_body},
        ]
        steps: list[dict[str, Any]] = []
        quota_observed = False
        selection_required = False
        contract: dict[str, Any] | None = None
        seen_once: set[str] = set()
        actor_ref = self._client.actor_ref
        qualification_digest = sha256(qualification_id.encode()).hexdigest()[:16]
        turn_instance_id = f"qualification-{qualification_digest}"

        def receipt(*, passed: bool, failure_code: str | None) -> dict[str, Any]:
            return _receipt(
                qualification_id=qualification_id,
                actor_ref=actor_ref,
                steps=steps,
                contract=contract,
                passed=passed,
                failure_code=failure_code,
            )

        for _ in range(SELECTED_TODO_TOOL_BEHAVIOR_MAX_CALLS):
            tool_call = self._client.next_tool_call(messages)
            if tool_call is None:
                return receipt(
                    passed=False,
                    failure_code="model_returned_without_tool_call",
                )

            kind, tool_output = _classify_tool_command(
                tool_call.command,
                fixture=fixture,
                quota_observed=quota_observed,
            )
            steps.append(
                {
                    "ordinal": len(steps) + 1,
                    "kind": kind,
                    "command_digest": digest_text(tool_call.command),
                }
            )
            if kind == "selected_action":
                if selection_required:
                    return receipt(
                        passed=False,
                        failure_code="delivery_before_action_selection",
                    )
                try:
                    _execute_selected_read(tool_call.command, fixture=fixture)
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="selected_action_execution_failed",
                    )
                return receipt(passed=True, failure_code=None)
            if kind in {
                "selected_action_before_quota",
                "wrong_selected_todo_target",
                "unexpected_command",
            }:
                return receipt(passed=False, failure_code=kind)
            if kind == "quota_should_run" and quota_observed:
                tokens = loopx_command_tokens(tool_call.command) or []
                requested_todo_id = argument_value(tokens, "--todo-id")
                if not selection_required:
                    return receipt(
                        passed=False,
                        failure_code="repeated_quota_should_run",
                    )
                kind = "quota_action_selection"
                steps[-1]["kind"] = kind
            if kind == "clock" and kind in seen_once:
                return receipt(passed=False, failure_code=f"repeated_{kind}")
            if kind == "clock":
                seen_once.add(kind)
            if kind in {"quota_should_run", "quota_action_selection"}:
                try:
                    tool_output = execute_loopx_cli(
                        tool_call.command,
                        source_root=fixture.source_root,
                        project_root=fixture.project_root,
                        argument_overrides={
                            "--registry": str(fixture.global_registry_path),
                            "--turn-instance-id": turn_instance_id,
                        },
                    )
                    quota_packet = json.loads(tool_output)
                    contract = _quota_contract(quota_packet)
                    quota_observed = True
                    selection_required = bool(contract["selection_required"])
                    if kind == "quota_action_selection":
                        if selection_required:
                            raise ValueError("action selection did not bind the turn")
                        if contract["selected_todo_id"] != requested_todo_id:
                            raise ValueError("action selection bound a different Todo")
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="quota_execution_failed",
                    )
            elif kind == "candidate_discovery":
                try:
                    tool_output = execute_loopx_cli(
                        tool_call.command,
                        source_root=fixture.source_root,
                        project_root=fixture.project_root,
                        argument_overrides={
                            "--registry": str(fixture.global_registry_path),
                        },
                    )
                except (RuntimeError, ValueError, json.JSONDecodeError):
                    return receipt(
                        passed=False,
                        failure_code="candidate_discovery_failed",
                    )
            elif kind == "workspace_read":
                try:
                    tool_output = _execute_workspace_read(
                        tool_call.command,
                        fixture=fixture,
                    )
                except RuntimeError:
                    return receipt(
                        passed=False,
                        failure_code="workspace_read_failed",
                    )
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call.provider_value],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.call_id,
                        "content": tool_output or "",
                    },
                ]
            )

        return receipt(passed=False, failure_code="tool_call_budget_exhausted")
