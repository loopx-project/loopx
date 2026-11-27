"""Dual-model execution adapters reusing LoopX Codex + Ark runtime clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Protocol

from benchmark.runtime.codex import (
    Execution,
    prepare_codex_home,
    process_environment,
)
from loopx.control_plane.testing.doubao_model_behavior_actor import (
    ALLOWED_MODEL_BEHAVIOR_MODELS,
    ARK_API_KEY_ENV,
    DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
    DoubaoActorTransport,
    DoubaoActorTransportError,
    _direct_ark_transport,
    _strip_json_markdown_fence,
)
from loopx.control_plane.testing.model_tool_behavior import (
    DoubaoExecToolClient,
    ExecToolStep,
    append_exec_tool_exchange,
    execute_loopx_cli,
    loopx_command_tokens,
)
from loopx.control_plane.turn_driver.codex_cli import CodexFailureObservation

from .metrics import (
    EfficiencyMetrics,
    collect_codex_rollout_metrics,
    compute_occupancy_summary,
    resolve_deadline,
)

DEFAULT_CODEX_MODEL = "gpt-6.2-sol"
DEFAULT_CODEX_EFFORT = "low"
DEFAULT_ARK_FLASH_MODEL = "deepseek-v4-1-flash-260910"

ARK_BEHAVIOR_AND_FLASH_MODELS = frozenset(
    ALLOWED_MODEL_BEHAVIOR_MODELS | {DEFAULT_ARK_FLASH_MODEL}
)

MICROBENCH_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "scenario_id",
        "decision",
        "selected_todo_id",
        "preserved_blocked_todo_ids",
        "extracted_proof",
        "preserved_failure_facts",
        "preserved_open_obligations",
        "claim_owner",
        "spend_attempted",
        "spend_classification",
        "tool_actions_summary",
    ],
    "properties": {
        "schema_version": {
            "type": "string",
            "enum": ["microbench_model_verdict_v0"],
        },
        "scenario_id": {"type": "string"},
        "decision": {"type": "string", "enum": ["execute", "ask_user", "stop"]},
        "selected_todo_id": {"type": ["string", "null"]},
        "preserved_blocked_todo_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "extracted_proof": {"type": ["string", "null"]},
        "preserved_failure_facts": {
            "type": "array",
            "items": {"type": "string"},
        },
        "preserved_open_obligations": {
            "type": "array",
            "items": {"type": "string"},
        },
        "claim_owner": {"type": ["string", "null"]},
        "spend_attempted": {"type": "boolean"},
        "spend_classification": {
            "type": "object",
            "additionalProperties": False,
            "required": ["observed", "attempted", "confirmed"],
            "properties": {
                "observed": {
                    "type": "string",
                    "enum": ["none", "read_only_receipt", "quota_spend_slot"],
                },
                "attempted": {"type": "boolean"},
                "confirmed": {"type": "boolean"},
            },
        },
        "tool_actions_summary": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

_MICROBENCH_REQUIRED_FIELDS = tuple(MICROBENCH_VERDICT_SCHEMA["required"])
_MICROBENCH_ALLOWED_FIELDS = frozenset(MICROBENCH_VERDICT_SCHEMA["properties"].keys())
_MICROBENCH_VALID_DECISIONS = frozenset({"execute", "ask_user", "stop"})
_MICROBENCH_SPEND_OBS_VALUES = frozenset({"none", "read_only_receipt", "quota_spend_slot"})
_MICROBENCH_SPEND_CLASS_KEYS = frozenset({"observed", "attempted", "confirmed"})
_LOOPX_GLOBAL_VALUE_FLAGS = frozenset({"--registry", "--runtime-root", "--format", "--cli-bin"})
_POSIX_SHELL_WRAPPERS = frozenset({"sh", "bash", "zsh"})
_TYPED_SPEND_TOOL_NAMES = frozenset({"quota_spend_slot", "spend_slot"})


def require_positive_finite_float(value: object, *, label: str) -> float:
    """Validate finite positive timeout/budget scalar before scheduling or telemetry emission."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite positive number, got bool")
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{label} must be a finite positive number")
        try:
            parsed = float(stripped)
        except ValueError as exc:
            raise ValueError(f"{label} must be a finite positive number, got {value!r}") from exc
    else:
        raise ValueError(f"{label} must be a finite positive number, got {type(value).__name__}")
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{label} must be a finite positive number, got {value!r}")
    return parsed


def validate_microbench_verdict(
    verdict: object,
    *,
    expected_scenario_id: str | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Validate parsed verdict against ``MICROBENCH_VERDICT_SCHEMA`` and current scenario."""
    if not isinstance(verdict, Mapping):
        return False, ("verdict_not_object",)
    errors: list[str] = []
    keys = set(verdict.keys())
    missing = [key for key in _MICROBENCH_REQUIRED_FIELDS if key not in keys]
    if missing:
        errors.append(f"missing_fields:{','.join(missing)}")
    extra = sorted(keys - _MICROBENCH_ALLOWED_FIELDS)
    if extra:
        errors.append(f"unexpected_fields:{','.join(extra)}")

    if verdict.get("schema_version") != "microbench_model_verdict_v0":
        errors.append("invalid_schema_version")

    sid = verdict.get("scenario_id")
    if not isinstance(sid, str) or not sid:
        errors.append("invalid_scenario_id")
    elif expected_scenario_id is not None and sid != expected_scenario_id:
        errors.append("verdict_scenario_mismatch")

    decision = verdict.get("decision")
    if not isinstance(decision, str) or decision not in _MICROBENCH_VALID_DECISIONS:
        errors.append("invalid_decision")

    for nullable_key in ("selected_todo_id", "extracted_proof", "claim_owner"):
        if nullable_key in verdict:
            val = verdict[nullable_key]
            if val is not None and not isinstance(val, str):
                errors.append(f"invalid_type:{nullable_key}")

    for list_key in (
        "preserved_blocked_todo_ids",
        "preserved_failure_facts",
        "preserved_open_obligations",
        "tool_actions_summary",
    ):
        if list_key in verdict:
            val = verdict[list_key]
            if not isinstance(val, list) or any(not isinstance(item, str) for item in val):
                errors.append(f"invalid_type:{list_key}")

    if "spend_attempted" in verdict and not isinstance(verdict["spend_attempted"], bool):
        errors.append("invalid_type:spend_attempted")

    if "spend_classification" in verdict:
        sc = verdict["spend_classification"]
        if not isinstance(sc, Mapping):
            errors.append("invalid_type:spend_classification")
        else:
            sc_keys = set(sc.keys())
            sc_missing = sorted(_MICROBENCH_SPEND_CLASS_KEYS - sc_keys)
            sc_extra = sorted(sc_keys - _MICROBENCH_SPEND_CLASS_KEYS)
            if sc_missing:
                errors.append(f"missing_fields:spend_classification.{','.join(sc_missing)}")
            if sc_extra:
                errors.append(f"unexpected_fields:spend_classification.{','.join(sc_extra)}")
            obs = sc.get("observed")
            if not isinstance(obs, str) or obs not in _MICROBENCH_SPEND_OBS_VALUES:
                errors.append("invalid_spend_classification_observed")
            for bkey in ("attempted", "confirmed"):
                if bkey in sc and not isinstance(sc[bkey], bool):
                    errors.append(f"invalid_type:spend_classification.{bkey}")

    return not errors, tuple(errors)


def _validate_adapter_verdict(
    parsed: dict[str, Any] | None,
    *,
    expected_scenario_id: str,
) -> tuple[dict[str, Any] | None, str, str | None]:
    if parsed is None:
        return None, "error", "invalid_verdict_json"
    ok, errors = validate_microbench_verdict(parsed, expected_scenario_id=expected_scenario_id)
    if not ok:
        code = (
            "verdict_scenario_mismatch"
            if "verdict_scenario_mismatch" in errors
            else "invalid_verdict_schema"
        )
        return None, "error", code
    return parsed, "ok", None


def _summarize_loopx_command(tokens: Sequence[str]) -> str:
    positional: list[str] = []
    idx = 1
    while idx < len(tokens):
        tok = tokens[idx]
        if tok in _LOOPX_GLOBAL_VALUE_FLAGS:
            idx += 2
            continue
        if any(tok.startswith(f"{flag}=") for flag in _LOOPX_GLOBAL_VALUE_FLAGS):
            idx += 1
            continue
        if tok.startswith("-"):
            idx += 1
            continue
        positional.append(tok)
        if len(positional) >= 2:
            break
        idx += 1
    if positional:
        return f"loopx:{' '.join(positional)}"
    return f"loopx:{' '.join(tokens[1:3])}"


@dataclass(frozen=True)
class ModelRunOutcome:
    """Normalized execution result from either Codex GPT or DeepSeek V4.1 Flash."""

    model_id: str
    provider: str
    status: str  # ok | unavailable | quota_exhausted | error
    error_code: str | None
    verdict: dict[str, Any] | None
    efficiency: EfficiencyMetrics
    observed_commands: tuple[str, ...] = ()
    telemetry_source: str = "unknown"
    telemetry_coverage: dict[str, Any] | None = None


class MicrobenchModelRunner(Protocol):
    model_id: str
    provider: str

    def run_case(
        self,
        *,
        scenario_id: str,
        prompt_text: str,
        trial_root: Path,
        project_root: Path,
        timeout_seconds: float,
    ) -> ModelRunOutcome: ...


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _execute_scoped_command(
    command: str,
    *,
    trial_root: Path,
    project_root: Path,
    source_root: Path,
    timeout_seconds: float = 30.0,
) -> tuple[str, str]:
    """Execute bounded inspection or LoopX CLI strictly inside ``trial_root``."""
    tokens = loopx_command_tokens(command)
    if tokens is not None:
        # Reject any file arguments escaping the isolated trial workspace.
        for tok in tokens[1:]:
            if tok.startswith("/") and not _is_within_root(Path(tok), trial_root):
                return (
                    f"blocked_loopx_cli:{tokens[1] if len(tokens) > 1 else 'loopx'}",
                    json.dumps({"ok": False, "error": "path escapes trial workspace"}),
                )
        try:
            stdout = execute_loopx_cli(
                command,
                source_root=source_root,
                project_root=project_root,
                accepted_return_codes=(0, 1, 2),
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            return "loopx_cli_error", json.dumps({"ok": False, "error": type(exc).__name__})
        label = _summarize_loopx_command(tokens)
        return label, stdout[:8192]

    try:
        parts = shlex.split(command)
    except ValueError:
        return "invalid_syntax", json.dumps({"ok": False, "error": "invalid shell syntax"})
    if not parts or any(op in {";", "&&", "||", "|", ">", ">>", "<", "$(", "`"} for op in parts):
        return "blocked_shell_op", json.dumps({"ok": False, "error": "shell chaining blocked"})

    cmd_name = Path(parts[0]).name
    if cmd_name not in {"cat", "head", "tail", "ls", "wc"}:
        return (
            f"blocked_command:{cmd_name}",
            json.dumps({"ok": False, "error": f"unsupported tool command: {cmd_name}"}),
        )
    target_args = [arg for arg in parts[1:] if not arg.startswith("-")]
    if not target_args and cmd_name == "ls":
        target_args = ["."]
    resolved_targets: list[str] = []
    for raw_target in target_args:
        cand = Path(raw_target)
        resolved = cand if cand.is_absolute() else (project_root / cand)
        if not _is_within_root(resolved, trial_root):
            return (
                "blocked_path_escape",
                json.dumps({"ok": False, "error": "target escapes trial workspace"}),
            )
        resolved_targets.append(str(resolved))
    argv = [cmd_name, *[arg for arg in parts[1:] if arg.startswith("-")], *resolved_targets]
    try:
        completed = subprocess.run(
            argv,
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(timeout_seconds, 15.0),
        )
    except subprocess.TimeoutExpired:
        return "tool_timeout", json.dumps({"ok": False, "error": "tool_timeout"})
    out = completed.stdout if completed.returncode == 0 else completed.stderr
    return f"read:{cmd_name}", (out or "")[:8192]


_ARK_USAGE_METRICS = (
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def _aggregate_ark_usage_coverage(
    step_totals: Mapping[str, int],
    step_counts: Mapping[str, int],
    total_steps: int,
) -> tuple[dict[str, int | None], dict[str, Any]]:
    """Aggregate Ark token usage per metric without coercing missing/partial coverage to 0."""
    resolved: dict[str, int | None] = {}
    metrics_cov: dict[str, dict[str, Any]] = {}
    partial_metrics: list[str] = []
    missing_metrics: list[str] = []

    for metric in _ARK_USAGE_METRICS:
        reported = int(step_counts.get(metric, 0))
        total_val = int(step_totals.get(metric, 0))
        if total_steps <= 0 or reported <= 0:
            resolved[metric] = None
            missing_metrics.append(metric)
            metrics_cov[metric] = {
                "status": "missing",
                "reported_steps": 0,
                "total_steps": total_steps,
            }
        elif reported < total_steps:
            resolved[metric] = None
            partial_metrics.append(metric)
            metrics_cov[metric] = {
                "status": "partial",
                "reported_steps": reported,
                "total_steps": total_steps,
                "partial_total": total_val,
            }
        else:
            resolved[metric] = total_val
            metrics_cov[metric] = {
                "status": "complete",
                "reported_steps": reported,
                "total_steps": total_steps,
            }

    return resolved, {
        "schema_version": "microbench_telemetry_coverage_v0",
        "total_steps": total_steps,
        "has_partial_coverage": bool(partial_metrics),
        "partial_metrics": partial_metrics,
        "missing_metrics": missing_metrics,
        "metrics": metrics_cov,
    }


def _extract_verdict(raw_text: str | None) -> dict[str, Any] | None:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    cleaned = _strip_json_markdown_fence(raw_text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    idx = 0
    while idx < len(raw_text):
        brace = raw_text.find("{", idx)
        if brace < 0:
            break
        try:
            obj, end_idx = decoder.raw_decode(raw_text, brace)
        except json.JSONDecodeError:
            idx = brace + 1
            continue
        if isinstance(obj, dict):
            if obj.get("schema_version") == "microbench_model_verdict_v0":
                return obj
            candidates.append(obj)
        idx = max(end_idx, brace + 1)
    return candidates[-1] if candidates else None


class DeepSeekArkMicrobenchRunner:
    """Run Tier-1 scenarios on DeepSeek V4.1 Flash via Ark ``DoubaoExecToolClient``."""

    provider = "ark"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_ARK_FLASH_MODEL,
        endpoint: str = DOUBAO_CHAT_COMPLETIONS_ENDPOINT,
        transport: DoubaoActorTransport = _direct_ark_transport,
        max_steps: int = 6,
        source_root: Path | None = None,
    ) -> None:
        self.model_id = model
        self._api_key = api_key
        self._endpoint = endpoint
        self._transport = transport
        self._max_steps = max_steps
        self._source_root = (source_root or Path(__file__).resolve().parents[2]).resolve()

    def _build_ark_outcome(
        self,
        *,
        status: str,
        error_code: str | None,
        verdict: dict[str, Any] | None,
        wire_bytes: int,
        usage_totals: Mapping[str, int],
        usage_reported_steps: Mapping[str, int],
        round_trips: int,
        per_round_prompt_tokens: Sequence[int | None],
        compaction_events: int,
        truncation_events: int,
        observed_commands: Sequence[str],
        started: float,
    ) -> ModelRunOutcome:
        resolved_usage, coverage = _aggregate_ark_usage_coverage(
            usage_totals, usage_reported_steps, round_trips
        )
        all_rounds_have_prompt = (
            round_trips > 0
            and len(per_round_prompt_tokens) == round_trips
            and all(isinstance(val, int) and not isinstance(val, bool) for val in per_round_prompt_tokens)
        )
        occ_summary = compute_occupancy_summary(
            per_turn_context_tokens=(
                [int(val) for val in per_round_prompt_tokens if isinstance(val, int)]
                if all_rounds_have_prompt
                else None
            ),
            effective_context_window=None,
            occupancy_measurement_kind="per_request_input_proxy",
            occupancy_window_source=None,
            compaction_events=compaction_events,
            truncation_events=truncation_events,
        )
        raw_per_turn = occ_summary.get("per_turn_context_tokens")
        per_turn_tuple = (
            tuple(int(v) for v in raw_per_turn if isinstance(v, int) and not isinstance(v, bool))
            if isinstance(raw_per_turn, (list, tuple)) and raw_per_turn
            else None
        )
        raw_ratio = occ_summary.get("peak_window_ratio")
        peak_ratio = (
            float(raw_ratio)
            if isinstance(raw_ratio, (int, float)) and not isinstance(raw_ratio, bool)
            else None
        )
        return ModelRunOutcome(
            model_id=self.model_id,
            provider=self.provider,
            status=status,
            error_code=error_code,
            verdict=verdict,
            efficiency=EfficiencyMetrics(
                wire_bytes=wire_bytes,
                prompt_tokens=resolved_usage["prompt_tokens"],
                cached_tokens=resolved_usage["cached_tokens"],
                completion_tokens=resolved_usage["completion_tokens"],
                reasoning_tokens=resolved_usage["reasoning_tokens"],
                total_tokens=resolved_usage["total_tokens"],
                tool_calls=len(observed_commands),
                round_trips=round_trips,
                wall_seconds=round(time.monotonic() - started, 3),
                initial_turn_context_tokens=_opt_int(occ_summary, "initial_turn_context_tokens"),
                per_turn_context_tokens=per_turn_tuple,
                peak_context_tokens=_opt_int(occ_summary, "peak_context_tokens"),
                effective_context_window=_opt_int(occ_summary, "effective_context_window"),
                min_headroom_tokens=_opt_int(occ_summary, "min_headroom_tokens"),
                peak_window_ratio=peak_ratio,
                compaction_events=_opt_int(occ_summary, "compaction_events"),
                truncation_events=_opt_int(occ_summary, "truncation_events"),
                occupancy_measurement_kind=str(occ_summary["occupancy_measurement_kind"]),
                occupancy_window_source=(
                    str(occ_summary["occupancy_window_source"])
                    if occ_summary.get("occupancy_window_source") is not None
                    else None
                ),
            ),
            observed_commands=tuple(observed_commands),
            telemetry_source="ark_usage",
            telemetry_coverage=coverage,
        )

    def run_case(
        self,
        *,
        scenario_id: str,
        prompt_text: str,
        trial_root: Path,
        project_root: Path,
        timeout_seconds: float = 45.0,
    ) -> ModelRunOutcome:
        timeout_seconds = require_positive_finite_float(timeout_seconds, label="timeout_seconds")
        started = time.monotonic()
        deadline = started + timeout_seconds
        wire_bytes = len(prompt_text.encode("utf-8"))
        key = (self._api_key or os.environ.get(ARK_API_KEY_ENV, "")).strip()
        if not key:
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="unavailable",
                error_code="missing_ark_api_key",
                verdict=None,
                efficiency=EfficiencyMetrics(
                    wire_bytes=wire_bytes,
                    wall_seconds=round(time.monotonic() - started, 3),
                ),
                telemetry_source="ark_usage",
            )

        usage_totals = {metric: 0 for metric in _ARK_USAGE_METRICS}
        usage_reported_steps = {metric: 0 for metric in _ARK_USAGE_METRICS}
        round_trips = 0
        per_round_prompt_tokens: list[int | None] = []
        compaction_events = 0
        truncation_events = 0

        def _record_usage_metric(metric: str, value: object) -> None:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage_totals[metric] += value
                usage_reported_steps[metric] += 1

        def _require_arm_remaining() -> float:
            rem = deadline - time.monotonic()
            if rem <= 0.0:
                raise DoubaoActorTransportError(
                    "Ark arm monotonic deadline exhausted",
                    error_code="ark_budget_exhausted",
                )
            return rem

        def tracked_transport(
            *,
            endpoint: str,
            headers: Mapping[str, str],
            body: bytes,
            timeout_seconds: float,
        ) -> Mapping[str, Any]:
            nonlocal round_trips, truncation_events
            remaining = _require_arm_remaining()
            round_trips += 1
            response = self._transport(
                endpoint=endpoint,
                headers=headers,
                body=body,
                timeout_seconds=min(float(timeout_seconds), remaining, 120.0),
            )
            usage = response.get("usage")
            round_prompt: int | None = None
            if isinstance(usage, Mapping):
                raw_prompt = usage.get("prompt_tokens")
                if isinstance(raw_prompt, int) and not isinstance(raw_prompt, bool) and raw_prompt >= 0:
                    round_prompt = raw_prompt
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    _record_usage_metric(key, usage.get(key))
                pt_details = usage.get("prompt_tokens_details")
                if isinstance(pt_details, Mapping):
                    _record_usage_metric("cached_tokens", pt_details.get("cached_tokens"))
                ct_details = usage.get("completion_tokens_details")
                if isinstance(ct_details, Mapping):
                    _record_usage_metric("reasoning_tokens", ct_details.get("reasoning_tokens"))
            per_round_prompt_tokens.append(round_prompt)
            choices = response.get("choices")
            finish_reason = (
                choices[0].get("finish_reason")
                if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], Mapping)
                else None
            )
            if response.get("truncated") is True or finish_reason == "context_length_exceeded":
                truncation_events += 1
            return response

        client = DoubaoExecToolClient(
            api_key=key,
            model=self.model_id,
            timeout_seconds=min(timeout_seconds, 120.0),
            transport=tracked_transport,
            endpoint=self._endpoint,
            allowed_models=ARK_BEHAVIOR_AND_FLASH_MODELS,
            provider_prefix="ark",
        )
        schema_hint = json.dumps(MICROBENCH_VERDICT_SCHEMA, separators=(",", ":"))
        system_prompt = (
            "You are executing a bounded LoopX verification microbenchmark inside an isolated fixture. "
            "Call AT MOST ONE exec_command tool per turn (never emit parallel tool calls in one step, "
            "and never chain shell commands with `;`, `&&`, `||`, `|`, or `cd`) when you need to read "
            "referenced files (`cat <file>`) or invoke exact LoopX CLI commands provided in the prompt. "
            "Never fabricate readings, never drop blocked Todos, failure_facts, or open_obligations, "
            "and never bypass a blocking user_gate. Set `spend_classification` accurately "
            "(`observed`: 'none' | 'read_only_receipt' | 'quota_spend_slot', `attempted`: bool, `confirmed`: bool). "
            "As soon as you have the required readings, reply immediately with ONLY ONE raw JSON object "
            "(no markdown code fences and no surrounding prose) matching this exact schema: "
            f"{schema_hint}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text},
        ]
        observed_commands: list[str] = []
        final_content: str | None = None

        try:
            for _ in range(self._max_steps):
                _require_arm_remaining()
                step: ExecToolStep = client.next_step(
                    messages,
                    tool_description=(
                        "Execute ONE single read-only command (`cat <path>` or `loopx ...`) per turn "
                        "inside the trial root; never return multiple tool calls in one step and never use `;`/`&&`/`cd`."
                    ),
                )
                if step.tool_call is None:
                    final_content = step.assistant_content
                    break
                rem_for_tool = _require_arm_remaining()
                label, tool_output = _execute_scoped_command(
                    step.tool_call.command,
                    trial_root=trial_root,
                    project_root=project_root,
                    source_root=self._source_root,
                    timeout_seconds=rem_for_tool,
                )
                observed_commands.append(label)
                append_exec_tool_exchange(messages, step.tool_call, tool_output)

            if final_content is None:
                _require_arm_remaining()
                final_content = client.next_final_content(
                    [
                        *messages,
                        {
                            "role": "user",
                            "content": "Return the final microbench_model_verdict_v0 JSON now without more tool calls.",
                        },
                    ]
                )
            _require_arm_remaining()
        except DoubaoActorTransportError as exc:
            status = "quota_exhausted" if exc.error_code == "provider_authentication_failed" else "error"
            return self._build_ark_outcome(
                status=status,
                error_code=exc.error_code,
                verdict=None,
                wire_bytes=wire_bytes,
                usage_totals=usage_totals,
                usage_reported_steps=usage_reported_steps,
                round_trips=round_trips,
                per_round_prompt_tokens=per_round_prompt_tokens,
                compaction_events=compaction_events,
                truncation_events=truncation_events,
                observed_commands=observed_commands,
                started=started,
            )

        parsed_verdict = _extract_verdict(final_content)
        verdict, status, error_code = _validate_adapter_verdict(
            parsed_verdict,
            expected_scenario_id=scenario_id,
        )
        return self._build_ark_outcome(
            status=status,
            error_code=error_code,
            verdict=verdict,
            wire_bytes=wire_bytes,
            usage_totals=usage_totals,
            usage_reported_steps=usage_reported_steps,
            round_trips=round_trips,
            per_round_prompt_tokens=per_round_prompt_tokens,
            compaction_events=compaction_events,
            truncation_events=truncation_events,
            observed_commands=observed_commands,
            started=started,
        )


def _opt_int(mapping: Mapping[str, object], key: str) -> int | None:
    val = mapping.get(key)
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def _parse_direct_loopx_command(cmd: str) -> str | None:
    tokens = loopx_command_tokens(cmd)
    if tokens is not None:
        return _summarize_loopx_command(tokens)
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    if (
        len(parts) == 3
        and Path(parts[0]).name in _POSIX_SHELL_WRAPPERS
        and parts[1] in {"-c", "-lc"}
    ):
        inner_tokens = loopx_command_tokens(parts[2])
        if inner_tokens is not None:
            return _summarize_loopx_command(inner_tokens)
    return None


def _count_codex_tool_events(stdout_text: str) -> tuple[int, list[str]]:
    tool_calls = 0
    observed: list[str] = []
    for line in stdout_text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, Mapping):
                item_type = str(item.get("type") or "")
                if item_type in {"command_execution", "local_shell_call", "function_call", "custom_tool_call"}:
                    tool_calls += 1
                    if item_type in {"function_call", "custom_tool_call"}:
                        tool_name = str(item.get("name") or "").strip()
                        if tool_name in _TYPED_SPEND_TOOL_NAMES:
                            observed.append(f"tool:{tool_name}")
                            continue
                    cmd = item.get("command")
                    if isinstance(cmd, str) and cmd.strip():
                        loopx_label = _parse_direct_loopx_command(cmd)
                        if loopx_label is not None:
                            observed.append(loopx_label)
                            continue
                    observed.append(item_type)
    return tool_calls, observed


class CodexGptMicrobenchRunner:
    """Run Tier-1 scenarios via isolated ``benchmark.runtime.codex`` + ``codex_session_usage``."""

    provider = "codex"

    def __init__(
        self,
        *,
        model: str = DEFAULT_CODEX_MODEL,
        effort: str = DEFAULT_CODEX_EFFORT,
        codex_bin: str = "codex",
        auth_json_path: Path | None = None,
        base_url: str = "",
        api_key: str = "",
        sandbox: str = "workspace-write",
        source_root: Path | None = None,
    ) -> None:
        self.model_id = model
        self._effort = effort
        self._codex_bin = codex_bin
        self._auth_json_path = auth_json_path
        self._base_url = base_url
        self._api_key = api_key
        self._sandbox = sandbox
        self._source_root = (source_root or Path(__file__).resolve().parents[2]).resolve()

    def _resolve_auth_mode(self) -> tuple[str, str, Path | None, str | None]:
        base_url = self._base_url or os.environ.get("OPENAI_BASE_URL", "")
        api_key = self._api_key or os.environ.get("OPENAI_API_KEY", "")
        if base_url and not api_key:
            return "", "", None, "openai_base_url_missing_key"
        if base_url and api_key:
            return base_url, api_key, None, None
        candidate_auth = self._auth_json_path
        if candidate_auth is None:
            env_auth = os.environ.get("CODEX_AUTH_JSON_PATH")
            if env_auth:
                candidate_auth = Path(env_auth)
        if candidate_auth is None or not candidate_auth.is_file():
            return "", "", None, "missing_codex_auth"
        return "", "", candidate_auth, None

    def run_case(
        self,
        *,
        scenario_id: str,
        prompt_text: str,
        trial_root: Path,
        project_root: Path,
        timeout_seconds: float = 90.0,
    ) -> ModelRunOutcome:
        timeout_seconds = require_positive_finite_float(timeout_seconds, label="timeout_seconds")
        started = time.monotonic()
        deadline = resolve_deadline(started + timeout_seconds) or (started + timeout_seconds)
        wire_bytes = len(prompt_text.encode("utf-8"))

        def _budget_exhausted_outcome() -> ModelRunOutcome:
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="error",
                error_code="codex_budget_exhausted",
                verdict=None,
                efficiency=EfficiencyMetrics(
                    wire_bytes=wire_bytes,
                    wall_seconds=round(time.monotonic() - started, 3),
                ),
                telemetry_source="codex_session_usage",
            )

        base_url, api_key, auth_src, auth_err = self._resolve_auth_mode()
        if auth_err is not None:
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="unavailable",
                error_code=auth_err,
                verdict=None,
                efficiency=EfficiencyMetrics(
                    wire_bytes=wire_bytes,
                    wall_seconds=round(time.monotonic() - started, 3),
                ),
                telemetry_source="codex_session_usage",
            )

        resolved_bin = (
            shutil.which(self._codex_bin)
            if os.path.sep not in self._codex_bin
            else self._codex_bin
        )
        if not resolved_bin or not Path(resolved_bin).is_file():
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status="unavailable",
                error_code="codex_binary_unavailable",
                verdict=None,
                efficiency=EfficiencyMetrics(
                    wire_bytes=wire_bytes,
                    wall_seconds=round(time.monotonic() - started, 3),
                ),
                telemetry_source="codex_session_usage",
            )

        if deadline - time.monotonic() <= 0.0:
            return _budget_exhausted_outcome()

        with tempfile.TemporaryDirectory(prefix="microbench-codex-run-") as run_tmp:
            tmp_path = Path(run_tmp)
            codex_home = tmp_path / "codex-home"
            codex_home.mkdir(parents=True, mode=0o700)
            if auth_src is not None:
                shutil.copyfile(auth_src, codex_home / "auth.json")
                (codex_home / "auth.json").chmod(0o600)
            prepare_codex_home(
                codex_home,
                execution=Execution(mode="plain", sandbox=self._sandbox),
                workspace=trial_root,
                model=self.model_id,
                effort=self._effort,
                base_url=base_url,
                api_key=api_key,
                wire_api="responses",
                skills=None,
                web_search="disabled",
            )
            env = process_environment(codex_home)
            if not base_url:
                env.pop("OPENAI_BASE_URL", None)
                env.pop("OPENAI_API_KEY", None)
            else:
                env["OPENAI_BASE_URL"] = base_url
                env["OPENAI_API_KEY"] = api_key
            existing_pp = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(self._source_root)
                if not existing_pp
                else f"{self._source_root}{os.pathsep}{existing_pp}"
            )
            # Ensure the isolated Python launcher from this venv stays reachable on PATH.
            py_dir = str(Path(sys.executable).resolve().parent)
            env["PATH"] = f"{py_dir}{os.pathsep}{env.get('PATH', '')}"

            schema_path = tmp_path / "verdict-schema.json"
            schema_path.write_text(json.dumps(MICROBENCH_VERDICT_SCHEMA), encoding="utf-8")
            output_path = tmp_path / "verdict-out.json"
            command = [
                str(resolved_bin),
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                self._sandbox,
                "-C",
                str(project_root),
                "--add-dir",
                str(trial_root),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--json",
                "-",
            ]
            remaining_before_exec = deadline - time.monotonic()
            if remaining_before_exec <= 0.0:
                return _budget_exhausted_outcome()

            timed_out = False
            stdout_text = ""
            stderr_text = ""
            returncode = 0
            try:
                completed = subprocess.run(
                    command,
                    input=prompt_text,
                    env=env,
                    cwd=project_root,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=remaining_before_exec,
                )
                stdout_text = completed.stdout
                stderr_text = completed.stderr
                returncode = completed.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout_text = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
                stderr_text = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
                returncode = -1

            failure_obs = CodexFailureObservation()
            complete_stream = True
            for line in stdout_text.splitlines():
                if line.strip() and not failure_obs.observe_stdout(line):
                    complete_stream = False
            for line in stderr_text.splitlines():
                if line.strip():
                    failure_obs.observe_stderr(line)

            rollout_metrics = collect_codex_rollout_metrics(codex_home / "sessions")
            tool_calls, observed_cmds = _count_codex_tool_events(stdout_text)
            wall = round(time.monotonic() - started, 3)

            parsed_verdict: dict[str, Any] | None = None
            if not timed_out and output_path.is_file():
                parsed_verdict = _extract_verdict(
                    output_path.read_text(encoding="utf-8", errors="replace")
                )

            failure_kind = (
                failure_obs.failure_kind(complete=complete_stream)
                if returncode != 0 and not timed_out
                else None
            )
            status = "ok"
            error_code: str | None = None
            verdict: dict[str, Any] | None = None
            if timed_out:
                status = "error"
                error_code = "codex_timeout"
            elif returncode != 0:
                status = "quota_exhausted" if failure_kind == "quota_exhausted" else "error"
                error_code = failure_kind or "codex_exec_failed"
            else:
                verdict, status, error_code = _validate_adapter_verdict(
                    parsed_verdict,
                    expected_scenario_id=scenario_id,
                )

            verified_round_trips = _opt_int(rollout_metrics, "round_trips")
            raw_per_turn = rollout_metrics.get("per_turn_context_tokens")
            per_turn_tuple = (
                tuple(int(v) for v in raw_per_turn if isinstance(v, int) and not isinstance(v, bool))
                if isinstance(raw_per_turn, (list, tuple)) and raw_per_turn
                else None
            )
            raw_ratio = rollout_metrics.get("peak_window_ratio")
            peak_ratio = (
                float(raw_ratio)
                if isinstance(raw_ratio, (int, float)) and not isinstance(raw_ratio, bool)
                else None
            )
            raw_win_src = rollout_metrics.get("occupancy_window_source")
            raw_coverage = rollout_metrics.get("telemetry_coverage")
            codex_coverage: dict[str, Any] | None = (
                dict(raw_coverage) if isinstance(raw_coverage, Mapping) else None
            )
            eff = EfficiencyMetrics(
                wire_bytes=wire_bytes,
                prompt_tokens=_opt_int(rollout_metrics, "prompt_tokens"),
                cached_tokens=_opt_int(rollout_metrics, "cached_tokens"),
                completion_tokens=_opt_int(rollout_metrics, "completion_tokens"),
                reasoning_tokens=_opt_int(rollout_metrics, "reasoning_tokens"),
                total_tokens=_opt_int(rollout_metrics, "total_tokens"),
                tool_calls=tool_calls,
                round_trips=(
                    verified_round_trips
                    if verified_round_trips is not None and verified_round_trips > 0
                    else None
                ),
                wall_seconds=wall,
                initial_turn_context_tokens=_opt_int(rollout_metrics, "initial_turn_context_tokens"),
                per_turn_context_tokens=per_turn_tuple,
                peak_context_tokens=_opt_int(rollout_metrics, "peak_context_tokens"),
                effective_context_window=_opt_int(rollout_metrics, "effective_context_window"),
                min_headroom_tokens=_opt_int(rollout_metrics, "min_headroom_tokens"),
                peak_window_ratio=peak_ratio,
                compaction_events=_opt_int(rollout_metrics, "compaction_events"),
                truncation_events=_opt_int(rollout_metrics, "truncation_events"),
                occupancy_measurement_kind=str(rollout_metrics.get("occupancy_measurement_kind") or "unknown"),
                occupancy_window_source=str(raw_win_src) if raw_win_src is not None else None,
            )
            return ModelRunOutcome(
                model_id=self.model_id,
                provider=self.provider,
                status=status,
                error_code=error_code,
                verdict=verdict,
                efficiency=eff,
                observed_commands=tuple(observed_cmds),
                telemetry_source=str(rollout_metrics.get("source") or "codex_session_usage"),
                telemetry_coverage=codex_coverage,
            )
