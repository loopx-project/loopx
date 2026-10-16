"""Invocation-local IO for an operator-frozen operation request.

The locator is neither an actor nor approval. Only the already authenticated
native tool handler resolves it; the existing operation service owns admission
and every proposal/confirmation/consumption transition.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from loopx.control_plane.content_digest import BARE_SHA256_PATTERN


def request_reference_instruction(configured: bool) -> str:
    if not configured:
        return ""
    return (
        '\nThe operator has frozen one request. Read context.prepared_request.ref, '
        'then prepare with {"action":"prepare","request":{"ref":"<that exact ref>"}}. '
        'Do not copy or rewrite its terms. The locator grants no approval or effect; '
        'inspect the canonical proposal to read its terms. Unknown or expired requests stay refused.'
    )


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate frozen request key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("non-finite frozen request value")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite frozen request value")
    return number


@dataclass(frozen=True)
class OperationRequestSource:
    path: Path
    sha256: str

    def read(self) -> dict[str, Any]:
        try:
            with self.path.open("rb") as stream:
                # Match the owned operation transport's bounded frame size.
                raw = stream.read(262145)
        except OSError as exc:
            raise ValueError("frozen operation request is unavailable") from exc
        if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("frozen operation request content changed")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_constant=_constant, parse_float=_finite_float)
        if not isinstance(value, dict) or value.get("action_kind") != "operation.execute":
            raise ValueError("frozen request must describe one typed operation")
        return value

    def profile(self, source_route: Mapping[str, str] | None) -> dict[str, Any]:
        return {"sha256": self.sha256, "source_route": source_route}

    def bind(
        self, request: Mapping[str, Any], *, lineage: Mapping[str, str],
        session_id: str, profile_digest: str,
    ) -> BoundOperationRequest:
        # This is a local locator, not a second state or permission decision.
        scope = [request["turn_key"], request.get("goal_ref"), dict(lineage),
                 session_id, profile_digest, self.sha256]
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return BoundOperationRequest(self, "request-" + digest)


@dataclass(frozen=True)
class BoundOperationRequest:
    source: OperationRequestSource
    ref: str

    def resolve(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value != {"ref": self.ref}:
            raise ValueError("frozen operation request reference mismatch")
        # Recheck bytes at consumption of the locator, after native call pairing.
        # Re-parsing also prevents handler injection from mutating the source.
        return self.source.read()


def operation_request_source(
    path: Path | None, sha256: str | None,
) -> OperationRequestSource | None:
    if path is None and sha256 is None:
        return None
    if (path is None or not path.is_absolute() or not isinstance(sha256, str)
            or BARE_SHA256_PATTERN.fullmatch(sha256) is None):
        raise ValueError("frozen operation request requires an absolute file and SHA-256")
    source = OperationRequestSource(path, sha256)
    source.read()  # Fail before SDK startup; no file path/content enters errors.
    return source
