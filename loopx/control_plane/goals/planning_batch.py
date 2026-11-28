"""General initial planning batch owner shared by LoopX CLI, /loopx, and benchmark."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import NotRequired, Protocol, TypedDict, cast

from ...agent_registry import require_registered_agent_id
from ..todos.contract import (
    TODO_ACTION_KIND_PATTERN,
    TODO_TASK_CLASS_ADVANCEMENT,
    normalize_todo_action_kind,
)
from ..todos.mutation_api import add_goal_todo
from .acceptance import (
    GoalAcceptanceRejected,
    public_goal_acceptance,
    refine_goal_outcome,
)
from .task_planning import compute_task_planning_input_digest, inspect_goal_planning_frontier

PLANNING_BATCH_SCHEMA_VERSION = "loopx_planning_batch_proposal_v1"
TODO_PRIORITY_VALUES = ("P0", "P1", "P2", "P3", "P4")

OPAQUE_ID_MAX_CHARS = 128
OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]*$")
OPAQUE_ID_HINT = "ASCII letters/digits and . _ : @ -, no '/' paths or spaces"
OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
OPERATION_ID_HINT = "ASCII letters/digits and . _ : -, no '@', '/' paths, or spaces"
OBJECTIVE_MAX_CHARS = 1024
REQUIREMENT_DESCRIPTION_MAX_CHARS = 2000
REQUIREMENTS_MAX_ITEMS = 32
SOURCE_SUMMARY_MAX_CHARS = 512
TODOS_MAX_ITEMS = 64


def utf16_code_unit_len(value: str) -> int:
    """Count UTF-16 code units so Python limits match TypeScript String.length."""
    return len(value.encode("utf-16-le")) // 2


class PlanningBatchCommandError(ValueError):
    """Preserve structured LoopX owner rejection details without echoing values."""

    def __init__(self, message: str, payload: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.payload: dict[str, object] = dict(payload or {})


class PlanningFrontierSnapshot(TypedDict):
    goal_id: str
    agent_id: str
    input_digest: str | None
    existing_todos: list[dict[str, object]]
    goal_has_existing_todos: NotRequired[bool]
    runnable_todo_ids: list[str]
    blocking_todo_ids: list[str]
    goal_waiting_on: object
    goal_acceptance: dict[str, object]


class PlanningBatchBackend(Protocol):
    def inspect_state(self) -> PlanningFrontierSnapshot: ...

    def refine_outcome(
        self,
        value: dict[str, object],
        expected_provider_revision: str,
        operation_id: str,
        *,
        execute: bool,
    ) -> dict[str, object]: ...

    def add_todo(
        self,
        item: dict[str, str],
        operation_id: str,
        *,
        execute: bool,
    ) -> dict[str, object]: ...


def planning_batch_schema(
    *,
    schema_version: str = PLANNING_BATCH_SCHEMA_VERSION,
    require_input_digest: bool = False,
    allow_product_identities: bool = True,
    input_digest: str | None = None,
) -> dict[str, object]:
    """Return the complete self-contained JSON proposal schema and field rules."""
    required_fields = (
        ["input_digest", "expected_provider_revision", "outcome", "todos"]
        if require_input_digest
        else ["expected_provider_revision", "outcome", "todos"]
    )
    optional_root: list[str] = []
    if not require_input_digest:
        optional_root.append("input_digest")
    if allow_product_identities:
        optional_root.extend(["outcome_id", "operation_id"])
    todo_optional = ["target_key", "priority"] if allow_product_identities else ["target_key"]
    fields: dict[str, object] = {
        "input_digest": {
            "type": "string",
            "optional": not require_input_digest,
            "description": "Exact packet.input_digest when bound to a `loopx todo plan` checkpoint.",
        },
        "expected_provider_revision": {
            "type": "string",
            "description": "Exact goal_acceptance.provider_revision from `goal-acceptance inspect` or `todo plan`.",
        },
        "outcome": {
            "type": "object",
            "required_fields": ["objective", "requirements", "source"],
            "properties": {
                "objective": {
                    "type": "string",
                    "length_unit": "utf16_code_units",
                    "min_utf16_code_units": 1,
                    "max_utf16_code_units": OBJECTIVE_MAX_CHARS,
                },
                "requirements": {
                    "type": "array",
                    "min_items": 1,
                    "max_items": REQUIREMENTS_MAX_ITEMS,
                    "items": {
                        "required_fields": ["id", "description"],
                        "id": {
                            "type": "string",
                            "length_unit": "utf16_code_units",
                            "min_utf16_code_units": 1,
                            "max_utf16_code_units": OPAQUE_ID_MAX_CHARS,
                            "pattern": OPAQUE_ID_PATTERN.pattern,
                            "unique": True,
                        },
                        "description": {
                            "type": "string",
                            "length_unit": "utf16_code_units",
                            "min_utf16_code_units": 1,
                            "max_utf16_code_units": REQUIREMENT_DESCRIPTION_MAX_CHARS,
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
                        "max_utf16_code_units": OPAQUE_ID_MAX_CHARS,
                        "pattern": OPAQUE_ID_PATTERN.pattern,
                        "note": "Use an opaque identifier such as checkpoint:initial or task:readme; never file paths or spaces.",
                    },
                    "summary": {
                        "type": "string",
                        "length_unit": "utf16_code_units",
                        "min_utf16_code_units": 1,
                        "max_utf16_code_units": SOURCE_SUMMARY_MAX_CHARS,
                    },
                },
            },
        },
        "todos": {
            "type": "array",
            "min_items": 1,
            "max_items": TODOS_MAX_ITEMS,
            "items": {
                "required_fields": ["text", "action_kind"],
                "optional_fields": todo_optional,
                "text": {"type": "string", "min_utf16_code_units": 1, "unique": True},
                "action_kind": {
                    "type": "string",
                    "pattern": TODO_ACTION_KIND_PATTERN.pattern,
                    "examples": ["implement", "validate", "investigate", "submit"],
                },
                "target_key": {"type": "string", "optional": True, "min_utf16_code_units": 1},
                **(
                    {
                        "priority": {
                            "type": "string",
                            "optional": True,
                            "enum": list(TODO_PRIORITY_VALUES),
                        }
                    }
                    if allow_product_identities
                    else {}
                ),
            },
        },
    }
    if allow_product_identities:
        fields["outcome_id"] = {
            "type": "string",
            "optional": True,
            "length_unit": "utf16_code_units",
            "min_utf16_code_units": 1,
            "max_utf16_code_units": OPAQUE_ID_MAX_CHARS,
            "pattern": OPAQUE_ID_PATTERN.pattern,
            "description": "Optional stable outcome identifier; derived deterministically when omitted.",
        }
        fields["operation_id"] = {
            "type": "string",
            "optional": True,
            "length_unit": "utf16_code_units",
            "min_utf16_code_units": 1,
            "max_utf16_code_units": OPAQUE_ID_MAX_CHARS,
            "pattern": OPERATION_ID_PATTERN.pattern,
            "description": (
                "Optional stable batch operation prefix for idempotent retries "
                "(public-safe token without '@', '/', or spaces)."
            ),
        }
    example: dict[str, object] = {
        **({"input_digest": input_digest or "<packet.input_digest>"} if require_input_digest else {}),
        "expected_provider_revision": "<goal_acceptance.provider_revision>",
        "outcome": {
            "objective": "Concise task objective (<=1024 UTF-16 code units).",
            "requirements": [
                {
                    "id": "req-1",
                    "description": "Verifiable requirement description (<=2000 UTF-16 code units).",
                },
            ],
            "source": {
                "ref": "checkpoint:initial",
                "summary": "Host-supplied planning checkpoint (<=512 UTF-16 code units).",
            },
        },
        "todos": [
            {"text": "[P0] First ordered work step", "action_kind": "implement"},
            {"text": "[P1] Verify and test result", "action_kind": "validate", "target_key": "verification"},
        ],
    }
    return {
        "schema_version": schema_version,
        "length_unit": "utf16_code_units",
        "required_fields": required_fields,
        **({"optional_fields": optional_root} if optional_root else {}),
        "fields": fields,
        "example": example,
    }


def product_planning_batch_schema(
    *,
    registry_path: Path,
    goal_id: str | None = None,
    agent_id: str | None = None,
    text: str | None = None,
) -> dict[str, object]:
    """Return the general batch schema, binding input_digest when checkpoint --text is given."""
    if text is None:
        return planning_batch_schema()
    cleaned_text = str(text)
    if not cleaned_text.strip() or not goal_id or not agent_id:
        raise ValueError(
            "todo plan-batch --schema with --text requires --goal-id, --agent-id, and non-empty --text"
        )
    canonical_goal_id = goal_id.strip()
    canonical_agent_id = require_registered_agent_id(
        registry_path=registry_path,
        goal_id=canonical_goal_id,
        agent_id=agent_id,
        field="agent_id",
    )
    digest = compute_task_planning_input_digest(
        goal_id=canonical_goal_id,
        agent_id=canonical_agent_id,
        text=cleaned_text,
    )
    return planning_batch_schema(require_input_digest=True, input_digest=digest)


def render_planning_batch_guidance(
    command: str,
    *,
    schema_command: str | None = None,
    proposal_arg_template: str = "<proposal.json>",
    save_location_hint: str = "save a JSON proposal",
    require_input_digest: bool = False,
) -> str:
    """Render concise, self-contained batch planning instructions for prompts/checkpoints."""
    effective_schema_cmd = schema_command or f"{command} --schema"
    digest_clause = (
        '"input_digest": "<checkpoint.input_digest>", '
        if require_input_digest
        else ""
    )
    return (
        "Initial plan batch (optional for an empty work and requirements frontier). "
        f"After following the planner, {save_location_hint} with exact keys: "
        "{"
        + digest_clause
        + '"expected_provider_revision": "<checkpoint.goal_acceptance.provider_revision>", '
        '"outcome": {"objective": "<str <=1024 UTF-16 code units>", '
        '"requirements": [{"id": "<opaque-id>", "description": "<str <=2000 UTF-16 code units>"}], '
        '"source": {"ref": "<opaque-id>", "summary": "<str <=512 UTF-16 code units>"}}, '
        '"todos": [{"text": "<str>", "action_kind": "<lowercase token>", "target_key": "<optional str>"}]}. '
        "Constraint: bounded string limits are measured in UTF-16 code units (non-BMP emoji count as 2); "
        "outcome.source.ref, requirements[*].id, and optional outcome_id must be opaque ASCII IDs "
        f"(1..{OPAQUE_ID_MAX_CHARS} UTF-16 code units matching {OPAQUE_ID_PATTERN.pattern}, NO '/' paths or spaces, "
        'e.g. "checkpoint:initial"); optional operation_id must match '
        f"{OPERATION_ID_PATTERN.pattern} (no '@'); todos[*].action_kind must match ^[a-z][a-z0-9_-]{{0,63}}$ "
        "(e.g. implement, validate, investigate, submit); inspect full schema with "
        f"`{effective_schema_cmd}`. "
        f"Run {command} {proposal_arg_template} and return the successful planning result JSON directly "
        '(when it exits 0 with status="ready", do not make extra todo list or goal-acceptance calls). '
        "Keep the unchanged proposal on interruption and rerun the same command. For existing work, "
        "requirement revisions or blockers, follow the ordinary planning steps.\n\n"
    )


def _record(
    value: object,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
    *,
    path: str = "proposal",
) -> dict[str, object]:
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
    return cast(dict[str, object], value)


def _text(value: object, *, path: str = "field", max_chars: int | None = None) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"Planning proposal requires non-empty text at {path} (expected string without NUL)")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Planning proposal requires non-empty text at {path}")
    if max_chars is not None:
        units = utf16_code_unit_len(normalized)
        if units > max_chars:
            raise ValueError(
                f"Planning proposal field {path} exceeds max length (got {units} UTF-16 code units, max {max_chars})"
            )
    return normalized


def _opaque_id(value: object, *, path: str) -> str:
    text = _text(value, path=path)
    units = utf16_code_unit_len(text)
    too_long = units > OPAQUE_ID_MAX_CHARS
    invalid_chars = not OPAQUE_ID_PATTERN.match(text)
    if too_long or invalid_chars:
        reasons = []
        if too_long:
            reasons.append(f"got {units} UTF-16 code units, max {OPAQUE_ID_MAX_CHARS}")
        if invalid_chars:
            reasons.append(f"must match {OPAQUE_ID_PATTERN.pattern} ({OPAQUE_ID_HINT})")
        raise ValueError(f"Planning proposal field {path} must be an opaque identifier ({'; '.join(reasons)})")
    return text


def _operation_id(value: object, *, path: str = "operation_id") -> str:
    text = _text(value, path=path)
    units = utf16_code_unit_len(text)
    too_long = units > OPAQUE_ID_MAX_CHARS
    invalid_chars = not OPERATION_ID_PATTERN.match(text)
    if too_long or invalid_chars:
        reasons = []
        if too_long:
            reasons.append(f"got {units} UTF-16 code units, max {OPAQUE_ID_MAX_CHARS}")
        if invalid_chars:
            reasons.append(f"must match {OPERATION_ID_PATTERN.pattern} ({OPERATION_ID_HINT})")
        raise ValueError(
            f"Planning proposal field {path} must be a public-safe operation token ({'; '.join(reasons)})"
        )
    return text


def _action_kind(value: object, *, path: str) -> str:
    raw = _text(value, path=path)
    if not normalize_todo_action_kind(raw):
        raise ValueError(
            f"Planning proposal field {path} must match {TODO_ACTION_KIND_PATTERN.pattern} "
            "(lowercase ASCII token, e.g. implement, validate, investigate, submit)"
        )
    # Preserve raw casing in the normalized proposal dict so deterministic
    # outcome/operation digests stay identical across upgrades when Todo owners
    # normalize action_kind on commit.
    return raw


def _priority(value: object, *, path: str) -> str:
    raw = _text(value, path=path).upper()
    if raw not in TODO_PRIORITY_VALUES:
        raise ValueError(
            f"Planning proposal field {path} must be one of {', '.join(TODO_PRIORITY_VALUES)}"
        )
    return raw


def validate_planning_batch_proposal(
    proposal: object,
    *,
    require_input_digest: bool = False,
    allow_product_identities: bool = True,
) -> dict[str, object]:
    """Validate proposal structure, UTF-16 lengths, and opaque IDs before state IO."""
    required_root = {"expected_provider_revision", "outcome", "todos"}
    optional_root: set[str] = set()
    if require_input_digest:
        required_root.add("input_digest")
    else:
        optional_root.add("input_digest")
    if allow_product_identities:
        optional_root.update({"outcome_id", "operation_id"})
    root = _record(proposal, required_root, optional_root, path="proposal")
    outcome = _record(root["outcome"], {"objective", "requirements", "source"}, path="outcome")
    source = _record(outcome["source"], {"ref", "summary"}, path="outcome.source")
    raw_requirements, raw_todos = outcome["requirements"], root["todos"]
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise ValueError("Planning proposal needs at least one requirement in outcome.requirements")
    if len(raw_requirements) > REQUIREMENTS_MAX_ITEMS:
        raise ValueError(
            f"Planning proposal outcome.requirements exceeds capacity (got {len(raw_requirements)} items, max {REQUIREMENTS_MAX_ITEMS})"
        )
    if not isinstance(raw_todos, list) or not raw_todos:
        raise ValueError("Planning proposal needs at least one Todo in todos")
    if len(raw_todos) > TODOS_MAX_ITEMS:
        raise ValueError(
            f"Planning proposal todos exceeds capacity (got {len(raw_todos)} items, max {TODOS_MAX_ITEMS})"
        )
    requirements: list[dict[str, str]] = []
    for index, item in enumerate(raw_requirements):
        row = _record(item, {"id", "description"}, path=f"outcome.requirements[{index}]")
        requirements.append({
            "id": _opaque_id(row["id"], path=f"outcome.requirements[{index}].id"),
            "description": _text(
                row["description"],
                path=f"outcome.requirements[{index}].description",
                max_chars=REQUIREMENT_DESCRIPTION_MAX_CHARS,
            ),
        })
    todo_optional = {"target_key", "priority"} if allow_product_identities else {"target_key"}
    todos: list[dict[str, str]] = []
    for index, todo in enumerate(raw_todos):
        row = _record(todo, {"text", "action_kind"}, todo_optional, path=f"todos[{index}]")
        todo_item: dict[str, str] = {
            "text": _text(row["text"], path=f"todos[{index}].text"),
            "action_kind": _action_kind(row["action_kind"], path=f"todos[{index}].action_kind"),
        }
        if "target_key" in row:
            todo_item["target_key"] = _text(row["target_key"], path=f"todos[{index}].target_key")
        if "priority" in row:
            todo_item["priority"] = _priority(row["priority"], path=f"todos[{index}].priority")
        todos.append(todo_item)
    if len({item["id"] for item in requirements}) != len(requirements):
        raise ValueError("Planning requirement IDs in outcome.requirements must be unique")
    if len({todo["text"] for todo in todos}) != len(todos):
        raise ValueError("Planning Todos in todos must have distinct text")
    normalized: dict[str, object] = {
        "expected_provider_revision": _text(
            root["expected_provider_revision"], path="expected_provider_revision"
        ),
        "outcome": {
            "objective": _text(outcome["objective"], path="outcome.objective", max_chars=OBJECTIVE_MAX_CHARS),
            "requirements": requirements,
            "source": {
                "ref": _opaque_id(source["ref"], path="outcome.source.ref"),
                "summary": _text(
                    source["summary"], path="outcome.source.summary", max_chars=SOURCE_SUMMARY_MAX_CHARS
                ),
            },
        },
        "todos": todos,
    }
    if "input_digest" in root:
        normalized["input_digest"] = _text(root["input_digest"], path="input_digest")
    if "outcome_id" in root:
        normalized["outcome_id"] = _opaque_id(root["outcome_id"], path="outcome_id")
    if "operation_id" in root:
        normalized["operation_id"] = _operation_id(root["operation_id"], path="operation_id")
    return normalized


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _outcomes(acceptance: Mapping[str, object]) -> list[dict[str, object]]:
    contract = acceptance.get("goal_acceptance_contract")
    if not isinstance(contract, Mapping):
        return []
    req_block = contract.get("outcome_requirements")
    if not isinstance(req_block, Mapping):
        return []
    outcomes = req_block.get("outcomes")
    return [cast(dict[str, object], row) for row in outcomes] if isinstance(outcomes, list) else []


def _declared_outcome(acceptance: Mapping[str, object], declaration: Mapping[str, object]) -> dict[str, object]:
    contract = acceptance.get("goal_acceptance_contract")
    state = contract.get("outcome_requirements") if isinstance(contract, Mapping) else None
    if not isinstance(state, Mapping) or state.get("current_lifetime") is not True:
        raise ValueError("Planning declaration belongs to a different Goal lifetime")
    expected_reqs = {
        str(item["id"]): str(item["description"])
        for item in cast(list[dict[str, str]], declaration["requirements"])
    }
    for row in _outcomes(acceptance):
        if row.get("id") == declaration["outcome_id"]:
            raw_reqs = row.get("requirements")
            requirements = (
                {str(item["id"]): str(item["description"]) for item in raw_reqs if isinstance(item, Mapping)}
                if isinstance(raw_reqs, list)
                else {}
            )
            if (
                row.get("owner_agent_id") == declaration["owner_agent_id"]
                and row.get("objective") == declaration["objective"]
                and requirements == expected_reqs
            ):
                return row
            break
    raise ValueError("Planning declaration is missing or changed; inspect the current Goal")


class DirectPlanningBatchBackend:
    """In-process product backend backed directly by canonical Goal and Todo owners."""

    def __init__(
        self,
        *,
        registry_path: Path,
        runtime_root_arg: str | None,
        goal_id: str,
        agent_id: str,
        project: Path | None = None,
        text: str | None = None,
    ) -> None:
        self.registry_path = registry_path
        self.runtime_root_arg = runtime_root_arg
        self.goal_id = goal_id.strip()
        self.agent_id = agent_id.strip()
        self.project = project
        self.text = text

    def inspect_state(self) -> PlanningFrontierSnapshot:
        frontier = inspect_goal_planning_frontier(
            registry_path=self.registry_path,
            runtime_root_arg=self.runtime_root_arg,
            goal_id=self.goal_id,
            agent_id=self.agent_id,
            project=self.project,
        )
        canonical_agent_id = str(frontier["agent_id"])
        self.agent_id = canonical_agent_id
        input_digest: str | None = None
        if self.text is not None:
            if not self.text.strip():
                raise ValueError("todo plan-batch requires non-empty --text when supplied")
            input_digest = compute_task_planning_input_digest(
                goal_id=self.goal_id,
                agent_id=canonical_agent_id,
                text=self.text,
            )
        return {
            "goal_id": self.goal_id,
            "agent_id": canonical_agent_id,
            "input_digest": input_digest,
            "existing_todos": cast(list[dict[str, object]], frontier["existing_todos"]),
            "goal_has_existing_todos": bool(frontier["goal_has_existing_todos"]),
            "runnable_todo_ids": cast(list[str], frontier["runnable_todo_ids"]),
            "blocking_todo_ids": cast(list[str], frontier["blocking_todo_ids"]),
            "goal_waiting_on": frontier["goal_waiting_on"],
            "goal_acceptance": cast(dict[str, object], frontier["goal_acceptance"]),
        }

    def refine_outcome(
        self,
        value: dict[str, object],
        expected_provider_revision: str,
        operation_id: str,
        *,
        execute: bool,
    ) -> dict[str, object]:
        try:
            raw = refine_goal_outcome(
                registry_path=self.registry_path,
                runtime_root=self.runtime_root_arg,
                goal_id=self.goal_id,
                agent_id=self.agent_id,
                refinement=dict(value),
                expected_provider_revision=expected_provider_revision,
                operation_id=operation_id,
                execute=execute,
            )
            return public_goal_acceptance(raw)
        except GoalAcceptanceRejected as exc:
            detail = exc.payload.get("error_detail")
            message = f"LoopX goal-acceptance refine failed: {exc.code}"
            if isinstance(detail, str) and detail:
                message = f"{message}: {detail}"
            raise PlanningBatchCommandError(
                message,
                {"stage": "goal-acceptance refine", "error_code": exc.code, **exc.payload},
            ) from None
        except ValueError as exc:
            code = str(exc)
            raise PlanningBatchCommandError(
                f"LoopX goal-acceptance refine failed: {code}",
                {"stage": "goal-acceptance refine", "error_code": code},
            ) from None

    def add_todo(
        self,
        item: dict[str, str],
        operation_id: str,
        *,
        execute: bool,
    ) -> dict[str, object]:
        monitor_meta = {"target_key": item["target_key"]} if "target_key" in item else None
        result = add_goal_todo(
            registry_path=self.registry_path,
            runtime_root_arg=self.runtime_root_arg,
            goal_id=self.goal_id,
            role="agent",
            claimed_by=self.agent_id,
            agent_id=None,
            task_class=TODO_TASK_CLASS_ADVANCEMENT,
            text=item["text"],
            action_kind=item["action_kind"],
            priority=item.get("priority"),
            monitor_metadata=monitor_meta,
            project=self.project,
            dry_run=not execute,
            operation_id=operation_id,
        )
        if result.get("ok") is not True:
            reason = str(result.get("error_code") or result.get("error") or "unknown")
            raise PlanningBatchCommandError(
                f"LoopX todo add failed: {reason}",
                {"stage": "todo add", "error_code": reason},
            )
        return result


class CliPlanningBatchBackend:
    """Routed subprocess CLI backend for callers executing through an external LOOPX_CLI."""

    def __init__(
        self,
        *,
        cli: list[str],
        cwd: str | Path,
        env: Mapping[str, str],
        packet_reader: Callable[[], dict[str, object]],
        project: str,
    ) -> None:
        self.cli = list(cli)
        self.cwd = str(cwd)
        self.env = dict(env)
        self.packet_reader = packet_reader
        self.project = project
        self._goal_id = ""
        self._agent_id = ""

    def inspect_state(self) -> PlanningFrontierSnapshot:
        packet = self.packet_reader()
        self._goal_id = str(packet["goal_id"])
        self._agent_id = str(packet["agent_id"])
        raw_digest = packet.get("input_digest")
        batch_meta = packet.get("planning_batch")
        existing_todos = cast(list[dict[str, object]], packet.get("existing_todos") or [])
        goal_has_existing_todos = (
            bool(batch_meta["goal_has_existing_todos"])
            if isinstance(batch_meta, Mapping) and "goal_has_existing_todos" in batch_meta
            else bool(existing_todos)
        )
        return {
            "goal_id": self._goal_id,
            "agent_id": self._agent_id,
            "input_digest": str(raw_digest) if isinstance(raw_digest, str) else None,
            "existing_todos": existing_todos,
            "goal_has_existing_todos": goal_has_existing_todos,
            "runnable_todo_ids": cast(list[str], packet.get("runnable_todo_ids") or []),
            "blocking_todo_ids": cast(list[str], packet.get("blocking_todo_ids") or []),
            "goal_waiting_on": packet.get("goal_waiting_on"),
            "goal_acceptance": cast(dict[str, object], packet["goal_acceptance"]),
        }

    def _call(self, *args: str) -> dict[str, object]:
        from ...extensions.process_runtime import no_console_creationflags

        process = subprocess.run(
            [*self.cli, *args],
            cwd=self.cwd,
            env=self.env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=120,
            creationflags=no_console_creationflags(),
        )
        try:
            result = json.loads(process.stdout)
        except ValueError:
            raise ValueError(f"LoopX {args[0]} {args[1]} returned no JSON receipt") from None
        if not isinstance(result, dict):
            raise ValueError(f"LoopX {args[0]} {args[1]} returned an invalid receipt")
        if process.returncode or result.get("ok") is not True:
            reason = str(result.get("error_code") or result.get("error") or "unknown")
            detail = result.get("error_detail")
            message = f"LoopX {args[0]} {args[1]} failed: {reason}"
            if isinstance(detail, str) and detail:
                message = f"{message}: {detail}"
            extra: dict[str, object] = {"stage": f"{args[0]} {args[1]}"}
            for key in ("error_code", "error_detail", "current_provider_revision"):
                val = result.get(key)
                if isinstance(val, str) and val:
                    extra[key] = val
            upstream = result.get("error")
            if isinstance(upstream, str) and upstream and upstream != reason:
                extra["upstream_error"] = upstream
            raise PlanningBatchCommandError(message, extra)
        return cast(dict[str, object], result)

    def refine_outcome(
        self,
        value: dict[str, object],
        expected_provider_revision: str,
        operation_id: str,
        *,
        execute: bool,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory(prefix="loopx-planning-") as directory:
            document = Path(directory) / "refinement.json"
            document.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            argv = [
                "goal-acceptance",
                "refine",
                "--goal-id",
                self._goal_id,
                "--agent-id",
                self._agent_id,
                "--document",
                str(document),
                "--expected-provider-revision",
                expected_provider_revision,
                "--operation-id",
                operation_id,
            ]
            if execute:
                argv.append("--execute")
            return self._call(*argv)

    def add_todo(
        self,
        item: dict[str, str],
        operation_id: str,
        *,
        execute: bool,
    ) -> dict[str, object]:
        argv = [
            "todo",
            "add",
            "--goal-id",
            self._goal_id,
            "--project",
            self.project,
            "--role",
            "agent",
            "--claimed-by",
            self._agent_id,
            "--task-class",
            TODO_TASK_CLASS_ADVANCEMENT,
            *(f"--{key.replace('_', '-')}={val}" for key, val in item.items()),
            "--operation-id",
            operation_id,
        ]
        argv.append("--execute" if execute else "--dry-run")
        return self._call(*argv)


def apply_planning_batch(
    *,
    proposal: Mapping[str, object] | object,
    backend: PlanningBatchBackend,
    execute: bool = True,
    require_input_digest: bool = False,
    allow_product_identities: bool = True,
    id_prefix: str = "plan",
    operation_id: str | None = None,
) -> dict[str, object]:
    """Validate, preview or commit an initial outcome + Todo planning batch."""
    validated = validate_planning_batch_proposal(
        proposal,
        require_input_digest=require_input_digest,
        allow_product_identities=allow_product_identities,
    )
    snapshot = backend.inspect_state()
    expected_digest = snapshot.get("input_digest")
    proposal_digest_field = validated.get("input_digest")
    if expected_digest is not None:
        if proposal_digest_field is not None and proposal_digest_field != expected_digest:
            raise ValueError("Planning input changed")
        if require_input_digest and proposal_digest_field != expected_digest:
            raise ValueError("Planning input changed")
    elif proposal_digest_field is not None:
        raise ValueError(
            "Planning proposal includes input_digest, but caller did not supply --text to verify it"
        )

    revision = str(validated["expected_provider_revision"])
    acceptance = snapshot["goal_acceptance"]

    safe_prefix = _operation_id(id_prefix, path="id_prefix")
    explicit_op = _operation_id(operation_id, path="operation_id") if operation_id is not None else None
    proposal_hash = _digest(validated)
    outcome_id = str(
        validated.get("outcome_id") or f"{safe_prefix}-{proposal_hash[:32]}"
    )
    effective_operation = str(
        explicit_op
        or validated.get("operation_id")
        or (f"{safe_prefix}:{expected_digest}" if expected_digest else f"{safe_prefix}:{proposal_hash[:32]}")
    )
    # When outcome_id is explicit, bind the declaration receipt to the complete
    # normalized proposal digest (ordered Todos + revision + input binding) so
    # an interrupted retry cannot mutate remaining Todos under the same batch.
    declare_operation_id = (
        f"{effective_operation}:declare:{proposal_hash[:32]}"
        if "outcome_id" in validated
        else f"{effective_operation}:declare"
    )
    outcome_body = cast(dict[str, object], validated["outcome"])
    declaration: dict[str, object] = {
        **outcome_body,
        "outcome_id": outcome_id,
        "owner_agent_id": snapshot["agent_id"],
    }
    current_rev = str(acceptance["provider_revision"])
    initial_linked_ids: list[str] = []
    if current_rev == revision:
        if snapshot["goal_waiting_on"] or snapshot["blocking_todo_ids"]:
            raise ValueError("Use the ordinary planning path for a Goal wait or blocker")
        if (
            snapshot["existing_todos"]
            or snapshot.get("goal_has_existing_todos")
            or _outcomes(acceptance)
        ):
            raise ValueError("Use the ordinary planning path for existing work or requirements")
    else:
        initial_row = _declared_outcome(acceptance, declaration)
        raw_initial_linked = initial_row.get("todo_ids")
        initial_linked_ids = (
            [str(x) for x in raw_initial_linked]
            if isinstance(raw_initial_linked, list)
            else []
        )
        if not initial_linked_ids and (
            snapshot["goal_waiting_on"] or snapshot["blocking_todo_ids"]
        ):
            raise ValueError("Use the ordinary planning path for a Goal wait or blocker")

    todos = cast(list[dict[str, str]], validated["todos"])
    if not execute or current_rev == revision:
        preview_acceptance = backend.refine_outcome(
            declaration, revision, declare_operation_id, execute=False
        )
        if current_rev == revision:
            for index, item in enumerate(todos):
                backend.add_todo(
                    item, f"{effective_operation}:todo:{index}:preview", execute=False
                )
    if not execute:
        return {
            "ok": True,
            "command": "plan-batch",
            "status": "planned",
            "dry_run": True,
            "changed": False,
            "goal_id": snapshot["goal_id"],
            "agent_id": snapshot["agent_id"],
            "outcome_id": outcome_id,
            "operation_id": effective_operation,
            "provider_revision": current_rev,
            "planned_todo_count": len(todos),
            "goal_acceptance_contract": preview_acceptance.get("goal_acceptance_contract"),
            **({"input_digest": expected_digest} if expected_digest is not None else {}),
        }

    backend.refine_outcome(declaration, revision, declare_operation_id, execute=True)
    ids: list[str] = []
    for index, item in enumerate(todos):
        written = backend.add_todo(item, f"{effective_operation}:todo:{index}", execute=True)
        todo_id = written.get("todo_id")
        if not isinstance(todo_id, str) or not todo_id or todo_id in ids:
            raise ValueError("Planning writer did not return distinct real Todo IDs")
        ids.append(todo_id)

    current_snapshot = backend.inspect_state()
    if expected_digest is not None and current_snapshot.get("input_digest") != expected_digest:
        raise ValueError("Planning input changed before binding")
    current_acceptance = current_snapshot["goal_acceptance"]
    declared_row = _declared_outcome(current_acceptance, declaration)
    linked = declared_row.get("todo_ids")
    linked_ids = [str(x) for x in linked] if isinstance(linked, list) else []
    if linked_ids and linked_ids != ids:
        raise ValueError("Planning work links changed; inspect the current Goal")
    already_bound = current_rev != revision and linked_ids == ids
    final_acceptance = current_acceptance
    if not already_bound:
        if not set(ids).issubset(current_snapshot["runnable_todo_ids"]):
            raise ValueError("Planning Todos are no longer runnable by this agent")
        if current_snapshot["goal_waiting_on"] or current_snapshot["blocking_todo_ids"]:
            raise ValueError("Planning encountered a new Goal wait or blocker")
        basis = str(current_acceptance["provider_revision"])
        final_acceptance = backend.refine_outcome(
            {"outcome_id": outcome_id, "todo_ids": ids},
            basis,
            f"{effective_operation}:bind:{_digest(basis)[:32]}",
            execute=True,
        )

    runnable_set = set(current_snapshot["runnable_todo_ids"])
    batch_runnable_ids = [tid for tid in ids if tid in runnable_set]
    final_rev = str(final_acceptance.get("provider_revision") or current_acceptance["provider_revision"])
    return {
        "ok": True,
        "command": "plan-batch",
        "status": "ready",
        "dry_run": False,
        "changed": not already_bound,
        "idempotent_replay": already_bound,
        "goal_id": snapshot["goal_id"],
        "agent_id": snapshot["agent_id"],
        "outcome_id": outcome_id,
        "operation_id": effective_operation,
        "todo_ids": ids,
        "runnable_todo_ids": batch_runnable_ids,
        "all_todos_runnable": len(batch_runnable_ids) == len(ids),
        "provider_revision": final_rev,
        "state_readback_verified": True,
        "execution_owner": "caller",
        "goal_acceptance_contract": final_acceptance.get("goal_acceptance_contract"),
        **({"input_digest": expected_digest} if expected_digest is not None else {}),
    }


def render_planning_batch_markdown(payload: Mapping[str, object]) -> str:
    if payload.get("schema_version") == PLANNING_BATCH_SCHEMA_VERSION:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    lines = ["# Todo plan-batch", "", f"- ok: {payload.get('ok')}"]
    if payload.get("error"):
        lines.append(f"- error: {payload['error']}")
        if payload.get("error_detail"):
            lines.append(f"- detail: {payload['error_detail']}")
        return "\n".join(lines)
    lines.extend([
        f"- status: {payload.get('status')}",
        f"- goal_id: {payload.get('goal_id')}",
        f"- agent_id: {payload.get('agent_id')}",
        f"- outcome_id: {payload.get('outcome_id')}",
        f"- provider_revision: {payload.get('provider_revision')}",
    ])
    todo_ids = payload.get("todo_ids")
    if isinstance(todo_ids, list) and todo_ids:
        lines.append(f"- todo_ids: {', '.join(str(item) for item in todo_ids)}")
        runnable_ids = payload.get("runnable_todo_ids")
        if isinstance(runnable_ids, list):
            rendered_runnable = ", ".join(str(item) for item in runnable_ids) if runnable_ids else "none"
            lines.append(f"- runnable_todo_ids: {rendered_runnable}")
    elif payload.get("planned_todo_count") is not None:
        lines.append(f"- planned_todo_count: {payload['planned_todo_count']}")
    return "\n".join(lines)
