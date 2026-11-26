"""A name Git can hold must never make a report's JSON text unparseable."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from loopx.contract import scan_public_boundary
from loopx.canary.maintainability_ratchet import _dependency_finding
from loopx.control_plane.runtime.file_reads import Utf8FileRead
from loopx.paths import display_path_bytes, display_path_text, display_report_text


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RATCHET_COMMAND = (
    REPOSITORY_ROOT / "examples" / "control_plane"
    / "control-plane-maintainability-ratchet-smoke.py"
)

# Materialize the synthetic ID only in test input so the public source scan
# can still inspect this suite.
_SYNTHETIC_TASK_TEXT = "ticket t-" + "20260828123456-example"


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


def _init_repo(repo: Path, *, exists: bool = False) -> None:
    if not exists:
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
    # A decodable name that spells the reserved label itself is escaped too,
    # so no ordinary label can stand for the undecodable record above.
    literal = "git-bytes:b'x'"
    assert display_path_text(literal) == "git-bytes:" + repr(b"git-bytes:b'x'")
    assert display_path_text(literal) != display_path_text("git-bytes:\udcff")

    # Every label recovers the exact bytes it names.
    for record in ("loopx/plain.py", "loopx/bad\udcff.py", literal):
        assert display_path_bytes(display_path_text(record)) == os.fsencode(record)


def test_a_name_that_spells_the_escape_keeps_its_own_finding(tmp_path: Path) -> None:
    """Two Git records never share one report label."""

    raw = "loopx/control_plane/hot\udcff.py"
    literal = display_path_text(raw)

    escaped = display_path_text(raw)
    twin = display_path_text(literal)

    assert escaped == "git-bytes:" + repr(b"loopx/control_plane/hot\xff.py")
    assert twin != escaped
    assert display_path_bytes(twin) == os.fsencode(literal)
    assert Path(os.fsdecode(display_path_bytes(escaped))).name == "hot\udcff.py"


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
        lambda paths: iter([Utf8FileRead(raw, _SYNTHETIC_TASK_TEXT, None)]),
    )

    boundary = scan_public_boundary([root], registry={})

    expected = "git-bytes:" + repr(b"leak\xff.md") + ":1: internal_task_id"
    assert boundary["ok"] is False
    assert boundary["hits"] == [expected]
    assert boundary["blocking_hits"] == [expected]
    assert json.dumps(boundary, ensure_ascii=False).encode("utf-8")


@pytest.mark.skipif(
    os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only"
)
def test_two_real_git_records_keep_distinct_labels(tmp_path: Path) -> None:
    """A legal name that spells the escape still names its own record."""

    repo = tmp_path / "repo"
    if not _supports_raw_name(tmp_path):
        pytest.skip("this filesystem rejects non-UTF-8 file names")
    _init_repo(repo)
    outgoing = repo / "outgoing"
    outgoing.mkdir()
    tiny = "ticket t-" + "20260828123456-example\n"
    raw_name = "leak\udcff.md"
    _write_raw_name(_os_path(outgoing, b"leak\xff.md"), "\n\n" + tiny)
    twin_name = display_path_text(raw_name)
    _write_raw_name(str(outgoing / twin_name), "\n" + tiny)
    _git(repo, "add", "--", "outgoing")
    _git(repo, "commit", "-m", "an escape label that is also a real file name")

    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", "outgoing"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    names = {os.fsdecode(Path(os.fsdecode(record)).name) for record in tracked if record}
    assert names == {raw_name, twin_name}

    raw_label = display_path_text(raw_name)
    twin_label = display_path_text(twin_name)
    assert raw_label != twin_label
    assert display_path_bytes(raw_label) == os.fsencode(raw_name)
    assert display_path_bytes(twin_label) == os.fsencode(twin_name)

    # The real scan still names the record it found at its own line.
    boundary = scan_public_boundary([outgoing], registry={})
    assert raw_label + ":3: internal_task_id" in boundary["hits"]
    assert json.dumps(boundary, ensure_ascii=False).encode("utf-8")


@pytest.mark.skipif(
    os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only"
)
@pytest.mark.parametrize("raw_root", [b"repo\rname", b"repo\xffname"])
def test_tracked_scan_keeps_a_root_the_text_read_cannot_spell(
    tmp_path: Path, raw_root: bytes
) -> None:
    """A tracked file below a byte-only root still reaches the report."""

    root = Path(os.fsdecode(os.path.join(os.fsencode(tmp_path), raw_root)))
    try:
        os.mkdir(os.fsencode(root))
    except (OSError, UnicodeEncodeError):
        pytest.skip("this filesystem rejects the root name")
    _init_repo(root, exists=True)
    hidden = root / ".venv"
    hidden.mkdir()
    _write_raw_name(
        str(hidden / "leak.md"), "ticket t-" + "20260828123456-example\n"
    )
    _git(root, "add", "-f", "--", ".venv")
    _git(root, "commit", "-m", "tracked file under a pruned directory")

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
            ".",
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        check=False,
        timeout=120,
    )

    payload = json.loads(result.stdout.decode("utf-8"))
    messages = "\n".join(str(item) for item in payload["errors"])
    assert "leak.md:1: internal_task_id" in messages, payload
    assert result.returncode == 1, result.stderr
    assert payload["ok"] is False
    assert json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_boundary_report_keeps_an_escape_label_and_a_real_twin_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One label names one Git record, even when the other name spells it."""

    root = tmp_path / "outgoing"
    root.mkdir()
    raw = root / "leak\udcff.md"
    twin = root / display_path_text("leak\udcff.md")
    twin.write_text(f"{_SYNTHETIC_TASK_TEXT}\n", encoding="utf-8")
    monkeypatch.setattr(
        "loopx.contract.iter_scan_files", lambda *args, **kwargs: [raw, twin]
    )
    monkeypatch.setattr(
        "loopx.contract.iter_utf8_file_reads",
        lambda paths: iter(
            [
                Utf8FileRead(raw, f"\n{_SYNTHETIC_TASK_TEXT}", None),
                Utf8FileRead(twin, _SYNTHETIC_TASK_TEXT, None),
            ]
        ),
    )

    boundary = scan_public_boundary([root], registry={})

    raw_label = display_path_text("leak\udcff.md")
    twin_label = display_path_text(display_path_text("leak\udcff.md"))
    assert raw_label != twin_label
    assert boundary["ok"] is False
    assert set(boundary["hits"]) == {
        raw_label + ":2: internal_task_id",
        twin_label + ":1: internal_task_id",
    }
    assert json.dumps(boundary, ensure_ascii=False).encode("utf-8")


def test_worktree_root_reads_bytes_and_removes_only_gits_terminator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root keeps CR, LF and undecodable bytes instead of being rewritten."""

    from loopx import contract

    answer = contract._GitBytesAnswer

    for raw_root in (b"/tmp/repo\rname", b"/tmp/repo\nname", b"/tmp/repo\xffname"):
        monkeypatch.setattr(
            contract,
            "_git_query_bytes",
            lambda *args, _raw=raw_root: answer(
                returncode=0, stdout=_raw + b"\n", stderr=b""
            ),
        )
        assert contract._git_worktree_root(tmp_path) == Path(
            os.fsdecode(raw_root)
        ).resolve()


def test_display_report_text_escapes_only_the_unspellable_bytes() -> None:
    """A message keeps its words and still carries a decodable path label."""

    assert display_report_text("registry should be gitignored") == (
        "registry should be gitignored"
    )
    raw = "registry should be gitignored: /tmp/repo\udcff/.loopx/registry.json"
    escaped = display_report_text(raw)

    assert escaped == (
        "registry should be gitignored: /tmp/repo"
        + "git-bytes:" + repr(b"\xff")
        + "/.loopx/registry.json"
    )
    assert "\udcff" not in escaped
    assert json.dumps({"warning": escaped}, ensure_ascii=False).encode("utf-8")
    fragment = escaped.split("git-bytes:", 1)[1].split("/.loopx", 1)[0]
    assert display_path_bytes("git-bytes:" + fragment) == b"\xff"


def test_check_report_projection_covers_config_and_diagnostics() -> None:
    """Registry, runtime root and nested diagnostics share one output boundary."""

    from loopx.contract import display_report_value

    payload = {
        "ok": False,
        "registry": "/tmp/repo\udcff/.loopx/registry.json",
        "runtime_root": "/tmp/repo\udcff/.loopx/runtime",
        "scan_roots": ["git-bytes:" + repr(b"/tmp/repo\xff")],
        "errors": ["leak.md:1: internal_task_id"],
        "warnings": ["runtime root does not exist yet: /tmp/repo\udcff/.loopx/runtime"],
        "public_boundary_scan": {
            "private_state_git_warnings": ["tracked private state: /tmp/repo\udcff/x"],
        },
    }
    projected = display_report_value(payload)
    encoded = json.dumps(projected, ensure_ascii=False).encode("utf-8")
    decoded = json.loads(encoded.decode("utf-8"))

    fragment = "git-bytes:" + repr(b"\xff")
    assert decoded["registry"] == f"/tmp/repo{fragment}/.loopx/registry.json"
    assert decoded["runtime_root"] == f"/tmp/repo{fragment}/.loopx/runtime"
    assert decoded["scan_roots"] == ["git-bytes:" + repr(b"/tmp/repo\xff")]
    assert fragment in decoded["warnings"][0]
    assert decoded["public_boundary_scan"]["private_state_git_warnings"][0].endswith(
        "x"
    )


@pytest.mark.skipif(
    os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only"
)
def test_check_json_keeps_project_local_config_under_a_byte_root(
    tmp_path: Path,
) -> None:
    """Registry and runtime inside the repo never block the findings' JSON."""

    raw_root = b"repo\xffname"
    root = Path(os.fsdecode(os.path.join(os.fsencode(tmp_path), raw_root)))
    try:
        os.mkdir(os.fsencode(root))
    except (OSError, UnicodeEncodeError):
        pytest.skip("this filesystem rejects the root name")
    _init_repo(root, exists=True)
    hidden = root / ".venv"
    hidden.mkdir()
    _write_raw_name(
        str(hidden / "leak.md"), "ticket t-" + "20260828123456-example\n"
    )
    _git(root, "add", "-f", "--", ".venv")
    _git(root, "commit", "-m", "tracked file under a pruned directory")

    loopx_config = root / ".loopx"
    loopx_config.mkdir()
    registry = loopx_config / "registry.json"
    registry.write_text(json.dumps({"goals": []}), encoding="utf-8")
    runtime_root = loopx_config / "runtime"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "check",
            "--scan-root",
            ".",
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        check=False,
        timeout=120,
    )

    # Every byte of the report must be readable, including the configuration
    # and warning paths that live under the same undecodable root.
    payload = json.loads(result.stdout.decode("utf-8"))
    assert result.returncode == 1, result.stderr
    assert payload["ok"] is False
    messages = "\n".join(str(item) for item in payload["errors"])
    assert "leak.md:1: internal_task_id" in messages, payload
    # The label carries the whole pathname, so compare the bytes each field
    # recovers instead of gluing an unescaped suffix onto a closed literal.
    assert display_path_bytes(payload["registry"]) == os.fsencode(registry)
    assert display_path_bytes(payload["runtime_root"]) == os.fsencode(runtime_root)
    assert json.dumps(payload, ensure_ascii=False).encode("utf-8")

def test_check_exception_payload_projects_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exception branch uses whole-path labels before JSON serialization."""

    from loopx.cli_commands import status

    raw_root = "/tmp/repo\udcffname"
    registry = Path(raw_root) / ".loopx" / "registry.json"
    runtime_root = Path(raw_root) / "runtime"
    args = SimpleNamespace(
        scan_path=[raw_root], scan_root=raw_root, limit=5, format="json"
    )
    captured: dict[str, object] = {}

    def fail(**_kwargs: object) -> dict[str, object]:
        raise ValueError("goals must be a list")

    monkeypatch.setattr(status, "check_contract", fail)
    exit_code = status.handle_check_command(
        args,
        registry_path=registry,
        runtime_root_arg=str(runtime_root),
        allow_missing_registry=False,
        print_payload=lambda payload, fmt, _renderer: captured.update(
            payload=payload, fmt=fmt
        ),
    )

    assert exit_code == 1
    decoded = json.loads(
        json.dumps(captured["payload"], ensure_ascii=False).encode("utf-8")
    )
    assert decoded["errors"] == ["goals must be a list"]
    assert display_path_bytes(decoded["registry"]) == os.fsencode(registry)
    assert display_path_bytes(decoded["runtime_root"]) == os.fsencode(runtime_root)
    assert display_path_bytes(decoded["scan_roots"][0]) == os.fsencode(raw_root)



@pytest.mark.skipif(os.name != "posix", reason="POSIX filename fixture")
def test_explicit_scan_root_keeps_git_pathspec_magic_literal(tmp_path: Path) -> None:
    """A directory name is passed to Git as a literal path, not a pathspec."""

    from loopx.contract import _tracked_scan_files

    repo = tmp_path / "repo"
    _init_repo(repo)
    magic_dir = repo / ":(missing-magic)"
    colon_dir = repo / ":"
    magic_dir.mkdir()
    colon_dir.mkdir()
    magic_file = magic_dir / "leak.md"
    colon_file = colon_dir / "clean.md"
    _write_raw_name(str(magic_file), f"{_SYNTHETIC_TASK_TEXT}\n")
    _write_raw_name(str(colon_file), "clean\n")
    _git(repo, "--literal-pathspecs", "add", "--", str(magic_dir), str(colon_dir))
    _git(repo, "commit", "-m", "tracked pathspec-like directories")

    assert _tracked_scan_files(magic_dir) == [magic_file.resolve()]
    assert _tracked_scan_files(colon_dir) == [colon_file.resolve()]


@pytest.mark.skipif(
    os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only"
)
def test_check_json_exception_under_byte_root_is_decodable(
    tmp_path: Path,
) -> None:
    """An invalid registry still returns a report a strict UTF-8 reader can parse."""

    raw_root = b"repo\xffname"
    root = Path(os.fsdecode(os.path.join(os.fsencode(tmp_path), raw_root)))
    try:
        os.mkdir(os.fsencode(root))
    except (OSError, UnicodeEncodeError):
        pytest.skip("this filesystem rejects the root name")
    loopx_config = root / ".loopx"
    loopx_config.mkdir()
    registry = loopx_config / "registry.json"
    registry.write_text(
        json.dumps({"goals": {"invalid": True}}), encoding="utf-8"
    )
    runtime_root = loopx_config / "runtime"
    runtime_root.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "check",
            "--scan-root",
            str(root),
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
        check=False,
        timeout=120,
    )

    payload = json.loads(result.stdout.decode("utf-8"))
    assert result.returncode == 1, result.stderr
    assert payload["ok"] is False
    assert any("goals must be a list" in str(item) for item in payload["errors"])
    assert display_path_bytes(payload["registry"]) == os.fsencode(registry)
    assert display_path_bytes(payload["runtime_root"]) == os.fsencode(runtime_root)
    assert display_path_bytes(payload["scan_roots"][0]) == os.fsencode(root)


@pytest.mark.skipif(
    os.name != "posix", reason="Git's pathspec environment is POSIX-only here"
)
def test_an_inherited_pathspec_mode_cannot_empty_the_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selected pruned directory reads the same with Git's glob mode inherited."""

    from loopx.contract import _tracked_scan_files

    repo = tmp_path / "repo"
    _init_repo(repo)
    pruned = repo / ".venv"
    pruned.mkdir()
    (pruned / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    leak = pruned / "leak.md"
    _write_raw_name(str(leak), f"{_SYNTHETIC_TASK_TEXT}\n")
    _git(repo, "add", "-f", "--", ".venv")
    _git(repo, "commit", "-m", "tracked file in a pruned directory")

    plain = _tracked_scan_files(pruned)
    monkeypatch.setenv("GIT_GLOB_PATHSPECS", "1")
    inherited = _tracked_scan_files(pruned)
    assert plain == inherited == sorted([leak.resolve(), (pruned / "pyvenv.cfg").resolve()])

    boundary = scan_public_boundary([pruned], registry={})
    assert boundary["ok"] is False
    assert boundary["hits"] == ["leak.md:1: internal_task_id"]
    assert boundary["unavailable_git_probes"] == []

    # A selected directory with nothing tracked stays a clean empty result.
    empty = repo / "empty"
    empty.mkdir()
    assert _tracked_scan_files(empty) == []


def test_private_state_keeps_ignored_unignored_and_tracked_apart(
    tmp_path: Path,
) -> None:
    """Git's own `.gitignore` answer decides whether the warning is due."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    for name in ("ignored", "plain", "tracked"):
        directory = repo / name
        directory.mkdir()
        (directory / "ACTIVE_GOAL_STATE.md").write_text("state\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked/ACTIVE_GOAL_STATE.md")
    _git(repo, "commit", "-m", "private state fixture")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": []}), encoding="utf-8"
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()

    def investigate(name: str) -> tuple[int, dict]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime_root),
                "check",
                "--scan-root",
                str(repo / name),
            ],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT)},
            check=False,
            timeout=120,
        )
        return result.returncode, json.loads(result.stdout.decode("utf-8"))

    code, ignored = investigate("ignored")
    assert code == 0, ignored
    assert ignored["warnings"] == []
    assert ignored["public_boundary_scan"]["unavailable_git_probes"] == []

    code, plain = investigate("plain")
    assert code == 0, plain
    assert plain["warnings"] == ["ACTIVE_GOAL_STATE.md: private state should be gitignored"]

    code, tracked = investigate("tracked")
    assert code == 1, tracked
    assert tracked["errors"] == ["ACTIVE_GOAL_STATE.md: local_private_state"]
