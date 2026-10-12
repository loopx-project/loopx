from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any


WORKER_BRIDGE_INSTALL_CONTRACT_VERSION = "loopx_worker_bridge_install_contract_v0"
WORKER_BRIDGE_SURFACE = "loopx_worker_bridge_source_mount_v0"
LOOPX_PROJECT_ROOT_PLACEHOLDER = "<loopx-project-root>"
LOOPX_RUNTIME_ROOT_PLACEHOLDER = "<loopx-runtime-root>"
LOOPX_ACTIVE_USER_HOST_DIR_PLACEHOLDER = "<active-user-host-dir>"
DEFAULT_WORKER_BRIDGE_TRACE_DIR = "/logs/agent"
DEFAULT_WORKER_BRIDGE_ACTIVE_USER_MOUNT_TARGET = "/loopx-active-user"
DEFAULT_WORKER_BRIDGE_COUNTER_TRACE_JSON = (
    DEFAULT_WORKER_BRIDGE_TRACE_DIR + "/loopx-counter-trace.jsonl"
)
DEFAULT_WORKER_BRIDGE_ACTIVE_USER_FEED_JSONL = (
    DEFAULT_WORKER_BRIDGE_TRACE_DIR + "/loopx-active-user-interventions.jsonl"
)
DEFAULT_WORKER_BRIDGE_ACTIVE_USER_OBSERVATION_JSON = (
    DEFAULT_WORKER_BRIDGE_TRACE_DIR + "/loopx-active-user-observation.json"
)
DEFAULT_ACTIVE_USER_CODEX_BIN = "/opt/homebrew/bin/codex"
DEFAULT_ACTIVE_USER_SIMULATOR_CONTEXT_DIR = "<active-user-public-context-dir>"
DEFAULT_ACTIVE_USER_SIMULATOR_PROMPT_JSON = "<active-user-simulator-prompt.json>"
DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_JSON = "<active-user-simulator-output.json>"
DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_SCHEMA_JSON = (
    "<active-user-simulator-output-schema.json>"
)
DEFAULT_WORKER_BRIDGE_PYTHON_BIN = "python3"
DEFAULT_WORKER_BRIDGE_MODULE = "loopx.cli"
WORKER_BRIDGE_PYTHON_RUNTIME_POLICY = "ensure_python3_before_worker_cli_bridge"
ACTIVE_USER_INTERVENTION_CHANNEL_CONTRACT_VERSION = (
    "loopx_active_user_intervention_channel_contract_v0"
)
ACTIVE_USER_INTERVENTION_EVENT_VERSION = "loopx_active_user_intervention_v0"
ACTIVE_USER_INTERVENTION_OBSERVATION_VERSION = (
    "loopx_active_user_intervention_observation_v0"
)
ACTIVE_USER_INTERVENTION_CHANNEL_SURFACE = (
    "loopx_active_user_external_update_loop_v0"
)
ACTIVE_USER_CODEX_SIMULATOR_CONTRACT_VERSION = (
    "loopx_active_user_codex_cli_simulator_contract_v0"
)
ACTIVE_USER_SIMULATOR_OUTPUT_VERSION = (
    "loopx_active_user_simulator_output_v0"
)
ACTIVE_USER_SIMULATOR_ALLOWED_EVIDENCE_BASIS = (
    "public task prompt visible to worker",
    "worker public artifacts",
    "compact LoopX status and run metadata",
)
ACTIVE_USER_SIMULATOR_NO_ORACLE_AUDIT_KEYS = (
    "hidden_tests_visible",
    "expected_solution_visible",
    "benchmark_answer_key_visible",
    "credential_values_visible",
    "private_material_visible",
    "solution_patch_visible",
)
DEFAULT_WORKER_BRIDGE_MODE = "codex_loopx_active_worker"
ACTIVE_USER_PUBLIC_TEXT_FORBIDDEN_MARKERS = (
    "/" + "Users/",
    "/" + "tmp/",
    ".local/",
    ".loopx",
    "OPENAI" + "_API_KEY",
    "ARK" + "_API_KEY",
    "CODEX" + "_AUTH_JSON",
    "Author" + "ization:",
    "Bear" + "er ",
    "-----BEGIN",
    "tok" + "en=",
    "pass" + "word=",
)
ACTIVE_USER_SECRET_KEY_SHAPED_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}", re.IGNORECASE)


def build_worker_bridge_mounts(
    *,
    project_root: str = LOOPX_PROJECT_ROOT_PLACEHOLDER,
    runtime_root: str = LOOPX_RUNTIME_ROOT_PLACEHOLDER,
    active_user_host_dir: str | None = None,
    active_user_mount_target: str = DEFAULT_WORKER_BRIDGE_ACTIVE_USER_MOUNT_TARGET,
) -> list[dict[str, Any]]:
    """Build read-only source/runtime mounts for a worker-side LoopX CLI."""

    mounts = [
        {
            "type": "bind",
            "source": project_root,
            "target": project_root,
            "read_only": True,
        },
        {
            "type": "bind",
            "source": runtime_root,
            "target": runtime_root,
            "read_only": True,
        },
    ]
    if active_user_host_dir:
        mounts.append(
            {
                "type": "bind",
                "source": active_user_host_dir,
                "target": active_user_mount_target,
                "read_only": False,
            }
        )
    return mounts


def build_worker_bridge_command_prefix(
    *,
    project_root: str = LOOPX_PROJECT_ROOT_PLACEHOLDER,
    python_bin: str = DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
    module: str = DEFAULT_WORKER_BRIDGE_MODULE,
) -> str:
    """Build the in-worker command prefix for LoopX CLI calls."""

    return (
        f"PYTHONPATH={shlex.quote(project_root)} "
        f"{shlex.quote(python_bin)} -m {shlex.quote(module)}"
    )


def build_worker_bridge_python_runtime_preflight_command(
    *,
    project_root: str = LOOPX_PROJECT_ROOT_PLACEHOLDER,
    python_bin: str = DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
    module: str = DEFAULT_WORKER_BRIDGE_MODULE,
) -> str:
    """Build a worker-side preflight that makes the Python CLI bridge runnable."""

    project_root_arg = shlex.quote(project_root)
    python_bin_arg = shlex.quote(python_bin)
    python_code = shlex.quote(
        "import importlib; "
        f"importlib.import_module({json.dumps(module)})"
    )
    return (
        "set -e; "
        f"if ! command -v {python_bin_arg} >/dev/null 2>&1; then "
        "if command -v apt-get >/dev/null 2>&1; then "
        "apt-get update && apt-get install -y python3; "
        "elif command -v apk >/dev/null 2>&1; then "
        "apk add --no-cache python3; "
        "elif command -v yum >/dev/null 2>&1; then "
        "yum install -y python3; "
        "else "
        "echo 'loopx worker bridge requires python3 but no supported package manager was found' >&2; "
        "exit 127; "
        "fi; "
        "fi; "
        f"PYTHONPATH={project_root_arg} {python_bin_arg} -c {python_code}"
    )


def _coerce_public_safe_worker_text(
    value: str,
    *,
    field: str,
    limit: int,
) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    text_lower = text.lower()
    leaked = [
        marker
        for marker in ACTIVE_USER_PUBLIC_TEXT_FORBIDDEN_MARKERS
        if marker.lower() in text_lower
    ]
    if ACTIVE_USER_SECRET_KEY_SHAPED_RE.search(text):
        leaked.append("sk-token-shaped")
    if leaked:
        raise ValueError(f"{field} contains a non-public marker")
    return text[:limit].rstrip()


def build_active_user_intervention_channel_contract(
    *,
    project_root: str = LOOPX_PROJECT_ROOT_PLACEHOLDER,
    runtime_root: str = LOOPX_RUNTIME_ROOT_PLACEHOLDER,
    python_bin: str = DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
    module: str = DEFAULT_WORKER_BRIDGE_MODULE,
    feed_jsonl: str = DEFAULT_WORKER_BRIDGE_ACTIVE_USER_FEED_JSONL,
    observation_json: str = DEFAULT_WORKER_BRIDGE_ACTIVE_USER_OBSERVATION_JSON,
    counter_trace_json: str = DEFAULT_WORKER_BRIDGE_COUNTER_TRACE_JSON,
    classification: str = "active_user_observe_checkpoint",
    min_interval_seconds: int = 300,
    max_interventions_per_task: int = 3,
) -> dict[str, Any]:
    """Build the pull-based active-user update channel for a worker run."""

    min_interval = _coerce_non_negative_int(
        int(min_interval_seconds),
        field="min_interval_seconds",
    )
    max_interventions = _coerce_non_negative_int(
        int(max_interventions_per_task),
        field="max_interventions_per_task",
    )
    if max_interventions <= 0:
        raise ValueError("max_interventions_per_task must be greater than zero")
    command_prefix = build_worker_bridge_command_prefix(
        project_root=project_root,
        python_bin=python_bin,
        module=module,
    )
    observe_command = (
        f"{command_prefix} worker-bridge active-user-observe "
        f"--feed-jsonl {shlex.quote(feed_jsonl)} "
        "--worker-start-seq <worker-start-seq> "
        f"--observation-json {shlex.quote(observation_json)} "
        f"--counter-trace-json {shlex.quote(counter_trace_json)} "
        f"--classification {shlex.quote(classification)} "
        "--format json"
    )
    simulator_append_command = (
        f"{command_prefix} worker-bridge active-user-intervention "
        "--seq <next-seq> "
        "--trigger public_progress_or_stall_signal "
        "--message '<public-safe-user-message>' "
        "--jsonl "
        f">> {shlex.quote(feed_jsonl)}"
    )
    return {
        "ok": True,
        "schema_version": ACTIVE_USER_INTERVENTION_CHANNEL_CONTRACT_VERSION,
        "bridge_surface": WORKER_BRIDGE_SURFACE,
        "channel_surface": ACTIVE_USER_INTERVENTION_CHANNEL_SURFACE,
        "mode": "audited_external_update_loop",
        "feed_jsonl": feed_jsonl,
        "observation_json": observation_json,
        "counter_trace_json": counter_trace_json,
        "worker_observe_command": observe_command,
        "simulator_append_command": simulator_append_command,
        "worker_start_marker": {
            "kind": "worker_start_seq",
            "proof_rule": "worker must observe an intervention with seq greater than worker_start_seq",
        },
        "frequency_budget": {
            "min_interval_seconds": min_interval,
            "max_interventions_per_task": max_interventions,
            "simulator_may_be_proactive": True,
            "artificial_mildness_required": False,
        },
        "visibility_policy": {
            "simulator_may_read": [
                "public task prompt visible to worker",
                "worker public artifacts",
                "compact LoopX status and run metadata",
            ],
            "simulator_must_not_read": [
                "hidden tests",
                "expected solutions",
                "benchmark answer keys",
                "credentials",
                "private project material",
            ],
        },
        "claim_boundary": {
            "official_score_claim_allowed": False,
            "leaderboard_claim_allowed": False,
            "assisted_collaboration_claim_allowed": True,
            "direct_codex_chat_injection": False,
            "worker_pull_required": True,
        },
        "public_boundary": {
            "raw_paths_recorded": False,
            "raw_transcript_recorded": False,
            "credential_values_recorded": False,
            "no_upload": True,
        },
    }


def active_user_simulator_output_json_schema() -> dict[str, Any]:
    """Return the strict JSON shape expected from a Codex CLI user simulator."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "simulator_kind",
            "trigger",
            "message",
            "visible_evidence_basis",
            "no_oracle_audit",
            "controller_authored_message",
        ],
        "properties": {
            "schema_version": {
                "type": "string",
                "const": ACTIVE_USER_SIMULATOR_OUTPUT_VERSION,
            },
            "simulator_kind": {"type": "string", "const": "codex_cli"},
            "trigger": {"type": "string", "minLength": 1, "maxLength": 120},
            "message": {"type": "string", "minLength": 1, "maxLength": 500},
            "visible_evidence_basis": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "string",
                    "enum": list(ACTIVE_USER_SIMULATOR_ALLOWED_EVIDENCE_BASIS),
                },
            },
            "no_oracle_audit": {
                "type": "object",
                "additionalProperties": False,
                "required": list(ACTIVE_USER_SIMULATOR_NO_ORACLE_AUDIT_KEYS),
                "properties": {
                    key: {"type": "boolean", "const": False}
                    for key in ACTIVE_USER_SIMULATOR_NO_ORACLE_AUDIT_KEYS
                },
            },
            "controller_authored_message": {"type": "boolean", "const": False},
        },
    }


def build_active_user_codex_simulator_contract(
    *,
    project_root: str = LOOPX_PROJECT_ROOT_PLACEHOLDER,
    python_bin: str = DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
    module: str = DEFAULT_WORKER_BRIDGE_MODULE,
    codex_bin: str = DEFAULT_ACTIVE_USER_CODEX_BIN,
    context_dir: str = DEFAULT_ACTIVE_USER_SIMULATOR_CONTEXT_DIR,
    prompt_json: str = DEFAULT_ACTIVE_USER_SIMULATOR_PROMPT_JSON,
    simulator_output_json: str = DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_JSON,
    simulator_output_schema_json: str = DEFAULT_ACTIVE_USER_SIMULATOR_OUTPUT_SCHEMA_JSON,
    feed_jsonl: str = DEFAULT_WORKER_BRIDGE_ACTIVE_USER_FEED_JSONL,
) -> dict[str, Any]:
    """Build the model-backed active-user simulator launch contract.

    The formal treatment path uses this contract instead of controller-written
    feed messages: a separate Codex CLI run reads only public context, writes a
    bounded JSON object, and LoopX validates it before appending to the
    worker-visible feed.
    """

    safe_codex_bin = shlex.quote(codex_bin)
    safe_context_dir = shlex.quote(context_dir)
    safe_schema_json = shlex.quote(simulator_output_schema_json)
    safe_output_json = shlex.quote(simulator_output_json)
    safe_prompt_json = shlex.quote(prompt_json)
    command_prefix = build_worker_bridge_command_prefix(
        project_root=project_root,
        python_bin=python_bin,
        module=module,
    )
    codex_exec_command = (
        f"{safe_codex_bin} exec "
        f"--cd {safe_context_dir} "
        "--sandbox read-only "
        "--ask-for-approval never "
        "--ephemeral "
        f"--output-schema {safe_schema_json} "
        f"--output-last-message {safe_output_json} "
        f"- < {safe_prompt_json}"
    )
    append_validated_output_command = (
        f"{command_prefix} worker-bridge active-user-simulator-output "
        "--seq <next-seq> "
        f"--simulator-output-json {safe_output_json} "
        "--jsonl "
        f">> {shlex.quote(feed_jsonl)}"
    )
    return {
        "ok": True,
        "schema_version": ACTIVE_USER_CODEX_SIMULATOR_CONTRACT_VERSION,
        "bridge_surface": WORKER_BRIDGE_SURFACE,
        "channel_surface": ACTIVE_USER_INTERVENTION_CHANNEL_SURFACE,
        "simulator_kind": "codex_cli",
        "formal_treatment_requires_model_backed_simulator": True,
        "manual_controller_feed_allowed": False,
        "codex_cli": {
            "codex_bin": codex_bin,
            "exec_command": codex_exec_command,
            "sandbox": "read-only",
            "approval_policy": "never",
            "ephemeral": True,
        },
        "simulator_input_contract": {
            "prompt_json": prompt_json,
            "context_dir": context_dir,
            "allowed_context": list(ACTIVE_USER_SIMULATOR_ALLOWED_EVIDENCE_BASIS),
            "must_not_include": [
                "hidden tests",
                "expected solutions",
                "benchmark answer keys",
                "credentials",
                "private project material",
                "controller-authored intervention text",
            ],
        },
        "simulator_output_contract": {
            "schema_version": ACTIVE_USER_SIMULATOR_OUTPUT_VERSION,
            "output_json": simulator_output_json,
            "output_schema_json": simulator_output_schema_json,
            "json_schema": active_user_simulator_output_json_schema(),
        },
        "append_validated_output_command": append_validated_output_command,
        "claim_boundary": {
            "official_score_claim_allowed": False,
            "leaderboard_claim_allowed": False,
            "assisted_collaboration_claim_allowed": True,
            "direct_codex_chat_injection": False,
            "worker_pull_required": True,
            "controller_authored_feed_allowed": False,
        },
        "public_boundary": {
            "raw_paths_recorded": False,
            "raw_transcript_recorded": False,
            "credential_values_recorded": False,
            "no_upload": True,
        },
    }


def build_active_user_intervention_from_simulator_output(
    *,
    seq: int,
    simulator_output: dict[str, Any],
    created_after_worker_start: bool = True,
) -> dict[str, Any]:
    """Validate a Codex CLI simulator output and convert it to feed JSON."""

    if not isinstance(simulator_output, dict):
        raise ValueError("simulator_output must be a JSON object")
    allowed_top_level_keys = {
        "schema_version",
        "simulator_kind",
        "trigger",
        "message",
        "visible_evidence_basis",
        "no_oracle_audit",
        "controller_authored_message",
    }
    extra_top_level_keys = [
        key for key in simulator_output if key not in allowed_top_level_keys
    ]
    if extra_top_level_keys:
        raise ValueError("simulator_output contains unsupported fields")
    if simulator_output.get("schema_version") != ACTIVE_USER_SIMULATOR_OUTPUT_VERSION:
        raise ValueError(
            "simulator_output must have "
            f"schema_version={ACTIVE_USER_SIMULATOR_OUTPUT_VERSION}"
        )
    if simulator_output.get("simulator_kind") != "codex_cli":
        raise ValueError("simulator_output must have simulator_kind=codex_cli")
    if simulator_output.get("controller_authored_message") is not False:
        raise ValueError("simulator_output must not be controller-authored")

    evidence_basis = simulator_output.get("visible_evidence_basis")
    if not isinstance(evidence_basis, list) or not evidence_basis:
        raise ValueError("visible_evidence_basis must be a non-empty list")
    evidence_basis_text = [
        _coerce_public_safe_worker_text(str(item), field="visible_evidence_basis", limit=120)
        for item in evidence_basis
    ]
    invalid_basis = [
        item
        for item in evidence_basis_text
        if item not in ACTIVE_USER_SIMULATOR_ALLOWED_EVIDENCE_BASIS
    ]
    if invalid_basis:
        raise ValueError("visible_evidence_basis contains non-public or unsupported evidence")

    audit = simulator_output.get("no_oracle_audit")
    if not isinstance(audit, dict):
        raise ValueError("no_oracle_audit must be an object")
    extra_audit_keys = [
        key for key in audit if key not in ACTIVE_USER_SIMULATOR_NO_ORACLE_AUDIT_KEYS
    ]
    if extra_audit_keys:
        raise ValueError("no_oracle_audit contains unsupported fields")
    missing_audit_keys = [
        key for key in ACTIVE_USER_SIMULATOR_NO_ORACLE_AUDIT_KEYS if key not in audit
    ]
    if missing_audit_keys:
        raise ValueError("no_oracle_audit is missing required keys")
    leaked_audit_keys = [
        key for key in ACTIVE_USER_SIMULATOR_NO_ORACLE_AUDIT_KEYS if audit.get(key) is not False
    ]
    if leaked_audit_keys:
        raise ValueError("simulator_output failed no-oracle audit")

    intervention = build_active_user_intervention(
        seq=seq,
        message=str(simulator_output.get("message") or ""),
        trigger=str(simulator_output.get("trigger") or "codex_cli_simulator_public_guidance"),
        channel="codex_cli_user_simulator",
        created_after_worker_start=created_after_worker_start,
    )
    intervention.update(
        {
            "simulator_kind": "codex_cli",
            "simulator_output_schema_version": ACTIVE_USER_SIMULATOR_OUTPUT_VERSION,
            "controller_authored_message": False,
            "formal_treatment_eligible": True,
            "manual_controller_feed": False,
            "visible_evidence_basis": evidence_basis_text,
            "no_oracle_audit": {
                key: False for key in ACTIVE_USER_SIMULATOR_NO_ORACLE_AUDIT_KEYS
            },
        }
    )
    return intervention


def build_active_user_intervention(
    *,
    seq: int,
    message: str,
    trigger: str = "public_progress_or_stall_signal",
    channel: str = "simulator_proactive_user_message",
    created_after_worker_start: bool = True,
) -> dict[str, Any]:
    """Build one public-safe active-user intervention event."""

    intervention_seq = _coerce_non_negative_int(seq, field="seq")
    safe_message = _coerce_public_safe_worker_text(
        message,
        field="message",
        limit=500,
    )
    safe_trigger = _coerce_public_safe_worker_text(
        trigger,
        field="trigger",
        limit=120,
    )
    safe_channel = _coerce_public_safe_worker_text(
        channel,
        field="channel",
        limit=120,
    )
    return {
        "ok": True,
        "schema_version": ACTIVE_USER_INTERVENTION_EVENT_VERSION,
        "channel_surface": ACTIVE_USER_INTERVENTION_CHANNEL_SURFACE,
        "seq": intervention_seq,
        "channel": safe_channel,
        "type": "active_user_instruction",
        "trigger": safe_trigger,
        "message": safe_message,
        "created_after_worker_start": bool(created_after_worker_start),
        "oracle_free": True,
        "hidden_tests_visible": False,
        "expected_solution_visible": False,
        "credential_values_visible": False,
        "private_material_visible": False,
        "official_score_claim_allowed": False,
        "leaderboard_claim_allowed": False,
    }


def observe_active_user_intervention_feed(
    feed_jsonl: str | Path,
    *,
    worker_start_seq: int = 0,
) -> dict[str, Any]:
    """Read a public-safe intervention feed and summarize worker-observable updates."""

    start_seq = _coerce_non_negative_int(worker_start_seq, field="worker_start_seq")
    feed_path = Path(feed_jsonl)
    valid_events: list[dict[str, Any]] = []
    invalid_line_count = 0
    if feed_path.exists():
        for line in feed_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid_line_count += 1
                continue
            if not isinstance(item, dict):
                invalid_line_count += 1
                continue
            if item.get("schema_version") != ACTIVE_USER_INTERVENTION_EVENT_VERSION:
                invalid_line_count += 1
                continue
            seq = item.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
                invalid_line_count += 1
                continue
            valid_events.append(item)
    observable = [
        item
        for item in valid_events
        if item.get("seq", -1) > start_seq and item.get("created_after_worker_start") is True
    ]
    latest = max(observable, key=lambda item: item["seq"], default=None)
    latest_summary: dict[str, Any] = {}
    if latest:
        latest_summary = {
            "seq": latest.get("seq"),
            "channel": latest.get("channel"),
            "type": latest.get("type"),
            "trigger": latest.get("trigger"),
            "message": latest.get("message"),
            "oracle_free": latest.get("oracle_free") is True,
            "hidden_tests_visible": latest.get("hidden_tests_visible") is True,
            "expected_solution_visible": latest.get("expected_solution_visible") is True,
            "credential_values_visible": latest.get("credential_values_visible") is True,
            "private_material_visible": latest.get("private_material_visible") is True,
            "simulator_kind": latest.get("simulator_kind"),
            "formal_treatment_eligible": latest.get("formal_treatment_eligible") is True,
            "manual_controller_feed": latest.get("manual_controller_feed") is True,
            "controller_authored_message": latest.get("controller_authored_message") is True,
        }
    return {
        "ok": True,
        "schema_version": ACTIVE_USER_INTERVENTION_OBSERVATION_VERSION,
        "bridge_surface": WORKER_BRIDGE_SURFACE,
        "channel_surface": ACTIVE_USER_INTERVENTION_CHANNEL_SURFACE,
        "feed_present": feed_path.exists(),
        "feed_path_recorded": False,
        "worker_start_seq": start_seq,
        "valid_intervention_count": len(valid_events),
        "invalid_line_count": invalid_line_count,
        "observed_after_worker_start": bool(latest),
        "observed_intervention_count": len(observable),
        "latest_intervention": latest_summary,
        "worker_observation_proof": bool(latest),
        "claim_boundary": {
            "official_score_claim_allowed": False,
            "leaderboard_claim_allowed": False,
            "assisted_collaboration_claim_allowed": bool(latest),
            "direct_codex_chat_injection": False,
            "worker_pull_channel": True,
        },
        "public_boundary": {
            "raw_paths_recorded": False,
            "raw_transcript_recorded": False,
            "credential_values_recorded": False,
        },
        "next_action": (
            "wire the worker prompt to poll active-user-observe during assisted treatment"
            if latest
            else "append a public-safe simulator intervention after worker start and poll again"
        ),
    }


def write_active_user_observation_file(
    path: str | Path | None,
    payload: dict[str, Any],
) -> bool:
    """Write compact active-user observation without exposing raw path values."""

    if not path:
        return False
    output_path = Path(path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def _compact_counter_trace_label(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def append_worker_bridge_counter_trace_row(
    path: str | Path | None,
    *,
    command: str,
    ok: bool,
    goal_id: str,
    mode: str,
    classification: str,
    observed_after_worker_start: bool | None = None,
    worker_observation_proof: bool | None = None,
) -> bool:
    """Append one compact worker-side LoopX CLI call trace row."""

    if not path:
        return False
    trace_path = Path(path)
    row: dict[str, Any] = {
        "kind": "loopx_cli_call",
        "command": _compact_counter_trace_label(command),
        "ok": bool(ok),
        "goal_id": str(goal_id or "").strip() or "worker-bridge",
        "mode": str(mode or "").strip() or DEFAULT_WORKER_BRIDGE_MODE,
        "classification": (
            str(classification or "").strip() or "worker_bridge_checkpoint"
        ),
    }
    if observed_after_worker_start is not None:
        row["observed_after_worker_start"] = bool(observed_after_worker_start)
    if worker_observation_proof is not None:
        row["worker_observation_proof"] = bool(worker_observation_proof)
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def load_worker_bridge_counter_trace_file(path: str | Path | None) -> list[dict[str, Any]]:
    """Load compact worker-side counter trace rows, ignoring malformed rows."""

    if not path:
        return []
    trace_path = Path(path)
    if not trace_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        return []
    return rows


def build_worker_bridge_interaction_counters_from_trace(
    trace_rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build generic compact interaction counters from worker bridge trace rows."""

    loopx_cli_calls: dict[str, int] = {}
    total = 0
    for row in trace_rows or []:
        if not isinstance(row, dict):
            continue
        kind = _compact_counter_trace_label(
            row.get("kind") or row.get("type") or row.get("event")
        )
        command = _compact_counter_trace_label(row.get("command") or row.get("call"))
        if kind and kind != "loopx_cli_call":
            continue
        if not command:
            continue
        total += 1
        loopx_cli_calls[command] = loopx_cli_calls.get(command, 0) + 1
    loopx_cli_calls["total"] = total
    return {
        "schema_version": "loopx_worker_bridge_interaction_counters_v0",
        "loopx_cli_calls": loopx_cli_calls,
        "trace_row_count": len(trace_rows or []),
        "state_reads": loopx_cli_calls.get("active_user_observe", 0),
        "state_writes": 0,
        "raw_trace_recorded": False,
        "raw_paths_recorded": False,
    }


def build_worker_bridge_install_contract(
    *,
    project_root: str = LOOPX_PROJECT_ROOT_PLACEHOLDER,
    runtime_root: str = LOOPX_RUNTIME_ROOT_PLACEHOLDER,
    python_bin: str = DEFAULT_WORKER_BRIDGE_PYTHON_BIN,
    module: str = DEFAULT_WORKER_BRIDGE_MODULE,
    scan_path: str | None = None,
    counter_trace_json: str = DEFAULT_WORKER_BRIDGE_COUNTER_TRACE_JSON,
    classification: str = "<classification>",
    active_user_host_dir: str | None = None,
    active_user_mount_target: str = DEFAULT_WORKER_BRIDGE_ACTIVE_USER_MOUNT_TARGET,
) -> dict[str, Any]:
    """Build a runner-agnostic worker bridge/install contract.

    The contract is intentionally declarative. An executor can translate
    `mounts` and `agent_kwargs` into its own container or worker launch surface.
    """

    registry_arg = f"{runtime_root}/registry.global.json"
    scan_path_arg = scan_path or f"{project_root}/loopx"
    command_prefix = build_worker_bridge_command_prefix(
        project_root=project_root,
        python_bin=python_bin,
        module=module,
    )
    runtime_preflight_command = build_worker_bridge_python_runtime_preflight_command(
        project_root=project_root,
        python_bin=python_bin,
        module=module,
    )
    active_user_feed_jsonl = DEFAULT_WORKER_BRIDGE_ACTIVE_USER_FEED_JSONL
    active_user_observation_json = DEFAULT_WORKER_BRIDGE_ACTIVE_USER_OBSERVATION_JSON
    if active_user_host_dir:
        mount_target = active_user_mount_target.rstrip("/")
        active_user_feed_jsonl = f"{mount_target}/loopx-active-user-interventions.jsonl"
        active_user_observation_json = f"{mount_target}/loopx-active-user-observation.json"
    active_user_intervention_channel_contract = (
        build_active_user_intervention_channel_contract(
            project_root=project_root,
            runtime_root=runtime_root,
            python_bin=python_bin,
            module=module,
            feed_jsonl=active_user_feed_jsonl,
            observation_json=active_user_observation_json,
            counter_trace_json=counter_trace_json,
            classification=classification,
        )
    )
    return {
        "ok": True,
        "schema_version": WORKER_BRIDGE_INSTALL_CONTRACT_VERSION,
        "bridge_surface": WORKER_BRIDGE_SURFACE,
        "install_mode": "source_mount_read_only_pythonpath",
        "runtime_policy": WORKER_BRIDGE_PYTHON_RUNTIME_POLICY,
        "runtime_preflight_command": runtime_preflight_command,
        "project_root": project_root,
        "runtime_root": runtime_root,
        "mounts": build_worker_bridge_mounts(
            project_root=project_root,
            runtime_root=runtime_root,
            active_user_host_dir=active_user_host_dir,
            active_user_mount_target=active_user_mount_target,
        ),
        "active_user_external_update_mount": {
            "enabled": bool(active_user_host_dir),
            "target": active_user_mount_target if active_user_host_dir else None,
            "read_only": False if active_user_host_dir else None,
            "raw_host_path_recorded": False,
        },
        "command_prefix": command_prefix,
        "agent_kwargs": {
            "loopx_command_prefix": command_prefix,
            "loopx_runtime_preflight_command": runtime_preflight_command,
            "loopx_registry_arg": registry_arg,
            "loopx_runtime_root_arg": runtime_root,
            "loopx_scan_path": scan_path_arg,
            "loopx_counter_trace_json": counter_trace_json,
            "loopx_classification": classification,
            "loopx_active_user_feed_jsonl": (
                active_user_intervention_channel_contract["feed_jsonl"]
            ),
            "loopx_active_user_observation_json": (
                active_user_intervention_channel_contract["observation_json"]
            ),
            "loopx_active_user_observe_command": (
                active_user_intervention_channel_contract["worker_observe_command"]
            ),
            "loopx_active_user_channel_surface": (
                ACTIVE_USER_INTERVENTION_CHANNEL_SURFACE
            ),
        },
        "active_user_intervention_channel_contract": (
            active_user_intervention_channel_contract
        ),
        "trace": {
            "counter_trace_json": counter_trace_json,
            "active_user_feed_jsonl": active_user_intervention_channel_contract[
                "feed_jsonl"
            ],
            "active_user_observation_json": active_user_intervention_channel_contract[
                "observation_json"
            ],
            "write_surface": "worker_agent_logs",
            "raw_trace_public": False,
        },
        "boundary": {
            "real_run": False,
            "submit_eligible": False,
            "no_upload": True,
            "credential_values_recorded": False,
            "raw_paths_required_in_public_artifacts": False,
        },
    }


def _coerce_non_negative_int(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def render_worker_bridge_install_contract_markdown(payload: dict[str, Any]) -> str:
    if payload.get("schema_version") == ACTIVE_USER_CODEX_SIMULATOR_CONTRACT_VERSION:
        cli = payload.get("codex_cli") or {}
        boundary = payload.get("claim_boundary") or {}
        lines = [
            "# LoopX Active User Codex Simulator Contract",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- schema_version: `{payload.get('schema_version')}`",
            f"- simulator_kind: `{payload.get('simulator_kind')}`",
            f"- manual_controller_feed_allowed: `{payload.get('manual_controller_feed_allowed')}`",
            f"- codex_bin: `{cli.get('codex_bin')}`",
            f"- official_score_claim_allowed: `{boundary.get('official_score_claim_allowed')}`",
            f"- direct_codex_chat_injection: `{boundary.get('direct_codex_chat_injection')}`",
            f"- controller_authored_feed_allowed: `{boundary.get('controller_authored_feed_allowed')}`",
        ]
        return "\n".join(lines) + "\n"

    if payload.get("schema_version") == ACTIVE_USER_INTERVENTION_CHANNEL_CONTRACT_VERSION:
        budget = payload.get("frequency_budget") or {}
        boundary = payload.get("claim_boundary") or {}
        lines = [
            "# LoopX Active User Intervention Channel",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- schema_version: `{payload.get('schema_version')}`",
            f"- channel_surface: `{payload.get('channel_surface')}`",
            f"- mode: `{payload.get('mode')}`",
            f"- max_interventions_per_task: `{budget.get('max_interventions_per_task')}`",
            f"- min_interval_seconds: `{budget.get('min_interval_seconds')}`",
            f"- official_score_claim_allowed: `{boundary.get('official_score_claim_allowed')}`",
            f"- direct_codex_chat_injection: `{boundary.get('direct_codex_chat_injection')}`",
            f"- worker_pull_required: `{boundary.get('worker_pull_required')}`",
        ]
        return "\n".join(lines) + "\n"

    if payload.get("schema_version") == ACTIVE_USER_INTERVENTION_EVENT_VERSION:
        lines = [
            "# LoopX Active User Intervention",
            "",
            f"- schema_version: `{payload.get('schema_version')}`",
            f"- channel_surface: `{payload.get('channel_surface')}`",
            f"- seq: `{payload.get('seq')}`",
            f"- channel: `{payload.get('channel')}`",
            f"- trigger: `{payload.get('trigger')}`",
            f"- created_after_worker_start: `{payload.get('created_after_worker_start')}`",
            f"- oracle_free: `{payload.get('oracle_free')}`",
            f"- simulator_kind: `{payload.get('simulator_kind')}`",
            f"- formal_treatment_eligible: `{payload.get('formal_treatment_eligible')}`",
        ]
        return "\n".join(lines) + "\n"

    if payload.get("schema_version") == ACTIVE_USER_INTERVENTION_OBSERVATION_VERSION:
        latest = payload.get("latest_intervention") or {}
        boundary = payload.get("claim_boundary") or {}
        lines = [
            "# LoopX Active User Intervention Observation",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- schema_version: `{payload.get('schema_version')}`",
            f"- channel_surface: `{payload.get('channel_surface')}`",
            f"- observed_after_worker_start: `{payload.get('observed_after_worker_start')}`",
            f"- observed_intervention_count: `{payload.get('observed_intervention_count')}`",
            f"- latest_seq: `{latest.get('seq')}`",
            f"- latest_trigger: `{latest.get('trigger')}`",
            f"- official_score_claim_allowed: `{boundary.get('official_score_claim_allowed')}`",
            f"- direct_codex_chat_injection: `{boundary.get('direct_codex_chat_injection')}`",
            f"- next_action: `{payload.get('next_action')}`",
        ]
        return "\n".join(lines) + "\n"

    lines = [
        "# LoopX Worker Bridge",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- bridge_surface: `{payload.get('bridge_surface')}`",
        f"- install_mode: `{payload.get('install_mode')}`",
        f"- command_prefix: `{payload.get('command_prefix')}`",
        f"- counter_trace_json: `{(payload.get('trace') or {}).get('counter_trace_json')}`",
    ]
    mounts = payload.get("mounts")
    if isinstance(mounts, list):
        lines.append("- mounts:")
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            lines.append(
                "  - "
                f"type=`{mount.get('type')}` "
                f"source=`{mount.get('source')}` "
                f"target=`{mount.get('target')}` "
                f"read_only=`{mount.get('read_only')}`"
            )
    agent_kwargs = payload.get("agent_kwargs")
    if isinstance(agent_kwargs, dict):
        lines.append("- agent_kwargs:")
        for key in sorted(agent_kwargs):
            lines.append(f"  - {key}: `{agent_kwargs[key]}`")
    return "\n".join(lines) + "\n"
