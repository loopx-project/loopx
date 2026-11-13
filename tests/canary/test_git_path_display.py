"""A name Git can hold must never make a report's JSON text unparseable."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.contract import scan_public_boundary
from loopx.canary.maintainability_ratchet import _dependency_finding
from loopx.control_plane.runtime.file_reads import Utf8FileRead
from loopx.paths import display_path_text


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RATCHET_COMMAND = (
    REPOSITORY_ROOT / "examples" / "control_plane"
    / "control-plane-maintainability-ratchet-smoke.py"
)


def _os_path(path: Path, name: bytes) -> str:
    return os.path.join(os.fsencode(path), name)


def _supports_raw_name(directory: Path) -> bool:
    """Report whether this filesystem can hold a name that is not UTF-8."""

    probe = _os_path(directory, b"probe\xff")
    try:
        os.close(os.open(probe, os.O_CREAT | os.O_WRONLY, 0o600))
    except OSError:
        return False
    os.unlink(probe)
    return True


def _write_raw_name(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")


def test_display_path_text_marks_only_the_undecodable_record() -> None:
    assert display_path_text(Path("loopx/plain.py")) == "loopx/plain.py"
    assert display_path_text("docs/中文.md") == "docs/中文.md"

    escaped = display_path_text("loopx/bad\udcff.py")

    # The label keeps the record's bytes, so the reader can still tell which
    # file the finding names, and JSON written from it stays valid UTF-8.
    assert escaped == "git-bytes:" + repr(b"loopx/bad\xff.py")
    assert json.dumps({"path": escaped}, ensure_ascii=False).encode("utf-8")
    # Two distinct bytes records never collapse onto one label.
    assert escaped != display_path_text("loopx/bad\udcfe.py")
    # A decodable name that merely looks like the marker keeps its own label.
    assert display_path_text("git-bytes:b'x'") == "git-bytes:b'x'"


@pytest.mark.skipif(
    os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only"
)
def test_maintainability_json_keeps_every_tracked_name(
    tmp_path: Path,
) -> None:
    """Plain, CR and undecodable module names all reach one parseable report."""

    repo = tmp_path / "repo"
    if not _supports_raw_name(tmp_path):
        pytest.skip("this filesystem rejects non-UTF-8 file names")
    _init_repo(repo)
    control_plane = repo / "loopx" / "control_plane"
    control_plane.mkdir(parents=True)
    (repo / "loopx" / "quota.py").write_text("", encoding="utf-8")
    (repo / "loopx" / "status.py").write_text("", encoding="utf-8")
    padding = "\n".join(["# padding"] * 2001)
    for name in ("plain.py", "cr\rname.py"):
        _write_raw_name(str(control_plane / name), padding)
    _write_raw_name(_os_path(control_plane, b"hot\xffspot.py"), padding)
    _git(repo, "add", "--", "loopx")
    _git(repo, "commit", "-m", "oversized modules, one with an undecodable name")

    command = repo / "examples" / "control_plane" / RATCHET_COMMAND.name
    command.parent.mkdir(parents=True)
    command.write_bytes(RATCHET_COMMAND.read_bytes())
    result = subprocess.run(
        [sys.executable, str(command), "--format", "json"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        check=False,
        timeout=120,
    )

    assert result.returncode == 1, result.stderr
    # The command's own writer is `json.dumps(..., ensure_ascii=False)`; a
    # verbatim surrogate name would make this strict decode fail.
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["ok"] is False
    findings = [
        item
        for item in payload["findings"]
        if item["category"] == "module_metric_budget"
    ]
    paths = {item["path"] for item in findings}
    assert paths == {
        "loopx/control_plane/plain.py",
        "loopx/control_plane/cr\rname.py",
        "git-bytes:" + repr(b"loopx/control_plane/hot\xffspot.py"),
    }
    escaped = next(item for item in findings if item["path"].startswith("git-bytes:"))
    assert escaped["id"].startswith("module_metric_budget:git-bytes:")
    assert escaped["module"].startswith("git-bytes:")


@pytest.mark.skipif(
    os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only"
)
def test_public_boundary_json_keeps_every_tracked_name(tmp_path: Path) -> None:
    """The check command's JSON names plain, CR and undecodable findings."""

    repo = tmp_path / "repo"
    if not _supports_raw_name(tmp_path):
        pytest.skip("this filesystem rejects non-UTF-8 file names")
    _init_repo(repo)
    outgoing = repo / "outgoing"
    outgoing.mkdir()
    _write_raw_name(str(outgoing / "plain.md"), "ticket t-" + "20260828123456-example\n")
    _write_raw_name(
        str(outgoing / "cr\rname.md"), "\nticket t-" + "20260828123456-example\n"
    )
    _write_raw_name(
        _os_path(outgoing, b"leak\xff.md"),
        "\n\nticket t-" + "20260828123456-example\n",
    )
    _git(repo, "add", "--", "outgoing")
    _git(repo, "commit", "-m", "leaking files, one with an undecodable name")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--registry",
            str(tmp_path / "registry.json"),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "check",
            "--scan-root",
            "outgoing",
        ],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        check=False,
        timeout=120,
    )

    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["ok"] is False
    messages = "\n".join(str(item) for item in payload["errors"])
    expected = {
        "plain.md:1: internal_task_id",
        "cr\rname.md:2: internal_task_id",
        "git-bytes:" + repr(b"leak\xff.md") + ":3: internal_task_id",
    }
    for label in expected:
        assert label in messages, messages

    # The library entry the command wraps keeps the same projection.
    boundary = scan_public_boundary([outgoing], registry={})
    assert boundary["ok"] is False
    assert set(boundary["hits"]) == expected
    assert json.dumps(boundary, ensure_ascii=False).encode("utf-8")


def test_dependency_finding_escapes_an_undecodable_source(
    tmp_path: Path,
) -> None:
    """The ratchet's own projection keeps a record it cannot spell readable."""

    path = tmp_path / "loopx" / "control_plane" / "bad\udcff.py"

    finding = _dependency_finding(
        source="loopx.control_plane.bad\udcff",
        target="loopx.cli",
        path=path,
        repository_root=tmp_path,
        rule="control_plane_outward_dependency",
    )

    assert finding["path"] == "git-bytes:" + repr(b"loopx/control_plane/bad\xff.py")
    assert finding["source"].startswith("git-bytes:")
    assert finding["id"].startswith("dependency_debt:git-bytes:")
    assert json.dumps(finding, ensure_ascii=False).encode("utf-8")


def test_boundary_report_escapes_an_undecodable_scanned_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan's report names a file whose bytes UTF-8 cannot encode."""

    root = tmp_path / "outgoing"
    root.mkdir()
    raw = root / "leak\udcff.md"
    monkeypatch.setattr(
        "loopx.contract.iter_scan_files", lambda *args, **kwargs: [raw]
    )
    monkeypatch.setattr(
        "loopx.contract.iter_utf8_file_reads",
        lambda paths: iter([Utf8FileRead(raw, "ticket t-20260828123456-example", None)]),
    )

    boundary = scan_public_boundary([root], registry={})

    expected = "git-bytes:" + repr(b"leak\xff.md") + ":1: internal_task_id"
    assert boundary["ok"] is False
    assert boundary["hits"] == [expected]
    assert boundary["blocking_hits"] == [expected]
    assert json.dumps(boundary, ensure_ascii=False).encode("utf-8")
