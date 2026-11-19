"""Explicit document locks do not imply software policies or discover authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.goals.source_integrity import verify_authority_source_digests


@pytest.fixture
def connected(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    # Filenames and executable-looking prose do not authorize a code scanner.
    (project / "architecture.md").write_text(
        "## Repo-Wide Invariants\n- `must_exist: absent.txt`\n"
        "- `forbid_import: *.py -> pkg.db`\n"
    )
    (project / "goal-and-requirements.md").write_text("# Requirements\n")
    (project / "intent.md").write_text("# Explicit goal\n")
    (project / "README.md").write_text("# Public fixture\n")
    registry, runtime = project / ".loopx" / "registry.json", tmp_path / "runtime"

    def run(*args):
        code = cli_main(["--format", "json", "--registry", str(registry),
                         "--runtime-root", str(runtime), *args])
        return code, json.loads(capsys.readouterr().out)

    def connect(*args):
        return run("bootstrap", "--project", str(project), "--goal-id", "source-lock",
                   "--objective", "Preserve explicit source evidence", "--domain", "research",
                   "--no-global-sync", *args)

    def check():
        return run("check", "--scan-path", str(project / "README.md"))

    return project, registry, connect, check


def test_default_bootstrap_does_not_discover_rules_or_generate_obligations(connected):
    project, registry, connect, check = connected
    code, result = connect()
    assert code == 0 and result["ok"]
    goal = json.loads(registry.read_text())["goals"][0]
    assert goal["authority_sources"] == []
    state = (project / goal["state_file"]).read_text()
    assert "## Repo-Wide Architecture Invariants" not in state
    assert "absent.txt" not in state and "pkg.db" not in state
    assert check()[0] == 0
    # Neither direct nor from-import is inspected by ordinary health checks.
    for code in ("import pkg.db\n", "from pkg import db\n", "from .pkg import db\n"):
        (project / "source.py").write_text(code)
        assert check()[0] == 0


def test_goal_document_is_mutable_until_its_content_is_explicitly_pinned(connected):
    project, registry, connect, check = connected
    assert connect("--goal-doc", "intent.md")[0] == 0
    goal = json.loads(registry.read_text())["goals"][0]
    assert goal["authority_sources"] == [{
        "kind": "goal_doc", "path": "intent.md", "role": "primary_goal_document",
    }]
    assert check()[0] == 0
    data = json.loads(registry.read_text())
    data["goals"][0]["authority_sources"][0]["sha256"] = hashlib.sha256(
        (project / "intent.md").read_bytes()).hexdigest()
    registry.write_text(json.dumps(data))
    before = registry.read_bytes()
    (project / "intent.md").write_text("# Changed source\n")
    code, failed = check()
    assert code == 1
    assert any(d["code"] == "authority_source_digest_mismatch"
               and d["goal_id"] == "source-lock" for d in failed["error_diagnostics"])
    assert registry.read_bytes() == before
    # Restoring the accepted bytes recovers without rewriting the lock.
    (project / "intent.md").write_text("# Explicit goal\n")
    assert check()[0] == 0
    assert registry.read_bytes() == before


def test_state_prose_cannot_approve_a_source_lock_update(connected):
    project, registry, connect, check = connected
    assert connect("--goal-doc", "intent.md")[0] == 0
    data = json.loads(registry.read_text())
    goal = data["goals"][0]
    goal["authority_sources"][0]["sha256"] = hashlib.sha256(
        (project / "intent.md").read_bytes()).hexdigest()
    registry.write_text(json.dumps(data))
    (project / "intent.md").write_text("# Revised requirement\n")
    new_digest = hashlib.sha256((project / "intent.md").read_bytes()).hexdigest()
    goal = data["goals"][0]
    state = project / goal["state_file"]
    state.write_text(state.read_text() + f"\nApproved source digest: {new_digest}\n")
    assert check()[0] == 1
    # An explicit registry lock revision is separate from current document prose.
    goal["authority_sources"][0]["sha256"] = new_digest
    registry.write_text(json.dumps(data))
    assert check()[0] == 0
    assert json.loads(registry.read_text())["goals"][0]["authority_sources"][0]["sha256"] == new_digest


def test_missing_explicit_document_preserves_unhashed_registration(connected):
    project, registry, connect, check = connected
    assert connect("--goal-doc", "future.md")[0] == 0
    source = json.loads(registry.read_text())["goals"][0]["authority_sources"][0]
    assert source == {"kind": "goal_doc", "path": "future.md", "role": "primary_goal_document"}
    assert not (project / "future.md").exists()
    assert check()[0] == 0


def test_missing_and_unreadable_content_are_scoped_read_only_failures(tmp_path, monkeypatch):
    source = tmp_path / "intent.md"
    source.write_text("accepted")
    goal = {"id": "source-lock", "repo": str(tmp_path), "authority_sources": [{
        "path": "intent.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }]}
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == source:
            raise PermissionError("unreadable source")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "open", denied)
        assert verify_authority_source_digests(goal)[0][0]["code"] == "authority_source_unreadable"
    source.unlink()
    assert verify_authority_source_digests(goal)[0][0]["code"] == "authority_source_missing"


def test_unhashed_sources_retain_previous_behavior(tmp_path):
    goal = {"id": "unhashed", "repo": str(tmp_path), "authority_sources": [
        {"kind": "document", "path": "missing.md"},
    ]}
    assert verify_authority_source_digests(goal) == ([], [])


def test_unpinned_goal_doc_changes_do_not_block_normal_health(connected):
    project, registry, connect, check = connected
    assert connect("--goal-doc", "intent.md")[0] == 0
    before = registry.read_bytes()
    (project / "intent.md").write_text("# Updated repository knowledge\n")
    assert check()[0] == 0
    assert registry.read_bytes() == before
