"""Contracts protecting Batch 2 host consolidation and thin compatibility shims."""

from __future__ import annotations

import ast
import importlib
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
from loopx.hosts.kunluncode import cli as kunlun_cli

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "loopx"

SHIM_TARGETS: dict[str, tuple[set[str], str]] = {
    "claude_goal_mode": (
        {
            "__init__.py",
            "hooks/goal_policy.py",
            "hooks/goal_state.py",
            "hooks/stdio_utf8.py",
            "mcp/loopx_mcp.py",
            "scripts/connect.py",
            "scripts/goalmode_cmd.py",
            "scripts/install.py",
            "statusline/goal_status.py",
        },
        "loopx.hosts.claude",
    ),
    "kunluncode_goal_mode": (
        {
            "__init__.py",
            "app_server.py",
            "cli.py",
            "context.py",
            "control_plane.py",
            "guards.py",
            "runtime.py",
            "server.py",
        },
        "loopx.hosts.kunluncode",
    ),
    "zcode_goal_mode": (
        {
            "__init__.py",
            "api.py",
            "bridge.py",
            "diagnostics.py",
        },
        "loopx.hosts.zcode",
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
        if file_path.suffix not in {".py", ".json", ".ts", ".toml", ".md", ".png"}:
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


def _build_pre_consolidation_stub_wheel(out_dir: Path) -> Path:
    meta = "loopx-0.0.0.dist-info"
    contents: dict[str, bytes] = {
        "loopx/__init__.py": b'__version__ = "0.0.0"\n',
        f"{meta}/METADATA": b"Metadata-Version: 2.1\nName: loopx\nVersion: 0.0.0\n",
        f"{meta}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    for pkg_name, (rel_files, _) in SHIM_TARGETS.items():
        for rel in rel_files:
            contents[f"loopx/{pkg_name}/{rel}"] = (
                b"raise RuntimeError('stale pre-consolidation stub was not replaced')\n"
                if rel != "__init__.py"
                else b"STALE = True\n"
            )
    contents[f"{meta}/RECORD"] = "".join(
        f"{name},,\n" for name in [*contents, f"{meta}/RECORD"]
    ).encode("utf-8")
    wheel_path = out_dir / "loopx-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return wheel_path


def test_batch2_hosts_packages_and_pyproject_package_data() -> None:
    for host_dir in ("claude", "kunluncode", "zcode"):
        assert (PACKAGE_ROOT / "hosts" / host_dir / "__init__.py").is_file()
        assert (PACKAGE_ROOT / "hosts" / host_dir / "README.md").is_file()

    for asset in (
        "claude/.claude-plugin/plugin.json",
        "claude/commands/loopx.md",
        "claude/hooks/hooks.json",
        "claude/hooks/goal_policy.py",
        "claude/hooks/goal_state.py",
        "claude/hooks/stdio_utf8.py",
        "claude/mcp/loopx_mcp.py",
        "claude/scripts/connect.py",
        "claude/scripts/goalmode_cmd.py",
        "claude/scripts/install.py",
        "claude/settings.example.json",
        "claude/statusline/goal_status.py",
        "kunluncode/app_server.py",
        "kunluncode/cli.py",
        "kunluncode/context.py",
        "kunluncode/control_plane.py",
        "kunluncode/guards.py",
        "kunluncode/runtime.py",
        "kunluncode/server.py",
        "zcode/api.py",
        "zcode/app-server.ts",
        "zcode/bridge.py",
        "zcode/cli.ts",
        "zcode/contract.json",
        "zcode/contract.ts",
        "zcode/diagnostics.py",
        "zcode/guard.ts",
        "zcode/images/native-error-mobile.png",
        "zcode/images/native-quota-desktop.png",
        "zcode/runtime.ts",
    ):
        assert (PACKAGE_ROOT / "hosts" / asset).is_file(), asset

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["loopx-kunluncode"] == "loopx.hosts.kunluncode.cli:main"
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    for legacy_pkg in ("loopx.claude_goal_mode", "loopx.kunluncode_goal_mode", "loopx.zcode_goal_mode"):
        assert legacy_pkg not in package_data
    assert package_data["loopx.hosts.claude"] == [
        "README.md",
        ".claude-plugin/*.json",
        "commands/*.md",
        "hooks/*.json",
        "settings.example.json",
    ]
    assert package_data["loopx.hosts.kunluncode"] == ["README.md"]
    assert package_data["loopx.hosts.zcode"] == [
        "README.md",
        "*.ts",
        "contract.json",
        "images/*.png",
    ]


def test_no_internal_loopx_production_module_imports_legacy_batch2_shims() -> None:
    legacy_prefixes = (
        "loopx.claude_goal_mode",
        "loopx.kunluncode_goal_mode",
        "loopx.zcode_goal_mode",
    )
    offenders: list[tuple[str, str]] = []
    for py_path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = py_path.relative_to(PACKAGE_ROOT).as_posix()
        if rel.startswith(("claude_goal_mode/", "kunluncode_goal_mode/", "zcode_goal_mode/")):
            continue
        for imported in sorted(_resolved_imports(py_path)):
            for prefix in legacy_prefixes:
                if imported == prefix or imported.startswith(prefix + "."):
                    offenders.append((rel, imported))
    assert offenders == [], (
        f"Internal modules must import loopx.hosts.{{claude,kunluncode,zcode}}: {offenders}"
    )


def test_legacy_batch2_packages_are_thin_forwarding_shims_only(tmp_path: Path) -> None:
    for pkg_name, (expected_files, target_prefix) in SHIM_TARGETS.items():
        pkg_dir = PACKAGE_ROOT / pkg_name
        assert pkg_dir.is_dir(), pkg_name
        actual_files = {
            path.relative_to(pkg_dir).as_posix()
            for path in pkg_dir.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        assert actual_files == expected_files, f"{pkg_name}: {actual_files}"

        for rel_name in sorted(expected_files):
            py_path = pkg_dir / rel_name
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
            functions = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            assert classes == [], f"{pkg_name}/{rel_name} defines classes: {classes}"
            assert set(functions) <= {"__getattr__", "__dir__"}, (
                f"{pkg_name}/{rel_name} defines logic functions: {functions}"
            )
            imported = _resolved_imports(py_path)
            assert any(
                mod == target_prefix or mod.startswith(target_prefix + ".")
                for mod in imported
            ), f"{pkg_name}/{rel_name} does not delegate to {target_prefix}: {imported}"

    from loopx.claude_goal_mode import CLAUDE_HOST_CAPABILITIES as legacy_claude_cap
    from loopx.hosts.claude import CLAUDE_HOST_CAPABILITIES as canonical_claude_cap
    from loopx.kunluncode_goal_mode import DEFAULT_AGENT_ID as legacy_kunlun_agent
    from loopx.hosts.kunluncode import DEFAULT_AGENT_ID as canonical_kunlun_agent
    from loopx.zcode_goal_mode import zcode_home as legacy_zcode_home
    from loopx.hosts.zcode import zcode_home as canonical_zcode_home

    assert legacy_claude_cap is canonical_claude_cap
    assert legacy_kunlun_agent == canonical_kunlun_agent
    assert legacy_zcode_home is canonical_zcode_home

    # Direct file-path execution of runnable legacy shims without PYTHONPATH works identically.
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    hook_event = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash"})
    for script_path in (
        PACKAGE_ROOT / "hosts" / "claude" / "hooks" / "goal_policy.py",
        PACKAGE_ROOT / "claude_goal_mode" / "hooks" / "goal_policy.py",
    ):
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=tmp_path,
            env=env,
            input=hook_event,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"{script_path}: {proc.stderr}"
        assert json.loads(proc.stdout) == {}

    for help_script in (
        PACKAGE_ROOT / "claude_goal_mode" / "scripts" / "install.py",
        PACKAGE_ROOT / "claude_goal_mode" / "scripts" / "connect.py",
        PACKAGE_ROOT / "kunluncode_goal_mode" / "cli.py",
        PACKAGE_ROOT / "zcode_goal_mode" / "bridge.py",
    ):
        proc = subprocess.run(
            [sys.executable, str(help_script), "--help"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"{help_script} --help: {proc.stderr}"


def test_kunluncode_source_root_and_core_prepare_stage_canonical_and_shims(
    tmp_path: Path,
) -> None:
    assert kunlun_cli._source_root() == REPO_ROOT

    spec = importlib.util.spec_from_file_location(
        "batch2_core_prepare", REPO_ROOT / "packages" / "loopx-core" / "prepare.py"
    )
    assert spec is not None and spec.loader is not None
    prepare_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare_mod)

    staged = prepare_mod.prepare_core_source(tmp_path / "core-stage")
    for rel in (
        "loopx/hosts/claude/__init__.py",
        "loopx/hosts/claude/hooks/goal_policy.py",
        "loopx/hosts/kunluncode/cli.py",
        "loopx/hosts/zcode/bridge.py",
        "loopx/claude_goal_mode/__init__.py",
        "loopx/claude_goal_mode/hooks/goal_policy.py",
        "loopx/claude_goal_mode/scripts/install.py",
        "loopx/kunluncode_goal_mode/__init__.py",
        "loopx/kunluncode_goal_mode/cli.py",
        "loopx/zcode_goal_mode/__init__.py",
        "loopx/zcode_goal_mode/bridge.py",
    ):
        assert (staged / rel).is_file(), rel


def test_claude_direct_scripts_resolve_repo_and_plugin_root_without_pythonpath(
    tmp_path: Path,
) -> None:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    expected_plugin = PACKAGE_ROOT / "hosts" / "claude"

    for base_dir in (expected_plugin, PACKAGE_ROOT / "claude_goal_mode"):
        for rel, check_plugin in (
            ("hooks/goal_state.py", False),
            ("mcp/loopx_mcp.py", False),
            ("scripts/install.py", True),
            ("scripts/connect.py", True),
        ):
            script = base_dir / rel
            assert script.is_file(), script
            checks = [
                "import importlib.util",
                "from pathlib import Path",
                f"spec = importlib.util.spec_from_file_location('claude_probe', {str(script)!r})",
                "assert spec is not None and spec.loader is not None",
                "mod = importlib.util.module_from_spec(spec)",
                "spec.loader.exec_module(mod)",
                f"assert mod.REPO_ROOT.resolve() == Path({str(REPO_ROOT)!r}).resolve()",
            ]
            if check_plugin:
                checks.append(
                    f"assert mod.PLUGIN_ROOT.resolve() == Path({str(expected_plugin)!r}).resolve()"
                )
            completed = subprocess.run(
                [sys.executable, "-c", "\n".join(checks)],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert completed.returncode == 0, f"{script}: {completed.stderr}{completed.stdout}"


def test_isolated_wheel_upgrade_preserves_batch2_shims_and_scripts(
    tmp_path: Path,
) -> None:
    pre_wheel = _build_pre_consolidation_stub_wheel(tmp_path)
    wheel = _build_pure_loopx_wheel(tmp_path)
    venv_dir = tmp_path / "isolated-venv"
    venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(venv_dir)
    py_bin = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    for cmd in (
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
    ):
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    probe = subprocess.run(
        [
            str(py_bin),
            "-I",
            "-c",
            (
                "import importlib.resources as r\n"
                "from loopx.claude_goal_mode import CLAUDE_HOST_CAPABILITIES as LC\n"
                "from loopx.claude_goal_mode.hooks.goal_policy import decide as LD\n"
                "from loopx.claude_goal_mode.hooks.goal_state import active_context as LA\n"
                "from loopx.claude_goal_mode.hooks.stdio_utf8 import pin_utf8_stdio as LP\n"
                "from loopx.claude_goal_mode.scripts.install import _rewrite_legacy_claude_command as LR\n"
                "from loopx.claude_goal_mode.statusline.goal_status import _clip as LCLIP\n"
                "from loopx.hosts.claude import CLAUDE_HOST_CAPABILITIES as CC\n"
                "from loopx.hosts.claude.hooks.goal_policy import decide as CD\n"
                "from loopx.hosts.claude.hooks.goal_state import active_context as CA\n"
                "from loopx.hosts.claude.hooks.stdio_utf8 import pin_utf8_stdio as CP\n"
                "from loopx.hosts.claude.scripts.install import _rewrite_legacy_claude_command as CR\n"
                "from loopx.hosts.claude.statusline.goal_status import _clip as CCLIP\n"
                "from loopx.kunluncode_goal_mode import DEFAULT_AGENT_ID as LK_AGENT\n"
                "from loopx.kunluncode_goal_mode.cli import main as LK_MAIN\n"
                "from loopx.kunluncode_goal_mode.runtime import run_native_goal as LK_RUN\n"
                "from loopx.hosts.kunluncode import DEFAULT_AGENT_ID as CK_AGENT\n"
                "from loopx.hosts.kunluncode.cli import main as CK_MAIN\n"
                "from loopx.hosts.kunluncode.runtime import run_native_goal as CK_RUN\n"
                "from loopx.zcode_goal_mode import zcode_home as LZ_HOME\n"
                "from loopx.zcode_goal_mode.bridge import _validate_main as LZ_VAL\n"
                "from loopx.zcode_goal_mode.diagnostics import INTERFACE_PATTERNS as LZ_PATTERNS\n"
                "from loopx.hosts.zcode import zcode_home as CZ_HOME\n"
                "from loopx.hosts.zcode.bridge import _validate_main as CZ_VAL\n"
                "from loopx.hosts.zcode.diagnostics import INTERFACE_PATTERNS as CZ_PATTERNS\n"
                "assert LC is CC and LD is CD and LA is CA and LP is CP and LR is CR and LCLIP is CCLIP\n"
                "assert LK_AGENT == CK_AGENT and LK_MAIN is CK_MAIN and LK_RUN is CK_RUN\n"
                "assert LZ_HOME is CZ_HOME and LZ_VAL is CZ_VAL and LZ_PATTERNS is CZ_PATTERNS\n"
                "assert '/loopx' in r.files('loopx.hosts.claude').joinpath('commands/loopx.md').read_text(encoding='utf-8')\n"
                "assert 'PreToolUse' in r.files('loopx.hosts.claude').joinpath('hooks/hooks.json').read_text(encoding='utf-8')\n"
                "assert 'actions' in r.files('loopx.hosts.zcode').joinpath('contract.json').read_text(encoding='utf-8')\n"
                "assert 'KunlunCode' in r.files('loopx.hosts.kunluncode').joinpath('README.md').read_text(encoding='utf-8')\n"
            ),
        ],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr

    site_pkg_raw = subprocess.check_output(
        [str(py_bin), "-I", "-c", "import loopx, pathlib; print(pathlib.Path(loopx.__file__).parent)"],
        cwd=work_dir,
        text=True,
    ).strip()
    site_loopx = Path(site_pkg_raw)

    # Direct file-path execution of legacy scripts in the upgraded venv (as Claude hooks/statusline invoke).
    for shim_rel, canonical_rel in (
        ("claude_goal_mode/hooks/goal_policy.py", "hosts/claude/hooks/goal_policy.py"),
        ("claude_goal_mode/statusline/goal_status.py", "hosts/claude/statusline/goal_status.py"),
    ):
        outputs: list[str] = []
        for rel in (shim_rel, canonical_rel):
            res = subprocess.run(
                [str(py_bin), "-I", str(site_loopx / rel)],
                cwd=work_dir,
                input=json.dumps({"cwd": str(work_dir), "tool_name": "Bash"}),
                capture_output=True,
                text=True,
                check=False,
            )
            assert res.returncode == 0, f"{rel}: {res.stderr}"
            outputs.append(res.stdout)
        assert outputs[0] == outputs[1]

    for args, shim_rel, canonical_rel in (
        (["--help"], "claude_goal_mode/scripts/install.py", "hosts/claude/scripts/install.py"),
        (["--help"], "claude_goal_mode/scripts/connect.py", "hosts/claude/scripts/connect.py"),
        (["--help"], "kunluncode_goal_mode/cli.py", "hosts/kunluncode/cli.py"),
        (["--validate-binding"], "zcode_goal_mode/bridge.py", "hosts/zcode/bridge.py"),
    ):
        outputs = []
        for rel in (shim_rel, canonical_rel):
            stdin_payload = "{}" if "--validate-binding" in args else None
            res = subprocess.run(
                [str(py_bin), "-I", str(site_loopx / rel), *args],
                cwd=work_dir,
                input=stdin_payload,
                capture_output=True,
                text=True,
                check=False,
            )
            expected_rc = 1 if "--validate-binding" in args else 0
            assert res.returncode == expected_rc, f"{rel}: rc={res.returncode} stderr={res.stderr}"
            if "--validate-binding" in args:
                payload = json.loads(res.stdout)
                assert payload["error_code"] == "invalid_zcode_goal_binding"
            outputs.append(res.stdout)
        if "--validate-binding" in args:
            assert outputs[0] == outputs[1]
        else:
            assert all("usage:" in out for out in outputs)

    for args, shim_mod, canonical_mod in (
        (["--help"], "loopx.claude_goal_mode.scripts.install", "loopx.hosts.claude.scripts.install"),
        (["--help"], "loopx.kunluncode_goal_mode.cli", "loopx.hosts.kunluncode.cli"),
        (["--validate-binding"], "loopx.zcode_goal_mode.bridge", "loopx.hosts.zcode.bridge"),
    ):
        mod_outputs: list[str] = []
        for mod in (shim_mod, canonical_mod):
            stdin_payload = "{}" if "--validate-binding" in args else None
            res = subprocess.run(
                [str(py_bin), "-I", "-m", mod, *args],
                cwd=work_dir,
                input=stdin_payload,
                capture_output=True,
                text=True,
                check=False,
            )
            expected_rc = 1 if "--validate-binding" in args else 0
            assert res.returncode == expected_rc, f"{mod}: rc={res.returncode} stderr={res.stderr}"
            mod_outputs.append(res.stdout)
        if "--validate-binding" in args:
            assert mod_outputs[0] == mod_outputs[1]
        else:
            assert all("usage:" in out for out in mod_outputs)
