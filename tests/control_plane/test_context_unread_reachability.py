"""Current-capture detail discovery stays separate from delivery and retention."""
import argparse
import copy
import json
from pathlib import Path
import shlex

import pytest

from loopx.cli_commands.quota_capture import read_context_capture
from loopx.control_plane.turn_driver import codex_context as context
from test_codex_context_lifecycle import setup, project
from test_progressive_native_context import payload, outcome
from test_quota_settlement_cli import _run_cli


@pytest.mark.parametrize("previous_detail_read", [False, True])
@pytest.mark.parametrize("change", ["unchanged", "updated", "reordered"])
def test_unconfirmed_material_reaches_next_turn_work_from_current_capture(
    tmp_path, monkeypatch, previous_detail_read, change,
):
    from loopx.cli_commands.quota_capture import native_context_capture_view

    setup(tmp_path, monkeypatch)
    original = payload()
    original["interaction_contract"] = {"schema_version": "loopx_interaction_contract_v0",
        "agent_channel": {"work_context": {"sources": []}}}
    # Necessary inline text makes a file delta useful independently of the
    # heavy supporting material whose delivery has never been confirmed.
    outcome(original)["requirements"][0]["description"] = "Keep this whole acceptance clause. " * 300
    outcome(original)["requirements"][0]["agent_review"]["note"] += "Tail constraint: keep the report local."
    peer = copy.deepcopy(outcome(original))
    peer["id"] = "peer"
    peer["owner_agent_id"] = "other-agent"
    original["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["outcomes"].append(peer)
    _, first = project(tmp_path, payload=original)
    first_details = first["context"]["context_details"]
    first_index = first_details["unread_context_index"]
    assert first_index["coverage_confirmed"] is False
    assert first_index["source"] == {key: first_details[key] for key in ("path", "sha256")}
    registry, runtime = tmp_path / "no-registry.json", tmp_path / "no-runtime"
    navigation = native_context_capture_view(first, Path(first["recovery"]["path"]).parent,
        context_directory=str(tmp_path))
    argv = shlex.split(navigation["first_read_command"])
    code, initial = _run_cli(registry, runtime, *argv[argv.index("quota"):], cwd=tmp_path)
    assert code == 0 and initial["eof"], initial
    shown = {row["pointer"]: row["value"] for row in json.loads(initial["content"])["selections"]}
    assert shown["/context_details/unread_context_index"] == first_index

    def read_detail(source, pointer):
        words = ["quota", "context-read", "--capture-file", source["path"],
            "--capture-sha256", source["sha256"], "--select", pointer,
            "--page-bytes", "1200", "--max-page-output-bytes", "2600"]
        body, offset = [], 0
        while True:
            code, page = _run_cli(registry, runtime, *words, cwd=tmp_path)
            assert code == 0 and page["ok"] and page["offset"] == offset, page
            body.append(page["content"])
            if page["eof"]:
                return json.loads("".join(body))["selections"][0]["value"]
            offset = page["next_offset"]
            argv = shlex.split(page["next_command"])
            words = argv[argv.index("quota"):]

    if previous_detail_read:
        previous_note = next(pointer for pointer in first_index["pointers"]
            if pointer.endswith("/outcomes/0/requirements/0/agent_review/note"))
        assert read_detail(first_index["source"], previous_note).endswith("keep the report local.")
    current = copy.deepcopy(original)
    if change == "updated":
        outcome(current)["requirements"][0]["agent_review"]["note"] += " Updated constraint: include the contrary result."
    if change == "reordered":
        current["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["outcomes"].reverse()
    _, warm = project(tmp_path, "next-turn", payload=current)
    assert warm["mode"] == "delta"
    required = json.loads(Path(warm["recovery"]["path"]).read_bytes())
    index = required["context_details"]["unread_context_index"]
    assert index["coverage_confirmed"] is False
    assert Path(index["source"]["path"]).name == "projection.json"
    assert index["source"]["path"] != first_index["source"]["path"]

    def literal_index(value):
        if isinstance(value, dict):
            found = value.get("unread_context_index")
            if found == index or isinstance(found, dict) and found.get("value") == index:
                return True
            return any(literal_index(child) for child in value.values())
        return isinstance(value, list) and any(literal_index(child) for child in value)

    assert literal_index(warm["changes"]), "Warm discovery must not require an inherited index."
    rows = current["planning_horizon"]["core_goal"]["acceptance_contract"]["outcome_requirements"]["outcomes"]
    position = next(i for i, row in enumerate(rows) if row["id"] == "outcome")
    pointer = (f"/planning_horizon/core_goal/acceptance_contract/outcome_requirements/outcomes/{position}"
        "/requirements/0/agent_review/note")
    assert pointer in index["pointers"]
    peer_position = next(i for i, row in enumerate(rows) if row["id"] == "peer")
    assert (f"/planning_horizon/core_goal/acceptance_contract/outcome_requirements/outcomes/{peer_position}/sources"
        in index["pointers"])
    # No previous packet, retained model input or patch chain is available to
    # this no-hook reader. Its actual next artifact must use the recovered tail.
    Path(first["recovery"]["path"]).unlink()
    Path(first_details["path"]).unlink()
    material = read_detail(index["source"], pointer)
    artifact = tmp_path / "next-turn-report.txt"
    artifact.write_text(material)
    assert artifact.read_text() == rows[position]["requirements"][0]["agent_review"]["note"]
    assert "Tail constraint: keep the report local." in artifact.read_text()
    assert not {"confirmed_read_sections", "consumed_pointers"} & context._state(tmp_path).keys()
    assert not registry.exists() and not runtime.exists()


def test_removed_material_does_not_keep_a_predecessor_discovery_route(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    original = payload()
    _, first = project(tmp_path, payload=original)
    assert first["context"]["context_details"]["unread_context_index"]["pointers"]
    current = copy.deepcopy(original)
    outcome(current)["sources"] = []
    outcome(current)["requirements"][0].pop("agent_review")
    _, next_turn = project(tmp_path, "removed", payload=current)
    recovery = next_turn["recovery"]
    assert json.loads(Path(recovery["path"]).read_bytes()) == current
    missing = read_context_capture(argparse.Namespace(capture_file=recovery["path"],
        capture_sha256=recovery["sha256"], select=["/context_details/unread_context_index"],
        offset=0, page_bytes=12000, max_page_output_bytes=18000, format="json"))
    assert missing["ok"] is False
