"""Synthetic authenticated Lark ingress, actual canonical File/SQLite work."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_room_work import setup as room_setup, snapshot, GOAL, TODO, ROOT  # noqa: F401
from loopx.extensions.lark import room_claim as ingress
from loopx.extensions.lark.goal_channel_contracts import write_goal_channel_binding


@pytest.fixture
def offered(room_setup, monkeypatch):  # noqa: F811 - pytest fixture dependency
    args, room, binding = room_setup
    source = json.loads(args["registry_path"].read_text())
    source["registry_role"] = "global-local"
    source["goals"][0]["source_registry"] = str(args["registry_path"])
    (args["runtime_root"] / "registry.global.json").write_text(json.dumps(source))
    monkeypatch.setattr(ingress, "resolve_extension_activation", lambda *a, **k: {"enabled": True})
    transport_state = {"fail_update": False, "readable": True, "member": True}

    def runner(argv, cwd, timeout):
        payload = None
        if "auth" in argv:
            return room(argv, cwd, timeout)
        if "chats" in argv and "get" in argv:
            payload = {"data": {"tenant_key": "room-fixture-tenant", "chat_id": "oc_room_fixture"}}
        elif "+chat-members-list" in argv and "--member-types" in argv and argv[argv.index("--member-types") + 1] == "user":
            payload = {"data": {"items": [{"open_id": "ou_room_owner", "tenant_key": "room-fixture-tenant"}]
                if transport_state["member"] else []}}
        elif "api" in argv and "GET" in argv:
            payload = {"data": {"items": room.messages if transport_state["readable"] else []}}
        elif "api" in argv and "POST" in argv:
            if transport_state["fail_update"]:
                return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True}
            data = json.loads(argv[argv.index("--data") + 1])
            mid = data["token"].removeprefix("callback:")
            target = next(m for m in room.messages if m["message_id"] == mid)
            target["body"]["content"] = json.dumps(data["card"])
            payload = {}
        elif "messages" in argv and "patch" in argv:
            mid = argv[argv.index("--message-id") + 1]
            data = json.loads(argv[argv.index("--data") + 1])
            next(m for m in room.messages if m["message_id"] == mid)["body"]["content"] = data["content"]
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

    def offer(actor="agent-a", key="room-offer-fixture", execute=True):
        return ingress.run_room_claim_offer(**offer_args, actor_id=actor, command="offer", execute=execute,
            todo_id=TODO, expected_revision=revision, idempotency_key=key,
            principals=["lark:ou_room_owner"], expires_at="2030-01-01T00:00:00Z")

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
