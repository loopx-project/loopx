from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from loopx.cli import build_parser, main
from loopx.cli_commands import support_control
from loopx.release_manifest import release_runtime_identity


class _CapabilitiesHandler(BaseHTTPRequestHandler):
    capabilities: dict[str, object] = {
        "ok": True,
        "schema_version": "loopx_chat_capabilities_v1",
        "runtime_identity": release_runtime_identity(),
    }

    def do_GET(self) -> None:
        body = json.dumps(self.capabilities).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def _serve_capabilities(handler_cls: type[_CapabilitiesHandler] = _CapabilitiesHandler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _prepare_dashboard_runtime_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    release_root = tmp_path / "release"
    scripts_dir = release_root / "scripts"
    dashboard_dir = release_root / "apps" / "presentation" / "dashboard"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    (dashboard_dir / "node_modules" / ".bin").mkdir(parents=True)
    (dashboard_dir / "node_modules" / ".bin" / "vite").touch()
    fake_bin.mkdir()
    _copy_dashboard_launcher(scripts_dir)
    # This fixture isolates service error propagation; bundle integrity has its own suite.
    (scripts_dir / "chat_bundle.py").write_text("# frontend already qualified in fixture\n")
    for executable in (fake_bin / "node", fake_bin / "npm"):
        executable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    return release_root, scripts_dir / "dashboard-dev.sh", fake_bin


def _write_logging_python_stub(path: Path, *, label: str) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "{label}:%s\\n" "$*" >> "$LOOPX_COMMAND_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _copy_dashboard_launcher(scripts_dir: Path) -> None:
    source_scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("dashboard-dev.sh", "loopx-python.sh"):
        shutil.copy2(source_scripts / name, scripts_dir / name)


def test_release_runtime_identity_uses_immutable_manifest_fields(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    release_root.mkdir()
    (release_root / "release.json").write_text(
        json.dumps(
            {
                "release_id": "20260821T164921Z",
                "package": {"version": "0.5.1"},
                "source": {"git_commit": "62647e2e299d"},
            }
        ),
        encoding="utf-8",
    )

    assert release_runtime_identity(release_root) == {
        "schema_version": "loopx_runtime_identity_v1",
        "package_version": "0.5.1",
        "release_id": "20260821T164921Z",
        "source_revision": "62647e2e299d",
    }


def test_dashboard_command_is_registered() -> None:
    try:
        args = build_parser().parse_args(["dashboard"])
    except SystemExit:
        pytest.fail("`loopx dashboard` must be a registered top-level command")

    assert args.command == "dashboard"


def test_chat_command_accepts_explicit_lark_cli_binary() -> None:
    args = build_parser().parse_args(["chat", "--lark-cli-bin", "custom-lark-cli"])

    assert args.lark_cli_bin == "custom-lark-cli"


def test_chat_command_can_request_managed_existing_service_replacement() -> None:
    args = build_parser().parse_args(["chat", "--replace-existing-loopx-chat"])

    assert args.replace_existing_loopx_chat is True


def test_chat_command_passes_explicit_lark_cli_binary_to_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(support_control, "serve_chat", lambda **kwargs: calls.append(kwargs))

    assert main(["chat", "--lark-cli-bin", "custom-lark-cli", "--no-open"]) == 0
    assert calls[0]["lark_cli_bin"] == "custom-lark-cli"


def test_chat_goal_subagent_configuration_is_default_off_and_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(support_control, "serve_chat", lambda **kwargs: calls.append(kwargs))

    assert main(["chat", "--no-open"]) == 0
    assert calls[-1]["enable_goal_subagent_configuration"] is False

    assert main(["chat", "--enable-goal-subagent-configuration", "--no-open"]) == 0
    assert calls[-1]["enable_goal_subagent_configuration"] is True


def test_chat_command_replaces_existing_service_before_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        support_control,
        "replace_existing_loopx_chat",
        lambda host, port: calls.append((host, port)),
    )
    monkeypatch.setattr(
        support_control,
        "serve_chat",
        lambda **_kwargs: calls.append("serve"),
    )

    assert main(["chat", "--replace-existing-loopx-chat", "--no-open"]) == 0
    assert calls == [("127.0.0.1", 8767), "serve"]


def test_macos_launchagent_passes_discovered_lark_cli_without_login_shell() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "macos-dashboard-launchagent.sh"
    ).read_text(encoding="utf-8")

    assert "--lark-cli-bin" in script
    assert "resolve_lark_cli_command" in script
    assert "<string>-lc</string>" not in script
    assert script.count("<string>-c</string>") == 2
    assert 'codex_home_export=" export CODEX_HOME=' in script


def test_dashboard_command_runs_the_dashboard_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_launch_dashboard(**kwargs: object) -> int:
        calls.append(kwargs)
        return 23

    monkeypatch.setattr(
        support_control,
        "launch_dashboard",
        fake_launch_dashboard,
        raising=False,
    )

    assert main(["dashboard"]) == 23
    assert len(calls) == 1


def test_dashboard_goal_subagent_configuration_is_default_off_and_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        support_control,
        "launch_dashboard",
        lambda **kwargs: calls.append(kwargs) or 0,
    )

    assert main(["dashboard", "--no-open"]) == 0
    assert calls[-1]["enable_goal_subagent_configuration"] is False

    assert (
        main(
            [
                "dashboard",
                "--enable-goal-subagent-configuration",
                "--no-open",
            ]
        )
        == 0
    )
    assert calls[-1]["enable_goal_subagent_configuration"] is True


def test_launch_dashboard_in_installed_mode_serves_packaged_chat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from loopx.dashboard_launcher import launch_dashboard

    # Point release root to an empty directory without scripts/dashboard-dev.sh (simulating installed wheel site-packages)
    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "loopx.dashboard_launcher._probe_existing_chat",
        lambda _host, _port, **_kwargs: "unavailable",
    )

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "loopx.chat_server.serve_chat",
        lambda **kwargs: calls.append(kwargs),
    )

    # Launch dashboard should invoke serve_chat using packaged web bundle
    result = launch_dashboard(open_browser=False, port=49_123)
    assert result == 0
    assert len(calls) == 1
    assert calls[0]["open_browser"] is False
    assert calls[0]["port"] == 49_123
    assert (Path(str(calls[0]["assets_dir"])) / "index.html").is_file()


def test_probe_existing_chat_accepts_exact_loopx_fingerprint() -> None:
    from loopx.dashboard_launcher import _probe_existing_chat

    server, thread = _serve_capabilities()
    try:
        assert _probe_existing_chat("127.0.0.1", server.server_address[1]) == "matching"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("capability", "requested"),
    [(None, True), ("preview_locked", False)],
)
def test_probe_existing_chat_rejects_goal_subagent_gate_mismatch(
    capability: str | None,
    requested: bool,
) -> None:
    from loopx.dashboard_launcher import _probe_existing_chat

    class _GateCapabilitiesHandler(_CapabilitiesHandler):
        capabilities = {
            **_CapabilitiesHandler.capabilities,
            **(
                {"goal_subagent_configuration": capability}
                if capability is not None
                else {}
            ),
        }

    server, thread = _serve_capabilities(_GateCapabilitiesHandler)
    try:
        assert (
            _probe_existing_chat(
                "127.0.0.1",
                server.server_address[1],
                goal_subagent_configuration_enabled=requested,
            )
            == "configuration_mismatch"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_existing_chat_rejects_unknown_capability_schema() -> None:
    from loopx.dashboard_launcher import _probe_existing_chat

    class _UnknownCapabilitiesHandler(_CapabilitiesHandler):
        capabilities = {"ok": True, "schema_version": "loopx_chat_capabilities_v0"}

    server, thread = _serve_capabilities(_UnknownCapabilitiesHandler)
    try:
        assert _probe_existing_chat("127.0.0.1", server.server_address[1]) == "foreign"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_existing_chat_rejects_stale_runtime_identity() -> None:
    from loopx.dashboard_launcher import _probe_existing_chat

    class _StaleCapabilitiesHandler(_CapabilitiesHandler):
        capabilities = {
            "ok": True,
            "schema_version": "loopx_chat_capabilities_v1",
            "runtime_identity": {
                "schema_version": "loopx_runtime_identity_v1",
                "package_version": "0.5.0",
                "release_id": "old-release",
                "source_revision": "old-revision",
            },
        }

    server, thread = _serve_capabilities(_StaleCapabilitiesHandler)
    try:
        assert _probe_existing_chat("127.0.0.1", server.server_address[1]) == "stale"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_existing_chat_defers_timeout_to_server_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import dashboard_launcher

    class _TimingOutConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def request(self, *_args: object, **_kwargs: object) -> None:
            raise TimeoutError("timed out")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        dashboard_launcher.http.client,
        "HTTPConnection",
        _TimingOutConnection,
    )

    assert dashboard_launcher._probe_existing_chat("127.0.0.1", 8767) == "unavailable"


def test_replace_existing_loopx_chat_stops_one_verified_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import dashboard_launcher

    listener_reads = iter([[4321], []])
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(dashboard_launcher, "_probe_existing_chat", lambda _host, _port, **_kwargs: "stale")
    monkeypatch.setattr(dashboard_launcher, "_listener_pids", lambda _port: next(listener_reads))
    monkeypatch.setattr(dashboard_launcher, "_is_same_user_loopx_chat_process", lambda pid: pid == 4321)
    monkeypatch.setattr(dashboard_launcher.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert dashboard_launcher.replace_existing_loopx_chat("127.0.0.1", 8767) == "stale"
    assert killed == [(4321, signal.SIGTERM)]


def test_replace_existing_loopx_chat_leaves_foreign_listener_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import dashboard_launcher

    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(dashboard_launcher, "_probe_existing_chat", lambda _host, _port, **_kwargs: "foreign")
    monkeypatch.setattr(dashboard_launcher.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(RuntimeError, match="unverified service"):
        dashboard_launcher.replace_existing_loopx_chat("127.0.0.1", 8767)

    assert killed == []


def test_replace_existing_loopx_chat_rejects_non_loopback_before_process_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import dashboard_launcher

    probed: list[tuple[str, int]] = []
    monkeypatch.setattr(
        dashboard_launcher,
        "_probe_existing_chat",
        lambda host, port, **_kwargs: probed.append((host, port)) or "stale",
    )

    with pytest.raises(ValueError, match="explicit loopback"):
        dashboard_launcher.replace_existing_loopx_chat("example.com", 8767)

    assert probed == []


def test_replace_existing_loopx_chat_rejects_unverified_same_port_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx import dashboard_launcher

    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(dashboard_launcher, "_probe_existing_chat", lambda _host, _port, **_kwargs: "stale")
    monkeypatch.setattr(dashboard_launcher, "_listener_pids", lambda _port: [4321])
    monkeypatch.setattr(dashboard_launcher, "_is_same_user_loopx_chat_process", lambda _pid: False)
    monkeypatch.setattr(dashboard_launcher.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    with pytest.raises(RuntimeError, match="same-user LoopX Chat"):
        dashboard_launcher.replace_existing_loopx_chat("127.0.0.1", 8767)

    assert killed == []


def test_launch_dashboard_reuses_existing_matching_chat_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from loopx.dashboard_launcher import launch_dashboard

    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "loopx.dashboard_launcher._probe_existing_chat",
        lambda _host, _port, **_kwargs: "matching",
    )
    opened: list[str] = []
    monkeypatch.setattr("loopx.dashboard_launcher.webbrowser.open", opened.append)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "loopx.chat_server.serve_chat",
        lambda **kwargs: calls.append(kwargs),
    )

    result = launch_dashboard(open_browser=True, goal_id="demo-goal")

    assert result == 0
    assert calls == []
    assert opened == ["http://127.0.0.1:8767/chat/?goalId=demo-goal"]


def test_launch_dashboard_rejects_foreign_chat_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from loopx.dashboard_launcher import launch_dashboard

    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "loopx.dashboard_launcher._probe_existing_chat",
        lambda _host, _port, **_kwargs: "foreign",
    )

    with pytest.raises(RuntimeError, match="not LoopX Chat"):
        launch_dashboard(open_browser=False)


def test_launch_dashboard_rejects_stale_chat_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from loopx.dashboard_launcher import launch_dashboard

    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "loopx.dashboard_launcher._probe_existing_chat",
        lambda _host, _port, **_kwargs: "stale",
    )

    with pytest.raises(RuntimeError, match="different installed runtime"):
        launch_dashboard(open_browser=False)


def test_serve_status_still_returns_success_when_server_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        support_control,
        "serve_status",
        lambda **kwargs: calls.append(kwargs),
    )

    assert main(["serve-status"]) == 0
    assert calls[-1]["enable_goal_subagent_configuration"] is False

    assert main(["serve-status", "--enable-goal-subagent-configuration"]) == 0
    assert calls[-1]["enable_goal_subagent_configuration"] is True


def test_dashboard_launcher_installs_missing_frontend_dependencies(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release"
    scripts_dir = release_root / "scripts"
    dashboard_dir = release_root / "apps" / "presentation" / "dashboard"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    dashboard_dir.mkdir(parents=True)
    fake_bin.mkdir()
    _copy_dashboard_launcher(scripts_dir)

    npm_log = tmp_path / "npm.log"
    python_stub = fake_bin / "python3.13"
    python_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    node_stub = fake_bin / "node"
    node_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    npm_stub = fake_bin / "npm"
    npm_stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$LOOPX_NPM_LOG"\n'
        'if [[ "$1" == "ci" ]]; then\n'
        "  mkdir -p node_modules/.bin\n"
        "  touch node_modules/.bin/vite\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    for executable in (python_stub, node_stub, npm_stub):
        executable.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(scripts_dir / "dashboard-dev.sh")],
        cwd=release_root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "LOOPX_NPM_LOG": str(npm_log),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert npm_log.read_text(encoding="utf-8").splitlines() == [
        "ci",
        "run dev:web",
    ]


@pytest.mark.parametrize(
    "configured_via",
    ["environment", "installer_record", "repository_venv"],
)
def test_dashboard_launcher_honors_configured_python_runtime(
    tmp_path: Path,
    configured_via: str,
) -> None:
    release_root = tmp_path / "release"
    scripts_dir = release_root / "scripts"
    dashboard_dir = release_root / "apps" / "presentation" / "dashboard"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    (dashboard_dir / "node_modules" / ".bin").mkdir(parents=True)
    (dashboard_dir / "node_modules" / ".bin" / "vite").touch()
    fake_bin.mkdir()
    _copy_dashboard_launcher(scripts_dir)

    python_log = tmp_path / "python.log"
    configured_python = (
        release_root / ".venv/bin/python"
        if configured_via == "repository_venv"
        else tmp_path / "configured-python"
    )
    configured_python.parent.mkdir(parents=True, exist_ok=True)
    configured_python.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$LOOPX_PYTHON_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    configured_python.chmod(0o755)
    (fake_bin / "node").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "npm").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for executable in (fake_bin / "node", fake_bin / "npm"):
        executable.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "LOOPX_PYTHON_LOG": str(python_log),
    }
    if configured_via == "environment":
        env["LOOPX_PYTHON"] = str(configured_python)
    elif configured_via == "installer_record":
        (release_root / ".loopx-python").write_text(
            f"{configured_python}\n",
            encoding="utf-8",
        )

    completed = subprocess.run(
        ["bash", str(scripts_dir / "dashboard-dev.sh")],
        cwd=release_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"Using LoopX Python: {configured_python}" in completed.stdout
    commands = python_log.read_text(encoding="utf-8")
    assert "-m loopx.cli serve-status" in commands
    assert "-m loopx.cli chat" in commands


def test_dashboard_launcher_preserves_early_service_error_context(
    tmp_path: Path,
) -> None:
    release_root, dashboard_script, fake_bin = _prepare_dashboard_runtime_fixture(
        tmp_path
    )
    python_wrapper = tmp_path / "python-wrapper"
    python_wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "-m" && "$*" == *"serve-status"* ]]; then\n'
        '  echo "distinct status startup failure" >&2\n'
        "  exit 42\n"
        "fi\n"
        'if [[ "$1" == "-m" && "$*" == *" chat "* ]]; then\n'
        "  while true; do sleep 1; done\n"
        "fi\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)

    started_at = time.monotonic()
    completed = subprocess.run(
        ["bash", str(dashboard_script)],
        cwd=release_root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "LOOPX_PYTHON": str(python_wrapper),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    elapsed = time.monotonic() - started_at

    assert completed.returncode == 1
    assert elapsed < 5
    assert "distinct status startup failure" in completed.stderr
    assert (
        "LoopX status service exited before it became ready (exit status 42)."
        in completed.stderr
    )
    assert "The original service error output is shown above." in completed.stderr


def test_dashboard_launcher_selects_a_compatible_nvm_node(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release"
    scripts_dir = release_root / "scripts"
    dashboard_dir = release_root / "apps" / "presentation" / "dashboard"
    fake_bin = tmp_path / "bin"
    nvm_bin = tmp_path / "nvm" / "versions" / "node" / "v22.22.2" / "bin"
    scripts_dir.mkdir(parents=True)
    dashboard_dir.mkdir(parents=True)
    fake_bin.mkdir()
    nvm_bin.mkdir(parents=True)
    _copy_dashboard_launcher(scripts_dir)

    npm_log = tmp_path / "npm.log"
    (fake_bin / "python3.13").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "node").write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "npm").write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    (nvm_bin / "node").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    (nvm_bin / "npm").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$LOOPX_NPM_LOG"\n'
        'if [[ "$1" == "ci" ]]; then\n'
        "  mkdir -p node_modules/.bin\n"
        "  touch node_modules/.bin/vite\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    for executable in (*fake_bin.iterdir(), *nvm_bin.iterdir()):
        executable.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(scripts_dir / "dashboard-dev.sh")],
        cwd=release_root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "NVM_DIR": str(tmp_path / "nvm"),
            "LOOPX_NPM_LOG": str(npm_log),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Using compatible Node.js from" in completed.stdout
    assert npm_log.read_text(encoding="utf-8").splitlines() == [
        "ci",
        "run dev:web",
    ]


def test_dashboard_launcher_prefers_explicit_loopx_python(
    tmp_path: Path,
) -> None:
    release_root, dashboard_script, fake_bin = _prepare_dashboard_runtime_fixture(
        tmp_path
    )
    configured_python = tmp_path / "configured-python"
    recorded_python = tmp_path / "recorded-python"

    command_log = tmp_path / "commands.log"
    for executable, label in (
        (configured_python, "configured"),
        (recorded_python, "recorded"),
    ):
        _write_logging_python_stub(executable, label=label)
    (release_root / ".loopx-python").write_text(
        f"{recorded_python}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(dashboard_script)],
        cwd=release_root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "LOOPX_COMMAND_LOG": str(command_log),
            "LOOPX_PYTHON": str(configured_python),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"Using LoopX Python: {configured_python}" in completed.stdout
    commands = command_log.read_text(encoding="utf-8")
    assert "configured:-m loopx.cli serve-status" in commands
    assert "configured:-m loopx.cli chat" in commands
    assert "recorded:" not in commands


def test_dashboard_launcher_uses_installer_recorded_python(
    tmp_path: Path,
) -> None:
    release_root, dashboard_script, fake_bin = _prepare_dashboard_runtime_fixture(
        tmp_path
    )
    recorded_python = tmp_path / "recorded-python"

    command_log = tmp_path / "commands.log"
    _write_logging_python_stub(recorded_python, label="recorded")
    (release_root / ".loopx-python").write_text(
        f"{recorded_python}\n",
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "LOOPX_COMMAND_LOG": str(command_log),
    }
    env.pop("LOOPX_PYTHON", None)
    completed = subprocess.run(
        ["bash", str(dashboard_script)],
        cwd=release_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"Using LoopX Python: {recorded_python}" in completed.stdout
    commands = command_log.read_text(encoding="utf-8")
    assert "recorded:-m loopx.cli serve-status" in commands
    assert "recorded:-m loopx.cli chat" in commands


def test_dashboard_launcher_ignores_stale_recorded_python(
    tmp_path: Path,
) -> None:
    release_root, dashboard_script, fake_bin = _prepare_dashboard_runtime_fixture(
        tmp_path
    )
    discovered_python = fake_bin / "python3.13"

    command_log = tmp_path / "commands.log"
    _write_logging_python_stub(discovered_python, label="discovered")
    (release_root / ".loopx-python").write_text(
        f"{tmp_path / 'missing-python'}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(dashboard_script)],
        cwd=release_root,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "LOOPX_COMMAND_LOG": str(command_log),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Ignoring non-functional Python recorded in .loopx-python" in (
        completed.stderr
    )
    commands = command_log.read_text(encoding="utf-8")
    assert "discovered:-m loopx.cli serve-status" in commands
    assert "discovered:-m loopx.cli chat" in commands


def test_dashboard_launcher_discovers_agent_bins_outside_restricted_path(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release"
    scripts_dir = release_root / "scripts"
    dashboard_dir = release_root / "apps" / "presentation" / "dashboard"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    npm_global = home / ".npm-global" / "bin"
    nvm_bin = home / ".nvm" / "versions" / "node" / "v22.22.2" / "bin"
    scripts_dir.mkdir(parents=True)
    dashboard_dir.mkdir(parents=True)
    fake_bin.mkdir()
    npm_global.mkdir(parents=True)
    nvm_bin.mkdir(parents=True)
    _copy_dashboard_launcher(scripts_dir)
    (dashboard_dir / "node_modules" / ".bin").mkdir(parents=True)
    (dashboard_dir / "node_modules" / ".bin" / "vite").touch()

    command_log = tmp_path / "commands.log"
    (fake_bin / "python3.13").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$LOOPX_COMMAND_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "node").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "npm").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (npm_global / "codex").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (nvm_bin / "claude").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (nvm_bin / "lark-cli").write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--version" ]]; then printf "%s\\n" "lark-cli 1.2.3"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    for executable in (
        fake_bin / "python3.13",
        fake_bin / "node",
        fake_bin / "npm",
        npm_global / "codex",
        nvm_bin / "claude",
        nvm_bin / "lark-cli",
    ):
        executable.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(scripts_dir / "dashboard-dev.sh")],
        cwd=release_root,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "NVM_DIR": str(home / ".nvm"),
            "NVM_BIN": str(nvm_bin),
            "LOOPX_COMMAND_LOG": str(command_log),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"Codex={npm_global / 'codex'}" in completed.stdout
    assert f"Claude Code={nvm_bin / 'claude'}" in completed.stdout
    commands = command_log.read_text(encoding="utf-8")
    assert f"--codex-bin {npm_global / 'codex'}" in commands
    assert f"--claude-bin {nvm_bin / 'claude'}" in commands
    assert f"--lark-cli-bin {nvm_bin / 'lark-cli'}" in commands


def test_dashboard_launcher_selects_highest_semantic_nvm_lark_cli(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "release"
    scripts_dir = release_root / "scripts"
    dashboard_dir = release_root / "apps" / "presentation" / "dashboard"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    low_bin = home / ".nvm" / "versions" / "node" / "v22.9.99" / "bin"
    high_bin = home / ".nvm" / "versions" / "node" / "v22.12.10" / "bin"
    scripts_dir.mkdir(parents=True)
    dashboard_dir.mkdir(parents=True)
    fake_bin.mkdir()
    low_bin.mkdir(parents=True)
    high_bin.mkdir(parents=True)
    _copy_dashboard_launcher(scripts_dir)
    (dashboard_dir / "node_modules" / ".bin").mkdir(parents=True)
    (dashboard_dir / "node_modules" / ".bin" / "vite").touch()

    command_log = tmp_path / "commands.log"
    (fake_bin / "python3.13").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$LOOPX_COMMAND_LOG"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "node").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "npm").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for lark_cli in (low_bin / "lark-cli", high_bin / "lark-cli"):
        lark_cli.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for executable in (
        fake_bin / "python3.13",
        fake_bin / "node",
        fake_bin / "npm",
        low_bin / "lark-cli",
        high_bin / "lark-cli",
    ):
        executable.chmod(0o755)

    completed = subprocess.run(
        ["bash", str(scripts_dir / "dashboard-dev.sh")],
        cwd=release_root,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "NVM_DIR": str(home / ".nvm"),
            "NVM_BIN": "",
            "LOOPX_COMMAND_LOG": str(command_log),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert f"--lark-cli-bin {high_bin / 'lark-cli'}" in commands
    assert f"--lark-cli-bin {low_bin / 'lark-cli'}" not in commands


def test_dashboard_command_in_installed_mode_resolves_packaged_web_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "loopx.dashboard_launcher._probe_existing_chat",
        lambda *args, **kwargs: "unavailable",
    )
    served: list[dict[str, object]] = []

    monkeypatch.setattr(
        "loopx.chat_server.serve_chat",
        lambda **kwargs: served.append(kwargs),
    )

    exit_code = main(["dashboard", "--host", "127.0.0.1", "--port", "8791", "--no-open"])
    assert exit_code == 0
    assert len(served) == 1
    assert served[0]["host"] == "127.0.0.1"
    assert served[0]["port"] == 8791
    assert served[0]["open_browser"] is False
    assert (Path(str(served[0]["assets_dir"])) / "index.html").is_file()


@pytest.mark.parametrize(
    ("command", "target", "argv"),
    [
        ("chat", "serve_chat", ["chat", "--no-open"]),
        ("dashboard", "launch_dashboard", ["dashboard"]),
        ("serve-status", "serve_status", ["serve-status"]),
    ],
)
def test_local_service_start_failure_keeps_its_error_in_markdown(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    target: str,
    argv: list[str],
) -> None:
    def fail(**_kwargs: object) -> None:
        raise RuntimeError("Chat bundle is missing; run npm run build:chat")

    monkeypatch.setattr(support_control, target, fail, raising=False)

    assert main(argv) == 1
    output = capsys.readouterr().out
    assert output.startswith(f"# LoopX {command} could not start")
    assert "- error: Chat bundle is missing; run npm run build:chat" in output
    # A failed start has no status projection to render.
    assert "- goals: `None`" not in output


def test_chat_start_failure_markdown_names_the_next_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_kwargs: object) -> None:
        raise RuntimeError("Codex app-server is unavailable")

    monkeypatch.setattr(support_control, "serve_chat", fail)

    assert main(["chat", "--no-open"]) == 1
    assert "- next action: Resolve the reported local host capability, then retry loopx chat." in capsys.readouterr().out

    assert main(["--format", "json", "chat", "--no-open"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "loopx_chat_start_v0"
    assert payload["error"] == "Codex app-server is unavailable"
