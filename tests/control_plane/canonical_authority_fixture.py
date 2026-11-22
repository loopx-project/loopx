"""Initialize an already canonical provider for its independent consumer tests.

This fixture runs the real FileAuthorityStore in a Node process. It deliberately
does not claim that a shadow qualification can promote canonical authority.
Resolve TypeScript modules from the imported package so source and installed
wheel tests exercise the same Python and TypeScript build.
"""
from __future__ import annotations

import atexit
from collections import deque
import json
import os
import queue
from datetime import datetime
from pathlib import Path
import selectors
import subprocess
import tempfile
import threading
import time

import loopx


_WORKER_LOCK = threading.Lock()
_WORKERS: dict[tuple[int, str, bool], _CanonicalAuthorityWorker] = {}
_SHUTDOWN_REGISTERED = False
_WORKER_REQUEST_TIMEOUT_SECONDS = 45.0


class _CanonicalAuthorityWorker:
    """Persistent Node worker for disposable canonical authority fixture setups."""

    def __init__(self, key: tuple[int, str, bool]) -> None:
        self.key = key
        self.call_lock = threading.Lock()
        self._stderr_tail: deque[str] = deque(maxlen=64)
        self._line_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._process = self._spawn(Path(key[1]))

    @staticmethod
    def _build_script(package: Path) -> str:
        module = package / "control_plane/coordination/file_authority_store.ts"
        provider_module = module.parent / "local_authority_provider.ts"
        fence_module = package / "control_plane/coordination/legacy_writer_fence.ts"
        codec_module = package / "control_plane/coordination/authority_store_codec.ts"
        return (
            "import {createInterface} from 'node:readline';"
            f"import {{FileAuthorityStore}} from {json.dumps(module.as_uri())};"
            f"import {{selectLocalSqliteAuthority,openLocalAuthorityStore}} from {json.dumps(provider_module.as_uri())};"
            f"import {{engageLegacyCoordinationWriterFence}} from {json.dumps(fence_module.as_uri())};"
            f"import {{canonicalAuthoritySha256}} from {json.dumps(codec_module.as_uri())};"
            "const rl=createInterface({input:process.stdin,crlfDelay:Infinity});"
            "for await(const line of rl){"
            "if(!line)continue;"
            "try{"
            "const req=JSON.parse(line);"
            "let result;"
            "if(req.op==='init'){"
            "if(req.provider==='sqlite')await selectLocalSqliteAuthority(req.root,req.goal,true);"
            "const store=await openLocalAuthorityStore(req.root,req.goal);"
            "result=await store.commitAuthority({expected_provider_revision:null,operation_id:'canonical-fixture',"
            "events:[],next_projection:req.projection,receipts:[]});"
            "const fence=await engageLegacyCoordinationWriterFence({schema_version:'loopx_legacy_coordination_writer_fence_engage_request_v0',"
            "runtime_root:req.root,goal_id:req.goal,state_path:req.state_path,fence:{schema_version:'loopx_legacy_coordination_writer_fence_v0',"
            "state:'engaged',goal_id:req.goal,fence_id:'canonical-fixture',source_version:'canonical-fixture',"
            "source_projection_sha256:canonicalAuthoritySha256(req.projection),expected_shadow_provider_revision:result.provider_revision}});"
            "if(fence.status!=='applied')throw new Error(JSON.stringify(fence));"
            "}else if(req.op==='advance'){"
            "const store=await openLocalAuthorityStore(req.root,req.goal);"
            "const head=await store.loadAuthority();"
            "if(head.status!=='loaded')throw new Error(JSON.stringify(head));"
            "result=await store.commitAuthority({expected_provider_revision:head.provider_revision,"
            "operation_id:req.operation_id,events:[],next_projection:req.projection,receipts:[]});"
            "}else{throw new Error('unsupported canonical fixture op');}"
            "process.stdout.write(JSON.stringify({ok:true,result})+'\\n');"
            "}catch(err){"
            "const msg=err instanceof Error?(err.stack||err.message):String(err);"
            "process.stdout.write(JSON.stringify({ok:false,error:msg})+'\\n');"
            "}}"
        )

    def _spawn(self, package: Path) -> subprocess.Popen[bytes]:
        self._stderr_tail.clear()
        proc = subprocess.Popen(
            ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", self._build_script(package)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert proc.stderr is not None

        def _drain_stderr() -> None:
            try:
                for raw_line in iter(proc.stderr.readline, b""):
                    self._stderr_tail.append(raw_line.decode("utf-8", errors="replace").rstrip())
            except OSError:
                pass

        threading.Thread(target=_drain_stderr, daemon=True).start()
        # Windows select() supports sockets only; drain response lines from a
        # thread instead of the selector-based pipe read in _read_line.
        if os.name == "nt":
            self._line_queue = queue.SimpleQueue()

            def _drain_lines() -> None:
                try:
                    for raw_line in iter(proc.stdout.readline, b""):
                        self._line_queue.put(raw_line)
                except OSError:
                    pass
                finally:
                    self._line_queue.put(None)

            threading.Thread(target=_drain_lines, daemon=True).start()
        return proc

    def _read_line(self, timeout_seconds: float) -> bytes:
        stdout = self._process.stdout
        assert stdout is not None
        deadline = time.monotonic() + timeout_seconds
        if os.name == "nt":
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("canonical authority worker timed out waiting for response")
                line = self._line_queue.get(timeout=remaining)
                if line is None:
                    raise EOFError("canonical authority worker closed stdout")
                return line
        chunks = bytearray()
        selector = selectors.DefaultSelector()
        selector.register(stdout, selectors.EVENT_READ)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("canonical authority worker timed out waiting for response")
                events = selector.select(remaining)
                if not events:
                    raise TimeoutError("canonical authority worker timed out waiting for response")
                chunk = os.read(stdout.fileno(), 65536)
                if not chunk:
                    raise EOFError("canonical authority worker closed stdout")
                chunks.extend(chunk)
                if b"\n" in chunk:
                    line, _rest = bytes(chunks).split(b"\n", 1)
                    return line
        finally:
            selector.close()

    def request(self, payload: dict[str, object]) -> dict:
        encoded = (json.dumps(payload) + "\n").encode("utf-8")
        with self.call_lock:
            for attempt in range(2):
                if self._process.poll() is not None:
                    self.close()
                    self._process = self._spawn(Path(self.key[1]))
                try:
                    assert self._process.stdin is not None
                    self._process.stdin.write(encoded)
                    self._process.stdin.flush()
                    raw_line = self._read_line(_WORKER_REQUEST_TIMEOUT_SECONDS)
                except (BrokenPipeError, EOFError, OSError):
                    self.close()
                    if attempt == 0:
                        self._process = self._spawn(Path(self.key[1]))
                        continue
                    stderr_text = "\n".join(self._stderr_tail)
                    raise AssertionError(f"canonical authority worker failed after respawn: {stderr_text}")
                except TimeoutError:
                    self.close()
                    raise
                response = json.loads(raw_line.decode("utf-8"))
                if not response.get("ok"):
                    raise AssertionError(response.get("error") or "\n".join(self._stderr_tail))
                return response["result"]
        raise AssertionError("unreachable")

    def close(self) -> None:
        proc = self._process
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if proc.poll() is None:
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def _shutdown_canonical_fixture_workers() -> None:
    current_pid = os.getpid()
    with _WORKER_LOCK:
        owned = [key for key in _WORKERS if key[0] == current_pid]
        workers = [_WORKERS.pop(key) for key in owned]
    for worker in workers:
        worker.close()


def _get_canonical_authority_worker(package: Path) -> _CanonicalAuthorityWorker:
    global _SHUTDOWN_REGISTERED
    sqlite_enabled = "--experimental-sqlite" in os.environ.get("NODE_OPTIONS", "").split()
    key = (os.getpid(), str(package), sqlite_enabled)
    with _WORKER_LOCK:
        if not _SHUTDOWN_REGISTERED:
            atexit.register(_shutdown_canonical_fixture_workers)
            _SHUTDOWN_REGISTERED = True
        worker = _WORKERS.get(key)
        if worker is None:
            worker = _CanonicalAuthorityWorker(key)
            _WORKERS[key] = worker
        return worker


def assert_todo_read_unchanged(current, previous):
    """Compare complete readback except validated evaluator/observation clocks."""
    def stable(payload):
        payload = without_resume_observation_time(payload)
        envelope = payload["projection_envelope"]
        assert envelope["schema_version"] == "loopx_projection_envelope_v0"
        for key in ("observed_at", "served_at"):
            assert datetime.fromisoformat(envelope.pop(key).replace("Z", "+00:00")).tzinfo is not None
        for source in envelope["sources"]:
            assert datetime.fromisoformat(source.pop("last_read_at").replace("Z", "+00:00")).tzinfo is not None
        return payload

    assert stable(current) == stable(previous)


def empty_todo_display(state_path: Path) -> None:
    """Remove display work without deleting Goal intent, acceptance or stops.

    Quota admission still requires those Goal sections. Empty Todo sections
    independently prove that the real provider owns work selection; a missing
    entire Goal is a different, deliberately fail-closed health case.
    """
    from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections

    state_path.write_text(render_canonical_todo_sections(
        state_path.read_text(), [], provider_revision="synthetic-empty-display",
    ).markdown)


def without_resume_observation_time(value):
    """Compare all readback facts except the typed evaluator's observation time."""
    if isinstance(value, list):
        return [without_resume_observation_time(item) for item in value]
    if not isinstance(value, dict):
        return value
    is_resume = value.get("schema_version") == "todo_resume_condition_v0"
    if is_resume and "evaluated_at" in value:
        assert datetime.fromisoformat(value["evaluated_at"].replace("Z", "+00:00")).tzinfo is not None
    return {key: without_resume_observation_time(item) for key, item in value.items()
            if not (is_resume and key == "evaluated_at")}


def initialize_canonical_authority(runtime_root: Path, goal_id: str, projection: dict, *, state_path: Path, provider: str = "file") -> dict:
    if provider not in ("file", "sqlite"):
        raise ValueError("Canonical local fixtures support File/SQLite only; PostgreSQL requires its service-owned fixture")
    package = Path(loopx.__file__).resolve().parent
    worker = _get_canonical_authority_worker(package)
    result = worker.request({
        "op": "init",
        "root": str(runtime_root),
        "goal": goal_id,
        "projection": projection,
        "state_path": str(state_path),
        "provider": provider,
    })
    assert result["status"] == "applied", result
    return result


def read_fixture_todos(state_path: Path) -> list[dict]:
    """Read authored input before initializing a disposable canonical fixture."""
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    from loopx.control_plane.todos.todo_summary import structured_todo_item

    # Attention summaries intentionally shorten text. Seeding their preview as
    # canonical authority would erase the tail before the consumer is tested.
    items, _, _ = parse_todo_source(state_path.read_text(encoding="utf-8"), state_path=state_path)
    return [
        structured_todo_item(item, role=role, source_section=item["source_section"], text_limit=None)
        for role in ("user", "agent") for item in items[role]
    ]


def advance_canonical_fixture_authority(
    runtime_root: Path, goal_id: str, projection: dict, *, operation_id: str,
) -> dict:
    """Advance an already initialized disposable store through its real CAS.

    Consumer fixtures may author a later synthetic state, but must not replace
    storage bytes, reset lineage, re-import Markdown or re-engage its fence.
    """
    package = Path(loopx.__file__).resolve().parent
    worker = _get_canonical_authority_worker(package)
    result = worker.request({
        "op": "advance",
        "root": str(runtime_root),
        "goal": goal_id,
        "projection": projection,
        "operation_id": operation_id,
    })
    assert result["status"] == "applied", result
    return result


def initialize_canonical_source_fixture(
    runtime_root: Path, goal_id: str, *, state_path: Path,
    provider: str = "file", handoff_mode: str = "soft_claim",
    leases: list[dict] | None = None,
) -> dict:
    """Finalize source authoring once; later changes use the canonical writer."""
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    return initialize_canonical_authority(
        runtime_root, goal_id,
        build_todo_runtime_shadow_projection(
            goal_id=goal_id, todos=read_fixture_todos(state_path),
            handoff_mode=handoff_mode, leases=leases or [],
        ),
        state_path=state_path, provider=provider,
    )


def isolate_sqlite_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite")
    # Do not reuse an Effect runtime started by the minimum-Node CI step.
    # CLI subprocesses resolve their root from the environment; the current
    # process may already have cached a different root in tempfile.tempdir.
    # Patch both for the same lifetime, restoring the cache at fixture teardown.
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def single_snapshot_page(result: dict, goal_id: str = "goal-a") -> dict:
    """Encode small provider read fixtures as one complete transport page."""
    return {
        "schema_version": "loopx_canonical_snapshot_page_result_v0", "status": "page",
        "snapshot": {"goal_id": goal_id, "store_identity": "fixture-store",
                     "provider_revision": result["provider_revision"], "cursor": result["cursor"],
                     "query_sha256": "fixture-query", "todo_count": len(result["todos"]),
                     "lease_count": len(result.get("leases", []))},
        "metadata": {key: result[key] for key in (
            "todo_read_model", "goal_acceptance_contract", "handoff_mode", "projection_readback"
        ) if key in result},
        **{key: result[key] for key in (
            "todos", "leases", "goal_acceptance_work_guards", "source_authority",
            "decision_read_from_provider", "legacy_fallback_used"
        ) if key in result},
        "next": None,
    }


def promoted_create_fixture(
    tmp_path: Path,
    *,
    provider: str = "file",
    goal_id: str = "goal-a",
    handoff_mode: str = "soft_claim",
    registered_agents: list[str] | None = None,
) -> tuple[Path, Path, Path]:
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.coordination.coordination_state_contract import (
        TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION, TODO_DOMAIN_RECORD_FIELDS,
    )

    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    state_file = project / f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "# Goal\n\n## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n## Completed Work Archive\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": goal_id,
                        "repo": str(project),
                        "state_file": f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md",
                        "coordination": {
                            "registered_agents": (
                                registered_agents
                                if registered_agents is not None
                                else ["agent-a"]
                            )
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id=goal_id, todos=[], handoff_mode=handoff_mode
    )
    projection["todo_read_model"] = {
        **projection["todo_read_model"],
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
    }
    initialize_canonical_authority(
        runtime_root, goal_id, projection, state_path=state_file, provider=provider
    )
    return registry_path, runtime_root, state_file


def ensure_unrelated_goal_in_registry(registry_path: Path, current_goal_id: str) -> str:
    """Add a second ordinary Goal to exercise shared-registry recovery."""
    from loopx.control_plane.projects.registry_codec import project_registry_transaction

    unrelated_goal_id = "unrelated-goal"
    with project_registry_transaction(
        registry_path, operation="test_add_unrelated_goal",
    ) as transaction:
        payload = transaction.payload_copy()
        goals = payload.get("goals")
        assert isinstance(goals, list)
        if not any(goal.get("id") == unrelated_goal_id for goal in goals if isinstance(goal, dict)):
            current = next(goal for goal in goals if isinstance(goal, dict) and goal.get("id") == current_goal_id)
            goals.append({
                "id": unrelated_goal_id,
                "repo": current["repo"],
                "state_file": current["state_file"],
                "title": "Unrelated Goal",
                "coordination": current.get("coordination", {}),
            })
        transaction.commit(payload)
    return unrelated_goal_id


def change_unrelated_goal_title(registry_path: Path, unrelated_goal_id: str = "unrelated-goal") -> None:
    """Change only an unrelated Goal title to move the shared registry digest."""
    from loopx.control_plane.projects.registry_codec import project_registry_transaction

    with project_registry_transaction(
        registry_path, operation="test_edit_unrelated_goal_title",
    ) as transaction:
        payload = transaction.payload_copy()
        goals = payload.get("goals")
        assert isinstance(goals, list)
        unrelated = next(
            goal for goal in goals
            if isinstance(goal, dict) and goal.get("id") == unrelated_goal_id
        )
        unrelated["title"] = "Renamed Unrelated Goal"
        transaction.commit(payload)
