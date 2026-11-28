"""Text-mode subprocess reads in the shipped runtime must pin UTF-8 explicitly.

`subprocess.run(..., text=True)` without `encoding=` decodes the child's output
with `locale.getpreferredencoding(False)` - gbk on a zh-CN Windows host - while
`gh`, `git`, and the wrapped LoopX CLI all emit UTF-8. On such a host a single
non-ASCII byte kills the pipe reader thread, `result.stdout` becomes `None`
with `returncode == 0`, and the caller reports a misleading secondary error.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from loopx.capabilities.change_quality import scope
from loopx.hosts.claude.scripts import goalmode_cmd

REPO_ROOT = Path(__file__).resolve().parents[1]


def _text_mode_calls_without_utf8_encoding(package_root: Path | None = None) -> list[str]:
    """Locate text-mode subprocess reads that do not pin UTF-8.

    Reporting only a missing `encoding` keyword is too weak: `encoding="latin-1"`
    reproduces the same locale bug with an explicit codec, so the guard must
    check the pinned value as well.
    """

    root = package_root or (REPO_ROOT / "loopx")
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "Popen", "check_output")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                continue
            text_mode = any(
                kw.arg == "text"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if not text_mode:
                continue
            pinned = next(
                (
                    kw.value.value
                    for kw in node.keywords
                    if kw.arg == "encoding"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ),
                None,
            )
            if pinned == "utf-8":
                continue
            offenders.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    return offenders


def test_shipped_runtime_pins_utf8_for_every_text_mode_subprocess_call() -> None:
    """A text-mode child read without an explicit codec reopens the #4155 bug."""

    assert _text_mode_calls_without_utf8_encoding() == []


def test_guard_reports_missing_and_non_utf8_codecs(tmp_path: Path) -> None:
    """The guard covers a wrong explicit codec, not only a missing keyword."""

    package = tmp_path / "loopx"
    package.mkdir()
    (package / "sample.py").write_text(
        "import subprocess\n"
        "\n"
        "\n"
        "def reads() -> None:\n"
        "    subprocess.run(['a'], text=True)\n"
        "    subprocess.run(['b'], text=True, encoding='latin-1')\n"
        "    subprocess.run(['c'], text=True, encoding='utf-8')\n"
        "    subprocess.run(['d'], capture_output=True)\n",
        encoding="utf-8",
    )

    offenders = _text_mode_calls_without_utf8_encoding(package)

    assert [entry.rsplit(":", 1)[-1] for entry in offenders] == ["5", "6"]


def test_goal_mode_cli_probe_survives_gbk_host_locale(monkeypatch) -> None:
    """The goal-mode loopx JSON probe decodes UTF-8 payload on a cp936 host."""

    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    payload = '{"goal_id": "gbk-probe", "title": "这条issue什么都不用改"}'
    script = (
        "import sys; sys.stdout.buffer.write("
        + repr(payload.encode("utf-8"))
        + ")"
    )
    monkeypatch.setattr(
        goalmode_cmd,
        "gh_prefix",
        lambda: [sys.executable, "-c", script],
    )

    result = goalmode_cmd.gh(["--format", "json", "quota", "should-run"])

    assert result.returncode == 0
    assert json.loads(result.stdout)["title"] == "这条issue什么都不用改"


def test_change_quality_scope_git_helper_pins_codec_only_in_text_mode(
    tmp_path: Path,
) -> None:
    """Bytes-mode `_git` callers must stay bytes while text callers get UTF-8."""

    env_repo = tmp_path / "repo"
    env_repo.mkdir()
    (env_repo / "中文笔记.txt").write_bytes("内容\n".encode("utf-8"))
    for args in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init"],
    ):
        subprocess.run(args, cwd=env_repo, check=True, capture_output=True)

    listed = scope._git(
        env_repo, "-c", "core.quotepath=false", "ls-files", text=True
    )
    assert listed.returncode == 0
    assert "中文笔记.txt" in listed.stdout

    raw = scope._git(env_repo, "-c", "core.quotepath=false", "ls-files")
    assert raw.returncode == 0
    assert isinstance(raw.stdout, bytes)
    assert "中文笔记.txt".encode("utf-8") in raw.stdout


def test_planning_batch_cli_backend_survives_gbk_host_locale(monkeypatch) -> None:
    """The batch-planning CLI receipt decodes UTF-8 JSON on a cp936 host."""

    from loopx.control_plane.goals.planning_batch import CliPlanningBatchBackend

    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    payload = '{"ok": true, "title": "中文目标"}'
    script = (
        "import sys; sys.stdout.buffer.write("
        + repr(payload.encode("utf-8"))
        + ")"
    )
    backend = CliPlanningBatchBackend(
        cli=[sys.executable, "-c", script],
        cwd=".",
        env={},
        packet_reader=lambda: {},
        project=".",
    )

    receipt = backend._call("status", "read")

    assert receipt["title"] == "中文目标"
