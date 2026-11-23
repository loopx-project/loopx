"""Apply one initial planning proposal through the existing LoopX owners."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from loopx.control_plane.todos.contract import (
    TODO_ACTION_KIND_PATTERN,
    normalize_todo_action_kind,
)

from .planning import task_plan_packet

_OPAQUE_ID_MAX_CHARS = 128
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]*$")
_OPAQUE_ID_HINT = "ASCII letters/digits and . _ : @ -, no '/' paths or spaces"
_OBJECTIVE_MAX_CHARS = 1024
_REQUIREMENT_DESCRIPTION_MAX_CHARS = 2000
_REQUIREMENTS_MAX_ITEMS = 32
_SOURCE_SUMMARY_MAX_CHARS = 512
_TODOS_MAX_ITEMS = 64


def _utf16_code_unit_len(value: str) -> int:
    """Count UTF-16 code units so Python limits match TypeScript String.length."""
    return len(value.encode("utf-16-le")) // 2


class PlanningBatchCommandError(ValueError):
    """Preserve structured LoopX CLI rejection details without leaking stdout noise."""

    def __init__(self, message: str, payload: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.payload = dict(payload or {})


def planning_batch_schema() -> dict[str, object]:
    """Return the complete self-contained JSON proposal schema and field rules."""
    return {
        "schema_version": "benchmark_planning_batch_proposal_v1",
        "length_unit": "utf16_code_units",
        "required_fields": ["input_digest", "expected_provider_revision", "outcome", "todos"],
        "fields": {
            "input_digest": {
                "type": "string",
                "description": "Exact packet.input_digest from the Host-supplied planning checkpoint.",
            },
            "expected_provider_revision": {
                "type": "string",
                "description": "Exact packet.goal_acceptance.provider_revision from the checkpoint.",
            },
            "outcome": {
                "type": "object",
                "required_fields": ["objective", "requirements", "source"],
                "properties": {
                    "objective": {
                        "type": "string",
                        "length_unit": "utf16_code_units",
                        "min_utf16_code_units": 1,
                        "max_utf16_code_units": _OBJECTIVE_MAX_CHARS,
                    },
                    "requirements": {
                        "type": "array",
                        "min_items": 1,
                        "max_items": _REQUIREMENTS_MAX_ITEMS,
                        "items": {
                            "required_fields": ["id", "description"],
                            "id": {
                                "type": "string",
                                "length_unit": "utf16_code_units",
                                "min_utf16_code_units": 1,
                                "max_utf16_code_units": _OPAQUE_ID_MAX_CHARS,
                                "pattern": _OPAQUE_ID_PATTERN.pattern,
                                "unique": True,
                            },
                            "description": {
                                "type": "string",
                                "length_unit": "utf16_code_units",
                                "min_utf16_code_units": 1,
                                "max_utf16_code_units": _REQUIREMENT_DESCRIPTION_MAX_CHARS,
                            },
                        },
                    },
                    "source": {
                        "type": "object",
                        "required_fields": ["ref", "summary"],
                        "ref": {
                            "type": "string",
                            "length_unit": "utf16_code_units",
                            "min_utf16_code_units": 1,
                            "max_utf16_code_units": _OPAQUE_ID_MAX_CHARS,
                            "pattern": _OPAQUE_ID_PATTERN.pattern,
                            "note": "Use an opaque identifier such as checkpoint:initial or task:readme; never file paths or spaces.",
                        },
                        "summary": {
                            "type": "string",
                            "length_unit": "utf16_code_units",
                            "min_utf16_code_units": 1,
                            "max_utf16_code_units": _SOURCE_SUMMARY_MAX_CHARS,
                        },
                    },
                },
            },
            "todos": {
                "type": "array",
                "min_items": 1,
                "max_items": _TODOS_MAX_ITEMS,
                "items": {
                    "required_fields": ["text", "action_kind"],
                    "optional_fields": ["target_key"],
                    "text": {"type": "string", "min_utf16_code_units": 1, "unique": True},
                    "action_kind": {
                        "type": "string",
                        "pattern": TODO_ACTION_KIND_PATTERN.pattern,
                        "examples": ["implement", "validate", "investigate", "submit"],
                    },
                    "target_key": {"type": "string", "optional": True, "min_utf16_code_units": 1},
                },
            },
        },
        "example": {
            "input_digest": "<packet.input_digest>",
            "expected_provider_revision": "<packet.goal_acceptance.provider_revision>",
            "outcome": {
                "objective": "Concise task objective (<=1024 UTF-16 code units).",
                "requirements": [
                    {"id": "req-1", "description": "Verifiable requirement description (<=2000 UTF-16 code units)."},
                ],
                "source": {"ref": "checkpoint:initial", "summary": "Host-supplied planning checkpoint (<=512 UTF-16 code units)."},
            },
            "todos": [
                {"text": "[P0] First ordered work step", "action_kind": "implement"},
                {"text": "[P1] Verify and test result", "action_kind": "validate", "target_key": "verification"},
            ],
        },
    }


def planning_batch_guidance() -> str:
    command = shlex.join([sys.executable, "-m", "benchmark.runtime.planning_batch"])
    return (
        "Initial plan batch (optional for an empty work and requirements frontier). "
        "After following the planner, save a JSON proposal alongside "
        "LOOPX_PLANNING_RESULT with exact keys: "
        '{"input_digest": "<checkpoint.input_digest>", '
        '"expected_provider_revision": "<checkpoint.goal_acceptance.provider_revision>", '
        '"outcome": {"objective": "<str <=1024 UTF-16 code units>", '
        '"requirements": [{"id": "<opaque-id>", "description": "<str <=2000 UTF-16 code units>"}], '
        '"source": {"ref": "<opaque-id>", "summary": "<str <=512 UTF-16 code units>"}}, '
        '"todos": [{"text": "<str>", "action_kind": "<lowercase token>", "target_key": "<optional str>"}]}. '
        "Constraint: bounded string limits are measured in UTF-16 code units (non-BMP emoji count as 2); "
        "outcome.source.ref and requirements[*].id must be opaque ASCII IDs "
        f"(1..{_OPAQUE_ID_MAX_CHARS} UTF-16 code units matching ^[A-Za-z0-9][A-Za-z0-9_.:@-]*$, NO '/' paths or spaces, "
        'e.g. "checkpoint:initial"); todos[*].action_kind must match ^[a-z][a-z0-9_-]{0,63}$ '
        "(e.g. implement, validate, investigate, submit); inspect full schema with "
        f"`{command} --schema`. "
        f"Run {command} <proposal.json> and return the successful planning result JSON directly "
        '(when it exits 0 with status="ready", do not make extra todo list or goal-acceptance calls). '
        "Keep the unchanged proposal on interruption and rerun the same command. For existing work, "
        "requirement revisions or blockers, follow the ordinary planning steps.\n\n"
    )


def _record(
    value,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
    *,
    path: str = "proposal",
) -> dict:
    allowed = required | optional
    if not isinstance(value, dict):
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"Planning proposal has invalid {path}: expected JSON object with fields ({expected})")
    missing = sorted(required - value.keys())
    unsupported_count = len(value.keys() - allowed)
    if missing or unsupported_count:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unsupported_count:
            details.append(f"unsupported field count: {unsupported_count}")
        details.append(f"allowed: {', '.join(sorted(allowed))}")
        raise ValueError(f"Planning proposal has missing or unsupported fields at {path} ({'; '.join(details)})")
    return value


def _text(value, *, path: str = "field", max_chars: int | None = None) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"Planning proposal requires non-empty text at {path} (expected string without NUL)")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Planning proposal requires non-empty text at {path}")
    if max_chars is not None:
        units = _utf16_code_unit_len(normalized)
        if units > max_chars:
            raise ValueError(
                f"Planning proposal field {path} exceeds max length (got {units} UTF-16 code units, max {max_chars})"
            )
    return normalized


def _opaque_id(value, *, path: str) -> str:
    text = _text(value, path=path)
    units = _utf16_code_unit_len(text)
    too_long = units > _OPAQUE_ID_MAX_CHARS
    invalid_chars = not _OPAQUE_ID_PATTERN.match(text)
    if too_long or invalid_chars:
        reasons = []
        if too_long:
            reasons.append(f"got {units} UTF-16 code units, max {_OPAQUE_ID_MAX_CHARS}")
        if invalid_chars:
            reasons.append(f"must match {_OPAQUE_ID_PATTERN.pattern} ({_OPAQUE_ID_HINT})")
        raise ValueError(
            f"Planning proposal field {path} must be an opaque identifier ({'; '.join(reasons)})"
        )
    return text


def _action_kind(value, *, path: str) -> str:
    raw = _text(value, path=path)
    if not normalize_todo_action_kind(raw):
        raise ValueError(
            f"Planning proposal field {path} must match {TODO_ACTION_KIND_PATTERN.pattern} "
            "(lowercase ASCII token, e.g. implement, validate, investigate, submit)"
        )
    return raw


def _request(proposal: dict) -> dict:
    _record(proposal, {"input_digest", "expected_provider_revision", "outcome", "todos"}, path="proposal")
    outcome = _record(proposal["outcome"], {"objective", "requirements", "source"}, path="outcome")
    source = _record(outcome["source"], {"ref", "summary"}, path="outcome.source")
    raw_requirements, raw_todos = outcome["requirements"], proposal["todos"]
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise ValueError("Planning proposal needs at least one requirement in outcome.requirements")
    if len(raw_requirements) > _REQUIREMENTS_MAX_ITEMS:
        raise ValueError(
            f"Planning proposal outcome.requirements exceeds capacity (got {len(raw_requirements)} items, max {_REQUIREMENTS_MAX_ITEMS})"
        )
    if not isinstance(raw_todos, list) or not raw_todos:
        raise ValueError("Planning proposal needs at least one Todo in todos")
    if len(raw_todos) > _TODOS_MAX_ITEMS:
        raise ValueError(
            f"Planning proposal todos exceeds capacity (got {len(raw_todos)} items, max {_TODOS_MAX_ITEMS})"
        )
    requirements = []
    for index, item in enumerate(raw_requirements):
        row = _record(item, {"id", "description"}, path=f"outcome.requirements[{index}]")
        requirements.append({
            "id": _opaque_id(row["id"], path=f"outcome.requirements[{index}].id"),
            "description": _text(
                row["description"],
                path=f"outcome.requirements[{index}].description",
                max_chars=_REQUIREMENT_DESCRIPTION_MAX_CHARS,
            ),
        })
    todos = []
    for index, todo in enumerate(raw_todos):
        row = _record(todo, {"text", "action_kind"}, {"target_key"}, path=f"todos[{index}]")
        item = {
            "text": _text(row["text"], path=f"todos[{index}].text"),
            "action_kind": _action_kind(row["action_kind"], path=f"todos[{index}].action_kind"),
        }
        if "target_key" in row:
            item["target_key"] = _text(row["target_key"], path=f"todos[{index}].target_key")
        todos.append(item)
    if len({item["id"] for item in requirements}) != len(requirements):
        raise ValueError("Planning requirement IDs in outcome.requirements must be unique")
    if len({todo["text"] for todo in todos}) != len(todos):
        raise ValueError("Planning Todos in todos must have distinct text")
    return {
        "input_digest": _text(proposal["input_digest"], path="input_digest"),
        "expected_provider_revision": _text(proposal["expected_provider_revision"], path="expected_provider_revision"),
        "outcome": {
            "objective": _text(outcome["objective"], path="outcome.objective", max_chars=_OBJECTIVE_MAX_CHARS),
            "requirements": requirements,
            "source": {
                "ref": _opaque_id(source["ref"], path="outcome.source.ref"),
                "summary": _text(source["summary"], path="outcome.source.summary", max_chars=_SOURCE_SUMMARY_MAX_CHARS),
            },
        },
        "todos": todos,
    }


def _outcomes(acceptance: dict) -> list[dict]:
    return acceptance["goal_acceptance_contract"].get("outcome_requirements", {}).get("outcomes", [])


def _declared_outcome(acceptance: dict, declaration: dict) -> dict:
    state = acceptance["goal_acceptance_contract"].get("outcome_requirements", {})
    if state.get("current_lifetime") is not True:
        raise ValueError("Planning declaration belongs to a different Goal lifetime")
    for row in _outcomes(acceptance):
        if row["id"] == declaration["outcome_id"]:
            requirements = {item["id"]: item["description"] for item in row["requirements"]}
            expected = {item["id"]: item["description"] for item in declaration["requirements"]}
            if (row["owner_agent_id"] == declaration["owner_agent_id"]
                    and row["objective"] == declaration["objective"]
                    and requirements == expected):
                return row
            break
    raise ValueError("Planning declaration is missing or changed; inspect the current Goal")


def apply_initial_plan(env: dict[str, str], proposal: dict) -> dict:
    if env.get("LOOPX_TASK_STAGE") != "plan":
        raise ValueError("Planning batch requires the runner's planning stage")
    proposal = _request(proposal)
    from .worker import loopx_command

    cli = loopx_command(env)
    packet = task_plan_packet(env, cli)
    if proposal["input_digest"] != packet["input_digest"]:
        raise ValueError("Planning input changed")
    revision = proposal["expected_provider_revision"]
    acceptance = packet["goal_acceptance"]
    if packet["goal_waiting_on"] or packet["blocking_todo_ids"]:
        raise ValueError("Use the ordinary planning path for a Goal wait or blocker")

    def digest(value) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    # Bind all Todo intent to the declaration receipt. Two different proposals
    # for one input cannot replay the same declaration and mix their Todo writes.
    operation = f"benchmark-plan:{packet['input_digest']}"
    declaration = {
        **proposal["outcome"],
        "outcome_id": f"benchmark-plan-{digest(proposal)[:32]}",
        "owner_agent_id": packet["agent_id"],
    }
    if acceptance["provider_revision"] == revision:
        if packet["existing_todos"] or _outcomes(acceptance):
            raise ValueError("Use the ordinary planning path for existing work or requirements")
    else:
        _declared_outcome(acceptance, declaration)

    goal = ["--goal-id", packet["goal_id"]]
    actor = ["--agent-id", packet["agent_id"]]

    def call(*args: str) -> dict:
        process = subprocess.run(
            [*cli, *args], cwd=env["LOOPX_PROJECT"], env=env, text=True,
            capture_output=True, timeout=120,
        )
        try:
            result = json.loads(process.stdout)
        except ValueError:
            raise ValueError(f"LoopX {args[0]} {args[1]} returned no JSON receipt") from None
        if not isinstance(result, dict):
            raise ValueError(f"LoopX {args[0]} {args[1]} returned an invalid receipt")
        if process.returncode or result.get("ok") is not True:
            reason = result.get("error_code") or result.get("error") or "unknown"
            detail = result.get("error_detail")
            message = f"LoopX {args[0]} {args[1]} failed: {reason}"
            if isinstance(detail, str) and detail:
                message = f"{message}: {detail}"
            extra: dict[str, object] = {"stage": f"{args[0]} {args[1]}"}
            for key in ("error_code", "error_detail", "current_provider_revision"):
                value = result.get(key)
                if isinstance(value, str) and value:
                    extra[key] = value
            upstream = result.get("error")
            if isinstance(upstream, str) and upstream and upstream != reason:
                extra["upstream_error"] = upstream
            raise PlanningBatchCommandError(message, extra)
        return result

    with tempfile.TemporaryDirectory(prefix="loopx-planning-") as directory:
        document = Path(directory) / "refinement.json"

        def refine(value: dict, expected: str, effect: str) -> None:
            document.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            call("goal-acceptance", "refine", *goal, *actor, "--document", str(document),
                 "--expected-provider-revision", expected, "--operation-id", effect, "--execute")

        refine(declaration, revision, f"{operation}:declare")
        ids = []
        for index, item in enumerate(proposal["todos"]):
            written = call(
                "todo", "add", *goal, "--project", env["LOOPX_PROJECT"],
                "--role", "agent", "--claimed-by", packet["agent_id"],
                "--task-class", "advancement_task",
                *(f"--{key.replace('_', '-')}={value}" for key, value in item.items()),
                "--operation-id", f"{operation}:todo:{index}", "--execute",
            )
            todo_id = written.get("todo_id")
            if not isinstance(todo_id, str) or not todo_id or todo_id in ids:
                raise ValueError("Planning writer did not return distinct real Todo IDs")
            ids.append(todo_id)

        # Resolve the binding revision and check actual runnable ownership. A
        # historical add receipt can be valid even if its Todo changed later.
        current_packet = task_plan_packet(env, cli)
        if current_packet["input_digest"] != packet["input_digest"]:
            raise ValueError("Planning input changed before binding")
        if not set(ids).issubset(current_packet["runnable_todo_ids"]):
            raise ValueError("Planning Todos are no longer runnable by this agent")
        if current_packet["goal_waiting_on"] or current_packet["blocking_todo_ids"]:
            raise ValueError("Planning encountered a new Goal wait or blocker")
        current = current_packet["goal_acceptance"]
        linked = _declared_outcome(current, declaration)["todo_ids"]
        if linked and linked != ids:
            raise ValueError("Planning work links changed; inspect the current Goal")
        if not linked:
            basis = current["provider_revision"]
            refine({"outcome_id": declaration["outcome_id"], "todo_ids": ids},
                   basis, f"{operation}:bind:{digest(basis)[:32]}")
    return {"input_digest": packet["input_digest"], "status": "ready", "todo_ids": ids}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.runtime.planning_batch",
        description="Submit an initial planning batch proposal (outcome declaration + ordered advancement Todos).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Proposal JSON schema summary (text limits in UTF-16 code units; non-BMP emoji count as 2):\n"
            "  Required root fields: input_digest, expected_provider_revision, outcome, todos\n"
            f"  outcome.objective: non-empty string (max {_OBJECTIVE_MAX_CHARS} UTF-16 code units)\n"
            f"  outcome.requirements: 1..{_REQUIREMENTS_MAX_ITEMS} items of {{id, description}} "
            f"(description max {_REQUIREMENT_DESCRIPTION_MAX_CHARS} UTF-16 code units)\n"
            f"  outcome.source: {{ref, summary}} (summary max {_SOURCE_SUMMARY_MAX_CHARS} UTF-16 code units)\n"
            f"  Opaque IDs (outcome.requirements[*].id, outcome.source.ref): 1..{_OPAQUE_ID_MAX_CHARS} UTF-16 code units "
            f"matching {_OPAQUE_ID_PATTERN.pattern} (no '/' paths or spaces)\n"
            f"  todos: 1..{_TODOS_MAX_ITEMS} items of {{text, action_kind, target_key?}}\n"
            f"  action_kind: lowercase ASCII token matching {TODO_ACTION_KIND_PATTERN.pattern}\n\n"
            "Pass --schema to print the full machine-readable JSON schema and example."
        ),
    )
    parser.add_argument(
        "proposal",
        nargs="?",
        type=Path,
        help="Path to the planning proposal JSON file.",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print the complete JSON schema, field limits, and an example proposal, then exit 0.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(planning_batch_schema(), indent=2))
        return 0
    if args.proposal is None:
        parser.error("the following arguments are required: proposal (or pass --schema)")
    try:
        proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
        result = apply_initial_plan(dict(os.environ), proposal)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        if isinstance(exc, PlanningBatchCommandError) and exc.payload:
            payload.update(exc.payload)
        print(json.dumps(payload))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
