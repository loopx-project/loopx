from __future__ import annotations

import hashlib
import json
import sys
import os
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime as runtime


def test_todo_source_views_share_only_the_exact_request_source(monkeypatch):
    from loopx.control_plane.todos.summary_item import (
        todo_source_display_views,
    )

    requests = []
    original = runtime.effect_runtime_request

    def track(method, params, **kwargs):
        if method in {"todo.context.page", "todo.read"}:
            requests.append(params)
        return original(method, params, **kwargs)

    monkeypatch.setattr(runtime, "effect_runtime_request", track)
    source = {"items": [{"todo_id": "todo_retained", "text": "Keep the full acceptance",
                         "role": "agent", "status": "done", "done": True,
                         "note": "Original recovery detail", "successor_todo_ids": ["todo_next"]}]}
    with runtime.effect_runtime_request_scope():
        views = todo_source_display_views(source)
        assert views["summary"] == []
        assert views["planning"] == []
        terminal = views["terminal"]
        assert len(terminal) == 1
        assert terminal[0]["note"] == "Original recovery detail"
        assert terminal[0]["successor_todo_ids"] == ["todo_next"]
        assert len(requests) == 1
        terminal[0]["successor_todo_ids"].append("todo_unrelated")
        assert todo_source_display_views(source)["terminal"][0]["successor_todo_ids"] == ["todo_next"]
        # The same Todo id cannot alias changed full-source facts.
        source["items"][0]["note"] = "Changed recovery detail"
        assert todo_source_display_views(source)["terminal"][0]["note"] == "Changed recovery detail"
        assert len(requests) == 2
    # A later logical request starts with a new source read and result.
    with runtime.effect_runtime_request_scope():
        assert todo_source_display_views(source)["terminal"][0]["note"] == "Changed recovery detail"
    assert len(requests) == 3


def test_large_or_unscoped_todo_sources_keep_single_view_transport(monkeypatch):
    from loopx.control_plane.todos.summary_item import todo_planning_source_items

    requests = []
    original = runtime.effect_runtime_request

    def track(method, params, **kwargs):
        if method == "todo.context.page":
            requests.append(params)
        return original(method, params, **kwargs)

    monkeypatch.setattr(runtime, "effect_runtime_request", track)
    source = {"items": [{"todo_id": "todo_retained", "text": "Keep acceptance", "status": "done",
                         "note": "Recovery detail " * (runtime.MAX_REQUEST_BYTES // 32)}]}
    with runtime.effect_runtime_request_scope():
        assert todo_planning_source_items(source, include_terminal=True)[0]["note"] == source["items"][0]["note"]
    assert set(requests[0]) == {"summary_display"}
    source["items"][0]["note"] = "Small source outside the shared request"
    assert todo_planning_source_items(source, include_terminal=True)[0]["note"] == source["items"][0]["note"]
    assert set(requests[1]) == {"summary_display"}


def test_explicit_large_source_falls_back_to_existing_single_view_limits(monkeypatch):
    from loopx.control_plane.todos.summary_item import todo_source_display_views

    requests = []
    original = runtime.effect_runtime_request

    def track(method, params, **kwargs):
        if method == "todo.context.page":
            requests.append(params)
        return original(method, params, **kwargs)

    monkeypatch.setattr(runtime, "effect_runtime_request", track)
    source = {"items": [{"todo_id": "todo_retained", "text": "Completed work", "status": "done", "done": True,
                         "note": "Complete recovery detail " * (runtime.MAX_REQUEST_BYTES // 64)}]}
    with runtime.effect_runtime_request_scope():
        views = todo_source_display_views(source)
    assert views["summary"] == views["planning"] == []
    assert views["terminal"][0]["note"] == source["items"][0]["note"]
    assert len(requests) == 3
    assert all(set(request) == {"summary_display"} for request in requests)


def test_incomplete_source_views_fail_before_entering_the_request_memo(monkeypatch):
    from loopx.control_plane.todos.summary_item import todo_source_display_views

    calls = []
    original = runtime.effect_runtime_request

    def response(method, params, **kwargs):
        if method != "todo.context.page":
            return original(method, params, **kwargs)
        calls.append(params)
        return {"result": {"summary": [], "planning": [],
                           "terminal": None if len(calls) == 1 else []}}

    monkeypatch.setattr(runtime, "effect_runtime_request", response)
    source = {"items": [{"todo_id": "todo_retained", "text": "Acceptance", "status": "done"}]}
    with runtime.effect_runtime_request_scope():
        with pytest.raises(RuntimeError, match="invalid TypeScript Todo source views"):
            todo_source_display_views(source)
        assert todo_source_display_views(source)["summary"] == []
    assert len(calls) == 2


def test_single_view_delivery_does_not_compute_or_snapshot_unused_views(monkeypatch):
    from loopx.control_plane.work_items.delivery_history import project_delivery_response

    requests, snapshots = [], []
    original = runtime.effect_runtime_request
    read_snapshot = runtime._read_local_snapshot_response

    def track(method, params, **kwargs):
        if method == "todo.context.page":
            requests.append(params)
        return original(method, params, **kwargs)

    def snapshot(*args, **kwargs):
        snapshots.append(args)
        return read_snapshot(*args, **kwargs)

    monkeypatch.setattr(runtime, "effect_runtime_request", track)
    monkeypatch.setattr(runtime, "_read_local_snapshot_response", snapshot)
    source = {"items": [{"todo_id": f"todo_{i}", "text": "Work", "role": "agent",
                         "status": "open", "successor_todo_ids": ["todo_next"]}
                        for i in range(8000)]}
    with runtime.effect_runtime_request_scope():
        result = project_delivery_response({"todo_id": "todo_0", "agent_id": "agent-a"}, source)
    assert result["schema_version"] == "delivery_response_v0"
    assert len(requests) == 1
    assert set(requests[0]) == {"summary_display"}
    assert requests[0]["summary_display"]["include_terminal"] is True
    assert snapshots == [], "a sub-limit single display must not materialize unused views"


def test_quota_caller_explicitly_composes_only_the_agent_source(monkeypatch):
    from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item, quota_todo_summary
    from loopx.quota import build_quota_should_run

    requests = []
    original = runtime.effect_runtime_request

    def track(method, params, **kwargs):
        if method in {"todo.context.page", "todo.read"}:
            requests.append(params)
        return original(method, params, **kwargs)

    monkeypatch.setattr(runtime, "effect_runtime_request", track)
    agent = quota_todo_summary([quota_todo_item(todo_id="todo_agent", title="Agent work", claimed_by="agent-a")])
    user = quota_todo_summary([quota_todo_item(todo_id="todo_user", title="User action", role="user", blocks_agent="agent-b")])
    status = quota_status_payload(goal_id="goal-a", status="active_state_agent_todo",
                                  recommended_action="Agent work", agent_todos=agent, user_todos=user,
                                  coordination={"registered_agents": ["agent-a", "agent-b"]})
    with runtime.effect_runtime_request_scope():
        result = build_quota_should_run(status, goal_id="goal-a", agent_id="agent-a", available_capabilities=["shell"])
    transactions = [p for p in requests if p.get("kind") == "quota_projection"]
    assert len(transactions) == 1
    sources = [r["summary_request"] for r in transactions[0]["requests"]]
    assert [r["consume_display_views"] for r in sources] == [False, True]
    assert sources[1]["source"]["record"]["first_open_items"][0]["todo_id"] == "todo_agent"
    assert sources[0]["source"]["record"]["first_open_items"][0]["todo_id"] == "todo_user"
    assert result["agent_identity"]["agent_id"] == "agent-a"


def serial_fingerprint(root: Path) -> str:
    """Independent reference: selected adapter, release identity and source bytes."""
    digest = hashlib.sha256()
    digest.update(sys.executable.encode("utf-8"))
    digest.update(str(sys.version_info[:3]).encode("ascii"))
    digest.update(b"loopx_effect_runtime_source_instance_v1\0")
    digest.update(os.fsencode(os.path.normcase(os.fspath(root.resolve()))))
    digest.update(b"\0")
    for path in sorted(p for p in root.rglob("*") if p.suffix in {".ts", ".json"}):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_fingerprint_preserves_exact_bytes_and_observes_source_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: tmp_path)
    files = {"a.ts": b"// line\r\n", "nested/b.json": '{"text":"中文🙂"}'.encode(),
             "z.ts": b"// bytes\n\xff", "ignored.py": b"not runtime source"}
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    before = runtime._runtime_fingerprint()
    assert before == serial_fingerprint(tmp_path)
    (tmp_path / "a.ts").write_bytes(b"// line\n")
    modified = runtime._runtime_fingerprint()
    assert modified == serial_fingerprint(tmp_path) and modified != before
    (tmp_path / "new.ts").write_bytes(b"export {};\n")
    added = runtime._runtime_fingerprint()
    assert added == serial_fingerprint(tmp_path) and added != modified
    (tmp_path / "nested/b.json").unlink()
    removed = runtime._runtime_fingerprint()
    assert removed == serial_fingerprint(tmp_path) and removed != added
    (tmp_path / "ignored.py").write_bytes(b"still not runtime source")
    assert runtime._runtime_fingerprint() == removed


@pytest.mark.parametrize("error", [PermissionError("denied"), RuntimeError("unexpected")])
def test_source_read_failure_is_not_a_partial_fingerprint(tmp_path, monkeypatch, error):
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: tmp_path)
    for name in ("a.ts", "b.ts", "c.ts"):
        (tmp_path / name).write_bytes(b"export {};\n")
    original = Path.read_bytes

    def read(path):
        if path.name == "b.ts":
            raise error
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(type(error), match=str(error)):
        runtime._runtime_fingerprint()
    monkeypatch.setattr(Path, "read_bytes", original)
    assert runtime._runtime_fingerprint() == serial_fingerprint(tmp_path)


def test_deleted_source_retries_the_new_topology(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: tmp_path)
    removed = tmp_path / "a.ts"
    removed.write_bytes(b"before")
    (tmp_path / "b.json").write_bytes(b"{}")
    original = Path.read_bytes

    def read(path):
        if path == removed and path.exists():
            path.unlink()
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert runtime._runtime_fingerprint() == serial_fingerprint(tmp_path)


posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX ctime keys the memo")


def _private_namespace(tmp_path, monkeypatch, *, settle_ns=0):
    namespace = tmp_path / "runtime"
    namespace.mkdir(mode=0o700)
    monkeypatch.setattr(runtime, "_runtime_dir", lambda: namespace)
    monkeypatch.setattr(runtime, "_SOURCE_DIGEST_SETTLE_NS", settle_ns)
    return namespace


def _fresh_process_fingerprint():
    runtime._runtime_fingerprint_for_snapshot.cache_clear()
    return runtime._runtime_fingerprint()


def _count_source_reads(monkeypatch):
    original, reads = runtime.iter_binary_file_reads, []

    def counted(paths, **kwargs):
        for read in original(paths, **kwargs):
            reads.append(read.path.name)
            yield read

    monkeypatch.setattr(runtime, "iter_binary_file_reads", counted)
    return reads


@posix_only
def test_fresh_process_reuses_the_private_digest_until_sources_change(tmp_path, monkeypatch):
    root = tmp_path / "release"
    (root / "nested").mkdir(parents=True)
    (root / "a.ts").write_bytes(b"export {};\n")
    (root / "nested/b.json").write_bytes(b"{}")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch)
    reads = _count_source_reads(monkeypatch)

    first = _fresh_process_fingerprint()
    assert first == serial_fingerprint(root) and len(reads) == 2
    [record] = namespace.iterdir()
    # Locator retirement and fixture cleanup own only runtime-*.json names.
    assert record.name.startswith("source-digest-") and not record.match("runtime-*.json")
    assert record.stat().st_mode & 0o777 == 0o600
    assert _fresh_process_fingerprint() == first and len(reads) == 2

    (root / "a.ts").write_bytes(b"export const edited = true;\n")
    edited = _fresh_process_fingerprint()
    assert edited == serial_fingerprint(root) != first and len(reads) == 4
    (root / "c.ts").write_bytes(b"export {};\n")
    added = _fresh_process_fingerprint()
    assert added == serial_fingerprint(root) != edited
    (root / "nested/b.json").unlink()
    removed = _fresh_process_fingerprint()
    assert removed == serial_fingerprint(root) != added
    assert [path.name for path in namespace.iterdir()] == [record.name]

    copy = tmp_path / "copy"
    copy.mkdir()
    for name in ("a.ts", "c.ts"):
        (copy / name).write_bytes((root / name).read_bytes())
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: copy)
    assert _fresh_process_fingerprint() == serial_fingerprint(copy) != removed


@posix_only
def test_source_change_before_a_digest_hit_is_not_adopted(tmp_path, monkeypatch):
    root = tmp_path / "release"
    root.mkdir()
    edited = root / "a.ts"
    edited.write_bytes(b"export {};\n")
    (root / "b.json").write_bytes(b"{}")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch)
    first = _fresh_process_fingerprint()
    assert first == serial_fingerprint(root) and len(list(namespace.iterdir())) == 1
    original, hits = runtime._read_source_digest, []

    def edit_after_snapshot(path, key):
        # The caller's snapshot is already taken; the stored key still matches it.
        recorded = original(path, key)
        if not hits:
            edited.write_bytes(b"export const edited = true;\n")
        hits.append(recorded)
        return recorded

    monkeypatch.setattr(runtime, "_read_source_digest", edit_after_snapshot)
    reads = _count_source_reads(monkeypatch)

    assert _fresh_process_fingerprint() == serial_fingerprint(root) != first
    assert hits[0] == first and hits[1:] == [None] and reads == ["a.ts", "b.json"]


@posix_only
@pytest.mark.parametrize("defect", ["foreign_mode", "symlink", "shared_namespace",
                                    "corrupt", "stale_key", "invalid_digest"])
def test_untrusted_digest_record_is_recomputed(tmp_path, monkeypatch, defect):
    root = tmp_path / "release"
    root.mkdir()
    (root / "a.ts").write_bytes(b"export {};\n")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch)
    path, key = runtime._source_digest_location(
        os.fspath(root.resolve()), runtime._runtime_source_snapshot(root))
    forged = {"schema_version": runtime._SOURCE_DIGEST_SCHEMA_VERSION,
              "key": "0" * 64 if defect == "stale_key" else key,
              "fingerprint": "F" * 64 if defect == "invalid_digest" else "0" * 64}
    target = tmp_path / "elsewhere.json" if defect == "symlink" else path
    target.write_text("{" if defect == "corrupt" else json.dumps(forged))
    target.chmod(0o644 if defect == "foreign_mode" else 0o600)
    if defect == "symlink":
        path.symlink_to(target)
    if defect == "shared_namespace":
        namespace.chmod(0o755)
    reads = _count_source_reads(monkeypatch)

    assert _fresh_process_fingerprint() == serial_fingerprint(root)
    assert reads == ["a.ts"]


@posix_only
def test_unsettled_or_failed_hash_records_no_digest(tmp_path, monkeypatch):
    root = tmp_path / "release"
    root.mkdir()
    for name in ("a.ts", "b.ts"):
        (root / name).write_bytes(b"export {};\n")
    monkeypatch.setattr(runtime, "_control_plane_root", lambda: root)
    namespace = _private_namespace(tmp_path, monkeypatch, settle_ns=3600 * 10**9)
    assert _fresh_process_fingerprint() == serial_fingerprint(root)
    assert list(namespace.iterdir()) == []

    monkeypatch.setattr(runtime, "_SOURCE_DIGEST_SETTLE_NS", 0)
    original = Path.read_bytes

    def read(path):
        if path.name == "b.ts":
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(PermissionError):
        _fresh_process_fingerprint()
    assert list(namespace.iterdir()) == []

    namespace.rmdir()
    monkeypatch.setattr(Path, "read_bytes", original)
    assert _fresh_process_fingerprint() == serial_fingerprint(root)
    assert not namespace.exists(), "the memo never creates the runtime namespace"
