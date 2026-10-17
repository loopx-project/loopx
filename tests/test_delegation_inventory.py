"""Journal recovery must preserve scope and expose incomplete readback honestly."""
import json
from hashlib import sha256

import pytest

from loopx.control_plane.collaboration import delegation_inventory
from loopx.control_plane.collaboration.inbox import _read, _write
from loopx.control_plane.effect_runtime import effect_runtime_result
from test_local_delegation import brief, service as delegation_service

service = delegation_service


def test_cli_inventory_discloses_page_scope_and_unreadable_operations(service, monkeypatch):
    from test_delegation_cli import cli

    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    for name in ["first", "second", "third", "fourth", "fifth"]:
        runner.start("analysis", name, brief())
    before = {path.name: path.read_bytes() for path in runner.path("inventory").parent.glob("*.json")}

    status, page = cli(runner, "operations", "--limit", "2")
    assert status == 0 and page["has_more"]
    envelope = page["projection_envelope"]
    assert envelope["fresh"] and envelope["complete"] and not envelope["alert"]
    assert envelope["coverage"]["scope"] == "requester_page"
    assert envelope["coverage"]["shown_count"] == 2
    assert envelope["coverage"]["available_count"] == 5
    assert envelope["coverage"]["truncated"]  # Complete page is not the whole inventory.
    # Lock-holder diagnostics change on reads; the operation records must not.
    assert {path.name: path.read_bytes() for path in runner.path("inventory").parent.glob("*.json")} == before

    runner.path(page["items"][0]["operation_id"]).write_text("{")
    status, damaged = cli(runner, "operations", "--limit", "2")
    assert status == 0 and len(damaged["items"]) == 2
    assert not damaged["page_readback_complete"]
    envelope = damaged["projection_envelope"]
    assert not envelope["fresh"] and not envelope["complete"] and envelope["alert"]
    assert envelope["alert_reasons"] == ["incomplete_coverage", "unreadable_sources"]
    assert envelope["coverage"]["omitted"] == [{"reason": "unavailable_operations", "count": 1, "refs": []}]
    assert not (root / "host-started").exists()


def test_inventory_source_read_times_survive_assembly_and_replay(service, monkeypatch):
    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "slow-read", brief())
    times = iter(["2026-10-01T10:00:00Z", "2026-10-01T10:00:01Z", "2026-10-01T10:06:00Z"])
    monkeypatch.setattr(delegation_inventory, "now_utc_iso", lambda: next(times))
    envelope = runner.operations()["projection_envelope"]
    assert not envelope["fresh"] and envelope["complete"]
    assert envelope["alert_reasons"] == ["stale_sources"]
    assert [row["last_read_at"] for row in envelope["sources"]] == [
        "2026-10-01T10:00:00Z", "2026-10-01T10:00:01Z"]
    replay = effect_runtime_result("projection.envelope.seal", {
        "schema_version": "loopx_projection_envelope_serve_request_v0", "envelope": envelope,
        "served_at": "2026-10-01T10:07:00Z"})
    assert replay["observed_at"] == envelope["observed_at"]
    assert replay["served_at"] == "2026-10-01T10:07:00Z"
    assert not replay["fresh"] and replay["age_seconds"] == 60


def test_pages_find_all_original_operations_without_dispatch(service, monkeypatch):
    root, runner = service
    starts = []
    monkeypatch.setattr(runner, "_spawn", starts.append)
    for i in range(7):
        runner.start("analysis", f"work-{i}", brief())
    starts.clear()
    projection_requests = []
    real_projection = delegation_inventory.effect_runtime_result

    def traced_projection(method, *args, **kwargs):
        projection_requests.append(method)
        return real_projection(method, *args, **kwargs)

    monkeypatch.setattr(delegation_inventory, "effect_runtime_result", traced_projection)
    seen = []
    cursor = None
    while True:
        projection_requests.clear()
        page = runner.operations(limit=2, cursor=cursor)
        assert len(projection_requests) <= 2  # One preflight and one page reduction across the RPC boundary.
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
    projection_requests.clear()
    page = runner.operations()
    assert len(projection_requests) <= 2
    assert not page["page_readback_complete"] and len(page["items"]) == 7
    assert all(row["status"] == "unavailable" for row in page["items"])


def test_corruption_and_stopped_worker_do_not_hide_healthy_sibling(service, monkeypatch):
    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    for name in ["healthy", "stopped", "corrupt", "mismatched", "malformed"]:
        runner.start("analysis", name, brief())
    row = _read(runner.path("stopped"))
    row["created_at"] = 0
    _write(runner.path("stopped"), row)
    runner.path("corrupt").write_text("{")
    row = _read(runner.path("mismatched"))
    row["identity"]["operation_id"] = "healthy"
    _write(runner.path("mismatched"), row)
    row = _read(runner.path("malformed"))
    row["status"] = "not-an-observation"
    _write(runner.path("malformed"), row)
    page = runner.operations()
    by_id = {row["operation_id"]: row for row in page["items"] if row["operation_id"]}
    assert by_id["healthy"]["status"] == "prepared"
    assert by_id["stopped"]["recovery_required"]
    assert len(page["items"]) == 5 and not page["page_readback_complete"]
    assert by_id["malformed"]["status"] == "unavailable"
    assert sum(row["status"] == "unavailable" for row in page["items"]) == 3
    # A stop receipt beside its record is not another record and reads back as stopped.
    assert runner.stop("healthy", execute=True)["phase"] == "settled"
    assert runner._stop_path(runner.path("healthy")).exists()
    # Nor is the record naming the native Host an operation's Turn launched.
    runner._host_process_record(runner.path("healthy")).write_text("{}")
    page = runner.operations()
    by_id = {row["operation_id"]: row for row in page["items"] if row["operation_id"]}
    assert len(page["items"]) == 5 and by_id["healthy"]["status"] == "stopped"
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


def test_full_page_preserves_accepted_references_above_inline_transport_budget(service):
    record_ids = sorted(sha256(str(index).encode()).hexdigest() for index in range(50))
    observations = [{
        "record": {"record_id": record_id, "operation_id": f"result-{index}"},
        "observation": {
            "operation_id": f"result-{index}", "request_id": f"request-{index}",
            "agent_id": "worker", "todo_id": f"todo_{index}", "status": "accepted",
            "worker_active": False, "recovery_required": False,
            "artifacts": [{"ref": f"output-{index}.json", "sha256": "a" * 64,
                           "text": "private body " * 4000}],
        },
    } for index, record_id in enumerate(record_ids)]
    request = {"query": {"limit": 50, "cursor": None}, "record_ids": record_ids,
               "observations": observations,
               "read_facts": {"observed_at": "2026-10-01T10:00:02Z",
                              "directory_read_at": "2026-10-01T10:00:00Z",
                              "operations_read_at": "2026-10-01T10:00:01Z", "available_count": 50}}
    assert len(json.dumps(request).encode()) > 2 * 1024 * 1024

    page = effect_runtime_result("collaboration.delegation.inventory_page", request,
                                 large_local_snapshot=True)

    assert page["page_readback_complete"] and not page["has_more"]
    assert page["next_cursor"] is None
    assert [row["record_id"] for row in page["items"]] == record_ids
    assert all(row["status"] == "accepted" for row in page["items"])
    assert [row["artifacts"] for row in page["items"]] == [
        [{"ref": f"output-{index}.json", "sha256": "a" * 64}] for index in range(50)
    ]
