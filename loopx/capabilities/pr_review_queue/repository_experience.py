"""Read-only Git-published review experience through existing Reward Memory.

Placement: pull-request-review owns these public source files and this file/lexical
adapter. reward_memory owns qualification, scope, recall and the TS delivery
decision. This adapter is not a configurable provider, store or decision owner.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import re
from typing import Any

from ...lexical_retrieval import score_bm25
from ..context_providers.base import ContextProviderItem, ContextProviderRetrieval
from ..reward_memory.application import build_active_reward_memory_record
from ..reward_memory.candidate_review import (
    build_reward_memory_candidate, review_reward_memory_candidate,
)
from ..reward_memory.decision import run_reward_memory_decision
from ..reward_memory.experience_quality import normalize_procedural_experience
from ..reward_memory.experiment import resolve_goal_reward_memory_experiment
from ..reward_memory.registry import normalize_reward_memory_corpus


REPOSITORY_REVIEW_SURFACE_ID = "pull_request_review.review"
REPOSITORY_REVIEW_CORPUS_ID = "repository_review_experiences"
REPOSITORY_REVIEW_PROVIDER_ID = "repository_review_experience"


class _RepositoryExperienceReader:
    """Transient exact-corpus reader; only installed public assets are discovered."""

    def __init__(self, repository: str, corpus: dict[str, Any]) -> None:
        self.root = files(__package__).joinpath("experiences", *repository.split("/"))
        self.corpus = corpus

    def retrieve(self, **request: Any) -> ContextProviderRetrieval:
        sources = sorted(self.root.iterdir(), key=lambda entry: entry.name) if self.root.is_dir() else []
        if len(sources) > 24:
            raise ValueError("repository experience corpus exceeds its read bound")
        records = []
        for source in sources:
            if not source.name.endswith(".json") or not source.is_file():
                continue
            content = source.read_bytes()
            if len(content) > 16384:
                raise ValueError("repository experience exceeds its read bound")
            experience = normalize_procedural_experience(json.loads(content))
            digest = "sha256:" + hashlib.sha256(content).hexdigest()
            records.append((source, content, experience, digest))
        scores = score_bm25((json.dumps(row[2]) for row in records), request["query"])
        ranked = sorted(zip(records, scores.documents, strict=True), key=lambda row: -row[1].score)
        items = []
        for (source, content, experience, digest), hit in ranked[:request["max_results"]]:
            if hit.score <= 0:
                continue
            # Exact readback guards an asset changing during this invocation.
            if source.read_bytes() != content:
                raise ValueError("repository experience changed before readback")
            candidate = build_reward_memory_candidate({
                "target_class": "procedural_experience",
                "content_summary": experience["future_behavior"]["trigger"],
                "experience": experience,
                "source": {"source_kind": "reviewed_repository_experience",
                           "source_ref": digest, "actor_ref": self.corpus["owner_ref"],
                           "actor_role": "repository_source_owner"},
                "scope": self.corpus["scope"],
                "reasoning": {"summary": "Git-published advisory experience; utility remains unproven.",
                              "confidence": "medium"},
                "guard_context": {"source_freshness": "current", "conflict_state": "clear",
                                  "current_artifact_verified": True},
                "requested_action_scopes": [], "raw_content_captured": False,
            })
            # Canonical source publication owns curation. This projects that source
            # into the existing envelope, without activating or writing a provider.
            reviewed = review_reward_memory_candidate(candidate, {
                "decision": "accept", "reviewer_ref": self.corpus["owner_ref"],
                "review_ref": digest,
                "reasoning_summary": "Read-only projection of the repository-owned curated source.",
            })
            active = build_active_reward_memory_record(reviewed, self.corpus,
                                                       activated_at=request["observed_at"])
            items.append(ContextProviderItem(resource_ref=f"repository:{source.name}:{digest}",
                summary=active["content_summary"], content=json.dumps(active), score=hit.score))
        return ContextProviderRetrieval(provider=REPOSITORY_REVIEW_PROVIDER_ID, namespace=request["namespace"],
            status="completed", query_summary=request["query_summary"],
            observed_at=request["observed_at"], search_performed=True, read_performed=True,
            items=tuple(items), requested_limit=request["max_results"])

    def sync(self, **request: Any) -> Any:
        raise ValueError("repository experience is read-only; publication uses repository review")


def attach_repository_review_experience(
    packet: dict[str, Any], *, goal: Mapping[str, Any] | None, agent_id: str | None,
) -> None:
    """Deliver scoped advisory context only to an already enabled review Agent."""
    if goal is None or not agent_id:
        return
    status, config = resolve_goal_reward_memory_experiment(goal=goal, agent_id=agent_id)
    if config is None or status.get("automatic_recall") is not True:
        return
    # A configured provider or another module's recall route is insufficient.
    if REPOSITORY_REVIEW_SURFACE_ID not in config["surfaces"]:
        return
    repository = str(packet.get("request", {}).get("repository") or "").casefold()
    if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        return
    scope = {"workspace_ref": f"goal:{goal['id']}", "project_ref": f"repository:{repository}",
             "peer_ref": f"agent:{agent_id}", "surface_ids": [REPOSITORY_REVIEW_SURFACE_ID]}
    corpus = normalize_reward_memory_corpus({
        "corpus_id": REPOSITORY_REVIEW_CORPUS_ID, "class_id": "procedural_experience", "provider_id": REPOSITORY_REVIEW_PROVIDER_ID,
        "owner_ref": f"repository:{repository}", "source_of_truth": f"repository:{repository}:experiences",
        "read_authority": "actor_scoped", "write_authority": "read_only", "scope": scope,
        "freshness": {"mode": "source_truth_bound"},
        "lifecycle": {"state": "active", "supersedes": []},
        "retrieval": {"index_required": False, "readback_required": True,
                      "application_receipt_required": True},
        "maintenance": {"writeback_triggers": [], "closure_policy": "repository_review",
                        "retirement_authority": f"repository:{repository}"},
        "privacy": {"visibility": "public_safe", "raw_content_in_registry": False},
    })
    reader = _RepositoryExperienceReader(repository, corpus)
    if not reader.root.is_dir():
        return
    # Native read-only source: no provider configuration is replaced, extended or
    # written. The existing opt-in admits this caller-owned context boundary.
    recall_config = {
        "automation": {"automatic_recall": True, "automatic_ingest": False, "fail_open": True},
        "corpora": {REPOSITORY_REVIEW_CORPUS_ID: {"corpus": corpus, "standing_policy": {}, "provider_binding": {
            "corpus_id": REPOSITORY_REVIEW_CORPUS_ID, "provider_id": REPOSITORY_REVIEW_PROVIDER_ID, "namespace": "reward_memory",
            "scope_ref": f"repository:{repository}:experiences", "timeout_seconds": 5}}},
        "surfaces": {REPOSITORY_REVIEW_SURFACE_ID: {"corpus_ids": [REPOSITORY_REVIEW_CORPUS_ID], "ingest_corpus_id": REPOSITORY_REVIEW_CORPUS_ID,
            "adapter": "scoped_feedback", "recall_profile": {"profile_id": "repository_review",
                "mode": "function_boundary", "max_queries": 1,
                "limit": min(3, config["surfaces"][REPOSITORY_REVIEW_SURFACE_ID]["recall_profile"]["limit"])}}},
    }

    def deliver(base: Any, items: tuple[Any, ...]) -> Mapping[str, Any]:
        return {"outcome": "applied", "output": [
            {"candidate_ref": item.candidate_ref, "experience_digest": item.experience_digest,
             "experience": dict(item.experience)} for item in items],
            "memory_refs": [item.memory_ref for item in items], "current_artifact_verified": True,
            "reasoning_summary": "Delivered advisory experience into this exact PR review packet; no verdict or utility inferred."}

    for item in packet.get("pull_requests", []):
        # Do not change selection, exact-head idempotency or merge-readiness work.
        if not item.get("review_action_kind") or item["review_action_kind"] == "qualify_pull_request_merge_readiness":
            continue
        head = str(item.get("head_oid") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head):
            continue
        artifact = f"github:{repository}:pr:{item['number']}:{head}"
        query = " ".join([str(item.get("title") or ""), *[
            str(entry.get("path") or "") for entry in item.get("key_files", [])]])[:500]
        result = run_reward_memory_decision(recall_config, query_ready=bool(query),
            application_kind="context_delivery", apply_memory=deliver,
            surface_id=REPOSITORY_REVIEW_SURFACE_ID, base_output=[], workspace_ref=scope["workspace_ref"],
            project_ref=scope["project_ref"], peer_ref=scope["peer_ref"], revision_ref=f"git:{head}",
            queries=[{"query": query, "query_summary": "Current PR title and changed paths; no historical verdict."}],
            observed_at=datetime.now(timezone.utc).isoformat(),
            freshness_context={"source_truth_current": True, "source_revision": f"git:{head}", "age_seconds": 0},
            conflict_state="clear", application_id=f"review:{item['number']}:{head}", artifact_ref=artifact,
            read_authority_checkpoints={REPOSITORY_REVIEW_CORPUS_ID: {"verified": True, "corpus_id": REPOSITORY_REVIEW_CORPUS_ID,
                "workspace_ref": scope["workspace_ref"], "project_ref": scope["project_ref"],
                "peer_ref": scope["peer_ref"], "surface_id": REPOSITORY_REVIEW_SURFACE_ID, "read_authority": "actor_scoped",
                "source_ref": f"registry:{goal['id']}:reward-memory"}}, provider=reader)
        if result is not None:
            item["repository_experience"] = {"decision": result.public_packet, "guidance": result.output}
    if any("repository_experience" in item for item in packet.get("pull_requests", [])):
        packet["agent_response_contract"]["required_packet_fields_to_preserve"].append(
            "pull_requests[review_action_kind!=null].repository_experience")
