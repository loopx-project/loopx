"""Turn-context hook integration for anti-entropy code governance."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...control_plane.capability_hooks import TurnContextHookRegistration
from .contract import ANTI_ENTROPY_CAPABILITY_ID, RetirementBasis
from .feedback import build_anti_entropy_turn_context

ANTI_ENTROPY_CONTEXT_MAX_RESULT_BYTES = 64 * 1024
_BASIS_KEYS = ("retirement_basis", "anti_entropy_basis")
_BASIS_CONTAINER_KEYS = ("metadata", "params", "task_spec", "task_parameters")
_TEXT_CARRIER_KEYS = (
    "note",
    "text",
    "description",
    "instruction",
    "evidence",
    "rationale",
)
_ENABLE_CAPABILITY_IDS = frozenset(
    {"anti-entropy-governance", "anti_entropy_governance", "anti_entropy"}
)
_UNSET = object()


def _embedded_json_objects(text: str):
    """Yield JSON objects embedded in free text, outermost first.

    Task instructions and notes are plain strings; an explicit retirement
    declaration may sit inside surrounding prose. A balanced scan is used
    instead of assuming the whole string is JSON, and a failed outer parse
    keeps scanning for an inner declaration.
    """
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "{":
            index += 1
            continue
        depth = 0
        in_string = False
        escaped = False
        cursor = index
        parsed: Any = None
        while cursor < length:
            char = text[cursor]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[index : cursor + 1])
                    except Exception:
                        parsed = None
                    break
            cursor += 1
        if isinstance(parsed, Mapping):
            yield parsed
            index = cursor + 1
        else:
            index += 1


def _extract_basis_candidate(candidate: Any) -> RetirementBasis | None:
    """Recursively search a task carrier for an explicit retirement basis."""
    if candidate is None:
        return None
    if isinstance(candidate, RetirementBasis):
        return (
            candidate
            if (candidate.target_symbols or candidate.target_paths)
            else None
        )
    if isinstance(candidate, str):
        for embedded in _embedded_json_objects(candidate):
            parsed = _extract_basis_candidate(embedded)
            if parsed is not None:
                return parsed
        return None
    if not isinstance(candidate, Mapping):
        return None
    for key in _BASIS_KEYS:
        if key in candidate:
            parsed = _extract_basis_candidate(candidate[key])
            if parsed is not None:
                return parsed
    anti_entropy = candidate.get("anti_entropy")
    if isinstance(anti_entropy, Mapping):
        parsed = _extract_basis_candidate(anti_entropy)
        if parsed is not None:
            return parsed
    for container_key in _BASIS_CONTAINER_KEYS:
        if container_key in candidate:
            parsed = _extract_basis_candidate(candidate[container_key])
            if parsed is not None:
                return parsed
    # A dedicated basis object (not arbitrary task metadata) carries the
    # declared targets directly.
    if "target_symbols" in candidate or "target_paths" in candidate:
        try:
            parsed = RetirementBasis.from_dict(candidate)
        except Exception:
            parsed = None
        if parsed is not None and (parsed.target_symbols or parsed.target_paths):
            return parsed
    for text_key in _TEXT_CARRIER_KEYS:
        text_value = candidate.get(text_key)
        if isinstance(text_value, str):
            parsed = _extract_basis_candidate(text_value)
            if parsed is not None:
                return parsed
    return None


def _work_context_sources(container: Any) -> list[Mapping[str, Any]]:
    """Return the bounded work-context sources from a payload or plan owner."""
    if not isinstance(container, Mapping):
        return []
    contexts: list[Mapping[str, Any]] = []
    direct = container.get("work_context")
    if isinstance(direct, Mapping):
        contexts.append(direct)
    channel = container.get("agent_channel")
    if isinstance(channel, Mapping):
        nested = channel.get("work_context")
        if isinstance(nested, Mapping):
            contexts.append(nested)
    sources: list[Mapping[str, Any]] = []
    for context in contexts:
        for source in context.get("sources") or ():
            if isinstance(source, Mapping):
                sources.append(source)
    return sources


def _selected_todo_records(
    payload: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Collect every native selected-Todo projection, richest form first."""
    records: list[Mapping[str, Any]] = []

    def add(value: Any) -> None:
        if isinstance(value, Mapping):
            records.append(value)

    envelope = payload.get("turn_envelope")
    if isinstance(envelope, Mapping):
        action = envelope.get("action")
        if isinstance(action, Mapping):
            add(action.get("selected_todo"))
        add(envelope.get("selected_todo"))
        for source in _work_context_sources(envelope):
            content = source.get("content")
            if isinstance(content, Mapping):
                add(content.get("todo"))
    route = payload.get("route")
    if isinstance(route, Mapping):
        add(route.get("selected_todo"))
    add(payload.get("selected_todo"))
    add(payload.get("task"))
    if isinstance(decision, Mapping):
        add(decision.get("selected_todo"))
        action = decision.get("action")
        if isinstance(action, Mapping):
            add(action.get("selected_todo"))
        route = decision.get("route")
        if isinstance(route, Mapping):
            add(route.get("selected_todo"))
        for container in (decision, decision.get("interaction_contract")):
            for source in _work_context_sources(container):
                content = source.get("content")
                if isinstance(content, Mapping):
                    add(content.get("todo"))
    return records


def _resolve_retirement_basis(
    payload: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> RetirementBasis | None:
    """Extract a declared retirement basis from the authoritative task input.

    The native Turn carries the selected Todo as a compact projection plus the
    bounded work context. The task's own instruction/note text and the
    recommended action are the intent sources; a hand-built top-level payload
    key is accepted for direct callers but is never the only source.
    """
    candidates: list[Any] = [
        payload.get("retirement_basis"),
        payload.get("anti_entropy_basis"),
        payload.get("anti_entropy"),
        decision.get("retirement_basis"),
        decision.get("anti_entropy_basis"),
        decision.get("anti_entropy"),
        payload.get("route", {}).get("selected_todo")
        if isinstance(payload.get("route"), Mapping)
        else None,
        payload.get("turn_envelope", {}).get("action", {}).get("selected_todo")
        if isinstance(payload.get("turn_envelope"), Mapping)
        and isinstance(payload.get("turn_envelope", {}).get("action"), Mapping)
        else None,
        payload.get("selected_todo"),
        payload.get("task"),
        decision.get("selected_todo"),
        decision.get("action", {}).get("selected_todo")
        if isinstance(decision.get("action"), Mapping)
        else None,
        decision.get("route", {}).get("selected_todo")
        if isinstance(decision.get("route"), Mapping)
        else None,
        decision.get("task"),
    ]
    for candidate in candidates:
        basis = _extract_basis_candidate(candidate)
        if basis is not None:
            return basis
    envelope = payload.get("turn_envelope")
    if isinstance(envelope, Mapping):
        action = envelope.get("action")
        if isinstance(action, Mapping):
            for text_key in ("recommended_action", "text"):
                basis = _extract_basis_candidate(action.get(text_key))
                if basis is not None:
                    return basis
    for record in _selected_todo_records(payload, decision):
        basis = _extract_basis_candidate(record)
        if basis is not None:
            return basis
    return None


def _load_goal(registry_path: Path | str | None, goal_id: str | None) -> Mapping[str, Any] | None:
    if registry_path is None or not goal_id:
        return None
    try:
        from ...agent_registry import load_goal_from_registry

        return load_goal_from_registry(Path(registry_path), goal_id)
    except Exception:  # noqa: BLE001 - optional read-only enablement hint
        return None


def _declared_capability_ids(goal: Mapping[str, Any]) -> set[str]:
    declared: set[str] = set()
    for container in (
        goal,
        goal.get("control_plane"),
        goal.get("coordination"),
    ):
        if not isinstance(container, Mapping):
            continue
        for key in ("available_capabilities", "required_capabilities"):
            value = container.get(key)
            if isinstance(value, (list, tuple, set)):
                declared.update(str(item).strip() for item in value if isinstance(item, str))
    return declared


def _record_required_capabilities(record: Mapping[str, Any]) -> set[str]:
    value = record.get("required_capabilities")
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if isinstance(item, str)}
    return set()


def _is_anti_entropy_enabled(
    payload: Mapping[str, Any],
    decision: Mapping[str, Any],
    basis: RetirementBasis | None,
    *,
    registry_path: Path | str | None = None,
    goal_id: str | None = None,
) -> bool:
    """Determine whether anti-entropy is enabled for this turn.

    A declared retirement basis is the task-scoped opt-in. The explicit
    capability id in the selected Todo's required capabilities and a Goal-level
    available-capability declaration are the typed enablement paths.
    """
    if basis is not None and (basis.target_symbols or basis.target_paths):
        return True

    if payload.get("anti_entropy_enabled") is True or decision.get("anti_entropy_enabled") is True:
        return True
    for owner in (payload, decision):
        value = owner.get("anti_entropy")
        if isinstance(value, Mapping) and value.get("enabled") is True:
            return True
    route = payload.get("route")
    if isinstance(route, Mapping) and route.get("anti_entropy_enabled") is True:
        return True
    envelope = payload.get("turn_envelope")
    if isinstance(envelope, Mapping):
        boundary = envelope.get("boundary")
        if isinstance(boundary, Mapping):
            capabilities = boundary.get("capabilities")
            if isinstance(capabilities, Mapping):
                capability = capabilities.get("anti_entropy_governance") or capabilities.get(
                    "anti_entropy"
                )
                if isinstance(capability, Mapping) and capability.get("enabled") is True:
                    return True
                if capability is True:
                    return True

    for record in _selected_todo_records(payload, decision):
        if record.get("anti_entropy_enabled") is True:
            return True
        for key in ("metadata", "params"):
            value = record.get(key)
            if isinstance(value, Mapping) and value.get("anti_entropy_enabled") is True:
                return True
        if _record_required_capabilities(record) & _ENABLE_CAPABILITY_IDS:
            return True

    goal = _load_goal(registry_path, goal_id)
    if goal is not None and _declared_capability_ids(goal) & _ENABLE_CAPABILITY_IDS:
        return True
    return False


def _resolve_workspace(
    payload: Mapping[str, Any],
    *,
    registry_path: Path | str | None,
    goal_id: str | None,
) -> Path | None:
    """Resolve the post-change workspace from the task or the Goal registry.

    The process working directory is never used: an unrelated checkout must not
    change the verdict for the same patch.
    """
    for key in ("workspace", "repo_path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if path.is_dir():
                return path.resolve()
    goal = _load_goal(registry_path, goal_id)
    repo = str((goal or {}).get("repo") or "").strip()
    if repo:
        path = Path(repo).expanduser()
        if path.is_dir():
            return path.resolve()
    return None


def anti_entropy_context_result(
    payload: Mapping[str, Any],
    *,
    goal_id: str,
    decision: Mapping[str, Any],
    workspace: Path | None = None,
    resolved_basis: Any = _UNSET,
    enabled: Any = _UNSET,
) -> dict[str, Any]:
    """Produce the bounded turn-context payload covering the four lifecycle states.

    1. Disabled: no readback slot and no injection.
    2. Enabled but not applicable: capability is active but the task has no
       retirement targets.
    3. New task: declared targets, the inspect descriptor, and delete-first
       guidance are projected into host input.
    4. Failure recovery: the previous inspect outcome in the payload produces
       remediation guidance.
    """
    basis = (
        _resolve_retirement_basis(payload, decision)
        if resolved_basis is _UNSET
        else resolved_basis
    )
    enabled_value = bool(
        _is_anti_entropy_enabled(payload, decision, basis)
        if enabled is _UNSET
        else enabled
    )

    if not enabled_value:
        return {
            "readback": {},
            "context_fields": {},
            "instructions": [],
            "result_fields": {},
        }

    if basis is None or (not basis.target_symbols and not basis.target_paths):
        return {
            "readback": {
                "anti_entropy_guidance": {
                    "enabled": True,
                    "applicable": False,
                }
            },
            "context_fields": {},
            "instructions": [],
            "result_fields": {},
        }

    recovery_evidence = (
        payload.get("anti_entropy_progress_fact")
        or payload.get("anti_entropy_last_result")
        or decision.get("anti_entropy_last_result")
    )
    is_recovery = bool(recovery_evidence)

    context = build_anti_entropy_turn_context(
        basis=basis,
        goal_id=goal_id,
        workspace=workspace,
    )

    if is_recovery:
        instructions = [
            "Anti-entropy recovery: the previous diff check identified unretired symbols "
            "or diff hygiene issues. Address blocking findings before attempting delivery.",
            "Run 'anti_entropy_inspect' or 'loopx anti-entropy inspect' to verify diff "
            "cleanliness; rejections are progress facts guiding the next correction.",
        ]
        status = "recovering"
    else:
        instructions = [
            "Anti-entropy code governance is active for this task: delete-first symbol "
            "and path retirement is required.",
            "Call 'anti_entropy_inspect' or 'loopx anti-entropy inspect' to check diff "
            "cleanliness; blocking findings are progress facts to fix before delivery.",
        ]
        status = "active"

    readback = {
        "anti_entropy_guidance": {
            "enabled": True,
            "applicable": True,
            "target_symbols": list(basis.target_symbols),
            "target_paths": list(basis.target_paths),
            "status": status,
            "workspace_available": workspace is not None,
        }
    }

    full_result = {
        "readback": readback,
        "context_fields": {"anti_entropy_context": context},
        "instructions": instructions,
        "result_fields": {},
    }

    encoded = json.dumps(full_result, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > ANTI_ENTROPY_CONTEXT_MAX_RESULT_BYTES:
        compact_context = dict(context)
        compact_context.pop("inspect_tool", None)
        return {
            "readback": readback,
            "context_fields": {"anti_entropy_context": compact_context},
            "instructions": instructions,
            "result_fields": {},
        }

    return full_result


def anti_entropy_context_registration(
    payload: Mapping[str, Any],
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    decision: Mapping[str, Any],
    turn_instance_id: str,
) -> TurnContextHookRegistration:
    """Create a TurnContextHookRegistration connecting anti-entropy into host input.

    The task intent and workspace are resolved once from the native Turn input
    and the Goal registry. Disabled turns declare no readback slot, so no empty
    guidance field is written into the host payload.
    """
    workspace = _resolve_workspace(
        payload, registry_path=registry_path, goal_id=goal_id
    )
    basis = _resolve_retirement_basis(payload, decision)
    enabled = _is_anti_entropy_enabled(
        payload,
        decision,
        basis,
        registry_path=registry_path,
        goal_id=goal_id,
    )

    def produce() -> Mapping[str, Any]:
        return anti_entropy_context_result(
            payload,
            goal_id=goal_id,
            decision=decision,
            workspace=workspace,
            resolved_basis=basis,
            enabled=enabled,
        )

    return TurnContextHookRegistration(
        hook_id="anti-entropy.turn-context",
        capability_id=ANTI_ENTROPY_CAPABILITY_ID,
        readback_slots=("anti_entropy_guidance",) if enabled else (),
        producer=produce,
        max_result_bytes=ANTI_ENTROPY_CONTEXT_MAX_RESULT_BYTES,
    )
