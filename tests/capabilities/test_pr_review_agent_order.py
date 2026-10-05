"""Scoped direction invariants, through the shipped writer and CLI."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import runpy

import pytest

from loopx.cli import main
from loopx.configure_goal import configure_goal
from loopx.capabilities.configuration_inspection import project_goal_configuration
from loopx.capabilities.pr_review_queue.goal_configuration import apply_change, resolve_configuration
from loopx.capabilities.pr_review_queue.core import build_pull_request_review_queue_observation
from loopx.pr_review import build_pr_review_packet


def _queue():
    make = runpy.run_path(str(Path(__file__).parents[1] / "test_pr_review_github_scan.py"))["_queue_pr"]
    # Two old other-author heads share a timestamp: PR number breaks the tie.
    rows = [make(number, author=author, ready_at=date, updated_at=date) for number, author, date in [
        (1, "other", "2026-09-01T00:00:00Z"),
        (2, "other", "2026-09-01T00:00:00Z"),
        (3, "other", "2026-09-02T11:00:00Z"),
        (4, "reviewer", "2026-09-01T00:00:00Z"),
        (5, "reviewer", "2026-09-02T11:00:00Z"),
        (6, "other", "2026-09-02T11:00:00Z"),
    ]]
    rows[-1]["isDraft"] = True
    return rows


def _packet(order=None, limit=100):
    return build_pr_review_packet(pull_requests=_queue(), repository="owner/repo",
        reviewer_login="reviewer", source="fixture",
        review_order=order, limit=limit)


def _sequence(packet):
    return [item["number"] for item in packet["review_sequence"]]


def test_reverse_whole_actionable_queue_before_limit_and_preserve_inactive():
    legacy = _packet()
    forward, reverse = _packet("forward"), _packet("reverse")
    assert _sequence(forward) == _sequence(legacy) == [1, 2, 3, 4, 5]
    assert _sequence(reverse) == [5, 4, 3, 2, 1]
    assert _sequence(_packet("reverse", 2)) == [5, 4]
    assert [p["number"] for p in reverse["pull_requests"]] == [5, 4, 3, 2, 1, 6]
    inactive = reverse["pull_requests"][-1]
    assert inactive["review_action_kind"] is None
    assert inactive["review_plan"] is None
    # Direction only changes scheduling: evidence and exact-head contracts match.
    assert {p["number"]: p["review_plan"] for p in reverse["pull_requests"] if p["review_action_kind"]} == {
        p["number"]: p["review_plan"] for p in forward["pull_requests"] if p["review_action_kind"]}


def test_direction_transport_does_not_send_large_review_evidence():
    from loopx.capabilities.pr_review_queue.order import order_queue
    rows = [{"review_action_kind": "review_pull_request_exact_head", "evidence": "x" * (3 * 1024 * 1024)},
            {"review_action_kind": None}, {"review_action_kind": "review_pull_request_exact_head", "number": 3}]
    ordered = order_queue(rows, "reverse")
    assert ordered[0]["number"] == 3
    assert ordered[1]["evidence"] == rows[0]["evidence"]
    assert ordered[2]["review_action_kind"] is None
    with pytest.raises(ValueError, match="unsupported"):
        apply_change({}, {"unknown_boolean": False}, clear=False)


def test_observation_selects_same_order_and_preserves_acknowledgments():
    rows = _packet("forward")["pull_requests"]
    def observe(order, **kwargs):
        return build_pull_request_review_queue_observation(repository="owner/repo",
            pull_requests=rows, result_completeness={"complete": True}, review_order=order, **kwargs)
    forward, reverse = observe("forward"), observe("reverse")
    assert forward["candidate"]["number"] == 1
    assert reverse["candidate"]["number"] == 5
    assert reverse["queue_fingerprint"] != forward["queue_fingerprint"]
    assert observe("reverse", previous_observation=forward)["observation_state"] == "material_transition"
    acknowledged = observe("reverse", previous_observation=reverse, handled_exact_heads=[f"5@{5:040x}"])
    assert acknowledged["candidate"]["number"] == 4
    selected = observe("reverse", previous_observation=acknowledged, projected_exact_heads=[f"4@{4:040x}"])
    assert selected["candidate"]["number"] == 3
    assert selected["handled_exact_head_count"] == 1
    assert selected["projected_candidate_count"] == 1


def test_native_direction_prevents_independent_approval_preemption():
    rows = _packet("forward")["pull_requests"]
    rows = [row for row in rows if row["number"] in {4, 5}]
    previous = build_pull_request_review_queue_observation(repository="owner/repo",
        pull_requests=rows, result_completeness={"complete": True}, review_order="forward")
    rows[1]["review_decision"] = "APPROVED"
    for direction, expected in [("forward", 4), ("reverse", 5)]:
        current = build_pull_request_review_queue_observation(repository="owner/repo",
            pull_requests=rows, result_completeness={"complete": True}, previous_observation=previous, review_order=direction)
        assert current["candidate"]["number"] == expected


def test_scoped_precedence_patch_inheritance_and_unknown_agent_no_mutation():
    goal = {"coordination": {"registered_agents": ["a", "b"]}}
    machine = {"namespaces": {"pull_request_review": {
        "schema_version": "pull_request_review_machine_defaults_v0", "review_order": "reverse"}}}
    assert resolve_configuration()["order_source"] == "capability_default"
    assert resolve_configuration(goal, machine, "a")["order_source"] == "machine_default"
    apply_change(goal, {"review_order": "forward", "wait_for_ci": False}, clear=False,
        agent_order_updates={"a": "forward", "b": "reverse"})
    assert resolve_configuration(goal, machine, "a")["review_order"] == "forward"
    assert resolve_configuration(goal, machine, "b")["review_order"] == "reverse"

    assert resolve_configuration(goal, machine, "b")["wait_for_ci"] is False
    apply_change(goal, None, clear=False, agent_order_updates={"b": None})
    assert goal["control_plane"]["pull_request_review"]["agent_orders"] == {"a": "forward"}
    assert resolve_configuration(goal, machine, "b")["order_source"] == "goal_override"
    before = deepcopy(goal)
    with pytest.raises(ValueError, match="not registered"):
        apply_change(goal, None, clear=False, agent_order_updates={"unknown": "reverse"})
    assert goal == before
    with pytest.raises(ValueError, match="not registered"):
        apply_change(goal, None, clear=False, agent_order_updates={"unknown": None})
    assert goal == before
    with pytest.raises(ValueError, match="registered Goal Agent"):
        resolve_configuration(goal, machine, "unknown")
    with pytest.raises(ValueError, match="cannot be combined"):
        apply_change(goal, {"review_order": "forward", "review_priority": "owner-first"}, clear=False)
    apply_change(goal, None, clear=True)
    assert resolve_configuration(goal, machine, "b")["review_order"] == "reverse"


def test_agent_only_patch_retains_live_machine_policy_and_safe_agent_dictionary():
    goal = {"coordination": {"registered_agents": ["a", "constructor"]}}
    machine = {"namespaces": {"pull_request_review": {
        "schema_version": "pull_request_review_machine_defaults_v0", "review_order": "reverse", "wait_for_ci": False}}}
    apply_change(goal, None, clear=False, agent_order_updates={"a": "forward"})
    assert "wait_for_ci" not in goal["control_plane"]["pull_request_review"]
    assert resolve_configuration(goal, machine, "a")["wait_for_ci"] is False
    assert resolve_configuration(goal, machine, "constructor")["review_order"] == "reverse"
    machine["namespaces"]["pull_request_review"]["review_order"] = "forward"
    assert resolve_configuration(goal, machine, "constructor")["review_order"] == "forward"


def test_native_cli_uses_agent_config_without_repeating_direction(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(tmp_path / "runtime"), "goals": [
        {"id": id, "repo": str(tmp_path), "coordination": {"registered_agents": ["a", "b"]}}
        for id in ["first", "second"]]}))
    common = ["--registry", str(registry), "--runtime-root", str(tmp_path / "runtime"), "--format", "json"]
    assert main([*common, "configure-goal", "--goal-id", "first", "--pr-review-agent-order", "a=forward",
        "--pr-review-agent-order", "b=reverse", "--execute"]) == 0
    capsys.readouterr()
    fixture = tmp_path / "prs.json"
    fixture.write_text(json.dumps({"repository": "owner/repo", "reviewer_login": "reviewer", "pull_requests": _queue()}))
    commands = [*common, "pr-review", "--fixture", str(fixture), "--limit", "100", "--autonomous-observation"]
    for goal, agent, expected in [("first", "a", [1, 2]), ("first", "b", [5, 4]), ("second", "b", [1, 2])]:
        assert main([*commands, "--goal-id", goal, "--agent-id", agent]) == 0
        packet = json.loads(capsys.readouterr().out)
        assert _sequence(packet)[:2] == expected
        assert packet["autonomous_review"]["candidate"]["number"] == expected[0]
    assert main([*commands, "--goal-id", "first", "--agent-id", "b", "--review-order", "forward"]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["request"]["review_order_source"] == "command_override"
    assert _sequence(packet)[:2] == [1, 2]
    # Bound current identity supplies the Agent, and rejects impersonation.
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["thread_agent_bindings"] = [{"host_surface": "codex-app", "thread_id": "session", "agent_id": "b"}]
    registry.write_text(json.dumps(data))
    monkeypatch.setenv("CODEX_THREAD_ID", "session")
    assert main([*commands, "--goal-id", "first"]) == 0
    assert _sequence(json.loads(capsys.readouterr().out))[:2] == [5, 4]
    before = registry.read_bytes()
    assert main([*commands, "--goal-id", "first", "--agent-id", "a"]) == 1
    assert "conflicts" in json.loads(capsys.readouterr().out)["error"]
    assert registry.read_bytes() == before


def test_goal_editor_lists_complete_registered_inventory_machine_has_no_agents(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "goal", "repo": str(tmp_path),
        "registered_agents": ["legacy"], "coordination": {"registered_agents": ["a", "b", "c", "d", "e", "f"]}}]}))
    result = configure_goal(registry_path=registry, goal_id="goal", execute=False)
    projected = project_goal_configuration(result)
    review = next(x for x in projected["capability_catalog"]["capabilities"] if x["capability_id"] == "pull_request_review")
    field = next(x for x in review["configuration_editor"]["fields"] if x["key"] == "agent_orders")
    assert set(field["agents"]) == {"legacy", "a", "b", "c", "d", "e", "f"}
    from loopx.capabilities.configuration_ui import capability_configuration_editor
    assert "agent_orders" not in {x["key"] for x in capability_configuration_editor("pull_request_review")["fields"]}


def test_retiring_registered_agent_removes_only_its_direction(tmp_path):
    registry = tmp_path / "registry.json"
    goal = {"id": "goal", "repo": str(tmp_path), "coordination": {"registered_agents": ["a", "b"]}}
    apply_change(goal, None, clear=False, agent_order_updates={"a": "forward", "b": "reverse"})
    registry.write_text(json.dumps({"goals": [goal]}))
    configure_goal(registry_path=registry, goal_id="goal", registered_agents=["a"], execute=True)
    after = json.loads(registry.read_text())["goals"][0]
    assert after["control_plane"]["pull_request_review"]["agent_orders"] == {"a": "forward"}
    assert resolve_configuration(after, agent_id="a")["review_order"] == "forward"


def test_two_owner_accounts_change_queue_membership_not_self_review(monkeypatch):
    monkeypatch.setattr("loopx.pr_review._now_iso", lambda: "2026-10-05T05:40:00Z")
    rows = _queue()
    rows[2]["author"] = {"login": "Maintainer"}
    def packet(order, owners=()):
        return build_pr_review_packet(pull_requests=rows, repository="owner/repo",
            reviewer_login="reviewer", source="fixture", review_order=order,
            owner_logins=owners, limit=100)
    baseline = packet("forward")
    assert _sequence(baseline) == [1, 2, 3, 4, 5]
    forward = packet("forward", ["MAINTAINER", "maintainer", "reviewer"])
    reverse = packet("reverse", ["maintainer"])
    assert _sequence(forward) == [1, 2, 4, 3, 5]
    assert _sequence(reverse) == [5, 3, 4, 2, 1]
    assert forward["scheduling_policy"]["owner_logins"] == ["reviewer", "maintainer"]
    for number in range(1, 7):
        old = next(p for p in baseline["pull_requests"] if p["number"] == number)
        current = next(p for p in forward["pull_requests"] if p["number"] == number)
        assert current["owner_authored"] is (number in {3, 4, 5})
        assert current["author_owned"] == old["author_owned"] == (number in {4, 5})
        assert current["review_conclusion"] == old["review_conclusion"]
        assert current["review_plan"] == old["review_plan"]
    assert packet("forward", []) == baseline  # opt-out parity, including emitted fields
    previous = build_pull_request_review_queue_observation(repository="owner/repo",
        pull_requests=baseline["pull_requests"], result_completeness={"complete": True}, review_order="reverse")
    observation = build_pull_request_review_queue_observation(repository="owner/repo",
        pull_requests=reverse["pull_requests"], result_completeness={"complete": True}, review_order="reverse",
        previous_observation=previous, handled_exact_heads=[f"5@{5:040x}"])
    assert observation["observation_state"] == "material_transition"
    assert observation["candidate"]["number"] == 3


def test_owner_list_scoped_inheritance_validation_and_clear():
    goal = {"coordination": {"registered_agents": ["a"]}}
    machine = {"namespaces": {"pull_request_review": {
        "schema_version": "pull_request_review_machine_defaults_v0", "review_order": "reverse",
        "wait_for_ci": False, "owner_logins": ["machine-owner"]}}}
    apply_change(goal, {"owner_logins": ["MAINTAINER", "maintainer"]}, clear=False)
    assert resolve_configuration(goal, machine, "a")["owner_logins"] == ["maintainer"]
    assert resolve_configuration(goal, machine, "a")["review_order"] == "reverse"
    assert resolve_configuration(goal, machine, "a")["wait_for_ci"] is False
    before = deepcopy(goal)
    for invalid in ["maintainer", None, [""], ["https://github.com/owner"], [0], ["owner name"]]:
        with pytest.raises(ValueError, match="owner_logins"):
            apply_change(goal, {"owner_logins": invalid}, clear=False)
        assert goal == before
    apply_change(goal, {"owner_logins": []}, clear=False)
    assert resolve_configuration(goal, machine, "a")["owner_logins"] == []
    apply_change(goal, None, clear=True)
    apply_change(goal, {"wait_for_ci": True}, clear=False)
    assert resolve_configuration(goal, machine, "a")["owner_logins"] == ["machine-owner"]
    machine["namespaces"]["pull_request_review"]["owner_logins"] = ["next-owner"]
    assert resolve_configuration(goal, machine, "a")["owner_logins"] == ["next-owner"]


def test_public_cli_owner_save_restart_cross_goal_and_clear(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(tmp_path / "runtime"), "goals": [
        {"id": id, "repo": str(tmp_path), "coordination": {"registered_agents": ["a", "b"]}}
        for id in ["first", "second"]]}))
    common = ["--registry", str(registry), "--format", "json"]
    assert main([*common, "configure-goal", "--goal-id", "first", "--pr-review-owner-login", "other",
        "--pr-review-agent-order", "a=forward", "--pr-review-agent-order", "b=reverse", "--execute"]) == 0
    capsys.readouterr()
    # Re-read from the durable writer rather than reuse an in-memory configuration.
    assert json.loads(registry.read_text())["goals"][0]["control_plane"]["pull_request_review"]["owner_logins"] == ["other"]
    fixture = tmp_path / "prs.json"
    fixture.write_text(json.dumps({"repository": "owner/repo", "reviewer_login": "reviewer", "pull_requests": _queue()}))
    for goal, agent, expected in [("first", "a", 1), ("first", "b", 5), ("second", "a", 1)]:
        assert main([*common, "pr-review", "--fixture", str(fixture), "--goal-id", goal, "--agent-id", agent,
                     "--autonomous-observation"]) == 0
        packet = json.loads(capsys.readouterr().out)
        assert packet["autonomous_review"]["candidate"]["number"] == expected
        if goal == "first":
            assert packet["autonomous_review"]["scheduling_policy"]["owner_logins"] == ["reviewer", "other"]
            assert all(row["owner_authored"] for row in packet["pull_requests"])
        else:
            assert "owner_logins" not in packet["scheduling_policy"]
    # New PRs after activation use the same scoped account rule; title text
    # cannot move an unrelated author into the owner group.
    future = [deepcopy(_queue()[2]) for _ in range(2)]
    for number, author, pr in [(7, "new-community", future[0]), (8, "other", future[1])]:
        pr.update(number=number, author={"login": author}, headRefOid=f"{number:040x}",
                  createdAt="2026-09-04T00:00:00Z", updatedAt="2026-09-04T00:00:00Z", title="other reviewer")
    fixture.write_text(json.dumps({"repository": "owner/repo", "reviewer_login": "reviewer", "pull_requests": [*_queue(), *future]}))
    for agent, expected in [("a", 7), ("b", 8)]:
        assert main([*common, "pr-review", "--fixture", str(fixture), "--goal-id", "first", "--agent-id", agent]) == 0
        packet = json.loads(capsys.readouterr().out)
        assert _sequence(packet)[0] == expected
        assert next(row for row in packet["pull_requests"] if row["number"] == 7)["owner_authored"] is False
    assert main([*common, "configure-goal", "--goal-id", "first", "--clear-pr-review-owner-logins", "--execute"]) == 0
    capsys.readouterr()
    saved = json.loads(registry.read_text())["goals"][0]["control_plane"]["pull_request_review"]
    assert saved["owner_logins"] == []
    assert saved["agent_orders"] == {"a": "forward", "b": "reverse"}
