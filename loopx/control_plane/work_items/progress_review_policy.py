"""Control-plane-owned vocabulary for the scoped progress-review sentinel policy.

The policy decides only whether typed external review receipts are recorded
(`shadow`) or may become the existing autonomous replan obligation (`assist`),
which receipt signal counts as drift, how many consecutive receipts are needed,
and which goal contract revision the receipts must be bound to. It grants no
file, provider, pause, gate or settlement authority. The capability package
re-exports this module; the control plane never imports the capability layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from ..content_digest import BARE_SHA256_PATTERN

PROGRESS_REVIEW_POLICY_SCHEMA_VERSION = "progress_review_policy_v0"
PROGRESS_REVIEW_MODES: tuple[str, ...] = ("off", "shadow", "assist")
PROGRESS_REVIEW_SIGNALS: tuple[str, ...] = ("noul", "choice")
PROGRESS_REVIEW_DEFAULT_MODE = "off"
PROGRESS_REVIEW_DEFAULT_SIGNAL = "noul"
PROGRESS_REVIEW_DEFAULT_DRIFT_THRESHOLD = 2
PROGRESS_REVIEW_MIN_DRIFT_THRESHOLD = 2
PROGRESS_REVIEW_MAX_DRIFT_THRESHOLD = 20


def normalize_progress_review_mode(value: Any) -> str:
    mode = str(value or "").strip()
    if mode not in PROGRESS_REVIEW_MODES:
        raise ValueError(
            "progress_review.mode must be one of: " + ", ".join(PROGRESS_REVIEW_MODES)
        )
    return mode


def normalize_progress_review_signal(value: Any) -> str:
    signal = str(value or "").strip()
    if signal not in PROGRESS_REVIEW_SIGNALS:
        raise ValueError(
            "progress_review.signal must be one of: "
            + ", ".join(PROGRESS_REVIEW_SIGNALS)
        )
    return signal


def normalize_progress_review_drift_threshold(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("progress_review.drift_threshold must be an integer")
    if not (
        PROGRESS_REVIEW_MIN_DRIFT_THRESHOLD
        <= value
        <= PROGRESS_REVIEW_MAX_DRIFT_THRESHOLD
    ):
        raise ValueError(
            "progress_review.drift_threshold must be between "
            f"{PROGRESS_REVIEW_MIN_DRIFT_THRESHOLD} and "
            f"{PROGRESS_REVIEW_MAX_DRIFT_THRESHOLD}"
        )
    return int(value)


def normalize_progress_review_contract_revision(value: Any) -> str | None:
    """The sha256 of the goal basis the receipts must be bound to, or None."""

    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        # An explicit empty value clears a pin at the change layer; the
        # effective policy reads it back as "no pin".
        return ""
    if not BARE_SHA256_PATTERN.fullmatch(text):
        raise ValueError(
            "progress_review.contract_revision must be a sha256 hex digest"
        )
    return text


def _default_policy() -> dict[str, Any]:
    return {
        "schema_version": PROGRESS_REVIEW_POLICY_SCHEMA_VERSION,
        "mode": PROGRESS_REVIEW_DEFAULT_MODE,
        "signal": PROGRESS_REVIEW_DEFAULT_SIGNAL,
        "drift_threshold": PROGRESS_REVIEW_DEFAULT_DRIFT_THRESHOLD,
        "contract_revision": None,
    }


def progress_review_goal_policy(goal: Mapping[str, Any]) -> dict[str, Any]:
    """Return the effective policy; any malformed stored block fails closed to off."""

    control_plane = goal.get("control_plane")
    raw = (
        control_plane.get("progress_review")
        if isinstance(control_plane, Mapping)
        else None
    )
    if not isinstance(raw, Mapping):
        return _default_policy()
    try:
        return {
            "schema_version": PROGRESS_REVIEW_POLICY_SCHEMA_VERSION,
            "mode": normalize_progress_review_mode(
                raw.get("mode", PROGRESS_REVIEW_DEFAULT_MODE)
            ),
            "signal": normalize_progress_review_signal(
                raw.get("signal", PROGRESS_REVIEW_DEFAULT_SIGNAL)
            ),
            "drift_threshold": normalize_progress_review_drift_threshold(
                raw.get("drift_threshold", PROGRESS_REVIEW_DEFAULT_DRIFT_THRESHOLD)
            ),
            "contract_revision": normalize_progress_review_contract_revision(
                raw.get("contract_revision")
            )
            or None,
        }
    except (TypeError, ValueError):
        return {**_default_policy(), "invalid_configuration": True}


def progress_review_goal_policy_summary(goal: Mapping[str, Any]) -> dict[str, Any]:
    policy = progress_review_goal_policy(goal)
    summary: dict[str, Any] = {
        "mode": policy["mode"],
        "signal": policy["signal"],
        "drift_threshold": policy["drift_threshold"],
        "contract_revision": policy["contract_revision"],
    }
    if policy.get("invalid_configuration"):
        summary["invalid_configuration"] = True
    return summary


__all__ = [
    "PROGRESS_REVIEW_DEFAULT_DRIFT_THRESHOLD",
    "PROGRESS_REVIEW_DEFAULT_MODE",
    "PROGRESS_REVIEW_DEFAULT_SIGNAL",
    "PROGRESS_REVIEW_MAX_DRIFT_THRESHOLD",
    "PROGRESS_REVIEW_MIN_DRIFT_THRESHOLD",
    "PROGRESS_REVIEW_MODES",
    "PROGRESS_REVIEW_POLICY_SCHEMA_VERSION",
    "PROGRESS_REVIEW_SIGNALS",
    "normalize_progress_review_contract_revision",
    "normalize_progress_review_drift_threshold",
    "normalize_progress_review_mode",
    "normalize_progress_review_signal",
    "progress_review_goal_policy",
    "progress_review_goal_policy_summary",
]
