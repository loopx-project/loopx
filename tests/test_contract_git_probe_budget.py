"""A Git probe that runs out of budget must not turn into a clean answer.

The scan decides whether a tracked private file is published. "Git says it is
not in the index" and "Git did not answer" therefore have to stay different
answers, and the process tree a blocked probe started has to stop with it.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from loopx import contract


def _registry(tmp_path: Path) -> Path:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "f@e.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    (repo / "ACTIVE_GOAL_STATE.md").write_text("# tracked private state\n", encoding="utf-8")
    (repo / "NOTES.md").write_text("# public notes\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "ACTIVE_GOAL_STATE.md", "NOTES.md"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


def _delay_git(
    tmp_path: Path, needle: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Put a `git` on PATH that waits forever on the query holding ``needle``."""

    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir = tmp_path / "slow-bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        'for argument in "$@"; do\n'
        f'  case "$argument" in *{needle}*) sleep 30 ;; esac\n'
        "done\n"
        f"exec {shlex.quote(real_git)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_an_unanswered_ownership_question_is_not_a_negative_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "public.md"
    target.write_text("public", encoding="utf-8")
    asked: list[tuple[str, ...]] = []

    def fake_query(*arguments: str) -> contract._GitAnswer | None:
        asked.append(arguments)
        return None

    def fake_bytes_query(*arguments: str) -> contract._GitBytesAnswer | None:
        asked.append(arguments)
        return contract._GitBytesAnswer(
            returncode=0, stdout=os.fsencode(str(tmp_path)) + b"\n"
        )

    monkeypatch.setattr(contract, "_git_query", fake_query)
    # The worktree root is a byte record now, so the probe's own read is the
    # one that answers `rev-parse`; ownership still asks the text helper.
    monkeypatch.setattr(contract, "_git_query_bytes", fake_bytes_query)
    observed = contract._git_probe(target)

    assert observed["tracked"] is None
    assert observed["ignored"] is None
    # The first unanswered question settles it; the ignore flag of a file whose
    # ownership is unknown cannot change any caller's answer.
    assert [arguments[2] for arguments in asked] == ["rev-parse", "ls-files"]


@pytest.mark.skipif(os.name != "posix", reason="the delayed git is a POSIX script")
def test_tracked_private_state_stays_blocked_when_the_track_query_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path)
    _delay_git(tmp_path, "--error-unmatch", monkeypatch)
    # The budget has to be well above a real Git start-up, while the delayed
    # query still spends far longer than the test is willing to wait.
    monkeypatch.setattr(contract, "GIT_PROBE_TIMEOUT_SECONDS", 1.5)

    boundary = contract.scan_public_boundary([repo])

    assert boundary["ok"] is False
    assert "ACTIVE_GOAL_STATE.md" in boundary["unavailable_git_probes"]
    # Private state is still never opened while ownership is unproven.
    assert boundary["skipped_private_state_files"] == ["ACTIVE_GOAL_STATE.md"]

    checked = contract.check_contract(
        registry_path=_registry(tmp_path),
        runtime_root_override=None,
        scan_roots=[repo],
        limit=5,
    )
    assert checked["ok"] is False
    assert checked["public_boundary_scan"]["ok"] is False
    assert "ACTIVE_GOAL_STATE.md" in checked["public_boundary_scan"][
        "unavailable_git_probes"
    ]
    codes = [diagnostic["code"] for diagnostic in checked["error_diagnostics"]]
    assert "public_boundary_scan_git_unavailable" in codes
    assert not [
        check for check in checked["checks"] if check.startswith("public boundary scan clean")
    ]


@pytest.mark.skipif(os.name != "posix", reason="the delayed git is a POSIX script")
def test_an_incomplete_enumeration_is_not_a_complete_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path)
    _delay_git(tmp_path, "-z", monkeypatch)
    monkeypatch.setattr(contract, "GIT_PROBE_TIMEOUT_SECONDS", 1.5)

    started = time.monotonic()
    boundary = contract.scan_public_boundary([repo])
    elapsed = time.monotonic() - started

    assert boundary["ok"] is False
    assert str(repo) in boundary["unavailable_git_probes"]
    assert elapsed < 20, f"a blocked enumeration held the scan for {elapsed:.1f}s"


@pytest.mark.skipif(os.name != "posix", reason="core.fsmonitor needs a POSIX hook")
def test_a_timed_out_probe_takes_the_helpers_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git starts an fsmonitor hook for `ls-files`, so the budget owns it too."""

    repo = _repository(tmp_path)
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    hook = repo / ".git" / "blocking-fsmonitor"
    hook.write_text(
        "#!/bin/sh\n"
        f"while :; do echo tick >> {shlex.quote(str(heartbeats))}; sleep 0.1; done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True
    )
    monkeypatch.setattr(contract, "GIT_PROBE_TIMEOUT_SECONDS", 0.8)

    started = time.monotonic()
    with pytest.raises(contract.GitProbeUnavailable):
        contract._tracked_scan_files(repo)
    assert time.monotonic() - started < 6

    retired = heartbeats.stat().st_size
    assert retired > 0, "the fixture hook never ran, so this proves nothing"
    time.sleep(0.6)
    assert heartbeats.stat().st_size == retired, (
        "the fsmonitor hook and its child outlived the probe budget"
    )


def test_a_non_git_directory_still_scans_cleanly(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir()
    (public / "NOTES.md").write_text("# Public\n", encoding="utf-8")

    boundary = contract.scan_public_boundary([public])

    assert boundary["ok"] is True
    assert boundary["unavailable_git_probes"] == []
