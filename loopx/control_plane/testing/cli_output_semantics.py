from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def heartbeat_user_language_prompt_revision(text: str) -> str | None:
    """Attribute the one-time user-language prompt transition in CLI probes.

    This recognizes the exact rendered policy, not runtime language or authority.
    Full/compact/Goal and thin/brief prompts use different bounded wording.
    """

    rules = (
        "Language=user; fallback=English; mix only if asked/scoped-bilingual.",
        "Lang=user; default=en; mix=asked/scoped.",
    )
    return "heartbeat_user_language_v1" if any(rule in text for rule in rules) else None


def host_prompt_static_safety_revision(text: str) -> str | None:
    """Exact renderer evidence for the one-time static-safety budget transition.

    This is test-output attribution, never a runtime permission classifier.
    Keep the full invariant block, not a substring such as 'safe' or 'LoopX'.
    """
    block = (
        "Follow user authority and repository rules. Protect credentials/private material; "
        "publish public-safe evidence. Destructive Git/production requires explicit authorization. "
        "Gate only the affected path; continue independent allowed work."
    )
    return "host_prompt_static_safety_v1" if block in text else None


def reward_memory_outcome_prompt_revision(text: str) -> str | None:
    """Attribute the one-time automatic outcome lifecycle prompt transition.

    This is qualification evidence for the exact fail-closed contract.  It is
    not a runtime detector and deliberately requires every safety invariant.
    """

    required = (
        "--reward-memory-reflection-json",
        "Todo validator",
        "digest",
        "evidence",
        "zero provider calls",
        "raw",
        "private",
    )
    return (
        "reward_memory_outcome_prompt_v1"
        if all(fragment in text for fragment in required)
        else None
    )


def managed_executor_binding_revision(text: str) -> str | None:
    """Attribute the managed-executor binding readback on a Turn surface.

    This is qualification evidence for the exact projection, never a runtime
    classifier: the binding key alone would match prose, so the revision also
    requires the executor identity, its launchability claim, and the typed
    reason slot that only this readback renders.
    """

    required = (
        '"managed_executor"',
        '"executor_kind"',
        '"available"',
        '"unavailable_reason"',
    )
    return (
        "managed_executor_binding_v0"
        if all(fragment in text for fragment in required)
        else None
    )

_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+.+$")
_RUNTIME_ROOT_COMMAND_ROUTE = re.compile(
    r"(?m)(?:^|[\"'`])[^\r\n\S]*loopx\s+--runtime-root\s+"
    r"(?:\"[^\"\r\n]+\"|'[^'\r\n]+'|\S+)"
)


def json_shape_paths(value: Any, *, path: str = "$") -> list[str]:
    paths = {path}
    if isinstance(value, dict):
        for key, child in value.items():
            paths.update(json_shape_paths(child, path=f"{path}.{key}"))
    elif isinstance(value, list):
        list_path = f"{path}[]"
        paths.add(list_path)
        for child in value:
            paths.update(json_shape_paths(child, path=list_path))
    return sorted(paths)


def action_signature_semantic_sha256(value: Any) -> str | None:
    signatures: list[dict[str, Any]] = []

    def collect(current: Any, *, path: str) -> None:
        if isinstance(current, dict):
            for key, child in current.items():
                child_path = f"{path}.{key}"
                if key == "action_signature":
                    normalized = child
                    if isinstance(child, dict):
                        normalized = {
                            "schema_version": child.get("schema_version"),
                            "coverage": child.get("coverage"),
                            "matches": child.get("matches"),
                            "source_envelope_hashes_present": (
                                "source_hash" in child and "envelope_hash" in child
                            ),
                            "source_envelope_match": (
                                child.get("source_hash") == child.get("envelope_hash")
                            ),
                            "source_decision_hash_present": (
                                "source_decision_hash" in child
                            ),
                        }
                    signatures.append({"path": child_path, "value": normalized})
                collect(child, path=child_path)
        elif isinstance(current, list):
            for child in current:
                collect(child, path=f"{path}[]")

    collect(value, path="$")
    if not signatures:
        return None
    canonical = json.dumps(
        signatures,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def action_signature_coverages(value: Any) -> list[str]:
    coverages: set[str] = set()

    def collect(current: Any) -> None:
        if isinstance(current, dict):
            for key, child in current.items():
                if key == "action_signature" and isinstance(child, dict):
                    coverage = child.get("coverage")
                    if isinstance(coverage, str) and coverage:
                        coverages.add(coverage)
                collect(child)
        elif isinstance(current, list):
            for child in current:
                collect(child)

    collect(value)
    return sorted(coverages)


def _schema_versions_for_key(value: Any, key: str) -> list[str]:
    versions: set[str] = set()

    def collect(current: Any) -> None:
        if isinstance(current, dict):
            for child_key, child in current.items():
                if child_key == key and isinstance(child, dict):
                    schema_version = child.get("schema_version")
                    if isinstance(schema_version, str) and schema_version:
                        versions.add(schema_version)
                collect(child)
        elif isinstance(current, list):
            for child in current:
                collect(child)

    collect(value)
    return sorted(versions)


def action_portfolio_schema_versions(value: Any) -> list[str]:
    return _schema_versions_for_key(value, "action_portfolio")


def planning_horizon_schema_versions(value: Any) -> list[str]:
    return _schema_versions_for_key(value, "planning_horizon")


def planning_inventory_detail_schema_versions(value: Any) -> list[str]:
    return _schema_versions_for_key(value, "agent_todo_planning_inventory")


def guided_todo_delta_schema_versions(value: Any) -> list[str]:
    return _schema_versions_for_key(value, "todo_delta")


def todo_work_counts_schema_versions(value: Any) -> list[str]:
    return _schema_versions_for_key(value, "work_counts")


def markdown_headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _MARKDOWN_HEADING.match(line)]


def runtime_root_command_route_count(text: str) -> int:
    return len(_RUNTIME_ROOT_COMMAND_ROUTE.findall(text))


def projection_envelope_schema_versions(value: Any) -> list[str]:
    if isinstance(value, str):
        return sorted(set(re.findall(r"^- projection: (?:🔴 )?envelope=`([a-z0-9_]+)`", value, re.MULTILINE)))
    return _schema_versions_for_key(value, "projection_envelope")
