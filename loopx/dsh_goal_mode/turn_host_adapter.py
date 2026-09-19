"""Thin DeepSeek Harness host adapter for the LoopX governed Turn contract.

This module is the canonical home of the dsh goal-mode adapter; the historical
``scripts/dsh_turn_host_adapter.py`` launcher still works and re-exports this
module for backward compatibility.

LoopX runs this adapter with one ``loopx_turn_host_request_v0`` JSON object on
stdin. The adapter:

1. extracts the bounded action text from the signed Turn envelope,
2. starts a DeepSeek Harness SDK runtime (or a compatible runner for tests),
3. asks dsh to execute one bounded work segment and return a schema-constrained
   result in its final assistant message,
4. emits exactly one ``loopx_turn_result_v0`` JSON object on stdout.

It does not read goal/todo state, build prompts from todo ids, write LoopX
state, spend quota, or validate its own work. Task body delivery and authority
stay in ``loopx turn run-once``; this is a dumb translation layer.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from ..control_plane.turn_driver.host_candidate import (
    ACCEPTED_RESULT_KINDS as ACCEPTED_RESULT_KINDS,
    COMPLETED_PHASES as COMPLETED_PHASES,
    LOOPX_TURN_HOST_REQUEST_SCHEMA as LOOPX_TURN_HOST_REQUEST_SCHEMA,
    LOOPX_TURN_RESULT_SCHEMA as LOOPX_TURN_RESULT_SCHEMA,
    MATERIAL_KINDS as MATERIAL_KINDS,
    TEXT_LIMITS as TEXT_LIMITS,
    _canonical_hash,
    _mapping,
    build_result as _build_host_result,
    extract_action_text as extract_action_text,
    extract_turn_authority as extract_turn_authority,
    parse_model_json as parse_model_json,
    render_prompt as render_prompt,
)
from ..control_plane.turn_driver.execution_profile import (
    MANAGED_MODEL_DEFAULT,
    MANAGED_PROVIDER_DEFAULT,
    MANAGED_REASONING_EFFORT_DEFAULT,
    managed_execution_profile,
    managed_profile_unavailable_reason,
    require_supported_reasoning_effort,
)
from ..control_plane.turn_driver.host_binding import (
    DEFAULT_DSH_OUTPUT_TOKEN_LIMIT,
    dsh_output_token_budget,
)
from ..control_plane.turn_driver.host_failure import BuiltInHostError

from .host_failure_map import classify_dsh_failure, classify_dsh_terminal_reason

# The adapter reads the managed execution profile for these three fields; the
# constants re-export the product defaults for callers that only need the
# shipped values. Nothing here reads the process environment at import time, so
# a caller that changes its environment still gets the value it just set.
DEFAULT_MODEL = MANAGED_MODEL_DEFAULT
DEFAULT_PROVIDER = MANAGED_PROVIDER_DEFAULT
DEFAULT_REASONING_EFFORT = MANAGED_REASONING_EFFORT_DEFAULT
DEFAULT_SESSION_ROOT_NAME = ".dsh-sessions"


def build_result(
    request: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
    *,
    fallback_reason: str = "",
) -> dict[str, Any]:
    """Preserve the published DSH adapter's diagnostic wording."""
    if candidate is None and not fallback_reason:
        fallback_reason = (
            "DeepSeek Harness returned no typed JSON result; rerun or inspect the dsh session."
        )
    return _build_host_result(
        request, candidate, fallback_reason=fallback_reason, host_name="DeepSeek Harness",
    )


def build_sdk_config(
    *,
    provider: str,
    model: str,
    reasoning_effort: str | None,
    workspace: Path,
    dsh_home: Path,
    max_tokens: int | None,
    cordis: Path | None,
    runtime_bin: str | None,
    request_timeout_seconds: float | None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build kwargs for the current ``DeepSeekHarnessConfig`` surface.

    The current SDK config calls its explicit local runtime root ``dsh_home``;
    the adapter's legacy ``session_root`` spelling maps to that field. The
    runtime binary maps to ``dsh_bin`` and a cordis file rides as one
    ``patches`` entry. ``reasoning_effort`` rides the SDK's own field so the
    effort the operator configured reaches the provider request instead of
    staying a LoopX-side note.

    ``env`` carries caller-owned runtime variables, such as the dsh permission
    mode a Chat channel pins for its own segments. A caller that pins nothing
    passes ``None`` and the runtime keeps the operator's composed default.
    """

    config: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "cwd": str(workspace),
        "dsh_home": str(dsh_home),
    }
    if reasoning_effort is not None:
        config["reasoning_effort"] = reasoning_effort
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    if cordis is not None:
        config["patches"] = (str(cordis.expanduser().resolve()),)
    if runtime_bin is not None:
        config["dsh_bin"] = runtime_bin
    if request_timeout_seconds is not None:
        config["request_timeout_seconds"] = request_timeout_seconds
    if env:
        config["env"] = dict(env)
    return config


class DshHostResultError(ValueError):
    """The SDK or an explicit runner returned an invalid result shape."""


def _result_field(result: object, name: str) -> object:
    if isinstance(result, Mapping):
        return result.get(name)
    return getattr(result, name, None)


def _last_turn_end_reason(events: object) -> dict[str, Any] | None:
    if events is None:
        return None
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise DshHostResultError("dsh runner events must be a sequence or null")
    if not all(isinstance(event, Mapping) for event in events):
        raise DshHostResultError("dsh runner events must contain only objects")
    for event in reversed(events):
        if event.get("type") != "turn/end":
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            raise DshHostResultError("dsh turn/end event requires object data")
        reason = data.get("reason")
        if not isinstance(reason, Mapping) or not isinstance(reason.get("kind"), str):
            raise DshHostResultError(
                "dsh turn/end event requires object reason with string kind"
            )
        return dict(reason)
    return None


def normalize_runner_outcome(value: object) -> dict[str, Any]:
    """Normalize and validate one SDK or explicit-runner result.

    The real SDK derives ``finish_reason`` from the last ``turn/end`` reason.
    Enforcing that invariant prevents a contradictory result from selecting a
    success path while carrying a terminal provider failure, or vice versa.
    Legacy runners may still return a bare final-response string.
    """

    if isinstance(value, str):
        return {"final_response": value, "finish_reason": None, "events": []}

    final_response = _result_field(value, "final_response")
    if not isinstance(final_response, str):
        raise DshHostResultError("dsh runner result requires string final_response")

    finish_reason = _result_field(value, "finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise DshHostResultError("dsh runner finish_reason must be a string or null")

    raw_events = _result_field(value, "events")
    terminal_reason = _last_turn_end_reason(raw_events)
    terminal_kind = str(terminal_reason["kind"]) if terminal_reason is not None else None
    if finish_reason is None:
        finish_reason = terminal_kind
    elif terminal_kind is not None and terminal_kind != finish_reason:
        raise DshHostResultError(
            "dsh runner finish_reason does not match the last turn/end reason"
        )

    events = (
        []
        if raw_events is None
        else list(raw_events)
        if isinstance(raw_events, Sequence) and not isinstance(raw_events, (str, bytes))
        else []  # Unreachable: _last_turn_end_reason validates the value.
    )
    return {
        "final_response": final_response,
        "finish_reason": finish_reason,
        "events": events,
    }


def run_dsh_turn(
    *,
    prompt: str,
    session_id: str,
    workspace: Path,
    session_root: Path,
    provider: str,
    model: str,
    reasoning_effort: str | None,
    max_tokens: int | None,
    cordis: Path | None,
    runtime_bin: str | None,
    request_timeout_seconds: float | None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one bounded DeepSeek Harness session through the Python SDK.

    Returns the terminal outcome (final response, finish reason, events)
    instead of only the final response: the SDK reports provider failures
    through ``RunResult.finish_reason`` and the last ``turn/end`` event
    rather than raising a Python exception.
    """

    try:
        from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
    except ImportError as exc:
        raise RuntimeError(
            "deepseek-harness-sdk is required; install it with "
            "`python -m pip install 'loopx[deepseek-harness]'`"
        ) from exc

    config = build_sdk_config(
        provider=provider,
        model=model,
        reasoning_effort=(
            require_supported_reasoning_effort(reasoning_effort)
            if reasoning_effort is not None
            else None
        ),
        workspace=workspace,
        dsh_home=session_root,
        max_tokens=max_tokens,
        cordis=cordis,
        runtime_bin=runtime_bin,
        request_timeout_seconds=request_timeout_seconds,
        env=env,
    )
    with DeepSeekHarness(DeepSeekHarnessConfig(**config)) as harness:
        result = harness.run(prompt, session_id=session_id)
    return normalize_runner_outcome(result)


def terminal_error_reason(outcome: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract the structured failure reason from a terminal outcome.

    Returns the last ``turn/end`` reason when the run ended in error, or a
    minimal reason when ``finish_reason == "error"`` arrives without one.
    """

    reason: dict[str, Any] | None = None
    for event in reversed(list(outcome.get("events") or [])):
        if not isinstance(event, Mapping) or event.get("type") != "turn/end":
            continue
        data = _mapping(event.get("data"))
        candidate = _mapping(data.get("reason"))
        if candidate:
            reason = candidate
        break
    if reason is not None and reason.get("kind") == "error":
        return reason
    if outcome.get("finish_reason") == "error":
        # A contradictory non-error turn/end reason must not leak its fields
        # into failure classification.
        return {"kind": "error"}
    return None


def terminal_output_budget_state(
    outcome: Mapping[str, Any],
) -> Literal["partial", "no_final"] | None:
    """Classify a token-limited terminal outcome without accepting a fragment.

    DeepSeek Harness 0.1.5rc1 defines ``maxTokens`` as a per-model-request
    output cap and counts reasoning inside ``outputTokens``. A max-token stop
    therefore cannot prove that the final response is complete, even when the
    SDK exposes a non-empty last assistant fragment.
    """

    if outcome.get("finish_reason") != "max-tokens":
        return None
    final_response = outcome.get("final_response")
    return (
        "partial"
        if isinstance(final_response, str) and final_response.strip()
        else "no_final"
    )


def load_dsh_runner(path: Path) -> Callable[..., object]:
    """Load an explicit runner hook exposing ``run_dsh_turn``.

    The runner module must accept the keyword set this adapter always supplies:
    ``prompt``, ``session_id``, ``workspace``, ``session_root``,
    ``provider``, ``model``, ``reasoning_effort``, ``max_tokens``,
    ``cordis``, ``runtime_bin`` and ``request_timeout_seconds``. ``env`` is an
    optional extra the default runner accepts for callers that pin their own
    runtime variables. A runner may return either the legacy bare
    final-response string or an outcome mapping carrying
    ``final_response``/``finish_reason``/``events``. This seam is primarily
    used by hermetic smokes so the repository does not need a real DeepSeek
    Harness SDK/runtime installed.
    """

    spec = importlib.util.spec_from_file_location("dsh_turn_runner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load dsh runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    runner = getattr(module, "run_dsh_turn", None)
    if not callable(runner):
        raise DshHostResultError(f"{path} must define callable run_dsh_turn(...)")
    return cast(Callable[..., object], runner)


def _default_session_root(workspace: Path) -> Path:
    return workspace / ".local" / DEFAULT_SESSION_ROOT_NAME


def resolve_dsh_home(workspace: Path, configured: Path | None = None) -> Path:
    """Resolve the explicit SDK home without falling back to a user-global path.

    Home precedence is an explicit value, then ``DSH_HOME``, then the
    workspace-local default. The steward channel resolves its own home through
    this same function so the channel and the bounded Turn cannot end up on two
    different dsh homes for the same workspace.
    """

    if configured is not None:
        return configured.expanduser().resolve()
    environment = os.environ.get("DSH_HOME", "").strip()
    if environment:
        return Path(environment).expanduser().resolve()
    return _default_session_root(workspace).expanduser().resolve()


@dataclass(frozen=True)
class DshHostConfig:
    """Owner-local runtime configuration for one bounded dsh host attempt.

    ``provider``/``model``/``reasoning_effort`` default to ``None``, which means
    "use the resolved managed execution profile". A CLI flag or an explicit
    value overrides one field without touching the others, and the profile is
    resolved when the attempt starts rather than at import time.
    """

    workspace: Path
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    # Keep one bounded LoopX default instead of inheriting the selected
    # adapter's much larger route default. This is a per-request cap, not a
    # whole-Turn or tool-call budget.
    max_tokens: int | None = DEFAULT_DSH_OUTPUT_TOKEN_LIMIT
    dsh_home: Path | None = None
    cordis: Path | None = None
    runtime_bin: str | None = None
    request_timeout_seconds: float | None = None
    dsh_runner: Path | None = None

    def resolved_profile(self) -> dict[str, Any]:
        """Return the profile fields this attempt would use, with their source."""

        return managed_execution_profile(
            provider=self.provider,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
        )


def _derive_session_id(request: Mapping[str, Any], turn_key: str) -> str:
    planned_session = _mapping(request.get("session"))
    context_policy = _mapping(planned_session.get("context_policy"))
    if context_policy.get("mode") == "fresh":
        # A fresh context is scoped to one LoopX iteration. The turn key is
        # stable for retries of that iteration but changes for the next one,
        # so dsh cannot silently resume an earlier iteration's local session.
        return "dsh-iteration-v1-" + _canonical_hash(
            [turn_key]
        ).removeprefix("sha256:")

    # Keep the opaque dsh session keyed by the same (goal, agent, todo) lineage
    # LoopX already uses for the Turn transaction. The exact value is a local
    # adapter concern and must not enter public LoopX state. Encode every
    # component position before hashing: delimiter-joined ids can collide when
    # an id itself contains the delimiter.
    envelope = _mapping(request.get("turn_envelope"))
    action = _mapping(envelope.get("action"))
    selected_todo = _mapping(action.get("selected_todo"))
    lineage = [
        envelope.get("goal_id"),
        envelope.get("agent_id"),
        selected_todo.get("todo_id"),
    ]
    # Keep the legacy truthiness boundary: empty or non-string false-like
    # values are not lineage identities, while their positions stay encoded.
    lineage = [value if value else None for value in lineage]
    if any(lineage):
        return "dsh-lineage-v1-" + _canonical_hash(lineage).removeprefix("sha256:")
    return f"dsh-{turn_key.removeprefix('sha256:')[:24]}"


def _execute_turn_host_request(
    request: Mapping[str, Any],
    authority: Mapping[str, Any],
    *,
    config: DshHostConfig,
    terminal_errors_as_host_failure: bool,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    workspace = config.workspace.expanduser().resolve()
    dsh_home = resolve_dsh_home(workspace, config.dsh_home)
    dsh_home.mkdir(parents=True, exist_ok=True)
    session_id = _derive_session_id(request, str(request.get("turn_key") or ""))
    profile = config.resolved_profile()
    profile_reason = managed_profile_unavailable_reason(profile)
    if profile_reason is not None:
        # The endpoint is known to reject this effort, so this is a contract
        # refusal rather than a provider failure a same-Turn retry could clear.
        raise BuiltInHostError(
            "dsh_execution_profile_rejected",
            failure_kind="contract_rejected",
        )
    output_token_budget = dsh_output_token_budget(config.max_tokens)
    if not output_token_budget["valid"]:
        raise BuiltInHostError(
            "dsh_output_token_limit_rejected",
            failure_kind="contract_rejected",
        )

    try:
        prompt = render_prompt(authority)
        runner = (
            load_dsh_runner(config.dsh_runner.expanduser().resolve())
            if config.dsh_runner is not None
            else run_dsh_turn
        )
        outcome = normalize_runner_outcome(
            runner(
                prompt=prompt,
                session_id=session_id,
                workspace=workspace,
                # Preserve the established runner keyword while mapping the
                # path to the current SDK's explicit dsh_home field.
                session_root=dsh_home,
                provider=str(profile["provider"]),
                model=str(profile["model"]),
                reasoning_effort=str(profile["reasoning_effort"]),
                max_tokens=output_token_budget["max_tokens"],
                cordis=config.cordis,
                runtime_bin=config.runtime_bin,
                request_timeout_seconds=config.request_timeout_seconds,
            )
        )
    except DshHostResultError as exc:
        raise BuiltInHostError(
            "dsh_host_result_rejected",
            failure_kind="contract_rejected",
        ) from exc
    except Exception as exc:  # noqa: BLE001 - adapter fails closed at boundary
        raise BuiltInHostError(
            "dsh_execution_failed",
            failure_kind=classify_dsh_failure(exc),
        ) from exc

    failure_reason = terminal_error_reason(outcome)
    if failure_reason is not None and terminal_errors_as_host_failure:
        raise BuiltInHostError(
            "dsh_execution_failed",
            failure_kind=classify_dsh_terminal_reason(failure_reason),
        )

    output_budget_state = terminal_output_budget_state(outcome)
    if output_budget_state is not None:
        reason = f"dsh_output_budget_exhausted_{output_budget_state}"
        if terminal_errors_as_host_failure:
            # Deliberately non-retryable: the SDK has no whole-Turn remaining
            # budget or final-response reserve proof, so repeating the same
            # call would be a blind rerun rather than a bounded recovery.
            raise BuiltInHostError(
                reason,
                failure_kind="output_budget_exhausted",
            )
        return build_result(
            request,
            {
                "result_kind": "iteration_failed",
                "classification": reason,
                "summary": (
                    "DeepSeek Harness exhausted the per-request output budget "
                    "before a trustworthy typed result was available."
                ),
                "next_action": (
                    "Inspect the retained local session and start a fresh "
                    "bounded recovery only with an explicit remaining budget "
                    "and reusable evidence; do not blindly rerun the request."
                ),
                "vision_unchanged_reason": (
                    "the token-limited host response was not admitted as progress"
                ),
            },
        )

    try:
        candidate = parse_model_json(outcome["final_response"])
        if candidate is None and warn is not None:
            warn("adapter: dsh final response did not contain a JSON result object")
        return build_result(
            request,
            candidate,
            fallback_reason=(
                "dsh returned no typed JSON result; inspect the local dsh session"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - result contract fails closed
        raise BuiltInHostError(
            "dsh_host_result_rejected",
            failure_kind="contract_rejected",
        ) from exc


def run_dsh_host(
    request: Mapping[str, Any],
    *,
    config: DshHostConfig,
) -> dict[str, Any]:
    """Run one bounded dsh attempt as an in-process LoopX host runner.

    Failures surface as :class:`BuiltInHostError` with a typed failure kind so
    the Turn journal records retryability instead of a bare ``unknown``.
    """

    if (
        not isinstance(request, Mapping)
        or request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA
    ):
        raise BuiltInHostError(
            "dsh_request_schema_mismatch",
            failure_kind="contract_rejected",
        )
    try:
        authority = extract_turn_authority(request)
    except ValueError as exc:
        raise BuiltInHostError(
            "dsh_turn_authority_rejected",
            failure_kind="contract_rejected",
        ) from exc
    return _execute_turn_host_request(
        request,
        authority,
        config=config,
        terminal_errors_as_host_failure=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        default=None,
        help=f"Provider for this attempt; defaults to the managed execution profile ({DEFAULT_PROVIDER}).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Model for this attempt; defaults to the managed execution profile ({DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help=(
            "Reasoning effort for this attempt; defaults to the managed "
            f"execution profile ({DEFAULT_REASONING_EFFORT})."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_DSH_OUTPUT_TOKEN_LIMIT,
        help=(
            "Per-model-request output-token cap; defaults to the bounded "
            f"LoopX value ({DEFAULT_DSH_OUTPUT_TOKEN_LIMIT}), not a whole-Turn budget."
        ),
    )
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument(
        "--dsh-home",
        "--session-root",
        dest="dsh_home",
        default=None,
        help=(
            "Explicit DeepSeek Harness home. --session-root remains a "
            "compatibility alias; defaults to DSH_HOME or "
            "<workspace>/.local/.dsh-sessions."
        ),
    )
    parser.add_argument("--cordis", default=None)
    parser.add_argument("--runtime-bin", default=None)
    parser.add_argument("--request-timeout-seconds", type=float, default=None)
    parser.add_argument(
        "--dsh-runner",
        default=None,
        help=(
            "Explicit runner hook intended for hermetic tests; this is not a "
            "permission boundary."
        ),
    )
    args = parser.parse_args(argv)

    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"adapter: invalid request JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if not isinstance(request, dict) or request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA:
        print("adapter: stdin is not a loopx_turn_host_request_v0 object", file=sys.stderr)
        return 2

    try:
        authority = extract_turn_authority(request)
    except ValueError as exc:
        print(f"adapter: invalid TurnEnvelope authority: {exc}", file=sys.stderr)
        return 2

    config = DshHostConfig(
        workspace=Path(args.workspace),
        provider=args.provider,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        max_tokens=args.max_tokens,
        dsh_home=Path(args.dsh_home) if args.dsh_home else None,
        cordis=Path(args.cordis).expanduser().resolve() if args.cordis else None,
        runtime_bin=args.runtime_bin,
        request_timeout_seconds=args.request_timeout_seconds,
        dsh_runner=Path(args.dsh_runner) if args.dsh_runner else None,
    )
    try:
        result = _execute_turn_host_request(
            request,
            authority,
            config=config,
            terminal_errors_as_host_failure=False,
            warn=lambda message: print(message, file=sys.stderr),
        )
    except BuiltInHostError as exc:
        cause = exc.__cause__
        detail = (
            f"{type(cause).__name__}: {cause}" if cause is not None else exc.reason
        )
        print(f"adapter: dsh execution failed: {detail}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
