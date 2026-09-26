"""Read-only Codex App delivery canary; never a scheduler admission receipt."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Any

OBSERVATION_SCHEMA = "codex_app_prompt_delivery_observation_v1"
RESULT_SCHEMA = "codex_app_prompt_delivery_canary_v0"
MAX_OBSERVATION_BYTES = 4096
MAX_MANIFEST_BYTES = 256 * 1024
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_FIELDS = frozenset(
    {
        "schema_version",
        "automation_id",
        "goal_id",
        "agent_id",
        "thread_id",
        "turn_id",
        "prompt_sha256",
        "turn_started_at_ms",
        "agent_activity_observed",
        "observed_at_ms",
    }
)
_FALLBACK = (
    "Inspect the selected scheduled turn in the host. If its prompt or agent-start "
    "observation is unavailable, test the generated heartbeat body in a visible "
    "session with the normal LoopX quota guard. Installation/ACK is configuration "
    "evidence only; do not spend quota or repeat external effects for this check."
)


def _result(reason: str, *, matched: bool = False) -> dict[str, Any]:
    # Do not echo input values, prompts, paths, host errors or observation bodies.
    return {
        "schema_version": RESULT_SCHEMA,
        "ok": matched,
        "status": "host_observation_matched"
        if matched
        else "host_prompt_delivery_unverified",
        "reason_code": reason,
        "read_only": True,
        "grants_execution_authority": False,
        "fallback": None if matched else _FALLBACK,
    }


def _read_bounded(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("input exceeds size limit")
    return data.decode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-standard JSON number")


def check_codex_app_delivery(
    *,
    manifest_path: Path,
    observation_path: Path | None,
    automation_id: str,
    goal_id: str,
    agent_id: str,
    thread_id: str,
    turn_id: str,
    now_ms: int,
    max_age_seconds: int = 900,
    observe_host: bool = False,
    codex_bin: str = "codex",
) -> dict[str, Any]:
    """Compare a caller-selected turn with a trusted host observer's compact facts.

    The caller must obtain the turn identity independently of the observation.
    This checks consistency, not authenticity; no host observer is synthesized
    from a manifest, scheduler ACK, process exit status, or an agent claim.
    """
    expected = {
        "automation_id": automation_id,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "thread_id": thread_id,
        "turn_id": turn_id,
    }
    if any(
        not isinstance(v, str) or not _IDENTITY.fullmatch(v) for v in expected.values()
    ):
        return _result("invalid_expected_identity")
    if (
        type(now_ms) is not int
        or not 0 < now_ms <= 2**53 - 1
        or type(max_age_seconds) is not int
        or not 1 <= max_age_seconds <= 86400
    ):
        return _result("invalid_observation_window")
    try:
        manifest = tomllib.loads(_read_bounded(manifest_path, MAX_MANIFEST_BYTES))
    except (OSError, UnicodeError, ValueError):
        return _result("manifest_unreadable_or_invalid")
    if manifest.get("id") != automation_id:
        return _result("manifest_automation_mismatch")
    if manifest.get("kind") != "heartbeat":
        return _result("unsupported_automation_kind")
    if manifest.get("status") != "ACTIVE":
        return _result("automation_not_active")
    if manifest.get("target_thread_id") != thread_id:
        return _result("manifest_thread_mismatch")
    prompt = manifest.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _result("installed_prompt_missing")
    if observe_host and observation_path is not None:
        return _result("conflicting_observation_sources")
    if observe_host:
        from .codex_app_delivery_observer import (
            HostObservationError,
            observe_codex_app_delivery,
        )

        try:
            observation = observe_codex_app_delivery(
                codex_bin=codex_bin,
                expected=expected,
                observed_at_ms=now_ms,
            )
        except HostObservationError as exc:
            return _result(exc.reason_code)
    elif observation_path is None:
        return _result("host_observation_missing")
    else:
        try:
            observation = json.loads(
                _read_bounded(observation_path, MAX_OBSERVATION_BYTES),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except FileNotFoundError:
            return _result("host_observation_missing")
        except (OSError, UnicodeError, ValueError, RecursionError):
            return _result("host_observation_invalid")
    if not isinstance(observation, dict) or set(observation) != _FIELDS:
        return _result("host_observation_invalid")
    if observation["schema_version"] != OBSERVATION_SCHEMA:
        return _result("host_observation_schema_mismatch")
    if any(observation[key] != value for key, value in expected.items()):
        return _result("host_observation_identity_mismatch")
    digest = observation["prompt_sha256"]
    if digest is None:
        return _result("prompt_delivery_not_observed")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        return _result("host_observation_invalid")
    if digest != hashlib.sha256(prompt.encode("utf-8")).hexdigest():
        return _result("host_prompt_digest_mismatch")
    started = observation["turn_started_at_ms"]
    activity = observation["agent_activity_observed"]
    observed = observation["observed_at_ms"]
    if activity is False or started is None:
        return _result("agent_start_not_observed")
    if type(activity) is not bool:
        return _result("host_observation_invalid")
    if any(type(t) is not int or not 0 < t <= 2**53 - 1 for t in (started, observed)):
        return _result("host_observation_invalid")
    if not started <= observed <= now_ms:
        return _result("host_observation_time_mismatch")
    # Re-observing an old turn must not extend its freshness.
    if now_ms - started > max_age_seconds * 1000:
        return _result("host_observation_stale")
    return _result("selected_turn_delivery_and_start_matched", matched=True)
