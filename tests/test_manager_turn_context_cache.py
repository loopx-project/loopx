"""Unit and integration tests for authority-revision manager turn context hot cache."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

import loopx.chat_manager_context as manager_ctx
from loopx.capabilities.manager_context import POLICY_SCHEMA, _root, _write
from loopx.capabilities.manager_context.turn_context_cache import (
    DEFAULT_MAX_AGE_SECONDS,
    MAX_CACHE_ENTRIES,
    clear_manager_turn_context_cache,
    compute_manager_turn_context_fingerprint,
    get_cached_manager_turn_context,
    put_cached_manager_turn_context,
)
from loopx.chat_manager import MANAGED_FOLLOWTHROUGH_CURRENT_MARKER, manager_followthrough_text


@pytest.fixture(autouse=True)
def _clean_turn_context_cache():
    clear_manager_turn_context_cache()
    yield
    clear_manager_turn_context_cache()


def _setup_registry_and_goal(tmp_path: Path, goal_id: str = "alpha") -> tuple[Path, Path, Path]:
    runtime_root = tmp_path / "runtime"
    repo_dir = tmp_path / "repos" / goal_id
    repo_dir.mkdir(parents=True, exist_ok=True)
    state_file = repo_dir / "STATE.md"
    state_file.write_text("# Initial state\n", encoding="utf-8")

    goal_dir = runtime_root / "goals" / goal_id
    (goal_dir / "runs").mkdir(parents=True, exist_ok=True)
    (goal_dir / "deliveries").mkdir(parents=True, exist_ok=True)
    (goal_dir / "todos.json").write_text(
        json.dumps({"todos": [{"todo_id": "todo_1", "status": "open", "text": "Ship slice"}]}),
        encoding="utf-8",
    )
    (goal_dir / "runs" / "index.jsonl").write_text(
        json.dumps({
            "run_id": "run_1",
            "goal_id": goal_id,
            "agent_id": "worker",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "classification": "advancement",
            "delivery_outcome": "outcome_progress",
        }) + "\n",
        encoding="utf-8",
    )

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({
            "schema_version": "0.1",
            "updated_at": "2026-11-23T10:00:00Z",
            "runtime_root": str(runtime_root),
            "goals": [
                {
                    "id": goal_id,
                    "repo": str(repo_dir),
                    "state_file": "STATE.md",
                    "status": "active",
                    "synced_at": "2026-11-23T10:00:00Z",
                    "coordination": {"registered_agents": ["worker"]},
                }
            ],
        }),
        encoding="utf-8",
    )
    return registry_path, runtime_root, repo_dir


def _bound_steward_session(tmp_path: Path) -> dict:
    binding_id = "b" * 24
    source_ref = "e" * 24
    return {
        "session_id": "f" * 32,
        "goal_id": "loopx-manager",
        "session_mode": "managed_runtime",
        "channel_id": f"manager.external.native.{binding_id}.{source_ref}",
        "steward_context": {
            "kind": "bound_steward",
            "project_ref": "a" * 24,
            "workspace_path": str(tmp_path),
            "audience": "bound_owner",
            "grant": "portfolio_read",
            "binding_id": binding_id,
            "provider_ref": "c" * 24,
            "operator_ref": "d" * 24,
            "source_ref": source_ref,
            "goal_ids": ["alpha"],
        },
    }


def _stub_fast_portfolio(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_build_goal_portfolio(**kwargs):
        calls.append(dict(kwargs))
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "ok": True,
            "schema_version": "goal_portfolio_v0",
            "snapshot_id": f"sha256:{len(calls):064x}",
            "inventory_revision": "sha256:" + __import__("hashlib").sha256(
                Path(kwargs["registry_path"]).read_bytes()
            ).hexdigest(),
            "inventory_semantic_revision": manager_ctx.registry_semantic_revision(
                Path(kwargs["registry_path"]),
                Path(kwargs["registry_path"]).read_bytes(),
            ),
            "collected_at": now_iso,
            "collection_completed_at": now_iso,
            "coverage": {"discovered": 1, "verified": 1, "complete": True},
            "warnings": [],
            "limitations": [],
            "goals": [
                {
                    "goal_id": "alpha",
                    "activation_state": "active",
                    "quality": "verified",
                    "progress": "recorded_delivery_available",
                    "attention": {"status": "read", "items": []},
                    "goal_lifecycle": {
                        "schema_version": "goal_lifecycle_readback_v0",
                        "goal_id": "alpha",
                        "status": "unavailable",
                        "reason": "projection_not_derived",
                    },
                    "agents": [],
                    "deliveries": [],
                    "warnings": [],
                }
            ],
        }

    monkeypatch.setattr(manager_ctx, "build_goal_portfolio", fake_build_goal_portfolio)
    return calls


def test_bound_steward_cache_hits_across_sync_timestamp_churn_and_isolates_caller_mutations(
    tmp_path: Path, monkeypatch,
) -> None:
    registry_path, runtime_root, _ = _setup_registry_and_goal(tmp_path)
    calls = _stub_fast_portfolio(monkeypatch)
    session = _bound_steward_session(tmp_path)

    first = manager_ctx.collect_manager_turn_context(
        registry_path,
        session,
        runtime_root,
        scope_resolver=lambda _s: ["alpha"],
        include_details=False,
    )
    assert len(calls) == 1
    original_snapshot_id = first["snapshot_id"]

    # Mutate returned payload the same way coordination.py does; cached entry must stay pristine.
    first["bound_steward"]["executions"] = [{"turn_id": "mutated"}]
    first["context_delegation"] = {"targets": [{"goal_id": "alpha", "agent_id": "worker"}]}
    first["goals"][0]["progress"] = "mutated_in_place"

    # Churn benign updated_at / synced_at timestamps in registry.json.
    reg_payload = json.loads(registry_path.read_text(encoding="utf-8"))
    reg_payload["updated_at"] = "2026-11-23T10:05:00Z"
    reg_payload["goals"][0]["synced_at"] = "2026-11-23T10:05:00Z"
    registry_path.write_text(json.dumps(reg_payload), encoding="utf-8")

    second = manager_ctx.collect_manager_turn_context(
        registry_path,
        session,
        runtime_root,
        scope_resolver=lambda _s: ["alpha"],
        include_details=False,
    )
    assert len(calls) == 1  # Hit hot cache despite sync timestamp churn!
    assert second["snapshot_id"] == original_snapshot_id
    assert "executions" not in second["bound_steward"]
    assert "context_delegation" not in second
    assert second["goals"][0]["progress"] == "recorded_delivery_available"


def test_cache_immediately_invalidates_on_every_local_authority_and_storage_mutation(
    tmp_path: Path, monkeypatch,
) -> None:
    registry_path, runtime_root, repo_dir = _setup_registry_and_goal(tmp_path)
    calls = _stub_fast_portfolio(monkeypatch)
    owner_session = {"session_id": "a" * 32, "goal_id": "loopx-manager", "channel_id": "manager"}

    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 1

    # Repeat without changes -> cache hit.
    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 1

    # 1. SQLite authority DB creation & WAL write invalidates immediately.
    goal_sha = __import__("hashlib").sha256(b"alpha").hexdigest()
    sqlite_dir = runtime_root / "authority" / "sqlite-v0"
    sqlite_dir.mkdir(parents=True, exist_ok=True)
    db_path = sqlite_dir / f"authority-{goal_sha}.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO items (val) VALUES ('v1')")
        conn.commit()
    finally:
        conn.close()

    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 2

    # Second SQLite commit updates WAL/header and invalidates again.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO items (val) VALUES ('v2')")
        conn.commit()
    finally:
        conn.close()

    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 3

    # 2. todos.json mutation invalidates immediately.
    (runtime_root / "goals" / "alpha" / "todos.json").write_text(
        json.dumps({"todos": [{"todo_id": "todo_2", "status": "open", "text": "Updated"}]}),
        encoding="utf-8",
    )
    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 4

    # 3. Declared repo/state_file mutation invalidates immediately.
    (repo_dir / "STATE.md").write_text("# Updated state v2\n", encoding="utf-8")
    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 5

    # 4. runs/index.jsonl append invalidates immediately.
    with (runtime_root / "goals" / "alpha" / "runs" / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": "run_2", "goal_id": "alpha"}) + "\n")
    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 6

    # 5. deliveries directory new entry invalidates immediately.
    (runtime_root / "goals" / "alpha" / "deliveries" / "receipt_1.json").write_text(
        '{"ok":true}', encoding="utf-8",
    )
    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 7

    # 6. machine/configuration.json mutation invalidates immediately.
    machine_cfg = runtime_root / "machine" / "configuration.json"
    machine_cfg.parent.mkdir(parents=True, exist_ok=True)
    machine_cfg.write_text('{"schema_version":"machine_configuration_v0","namespaces":{}}', encoding="utf-8")
    manager_ctx.collect_manager_turn_context(
        registry_path, owner_session, runtime_root, include_details=False,
    )
    assert len(calls) == 8


def test_cache_bypasses_stopped_status_off_ssh_grants_env_off_and_incomplete_results(
    tmp_path: Path, monkeypatch,
) -> None:
    registry_path, runtime_root, _ = _setup_registry_and_goal(tmp_path)
    calls = _stub_fast_portfolio(monkeypatch)
    session = _bound_steward_session(tmp_path)

    # 1. include_stopped=True (observation_only watch turns) ALWAYS bypasses cache.
    manager_ctx.collect_manager_turn_context(
        registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
        include_details=False, include_stopped=True,
    )
    manager_ctx.collect_manager_turn_context(
        registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
        include_details=False, include_stopped=True,
    )
    assert len(calls) == 2

    # 2. read_goal_status=False bypasses cache.
    manager_ctx.collect_manager_turn_context(
        registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
        include_details=False, read_goal_status=False,
    )
    manager_ctx.collect_manager_turn_context(
        registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
        include_details=False, read_goal_status=False,
    )
    assert len(calls) == 4

    # 3. LOOPX_MANAGER_CONTEXT_CACHE=off bypasses cache.
    with monkeypatch.context() as env:
        env.setenv("LOOPX_MANAGER_CONTEXT_CACHE", "off")
        manager_ctx.collect_manager_turn_context(
            registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
            include_details=False,
        )
        manager_ctx.collect_manager_turn_context(
            registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
            include_details=False,
        )
    assert len(calls) == 6

    # 4. Non-empty SSH grants on channel bypasses cache.
    _write(
        _root(runtime_root) / "policy.json",
        {
            "schema_version": POLICY_SCHEMA,
            "sources": {
                session["channel_id"]: {
                    "evidence_ssh_hosts": {"gpu-box": ["remote-goal"]},
                }
            },
        },
    )
    manager_ctx.collect_manager_turn_context(
        registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
        include_details=False,
    )
    manager_ctx.collect_manager_turn_context(
        registry_path, session, runtime_root, scope_resolver=lambda _s: ["alpha"],
        include_details=False,
    )
    assert len(calls) == 8

    # 5. Incomplete coverage or non-empty warnings are never stored.
    assert put_cached_manager_turn_context(
        "sha256:warn",
        {"warnings": ["registry_unreadable"], "coverage": {"complete": True}},
    ) is False
    assert put_cached_manager_turn_context(
        "sha256:incomplete",
        {"warnings": [], "coverage": {"complete": False}},
    ) is False
    assert get_cached_manager_turn_context("sha256:warn") is None
    assert get_cached_manager_turn_context("sha256:incomplete") is None


def test_cache_ttl_lru_eviction_thread_safety_and_followthrough_fast_slow_lane_guidance(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, _ = _setup_registry_and_goal(tmp_path)
    fp = compute_manager_turn_context_fingerprint(
        registry_path=registry_path,
        session={"channel_id": "manager"},
        runtime_root=runtime_root,
        scope_kind="owner_portfolio",
        authorized_goal_ids=None,
        bound_steward=False,
        include_details=False,
        include_stopped=False,
        read_goal_status=True,
        remote_evidence=False,
    )
    assert fp is not None and fp.startswith("sha256:")

    # TTL expiry check.
    sample = {"warnings": [], "coverage": {"complete": True}, "goals": [{"goal_id": "alpha"}]}
    assert put_cached_manager_turn_context(fp, sample, now_monotonic=100.0) is True
    assert get_cached_manager_turn_context(fp, now_monotonic=100.0 + DEFAULT_MAX_AGE_SECONDS - 0.1) is not None
    assert get_cached_manager_turn_context(fp, now_monotonic=100.0 + DEFAULT_MAX_AGE_SECONDS + 0.1) is None

    # Bounded LRU eviction check (MAX_CACHE_ENTRIES = 16).
    for idx in range(MAX_CACHE_ENTRIES + 4):
        assert put_cached_manager_turn_context(
            f"sha256:key_{idx}",
            {"warnings": [], "coverage": {"complete": True}, "idx": idx},
            now_monotonic=200.0 + idx * 0.01,
        ) is True
    for evicted_idx in range(4):
        assert get_cached_manager_turn_context(f"sha256:key_{evicted_idx}", now_monotonic=201.0) is None
    for retained_idx in range(4, MAX_CACHE_ENTRIES + 4):
        hit = get_cached_manager_turn_context(f"sha256:key_{retained_idx}", now_monotonic=201.0)
        assert hit is not None and hit["idx"] == retained_idx

    # Concurrent read/write thread safety.
    def worker(n: int) -> bool:
        key = f"sha256:concurrent_{n % 8}"
        put_cached_manager_turn_context(
            key,
            {"warnings": [], "coverage": {"complete": True}, "n": n},
        )
        got = get_cached_manager_turn_context(key)
        return got is None or isinstance(got.get("n"), int)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(64)))
    assert all(results)

    clear_manager_turn_context_cache()
    assert get_cached_manager_turn_context("sha256:concurrent_0") is None

    # Verify FOLLOWTHROUGH.md fast/slow lane guidance and marker invariants.
    followthrough = manager_followthrough_text()
    assert followthrough.startswith(MANAGED_FOLLOWTHROUGH_CURRENT_MARKER)
    assert "fast/slow lane separation" in followthrough
    assert "resolve-peer-route" in followthrough
    assert "original_conversation" in followthrough
    assert len(followthrough.encode("utf-8")) <= 24_000
