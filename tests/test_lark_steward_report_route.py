"""Report routes retain the prepared actor across private Chat session changes."""
import json
from types import SimpleNamespace

import pytest

from test_native_steward_managed_mode import original, storage
from test_native_steward_private import steward as steward_fixture, finish
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from loopx.capabilities.native_chat.steward_reports import StewardReports
from loopx.extensions.lark.conversation_identity import identity_ref

steward = steward_fixture


@pytest.fixture(params=["file", "sqlite"])
def rotated_steward(steward, tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, provider, transport, binding, capture, _ = steward
    # This real receipt sorts before both the /new command and current message.
    # Selection must use the actor's identity, never receipt-name chronology.
    old_event = provider.event("steward-app", "old-1", "An earlier independent conversation.")
    old_ref = identity_ref(binding["provider_ref"], old_event["message_id"])
    assert transport.admit("steward-app", old_event)["status"] == "durably_accepted"
    old = transport.core.read_request(old_ref)
    assert finish(runtime, old)["status"] == "completed"
    command = provider.event("steward-app", "rotate-review", "/new")
    transport.admit("steward-app", command)
    reset = transport.core.read_request(identity_ref(binding["provider_ref"], command["message_id"]))
    assert reset["status"] == "command_completed" and reset["response_code"] == "new_session"
    sid, managed = original(steward)
    assert sid != old["session_id"]
    current_ref = identity_ref(binding["provider_ref"], "om_original")
    assert old_ref > max(current_ref, reset["request_ref"])
    storage(runtime, request.param)
    assert not managed.health(binding["binding_id"])["prepared"]
    preview = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid, execute=True,
        expected_plan_id=preview["plan_id"])["actor"]
    binding = next(row for row in transport.bindings.read()["bindings"]
        if row["binding_id"] == binding["binding_id"])
    schedule = {"schema_version": "periodic_report_schedule_v0", "schedule_id": "daily-report",
        "timezone": "UTC", "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "start_date": "2999-01-01"}
    return SimpleNamespace(store=store, runtime=runtime, provider=provider, transport=transport,
        binding=binding, actor=actor, capture=capture, reports=StewardReports(runtime),
        old=old, current_ref=current_ref, schedule=schedule)


def test_report_preview_and_apply_find_original_actor_after_new_session(rotated_steward):
    o = rotated_steward
    sid = o.actor["session_id"]
    before = o.runtime.registry_path.read_bytes(), o.store.messages(sid), o.capture.read_bytes(), list(o.provider.writes)
    preview = o.reports.configure(o.binding["binding_id"], operation="enable", schedule=o.schedule)
    assert not preview["applied"]
    assert before == (o.runtime.registry_path.read_bytes(), o.store.messages(sid),
        o.capture.read_bytes(), list(o.provider.writes))
    applied = o.reports.configure(o.binding["binding_id"], operation="enable", schedule=o.schedule,
        execute=True, expected_plan_id=preview["plan"]["plan_id"])
    assert applied["applied"] and applied["report"]["enabled"]
    assert not applied["model_invoked"] and not applied["external_writes_performed"]
    assert o.reports.subscription(o.binding["binding_id"])["route_ref"] == o.reports.route_ref(o.binding, o.actor)
    route = o.transport.steward_reports._original_route(o.binding, o.actor)
    assert route["request_ref"] == o.current_ref and route["source"]["source_ref"] == o.actor["source_ref"]
    assert o.transport.core.read_request(route["request_ref"])["session_id"] == sid
    # The general owner-notification caller keeps its original route behavior.
    assert o.transport.owner_attention._route(o.binding)["request_ref"] == o.old["request_ref"]
    assert before[1:] == (o.store.messages(sid), o.capture.read_bytes(), list(o.provider.writes))


@pytest.mark.parametrize("field,value", [("session_id", "0" * 32), ("source_ref", "0" * 24)])
def test_report_route_never_falls_back_to_another_actor_or_source(rotated_steward, field, value):
    o = rotated_steward
    before = o.runtime.registry_path.read_bytes(), o.capture.read_bytes(), list(o.provider.writes)
    with pytest.raises(ValueError):
        o.transport.steward_reports.route_proof(o.binding, {**o.actor, field: value})
    assert before == (o.runtime.registry_path.read_bytes(), o.capture.read_bytes(), list(o.provider.writes))


@pytest.mark.parametrize("fault", ["wrong_app", "wrong_owner", "source_changed", "source_missing"])
def test_matching_actor_still_requires_current_provider_and_source(rotated_steward, fault):
    o = rotated_steward
    if fault == "wrong_app":
        o.provider.profile_apps["steward-app"] = "cli_foreign"
    elif fault == "wrong_owner":
        o.provider.owner_ready = False
    elif fault == "source_changed":
        o.provider.messages["om_original"]["chat_id"] = "oc_foreign"
    else:
        (o.transport.root / f"{o.current_ref}.json").unlink()
    before = o.runtime.registry_path.read_bytes(), o.capture.read_bytes(), list(o.provider.writes)
    with pytest.raises(ValueError):
        o.reports.configure(o.binding["binding_id"], operation="enable", schedule=o.schedule)
    assert before == (o.runtime.registry_path.read_bytes(), o.capture.read_bytes(), list(o.provider.writes))


def test_report_route_uses_canonical_session_not_transport_copy(rotated_steward):
    o = rotated_steward
    path = o.transport.root / f"{o.current_ref}.json"
    record = json.loads(path.read_text())
    record["session_id"] = o.old["session_id"]
    path.write_text(json.dumps(record))
    route = o.transport.steward_reports._original_route(o.binding, o.actor)
    assert route["request_ref"] == o.current_ref
    assert o.transport.core.read_request(route["request_ref"])["session_id"] == o.actor["session_id"]


def test_retained_report_route_cannot_fall_back_after_session_mismatch(rotated_steward):
    o = rotated_steward
    route = o.transport.steward_reports._original_route(o.binding, o.actor)
    assert o.transport.steward_reports._original_route(o.binding, o.actor, route) == route
    old_route = o.transport.owner_attention._route(o.binding)
    before = o.capture.read_bytes(), list(o.provider.writes)
    # A valid current route exists, but it cannot replace a frozen old route.
    with pytest.raises(ValueError):
        o.transport.steward_reports._original_route(o.binding, o.actor, old_route)
    assert before == (o.capture.read_bytes(), list(o.provider.writes))
