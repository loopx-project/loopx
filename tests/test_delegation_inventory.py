"""Journal recovery must preserve scope and expose incomplete readback honestly."""
import json

import pytest

from loopx.control_plane.collaboration.inbox import _read, _write
from test_local_delegation import brief, service as delegation_service

service = delegation_service


def test_pages_find_all_original_operations_without_dispatch(service, monkeypatch):
    root, runner = service
    starts = []
    monkeypatch.setattr(runner, "_spawn", starts.append)
    for i in range(7):
        runner.start("analysis", f"work-{i}", brief())
    starts.clear()
    seen = []
    cursor = None
    while True:
        page = runner.operations(limit=2, cursor=cursor)
        assert page["page_readback_complete"] and len(page["items"]) <= 2
        seen.extend(row["operation_id"] for row in page["items"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        assert page["next_cursor"] != cursor
        cursor = page["next_cursor"]
    assert sorted(seen) == [f"work-{i}" for i in range(7)]
    assert starts == [] and not (root / "host-started").exists()
    # Revoking one binding is not authority to report prior work absent or accepted.
    config = json.loads(runner.config.read_text())
    config["bindings"] = []
    runner.config.write_text(json.dumps(config))
    page = runner.operations()
    assert not page["page_readback_complete"] and len(page["items"]) == 7
    assert all(row["status"] == "unavailable" for row in page["items"])


def test_corruption_and_stopped_worker_do_not_hide_healthy_sibling(service, monkeypatch):
    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    for name in ["healthy", "stopped", "corrupt", "mismatched"]:
        runner.start("analysis", name, brief())
    row = _read(runner.path("stopped"))
    row["created_at"] = 0
    _write(runner.path("stopped"), row)
    runner.path("corrupt").write_text("{")
    row = _read(runner.path("mismatched"))
    row["identity"]["operation_id"] = "healthy"
    _write(runner.path("mismatched"), row)
    page = runner.operations()
    by_id = {row["operation_id"]: row for row in page["items"] if row["operation_id"]}
    assert by_id["healthy"]["status"] == "prepared"
    assert by_id["stopped"]["recovery_required"]
    assert len(page["items"]) == 4 and not page["page_readback_complete"]
    assert sum(row["status"] == "unavailable" for row in page["items"]) == 2
    # A stop receipt beside its record is not another record and reads back as stopped.
    assert runner.stop("healthy", execute=True)["phase"] == "settled"
    assert runner._stop_path(runner.path("healthy")).exists()
    # Nor is the record naming the native Host an operation's Turn launched.
    runner._host_process_record(runner.path("healthy")).write_text("{}")
    page = runner.operations()
    by_id = {row["operation_id"]: row for row in page["items"] if row["operation_id"]}
    assert len(page["items"]) == 4 and by_id["healthy"]["status"] == "stopped"
    assert not by_id["healthy"]["recovery_required"]


def test_unknown_requester_and_unreadable_source_are_not_empty_inventory(service, monkeypatch):
    _, runner = service
    runner.agent_id = "unregistered"
    with pytest.raises(ValueError, match="registered"):
        runner.operations()
    runner.agent_id = "lead"
    directory = runner.path("inventory").parent
    original = type(directory).iterdir

    def denied(path):
        if path == directory:
            raise PermissionError("fixture source denied")
        return original(path)

    monkeypatch.setattr(type(directory), "iterdir", denied)
    with pytest.raises(PermissionError):
        runner.operations()
