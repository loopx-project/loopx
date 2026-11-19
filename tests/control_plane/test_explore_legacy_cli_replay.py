"""Replay real old-CLI primary/hook receipts through the public entry point."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _run_cli, _write_fixture,
)


FIXTURE = Path(__file__).with_name("fixtures") / "explore_legacy_cli_writeback_v0.json"


def _legacy_cli(tmp_path):
    original = json.loads(FIXTURE.read_text())
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["explore_graph"] = {"enabled": True}
    config["goals"][0]["spawn_policy"] = {
        "allowed": False, "explore_harness": {"enabled": True},
    }
    registry.write_text(json.dumps(config))
    # Restore only synthetic, base-produced persisted records in this disposable
    # runtime. Do not invent a primary receipt or mutate an existing real Goal.
    for relative, encoded in original["runtime_files"].items():
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if "json" in encoded:
            text = json.dumps(encoded["json"]) + "\n"
        elif "jsonl" in encoded:
            text = "".join(json.dumps(row) + "\n" for row in encoded["jsonl"])
        else:
            text = encoded["text"]
        target.write_text(text)
    primary = deepcopy(original["writeback"])
    assert primary["state"]["path"] == "$STATE_FILE"
    primary["state"]["path"] = str(project / config["goals"][0]["state_file"])
    json_path = runtime / original["writeback_relative_path"]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(primary) + "\n")
    index = {key: primary[key] for key in original["index_fields"]}
    index.update(original["index_overrides"])
    index.update(json_path=str(json_path), markdown_path=str(json_path.with_suffix(".md")))
    (json_path.parent / "index.jsonl").write_text(json.dumps(index) + "\n")
    attachment = tmp_path / "attachment.json"
    attachment.write_text(json.dumps(original["attachment"]))
    command = (
        "refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
        "--classification", "validated_change", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--no-global-sync",
        "--suppress-external-sinks", "--explore-result-json", str(attachment),
    )
    return project, runtime, registry, attachment, command


def _business_files(runtime):
    return {
        str(path.relative_to(runtime)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(runtime.rglob("*"))
        if path.is_file() and not path.name.endswith((".lock", "-wal", "-shm"))
    }


@pytest.mark.parametrize("mutation", [None, "attachment", "missing-graph", "missing-hook"])
def test_old_successful_command_replays_only_its_completed_delivery(tmp_path, mutation):
    project, runtime, registry, attachment, command = _legacy_cli(tmp_path)
    if mutation == "attachment":
        changed = json.loads(attachment.read_text())
        changed["interpretation"] = "A changed request cannot borrow the old success."
        attachment.write_text(json.dumps(changed))
    elif mutation == "missing-graph":
        (runtime / "goals" / GOAL_ID / "explore-result-log.jsonl").unlink()
    elif mutation == "missing-hook":
        for receipt in (runtime / "goals" / GOAL_ID / "post_writeback_hooks").glob("*.json"):
            receipt.unlink()
    before = _business_files(runtime)
    code, replay = _run_cli(registry, runtime, *command, cwd=project)
    assert not replay["appended"], replay
    if mutation is None:
        assert code == 0 and replay["idempotent_replay"], replay
        delivery = replay["explore_result_delivery"]
        assert delivery["ok"] and delivery["idempotent_replay"]
        assert delivery["hook"]["invoked_count"] == 0
        assert _business_files(runtime) == before
        code, again = _run_cli(registry, runtime, *command, cwd=project)
        assert code == 0 and again["explore_result_delivery"]["idempotent_replay"], again
        assert _business_files(runtime) == before
    else:
        assert code == 1 and not replay["ok"], replay
        assert not replay.get("explore_result_delivery", {}).get("ok"), replay
        after = _business_files(runtime)
        # A missing hook may gain a failed-dispatch receipt. It cannot mint a
        # valid intent, append graph evidence, mutate the Todo or recommit primary.
        stable_before = {key: value for key, value in before.items() if "/post_writeback_hooks/" not in key}
        stable_after = {key: value for key, value in after.items() if "/post_writeback_hooks/" not in key}
        assert stable_after == stable_before
