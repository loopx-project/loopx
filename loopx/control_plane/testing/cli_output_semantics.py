from __future__ import annotations

import hashlib
import json
import re
import shlex
from typing import Any

from loopx.control_plane.content_digest import ENVELOPED_SHA256_PATTERN


def authoring_input_observations(text: str) -> dict[str, bool]:
    """Recognize complete reviewed guidance for one-time output qualification.

    These observations never classify runtime authority. Match the structured
    owner and full instruction, not a keyword in arbitrary operator prose.
    """
    readback_purpose = (
        "Read each authored/reused id: one match, todo_detail_projection.source_complete=true; "
        "compare .todo.text, status/claim. Excerpts cannot verify writes. "
        "Missing/ambiguous/changed: reinspect before handoff. Readback grants no guard/lease authority."
    )
    vision_hint = (
        "Replace example claims/refs with evidence; obey the live contract and total limit. "
        "For ordinary CLI writeback, pass the packet with --agent-vision-json <file>. "
        "checkpoint-context is only for recovery after the original committed Turn writeback; "
        "follow its returned same-Turn recovery action, not a fresh-turn preflight."
    )
    observed = {"complete_todo_readback": False, "vision_cli_authoring": False}

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if (value.get("id") == "read_back_authored_todos"
                    and value.get("kind") == "operator_or_agent_actions"
                    and value.get("purpose") == readback_purpose
                    and "--todo-id '<todo-id>'" in str(value.get("command_template", ""))):
                observed["complete_todo_readback"] = True
            if (value.get("schema_version") == "goal_vision_replan_contract_v0"
                    and value.get("authoring_hint") == vision_hint):
                observed["vision_cli_authoring"] = True
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    try:
        collect(json.loads(text))
    except json.JSONDecodeError:
        observed["complete_todo_readback"] = bool(re.search(
            r"^\d+\. `read_back_authored_todos` \(operator_or_agent_actions\): "
            + re.escape(readback_purpose)
            + r"\n   - command/source: `loopx [^\n]*--todo-id '<todo-id>'`$",
            text, re.MULTILINE,
        ))
    return observed


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


def heartbeat_peer_admission_prompt_revision(text: str) -> str | None:
    """Attribute readable peer guidance for a one-time CLI budget transition.

    This recognizes the complete reviewed instruction block for validation only;
    workspace and claim/lease admission remain owned by the live quota contract.
    """
    block = (
        "Follow the current quota claim/lease and workspace contract plus repository rules. "
        "Follow todo continuation policy. Task-scoped coordination grants no authority over "
        "other agents. Keep scope in this prompt, not todo metadata."
    )
    return "heartbeat_peer_admission_v1" if block in text else None


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


def next_action_basis_count(value: Any) -> int:
    """Count rendered read fences for the one-time task-step projection change."""
    if isinstance(value, list):
        return sum(next_action_basis_count(child) for child in value)
    if not isinstance(value, dict):
        return 0
    basis = value.get("next_action_basis")
    return int(isinstance(basis, str) and ENVELOPED_SHA256_PATTERN.fullmatch(basis) is not None) + sum(
        next_action_basis_count(child) for child in value.values()
    )


def markdown_headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _MARKDOWN_HEADING.match(line)]


def command_route_counts(text: str) -> dict[str, int]:
    """Measure well-formed rendered routes, never grant runtime authority.

    Decode JSON strings before shell parsing, or read standalone/Markdown code
    commands. Prose mentioning an option and malformed argv earn no allowance.
    Both bindings are measured in one pass; duplicates within a command count once.
    """
    counts = {"runtime_root": 0, "registry": 0}
    pending: list[Any] = [text]
    commands: list[str] = []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                for line in value.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("loopx "):
                        commands.append(stripped)
                        continue
                    try:
                        pending.append(json.loads(line))
                    except ValueError:
                        commands.extend(re.findall(r"`(loopx [^`\r\n]+)`", line))
            else:
                if isinstance(decoded, (dict, list, str)):
                    pending.append(decoded)

    for command in commands:
        try:
            argv = shlex.split(command)
        except ValueError:
            continue
        bindings: set[str] = set()
        index = 1
        while index < len(argv) and argv[index].startswith("-"):
            option = argv[index]
            if (option not in {"--registry", "--runtime-root", "--format"}
                    or index + 1 >= len(argv)
                    or not argv[index + 1] or argv[index + 1].startswith("-")):
                break
            if option == "--format":
                if argv[index + 1] not in {"json", "markdown"}:
                    break
            else:
                bindings.add(option[2:].replace("-", "_"))
            index += 2
        # Reject an incomplete/invalid option prefix, or one with no subcommand.
        if (index == len(argv) or not argv[index].strip()
                or argv[index].startswith("-")):
            continue
        for binding in bindings:
            counts[binding] += 1
    return counts


def projection_envelope_schema_versions(value: Any) -> list[str]:
    if isinstance(value, str):
        return sorted(set(re.findall(r"^- projection: (?:🔴 )?envelope=`([a-z0-9_]+)`", value, re.MULTILINE)))
    return _schema_versions_for_key(value, "projection_envelope")
