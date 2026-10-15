#!/usr/bin/env python3
"""Smoke-test that the Claude adapter install does not mutate things it doesn't own.

Review P1:
  1. `provision_mcp_python` must NOT fall back to `pip install
     --break-system-packages mcp` against the active interpreter by default; that
     is gated behind `allow_system_pip=True`.
  2. `install_mcp` must NOT remove a legacy `goal-harness` MCP entry by default;
     that is gated behind `migrate_goal_harness=True`. It still replaces our own
     `loopx` entry.
  3. The `--harden` refusal must narrate the install that really happened: the
     /loopx command is written, and the MCP state is the registration receipt,
     never a claim that a missing `claude` CLI or a failed `claude mcp add`
     installed a server.

Loads install.py as a module and records subprocess calls (no real venv/pip/claude
runs, no filesystem side effects outside a temp dir).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
INSTALLER = REPO_ROOT / "loopx" / "claude_goal_mode" / "scripts" / "install.py"
CONNECTOR = REPO_ROOT / "loopx" / "claude_goal_mode" / "scripts" / "connect.py"


def load_install():
    spec = importlib.util.spec_from_file_location("loopx_claude_installpy", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_connect():
    spec = importlib.util.spec_from_file_location("loopx_claude_connectpy", CONNECTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def flat(cmd):
    return " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)


def recorder(get_goal_harness_rc=0):
    calls = []
    working_dirs = []

    def run(cmd, *a, **k):
        calls.append(flat(cmd))
        working_dirs.append(k.get("cwd"))
        rc = 0
        f = flat(cmd)
        if "-m venv" in f:                          # force the dedicated venv to "fail"
            rc = 1
        if "mcp get goal-harness" in f:             # simulate a legacy entry existing
            rc = get_goal_harness_rc
        return SimpleNamespace(returncode=rc, stdout="", stderr="")

    return calls, working_dirs, run


def test_no_break_system_packages_by_default():
    inst = load_install()
    with tempfile.TemporaryDirectory(prefix="loopx-prov-") as d:
        inst.MCP_VENV = Path(d) / "mcp-venv"
        inst._has_mcp = lambda py: False            # nothing importable -> reach the fallback
        inst._python_cmd = lambda: "python3"
        # default: allow_system_pip=False
        calls, _, run = recorder()
        inst.subprocess = SimpleNamespace(run=run)
        inst.provision_mcp_python(dry=False, allow_system_pip=False)
        assert not any("--break-system-packages" in c for c in calls), \
            f"must NOT touch system Python by default:\n{calls}"
        # opt-in: allow_system_pip=True DOES use it
        calls, _, run = recorder()
        inst.subprocess = SimpleNamespace(run=run)
        inst.provision_mcp_python(dry=False, allow_system_pip=True)
        assert any("--break-system-packages" in c for c in calls), \
            f"--allow-system-pip should use break-system-packages:\n{calls}"


def test_does_not_remove_goal_harness_by_default():
    inst = load_install()
    inst.shutil = SimpleNamespace(which=lambda x: "/usr/bin/claude")
    # default: migrate_goal_harness=False
    calls, _, run = recorder(get_goal_harness_rc=0)  # legacy goal-harness "exists"
    inst.subprocess = SimpleNamespace(run=run)
    inst.install_mcp(dry=False, py="python3", scope="user", migrate_goal_harness=False)
    removed_gh = [c for c in calls if "mcp remove" in c and "goal-harness" in c]
    assert not removed_gh, f"must NOT remove goal-harness by default:\n{calls}"
    assert any("mcp remove" in c and "loopx" in c for c in calls), \
        f"should still replace our own loopx entry:\n{calls}"
    # opt-in: migrate_goal_harness=True removes it
    calls, _, run = recorder(get_goal_harness_rc=0)
    inst.subprocess = SimpleNamespace(run=run)
    inst.install_mcp(dry=False, py="python3", scope="user", migrate_goal_harness=True)
    assert any("mcp remove" in c and "goal-harness" in c for c in calls), \
        f"--migrate-goal-harness should remove goal-harness:\n{calls}"


def test_project_scope_uses_project_working_directory():
    inst = load_install()
    inst.shutil = SimpleNamespace(which=lambda x: "/usr/bin/claude")
    calls, working_dirs, run = recorder(get_goal_harness_rc=1)
    inst.subprocess = SimpleNamespace(run=run)
    with tempfile.TemporaryDirectory(prefix="loopx-project-") as d:
        dir_project = Path(d) / "target"
        dir_project.mkdir()
        inst.install_mcp(
            dry=False,
            py="python3",
            scope="project",
            project_dir=dir_project,
        )
        calls_mcp = [
            (call, cwd)
            for call, cwd in zip(calls, working_dirs)
            if "/usr/bin/claude mcp" in call
        ]
        assert len(calls_mcp) == 3, f"expected remove/get/add calls:\n{calls_mcp}"
        assert all(cwd == str(dir_project) for _, cwd in calls_mcp), \
            f"project MCP calls must use the target project cwd:\n{calls_mcp}"


def test_connect_runs_installer_from_project_working_directory():
    conn = load_connect()
    calls = []

    def run(cmd, *a, **k):
        calls.append((flat(cmd), k.get("cwd")))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    conn.subprocess = SimpleNamespace(run=run)
    argv_original = conn.sys.argv
    with tempfile.TemporaryDirectory(prefix="loopx-connect-") as d:
        dir_project = Path(d) / "target"
        dir_project.mkdir()
        conn.sys.argv = [
            "connect.py",
            "--project",
            str(dir_project),
            "--goal-id",
            "goal-fixture",
            "--dry-run",
        ]
        try:
            conn.main()
        finally:
            conn.sys.argv = argv_original
        assert len(calls) == 1, f"expected one installer call:\n{calls}"
        assert Path(calls[0][1]).resolve() == dir_project.resolve(), \
            f"connector must run installer from the target project cwd:\n{calls}"


def run_installer(project_dir, settings_bytes):
    """Run the shipped `--harden` install against one project scope."""
    claude_dir = Path(project_dir) / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings = claude_dir / "settings.json"
    if settings_bytes is not None:
        settings.write_bytes(settings_bytes)
    completed = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--scope",
            "project",
            "--project",
            str(project_dir),
            "--harden",
            "--skip-mcp",
        ],
        capture_output=True,
        text=True,
        # The child writes its own narration with the host console codec, which
        # is not UTF-8 on a cp936 Windows console; without `errors` the reader
        # thread dies and `stdout` comes back as None.
        encoding="utf-8",
        errors="replace",
    )
    return completed, settings


def test_harden_refuses_a_settings_file_it_cannot_read():
    """Review P1: the merge rewrites the whole file, so an unreadable one is
    refused instead of being replaced by the loopx block alone.

    `add_hardening` used to fall back to `cur = {}` on any read failure and then
    write the merged result back, so a settings.json that does not parse - a
    UTF-8 BOM from Notepad, a hand-added `//` comment or trailing comma, a torn
    tail from an interrupted write - lost `permissions` (including `deny`
    rules), `env`, `model`, other hooks and MCP approvals, at exit status 0 and
    with no backup. The file belongs to the user, so the install must fail
    closed and leave it byte-identical.
    """
    torn = b'{"model": "opus", "permissions": {"deny": ["Read(~/.ssh/**)"]}, "hooks"'
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        completed, settings = run_installer(d, torn)
        assert completed.returncode != 0, \
            f"an unreadable settings.json must fail the install:\n{completed.stdout}"
        assert settings.read_bytes() == torn, "settings.json must be left untouched"
        # The refusal is scoped to the hardening step: the rest of the adapter
        # must still be installed, so a refusal is not a failed install.
        assert (Path(d) / ".claude" / "commands" / "loopx.md").exists(), \
            "the /loopx command must still be installed"

    bom = "\ufeff".encode("utf-8") + b'{"model": "opus"}'
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        completed, settings = run_installer(d, bom)
        assert completed.returncode != 0, \
            f"a BOM-prefixed settings.json must fail the install:\n{completed.stdout}"
        assert settings.read_bytes() == bom, "settings.json must be left untouched"


def test_harden_still_merges_a_readable_settings_file():
    original = {
        "permissions": {"deny": ["Read(~/.ssh/**)"]},
        "env": {"ANTHROPIC_LOG": "1"},
        "model": "opus",
    }
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        completed, settings = run_installer(
            d, json.dumps(original).encode("utf-8")
        )
        assert completed.returncode == 0, f"--harden failed:\n{completed.stderr}"
        written = json.loads(settings.read_text(encoding="utf-8"))
        for key, value in original.items():
            assert written[key] == value, f"{key} must survive the merge:\n{written}"
        assert written["statusLine"]["type"] == "command"


TORN_SETTINGS = b'{"model": "opus", "permissions": {"deny": ["Read(~/.ssh/**)"]}, "hooks"'


def run_main(project_dir, settings_bytes, argv_extra=(), claude_path="/usr/bin/claude",
             add_rc=0):
    """Drive install.py's own `main()` for one MCP outcome.

    The real `main` -> `install_mcp` -> refusal path, with only these replaced:
    the venv/pip provisioning (never touched here), the `claude` PATH lookup, and
    the `claude` subprocess. A missing CLI and a failed `claude mcp add` are the
    two real paths that leave the server unregistered, so the refusal message has
    to report them as such.
    """
    inst = load_install()
    claude_dir = Path(project_dir) / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings = claude_dir / "settings.json"
    settings.write_bytes(settings_bytes)
    calls = []

    def fake_run(cmd, *a, **k):
        flat_cmd = flat(cmd)
        calls.append(flat_cmd)
        rc = add_rc if "mcp add" in flat_cmd else 1  # 1: no legacy goal-harness
        return SimpleNamespace(returncode=rc, stdout="", stderr="")

    inst.provision_mcp_python = lambda dry, allow_system_pip=False: sys.executable
    inst.shutil = SimpleNamespace(which=lambda name: claude_path if name == "claude" else None)
    inst.subprocess = SimpleNamespace(run=fake_run)
    out, err = io.StringIO(), io.StringIO()
    argv_original = sys.argv
    sys.argv = [
        "install.py", "--scope", "project", "--project", str(project_dir),
        "--harden", *argv_extra,
    ]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = inst.main()
    finally:
        sys.argv = argv_original
    return rc, settings, calls, out.getvalue(), err.getvalue()


def test_refusal_reports_a_missing_claude_cli_instead_of_claiming_an_install():
    """The refusal must not turn an MCP *attempt* into an MCP *install*.

    `install_mcp` prints the manual `claude mcp add` command and returns when
    `claude` is not on PATH. A refusal message that says the MCP server "was
    installed" would let that user skip the manual registration the message just
    told them to run.
    """
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        rc, settings, calls, out, err = run_main(d, TORN_SETTINGS, claude_path=None)
        assert rc == 2, f"a refused --harden must exit 2:\n{err}"
        assert settings.read_bytes() == TORN_SETTINGS, "settings.json must be untouched"
        assert "add the MCP server manually" in out, \
            f"the manual registration command must still be printed:\n{out}"
        assert "the MCP server was not registered" in err, \
            f"the refusal must report the real MCP state:\n{err}"
        assert "were installed" not in err, \
            f"no MCP success claim without a successful registration:\n{err}"
        assert "the /loopx command was installed" in err, \
            f"the command really was written and may be reported:\n{err}"
        assert (Path(d) / ".claude" / "commands" / "loopx.md").exists()
        assert not calls, f"no claude call is possible without the CLI:\n{calls}"


def test_refusal_reports_a_failed_mcp_registration():
    """`claude mcp add` exiting non-zero leaves the server unregistered too."""
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        rc, settings, calls, out, err = run_main(d, TORN_SETTINGS, add_rc=1)
        assert rc == 2, f"a refused --harden must exit 2:\n{err}"
        assert settings.read_bytes() == TORN_SETTINGS, "settings.json must be untouched"
        assert any("mcp add" in c for c in calls), f"the add must be attempted:\n{calls}"
        assert "mcp add failed; add manually" in out, \
            f"the manual registration command must still be printed:\n{out}"
        assert "the MCP server was not registered" in err, \
            f"the refusal must report the real MCP state:\n{err}"
        assert "were installed" not in err, \
            f"no MCP success claim after a failed registration:\n{err}"


def test_refusal_reports_a_registration_that_did_succeed():
    """The receipt is read both ways: a real registration is reported as one."""
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        rc, settings, calls, _out, err = run_main(d, TORN_SETTINGS, add_rc=0)
        assert rc == 2, f"a refused --harden must exit 2:\n{err}"
        assert settings.read_bytes() == TORN_SETTINGS, "settings.json must be untouched"
        assert any("mcp add" in c for c in calls), f"the add must be attempted:\n{calls}"
        assert "the MCP server was registered" in err, \
            f"a successful registration must be reported as one:\n{err}"


def test_refusal_under_dry_run_claims_nothing_was_written():
    """A dry run writes neither the command nor the settings file."""
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        rc, settings, _calls, _out, err = run_main(
            d, TORN_SETTINGS, argv_extra=("--dry-run",), add_rc=0
        )
        assert rc == 2, f"a refused --harden must exit 2:\n{err}"
        assert settings.read_bytes() == TORN_SETTINGS, "settings.json must be untouched"
        assert "nothing was written (dry-run)" in err, \
            f"the refusal must say nothing was written:\n{err}"
        assert "command was installed" not in err, \
            f"a dry run installs nothing and must claim nothing:\n{err}"
        assert not (Path(d) / ".claude" / "commands" / "loopx.md").exists(), \
            "a dry run must not write the command"


def test_refusal_reports_a_skipped_mcp_step():
    """`--skip-mcp` is neither an install nor a registration failure."""
    with tempfile.TemporaryDirectory(prefix="loopx-harden-") as d:
        rc, settings, calls, _out, err = run_main(
            d, TORN_SETTINGS, argv_extra=("--skip-mcp",)
        )
        assert rc == 2, f"a refused --harden must exit 2:\n{err}"
        assert settings.read_bytes() == TORN_SETTINGS, "settings.json must be untouched"
        assert "the MCP server was skipped (--skip-mcp)" in err, \
            f"a skipped MCP step must be reported as skipped:\n{err}"
        assert "were installed" not in err, \
            f"no MCP success claim for a skipped step:\n{err}"
        assert not calls, f"--skip-mcp must not call claude:\n{calls}"
        assert (Path(d) / ".claude" / "commands" / "loopx.md").exists()


def main() -> int:
    test_no_break_system_packages_by_default()
    test_does_not_remove_goal_harness_by_default()
    test_project_scope_uses_project_working_directory()
    test_connect_runs_installer_from_project_working_directory()
    test_harden_refuses_a_settings_file_it_cannot_read()
    test_harden_still_merges_a_readable_settings_file()
    test_refusal_reports_a_missing_claude_cli_instead_of_claiming_an_install()
    test_refusal_reports_a_failed_mcp_registration()
    test_refusal_reports_a_registration_that_did_succeed()
    test_refusal_under_dry_run_claims_nothing_was_written()
    test_refusal_reports_a_skipped_mcp_step()
    print("claude-install-no-system-mutation-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
