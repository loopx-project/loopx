from copy import deepcopy
import json

import pytest

from tests.capabilities.test_explore_turn_context import registry
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.todos import add_goal_todo, update_goal_todo, list_goal_todos
from loopx.capabilities.explore.result_writeback import (
    prepare_result_attachment,
    deliver_result_attachment,
)
from loopx.capabilities.explore.turn_context import explore_turn_context
from loopx.capabilities.explore.result_log import (
    explore_result_log_path,
    load_explore_result_events,
)

ATTACHMENT = dict(
    schema_version="explore_result_attachment_v0",
    node_id="prefix-bound",
    question="Does a finite prefix establish the tail bound?",
    applicability="Finite prefix only; no uniform tail estimate.",
    input_revision="fixture-v1",
    observation="A divergent tail shares the tested prefix.",
    interpretation="Use a uniform bound before transferring the result.",
    status="refuted",
    evidence_refs=["validation:counterexample-1"],
)


def fixture(tmp_path, enabled=True):
    path = registry(tmp_path, graph=enabled, planning=enabled)
    todo = add_goal_todo(
        registry_path=path,
        goal_id="research",
        text="Establish the tail bound",
        role="agent",
        claimed_by="worker",
        agent_id="worker",
    )
    return dict(
        registry_path=path,
        runtime_root=tmp_path / "runtime",
        goal_id="research",
        agent_id="worker",
        todo_id=todo["todo_id"],
        turn_instance_id="turn-one",
    )


def payload():
    return dict(
        explore_result=deepcopy(ATTACHMENT),
        generated_at="2026-01-01T00:00:00Z",
        appended=True,
        dry_run=False,
        settlement_identity={"effect_id": "research:worker:turn-one"},
    )


def test_real_result_link_read_and_replay(tmp_path):
    args = fixture(tmp_path)
    context = explore_turn_context(**{
        key: args[key] for key in ("registry_path", "runtime_root", "goal_id", "agent_id")
    })
    attachment = context["graph"]["result_attachment_template"]
    with pytest.raises((ValueError, EffectRuntimeRejected)):
        prepare_result_attachment(attachment, **args)
    assert not explore_result_log_path(args["runtime_root"], "research").exists()
    assert set(attachment) == set(ATTACHMENT) - {"node_id"}
    attachment.update(ATTACHMENT)
    prepare_result_attachment(attachment, **args)
    assert not explore_result_log_path(args["runtime_root"], "research").exists()
    first = deliver_result_attachment(payload=payload(), **args)
    assert first["ok"], first
    second = deliver_result_attachment(payload=payload(), **args)
    assert second["ok"] and second["idempotent_replay"], second
    events = load_explore_result_events(
        explore_result_log_path(args["runtime_root"], "research")
    )
    assert len(events) == 2
    supplement = payload()
    supplement["explore_result_recorded_at"] = supplement["generated_at"]
    supplement["generated_at"] = "2026-01-02T00:00:00Z"
    assert deliver_result_attachment(payload=supplement, **args)["idempotent_replay"]
    assert (
        len(
            load_explore_result_events(
                explore_result_log_path(args["runtime_root"], "research")
            )
        )
        == 2
    )
    context = explore_turn_context(
        **{
            key: args[key]
            for key in ("registry_path", "runtime_root", "goal_id", "agent_id")
        }
    )
    assert context["graph"]["writeback_results"][0]["finding_id"] == first["finding_id"]
    assert (
        ATTACHMENT["interpretation"]
        in context["graph"]["writeback_results"][0]["summary"]
    )
    audit = context["harness"]["selected_branches"][0]["typed_evidence_audit"]
    assert ATTACHMENT["node_id"] in audit["requested_node_refs"]
    assert audit["findings"][0]["status"] == "refuted"


def test_todo_additive_owner_preserves_other_links_and_rejects_overflow(tmp_path):
    args = fixture(tmp_path)
    keys = {
        key: args[key] for key in ("registry_path", "goal_id", "todo_id", "agent_id")
    }
    update_goal_todo(**keys, explore_result_node_refs=["prior"])
    for _ in range(2):
        update_goal_todo(**keys, append_explore_result_node_refs=["prefix-bound"])
    rows = list_goal_todos(registry_path=args["registry_path"], goal_id="research")[
        "todos"
    ]
    assert rows[0]["explore_result_node_refs"] == ["prior", "prefix-bound"]
    with pytest.raises(ValueError):
        update_goal_todo(
            **keys, append_explore_result_node_refs=[f"node{i}" for i in range(8)]
        )
    with pytest.raises(ValueError):
        update_goal_todo(
            **keys,
            explore_result_node_refs=[],
            append_explore_result_node_refs=["other"],
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "disabled",
        "foreign-agent",
        "unknown-todo",
        "missing-turn",
        "invalid-evidence",
        "unknown-field",
    ],
)
def test_invalid_attachment_never_writes_graph(tmp_path, mutation):
    args = fixture(tmp_path, enabled=mutation != "disabled")
    attachment = deepcopy(ATTACHMENT)
    if mutation == "foreign-agent":
        args["agent_id"] = "other"
    if mutation == "unknown-todo":
        args["todo_id"] = "todo_missing"
    if mutation == "missing-turn":
        args["turn_instance_id"] = ""
    if mutation == "invalid-evidence":
        attachment["evidence_refs"] = ["/private/raw.log"]
    if mutation == "unknown-field":
        attachment["score"] = 1
    with pytest.raises((ValueError, EffectRuntimeRejected)):
        prepare_result_attachment(attachment, **args)
    assert not explore_result_log_path(args["runtime_root"], "research").exists()


def test_partial_link_failure_replays_without_duplicate_or_primary_rollback(
    tmp_path, monkeypatch
):
    args = fixture(tmp_path)
    import loopx.capabilities.explore.result_writeback as module

    original = module.update_goal_todo

    def interrupt(**kwargs):
        if not kwargs.get("dry_run"):
            raise OSError("simulated interruption after graph append")
        return original(**kwargs)

    monkeypatch.setattr(module, "update_goal_todo", interrupt)
    first = deliver_result_attachment(payload=payload(), **args)
    assert not first["ok"] and first["primary_committed"] and first["retryable"]
    assert first["graph"]["appended_event_count"] == 2
    monkeypatch.setattr(module, "update_goal_todo", original)
    second = deliver_result_attachment(payload=payload(), **args)
    assert second["ok"] and second["graph"]["appended_event_count"] == 0


@pytest.mark.parametrize("case", ["disabled", "null", "invalid", "conflict", "off-without-result",
                                "path-disabled", "path-missing", "path-no-evidence", "path-overflow"])
def test_real_cli_inline_validation_precedes_primary_commit(tmp_path, case):
    from tests.control_plane.test_quota_settlement_cli import (
        _write_fixture, _run_cli, GOAL_ID, AGENT_ID, TODO_ID, TURN_ID,
    )

    project, runtime, path = _write_fixture(tmp_path)
    config = json.loads(path.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": case not in {"disabled", "off-without-result", "path-disabled"}}
    path.write_text(json.dumps(config))
    rc, claimed = _run_cli(path, runtime, "todo", "claim", "--goal-id", GOAL_ID,
                          "--todo-id", TODO_ID, "--agent-id", AGENT_ID,
                          "--claimed-by", AGENT_ID, cwd=project)
    assert rc == 0, claimed
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", TURN_ID)
    rc, guard = _run_cli(path, runtime, "quota", "should-run", "--codex-app",
                        *binding, "--scan-path", str(project), cwd=project)
    assert rc == 0, guard
    packet = {"schema_version": "goal_vision_replan_contract_v0",
              "state": "vision_patch_proposed",
              "vision_patch": {"acceptance_summary": "Require a uniform tail bound."}}
    if case != "off-without-result":
        packet["explore_result"] = deepcopy(ATTACHMENT)
    if case == "null":
        packet["explore_result"] = None
    if case == "invalid":
        packet["explore_result"]["evidence_refs"] = []
    if case.startswith("path-"):
        packet["explore_result"] = {
            key: ATTACHMENT[key] for key in
            ("node_id", "question", "applicability", "input_revision", "status")
        }
        packet["explore_result"]["schema_version"] = "explore_result_from_path_delta_v0"
        if case != "path-missing":
            packet["path_delta"] = {
                "schema_version": "goal_path_delta_v0", "outcome": "continue",
                "prior_assumption": "A finite prefix might bound the tail.",
                "observed_reality": ATTACHMENT["observation"],
                "changed": [ATTACHMENT["interpretation"]],
                "evidence_refs": ATTACHMENT["evidence_refs"],
            }
            if case == "path-no-evidence":
                packet["path_delta"]["evidence_refs"] = []
            if case == "path-overflow":
                packet["path_delta"]["observed_reality"] = "x" * 321
    source = tmp_path / "vision.json"
    source.write_text(json.dumps(packet))
    extra = ()
    if case == "conflict":
        other = tmp_path / "other.json"
        other.write_text(json.dumps({**ATTACHMENT, "interpretation": "Transfer without a uniform bound."}))
        extra = ("--explore-result-json", str(other))
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    before = index.read_bytes() if index.exists() else b""
    rc, result = _run_cli(path, runtime, "refresh-state", *binding,
                         "--classification", "validated_change", "--delivery-batch-scale", "implementation",
                         "--delivery-outcome", "outcome_progress", "--no-global-sync",
                         "--suppress-external-sinks", "--agent-vision-json", str(source), *extra, cwd=project)
    if case == "off-without-result":
        assert rc == 0 and result["appended"], result
        assert "explore_result_delivery" not in result
        rc, context = _run_cli(path, runtime, "explore", "turn-context", "--goal-id", GOAL_ID,
                              "--agent-id", AGENT_ID, cwd=project)
        assert rc == 0 and context["graph"] is None and context["harness"] is None
    else:
        assert rc == 1 and not result["appended"], result
        assert (index.read_bytes() if index.exists() else b"") == before
    assert not explore_result_log_path(runtime, GOAL_ID).exists()


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("source_mode", ["file", "vision", "both", "path_delta", "wide_path_delta", "linked_path_delta"])
def test_real_cli_normal_writeback_persists_attachment_and_rejects_conflicting_retry(
    tmp_path, terminal, interrupted, source_mode, monkeypatch, capsys,
):
    from tests.control_plane.test_quota_settlement_cli import (
        _write_fixture,
        _run_cli,
        GOAL_ID,
        AGENT_ID,
        TODO_ID,
        TURN_ID,
    )

    project, runtime, path = _write_fixture(tmp_path)
    config = json.loads(path.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    config["goals"][0]["spawn_policy"] = {
        "allowed": False,
        "explore_harness": {"enabled": True},
    }
    path.write_text(json.dumps(config))
    rc, claimed = _run_cli(
        path,
        runtime,
        "todo",
        "claim",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--agent-id",
        AGENT_ID,
        "--claimed-by",
        AGENT_ID,
        cwd=project,
    )
    assert rc == 0, claimed
    if source_mode == "linked_path_delta":
        from loopx.capabilities.explore.result_log import (
            append_explore_result_event, build_explore_node_event,
        )
        append_explore_result_event(explore_result_log_path(runtime, GOAL_ID),
            build_explore_node_event(goal_id=GOAL_ID, node_id=ATTACHMENT["node_id"],
                node_kind="question", title=ATTACHMENT["question"],
                summary=ATTACHMENT["applicability"], status="open"))
        update_goal_todo(registry_path=path, goal_id=GOAL_ID, todo_id=TODO_ID,
            agent_id=AGENT_ID, explore_result_node_refs=[ATTACHMENT["node_id"]])
    terminal_proof = {}
    if terminal:
        from canonical_authority_fixture import initialize_canonical_authority
        from loopx.control_plane.coordination.runtime_shadow import (
            build_todo_runtime_shadow_projection,
        )
        from loopx.control_plane.work_items.task_lease import acquire_task_lease
        from loopx.todos import complete_goal_todo

        todos = list_goal_todos(registry_path=path, goal_id=GOAL_ID)["todos"]
        initialize_canonical_authority(
            runtime,
            GOAL_ID,
            build_todo_runtime_shadow_projection(
                goal_id=GOAL_ID, todos=todos, handoff_mode="hard_lease"
            ),
            state_path=project / config["goals"][0]["state_file"],
            provider="file",
        )
        lease = acquire_task_lease(
            registry_path=path,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            todo_id=TODO_ID,
            owner=AGENT_ID,
            idempotency_key="cli-terminal-result",
        )
        terminal_proof = dict(
            task_lease_idempotency_key="cli-terminal-result",
            task_lease_expected_version=lease["lease"]["version"],
        )
    binding = (
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )
    rc, guard = _run_cli(
        path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0, guard
    if terminal:
        complete_goal_todo(
            registry_path=path,
            goal_id=GOAL_ID,
            todo_id=TODO_ID,
            agent_id=AGENT_ID,
            evidence="Synthetic validation passed",
            **terminal_proof,
        )
    source = tmp_path / "result.json"
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    template = plan["ordered_steps"][1]["optional_attachments"][0]["attachment_template"]
    assert set(template) == set(ATTACHMENT) - {"node_id"}
    template.update(ATTACHMENT)
    source.write_text(json.dumps(template))
    vision = tmp_path / "vision.json"
    packet = {
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Require a uniform tail bound."},
        "explore_result": template,
    }
    if source_mode in {"path_delta", "wide_path_delta", "linked_path_delta"}:
        packet["path_delta"] = {
            "schema_version": "goal_path_delta_v0", "outcome": "continue",
            "prior_assumption": "A finite prefix might establish the bound.",
            "observed_reality": ATTACHMENT["observation"],
            "changed": [ATTACHMENT["interpretation"]],
            "evidence_refs": ATTACHMENT["evidence_refs"],
        }
        packet["explore_result"] = {
            key: ATTACHMENT[key] for key in
            ("node_id", "question", "applicability", "input_revision", "status")
        }
        packet["explore_result"]["schema_version"] = "explore_result_from_path_delta_v0"
        if source_mode == "linked_path_delta":
            for field in ("question", "applicability"):
                del packet["explore_result"][field]
        if source_mode == "wide_path_delta":
            # Independent scope/tail oracle, exceeding the old 1200-character
            # stored-summary cap and using all nine legal route entries.
            packet["explore_result"]["input_revision"] = "r" * 160
            packet["explore_result"]["applicability"] = "Finite prefix only; ".ljust(200, "a")
            packet["path_delta"]["observed_reality"] = ATTACHMENT["observation"].ljust(320, "o")
            for kind in ("retained", "changed", "stopped"):
                packet["path_delta"][kind] = [f"{kind}-{i}: ".ljust(120, str(i)) for i in range(3)]
            packet["path_delta"]["stopped"][-1] = "Do not transfer without a uniform tail bound.".rjust(120, "s")
            packet["path_delta"]["evidence_refs"] = [*ATTACHMENT["evidence_refs"], ".loopx/evidence/probe.json"]
            packet["explore_result"]["evidence_refs"] = ATTACHMENT["evidence_refs"]
    vision.write_text(json.dumps(packet))
    attachment_args = ()
    if source_mode in {"file", "both"}:
        attachment_args += ("--explore-result-json", str(source))
    if source_mode in {"vision", "both", "path_delta", "wide_path_delta", "linked_path_delta"}:
        attachment_args += ("--agent-vision-json", str(vision))
    args = (
        "refresh-state",
        *binding,
        "--classification",
        "validated_change",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--no-global-sync",
        "--suppress-external-sinks",
        *attachment_args,
    )
    if interrupted:
        from loopx.cli import main
        import loopx.capabilities.explore.result_writeback as module
        original = module.update_goal_todo
        def interrupt_link(**kwargs):
            if not kwargs.get("dry_run"):
                raise OSError("simulated interruption after graph append")
            return original(**kwargs)
        # Exercise the real CLI/primary record and real owner, interrupting only
        # the graph-to-Todo link. Recovery below runs in a fresh CLI process.
        with monkeypatch.context() as patch:
            patch.chdir(project)
            patch.setattr(module, "update_goal_todo", interrupt_link)
            rc = main(["--registry", str(path), "--runtime-root", str(runtime),
                       "--format", "json", *args])
            first = json.loads(capsys.readouterr().out)
        assert rc == 1 and first["appended"], first
        assert first["explore_result_delivery"]["primary_committed"], first
        assert not first["explore_result_delivery"]["ok"], first
        assert "replay this exact refresh" in first["error"]
    else:
        rc, first = _run_cli(path, runtime, *args, cwd=project)
        assert rc == 0, first
        assert first["explore_result_delivery"]["ok"] and first["appended"], first
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    before = index.read_bytes()
    rc, replay = _run_cli(path, runtime, *args, cwd=project)
    assert rc == 0 and replay["idempotent_replay"], replay
    assert replay["explore_result_delivery"]["ok"], replay
    assert index.read_bytes() == before
    # Follow the owning hook's required read in a new process. Presence in the
    # receiver packet is transport evidence, not a claim of model adoption.
    import shlex
    import subprocess
    import sys
    from loopx.capabilities.explore.turn_context import extend_turn_start_dispatch
    hook = extend_turn_start_dispatch(
        {}, registry_path=path, runtime_root=runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
    )
    command = shlex.split(hook["required_reads"][0]["command"])
    read = subprocess.run([sys.executable, "-m", "loopx.cli", *command[1:]],
                          capture_output=True, text=True, check=True)
    received = json.loads(read.stdout)
    finding = received["graph"]["writeback_results"][0]
    assert finding["finding_id"] == replay["explore_result_delivery"]["finding_id"]
    assert ATTACHMENT["observation"] in finding["summary"]
    if source_mode != "wide_path_delta":
        assert ATTACHMENT["applicability"] in finding["summary"]
    if source_mode == "wide_path_delta":
        assert len(finding["summary"]) > 1200
        assert packet["explore_result"]["input_revision"] in finding["summary"]
        assert packet["explore_result"]["applicability"] in finding["summary"]
        assert packet["path_delta"]["observed_reality"] in finding["summary"]
        for kind in ("retained", "changed", "stopped"):
            for item in packet["path_delta"][kind]:
                assert item in finding["summary"]
        assert finding["evidence_refs"] == ATTACHMENT["evidence_refs"]
        assert len(finding["summary"]) <= 2000
    assert index.read_bytes() == before
    changed = {**ATTACHMENT, "interpretation": "A different conclusion."}
    if source_mode in {"path_delta", "wide_path_delta", "linked_path_delta"}:
        packet["path_delta"]["observed_reality"] = "A changed observation on retry."
        vision.write_text(json.dumps(packet))
    elif source_mode == "vision":
        vision.write_text(json.dumps({**packet, "explore_result": changed}))
    else:
        source.write_text(json.dumps(changed))
    rc, conflict = _run_cli(path, runtime, *args, cwd=project)
    assert rc == 1, conflict
    assert index.read_bytes() == before
    assert (
        len(load_explore_result_events(explore_result_log_path(runtime, GOAL_ID))) == 2
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_delivery_keeps_existing_refs_and_receiver_reads_scoped_result(
    tmp_path, provider
):
    from tests.control_plane.test_native_todo_planning_update import (
        fixture as canonical_fixture,
        records,
    )

    path, _state = canonical_fixture(tmp_path, True, provider)
    config = json.loads(path.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    config["goals"][0]["spawn_policy"] = {
        "allowed": False,
        "explore_harness": {"enabled": True},
    }
    path.write_text(json.dumps(config))
    args = dict(
        registry_path=path,
        runtime_root=tmp_path / "runtime",
        goal_id="goal-a",
        agent_id="agent-a",
        todo_id="todo_target",
        turn_instance_id="turn-one",
    )
    update_goal_todo(
        registry_path=path,
        goal_id="goal-a",
        agent_id="agent-a",
        todo_id="todo_target",
        explore_result_node_refs=["prior-question"],
    )
    before = records(path)["todo_other"]
    result = deliver_result_attachment(payload=payload(), **args)
    assert result["ok"], result.get("error", result)
    assert records(path)["todo_target"]["explore_result_node_refs"] == [
        "prior-question",
        "prefix-bound",
    ]
    assert records(path)["todo_other"] == before
    assert deliver_result_attachment(payload=payload(), **args)["idempotent_replay"]


def test_later_observation_preserves_question_state_and_scoped_history(tmp_path):
    from loopx.capabilities.explore.result_log import (
        append_explore_result_event,
        build_explore_node_event,
    )

    args = fixture(tmp_path)
    first = deliver_result_attachment(payload=payload(), **args)
    assert first["ok"]
    log = explore_result_log_path(args["runtime_root"], "research")
    append_explore_result_event(
        log,
        build_explore_node_event(
            goal_id="research",
            node_id="prefix-bound",
            node_kind="question",
            title=ATTACHMENT["question"],
            summary=ATTACHMENT["applicability"],
            status="resolved",
        ),
    )
    second = payload()
    second["generated_at"] = "2026-01-02T00:00:00Z"
    second["settlement_identity"]["effect_id"] = "research:worker:turn-two"
    second["explore_result"].update(
        status="tentative",
        input_revision="fixture-v2",
        observation="The prerequisite did not build.",
        interpretation="No conclusion about the bound.",
    )
    result = deliver_result_attachment(
        payload=second, **{**args, "turn_instance_id": "turn-two"}
    )
    assert result["ok"], result.get("error", result)
    context = explore_turn_context(
        **{
            key: args[key]
            for key in ("registry_path", "runtime_root", "goal_id", "agent_id")
        }
    )
    assert context["graph"]["recent_nodes"][0]["status"] == "resolved"
    assert {row["status"] for row in context["graph"]["writeback_results"]} == {
        "tentative",
        "refuted",
    }
    bad = {**ATTACHMENT, "applicability": "Different question scope"}
    with pytest.raises(ValueError, match="scope"):
        prepare_result_attachment(bad, **args)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["soft_claim", "hard_lease"])
def test_completed_work_can_attach_evidence_without_reopening_or_changing_completion(
    tmp_path, provider, mode
):
    from tests.control_plane.test_native_todo_planning_update import (
        fixture as canonical_fixture,
        records,
    )
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from loopx.control_plane.coordination.local_authority import (
        read_canonical_todos_if_promoted,
    )
    from loopx.control_plane.work_items.task_lease import acquire_task_lease
    from loopx.todos import complete_goal_todo

    path, state = canonical_fixture(tmp_path, False, provider)
    config = json.loads(path.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    path.write_text(json.dumps(config))
    runtime = tmp_path / "runtime"
    todos = list_goal_todos(registry_path=path, goal_id="goal-a")["todos"]
    initialize_canonical_authority(
        runtime,
        "goal-a",
        build_todo_runtime_shadow_projection(
            goal_id="goal-a", todos=todos, handoff_mode=mode
        ),
        state_path=state,
        provider=provider,
    )
    proof = {}
    if mode == "hard_lease":
        lease = acquire_task_lease(
            registry_path=path,
            runtime_root=runtime,
            goal_id="goal-a",
            todo_id="todo_target",
            owner="agent-a",
            idempotency_key="terminal-result",
        )
        proof = dict(
            task_lease_idempotency_key="terminal-result",
            task_lease_expected_version=lease["lease"]["version"],
        )
    complete_goal_todo(
        registry_path=path,
        goal_id="goal-a",
        todo_id="todo_target",
        agent_id="agent-a",
        evidence="Synthetic validation passed",
        **proof,
    )
    before = records(path)["todo_target"]
    leases_before = read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id="goal-a", include_leases=True
    )["leases"]
    args = dict(
        registry_path=path,
        runtime_root=runtime,
        goal_id="goal-a",
        agent_id="agent-a",
        todo_id="todo_target",
        turn_instance_id="terminal-turn",
    )
    assert before["status"] == "done"
    prepare_result_attachment(ATTACHMENT, **args)
    result = deliver_result_attachment(payload=payload(), **args)
    assert result["ok"], result
    after = records(path)["todo_target"]
    assert after["status"] == "done" and after["claimed_by"] == "agent-a"
    assert after.get("completion_continuation") == before.get("completion_continuation")
    assert after["explore_result_node_refs"] == [ATTACHMENT["node_id"]]
    assert (
        read_canonical_todos_if_promoted(
            runtime_root=runtime, goal_id="goal-a", include_leases=True
        )["leases"]
        == leases_before
    )
    with pytest.raises((ValueError, RuntimeError)):
        update_goal_todo(
            registry_path=path,
            goal_id="goal-a",
            agent_id="agent-b",
            todo_id="todo_target",
            append_explore_result_node_refs=["foreign"],
            **proof,
        )
    with pytest.raises((ValueError, RuntimeError)):
        update_goal_todo(
            registry_path=path,
            goal_id="goal-a",
            agent_id="agent-a",
            todo_id="todo_target",
            append_explore_result_node_refs=["mixed"],
            note="Other edit",
            **proof,
        )
    if mode == "hard_lease":
        # A mismatched retained lease version must not authorize even this
        # narrow metadata operation; completion need not increment the version.
        assert leases_before[0]["status"] == "released"
        with pytest.raises((ValueError, RuntimeError)):
            update_goal_todo(
                registry_path=path,
                goal_id="goal-a",
                agent_id="agent-a",
                todo_id="todo_target",
                append_explore_result_node_refs=["stale"],
                **{
                    **proof,
                    "task_lease_expected_version": proof["task_lease_expected_version"]
                    + 1,
                },
            )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_hard_lease_is_required_for_new_delivery_but_not_successful_readback(
    tmp_path, provider
):
    from tests.control_plane.test_native_todo_planning_update import (
        fixture as canonical_fixture,
    )
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from loopx.control_plane.work_items.task_lease import (
        acquire_task_lease,
        release_task_lease,
    )

    path, state = canonical_fixture(tmp_path, False, provider)
    config = json.loads(path.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    path.write_text(json.dumps(config))
    runtime = tmp_path / "runtime"
    todos = list_goal_todos(registry_path=path, goal_id="goal-a")["todos"]
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=todos, handoff_mode="hard_lease"
    )
    initialize_canonical_authority(
        runtime, "goal-a", projection, state_path=state, provider=provider
    )
    args = dict(
        registry_path=path,
        runtime_root=runtime,
        goal_id="goal-a",
        agent_id="agent-a",
        todo_id="todo_target",
        turn_instance_id="turn-one",
    )
    failed = deliver_result_attachment(payload=payload(), **args)
    assert not failed["ok"] and "lease" in failed["error"]
    assert not explore_result_log_path(runtime, "goal-a").exists()
    lease_args = dict(
        registry_path=path,
        runtime_root=runtime,
        goal_id="goal-a",
        todo_id="todo_target",
        owner="agent-a",
        idempotency_key="result-test",
    )
    lease = acquire_task_lease(**lease_args)
    result = deliver_result_attachment(payload=payload(), **args)
    assert result["ok"], result.get("error", result)
    release_task_lease(**lease_args, expected_version=lease["lease"]["version"])
    replay = deliver_result_attachment(payload=payload(), **args)
    assert replay["ok"] and replay["idempotent_replay"]
    later = payload()
    later["generated_at"] = "2026-01-02T00:00:00Z"
    later["settlement_identity"]["effect_id"] = "second-result"
    rejected = deliver_result_attachment(
        payload=later, **{**args, "turn_instance_id": "turn-two"}
    )
    assert not rejected["ok"] and "lease" in rejected["error"]
    assert (
        len(load_explore_result_events(explore_result_log_path(runtime, "goal-a"))) == 2
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_cli_open_capture_release_order_and_readback_replay(tmp_path, provider):
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from tests.control_plane.test_quota_settlement_cli import (
        _write_fixture, _run_cli, GOAL_ID, AGENT_ID, TODO_ID, TURN_ID,
    )

    project, runtime, path = _write_fixture(tmp_path)
    config = json.loads(path.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    path.write_text(json.dumps(config))

    def run(*command):
        rc, packet = _run_cli(path, runtime, *command, cwd=project)
        assert rc == 0, packet
        return packet

    run("todo", "claim", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
        "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID)
    todos = list_goal_todos(registry_path=path, goal_id=GOAL_ID)["todos"]
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="hard_lease"),
        state_path=project / config["goals"][0]["state_file"], provider=provider)
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]
    guard_command = ["quota", "should-run", "--codex-app", *binding,
                     "--scan-path", str(project)]
    guard = run(*guard_command)
    hint = guard["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["optional_attachments"][0]
    assert "release only after explore_result_delivery.ok=true" in hint["guidance"]
    lease_binding = ["--goal-id", GOAL_ID, "--todo-id", TODO_ID, "--owner", AGENT_ID]
    early = run("task-lease", "acquire", *lease_binding, "--idempotency-key", "early-release")["lease"]
    run("task-lease", "release", *lease_binding, "--idempotency-key", "early-release",
        "--expected-version", str(early["version"]))
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Require a uniform tail bound."},
        "explore_result": ATTACHMENT,
    }))
    refresh = ["refresh-state", *binding, "--classification", "validated_change",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks"]
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    before_index = index.read_bytes() if index.exists() else b""
    rc, rejected = _run_cli(path, runtime, *refresh, cwd=project)
    assert rc == 1 and not rejected["ok"] and not rejected["appended"], rejected
    assert (index.read_bytes() if index.exists() else b"") == before_index
    log = explore_result_log_path(runtime, GOAL_ID)
    assert not log.exists()
    assert list_goal_todos(registry_path=path, goal_id=GOAL_ID)["todos"] == todos

    # Supported recovery: fresh admission and an active lease, then the same capture.
    run(*guard_command)
    current = run("task-lease", "acquire", *lease_binding, "--idempotency-key", "recovery")["lease"]
    captured = run(*refresh)
    assert captured["appended"] and captured["explore_result_delivery"]["ok"]
    assert len(load_explore_result_events(log)) == 2
    committed_index, committed_log = index.read_bytes(), log.read_bytes()
    rows = list_goal_todos(registry_path=path, goal_id=GOAL_ID)["todos"]
    target = next(row for row in rows if row["todo_id"] == TODO_ID)
    assert target["status"] == "open" and target["claimed_by"] == AGENT_ID
    assert target["explore_result_node_refs"] == [ATTACHMENT["node_id"]]
    run("task-lease", "release", *lease_binding, "--idempotency-key", "recovery",
        "--expected-version", str(current["version"]))
    leases = read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)["leases"]
    assert leases[0]["status"] == "released"
    replay = run(*refresh)
    assert replay["idempotent_replay"] and replay["explore_result_delivery"]["idempotent_replay"]
    assert index.read_bytes() == committed_index and log.read_bytes() == committed_log
    assert list_goal_todos(registry_path=path, goal_id=GOAL_ID)["todos"] == rows
    assert read_canonical_todos_if_promoted(
        runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)["leases"] == leases
    context = run("explore", "turn-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    finding = context["graph"]["writeback_results"][0]
    assert finding["status"] == "refuted"
    for field in ("input_revision", "applicability", "observation", "interpretation"):
        assert ATTACHMENT[field] in finding["summary"]


@pytest.mark.parametrize("planning", [True, False])
def test_selected_route_retains_scope_after_unrelated_newer_results(tmp_path, planning):
    from loopx.configure_goal import configure_goal

    args = fixture(tmp_path)
    configure_goal(
        registry_path=args["registry_path"], goal_id="research", execute=True,
        explore_mode="planning" if planning else "evidence",
    )
    for index in range(4):
        record = payload()
        record["generated_at"] = f"2026-01-01T00:0{index}:00Z"
        record["explore_result"]["node_id"] = f"question-{index}"
        turn = dict(args, turn_instance_id=f"result-{index}")
        record["settlement_identity"]["effect_id"] = f"research:worker:result-{index}"
        assert deliver_result_attachment(payload=record, **turn)["ok"]
    # Only the older question is relevant to the next selected work item.
    update_goal_todo(
        registry_path=args["registry_path"], goal_id="research",
        todo_id=args["todo_id"], agent_id="worker",
        explore_result_node_refs=["question-0"],
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    context = explore_turn_context(**{
        key: args[key] for key in ("registry_path", "runtime_root", "goal_id", "agent_id")
    })
    results = context["graph"]["writeback_results"]
    assert len(results) == 3
    assert results[0]["node_id"] == ("question-0" if planning else "question-3")
    assert ATTACHMENT["applicability"] in results[0]["summary"]
    assert ATTACHMENT["interpretation"] in results[0]["summary"]
    assert results[0]["evidence_refs"] == ATTACHMENT["evidence_refs"]
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("planning", [True, False])
def test_linked_refutation_keeps_scope_after_later_positive_observations(tmp_path, planning):
    from loopx.configure_goal import configure_goal
    from loopx.capabilities.explore.result_log import (
        append_explore_result_event, build_explore_finding_event,
    )

    args = fixture(tmp_path)
    configure_goal(
        registry_path=args["registry_path"], goal_id="research", execute=True,
        explore_mode="planning" if planning else "evidence",
    )
    first = deliver_result_attachment(payload=payload(), **args)
    assert first["ok"]
    log = explore_result_log_path(args["runtime_root"], "research")
    # Successful checks on a narrower input do not supersede the counterexample.
    # Enough later results also cross the upstream audit detail cap.
    for index in range(25):
        append_explore_result_event(log, build_explore_finding_event(
            goal_id="research", node_id=ATTACHMENT["node_id"],
            finding_id=f"positive-{index}", title="Bound holds for this finite input",
            summary="Applicability: finite input only. No conclusion about an infinite tail.",
            status="confirmed", tags=["writeback-result"],
            recorded_at=f"2026-01-02T00:{index:02d}:00Z",
        ))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    context = explore_turn_context(**{
        key: args[key] for key in ("registry_path", "runtime_root", "goal_id", "agent_id")
    })
    results = context["graph"]["writeback_results"]
    assert len(results) == 3
    if planning:
        counter = next(row for row in results if row["finding_id"] == first["finding_id"])
        assert ATTACHMENT["applicability"] in counter["summary"]
        assert ATTACHMENT["interpretation"] in counter["summary"]
        assert any(row["status"] == "confirmed" for row in results)
        audit = context["harness"]["selected_branches"][0]["typed_evidence_audit"]
        assert "linked_finding_refuted" in audit["hazards"]
        assert audit["omitted_audit_findings"] == 23
        assert audit["score_delta"] == 0
    else:
        assert [row["finding_id"] for row in results] == ["positive-24", "positive-23", "positive-22"]
    assert all(p.read_bytes() == content for p, content in before.items())
    if planning:
        # An explicit correction of the same finding supersedes its old status;
        # an unrelated newer positive finding above did not.
        append_explore_result_event(log, build_explore_finding_event(
            goal_id="research", node_id=ATTACHMENT["node_id"],
            finding_id=first["finding_id"], title="Counterexample corrected",
            summary="The recorded counterexample used an invalid input.",
            status="confirmed", tags=["writeback-result"],
            recorded_at="2026-01-03T00:00:00Z",
        ))
        corrected = explore_turn_context(**{
            key: args[key] for key in ("registry_path", "runtime_root", "goal_id", "agent_id")
        })
        assert all(row["status"] == "confirmed" for row in corrected["graph"]["writeback_results"])
        assert "linked_finding_refuted" not in corrected["harness"]["selected_branches"][0]["typed_evidence_audit"]["hazards"]


def test_corrupt_graph_cannot_report_success_on_replay_and_recovers_after_repair(tmp_path):
    args = fixture(tmp_path)
    assert deliver_result_attachment(payload=payload(), **args)["ok"]
    log = explore_result_log_path(args["runtime_root"], args["goal_id"])
    valid = log.read_bytes()
    before = list_goal_todos(registry_path=args["registry_path"], goal_id=args["goal_id"])
    corrupt = valid + b'{incomplete-record\n'
    log.write_bytes(corrupt)
    with pytest.raises(ValueError, match="invalid Explore result JSON"):
        prepare_result_attachment(ATTACHMENT, **args)
    result = deliver_result_attachment(payload=payload(), **args)
    assert not result["ok"], "A partial log must not become a successful delivery receipt"
    assert result["primary_committed"] and result["retryable"]
    assert log.read_bytes() == corrupt
    assert list_goal_todos(registry_path=args["registry_path"], goal_id=args["goal_id"]) == before
    log.write_bytes(valid)
    assert deliver_result_attachment(payload=payload(), **args)["idempotent_replay"]
    assert log.read_bytes() == valid


@pytest.mark.parametrize("planning", [True, False])
def test_linked_refutations_share_detail_budget_across_questions(tmp_path, planning):
    from loopx.configure_goal import configure_goal

    args = fixture(tmp_path)
    configure_goal(
        registry_path=args["registry_path"], goal_id="research", execute=True,
        explore_mode="planning" if planning else "evidence",
    )
    # Three selected questions have scoped counterexamples. Repeated newer
    # findings on one question must not erase the other questions' conditions.
    order = ["route-c", "route-b", "route-a", "route-a", "route-a"]
    for index, node in enumerate(order):
        record = payload()
        record["generated_at"] = f"2026-01-01T00:0{index}:00Z"
        record["settlement_identity"]["effect_id"] = f"research:worker:probe-{index}"
        record["explore_result"].update(
            node_id=node, question=f"Does {node} hold for this input?",
            applicability=f"Only {node} input revision one.",
            observation=f"Independent counterexample {index}.",
        )
        delivered = deliver_result_attachment(
            payload=record, **{**args, "turn_instance_id": f"probe-{index}"}
        )
        assert delivered["ok"], delivered
    update_goal_todo(
        registry_path=args["registry_path"], goal_id="research",
        todo_id=args["todo_id"], agent_id="worker",
        explore_result_node_refs=["route-a", "route-b", "route-c"],
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    context = explore_turn_context(**{
        key: args[key] for key in ("registry_path", "runtime_root", "goal_id", "agent_id")
    })
    results = context["graph"]["writeback_results"]
    assert [row["node_id"] for row in results] == (
        ["route-a", "route-b", "route-c"] if planning else ["route-a"] * 3
    )
    for row in results:
        assert f"Only {row['node_id']} input revision one." in row["summary"]
    assert "Independent counterexample 4." in results[0]["summary"]
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("planning", [True, False])
def test_progressive_result_reads_follow_real_cli_and_reject_stale_pages(tmp_path, planning):
    import subprocess
    import sys
    from loopx.configure_goal import configure_goal

    args = fixture(tmp_path)
    configure_goal(
        registry_path=args["registry_path"], goal_id="research", execute=True,
        explore_mode="planning" if planning else "evidence",
    )
    for index in range(7):
        record = payload()
        record["generated_at"] = f"2026-01-01T00:0{index}:00Z"
        record["settlement_identity"]["effect_id"] = f"research:worker:page-{index}"
        record["explore_result"]["node_id"] = f"question-{index % 2}"
        assert deliver_result_attachment(
            payload=record, **{**args, "turn_instance_id": f"page-{index}"}
        )["ok"]
    keys = {key: args[key] for key in ("registry_path", "runtime_root", "goal_id", "agent_id")}
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    first = explore_turn_context(**keys)
    page = first["graph"]["result_page"]
    assert (page["total"], page["limit"], page["remaining"]) == (7, 3, 4)
    seen = [r["finding_id"] for r in first["graph"]["writeback_results"]]
    saved_next = page["next_command"]
    while page["next_command"]:
        read = subprocess.run(
            [sys.executable, "-m", "loopx.cli", *page["next_command"][1:]],
            capture_output=True, text=True,
        )
        assert read.returncode == 0, read.stdout
        received = json.loads(read.stdout)
        seen += [r["finding_id"] for r in received["graph"]["writeback_results"]]
        page = received["graph"]["result_page"]
    assert len(seen) == len(set(seen)) == 7
    expanded = explore_turn_context(**keys, result_limit=10)
    assert len(expanded["graph"]["writeback_results"]) == 7
    assert expanded["graph"]["result_page"]["next_command"] is None
    command = first["graph"]["result_page"]["node_command_template"]
    read = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *["question-0" if x == "<node-id>" else x for x in command[1:]]],
        capture_output=True, text=True,
    )
    assert read.returncode == 0, read.stdout
    filtered = json.loads(read.stdout)["graph"]
    assert filtered["result_page"]["total"] == 4
    assert {r["node_id"] for r in filtered["writeback_results"]} == {"question-0"}
    assert all(p.read_bytes() == content for p, content in before.items())
    for options in ({"result_limit": 0}, {"result_limit": 21}, {"result_offset": -1}, {"result_offset": 3}):
        with pytest.raises(EffectRuntimeRejected):
            explore_turn_context(**keys, **options)
    # A changed graph/order must not silently skip or repeat evidence mid-read.
    record["generated_at"] = "2026-01-02T00:00:00Z"
    record["settlement_identity"]["effect_id"] = "research:worker:new-evidence"
    assert deliver_result_attachment(
        payload=record, **{**args, "turn_instance_id": "new-evidence"}
    )["ok"]
    read = subprocess.run([sys.executable, "-m", "loopx.cli", *saved_next[1:]], capture_output=True, text=True)
    assert read.returncode != 0
    assert "restart at result_offset 0" in json.loads(read.stdout)["error"]
    assert explore_turn_context(**keys)["graph"]["result_page"]["total"] == 8
