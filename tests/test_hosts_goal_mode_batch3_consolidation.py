"""Contracts protecting Batch 3 host consolidation and thin compatibility shims."""

from __future__ import annotations

import ast
import importlib.util
from importlib.util import resolve_name
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import venv
import zipfile

from loopx import __version__ as loopx_version
from loopx.hosts import agy, dsh
from loopx.hosts.agy.launchd_watchdog import build_antigravity_launchd_plist
from tests.test_dsh_goal_mode import _signed_request

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "loopx"
SHIM_TARGETS: dict[str, tuple[set[str], str]] = {
    "agy_goal_mode": (
        {"__init__.py", "host_runtime.py", "launchd_watchdog.py"},
        "loopx.hosts.agy",
    ),
    "dsh_goal_mode": (
        {"__init__.py", "__main__.py", "host_failure_map.py", "turn_host_adapter.py"},
        "loopx.hosts.dsh",
    ),
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("loopx", *parts))


def _resolved_imports(path: Path) -> set[str]:
    module_name = _module_name(path)
    package_name = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    tree = ast.parse(path.read_bytes(), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package_name)
            if module:
                imports.add(module)
                if module == "loopx" or module.startswith("loopx."):
                    rel_parts = module.split(".")[1:]
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        candidate_path = PACKAGE_ROOT.joinpath(*rel_parts, alias.name)
                        if candidate_path.with_suffix(".py").is_file() or candidate_path.is_dir():
                            imports.add(f"{module}.{alias.name}")
    return imports


def _build_pure_loopx_wheel(out_dir: Path) -> Path:
    version = loopx_version
    metadata = f"loopx-{version}.dist-info"
    contents: dict[str, bytes] = {}
    for file_path in sorted(PACKAGE_ROOT.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(REPO_ROOT).as_posix()
        if "/__pycache__/" in rel or rel.startswith("loopx/web/") or rel.endswith(".pyc"):
            continue
        if file_path.suffix not in {".py", ".json", ".ts", ".toml", ".md"}:
            continue
        contents[rel] = file_path.read_bytes()

    contents[f"{metadata}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: loopx\nVersion: {version}\n".encode("utf-8")
    )
    contents[f"{metadata}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    contents[f"{metadata}/RECORD"] = "".join(
        f"{name},,\n" for name in [*contents, f"{metadata}/RECORD"]
    ).encode("utf-8")

    wheel_path = out_dir / f"loopx-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return wheel_path


def test_batch3_hosts_packages_and_pyproject_package_data() -> None:
    for rel in (
        "agy/__init__.py",
        "agy/README.md",
        "agy/host_runtime.py",
        "agy/launchd_watchdog.py",
        "dsh/__init__.py",
        "dsh/__main__.py",
        "dsh/README.md",
        "dsh/host_failure_map.py",
        "dsh/turn_host_adapter.py",
    ):
        assert (PACKAGE_ROOT / "hosts" / rel).is_file(), rel

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "loopx.agy_goal_mode" not in package_data
    assert "loopx.dsh_goal_mode" not in package_data
    assert package_data["loopx.hosts.agy"] == ["README.md"]
    assert package_data["loopx.hosts.dsh"] == ["README.md"]


def test_no_internal_loopx_production_module_imports_legacy_batch3_shims() -> None:
    legacy_prefixes = ("loopx.agy_goal_mode", "loopx.dsh_goal_mode")
    offenders: list[tuple[str, str]] = []
    for py_path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = py_path.relative_to(PACKAGE_ROOT).as_posix()
        if rel.startswith(("agy_goal_mode/", "dsh_goal_mode/")):
            continue
        for imported in sorted(_resolved_imports(py_path)):
            if imported == legacy_prefixes[0] or imported.startswith(legacy_prefixes[0] + "."):
                offenders.append((rel, imported))
            elif imported == legacy_prefixes[1] or imported.startswith(legacy_prefixes[1] + "."):
                offenders.append((rel, imported))
    assert offenders == [], f"Internal modules must import loopx.hosts.{{agy,dsh}}: {offenders}"


def test_legacy_batch3_packages_are_thin_forwarding_shims_only() -> None:
    for pkg_name, (expected_files, target_prefix) in SHIM_TARGETS.items():
        pkg_dir = PACKAGE_ROOT / pkg_name
        assert pkg_dir.is_dir(), pkg_name
        actual_files = {
            path.name
            for path in pkg_dir.iterdir()
            if path.name != "__pycache__"
        }
        assert actual_files == expected_files, f"{pkg_name}: {actual_files}"
        assert not (pkg_dir / "README.md").exists()

        for py_name in expected_files:
            py_path = pkg_dir / py_name
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
            functions = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            assert classes == [], f"{pkg_name}/{py_name} defines classes: {classes}"
            assert set(functions) <= {"__getattr__", "__dir__"}, (
                f"{pkg_name}/{py_name} defines logic functions: {functions}"
            )
            imported = _resolved_imports(py_path)
            assert any(
                mod == target_prefix or mod.startswith(target_prefix + ".")
                for mod in imported
            ), f"{pkg_name}/{py_name} does not delegate to {target_prefix}: {imported}"


def test_agy_and_dsh_canonical_surfaces_and_compat_launcher(tmp_path: Path) -> None:
    cmd = agy.build_antigravity_launchd_command(
        goal_id="g1",
        agent_id="a1",
        conversation_id="c1",
        resolve_local_executable=False,
    )
    assert cmd[3:6] == ["-m", "loopx.hosts.agy.host_runtime", "install-launchd"]

    hooks = agy.build_antigravity_hooks_template(
        goal_id="g1",
        agent_id="a1",
        conversation_id="c1",
        resolve_local_executable=False,
    )
    assert "loopx.hosts.agy.host_runtime" in hooks["loopx"]["Stop"][0]["command"]
    assert "agy_goal_mode" not in hooks["loopx"]["Stop"][0]["command"]

    plist = build_antigravity_launchd_plist(
        goal_id="g1",
        agent_id="a1",
        conversation_id="c1",
        state_dir=str(tmp_path / "watchdog"),
        log_dir=str(tmp_path / "watchdog-logs"),
        resolve_local_executable=False,
    )
    prog_args = plist["ProgramArguments"]
    assert isinstance(prog_args, list)
    assert prog_args[3:6] == [
        "-m",
        "loopx.hosts.agy.host_runtime",
        "heartbeat-tick",
    ]
    assert dsh.ADAPTER_MODULE == "loopx.hosts.dsh"

    runner = REPO_ROOT / "tests" / "dsh_goal_mode_fake_runner.py"
    request_json = json.dumps(_signed_request())
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    launcher = REPO_ROOT / "scripts" / "dsh_turn_host_adapter.py"
    compat_run = subprocess.run(
        [sys.executable, str(launcher), "--dsh-runner", str(runner)],
        input=request_json,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compat_run.returncode == 0, compat_run.stderr
    assert json.loads(compat_run.stdout)["result_kind"] == "validated_progress"

    spec = importlib.util.spec_from_file_location(
        "batch3_core_prepare", REPO_ROOT / "packages" / "loopx-core" / "prepare.py"
    )
    assert spec is not None and spec.loader is not None
    prepare_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare_mod)

    staged = prepare_mod.prepare_core_source(tmp_path / "core-stage")
    for rel in (
        "loopx/agy_goal_mode/__init__.py",
        "loopx/agy_goal_mode/host_runtime.py",
        "loopx/agy_goal_mode/launchd_watchdog.py",
        "loopx/dsh_goal_mode/__init__.py",
        "loopx/dsh_goal_mode/__main__.py",
        "loopx/dsh_goal_mode/host_failure_map.py",
        "loopx/dsh_goal_mode/turn_host_adapter.py",
        "loopx/hosts/agy/__init__.py",
        "loopx/hosts/agy/README.md",
        "loopx/hosts/agy/host_runtime.py",
        "loopx/hosts/agy/launchd_watchdog.py",
        "loopx/hosts/dsh/__init__.py",
        "loopx/hosts/dsh/__main__.py",
        "loopx/hosts/dsh/README.md",
        "loopx/hosts/dsh/host_failure_map.py",
        "loopx/hosts/dsh/turn_host_adapter.py",
    ):
        assert (staged / rel).is_file(), rel


def _build_pre_consolidation_stub_wheel(out_dir: Path) -> Path:
    meta = "loopx-0.0.0.dist-info"
    contents = {
        "loopx/__init__.py": b'__version__ = "0.0.0"\n',
        "loopx/agy_goal_mode/__init__.py": b"",
        "loopx/agy_goal_mode/host_runtime.py": b"def main() -> int:\n    return 0\n",
        "loopx/agy_goal_mode/launchd_watchdog.py": b"",
        "loopx/dsh_goal_mode/__init__.py": b"",
        "loopx/dsh_goal_mode/__main__.py": b"def main() -> int:\n    return 0\n",
        "loopx/dsh_goal_mode/host_failure_map.py": b"",
        "loopx/dsh_goal_mode/turn_host_adapter.py": b"def main() -> int:\n    return 0\n",
        f"{meta}/METADATA": b"Metadata-Version: 2.1\nName: loopx\nVersion: 0.0.0\n",
        f"{meta}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    contents[f"{meta}/RECORD"] = "".join(
        f"{name},,\n" for name in [*contents, f"{meta}/RECORD"]
    ).encode("utf-8")
    wheel_path = out_dir / "loopx-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return wheel_path


def test_isolated_wheel_preserves_legacy_hooks_and_dsh_entrypoints(
    tmp_path: Path,
) -> None:
    pre_wheel = _build_pre_consolidation_stub_wheel(tmp_path)
    wheel = _build_pure_loopx_wheel(tmp_path)
    venv_dir = tmp_path / "isolated-venv"
    venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(venv_dir)
    py_bin = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    subprocess.run(
        [
            str(py_bin),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            str(pre_wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run(
        [
            str(py_bin),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            "--upgrade",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    resource_probe = subprocess.run(
        [
            str(py_bin),
            "-I",
            "-c",
            (
                "import importlib.resources as r\n"
                "from loopx.agy_goal_mode import AGY_HOST_CAPABILITIES as LEGACY_AGY\n"
                "from loopx.dsh_goal_mode import DSH_HOST_CAPABILITIES as LEGACY_DSH\n"
                "from loopx.dsh_goal_mode.host_failure_map import classify_dsh_failure as LEGACY_CLASSIFY\n"
                "from loopx.dsh_goal_mode.turn_host_adapter import DSH_HOST_CAPABILITIES as LEGACY_DSH_ADAPTER\n"
                "from loopx.hosts.agy import AGY_HOST_CAPABILITIES as CANONICAL_AGY\n"
                "from loopx.hosts.dsh.host_failure_map import classify_dsh_failure as CANONICAL_CLASSIFY\n"
                "from loopx.hosts.dsh.turn_host_adapter import DSH_HOST_CAPABILITIES as CANONICAL_DSH\n"
                "assert LEGACY_AGY is CANONICAL_AGY\n"
                "assert LEGACY_CLASSIFY is CANONICAL_CLASSIFY\n"
                "assert LEGACY_DSH is CANONICAL_DSH and LEGACY_DSH_ADAPTER is CANONICAL_DSH\n"
                "assert 'Antigravity' in r.files('loopx.hosts.agy').joinpath('README.md').read_text(encoding='utf-8')\n"
                "assert 'DeepSeek Harness' in r.files('loopx.hosts.dsh').joinpath('README.md').read_text(encoding='utf-8')\n"
            ),
        ],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resource_probe.returncode == 0, resource_probe.stderr

    # Legacy Stop hook command installed before consolidation must keep working in-place.
    stop_event = json.dumps(
        {
            "conversationId": "conv-1",
            "terminationReason": "USER_CANCELED",
            "fullyIdle": True,
            "lastUserInput": "Continue",
            "error": "",
        }
    )
    agy_outputs: list[dict[str, object]] = []
    for mod in ("loopx.agy_goal_mode.host_runtime", "loopx.hosts.agy.host_runtime"):
        proc = subprocess.run(
            [
                str(py_bin),
                "-I",
                "-E",
                "-P",
                "-m",
                mod,
                "stop-hook",
                "--goal-id",
                "g1",
                "--agent-id",
                "a1",
                "--conversation-id",
                "conv-1",
                "--state-dir",
                str(work_dir / "agy-state"),
            ],
            input=stop_event,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"{mod}: {proc.stderr}"
        agy_outputs.append(json.loads(proc.stdout))
    assert agy_outputs[0] == {} and agy_outputs[0] == agy_outputs[1]

    # Legacy DSH package and direct submodule entrypoints must emit the exact same typed result.
    call_log = work_dir / "runner_calls.log"
    runner = work_dir / "fake_dsh_runner.py"
    runner.write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"CALL_LOG = Path({str(call_log)!r})\n"
        "def run_dsh_turn(**_kwargs):\n"
        "    with CALL_LOG.open('a', encoding='utf-8') as handle:\n"
        "        handle.write('called\\n')\n"
        "    return json.dumps({\n"
        "        'result_kind': 'validated_progress',\n"
        "        'classification': 'fake dsh typed result',\n"
        "        'summary': 'one bounded fake work segment',\n"
        "        'recommended_action': 'validate the fake typed result',\n"
        "        'next_action': 'run the LoopX validator',\n"
        "        'vision_unchanged_reason': 'the fake runner never replans',\n"
        "    })\n",
        encoding="utf-8",
    )
    request_json = json.dumps(_signed_request())
    dsh_modules = (
        "loopx.dsh_goal_mode",
        "loopx.dsh_goal_mode.turn_host_adapter",
        "loopx.hosts.dsh",
        "loopx.hosts.dsh.turn_host_adapter",
    )
    dsh_outputs: list[dict[str, object]] = []
    for mod in dsh_modules:
        help_proc = subprocess.run(
            [str(py_bin), "-I", "-m", mod, "--help"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert help_proc.returncode == 0, f"{mod} --help: {help_proc.stderr}"
        assert "--dsh-runner" in help_proc.stdout
        proc = subprocess.run(
            [
                str(py_bin),
                "-I",
                "-m",
                mod,
                "--workspace",
                str(work_dir),
                "--dsh-runner",
                str(runner),
            ],
            input=request_json,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"{mod}: {proc.stderr}"
        assert proc.stdout.strip(), f"{mod} emitted empty stdout"
        dsh_outputs.append(json.loads(proc.stdout))
    assert dsh_outputs[0]["result_kind"] == "validated_progress"
    assert all(output == dsh_outputs[0] for output in dsh_outputs[1:])
    assert call_log.read_text(encoding="utf-8").splitlines() == ["called"] * len(dsh_modules)
