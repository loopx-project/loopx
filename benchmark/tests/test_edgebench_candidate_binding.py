"""Native submission binding over exact receipt and local probe artifacts."""

import hashlib
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from benchmark.runtime.research_probe import candidate_snapshot, run_probe


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value))
    return path


def _tar(path, files):
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files:
            member = tarfile.TarInfo(name)
            if data is None:
                member.type = tarfile.SYMTYPE
                member.linkname = "solver.py"
                archive.addfile(member)
                continue
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


@pytest.fixture
def case(tmp_path):
    sources = {"solver.py": b"answer = 2\n", "fixture.py": b"sample = 7\n"}
    for name, data in sources.items():
        (tmp_path / name).write_bytes(data)
    digest, pins = candidate_snapshot([tmp_path / name for name in sources], tmp_path)
    observation = _write(tmp_path / "probe.json", {
        "schema_version": "explore_execution_observation_v0",
        "candidate_sha256": digest,
        "input_revision": "dataset:public-v1",
        "exit_code": 0,
        "stop_reason": "process_exit:0",
        "evidence_refs": ["source:" + digest],
    })
    _write(tmp_path / "probe.json.candidate.json", {"candidate_sha256": digest, "files": pins})
    output = tmp_path / "generated.json"
    output.write_bytes(b'{"answer":2}')
    files = [("solver.py", sources["solver.py"]), ("result.json", output.read_bytes())]
    snapshot = _write(tmp_path / "manifest.json", {
        "execution_observation": observation.name,
        "input_revision": "dataset:public-v1",
        "files": [{"path": name, "sha256": _digest(data)} for name, data in files],
    })
    archive = tmp_path / "submission.tar.gz"
    _tar(archive, files)
    result = tmp_path / "result-response.json"
    return observation, snapshot, output, files, archive, result


def _native_response(archive):
    raw = archive.read_bytes()
    return {
        "status": "completed",
        "submission_id": "submission-2",
        "receipt": {
            "schema_version": "sforge_submission_receipt_v1",
            "task_id": "task",
            "round_id": "agent-2",
            "archive_sha256": _digest(raw),
            "archive_bytes": len(raw),
        },
    }


def _evaluation_context():
    return {
        "schema_version": "sforge_run_evaluation_context_v1",
        "task_id": "task", "run_id": "run", "registration_id": "registration",
        "evaluation_conditions": {
            "schema_version": "sforge_evaluation_conditions_v1",
            "task_sha256": "a" * 64, "benchmark_sha256": "b" * 64,
            "task_spec_sha256": "c" * 64, "native_source_sha256": "d" * 64,
            "execution_config_sha256": "e" * 64, "judge_image_id": "sha256:" + "f" * 64,
            "score_direction": "maximize", "selection": "score_first",
        },
    }


def _bind(case, *, pass_output=True):
    observation, snapshot, output, _, archive, result = case
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable, "-m", "benchmark.runtime.sforge_candidate_binding",
        "--observation", str(observation), "--snapshot", str(snapshot),
        "--archive", str(archive), "--result", str(result),
    ]
    if pass_output:
        command += ["--generated-file", f"result.json={output}"]
    return subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)


def test_matches_probe_pins_and_generated_bytes_to_the_exact_native_request(case):
    observation, _, _, _, archive, result = case
    _write(result, _native_response(archive))
    response = _bind(case)
    assert response.returncode == 0, response.stderr
    proof = json.loads(response.stdout)
    assert proof["candidate_ref"] == "archive:" + _digest(archive.read_bytes())
    assert proof["probe_candidate_ref"] == "candidate:" + json.loads(observation.read_text())["candidate_sha256"]
    assert proof["input_revision"] == "dataset:public-v1"
    assert proof["submission_id"] == "submission-2"


def test_full_file_set_matches_preupload_pins_across_distinct_uploads(case, tmp_path):
    _, snapshot, output, files, archive, result = case
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    for name, data in files:
        (prepared / name).write_bytes(data)
    before_upload, _ = candidate_snapshot(
        [prepared / name for name, _ in files], prepared
    )

    def bind_upload():
        _write(result, _native_response(archive))
        response = _bind(case)
        assert response.returncode == 0, response.stderr
        return json.loads(response.stdout)

    first = bind_upload()
    assert first["submitted_file_set_ref"] == "candidate:" + before_upload
    assert first["submitted_file_set_ref"] != first["probe_candidate_ref"]

    _tar(archive, list(reversed(files)))
    reordered = bind_upload()
    assert reordered["candidate_ref"] != first["candidate_ref"]
    assert reordered["submitted_file_set_ref"] == first["submitted_file_set_ref"]

    output.write_bytes(b'{"answer":3}')
    (prepared / "result.json").write_bytes(output.read_bytes())
    changed, pins = candidate_snapshot([prepared / name for name, _ in files], prepared)
    _write(snapshot, {**json.loads(snapshot.read_text()), "files": pins})
    _tar(archive, [files[0], ("result.json", output.read_bytes())])
    updated = bind_upload()
    assert updated["probe_candidate_ref"] == first["probe_candidate_ref"]
    assert updated["submitted_file_set_ref"] == "candidate:" + changed
    assert updated["submitted_file_set_ref"] != first["submitted_file_set_ref"]


def test_offline_binding_rejects_changed_output_before_opening_missing_result(case):
    _, _, output, _, _, _ = case
    output.write_bytes(b'{"answer":3}')
    checked = _bind(case)
    assert checked.returncode == 1
    assert checked.stdout == ""
    assert "generated output bytes differ from the sealed snapshot" in checked.stderr


def test_scoped_read_falls_back_on_truncated_http(case, monkeypatch, capsys):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from benchmark.runtime import sforge_candidate_binding, sforge_native_result

    class TruncatedResult(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"5\r\n{")

    observation, snapshot, _, _, archive, _ = case
    with ThreadingHTTPServer(("127.0.0.1", 0), TruncatedResult) as server:
        scope = sforge_native_result.NativeResultScope(
            relay=f"http://127.0.0.1:{server.server_port}/api/v1/feedback/test",
            run="run", task="task", goal="goal", agent="agent",
            task_sha256="a" * 64, benchmark_sha256="b" * 64, higher_is_better=True,
            evaluation_context=_evaluation_context(),
        )
        monkeypatch.setattr(sforge_native_result, "read_installed_scope", lambda: scope)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            exit_code = sforge_candidate_binding.main([
                "--submission-id", "submission-2", "--observation", str(observation),
                "--snapshot", str(snapshot), "--archive", str(archive),
            ])
        finally:
            server.shutdown()
            thread.join(timeout=5)
    captured = capsys.readouterr()
    assert exit_code == 1 and captured.out == ""
    assert "use sforge-submit --result ID --json" in captured.err


@pytest.mark.parametrize("defect", [None, "input_revision", "legacy_conditions", "registration",
                                    "task_spec", "native_source", "image", "execution_config"])
def test_native_read_uses_service_conditions_and_one_result_request(case, defect):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from benchmark.runtime.sforge_native_result import (
        NativeResultScope, read_candidate_measurement, read_run_evaluation_context,
    )

    observation, snapshot, output, _, archive, _ = case
    task_digest = "a" * 64
    if defect != "input_revision":
        for document in (observation, snapshot):
            _write(document, {**json.loads(document.read_text()), "input_revision": "task:" + task_digest})
    native = _native_response(archive)
    context = _evaluation_context()
    native["receipt"]["evaluation_context"] = deepcopy(context)
    if defect == "legacy_conditions":
        native["receipt"].pop("evaluation_context")
    elif defect == "registration":
        native["receipt"]["evaluation_context"]["registration_id"] = "previous-registration"
    elif defect in {"task_spec", "native_source", "image", "execution_config"}:
        field = "judge_image_id" if defect == "image" else defect + "_sha256"
        native["receipt"]["evaluation_context"]["evaluation_conditions"][field] = (
            "sha256:" if defect == "image" else ""
        ) + "0" * 64
    native["report"] = {"submission_id": native["submission_id"], "task_id": "task",
                        "score": -2, "valid": True, "timed_out": False}
    requests = []

    class ResultRelay(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(context if self.path.endswith("/context") else native).encode())

    with ThreadingHTTPServer(("127.0.0.1", 0), ResultRelay) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            relay = f"http://127.0.0.1:{server.server_port}/api/v1/feedback/test"
            scope = NativeResultScope(
                relay=relay, run="run", task="task", goal="goal", agent="agent",
                task_sha256=task_digest, benchmark_sha256="b" * 64, higher_is_better=True,
                evaluation_context=read_run_evaluation_context(relay),
            )
            assert requests == ["/api/v1/feedback/test/context"]
            requests.clear()  # Registration qualification is outside the result-read budget.
            result = read_candidate_measurement(
                scope, native["submission_id"], observation=observation, snapshot=snapshot,
                archive=archive, generated_files=[("result.json", output)],
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert requests == ["/api/v1/feedback/test/result/submission-2?include_receipt=true"]
    assert result["native"] == native
    if defect in (None, "input_revision"):
        measurement = result["native_measurement"]
        assert measurement["input_revision"] == "task:" + task_digest
        assert measurement["value"] == -2
        assert measurement["evaluation_ref"] == scope.evaluation_ref
        assert measurement["candidate_ref"] == result["candidate_binding"]["archive_ref"]
        assert result["candidate_binding"]["archive_ref"] == "archive:" + _digest(archive.read_bytes())
    else:
        assert result["native_measurement"] is None
        assert result["native_measurement_unavailable"] is True


@pytest.mark.parametrize(("change", "value"), [
    ("task_sha256", "0" * 64), ("benchmark_sha256", "0" * 64),
    ("higher_is_better", False), ("run", "other-run"), ("task", "other-task"),
])
def test_worker_labels_cannot_override_service_grading_conditions(change, value):
    from benchmark.runtime.sforge_native_result import NativeResultScope
    values = dict(relay="http://127.0.0.1/api/v1/feedback/test", run="run", task="task",
                  goal="goal", agent="agent", task_sha256="a" * 64, benchmark_sha256="b" * 64,
                  higher_is_better=True, evaluation_context=_evaluation_context())
    with pytest.raises(ValueError, match="conditions differ"):
        NativeResultScope(**{**values, change: value})


@pytest.mark.parametrize("response", [None, {"evaluation_conditions": None}])
def test_unknown_service_conditions_cannot_be_promoted_by_worker(response):
    from benchmark.runtime.sforge_native_result import NativeResultScope
    with pytest.raises(ValueError, match="context is unavailable"):
        NativeResultScope(relay="http://127.0.0.1/api/v1/feedback/test", run="run", task="task",
                          goal="goal", agent="agent", task_sha256="a" * 64, benchmark_sha256="b" * 64,
                          higher_is_better=True, evaluation_context=response)


def test_real_preupload_probe_preserves_full_set_identity_and_subset_boundaries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    solver = tmp_path / "solver.py"
    solver.write_text("print('local execution completed')\n")
    output = tmp_path / "result.json"
    output.write_bytes(b'{"answer":2}')
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(b'{"input":7}')

    def probe(name, paths):
        observation = tmp_path / name
        result = run_probe(command=[sys.executable, str(solver)], candidate_files=paths,
            output=observation, input_revision="dataset:public-v1", planned_method_id="method:local",
            executed_method_id="method:local", evidence_ref="artifact:local-execution", limit_seconds=5)
        assert result["exit_code"] == 0 and result["stop_reason"] == "process_exit:0"
        return observation, result

    # The measurement exists before either upload, and pins every submitted file.
    full_observation, full_probe = probe("full-probe.json", [solver, output])
    original_observation = full_observation.read_bytes()
    full_ref = "candidate:" + full_probe["candidate_sha256"]
    _, pins = candidate_snapshot([solver, output], tmp_path)
    snapshot = _write(tmp_path / "manifest.json", {
        "execution_observation": full_observation.name,
        "input_revision": "dataset:public-v1", "files": pins,
    })
    archive, result = tmp_path / "submission.tar.gz", tmp_path / "native.json"
    files = [("solver.py", solver.read_bytes()), ("result.json", output.read_bytes())]

    def bind(observation, members, generated=False):
        _tar(archive, members)
        _write(result, _native_response(archive))
        response = _bind((observation, snapshot, output, members, archive, result),
                         pass_output=generated)
        assert response.returncode == 0, response.stderr
        return json.loads(response.stdout)

    first = bind(full_observation, files)
    reordered = bind(full_observation, list(reversed(files)))
    assert first["submitted_file_set_ref"] == reordered["submitted_file_set_ref"] == full_ref
    assert first["candidate_ref"] != reordered["candidate_ref"]
    assert first["probe_candidate_ref"] == full_ref

    # A real probe over a different file set cannot relabel its existing measurement.
    subset_observation, subset_probe = probe("subset-probe.json", [solver, fixture])
    _write(snapshot, {**json.loads(snapshot.read_text()),
                      "execution_observation": subset_observation.name})
    subset = bind(subset_observation, files, generated=True)
    assert subset["probe_candidate_ref"] == "candidate:" + subset_probe["candidate_sha256"]
    assert subset["probe_candidate_ref"] != subset["submitted_file_set_ref"] == full_ref

    output.write_bytes(b'{"answer":3}')
    changed_files = [files[0], ("result.json", output.read_bytes())]
    changed_digest, changed_pins = candidate_snapshot([solver, output], tmp_path)
    _write(snapshot, {**json.loads(snapshot.read_text()), "files": changed_pins})
    changed = bind(subset_observation, changed_files, generated=True)
    assert changed["probe_candidate_ref"] == subset["probe_candidate_ref"]
    assert changed["submitted_file_set_ref"] == "candidate:" + changed_digest != full_ref
    unproven = _bind((subset_observation, snapshot, output, changed_files, archive, result),
                     pass_output=False)
    assert unproven.returncode == 1 and not unproven.stdout
    assert "explicit generated output" in unproven.stderr
    assert full_observation.read_bytes() == original_observation


@pytest.mark.parametrize(("defect", "diagnostic"), [
    ("archive_swapped", "native source receipt"),
    ("archive_content", "differ from the sealed snapshot"),
    ("extra_member", "differ from the sealed snapshot"),
    ("duplicate_member", "duplicate members"),
    ("link_member", "ordinary files"),
    ("traversal", "stay within the snapshot"),
    ("generated_missing", "explicit generated output"),
    ("generated_mismatch", "generated output bytes differ"),
    ("input_revision", "this probe and its input revision"),
    ("legacy_receipt", "native source receipt"),
])
def test_refuses_to_emit_a_candidate_ref_for_unproven_sources(case, defect, diagnostic):
    _, snapshot, output, files, archive, result = case
    original = _native_response(archive)
    if defect in {"archive_swapped", "archive_content"}:
        _tar(archive, [("solver.py", b"answer = 3\n"), files[1]])
    elif defect in {"extra_member", "duplicate_member", "link_member", "traversal"}:
        extra = {
            "extra_member": ("notes.txt", b"extra"),
            "duplicate_member": files[0],
            "link_member": ("notes.txt", None),
            "traversal": ("../notes.txt", b"outside"),
        }
        _tar(archive, [*files, extra[defect]])
    elif defect == "generated_mismatch":
        output.write_bytes(b'{"answer":3}')
    elif defect == "input_revision":
        value = json.loads(snapshot.read_text())
        _write(snapshot, {**value, "input_revision": "dataset:public-v2"})
    response = original if defect == "archive_swapped" else _native_response(archive)
    if defect == "legacy_receipt":
        response["receipt"].update(archive_sha256=None, archive_bytes=None)
    _write(result, response)
    checked = _bind(case, pass_output=defect != "generated_missing")
    assert checked.returncode == 1
    assert checked.stdout == ""
    assert diagnostic in checked.stderr


def test_native_archive_facts_do_not_require_a_local_probe(case):
    from benchmark.runtime.sforge_candidate_binding import native_archive_references

    observation, snapshot, _, _, archive, result = case
    native = _native_response(archive)
    for path in (observation, snapshot, observation.with_suffix(observation.suffix + ".candidate.json")):
        path.unlink()
    receipt_only = native_archive_references(native)
    assert receipt_only["archive_ref"] == "archive:" + _digest(archive.read_bytes())
    assert "submitted_file_set_ref" not in receipt_only
    with_content = native_archive_references(native, archive)
    assert all(with_content[key] == value for key, value in receipt_only.items())
    assert set(with_content["files"]) == {"solver.py", "result.json"}
    _write(result, native)
    completed = subprocess.run([sys.executable, "-m", "benchmark.runtime.sforge_candidate_binding",
        "--result", str(result)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == receipt_only


def test_content_refs_do_not_erase_archive_mode_or_packaging(tmp_path):
    from benchmark.runtime.sforge_candidate_binding import native_archive_references

    refs = []
    for mode in (0o644, 0o755):
        archive = tmp_path / f"mode-{mode}.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            member = tarfile.TarInfo("solver.sh")
            body = b"#!/bin/sh\nexit 0\n"
            member.mode, member.size = mode, len(body)
            stream.addfile(member, io.BytesIO(body))
        refs.append(native_archive_references(_native_response(archive), archive))
    assert refs[0]["archive_ref"] != refs[1]["archive_ref"]
    assert refs[0]["submitted_file_set_ref"] == refs[1]["submitted_file_set_ref"]
    # Content identity claims only path/bytes; neither record claims execution equivalence.
    assert set(refs[0]) == {"archive_ref", "archive_bytes", "submitted_file_set_ref", "files",
                            "submission_id", "task_id", "round_id"}


@pytest.mark.parametrize("local_state", ["absent", "failed", "different-workload"])
def test_formal_fact_is_stable_without_local_execution_or_retained_upload(case, local_state):
    from benchmark.runtime.sforge_native_result import NativeResultScope, _measurement

    observation, snapshot, _, _, archive, _ = case
    scope = NativeResultScope(relay="http://127.0.0.1/api/v1/feedback/test", run="run", task="task",
        goal="goal", agent="agent", task_sha256="a" * 64, benchmark_sha256="b" * 64,
        higher_is_better=True, evaluation_context=_evaluation_context())
    native = _native_response(archive)
    native["receipt"]["evaluation_context"] = _evaluation_context()
    native["report"] = dict(submission_id="submission-2", task_id="task", score=7, valid=True, timed_out=False)
    if local_state == "absent":
        observation.unlink()
        snapshot.unlink()
    else:
        value = json.loads(observation.read_text())
        value.update(input_revision="dataset:small-proxy-v3", exit_code=1 if local_state == "failed" else 0)
        _write(observation, value)
    first = _measurement(scope, native, "submission-2")
    with_content = _measurement(scope, native, "submission-2", archive=archive,
        observation=observation, snapshot=snapshot, generated_files=[])
    assert first["native_measurement"] == with_content["native_measurement"]
    assert first["native_measurement"]["candidate_ref"] == "archive:" + _digest(archive.read_bytes())
    assert first["native_measurement"]["input_revision"] == "task:" + "a" * 64
    assert "submitted_file_set_ref" not in first["candidate_binding"]
    assert "submitted_file_set_ref" in with_content["candidate_binding"]
    original = observation.read_bytes() if observation.exists() else None
    archive.write_bytes(b"tampered local copy")
    unproven_content = _measurement(scope, native, "submission-2", archive=archive)
    assert unproven_content["native_measurement"] == first["native_measurement"]
    assert unproven_content["native_content_evidence_unavailable"] is True
    assert "submitted_file_set_ref" not in unproven_content["candidate_binding"]
    if original is not None:
        assert observation.read_bytes() == original
