#!/usr/bin/env python3
"""Run bounded, source-pinned candidate quality checks before measurement."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

BASE = "04c5799b88f6c4cf12a224b7fa679f272e1e794e"
HEAD = "e42c536e609ba383217a3e83b8cf07c72542944c"
CAP_SECONDS = 45 * 60
RESERVE_SECONDS = 5 * 60
NATIVE_PACKAGE = "@openai/codex@0.163.0-alpha.11.1"
NODE_TESTS = (
    "tests/control_plane_ts/context_delivery.test.ts",
    "tests/control_plane_ts/progressive_context.test.ts",
    "tests/control_plane_ts/turn_envelope.test.ts",
    "tests/control_plane_ts/interaction_contract.test.ts",
    "tests/control_plane_ts/turn_loop_controller.test.ts",
)
PYTHON_TESTS = (
    "tests/test_turn_envelope.py",
    "tests/test_loop_turn_loop_controller.py",
    "tests/control_plane/test_context_delivery_cli.py",
    "tests/control_plane/test_progressive_native_context.py",
    "tests/control_plane/test_saved_context_project.py",
    "tests/control_plane/test_turn_envelope_settlement.py",
    "tests/control_plane/test_turn_envelope_capability_facts.py",
    "tests/control_plane/test_turn_envelope_budget_warning.py",
    "tests/control_plane/test_codex_context_resume.py",
    "tests/control_plane/test_codex_context_lifecycle.py",
    "tests/control_plane/test_quota_settlement_cli.py",
    "tests/cli_commands/test_quota_turn_envelope_validation_failure.py",
    "tests/capabilities/test_benchmark_token_capacity.py",
)
SAFE_ENV_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "LD_LIBRARY_PATH",
    "CI", "GITHUB_ACTIONS", "GITHUB_WORKSPACE", "RUNNER_TEMP", "RUNNER_TOOL_CACHE",
    "PYTHONUNBUFFERED", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "LOOPX_USAGE_PING", "TMPDIR", "TEMP", "TMP",
)
NATIVE_FIXTURE_ENV_KEYS = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")


def safe_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}


def root_for(script: Path) -> Path:
    for candidate in (script.parent, script.parent.parent, script.parent.parent / "harness"):
        if (candidate / "evaluate.py").is_file():
            return candidate
    raise SystemExit("quality_gate.py must be beside the flattened harness or inside its quality/ directory")


def plan(root: Path, config: dict | None = None, script: Path | None = None) -> list[dict]:
    config = config or json.loads((root / "evaluation_config.json").read_text())
    script = script or root / "quality_gate.py"
    sources = {"base": Path(config["baseline_source"]), "head": Path(config["candidate_source"])}
    if config["baseline"] != BASE or config["candidate_head"] != HEAD:
        raise SystemExit("quality gate source pins differ from the frozen protocol")
    steps: list[dict] = []

    def add(name: str, arm: str, argv: list[str], timeout: int = 300, env: dict | None = None):
        steps.append({"name": name, "arm": arm, "cwd": str(sources[arm]), "argv": argv,
                      "timeout_seconds": timeout, "env": env or {}})

    py = ".venv/bin/python"
    node = ["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
            "--test", "--test-concurrency=4"]
    add("ruff", "head", [py, "-m", "ruff", "check", "tests", "loopx/canary", "loopx/control_plane",
                            "loopx/domain_packs", "loopx/presentation"], 600)
    add("mypy", "head", [py, "-m", "mypy"], 900)
    add("control-plane-tsc", "head", ["npm", "run", "typecheck:control-plane"], 900)
    add("affected-node-tests", "head", node + list(NODE_TESTS), 900)
    add("affected-python-tests", "head", [py, "-m", "pytest", "-q", *PYTHON_TESTS], 1200)
    add("canary-premerge", "head", [".venv/bin/loopx", "canary", "premerge", "--from-git-diff",
                                      "--git-diff-base", BASE], 1200)

    # Use the existing fixture creator and CLI preflight; expand only the copied
    # quality config, never the immutable protocol or the measurement job config.
    add("functional-preflight", "head", [py, str(script), "--functional-preflight"], 1200)

    add("baseline-wheel-build", "base", ["uv", "build", "--wheel", "--out-dir", str(root / "quality-baseline-wheelhouse")], 900)
    add("wheel-build", "head", ["uv", "build", "--wheel", "--out-dir", str(root / "quality-wheelhouse")], 900)
    add("wheel-byte-check", "head", [py, str(script), "--wheel-byte-check"], 300)
    add("installed-origin-recorder", "head", [py, str(script), "--installed-origin-recorder"], 120)

    native_dir = root / "quality-native-sdk"
    add("native-sdk-install", "head", ["npm", "install", "--prefix", str(native_dir), "--no-save", NATIVE_PACKAGE], 900)
    for arm in ("base", "head", "installed"):
        env = {"LOOPX_NATIVE_CHECKPOINT_BIN": str(native_dir / "node_modules/.bin/codex"),
               "CODEX_HOME": str(root / f"quality-codex-home-{arm}"),
               "PYTHONPATH": str(sources["head" if arm == "installed" else arm] / "tests/control_plane")}
        interpreter = str(root / ".quality-wheel-venv/bin/python") if arm == "installed" else py
        if arm == "installed":
            env["LOOPX_NATIVE_CONTEXT_CLI"] = str(root / ".quality-wheel-venv/bin/loopx")
            argv = [interpreter, "-c", "import pytest,sys;sys.exit(pytest.main(sys.argv[1:]))",
                    "-q", str(root / "qa_native/test_native_current_oracle.py")]
        else:
            argv = [interpreter, "-m", "pytest", "-q", str(root / "qa_native/test_native_current_oracle.py")]
        add(f"native-ssh-goal-{arm}", "head" if arm == "installed" else arm, argv, 900, env)

    return steps


def self_check() -> None:
    config = {"baseline": BASE, "candidate_head": HEAD,
              "baseline_source": "/private/source/base", "candidate_source": "/private/source/head"}
    private_root = Path("/private/candidate-qualification/harness")
    private_script = Path("/private/candidate-qualification/quality/quality_gate.py")
    flat_root = Path("/private/flattened-harness")
    flat_script = flat_root / "quality_gate.py"
    private_steps = plan(private_root, config, private_script)
    flat_steps = plan(flat_root, config, flat_script)
    expected = {"ruff", "mypy", "control-plane-tsc", "affected-node-tests", "affected-python-tests",
                "canary-premerge", "functional-preflight", "baseline-wheel-build", "wheel-build", "wheel-byte-check",
                "native-sdk-install", "native-ssh-goal-base", "native-ssh-goal-head", "native-ssh-goal-installed",
                "installed-origin-recorder"}
    assert len(NODE_TESTS) == 5 and len(set(NODE_TESTS)) == 5
    assert all(name.endswith(".test.ts") for name in NODE_TESTS)
    assert all(Path(name).is_relative_to("tests") for name in PYTHON_TESTS)
    assert BASE != HEAD and len(BASE) == len(HEAD) == 40
    assert CAP_SECONDS == 2700 and NATIVE_PACKAGE.endswith("0.163.0-alpha.11.1")
    assert RESERVE_SECONDS == 300
    assert {step["name"] for step in private_steps} == expected
    assert {step["name"] for step in flat_steps} == expected
    for steps, script in ((private_steps, private_script), (flat_steps, flat_script)):
        helpers = [step for step in steps if step["name"] in {"functional-preflight", "wheel-byte-check",
                                                               "installed-origin-recorder"}]
        assert all(str(script) in step["argv"] for step in helpers)
        assert all(step["timeout_seconds"] > 0 for step in steps)
    installed = next(step for step in flat_steps if step["name"] == "native-ssh-goal-installed")
    assert installed["env"]["LOOPX_NATIVE_CONTEXT_CLI"] == "/private/flattened-harness/.quality-wheel-venv/bin/loopx"
    provenance_self_check()
    wheel_inventory_self_check()
    print(json.dumps({"self_check": "passed", "node_tests": list(NODE_TESTS),
                      "python_tests": list(PYTHON_TESTS),
                      "layouts_checked": [str(private_root), str(flat_root)],
                      "steps": [step["name"] for step in flat_steps]}, indent=2))


def run_one(step: dict, root: Path, logs: Path, job_started_epoch: float) -> dict:
    remaining = job_started_epoch + CAP_SECONDS - RESERVE_SECONDS - time.time()
    timeout = min(step["timeout_seconds"], max(1, int(remaining)))
    record = {"name": step["name"], "cwd": step["cwd"], "argv": step["argv"],
              "timeout_seconds": timeout, "started_epoch": time.time()}
    if step["name"] == "native-ssh-goal-installed":
        record["installed_cli_path"] = step["env"]["LOOPX_NATIVE_CONTEXT_CLI"]
        record["native_launch_contract"] = {
            "test_cwd_is_head_checkout": True,
            "operator_pythonpath_inherited": False,
            "pytest_fixture_pythonpath": step["env"].get("PYTHONPATH"),
            "test_selects_installed_cli_override": True,
            "cli_argv_injects_source_pythonpath": False,
        }
    env = safe_environment()
    env.update(step["env"])
    env["PYTHONUNBUFFERED"] = "1"
    binary_dir = (root / ".quality-wheel-venv/bin" if step["name"] == "native-ssh-goal-installed"
                  else Path(step["cwd"]) / ".venv/bin")
    env["PATH"] = str(binary_dir) + os.pathsep + env.get("PATH", os.defpath)
    log = logs / step["name"]
    log.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(step["argv"], cwd=step["cwd"], env=env, capture_output=True,
                                timeout=timeout, check=False)
        (log / "stdout.bin").write_bytes(result.stdout)
        (log / "stderr.bin").write_bytes(result.stderr)
        record.update({"returncode": result.returncode, "timed_out": False,
                       "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr)})
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        (log / "stdout.bin").write_bytes(stdout if isinstance(stdout, bytes) else stdout.encode())
        (log / "stderr.bin").write_bytes(stderr if isinstance(stderr, bytes) else stderr.encode())
        record.update({"returncode": None, "timed_out": True,
                       "stdout_bytes": len(stdout), "stderr_bytes": len(stderr)})
    except OSError as exc:
        (log / "stdout.bin").write_bytes(b"")
        (log / "stderr.bin").write_bytes(b"")
        record.update({"returncode": 127, "timed_out": False, "failed_to_start": True,
                       "error_kind": type(exc).__name__, "stdout_bytes": 0, "stderr_bytes": 0})
    record["finished_epoch"] = time.time()
    (log / "metadata.json").write_text(json.dumps(record, indent=2) + "\n")
    if step["name"] == "native-ssh-goal-installed":
        marker = root / "quality-results/installed-cli-imports.jsonl"
        try:
            site = Path(subprocess.check_output([str(root / ".quality-wheel-venv/bin/python"), "-I", "-c",
                                                 "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                                cwd=logs, env=safe_environment(), text=True).strip()).resolve()
            origins = [json.loads(line) for line in marker.read_text().splitlines()] if marker.is_file() else []
            valid = bool(origins) and all(Path(row["origin"]).resolve().is_relative_to(site) for row in origins)
            record["installed_cli_origins_verified"] = valid
            if valid:
                record["installed_cli_import_records"] = origins
            else:
                record["returncode"] = 1
                record["error"] = "installed console-script child did not record only wheel-venv LoopX imports"
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            record["returncode"] = 1
            record["installed_cli_origins_verified"] = False
            record["error"] = repr(exc)
        (log / "metadata.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def functional_preflight(root: Path) -> None:
    config_path = root / "evaluation_config.json"
    original = config_path.read_bytes()
    config = json.loads(original)
    config["providers"], config["history_counts"], config["modes"] = ["file", "sqlite"], [0, 500], ["cold-daemon", "warm"]
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    source = Path(config["candidate_source"])
    python = source / ".venv/bin/python"
    try:
        env = safe_environment()
        subprocess.run([str(python), str(root / "evaluate.py"), "prepare"], cwd=source, env=env, check=True, timeout=180)
        subprocess.run([str(python), str(root / "evaluate.py"), "preflight"], cwd=source, env=env, check=True, timeout=900)
        subprocess.run([str(python), str(root / "complete_token_check.py")], cwd=source, env=env, check=True, timeout=300)
    finally:
        config_path.write_bytes(original)


def wheel_runtime_bytes(source: Path, wheel: Path) -> tuple[dict[str, str], list[str]]:
    manifests = list(source.glob("*.egg-info/SOURCES.txt"))
    if len(manifests) != 1:
        raise SystemExit("expected one setuptools source manifest from the completed wheel build")
    selected = {name for name in manifests[0].read_text().splitlines() if name.startswith("loopx/")}
    with zipfile.ZipFile(wheel) as archive:
        members = {name: archive.read(name) for name in archive.namelist() if name.startswith("loopx/") and not name.endswith("/")}
    if selected != set(members):
        raise SystemExit(json.dumps({"manifest_missing_from_wheel": sorted(selected - set(members)),
                                     "wheel_not_in_manifest": sorted(set(members) - selected)}))
    for name, content in members.items():
        if not (source / name).is_file() or (source / name).read_bytes() != content:
            raise SystemExit(f"wheel runtime byte mismatch: {name}")
    tracked = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z", "loopx"], text=False).split(b"\0")
    return ({name: hashlib.sha256(data).hexdigest() for name, data in members.items()},
            sorted(p.decode() for p in tracked if p and p.decode() not in selected))


def wheel_inventory_self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="c2-wheel-inventory-") as temporary:
        source = Path(temporary)
        (source / "loopx").mkdir()
        (source / "loopx/owner.py").write_bytes(b"VALUE = 1\n")
        (source / "loopx/example.txt").write_text("documented source example")
        (source / "fixture.egg-info").mkdir()
        (source / "fixture.egg-info/SOURCES.txt").write_text("loopx/owner.py\n")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "loopx"], check=True)
        wheel = source / "fixture.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("loopx/owner.py", b"VALUE = 1\n")
        members, omitted = wheel_runtime_bytes(source, wheel)
        assert set(members) == {"loopx/owner.py"} and omitted == ["loopx/example.txt"]
        for contents in ({}, {"loopx/owner.py": b"VALUE = 2\n"}):
            with zipfile.ZipFile(wheel, "w") as archive:
                for name, value in contents.items():
                    archive.writestr(name, value)
            try:
                wheel_runtime_bytes(source, wheel)
            except SystemExit:
                pass
            else:
                raise AssertionError("missing or modified packaged runtime was accepted")


def wheel_byte_check(root: Path) -> None:
    config = json.loads((root / "evaluation_config.json").read_text())
    source = Path(config["candidate_source"])
    wheels = list((root / "quality-wheelhouse").glob("*.whl"))
    base_wheels = list((root / "quality-baseline-wheelhouse").glob("*.whl"))
    if len(wheels) != 1 or len(base_wheels) != 1:
        raise SystemExit("expected one separately built wheel per source version")
    subprocess.run(["git", "-C", str(source), "diff", "--exit-code", BASE, HEAD, "--",
                    "pyproject.toml", "setup.py", "MANIFEST.in"], check=True)
    expected, omitted = wheel_runtime_bytes(source, wheels[0])
    base_expected, base_omitted = wheel_runtime_bytes(Path(config["baseline_source"]), base_wheels[0])
    if set(expected) != set(base_expected) or omitted != base_omitted:
        raise SystemExit("packaging-neutral C2 changed the selected runtime inventory or inherited omissions")
    changed = subprocess.check_output(["git", "-C", str(source), "diff", "--name-only", BASE, HEAD, "--", "loopx"], text=True).splitlines()
    if not {"loopx/entrypoint.py", *changed}.issubset(expected):
        raise SystemExit("wheel lacks the entrypoint or a changed C2 runtime owner")
    venv = root / ".quality-wheel-venv"
    env = safe_environment()
    if subprocess.check_output([sys.executable, "-c", "import platform; print(platform.python_version())"],
                               env=env, text=True).strip() != "3.13.12":
        raise SystemExit("wheel qualification requires Python 3.13.12")
    subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], env=env, check=True, timeout=120)
    subprocess.run(["uv", "pip", "install", "--python", str(venv / "bin/python"),
                    "-r", str(root / "requirements.txt")], env=env, check=True, timeout=600)
    subprocess.run(["uv", "pip", "install", "--python", str(venv / "bin/python"),
                    "--no-deps", str(wheels[0])], env=env, check=True, timeout=300)
    site = Path(subprocess.check_output([str(venv / "bin/python"), "-I", "-c",
        "import sysconfig; print(sysconfig.get_paths()['purelib'])"], cwd=root / "quality-results",
        env=env, text=True).strip()).resolve()
    installed = Path(subprocess.check_output([str(venv / "bin/python"), "-I", "-c",
        "from pathlib import Path; import loopx; print(Path(loopx.__file__).resolve())"],
        cwd=root / "quality-results", env=env, text=True).strip()).resolve()
    if not installed.is_relative_to(site):
        raise SystemExit(f"isolated wheel import resolved outside installed site-packages: {installed}")
    for name, digest in expected.items():
        installed_file = site / name
        if not installed_file.is_file() or hashlib.sha256(installed_file.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"installed product byte mismatch: {name}")
    print(json.dumps({"wheel": str(wheels[0]), "product_files": len(expected),
                      "installed_import": str(installed), "installed_bytes_match": True,
                      "inventory_source": "setuptools build manifest; matched independently built baseline wheel",
                      "inherited_tracked_files_not_selected": omitted,
                      "scope": "C2 installed runtime and packaging parity; inherited omissions are not fixed"}))


def installed_origin_recorder(root: Path) -> None:
    venv = root / ".quality-wheel-venv"
    site = subprocess.check_output([str(venv / "bin/python"), "-c",
                                    "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                   env=safe_environment(), text=True).strip()
    cli = (venv / "bin/loopx").resolve()
    marker = root / "quality-results/installed-cli-imports.jsonl"
    recorder = f'''import builtins, json, pathlib, sys
cli = pathlib.Path({str(cli)!r})
if sys.argv and pathlib.Path(sys.argv[0]).resolve() == cli:
    original_import = builtins.__import__
    def record_loopx_import(name, *args, **kwargs):
        result = original_import(name, *args, **kwargs)
        if name == "loopx" or name.startswith("loopx."):
            module = sys.modules.get(name)
            spec = getattr(module, "__spec__", None)
            origin = getattr(spec, "origin", None)
            if origin:
                row = {{"argv0": str(pathlib.Path(sys.argv[0]).resolve()),
                       "cwd": str(pathlib.Path.cwd()), "module": name, "origin": str(pathlib.Path(origin).resolve())}}
                with pathlib.Path({str(marker)!r}).open("a") as stream:
                    stream.write(json.dumps(row) + "\\n")
        return result
    builtins.__import__ = record_loopx_import
'''
    (Path(site) / "sitecustomize.py").write_text(recorder)


def provenance_self_check() -> None:
    import tempfile
    import venv

    with tempfile.TemporaryDirectory(prefix="c2-installed-origin-") as temporary:
        root = Path(temporary)
        source, env_root = root / "source", root / "installed-env"
        (source / "loopx").mkdir(parents=True)
        (source / "loopx/__init__.py").write_text("ORIGIN = 'source'\n")
        venv.EnvBuilder(with_pip=False).create(env_root)
        python = env_root / "bin/python"
        site = Path(subprocess.check_output([str(python), "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])"], text=True).strip())
        (site / "loopx").mkdir()
        (site / "loopx/__init__.py").write_text("ORIGIN = 'installed'\n")
        (site / "sitecustomize.py").write_text(f'''import builtins, json, pathlib, sys
cli = pathlib.Path({str((env_root / "bin/loopx").resolve())!r})
if pathlib.Path(sys.argv[0]).resolve() == cli:
    original = builtins.__import__
    def record(name, *args, **kwargs):
        result = original(name, *args, **kwargs)
        if name == "loopx":
            origin = pathlib.Path(sys.modules[name].__file__).resolve()
            pathlib.Path({str(root / "origin.jsonl")!r}).open("a").write(json.dumps({{"origin": str(origin), "cwd": str(pathlib.Path.cwd())}}) + "\\n")
        return result
    builtins.__import__ = record
''')
        cli = env_root / "bin/loopx"
        cli.write_text(f"#!{python}\nimport loopx; print(loopx.__file__)\n")
        cli.chmod(0o755)
        command = [str(cli), "--cli-bin", str(cli)]
        env = safe_environment()
        env["PYTHONPATH"] = str(source)
        env["LOOPX_NATIVE_CONTEXT_CLI"] = str(cli)
        child_env = {key: env[key] for key in NATIVE_FIXTURE_ENV_KEYS if key in env}
        result = subprocess.run(command, cwd=source, env=child_env, capture_output=True, text=True, check=True)
        rows = [json.loads(line) for line in (root / "origin.jsonl").read_text().splitlines()]
        assert rows and all(Path(row["origin"]).is_relative_to(site) and Path(row["cwd"]) == source
                            for row in rows)
        assert "PYTHONPATH" not in child_env
        reported = Path(result.stdout.strip()).resolve()
        assert reported.is_relative_to(site), {
            "reported": str(reported), "site": str(site), "source": str(source)}
        source_bin = source / ".venv/bin"
        source_bin.mkdir(parents=True)
        local_cli = source_bin / "loopx"
        local_cli.write_bytes(cli.read_bytes())
        local_cli.chmod(0o755)
        logs = root / "step-logs"
        missing = run_one({"name": "missing-console", "cwd": str(source),
                           "argv": [str(root / "missing-console")], "timeout_seconds": 5, "env": {}},
                          root, logs, time.time())
        assert missing["returncode"] == 127 and missing["error_kind"] == "FileNotFoundError"
        resolved = run_one({"name": "bare-console", "cwd": str(source),
                            "argv": ["loopx"], "timeout_seconds": 5, "env": {}}, root, logs, time.time())
        assert resolved["returncode"] == 0
        assert Path((logs / "bare-console/stdout.bin").read_text().strip()).resolve().is_relative_to(site)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="execute the bounded quality gate")
    parser.add_argument("--self-check", action="store_true", help="validate the fixed command plan only")
    parser.add_argument("--functional-preflight", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--wheel-byte-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--installed-origin-recorder", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    script = Path(__file__).resolve()
    root = root_for(script)
    if args.functional_preflight:
        functional_preflight(root)
        return 0
    if args.wheel_byte_check:
        wheel_byte_check(root)
        return 0
    if args.installed_origin_recorder:
        installed_origin_recorder(root)
        return 0
    if not args.run:
        parser.error("use --run to execute, or --self-check for the pure command-plan check")
    script = Path(__file__).resolve()
    steps = plan(root, script=script)
    logs = root / "quality-results"
    logs.mkdir(exist_ok=False)
    started_epoch_raw = os.environ.get("C2_QUAL_JOB_STARTED_EPOCH")
    try:
        started_epoch = float(started_epoch_raw) if started_epoch_raw is not None else None
    except ValueError:
        started_epoch = None
    if started_epoch is None:
        started_epoch = time.time()
        results = [{"name": "qualification-job-start-clock", "returncode": 1,
                    "error": "C2_QUAL_JOB_STARTED_EPOCH missing or invalid; refusing an unbounded late-start gate"}]
    else:
        results = []
    for step in steps:
        if results and results[0].get("name") == "qualification-job-start-clock":
            results.append({"name": step["name"], "returncode": None,
                            "skipped": "job start clock unavailable; scope remains unqualified"})
            continue
        if time.time() >= started_epoch + CAP_SECONDS - RESERVE_SECONDS:
            results.append({"name": step["name"], "returncode": None,
                            "skipped": "45-minute quality-job deadline reached with cleanup reserve"})
            continue
        if step["name"].startswith("native-ssh-goal-") and not (root / "qa_native/test_native_current_oracle.py").is_file():
            results.append({"name": step["name"], "skipped": "qa_native integration asset missing"})
            continue
        if step["name"].startswith("native-ssh-goal-") and step["name"] != "native-ssh-goal-base" and not any(
                row.get("name") == "native-sdk-install" and row.get("returncode") == 0 for row in results):
            results.append({"name": step["name"], "skipped": "native SDK install failed"})
            continue
        results.append(run_one(step, root, logs, started_epoch))
    summary = {"baseline": BASE, "candidate": HEAD, "cap_seconds": CAP_SECONDS,
               "cleanup_upload_reserve_seconds": RESERVE_SECONDS,
               "job_started_epoch": started_epoch,
               "elapsed_since_job_start_seconds": round(time.time() - started_epoch, 3), "results": results,
               "passed": all(row.get("returncode") == 0 for row in results)}
    (logs / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
