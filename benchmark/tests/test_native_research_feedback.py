"""Native public HTTP + exact tar snapshots, with bounded synthetic evaluations."""
from __future__ import annotations

from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
import gzip
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

from benchmark.edgebench.research_feedback import SCHEMA, NativeResearchFeedback, archive_manifest, canonical_json
from benchmark.runtime.research_submission import (
    bind_execution_observations, declared_wait_packet, instrument_submit_script,
    mark_dispatched, prepare_submission, record_response, record_result,
    register_probe, synchronize_intents,
)


def archive_bytes(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return stream.getvalue()


def probe_record(source=b"original"):
    files = [{"path": "candidate.py", "sha256": hashlib.sha256(source).hexdigest()}]
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    observation = dict(schema_version="explore_execution_observation_v0", candidate_sha256=digest,
        input_revision="synthetic-public-v1", planned_method_id="local-search", executed_method_id="local-search",
        change_reason=None, elapsed_seconds=0.4, limit_seconds=30.0, search_iterations=2,
        stop_reason="process_exit:0; solver_report:search_cap", exit_code=0,
        evidence_refs=["probe:synthetic", "source:" + digest])
    return observation, {"candidate_sha256": digest, "files": files}


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
    root = tmp_path / "feedback"
    observer = NativeResearchFeedback(trial=trial, run_id=state["run_id"], task_id=state["task_id"],
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


def test_late_negative_result_binds_original_selected_manifest_not_current_files(tmp_path):
    with native_http(tmp_path) as (state, release, observer, root):
        raw = archive_bytes({"candidate.py": b"original", **{f"data/{i}.txt": b"x" for i in range(35)}})
        accepted = submit_fixture(observer, raw)
        old, manifest = probe_record()
        register_probe(root, old, manifest)
        new, new_manifest = probe_record(b"new workspace")
        register_probe(root, new, new_manifest)
        pending = observer.update(LocalCopyBackend(), None)
        assert state["result_reads"] == 0, "pending public history must not read a result"
        assert "result_ref" not in pending["records"][0]
        assert "candidate_sha256" not in pending["records"][0], "a large full archive is not a 32-file probe"
        (tmp_path / "candidate.py").write_text("new workspace")
        release(accepted["submission_id"], score=0)
        completed = observer.update(LocalCopyBackend(), None)
        [bound] = bind_execution_observations(root, completed)
        assert bound["execution_observation"]["result_candidate_sha256"] == old["candidate_sha256"]
        assert bound["archive_sha256"] == hashlib.sha256(raw).hexdigest()
        assert bound["archive_sha256"] != old["candidate_sha256"]
        assert bound["result_summary"]["score"] == 0
        assert completed == observer.poll(), "unchanged results retain their first observation identity"
        assert (tmp_path / "candidate.py").read_text() == "new workspace"


@pytest.mark.parametrize("corruption", ["invalid-gzip", "truncated-gzip", "invalid-deflate", "bad-crc"])
def test_malformed_native_error_archive_does_not_block_independent_probe_binding(tmp_path, corruption):
    with native_http(tmp_path) as (state, release, observer, root):
        observation, manifest = probe_record()
        register_probe(root, observation, manifest)
        # Native admission accepts opaque bytes; failed archive listing releases
        # an error while retaining those exact bytes, without a source manifest.
        archive = archive_bytes({"candidate.py": b"original"})
        if corruption == "invalid-gzip":
            invalid = b"not-a-tar-gzip"
        elif corruption == "truncated-gzip":
            invalid = archive[:len(archive) // 2]
        else:
            # Keep the tar header in one gzip member so decompression/CRC
            # errors surface during member reads, after tarfile.open returns.
            unpacked = gzip.decompress(archive)
            first = bytearray(gzip.compress(unpacked[:512]))
            if corruption == "bad-crc":
                first[-8] ^= 1
                following = gzip.compress(unpacked[512:])
            else:
                following = archive[:10] + b"\x07" + archive[-8:]  # Reserved deflate block type.
            invalid = bytes(first) + following
        failed = submit_fixture(observer, invalid)
        release(failed["submission_id"], status="error")
        good = submit_fixture(observer, archive)
        release(good["submission_id"])
        packet = observer.update(LocalCopyBackend(), None)
        failed_record = next(row for row in packet["records"] if row["submission_id"] == failed["submission_id"])
        assert failed_record["status"] == "error" and failed_record["result_ref"]
        assert failed_record["attribution_status"] == "verified", "the native archive/result identity remains evidence"
        assert "candidate_sha256" not in failed_record, "invalid tar bytes cannot qualify a source manifest"
        [bound] = bind_execution_observations(root, packet)
        assert bound["submission_id"] == good["submission_id"]
        assert bound["execution_observation"]["result_candidate_sha256"] == observation["candidate_sha256"]


@pytest.mark.parametrize("per_run_limit", [False, True], ids=["aggregate", "run-task"])
def test_native_capacity_refusal_allows_an_explicit_later_submission(tmp_path, monkeypatch, per_run_limit):
    native = pytest.importorskip("sforge.harness.judge_server")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from sforge.harness.config import SForgeConfig

    run_id, task_id = "synthetic-run", "synthetic-task"
    task = SimpleNamespace(judge=SimpleNamespace(selection="pass_rate_first", score_direction="maximize"))
    monkeypatch.setattr(native, "create_backend_from_config", lambda _config: SimpleNamespace(backend_name="synthetic"))
    monkeypatch.setattr(native.JudgeState, "load_tasks", lambda state: setattr(state, "tasks", {task_id: task}))
    config = SForgeConfig(log_dir=tmp_path / "judge", tasks_dir=tmp_path / "tasks",
        judge_max_concurrent=1, judge_max_pending=0,
        judge_max_outstanding_per_run=1 if per_run_limit else None)
    with TestClient(native.create_app(config)) as client:
        state = client.app.state.judge
        token = state.register_session(task_id, run_id)
        def history():
            response = client.get("/api/v1/history", params={"token": token})
            response.raise_for_status()
            return response.json()
        root = tmp_path / "feedback"
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


@pytest.mark.parametrize("http_status,response", [
    (503, {"detail": "Service Unavailable"}),
    (503, {"detail": "Judge capacity full; retry this snapshot later; acceptance unknown"}),
    (500, {"detail": "Judge capacity full; retry this snapshot later"}),
    (502, {"detail": "Bad Gateway"}),
    (503, {"detail": {"message": "Judge capacity full; retry this snapshot later"}}),
])
def test_unproven_server_failure_cannot_allow_resubmission(tmp_path, http_status, response):
    root = tmp_path / "feedback"
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
        packet = observer.update(LocalCopyBackend(), None)
        [intent] = synchronize_intents(root, packet)
        assert intent["operation_id"] == operation
        assert intent["submission_id"] == accepted["submission_id"]
        archive.write_bytes(archive_bytes({"candidate.py": b"newer"}))
        recovered = prepare_submission(root, archive)
        assert recovered == dict(action="recover", operation_id=operation, submission_id=accepted["submission_id"])
        assert state["posts"] == 1
        with pytest.raises(ValueError, match="already_dispatched"):
            mark_dispatched(root, operation)


def test_ambiguous_archives_remain_unknown_and_duplicate_native_ids_cannot_release(tmp_path):
    with native_http(tmp_path) as (state, release, observer, root):
        source = tmp_path / "packaged.tar.gz"
        source.write_bytes(archive_bytes({"candidate.py": b"original"}))
        prepared = prepare_submission(root, source,
            history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        mark_dispatched(root, prepared["operation_id"])
        for _ in range(2):
            submit_fixture(observer, source.read_bytes())
        packet = observer.update(LocalCopyBackend(), None)
        [intent] = synchronize_intents(root, packet)
        assert intent["acceptance"] == "unknown"
        assert intent["recovery_reason"] == "ambiguous_native_archive_match"
        with pytest.raises(ValueError, match="unknown_no_resubmit"):
            prepare_submission(root, source)
        state["entries"].append(dict(state["entries"][0]))
        duplicate = observer.poll()
        rows = [row for row in duplicate["records"] if row["submission_id"] == "submission-1"]
        assert all(row["attribution_status"] == "unknown" and "result_ref" not in row for row in rows)


def test_pending_acceptance_can_register_wait_before_host_poll_without_result_attribution(tmp_path):
    with native_http(tmp_path) as (state, release, observer, root):
        archive = tmp_path / "packaged.tar.gz"
        archive.write_bytes(archive_bytes({"candidate.py": b"original"}))
        prepared = prepare_submission(root, archive, wait_todo_id="requested-work",
            history_reader=lambda: {"run_id": state["run_id"], "entries": []})
        mark_dispatched(root, prepared["operation_id"])
        receipt = submit_fixture(observer, archive.read_bytes())
        intent = record_response(root, prepared["operation_id"], 200, receipt)
        [row] = declared_wait_packet(root, intent)["records"]
        assert row["accepted_record"] and row["status"] == "queued"
        assert row["submission_id"] == receipt["submission_id"]
        assert row["attribution_status"] == "unknown" and "result_ref" not in row
        assert observer.snapshot()["records"] == [], "the receipt fills only this explicitly requested wait"


def test_failure_to_publish_control_effect_retries_and_restart_preserves_release_time(tmp_path):
    with native_http(tmp_path, immediately_complete=True) as (state, release, observer, root):
        submit_fixture(observer, archive_bytes({"candidate.py": b"original"}))
        attempts = []
        def after_publish(*_args):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("synthetic control writeback interruption")
        observer.after_publish = after_publish
        with pytest.raises(RuntimeError, match="control writeback"):
            observer.update(LocalCopyBackend(), None)
        completed = observer.update(LocalCopyBackend(), None)
        assert len(attempts) == 2
        duplicate = observer.update(LocalCopyBackend(), None)
        assert duplicate == completed and len(attempts) == 2
        restarted = NativeResearchFeedback(trial=observer.trial, run_id=state["run_id"],
            task_id=state["task_id"], judge_url=observer.judge_url, remote_root=str(root))
        try:
            restarted.bind({"SFORGE_TOKEN": "synthetic-token"})
            assert restarted.poll() == completed
        finally:
            restarted.close()


def test_mismatched_released_details_and_native_symlink_stay_unattributed(tmp_path):
    with native_http(tmp_path, immediately_complete=True) as (state, release, observer, root):
        receipt = submit_fixture(observer, archive_bytes({"candidate.py": b"original"}))
        state["results"][receipt["submission_id"]]["report"]["task_id"] = "unrelated-task"
        [row] = observer.poll()["records"]
        assert row["attribution_status"] == "unknown" and "result_ref" not in row
        archive = observer.trial / "submissions" / "agent-1" / "submission.tar.gz"
        outside = tmp_path / "untrusted.tar.gz"
        archive.rename(outside)
        archive.symlink_to(outside)
        [row] = observer.poll()["records"]
        assert row["reason"] == "native_archive_not_physical" and "archive_sha256" not in row


def test_archive_manifest_rejects_ambiguous_paths_links_and_matches_probe_hash():
    observation, _ = probe_record()
    digest, _ = archive_manifest(archive_bytes({"./candidate.py": b"original"}), ["candidate.py"])
    assert digest == observation["candidate_sha256"]
    for files in ({"candidate.py": b"x", "./candidate.py": b"y"}, {"../candidate.py": b"x"}):
        with pytest.raises(ValueError, match="ambiguous_archive_member"):
            archive_manifest(archive_bytes(files))


@pytest.mark.parametrize("lose_reply", [False, True])
def test_actual_native_submit_script_archives_once_and_recovers_lost_reply(tmp_path, lose_reply):
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
            [sys.executable, "-m", "benchmark.runtime.research_submission"], str(root)))
        script.chmod(0o755)
        env = dict(os.environ, SFORGE_JUDGE_URL=observer.judge_url, SFORGE_TOKEN="synthetic-token",
            SFORGE_PATCH_DIR=str(workspace), SFORGE_SUBMIT_PATHS="candidate.py",
            PYTHONPATH=str(Path(__file__).resolve().parents[2]), NO_PROXY="127.0.0.1")
        first = subprocess.run([str(script)], cwd=workspace, env=env, capture_output=True, text=True, timeout=30)
        assert (first.returncode != 0) == lose_reply, first.stderr
        assert state["posts"] == 1
        [intent_file] = list((root / "intents").glob("*.json"))
        intent = json.loads(intent_file.read_bytes())
        archive = observer.trial / "submissions" / "agent-1" / "submission.tar.gz"
        assert archive.read_bytes() == Path(intent["archive_path"]).read_bytes()
        if lose_reply:
            observer.update(LocalCopyBackend(), None)
            (workspace / "candidate.py").write_bytes(b"newer workspace")
            recovered = subprocess.run([str(script)], cwd=workspace, env=env,
                capture_output=True, text=True, timeout=30)
            assert recovered.returncode == 0, recovered.stderr
            assert "Reading existing submission: submission-1" in recovered.stdout
            assert state["posts"] == 1, "recovery never creates a second native opportunity"
            assert json.loads(intent_file.read_bytes())["operation_id"] == intent["operation_id"]


def test_native_client_hook_requires_the_observed_packaging_boundaries():
    with pytest.raises(ValueError, match="unsupported_native_submit_script"):
        instrument_submit_script("#!/bin/sh\nexit 0\n", [sys.executable, "-m", "fixture"], "/synthetic")


def test_ambiguous_record_does_not_prevent_an_independent_verified_result(tmp_path):
    from benchmark.runtime.research_feedback_wait import _records
    with native_http(tmp_path, immediately_complete=True) as (state, release, observer, root):
        submit_fixture(observer, archive_bytes({"candidate.py": b"first"}))
        independent = submit_fixture(observer, archive_bytes({"candidate.py": b"second"}))
        state["entries"].append(dict(state["entries"][0]))
        packet = observer.poll()
        accepted = _records(packet)
        assert set(accepted) == {independent["submission_id"]}
        assert accepted[independent["submission_id"]][1]["result_ref"]


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


def test_accepted_archive_identity_cannot_be_replaced_after_first_observation(tmp_path):
    with native_http(tmp_path, immediately_complete=True) as (state, release, observer, root):
        submit_fixture(observer, archive_bytes({"candidate.py": b"original"}))
        original = observer.update(LocalCopyBackend(), None)
        native_archive = observer.trial / "submissions" / "agent-1" / "submission.tar.gz"
        native_archive.write_bytes(archive_bytes({"candidate.py": b"changed after acceptance"}))
        [changed] = observer.update(LocalCopyBackend(), None)["records"]
        assert changed["attribution_status"] == "unknown"
        assert changed["reason"] == "native_accepted_archive_changed"
        assert "result_ref" not in changed
        restarted = NativeResearchFeedback(trial=observer.trial, run_id=state["run_id"],
            task_id=state["task_id"], judge_url=observer.judge_url, remote_root=str(root))
        try:
            restarted.bind({"SFORGE_TOKEN": "synthetic-token"})
            assert restarted.poll()["records"][0]["reason"] == "native_accepted_archive_changed"
        finally:
            restarted.close()
        assert Path(original["records"][0]["archive_path"]).read_bytes() != native_archive.read_bytes()
