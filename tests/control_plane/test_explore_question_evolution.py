"""Ordinary result capture evolves knowledge without a separate cleanup pass."""
from copy import deepcopy
import json

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_node_event, build_explore_edge_event,
    build_explore_result_projection, explore_result_log_path, load_explore_result_events,
)
from loopx.capabilities.explore.result_writeback import prepare_result_attachment, deliver_result_attachment
from loopx.capabilities.explore.turn_context import explore_turn_context
from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from tests.control_plane.test_explore_result_writeback import ATTACHMENT, fixture, payload


def graph(args):
    return build_explore_result_projection(load_explore_result_events(
        explore_result_log_path(args["runtime_root"], args["goal_id"])), goal_id=args["goal_id"])


def node(args, node_id, **changes):
    event = build_explore_node_event(
        goal_id=args["goal_id"], agent_id=args["agent_id"], node_id=node_id,
        node_kind="question", title=changes.pop("title", ATTACHMENT["question"]),
        summary=changes.pop("summary", ATTACHMENT["applicability"]), **changes)
    append_explore_result_event(explore_result_log_path(args["runtime_root"], args["goal_id"]), event)


def record(status="resolved", **changes):
    result = payload()
    result["generated_at"] = "2026-01-02T00:00:00Z"
    result["explore_result"].update(question_update={"status": status}, **changes)
    return result


def test_result_answer_relation_next_turn_and_explicit_reopen_preserve_scope(tmp_path):
    args = fixture(tmp_path)
    first = deliver_result_attachment(payload=payload(), **args)
    assert first["ok"], first
    node(args, "prerequisite", title="Uniform estimate", summary="Required before transfer")
    node(args, ATTACHMENT["node_id"], status="exploring", parent_id="prerequisite",
         evidence_refs=["validation:original"], tags=["keep"], supersedes="earlier-question")
    before = next(n for n in graph(args)["nodes"] if n["node_id"] == ATTACHMENT["node_id"])
    answer = record(relations=[{"to_node": "prerequisite", "edge_type": "depends_on",
                                "summary": "Transfer requires the separately established uniform estimate."}])
    result = deliver_result_attachment(payload=answer, **dict(args, turn_instance_id="turn-two"))
    assert result["ok"], result
    after = next(n for n in graph(args)["nodes"] if n["node_id"] == ATTACHMENT["node_id"])
    assert after["status"] == "resolved"
    for key in ("title", "summary", "node_kind", "agent_id", "parent_id", "tags", "supersedes", "evidence_refs"):
        assert after[key] == before[key]
    context = explore_turn_context(**{k: args[k] for k in ("registry_path", "runtime_root", "goal_id", "agent_id")})
    current = context["graph"]["writeback_results"][0]
    assert current["status"] == "refuted" and current["question_status"] == "resolved"
    assert any(e["edge_type"] == "depends_on" for e in current["relations"])
    assert len(graph(args)["findings"]) == 2

    # A new finding alone cannot reopen the finite answered question.
    ordinary = payload()
    ordinary["generated_at"] = "2026-01-03T00:00:00Z"
    ordinary["explore_result"].update(status="confirmed", input_revision="fixture-v2")
    assert deliver_result_attachment(payload=ordinary, **args)["ok"]
    assert next(n for n in graph(args)["nodes"] if n["node_id"] == ATTACHMENT["node_id"])["status"] == "resolved"
    reopened = record("exploring", input_revision="fixture-v3")
    reopened["generated_at"] = "2026-01-04T00:00:00Z"
    assert deliver_result_attachment(payload=reopened, **args)["ok"]
    log = explore_result_log_path(args["runtime_root"], args["goal_id"])
    frozen = log.read_bytes()
    assert deliver_result_attachment(payload=answer, **dict(args, turn_instance_id="turn-two"))["idempotent_replay"]
    assert log.read_bytes() == frozen
    assert next(n for n in graph(args)["nodes"] if n["node_id"] == ATTACHMENT["node_id"])["status"] == "exploring"


@pytest.mark.parametrize("case", ["foreign", "scope", "dangling", "self", "cycle", "blocked", "unknown-status"])
def test_invalid_evolution_fails_preflight_without_graph_effect(tmp_path, case):
    args = fixture(tmp_path)
    node(args, ATTACHMENT["node_id"], status="open")
    node(args, "prerequisite")
    if case == "foreign":
        node(dict(args, agent_id="other"), ATTACHMENT["node_id"], status="open")
    if case == "cycle":
        append_explore_result_event(explore_result_log_path(args["runtime_root"], args["goal_id"]),
            build_explore_edge_event(goal_id=args["goal_id"], from_node="prerequisite",
                                     to_node=ATTACHMENT["node_id"], edge_type="depends_on"))
    attachment = record()["explore_result"]
    if case == "scope":
        attachment["applicability"] = "A different scope"
    if case in {"dangling", "self", "cycle"}:
        attachment["relations"] = [{"to_node": {"dangling": "missing", "self": ATTACHMENT["node_id"],
                                                "cycle": "prerequisite"}[case],
                                    "edge_type": "depends_on", "summary": "Required before transfer"}]
    if case == "blocked":
        attachment["question_update"]["status"] = "blocked"
    if case == "unknown-status":
        attachment["question_update"]["status"] = "confirmed"
    log = explore_result_log_path(args["runtime_root"], args["goal_id"])
    before = log.read_bytes()
    with pytest.raises((ValueError, EffectRuntimeRejected)):
        prepare_result_attachment(attachment, **args)
    assert log.read_bytes() == before


def test_partial_delivery_retries_link_without_reapplying_old_question_state(tmp_path, monkeypatch):
    import loopx.capabilities.explore.result_writeback as module
    args = fixture(tmp_path)
    original = module.update_goal_todo

    def fail_link(**kwargs):
        if not kwargs.get("dry_run"):
            raise OSError("interrupted link")
        return original(**kwargs)

    monkeypatch.setattr(module, "update_goal_todo", fail_link)
    answer = record()
    first = deliver_result_attachment(payload=answer, **args)
    assert not first["ok"] and first["primary_committed"] and first["graph"]["appended_event_count"] == 2
    node(args, ATTACHMENT["node_id"], status="exploring", tags=["new-input"])
    log = explore_result_log_path(args["runtime_root"], args["goal_id"])
    before = log.read_bytes()
    monkeypatch.setattr(module, "update_goal_todo", original)
    recovered = deliver_result_attachment(payload=answer, **args)
    assert recovered["ok"], recovered
    assert log.read_bytes() == before
    assert graph(args)["nodes"][0]["status"] == "exploring"


def test_concurrent_graph_change_refuses_the_whole_new_batch(tmp_path, monkeypatch):
    import loopx.capabilities.explore.result_writeback as module
    args = fixture(tmp_path)
    node(args, ATTACHMENT["node_id"], status="open")
    original = module.append_explore_result_events

    def race(*positional, **kwargs):
        node(args, ATTACHMENT["node_id"], status="exploring", tags=["concurrent"])
        return original(*positional, **kwargs)

    monkeypatch.setattr(module, "append_explore_result_events", race)
    refused = deliver_result_attachment(payload=record(), **args)
    assert not refused["ok"] and refused["retryable"] and "graph changed" in refused["error"]
    assert graph(args)["findings"] == []
    assert graph(args)["nodes"][0]["status"] == "exploring"


@pytest.mark.parametrize("case", ["valid", "foreign", "dangling", "cycle"])
def test_real_cli_path_delta_result_evolves_question_and_reads_it_back(tmp_path, case):
    from tests.control_plane.test_quota_settlement_cli import (
        _write_fixture, _run_cli, AGENT_ID, GOAL_ID, TODO_ID, TURN_ID,
    )
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    registry.write_text(json.dumps(config))

    def cli(*args):
        rc, result = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, result
        return result

    cli("todo", "claim", "--goal-id", GOAL_ID, "--todo-id", TODO_ID,
        "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID)
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID)
    cli("quota", "should-run", "--codex-app", *binding, "--scan-path", str(project))
    cli("explore", "node", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--node-id", "transfer", "--title", "Can the estimate transfer?", "--kind", "question")
    attachment = {k: deepcopy(ATTACHMENT[k]) for k in ("question", "applicability", "input_revision", "status")}
    attachment.update(schema_version="explore_result_from_path_delta_v0",
                      question_update={"status": "resolved"},
                      relations=[{"to_node": "transfer", "edge_type": "refutes",
                                  "summary": "This prefix counterexample prevents unconditional transfer."}])
    if case != "valid":
        attachment["node_id"] = ATTACHMENT["node_id"]
        append_explore_result_event(explore_result_log_path(runtime, GOAL_ID), build_explore_node_event(
            goal_id=GOAL_ID, agent_id="other" if case == "foreign" else AGENT_ID,
            node_id=ATTACHMENT["node_id"], node_kind="question", title=ATTACHMENT["question"],
            summary=ATTACHMENT["applicability"], status="exploring"))
    if case == "dangling":
        attachment["relations"][0]["to_node"] = "missing"
    if case == "cycle":
        attachment["relations"][0]["edge_type"] = "depends_on"
        append_explore_result_event(explore_result_log_path(runtime, GOAL_ID), build_explore_edge_event(
            goal_id=GOAL_ID, from_node="transfer", to_node=ATTACHMENT["node_id"], edge_type="depends_on"))
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Require a uniform tail estimate."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
                       "prior_assumption": "A finite prefix might suffice.",
                       "observed_reality": ATTACHMENT["observation"],
                       "changed": [ATTACHMENT["interpretation"]], "evidence_refs": ATTACHMENT["evidence_refs"]},
        "explore_result": attachment,
    }))
    command = ("refresh-state", *binding, "--classification", "validated_change",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks")
    if case != "valid":
        log = explore_result_log_path(runtime, GOAL_ID)
        before = log.read_bytes()
        index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        primary = index.read_bytes() if index.exists() else b""
        rc, result = _run_cli(registry, runtime, *command, cwd=project)
        assert rc == 1 and not result["appended"], result
        assert log.read_bytes() == before
        assert (index.read_bytes() if index.exists() else b"") == primary
        return
    result = cli(*command)
    assert result["appended"] and result["explore_result_delivery"]["ok"], result
    log = explore_result_log_path(runtime, GOAL_ID)
    before = log.read_bytes()
    replay = cli(*command)
    assert replay["idempotent_replay"] and log.read_bytes() == before
    context = cli("explore", "turn-context", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    finding = context["graph"]["writeback_results"][0]
    assert finding["status"] == "refuted" and finding["question_status"] == "resolved"
    assert finding["relations"][0]["to_node"] == "transfer"
