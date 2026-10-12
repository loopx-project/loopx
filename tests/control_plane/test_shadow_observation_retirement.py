"""Retained migration observations must survive source-producer retirement."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from shadow_e2e_fixture import workspace
from test_canonical_todo_writer_isolation import (
    isolated_todo_distribution, retirement_wheel,  # noqa: F401,F811
)


def _bytes(root: Path):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and path.suffix != ".lock"}


@pytest.mark.parametrize("producers_present", [True, False], ids=["present", "absent"])
def test_complete_shadow_observation_without_producers(
    tmp_path, isolated_todo_distribution, producers_present,  # noqa: F811 -- imported pytest fixture
):
    package, command = isolated_todo_distribution
    w = workspace(tmp_path / "shadow", bootstrap=False)
    w.command, w.cwd, w.package = tuple(command), tmp_path, package
    assert w.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    todo_id = w.add("Preserve the original source work")["todo_id"]
    w.add("Second source mutation")
    w.add("Third source mutation")
    if not producers_present:
        for name in ("runtime_shadow", "runtime_shadow_writer_adapter",
                     "local_authority_shadow_adapter", "local_authority_shadow_outbox"):
            (package / f"control_plane/coordination/{name}.py").unlink()
        check = subprocess.run([command[0], "-S", "-c",
            "import importlib.util; assert importlib.util.find_spec("
            "'loopx.control_plane.coordination.runtime_shadow') is None"],
            cwd=tmp_path, capture_output=True, text=True, timeout=45)
        assert check.returncode == 0, check.stdout + check.stderr

    def observe(*args, success=True):
        before = _bytes(w.registry.parent)
        result = w.cli("coordination-shadow", *args, success=success)
        assert _bytes(w.registry.parent) == before
        assert result["executed"] is False
        assert result["decision_read_from_shadow"] is False
        return result

    inspected = observe("inspect")
    assert inspected["inspection"]["status"] == "matched"
    qualified = observe("qualify", "--require-event-kind", "todo_add")
    assert qualified["qualification"]["status"] == "qualified"
    insufficient = observe("qualify", "--minimum-operations", "4", success=False)
    assert insufficient["ok"] is False
    assert insufficient["qualification"]["qualified"] is False
    candidate = observe("read-candidate", "--todo-id", todo_id)
    assert candidate["read_candidate"]["read_candidate_qualified"] is True
    missing = observe("read-candidate", "--todo-id", "todo_missing", success=False)
    assert missing["ok"] is False
    assert missing["read_candidate"]["read_candidate_qualified"] is False

    original = w.state.read_bytes()
    w.state.write_text(w.state.read_text().replace("Third source mutation", "Changed source work"))
    drifted = observe("inspect", success=False)
    assert drifted["inspection"]["parity_matches"] is False
    refused = observe("read-candidate", "--todo-id", todo_id, success=False)
    assert refused["ok"] is False
    assert refused["read_candidate"]["read_candidate_qualified"] is False
    w.state.write_bytes(original)
    assert observe("inspect")["inspection"]["status"] == "matched"

    store = next((w.runtime / "authority-shadow/file-v0").glob("authority-store-*.json"))
    stored = store.read_bytes()
    store.write_text("{unreadable historical store")
    for args in [("inspect",), ("qualify",), ("read-candidate", "--todo-id", todo_id)]:
        unavailable = observe(*args, success=False)
        assert unavailable["ok"] is False
        detail = unavailable[{"inspect": "inspection", "qualify": "qualification",
                              "read-candidate": "read_candidate"}[args[0]]]
        assert detail["reason_code"] == "provider_protocol_violation"
    store.write_bytes(stored)
    assert observe("inspect")["inspection"]["status"] == "matched"

    store.rename(store.with_suffix(".retained"))
    store.mkdir()
    for args in [("inspect",), ("qualify",), ("read-candidate", "--todo-id", todo_id)]:
        unavailable = observe(*args, success=False)
        assert unavailable["ok"] is False
        detail = unavailable[{"inspect": "inspection", "qualify": "qualification",
                              "read-candidate": "read_candidate"}[args[0]]]
        assert detail["reason_code"] == "provider_read_unavailable"
    store.rmdir()
    store.with_suffix(".retained").rename(store)
    assert observe("inspect")["inspection"]["status"] == "matched"

    directory = store.parent
    retained = directory.with_name("retained-original")
    directory.rename(retained)
    assert observe("inspect", success=False)["inspection"]["parity_matches"] is False
    for args in [("qualify",), ("read-candidate", "--todo-id", todo_id)]:
        assert observe(*args, success=False)["ok"] is False
    assert not directory.exists()
    retained.rename(directory)
    assert observe("inspect")["inspection"]["status"] == "matched"

    registry = json.loads(w.registry.read_text())
    registry["goals"][0]["coordination"]["runtime_shadow"]["enabled"] = False
    w.registry.write_text(json.dumps(registry))
    for args in [("inspect",), ("qualify",), ("read-candidate", "--todo-id", todo_id)]:
        disabled = observe(*args, success=False)
        assert disabled["error_code"] == "coordination_shadow_not_enabled"


def test_historical_observation_imports_share_the_retained_owner():
    from loopx.control_plane.coordination import local_authority_shadow_readback as reader
    from loopx.control_plane.coordination import runtime_shadow as old

    for name in ("inspect_coordination_runtime_shadow", "qualify_coordination_runtime_shadow",
                 "read_coordination_runtime_shadow_todo_candidate"):
        assert getattr(old, name) is getattr(reader, name)
