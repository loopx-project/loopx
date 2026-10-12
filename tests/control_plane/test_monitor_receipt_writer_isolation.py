"""Retained Monitor receipts survive retirement of their original writer."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from test_canonical_todo_writer_isolation import (
    isolated_todo_distribution, retirement_wheel,  # noqa: F401 -- shared distribution fixtures
    _remove_monitor_source_modules,
)
from test_monitor_poll_transaction_recovery import (
    AGENT, GOAL, _business_receipt, _cli, _fixture, _poll_events, _quota_receipt,
)

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.local_authority_shadow_outbox import outbox_root
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import list_goal_todos


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("outbox_present", [False, True])
@pytest.mark.parametrize("writer_present", [False, True], ids=["absent", "present"])
def test_imported_monitor_receipt_settles_without_legacy_writer(
    tmp_path, provider, outbox_present, writer_present, isolated_todo_distribution,  # noqa: F811
):
    package, command = isolated_todo_distribution
    registry, runtime, state, _monitor, poll = _fixture(tmp_path)
    code, response = _cli(registry, runtime, *poll, fault="after")
    assert (code, response) == (83, None)
    original = _business_receipt(state)["writeback"]
    successor_ids = {item["todo_id"] for item in original["next_todos"]}
    pending_path = _quota_receipt(runtime)
    pending = json.loads(pending_path.read_text())
    pending["provider_plan"].pop("legacy_batch_version", None)
    pending_path.write_text(json.dumps(pending))
    items = list_goal_todos(registry_path=registry, goal_id=GOAL)["todos"]
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=items, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, GOAL, projection,
                                   state_path=state, provider=provider)

    def cli(*args):
        result = subprocess.run(
            [*command, "--registry", str(registry), "--runtime-root", str(runtime),
             "--format", "json", *args], cwd=tmp_path,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"], payload
        return payload

    if not writer_present:
        _remove_monitor_source_modules(package)
    # New work after cutover must survive recovery of the old operation.
    later = cli("todo", "add", "--goal-id", GOAL, "--role", "agent",
                "--text", "Later canonical work", "--claimed-by", AGENT,
                "--operation-id", "later-canonical-work")
    root = outbox_root(runtime, GOAL)
    if outbox_present:
        root.mkdir(parents=True, exist_ok=True)
        # Deliberately ambiguous old evidence must remain inert after cutover.
        (root / "retained.json").write_text('{"ambiguous_original":true}')

    def facts():
        return (state.read_bytes(), read_canonical_todos_if_promoted(
            runtime_root=runtime, goal_id=GOAL),
            {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    before = facts()
    assert later["todo_id"] in {t["todo_id"] for t in before[1]["todos"]}
    settled = cli(*poll)
    assert set(settled["successor_todo_ids"]) == successor_ids
    assert facts() == before
    assert len(_poll_events(runtime)) == 1
    events = _poll_events(runtime)
    assert cli(*poll)["replayed"] is True
    assert _poll_events(runtime) == events and facts() == before

    # Cold provider replay must remain independent of the same source modules.
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"plan": pending["provider_plan"],
                                  "registry": str(registry), "runtime": str(runtime)}))
    child = """
import json, sys
from pathlib import Path
from loopx.control_plane.quota.monitor_poll import _provider_writeback
request = json.loads(Path(sys.argv[1]).read_text())
result = _provider_writeback(request['plan'], registry_path=Path(request['registry']),
                             runtime_root=Path(request['runtime']))
for changed in ({'next_agent_todo': 'Changed historical intent'},
                {'agent_id': 'different-observer'}, {'result_hash': 'different-evidence'}):
    try:
        _provider_writeback({**request['plan'], **changed},
                            registry_path=Path(request['registry']),
                            runtime_root=Path(request['runtime']))
    except ValueError as error:
        assert 'request mismatch' in str(error), str(error)
    else:
        raise AssertionError('different original-operation intent must be rejected')
assert 'loopx.control_plane.scheduler.legacy_monitor_poll' not in sys.modules
assert 'loopx.control_plane.coordination.local_authority_shadow_adapter' not in sys.modules
print(json.dumps(result))
"""
    result = subprocess.run([sys.executable, "-c", child, str(request)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    replay = json.loads(result.stdout)
    assert replay["provider_replayed"] is True
    assert {k: v for k, v in replay.items() if k != "provider_replayed"} == {
        k: v for k, v in original.items() if k != "provider_replayed"}
    assert facts() == before
