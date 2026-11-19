"""File-content I/O is separate from measurement and execution declarations."""
import hashlib
import json

import pytest

from loopx.capabilities.explore.candidate_content import candidate_snapshot, candidate_manifest_sha256


def test_content_manifest_keeps_existing_encoding_and_selected_file_scope(tmp_path):
    solver = tmp_path / "solver.py"
    output = tmp_path / "output.json"
    solver.write_bytes(b"print(7)\n")
    output.write_bytes(b"7")
    expected = [{"path": "output.json", "sha256": hashlib.sha256(b"7").hexdigest()},
                {"path": "solver.py", "sha256": hashlib.sha256(b"print(7)\n").hexdigest()}]
    digest, rows = candidate_snapshot([solver, output], tmp_path)
    assert rows == expected
    assert digest == hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert candidate_snapshot([output, solver], tmp_path) == (digest, rows)
    solver.chmod(0o755)
    assert candidate_snapshot([solver, output], tmp_path)[0] == digest
    subset = candidate_snapshot([solver], tmp_path)[0]
    output.write_bytes(b"8")
    assert candidate_snapshot([solver, output], tmp_path)[0] != digest
    assert candidate_snapshot([solver], tmp_path)[0] == subset
    assert candidate_manifest_sha256(rows) == digest


def test_capture_rejects_ambiguous_or_unbounded_file_selections(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"x")
    for paths in ([], [path, path], [tmp_path]):
        with pytest.raises(ValueError):
            candidate_snapshot(paths, tmp_path)
