"""Fail-closed continuity checks for benchmark runtime closeout."""

from __future__ import annotations

from enum import Enum
from typing import Any
from ...control_plane.content_digest import BARE_SHA256_PATTERN

BENCHMARK_RUNTIME_CONTINUITY_SCHEMA_VERSION = "benchmark_runtime_continuity_v0"
_SHA256_DIGEST = BARE_SHA256_PATTERN


class BenchmarkEventWindowState(str, Enum):
    """Provider-qualified relationship between runtime events and one run."""

    QUALIFIED = "qualified"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    OUTSIDE_LAUNCH_WINDOW = "outside_launch_window"


class BenchmarkRuntimeContinuityClassification(str, Enum):
    """Why a terminal closeout is or is not safe to write."""

    INPUT_INVALID = "continuity_input_invalid"
    QUALIFIED = "qualified"
    GENERATION_MISMATCH = "generation_mismatch"
    RUNTIME_ARTIFACT_MISMATCH = "runtime_artifact_mismatch"
    EVENT_WINDOW_MISSING = "event_window_missing"
    EVENT_WINDOW_AMBIGUOUS = "event_window_ambiguous"
    EVENT_WINDOW_OUTSIDE_LAUNCH = "event_window_outside_launch"


class BenchmarkRuntimeContinuityTransition(str, Enum):
    """Provider-owned transition selected by the continuity gate."""

    REPAIR_CONTINUITY_EVIDENCE = "repair_continuity_evidence"
    ACCEPT_CLOSEOUT = "accept_closeout"
    ROUTE_TO_LAUNCH_GENERATION = "route_to_launch_generation"
    REJECT_RUNTIME_ARTIFACT = "reject_runtime_artifact"
    REPAIR_EVENT_WINDOW_EVIDENCE = "repair_event_window_evidence"


def _normalize_digest(value: str, *, field: str) -> str:
    digest = str(value).strip()
    if not _SHA256_DIGEST.fullmatch(digest):
        raise ValueError(f"invalid_{field}")
    return digest


def _coerce_event_window_state(
    value: str | BenchmarkEventWindowState,
) -> BenchmarkEventWindowState:
    try:
        return BenchmarkEventWindowState(value)
    except ValueError as exc:
        raise ValueError("invalid_event_window_state") from exc


def build_benchmark_runtime_continuity(
    *,
    launch_runtime_digest: str,
    closeout_runtime_digest: str,
    launch_generation_digest: str,
    closeout_generation_digest: str,
    event_window_state: str | BenchmarkEventWindowState,
) -> dict[str, Any]:
    """Compare launch and closeout facts without emitting private identities.

    The runner owns artifact generation, event attribution, and all writes. This
    reducer only allows a closeout when its content-addressed runtime artifact and
    generation match launch and the provider has qualified the required event
    window. Raw digests and event payloads are never copied into the receipt.
    """

    launch_runtime = _normalize_digest(
        launch_runtime_digest,
        field="launch_runtime_digest",
    )
    closeout_runtime = _normalize_digest(
        closeout_runtime_digest,
        field="closeout_runtime_digest",
    )
    launch_generation = _normalize_digest(
        launch_generation_digest,
        field="launch_generation_digest",
    )
    closeout_generation = _normalize_digest(
        closeout_generation_digest,
        field="closeout_generation_digest",
    )
    window = _coerce_event_window_state(event_window_state)

    runtime_matches = launch_runtime == closeout_runtime
    generation_matches = launch_generation == closeout_generation
    event_window_qualified = window is BenchmarkEventWindowState.QUALIFIED

    if not generation_matches:
        classification = BenchmarkRuntimeContinuityClassification.GENERATION_MISMATCH
        transition = BenchmarkRuntimeContinuityTransition.ROUTE_TO_LAUNCH_GENERATION
    elif not runtime_matches:
        classification = (
            BenchmarkRuntimeContinuityClassification.RUNTIME_ARTIFACT_MISMATCH
        )
        transition = BenchmarkRuntimeContinuityTransition.REJECT_RUNTIME_ARTIFACT
    elif window is BenchmarkEventWindowState.MISSING:
        classification = BenchmarkRuntimeContinuityClassification.EVENT_WINDOW_MISSING
        transition = BenchmarkRuntimeContinuityTransition.REPAIR_EVENT_WINDOW_EVIDENCE
    elif window is BenchmarkEventWindowState.AMBIGUOUS:
        classification = BenchmarkRuntimeContinuityClassification.EVENT_WINDOW_AMBIGUOUS
        transition = BenchmarkRuntimeContinuityTransition.REPAIR_EVENT_WINDOW_EVIDENCE
    elif window is BenchmarkEventWindowState.OUTSIDE_LAUNCH_WINDOW:
        classification = (
            BenchmarkRuntimeContinuityClassification.EVENT_WINDOW_OUTSIDE_LAUNCH
        )
        transition = BenchmarkRuntimeContinuityTransition.REPAIR_EVENT_WINDOW_EVIDENCE
    else:
        classification = BenchmarkRuntimeContinuityClassification.QUALIFIED
        transition = BenchmarkRuntimeContinuityTransition.ACCEPT_CLOSEOUT

    qualified = classification is BenchmarkRuntimeContinuityClassification.QUALIFIED
    return {
        "schema_version": BENCHMARK_RUNTIME_CONTINUITY_SCHEMA_VERSION,
        "classification": classification.value,
        "qualified": qualified,
        "closeout_write_allowed": qualified,
        "runtime_artifact_matches": runtime_matches,
        "generation_matches": generation_matches,
        "event_window_state": window.value,
        "event_window_qualified": event_window_qualified,
        "recommended_transition": transition.value,
        "public_boundary": {
            "runtime_artifact_digest_recorded": False,
            "generation_digest_recorded": False,
            "event_payload_recorded": False,
            "run_identity_recorded": False,
            "path_recorded": False,
        },
        "write_performed": False,
    }
