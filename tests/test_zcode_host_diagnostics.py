from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.zcode_goal_mode import diagnostics


# Independent syntax examples, not a snapshot of one installed release.
HELP = """zcode 1.2.3
Usage:
  zcode [command] [options]
Commands:
  app-server Run the stdio app server
  agent-server Alias for the app server
  plugins Manage plugins
  skills List local skills
Options:
  --prompt <text> Run a single prompt
  --target <text> Set the session goal
  --continue Resume the latest session
  --resume <sessionId> Resume a session
  --json Print JSON
Slash Commands:
  /mcp [list|status] Show servers
"""


@pytest.fixture(autouse=True)
def no_ambient_zcode(monkeypatch):
    for name in ("ZCODE_CLI_PATH", "ZCODE_DESKTOP_PATH", "ZCODE_SOURCE_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(diagnostics, "_desktop_candidates", lambda: [])
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic fixture; never execute", encoding="utf-8")
    return path


def _probe(output: str, status: str = "observed") -> dict:
    return {"status": status, "exit_code": 0 if status == "observed" else 1, "output": output}


def test_cli_observations_use_only_help_and_version(monkeypatch, tmp_path):
    executable = _file(tmp_path / "zcode")
    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        if "loopx-doctor-invalid" in command:
            return _probe("Invalid format", status="failed")
        return _probe(HELP if "--help" in command else "ZCode CLI 1.2.3\n")

    monkeypatch.setattr(diagnostics, "_run_probe", probe)
    cli = diagnostics._inspect_cli(executable, discovery="explicit")

    assert cli["version"] == "1.2.3"
    assert cli["status"] == "available"
    assert cli["runtime_verified"] is False
    for name in ("stdio_app_server", "stdio_agent_server_alias", "headless_prompt", "native_goal", "resume", "continue", "json_result", "mcp", "plugins", "skills"):
        assert cli["interfaces"][name]["status"] == "advertised"
    assert cli["interfaces"]["stream_json_syntax"]["status"] == "advertised"
    assert [command[1:] for command in calls] == [
        ["--version"],
        ["--help", "--locale", "en-US"],
        ["--version", "--output-format", "stream-json"],
        ["--version", "--output-format", "loopx-doctor-invalid"],
    ]
    assert all(command[0] == str(executable) for command in calls)


def test_help_requires_exact_interface_tokens(monkeypatch, tmp_path):
    executable = _file(tmp_path / "zcode")
    near_matches = """zcode 1.2.3
Usage:
  zcode [command] [options]
Commands:
  app-server-remote
  agent-server-remote
  plugins-extra
  skills-extra
Options:
  --prompt-cache --target-replace --continue-cache --resume-cache --jsonlines
Slash Commands:
  /mcp-extra /skills-extra
"""
    monkeypatch.setattr(
        diagnostics, "_run_probe",
        lambda command, **kwargs: _probe(near_matches if "--help" in command else "1.2.3"),
    )

    interfaces = diagnostics._inspect_cli(executable, discovery="explicit")["interfaces"]

    for name in ("stdio_app_server", "stdio_agent_server_alias", "headless_prompt", "native_goal", "resume", "continue", "json_result", "mcp", "plugins", "skills"):
        assert interfaces[name]["status"] == "not_advertised"


def test_unidentified_executable_help_cannot_claim_zcode_interfaces(monkeypatch, tmp_path):
    executable = _file(tmp_path / "other-tool")
    unrelated_help = HELP[HELP.index("Commands:"):]
    monkeypatch.setattr(
        diagnostics, "_run_probe",
        lambda command, **kwargs: _probe(unrelated_help if "--help" in command else "1.2.3"),
    )

    cli = diagnostics._inspect_cli(executable, discovery="explicit")

    assert cli["status"] == "probe_failed"
    assert all(row["status"] == "unverified" for row in cli["interfaces"].values())


def test_ignored_output_format_flag_is_not_evidence_of_stream_json(monkeypatch, tmp_path):
    executable = _file(tmp_path / "zcode")
    monkeypatch.setattr(
        diagnostics, "_run_probe",
        lambda command, **kwargs: _probe(HELP if "--help" in command else "1.2.3"),
    )

    cli = diagnostics._inspect_cli(executable, discovery="explicit")

    assert cli["status"] == "available"
    assert cli["interfaces"]["stream_json_syntax"]["status"] == "unverified"
    assert cli["runtime_verified"] is False


def test_explicit_missing_cli_never_falls_back_to_path(monkeypatch, tmp_path):
    def unexpected_lookup(name):
        pytest.fail(f"Explicit missing CLI must not discover an alternative: {name}")

    monkeypatch.setattr(diagnostics.shutil, "which", unexpected_lookup)
    missing = tmp_path / "missing-zcode"

    payload = diagnostics.collect_zcode_host_diagnostics(cli_path=str(missing))

    assert payload["cli"]["status"] == "not_found"
    assert payload["cli"]["path"] == str(missing)
    assert payload["cli"]["discovery"] == "explicit"
    assert payload["cli"]["version"] is None


def test_failed_help_remains_unverified_and_does_not_export_raw_errors(monkeypatch, tmp_path):
    executable = _file(tmp_path / "zcode")
    secret = "private-configuration-error-marker"
    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        return _probe(secret + HELP, status="failed")

    monkeypatch.setattr(diagnostics, "_run_probe", probe)
    payload = diagnostics.collect_zcode_host_diagnostics(cli_path=str(executable))

    assert payload["cli"]["status"] == "probe_failed"
    assert all(row["status"] == "unverified" for row in payload["cli"]["interfaces"].values())
    assert len(calls) == 2  # Failed help cannot authorize another syntax probe.
    assert secret not in json.dumps(payload)
    markdown = "\n".join(diagnostics.render_zcode_diagnostics_markdown(payload))
    assert secret not in markdown
    assert "unverified" in markdown


@pytest.mark.parametrize("status,output", [("failed", "8.1.0"), ("observed", "invalid-version")])
def test_failed_or_unparseable_version_is_unknown(monkeypatch, tmp_path, status, output):
    executable = _file(tmp_path / "zcode")
    monkeypatch.setattr(
        diagnostics, "_run_probe",
        lambda command, **kwargs: _probe(HELP) if "--help" in command else _probe(output, status),
    )

    cli = diagnostics._inspect_cli(executable, discovery="explicit")

    assert cli["version"] is None
    assert cli["status"] == "probe_failed"
    assert cli["interfaces"]["stream_json_syntax"]["status"] == "unverified"
    assert cli["runtime_verified"] is False


def test_source_package_version_cannot_stand_in_for_built_runtime(monkeypatch, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(json.dumps({"name": "zcode", "version": "9.9.9"}), encoding="utf-8")
    monkeypatch.setattr(diagnostics, "_run_probe", lambda *args, **kwargs: pytest.fail("No built CLI exists"))

    payload = diagnostics.collect_zcode_host_diagnostics(source_root=str(source))

    assert payload["source_checkout"]["package_version"] == "9.9.9"
    built = payload["source_checkout"]["built_cli"]
    assert built["status"] == "not_found"
    assert built["version"] is None
    assert built["runtime_verified"] is False
    assert payload["cli"]["version"] is None
    assert payload["desktop"]["version"] is None
    markdown = "\n".join(diagnostics.render_zcode_diagnostics_markdown(payload))
    assert "declared package version: 9.9.9 (not runtime version)" in markdown


def test_cli_desktop_and_source_bundle_versions_are_independent(monkeypatch, tmp_path):
    executable = _file(tmp_path / "cli" / "zcode")
    desktop = _file(tmp_path / "desktop" / "ZCode.exe")
    desktop_bundle = _file(desktop.parent / "resources" / "glm" / "zcode.cjs")
    source = tmp_path / "source"
    source_bundle = _file(source / "apps" / "zcode-cli" / "packages" / "cli" / "dist" / "zcode.cjs")
    (source / "package.json").write_text(json.dumps({"name": "zcode", "version": "9.9.9"}), encoding="utf-8")
    versions = {str(executable): "1.2.3", str(desktop_bundle): "4.5.6", str(source_bundle): "7.8.9"}
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "synthetic-node" if name == "node" else None)
    monkeypatch.setattr(diagnostics, "_desktop_version", lambda *args: {"value": "2.3.4", "evidence": "executable ProductVersion"})

    def probe(command, **kwargs):
        if "loopx-doctor-invalid" in command:
            return _probe("Invalid format", status="failed")
        target = command[1] if command[0] == "synthetic-node" else command[0]
        return _probe(HELP if "--help" in command else versions[target])

    monkeypatch.setattr(diagnostics, "_run_probe", probe)
    payload = diagnostics.collect_zcode_host_diagnostics(
        cli_path=str(executable), desktop_path=str(desktop), source_root=str(source),
    )

    assert payload["cli"]["version"] == "1.2.3"
    assert payload["desktop"]["version"] == "2.3.4"
    assert payload["desktop"]["bundled_cli"]["version"] == "4.5.6"
    assert payload["source_checkout"]["package_version"] == "9.9.9"
    assert payload["source_checkout"]["built_cli"]["version"] == "7.8.9"
    for host in (payload["cli"], payload["desktop"], payload["desktop"]["bundled_cli"], payload["source_checkout"]["built_cli"]):
        assert host["runtime_verified"] is False
    markdown = "\n".join(diagnostics.render_zcode_diagnostics_markdown(payload))
    assert "Desktop bundled CLI:" in markdown
    assert "Source built CLI:" in markdown
    assert "managed native CLI Goal requires explicit zcode-goal bind" in markdown
    assert "Desktop attachment and Automations are not integrated" in markdown



def test_relative_cli_and_source_paths_are_resolved_before_probe(monkeypatch, tmp_path):
    executable = _file(tmp_path / "zcode")
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(json.dumps({"name": "zcode", "version": "9.9.9"}), encoding="utf-8")
    commands = []

    def probe(command, **kwargs):
        commands.append(command)
        if "loopx-doctor-invalid" in command:
            return _probe("Invalid format", status="failed")
        return _probe(HELP if "--help" in command else "1.2.3")

    monkeypatch.setattr(diagnostics, "_run_probe", probe)
    monkeypatch.chdir(tmp_path)
    payload = diagnostics.collect_zcode_host_diagnostics(cli_path="zcode", source_root="source")

    assert payload["cli"]["path"] == str(executable.resolve())
    assert payload["source_checkout"]["path"] == str(source.resolve())
    assert all(command[0] == str(executable.resolve()) for command in commands)


def test_probe_uses_disposable_storage_and_preserves_parent_environment(monkeypatch):
    monkeypatch.setenv("ZCODE_STORAGE_DIR", "existing-private-storage")
    script = (
        "import json, os, sys; "
        "names = ('ZCODE_HOME', 'ZCODE_STORAGE_DIR', 'ZCODE_DATA_BASE_DIR', 'SYNTHETIC_METADATA_PATH'); "
        "print(json.dumps({'cwd': os.getcwd(), 'env': {name: os.environ.get(name) for name in names}, 'stdin': sys.stdin.read()}), flush=True); "
        "print('private-stderr-marker', file=sys.stderr)"
    )
    result = diagnostics._run_probe(
        [sys.executable, "-c", script],
        extra_env={"SYNTHETIC_METADATA_PATH": "safe-metadata", "ZCODE_HOME": "ignored-override"},
    )

    assert result["status"] == "observed"
    metadata = json.loads(next(line for line in result["output"].splitlines() if line.startswith("{")))
    disposable_root = Path(metadata["cwd"]).resolve()
    for name in ("ZCODE_HOME", "ZCODE_STORAGE_DIR", "ZCODE_DATA_BASE_DIR"):
        assert Path(metadata["env"][name]).resolve() == disposable_root
    assert metadata["env"]["SYNTHETIC_METADATA_PATH"] == "safe-metadata"
    assert not disposable_root.exists()
    assert diagnostics.os.environ["ZCODE_STORAGE_DIR"] == "existing-private-storage"
    assert metadata["stdin"] == ""
    assert "private-stderr-marker" not in json.dumps(diagnostics._public_probe(result))


def test_probe_timeout_drops_partial_output(monkeypatch):
    monkeypatch.setattr(diagnostics, "PROBE_TIMEOUT_SECONDS", 0.1)
    result = diagnostics._run_probe([
        sys.executable, "-c",
        "import time; print('private-timeout-output', flush=True); time.sleep(30)",
    ])

    assert result["status"] == "timeout"
    assert result["exit_code"] is None
    assert result["output"] == ""


def test_missing_probe_executable_does_not_return_exception_content(tmp_path):
    result = diagnostics._run_probe([str(tmp_path / "private-missing-executable")])

    assert result["status"] == "unreadable"
    assert result["exit_code"] is None
    assert result["output"] == ""
    assert "private-missing-executable" not in json.dumps(result)


def test_oversized_probe_output_is_not_projected():
    result = diagnostics._run_probe([sys.executable, "-c", "print('x' * 65537, flush=True)"])

    assert result["status"] == "output_limit"
    assert result["output"] == ""


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_probe_deadline_survives_descendant_inheriting_stdout(monkeypatch, capsys):
    monkeypatch.setattr(diagnostics, "PROBE_TIMEOUT_SECONDS", 0.1)
    script = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)']); "
        "print('private-descendant-output', flush=True)"
    )
    started = time.monotonic()
    result = diagnostics._run_probe([sys.executable, "-c", script])
    elapsed = time.monotonic() - started

    assert result["status"] == "timeout"
    assert result["output"] == ""
    assert elapsed < 1.5
    # The child exits shortly afterward; the asynchronous reader must finish
    # without racing another thread that closes its pipe.
    time.sleep(max(0, 2.2 - elapsed))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("mode,expected", [("timeout_default", "timeout"), ("timeout", "timeout"), ("output_limit", "output_limit"), ("exit_pipe", "timeout"), ("exit_closed", "observed")])
def test_probe_reclaims_persistent_descendants_before_return(tmp_path, monkeypatch, mode, expected):
    if mode != "timeout_default":
        monkeypatch.setattr(diagnostics, "PROBE_TIMEOUT_SECONDS", .5)
    child_script = "import time, pathlib, sys, os; p=pathlib.Path(sys.argv[1]); p.write_text(str(os.getpid())); n=0\nwhile True:\n n+=1; p.with_suffix('.pulse').write_text(str(n)); time.sleep(.01)"
    path = tmp_path / "child.pid"
    parent = "import subprocess,sys,time,pathlib,os; p=pathlib.Path(sys.argv[1]); c=subprocess.Popen([sys.executable,'-c',sys.argv[2],str(p)],stdout=subprocess.DEVNULL if sys.argv[3]=='exit_closed' else None,stderr=subprocess.DEVNULL if sys.argv[3]=='exit_closed' else None)\nwhile not p.exists(): time.sleep(.01)\nif sys.argv[3]=='output_limit': print('x'*70000,flush=True)\nif sys.argv[3].startswith('exit'): sys.exit(0)\ntime.sleep(30)"
    for _ in range(2):
        path.unlink(missing_ok=True)
        try:
            result = diagnostics._run_probe([sys.executable, "-c", parent, str(path), child_script, mode])
            assert result["status"] == expected, result
            pid = int(path.read_text())
            if os.name == "posix":
                snapshot = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "stat="], check=True, capture_output=True, text=True).stdout
                assert not any(int(parts[0]) == pid and not parts[1].startswith("Z") for line in snapshot.splitlines() if len(parts := line.split()) == 2)
            else:
                snapshot = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], check=True, capture_output=True, text=True).stdout
                assert f'","{pid}",' not in snapshot
            before = path.with_suffix(".pulse").read_text()
            time.sleep(.05)
            assert path.with_suffix(".pulse").read_text() == before
        finally:
            if path.exists():
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", path.read_text(), "/F"], capture_output=True)
                else:
                    try:
                        os.kill(int(path.read_text()), 9)
                    except ProcessLookupError:
                        pass


@pytest.mark.skipif(os.name == "nt", reason="Windows children must resume through the real job owner")
def test_probe_cleanup_failure_is_not_an_observed_result(monkeypatch):
    def prepare(process):
        def fail():
            process.wait(timeout=1)
            raise RuntimeError("private-cleanup-error")
        return fail
    monkeypatch.setattr(diagnostics, "prepare_owned_process_cleanup", prepare)
    # No descendant is created; the independent process exits before failure.
    result = diagnostics._run_probe([sys.executable, "-c", "print('private-output')"])
    assert result == {"status": "unreadable", "exit_code": 0, "output": ""}
