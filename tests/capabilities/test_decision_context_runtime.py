from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.decision_context import cli as decision_context_cli
from loopx.capabilities.decision_context import runtime as decision_context_runtime
from loopx.capabilities.context_providers import (
    ContextProviderItem,
    ContextProviderRetrieval,
)
from loopx.capabilities.decision_context import (
    DecisionEvidenceRecords,
    LocalFileDecisionSourceProvider,
    assemble_profile_decision_evidence,
    build_decision_outcome_receipt,
    build_decision_proposal,
    commit_profile_decision_cursors,
    load_private_pending_decision_settlement,
    recall_profile_decision_context,
    settle_profile_decision_review,
    write_private_pending_decision_settlement,
)
from loopx.cli import main
from loopx.rollout_event_log import append_rollout_event, build_rollout_event

OBSERVED_AT = "2100-01-01T00:00:00+00:00"
BEFORE = "2100-01-01T00:01:00+00:00"
PRIVATE_CONTENT = "Current authority keeps adoption at a bounded pilot."
PRIVATE_RECALL_CONTENT = "Peer task found the extension boundary."


def write_profile(
    path: Path,
    authority_path: Path,
    *,
    enabled: bool = True,
    max_bytes: int = 4096,
    scan_mode: str = "incremental",
    adapter: str = "local-file",
) -> Path:
    payload = {
        "schema_version": "decision_context_profile_v0",
        "goal_id": "example-decision-goal",
        "enabled": enabled,
        "enabled_agents": ["example-agent"],
        "source_provider_bindings": [
            {
                "provider_id": "local-authority",
                "adapter": adapter,
                "config": {"max_bytes": max_bytes},
            }
        ],
        "sources": [
            {
                "source_id": "source:authority",
                "provider_id": "local-authority",
                "source_kind": "artifact",
                "priority": "p0",
                "evidence_level": "s3",
                "objectives": ["adoption"],
                "scan_mode": scan_mode,
                "exact_read_policy": "material_change",
                "freshness_seconds": 3600,
                "private_locator": str(authority_path),
                "max_changes": 4,
                "enabled": True,
            }
        ],
        "context_provider": None,
        "automation": {
            "automatic_capture": False,
            "fail_open": True,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_extension_context_profile(
    path: Path,
    authority_path: Path,
    *,
    extension_id: str = "loopx-obelisk",
) -> Path:
    payload = json.loads(
        write_profile(path, authority_path).read_text(encoding="utf-8")
    )
    payload["context_provider"] = {
        "provider": "extension",
        "namespace": "peer-session",
        "scope_ref": "host-session:codex:thread-a",
        "max_results": 4,
        "timeout_seconds": 10,
        "config": {"extension_id": extension_id},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def semantic_rebase(collection: Any) -> DecisionEvidenceRecords:
    current = collection.authority[0]
    return DecisionEvidenceRecords(
        changed_facts=(
            {
                "fact_id": "fact:pilot",
                "summary": "Current authority keeps adoption at pilot.",
                "source_ref": current.source_ref,
                "source_revision": current.source_revision,
                "observed_at": current.observed_at,
                "freshness": "current",
                "authority": "first_party",
            },
        )
    )


def decision_chain(assembly: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    packet = assembly.public_packet()
    proposal = build_decision_proposal(
        goal_id="example-decision-goal",
        decision_id="decision:adoption",
        evidence_packet_ref=packet["evidence_packet"]["packet_ref"],
        observed_at=OBSERVED_AT,
        objective_scores=[
            {
                "objective_id": "adoption",
                "score": 0.8,
                "rationale": "The current authority supports a bounded pilot.",
            }
        ],
        recommended_decision={
            "option_id": "bounded-pilot",
            "summary": "Continue the bounded pilot.",
            "rationale": "Current first-party evidence remains positive.",
            "confidence": 0.8,
        },
        review_at="2100-01-02T00:00:00+00:00",
    )
    outcome = build_decision_outcome_receipt(
        goal_id="example-decision-goal",
        decision_id="decision:adoption",
        proposal_packet_ref=proposal["packet_ref"],
        recorded_at=OBSERVED_AT,
        verification_status="pending",
        accepted_decision={
            "option_id": "bounded-pilot",
            "summary": "Continue the bounded pilot.",
            "accepted_by": "example-owner",
            "accepted_at": OBSERVED_AT,
        },
        review_at="2100-01-02T00:00:00+00:00",
    )
    return proposal, outcome


def append_decision_writeback(
    path: Path,
    assembly: Any,
    proposal: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    packet = assembly.public_packet()
    event = build_rollout_event(
        goal_id="example-decision-goal",
        event_kind="validation",
        decision_id="decision:adoption",
        status="committed",
        summary="Decision packets were written through the existing lifecycle.",
        artifact_refs=[
            packet["evidence_packet"]["packet_ref"],
            proposal["packet_ref"],
            outcome["packet_ref"],
        ],
        recorded_at=OBSERVED_AT,
    )
    return append_rollout_event(path, event)


def append_review_gate_event(
    path: Path,
    proposal: dict[str, Any],
    disposition: str,
) -> dict[str, Any]:
    deferred = disposition == "defer"
    event = build_rollout_event(
        goal_id="example-decision-goal",
        event_kind="todo_update" if deferred else "todo_complete",
        todo_id="todo:decision-review",
        status="deferred" if deferred else "done",
        summary="Owner review state changed.",
        details={
            "task_class": "user_gate",
            "decision_scope": (
                f"direction:action:{proposal['packet_ref']}"
            ),
            "decision_outcome": None if deferred else disposition,
        },
        recorded_at=OBSERVED_AT,
    )
    return append_rollout_event(path, event)


def append_unrelated_rollout_events(path: Path, *, count: int = 1001) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for index in range(count):
            event = build_rollout_event(
                goal_id="example-decision-goal",
                event_kind="quota_monitor_poll",
                run_id=f"unrelated-{index}",
                status="observed",
                recorded_at=OBSERVED_AT,
            )
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def test_profile_runtime_builds_providers_and_returns_private_cursor_proposal(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    callback_contents: list[str] = []

    def rebase(collection: Any) -> DecisionEvidenceRecords:
        current = collection.authority[0]
        callback_contents.append(current.content)
        return DecisionEvidenceRecords(
            changed_facts=(
                {
                    "fact_id": "fact:pilot",
                    "summary": "Current authority keeps adoption at pilot.",
                    "source_ref": current.source_ref,
                    "source_revision": current.source_revision,
                    "observed_at": current.observed_at,
                    "freshness": "current",
                    "authority": "first_party",
                },
            )
        )

    activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        rebase=rebase,
    )

    assert activation["status"] == "available"
    assert assembly is not None
    assert callback_contents == [PRIVATE_CONTENT]
    assert assembly.proposed_cursors["source:authority"].startswith("sha256:")
    packet = assembly.public_packet()
    assert packet["evidence_packet"]["changed_facts"][0]["fact_id"] == "fact:pilot"
    assert packet["cursor_checkpoint"]["sources"][0]["disposition"] == (
        "ready_after_writeback"
    )
    assert PRIVATE_CONTENT not in json.dumps(packet, sort_keys=True)
    assert str(authority) not in json.dumps(packet, sort_keys=True)


def test_extension_context_provider_failure_fails_open_without_blocking_sources(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_extension_context_profile(
        tmp_path / "profile.json",
        authority,
    )

    activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        rebase=lambda _collection: DecisionEvidenceRecords(),
        runtime_root=tmp_path / "runtime",
    )

    assert activation["status"] == "available"
    assert assembly is not None
    packet = assembly.public_packet()
    assert packet["source_scan_receipts"][0]["status"] == "completed"
    assert packet["context_retrieval_receipt"]["status"] == "unavailable"
    assert packet["context_retrieval_receipt"]["reason_code"] == (
        "extension_not_installed"
    )


def test_private_host_provider_drives_generic_scan_exact_read_and_checkpoint(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(
        tmp_path / "profile.json",
        authority,
        adapter="private-host-adapter",
    )
    provider = LocalFileDecisionSourceProvider(
        provider_id="local-authority",
        max_bytes=4096,
    )

    activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        rebase=semantic_rebase,
        source_provider_overrides={"local-authority": provider},
    )

    assert activation["status"] == "available"
    assert activation["runtime_bound_provider_count"] == 1
    assert assembly is not None
    packet = assembly.public_packet()
    assert packet["source_scan_receipts"][0]["status"] == "completed"
    assert packet["source_scan_receipts"][0]["exact_read_count"] == 1
    assert packet["evidence_packet"]["provider_health"][0]["status"] == "healthy"
    assert packet["cursor_checkpoint"]["sources"][0]["disposition"] == (
        "ready_after_writeback"
    )
    assert PRIVATE_CONTENT not in json.dumps(packet, sort_keys=True)
    assert str(authority) not in json.dumps(packet, sort_keys=True)


def test_private_host_provider_can_commit_cursor_after_validated_writeback(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(
        tmp_path / "profile.json",
        authority,
        adapter="private-host-adapter",
    )
    provider = LocalFileDecisionSourceProvider(
        provider_id="local-authority",
        max_bytes=4096,
    )
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"

    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
        source_provider_overrides={"local-authority": provider},
    )
    assert assembly is not None
    assert assembly.runtime_bound_provider_ids == ("local-authority",)
    proposal, outcome = decision_chain(assembly)
    event = append_decision_writeback(event_log, assembly, proposal, outcome)

    receipt = commit_profile_decision_cursors(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        cursor_path=cursors,
        assembly=assembly,
        proposal=proposal,
        outcome_receipt=outcome,
        lifecycle_event_log_path=event_log,
        lifecycle_event_id=event["event_id"],
    )

    assert receipt["status"] == "committed"
    assert receipt["cursor_state_mutated"] is True
    assert receipt["readback_verified"] is True
    assert json.loads(cursors.read_text(encoding="utf-8")) == dict(
        assembly.proposed_cursors
    )


def test_cursor_commit_finds_validated_writeback_before_long_log_tail(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, outcome = decision_chain(assembly)
    writeback = append_decision_writeback(event_log, assembly, proposal, outcome)
    append_unrelated_rollout_events(event_log)

    receipt = commit_profile_decision_cursors(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        cursor_path=cursors,
        assembly=assembly,
        proposal=proposal,
        outcome_receipt=outcome,
        lifecycle_event_log_path=event_log,
        lifecycle_event_id=str(writeback["event_id"]),
    )

    assert receipt["status"] == "committed"
    assert receipt["lifecycle_event_id"] == writeback["event_id"]
    assert json.loads(cursors.read_text(encoding="utf-8")) == dict(
        assembly.proposed_cursors
    )


def test_runtime_provider_identity_mismatch_fails_open(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(
        tmp_path / "profile.json",
        authority,
        adapter="private-host-adapter",
    )
    provider = LocalFileDecisionSourceProvider(
        provider_id="another-provider",
        max_bytes=4096,
    )

    activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        rebase=lambda _collection: DecisionEvidenceRecords(),
        source_provider_overrides={"local-authority": provider},
    )

    assert activation["status"] == "available"
    assert assembly is not None
    receipt = assembly.public_packet()["source_scan_receipts"][0]
    assert receipt["status"] == "unavailable"
    assert receipt["reason_code"] == "runtime_provider_identity_mismatch"
    assert assembly.proposed_cursors == {}


def test_prepare_evidence_cli_preserves_private_cursor_until_semantic_rebase(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    cursor_payload = {"source:authority": "sha256:previous"}
    cursors.write_text(json.dumps(cursor_payload), encoding="utf-8")
    cursor_before = cursors.read_bytes()

    assert (
        main(
            [
                "--format",
                "json",
                "decision-context",
                "prepare-evidence",
                "--goal-id",
                "example-decision-goal",
                "--agent-id",
                "example-agent",
                "--profile",
                str(profile),
                "--decision-id",
                "decision:adoption",
                "--cursor-state",
                str(cursors),
                "--observed-at",
                OBSERVED_AT,
                "--before",
                BEFORE,
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload, sort_keys=True)
    assembly = payload["assembly"]

    assert payload["status"] == "available"
    assert payload["semantic_rebase_performed"] is False
    assert payload["validated_writeback_required"] is True
    assert payload["cursor_commit_allowed"] is False
    assert payload["cursor_state_mutated"] is False
    assert assembly["source_scan_receipts"][0]["exact_read_count"] == 1
    assert assembly["cursor_checkpoint"]["sources"][0]["disposition"] == "preserve"
    assert assembly["cursor_checkpoint"]["sources"][0]["reason_code"] == (
        "rebase_incomplete"
    )
    assert cursors.read_bytes() == cursor_before
    for private_value in (
        PRIVATE_CONTENT,
        str(authority),
        str(profile),
        str(cursors),
        "sha256:previous",
    ):
        assert private_value not in serialized


def test_prepare_evidence_cli_passes_effective_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    captured: dict[str, Any] = {}

    def assemble(**kwargs: Any) -> tuple[dict[str, Any], None]:
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "disabled",
            "available": False,
        }, None

    monkeypatch.setattr(
        decision_context_cli,
        "assemble_profile_decision_evidence",
        assemble,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--format",
                "json",
                "decision-context",
                "prepare-evidence",
                "--goal-id",
                "example-decision-goal",
                "--agent-id",
                "example-agent",
                "--profile",
                str(profile),
                "--decision-id",
                "decision:adoption",
            ]
        ) == 0

    assert captured["runtime_root"] == tmp_path / "runtime"


def test_ephemeral_recall_overrides_scope_without_scanning_or_profile_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_extension_context_profile(
        tmp_path / "profile.json",
        authority,
    )
    profile_before = profile.read_bytes()
    captured: dict[str, Any] = {}

    class Provider:
        provider_id = "loopx-obelisk"

        def retrieve(self, **kwargs: Any) -> ContextProviderRetrieval:
            captured.update(kwargs)
            return ContextProviderRetrieval(
                provider=self.provider_id,
                namespace=kwargs["namespace"],
                status="completed",
                query_summary=kwargs["query_summary"],
                observed_at=kwargs["observed_at"],
                search_performed=True,
                read_performed=True,
                items=(
                    ContextProviderItem(
                        resource_ref="private:thread-b:message-1",
                        summary="Historical Codex task assistant",
                        content=PRIVATE_RECALL_CONTENT,
                        score=0.9,
                    ),
                ),
                requested_limit=kwargs["max_results"],
            )

    monkeypatch.setattr(
        decision_context_runtime,
        "_build_advisory_context_provider",
        lambda *_args, **_kwargs: Provider(),
    )
    monkeypatch.setattr(
        decision_context_runtime,
        "_build_source_providers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ephemeral recall must not build source providers")
        ),
    )

    packet = recall_profile_decision_context(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        context_scope_ref="host-session:codex:thread-b",
        query="extension boundary",
        query_summary="peer task extension decision",
        observed_at=OBSERVED_AT,
        max_results=2,
        timeout_seconds=3,
        runtime_root=tmp_path / "runtime",
    )

    assert captured["scope_ref"] == "host-session:codex:thread-b"
    assert captured["query"] == "extension boundary"
    assert packet["status"] == "completed"
    assert packet["visibility"] == "local_private_transient"
    assert packet["source_scan_performed"] is False
    assert packet["cursor_state_read"] is False
    assert packet["pending_settlement_written"] is False
    assert packet["profile_write_performed"] is False
    assert packet["private_locator_persisted"] is False
    assert packet["execution_authorized"] is False
    assert packet["results"][0]["content"] == PRIVATE_RECALL_CONTENT
    assert packet["results"][0]["content_trust"] == "untrusted_advisory"
    assert packet["results"][0]["content_may_instruct"] is False
    assert packet["raw_content_returned"] is True
    assert [
        {
            key: value
            for key, value in item.items()
            if key in {"provider_ref", "summary", "score"}
        }
        for item in packet["results"]
    ] == packet["retrieval_receipt"]["results"]
    assert packet["raw_content_persisted"] is False
    assert "content" not in packet["retrieval_receipt"]["results"][0]
    assert "thread-b" not in json.dumps(packet["retrieval_receipt"])
    assert "extension boundary" not in json.dumps(
        packet["retrieval_receipt"]
    )
    assert profile.read_bytes() == profile_before


def test_ephemeral_recall_cli_passes_one_off_scope_and_effective_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    captured: dict[str, Any] = {}

    def recall(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "schema_version": "decision_context_ephemeral_recall_v0",
            "ok": True,
            "status": "completed",
        }

    monkeypatch.setattr(
        decision_context_cli,
        "recall_profile_decision_context",
        recall,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(
            [
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--format",
                "json",
                "decision-context",
                "recall-context",
                "--goal-id",
                "example-decision-goal",
                "--agent-id",
                "example-agent",
                "--profile",
                str(profile),
                "--context-scope-ref",
                "host-session:codex:thread-b",
                "--query",
                "approved extension boundary",
                "--query-summary",
                "peer task extension decision",
                "--max-results",
                "2",
            ]
        ) == 0

    assert captured["runtime_root"] == tmp_path / "runtime"
    assert captured["context_scope_ref"] == "host-session:codex:thread-b"
    assert captured["query"] == "approved extension boundary"
    assert captured["query_summary"] == "peer task extension decision"
    assert captured["max_results"] == 2


def test_ephemeral_recall_requires_goal_and_agent_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_extension_context_profile(
        tmp_path / "profile.json",
        authority,
    )
    provider_calls = 0

    def build_provider(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("disabled agent must not reach provider construction")

    monkeypatch.setattr(
        decision_context_runtime,
        "_build_advisory_context_provider",
        build_provider,
    )

    packet = recall_profile_decision_context(
        goal_id="example-decision-goal",
        agent_id="another-agent",
        profile_path=profile,
        context_scope_ref="host-session:codex:thread-b",
        query="extension boundary",
        query_summary="peer task extension decision",
        observed_at=OBSERVED_AT,
    )

    assert packet["status"] == "agent_not_enabled"
    assert packet["ok"] is False
    assert packet["results"] == []
    assert packet["raw_content_returned"] is False
    assert provider_calls == 0


def test_ephemeral_recall_disabled_profile_does_not_build_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_extension_context_profile(
        tmp_path / "profile.json",
        authority,
    )
    payload = json.loads(profile.read_text(encoding="utf-8"))
    payload["enabled"] = False
    profile.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        decision_context_runtime,
        "_build_advisory_context_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled profile must not build a context provider")
        ),
    )

    packet = recall_profile_decision_context(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        context_scope_ref="host-session:codex:thread-b",
        query="extension boundary",
        query_summary="peer task extension decision",
        observed_at=OBSERVED_AT,
    )

    assert packet["status"] == "disabled"
    assert packet["ok"] is False
    assert packet["results"] == []
    assert packet["raw_content_returned"] is False


def test_ephemeral_recall_extension_unavailable_returns_degraded_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_extension_context_profile(
        tmp_path / "profile.json",
        authority,
    )
    profile_before = profile.read_bytes()

    packet = recall_profile_decision_context(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        context_scope_ref="host-session:codex:thread-b",
        query="extension boundary",
        query_summary="peer task extension decision",
        observed_at=OBSERVED_AT,
        runtime_root=tmp_path / "missing-runtime",
    )

    assert packet["status"] == "unavailable"
    assert packet["reason_code"] == "extension_not_installed"
    assert packet["ok"] is False
    assert packet["results"] == []
    assert packet["retrieval_receipt"]["status"] == "unavailable"
    assert packet["retrieval_receipt"]["reason_code"] == (
        "extension_not_installed"
    )
    assert packet["retrieval_receipt"]["search_performed"] is False
    assert packet["retrieval_receipt"]["read_performed"] is False
    assert packet["provider_readiness"] == {
        "schema_version": "loopx_extension_provider_readiness_v0",
        "extension_id": "loopx-obelisk",
        "status": "extension_not_installed",
        "installed": False,
        "enabled": False,
        "doctor_verified": False,
        "next_action": "install_extension",
    }
    assert packet["source_scan_performed"] is False
    assert packet["cursor_state_read"] is False
    assert packet["pending_settlement_written"] is False
    assert packet["profile_write_performed"] is False
    assert packet["external_writes_performed"] is False
    assert packet["execution_authorized"] is False
    assert profile.read_bytes() == profile_before
    assert not (tmp_path / "missing-runtime").exists()

    assert (
        main(
            [
                "--runtime-root",
                str(tmp_path / "missing-runtime"),
                "--format",
                "json",
                "decision-context",
                "recall-context",
                "--goal-id",
                "example-decision-goal",
                "--agent-id",
                "example-agent",
                "--profile",
                str(profile),
                "--context-scope-ref",
                "host-session:codex:thread-b",
                "--query",
                "extension boundary",
                "--query-summary",
                "peer task extension decision",
                "--observed-at",
                OBSERVED_AT,
            ]
        )
        == 0
    )
    cli_packet = json.loads(capsys.readouterr().out)
    assert cli_packet["reason_code"] == "extension_not_installed"
    assert cli_packet["provider_readiness"] == packet["provider_readiness"]
    assert profile.read_bytes() == profile_before
    assert not (tmp_path / "missing-runtime").exists()


def test_ephemeral_recall_fails_closed_when_profile_changes_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_extension_context_profile(
        tmp_path / "profile.json",
        authority,
    )

    class Provider:
        provider_id = "loopx-obelisk"

        def retrieve(self, **kwargs: Any) -> ContextProviderRetrieval:
            profile.write_text(profile.read_text() + " ", encoding="utf-8")
            return ContextProviderRetrieval(
                provider=self.provider_id,
                namespace=kwargs["namespace"],
                status="completed",
                query_summary=kwargs["query_summary"],
                observed_at=kwargs["observed_at"],
                search_performed=True,
                read_performed=True,
                items=(
                    ContextProviderItem(
                        resource_ref="private:thread-b:message-1",
                        summary="Historical Codex task assistant",
                        content=PRIVATE_RECALL_CONTENT,
                    ),
                ),
                requested_limit=kwargs["max_results"],
            )

    monkeypatch.setattr(
        decision_context_runtime,
        "_build_advisory_context_provider",
        lambda *_args, **_kwargs: Provider(),
    )

    packet = recall_profile_decision_context(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        context_scope_ref="host-session:codex:thread-b",
        query="extension boundary",
        query_summary="peer task extension decision",
        observed_at=OBSERVED_AT,
    )

    assert packet["status"] == "unavailable"
    assert packet["reason_code"] == "profile_changed_during_recall"
    assert packet["results"] == []
    assert packet["raw_content_returned"] is False


def test_profile_runtime_fails_open_when_provider_config_cannot_build(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(
        tmp_path / "profile.json",
        authority,
        max_bytes=-1,
    )

    activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        rebase=lambda _collection: DecisionEvidenceRecords(),
    )

    assert activation["status"] == "available"
    assert assembly is not None
    receipt = assembly.public_packet()["source_scan_receipts"][0]
    assert receipt["status"] == "unavailable"
    assert receipt["reason_code"] == "provider_configuration_failed"
    assert assembly.proposed_cursors == {}


def test_disabled_profile_does_not_touch_authority_provider(tmp_path: Path) -> None:
    missing_authority = tmp_path / "missing.md"
    profile = write_profile(
        tmp_path / "profile.json",
        missing_authority,
        enabled=False,
    )

    activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        rebase=lambda _collection: DecisionEvidenceRecords(),
    )

    assert activation["status"] == "disabled"
    assert assembly is None


def test_private_cursor_state_rejects_unknown_source_ids(tmp_path: Path) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    cursors.write_text(json.dumps({"source:unknown": "cursor"}), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown source ids"):
        assemble_profile_decision_evidence(
            goal_id="example-decision-goal",
            agent_id="example-agent",
            profile_path=profile,
            decision_id="decision:adoption",
            observed_at=OBSERVED_AT,
            before=BEFORE,
            cursor_path=cursors,
            rebase=lambda _collection: DecisionEvidenceRecords(),
        )


def test_on_demand_source_requires_explicit_selection(tmp_path: Path) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(
        tmp_path / "profile.json",
        authority,
        scan_mode="on_demand",
    )

    _activation, automatic = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        rebase=lambda _collection: DecisionEvidenceRecords(),
    )
    _activation, explicit = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        source_ids={"source:authority"},
        rebase=lambda _collection: DecisionEvidenceRecords(),
    )

    assert automatic is not None
    assert automatic.public_packet()["source_manifest"]["source_count"] == 0
    assert explicit is not None
    assert explicit.public_packet()["source_manifest"]["source_count"] == 1
    assert explicit.public_packet()["source_scan_receipts"][0]["status"] == "completed"


def test_cursor_commit_requires_exact_read_lifecycle_writeback(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, outcome = decision_chain(assembly)

    with pytest.raises(ValueError, match="writeback event was not found"):
        commit_profile_decision_cursors(
            goal_id="example-decision-goal",
            agent_id="example-agent",
            profile_path=profile,
            cursor_path=cursors,
            assembly=assembly,
            proposal=proposal,
            outcome_receipt=outcome,
            lifecycle_event_log_path=event_log,
            lifecycle_event_id="missing-event",
        )

    assert not cursors.exists()


def test_thin_host_callsite_commits_cursor_after_lifecycle_writeback(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, outcome = decision_chain(assembly)
    event = append_decision_writeback(event_log, assembly, proposal, outcome)

    receipt = commit_profile_decision_cursors(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        cursor_path=cursors,
        assembly=assembly,
        proposal=proposal,
        outcome_receipt=outcome,
        lifecycle_event_log_path=event_log,
        lifecycle_event_id=event["event_id"],
    )

    assert receipt["status"] == "committed"
    assert receipt["cursor_state_mutated"] is True
    assert receipt["readback_verified"] is True
    assert receipt["sources"][0]["status"] == "advanced"
    assert json.loads(cursors.read_text(encoding="utf-8")) == dict(
        assembly.proposed_cursors
    )
    serialized = json.dumps(receipt, sort_keys=True)
    for private_value in (
        PRIVATE_CONTENT,
        str(authority),
        str(profile),
        str(cursors),
        next(iter(assembly.proposed_cursors.values())),
    ):
        assert private_value not in serialized

    replay = commit_profile_decision_cursors(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        cursor_path=cursors,
        assembly=assembly,
        proposal=proposal,
        outcome_receipt=outcome,
        lifecycle_event_log_path=event_log,
        lifecycle_event_id=event["event_id"],
    )
    assert replay["status"] == "replayed"
    assert replay["cursor_state_mutated"] is False
    assert replay["sources"][0]["status"] == "replayed"


def test_cursor_commit_rejects_concurrent_cursor_change(tmp_path: Path) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    cursors.write_text(
        json.dumps({"source:authority": "sha256:previous"}),
        encoding="utf-8",
    )
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, outcome = decision_chain(assembly)
    event = append_decision_writeback(event_log, assembly, proposal, outcome)
    concurrent = {"source:authority": "sha256:concurrent"}
    cursors.write_text(json.dumps(concurrent), encoding="utf-8")

    with pytest.raises(ValueError, match="changed since evidence assembly"):
        commit_profile_decision_cursors(
            goal_id="example-decision-goal",
            agent_id="example-agent",
            profile_path=profile,
            cursor_path=cursors,
            assembly=assembly,
            proposal=proposal,
            outcome_receipt=outcome,
            lifecycle_event_log_path=event_log,
            lifecycle_event_id=event["event_id"],
        )

    assert json.loads(cursors.read_text(encoding="utf-8")) == concurrent


def test_cursor_commit_rejects_profile_change_after_assembly(tmp_path: Path) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, outcome = decision_chain(assembly)
    event = append_decision_writeback(event_log, assembly, proposal, outcome)
    changed_profile = json.loads(profile.read_text(encoding="utf-8"))
    changed_profile["sources"][0]["max_changes"] = 5
    profile.write_text(json.dumps(changed_profile), encoding="utf-8")

    with pytest.raises(ValueError, match="profile changed since evidence assembly"):
        commit_profile_decision_cursors(
            goal_id="example-decision-goal",
            agent_id="example-agent",
            profile_path=profile,
            cursor_path=cursors,
            assembly=assembly,
            proposal=proposal,
            outcome_receipt=outcome,
            lifecycle_event_log_path=event_log,
            lifecycle_event_id=event["event_id"],
        )

    assert not cursors.exists()


def test_cursor_commit_rejects_writeback_without_full_packet_chain(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, outcome = decision_chain(assembly)
    packet = assembly.public_packet()
    event = build_rollout_event(
        goal_id="example-decision-goal",
        event_kind="validation",
        decision_id="decision:adoption",
        status="committed",
        artifact_refs=[
            packet["evidence_packet"]["packet_ref"],
            proposal["packet_ref"],
        ],
        recorded_at=OBSERVED_AT,
    )
    append_rollout_event(event_log, event)

    with pytest.raises(ValueError, match="does not match decision"):
        commit_profile_decision_cursors(
            goal_id="example-decision-goal",
            agent_id="example-agent",
            profile_path=profile,
            cursor_path=cursors,
            assembly=assembly,
            proposal=proposal,
            outcome_receipt=outcome,
            lifecycle_event_log_path=event_log,
            lifecycle_event_id=event["event_id"],
        )

    assert not cursors.exists()


@pytest.mark.parametrize("disposition", ["approve", "reject", "defer"])
def test_review_settlement_commits_consumed_cursor_without_future_outcome(
    tmp_path: Path,
    disposition: str,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    pending = tmp_path / "pending.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, _legacy_outcome = decision_chain(assembly)
    write_private_pending_decision_settlement(pending, assembly)
    reloaded = load_private_pending_decision_settlement(pending)
    gate_event = append_review_gate_event(event_log, proposal, disposition)

    result = settle_profile_decision_review(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        cursor_path=cursors,
        assembly=reloaded,
        lifecycle_event_log_path=event_log,
        actor_ref="owner:goal",
        reason_code=f"owner_{disposition}",
        summary=f"Owner recorded {disposition}.",
        proposal=proposal,
        source_event_id=str(gate_event["event_id"]),
        execute=True,
    )

    assert result["disposition"] == disposition
    assert result["review_receipt"]["outcome_observation_required"] is (
        disposition == "approve"
    )
    assert result["cursor_commit"]["settlement_disposition"] == disposition
    assert result["cursor_commit"]["outcome_receipt_ref"] is None
    assert json.loads(cursors.read_text(encoding="utf-8")) == dict(
        assembly.proposed_cursors
    )


def test_review_settlement_finds_gate_event_before_long_log_tail(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, _legacy_outcome = decision_chain(assembly)
    gate_event = append_review_gate_event(event_log, proposal, "approve")
    append_unrelated_rollout_events(event_log)

    result = settle_profile_decision_review(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        cursor_path=cursors,
        assembly=assembly,
        lifecycle_event_log_path=event_log,
        actor_ref="owner:goal",
        reason_code="owner_approve",
        summary="Owner recorded approve.",
        proposal=proposal,
        source_event_id=str(gate_event["event_id"]),
        execute=True,
    )

    assert result["disposition"] == "approve"
    assert result["cursor_commit"]["status"] == "committed"
    assert json.loads(cursors.read_text(encoding="utf-8")) == dict(
        assembly.proposed_cursors
    )


def test_semantic_no_change_settlement_is_quiet_and_commits_cursor(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=lambda _collection: DecisionEvidenceRecords(
            semantic_no_change=True
        ),
    )
    assert assembly is not None
    assert assembly.public_packet()["semantic_rebase"]["status"] == (
        "no_material_change"
    )

    result = settle_profile_decision_review(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        cursor_path=cursors,
        assembly=assembly,
        lifecycle_event_log_path=event_log,
        actor_ref="agent:decision-advisor",
        reason_code="no_material_thesis_change",
        summary="Changed material does not alter the current decision.",
        execute=True,
    )

    assert result["disposition"] == "no_change"
    assert result["quiet_noop"] is True
    assert result["review_receipt"]["gate_todo_id"] is None
    assert result["cursor_commit"]["settlement_disposition"] == "no_change"
    assert json.loads(cursors.read_text(encoding="utf-8")) == dict(
        assembly.proposed_cursors
    )


def test_review_settlement_rejects_gate_bound_to_another_proposal(
    tmp_path: Path,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "rollout-events.jsonl"
    _activation, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal",
        agent_id="example-agent",
        profile_path=profile,
        decision_id="decision:adoption",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        cursor_path=cursors,
        rebase=semantic_rebase,
    )
    assert assembly is not None
    proposal, _legacy_outcome = decision_chain(assembly)
    wrong = dict(proposal)
    wrong["packet_ref"] = "decision-proposal-another"
    gate_event = append_review_gate_event(event_log, wrong, "approve")

    with pytest.raises(ValueError, match="does not match proposal"):
        settle_profile_decision_review(
            goal_id="example-decision-goal",
            agent_id="example-agent",
            profile_path=profile,
            cursor_path=cursors,
            assembly=assembly,
            lifecycle_event_log_path=event_log,
            actor_ref="owner:goal",
            reason_code="owner_approve",
            summary="Owner approved another proposal.",
            proposal=proposal,
            source_event_id=str(gate_event["event_id"]),
            execute=True,
        )

    assert not cursors.exists()


@pytest.mark.parametrize("rebase", [
    lambda _: DecisionEvidenceRecords(),
    lambda _: DecisionEvidenceRecords(semantic_no_change=True),
])
@pytest.mark.parametrize("execute", [False, True])
def test_observed_settlement_requires_material_review_before_any_write(
    tmp_path: Path, rebase: Any, execute: bool,
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    event_log = tmp_path / "events.jsonl"
    _, assembly = assemble_profile_decision_evidence(
        goal_id="example-decision-goal", agent_id="example-agent",
        profile_path=profile, cursor_path=cursors,
        decision_id="decision:adoption", observed_at=OBSERVED_AT,
        before=BEFORE, rebase=rebase,
    )
    assert assembly is not None
    with pytest.raises(ValueError, match="observed settlement lacks material review"):
        settle_profile_decision_review(
            goal_id="example-decision-goal", agent_id="example-agent",
            profile_path=profile, cursor_path=cursors, assembly=assembly,
            lifecycle_event_log_path=event_log,
            actor_ref="agent:decision-advisor", reason_code="new_facts_reviewed",
            summary="New facts were reviewed.", disposition="observed",
            execute=execute,
        )
    assert not cursors.exists()
    assert not event_log.exists()


def test_prepare_and_settle_no_change_cli_crosses_turns_without_private_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    authority = tmp_path / "authority.md"
    authority.write_text(PRIVATE_CONTENT, encoding="utf-8")
    profile = write_profile(tmp_path / "profile.json", authority)
    cursors = tmp_path / "cursors.json"
    pending = tmp_path / "pending.json"
    event_log = tmp_path / "rollout-events.jsonl"
    rebase = tmp_path / "rebase.json"
    rebase.write_text(
        json.dumps({"semantic_no_change": True}),
        encoding="utf-8",
    )

    assert main(
        [
            "--format",
            "json",
            "decision-context",
            "prepare-review",
            "--goal-id",
            "example-decision-goal",
            "--agent-id",
            "example-agent",
            "--profile",
            str(profile),
            "--decision-id",
            "decision:adoption",
            "--rebase-json",
            str(rebase),
            "--pending-settlement",
            str(pending),
            "--cursor-state",
            str(cursors),
            "--observed-at",
            OBSERVED_AT,
            "--before",
            BEFORE,
            "--execute",
        ]
    ) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["pending_settlement_written"] is True
    assert not cursors.exists()

    assert main(
        [
            "--format",
            "json",
            "decision-context",
            "settle-review",
            "--goal-id",
            "example-decision-goal",
            "--agent-id",
            "example-agent",
            "--profile",
            str(profile),
            "--cursor-state",
            str(cursors),
            "--pending-settlement",
            str(pending),
            "--event-log",
            str(event_log),
            "--actor-ref",
            "agent:decision-advisor",
            "--reason-code",
            "no_material_thesis_change",
            "--summary",
            "Changed material does not alter the current decision.",
            "--execute",
        ]
    ) == 0
    settled = json.loads(capsys.readouterr().out)
    serialized = json.dumps(settled, sort_keys=True)
    assert settled["disposition"] == "no_change"
    assert settled["cursor_commit"]["readback_verified"] is True
    for private_value in (
        PRIVATE_CONTENT,
        str(authority),
        str(profile),
        str(cursors),
        str(pending),
        str(event_log),
    ):
        assert private_value not in serialized
