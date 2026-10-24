"""Independent original-audience and partial/unknown-send report recovery."""
import json

import pytest

from test_native_steward_private import steward as steward_fixture, finish
from test_native_steward_reports import configured, install_model
from loopx.capabilities.native_chat.steward_monitor import pump_steward_monitors
from loopx.capabilities.periodic_report.cadence_journal import read_cadence_journal
from loopx.capabilities.periodic_report.private_generation import attempt_directory
from loopx.capabilities.periodic_report.incremental import read_periodic_report_publication_cursor
from loopx.extensions.lark.steward_reports import LarkStewardReports

steward = steward_fixture


def generated(steward, tmp_path, monkeypatch, provider="sqlite"):
    _, runtime, _, _, binding, _, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch, provider)
    install_model(steward, tmp_path, sid)
    turns = pump_steward_monitors(runtime)
    assert len(turns) == 1
    done = finish(runtime, turns[0])
    assert done["status"] == "completed" and done["response"]["native_loopx"]["status"] == "committed", done
    window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
    return actor, reports, window, attempt_directory(reports.root, window), binding


@pytest.mark.parametrize("provider_kind", ["file", "sqlite"])
@pytest.mark.parametrize("phase", ["before_send", "after_partial"])
def test_added_authorized_goal_does_not_strand_an_existing_frozen_report(steward, tmp_path, monkeypatch, provider_kind, phase):
    from datetime import datetime, timedelta, timezone
    from loopx.bootstrap import bootstrap_project
    from loopx.capabilities.native_chat import steward_reports as report_module
    from loopx.capabilities.periodic_report import cadence_runtime

    _, runtime, provider, transport, _, capture, _ = steward
    point = datetime.now(timezone.utc).replace(hour=9, minute=1, second=0, microsecond=0) - timedelta(days=3)

    class CalendarClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)

    monkeypatch.setattr(report_module, "datetime", CalendarClock)
    monkeypatch.setattr(cadence_runtime, "datetime", CalendarClock)
    actor, reports, window, folder, binding = generated(steward, tmp_path, monkeypatch, provider_kind)
    frozen = (folder / "source.json").read_bytes(), (folder / "editorial-request.json").read_bytes()
    calls = capture.read_bytes()
    if phase == "after_partial":
        provider.verify_replies = False
        assert transport.steward_reports.reconcile() == 0
        assert provider.writes
        provider.verify_replies = True
    sent = list(provider.writes)
    project = tmp_path / "new-authorized-goal"
    project.mkdir()
    bootstrap_project(project=project, registry_path=runtime.registry_path,
        runtime_root=runtime.coordination_runtime_root, goal_id="new-authorized-goal",
        objective="Keep the independently authorized new work.", domain="test", role="primary",
        parent_goal_id=None, state_file=None, goal_doc=None, adapter_kind="generic_project_goal_v0",
        adapter_status="connected", next_probe=None, spawn_allowed=False, max_children=0,
        allowed_domains=[], write_scope=[], preserve_todos=True, force=False, dry_run=False, sync_global=False)
    assert reports.delivery_current(binding["binding_id"], window)
    assert transport.steward_reports.reconcile() == 1
    assert provider.writes[:len(sent)] == sent
    assert capture.read_bytes() == calls
    assert ((folder / "source.json").read_bytes(), (folder / "editorial-request.json").read_bytes()) == frozen
    assert read_periodic_report_publication_cursor(runtime_root=reports.root,
        goal_id=actor["goal_id"], agent_id=actor["agent_id"])["covered_until"] == window["due_at"]
    after = list(provider.writes)
    assert transport.steward_reports.reconcile() == 0 and provider.writes == after
    point += timedelta(days=1)
    runtime._steward_monitor_next_check.clear()
    following = pump_steward_monitors(runtime)
    assert len(following) == 1
    done = finish(runtime, following[0])
    assert done["status"] == "completed" and done["response"]["native_loopx"].get("status") == "committed", done
    assert transport.steward_reports.reconcile() == 1
    next_window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
    assert next_window["window_id"] != window["window_id"]
    next_source = json.loads((attempt_directory(reports.root, next_window) / "source.json").read_text())
    assert "new-authorized-goal" in next_source["goal_ids"]


@pytest.mark.parametrize("provider_kind", ["file", "sqlite"])
def test_replacing_a_frozen_goal_identity_still_blocks_report_delivery(steward, tmp_path, monkeypatch, provider_kind):
    _, runtime, provider, transport, _, capture, _ = steward
    actor, _, _, folder, _ = generated(steward, tmp_path, monkeypatch, provider_kind)
    before = capture.read_bytes(), list(provider.writes), (folder / "source.json").read_bytes()
    registry = json.loads(runtime.registry_path.read_text())
    goal = next(row for row in registry["goals"] if row["id"] == actor["goal_id"])
    goal["creation_operation_id"] += ".replacement"
    runtime.registry_path.write_text(json.dumps(registry))
    assert transport.steward_reports.reconcile() == 0
    assert before == (capture.read_bytes(), provider.writes, (folder / "source.json").read_bytes())


@pytest.mark.parametrize("fault", ["pause", "cancel", "revoked", "scope", "provider", "body", "source", "foreign_candidate"])
def test_late_report_never_escapes_changed_original_authority_or_frozen_evidence(steward, tmp_path, monkeypatch, fault):
    _, runtime, provider, transport, _, capture, _ = steward
    actor, reports, window, folder, binding = generated(steward, tmp_path, monkeypatch)
    if fault in {"pause", "cancel"}:
        preview = reports.configure(binding["binding_id"], operation=fault)["plan"]
        reports.configure(binding["binding_id"], operation=fault, execute=True, expected_plan_id=preview["plan_id"])
    elif fault == "revoked":
        transport.bindings.disconnect(binding["binding_id"], expected_revision=transport.bindings.read()["revision"])
    elif fault == "scope":
        transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="codex", context_kind="steward", goal_scope="selected")
        assert not reports.delivery_current(binding["binding_id"], window)
    elif fault == "provider":
        provider.profile_apps["steward-app"] = "cli_replaced_application"
    elif fault == "body":
        (folder / "report.md").write_text("篡改后的不完整结果")
    elif fault == "source":
        value = json.loads((folder / "source.json").read_text())
        value["rows"] = []
        (folder / "source.json").write_text(json.dumps(value))
    elif fault == "foreign_candidate":
        from loopx.capabilities.periodic_report.incremental import build_periodic_report_publication_candidate
        # A well-formed candidate for another Goal passes the generic cursor
        # schema. This original-report validator must reject it before sending.
        candidate = build_periodic_report_publication_candidate(goal_id="foreign-reporting-goal",
            agent_id=actor["agent_id"], generation_id=json.loads((folder / "private-generation.json").read_text())["generation_id"],
            trigger_receipt={"coalesced_trigger_ids": []}, facts=[], baseline=None)
        (folder / "publication-candidate.json").write_text(json.dumps(candidate))
    before = list(provider.writes), capture.read_bytes()
    assert transport.steward_reports.reconcile() == 0
    assert before == (provider.writes, capture.read_bytes())
    assert read_periodic_report_publication_cursor(runtime_root=reports.root,
        goal_id=actor["goal_id"], agent_id=actor["agent_id"]) is None


@pytest.mark.parametrize("provider_kind", ["file", "sqlite"])
def test_known_partial_reply_recovers_original_parts_after_provider_adapter_restart(steward, tmp_path, monkeypatch, provider_kind):
    _, _, provider, transport, _, capture, _ = steward
    actor, reports, window, folder, _ = generated(steward, tmp_path, monkeypatch, provider_kind)
    calls = capture.read_bytes()
    provider.verify_replies = False
    original_count = len(provider.writes)
    assert transport.steward_reports.reconcile() == 0
    assert len(provider.writes) > original_count
    sent = list(provider.writes)
    path = folder / "private-delivery.json"
    pending = json.loads(path.read_text())
    assert pending["delivery_status"] == "unconfirmed"
    assert read_periodic_report_publication_cursor(runtime_root=reports.root,
        goal_id=actor["goal_id"], agent_id=actor["agent_id"]) is None
    # Restoring visibility is a readback change, not authorization to replay
    # the model or already-created parts. Missing subsequent parts may proceed.
    provider.verify_replies = True
    restarted = LarkStewardReports(transport)
    assert restarted.reconcile() == 1
    assert provider.writes[:len(sent)] == sent
    assert len(provider.writes) > len(sent)  # Remaining full-body parts, rather than a truncated first part.
    writes = list(provider.writes)
    assert restarted.reconcile() == 0 and provider.writes == writes
    assert capture.read_bytes() == calls
    assert json.loads(path.read_text())["delivery_status"] == "delivered"


def test_unknown_send_without_locator_is_retained_without_second_create_or_model(steward, tmp_path, monkeypatch):
    _, _, provider, transport, _, capture, _ = steward
    actor, reports, _, folder, _ = generated(steward, tmp_path, monkeypatch)
    original = transport._reply_runner
    lost = False

    def lose_create_response(args):
        nonlocal lost
        result = original(args)
        if not lost and any(command in args for command in ["+messages-send", "+messages-reply"]) and "--dry-run" not in args:
            lost = True
            return {"returncode": 1, "stdout": "", "stderr": "synthetic unknown provider response"}
        return result

    monkeypatch.setattr(transport, "_reply_runner", lose_create_response)
    assert transport.steward_reports.reconcile() == 0 and lost
    sent, calls = list(provider.writes), capture.read_bytes()
    for _ in range(2):
        assert LarkStewardReports(transport).reconcile() == 0
    assert provider.writes == sent and capture.read_bytes() == calls
    assert json.loads((folder / "private-delivery.json").read_text())["delivery_status"] == "unconfirmed"
    assert read_periodic_report_publication_cursor(runtime_root=reports.root,
        goal_id=actor["goal_id"], agent_id=actor["agent_id"]) is None
    binding = reports.selected(actor["goal_id"].removeprefix("steward-agent-"))["binding"]
    plan = reports.configure(binding["binding_id"], operation="cancel")["plan"]
    assert not plan["retires_failed_window"]
    reports.configure(binding["binding_id"], operation="cancel", execute=True, expected_plan_id=plan["plan_id"])
    assert not read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"]).get("retirement")
    assert provider.writes == sent and capture.read_bytes() == calls


def test_original_production_wake_and_outbound_loops_complete_the_report_without_manual_submit(steward, tmp_path, monkeypatch):
    import threading
    import time
    from loopx.chat_loopx_mode import DelegationWakeService
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService

    _, runtime, _, transport, _, _, _ = steward
    sid, actor, reports, _ = configured(steward, tmp_path, monkeypatch)
    install_model(steward, tmp_path, sid)
    wake = DelegationWakeService(runtime, interval=.02).start()
    outbound = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=reports.root,
        runtime_controller=runtime, private_conversations=transport)
    # Run the existing outbound producer, without starting any event listener.
    delivery = threading.Thread(target=outbound._reconcile_private)
    delivery.start()
    try:
        deadline = time.monotonic() + 35
        while read_periodic_report_publication_cursor(runtime_root=reports.root,
                goal_id=actor["goal_id"], agent_id=actor["agent_id"]) is None:
            assert time.monotonic() < deadline, "production wake/outbound hooks never completed the due report"
            time.sleep(.05)
        window = read_cadence_journal(runtime_root=reports.root, goal_id=actor["goal_id"])["window"]
        source = json.loads((attempt_directory(reports.root, window) / "source.json").read_text())
        assert source["coverage"]["goals_in_inventory"] == 1
        assert reports.health(transport.bindings.read()["bindings"][-1]["binding_id"])["available"]
    finally:
        wake.close()
        outbound.close()
        delivery.join(5)
    assert not delivery.is_alive()


def test_full_host_restart_recovers_frozen_delivery_without_a_second_native_model(steward, tmp_path, monkeypatch):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.extensions.lark.private_conversations import LarkPrivateConversations

    store, runtime, provider, transport, _, capture, _ = steward
    actor, reports, _, folder, _ = generated(steward, tmp_path, monkeypatch)
    provider.verify_replies = False
    assert transport.steward_reports.reconcile() == 0
    sent, calls = list(provider.writes), capture.read_bytes()
    runtime.close()
    restored = ChatRuntimeController(store=store, codex_bin=runtime.codex_bin,
        project_contexts=runtime.project_contexts, registry_path=runtime.registry_path,
        manager_scope_resolver=runtime.manager_scope_resolver)
    try:
        replies = LarkPrivateConversations(controller=restored, runtime_root=reports.root,
            runner=provider, cli_bin="lark-cli")
        provider.verify_replies = True
        assert replies.steward_reports.reconcile() == 1
        assert provider.writes[:len(sent)] == sent and capture.read_bytes() == calls
        assert json.loads((folder / "private-delivery.json").read_text())["delivery_status"] == "delivered"
        assert not pump_steward_monitors(restored) and capture.read_bytes() == calls
        assert read_periodic_report_publication_cursor(runtime_root=reports.root,
            goal_id=actor["goal_id"], agent_id=actor["agent_id"])
    finally:
        restored.close()


def test_schedule_change_cannot_strand_a_frozen_original_report(steward, tmp_path, monkeypatch):
    from loopx.capabilities.native_chat.steward_reports import PendingStewardReportError
    _, runtime, _, transport, _, capture, _ = steward
    _, reports, _, _, binding = generated(steward, tmp_path, monkeypatch)
    schedule = reports.health(binding["binding_id"])["schedule"]
    changed = {**schedule, "start_date": "2099-05-01"}
    before = runtime.registry_path.read_bytes(), capture.read_bytes()
    with pytest.raises(PendingStewardReportError):
        reports.configure(binding["binding_id"], operation="enable", schedule=changed)
    assert before == (runtime.registry_path.read_bytes(), capture.read_bytes())
    pause = reports.configure(binding["binding_id"], operation="pause")["plan"]
    reports.configure(binding["binding_id"], operation="pause", execute=True, expected_plan_id=pause["plan_id"])
    resume = reports.configure(binding["binding_id"], operation="enable", schedule=schedule)["plan"]
    reports.configure(binding["binding_id"], operation="enable", schedule=schedule, execute=True, expected_plan_id=resume["plan_id"])
    assert transport.steward_reports.reconcile() == 1
    assert reports.health(binding["binding_id"])["publication_verified"]
    preview = reports.configure(binding["binding_id"], operation="enable", schedule=changed)["plan"]
    result = reports.configure(binding["binding_id"], operation="enable", schedule=changed,
        execute=True, expected_plan_id=preview["plan_id"])
    assert result["applied"] and result["report"]["schedule"] == changed
