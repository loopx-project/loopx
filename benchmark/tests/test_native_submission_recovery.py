"""Native submission recovery over real local HTTP and pinned native scripts."""
from __future__ import annotations

from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from benchmark.runtime.submission_receipts import SCHEMA, NativeSubmissionReceipts, canonical_json
from benchmark.runtime.submission_recovery import (
    instrument_submit_script, mark_dispatched, prepare_submission,
    record_response, record_result, synchronize_intents,
)


def archive_bytes(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return stream.getvalue()


class LocalCopyBackend:
    """Exercise the publisher's actual copy/rename protocol in a disposable tree."""
    def exec_run(self, _handle, command, **_kwargs):
        result = subprocess.run(command, capture_output=True)
        return SimpleNamespace(exit_code=result.returncode)

    def copy_to_container(self, _handle, source, destination):
        shutil.copyfile(source, destination)


@contextmanager
def native_http(tmp_path, *, immediately_complete=False, lose_reply=False):
    state = dict(entries=[], results={}, posts=0, result_reads=0, immediate=immediately_complete,
                 lose_reply=lose_reply, run_id="synthetic-run", task_id="synthetic-task")
    trial = tmp_path / "trial"
    trial.mkdir()

    def release(sid, *, score=0, status="completed"):
        entry = next(row for row in state["entries"] if row["submission_id"] == sid)
        report = (dict(submission_id=sid, task_id=state["task_id"], score=score, pass_rate=0.0,
                       valid=True, passed=0, failed=2, total_tests=2, summary="Synthetic negative result")
                  if status == "completed" else None)
        entry.update(status=status, score=score if report else None,
                     pass_rate=0.0 if report else None, valid=True if report else None)
        state["results"][sid] = dict(submission_id=sid, status=status, report=report,
                                     error=None if report else "Synthetic evaluator failure")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, payload, status=200):
            raw = canonical_json(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/api/v1/history":
                assert parse_qs(url.query) == {"token": ["synthetic-token"]}
                return self.send(dict(run_id=state["run_id"], entries=state["entries"],
                    best_score=0, best_pass_rate=0.0, best_round=None))
            if url.path.startswith("/api/v1/result/"):
                sid = url.path.rsplit("/", 1)[-1]
                state["result_reads"] += 1
                return self.send(state["results"][sid])
            self.send({}, 404)

        def do_POST(self):
            assert self.path == "/api/v1/submit"
            state["posts"] += 1
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw)
            parts = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                     for part in message.iter_parts()}
            assert parts["token"] == b"synthetic-token"
            number = state["posts"]
            sid, round_id = f"submission-{number}", f"agent-{number}"
            folder = trial / "submissions" / round_id
            folder.mkdir(parents=True)
            (folder / "submission.tar.gz").write_bytes(parts["archive"])
            state["entries"].append(dict(type="submission", submission_id=sid, round=round_id,
                task_id=state["task_id"], status="queued", valid=None, score=None, pass_rate=None))
            state["results"][sid] = dict(submission_id=sid, status="queued", report=None, error=None)
            if state["immediate"]:
                release(sid)
            if state["lose_reply"]:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send(dict(submission_id=sid, round_id=round_id, status="queued", remaining_submissions=8))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = tmp_path / "recovery"
    observer = NativeSubmissionReceipts(trial=trial, run_id=state["run_id"], task_id=state["task_id"],
        judge_url=f"http://127.0.0.1:{server.server_port}", remote_root=str(root))
    observer.bind({"SFORGE_TOKEN": "synthetic-token"})
    observer.update(LocalCopyBackend(), None)
    try:
        yield state, release, observer, root
    finally:
        observer.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def submit_fixture(observer, raw):
    response = observer.session.post(observer.judge_url + "/api/v1/submit",
        data={"token": "synthetic-token"}, files={"archive": ("submission.tar.gz", raw)}, timeout=5)
    response.raise_for_status()
    return response.json()


@contextmanager
def native_judge(tmp_path, monkeypatch, **limits):
    native = pytest.importorskip("sforge.harness.judge_server")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from sforge.harness.config import SForgeConfig

    run_id, task_id = "synthetic-run", "synthetic-task"
    task = SimpleNamespace(judge=SimpleNamespace(selection="pass_rate_first", score_direction="maximize"))
    monkeypatch.setattr(native, "create_backend_from_config", lambda _config: SimpleNamespace(backend_name="synthetic"))
    monkeypatch.setattr(native.JudgeState, "load_tasks", lambda state: setattr(state, "tasks", {task_id: task}))
    config = SForgeConfig(log_dir=tmp_path / "judge", tasks_dir=tmp_path / "tasks",
        judge_max_concurrent=1, judge_max_pending=0, **limits)
    with TestClient(native.create_app(config)) as client:
        state = client.app.state.judge
        token = state.register_session(task_id, run_id)
        def history():
            response = client.get("/api/v1/history", params={"token": token})
            response.raise_for_status()
            return response.json()
        yield native, state, client, token, history


@pytest.mark.parametrize("per_run_limit", [False, True], ids=["aggregate", "run-task"])
def test_native_capacity_refusal_allows_an_explicit_later_submission(tmp_path, monkeypatch, per_run_limit):
    with native_judge(tmp_path, monkeypatch, judge_max_outstanding_per_run=1 if per_run_limit else None) as (
            _native, state, client, token, history):
        run_id, task_id = "synthetic-run", "synthetic-task"
        root = tmp_path / "recovery"
        root.mkdir()
        (root / "native.json").write_bytes(canonical_json(dict(
            schema_version=SCHEMA, run_id=run_id, task_id=task_id, records=[])))
        archive = tmp_path / "packaged.tar.gz"
        archive.write_bytes(archive_bytes({"candidate.py": b"original"}))
        prepared = prepare_submission(root, archive, history_reader=history)
        mark_dispatched(root, prepared["operation_id"])
        # Occupy real native admission capacity without starting an evaluation.
        state._reserve(run_id, task_id)
        try:
            response = client.post("/api/v1/submit", data={"token": token},
                files={"archive": ("submission.tar.gz", Path(prepared["archive_path"]).read_bytes())})
            assert response.status_code == 503 and response.headers["retry-after"] == "5"
            intent = record_response(root, prepared["operation_id"], response.status_code, response.json())
            assert intent["acceptance"] == "rejected"
        finally:
            state._release(run_id, task_id)
        assert history()["entries"] == []
        assert state.resolve_token(token)["next_agent"] == 1
        state._reserve(run_id, task_id)
        state._release(run_id, task_id)
        later = prepare_submission(root, archive, history_reader=history)
        assert later["action"] == "submit" and later["operation_id"] != prepared["operation_id"]
        assert not state.submissions and history()["entries"] == [], "neither response handling nor prepare resubmits"


@pytest.mark.parametrize("refusal", ["budget", "cooldown"])
def test_native_round_refusal_allows_an_explicit_later_prepare(tmp_path, monkeypatch, refusal):
    with native_judge(tmp_path, monkeypatch) as (native, state, client, token, history):
        root = tmp_path / "recovery"
        root.mkdir()
        (root / "native.json").write_bytes(canonical_json(dict(schema_version=SCHEMA,
            run_id="synthetic-run", task_id="synthetic-task", records=[])))
        archive = tmp_path / "packaged.tar.gz"
        archive.write_bytes(archive_bytes({"candidate.py": b"original"}))
        prepared = prepare_submission(root, archive, history_reader=history)
        mark_dispatched(root, prepared["operation_id"])
        if refusal == "budget":
            state.tokens[token]["max_agent_submissions"] = 0
        else:
            # A fixed provider clock proves refusal without a timing race.
            monkeypatch.setattr(native, "time", SimpleNamespace(time=lambda: 1000))
            state.tokens[token].update(submission_cooldown=60, last_agent_submit_at=1000)
        response = client.post("/api/v1/submit", data={"token": token},
            files={"archive": ("submission.tar.gz", Path(prepared["archive_path"]).read_bytes())})
        assert response.status_code == 429
        intent = record_response(root, prepared["operation_id"], response.status_code, response.json())
        assert intent["acceptance"] == "rejected"
        later = prepare_submission(root, archive, history_reader=history)
        assert later["operation_id"] != prepared["operation_id"]
        assert state.resolve_token(token)["next_agent"] == 1
        assert not state.submissions and history()["entries"] == []


@pytest.mark.parametrize("error_type,http_status", [(ValueError, 404), (KeyError, 401)])
def test_native_error_after_admission_cannot_be_mistaken_for_a_safe_rejection(tmp_path, monkeypatch, error_type, http_status):
    with native_judge(tmp_path, monkeypatch) as (_native, state, client, token, history):
        root = tmp_path / "recovery"
        root.mkdir()
        (root / "native.json").write_bytes(canonical_json(dict(schema_version=SCHEMA,
            run_id="synthetic-run", task_id="synthetic-task", records=[])))
        archive = tmp_path / "packaged.tar.gz"
        archive.write_bytes(archive_bytes({"candidate.py": b"original"}))
        prepared = prepare_submission(root, archive, history_reader=history)
        mark_dispatched(root, prepared["operation_id"])

        def fail_after_recording(*_args, **_kwargs):
            assert state.resolve_token(token)["next_agent"] == 2
            assert len(state.submissions) == 1
            assert len(state.get_run_history("synthetic-run", "synthetic-task")["entries"]) == 1
            raise error_type("synthetic interruption after native admission")

        monkeypatch.setattr(state._executor, "submit", fail_after_recording)
        response = client.post("/api/v1/submit", data={"token": token},
            files={"archive": ("submission.tar.gz", Path(prepared["archive_path"]).read_bytes())})
        assert response.status_code == http_status
        intent = record_response(root, prepared["operation_id"], response.status_code, response.json())
        assert intent["acceptance"] == "unknown"
        with pytest.raises(ValueError, match="native_acceptance_unknown_no_resubmit"):
            prepare_submission(root, archive, history_reader=history)
        assert state.resolve_token(token)["next_agent"] == 2
        assert len(state.submissions) == 1 and len(history()["entries"]) == 1
        assert len(list((root / "intents").glob("*.json"))) == 1


@pytest.mark.parametrize("http_status,response", [
    (503, {"detail": "Service Unavailable"}),
    (503, {"detail": "Judge capacity full; retry this snapshot later; acceptance unknown"}),
    (500, {"detail": "Judge capacity full; retry this snapshot later"}),
    (502, {"detail": "Bad Gateway"}),
    (503, {"detail": {"message": "Judge capacity full; retry this snapshot later"}}),
    (400, {"detail": "Bad Request"}),
    (401, {"detail": "Invalid token"}),
    (403, {"detail": "Forbidden"}),
    (404, {"detail": "Not Found"}),
    (413, {"detail": "Content Too Large"}),
    (429, {"detail": "Too Many Requests"}),
    (429, {"detail": "Submission budget exhausted (1/2 used)"}),
    (429, {"detail": "Submission cooldown active — wait 1s before next submission."}),
])
def test_unproven_server_failure_cannot_allow_resubmission(tmp_path, http_status, response):
    root = tmp_path / "recovery"
    root.mkdir()
    (root / "native.json").write_bytes(canonical_json(dict(schema_version=SCHEMA,
        run_id="synthetic-run", task_id="synthetic-task", records=[])))
    archive = tmp_path / "packaged.tar.gz"
    archive.write_bytes(archive_bytes({"candidate.py": b"original"}))
    prepared = prepare_submission(root, archive,
        history_reader=lambda: {"run_id": "synthetic-run", "entries": []})
    mark_dispatched(root, prepared["operation_id"])
    intent = record_response(root, prepared["operation_id"], http_status, response)
    assert intent["acceptance"] == "unknown"
    with pytest.raises(ValueError, match="native_acceptance_unknown_no_resubmit"):
        prepare_submission(root, archive)
    assert len(list((root / "intents").glob("*.json"))) == 1


def test_lost_reply_recovers_same_operation_and_never_resubmits(tmp_path):
    with native_http(tmp_path) as (state, release, observer, root):
        archive = tmp_path / "packaged.tar.gz"
        archive.write_bytes(archive_bytes({"candidate.py": b"original"}))
        initial = prepare_submission(root, archive,
            history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        operation = initial["operation_id"]
        mark_dispatched(root, operation)
        accepted = submit_fixture(observer, Path(initial["archive_path"]).read_bytes())
        # Simulate response loss: there is no record_response call.
        with pytest.raises(ValueError, match="unknown_no_resubmit"):
            prepare_submission(root, archive)
        pending = observer.update(LocalCopyBackend(), None)
        assert "archive_sha256" not in pending["records"][0]
        with pytest.raises(ValueError, match="unknown_no_resubmit"):
            prepare_submission(root, archive)
        assert state["posts"] == 1
        release(accepted["submission_id"])
        packet = observer.update(LocalCopyBackend(), None)
        [intent] = synchronize_intents(root, packet)
        assert intent["operation_id"] == operation
        assert intent["submission_id"] == accepted["submission_id"]
        archive.write_bytes(archive_bytes({"candidate.py": b"newer"}))
        recovered = prepare_submission(root, archive)
        assert recovered["action"] == "recover" and recovered["operation_id"] == operation
        assert recovered["submission_id"] == accepted["submission_id"]
        assert recovered["archive_sha256"] == hashlib.sha256(Path(initial["archive_path"]).read_bytes()).hexdigest()
        assert state["posts"] == 1
        with pytest.raises(ValueError, match="already_dispatched"):
            mark_dispatched(root, operation)


def test_ambiguous_archives_and_duplicate_native_ids_cannot_recover(tmp_path):
    with native_http(tmp_path) as (state, release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        source.write_bytes(archive_bytes({"candidate.py": b"original"}))
        prepared = prepare_submission(root, source,
            history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        mark_dispatched(root, prepared["operation_id"])
        for _ in range(2):
            accepted = submit_fixture(observer, source.read_bytes())
            release(accepted["submission_id"])
        packet = observer.update(LocalCopyBackend(), None)
        [intent] = synchronize_intents(root, packet)
        assert intent["acceptance"] == "unknown"
        assert intent["recovery_reason"] == "ambiguous_native_archive_match"
        with pytest.raises(ValueError, match="unknown_no_resubmit"):
            prepare_submission(root, source)
        state["entries"].append(dict(state["entries"][0]))
        duplicate = observer.poll()
        rows = [row for row in duplicate["records"] if row["submission_id"] == "submission-1"]
        assert all(not row["accepted_record"] and "archive_sha256" not in row for row in rows)


@pytest.mark.parametrize("lose_reply", [False, True])
@pytest.mark.parametrize("aliased_parent", [False, True])
def test_actual_native_submit_script_archives_once_and_recovers_lost_reply(tmp_path, lose_reply, aliased_parent):
    native = pytest.importorskip("sforge.harness.evolve_scripts")
    with native_http(tmp_path, immediately_complete=True, lose_reply=lose_reply) as (state, release, observer, root):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "candidate.py").write_bytes(b"original")
        generated = native.generate_submit_script()
        # Give this real client a private display cache rather than the fixed
        # global /tmp display cache; admission/packaging/HTTP paths stay native.
        generated = generated.replace('STATE_FILE="/tmp/sforge_state.json"',
                                      'STATE_FILE="' + str(tmp_path / "display.json") + '"')
        script = tmp_path / "sforge-submit"
        script.write_text(instrument_submit_script(generated,
            [sys.executable, "-m", "benchmark.runtime.submission_recovery"], str(root)))
        script.chmod(0o755)
        native_tmp = tmp_path / "native-tmp"
        native_tmp.mkdir()
        if aliased_parent:
            alias = tmp_path / "native-tmp-alias"
            alias.symlink_to(native_tmp, target_is_directory=True)
            native_tmp = alias
        env = dict(os.environ, SFORGE_JUDGE_URL=observer.judge_url, SFORGE_TOKEN="synthetic-token",
            SFORGE_PATCH_DIR=str(workspace), SFORGE_SUBMIT_PATHS="candidate.py",
            PYTHONPATH=str(Path(__file__).resolve().parents[2]), NO_PROXY="127.0.0.1", TMPDIR=str(native_tmp))
        first = subprocess.run([str(script)], cwd=workspace, env=env, capture_output=True, text=True, timeout=30)
        assert (first.returncode != 0) == lose_reply, first.stderr
        assert state["posts"] == 1
        [intent_file] = list((root / "intents").glob("*.json"))
        intent = json.loads(intent_file.read_bytes())
        archive = observer.trial / "submissions" / "agent-1" / "submission.tar.gz"
        assert archive.read_bytes() == Path(intent["archive_path"]).read_bytes()
        assert not list(native_tmp.iterdir()), "the native EXIT trap cleans the first call"
        if lose_reply:
            observer.update(LocalCopyBackend(), None)
            assert state["result_reads"] == 0, "the receipt bridge never fetches an evaluation result"
            (workspace / "candidate.py").write_bytes(b"newer workspace")
            recovered = subprocess.run([str(script)], cwd=workspace, env=env,
                capture_output=True, text=True, timeout=30)
            assert recovered.returncode == 0, recovered.stderr
            assert "Reading existing submission: submission-1" in recovered.stdout
            assert state["posts"] == 1, "recovery never creates a second native opportunity"
            assert json.loads(intent_file.read_bytes())["operation_id"] == intent["operation_id"]
            assert all(value in recovered.stderr for value in (
                intent["operation_id"], "submission-1", intent["archive_sha256"]))
            assert not list(native_tmp.iterdir()), "recovery cleans its unused package before native result reading"
            state["lose_reply"] = False
            second = subprocess.run([str(script)], cwd=workspace, env=env,
                capture_output=True, text=True, timeout=30)
            assert second.returncode == 0, second.stderr
            assert state["posts"] == 2, "after reading the old result a new call submits current bytes"
            second_archive = observer.trial / "submissions" / "agent-2" / "submission.tar.gz"
            with tarfile.open(second_archive) as native_archive:
                assert native_archive.extractfile("candidate.py").read() == b"newer workspace"


@pytest.mark.parametrize("invalid_archive", ["leaf_symlink", "oversized", "moved"])
def test_submission_parent_alias_preserves_archive_integrity_checks(tmp_path, monkeypatch, invalid_archive):
    from benchmark.runtime.submission_receipts import MAX_ARCHIVE_BYTES

    with native_http(tmp_path) as (state, _release, _observer, root):
        physical = tmp_path / "physical"
        physical.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(physical, target_is_directory=True)
        archive = physical / "packaged.tar.gz"
        archive.write_bytes(archive_bytes({"candidate.py": b"original"}))
        expected = "native_archive_size_or_type"
        if invalid_archive == "leaf_symlink":
            target = physical / "target.tar.gz"
            archive.rename(target)
            archive.symlink_to(target)
            expected = "native_archive_not_physical"
        elif invalid_archive == "oversized":
            with archive.open("r+b") as stream:
                stream.truncate(MAX_ARCHIVE_BYTES + 1)
        else:
            original_open = os.open

            def move_after_open(path, flags, *args, **kwargs):
                descriptor = original_open(path, flags, *args, **kwargs)
                if Path(path) == archive:
                    replacement = physical / "replacement.tar.gz"
                    replacement.write_bytes(archive_bytes({"candidate.py": b"replacement"}))
                    replacement.replace(archive)
                return descriptor

            monkeypatch.setattr(os, "open", move_after_open)
            expected = "native_archive_moved"
        with pytest.raises(ValueError, match=expected):
            prepare_submission(root, alias / archive.name,
                history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        assert not (root / "intents").exists()
        assert not (root / "snapshots").exists()
        assert state["posts"] == 0


def test_native_client_hook_requires_the_observed_packaging_boundaries():
    with pytest.raises(ValueError, match="unsupported_native_submit_script"):
        instrument_submit_script("#!/bin/sh\nexit 0\n", [sys.executable, "-m", "fixture"], "/synthetic")


def test_malformed_terminal_feedback_cannot_make_a_retry_submit_again(tmp_path):
    with native_http(tmp_path) as (state, release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        source.write_bytes(archive_bytes({"candidate.py": b"original"}))
        prepared = prepare_submission(root, source,
            history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        mark_dispatched(root, prepared["operation_id"])
        receipt = submit_fixture(observer, source.read_bytes())
        record_response(root, prepared["operation_id"], 200, receipt)
        with pytest.raises(ValueError, match="completed_result_scope"):
            record_result(root, receipt["submission_id"],
                          dict(submission_id=receipt["submission_id"], status="completed", report=None))
        assert prepare_submission(root, source)["action"] == "recover"
        assert state["posts"] == 1


@pytest.mark.parametrize("receipt_available", [False, True])
def test_direct_native_result_only_finishes_a_matching_recovered_operation(tmp_path, receipt_available):
    with native_http(tmp_path, immediately_complete=True) as (state, _release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        source.write_bytes(archive_bytes({"candidate.py": b"original"}))
        def history():
            return {"run_id": state["run_id"], "entries": state["entries"]}
        prepared = prepare_submission(root, source, history_reader=history)
        mark_dispatched(root, prepared["operation_id"])
        accepted = submit_fixture(observer, Path(prepared["archive_path"]).read_bytes())
        # No response hook: the native acceptance reply was lost.
        if receipt_available:
            observer.update(LocalCopyBackend(), None)
        result = observer.session.get(observer.judge_url + "/api/v1/result/" + accepted["submission_id"], timeout=5)
        result.raise_for_status()
        record_result(root, accepted["submission_id"], result.json())
        intent = json.loads((root / "intents" / (prepared["operation_id"] + ".json")).read_bytes())
        assert intent["result_observed"] is receipt_available
        source.write_bytes(archive_bytes({"candidate.py": b"next candidate"}))
        if receipt_available:
            later = prepare_submission(root, source, history_reader=history)
            assert later["action"] == "submit" and later["operation_id"] != prepared["operation_id"]
        else:
            with pytest.raises(ValueError, match="native_acceptance_unknown_no_resubmit"):
                prepare_submission(root, source, history_reader=history)
        assert state["posts"] == 1


def test_replacement_before_dispatch_submits_current_bytes_and_retires_the_old_operation(tmp_path):
    with native_http(tmp_path) as (state, _release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        old_bytes = archive_bytes({"candidate.py": b"A"})
        source.write_bytes(old_bytes)
        def history():
            return {"run_id": state["run_id"], "entries": state["entries"]}
        first = prepare_submission(root, source, history_reader=history)
        assert prepare_submission(root, source, history_reader=history)["operation_id"] == first["operation_id"]
        current_bytes = archive_bytes({"candidate.py": b"B"})
        source.write_bytes(current_bytes)
        current = prepare_submission(root, source, history_reader=history)
        assert current["operation_id"] != first["operation_id"]
        assert Path(first["archive_path"]).read_bytes() == old_bytes
        with pytest.raises(ValueError, match="native_prepared_operation_retired"):
            mark_dispatched(root, first["operation_id"])
        mark_dispatched(root, current["operation_id"])
        submit_fixture(observer, Path(current["archive_path"]).read_bytes())
        assert (observer.trial / "submissions" / "agent-1" / "submission.tar.gz").read_bytes() == current_bytes
        assert state["posts"] == 1


@pytest.mark.parametrize("winner", ["replacement", "dispatch"])
def test_contending_process_rechecks_prepared_operation_under_the_dispatch_lock(tmp_path, monkeypatch, winner):
    from benchmark.runtime import submission_recovery

    with native_http(tmp_path) as (state, _release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        original_bytes = archive_bytes({"candidate.py": b"A"})
        source.write_bytes(original_bytes)
        def history():
            return {"run_id": state["run_id"], "entries": state["entries"]}
        first = prepare_submission(root, source, history_reader=history)
        next_bytes = archive_bytes({"candidate.py": b"B"})
        source.write_bytes(next_bytes)
        write = submission_recovery._atomic
        contender = None

        def launch_contender_while_locked(path, value):
            nonlocal contender
            entering = value.get("retired_before_dispatch") if winner == "replacement" else value.get("dispatch_started")
            if path.stem == first["operation_id"] and entering and contender is None:
                args = (["dispatched", "--operation-id", first["operation_id"]] if winner == "replacement"
                        else ["prepare", "--archive", str(source)])
                contender = subprocess.Popen([sys.executable, "-c",
                    "import sys; from benchmark.runtime.submission_recovery import main; "
                    "print('ready', flush=True); raise SystemExit(main(sys.argv[1:]))",
                    "--root", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                assert contender.stdout.readline().strip() == "ready"
            return write(path, value)

        monkeypatch.setattr(submission_recovery, "_atomic", launch_contender_while_locked)
        try:
            if winner == "replacement":
                current = prepare_submission(root, source, history_reader=history)
                mark_dispatched(root, current["operation_id"])
                submitted = Path(current["archive_path"]).read_bytes()
                expected_reason = "native_prepared_operation_retired"
            else:
                mark_dispatched(root, first["operation_id"])
                submitted = Path(first["archive_path"]).read_bytes()
                expected_reason = "native_acceptance_unknown_no_resubmit"
            assert contender is not None
            _stdout, stderr = contender.communicate(timeout=10)
            assert contender.returncode == 1 and expected_reason in stderr
            submit_fixture(observer, submitted)
            assert submitted == (next_bytes if winner == "replacement" else original_bytes)
            assert state["posts"] == 1
        finally:
            if contender is not None and contender.poll() is None:
                contender.kill()
                contender.communicate(timeout=5)


def test_two_clients_of_the_same_prepared_snapshot_cannot_both_dispatch(tmp_path):
    with native_http(tmp_path) as (state, _release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        source.write_bytes(archive_bytes({"candidate.py": b"same candidate"}))
        prepared = prepare_submission(root, source,
            history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        command = [sys.executable, "-m", "benchmark.runtime.submission_recovery", "--root", str(root),
                   "dispatched", "--operation-id", prepared["operation_id"]]
        children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(2)]
        try:
            outputs = [child.communicate(timeout=10) for child in children]
            assert sorted(child.returncode for child in children) == [0, 1]
            assert sum("native_operation_already_dispatched" in stderr for _, stderr in outputs) == 1
            submit_fixture(observer, Path(prepared["archive_path"]).read_bytes())
            assert state["posts"] == 1
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)


def test_crash_after_retiring_prepared_work_cannot_revive_its_stale_dispatch(tmp_path, monkeypatch):
    from benchmark.runtime import submission_recovery

    with native_http(tmp_path) as (state, _release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        source.write_bytes(archive_bytes({"candidate.py": b"A"}))
        def history():
            return {"run_id": state["run_id"], "entries": state["entries"]}
        first = prepare_submission(root, source, history_reader=history)
        current_bytes = archive_bytes({"candidate.py": b"B"})
        source.write_bytes(current_bytes)
        write = submission_recovery._atomic

        def fail_new_intent(path, value):
            if value["operation_id"] != first["operation_id"]:
                raise OSError("synthetic interruption after retirement")
            return write(path, value)

        monkeypatch.setattr(submission_recovery, "_atomic", fail_new_intent)
        with pytest.raises(OSError, match="after retirement"):
            prepare_submission(root, source, history_reader=history)
        with pytest.raises(ValueError, match="native_prepared_operation_retired"):
            mark_dispatched(root, first["operation_id"])
        monkeypatch.setattr(submission_recovery, "_atomic", write)
        current = prepare_submission(root, source, history_reader=history)
        mark_dispatched(root, current["operation_id"])
        submit_fixture(observer, Path(current["archive_path"]).read_bytes())
        assert (observer.trial / "submissions" / "agent-1" / "submission.tar.gz").read_bytes() == current_bytes
        assert state["posts"] == 1
