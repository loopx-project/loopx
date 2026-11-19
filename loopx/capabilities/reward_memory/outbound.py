"""Scoped advisory recall for a caller-owned outbound intent.

The caller owns authorization, destination validation and delivery. Recalled
text is returned to the agent, never interpreted as a send/deny policy.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...control_plane.todos.contract import normalize_todo_claimed_by
from .experiment import (
    resolve_reward_memory_experiment,
    resolve_reward_memory_surface_config,
)
from .runtime_hooks import run_reward_memory_automatic_recall_hook

SURFACE = "outbound_message.before_send"


def outbound_guidance_hook(
    *,
    registry_path: Path,
    goal_id: str | None,
    agent_id: str | None,
    purpose: str = "unspecified",
    reviewed_digest: str | None = None,
):
    """Build an opt-in hook; absence preserves the existing sender exactly."""
    if purpose not in {"unspecified", "help", "progress", "urgent"}:
        raise ValueError("invalid outbound message purpose")
    if not goal_id or not agent_id:
        return None
    normalized_agent_id = normalize_todo_claimed_by(agent_id) or ""
    resolution, config = resolve_reward_memory_experiment(
        registry_path=registry_path, goal_id=goal_id, agent_id=agent_id
    )
        # Do not inspect invalid config fragments to guess whether reads are required.
    if resolution.get("status") in {"config_invalid", "config_missing"}:
        return _configuration_failure_hook(resolution["status"])
    if config is None or not config["automation"]["automatic_recall"]:
        return None
    if SURFACE not in config["surfaces"]:
        return None
    route = resolve_reward_memory_surface_config(config, SURFACE)
    identity = None
    checkpoints = {}
    for item in route["recall_corpora"]:
        corpus = item["corpus"]
        scope = corpus["scope"]
        current = {
            key: scope.get(key)
            for key in (
                "workspace_ref",
                "project_ref",
                "user_ref",
                "peer_ref",
                "session_ref",
            )
        }
        if current["peer_ref"] != f"agent:{normalized_agent_id}":
            return _configuration_failure_hook("scope_mismatch")
        if identity is not None and identity != current:
            return _configuration_failure_hook("scope_mismatch")
        identity = current
        checkpoints[corpus["corpus_id"]] = {
            **{k: v for k, v in current.items() if v is not None},
            "verified": True,
            "corpus_id": corpus["corpus_id"],
            "surface_id": SURFACE,
            "read_authority": corpus["read_authority"],
            "source_ref": f"registry:{goal_id}:reward-memory",
        }

    def recall(intent_digest: str, destination_id: str | None = None) -> dict[str, Any]:
        def apply(base, items):
            guidance = [
                {"candidate_ref": i.candidate_ref, "content_summary": i.content_summary}
                for i in items
                if i.target_class == "soft_preference"
            ]
            return {
                "outcome": "applied" if guidance else "ignored",
                "output": {"guidance": guidance},
                "memory_refs": [
                    i.memory_ref for i in items if i.target_class == "soft_preference"
                ],
                "reasoning_summary": "Guidance returned to the agent before delivery; no send authority granted.",
                "current_artifact_verified": True,
            }

        destination_digest = (
            hashlib.sha256(destination_id.encode()).hexdigest()
            if destination_id
            else None
        )
        destination = (
            config["surfaces"][SURFACE]
            .get("destinations", {})
            .get(destination_digest, {})
        )
        # A configured-but-mismatched digest silently degrades to no required
        # refs; surface the match so configuration drift stays observable in
        # the receipt instead of masquerading as a satisfied check.
        destination_configured = bool(destination)
        required_refs = destination.get("required_candidate_refs", [])
        queries = []
        # Required records have their own bounded lookup and are checked after
        # merge. Similarity ranking cannot silently remove a required record.
        queries.extend(
            {"query": ref, "query_summary": "required destination guidance"}
            for ref in required_refs
        )
        if destination_digest:
            queries.append(
                {
                    "query": f"Group {destination.get('query_label', '')} destination:{destination_digest} communication experiences, owner instructions and restrictions",
                    "query_summary": "destination communication guidance",
                }
            )
        queries.append(
            {
                "query": f"Reviewed guidance before an outbound {purpose} message: alternatives, evidence, recipient and escalation",
                "query_summary": "outbound communication guidance",
            }
        )
        results = []
        for query in queries:
            results.append(
                run_reward_memory_automatic_recall_hook(
                    config,
                    surface_id=SURFACE,
                    base_output={"guidance": []},
                    **identity,
                    revision_ref=intent_digest,
                    artifact_ref=intent_digest,
                    queries=[query],
                    observed_at=datetime.now(timezone.utc).isoformat(),
                    freshness_context={
                        "source_truth_current": True,
                        "source_revision": intent_digest,
                        "age_seconds": 0,
                    },
                    conflict_state="clear",
                    read_authority_checkpoints=checkpoints,
                    application_id="outbound-guidance",
                    apply_memory=apply,
                )
            )
        # Merge records, not overview/file URIs. Required lookups come first.
        by_candidate = {}
        for result in results:
            for item in result.get("output", {}).get("guidance", []):
                by_candidate.setdefault(item["candidate_ref"], item)
        guidance = [by_candidate[ref] for ref in required_refs if ref in by_candidate]
        guidance.extend(
            item for ref, item in by_candidate.items() if ref not in required_refs
        )
        missing_required = sorted(set(required_refs) - set(by_candidate))
        result = results[-1]
        telemetry = dict(result.get("telemetry") or {})
        for key in ("provider_call_count", "provider_call_cap", "query_count"):
            telemetry[key] = sum(
                int(r.get("telemetry", {}).get(key, 0)) for r in results
            )
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "intent": intent_digest,
                        "identity": identity,
                        "purpose": purpose,
                        "destination_digest": destination_digest,
                        "required_candidate_refs": required_refs,
                        "guidance": guidance,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        # This acknowledgement is by the executing agent, never a user gate.
        # Advisory-only urgent/failure behavior is unchanged. Explicit required
        # reads cannot be waived by urgency or an old acknowledgement.
        review_required = (
            bool(guidance)
            and (purpose != "urgent" or bool(required_refs))
            and reviewed_digest != digest
        ) or bool(missing_required)
        return {
            "schema_version": "outbound_guidance_review_v0",
            "status": "required_guidance_missing"
            if missing_required
            else ("applied" if guidance else result["status"]),
            "guidance": guidance,
            "review_digest": digest,
            "agent_review_required": review_required,
            "continue_delivery": not review_required,
            "urgent_notice": purpose == "urgent",
            "provider_failure_is_user_gate": False,
            "grants_new_action_authority": False,
            "required_guidance_complete": not missing_required,
            "missing_required_candidate_refs": missing_required,
            "destination_configured": destination_configured,
            "application": result.get("application"),
            "applications": [r.get("application") for r in results],
            "telemetry": telemetry,
        }

    # Preserve the one-argument callback for existing callers. The sender binds
    # the actual verified chat, never an agent-supplied display name.
    setattr(
        recall,
        "for_destination",
        lambda chat_id: lambda digest: recall(digest, chat_id),
    )
    return recall


def _configuration_failure_hook(reason: str):
    def blocked(intent_digest: str) -> dict[str, Any]:
        return {
            "schema_version": "outbound_guidance_review_v0",
            "status": "configuration_error",
            "reason_code": reason,
            "guidance": [],
            "agent_review_required": True,
            "continue_delivery": False,
            "provider_failure_is_user_gate": False,
            "grants_new_action_authority": False,
            "required_guidance_complete": False,
            "recommended_action": (
                "Repair the enabled Reward Memory configuration and verify it with "
                "reward-memory experiment-status before retrying. Review acknowledgement "
                "cannot waive this error; explicit disable remains an owner decision."
            ),
        }

    return blocked
