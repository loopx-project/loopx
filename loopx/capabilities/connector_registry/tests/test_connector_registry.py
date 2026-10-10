from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

from loopx.capabilities.connector_registry.core import (
    BUILTIN_CONNECTOR_CATALOG,
    list_connectors,
    load_connector_registry,
    rank_connectors,
    record_connector_use,
    register_connector,
    save_connector_registry,
)
from loopx.capabilities.connector_registry import cli as connector_cli


def test_builtin_catalog_covers_layers(tmp_path: Path) -> None:
    layers = {c["layer"] for c in BUILTIN_CONNECTOR_CATALOG}
    assert layers == {"L0", "L1", "L2", "L3", "L4", "L5"}
    ids = [c["id"] for c in BUILTIN_CONNECTOR_CATALOG]
    assert len(ids) == len(set(ids))
    assert "cninfo-announcement" in ids
    assert "ark-web-search" in ids


def test_register_use_rank_persists(tmp_path: Path) -> None:
    path = tmp_path / "connector-registry.json"
    state = load_connector_registry(path)
    result = register_connector(state, "probe-connector", status="supported",
                                value_tier="P0", layer="L1", kind="announcement")
    state2 = {**state, "connectors": result["connectors"], "usage": result["usage_map"]}
    used = record_connector_use(state2, "probe-connector", ok=True, ms=120)
    state3 = {**state2, "connectors": used["connectors"], "usage": used["usage_map"]}
    save_connector_registry(state3, path)

    reloaded = load_connector_registry(path)
    entry = next(c for c in reloaded["connectors"] if c["id"] == "probe-connector")
    assert entry["status"] == "supported"
    assert reloaded["usage"]["probe-connector"]["count"] == 1
    assert reloaded["usage"]["probe-connector"]["ok"] == 1

    ranked = rank_connectors(reloaded)
    top = ranked[0]
    assert top["score"] > 0
    packet = list_connectors(reloaded)
    assert packet["summary"]["supported"] >= 1
    assert json.dumps(packet, ensure_ascii=False)


def test_record_connector_use_rejects_negative_elapsed_time(tmp_path: Path) -> None:
    state = load_connector_registry(tmp_path / "connector-registry.json")

    with pytest.raises(ValueError, match="elapsed milliseconds must be non-negative"):
        record_connector_use(state, "sina-daily", ms=-7)

    assert state["usage"]["sina-daily"]["total_ms"] == 0


def test_cli_rejects_negative_elapsed_time_without_persisting(tmp_path: Path) -> None:
    path = tmp_path / "connector-registry.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "connector",
            "use",
            "sina-daily",
            "--ms",
            "-7",
            "--path",
            str(path),
            "--format",
            "json",
        ],
        check=False,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload == {
        "ok": False,
        "error": "elapsed milliseconds must be non-negative",
    }
    assert not path.exists()


def test_builtin_connector_updates_survive_catalog_resync(tmp_path: Path) -> None:
    path = tmp_path / "connector-registry.json"
    state = load_connector_registry(path)
    result = register_connector(
        state,
        "cninfo-announcement",
        status="supported",
        blocker="",
    )
    save_connector_registry(
        {**state, "connectors": result["connectors"], "usage": result["usage_map"]},
        path,
    )

    reloaded = load_connector_registry(path)
    entry = next(c for c in reloaded["connectors"] if c["id"] == "cninfo-announcement")
    assert entry["status"] == "supported"
    assert entry.get("blocker") == ""


def test_cli_path_override_reads_the_registry_it_writes(tmp_path: Path) -> None:
    path = tmp_path / "connector-registry.json"
    register = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "connector",
            "register",
            "probe-cli",
            "--status",
            "supported",
            "--value-tier",
            "P0",
            "--layer",
            "L1",
            "--path",
            str(path),
            "--format",
            "json",
        ],
        check=True,
        cwd=Path.cwd(),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    assert json.loads(register.stdout)["ok"] is True

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "connector",
            "list",
            "--path",
            str(path),
            "--format",
            "json",
        ],
        check=True,
        cwd=Path.cwd(),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    payload = json.loads(listed.stdout)
    assert any(row["id"] == "probe-cli" for row in payload["ranked"])

    ranked = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "connector",
            "rank",
            "--path",
            str(path),
            "--format",
            "json",
        ],
        check=True,
        cwd=Path.cwd(),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    assert any(row["id"] == "probe-cli" for row in json.loads(ranked.stdout)["ranked"])


@pytest.mark.parametrize("action", ("use", "register"))
def test_concurrent_cli_mutations_preserve_both_updates(
    tmp_path: Path, monkeypatch, action: str
) -> None:
    path = tmp_path / "connector-registry.json"
    save_connector_registry(load_connector_registry(path), path)
    real_load = connector_cli.load_connector_registry
    arrivals_lock = Lock()
    release_loads = Event()
    arrivals = 0

    def synchronized_load(registry_path=None):
        state = real_load(registry_path)
        nonlocal arrivals
        with arrivals_lock:
            arrivals += 1
            if arrivals == 2:
                release_loads.set()
        # Load before waiting: without a mutation lock, both callers now hold
        # the same pre-update state. With the lock, the second caller cannot
        # load until the first caller has saved its update.
        release_loads.wait(timeout=0.2)
        return state

    monkeypatch.setattr(connector_cli, "load_connector_registry", synchronized_load)

    def mutate(index: int) -> int | None:
        connector_id = "sina-daily" if action == "use" else f"race-{index}"
        args = Namespace(
            command="connector",
            connector_action=action,
            connector_id=connector_id,
            fail=False,
            ms=10,
            note=None,
            path=path,
            name=None,
            layer=None,
            kind=None,
            status="supported",
            credential=None,
            value_tier=None,
            purpose=None,
            blocker=None,
        )
        return connector_cli.handle_connector_command(
            args,
            output_format=lambda _args: "json",
            print_payload=lambda *_args: None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(mutate, range(2)))

    assert results == [0, 0]
    state = load_connector_registry(path)
    if action == "use":
        usage = state["usage"]["sina-daily"]
        assert usage["count"] == 2
        assert usage["ok"] == 2
    else:
        stored_ids = {row["id"] for row in state["connectors"]}
        assert {"race-0", "race-1"} <= stored_ids
