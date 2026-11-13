"""Pure OpenViking URI classification shared by bindings and the provider."""

from __future__ import annotations

import re
from dataclasses import dataclass

# OpenViking v0.4.19 rejects identity segments containing path separators,
# dot segments, colons, or plus signs. Keep the client-side boundary at least
# as strict as the current server contract.
OPENVIKING_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


@dataclass(frozen=True)
class OpenVikingScope:
    """Public-safe classification of one exact OpenViking URI."""

    visibility: str
    scope_kind: str
    write_strategy: str
    user_scope_id: str | None = None
    actor_scope_id: str | None = None

    @property
    def actor_binding_required(self) -> bool:
        return self.actor_scope_id is not None


def classify_openviking_scope(scope_ref: str) -> OpenVikingScope:
    """Classify supported public/private storage without inferring identity.

    OpenViking resource ingestion is valid only below a ``resources`` root.
    Structured Reward Memory records under a memory root use the filesystem
    content-write API instead.  Agent/peer identity is returned to the caller
    for an explicit equality check against ``actor_peer_id``.
    """

    value = str(scope_ref or "").strip().rstrip("/")
    if not value.startswith("viking://"):
        raise ValueError("OpenViking scope_ref must use viking://")
    segments = value[len("viking://") :].split("/")
    if not segments or any(
        not segment or segment in {".", ".."} for segment in segments
    ):
        raise ValueError("OpenViking scope_ref must use safe non-empty path segments")
    if segments[0] == "resources":
        return OpenVikingScope(
            visibility="public",
            scope_kind="account_resources",
            write_strategy="resource_ingest",
        )
    if (
        segments[0] == "~"
        and len(segments) >= 2
        and segments[1]
        in {
            "resources",
            "memories",
        }
    ):
        return OpenVikingScope(
            visibility="private",
            scope_kind=f"current_user_{segments[1]}",
            write_strategy=(
                "resource_ingest" if segments[1] == "resources" else "content_write"
            ),
        )
    if segments[0] == "agent":
        raise ValueError(
            "viking://agent is a shared read-only compatibility scope in "
            "OpenViking v0.4.19; use an explicit user/peer target"
        )
    if segments[0] != "user" or len(segments) < 3:
        raise ValueError(
            "OpenViking scope_ref must select account resources or a supported "
            "user/peer private collection"
        )
    user_id = segments[1]
    if not OPENVIKING_IDENTITY_RE.fullmatch(user_id):
        raise ValueError("OpenViking user scope must use a safe user id")
    if segments[2] in {"resources", "memories"}:
        return OpenVikingScope(
            visibility="private",
            scope_kind=f"user_{segments[2]}",
            write_strategy=(
                "resource_ingest" if segments[2] == "resources" else "content_write"
            ),
            user_scope_id=user_id,
        )
    if (
        len(segments) >= 5
        and segments[2] == "peers"
        and segments[4] in {"resources", "memories"}
    ):
        actor_id = segments[3]
        if not OPENVIKING_IDENTITY_RE.fullmatch(actor_id):
            raise ValueError("OpenViking peer scope must use a safe actor id")
        return OpenVikingScope(
            visibility="private",
            scope_kind=f"peer_{segments[4]}",
            write_strategy=(
                "resource_ingest" if segments[4] == "resources" else "content_write"
            ),
            user_scope_id=user_id,
            actor_scope_id=actor_id,
        )
    raise ValueError(
        "OpenViking private scope_ref must select user resources/memories or "
        "one explicit peer collection"
    )


# The original module re-exports these objects. Keep Python's public object
# identity and pickle references compatible with previously installed clients.
OpenVikingScope.__module__ = "loopx.capabilities.context_providers.openviking"
classify_openviking_scope.__module__ = "loopx.capabilities.context_providers.openviking"
