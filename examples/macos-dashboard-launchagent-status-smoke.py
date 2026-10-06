#!/usr/bin/env python3
"""Smoke-test LaunchAgent status output without touching real launchctl."""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHAGENT_SCRIPT = REPO_ROOT / "scripts" / "macos-dashboard-launchagent.sh"


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def run_script(fake_bin: Path, home: Path, args: list[str], *, schema_version: int, write_enabled: bool = False, extra_env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "FAKE_STATUS_CONTRACT_SCHEMA_VERSION": str(schema_version),
        "FAKE_CONTROL_PLANE_WRITE_ENABLED": "true" if write_enabled else "false",
        "LOOPX_STATUS_CONTRACT_MIN_VERSION": "2",
        "CODEX_HOME": "",
        "LOOPX_CHAT_CODEX_HOME": "",
        "LOOPX_CHAT_RUNTIME_ROOT": "",
        "LOOPX_CHAT_IDLE_TIMEOUT_SECONDS": "",
        "LOOPX_CHAT_HARD_TIMEOUT_SECONDS": "",
        **(extra_env or {}),
    }
    return subprocess.run(
        [str(LAUNCHAGENT_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def run_status(fake_bin: Path, home: Path, *, schema_version: int, write_enabled: bool = False) -> str:
    return run_script(fake_bin, home, ["status"], schema_version=schema_version, write_enabled=write_enabled).stdout


def check_real_status_deadline(fake_bin: Path, home: Path) -> None:
    """Exercise the public helper with real curl, not a mocked deadline."""
    real_curl = shutil.which("curl")
    assert real_curl, "curl is required for the status HTTP regression"
    response = {"delay": 6, "version": 2, "code": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/status.json", self.path
            time.sleep(response["delay"])
            payload = json.dumps({
                "status_contract": {"schema_version": response["version"], "producer": "loopx status"},
                "local_dashboard_api": {"control_plane_write_enabled": False},
            }).encode()
            try:
                self.send_response(response["code"])
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The bounded client deliberately abandons the hung feed.

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    mocked_curl = fake_bin / "curl.mocked"
    (fake_bin / "curl").rename(mocked_curl)
    (fake_bin / "curl").symlink_to(real_curl)
    try:
        def read_status() -> str:
            return run_script(fake_bin, home, ["status"], schema_version=2,
                              extra_env={"LOOPX_STATUS_PORT": str(server.server_port),
                                         "LOOPX_DASHBOARD_HOST": "127.0.0.1"}).stdout

        delayed = read_status()
        assert "status_contract: schema_version=2 producer=loopx status" in delayed, delayed
        assert "control_plane_write_api: disabled" in delayed, delayed
        response.update(delay=0, version=1)
        assert "warning: status feed is using an old contract" in read_status()
        response.update(version=2, code=503)
        assert "status_contract: unavailable" in read_status()
        response.update(delay=40, code=200)
        started = time.monotonic()
        assert "status_contract: unavailable" in read_status()
        assert time.monotonic() - started < 25, "a hung feed must not make status wait indefinitely"
    finally:
        (fake_bin / "curl").unlink()
        mocked_curl.rename(fake_bin / "curl")
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def log_rotation_prelude(plist: Path) -> str:
    """The rotation step the agent wrapper runs before it execs the service."""
    command = plistlib.loads(plist.read_bytes())["ProgramArguments"][2]
    prelude, separator, _ = command.partition(" export LOOPX_PYTHON=")
    assert separator, command
    return prelude


def run_rotation_prelude(fake_bin: Path, prelude: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["zsh", "-c", prelude],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        check=True,
        capture_output=True,
        text=True,
    )


def check_log_rotation(fake_bin: Path, home: Path, plist: Path, basename: str, limit: int) -> None:
    logs_dir = home / "Library" / "Logs" / "loopx"
    prelude = log_rotation_prelude(plist)
    for stream in ("out", "err"):
        assert str(logs_dir / f"{basename}.{stream}.log") in prelude, prelude

    # launchd opens StandardOutPath before the wrapper runs and keeps appending
    # to that descriptor. Rotation must therefore truncate the live file rather
    # than rename it, or the service's output follows the rotated copy and the
    # live log stays empty until the next restart. Reproduce that descriptor.
    live = logs_dir / f"{basename}.out.log"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(b"O" * (limit + 1))
    descriptor = os.open(live, os.O_WRONLY | os.O_APPEND)
    try:
        run_rotation_prelude(fake_bin, prelude)
        os.write(descriptor, b"after-rotation\n")
    finally:
        os.close(descriptor)
    assert live.exists(), (
        "rotation must truncate the live log in place: launchd's descriptor "
        "follows a rename, which would strand the service's output in the "
        "rotated copy and leave this path missing"
    )
    assert live.read_bytes() == b"after-rotation\n", live.read_bytes()[:80]
    assert live.with_suffix(".log.1").read_bytes() == b"O" * (limit + 1)

    # A log under the limit keeps its history; rotation is retention, not a
    # reset on every service start.
    small = logs_dir / f"{basename}.err.log"
    small.write_bytes(b"kept")
    run_rotation_prelude(fake_bin, prelude)
    assert not small.with_suffix(".log.1").exists()
    assert small.read_bytes() == b"kept"


def check_retention_keeps_the_log_when_the_backup_fails(fake_bin: Path, home: Path, plist: Path, basename: str, limit: int) -> None:
    """A failed backup must leave the live log alone.

    Truncating on a failed copy would destroy the only record of the failure
    the operator is trying to diagnose, so retention has to stand down and say
    so instead.
    """
    logs_dir = home / "Library" / "Logs" / "loopx"
    prelude = log_rotation_prelude(plist)
    live = logs_dir / f"{basename}.out.log"
    live.parent.mkdir(parents=True, exist_ok=True)
    original = b"E" * (limit + 1)
    backup = live.with_suffix(".log.1")

    def run_prelude() -> subprocess.CompletedProcess[str]:
        result = run_rotation_prelude(fake_bin, prelude)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert live.read_bytes() == original, "a failed backup must not truncate the live log"
        assert not backup.is_file(), "a failed backup must not leave a partial generation"
        assert "skipped retention" in result.stderr, result.stderr
        return result

    # The previous generation is not replaceable: a directory at the backup path
    # is a real copy failure, and cp would otherwise copy *into* it.
    live.write_bytes(original)
    if backup.is_file():
        backup.unlink()
    backup.mkdir()
    try:
        run_prelude()
    finally:
        shutil.rmtree(backup, ignore_errors=True)

    # And a directory that cannot accept a new file fails the same way.
    if os.geteuid() != 0:
        backup.unlink(missing_ok=True)
        live.write_bytes(original)
        logs_dir.chmod(0o500)
        try:
            run_prelude()
        finally:
            logs_dir.chmod(0o755)


def check_installed_retention_readback(
    fake_bin: Path, home: Path, plist: Path, basename: str, limit: int
) -> None:
    """Status reports the installed policy, not the caller's environment."""
    command = plistlib.loads(plist.read_bytes())["ProgramArguments"][2]
    assert f"-gt {limit}" in command, command
    installed = run_script(fake_bin, home, ["status"], schema_version=2).stdout
    assert f"- retention: rotated to .1 at each agent start once a log exceeds {limit} bytes" in installed, installed
    assert "not in effect" not in installed, installed
    overridden = run_script(
        fake_bin, home, ["status"], schema_version=2, extra_env={"LOOPX_LOG_MAX_BYTES": "4096"}
    ).stdout
    assert f"once a log exceeds {limit} bytes" in overridden, overridden
    assert "LOOPX_LOG_MAX_BYTES=4096 is not in effect" in overridden, overridden


def check_invalid_retention_is_rejected(fake_bin: Path, home: Path, plist: Path, limit: int) -> None:
    """An unusable threshold fails before a wrapper is written."""
    rejected = run_script(
        fake_bin, home, ["install"], schema_version=2,
        extra_env={"LOOPX_LOG_MAX_BYTES": "invalid"}, check=False,
    )
    assert rejected.returncode != 0, rejected.stdout
    assert "LOOPX_LOG_MAX_BYTES must be a positive byte count, got: invalid" in rejected.stderr, rejected.stderr
    # The rejected install left the previously installed wrapper in place.
    command = plistlib.loads(plist.read_bytes())["ProgramArguments"][2]
    assert f"-gt {limit}" in command, command


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-launchagent-status-smoke-") as raw_tmp:
        tmp = Path(raw_tmp)
        fake_bin = tmp / "bin"
        home = tmp / "home"
        fake_bin.mkdir()
        home.mkdir()

        # This fixture models macOS commands on Linux CI. Keep the real BSD
        # stat on macOS; emulate only its byte-count operation elsewhere.
        if sys.platform != "darwin":
            write_executable(
                fake_bin / "stat",
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if len(sys.argv) != 3 or sys.argv[1] != '-f%z':\n"
                "    raise SystemExit(2)\n"
                "print(os.stat(sys.argv[2]).st_size)\n",
            )

        write_executable(
            fake_bin / "uname",
            "#!/usr/bin/env bash\nprintf 'Darwin\\n'\n",
        )
        write_executable(
            fake_bin / "launchctl",
            "#!/usr/bin/env bash\n"
            "if [[ \"$1\" == \"print\" ]]; then\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$1\" == \"bootout\" || \"$1\" == \"bootstrap\" || \"$1\" == \"kickstart\" ]]; then\n"
            "  exit 0\n"
            "fi\n"
            "echo \"unexpected launchctl args: $*\" >&2\n"
            "exit 2\n",
        )
        write_executable(
            fake_bin / "loopx",
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *\"--format json doctor\"* ]]; then\n"
            "  if [[ -n \"${FAKE_RUNTIME_IDENTITY:-}\" ]]; then\n"
            "    printf '{\"service_runtime_identity\":%s}\\n' \"$FAKE_RUNTIME_IDENTITY\"\n"
            "    exit 0\n"
            "  fi\n"
            "  printf '%s\\n' '{\"release_manifest\":{\"manifest\":{\"release_id\":\"current-release\",\"package\":{\"version\":\"0.5.3\"},\"source\":{\"git_commit\":\"current-revision\"}}}}'\n"
            "  exit 0\n"
            "fi\n"
            "echo loopx \"$@\"\n",
        )
        write_executable(
            fake_bin / "loopx-canary",
            "#!/usr/bin/env bash\n"
            "echo loopx-canary \"$@\"\n",
        )
        write_executable(
            fake_bin / "curl",
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *\"/api/chat/capabilities\"* ]]; then\n"
            "  if [[ -n \"${FAKE_RUNTIME_IDENTITY:-}\" ]]; then\n"
            "    printf '{\"ok\":true,\"schema_version\":\"loopx_chat_capabilities_v1\",\"runtime_identity\":%s}\\n' \"$FAKE_RUNTIME_IDENTITY\"\n"
            "    exit 0\n"
            "  fi\n"
            "  printf '%s\\n' '{\"ok\":true,\"schema_version\":\"loopx_chat_capabilities_v1\",\"runtime_identity\":{\"schema_version\":\"loopx_runtime_identity_v1\",\"package_version\":\"0.5.3\",\"release_id\":\"current-release\",\"source_revision\":\"current-revision\"}}'\n"
            "  exit 0\n"
            "fi\n"
            "version=\"${FAKE_STATUS_CONTRACT_SCHEMA_VERSION:-0}\"\n"
            "write_enabled=\"${FAKE_CONTROL_PLANE_WRITE_ENABLED:-false}\"\n"
            "cat <<EOF\n"
            "{\"ok\":true,\"status_contract\":{\"schema_version\":${version},\"producer\":\"loopx status\"},\"local_dashboard_api\":{\"control_plane_write_enabled\":${write_enabled}}}\n"
            "EOF\n",
        )

        old_output = run_status(fake_bin, home, schema_version=1)
        assert "- com.loopx.status: loaded" in old_output, old_output
        assert "- com.loopx.chat: loaded" in old_output, old_output
        assert "- com.loopx.dashboard" not in old_output, old_output
        assert "- status_contract: schema_version=1 producer=loopx status expected>=2" in old_output, old_output
        assert "- control_plane_write_api: disabled" in old_output, old_output
        assert "warning: status feed is using an old contract; run:" in old_output, old_output
        assert "macos-dashboard-launchagent.sh restart" in old_output, old_output

        current_output = run_status(fake_bin, home, schema_version=2, write_enabled=True)
        assert "- status_contract: schema_version=2 producer=loopx status expected>=2" in current_output, current_output
        assert "- control_plane_write_api: enabled" in current_output, current_output
        assert "warning: control-plane registry writes are enabled" in current_output, current_output
        assert "warning: status feed is using an old contract" not in current_output, current_output
        assert "LaunchAgents:" in current_output, current_output
        assert "URLs:" in current_output, current_output
        assert "Logs:" in current_output, current_output

        check_real_status_deadline(fake_bin, home)

        run_script(fake_bin, home, ["install"], schema_version=2)
        status_plist = home / "Library" / "LaunchAgents" / "com.loopx.status.plist"
        chat_plist = home / "Library" / "LaunchAgents" / "com.loopx.chat.plist"
        default_plist = status_plist.read_text(encoding="utf-8")
        default_chat_plist = chat_plist.read_text(encoding="utf-8")
        assert "--enable-control-plane-write-api" not in default_plist, default_plist
        assert " chat --host " in default_chat_plist, default_chat_plist
        assert "--global-registry" not in default_chat_plist, default_chat_plist
        assert "--port 8767" in default_chat_plist, default_chat_plist
        assert "--replace-existing-loopx-chat" in default_chat_plist, default_chat_plist
        assert "--no-open" in default_chat_plist, default_chat_plist
        assert "--runtime-root" not in shlex.split(plistlib.loads(chat_plist.read_bytes())["ProgramArguments"][2])
        assert f"export CODEX_HOME={(home / '.codex').resolve()};" in default_chat_plist, default_chat_plist
        assert "export LOOPX_PYTHON=" in default_plist, default_plist
        assert "export LOOPX_PYTHON=" in default_chat_plist, default_chat_plist
        assert "/loopx --registry" in default_plist, default_plist
        assert "/loopx-canary" not in default_plist, default_plist
        assert not (home / "Library" / "LaunchAgents" / "com.loopx.dashboard.plist").exists(), "retired dashboard LaunchAgent should not be installed"

        # KeepAlive restarts never re-enter this installer, so each agent
        # carries its own retention step for both of its streams.
        rotation_limit = 1024
        run_script(fake_bin, home, ["install"], schema_version=2,
                   extra_env={"LOOPX_LOG_MAX_BYTES": str(rotation_limit)})
        for plist, basename in ((status_plist, "status"), (chat_plist, "chat")):
            check_log_rotation(fake_bin, home, plist, basename, rotation_limit)
            check_retention_keeps_the_log_when_the_backup_fails(fake_bin, home, plist, basename, rotation_limit)
            check_installed_retention_readback(fake_bin, home, plist, basename, rotation_limit)
        check_invalid_retention_is_rejected(fake_bin, home, status_plist, rotation_limit)
        assert f"- retention: rotated to .1 at each agent start once a log exceeds {rotation_limit} bytes" in run_script(
            fake_bin, home, ["status"], schema_version=2,
        ).stdout

        run_script(
            fake_bin,
            home,
            ["--enable-control-plane-write-api", "restart"],
            schema_version=2,
            extra_env={"LOOPX_CHAT_CODEX_HOME": str(home / "selected-codex-home")},
        )
        write_plist = status_plist.read_text(encoding="utf-8")
        selected_chat_plist = chat_plist.read_text(encoding="utf-8")
        assert "--enable-control-plane-write-api" in write_plist, write_plist
        selected = (home / 'selected-codex-home').resolve()
        assert f"export LOOPX_CHAT_CODEX_HOME={selected};" in selected_chat_plist, selected_chat_plist
        assert f"export CODEX_HOME={(home / '.codex').resolve()};" in selected_chat_plist, selected_chat_plist
        run_script(fake_bin, home, ["install"], schema_version=2,
                   extra_env={"CODEX_HOME": str(home / "unrelated-upgrader")})
        assert plistlib.loads(chat_plist.read_bytes())["EnvironmentVariables"]["LOOPX_CHAT_CODEX_HOME"] == str(selected)

        # Two independently selected workspaces and a custom registry survive
        # reinstall. Shell metacharacters in a directory are literal arguments.
        workspaces = [(home / "workspace one").resolve(), (home / "workspace $(touch sentinel) & two").resolve()]
        for workspace in workspaces:
            workspace.mkdir()
        custom_registry = (home / "isolated" / "registry.json").resolve()
        run_script(fake_bin, home, ["install"], schema_version=2, extra_env={
            "LOOPX_CHAT_SCAN_PATHS_JSON": json.dumps([str(p) for p in workspaces]),
            "LOOPX_GLOBAL_REGISTRY": str(custom_registry),
        })
        run_script(fake_bin, home, ["restart"], schema_version=2)
        context_plist = plistlib.loads(chat_plist.read_bytes())
        assert json.loads(context_plist["EnvironmentVariables"]["LOOPX_CHAT_SCAN_PATHS_JSON"]) == [str(p) for p in workspaces]
        command = shlex.split(context_plist["ProgramArguments"][2])
        assert [command[i + 1] for i, word in enumerate(command[:-1]) if word == "--scan-path"] == [str(p) for p in workspaces]
        assert command[command.index("--registry") + 1] == str(custom_registry)
        assert "--global-registry" not in command
        assert str(custom_registry) in status_plist.read_text()
        status_command = shlex.split(plistlib.loads(status_plist.read_bytes())["ProgramArguments"][2])
        assert [status_command[i + 1] for i, word in enumerate(status_command[:-1]) if word == "--scan-path"] == [str(p) for p in workspaces]
        before = chat_plist.read_bytes()
        rejected = run_script(fake_bin, home, ["install"], schema_version=2,
                              extra_env={"LOOPX_CHAT_SCAN_PATHS_JSON": '["relative"]'}, check=False)
        assert rejected.returncode != 0
        assert chat_plist.read_bytes() == before

        wheel_identity = {"schema_version": "loopx_runtime_identity_v1", "package_version": "1.2.4",
                          "release_id": None, "source_revision": None, "package_fingerprint": "sha256:" + "a" * 64}
        run_script(fake_bin, home, ["restart"], schema_version=2,
                   extra_env={"FAKE_RUNTIME_IDENTITY": json.dumps(wheel_identity)})
        before = chat_plist.read_bytes()
        del wheel_identity["package_fingerprint"]
        rejected = run_script(fake_bin, home, ["install"], schema_version=2,
                              extra_env={"FAKE_RUNTIME_IDENTITY": json.dumps(wheel_identity)}, check=False)
        assert rejected.returncode != 0
        assert chat_plist.read_bytes() == before

        # A coordinator can resume workers from another existing Codex home.
        # The Chat override must not overwrite that execution profile.
        execution_home = (home / "worker home").resolve()
        run_script(fake_bin, home, ["install"], schema_version=2,
                   extra_env={"CODEX_HOME": str(execution_home),
                              "LOOPX_CHAT_CODEX_HOME": str(selected)})
        installed = plistlib.loads(chat_plist.read_bytes())
        assert installed["EnvironmentVariables"]["CODEX_HOME"] == str(execution_home)
        assert installed["EnvironmentVariables"]["LOOPX_CHAT_CODEX_HOME"] == str(selected)
        run_script(fake_bin, home, ["restart"], schema_version=2)
        preserved = plistlib.loads(chat_plist.read_bytes())
        assert preserved["EnvironmentVariables"] == installed["EnvironmentVariables"]
        # Execute the generated wrapper with a bounded fixture entrypoint.
        # It must transport both settings, including spaces, to the same child.
        fake_loopx = fake_bin / "loopx"
        original_entry = fake_loopx.read_bytes()
        write_executable(fake_loopx, f"#!{sys.executable}\nimport json, os, sys\n"
                         "print(json.dumps({'homes':{k:os.environ[k] for k in "
                         "['CODEX_HOME','LOOPX_CHAT_CODEX_HOME']},'argv':sys.argv[1:]}))\n")
        try:
            launched = subprocess.run(preserved["ProgramArguments"],
                                      capture_output=True, text=True, check=True)
            transported = json.loads(launched.stdout)
            assert transported["homes"] == {key: preserved["EnvironmentVariables"][key]
                                            for key in ("CODEX_HOME", "LOOPX_CHAT_CODEX_HOME")}
            argv = transported["argv"]
            assert argv[argv.index("--registry") + 1] == str(custom_registry)
            assert [argv[i + 1] for i, word in enumerate(argv[:-1]) if word == "--scan-path"] == [str(p) for p in workspaces]
            assert "--global-registry" not in argv
            assert not (root_sentinel := REPO_ROOT / "sentinel").exists(), root_sentinel
        finally:
            fake_loopx.write_bytes(original_entry)
        before_invalid_home = chat_plist.read_bytes()
        rejected = run_script(fake_bin, home, ["install"], schema_version=2,
                              extra_env={"CODEX_HOME": "relative-worker-home"}, check=False)
        assert rejected.returncode != 0 and "CODEX_HOME must be absolute" in rejected.stderr
        assert chat_plist.read_bytes() == before_invalid_home

        # Login/restart must reopen the selected Session store and retain its
        # execution timeouts, rather than silently using another root/defaults.
        runtime_root = (home / "chat state $(touch sentinel) & existing").resolve()
        runtime_root.mkdir()
        native_state = runtime_root / "retained-session.json"
        native_state.write_text('{"session":"retained"}')
        run_script(fake_bin, home, ["install"], schema_version=2, extra_env={
            "LOOPX_CHAT_RUNTIME_ROOT": str(runtime_root),
            "LOOPX_CHAT_IDLE_TIMEOUT_SECONDS": "600",
            "LOOPX_CHAT_HARD_TIMEOUT_SECONDS": "1800",
        })
        run_script(fake_bin, home, ["restart"], schema_version=2)
        runtime_plist = plistlib.loads(chat_plist.read_bytes())
        runtime_command = shlex.split(runtime_plist["ProgramArguments"][2])
        assert runtime_command[runtime_command.index("--runtime-root") + 1] == str(runtime_root)
        assert float(runtime_command[runtime_command.index("--idle-timeout-seconds") + 1]) == 600
        assert float(runtime_command[runtime_command.index("--hard-timeout-seconds") + 1]) == 1800
        assert "--runtime-root" not in shlex.split(plistlib.loads(status_plist.read_bytes())["ProgramArguments"][2])
        assert native_state.read_text() == '{"session":"retained"}'
        assert not (REPO_ROOT / "sentinel").exists()
        original_entry = fake_loopx.read_bytes()
        write_executable(fake_loopx, f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
                         "args=sys.argv[1:]; root=Path(args[args.index('--runtime-root')+1])\n"
                         "print(json.dumps({'argv':args,'state':json.loads((root/'retained-session.json').read_text())}))\n")
        try:
            actual = subprocess.run(runtime_plist["ProgramArguments"],
                                    capture_output=True, text=True, check=True)
            readback = json.loads(actual.stdout)
            assert readback["state"] == {"session": "retained"}
            assert readback["argv"][readback["argv"].index("--runtime-root") + 1] == str(runtime_root)
            assert not (REPO_ROOT / "sentinel").exists()
        finally:
            fake_loopx.write_bytes(original_entry)
        both_before = [p.read_bytes() for p in (chat_plist, status_plist)]
        for key, value in (("LOOPX_CHAT_RUNTIME_ROOT", "relative"),
                           ("LOOPX_CHAT_RUNTIME_ROOT", "bad\npath"),
                           ("LOOPX_CHAT_IDLE_TIMEOUT_SECONDS", "nan"),
                           ("LOOPX_CHAT_HARD_TIMEOUT_SECONDS", "-1")):
            rejected = run_script(fake_bin, home, ["install"], schema_version=2,
                                  extra_env={key: value}, check=False)
            assert rejected.returncode != 0 and key in rejected.stderr
            assert [p.read_bytes() for p in (chat_plist, status_plist)] == both_before

        # An older explicit argv binding is decoded without executing it.
        legacy_runtime = plistlib.loads(chat_plist.read_bytes())
        legacy_runtime.pop("EnvironmentVariables")
        legacy_runtime["ProgramArguments"] = ["/bin/zsh", "-c",
            f"exec loopx --registry {shlex.quote(str(custom_registry))} "
            f"--runtime-root {shlex.quote(str(runtime_root))} chat "
            "--idle-timeout-seconds 600 --hard-timeout-seconds 1800"]
        chat_plist.write_bytes(plistlib.dumps(legacy_runtime))
        run_script(fake_bin, home, ["install"], schema_version=2)
        saved = plistlib.loads(chat_plist.read_bytes())["EnvironmentVariables"]
        assert saved["LOOPX_CHAT_RUNTIME_ROOT"] == str(runtime_root)
        assert float(saved["LOOPX_CHAT_IDLE_TIMEOUT_SECONDS"]) == 600
        assert float(saved["LOOPX_CHAT_HARD_TIMEOUT_SECONDS"]) == 1800
        changed_root = (home / "explicit replacement").resolve()
        run_script(fake_bin, home, ["install"], schema_version=2,
                   extra_env={"LOOPX_CHAT_RUNTIME_ROOT": str(changed_root),
                              "LOOPX_CHAT_IDLE_TIMEOUT_SECONDS": "700"})
        changed = plistlib.loads(chat_plist.read_bytes())["EnvironmentVariables"]
        assert changed["LOOPX_CHAT_RUNTIME_ROOT"] == str(changed_root)
        assert float(changed["LOOPX_CHAT_IDLE_TIMEOUT_SECONDS"]) == 700
        assert float(changed["LOOPX_CHAT_HARD_TIMEOUT_SECONDS"]) == 1800
        assert not changed_root.exists(), "selection must not create or migrate a Session store"
        assert native_state.read_text() == '{"session":"retained"}'

        # Legacy generated plists used only a shell export. Preserve quoted
        # paths across upgrades without ever executing their command contents.
        legacy = plistlib.loads(chat_plist.read_bytes())
        legacy.pop("EnvironmentVariables")
        legacy_home = (home / "legacy home").resolve()
        legacy["ProgramArguments"] = ["/bin/zsh", "-c", f"export CODEX_HOME='{legacy_home}'; exec loopx chat"]
        chat_plist.write_bytes(plistlib.dumps(legacy))
        run_script(fake_bin, home, ["install"], schema_version=2,
                   extra_env={"CODEX_HOME": str(home / "unrelated-upgrader")})
        assert plistlib.loads(chat_plist.read_bytes())["EnvironmentVariables"]["LOOPX_CHAT_CODEX_HOME"] == str(legacy_home)

        chat_plist.write_bytes(b"invalid plist")
        try:
            run_script(fake_bin, home, ["install"], schema_version=2)
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError("malformed existing binding must fail closed")
        assert chat_plist.read_bytes() == b"invalid plist"

    print("macos-dashboard-launchagent-status-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
