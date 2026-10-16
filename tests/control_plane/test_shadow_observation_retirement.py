"""Retained migration observations must survive source-producer retirement."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from loopx.control_plane.coordination.authority_source_capture import (
    build_runtime_shadow_source_snapshot,
)
from shadow_e2e_fixture import workspace
from test_canonical_todo_writer_isolation import (
    isolated_todo_distribution, retirement_wheel,  # noqa: F401,F811
)

REPO = Path(__file__).resolve().parents[2]


def _bytes(root: Path):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and path.suffix != ".lock"}


def _seed_historical_shadow_transactions(
    runtime: Path,
    state: Path,
    partitions: list[dict[str, object]],
) -> None:
    node = shutil.which("node")
    assert node is not None
    script = """
import {pathToFileURL} from "node:url";
import {join} from "node:path";
import {readFileSync} from "node:fs";
const [storePath, shadowPath, fixturePath, root, statePath] = process.argv.slice(1);
const {FileAuthorityStore} = await import(pathToFileURL(storePath).href);
const {commitLocalAuthorityShadowEntry} = await import(pathToFileURL(shadowPath).href);
const {pendingEntry, settleFiles} = await import(pathToFileURL(fixturePath).href);
const store = new FileAuthorityStore(join(root, "authority-shadow", "file-v0"), "goal-a",
  {existingOnly: true});
const loaded = await store.loadAuthority();
if (loaded.status !== "loaded") throw new Error(`shadow bootstrap missing: ${JSON.stringify(loaded)}`);
const fixture = {root, statePath, store, baseline: loaded.head};
const partitions = JSON.parse(readFileSync(0, "utf8"));
for (const [index, partition] of partitions.entries()) {
  const seq = index + 1;
  const request = await pendingEntry(fixture, seq, partition);
  const result = await commitLocalAuthorityShadowEntry(request);
  if (result.outcome !== "delivered") {
    throw new Error(`historical shadow seed failed: ${JSON.stringify(result)}`);
  }
  await settleFiles(fixture, request, result);
}
"""
    result = subprocess.run(
        [
            node,
            "--no-warnings",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            script,
            str(REPO / "loopx/control_plane/coordination/file_authority_store.ts"),
            str(REPO / "loopx/control_plane/coordination/local_authority_shadow.ts"),
            str(REPO / "tests/control_plane_ts/shadow_file_fixture.ts"),
            str(runtime),
            str(state),
        ],
        cwd=REPO,
        input=json.dumps(partitions),
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("bridges_present", [True, False], ids=["present", "absent"])
def test_complete_shadow_observation_without_producers(
    tmp_path, isolated_todo_distribution, bridges_present,  # noqa: F811 -- imported pytest fixture
):
    package, command = isolated_todo_distribution
    w = workspace(tmp_path / "shadow", bootstrap=False)
    registry = json.loads(w.registry.read_text())
    registry["goals"][0]["id"] = "goal-a"
    w.registry.write_text(json.dumps(registry))
    w.goal = "goal-a"
    todo_id = "todo_observed"
    header = (
        "---\ngoal_id: goal-a\nhandoff_mode: hard_lease\n"
        "updated_at: 2026-09-01T00:00:00+00:00\n---\n\n## Agent Todo\n\n"
    )
    records = [
        ("Preserve the original source work", todo_id),
        ("Second source mutation", "todo_second"),
        ("Third source mutation", "todo_third"),
    ]
    w.state.write_text(header, encoding="utf-8")
    w.command, w.cwd, w.package = tuple(command), tmp_path, package
    assert w.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    partitions = []
    body = header
    for text, record_id in records:
        body += (
            f"- [ ] {text}\n"
            f"  <!-- loopx:todo todo_id={record_id} role=agent "
            "task_class=advancement_task -->\n"
        )
        w.state.write_text(body, encoding="utf-8")
        projection, _snapshot = build_runtime_shadow_source_snapshot(
            goal=registry["goals"][0],
            runtime_root=w.runtime,
            state_path=w.state,
            registry_path=w.registry,
        )
        partitions.append({
            "handoff_mode": projection["handoff_mode"],
            "todos": projection["todos"],
        })
    source = w.state.read_bytes()
    _seed_historical_shadow_transactions(w.runtime, w.state, partitions)
    w.state.write_bytes(source)
    if not bridges_present:
        for name in ("runtime_shadow", "local_authority_shadow_adapter",
                     "local_authority_shadow_outbox"):
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
