#!/usr/bin/env python3
"""Run isolated, exact-commit pytest preflight and single-wave shard qualification."""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(REPO_ROOT):
    sys.path.insert(0, str(REPO_ROOT))

# Ensure the checked-out source tree beats any foreign installed loopx package.
from loopx.control_plane.testing.release_commit_qualification import (  # noqa: E402
    EXACT_RELEASE_CHECK_RECEIPT_SCHEMA_VERSION,
    EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION,
    collect_release_source_identity,
)

PYTEST_SUMMARY_SCHEMA_VERSION = EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION["pytest"]
_STRIPPED_ENV_PREFIXES = ("CODEX_", "LOOPX_", "ANTIGRAVITY_")
_MAX_DEFAULT_SHARDS = 12
_PUBLIC_SAFETY_SURFACE_PREFIXES = (
    "loopx/public_safe_text.py",
    "loopx/chat.py",
    "packages/dsh-loopx-plugin/src/observer.ts",
    "packages/dsh-loopx-plugin/tests/observer.spec.ts",
)
_PUBLIC_SAFETY_TESTS = (
    "tests/capabilities/test_reliability_diagnostics_dsh_provider.py",
    "tests/control_plane/test_public_safe_local_path_owner.py",
    "tests/test_chat_path_redaction.py",
)


def default_pytest_parallelism(cpu_count: int | None = None) -> int:
    """Choose single-wave shard concurrency sized to two-thirds of host CPUs (1..12)."""
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    if cpus < 1:
        cpus = 1
    return max(1, min(_MAX_DEFAULT_SHARDS, round(cpus * 2 / 3)))


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _is_direct_test_path(rel_path: str) -> bool:
    name = Path(rel_path).name
    if not name.endswith(".py") or not (name.startswith("test_") or name.endswith("_test.py")):
        return False
    return rel_path.startswith(("tests/", "benchmark/", "packages/", "scripts/"))


def _load_pytest_norecursedirs(repo_root: Path) -> tuple[str, ...]:
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return ()
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    tool = data.get("tool")
    if not isinstance(tool, Mapping):
        return ()
    pytest_cfg = tool.get("pytest")
    if not isinstance(pytest_cfg, Mapping):
        return ()
    ini_options = pytest_cfg.get("ini_options")
    if not isinstance(ini_options, Mapping):
        return ()
    raw = ini_options.get("norecursedirs")
    if not isinstance(raw, list):
        return ()
    patterns: list[str] = []
    for item in raw:
        if isinstance(item, str):
            normalized = item.strip().replace("\\", "/").strip("/")
            if normalized:
                patterns.append(normalized)
    return tuple(patterns)


def _normalize_rel_path(raw: str) -> str:
    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _is_excluded_by_norecursedirs(rel_path: str, patterns: Sequence[str]) -> bool:
    normalized = _normalize_rel_path(rel_path).strip("/")
    if not normalized or not patterns:
        return False
    parts = Path(normalized).parent.parts
    if not parts:
        return False
    dir_path = "/".join(parts)
    for raw_pattern in patterns:
        pattern = raw_pattern.replace("\\", "/").strip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if dir_path == pattern or dir_path.startswith(f"{pattern}/"):
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def select_preflight_tests(
    changed_paths: Sequence[str],
    *,
    repo_root: Path,
) -> list[str]:
    """Map changed repository paths to existing high-signal pytest modules."""
    norecursedirs = _load_pytest_norecursedirs(repo_root)
    selected: list[str] = []
    seen: set[str] = set()

    def add(rel_path: str) -> None:
        normalized = _normalize_rel_path(rel_path)
        if normalized in seen or _is_excluded_by_norecursedirs(normalized, norecursedirs):
            return
        if (repo_root / normalized).is_file():
            seen.add(normalized)
            selected.append(normalized)

    for raw in changed_paths:
        rel = _normalize_rel_path(raw)
        if not rel:
            continue
        if _is_direct_test_path(rel):
            if _is_excluded_by_norecursedirs(rel, norecursedirs):
                continue
            add(rel)
        if any(rel.startswith(prefix) for prefix in _PUBLIC_SAFETY_SURFACE_PREFIXES):
            for test_path in _PUBLIC_SAFETY_TESTS:
                add(test_path)
        if rel.startswith("loopx/cli_commands/turn"):
            add("tests/test_loopx_turn_driver.py")
        if rel.startswith("loopx/extensions/lark/"):
            add("tests/extensions/test_lark_realtime_dispatch.py")
            add("tests/test_chat_startup_isolation.py")
        if rel == "loopx/registry.py":
            add("tests/test_registry_git_probe.py")
        if rel in {
            "scripts/qualify_release_commit.py",
            "scripts/qualify-doubao-model-behavior-live.py",
            "loopx/control_plane/testing/release_commit_qualification.py",
        }:
            add("tests/control_plane/test_release_commit_qualification.py")
            add("tests/control_plane/test_actual_default_model_behavior_live.py")
        if rel == "pyproject.toml":
            add("tests/test_python_ci_workflow.py")
        if rel.startswith("packages/dsh-loopx-plugin/") or rel in {
            ".github/workflows/dsh-plugin.yml",
            ".github/workflows/dsh-plugin-publish.yml",
        }:
            add("tests/test_dsh_plugin_publish_workflow.py")
        if rel.startswith(("scripts/desktop_", ".github/workflows/desktop-")):
            add("tests/desktop/test_desktop_signing_workflow.py")
            add("tests/desktop/test_update_feed.py")
        if rel in {
            "scripts/release_multi_surface_preflight.py",
            "docs/product/release-readiness.md",
        }:
            add("tests/control_plane/test_release_multi_surface_preflight.py")
        if rel.endswith(".py") and rel.startswith(("loopx/", "scripts/")):
            stem = Path(rel).stem
            if stem != "__init__" and len(stem) >= 4:
                for directory in (
                    "tests",
                    "tests/control_plane",
                    "tests/capabilities",
                    "tests/extensions",
                    "tests/cli_commands",
                ):
                    add(f"{directory}/test_{stem}.py")
    return selected


def git_changed_paths_since(repo_root: Path, git_ref: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--name-only", f"{git_ref}..HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _load_runtime_stopper(repo_root: Path) -> Any:
    helper = repo_root / "tests" / "managed_effect_runtime_fixture.py"
    if not helper.is_file():
        return None
    repo_text = str(repo_root)
    if sys.path[0] != repo_text:
        sys.path.insert(0, repo_text)
    spec = importlib.util.spec_from_file_location("_qualify_runtime_cleanup", helper)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "stop_disposable_effect_runtimes", None)


def _is_link_or_junction(path: Path | str) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(is_junction and is_junction(path))


def _make_owned_tree_removable(root: Path) -> None:
    """Restore owner access inside a script-owned tree without following links."""
    user_access = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    root_stat = os.lstat(root)
    if not stat.S_ISDIR(root_stat.st_mode):
        return
    os.chmod(root, stat.S_IMODE(root_stat.st_mode) | user_access)
    for directory, subdirectories, files in os.walk(root, topdown=True, followlinks=False):
        subdirectories[:] = [
            name
            for name in subdirectories
            if not _is_link_or_junction(os.path.join(directory, name))
        ]
        for name in [directory, *(os.path.join(directory, item) for item in subdirectories)]:
            try:
                status = os.lstat(name)
                if stat.S_ISDIR(status.st_mode):
                    os.chmod(name, stat.S_IMODE(status.st_mode) | user_access)
            except FileNotFoundError:
                continue
        for name in files:
            target = os.path.join(directory, name)
            if _is_link_or_junction(target):
                continue
            try:
                status = os.lstat(target)
                # A hard link shares attributes with an entry outside this tree.
                if stat.S_ISREG(status.st_mode) and status.st_nlink == 1:
                    os.chmod(target, stat.S_IMODE(status.st_mode) | stat.S_IWUSR)
            except FileNotFoundError:
                continue


def _remove_tree_tolerant(root: Path) -> None:
    try:
        if _is_link_or_junction(root):
            root.unlink()
            return
        if not root.exists():
            return
        try:
            shutil.rmtree(root)
        except OSError:
            _make_owned_tree_removable(root)
            shutil.rmtree(root)
    except FileNotFoundError:
        return


def _isolated_env(
    root: Path,
    *,
    python_bin: Path,
    extra_bin_dirs: Sequence[Path],
) -> dict[str, str]:
    home = root / "home"
    tmp = root / "tmp"
    hypothesis_dir = root / "hypothesis"
    for directory in (
        home,
        home / "config",
        home / "cache",
        home / "data",
        home / "state",
        home / ".codex",
        tmp,
        root / "pytest-cache",
        hypothesis_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    gitconfig = root / "gitconfig"
    gitconfig.write_text(
        "[user]\n name = LoopX Release\n email = 337587101+loopx-agent@users.noreply.github.com\n",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_STRIPPED_ENV_PREFIXES)
    }
    path_parts = [str(python_bin.parent), *(str(item) for item in extra_bin_dirs)]
    if env.get("PATH"):
        path_parts.append(env["PATH"])
    env.update(
        {
            "PATH": os.pathsep.join(path_parts),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMPDIR": str(tmp),
            "TMP": str(tmp),
            "TEMP": str(tmp),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_STATE_HOME": str(home / "state"),
            "CODEX_HOME": str(home / ".codex"),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "LOOPX_USAGE_PING": "0",
            "HYPOTHESIS_STORAGE_DIRECTORY": str(hypothesis_dir),
        }
    )
    if env.get("NODE_DISABLE_COMPILE_CACHE") != "1" and not env.get("NODE_V8_COVERAGE"):
        node_compile_cache = root / "node-compile-cache"
        node_compile_cache.mkdir(parents=True, exist_ok=True)
        os.chmod(node_compile_cache, 0o700)
        env["NODE_COMPILE_CACHE"] = str(node_compile_cache)
    else:
        env.pop("NODE_COMPILE_CACHE", None)
    return env


def shard_selection_fingerprint(
    *,
    shards: int,
    use_pytest_split: bool,
    durations_path: Path | None,
    extra_pytest_args: Sequence[str],
) -> str:
    """Compute a deterministic fingerprint over shard-selection inputs for safe resume."""
    durations_sha256: str | None = None
    if durations_path is not None and durations_path.is_file():
        durations_sha256 = hashlib.sha256(durations_path.read_bytes()).hexdigest()
    return _canonical_sha256(
        {
            "shards": shards,
            "use_pytest_split": use_pytest_split,
            "durations_sha256": durations_sha256,
            "extra_pytest_args": list(extra_pytest_args),
        }
    )


def can_reuse_shard_receipt(
    summary_path: Path,
    xml_path: Path,
    *,
    expected_source: Mapping[str, Any],
    expected_groups: int,
    expected_selection_fingerprint: str | None = None,
) -> bool:
    """Reuse a shard receipt only when commit, tree, clean status, and selection match."""
    if not summary_path.is_file() or not xml_path.is_file():
        return False
    if expected_source.get("git_dirty") is not False:
        return False
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, Mapping):
        return False
    if data.get("exit_code") != 0 or data.get("groups") != expected_groups:
        return False
    if (
        expected_selection_fingerprint is not None
        and data.get("selection_fingerprint") != expected_selection_fingerprint
    ):
        return False
    expected = dict(expected_source)
    return data.get("source") == expected and data.get("current_source") == expected


def summarize_junit_xml_files(xml_paths: Sequence[Path]) -> dict[str, int]:
    """Aggregate passed, failed+error, and skipped counts across pytest JUnit XML files."""
    total = 0
    failed = 0
    skipped = 0
    for xml_path in xml_paths:
        if not xml_path.is_file():
            continue
        root = ET.parse(xml_path).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        for suite in suites:
            tests = int(suite.attrib.get("tests", 0))
            failures = int(suite.attrib.get("failures", 0))
            errors = int(suite.attrib.get("errors", 0))
            suite_skipped = int(suite.attrib.get("skipped", 0))
            total += tests
            failed += failures + errors
            skipped += suite_skipped
    passed = max(0, total - failed - skipped)
    return {"passed_count": passed, "failed_count": failed, "skipped_count": skipped}


def _require_clean_source(repo_root: Path) -> dict[str, Any]:
    source = collect_release_source_identity(repo_root)
    if source["git_dirty"]:
        raise RuntimeError("clean candidate checkout required (git_dirty must be false)")
    return source


def run_preflight(
    *,
    repo_root: Path,
    output_dir: Path,
    python_bin: Path,
    extra_bin_dirs: Sequence[Path],
    git_ref: str,
    runtime_stopper: Any,
) -> dict[str, Any]:
    changed = git_changed_paths_since(repo_root, git_ref)
    selected = select_preflight_tests(changed, repo_root=repo_root)
    receipt: dict[str, Any] = {
        "git_ref": git_ref,
        "changed_file_count": len(changed),
        "selected_tests": selected,
        "exit_code": 0,
        "wall_seconds": 0.0,
    }
    if not selected:
        (output_dir / "preflight-summary.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        return receipt
    root = Path(tempfile.mkdtemp(prefix="lxq-preflight-")).resolve()
    env = _isolated_env(root, python_bin=python_bin, extra_bin_dirs=extra_bin_dirs)
    cmd = [
        str(python_bin),
        "-m",
        "pytest",
        "-q",
        "-o",
        "tmp_path_retention_policy=failed",
        "-o",
        f"cache_dir={root / 'pytest-cache'}",
        "--junitxml=" + str(output_dir / "preflight.xml"),
        *selected,
    ]
    start = time.perf_counter()
    try:
        with (output_dir / "preflight.log").open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                cmd, cwd=repo_root, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False
            )
    finally:
        if runtime_stopper is not None:
            runtime_stopper(root)
    receipt["wall_seconds"] = time.perf_counter() - start
    receipt["exit_code"] = result.returncode
    if result.returncode == 0:
        _remove_tree_tolerant(root)
    (output_dir / "preflight-summary.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def _run_shard(
    *,
    group: int,
    shards: int,
    repo_root: Path,
    output_dir: Path,
    python_bin: Path,
    extra_bin_dirs: Sequence[Path],
    source: Mapping[str, Any],
    selection_fingerprint: str,
    durations_path: Path | None,
    store_durations: bool,
    use_pytest_split: bool,
    extra_pytest_args: Sequence[str],
    keep_failed_namespaces: bool,
    runtime_stopper: Any,
    resume: bool,
) -> tuple[dict[str, Any], Path | None]:
    name = f"pytest-shard-{group}"
    summary_path = output_dir / f"{name}-process-summary.json"
    xml_path = output_dir / f"{name}.xml"
    if resume and can_reuse_shard_receipt(
        summary_path,
        xml_path,
        expected_source=source,
        expected_groups=shards,
        expected_selection_fingerprint=selection_fingerprint,
    ):
        cached = dict(json.loads(summary_path.read_text(encoding="utf-8")))
        cached["resumed"] = True
        return cached, None

    root = Path(tempfile.mkdtemp(prefix=f"lxq-{group}-")).resolve()
    env = _isolated_env(root, python_bin=python_bin, extra_bin_dirs=extra_bin_dirs)
    shard_durations_file: Path | None = None
    cmd = [
        str(python_bin),
        "-m",
        "pytest",
        "-q",
        "-o",
        "tmp_path_retention_policy=failed",
        "-o",
        f"cache_dir={root / 'pytest-cache'}",
    ]
    if (repo_root / "scripts" / "ci" / "pytest_timing.py").is_file():
        cmd.extend(["-p", "scripts.ci.pytest_timing", "--ci-stall-after", "120"])
    if use_pytest_split:
        cmd.extend(["--splits", str(shards), "--group", str(group)])
        if store_durations:
            shard_durations_file = output_dir / f".shard-{group}-durations.json"
            if durations_path is not None and durations_path.is_file():
                shutil.copyfile(durations_path, shard_durations_file)
                cmd.extend(["--splitting-algorithm", "least_duration"])
            cmd.extend(
                [
                    "--clean-durations",
                    "--store-durations",
                    f"--durations-path={shard_durations_file}",
                ]
            )
        elif durations_path is not None and durations_path.is_file():
            cmd.extend(
                [
                    "--splitting-algorithm",
                    "least_duration",
                    f"--durations-path={durations_path}",
                ]
            )
    elif shards > 1:
        cmd.extend(
            [
                "-p",
                "scripts.ci.module_shard",
                "--ci-module-shards",
                str(shards),
                "--ci-module-shard",
                str(group),
            ]
        )
    cmd.extend(["--junitxml=" + str(xml_path), *extra_pytest_args])
    start = time.perf_counter()
    try:
        with (output_dir / f"{name}.log").open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                cmd, cwd=repo_root, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False
            )
        wall_seconds = time.perf_counter() - start
        completed_at_unix = time.time()
        current_source = collect_release_source_identity(repo_root)
        if current_source != dict(source):
            raise RuntimeError(f"candidate source mutated during shard {group}")
    finally:
        if runtime_stopper is not None:
            runtime_stopper(root)
    if result.returncode == 0 or not keep_failed_namespaces:
        _remove_tree_tolerant(root)
        cleanup = "removed_after_runtime_shutdown"
    else:
        cleanup = "retained_for_diagnosis"
    receipt: dict[str, Any] = {
        "source": dict(source),
        "current_source": current_source,
        "selection_fingerprint": selection_fingerprint,
        "group": group,
        "groups": shards,
        "exit_code": result.returncode,
        "wall_seconds": wall_seconds,
        "completed_at_unix": completed_at_unix,
        "completed_at": datetime.fromtimestamp(completed_at_unix, timezone.utc).isoformat(),
        "cleanup": cleanup,
        "resumed": False,
        "operator_state_used": False,
    }
    summary_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt, shard_durations_file


def _load_durations_mapping(file_path: Path) -> dict[str, float]:
    if not file_path.is_file():
        return {}
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return {}
    return {
        key: float(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _merge_shard_durations(
    duration_files: Sequence[Path],
    out_path: Path,
    *,
    seed_durations_path: Path | None = None,
    preserve_seed: bool = False,
) -> None:
    merged: dict[str, float] = {}
    if preserve_seed and seed_durations_path is not None:
        merged.update(_load_durations_mapping(seed_durations_path))
    for file_path in sorted(duration_files):
        merged.update(_load_durations_mapping(file_path))
        file_path.unlink(missing_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run isolated, single-wave exact-commit pytest release qualification."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--shards", type=int, default=default_pytest_parallelism())
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--preflight-since", type=str, default=None)
    parser.add_argument("--only-preflight", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--durations-path", type=Path, default=None)
    parser.add_argument("--durations-out", type=Path, default=None)
    parser.add_argument("--no-pytest-split", action="store_true")
    parser.add_argument("--extra-bin-dir", type=Path, action="append", default=[])
    parser.add_argument("--pytest-arg", type=str, action="append", default=[])
    parser.add_argument("--no-keep-failed-namespaces", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o022)
    parser = _parser()
    args = parser.parse_args(argv)
    if args.only_preflight and not args.preflight_since:
        parser.error("--only-preflight requires --preflight-since")
    if args.shards < 1:
        raise ValueError("--shards must be at least 1")
    workers = args.shards if args.max_workers is None else max(1, min(args.shards, args.max_workers))
    repo_root = args.repo_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    python_bin = Path(os.path.abspath(os.fspath(args.python.expanduser())))
    extra_bins = [path.expanduser().resolve() for path in args.extra_bin_dir]

    source = _require_clean_source(repo_root)
    (output_dir / "source-identity.json").write_text(
        json.dumps(source, indent=2) + "\n", encoding="utf-8"
    )
    stopper = _load_runtime_stopper(repo_root)

    if args.preflight_since:
        preflight = run_preflight(
            repo_root=repo_root,
            output_dir=output_dir,
            python_bin=python_bin,
            extra_bin_dirs=extra_bins,
            git_ref=args.preflight_since,
            runtime_stopper=stopper,
        )
        if preflight["exit_code"] != 0 or args.only_preflight:
            print(json.dumps({"source": source, "preflight": preflight}, indent=2))
            return int(preflight["exit_code"])

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    duration_files: list[Path] = []
    use_pytest_split = not args.no_pytest_split
    store_durations = use_pytest_split and args.durations_out is not None
    resolved_durations = (
        args.durations_path.expanduser().resolve() if args.durations_path else None
    )
    selection_fp = shard_selection_fingerprint(
        shards=args.shards,
        use_pytest_split=use_pytest_split,
        durations_path=resolved_durations,
        extra_pytest_args=args.pytest_arg,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _run_shard,
                group=group,
                shards=args.shards,
                repo_root=repo_root,
                output_dir=output_dir,
                python_bin=python_bin,
                extra_bin_dirs=extra_bins,
                source=source,
                selection_fingerprint=selection_fp,
                durations_path=resolved_durations,
                store_durations=store_durations,
                use_pytest_split=use_pytest_split,
                extra_pytest_args=args.pytest_arg,
                keep_failed_namespaces=not args.no_keep_failed_namespaces,
                runtime_stopper=stopper,
                resume=args.resume,
            ): group
            for group in range(1, args.shards + 1)
        }
        for future in concurrent.futures.as_completed(futures):
            receipt, dur_file = future.result()
            results.append(receipt)
            if dur_file is not None:
                duration_files.append(dur_file)

    if store_durations and args.durations_out is not None:
        _merge_shard_durations(
            duration_files,
            args.durations_out.expanduser().resolve(),
            seed_durations_path=resolved_durations,
            preserve_seed=any(bool(row.get("resumed")) for row in results),
        )

    final_source = _require_clean_source(repo_root)
    if final_source != source:
        raise RuntimeError("candidate source changed during qualification")

    xml_paths = [output_dir / f"pytest-shard-{g}.xml" for g in range(1, args.shards + 1)]
    counts = summarize_junit_xml_files(xml_paths)
    all_ok = (
        all(path.is_file() for path in xml_paths)
        and all(row["exit_code"] == 0 for row in results)
        and counts["failed_count"] == 0
    )
    completed_at = datetime.now(timezone.utc).isoformat()
    (output_dir / "pytest-summary.json").write_text(
        json.dumps(counts, indent=2) + "\n", encoding="utf-8"
    )
    receipt_payload = {
        "schema_version": EXACT_RELEASE_CHECK_RECEIPT_SCHEMA_VERSION,
        "source": source,
        "status": "passed" if (all_ok and counts["passed_count"] > 0) else "failed",
        "result_schema_version": PYTEST_SUMMARY_SCHEMA_VERSION,
        "result_digest": _canonical_sha256(counts),
        "completed_at": completed_at,
        "summary": counts,
    }
    (output_dir / "receipt-pytest.json").write_text(
        json.dumps(receipt_payload, indent=2) + "\n", encoding="utf-8"
    )
    shards_summary = {
        "source": source,
        "shards": sorted(results, key=lambda row: int(row["group"])),
        "passed": receipt_payload["status"] == "passed",
        "wall_seconds": time.perf_counter() - started,
        "completed_at": completed_at,
        "summary": counts,
    }
    (output_dir / "pytest-shards-process-summary.json").write_text(
        json.dumps(shards_summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt_payload, indent=2))
    return 0 if receipt_payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
