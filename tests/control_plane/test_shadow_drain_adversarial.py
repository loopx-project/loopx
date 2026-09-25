"""Adversarial recovery ordering through the public CLI and real file provider."""
from __future__ import annotations

from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRejected


from shadow_e2e_fixture import workspace
from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox


pytestmark = pytest.mark.stage2c_e2e


@pytest.mark.parametrize("window", ["before_commit", "after_commit", "after_cursor"])
def test_cleanup_permission_failure_reports_verified_commit_and_recovers(
    tmp_path: Path, window: str,
) -> None:
    """A failed local checkpoint cannot hide a proven candidate transaction."""
    w = workspace(tmp_path)
    w.crash(window, "todo", "add", "--role", "agent", "--text", "Durable despite cleanup failure")
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    mode = directory.stat().st_mode & 0o777
    directory.chmod(0o500)
    try:
        # Exercise a real filesystem denial, with the provider still writable.
        with pytest.raises(PermissionError):
            (directory / "permission-control").write_bytes(b"must not be writable")
        stopped = w.drain()
        assert stopped["ok"] is False and stopped["reason_code"], stopped
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
        view = adapter.read_local_authority_shadow(runtime_root=w.runtime, goal_id=w.goal, scan_limit=20)
        assert len(view["proof"]["transactions"]) == 2  # Baseline and the actual mutation.
        assert stopped["candidate_readback_verified"] is True, stopped
        assert stopped["provider_revision"] == view["provider_revision"], stopped
    finally:
        directory.chmod(mode)
    recovered = w.drain()
    assert recovered["ok"] is True and recovered["replayed"] == 1, recovered
    assert recovered["delivered"] == 0, recovered
    assert outbox.read_cursor(directory)["last_seq"] == 1
    assert {path.name for path in directory.iterdir()} == {"drain-cursor.json"}
    assert adapter.read_local_authority_shadow(
        runtime_root=w.runtime, goal_id=w.goal, scan_limit=20,
    )["proof"]["transactions"] == view["proof"]["transactions"]


def test_missing_cursor_cannot_reuse_a_sequence_when_the_next_writer_arrives_first(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    w.add("First complete transaction")
    w.add("Second complete transaction")
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    (directory / "drain-cursor.json").unlink()
    # No operator drain runs between evidence loss and the next public writer.
    result = w.add("Writer arrives before cursor recovery")
    assert result["ok"] is True
    recovered = w.drain()
    assert recovered["ok"] is True, recovered
    view = adapter.read_local_authority_shadow(runtime_root=w.runtime, goal_id=w.goal, scan_limit=20)
    receipts = [tx["receipts"][0] for tx in view["proof"]["transactions"][1:]]
    assert [receipt["seq"] for receipt in receipts] == [1, 2, 3]
    assert outbox.read_cursor(directory)["last_seq"] == 3


def test_small_recovery_budget_makes_progress_through_verified_residue(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    residue: dict[str, bytes] = {}
    for index in range(3):
        w.crash("after_commit", "todo", "add", "--role", "agent", "--text", f"Acknowledged transaction {index}")
        residue.update({path.name: path.read_bytes() for path in directory.glob("*.json") if path.name != "drain-cursor.json"})
        assert w.drain()["ok"] is True
    for name, data in residue.items():
        (directory / name).write_bytes(data)
    before = adapter.read_local_authority_shadow(runtime_root=w.runtime, goal_id=w.goal, scan_limit=20)
    for left in (2, 1, 0):
        result = w.drain(max_entries="1")
        assert result["ok"] is True, result
        assert result["replayed"] == 1, result
        assert len(outbox.list_entries(directory)) == left
    after = adapter.read_local_authority_shadow(runtime_root=w.runtime, goal_id=w.goal, scan_limit=20)
    assert after["proof"]["transactions"] == before["proof"]["transactions"]


def test_raw_residue_mismatch_preserves_every_file_before_any_cleanup(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    residue: dict[str, bytes] = {}
    for index in range(2):
        w.crash("after_commit", "todo", "add", "--role", "agent", "--text", f"Exact receipt {index}")
        residue.update({path.name: path.read_bytes() for path in directory.glob("*.json") if path.name != "drain-cursor.json"})
        assert w.drain()["ok"] is True
    for name, data in residue.items():
        (directory / name).write_bytes(data)
    last = sorted(directory.glob("*.prepared.json"))[-1]
    # Valid identical JSON semantics still do not match the receipt's exact bytes.
    last.write_bytes(last.read_bytes() + b"\n")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    result = w.drain()
    assert result["ok"] is False and result["reason_code"] == "outbox_receipt_mismatch", result
    assert result["reclaimed_residue"] == 0
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_real_prepare_io_failure_holds_public_primary_before_replace(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    directory.write_text("A real filesystem obstruction, not a directory")
    before = w.state.read_bytes()
    rejected = w.cli("handoff-mode", "set", "--mode", "soft_claim", success=False)
    assert rejected["ok"] is False, rejected
    assert w.state.read_bytes() == before
    assert directory.read_text() == "A real filesystem obstruction, not a directory"
    directory.unlink()
    w.cli("handoff-mode", "set", "--mode", "soft_claim")
    assert w.drain()["ok"] is True
    view = adapter.read_local_authority_shadow(runtime_root=w.runtime, goal_id=w.goal, scan_limit=20)
    assert view["head"]["handoff_mode"] == "soft_claim"
    assert len(view["proof"]["transactions"]) == 2
    assert view["proof"]["transactions"][1]["receipts"][0]["seq"] == 1


@pytest.mark.parametrize(
    ("window", "claimed_resolution"),
    [("before_replace", "committed_proven_by_readback"), ("before_marker", "abandoned")],
)
def test_native_markerless_resolution_requires_source_evidence(
    tmp_path: Path, window: str, claimed_resolution: str,
) -> None:
    w = workspace(tmp_path)
    w.crash(window, "todo", "add", "--role", "agent", "--text", "Source proof is not a caller flag")
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    [entry] = outbox.list_entries(directory)
    request = adapter._commit_entry_request(runtime_root=w.runtime, goal_id=w.goal, entry=entry)
    request["resolution"] = claimed_resolution
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(EffectRuntimeRejected, match="shadow_entry_selection_invalid"):
        adapter.effect_runtime_result("coordination.runtime_shadow.commit_entry", request, timeout=15)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    view = adapter.read_local_authority_shadow(runtime_root=w.runtime, goal_id=w.goal, scan_limit=20)
    assert len(view["proof"]["transactions"]) == 1


def test_registry_runtime_override_cannot_bypass_an_active_source_binding(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys
    from shadow_e2e_fixture import REPO

    w = workspace(tmp_path)
    for index in range(3):
        w.add(f"Qualification evidence {index}")
    before = w.state.read_bytes()
    other_root = tmp_path / "other-runtime"
    results = []
    for mode in ("soft_claim", "hard_lease"):
        argv = w.arguments("handoff-mode", "set", "--mode", mode)
        argv[argv.index("--runtime-root") + 1] = str(other_root)
        command = subprocess.run([sys.executable, "-m", "loopx.cli", *argv], cwd=REPO,
                                 capture_output=True, text=True, timeout=45)
        assert "Traceback" not in command.stderr, command.stderr
        results.append(json.loads(command.stdout))
    after_qualification = w.cli("coordination-shadow", "qualify", success=False)
    assert not any(result.get("ok") and result.get("changed") for result in results), {
        "override_results": results, "qualification_at_original_root": after_qualification,
    }
    assert w.state.read_bytes() == before




@pytest.mark.parametrize("missing", ["identity", "candidate"])
def test_missing_cursor_source_allocation_never_recreates_missing_store_files(
    tmp_path: Path, missing: str,
) -> None:
    w = workspace(tmp_path)
    w.add("Existing generation")
    directory = outbox.partition_directory(w.runtime, w.goal, "todos")
    (directory / "drain-cursor.json").unlink()
    store = w.runtime / "authority-shadow" / "file-v0"
    path = store / "store-identity" if missing == "identity" else next(store.glob("authority-store-*.json"))
    path.unlink()
    shadow = w.runtime / "authority-shadow"
    before = {str(item.relative_to(shadow)): item.read_bytes() for item in shadow.rglob("*") if item.is_file()}
    primary = w.state.read_bytes()
    result = w.cli("todo", "add", "--role", "agent", "--text", "Read-only recovery cannot bootstrap", success=False)
    assert result["ok"] is False, result
    assert w.state.read_bytes() == primary
    assert not path.exists()
    assert {str(item.relative_to(shadow)): item.read_bytes() for item in shadow.rglob("*") if item.is_file()} == before


def test_state_file_override_cannot_attribute_an_unbound_source_to_active_lineage(tmp_path: Path) -> None:
    w = workspace(tmp_path)
    other = tmp_path / "OTHER_ACTIVE_STATE.md"
    other.write_bytes(w.state.read_bytes())
    original = w.state.read_bytes()
    outcomes = []
    for mode in ("soft_claim", "hard_lease", "soft_claim", "hard_lease"):
        outcomes.append(w.cli("handoff-mode", "set", "--mode", mode, "--state-file", str(other), success=False))
    qualification = w.cli("coordination-shadow", "qualify", success=False)
    assert not any(result.get("ok") and result.get("changed") for result in outcomes), {
        "override_results": outcomes, "qualification_for_bound_source": qualification,
    }
    assert w.state.read_bytes() == original
