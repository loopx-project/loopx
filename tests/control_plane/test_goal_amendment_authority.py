from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_goal_amendment_proposal import _stall_runs
from loopx.cli import main
from loopx.control_plane.goals.source_session_recreation import RecreateGoalRequest, recreate_goal_instance
from loopx.control_plane.goals.goal_amendment_proposal import _derive_open_replan_obligation_inventory, _open_replan_obligation_inventory
from loopx.control_plane.projects.registry_codec import load_project_registry


@pytest.fixture(params=["file", "sqlite"])
def source_goal(request, tmp_path, monkeypatch, capsys):
    if request.param == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    runtime = tmp_path / "runtime"
    state = project / "ACTIVE_GOAL_STATE.md"
    goal = {"id": "amendment-goal", "status": "active", "repo": str(project), "state_file": "ACTIVE_GOAL_STATE.md",
            "coordination": {"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"]}}
    assert main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", "project", "register",
        "--project-id", "amendment-project", "--project-kind", "work", "--knowledge-root", str(project), "--goal-id", goal["id"],
        "--objective", "Verify the public contract", "--non-goal", "No remote changes", "--acceptance", "Independent validation passes",
        "--next-effect", "Inspect the current contract", "--stop-condition", "Stop when the Goal is inactive",
        "--goal-instance-profile", "source_session_v1", "--operation-id", "create-goal",
        "--registered-agent", "agent-a", "--registered-agent", "agent-b", "--canonical-storage", request.param]) == 0
    created = json.loads(capsys.readouterr().out)
    state = Path(created["state_file"])
    goal = created["goal"]
    ref = created["goal_ref"]
    assert created["storage_initialization"]["authority_initialized"] is True
    assert created["storage_initialization"]["promotion_performed"] is False
    runs = runtime / "goals" / goal["id"] / "runs" / "index.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    rows = [{**row, "goal_ref": ref} for row in _stall_runs(goal_id=goal["id"])]
    runs.write_text("".join(json.dumps(row) + "\n" for row in rows))
    obligations = _open_replan_obligation_inventory(goal_id=goal["id"], registered_agents=["agent-a", "agent-b"],
        status_item=_derive_open_replan_obligation_inventory(runtime_root=runtime, goal_id=goal["id"], goal=goal, state_text="", goal_ref=ref))
    assert obligations
    return {"registry": registry, "runtime": runtime, "state": state, "ref": ref, "obligation": obligations[0]["obligation_id"], "runs": runs}


def _call(fixture, tmp_path, capsys, operation, params=None, *, agent="agent-a", operation_id=None, execute=False, instance=None):
    args = ["--registry", str(fixture["registry"]), "--runtime-root", str(fixture["runtime"]), "--format", "json",
        "goal-amendment-proposal", "--governed", operation, "--goal-id", fixture["ref"]["goal_id"],
        "--goal-instance-id", instance or fixture["ref"]["goal_instance_id"]]
    if agent:
        args += ["--agent-id", agent]
    if operation_id:
        args += ["--operation-id", operation_id]
    if params is not None:
        path = tmp_path / "request.json"
        path.write_text(json.dumps(params))
        args += ["--request-json", str(path)]
    if execute:
        args.append("--execute")
    code = main(args)
    return code, json.loads(capsys.readouterr().out)


def _root(view):
    return {"expected_provider_revision": view["provider_revision"], "root_intent": {
        "objective": "Verify the public contract", "non_goals": ["No remote changes"], "acceptance": view["source_commitments"]["acceptance"],
        "permission_scope": {"write_scopes": ["tests/**"], "capabilities": ["filesystem_write"]},
        "stop_conditions": ["Stop when the Goal is inactive"], "terminal_conditions": ["Accepted result returned"]},
        "policy": {"out_of_policy": "reject", "eligible_agents": ["agent-a", "agent-b"], "max_created_todos": 4, "templates": [{"id": "check", "todo": {
            "text": "Verify the public contract", "action_kind": "validate", "target_key": "contract-check",
            "required_write_scopes": ["tests/**"], "required_capabilities": ["filesystem_write"]}}]}}


def _basis(view):
    return {"base_goal_revision": view["intent"]["goal_revision"], "base_intent_digest": view["intent"]["intent_digest"], "base_work_digest": view["work_digest"]}


def test_source_owned_cli_configuration_proposal_commit_and_historical_readback(source_goal, tmp_path, capsys):
    f = source_goal
    code, view = _call(f, tmp_path, capsys, "inspect")
    assert code == 0 and view["intent"] is None
    root = _root(view)
    assert _call(f, tmp_path, capsys, "configure", root, agent=None, operation_id="root")[1]["status"] == "planned"
    assert _call(f, tmp_path, capsys, "inspect")[1]["intent"] is None
    assert _call(f, tmp_path, capsys, "configure", root, agent=None, operation_id="root", execute=True)[0] == 0
    view = _call(f, tmp_path, capsys, "inspect")[1]
    assert _call(f, tmp_path, capsys, "adopt", _basis(view), operation_id="adopt", execute=True)[0] == 0
    proposal = {**_basis(view), "proposal_id": "proposal-1", "template_id": "check", "evidence_refs": ["evidence:contract-gap"], "replan_obligation_id": f["obligation"]}
    code, admission = _call(f, tmp_path, capsys, "propose", proposal, operation_id="proposal-op", execute=True)
    assert code == 0, admission
    assert admission["canonical_effect"] == "none"
    commit = {"proposal_id": "proposal-1", "proposal_digest": admission["proposal"]["digest"]}
    code, committed = _call(f, tmp_path, capsys, "commit", commit, operation_id="commit-op", execute=True)
    assert code == 0, committed
    assert committed["canonical_effect"] == "committed" and committed["goal_revision"] == 2
    assert committed["replan_settlement"] == "pending"
    assert committed["execution_authority_granted"] is False
    replay = _call(f, tmp_path, capsys, "commit", commit, operation_id="commit-op", execute=True)[1]
    assert replay["status"] == "replayed" and replay["original_receipt"] == committed["original_receipt"]
    historical = _call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "commit-op"})[1]
    assert historical["status"] == "found" and historical["historical_only"]
    after = _call(f, tmp_path, capsys, "inspect")[1]
    assert after["intent"]["created_todo_ids"] == [committed["todo_id"]]
    assert after["intent"]["root_intent"] == root["root_intent"]
    assert all(not item["current"] for item in after["peer_adoption"])
    registry = load_project_registry(f["registry"])
    assert registry["goals"][0]["execution_authority"] is False


def test_alias_only_or_retired_history_cannot_authorize_a_current_proposal(source_goal, tmp_path, capsys):
    f = source_goal
    view = _call(f, tmp_path, capsys, "inspect")[1]
    _call(f, tmp_path, capsys, "configure", _root(view), agent=None, operation_id="root", execute=True)
    view = _call(f, tmp_path, capsys, "inspect")[1]
    _call(f, tmp_path, capsys, "adopt", _basis(view), operation_id="adopt", execute=True)
    proposal = {**_basis(view), "proposal_id": "p", "template_id": "check", "evidence_refs": ["evidence:gap"], "replan_obligation_id": f["obligation"]}
    for old_ref in [None, {**f["ref"], "goal_instance_id": "ginst_" + "b" * 32}]:
        rows = [{**row, **({"goal_ref": old_ref} if old_ref else {})} for row in _stall_runs(goal_id=f["ref"]["goal_id"])]
        f["runs"].write_text("".join(json.dumps(row) + "\n" for row in rows))
        code, result = _call(f, tmp_path, capsys, "propose", proposal, operation_id="p-op", execute=True)
        assert code == 1 and "source-owned replan" in result["reason"]
    assert _call(f, tmp_path, capsys, "inspect")[1]["intent"]["proposals"] == []


def test_goal_recreation_fences_new_mutations_but_preserves_original_receipt(source_goal, tmp_path, capsys):
    f = source_goal
    view = _call(f, tmp_path, capsys, "inspect")[1]
    _call(f, tmp_path, capsys, "configure", _root(view), agent=None, operation_id="root", execute=True)
    recreated = recreate_goal_instance(RecreateGoalRequest(registry_path=f["registry"], goal_id=f["ref"]["goal_id"],
        goal_instance_id=f["ref"]["goal_instance_id"], operation_id="recreate"))
    assert recreated["ok"]
    code, old = _call(f, tmp_path, capsys, "adopt", {"base_goal_revision": 1, "base_intent_digest": "old", "base_work_digest": "old"}, operation_id="stale", execute=True)
    assert code == 1 and old["reason_code"] == "stale_goal_instance"
    historical = _call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "root"})[1]
    assert historical["historical_only"] and historical["status"] == "found"
    current = _call(f, tmp_path, capsys, "inspect", instance=recreated["goal_ref"]["goal_instance_id"])[1]
    assert current["current_lifetime"] is False


def test_timed_out_commit_cannot_arrive_after_source_goal_recreation(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.goals import goal_amendment_runtime
    from loopx.control_plane.effect_runtime import effect_runtime_result

    f = source_goal
    view = _call(f, tmp_path, capsys, "inspect")[1]
    assert _call(f, tmp_path, capsys, "configure", _root(view), agent=None, operation_id="root", execute=True)[0] == 0
    view = _call(f, tmp_path, capsys, "inspect")[1]
    assert _call(f, tmp_path, capsys, "adopt", _basis(view), operation_id="adopt", execute=True)[0] == 0
    proposal = {**_basis(view), "proposal_id": "p", "template_id": "check", "evidence_refs": ["evidence:gap"], "replan_obligation_id": f["obligation"]}
    admission = _call(f, tmp_path, capsys, "propose", proposal, operation_id="proposal", execute=True)[1]
    captured = []

    def delayed(method, params, **kwargs):
        assert method == "goal.amendment.execute"
        if params["operation"] != "commit":
            return effect_runtime_result(method, params, **kwargs)
        captured.append(params)
        raise TimeoutError("synthetic transport response timeout")

    with monkeypatch.context() as patch:
        patch.setattr(goal_amendment_runtime, "effect_runtime_result", delayed)
        assert _call(f, tmp_path, capsys, "commit", {"proposal_id": "p", "proposal_digest": admission["proposal"]["digest"]}, operation_id="late-commit", execute=True)[0] == 1
    assert len(captured) == 1
    recreated = recreate_goal_instance(RecreateGoalRequest(registry_path=f["registry"], goal_id=f["ref"]["goal_id"],
        goal_instance_id=f["ref"]["goal_instance_id"], operation_id="recreate"))
    assert recreated["ok"]
    # The real runtime receives the original request after the caller's Python
    # locks were released. Source facts in that queued request are now stale.
    late = effect_runtime_result("goal.amendment.execute", captured[0])
    assert late["status"] == "failed", late
    current = _call(f, tmp_path, capsys, "inspect", instance=recreated["goal_ref"]["goal_instance_id"])[1]
    assert current["intent"]["created_todo_ids"] == []
    history = _call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "late-commit"})[1]
    assert history["status"] == "missing"


@pytest.mark.parametrize("claim_agent", ["agent-a", "agent-b"])
def test_claimed_successor_is_consumed_by_existing_replan_owner(source_goal, tmp_path, capsys, claim_agent):
    f = source_goal
    view = _call(f, tmp_path, capsys, "inspect")[1]
    root = _root(view)
    assert _call(f, tmp_path, capsys, "configure", root, agent=None, operation_id="root", execute=True)[0] == 0
    view = _call(f, tmp_path, capsys, "inspect")[1]
    assert _call(f, tmp_path, capsys, "adopt", _basis(view), operation_id="adopt", execute=True)[0] == 0
    proposal = {**_basis(view), "proposal_id": "p", "template_id": "check", "evidence_refs": ["evidence:gap"], "replan_obligation_id": f["obligation"]}
    admitted = _call(f, tmp_path, capsys, "propose", proposal, operation_id="propose", execute=True)[1]
    committed = _call(f, tmp_path, capsys, "commit", {"proposal_id": "p", "proposal_digest": admitted["proposal"]["digest"]}, operation_id="commit", execute=True)[1]
    assert committed["replan_settlement"] == "pending"
    view = committed["current_projection"]
    assert any(row["state"] == "pending" and row["agent_id"] == "agent-a" for row in view["replan_observations"])
    adopted = _call(f, tmp_path, capsys, "adopt", _basis(view), agent=claim_agent, operation_id="adopt-new", execute=True)[1]
    view = adopted["current_projection"]
    claim = {**_basis(view), "todo_id": committed["todo_id"], "expected_provider_revision": view["provider_revision"], "lease_request": {"idempotency_key": "amendment-claim", "expected_version": None, "ttl_seconds": 300}}
    code, claimed = _call(f, tmp_path, capsys, "claim", claim, agent=claim_agent, operation_id="claim", execute=True)
    assert code == 0, claimed
    current = claimed["current_projection"]
    task = next(row for row in current["work_items"] if row["todo_id"] == committed["todo_id"])
    assert task["claimed_by"] == claim_agent
    assert task.get("replan_obligation_id") == (f["obligation"] if claim_agent == "agent-a" else None)
    observed = [row for row in current["replan_observations"] if row["obligation_id"] == f["obligation"] and row["agent_id"] == "agent-a"]
    assert observed and observed[0]["state"] == ("settled" if claim_agent == "agent-a" else "pending")
    if claim_agent == "agent-a":
        assert observed[0]["acknowledgement"]["source"] == "todo_replan_successor_transition"
    replay = _call(f, tmp_path, capsys, "claim", claim, agent=claim_agent, operation_id="claim", execute=True)[1]
    assert replay["status"] == "replayed" and replay["original_receipt"] == claimed["original_receipt"]
    # Reconfirming an existing claim/lease under a fresh operation is a
    # successful no-op, including after the original replan was consumed.
    unchanged = {**claim, **_basis(current), "expected_provider_revision": current["provider_revision"]}
    code, noop = _call(f, tmp_path, capsys, "claim", unchanged, agent=claim_agent, operation_id="same-owner-noop", execute=True)
    assert code == 0 and noop["ok"] is True and noop["status"] == "no_change", noop
    assert noop["changed"] is False
    assert noop["current_projection"]["work_digest"] == current["work_digest"]
    assert noop["current_projection"]["intent"] == current["intent"]
    # An already-consumed obligation cannot authorize another new proposal.
    next_proposal = {**proposal, **_basis(current), "proposal_id": "not-a-new-obligation"}
    assert _call(f, tmp_path, capsys, "propose", next_proposal, operation_id="duplicate-obligation", execute=True)[0] == 1
    original = _call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "commit"})[1]
    assert original["receipts"][0]["result"]["replan_settlement"] == "pending"


@pytest.mark.parametrize("strategy", ["off", "evidence_conditioned_v1"])
def test_periodic_successor_readback_uses_live_machine_stride(source_goal, tmp_path, capsys, strategy):
    from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
    from loopx.capabilities.machine_configuration.store import configure_machine_configuration
    from loopx.capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal

    f = source_goal
    # This retained source inventory reviews twenty durable records. Give it
    # distinct advancing observations so a stall cannot replace that trigger.
    rows = _stall_runs(goal_id=f["ref"]["goal_id"], count=20)
    for index, row in enumerate(rows):
        row["goal_ref"] = f["ref"]
        row["progress_observation"].update(result_class="advanced",
            surface_id=f"coverage-{index}", hypothesis_id=f"hypothesis-{index}")
    f["runs"].write_text("".join(json.dumps(row) + "\n" for row in rows))
    owner = build_builtin_machine_configuration_registry()
    configuration = {"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "todo_replan_cadence": {"schema_version": "todo_replan_cadence_machine_defaults_v2",
            "count_unit": "effective_turns", "count": 6, "stride_strategy": strategy}}}
    preview = configure_machine_configuration(runtime_root=f["runtime"], registry=owner, configuration=configuration)
    configure_machine_configuration(runtime_root=f["runtime"], registry=owner, configuration=configuration,
        execute=True, expected_plan_revision=preview["plan_revision"])
    goal = load_project_registry(f["registry"])["goals"][0]
    assert "stride_strategy" not in goal.get("execution_profile", {})
    effective = resolve_todo_replan_cadence_goal(goal, f["runtime"])
    assert effective["execution_profile"].get("stride_strategy", "off") == strategy
    derived = _derive_open_replan_obligation_inventory(runtime_root=f["runtime"], goal_id=f["ref"]["goal_id"],
        goal=goal, state_text="", goal_ref=f["ref"])
    candidates = list(derived.get("autonomous_replan_obligations_by_agent", {}).values())
    assert candidates and all(any(trigger["kind"] == "periodic_review_due" for trigger in item["triggers"])
        for item in candidates)
    inventory = _open_replan_obligation_inventory(goal_id=f["ref"]["goal_id"],
        registered_agents=["agent-a", "agent-b"], status_item=derived)
    f["obligation"] = next(row["obligation_id"] for row in inventory if "agent-a" in row["bound_agent_ids"])

    view = _call(f, tmp_path, capsys, "inspect")[1]
    assert _call(f, tmp_path, capsys, "configure", _root(view), agent=None,
        operation_id="root", execute=True)[0] == 0
    view = _call(f, tmp_path, capsys, "inspect")[1]
    assert _call(f, tmp_path, capsys, "adopt", _basis(view), operation_id="adopt", execute=True)[0] == 0
    proposal = {**_basis(view), "proposal_id": "periodic", "template_id": "check",
        "evidence_refs": ["evidence:gap"], "replan_obligation_id": f["obligation"]}
    admitted = _call(f, tmp_path, capsys, "propose", proposal, operation_id="propose", execute=True)[1]
    committed = _call(f, tmp_path, capsys, "commit", {"proposal_id": "periodic",
        "proposal_digest": admitted["proposal"]["digest"]}, operation_id="commit", execute=True)[1]
    view = committed["current_projection"]
    adopted = _call(f, tmp_path, capsys, "adopt", _basis(view), operation_id="adopt-new", execute=True)[1]
    view = adopted["current_projection"]
    code, claimed = _call(f, tmp_path, capsys, "claim", {**_basis(view), "todo_id": committed["todo_id"],
        "expected_provider_revision": view["provider_revision"],
        "lease_request": {"idempotency_key": "periodic-claim", "expected_version": None, "ttl_seconds": 300}},
        operation_id="claim", execute=True)
    assert code == 0, claimed
    observation = next(row for row in claimed["current_projection"]["replan_observations"]
        if row["obligation_id"] == f["obligation"] and row["agent_id"] == "agent-a")
    if strategy == "off":
        assert observation["state"] == "settled"
        assert observation["acknowledgement"]["source"] == "todo_replan_successor_transition"
    else:
        assert observation["state"] == "pending"
        assert observation["acknowledgement"] is None
    # The source observation neither fabricates a decision nor rewrites history.
    assert f["runs"].read_text() == "".join(json.dumps(row) + "\n" for row in rows)


def test_historical_receipt_does_not_read_current_stride_configuration(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.capabilities.todo_replan_cadence import machine_defaults

    f = source_goal
    view = _call(f, tmp_path, capsys, "inspect")[1]
    code, configured = _call(f, tmp_path, capsys, "configure", _root(view),
        agent=None, operation_id="root", execute=True)
    assert code == 0, configured

    def unavailable(*_args, **_kwargs):
        raise OSError("synthetic current configuration unavailable")

    monkeypatch.setattr(machine_defaults, "resolve_todo_replan_cadence_goal", unavailable)
    code, historical = _call(f, tmp_path, capsys, "receipt", {"receipt_operation_id": "root"})
    assert code == 0 and historical["status"] == "found" and historical["historical_only"], historical


def test_root_initialization_cannot_shrink_source_goal_commitments(source_goal, tmp_path, capsys):
    f = source_goal
    view = _call(f, tmp_path, capsys, "inspect")[1]
    document = _root(view)
    document["root_intent"]["objective"] = "Replace the original Goal with an easier objective"
    code, result = _call(f, tmp_path, capsys, "configure", document, agent=None, operation_id="bad-root", execute=True)
    assert code == 1, result
    assert _call(f, tmp_path, capsys, "inspect")[1]["intent"] is None


def test_existing_causal_binding_allows_lease_reacquisition_after_replan_settlement(source_goal, tmp_path, capsys, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from loopx.control_plane.goals import goal_amendment_runtime

    f = source_goal
    test_claimed_successor_is_consumed_by_existing_replan_owner(f, tmp_path, capsys, "agent-a")
    view = _call(f, tmp_path, capsys, "inspect")[1]
    assert view["replan_observations"][0]["state"] == "settled"
    # Advance only the trusted clock input; the real lease decision and provider
    # must issue a new epoch. No mocked acceptance or active user Goal is used.
    later = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(goal_amendment_runtime, "now_utc_iso", lambda: later)
    code, continued = _call(f, tmp_path, capsys, "claim", {
        **_basis(view), "todo_id": view["intent"]["created_todo_ids"][0],
        "expected_provider_revision": view["provider_revision"],
        "lease_request": {"idempotency_key": "continuation", "expected_version": 1, "ttl_seconds": 300},
    }, operation_id="claim-continuation", execute=True)
    assert code == 0 and continued["status"] == "applied", continued
    assert continued["lease"]["version"] == 2 and continued["lease"]["lease_epoch"] == 2
    assert continued["current_projection"]["intent"] == view["intent"]
    assert continued["current_projection"]["replan_observations"][0]["state"] == "settled"


@pytest.mark.parametrize("source_goal", ["file"], indirect=True)
def test_missing_authority_is_not_successful_inspect_or_configuration(source_goal, tmp_path, capsys):
    f = source_goal
    view = _call(f, tmp_path, capsys, "inspect")[1]
    documents = list((f["runtime"] / "authority/file-v0").glob("authority-store-*.json"))
    assert len(documents) == 1
    saved = documents[0].read_bytes()
    documents[0].unlink()  # Only this disposable fixture's provider document.
    try:
        for operation, parameters in [("inspect", None), ("configure", _root(view))]:
            code, unavailable = _call(f, tmp_path, capsys, operation, parameters,
                agent=None, operation_id="restore-root" if operation == "configure" else None, execute=operation == "configure")
            assert code == 1 and unavailable["ok"] is False, unavailable
            assert not documents[0].exists()
    finally:
        documents[0].write_bytes(saved)
    # Original storage restoration permits the original request, not new state
    # inferred from an absent provider or mutable Markdown.
    code, recovered = _call(f, tmp_path, capsys, "configure", _root(view), agent=None, operation_id="restore-root", execute=True)
    assert code == 0 and recovered["status"] == "applied", recovered


@pytest.mark.parametrize("gate_case", ["open_ascii", "closing_unicode", "malformed_json", "invalid_utf8"])
def test_gate_codec_preserves_native_amendment_admission(source_goal, tmp_path, capsys, monkeypatch, gate_case):
    """A non-UTF-8 host must decode the source gate without bypassing its refusal."""
    from loopx.control_plane.goals.source_session_turn_effects import source_turn_effect_gate_path

    f = source_goal
    code, before = _call(f, tmp_path, capsys, "inspect")
    assert code == 0 and before["intent"] is None
    gate_path = source_turn_effect_gate_path(f["registry"], f["ref"]["goal_id"])
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    original = gate_path.read_bytes() if gate_path.exists() else None
    gate = {"schema_version": "loopx_source_turn_effect_gate_v1", "state": "open", "goal_ref": f["ref"]}
    if gate_case == "closing_unicode":
        gate = {"schema_version": "loopx_source_turn_effect_gate_v1", "state": "closing",
            "retired_goal_ref": f["ref"], "new_goal_ref": {**f["ref"], "goal_instance_id": "ginst_" + "b" * 32},
            "operation_id": "recreate-复核", "request_digest": "sha256:" + "a" * 64}
    raw = json.dumps(gate, ensure_ascii=False).encode("utf-8")
    if gate_case == "malformed_json":
        raw = b'{"state":'
    elif gate_case == "invalid_utf8":
        raw = b'{"state":"\xff"}'
    gate_path.write_bytes(raw)
    read_text = Path.read_text

    def read_with_ascii_default(path, *args, **kwargs):
        if path == gate_path and not args and kwargs.get("encoding") is None:
            kwargs["encoding"] = "ascii"
        return read_text(path, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "read_text", read_with_ascii_default)
            code, result = _call(f, tmp_path, capsys, "configure", _root(before),
                agent=None, operation_id="codec-root", execute=True)
        if gate_case == "open_ascii":
            assert code == 0 and result["status"] == "applied", result
        else:
            assert code == 1 and result["ok"] is False, result
            if gate_case == "closing_unicode":
                assert result.get("reason_code") == "goal_retirement_in_progress", result
                assert result["execution_authority_granted"] is False
    finally:
        if original is None:
            gate_path.unlink()
        else:
            gate_path.write_bytes(original)
    code, after = _call(f, tmp_path, capsys, "inspect")
    assert code == 0, after
    if gate_case == "open_ascii":
        assert after["intent"]["root_intent"] == _root(before)["root_intent"]
    else:
        assert after["provider_revision"] == before["provider_revision"]
        assert after["intent"] is None
