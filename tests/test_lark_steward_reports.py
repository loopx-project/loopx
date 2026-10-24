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


@pytest.mark.parametrize("provider_kind", ["file", "sqlite"])
@pytest.mark.parametrize("legacy", [False, True], ids=["new-report", "retained-attempt"])
def test_periodic_result_has_its_own_private_message_without_retargeting_history(
    steward, tmp_path, monkeypatch, provider_kind, legacy,
):
    _, _, provider, transport, _, capture, _ = steward

    def threaded_original(fixture):
        from loopx.extensions.lark.conversation_identity import identity_ref
        _, runtime, source, adapter, binding, _, _ = fixture
        parent = source.event("steward-app", "earlier-question", "An unrelated earlier question")
        event = source.event("steward-app", "original", "Retain the original private conversation")
        thread = dict(root_id=parent["message_id"], parent_id=parent["message_id"], thread_id="omt_earlier")
        event.update(thread)
        source.messages[event["message_id"]].update(thread)
        adapter.admit("steward-app", event)
        # Referenced-source context changes the admitted text, not its identity.
        row = adapter.core.read_request(identity_ref(binding["provider_ref"], event["message_id"]))
        assert finish(runtime, row)["status"] == "completed"
        return row["session_id"], adapter.managed_steward

    monkeypatch.setattr("test_native_steward_reports.original", threaded_original)
    actor, reports, window, folder, _ = generated(steward, tmp_path, monkeypatch, provider_kind)
    calls = capture.read_bytes()
    frozen = tuple((folder / name).read_bytes() for name in ["report.md", "editorial-request.json", "source.json"])
    path = folder / "private-delivery.json"
    if legacy:
        # Seed the previous persisted representation before any provider write.
        # Its absent placement keeps the original source-context intent on upgrade.
        with monkeypatch.context() as pending:
            pending.setattr(transport, "_deliver", lambda *_a, **_kw: False)
            assert transport.steward_reports.reconcile() == 0
        record = json.loads(path.read_text())
        record.pop("reply_placement", None)
        path.write_text(json.dumps(record))
    before = len(provider.writes), len(provider.topic_writes)
    provider.verify_replies = False
    assert transport.steward_reports.reconcile() == 0
    assert len(provider.writes) == before[0] + 1
    first = list(provider.writes)
    provider.verify_replies = True
    assert LarkStewardReports(transport).reconcile() == 1
    assert provider.writes[:len(first)] == first
    assert len(provider.writes) > len(first)  # Full report resumes its unsent rich parts.
    sent = provider.writes[before[0]:]
    assert bool(provider.topic_writes[before[1]:]) is legacy
    for index, (_, body) in enumerate(sent, before[0]):
        message = provider.messages[f"om_out_{index}"]
        assert message["chat_id"] == "oc_steward_app"
        assert bool(message.get("parent_id")) is legacy
        assert body in json.loads(message["body"]["content"])["zh_cn"]["content"][0][0]["text"]
    writes = list(provider.writes)
    assert LarkStewardReports(transport).reconcile() == 0 and provider.writes == writes
    assert capture.read_bytes() == calls
    assert frozen == tuple((folder / name).read_bytes() for name in ["report.md", "editorial-request.json", "source.json"])
    assert read_periodic_report_publication_cursor(runtime_root=reports.root,
        goal_id=actor["goal_id"], agent_id=actor["agent_id"])["covered_until"] == window["due_at"]


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


@pytest.mark.parametrize("fault", [None, "missing-chat", "foreign-chat", "thread", "group"])
def test_private_result_verifies_placement_and_keeps_the_source_authority(steward, tmp_path, fault):
    from tests.extensions.test_lark_inbox_reactions import _fixture
    from loopx.extensions.lark.inbox_reply import reply_lark_event_inbox, verify_lark_inbox_reply

    _, _, provider, transport, _, _, _ = steward
    config, inbox, project = _fixture(tmp_path / "private-result", lifecycle=False)
    settings = json.loads(config.read_text())
    settings["reply"].update(sender_profile="steward-app", bot_display_name="steward-app", chat_id="oc_steward_app")
    config.write_text(json.dumps(settings))
    source = inbox / "om_reaction_fixture.json"
    event = json.loads(source.read_text())
    event.update(chat_type="group" if fault == "group" else "p2p",
        parent_id="om_earlier_question", root_id="om_earlier_question")
    source.write_text(json.dumps(event))
    attempts = []

    def runner(args):
        result = provider(args)
        if "+messages-send" in args and "--dry-run" not in args:
            ref = json.loads(result["stdout"])["data"]["message_id"]
            message = provider.messages[ref]
            if fault == "missing-chat":
                message.pop("chat_id")
            elif fault == "foreign-chat":
                message["chat_id"] = "oc_another_audience"
            elif fault == "thread":
                message.update(parent_id="om_earlier_question", root_id="om_earlier_question")
        return result

    args = dict(project=project, config_path=config, message_id=event["message_id"],
        text="# Complete result\n\n" + "完整正文与来源边界。" * 300,
        reply_placement="chat_root", runner=runner, finalize_reactions=False,
        source_authority_verifier=lambda *_: ("steward-app", True))
    if fault == "group":
        with pytest.raises(ValueError, match="captured private source"):
            transport._deliver(tmp_path / "group-result.json", {"event": event, "source": {}},
                "periodic_report.fixture", args["text"], reply_placement="chat_root",
                inbox=lambda: pytest.fail("group override reached delivery"))
        assert not provider.writes
        return
    sent = reply_lark_event_inbox(**args, content_format="markdown", execute=True,
        delivery_attempt_recorder=lambda value: attempts.append(dict(value)))
    assert len(provider.writes) == 1 and provider.writes[0][1] == args["text"]
    assert sent["reply_verified"] is (fault is None)
    assert len(attempts) == 1
    retained = list(provider.writes)
    assert verify_lark_inbox_reply(**args, attempt=attempts[0])["reply_verified"] is (fault is None)
    # Visibility correction only: the original locator is read, never resent.
    provider.messages[attempts[0]["message_ref"]].update(chat_id="oc_steward_app", parent_id=None, root_id=None)
    assert verify_lark_inbox_reply(**args, attempt=attempts[0])["reply_verified"]
    assert provider.writes == retained


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
