"""Collaboration scans ignore Windows lock metadata beside real records."""

import json
import subprocess
import sys

import pytest

from loopx.control_plane.collaboration.inbox import pending
from loopx.control_plane.collaboration.peers import request, returns


def test_sidecars(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("x")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "delivery",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["sender", "receiver"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    brief = {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Review the result",
        "context": "Synthetic collaboration regression",
        "constraints": [],
        "inputs": [],
        "acceptance": ["Return a conclusion"],
        "return_requirement": "Report the review result",
    }
    receipt = request(
        tmp_path, registry, "delivery", "sender", "receiver", "review-1", brief
    )
    store = tmp_path / ".local" / "manager-context"
    entry = next((store / "entries").glob(f"*/{receipt['request_id']}.json"))
    operation_id = json.loads(entry.read_text(encoding="utf-8"))["source_id"][5:]
    operation = next((store / "peer-operations").glob(f"*/{operation_id}.json"))

    for record in (entry, operation):
        sidecar = record.with_name(f"{record.stem}.lock.lock.holder.json")
        # Windows creates this on every lock acquisition. Simulate the same
        # persistent artifact on POSIX so both CI platforms guard the scan.
        if not sidecar.exists():
            sidecar.write_text(
                json.dumps({"schema_version": "file_lock_holder_v0"}),
                encoding="utf-8",
            )
        assert sidecar.exists()
    entry.with_name("foreign.json").write_text("{}", encoding="utf-8")

    page = pending(tmp_path, "delivery", "receiver")
    assert [item["request_id"] for item in page["items"]] == [receipt["request_id"]]
    assert returns(tmp_path, "delivery", "sender")["items"] == []

    read = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--runtime-root",
            str(tmp_path),
            "--registry",
            str(registry),
            "manager-inbox",
            "read",
            "--goal-id",
            "delivery",
            "--agent-id",
            "receiver",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert read.returncode == 0, (read.stdout, read.stderr)
    assert [item["request_id"] for item in json.loads(read.stdout)["items"]] == [
        receipt["request_id"]
    ]

    damaged_entry = json.loads(entry.read_text(encoding="utf-8"))
    damaged_entry["goal_id"] = "other"
    entry.write_text(json.dumps(damaged_entry), encoding="utf-8")
    with pytest.raises(ValueError, match="context inbox scope mismatch"):
        pending(tmp_path, "delivery", "receiver")

    damaged_operation = json.loads(operation.read_text(encoding="utf-8"))
    damaged_operation["goal_id"] = "other"
    operation.write_text(json.dumps(damaged_operation), encoding="utf-8")
    with pytest.raises(ValueError, match="peer return scope mismatch"):
        returns(tmp_path, "delivery", "sender")
