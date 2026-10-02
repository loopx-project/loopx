"""Private context, synthetic OV/Lark, real local authority and source CLI."""
import copy
import argparse
import json
import subprocess
import sys

import pytest

from test_room_work import setup as room_setup, snapshot, claim, GOAL, TODO, ROOT  # noqa: F401
from loopx.cli_commands.goal_channel import _quota_packet, handle_goal_channel_command
from loopx.extensions.lark import room_resume as resume
from loopx.extensions.lark.goal_channel_contracts import write_goal_channel_binding
from loopx.capabilities.agent_turn_recall import runtime as recall_runtime
from loopx.capabilities.reward_memory import outcome_lifecycle
from loopx.capabilities.reward_memory.experiment import load_reward_memory_experiment_config
from loopx.capabilities.context_providers.base import ContextProviderItem, ContextProviderRetrieval
from tests.capabilities.test_agent_turn_recall import raw_config, active_record

TURN = "room-resume-fixture-turn"


@pytest.fixture
def reconnect(room_setup, monkeypatch):  # noqa: F811
    args, room, binding = room_setup
    assert claim(args, "agent-a", snapshot(args)["provider_revision"], "resume-initial-claim")["ok"]
    room.messages.clear()
    source = json.loads(args["registry_path"].read_text())
    source["goals"][0].update(domain="room-resume-fixture", status="active-read-only",
        adapter={"kind": "read_only_project_map_v0", "status": "connected-read-only"})
    args["registry_path"].write_text(json.dumps(source))
    source["registry_role"] = "global-local"
    source["goals"][0]["source_registry"] = str(args["registry_path"])
    (args["runtime_root"] / "registry.global.json").write_text(json.dumps(source))
    monkeypatch.setattr(resume, "resolve_extension_activation", lambda *a, **k: {"enabled": True})

    def fresh():
        return _quota_packet(registry_path=args["registry_path"], runtime_root_arg=str(args["runtime_root"]),
            goal_id=GOAL, agent_id="agent-a")

    quota = fresh()
    admitted = copy.deepcopy(quota)
    admitted.update(mode="should-run", heartbeat_receipt={"turn_instance_id": TURN, "status": "committed"})
    kwargs = dict(registry_path=args["registry_path"], authority_root=args["runtime_root"],
        broker_root=args["runtime_root"], binding_path=args["binding_path"], target_path=args["target_path"],
        goal_id=GOAL, actor_id="agent-a", quota_decision=admitted, turn_instance_id=TURN,
        read_current_quota=fresh, runner=room)
    return args, room, binding, kwargs


def test_source_cli_preview_is_private_and_performs_no_recall_or_claim(reconnect, tmp_path):
    args, room, _, kwargs = reconnect
    before = snapshot(args)["provider_revision"]
    result = resume.run_room_resume(**kwargs, execute=False)
    assert result["ok"] and result["status"] == "planned", json.dumps(result)
    saved = tmp_path / "admitted.json"
    guard = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
        "--format", "json", "quota", "should-run", "--goal-id", GOAL, "--agent-id", "agent-a",
        "--codex-app", "--turn-instance-id", TURN, "--available-capability", "network"],
        cwd=ROOT, text=True, capture_output=True)
    assert guard.returncode == 0, guard.stdout + guard.stderr
    admitted = json.loads(guard.stdout)
    assert admitted["ok"] and admitted["heartbeat_receipt"]["status"] == "committed", admitted
    saved.write_text(guard.stdout)
    proc = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
        "--format", "json", "goal-channel", "work", "resume", "--goal-id", GOAL, "--agent-id", "agent-a",
        "--turn-instance-id", TURN, "--quota-decision-json", str(saved)], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["status"] == "planned"
    assert not room.messages and snapshot(args)["provider_revision"] == before
    assert result["provider_call_count"] == 0 and result["private_context"] is None


def test_default_off_preserves_canonical_work_without_room_or_memory_writes(reconnect):
    args, room, _, kwargs = reconnect
    before = snapshot(args)["provider_revision"]
    result = resume.run_room_resume(**kwargs, execute=True)
    assert result["ok"] and result["status"] == "context_unavailable", result
    assert result["private_context"] is None and result["artifact_references"] == []
    assert result["provider_call_count"] == 0 and not room.messages
    assert snapshot(args)["provider_revision"] == before
    assert not result["external_writes_performed"] and not result["execution_authority_granted"]


@pytest.fixture
def recalled(reconnect, tmp_path, monkeypatch):
    args, room, binding, kwargs = reconnect
    raw = raw_config(goal_id=GOAL, agent_id="agent-a", peer_ref="agent:agent-a")
    raw["automation"]["automatic_ingest"] = True
    path = tmp_path / "recall-config.json"
    path.write_text(json.dumps(raw))
    config = load_reward_memory_experiment_config(project=tmp_path, config_path=path.name)
    status = {"ok": True, "status": "available", "automatic_recall": True, "automatic_ingest": True}
    def resolver(**_):
        return status, config
    monkeypatch.setattr(resume, "resolve_reward_memory_experiment", resolver)
    monkeypatch.setattr(recall_runtime, "resolve_reward_memory_experiment", resolver)

    def forbid_ingest(**_):
        raise AssertionError("Read-only room restore must not reconcile memory writes")
    monkeypatch.setattr(outcome_lifecycle, "reconcile_pending_turn_outcome_ingests_fail_open", forbid_ingest)

    class Provider:
        provider_id = "openviking"
        calls = 0
        mutate = None
        unavailable = False
        content = active_record(config, expires_at="2030-01-01T00:00:00Z")
        ref = raw["project_provider_binding"]["corpus_scopes"][0]["scope_ref"] + "/artifact.json"

        def retrieve(self, **parameters):
            self.calls += 1
            if self.mutate:
                self.mutate()
            if self.unavailable:
                raise RuntimeError("synthetic unavailable provider")
            assert self.ref.startswith(parameters["scope_ref"].rstrip("/") + "/")
            return ContextProviderRetrieval(provider="openviking", namespace=parameters["namespace"],
                status="completed", query_summary=parameters["query_summary"], observed_at=parameters["observed_at"],
                search_performed=True, read_performed=True,
                items=(ContextProviderItem(resource_ref=self.ref, summary="Scoped artifact", content=self.content),))

    provider = Provider()
    return args, room, binding, kwargs, provider, config


def test_restore_carries_only_current_explicit_scoped_references_without_writes(recalled):
    args, room, _, kwargs, provider, _ = recalled
    before = snapshot(args)["provider_revision"]
    requested = [provider.ref, "viking://user/other/private.json"]
    first = resume.run_room_resume(**kwargs, execute=True, artifact_refs=requested, provider=provider)
    assert first["ok"] and first["status"] == "restored", json.dumps(first)
    assert first["private_context"]["guidance"]
    assert first["artifact_references"] == [{"ref": provider.ref, "source": "current_scoped_recall", "target_access_granted": False}]
    assert first["readback"]["omitted_reference_count"] == 1
    assert first["current_quota"]["selected_todo"]["claimed_by"] == "agent-a"
    # Reconnect retrieves anew even when a previous private receipt exists.
    second = resume.run_room_resume(**kwargs, execute=True, provider=provider)
    assert second["ok"] and provider.calls == 2
    assert snapshot(args)["provider_revision"] == before and not room.messages
    assert not first["execution_authority_granted"] and not first["external_writes_performed"]


@pytest.mark.parametrize("change", ["actor", "binding", "route", "memory_scope", "canonical_revision", "user_gate"])
def test_changes_during_retrieval_discard_context_and_references(recalled, change):
    args, room, binding, kwargs, provider, config = recalled
    before = snapshot(args)["provider_revision"]

    def mutate():
        if change == "binding":
            binding["bindings"][GOAL]["connections"]["agent-a"]["enabled"] = False
            write_goal_channel_binding(args["binding_path"], binding)
        elif change == "memory_scope":
            config["automation"]["automatic_recall"] = False
        elif change in {"actor", "route"}:
            path = args["registry_path"] if change == "actor" else args["runtime_root"] / "registry.global.json"
            source = json.loads(path.read_text())
            if change == "actor":
                source["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
            else:
                source["goals"][0]["source_registry"] = str(path.parent / "replacement.json")
            path.write_text(json.dumps(source))
        else:
            operation = ["todo", "update", "--todo-id", TODO, "--text", "Current fixture task changed.",
                "--update-operation-id", "resume-concurrent-fixture"] if change == "canonical_revision" else [
                "todo", "add", "--role", "user", "--task-class", "user_gate", "--blocks-agent", "agent-a",
                "--bound-agent", "agent-a", "--text", "Approve the fixture action."]
            proc = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
                "--format", "json", *operation, "--goal-id", GOAL, "--agent-id", "agent-a"],
                cwd=ROOT, text=True, capture_output=True)
            assert proc.returncode == 0, proc.stdout + proc.stderr

    provider.mutate = mutate
    result = resume.run_room_resume(**kwargs, execute=True, artifact_refs=[provider.ref], provider=provider)
    assert not result["ok"], result
    assert result["private_context"] is None and result["artifact_references"] == []
    assert provider.calls == 1 and not room.messages
    if change in {"actor", "binding", "route"}:
        assert "current_quota" not in result and "work_projection" not in result
    if change == "user_gate":
        assert result["status"] == "authority_changed"
        assert result["current_quota"]["should_run"] is False
        assert result["work_projection"]["counts"]["user_gates"] == 1
    if change not in {"canonical_revision", "user_gate"}:
        assert snapshot(args)["provider_revision"] == before


def test_provider_failure_preserves_current_quota_with_reduced_context(recalled):
    args, room, _, kwargs, provider, _ = recalled
    before = snapshot(args)["provider_revision"]
    provider.unavailable = True
    result = resume.run_room_resume(**kwargs, execute=True, artifact_refs=[provider.ref], provider=provider)
    assert result["ok"] and result["status"] == "context_unavailable", result
    assert result["private_context"] is None and result["artifact_references"] == []
    assert result["current_quota"]["selected_todo"]["todo_id"] == TODO
    assert not room.messages and snapshot(args)["provider_revision"] == before


@pytest.mark.parametrize("invalid", ["actor", "turn", "revision_basis", "provider_identity"])
def test_invalid_admission_never_contacts_context_provider(recalled, invalid):
    args, room, _, kwargs, provider, _ = recalled
    kwargs = copy.deepcopy(kwargs) if invalid != "provider_identity" else dict(kwargs)
    if invalid == "actor":
        kwargs["quota_decision"]["agent_identity"]["agent_id"] = "agent-b"
    elif invalid == "turn":
        kwargs["turn_instance_id"] = "old-turn"
    elif invalid == "revision_basis":
        kwargs["quota_decision"]["selected_todo"]["todo_id"] = "todo_other"
    else:
        room.authenticated = False
    before = snapshot(args)["provider_revision"]
    result = resume.run_room_resume(**kwargs, execute=True, provider=provider)
    assert not result["ok"] and provider.calls == 0
    assert result["private_context"] is None and not room.messages
    assert snapshot(args)["provider_revision"] == before


@pytest.mark.parametrize("invalid", ["expired", "foreign_peer"])
def test_unqualified_records_cannot_restore_context_or_references(recalled, invalid):
    _, room, _, kwargs, provider, _ = recalled
    content = json.loads(provider.content)
    if invalid == "expired":
        content["lifecycle"]["expires_at"] = "2000-01-01T00:00:00Z"
    else:
        content["scope"]["peer_ref"] = "agent:other"
    provider.content = json.dumps(content)
    result = resume.run_room_resume(**kwargs, execute=True, artifact_refs=[provider.ref], provider=provider)
    assert result["ok"] and result["status"] == "context_unavailable", result
    assert result["private_context"] is None and result["artifact_references"] == [] and not room.messages


def test_existing_automatic_recall_keeps_its_reconciliation_default(recalled, monkeypatch):
    _, _, _, kwargs, provider, _ = recalled
    reconciled = []
    monkeypatch.setattr(outcome_lifecycle, "reconcile_pending_turn_outcome_ingests_fail_open",
        lambda **options: reconciled.append(options) or {"ok": True, "status": "not_required"})
    recall_runtime.run_configured_agent_turn_recall(registry_path=kwargs["registry_path"], goal_id=GOAL,
        agent_id="agent-a", quota_decision=kwargs["quota_decision"], turn_instance_id=TURN,
        execute=True, force_refresh=True, provider=provider)
    assert len(reconciled) == 1
    assert resume.run_room_resume(**kwargs, execute=True, provider=provider)["ok"]
    assert len(reconciled) == 1


def test_executing_cli_dispatch_returns_private_context_without_room_delivery(recalled, tmp_path, monkeypatch):
    args, room, _, kwargs, provider, _ = recalled
    saved = tmp_path / "quota.json"
    saved.write_text(json.dumps(kwargs["quota_decision"]))
    actual = resume.run_room_resume
    monkeypatch.setattr(resume, "run_room_resume", lambda **options: actual(**options, runner=room, provider=provider))
    namespace = argparse.Namespace(command="goal-channel", goal_channel_command="work", goal_channel_work_command="resume",
        goal_id=GOAL, agent_id="agent-a", execute=True, binding_path=None, target_path=None,
        turn_instance_id=TURN, quota_decision_json=str(saved), artifact_ref=[provider.ref])
    captured = {}
    code = handle_goal_channel_command(namespace, registry_path=args["registry_path"],
        runtime_root_arg=str(args["runtime_root"]), print_payload=lambda p, *_: captured.update(p), output_format=lambda _: "json")
    assert code == 0 and captured["status"] == "restored", captured
    assert captured["private_context"]["guidance"] and captured["artifact_references"]
    assert not room.messages and not captured["execution_authority_granted"]


def test_exact_instance_profile_fails_closed_before_retrieval(recalled):
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction
    from loopx.control_plane.coordination.local_authority import claim_canonical_todo_if_promoted
    args, room, _, kwargs, provider, _ = recalled
    before = snapshot(args)["provider_revision"]
    source = json.loads(args["registry_path"].read_text())
    source.update(profile_id="source_session_v1", session_bindings=[], session_receipts=[], lifetime_receipts=[])
    source["goals"][0].update(goal_instance_id="ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", status="active")
    args["registry_path"].unlink()
    with source_session_registry_transaction(args["registry_path"], operation="create_resume_fixture", create=lambda: source) as tx:
        tx.commit(tx.payload_copy())
    result = resume.run_room_resume(**kwargs, execute=True, provider=provider)
    assert not result["ok"] and provider.calls == 0 and result["private_context"] is None and not room.messages
    # The underlying current Todo facade is also profile-gated. A room adapter
    # must not bypass this owner or infer a Goal instance from its provider key.
    with pytest.raises(ValueError, match="source_session_v1"):
        claim_canonical_todo_if_promoted(registry_path=args["registry_path"], runtime_root=args["runtime_root"],
            goal_id=GOAL, todo_id=TODO, role="agent", claimed_by="agent-a", actor_agent_id="agent-a",
            dry_run=False, operation_id="exact-scope-denied", expected_provider_revision=before)
    assert snapshot(args)["provider_revision"] == before
