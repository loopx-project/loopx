from __future__ import annotations

import hashlib
import json
import re
import shlex
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ...agent_registry import normalize_registered_agents
from ...control_plane.reward_memory import (
    reward_memory_goal_policy,
    reward_memory_host_coverage,
)
from ...control_plane.operator_inbox_binding import local_private_config_digest
from ...control_plane.todos.contract import normalize_todo_claimed_by
from ..context_providers import build_context_provider
from ..context_providers.base import ContextProvider
from ..context_providers.openviking import classify_openviking_scope
from .application import TOKEN_RE, normalize_reward_memory_provider_binding
from .ingestion import normalize_reward_memory_standing_policy
from .registry import MAX_CORPORA, normalize_reward_memory_corpus
from .scoped_feedback import SCOPED_FEEDBACK_ADAPTER


REWARD_MEMORY_EXPERIMENT_SCHEMA_VERSION = "reward_memory_experiment_config_v1"
ISSUE_FIX_MAINTAINER_FEEDBACK_ADAPTER = "issue_fix_maintainer_feedback"
SUPPORTED_REWARD_MEMORY_EXPERIMENT_ADAPTERS = {
    ISSUE_FIX_MAINTAINER_FEEDBACK_ADAPTER,
    SCOPED_FEEDBACK_ADAPTER,
}

_V1_CONFIG_FIELDS = {
    "schema_version",
    "project_provider_binding",
    "corpora",
    "surfaces",
    "automation",
}
_PROJECT_BINDING_FIELDS = {
    "provider_id",
    "namespace",
    "timeout_seconds",
    "setup_hints",
    "provider_binary",
    "minimum_provider_version",
    "actor_peer_id",
    "corpus_scopes",
}
_CORPUS_SCOPE_FIELDS = {"corpus_id", "scope_ref"}
_CORPUS_ENTRY_FIELDS = {"corpus", "standing_policy"}
_SURFACE_FIELDS = {
    "surface_id",
    "adapter",
    "corpus_ids",
    "ingest_corpus_id",
    "recall_profile",
    "destinations",
}
_RECALL_PROFILE_FIELDS = {"profile_id", "mode", "max_queries", "limit"}
_AUTOMATION_FIELDS = {"automatic_recall", "automatic_ingest", "fail_open"}
_RECALL_MODES = {"function_boundary", "bounded_agentic_search"}


def canonical_reward_memory_actor_peer_id(*, goal_id: str, agent_id: str) -> str:
    """Return one OpenViking-safe actor for a Goal-scoped LoopX Agent.

    LoopX Agent names are only unique inside one Goal. The readable prefixes
    aid operations while the digest makes normalization collisions and reuse of
    the same local Agent name across Goals harmless.
    """

    normalized_agent = normalize_todo_claimed_by(agent_id)
    goal_value = str(goal_id or "").strip()
    if not normalized_agent or not goal_value:
        raise ValueError("goal_id and a registered agent_id are required")

    def segment(value: str, *, fallback: str) -> str:
        result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return (result or fallback)[:40].rstrip("-")

    digest = hashlib.sha256(
        f"{goal_value}\0{normalized_agent}".encode("utf-8")
    ).hexdigest()[:20]
    return (
        f"loopx-{segment(goal_value, fallback='goal')}-"
        f"{segment(normalized_agent, fallback='agent')}-{digest}"
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def _strict_object(
    value: object, *, label: str, allowed: set[str]
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(
            f"{label} contains unsupported fields: {', '.join(unexpected)}"
        )
    return value


def _bounded_objects(
    value: object, *, label: str, maximum: int
) -> list[Mapping[str, Any]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not 1 <= len(value) <= maximum
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise ValueError(f"{label} must be a bounded non-empty object list")
    return list(value)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _positive_int(value: object, label: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"{label} must be an integer between 1 and {maximum}")
    return value


def _adapter(value: object) -> str:
    result = str(value or "").strip()
    if result not in SUPPORTED_REWARD_MEMORY_EXPERIMENT_ADAPTERS:
        raise ValueError(
            "reward-memory experiment adapter must be one of: "
            + ", ".join(sorted(SUPPORTED_REWARD_MEMORY_EXPERIMENT_ADAPTERS))
        )
    return result


def _policy_matches_corpus(
    policy: Mapping[str, Any], corpus: Mapping[str, Any]
) -> bool:
    policy_scope = policy["scope"]
    corpus_scope = corpus["scope"]
    return (
        policy["owner_ref"] == corpus["owner_ref"]
        and policy_scope["workspace_ref"] == corpus_scope["workspace_ref"]
        and policy_scope["project_ref"] == corpus_scope["project_ref"]
        and set(policy_scope["surface_ids"]).issubset(corpus_scope["surface_ids"])
        and policy["allowed_target_classes"] == [corpus["class_id"]]
    )


def _compatibility_signature(
    corpus: Mapping[str, Any], policy: Mapping[str, Any]
) -> tuple[str, ...]:
    """Return the exact compatibility boundary for one surface corpus set."""

    return (
        str(corpus["class_id"]),
        str(corpus["owner_ref"]),
        str(corpus["read_authority"]),
        str(corpus["write_authority"]),
        str(policy["authority_source_ref"]),
        json.dumps(corpus["privacy"], sort_keys=True, separators=(",", ":")),
        json.dumps(corpus["freshness"], sort_keys=True, separators=(",", ":")),
        json.dumps(corpus["lifecycle"], sort_keys=True, separators=(",", ":")),
    )


def _materialize_provider_binding(
    project_binding: Mapping[str, Any],
    *,
    corpus: Mapping[str, Any],
    scope_ref: str,
) -> dict[str, Any]:
    raw = {
        key: project_binding[key]
        for key in (
            "provider_id",
            "namespace",
            "timeout_seconds",
            "setup_hints",
            "provider_binary",
            "minimum_provider_version",
            "actor_peer_id",
        )
        if key in project_binding
    }
    raw.update({"corpus_id": corpus["corpus_id"], "scope_ref": scope_ref})
    return normalize_reward_memory_provider_binding(raw, corpus)


def validate_reward_memory_goal_agent_scope(
    config: Mapping[str, Any], *, goal_id: str, agent_id: str
) -> dict[str, Any]:
    """Bind private provider storage to the owning Agent and attributed Goal.

    LoopX Agent names are Goal-local. The OpenViking actor peer is therefore a
    deterministic identity derived from ``(goal_id, agent_id)``, while Goal
    remains an explicit path/receipt attribution check. Neither value is
    inferred from the provider URI.
    """

    normalized_agent = normalize_todo_claimed_by(agent_id)
    if not normalized_agent:
        raise ValueError("agent_id must be a public-safe registered agent id")
    goal_segment = str(goal_id or "").strip()
    if not goal_segment or "/" in goal_segment:
        raise ValueError("goal_id must be a safe provider path segment")
    expected_actor = canonical_reward_memory_actor_peer_id(
        goal_id=goal_segment,
        agent_id=normalized_agent,
    )
    corpora = config.get("corpora")
    if not isinstance(corpora, Mapping):
        raise ValueError("reward-memory config is not normalized")
    private_count = 0
    for entry in corpora.values():
        if not isinstance(entry, Mapping):
            raise ValueError("reward-memory corpus entry is invalid")
        corpus = entry.get("corpus")
        binding = entry.get("provider_binding")
        if not isinstance(corpus, Mapping) or not isinstance(binding, Mapping):
            raise ValueError("reward-memory corpus route is incomplete")
        privacy = corpus.get("privacy")
        if not isinstance(privacy, Mapping) or privacy.get("visibility") != "private":
            continue
        private_count += 1
        if binding.get("provider_id") != "openviking":
            raise ValueError(
                "private Goal/Agent Reward Memory currently requires OpenViking"
            )
        peer_ref = str((corpus.get("scope") or {}).get("peer_ref") or "")
        if peer_ref != f"agent:{normalized_agent}":
            raise ValueError(
                "private Reward Memory corpus peer_ref must match the enabled Agent"
            )
        if (corpus.get("scope") or {}).get("session_ref"):
            raise ValueError(
                "private Goal/Agent Reward Memory cannot be session-scoped"
            )
        if binding.get("actor_peer_id") != expected_actor:
            raise ValueError(
                "private Reward Memory actor_peer_id must be the canonical "
                "Goal-scoped Agent actor"
            )
        provider_scope = classify_openviking_scope(str(binding.get("scope_ref") or ""))
        if (
            provider_scope.visibility != "private"
            or provider_scope.actor_scope_id != expected_actor
        ):
            raise ValueError(
                "private Reward Memory requires the canonical actor-bound peer scope"
            )
        segments = str(binding["scope_ref"]).rstrip("/").split("/")
        if not any(
            left == "goals" and right == goal_segment
            for left, right in zip(segments, segments[1:])
        ):
            raise ValueError(
                "private Reward Memory provider scope must contain the exact Goal segment"
            )
    return {
        "schema_version": "reward_memory_goal_agent_scope_v1",
        "goal_id": goal_segment,
        "agent_id": normalized_agent,
        "isolation_mode": (
            "goal_scoped_agent_private" if private_count else "explicit_shared"
        ),
        "private_corpus_count": private_count,
        "actor_binding_verified": private_count > 0,
        "actor_peer_id": expected_actor if private_count else None,
    }


def preflight_reward_memory_experiment_config(
    config: Mapping[str, Any],
    *,
    goal_id: str,
    agent_ids: Sequence[str],
    observed_at: str,
    execute: bool,
    provider: ContextProvider | None = None,
) -> dict[str, Any]:
    """Probe every configured corpus and return a restart-safe public receipt.

    ``execute=False`` performs provider/service and target-access checks only.
    ``execute=True`` writes a fresh non-memory probe through the exact route,
    then requires provider exact readback before enablement may commit.
    """

    corpora = config.get("corpora")
    if not isinstance(corpora, Mapping):
        raise ValueError("reward-memory config is not normalized")
    private_config = any(
        isinstance(entry, Mapping)
        and isinstance(entry.get("corpus"), Mapping)
        and isinstance(entry["corpus"].get("privacy"), Mapping)
        and entry["corpus"]["privacy"].get("visibility") == "private"
        for entry in corpora.values()
    )
    if private_config and len(agent_ids) != 1:
        raise ValueError(
            "one private Reward Memory config must bind exactly one Goal-scoped Agent"
        )
    receipts: dict[str, dict[str, Any]] = {}
    probe_run_id = uuid.uuid4().hex[:16]
    for raw_agent_id in agent_ids:
        agent_id = normalize_todo_claimed_by(raw_agent_id)
        if not agent_id:
            raise ValueError("reward-memory preflight requires registered Agent ids")
        isolation = validate_reward_memory_goal_agent_scope(
            config, goal_id=goal_id, agent_id=agent_id
        )
        sync_packets: list[dict[str, Any]] = []
        with TemporaryDirectory(prefix="loopx-reward-memory-preflight-") as temp_root:
            for corpus_id, entry in sorted(corpora.items()):
                if not isinstance(entry, Mapping):
                    raise ValueError("reward-memory corpus entry is invalid")
                binding = entry.get("provider_binding")
                if not isinstance(binding, Mapping):
                    raise ValueError("reward-memory provider binding is unavailable")
                digest = hashlib.sha256(
                    f"{goal_id}\n{agent_id}\n{corpus_id}\n{probe_run_id}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
                filename = f"loopx-writability-probe-{digest}.json"
                source_path = Path(temp_root) / filename
                source_path.write_text(
                    json.dumps(
                        {
                            "schema_version": (
                                "reward_memory_provider_writability_probe_v0"
                            ),
                            "goal_id": goal_id,
                            "agent_id": agent_id,
                            "corpus_id": corpus_id,
                            "recallable": False,
                            "raw_content_captured": False,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
                configured_provider = provider or build_context_provider(
                    {
                        "provider": binding["provider_id"],
                        "provider_binary": binding.get("provider_binary"),
                        "minimum_provider_version": binding.get(
                            "minimum_provider_version"
                        ),
                        "actor_peer_id": binding.get("actor_peer_id"),
                    }
                )
                sync = configured_provider.sync(
                    namespace=str(binding["namespace"]),
                    resources=[
                        (
                            str(source_path),
                            f"{str(binding['scope_ref']).rstrip('/')}/{filename}",
                        )
                    ],
                    timeout_seconds=float(binding["timeout_seconds"]),
                    observed_at=observed_at,
                    execute=execute,
                )
                sync_packets.append(sync.public_packet())
        all_verified = bool(sync_packets) and all(
            packet.get("status") == "completed"
            and packet.get("writability_verified") is True
            and int(packet.get("write_count") or 0) == 1
            for packet in sync_packets
        )
        preview_ready = bool(sync_packets) and all(
            packet.get("status") == "preflight_ready" for packet in sync_packets
        )
        status = (
            "verified"
            if execute and all_verified
            else "ready_for_apply"
            if not execute and preview_ready
            else "blocked"
        )
        receipts[agent_id] = {
            "schema_version": "reward_memory_enablement_receipt_v0",
            "status": status,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "provider_id": str(
                (config.get("project_provider_binding") or {}).get("provider_id") or ""
            ),
            "isolation_mode": isolation["isolation_mode"],
            "actor_binding_verified": isolation["actor_binding_verified"],
            "writability_verified": all_verified,
            "exact_readback_verified": all_verified,
            "probe_count": len(sync_packets),
            "write_count": sum(
                int(packet.get("write_count") or 0) for packet in sync_packets
            ),
            "external_writes_performed": any(
                packet.get("external_writes_performed") is True
                for packet in sync_packets
            ),
            "observed_at": observed_at,
            "probe_records_recallable": False,
            "provider_preflight_performed": all(
                packet.get("provider_preflight_performed") is True
                for packet in sync_packets
            ),
            "reason_codes": sorted(
                {
                    str(packet["reason_code"])
                    for packet in sync_packets
                    if packet.get("reason_code")
                }
            ),
        }
    expected_status = "verified" if execute else "ready_for_apply"
    ok = bool(receipts) and all(
        receipt["status"] == expected_status for receipt in receipts.values()
    )
    return {
        "ok": ok,
        "schema_version": "reward_memory_enablement_preflight_v0",
        "status": expected_status if ok else "blocked",
        "goal_id": goal_id,
        "agent_count": len(receipts),
        "agent_receipts": receipts,
        "external_writes_performed": any(
            receipt["external_writes_performed"] for receipt in receipts.values()
        ),
        "raw_content_captured": False,
        "raw_provider_payload_captured": False,
    }


def _normalize_v1(raw: Mapping[str, Any]) -> dict[str, Any]:
    _strict_object(
        raw, label="reward-memory experiment config", allowed=_V1_CONFIG_FIELDS
    )
    project_binding = _strict_object(
        raw.get("project_provider_binding"),
        label="project_provider_binding",
        allowed=_PROJECT_BINDING_FIELDS,
    )
    scopes = _bounded_objects(
        project_binding.get("corpus_scopes"),
        label="project_provider_binding.corpus_scopes",
        maximum=MAX_CORPORA,
    )
    scope_refs: dict[str, str] = {}
    for index, item in enumerate(scopes):
        scope = _strict_object(
            item,
            label=f"project_provider_binding.corpus_scopes[{index}]",
            allowed=_CORPUS_SCOPE_FIELDS,
        )
        corpus_id = str(scope.get("corpus_id") or "").strip()
        scope_ref = str(scope.get("scope_ref") or "").strip()
        if not corpus_id or not scope_ref:
            raise ValueError("each project provider corpus scope must be exact")
        if corpus_id in scope_refs:
            raise ValueError(
                "project provider corpus scopes must not contain duplicates"
            )
        scope_refs[corpus_id] = scope_ref

    raw_corpora = _bounded_objects(
        raw.get("corpora"), label="corpora", maximum=MAX_CORPORA
    )
    corpora: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_corpora):
        entry = _strict_object(
            item, label=f"corpora[{index}]", allowed=_CORPUS_ENTRY_FIELDS
        )
        corpus_raw = entry.get("corpus")
        policy_raw = entry.get("standing_policy")
        if not isinstance(corpus_raw, Mapping) or not isinstance(policy_raw, Mapping):
            raise ValueError("each corpus entry requires corpus and standing_policy")
        corpus = normalize_reward_memory_corpus(corpus_raw)
        policy = normalize_reward_memory_standing_policy(policy_raw)
        corpus_id = corpus["corpus_id"]
        if corpus_id in corpora:
            raise ValueError("corpora must not contain duplicate corpus ids")
        if not _policy_matches_corpus(policy, corpus):
            raise ValueError(
                "standing policy authority and scope must stay inside its corpus"
            )
        if corpus["provider_id"] != project_binding.get("provider_id"):
            raise ValueError("every corpus must use the project provider binding")
        corpora[corpus_id] = {
            "corpus": corpus,
            "standing_policy": policy,
        }

    if set(scope_refs) != set(corpora):
        raise ValueError(
            "project provider corpus scopes must exactly match declared corpora"
        )
    workspace_projects = {
        (
            entry["corpus"]["scope"]["workspace_ref"],
            entry["corpus"]["scope"]["project_ref"],
        )
        for entry in corpora.values()
    }
    if len(workspace_projects) != 1:
        raise ValueError("all corpora must belong to one exact project scope")
    for corpus_id, entry in corpora.items():
        entry["provider_binding"] = _materialize_provider_binding(
            project_binding,
            corpus=entry["corpus"],
            scope_ref=scope_refs[corpus_id],
        )

    raw_surfaces = _bounded_objects(raw.get("surfaces"), label="surfaces", maximum=20)
    surfaces: dict[str, dict[str, Any]] = {}
    referenced_corpora: set[str] = set()
    for index, item in enumerate(raw_surfaces):
        surface = _strict_object(
            item, label=f"surfaces[{index}]", allowed=_SURFACE_FIELDS
        )
        surface_id = str(surface.get("surface_id") or "").strip()
        if not surface_id or surface_id in surfaces:
            raise ValueError("surface_id must be present and unique")
        corpus_ids_raw = surface.get("corpus_ids")
        if (
            not isinstance(corpus_ids_raw, Sequence)
            or isinstance(corpus_ids_raw, (str, bytes))
            or not 1 <= len(corpus_ids_raw) <= MAX_CORPORA
        ):
            raise ValueError("surface.corpus_ids must be a bounded non-empty list")
        corpus_ids = [str(value or "").strip() for value in corpus_ids_raw]
        if any(not value for value in corpus_ids) or len(corpus_ids) != len(
            set(corpus_ids)
        ):
            raise ValueError("surface.corpus_ids must contain unique corpus ids")
        unknown = sorted(set(corpus_ids) - set(corpora))
        if unknown:
            raise ValueError(
                "surface selects an undeclared corpus: " + ", ".join(unknown)
            )
        ingest_corpus_id = str(surface.get("ingest_corpus_id") or "").strip()
        if ingest_corpus_id not in corpus_ids:
            raise ValueError("surface.ingest_corpus_id must be one selected corpus")
        for corpus_id in corpus_ids:
            if surface_id not in corpora[corpus_id]["corpus"]["scope"]["surface_ids"]:
                raise ValueError("surface must be declared by every selected corpus")
            if (
                surface_id
                not in (corpora[corpus_id]["standing_policy"]["scope"]["surface_ids"])
            ):
                raise ValueError(
                    "surface must be authorized by every selected standing policy"
                )
        signatures = {
            _compatibility_signature(
                corpora[corpus_id]["corpus"],
                corpora[corpus_id]["standing_policy"],
            )
            for corpus_id in corpus_ids
        }
        if len(signatures) != 1:
            raise ValueError(
                "surface corpora must share memory class, authority, privacy, freshness, and lifecycle"
            )
        profile = _strict_object(
            surface.get("recall_profile"),
            label=f"surfaces[{index}].recall_profile",
            allowed=_RECALL_PROFILE_FIELDS,
        )
        mode = str(profile.get("mode") or "").strip()
        if mode not in _RECALL_MODES:
            raise ValueError("recall_profile.mode is unsupported")
        profile_id = str(profile.get("profile_id") or "").strip()
        if not profile_id:
            raise ValueError("recall_profile.profile_id must be present")
        surfaces[surface_id] = {
            "surface_id": surface_id,
            "adapter": _adapter(surface.get("adapter")),
            "corpus_ids": corpus_ids,
            "ingest_corpus_id": ingest_corpus_id,
            "recall_profile": {
                "profile_id": profile_id,
                "mode": mode,
                "max_queries": _positive_int(
                    profile.get("max_queries"),
                    "recall_profile.max_queries",
                    maximum=3,
                ),
                "limit": _positive_int(
                    profile.get("limit"), "recall_profile.limit", maximum=8
                ),
            },
        }
        if "destinations" in surface:
            if surface_id != "outbound_message.before_send":
                raise ValueError("destinations require the outbound surface")
            destinations = {}
            for destination in _bounded_objects(
                surface["destinations"], label="destinations", maximum=20
            ):
                entry = _strict_object(
                    destination,
                    label="destination",
                    allowed={
                        "destination_digest",
                        "query_label",
                        "required_candidate_refs",
                    },
                )
                digest = str(entry.get("destination_digest") or "")
                if len(digest) != 64 or any(
                    c not in "0123456789abcdef" for c in digest
                ):
                    raise ValueError("destination_digest must be a SHA-256 hex digest")
                if digest in destinations:
                    raise ValueError("destination_digest must be unique")
                label = entry.get("query_label", "")
                if not isinstance(label, str) or len(label) > 120:
                    raise ValueError(
                        "destination query_label must be at most 120 characters"
                    )
                refs = entry.get("required_candidate_refs", [])
                if (
                    not isinstance(refs, list)
                    or len(refs) > 3
                    or any(
                        not isinstance(ref, str)
                        or not ref.startswith("candidate:")
                        or len(ref) > 100
                        or any(c.isspace() for c in ref)
                        # A ref outside the application token charset can never
                        # match an emitted candidate_ref, so requiring it would
                        # permanently block every message for this destination.
                        or TOKEN_RE.fullmatch(ref) is None
                        for ref in refs
                    )
                    or len(set(refs)) != len(refs)
                ):
                    raise ValueError(
                        "required_candidate_refs must contain up to three unique "
                        "public-safe candidate refs matching the application token "
                        "charset"
                    )
                destinations[digest] = {
                    "query_label": label,
                    "required_candidate_refs": refs,
                }
            surfaces[surface_id]["destinations"] = destinations
        referenced_corpora.update(corpus_ids)
    if referenced_corpora != set(corpora):
        raise ValueError(
            "every declared corpus must be assigned to an explicit surface"
        )

    automation = _strict_object(
        raw.get("automation", {}), label="automation", allowed=_AUTOMATION_FIELDS
    )
    fail_open = (
        _boolean(automation.get("fail_open"), "automation.fail_open")
        if "fail_open" in automation
        else True
    )
    if not fail_open:
        raise ValueError("reward-memory automation must be fail-open")
    normalized = {
        "schema_version": REWARD_MEMORY_EXPERIMENT_SCHEMA_VERSION,
        "project_provider_binding": {
            key: value
            for key, value in project_binding.items()
            if key != "corpus_scopes"
        },
        "corpora": corpora,
        "surfaces": surfaces,
        "automation": {
            "automatic_recall": (
                _boolean(
                    automation.get("automatic_recall"),
                    "automation.automatic_recall",
                )
                if "automatic_recall" in automation
                else True
            ),
            "automatic_ingest": (
                _boolean(
                    automation.get("automatic_ingest"),
                    "automation.automatic_ingest",
                )
                if "automatic_ingest" in automation
                else True
            ),
            "fail_open": True,
        },
        "automation_intent": {
            field: "explicit" if field in automation else "default_enabled_new_config"
            for field in ("automatic_recall", "automatic_ingest")
        }
        | {"fail_open": "explicit" if "fail_open" in automation else "default"},
    }
    return normalized


def load_reward_memory_experiment_config(
    *, project: Path, config_path: str
) -> dict[str, Any]:
    path = project / config_path
    try:
        raw = _read_json_object(path)
    except (OSError, ValueError) as exc:
        raise ValueError("reward-memory experiment config is unavailable") from exc
    if raw.get("schema_version") != REWARD_MEMORY_EXPERIMENT_SCHEMA_VERSION:
        raise ValueError(
            "reward-memory experiment config must use reward_memory_experiment_config_v1"
        )
    return _normalize_v1(raw)


def resolve_reward_memory_surface_config(
    config: Mapping[str, Any],
    surface_id: str,
    *,
    adapter: str | None = None,
) -> dict[str, Any]:
    """Select only the corpora explicitly assigned to one module surface."""

    surfaces = config.get("surfaces")
    corpora = config.get("corpora")
    if not isinstance(surfaces, Mapping) or not isinstance(corpora, Mapping):
        raise ValueError("reward-memory config is not normalized")
    surface = surfaces.get(surface_id)
    if not isinstance(surface, Mapping):
        raise ValueError("reward-memory surface is not configured")
    configured_adapter = str(surface.get("adapter") or "")
    if adapter and adapter != configured_adapter:
        raise ValueError("input adapter does not match the configured route")
    routes: list[dict[str, Any]] = []
    for corpus_id in surface.get("corpus_ids") or []:
        entry = corpora.get(corpus_id)
        if not isinstance(entry, Mapping):
            raise ValueError("configured surface corpus is unavailable")
        routes.append(
            {
                "corpus": entry["corpus"],
                "standing_policy": entry["standing_policy"],
                "provider_binding": entry["provider_binding"],
            }
        )
    ingest_id = str(surface.get("ingest_corpus_id") or "")
    ingest = next(
        (route for route in routes if route["corpus"]["corpus_id"] == ingest_id),
        None,
    )
    if ingest is None:
        raise ValueError("configured ingest corpus is unavailable")
    return {
        "schema_version": "reward_memory_surface_config_v0",
        "surface_id": surface_id,
        "adapter": configured_adapter,
        "recall_profile": dict(surface["recall_profile"]),
        "recall_corpora": routes,
        "selection": {
            "mode": "explicit_surface_corpus_ids",
            "global_corpus_scan": False,
            "corpus_ids": list(surface["corpus_ids"]),
        },
        "corpus": ingest["corpus"],
        "standing_policy": ingest["standing_policy"],
        "provider_binding": ingest["provider_binding"],
    }


def _enablement_repair(
    *, goal_id: str, agent_ids: Sequence[str], agent_id: str,
    binding_agent_id: str | None = None,
) -> dict[str, Any]:
    """Reuse the configuration owner without publishing its private pointer.

    This is a recovery plan, never authorization to trust a changed binding.
    Retain every enabled Agent when requalifying a shared corpus.
    """
    argv = [
        "loopx", "--registry", "<invoked-registry>",
        "configure-goal", "--goal-id", goal_id,
    ]
    if binding_agent_id:
        argv.extend(["--reward-memory-agent-id", binding_agent_id])
    else:
        for enabled_agent in agent_ids:
            argv.extend(["--reward-memory-agent", enabled_agent])
    return {
        "schema_version": "reward_memory_enablement_repair_v0",
        "kind": "requalify_existing_binding",
        "owner": "configure-goal",
        "registry_context": "reuse_invoked_registry",
        "commands_are_templates": True,
        "required_bindings": {"<invoked-registry>": "invoked_registry_path"},
        "automatic_apply": False,
        "instruction": (
            "Bind <invoked-registry> to the exact registry used for this invocation "
            "before executing any command; never substitute the default registry. "
            "Inspect the local configuration change and its existing authorization; "
            "preview, then apply through configure-goal within that authorization. "
            "Apply performs provider write/readback and synchronizes the binding. "
            "Do not copy the new digest into an old receipt. Keep ordinary work running."
        ),
        "preview_command": shlex.join(argv),
        "apply_command": shlex.join([*argv, "--execute"]),
        "verify_command": shlex.join(
            [
                "loopx",
                "--registry",
                "<invoked-registry>",
                "reward-memory",
                "experiment-status",
                "--goal-id",
                goal_id,
                "--agent-id",
                agent_id,
            ]
        ),
        "success_status": "available",
    }


def resolve_reward_memory_experiment(
    *, registry_path: Path, goal_id: str, agent_id: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve an agent-scoped experiment without exposing local config details."""

    normalized_agent = normalize_todo_claimed_by(agent_id)
    if not normalized_agent:
        raise ValueError("agent_id must be a public-safe registered agent id")
    registry = _read_json_object(registry_path)
    goals = registry.get("goals") if isinstance(registry.get("goals"), list) else []
    goal = next(
        (
            item
            for item in goals
            if isinstance(item, Mapping) and str(item.get("id")) == goal_id
        ),
        None,
    )
    if goal is None:
        raise ValueError(f"goal_id not found in registry: {goal_id}")
    return resolve_goal_reward_memory_experiment(
        goal=goal,
        agent_id=normalized_agent,
        registry_role=str(registry.get("registry_role") or "project-local"),
    )


def resolve_goal_reward_memory_experiment(
    *, goal: Mapping[str, Any], agent_id: str, registry_role: str = "project-local"
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve an already loaded Goal with the same checks as runtime admission.

    Configuration summaries reuse this read-only owner; no provider is contacted.
    """
    goal_id = str(goal.get("id") or "")
    normalized_agent = normalize_todo_claimed_by(agent_id)
    if not normalized_agent:
        raise ValueError("agent_id must be a public-safe registered agent id")
    registered_agents = normalize_registered_agents(
        (goal.get("coordination") or {}).get("registered_agents")
        if isinstance(goal.get("coordination"), Mapping)
        else None
    )
    if normalized_agent not in registered_agents:
        raise ValueError(f"agent_id is not registered for goal {goal_id}")
    policy = reward_memory_goal_policy(goal, agent_id=normalized_agent)
    registry_role = str(registry_role or "project-local").strip()
    config_runtime_route = {
        "schema_version": "reward_memory_config_runtime_route_v0",
        "registry_source": "invoked_registry",
        "registry_role": registry_role,
        "runtime_scope": (
            "shared_runtime" if registry_role == "global-local" else "project_runtime"
        ),
        "goal_source": "registry.goals",
        "config_source": "goal_repo_relative_config_pointer",
        "readback_status": "not_attempted",
        "exact_readback_verified": False,
    }
    if policy.get("binding_agent_id"):
        config_runtime_route["binding_agent_id"] = policy["binding_agent_id"]
    base = {
        "ok": True,
        "schema_version": "reward_memory_experiment_status_v1",
        "goal_id": goal_id,
        "agent_id": normalized_agent,
        "experimental": policy["experimental"],
        "enabled": policy["enabled"],
        "configured_for_agent": normalized_agent in policy["enabled_agents"],
        "config_pointer_registered": bool(policy["config_path"]),
        "automatic_ingest": False,
        "automatic_recall": False,
        "fail_open": True,
        "config_runtime_route": config_runtime_route,
        "external_writes_performed": False,
        "host_coverage": reward_memory_host_coverage(),
        **({"binding_revision": policy["binding_revision"]} if policy.get("binding_revision") else {}),
    }
    if not policy["enabled"]:
        return base | {"status": "disabled", "available": False}, None
    if normalized_agent not in policy["enabled_agents"]:
        return base | {"status": "agent_not_enabled", "available": False}, None
    if not policy["config_path"]:
        return base | {"status": "config_missing", "available": False}, None
    project = Path(str(goal.get("repo") or "")).expanduser()
    try:
        config = load_reward_memory_experiment_config(
            project=project,
            config_path=policy["config_path"],
        )
    except ValueError:
        return base | {
            "status": "config_invalid",
            "available": False,
            "reason_code": "config_unavailable_or_invalid",
            "config_runtime_route": config_runtime_route
            | {"readback_status": "rejected"},
        }, None
    try:
        isolation = validate_reward_memory_goal_agent_scope(
            config,
            goal_id=goal_id,
            agent_id=normalized_agent,
        )
    except ValueError:
        return base | {
            "status": "config_invalid",
            "available": False,
            "reason_code": "goal_agent_provider_scope_mismatch",
            "config_runtime_route": config_runtime_route
            | {"readback_status": "rejected"},
        }, None
    actual_digest = local_private_config_digest(
        project=project,
        config_path=policy["config_path"],
    )
    receipt = policy["enablement_receipts"].get(normalized_agent) or {}
    digest_matches = bool(actual_digest) and all(
        value == actual_digest
        for value in (policy["config_digest"], receipt.get("config_digest"))
    )
    if not digest_matches:
        return base | {
            "status": "enablement_stale",
            "available": False,
            "reason_code": "config_digest_missing_or_drifted",
            "repair": _enablement_repair(
                goal_id=goal_id,
                agent_ids=policy["enabled_agents"],
                agent_id=normalized_agent,
                binding_agent_id=policy.get("binding_agent_id"),
            ),
            "isolation_mode": isolation["isolation_mode"],
            "actor_binding_verified": isolation["actor_binding_verified"],
            "writability_verified": False,
            "exact_readback_verified": False,
            "config_runtime_route": config_runtime_route
            | {"readback_status": "drifted"},
        }, None
    private_actor_required = isolation["private_corpus_count"] > 0
    receipt_verified = (
        receipt.get("status") == "verified"
        and receipt.get("goal_id") == goal_id
        and receipt.get("agent_id") == normalized_agent
        and receipt.get("provider_id")
        == config["project_provider_binding"]["provider_id"]
        and receipt.get("isolation_mode") == isolation["isolation_mode"]
        and receipt.get("writability_verified") is True
        and receipt.get("exact_readback_verified") is True
        and (
            not private_actor_required or receipt.get("actor_binding_verified") is True
        )
    )
    if not receipt_verified:
        return base | {
            "status": "enablement_unverified",
            "available": False,
            "reason_code": "provider_write_preflight_missing_or_unverified",
            "repair": _enablement_repair(
                goal_id=goal_id,
                agent_ids=policy["enabled_agents"],
                agent_id=normalized_agent,
                binding_agent_id=policy.get("binding_agent_id"),
            ),
            "isolation_mode": isolation["isolation_mode"],
            "actor_binding_verified": isolation["actor_binding_verified"],
            "writability_verified": False,
            "exact_readback_verified": False,
            "config_runtime_route": config_runtime_route
            | {"readback_status": "unverified"},
        }, None
    corpora = config["corpora"]
    surfaces = config["surfaces"]
    automation = config["automation"]
    result = base | {
        "status": "available",
        "available": True,
        "config_schema_version": config["schema_version"],
        "provider_id": config["project_provider_binding"]["provider_id"],
        "corpus_count": len(corpora),
        "corpus_ids": sorted(corpora),
        "surface_ids": sorted(surfaces),
        "surface_profiles": [
            {
                "surface_id": surface_id,
                "adapter": surface["adapter"],
                "corpus_count": len(surface["corpus_ids"]),
                "recall_profile_id": surface["recall_profile"]["profile_id"],
            }
            for surface_id, surface in sorted(surfaces.items())
        ],
        "automatic_ingest": automation["automatic_ingest"],
        "automatic_recall": automation["automatic_recall"],
        "automation_intent": dict(config["automation_intent"]),
        "fail_open": automation["fail_open"],
        "config_runtime_route": config_runtime_route
        | {
            "readback_status": "verified",
            "exact_readback_verified": True,
        },
        "raw_content_captured": False,
        "isolation_mode": isolation["isolation_mode"],
        "actor_binding_verified": (
            receipt.get("actor_binding_verified") is True
            if private_actor_required
            else isolation["actor_binding_verified"]
        ),
        "writability_verified": True,
        "exact_readback_verified": True,
        "enablement_receipt_status": "verified",
    }
    if len(surfaces) == 1:
        route = resolve_reward_memory_surface_config(config, next(iter(surfaces)))
        result.update(
            {
                "adapter": route["adapter"],
                "corpus_id": route["corpus"]["corpus_id"],
            }
        )
    return result, config


def resolve_reward_memory_experiment_from_status(
    status_payload: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any] | None:
    """Resolve the agent policy from the registry that produced a status packet."""

    registry_value = str(status_payload.get("registry") or "").strip()
    if not agent_id or not registry_value:
        return None
    try:
        status, _ = resolve_reward_memory_experiment(
            registry_path=Path(registry_value).expanduser(),
            goal_id=goal_id,
            agent_id=agent_id,
        )
    except (OSError, ValueError):
        return None
    return status
