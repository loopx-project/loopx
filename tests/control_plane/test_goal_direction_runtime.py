"""Actual source registration/provider read/receipt via the ordinary CLI."""
# ruff: noqa: F811 - imported source_goal is a reused pytest fixture.
from __future__ import annotations

import hashlib
import json


from test_goal_amendment_authority import source_goal  # noqa: F401
from loopx.cli import main
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.goals.source_session_recreation import RecreateGoalRequest, recreate_goal_instance


def call(fixture, tmp_path, capsys, operation, parameters=None, *, actor=None, execute=False):
    args = ["--registry", str(fixture["registry"]), "--format", "json", "goal-direction",
            "--operation", operation, "--goal-id", fixture["ref"]["goal_id"], "--goal-instance-id", fixture["ref"]["goal_instance_id"]]
    if actor:
        args += ["--agent-id", actor]
    if parameters is not None:
        path = tmp_path / "direction-request.json"
        path.write_text(json.dumps(parameters))
        args += ["--request-json", str(path)]
    if execute:
        args.append("--execute")
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


def setup(source_goal, tmp_path, capsys):
    f = source_goal
    document = f["registry"].parent.parent / "direction.md"
    document.write_text("# Contract\nKeep the original acceptance.\n")
    code, view = call(f, tmp_path, capsys, "inspect", actor="agent-a")
    assert code == 0 and view["enabled"] is False and view["projection"] is None, view
    config = {"materials": [{"material_id": "contract", "source_path": "direction.md", "purpose": "Preserve the contract",
        "topics": ["architecture"], "required": True, "boundary": "local_private"}],
        "expected_provider_revision": view["provider_revision"], "operation_id": "direction-root"}
    preview = call(f, tmp_path, capsys, "configure", config)[1]
    assert preview["ok"] and preview["status"] == "planned", preview
    assert call(f, tmp_path, capsys, "inspect")[1]["enabled"] is False
    code, configured = call(f, tmp_path, capsys, "configure", config, execute=True)
    assert code == 0 and configured["ok"], configured
    view = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    assert view["projection"]["direction_state"] == "blocked"
    assert view["projection"]["items"][0]["state"] == "required_unread"
    request = {"material_id": "contract", "source_path": "direction.md", "required_revision": "sha256:" + hashlib.sha256(document.read_bytes()).hexdigest(),
               "authority_digest": view["authority_digest"], "operation_id": "material-read"}
    return f, document, config, request


def test_actual_read_then_same_agent_acknowledgement_and_drift(source_goal, tmp_path, capsys):
    f, document, config, request = setup(source_goal, tmp_path, capsys)
    work_before = read_canonical_todos_if_promoted(runtime_root=f["runtime"], goal_id=f["ref"]["goal_id"], include_leases=True)
    state_before = f["state"].read_bytes()
    code, read = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)
    assert code == 0 and read["content"] == document.read_text() and read["receipt_recorded"] is False, read
    assert call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]["projection"]["items"][0]["state"] == "required_unread"
    ack = {"read_operation_id": "material-read", "delivery_digest": read["delivery_digest"], "operation_id": "read-ack"}
    before = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    preview = call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-a")[1]
    assert preview["status"] == "planned" and preview["receipt_recorded"] is False, preview
    assert call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]["provider_revision"] == before["provider_revision"]
    assert call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-b", execute=True)[0] == 1
    after_wrong = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    assert after_wrong["provider_revision"] == before["provider_revision"]
    assert after_wrong["projection"]["items"] == before["projection"]["items"]
    code, confirmed = call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-a", execute=True)
    assert code == 0 and confirmed["receipt_recorded"] is True and confirmed["understanding_verified"] is False, confirmed
    assert confirmed["current"]["projection"]["direction_state"] == "current"
    assert confirmed["current"]["acceptance_basis"]["acceptance_assessed"] is False
    assert call(f, tmp_path, capsys, "inspect", actor="agent-b")[1]["projection"]["direction_state"] == "blocked"
    replay = call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-a", execute=True)[1]
    assert replay["status"] == "replayed" and replay["receipt"] == confirmed["receipt"]
    document.write_text("# Revised contract\nKeep all original acceptance and verify dependency.\n")
    current = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    update = {**config, "operation_id": "source-revision", "expected_provider_revision": current["provider_revision"]}
    assert call(f, tmp_path, capsys, "configure", update, execute=True)[0] == 0
    changed = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    assert changed["projection"]["direction_state"] == "re_evaluation_required"
    assert changed["vision_gap"]["kind"] == "goal_direction_basis"
    new_read = {**request, "operation_id": "material-read-new", "required_revision": changed["projection"]["items"][0]["required_revision"], "authority_digest": changed["authority_digest"]}
    latest = call(f, tmp_path, capsys, "read", new_read, actor="agent-a", execute=True)[1]
    fixed = call(f, tmp_path, capsys, "acknowledge", {"read_operation_id": "material-read-new", "delivery_digest": latest["delivery_digest"], "operation_id": "new-ack"}, actor="agent-a", execute=True)[1]
    assert fixed["current"]["projection"]["direction_state"] == "current"
    work_after = read_canonical_todos_if_promoted(runtime_root=f["runtime"], goal_id=f["ref"]["goal_id"], include_leases=True)
    assert work_after["todos"] == work_before["todos"] and work_after["leases"] == work_before["leases"]
    assert f["state"].read_bytes() == state_before
    # Canonical metadata/receipts cannot retain the locator or body.
    assert "direction.md" not in json.dumps(fixed) and document.read_text() not in json.dumps(fixed)


def test_read_authority_precedes_io_and_disable_preserves_history(source_goal, tmp_path, capsys):
    f, document, _, request = setup(source_goal, tmp_path, capsys)
    document.unlink()
    for actor in ["unknown", "agent-b"]:
        wrong = request if actor == "unknown" else {**request, "source_path": "another.md"}
        code, result = call(f, tmp_path, capsys, "read", wrong, actor=actor, execute=True)
        assert code == 1 and result["schema_version"] == "goal_direction_result_v0", result
        assert "content" not in result
    current = call(f, tmp_path, capsys, "inspect")[1]
    assert call(f, tmp_path, capsys, "disable", {"expected_provider_revision": current["provider_revision"], "operation_id": "disable"}, execute=True)[0] == 0
    assert call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]["projection"] is None
    assert call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[0] == 1
    history = call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "direction-root"})[1]
    assert history["status"] == "found" and history["historical_only"]


def test_recreated_lifetime_cannot_acknowledge_old_offer(source_goal, tmp_path, capsys):
    f, _, _, request = setup(source_goal, tmp_path, capsys)
    read = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[1]
    renewed = recreate_goal_instance(RecreateGoalRequest(registry_path=f["registry"], goal_id=f["ref"]["goal_id"], goal_instance_id=f["ref"]["goal_instance_id"], operation_id="replace"))
    assert renewed["ok"]
    ack = {"read_operation_id": "material-read", "delivery_digest": read["delivery_digest"], "operation_id": "late-ack"}
    assert call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-a", execute=True)[0] != 0
    current = {**f, "ref": renewed["goal_ref"]}
    assert call(current, tmp_path, capsys, "acknowledge", ack, actor="agent-a", execute=True)[0] != 0
    old = call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "material-read"}, actor="agent-a")[1]
    assert old["status"] == "found" and old["historical_only"]


def test_unavailable_read_supersedes_old_success_and_requires_new_delivery(source_goal, tmp_path, capsys, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from loopx.control_plane.goals import direction_runtime

    f, document, _, request = setup(source_goal, tmp_path, capsys)
    read = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[1]
    ack = {"read_operation_id": "material-read", "delivery_digest": read["delivery_digest"], "operation_id": "ack"}
    assert call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-a", execute=True)[0] == 0
    before = document.read_text()
    second = call(f, tmp_path, capsys, "read", {**request, "operation_id": "unconfirmed"}, actor="agent-a", execute=True)[1]
    document.unlink()
    class RewoundClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) - timedelta(days=1)
    monkeypatch.setattr(direction_runtime, "datetime", RewoundClock)
    code, failed = call(f, tmp_path, capsys, "read", {**request, "operation_id": "unavailable"}, actor="agent-a", execute=True)
    assert code == 1 and failed["read_outcome"] == "unavailable" and "content" not in failed, failed
    view = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    assert view["projection"]["direction_state"] == "blocked"
    assert view["projection"]["items"][0]["state"] == "inaccessible"
    stale = {"read_operation_id": "unconfirmed", "delivery_digest": second["delivery_digest"], "operation_id": "late-confirm"}
    assert call(f, tmp_path, capsys, "acknowledge", stale, actor="agent-a", execute=True)[0] == 1
    document.write_text(before)
    fresh = call(f, tmp_path, capsys, "read", {**request, "operation_id": "recovered-read"}, actor="agent-a", execute=True)[1]
    fixed = call(f, tmp_path, capsys, "acknowledge", {"read_operation_id": "recovered-read", "delivery_digest": fresh["delivery_digest"], "operation_id": "recovered-ack"}, actor="agent-a", execute=True)[1]
    assert fixed["current"]["projection"]["direction_state"] == "current"


def test_read_response_loss_replays_original_offer_without_forging_usage(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.goals import direction_runtime
    from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous

    f, document, _, request = setup(source_goal, tmp_path, capsys)
    original = direction_runtime.effect_runtime_result
    dropped = []

    def drop_reply(method, payload, **kwargs):
        result = original(method, payload, **kwargs)
        if payload["operation"] == "offer_read" and not dropped:
            dropped.append(result)
            raise EffectRuntimeResponseAmbiguous(method, timeout=10)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(direction_runtime, "effect_runtime_result", drop_reply)
        assert call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[0] == 1
    view = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    assert view["receipt_count"] == 0 and view["projection"]["items"][0]["state"] == "required_unread"
    code, recovered = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)
    assert code == 0 and recovered["status"] == "replayed" and recovered["content"] == document.read_text(), recovered
    assert recovered["original_receipt"] == dropped[0]["original_receipt"]
    ack = {"read_operation_id": request["operation_id"], "delivery_digest": recovered["delivery_digest"], "operation_id": "recovered-ack"}
    assert call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-a", execute=True)[1]["current"]["receipt_count"] == 1


def test_delayed_acknowledgement_after_recreation_cannot_publish_usage(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.goals import direction_runtime
    from loopx.control_plane.effect_runtime import effect_runtime_result, EffectRuntimeResponseAmbiguous

    f, _, _, request = setup(source_goal, tmp_path, capsys)
    offered = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[1]
    captured = []

    def delay(method, payload, **kwargs):
        if payload["operation"] != "acknowledge":
            return effect_runtime_result(method, payload, **kwargs)
        captured.append(payload)
        raise EffectRuntimeResponseAmbiguous(method, timeout=10)

    with monkeypatch.context() as patch:
        patch.setattr(direction_runtime, "effect_runtime_result", delay)
        assert call(f, tmp_path, capsys, "acknowledge", {"read_operation_id": request["operation_id"], "delivery_digest": offered["delivery_digest"], "operation_id": "delayed-ack"}, actor="agent-a", execute=True)[0] == 1
    recreated = recreate_goal_instance(RecreateGoalRequest(registry_path=f["registry"], goal_id=f["ref"]["goal_id"], goal_instance_id=f["ref"]["goal_instance_id"], operation_id="recreate"))
    assert recreated["ok"] and len(captured) == 1
    # Dispatch the original request after the caller's observation locks ended.
    try:
        late = effect_runtime_result("goal.direction.execute", captured[0])
        assert late["ok"] is False, late
    except Exception as error:
        from loopx.control_plane.effect_runtime import EffectRuntimeConflict
        assert isinstance(error, EffectRuntimeConflict)
    history = call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "delayed-ack"}, actor="agent-a")[1]
    assert history["status"] == "missing"


def test_direction_keeps_existing_shared_intent_work_and_real_lease(source_goal, tmp_path, capsys):
    from test_goal_amendment_authority import test_claimed_successor_is_consumed_by_existing_replan_owner, _call

    f = source_goal
    test_claimed_successor_is_consumed_by_existing_replan_owner(f, tmp_path, capsys, "agent-a")
    before = _call(f, tmp_path, capsys, "inspect")[1]
    f, _, _, request = setup(f, tmp_path, capsys)
    read = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[1]
    ack = call(f, tmp_path, capsys, "acknowledge", {"read_operation_id": request["operation_id"], "delivery_digest": read["delivery_digest"], "operation_id": "ack"}, actor="agent-a", execute=True)[1]
    assert ack["current"]["projection"]["direction_state"] == "current"
    after = _call(f, tmp_path, capsys, "inspect")[1]
    assert before["work_digest"] == after["work_digest"] and before["intent"] == after["intent"]
    assert before["work_items"] == after["work_items"]


def test_direction_basis_reads_actual_acceptance_owner_without_verifying_it(source_goal, tmp_path, capsys):
    from loopx.control_plane.effect_runtime import effect_runtime_result

    f, _, _, request = setup(source_goal, tmp_path, capsys)
    view = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    document = {"scope": {"kind": "all_advancement"}, "objective": "Preserve the accepted Goal",
        "non_goals": [], "criteria": [{"id": "proof", "description": "Independent result remains required",
            "validation_argv": ["true"], "validation_timeout_seconds": 5, "validation_files": []}], "bindings": []}
    # Use the existing typed acceptance owner, not a forged head/verification.
    # The generic source-session acceptance CLI remains unqualified; this tests
    # linkage to an existing canonical contract without opening that writer.
    route = {"runtime_root": str(f["runtime"]), "goal_id": f["ref"]["goal_id"], "goal_ref": f["ref"]}
    configured = effect_runtime_result("goal.acceptance.configure", {**route, "actor_agent_id": None,
        "expected_provider_revision": view["provider_revision"], "document": document,
        "operation_id": "acceptance-configure", "dry_run": False})
    before = effect_runtime_result("goal.acceptance.inspect", route)
    assert before["goal_acceptance_contract"]["status"] == "unverified"
    read = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[1]
    confirmed = call(f, tmp_path, capsys, "acknowledge", {"read_operation_id": request["operation_id"], "delivery_digest": read["delivery_digest"], "operation_id": "ack"}, actor="agent-a", execute=True)[1]
    basis = confirmed["current"]["acceptance_basis"]
    assert basis["contract_revision"] == configured["goal_acceptance_contract"]["revision"]
    assert basis["contract_digest"] == configured["goal_acceptance_contract"]["digest"]
    assert basis["acceptance_assessed"] is False and basis["result"] == "not_assessed"
    after = effect_runtime_result("goal.acceptance.inspect", route)
    assert after["goal_acceptance_contract"] == before["goal_acceptance_contract"]
    assert after["contract"] == before["contract"]


def test_reordered_same_revision_declarations_do_not_invalidate_an_offer(source_goal, tmp_path, capsys):
    f, _, config, request = setup(source_goal, tmp_path, capsys)
    second = dict(config["materials"][0], material_id="additional", purpose="Retain related scope", required=False)
    before = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    config.update(materials=[*config["materials"], second], operation_id="two-materials", expected_provider_revision=before["provider_revision"])
    first = call(f, tmp_path, capsys, "configure", config, execute=True)[1]
    view = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    request["authority_digest"] = view["authority_digest"]
    offered = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[1]
    latest = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    shuffled = {**config, "materials": list(reversed(config["materials"])), "expected_provider_revision": latest["provider_revision"], "operation_id": "reordered"}
    code, result = call(f, tmp_path, capsys, "configure", shuffled, execute=True)
    assert code == 0 and result["status"] == "no_change" and result["authority_digest"] == first["authority_digest"], result
    confirmed = call(f, tmp_path, capsys, "acknowledge", {"operation_id": "ack", "read_operation_id": request["operation_id"], "delivery_digest": offered["delivery_digest"]}, actor="agent-a", execute=True)[1]
    assert confirmed["current"]["projection"]["direction_state"] == "current"


def test_authority_summary_is_not_empty_and_blocks_source_io_before_configuration(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.goals import direction_runtime
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    f, _, config, _ = setup(source_goal, tmp_path, capsys)
    view = call(f, tmp_path, capsys, "inspect")[1]
    with source_session_registry_transaction(f["registry"], operation="synthetic_existing_authority") as transaction:
        registry = transaction.payload_copy()
        registry["goals"][0]["authority_sources"] = [{"id": "preexisting", "source_ref_redacted": True}]
        transaction.commit(registry)
    touched = []
    monkeypatch.setattr(direction_runtime, "_read", lambda *args: touched.append(args))
    assert call(f, tmp_path, capsys, "inspect")[0] == 1
    code, blocked = call(f, tmp_path, capsys, "configure", {**config,
        "expected_provider_revision": view["provider_revision"], "operation_id": "second-owner"}, execute=True)
    assert code == 1 and "existing registry authority" in blocked["reason"], blocked
    assert touched == []
    # Stopping this reader grants no new access and remains an owner operation.
    assert call(f, tmp_path, capsys, "disable", {"expected_provider_revision": view["provider_revision"],
        "operation_id": "stop-conflicting-reader"}, execute=True)[0] == 0
    assert touched == []


def test_retirement_closes_configuration_before_any_source_io(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.goals import direction_runtime
    from loopx.control_plane.effect_runtime import effect_runtime_result

    f, _, config, _ = setup(source_goal, tmp_path, capsys)
    route = {"runtime_root": str(f["runtime"]), "goal_id": f["ref"]["goal_id"], "actor_agent_id": None}
    for kind in ["bind_existing", "retire"]:
        transition = effect_runtime_result("goal.acceptance.lifecycle.transition", {**route, "operation_id": kind,
            "transition": {"kind": kind, "goal_ref": f["ref"]}})
        assert transition["status"] in {"applied", "no_change"}, transition
    touched = []
    monkeypatch.setattr(direction_runtime, "_read", lambda *args: touched.append(args))
    code, result = call(f, tmp_path, capsys, "configure", {**config, "operation_id": "retiring-read"}, execute=True)
    assert code == 1 and "lifetime retired or changed" in result["reason"], result
    assert touched == []


def test_missing_repo_material_never_falls_back_to_same_named_runtime_copy(source_goal, tmp_path, capsys):
    from loopx.materials import read_review_material
    f, document, _, request = setup(source_goal, tmp_path, capsys)
    replica = f["runtime"] / "goals" / f["ref"]["goal_id"] / "direction.md"
    replica.parent.mkdir(parents=True, exist_ok=True)
    replica.write_bytes(document.read_bytes())
    document.unlink()
    code, refused = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)
    assert code == 1 and "content" not in refused and refused["read_outcome"] == "unavailable", refused
    assert call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]["projection"]["direction_state"] == "blocked"
    # Existing unregistered review links retain their older multi-root lookup;
    # only this explicitly registered direction source uses repo-relative pinning.
    legacy = read_review_material(registry={"goals": [{"id": f["ref"]["goal_id"],
        "repo": str(f["registry"].parent.parent), "state_file": str(f["state"])}]},
        runtime_root=f["runtime"], goal_id=f["ref"]["goal_id"], raw_path="direction.md")
    assert legacy["content"] == replica.read_text()


def test_full_usage_preserves_failure_and_recovers_without_losing_history(source_goal, tmp_path, capsys):
    """Fill only a disposable native store; all exercised operations use the CLI."""
    import subprocess
    from pathlib import Path

    f, document, _, request = setup(source_goal, tmp_path, capsys)
    read = call(f, tmp_path, capsys, "read", request, actor="agent-a", execute=True)[1]
    ack = {"read_operation_id": request["operation_id"], "delivery_digest": read["delivery_digest"], "operation_id": "original-ack"}
    confirmed = call(f, tmp_path, capsys, "acknowledge", ack, actor="agent-a", execute=True)[1]
    original_receipt = confirmed["original_receipt"]
    # Actual native creation/first read/ack above supplies the exact lifetime and
    # receipt shape. Saturate legal synthetic history through the real provider.
    script = """
import {openLocalAuthorityStore} from './loopx/control_plane/coordination/local_authority_provider.ts';
const store = await openLocalAuthorityStore(process.argv[1], process.argv[2]);
const loaded = await store.loadAuthority();
if (loaded.status !== 'loaded') throw Error('missing authority');
const original = loaded.head.material_usage_receipts[0];
const rows = Array.from({length:4096}, (_,i) => ({...original,
 read_operation_id:'historical-read-'+i, receipt:{...original.receipt,
 receipt_id:'historical-'+i, recorded_at:new Date(Date.UTC(2026,0,1)+i).toISOString()}}));
const result = await store.commitAuthority({expected_provider_revision:loaded.provider_revision,
 operation_id:'synthetic-full-history',events:[],receipts:[],
 next_projection:{...loaded.head,material_usage_receipts:rows}});
if (result.status !== 'applied') throw Error(JSON.stringify(result));
console.log(JSON.stringify(rows[0]));
"""
    seeded = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script,
        str(f["runtime"]), f["ref"]["goal_id"]], cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, check=True)
    first_record = json.loads(seeded.stdout)
    assert call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]["projection"]["direction_state"] == "current"
    stale = call(f, tmp_path, capsys, "read", {**request, "operation_id": "before-loss"}, actor="agent-a", execute=True)[1]
    content = document.read_text()
    document.unlink()
    missing_request = {**request, "operation_id": "full-missing"}
    code, missing = call(f, tmp_path, capsys, "read", missing_request, actor="agent-a", execute=True)
    cold = call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]
    assert cold["projection"]["direction_state"] == "blocked", (missing, cold["projection"])
    assert code == 1 and missing.get("read_outcome") == "unavailable", missing
    assert missing["archived_usage_record"] == first_record
    assert cold["receipt_count"] == 4096
    assert cold["projection"]["items"][0]["state"] == "inaccessible"
    replay = call(f, tmp_path, capsys, "read", missing_request, actor="agent-a", execute=True)[1]
    assert replay["status"] == "replayed" and replay["original_receipt"] == missing["original_receipt"]
    archived = call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "full-missing"}, actor="agent-a")[1]
    assert archived["receipts"][0]["result"]["archived_usage_record"] == first_record
    history = call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "original-ack"}, actor="agent-a")[1]
    assert history["receipts"][0] == original_receipt
    old_ack = {"read_operation_id": "before-loss", "delivery_digest": stale["delivery_digest"], "operation_id": "stale-full-ack"}
    assert call(f, tmp_path, capsys, "acknowledge", old_ack, actor="agent-a", execute=True)[0] == 1
    document.write_text(content)
    recovered = call(f, tmp_path, capsys, "read", {**request, "operation_id": "full-recovered"}, actor="agent-a", execute=True)[1]
    fresh_ack = {"read_operation_id": "full-recovered", "delivery_digest": recovered["delivery_digest"], "operation_id": "full-recovered-ack"}
    code, fixed = call(f, tmp_path, capsys, "acknowledge", fresh_ack, actor="agent-a", execute=True)
    assert code == 0 and fixed["current"]["projection"]["direction_state"] == "current", fixed
    assert fixed["current"]["receipt_count"] == 4096
    assert call(f, tmp_path, capsys, "acknowledge", fresh_ack, actor="agent-a", execute=True)[1]["status"] == "replayed"
    # Another Agent with no retained observation cannot evict this Agent's rows.
    document.unlink()
    code, refused = call(f, tmp_path, capsys, "read", {**request, "operation_id": "peer-full"}, actor="agent-b", execute=True)
    assert code == 1 and "capacity" in refused["reason"], refused
    assert call(f, tmp_path, capsys, "inspect", actor="agent-b")[1]["projection"]["direction_state"] == "blocked"
    assert call(f, tmp_path, capsys, "inspect", actor="agent-a")[1]["receipt_count"] == 4096
