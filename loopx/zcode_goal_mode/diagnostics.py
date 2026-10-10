from __future__ import annotations

# Local host observations; no control-plane decisions or execution authority.
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Literal, TypedDict

from loopx.extensions.process_runtime import prepare_owned_process_cleanup

InterfaceStatus = Literal["advertised", "not_advertised", "unverified"]
ProbeStatus = Literal["observed", "failed", "timeout", "unreadable", "output_limit"]


class _ProbeReport(TypedDict):
    status: ProbeStatus
    exit_code: int | None
    output: str


PROBE_TIMEOUT_SECONDS = 5
MAX_METADATA_BYTES = 1_048_576
INTERFACE_PATTERNS = {
    "headless_prompt": r"(?<![\w-])--prompt(?:[ =,]|$)",
    "continue": r"(?<![\w-])--continue(?:[ =,]|$)",
    "stdio_agent_server_alias": r"(?m)^\s*agent-server(?:\s|$)",
    "stdio_app_server": r"(?m)^\s*app-server(?:\s|$)",
    "native_goal": r"(?<![\w-])--target(?:[ =,]|$)",
    "resume": r"(?<![\w-])--resume(?:[ =,]|$)",
    "json_result": r"(?<![\w-])--json(?:[ =,]|$)",
    "mcp": r"(?<![\w-])/mcp(?:[ \t,]|$)",
    "plugins": r"(?m)^\s*plugins(?:\s|$)",
    "skills": r"(?m)(?:^\s*skills(?:\s|$)|(?<![\w-])/skills?(?:[ \t,]|$))",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _run_probe(command: list[str], *, extra_env: dict[str, str] | None = None) -> _ProbeReport:
    # Metadata/help only. Shared transport owns the complete isolated tree;
    # the reader owns pipe close, which may otherwise wait on a descendant.
    disposable = None
    try:
        disposable = tempfile.TemporaryDirectory(prefix="loopx-zcode-doctor-", ignore_cleanup_errors=True)
        env = dict(os.environ)
        env.update(extra_env or {})
        for name in ("ZCODE_HOME", "ZCODE_STORAGE_DIR", "ZCODE_DATA_BASE_DIR"):
            env[name] = disposable.name
        process = subprocess.Popen(
            command, cwd=disposable.name, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
            start_new_session=os.name == "posix",
            creationflags=(subprocess.CREATE_NO_WINDOW | 4) if os.name == "nt" else 0,
        )
        cleanup = prepare_owned_process_cleanup(process)
    except OSError:
        if disposable is not None:
            disposable.cleanup()
        return {"status": "unreadable", "exit_code": None, "output": ""}

    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    output: list[bytes] = []
    limited = threading.Event()
    read_failed = threading.Event()
    stream = process.stdout
    assert stream is not None

    def capture() -> None:
        size = 0
        try:
            while chunk := stream.read(4096):
                size += len(chunk)
                if size > 65536:
                    limited.set()
                    break
                output.append(chunk)
        except (OSError, ValueError):
            read_failed.set()
        finally:
            try:
                stream.close()
            except OSError:
                pass

    reader = threading.Thread(target=capture, daemon=True)
    reader.start()
    status: ProbeStatus
    exit_code = None
    while True:
        exit_code = process.poll()
        if limited.is_set():
            status = "output_limit"
            break
        if exit_code is not None and not reader.is_alive():
            status = "unreadable" if read_failed.is_set() else "observed" if exit_code == 0 else "failed"
            break
        if time.monotonic() >= deadline:
            status = "timeout"
            break
        limited.wait(min(.01, max(0, deadline - time.monotonic())))
    try:
        # Ownership survives leader exit; the shared transport confirms the
        # isolated group is no longer executable before returning a report.
        cleanup()
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        status = "unreadable"
    reader.join(timeout=1)
    if reader.is_alive():
        status = "unreadable"
    else:
        disposable.cleanup()
    return {
        "status": status, "exit_code": exit_code,
        "output": b"".join(output).decode("utf-8", errors="replace") if status in {"observed", "failed"} else "",
    }



def _public_probe(probe: _ProbeReport) -> dict[str, Any]:
    # Raw output may include private configuration/errors; never project it.
    return {"status": probe["status"], "exit_code": probe["exit_code"]}


def _version(output: str) -> str | None:
    for line in output.splitlines()[:8]:
        match = re.fullmatch(
            r"\s*(?:ZCode(?: CLI)?(?: version)?[: ]+)?v?(\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?)\s*",
            line, re.IGNORECASE,
        )
        if match:
            return match.group(1)
    return None


def _unknown_interfaces() -> dict[str, dict[str, str]]:
    return {name: {"status": "unverified", "evidence": "none"} for name in (*INTERFACE_PATTERNS, "stream_json_syntax")}


def _inspect_cli(path: Path | None, *, discovery: str) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "status": "not_found", "path": str(path) if path else None,
        "discovery": discovery, "version": None, "interfaces": _unknown_interfaces(),
        "runtime_verified": False, "version_evidence": "unavailable",
        "next_action": "Install ZCode CLI or supply --zcode-cli with its executable or existing JS bundle.",
    }
    if path is None:
        return observation
    try:
        path = path.resolve()
        observation["path"] = str(path)
        if not path.is_file():
            return observation
    except OSError:
        observation["status"] = "unreadable"
        return observation
    if (path.parent / "resources/glm/zcode.cjs").is_file() or (path.parent / "resources/app.asar").is_file():
        observation.update(status="invalid", next_action="This is a Desktop installation. Use --zcode-desktop; the GUI executable is never used for CLI probes.")
        return observation
    is_js = path.suffix.lower() in {".js", ".cjs", ".mjs"}
    node = shutil.which("node") if is_js else None
    if is_js and node is None:
        observation.update(status="probe_failed", next_action="Make node available on PATH to inspect this JS bundle.")
        return observation
    command = [node, str(path)] if node else [str(path)]
    version = _run_probe([*command, "--version"])
    help_probe = _run_probe([*command, "--help", "--locale", "en-US"])
    observation["probes"] = {"version": _public_probe(version), "help": _public_probe(help_probe)}
    if version["status"] == "observed":
        observation["version"] = _version(version["output"])
        if observation["version"]:
            observation["version_evidence"] = "--version"
    identity = help_probe["status"] == "observed" and re.search(
        r"(?im)^\s*zcode(?:\s+v?\d+\.\d+\.\d+|\s+\[command\])",
        help_probe["output"],
    ) is not None
    observation["identity_verified"] = identity
    if identity:
        for name, pattern in INTERFACE_PATTERNS.items():
            status: InterfaceStatus = "advertised" if re.search(pattern, help_probe["output"]) else "not_advertised"
            observation["interfaces"][name] = {"status": status, "evidence": "--help --locale en-US"}
        # Version short-circuits before sessions/models: parser recognition
        # does not verify a runtime stream or execution readiness.
        syntax = _run_probe([*command, "--version", "--output-format", "stream-json"])
        invalid_syntax = _run_probe([*command, "--version", "--output-format", "loopx-doctor-invalid"])
        recognized = invalid_syntax["status"] == "failed" and syntax["status"] == "observed" and _version(syntax["output"]) == observation["version"] and observation["version"] is not None
        observation["probes"]["stream_json_syntax"] = _public_probe(syntax)
        observation["probes"]["invalid_output_format_control"] = _public_probe(invalid_syntax)
        observation["interfaces"]["stream_json_syntax"] = {
            "status": "advertised" if recognized else "unverified",
            "evidence": "--version --output-format stream-json" if recognized else "none",
        }
    observation["status"] = "available" if identity and observation["version"] else "probe_failed"
    observation["next_action"] = (
        "Help/version observed; sessions, models, permissions and native protocol execution remain unverified."
        if observation["status"] == "available" else
        "Check the CLI path, Node runtime and permissions; failed probes do not prove interface absence."
    )
    return observation


def _desktop_candidates() -> list[Path]:
    if os.name == "nt":
        return [
            Path(os.environ[variable]) / suffix
            for variable, suffix in (
                ("LOCALAPPDATA", "Programs/ZCode/ZCode.exe"),
                ("LOCALAPPDATA", "ZCode/ZCode.exe"),
                ("ProgramFiles", "ZCode/ZCode.exe"),
            ) if os.environ.get(variable)
        ]
    if sys.platform == "darwin":
        return [Path("/Applications/ZCode.app"), Path.home() / "Applications/ZCode.app"]
    return [Path("/opt/ZCode/zcode"), Path("/opt/zcode/zcode")]


def _resolve_desktop(path: Path) -> tuple[Path, Path]:
    # Locate executable/resources without starting the GUI.
    if path.suffix.lower() == ".app":
        return path / "Contents/MacOS/ZCode", path / "Contents/Resources"
    if path.is_dir():
        return path / ("ZCode.exe" if os.name == "nt" else "zcode"), path / "resources"
    if path.parent.name == "MacOS":
        return path, path.parent.parent / "Resources"
    return path, path.parent / "resources"


def _desktop_version(executable: Path, resources: Path) -> dict[str, Any]:
    if executable.suffix.lower() == ".exe" and os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell:
            script = "(Get-Item -LiteralPath $env:LOOPX_ZCODE_METADATA_PATH -ErrorAction Stop).VersionInfo | Select-Object ProductVersion,ProductName | ConvertTo-Json -Compress"
            probe = _run_probe(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                extra_env={"LOOPX_ZCODE_METADATA_PATH": str(executable)},
            )
            try:
                metadata = json.loads(probe["output"]) if probe["status"] == "observed" else {}
                value = metadata.get("ProductVersion")
                if isinstance(value, str) and _version(value):
                    return {"value": value, "evidence": "executable ProductVersion", "identity_verified": str(metadata.get("ProductName", "")).casefold() == "zcode"}
            except (ValueError, AttributeError):
                pass
    plist_path = resources.parent / "Info.plist"
    if plist_path.is_file():
        try:
            with plist_path.open("rb") as stream:
                value = plistlib.load(stream).get("CFBundleShortVersionString")
            if isinstance(value, str):
                return {"value": value, "evidence": "Info.plist CFBundleShortVersionString", "identity_verified": resources.parent.parent.name.casefold() == "zcode.app"}
        except (OSError, ValueError, plistlib.InvalidFileException):
            pass
    package = _read_json(resources / "app/package.json")
    if package and isinstance(package.get("version"), str):
        return {"value": package["version"], "evidence": "installed app/package.json", "identity_verified": str(package.get("name", "")).casefold() == "zcode"}
    return {"value": None, "evidence": "unavailable", "identity_verified": False}


def _inspect_desktop(path_text: str | None) -> dict[str, Any]:
    explicit = path_text or os.environ.get("ZCODE_DESKTOP_PATH")
    candidates = [Path(explicit).expanduser()] if explicit else _desktop_candidates()
    selected = resources = None
    for candidate in candidates:
        executable, root = _resolve_desktop(candidate.resolve())
        if executable.is_file():
            selected, resources = executable, root
            break
    observation: dict[str, Any] = {
        "status": "available" if selected else "not_found",
        "path": str(selected) if selected else (str(candidates[0]) if explicit else None),
        "discovery": "explicit" if explicit else "standard_install_locations",
        "version": None, "version_evidence": "unavailable", "runtime_verified": False,
        "next_action": "Supply --zcode-desktop with the executable, install directory or .app bundle; inaccessible locations do not prove no installation.",
    }
    if selected is not None and resources is not None:
        metadata = _desktop_version(selected, resources)
        identity = metadata.get("identity_verified", False) or (resources / "glm/zcode.cjs").is_file()
        observation.update(version=metadata["value"], version_evidence=metadata["evidence"], identity_verified=identity)
        if not identity:
            observation.update(status="unverified", next_action="The selected file exists but ZCode Desktop identity could not be verified. Check its product metadata or installation resources.")
            return observation
        observation["bundled_cli"] = _inspect_cli(resources / "glm/zcode.cjs", discovery="desktop_bundle")
        observation["next_action"] = "Desktop metadata and bundled CLI are separate; the Desktop UI and Automations have not been exercised."
    return observation


def _inspect_source(path_text: str | None) -> dict[str, Any]:
    text = path_text or os.environ.get("ZCODE_SOURCE_ROOT")
    if not text:
        return {"status": "not_selected", "path": None, "package_version": None}
    root = Path(text).expanduser().resolve()
    package = _read_json(root / "package.json")
    valid = package is not None and str(package.get("name", "")).lower() == "zcode"
    observation: dict[str, Any] = {
        "status": "available" if valid else "invalid", "path": str(root),
        "package_version": package.get("version") if valid and package is not None else None,
        "version_evidence": "source package.json; not an installed/runtime version",
    }
    if valid:
        observation["built_cli"] = _inspect_cli(root / "apps/zcode-cli/packages/cli/dist/zcode.cjs", discovery="source_bundle")
    else:
        observation["next_action"] = "Supply --zcode-source with a ZCode checkout containing its root package.json."
    return observation


def collect_zcode_host_diagnostics(
    *, cli_path: str | None = None, desktop_path: str | None = None,
    source_root: str | None = None,
) -> dict[str, Any]:
    explicit_cli = cli_path or os.environ.get("ZCODE_CLI_PATH")
    found = shutil.which("zcode") if not explicit_cli else None
    resolved_cli = Path(explicit_cli).expanduser() if explicit_cli else (Path(found) if found else None)
    return {
        "cli": _inspect_cli(resolved_cli, discovery="explicit" if explicit_cli else "PATH"),
        "desktop": _inspect_desktop(desktop_path),
        "source_checkout": _inspect_source(source_root),
        "loopx_binding": {"mode": "skill_facade", "native_goal": "opt_in_managed_cli", "automations": "not_integrated"},
        "probe_boundary": "Isolated help/version only. No Desktop launch, session, model, app-server handshake or credential read. Interface observations do not certify runtime readiness.",
    }


def render_zcode_diagnostics_markdown(payload: dict[str, Any]) -> list[str]:
    lines = ["", "## ZCode hosts", ""]
    hosts = [("CLI", payload["cli"]), ("Desktop", payload["desktop"])]
    if payload["desktop"].get("bundled_cli"):
        hosts.append(("Desktop bundled CLI", payload["desktop"]["bundled_cli"]))
    source = payload["source_checkout"]
    lines.append(f"- Source checkout: **{source['status']}**; declared package version: {source.get('package_version') or 'unknown'} (not runtime version).")
    if source.get("path"):
        lines.append(f"  Path: {source['path']}")
    if source.get("built_cli"):
        hosts.append(("Source built CLI", source["built_cli"]))
    for label, host in hosts:
        lines.append(f"- {label}: **{host['status']}**; version: {host.get('version') or 'unknown'}; path: {host.get('path') or 'not discovered'}.")
        if host.get("version_evidence"):
            lines.append(f"  Version evidence: {host['version_evidence']}.")
        if host.get("interfaces"):
            interfaces = ", ".join(f"{name}={row['status']}" for name, row in host["interfaces"].items())
            lines.append(f"  Interface observations: {interfaces}.")
        if host.get("next_action"):
            lines.append(f"  {host['next_action']}")
    lines.extend(["", payload["probe_boundary"], "LoopX binding: Skill facade by default; managed native CLI Goal requires explicit zcode-goal bind. Desktop attachment and Automations are not integrated."])
    return lines
