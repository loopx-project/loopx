#!/usr/bin/env python3
"""Compare legacy, file, and SQLite archive semantics from one live snapshot.

The selected Goal is read only. All mutations happen in a temporary Markdown
clone, an isolated file store, and an isolated SQLite store. Output contains
bounded counts and digest prefixes, never Todo text, identifiers, paths, or raw
projections.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from loopx.control_plane.coordination.runtime_shadow import (  # noqa: E402
    build_runtime_shadow_source_snapshot,
)
from loopx.history import load_registry  # noqa: E402
from loopx.paths import resolve_runtime_root  # noqa: E402
from loopx.state_refresh import resolve_goal_state  # noqa: E402
from loopx.todos import archive_completed_todos  # noqa: E402


NODE_REHEARSAL = r"""
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {FileAuthorityStore} from '__FILE_STORE__';
import {SqliteAuthorityStore} from '__SQLITE_STORE__';
import {executeCoordinationTodoArchiveCompleted} from '__ARCHIVE__';
import {canonicalAuthorityBytes} from '__CODEC__';
import {evaluateTodoResumeConditions} from '__RESUME__';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const requestedTodoIds = new Set(request.initial.todos.map((todo) => todo.todo_id));
assert.equal(
  request.initial.leases.filter((lease) => !requestedTodoIds.has(lease.todo_id)).length,
  0,
  'input projection has orphan leases',
);
const digest = (value) => createHash('sha256')
  .update(canonicalAuthorityBytes(value)).digest('hex');
const archiveFailureCategory = (result) => {
  const reason = String(result.reason ?? '');
  for (const [marker, category] of [
    ['lease references an unknown todo', 'orphan_lease'],
    ['deterministic todo_id order', 'todo_order'],
    ['read-model schema mismatch', 'read_model_schema'],
    ['read-model count mismatch', 'read_model_count'],
    ['read-model digest mismatch', 'read_model_digest'],
    ['read-model field contract mismatch', 'read_model_contract'],
    ['unversioned fields', 'unversioned_todo_fields'],
    ['omits required fields', 'missing_todo_fields'],
    ['invalid required semantics', 'invalid_todo_semantics'],
  ]) {
    if (reason.includes(marker)) return category;
  }
  return 'unclassified';
};
const semanticTodo = (todo) => {
  const result = {...todo};
  delete result.index;
  if (result.resume_condition) {
    result.resume_condition = {...result.resume_condition};
    // Imported Markdown location is presentation, not completion evidence.
    delete result.resume_condition.target_source_section;
  }
  return result;
};
const fileRoot = await mkdtemp(join(tmpdir(), 'loopx-three-arm-file-'));
const sqliteRoot = await mkdtemp(join(tmpdir(), 'loopx-three-arm-sqlite-'));
const stores = [
  ['file', new FileAuthorityStore(fileRoot, request.goal_id)],
  ['sqlite', new SqliteAuthorityStore(sqliteRoot, request.goal_id)],
];
const results = {};
try {
  for (const [name, store] of stores) {
    const initialized = await store.commitAuthority({
      expected_provider_revision: null,
      operation_id: `${name}-three-arm-initialize`,
      events: [],
      receipts: [],
      next_projection: request.initial,
    });
    assert.equal(initialized.status, 'applied', `${name} initialization failed`);
    // Lose the response after the actual backend commit. Recovery must read
    // the original receipt rather than attempt the archive a second time.
    let commitCount = 0;
    const responseLostStore = {
      storeIdentity: () => store.storeIdentity(),
      loadAuthority: () => store.loadAuthority(),
      readReceipt: (id) => store.readReceipt(id),
      scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit),
      commitAuthority: async (request) => {
        commitCount++;
        const committed = await store.commitAuthority(request);
        assert.equal(committed.status, 'applied');
        return {status: 'ambiguous', reason_code: 'synthetic_response_loss',
          reason: 'isolated rehearsal discarded the commit response'};
      },
    };
    const archiveRequest = {
      goal_id: request.goal_id,
      role: request.role,
      max_active_done: request.max_active_done,
      operation_id: `${name}-three-arm-archive`,
      dry_run: false,
      now: new Date('2026-01-01T00:00:00Z'),
    };
    const archived = await executeCoordinationTodoArchiveCompleted(responseLostStore, archiveRequest);
    assert.equal(commitCount, 1);
    assert.equal(
      archived.status,
      'recovered',
      `${name} archive failed (${String(archived.reason_code ?? 'unknown')}:` +
        `${archiveFailureCategory(archived)})`,
    );
    const loaded = await store.loadAuthority();
    assert.equal(loaded.status, 'loaded', `${name} readback failed`);
    const receipt = await store.readReceipt(`${name}-three-arm-archive`);
    assert.equal(receipt.status, 'found', `${name} receipt missing`);
    const firstPage = await store.scanCommitted(null, 1);
    assert.equal(firstPage.status, 'page', `${name} first journal page failed`);
    assert.equal(firstPage.has_more, true);
    assert.deepEqual(firstPage.transactions[0].projection, request.initial);
    const finalPage = await store.scanCommitted(firstPage.next_cursor, 1);
    assert.equal(finalPage.status, 'page', `${name} final journal page failed`);
    assert.equal(finalPage.has_more, false);
    assert.deepEqual(finalPage.transactions[0].projection, loaded.head);
    assert.equal(finalPage.transactions[0].provider_revision, loaded.provider_revision);
    assert.deepEqual(finalPage.transactions[0].receipts, receipt.receipts);
    const end = await store.scanCommitted(finalPage.next_cursor, 1);
    assert.deepEqual(end, {status: 'page', transactions: [],
      next_cursor: finalPage.next_cursor, has_more: false});
    assert.deepEqual(await store.loadAuthority(), loaded, 'journal reads changed authority');
    const replay = await executeCoordinationTodoArchiveCompleted(store, archiveRequest);
    assert.equal(replay.status, 'replayed');
    assert.equal(replay.cursor, archived.cursor);
    assert.deepEqual(await store.loadAuthority(), loaded);
    results[name] = {archived, head: loaded.head};
  }

  assert.equal(
    digest(results.file.head),
    digest(results.sqlite.head),
    'file and SQLite heads differ',
  );
  const initialById = new Map(request.initial.todos.map((todo) => [todo.todo_id, todo]));
  const providerById = new Map(results.file.head.todos.map((todo) => [todo.todo_id, todo]));
  const moved = results.file.head.todos.filter((todo) =>
    todo.archive_state === 'archive' &&
    initialById.get(todo.todo_id)?.archive_state !== 'archive');
  const movedIds = new Set(moved.map((todo) => todo.todo_id));
  const legacyActive = request.legacy.todos.filter(todo => todo.archive_state === 'active');
  const legacyIds = new Set(legacyActive.map((todo) => todo.todo_id));
  const removedByLegacy = request.initial.todos
    .filter((todo) => todo.archive_state === 'active' && !legacyIds.has(todo.todo_id))
    .map((todo) => todo.todo_id)
    .sort();
  assert.equal(
    digest([...movedIds].sort()),
    digest(removedByLegacy),
    'legacy and provider archive selection differs',
  );
  assert.equal(moved.length, request.legacy_moved_count, 'archive counts differ');

  const initialUntouched = request.initial.todos
    .filter((todo) => !movedIds.has(todo.todo_id))
    .map(semanticTodo);
  const providerUntouched = results.file.head.todos
    .filter((todo) => !movedIds.has(todo.todo_id))
    .map(semanticTodo);
  assert.equal(
    digest(initialUntouched),
    digest(providerUntouched),
    'non-target Todo semantics changed',
  );
  assert.equal(
    digest(results.file.head.leases),
    digest(request.initial.leases),
    'provider archive changed lease history',
  );

  // Legacy Markdown moves archived blocks outside its hot projection and
  // compacts the remaining display indexes. Provider heads retain archived
  // records and stable imported indexes. Compare active domain records without
  // absolute display ordinals, then prove the per-role relative order itself.
  // Compare current consumer semantics, not stale derived diagnostics stored
  // before archive. The full post-commit head remains the only fact source.
  const evaluated = evaluateTodoResumeConditions({
    schema_version: 'todo_resume_evaluation_request_v0',
    items: results.file.head.todos, source_items: results.file.head.todos,
    kinds: ['todo_done', 'monitor_changed'],
  });
  const conditions = new Map(evaluated.conditions.map(entry => [entry.todo_id, entry.condition]));
  const activeTodos = results.file.head.todos.filter(todo => todo.archive_state === 'active')
    .map(todo => conditions.has(todo.todo_id) ? {...todo,
      resume_condition: conditions.get(todo.todo_id), resume_ready: conditions.get(todo.todo_id).satisfied === true} : todo);
  const activeIds = new Set(activeTodos.map((todo) => todo.todo_id));
  const activeLeases = results.file.head.leases.filter((lease) =>
    activeIds.has(lease.todo_id));
  assert.equal(
    digest(activeTodos.map(semanticTodo)),
    digest(legacyActive.map(semanticTodo)),
    'legacy and provider active Todo semantics differ',
  );
  assert.equal(
    digest(activeLeases),
    digest(request.legacy.leases),
    'legacy and provider active lease semantics differ',
  );
  for (const role of ['agent', 'user']) {
    const order = (todos) => todos
      .filter((todo) => todo.role === role)
      .sort((left, right) => Number(left.index) - Number(right.index))
      .map((todo) => todo.todo_id);
    assert.equal(
      digest(order(activeTodos)),
      digest(order(legacyActive)),
      `${role} relative order differs`,
    );
  }
  const movedDigest = createHash('sha256')
    .update([...movedIds].sort().join('\n'))
    .digest('hex');
  process.stdout.write(JSON.stringify({
    schema_version: 'loopx_authority_three_arm_rehearsal_result_v0',
    status: 'passed',
    todo_count: request.initial.todos.length,
    lease_count: request.initial.leases.length,
    moved_count: moved.length,
    active_todo_count_after: activeTodos.length,
    active_lease_count_after: activeLeases.length,
    moved_ids_sha256_prefix: movedDigest.slice(0, 16),
    provider_heads_exact: true,
    journal_pages_exact: true,
    legacy_active_semantics_exact: true,
    relative_order_exact: true,
    non_target_semantics_unchanged: true,
    provider_receipts_found: true,
  }));
} finally {
  await rm(fileRoot, {recursive: true, force: true});
  await rm(sqliteRoot, {recursive: true, force: true});
}
"""


def _module_uri(repository: Path, relative: str) -> str:
    return (repository / relative).resolve().as_uri()


def _node_script(repository: Path) -> str:
    return (
        NODE_REHEARSAL.replace(
            "__FILE_STORE__",
            _module_uri(
                repository,
                "loopx/control_plane/coordination/file_authority_store.ts",
            ),
        )
        .replace(
            "__SQLITE_STORE__",
            _module_uri(
                repository,
                "loopx/control_plane/coordination/sqlite_authority_store.ts",
            ),
        )
        .replace(
            "__ARCHIVE__",
            _module_uri(
                repository,
                "loopx/control_plane/coordination/todo_archive.ts",
            ),
        )
        .replace(
            "__CODEC__",
            _module_uri(
                repository,
                "loopx/control_plane/coordination/authority_store_codec.ts",
            ),
        )
        .replace("__RESUME__", _module_uri(repository, "loopx/control_plane/todos/resume_condition.ts"))
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--role", choices=("agent", "user"), default="agent")
    parser.add_argument("--max-active-done", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.max_active_done < 0:
        raise SystemExit("--max-active-done must be non-negative")

    registry_path = args.registry.expanduser().resolve()
    registry = load_registry(registry_path)
    goal = next(
        (item for item in registry.get("goals", []) if item.get("id") == args.goal_id),
        None,
    )
    if goal is None:
        raise SystemExit("goal is not registered")
    runtime_root = resolve_runtime_root(registry, None, registry_path=registry_path)
    _, _, state_path = resolve_goal_state(
        registry=registry,
        goal_id=args.goal_id,
        project_override=None,
        state_file_override=None,
    )
    source_bytes = state_path.read_bytes()
    initial, source_snapshot = build_runtime_shadow_source_snapshot(
        goal=goal,
        runtime_root=runtime_root,
        state_path=state_path,
        registry_path=registry_path,
    )
    # The live source arm is a point-in-time input. Keep it detached from any
    # compatibility code exercised by the cloned legacy arm below.
    initial = copy.deepcopy(initial)
    captured_ids = {item["todo_id"] for item in initial["todos"]}
    missing_history = sum(
        1 for item in initial["todos"]
        if isinstance((condition := item.get("resume_condition")), dict)
        and condition.get("kind") == "todo_done"
        and condition.get("target_status") == "done"
        and condition.get("target_archive_state") == "archive"
        and condition.get("target_todo_id") not in captured_ids
    )
    if missing_history:
        raise SystemExit(
            f"source projection omits {missing_history} archived resume target records; "
            "promotion rehearsal held (derived readiness is not canonical evidence)"
        )

    with tempfile.TemporaryDirectory(prefix="loopx-three-arm-legacy-") as temporary:
        root = Path(temporary)
        project = root / "project"
        clone_runtime = root / "runtime"
        clone_state = project / "ACTIVE_GOAL_STATE.md"
        project.mkdir()
        clone_state.write_bytes(source_bytes)
        source_leases = runtime_root / "goals" / args.goal_id / "task-leases"
        if source_leases.exists():
            shutil.copytree(
                source_leases,
                clone_runtime / "goals" / args.goal_id / "task-leases",
            )
        clone_goal = copy.deepcopy(goal)
        clone_goal["repo"] = str(project)
        clone_goal["state_file"] = clone_state.name
        clone_registry = {
            "schema_version": registry.get("schema_version", 1),
            "common_runtime_root": str(clone_runtime),
            "goals": [clone_goal],
        }
        clone_registry_path = root / "registry.json"
        clone_registry_path.write_text(json.dumps(clone_registry), encoding="utf-8")
        legacy_result = archive_completed_todos(
            registry_path=clone_registry_path,
            goal_id=args.goal_id,
            role=args.role,
            max_active_done=args.max_active_done,
            dry_run=False,
        )
        if int(legacy_result.get("moved_count") or 0) < 1:
            raise SystemExit("snapshot has no archive pressure for this rehearsal")
        legacy, _ = build_runtime_shadow_source_snapshot(
            goal=clone_goal,
            runtime_root=clone_runtime,
            state_path=clone_state,
            registry_path=clone_registry_path,
        )
        request: dict[str, Any] = {
            "goal_id": args.goal_id,
            "role": args.role,
            "max_active_done": args.max_active_done,
            "initial": initial,
            "legacy": legacy,
            "legacy_moved_count": legacy_result["moved_count"],
        }
        initial_todo_ids = {str(item["todo_id"]) for item in initial["todos"]}
        initial_orphan_lease_count = sum(
            1
            for item in initial["leases"]
            if str(item["todo_id"]) not in initial_todo_ids
        )
        if initial_orphan_lease_count:
            raise SystemExit(
                "source projection contains "
                f"{initial_orphan_lease_count} orphan leases; "
                f"todos={len(initial['todos'])}, leases={len(initial['leases'])}; "
                "three-arm rehearsal held"
            )
        process = subprocess.run(
            [
                "node",
                "--no-warnings",
                "--experimental-strip-types",
                "--input-type=module",
                "-e",
                _node_script(REPOSITORY),
            ],
            cwd=REPOSITORY,
            input=json.dumps(request, separators=(",", ":")),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if process.returncode != 0:
            raise SystemExit(process.stderr.strip() or "three-arm provider rehearsal failed")
        result = json.loads(process.stdout)

    # Detect concurrent live-state movement; never overwrite or restore it.
    if state_path.read_bytes() != source_bytes:
        raise SystemExit("source state changed concurrently; discard this rehearsal")
    current, current_snapshot = build_runtime_shadow_source_snapshot(
        goal=goal,
        runtime_root=runtime_root,
        state_path=state_path,
        registry_path=registry_path,
    )
    if current != initial or current_snapshot != source_snapshot:
        raise SystemExit("source projection changed concurrently; discard this rehearsal")
    result.update(
        {
            "source_unchanged": True,
            "source_bytes_sha256_prefix": hashlib.sha256(source_bytes)
            .hexdigest()[:16],
            "source_projection_sha256_prefix": str(
                source_snapshot["projection_sha256"]
            )[:16],
        }
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
