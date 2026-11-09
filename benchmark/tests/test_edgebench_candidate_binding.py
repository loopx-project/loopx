"""Native submission binding over exact receipt and local probe artifacts."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from benchmark.runtime.research_probe import candidate_snapshot


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
