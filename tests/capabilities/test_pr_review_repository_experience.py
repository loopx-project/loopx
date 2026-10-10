"""Real repository-source readback and exact-head CLI context delivery.

Enablement fixtures represent an already qualified configuration. Provider
enablement itself retains its separate production preflight coverage.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.pr_review import build_pr_review_packet, render_pr_review_markdown
from loopx.capabilities.pr_review_queue import repository_experience as source
from tests.capabilities.test_reward_memory_experiment import (
    _experiment, _v1_config, _write_v1_config,
)

ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "loopx/capabilities/pr_review_queue/experiences/loopx-project/loopx/pr-5944-v1.json"
HEAD = "a" * 40


def enabled_goal(tmp_path):
    registry, _, _ = _experiment(tmp_path)
    config = _v1_config()
    review = deepcopy(config["surfaces"][0])
    review["surface_id"] = source.REPOSITORY_REVIEW_SURFACE_ID
    config["surfaces"].append(review)
    for entry in config["corpora"][:2]:
        entry["corpus"]["scope"]["surface_ids"].append(source.REPOSITORY_REVIEW_SURFACE_ID)
        entry["standing_policy"]["scope"]["surface_ids"].append(source.REPOSITORY_REVIEW_SURFACE_ID)
    _write_v1_config(registry, config)
    return registry, json.loads(registry.read_text())["goals"][0]


def rows():
    return [{"number": 8001, "headRefOid": HEAD, "state": "OPEN",
        "title": "RFC: recovery design and phased user outcome",
        "url": "https://github.com/loopx-project/loopx/pull/8001",
        "author": {"login": "contributor"}, "body": "Review the first recovered Goal.",
        "files": [{"path": "docs/architecture/rfcs/recovery.md", "additions": 8, "deletions": 0}],
        "changedFiles": 1, "isDraft": False, "reviews": []}]


def packet(repository="loopx-project/loopx"):
    return build_pr_review_packet(pull_requests=rows(), repository=repository, source="fixture", limit=10)


def test_real_file_recall_delivers_advice_not_a_verdict_or_utility(tmp_path):
    _, goal = enabled_goal(tmp_path)
    value = packet()
    source.attach_repository_review_experience(value, goal=goal, agent_id="pilot")
    memory = value["pull_requests"][0]["repository_experience"]
    decision = memory["decision"]
    assert decision["status"] == "context_delivered"
    assert decision["context_delivery_verified"] is True
    assert decision["artifact_ref"].endswith(f":8001:{HEAD}")
    assert decision["semantic_disposition"] is None
    assert decision["decision_consumption_complete"] is False
    assert decision["utility_verified"] is False
    assert decision["grants_new_action_authority"] is False
    assert decision["external_writes_performed"] is False
    experience = memory["guidance"][0]["experience"]
    assert experience == json.loads(ASSET.read_text())
    assert "first useful user outcome" in experience["future_behavior"]["action"]
    assert "APPROVE" not in json.dumps(memory)
    assert "REQUEST_CHANGES" not in json.dumps(memory)
    assert "Repository experience (advisory)" in render_pr_review_markdown(value)


@pytest.mark.parametrize("condition", ["no_goal", "no_agent", "disabled", "not_enabled",
    "recall_off", "config_drift", "wrong_surface", "unverified", "different_repository", "inactive", "readiness"])
def test_off_and_unqualified_paths_preserve_the_entire_packet(tmp_path, condition):
    registry, goal = enabled_goal(tmp_path)
    agent = "pilot"
    value = packet("other/repository" if condition == "different_repository" else "loopx-project/loopx")
    if condition == "no_goal":
        goal = None
    elif condition == "no_agent":
        agent = None
    elif condition == "disabled":
        goal["control_plane"]["reward_memory"]["enabled"] = False
    elif condition == "not_enabled":
        agent = "meta"
    elif condition in {"recall_off", "config_drift", "wrong_surface"}:
        config = _v1_config()
        config["automation"]["automatic_recall"] = condition == "wrong_surface"
        _write_v1_config(registry, config)
        if condition in {"recall_off", "wrong_surface"}:
            goal = json.loads(registry.read_text())["goals"][0]
    elif condition == "unverified":
        goal["control_plane"]["reward_memory"]["enablement_receipts"] = {}
    elif condition == "inactive":
        value["pull_requests"][0]["review_action_kind"] = None
    elif condition == "readiness":
        value["pull_requests"][0]["review_action_kind"] = "qualify_pull_request_merge_readiness"
    before = deepcopy(value)
    source.attach_repository_review_experience(value, goal=goal, agent_id=agent)
    assert value == before


def test_malformed_or_changed_source_fails_open_without_partial_context(tmp_path, monkeypatch):
    _, goal = enabled_goal(tmp_path)
    root = tmp_path / "assets/experiences/loopx-project/loopx"
    root.mkdir(parents=True)
    target = root / "case.json"
    target.write_text(ASSET.read_text())
    (root / "invalid.json").write_text('{"schema_version":"unqualified"}')
    monkeypatch.setattr(source, "files", lambda package: tmp_path / "assets")
    value = packet()
    source.attach_repository_review_experience(value, goal=goal, agent_id="pilot")
    memory = value["pull_requests"][0]["repository_experience"]
    assert memory["guidance"] == []
    assert memory["decision"]["context_delivery_verified"] is False
    assert memory["decision"]["preserve_base_output"] is True
    (root / "invalid.json").unlink()
    original_read = Path.read_bytes
    reads = 0
    def changing_read(path):
        nonlocal reads
        if path == target:
            reads += 1
            if reads == 2:
                return b"changed"
        return original_read(path)
    monkeypatch.setattr(Path, "read_bytes", changing_read)
    value = packet()
    source.attach_repository_review_experience(value, goal=goal, agent_id="pilot")
    assert value["pull_requests"][0]["repository_experience"]["guidance"] == []


def test_cli_json_and_markdown_readback(tmp_path, capsys):
    registry, _ = enabled_goal(tmp_path)
    fixture = tmp_path / "prs.json"
    fixture.write_text(json.dumps({"pull_requests": rows()}))
    common = ["--registry", str(registry), "pr-review", "--goal-id", "reward-memory-goal",
              "--agent-id", "pilot", "--repo", "loopx-project/loopx", "--fixture", str(fixture)]
    assert main([*common, "--format", "json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["pull_requests"][0]["repository_experience"]["decision"]["context_delivery_verified"] is True
    assert main([*common, "--format", "markdown"]) == 0
    assert "first useful user outcome" in capsys.readouterr().out


@pytest.mark.parametrize("path, expected", [
    ("docs/architecture/rfcs/recovery.md", "context_delivered"),
    ("src/opaque_xyz.py", "empty"),
])
def test_cli_uses_normalized_changed_paths_with_an_ordinary_title(tmp_path, capsys, path, expected):
    registry, _ = enabled_goal(tmp_path)
    raw = rows()
    raw[0]["title"] = "Adjust wording"
    raw[0]["body"] = ""
    raw[0]["files"][0]["path"] = path
    fixture = tmp_path / "prs.json"
    fixture.write_text(json.dumps({"pull_requests": raw}))
    assert main(["--registry", str(registry), "pr-review", "--goal-id", "reward-memory-goal",
                 "--agent-id", "pilot", "--repo", "loopx-project/loopx", "--fixture", str(fixture),
                 "--format", "json"]) == 0
    item = json.loads(capsys.readouterr().out)["pull_requests"][0]
    assert "files" not in item
    assert item["key_files"][0]["path"] == path
    memory = item["repository_experience"]
    assert memory["decision"]["status"] == expected
    assert bool(memory["guidance"]) is (expected == "context_delivered")
