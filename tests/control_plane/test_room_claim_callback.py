"""Synthetic authenticated Lark ingress, actual canonical File/SQLite work."""
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from test_room_work import setup as room_setup, snapshot, GOAL, TODO, ROOT  # noqa: F401
from loopx.extensions.lark import room_claim as ingress
from loopx.extensions.lark.goal_channel_contracts import write_goal_channel_binding
from loopx.control_plane.projects.registry_codec import project_registry_transaction


def _version_mismatch(target_message, incoming_card):
    """Lark rejects updating an existing card with a different card schema
    (error 200830): the update must match the original card's version."""
    current = json.loads(target_message["body"]["content"])
    return current.get("schema") != incoming_card.get("schema")


@pytest.fixture
def offered(room_setup, monkeypatch):  # noqa: F811 - pytest fixture dependency
    args, room, binding = room_setup
    source = json.loads(args["registry_path"].read_text())
    source["registry_role"] = "global-local"
    source["goals"][0]["source_registry"] = str(args["registry_path"])
    (args["runtime_root"] / "registry.global.json").write_text(json.dumps(source))
    monkeypatch.setattr(ingress, "resolve_extension_activation", lambda *a, **k: {"enabled": True})
    transport_state = {"fail_update": False, "readable": True, "member": True, "delay_chat_get_until": None,
        "fail_update_message_ids": (), "seen_update_cards": []}

    def runner(argv, cwd, timeout):
        payload = None
        if "auth" in argv:
            return room(argv, cwd, timeout)
        if "chats" in argv and "get" in argv:
            delayed_until = transport_state["delay_chat_get_until"]
            if delayed_until is not None:
                time.sleep(max(0.0, (delayed_until - datetime.now(timezone.utc)).total_seconds()))
            requested_chat = argv[argv.index("--chat-id") + 1] if "--chat-id" in argv else "oc_room_fixture"
            payload = {"data": {"tenant_key": "room-fixture-tenant", "chat_id": requested_chat}}
        elif "+chat-members-list" in argv and "--member-types" in argv and argv[argv.index("--member-types") + 1] == "user":
            payload = {"data": {"items": [{"open_id": "ou_room_owner", "tenant_key": "room-fixture-tenant"}]
                if transport_state["member"] else []}}
        elif "api" in argv and "GET" in argv:
            payload = {"data": {"items": room.messages if transport_state["readable"] else []}}
        elif "api" in argv and "POST" in argv:
            data = json.loads(argv[argv.index("--data") + 1])
            mid = data["token"].removeprefix("callback:")
            if transport_state["fail_update"] or mid in (transport_state.get("fail_update_message_ids") or ()):
                return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True}
            target = next(m for m in room.messages if m["message_id"] == mid)
            if _version_mismatch(target, data["card"]):
                return {"returncode": 1, "stdout": json.dumps({"code": 200830,
                    "msg": "card update schema must match the original card"}),
                    "stderr": "", "returncode_payload": True}
            transport_state["seen_update_cards"].append(data["card"])
            target["body"]["content"] = json.dumps(data["card"])
            payload = {}
        elif "messages" in argv and "patch" in argv:
            mid = argv[argv.index("--message-id") + 1]
            if mid in (transport_state.get("fail_update_message_ids") or ()):
                return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True}
            data = json.loads(argv[argv.index("--data") + 1])
            target = next(m for m in room.messages if m["message_id"] == mid)
            card = json.loads(data["content"])
            if _version_mismatch(target, card):
                return {"returncode": 1, "stdout": json.dumps({"code": 200830,
                    "msg": "card update schema must match the original card"}),
                    "stderr": "", "returncode_payload": True}
            transport_state["seen_update_cards"].append(card)
            target["body"]["content"] = data["content"]
            payload = {}
        if payload is None:
            result = room(argv, cwd, timeout)
            payload = json.loads(result["stdout"])
        payload["ok"] = True
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    offer_args = dict(registry_path=args["registry_path"], authority_root=args["runtime_root"],
        broker_root=args["runtime_root"], binding_path=args["binding_path"], target_path=args["target_path"],
        goal_id=GOAL, runner=runner)
    revision = snapshot(args)["provider_revision"]

    def offer(actor="agent-a", key="room-offer-fixture", execute=True, expires_at="2030-01-01T00:00:00Z"):
        return ingress.run_room_claim_offer(**offer_args, actor_id=actor, command="offer", execute=execute,
            todo_id=TODO, expected_revision=revision, idempotency_key=key,
            principals=["lark:ou_room_owner"], expires_at=expires_at)

    def event(result):
        mid = next(m["message_id"] for m in room.messages
            if json.loads(m["body"]["content"])["body"]["elements"][1]["behaviors"][0]["value"]["request_id"] == result["request_id"])
        card = json.loads(next(m for m in room.messages if m["message_id"] == mid)["body"]["content"])
        return {"type": "card.action.trigger", "action_tag": "button", "host": "im_message",
            "operator_id": "ou_room_owner", "message_id": mid, "chat_id": "oc_room_fixture",
            "token": "callback:" + mid, "event_id": "room-fixture-event",
            "action_value": card["body"]["elements"][1]["behaviors"][0]["value"]}

    callback_args = dict(runtime_root=args["runtime_root"], profile_app_id="cli_room_fixture",
        cli_bin="fixture-cli", profile="room-fixture", runner=runner)
    return args, room, binding, offer_args, offer, event, callback_args, transport_state


def test_offer_preview_and_cli_do_not_claim_or_publish(offered):
    args, room, _, _, offer, _, _, _ = offered
    before = snapshot(args)["provider_revision"]
    preview = offer(execute=False)
    assert preview["ok"] and not preview["canonical_claim_accepted"], preview
    assert not room.messages and not (args["runtime_root"] / "room-claim-offers").exists()
    assert snapshot(args)["provider_revision"] == before
    proc = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
        "--format", "json", "goal-channel", "work", "offer", "--goal-id", GOAL, "--agent-id", "agent-a",
        "--todo-id", TODO, "--expected-revision", before, "--idempotency-key", "cli-offer-fixture",
        "--principal", "lark:ou_room_owner", "--expires-at", "2030-01-01T00:00:00Z"],
        cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["canonical_claim_accepted"] is False
    assert "PRIVATE_" not in proc.stdout and snapshot(args)["provider_revision"] == before


def test_authenticated_callback_and_duplicate_return_one_canonical_claim(offered):
    args, room, _, _, offer, event, callback_args, _ = offered
    before = snapshot(args)["provider_revision"]
    prepared = offer()
    assert prepared["ok"] and prepared["readback_verified"], json.dumps(prepared)
    assert snapshot(args)["provider_revision"] == before
    callback = event(prepared)
    accepted = ingress.handle_room_claim_callback(callback, **callback_args)
    assert accepted["ok"] and accepted["canonical_claim_accepted"], accepted
    assert accepted["execution_authority_granted"] is False
    assert snapshot(args)["todos"][0]["claimed_by"] == "agent-a"
    committed = snapshot(args)["provider_revision"]
    replay = ingress.handle_room_claim_callback(callback, **callback_args)
    assert replay["ok"] and replay["claim_status"] == "already_applied", replay
    assert snapshot(args)["provider_revision"] == committed
    assert "PRIVATE_" not in json.dumps(room.messages) and len(room.messages) == 1


def test_two_actor_offers_compete_at_one_revision(offered):
    args, _, _, _, offer, event, callback_args, _ = offered
    cards = [offer(actor, "compete-" + actor) for actor in ["agent-a", "agent-b"]]
    assert all(card["ok"] for card in cards), cards
    callbacks = [event(card) for card in cards]
    with ThreadPoolExecutor(2) as pool:
        receipts = list(pool.map(lambda e: ingress.handle_room_claim_callback(e, **callback_args), callbacks))
    assert sum(r["canonical_claim_accepted"] for r in receipts) == 1, receipts
    assert sorted(r["claim_status"] for r in receipts) == ["applied", "conflict"]
    assert snapshot(args)["todos"][0]["claimed_by"] in {"agent-a", "agent-b"}


def test_final_native_expiry_refusal_preserves_offer_message_and_authority(offered, monkeypatch):
    from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityRejection
    from loopx.extensions.lark import goal_channel_work

    args, room, _, _, offer, event, callback_args, _ = offered
    prepared = offer()
    assert prepared["ok"], prepared
    path = args["runtime_root"] / "room-claim-offers" / (prepared["request_id"] + ".json")
    original_offer = path.read_bytes()
    deadline = json.loads(original_offer)["request"]["expires_at"]
    before, messages = snapshot(args), json.dumps(room.messages)
    observed = []

    def expired_at_native_admission(**kwargs):
        observed.append(kwargs["admit_before"])
        raise LocalCoordinationAuthorityRejection("Todo claim admission deadline elapsed",
            code="claim_expired", payload={"failure_kind": "decision_rejection"})

    monkeypatch.setattr(goal_channel_work, "claim_canonical_todo_if_promoted", expired_at_native_admission)
    with pytest.raises(ingress.RoomClaimCallbackError) as rejected:
        ingress.handle_room_claim_callback(event(prepared), **callback_args)
    assert rejected.value.code == "offer_expired"
    assert observed == [deadline]
    assert snapshot(args) == before
    assert json.dumps(room.messages) == messages
    assert path.read_bytes() == original_offer


def _operation_receipt(args, operation_id):
    from loopx.control_plane.effect_runtime import effect_runtime_result

    return effect_runtime_result("coordination.local_authority.operation_receipt", {
        "schema_version": "loopx_local_coordination_operation_receipt_request_v0",
        "runtime_root": str(args["runtime_root"]), "goal_id": GOAL, "operation_id": operation_id,
    })


@pytest.mark.parametrize("delay_stage", ["card_read", "delivery_preflight"])
def test_offer_expired_during_provider_reads_cannot_claim(offered, monkeypatch, delay_stage):
    """Real-clock deadline crossing: an offer that was valid at callback entry
    must not produce a canonical claim, receipt or room effect when provider
    reads push the attempt past its declared expiry."""
    args, room, _, _, offer, event, callback_args, transport = offered
    deadline = datetime.now(timezone.utc) + timedelta(seconds=1.2)
    prepared = offer(expires_at=deadline.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert prepared["ok"], prepared
    path = args["runtime_root"] / "room-claim-offers" / (prepared["request_id"] + ".json")
    original_offer = path.read_bytes()
    before, messages = snapshot(args), json.dumps(room.messages)
    receipt_before = _operation_receipt(args, json.loads(original_offer)["request"]["idempotency_key"])
    assert receipt_before["status"] == "missing", receipt_before
    cross_after = deadline + timedelta(seconds=0.3)

    if delay_stage == "card_read":
        real_read = ingress.read_callback_card_content

        def slow_read(**kwargs):
            time.sleep(max(0.0, (cross_after - datetime.now(timezone.utc)).total_seconds()))
            return real_read(**kwargs)

        monkeypatch.setattr(ingress, "read_callback_card_content", slow_read)
    else:
        # The delivery preflight inside the claim facade re-verifies the route
        # through the same provider; a slow chat read here also crosses the
        # deadline after callback admission already passed.
        transport["delay_chat_get_until"] = cross_after
    with pytest.raises(ingress.RoomClaimCallbackError) as rejected:
        ingress.handle_room_claim_callback(event(prepared), **callback_args)
    assert rejected.value.code == "offer_expired"
    # Refusal preserves the full canonical state, revision, operation receipt
    # and room messages; only the wall clock moved.
    assert snapshot(args) == before
    assert json.dumps(room.messages) == messages
    assert path.read_bytes() == original_offer
    assert _operation_receipt(args, json.loads(original_offer)["request"]["idempotency_key"]) == receipt_before


def test_unexpired_offer_still_claims_after_the_same_reads(offered):
    """Control for the real-clock crossing above: without an expiry crossing,
    the same callback path still yields exactly one canonical acceptance."""
    args, room, _, _, offer, event, callback_args, _ = offered
    prepared = offer()  # fixture default expiry stays in the future
    before = snapshot(args)
    result = ingress.handle_room_claim_callback(event(prepared), **callback_args)
    assert result["canonical_claim_accepted"] is True, result
    after = snapshot(args)
    assert after["provider_revision"] != before["provider_revision"]
    assert after["todos"][0]["claimed_by"] == "agent-a"
    receipt = _operation_receipt(args, json.loads(
        (args["runtime_root"] / "room-claim-offers" / (prepared["request_id"] + ".json")).read_bytes()
    )["request"]["idempotency_key"])
    assert receipt["status"] == "found", receipt


@pytest.mark.parametrize("rejection", ["principal", "grant", "actor", "binding", "message", "app", "card", "expiry", "membership", "route"])
def test_scope_and_stale_callback_rejections_preserve_canonical_state(offered, monkeypatch, rejection):
    args, room, binding, offer_args, offer, event, callback_args, transport = offered
    prepared = offer()
    assert prepared["ok"], prepared
    callback = event(prepared)
    before = snapshot(args)["provider_revision"]
    if rejection == "principal":
        callback["operator_id"] = "ou_unapproved_room_member"
    elif rejection == "grant":
        revoked = ingress.run_room_claim_offer(**offer_args, actor_id="agent-a", command="revoke", execute=True,
            request_id=prepared["request_id"])
        assert revoked["ok"]
    elif rejection == "actor":
        r = json.loads(args["registry_path"].read_text())
        r["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
        args["registry_path"].write_text(json.dumps(r))
    elif rejection == "binding":
        binding["bindings"][GOAL]["connections"]["agent-a"]["enabled"] = False
        write_goal_channel_binding(args["binding_path"], binding)
    elif rejection == "message":
        callback["message_id"] = "om_another_card"
    elif rejection == "app":
        callback_args["profile_app_id"] = "cli_another_app"
    elif rejection == "card":
        card = json.loads(room.messages[0]["body"]["content"])
        card["body"]["elements"][1]["behaviors"][0]["value"]["request_id"] = "rc_" + "0" * 32
        room.messages[0]["body"]["content"] = json.dumps(card)
    elif rejection == "expiry":
        monkeypatch.setattr(ingress, "_now", lambda: "2030-01-01T00:00:00Z")
    elif rejection == "membership":
        transport["member"] = False
    elif rejection == "route":
        r = json.loads((args["runtime_root"] / "registry.global.json").read_text())
        r["goals"][0]["source_registry"] = str(args["registry_path"].parent / "rebound.json")
        (args["runtime_root"] / "registry.global.json").write_text(json.dumps(r))
    with pytest.raises(ingress.RoomClaimCallbackError):
        ingress.handle_room_claim_callback(callback, **callback_args)
    assert snapshot(args)["provider_revision"] == before
    assert not snapshot(args)["todos"][0].get("claimed_by")
    assert "PRIVATE_" not in json.dumps(room.messages)


@pytest.mark.parametrize("read_stage", ["operator_membership_verified", "read_callback_card_content"])
def test_callback_retargeted_during_provider_read_cannot_claim(offered, monkeypatch, read_stage):
    args, room, binding, _, offer, event, callback_args, _ = offered
    operation = "retarget-during-provider-read"
    prepared = offer(key=operation)
    assert prepared["ok"], prepared
    callback = event(prepared)
    before = snapshot(args)
    messages = json.dumps(room.messages)
    def receipt():
        return ingress.effect_runtime_result("coordination.local_authority.operation_receipt", {
            "schema_version": "loopx_local_coordination_operation_receipt_request_v0",
            "runtime_root": str(args["runtime_root"]), "goal_id": GOAL, "operation_id": operation,
        })

    original_receipt = receipt()
    assert original_receipt["status"] == "missing", original_receipt
    original_read = getattr(ingress, read_stage)

    def retarget_after_read(**kwargs):
        observed = original_read(**kwargs)
        binding["bindings"][GOAL]["connections"]["agent-a"]["channel"]["chat_id"] = "oc_retargeted_fixture"
        write_goal_channel_binding(args["binding_path"], binding)
        return observed

    monkeypatch.setattr(ingress, read_stage, retarget_after_read)
    with pytest.raises(ingress.RoomClaimCallbackError, match="Room claim callback") as rejected:
        ingress.handle_room_claim_callback(callback, **callback_args)
    assert rejected.value.code == "binding_changed"
    assert snapshot(args) == before
    assert not snapshot(args)["todos"][0].get("claimed_by")
    assert receipt() == original_receipt
    assert json.dumps(room.messages) == messages


@pytest.mark.parametrize("read_stage", ["operator_membership_verified", "read_callback_card_content", "claim_effect"])
def test_callback_source_route_change_cannot_claim_old_source(offered, monkeypatch, read_stage):
    args, room, _, _, offer, event, callback_args, _ = offered
    operation = "source-route-during-callback"
    prepared = offer(key=operation)
    assert prepared["ok"], prepared
    callback = event(prepared)
    before = snapshot(args)
    messages = json.dumps(room.messages)
    broker = args["runtime_root"] / "registry.global.json"
    replacement = args["registry_path"].with_name("another-source.json")
    replacement.write_bytes(args["registry_path"].read_bytes())

    def receipt():
        return ingress.effect_runtime_result("coordination.local_authority.operation_receipt", {
            "schema_version": "loopx_local_coordination_operation_receipt_request_v0",
            "runtime_root": str(args["runtime_root"]), "goal_id": GOAL, "operation_id": operation,
        })

    original_receipt = receipt()
    assert original_receipt["status"] == "missing", original_receipt

    def retarget():
        with project_registry_transaction(broker, operation="fixture_source_retarget") as transaction:
            current = transaction.payload_copy()
            current["goals"][0]["source_registry"] = str(replacement)
            transaction.commit(current)

    if read_stage == "claim_effect":
        from loopx.extensions.lark import goal_channel_work
        original = goal_channel_work.claim_canonical_todo_if_promoted

        def change_before_native_claim(**kwargs):
            retarget()
            return original(**kwargs)

        monkeypatch.setattr(goal_channel_work, "claim_canonical_todo_if_promoted", change_before_native_claim)
    else:
        original = getattr(ingress, read_stage)

        def change_after_read(**kwargs):
            result = original(**kwargs)
            retarget()
            return result

        monkeypatch.setattr(ingress, read_stage, change_after_read)
    with pytest.raises(ingress.RoomClaimCallbackError) as rejected:
        ingress.handle_room_claim_callback(callback, **callback_args)
    assert rejected.value.code == "source_route_changed"
    assert snapshot(args) == before
    assert not snapshot(args)["todos"][0].get("claimed_by")
    assert receipt() == original_receipt
    assert json.dumps(room.messages) == messages


def test_restart_recovers_only_result_transport_without_a_new_transition(offered):
    args, _, _, _, offer, event, callback_args, transport = offered
    prepared = offer()
    assert prepared["ok"], prepared
    transport["fail_update"] = True
    pending = ingress.handle_room_claim_callback(event(prepared), **callback_args)
    assert pending["canonical_claim_accepted"] and not pending["ok"], pending
    committed = snapshot(args)["provider_revision"]
    transport["fail_update"] = False
    recovery = ingress.recover_room_claim_results(**callback_args, allowed_chat_ids={"oc_room_fixture"})
    assert recovery == {"attempted": 1, "delivered": 1, "failed": 0}
    assert snapshot(args)["provider_revision"] == committed
    assert ingress.recover_room_claim_results(**callback_args, allowed_chat_ids={"oc_room_fixture"})["attempted"] == 0


@pytest.mark.parametrize("projection_read", ["ok", "fail_after_commit"])
def test_committed_receipt_stays_on_the_original_card(offered, monkeypatch, projection_read):
    """Four contrasts in one room: the original callback, one successful
    commit, a live projection read failure after that commit, and process
    recovery. Once the canonical claim is committed, the originating message
    keeps the same receipt id and the canonical revision does not advance
    again, whether or not the live projection read succeeds."""
    from loopx.extensions.lark import goal_channel_work as work_module

    args, room, _, _, offer, event, callback_args, _ = offered
    prepared = offer()
    before = snapshot(args)["provider_revision"]
    if projection_read == "fail_after_commit":
        def unavailable(**_kwargs):
            raise RuntimeError("projection read failed after the committed claim")
        monkeypatch.setattr(work_module, "read_canonical_todos_if_promoted", unavailable)
    result = ingress.handle_room_claim_callback(event(prepared), **callback_args)
    assert result["ok"] is True and result["canonical_claim_accepted"] is True, result
    assert result["card_update_verified"] is True
    receipt_id = result["receipt"]["receipt_id"]
    committed = snapshot(args)["provider_revision"]
    assert committed != before
    # The originating offer message carries the committed receipt number.
    delivery = json.loads((args["runtime_root"] / "room-claim-offers"
        / (prepared["request_id"] + ".json")).read_bytes())["delivery"]
    original = next(m for m in room.messages if m["message_id"] == delivery["message_id"])
    assert receipt_id in original["body"]["content"], original["body"]["content"]
    assert "PRIVATE_" not in original["body"]["content"]
    # Process recovery has nothing left to transport and preserves the card.
    recovery = ingress.recover_room_claim_results(**callback_args, allowed_chat_ids={"oc_room_fixture"})
    assert recovery == {"attempted": 0, "delivered": 0, "failed": 0}, recovery
    still_original = next(m for m in room.messages if m["message_id"] == delivery["message_id"])
    assert receipt_id in still_original["body"]["content"]
    assert snapshot(args)["provider_revision"] == committed


def test_collector_default_off_and_native_callback_dispatch(offered, tmp_path):
    import shutil
    from loopx.extensions.lark.event_collector_runtime import run_lark_event_collector

    args, room, binding, _, offer, event, callback_args, _ = offered
    project = args["registry_path"].parent.parent
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".gitignore").write_text(".loopx/\nruntime/\n")
    cli = tmp_path / "room-event-cli.mjs"
    for connection in binding["bindings"][GOAL]["connections"].values():
        connection["identity"]["cli_bin"] = str(cli)
    write_goal_channel_binding(args["binding_path"], binding)
    prepared = offer()
    assert prepared["ok"], prepared
    callback = event(prepared)
    cli.write_text("const key=process.argv[process.argv.indexOf('consume')+1];"
        "if(key==='card.action.trigger'){console.log('[event] ready event_key=card.action.trigger');"
        "console.log(" + json.dumps(json.dumps(callback)) + ");}else{setTimeout(()=>{},500);}")
    cfg = project / ".loopx/config/lark"
    cfg.mkdir(parents=True)
    (cfg / "inbox.json").write_text(json.dumps({"schema_version": "lark_event_inbox_config_v0", "enabled": True,
        "inbox_dir": ".loopx/inbox/room", "capture_scope": "configured_chat_all", "reply": {"enabled": True,
            "sender_profile": "room-fixture", "sender_identity": "bot", "bot_display_name": "Fixture Bot",
            "chat_id": "oc_room_fixture", "placement_policy": "source_context", "editorial_style": "bullet_points_preferred"}}))
    config = {"schema_version": "lark_event_collector_config_v1", "enabled": True,
        "service_name": "loopx-room-fixture", "event_key": "im.message.receive_v1", "identity": "bot",
        "supervisor": "systemd", "consume_timeout": "30m", "lark_cli_bin": "lark-cli",
        "operation_callbacks": {"enabled": False}, "routes": [{"route_key": "room",
            "chat_id": "oc_room_fixture", "event_inbox_config": ".loopx/config/lark/inbox.json"}]}
    path = cfg / "collector.json"
    path.write_text(json.dumps(config))

    def process_runner(argv, **kwargs):
        if "whoami" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"appId": "cli_room_fixture"}), "")
        reply = callback_args["runner"](argv, None, kwargs.get("timeout"))
        return subprocess.CompletedProcess(argv, reply["returncode"], reply["stdout"], reply.get("stderr", ""))

    before = snapshot(args)["provider_revision"]
    inactive = run_lark_event_collector(project=project, config_path=path, lark_cli_executable=str(cli),
        node_executable=shutil.which("node"), runtime_root=args["runtime_root"], runner=process_runner)
    assert inactive["ok"] is True
    assert inactive.get("operation_callback_listener_started", False) is False
    assert snapshot(args)["provider_revision"] == before
    config["operation_callbacks"]["enabled"] = True
    path.write_text(json.dumps(config))
    active = run_lark_event_collector(project=project, config_path=path, lark_cli_executable=str(cli),
        node_executable=shutil.which("node"), runtime_root=args["runtime_root"], runner=process_runner)
    assert active["operation_callback_listener_started"] is True
    assert active["operation_callback_verified_count"] == 1, active
    assert snapshot(args)["todos"][0]["claimed_by"] == "agent-a"
    assert len(room.messages) == 1


def test_legacy_offer_cannot_inherit_an_exact_goal_instance(offered):
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction
    args, _, _, _, offer, event, callback_args, _ = offered
    prepared = offer()
    assert prepared["ok"], prepared
    callback = event(prepared)
    before = snapshot(args)["provider_revision"]
    payload = json.loads(args["registry_path"].read_text())
    payload.update(profile_id="source_session_v1", session_bindings=[], session_receipts=[], lifetime_receipts=[])
    payload["goals"][0].update(goal_instance_id="ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", status="active")
    args["registry_path"].unlink()
    with source_session_registry_transaction(args["registry_path"], operation="create_fixture_instance",
        create=lambda: payload) as transaction:
        transaction.commit(transaction.payload_copy())
    with pytest.raises(ingress.RoomClaimCallbackError):
        ingress.handle_room_claim_callback(callback, **callback_args)
    assert snapshot(args)["provider_revision"] == before
    denied = offer(key="exact-profile-offer")
    assert not denied["ok"] and denied["blocker"] == "exact_instance_claim_unqualified", denied

def _offer_on(offer_args, todo_id, revision, key):
    return ingress.run_room_claim_offer(**offer_args, actor_id="agent-a", command="offer", execute=True,
        todo_id=todo_id, expected_revision=revision, idempotency_key=key,
        principals=["lark:ou_room_owner"], expires_at="2030-01-01T00:00:00Z")


def _add_todo(args, text):
    before = {todo["todo_id"] for todo in snapshot(args)["todos"]}
    proc = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(args["registry_path"]),
        "--format", "json", "todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--text", text], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PRIVATE_" not in proc.stdout
    return next(iter({todo["todo_id"] for todo in snapshot(args)["todos"]} - before))


def _add_second_todo(args):
    return _add_todo(args, "Second independently claimable task")


@pytest.mark.parametrize("limit", [1, 2])
def test_revoked_offer_cannot_starve_valid_receipt_recovery(offered, limit):
    """Mixed recovery queue: two legal claims committed but both first card
    updates failed; the offer sorting first is then revoked. Recovery must
    deliver the still-valid receipt to its original message within the limit,
    leave the revoked card untouched and never advance the canonical revision."""
    args, room, _, offer_args, _, event, callback_args, transport = offered
    second = _add_second_todo(args)
    base = snapshot(args)["provider_revision"]
    first_offer = _offer_on(offer_args, TODO, base, "room-recovery-first")
    transport["fail_update"] = True
    first_pending = ingress.handle_room_claim_callback(event(first_offer), **callback_args)
    assert first_pending["canonical_claim_accepted"] and not first_pending["ok"], first_pending
    mid = snapshot(args)["provider_revision"]
    assert mid != base
    second_offer = _offer_on(offer_args, second, mid, "room-recovery-second")
    second_pending = ingress.handle_room_claim_callback(event(second_offer), **callback_args)
    assert second_pending["canonical_claim_accepted"] and not second_pending["ok"], second_pending
    committed = snapshot(args)["provider_revision"]
    assert committed != mid
    offers = {"first": first_offer, "second": second_offer}
    files = {name: args["runtime_root"] / "room-claim-offers" / (result["request_id"] + ".json")
             for name, result in offers.items()}
    revoked_name, valid_name = sorted(files, key=lambda name: str(files[name]))
    valid_receipt_id = (first_pending if valid_name == "first" else second_pending)["receipt"]["receipt_id"]
    revoked_file, valid_file = files[revoked_name], files[valid_name]
    revoked_message_id = json.loads(revoked_file.read_bytes())["delivery"]["message_id"]
    valid_message_id = json.loads(valid_file.read_bytes())["delivery"]["message_id"]
    revoked_card_before = next(m for m in room.messages if m["message_id"] == revoked_message_id)["body"]["content"]
    revoked = ingress.run_room_claim_offer(**offer_args, actor_id="agent-a", command="revoke",
        execute=True, request_id=offers[revoked_name]["request_id"])
    assert revoked["ok"], revoked
    revoked_offer_after_revoke = revoked_file.read_bytes()
    transport["fail_update"] = False
    recovery = ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"}, limit=limit)
    assert recovery == {"attempted": 1, "delivered": 1, "failed": 0}, recovery
    valid_message = next(m for m in room.messages if m["message_id"] == valid_message_id)
    assert valid_receipt_id in valid_message["body"]["content"], valid_message["body"]["content"]
    revoked_message = next(m for m in room.messages if m["message_id"] == revoked_message_id)
    assert revoked_message["body"]["content"] == revoked_card_before
    # Recovery neither rewrote the revoked record nor touched its message.
    assert revoked_file.read_bytes() == revoked_offer_after_revoke
    assert snapshot(args)["provider_revision"] == committed
    assert ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"}, limit=limit)["attempted"] == 0


def test_persistent_dead_prefix_cannot_monopolize_recovery_budget(offered):
    """Repro A: the front-sorted original message persistently fails to
    update. The per-round transport budget must rotate so the still-valid
    receipt behind the dead prefix gets its attempt, while the unresolved
    record keeps its card and receipt evidence forever."""
    args, room, _, offer_args, _, event, callback_args, transport = offered
    second = _add_second_todo(args)
    base = snapshot(args)["provider_revision"]
    first_offer = _offer_on(offer_args, TODO, base, "room-dead-prefix-first")
    transport["fail_update"] = True
    first_pending = ingress.handle_room_claim_callback(event(first_offer), **callback_args)
    assert first_pending["canonical_claim_accepted"] and not first_pending["ok"], first_pending
    mid = snapshot(args)["provider_revision"]
    second_offer = _offer_on(offer_args, second, mid, "room-dead-prefix-second")
    second_pending = ingress.handle_room_claim_callback(event(second_offer), **callback_args)
    assert second_pending["canonical_claim_accepted"] and not second_pending["ok"], second_pending
    committed = snapshot(args)["provider_revision"]
    files = {name: args["runtime_root"] / "room-claim-offers" / (result["request_id"] + ".json")
             for name, result in (("first", first_offer), ("second", second_offer))}
    dead_name, valid_name = sorted(files, key=lambda name: str(files[name]))
    dead_file, valid_file = files[dead_name], files[valid_name]
    dead_message_id = json.loads(dead_file.read_bytes())["delivery"]["message_id"]
    valid_message_id = json.loads(valid_file.read_bytes())["delivery"]["message_id"]
    valid_receipt_id = (first_pending if valid_name == "first" else second_pending)["receipt"]["receipt_id"]
    # Transport works again except for the dead prefix message itself.
    transport["fail_update"] = False
    transport["fail_update_message_ids"] = (dead_message_id,)
    round_one = ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"}, limit=1)
    assert round_one == {"attempted": 1, "delivered": 0, "failed": 1}, round_one
    # Rotation must move the next round past the dead prefix.
    round_two = ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"}, limit=1)
    assert round_two == {"attempted": 1, "delivered": 1, "failed": 0}, round_two
    valid_message = next(m for m in room.messages if m["message_id"] == valid_message_id)
    assert valid_receipt_id in valid_message["body"]["content"], valid_message["body"]["content"]
    dead_record = json.loads(dead_file.read_bytes())
    assert dead_record["result_delivery_verified"] is False and dead_record["last_result_card"]
    assert snapshot(args)["provider_revision"] == committed
    # The dead record is retried periodically, never finalized by failure.
    round_three = ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"}, limit=1)
    assert round_three == {"attempted": 1, "delivered": 0, "failed": 1}, round_three
    assert json.loads(dead_file.read_bytes())["result_delivery_verified"] is False
    assert snapshot(args)["provider_revision"] == committed


def test_default_budget_rotation_reaches_the_tail_record(offered):
    """Repro B: 21 committed claims, the first 20 card updates persistently
    fail. Under the default limit of 20 the tail record must still receive its
    transport attempt on the next recovery round, and the failing records
    keep their evidence."""
    args, room, _, offer_args, _, event, callback_args, transport = offered
    todos = [TODO]
    for index in range(20):
        todos.append(_add_todo(args, f"Tail-reach task {index}"))
    transport["fail_update"] = True
    pendings = {}
    revision = snapshot(args)["provider_revision"]
    for index, todo_id in enumerate(todos):
        prepared = _offer_on(offer_args, todo_id, revision, f"room-tail-{index:02d}")
        pending = ingress.handle_room_claim_callback(event(prepared), **callback_args)
        assert pending["canonical_claim_accepted"] and not pending["ok"], pending
        pendings[prepared["request_id"]] = pending
        revision = snapshot(args)["provider_revision"]
    committed = revision
    files = sorted(args["runtime_root"].glob("room-claim-offers/rc_*.json"))
    assert len(files) == 21
    dead, tail = files[:20], files[20]
    tail_id = tail.stem
    tail_message_id = json.loads(tail.read_bytes())["delivery"]["message_id"]
    tail_receipt_id = pendings[tail_id]["receipt"]["receipt_id"]
    transport["fail_update"] = False
    transport["fail_update_message_ids"] = tuple(
        json.loads(f.read_bytes())["delivery"]["message_id"] for f in dead)
    round_one = ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"})
    assert round_one == {"attempted": 20, "delivered": 0, "failed": 20}, round_one
    round_two = ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"})
    assert round_two == {"attempted": 20, "delivered": 1, "failed": 19}, round_two
    tail_message = next(m for m in room.messages if m["message_id"] == tail_message_id)
    assert tail_receipt_id in tail_message["body"]["content"], tail_message["body"]["content"]
    for f in dead:
        record = json.loads(f.read_bytes())
        assert record["result_delivery_verified"] is False and record["last_result_card"]
    assert snapshot(args)["provider_revision"] == committed


def test_shared_runtime_scopes_keep_independent_recovery_rotation(offered):
    """Two collectors with different room scopes share one runtime directory.
    Each scope's rotation cursor must be isolated: alternating rounds under
    limit=1 still deliver each scope's valid receipt past its own dead prefix,
    each scope keeps its own progress, and canonical state never advances."""
    args, room, binding, offer_args, _, event, callback_args, transport = offered
    binding["bindings"][GOAL]["connections"]["agent-b"]["channel"]["chat_id"] = "oc_room_scope_b"
    write_goal_channel_binding(args["binding_path"], binding)
    todos = [TODO]
    for index in range(3):
        todos.append(_add_todo(args, f"Shared-scope task {index}"))
    def event_in_room(result):
        callback = event(result)
        message = next(m for m in room.messages if m["message_id"] == json.loads(
            (args["runtime_root"] / "room-claim-offers" / (result["request_id"] + ".json")).read_bytes()
        )["delivery"]["message_id"])
        return {**callback, "chat_id": message["chat_id"]}

    transport["fail_update"] = True
    packet_by_file = {}
    revision = snapshot(args)["provider_revision"]
    for actor, todo_id in (("agent-a", todos[0]), ("agent-a", todos[1]),
                           ("agent-b", todos[2]), ("agent-b", todos[3])):
        prepared = ingress.run_room_claim_offer(**offer_args, actor_id=actor, command="offer", execute=True,
            todo_id=todo_id, expected_revision=revision, idempotency_key=f"room-shared-{actor}-{todo_id[:8]}",
            principals=["lark:ou_room_owner"], expires_at="2030-01-01T00:00:00Z")
        pending = ingress.handle_room_claim_callback(event_in_room(prepared), **callback_args)
        assert pending["canonical_claim_accepted"] and not pending["ok"], pending
        packet_by_file[args["runtime_root"] / "room-claim-offers" / (prepared["request_id"] + ".json")] = pending
        revision = snapshot(args)["provider_revision"]
    committed = revision
    scope_a, scope_b = {"oc_room_fixture"}, {"oc_room_scope_b"}
    actors = {"agent-a": scope_a, "agent-b": scope_b}
    files_by_actor = {actor: sorted(f for f in packet_by_file
                                    if json.loads(f.read_bytes())["request"]["actor_id"] == actor)
                      for actor in actors}
    # dead prefix = each scope's first-sorted record; the other is the valid one.
    dead = {actor: files_by_actor[actor][0] for actor in actors}
    valid = {actor: files_by_actor[actor][1] for actor in actors}
    valid_receipt_id = {actor: packet_by_file[valid[actor]]["receipt"]["receipt_id"] for actor in actors}
    valid_message_id = {actor: json.loads(valid[actor].read_bytes())["delivery"]["message_id"] for actor in actors}
    transport["fail_update"] = False
    transport["fail_update_message_ids"] = tuple(
        json.loads(f.read_bytes())["delivery"]["message_id"] for f in dead.values())

    def recover(scope):
        return ingress.recover_room_claim_results(**callback_args, allowed_chat_ids=scope, limit=1)

    assert recover(scope_a) == {"attempted": 1, "delivered": 0, "failed": 1}
    assert recover(scope_b) == {"attempted": 1, "delivered": 0, "failed": 1}
    # Independent rotation: each scope's second round must pass its OWN dead
    # prefix and deliver its valid receipt (a shared cursor would restart
    # scope A at its dead prefix after scope B's round).
    assert recover(scope_a) == {"attempted": 1, "delivered": 1, "failed": 0}
    assert recover(scope_b) == {"attempted": 1, "delivered": 1, "failed": 0}
    for actor, scope in (("agent-a", scope_a), ("agent-b", scope_b)):
        message = next(m for m in room.messages if m["message_id"] == valid_message_id[actor])
        assert message["chat_id"] in scope
        assert valid_receipt_id[actor] in message["body"]["content"], message["body"]["content"]
    for actor, f in dead.items():
        record = json.loads(f.read_bytes())
        assert record["result_delivery_verified"] is False and record["last_result_card"]
    assert snapshot(args)["provider_revision"] == committed
    # Further alternation keeps respecting the per-round cap and stays idle
    # once both scopes delivered their valid records in this phase.
    idle_a = recover(scope_a)
    assert idle_a["attempted"] <= 1 and snapshot(args)["provider_revision"] == committed

def test_result_updates_match_the_original_card_schema(offered):
    """The committed-receipt update and its recovery patch must ride the same
    Card 2.0 structure as the original offer card (Lark rejects a schema
    mismatch on in-place updates, error 200830): schema "2.0" with
    body.elements, the same receipt lands on the original message id, and no
    second canonical claim happens."""
    args, room, _, _, offer, event, callback_args, transport = offered
    prepared = offer()
    original_message_id = next(m["message_id"] for m in room.messages
        if "request_id" in m["body"]["content"])
    original_card = json.loads(next(m for m in room.messages
        if m["message_id"] == original_message_id)["body"]["content"])
    assert original_card["schema"] == "2.0" and original_card["body"]["elements"]
    # First update attempt loses its transport response; the claim commits.
    transport["fail_update"] = True
    result = ingress.handle_room_claim_callback(event(prepared), **callback_args)
    assert result["canonical_claim_accepted"] is True and result["ok"] is False, result
    receipt_id = result["receipt"]["receipt_id"]
    committed = snapshot(args)["provider_revision"]
    # Recovery patches the committed receipt onto the original message with a
    # structure the original 2.0 card accepts (the fixture enforces 200830).
    transport["fail_update"] = False
    transport["seen_update_cards"].clear()
    recovery = ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"})
    assert recovery == {"attempted": 1, "delivered": 1, "failed": 0}, recovery
    update_card = transport["seen_update_cards"][0]
    assert update_card["schema"] == "2.0" and update_card["body"]["elements"]
    message = next(m for m in room.messages if m["message_id"] == original_message_id)
    assert receipt_id in message["body"]["content"]
    assert len([m for m in room.messages if m["message_id"] == original_message_id]) == 1
    assert ingress.recover_room_claim_results(**callback_args,
        allowed_chat_ids={"oc_room_fixture"})["attempted"] == 0
    # Exactly one canonical claim; recovery advanced nothing.
    assert snapshot(args)["provider_revision"] == committed
    assert next(t for t in snapshot(args)["todos"] if t["todo_id"] == TODO)["claimed_by"] == "agent-a"
