from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from loopx.canary import runner


def _write_env_probe_script(repo: Path, rel_path: str) -> None:
    script = repo / rel_path
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import json, os\n"
        "payload = {\n"
        "    'TMPDIR': os.environ.get('TMPDIR'),\n"
        "    'TEMP': os.environ.get('TEMP'),\n"
        "    'TMP': os.environ.get('TMP'),\n"
        "    'NODE_COMPILE_CACHE': os.environ.get('NODE_COMPILE_CACHE'),\n"
        "    'NODE_DISABLE_COMPILE_CACHE': os.environ.get('NODE_DISABLE_COMPILE_CACHE'),\n"
        "    'NODE_V8_COVERAGE': os.environ.get('NODE_V8_COVERAGE'),\n"
        "}\n"
        "cache_dir = payload['NODE_COMPILE_CACHE']\n"
        "if cache_dir:\n"
        "    payload['cache_mode'] = os.stat(cache_dir).st_mode & 0o777\n"
        "    marker = os.path.join(cache_dir, f'marker-{os.getpid()}.txt')\n"
        "    with open(marker, 'w', encoding='utf-8') as handle:\n"
        "        handle.write('ok')\n"
        "print(json.dumps(payload))\n",
        encoding="utf-8",
    )


def _clear_compile_cache_env(monkeypatch: pytest.MonkeyPatch, scratch: Path) -> None:
    for var in ("NODE_COMPILE_CACHE", "NODE_DISABLE_COMPILE_CACHE", "NODE_V8_COVERAGE"):
        monkeypatch.delenv(var, raising=False)
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))


def _payload_from_check(check_result: dict[str, Any]) -> dict[str, Any]:
    return dict(json.loads(str(check_result["stdout_tail"]).strip().splitlines()[-1]))


@pytest.mark.parametrize("parallel_jobs", [1, 2])
def test_smoke_suite_shares_one_private_compile_cache_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parallel_jobs: int,
) -> None:
    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _write_env_probe_script(repo, "examples/first-smoke.py")
    _write_env_probe_script(repo, "examples/second-smoke.py")
    _clear_compile_cache_env(monkeypatch, scratch)

    result = runner.build_canary_smoke_suite_run(
        suite="default-public",
        repo_root=repo,
        parallel_jobs=parallel_jobs,
    )

    assert result["ok"] is True, result
    assert result["executed_check_count"] == 2
    probes = [_payload_from_check(item) for item in result["selected_checks"]]
    caches = {probe["NODE_COMPILE_CACHE"] for probe in probes}
    assert len(caches) == 1
    cache_path = Path(str(next(iter(caches))))
    assert cache_path.parent == scratch
    assert cache_path.name.startswith("loopx-smoke-compile-cache-")
    assert {probe["cache_mode"] for probe in probes} == {0o700}
    assert not cache_path.exists()

    tmp_roots = [Path(str(probe["TMPDIR"])) for probe in probes]
    assert len(set(tmp_roots)) == 2
    for probe, root in zip(probes, tmp_roots, strict=True):
        assert root != cache_path
        assert root.name.startswith("loopx-smoke-")
        assert probe["TEMP"] == str(root)
        assert probe["TMP"] == str(root)
        assert not root.exists()


def test_smoke_suite_node_subprocesses_populate_and_reuse_compile_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if shutil.which("node") is None:
        pytest.skip("node executable unavailable")
    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    helper = repo / "examples" / "shared_helper.mjs"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("export function computeValue() { return 42; }\n", encoding="utf-8")
    node_probe = repo / "examples" / "node_probe.mjs"
    node_probe.write_text(
        "import { readdirSync } from 'node:fs';\n"
        "import { getCompileCacheDir } from 'node:module';\n"
        "import { computeValue } from './shared_helper.mjs';\n"
        "const cacheDir = getCompileCacheDir();\n"
        "const entriesBefore = cacheDir ? readdirSync(cacheDir) : [];\n"
        "console.log(JSON.stringify({\n"
        "  value: computeValue(),\n"
        "  cacheDir,\n"
        "  envCache: process.env.NODE_COMPILE_CACHE ?? null,\n"
        "  tmpdir: process.env.TMPDIR ?? null,\n"
        "  entryCountBefore: entriesBefore.length,\n"
        "}));\n",
        encoding="utf-8",
    )
    for idx in (1, 2):
        script = repo / "examples" / f"node-{idx}-smoke.py"
        script.write_text(
            "import subprocess, sys\n"
            "from pathlib import Path\n"
            f"probe = Path({str(node_probe)!r})\n"
            "completed = subprocess.run(['node', str(probe)], check=True, text=True, capture_output=True)\n"
            "sys.stdout.write(completed.stdout)\n",
            encoding="utf-8",
        )
    _clear_compile_cache_env(monkeypatch, scratch)

    result = runner.build_canary_smoke_suite_run(
        suite="default-public",
        repo_root=repo,
        parallel_jobs=1,
        scripts=["examples/node-1-smoke.py", "examples/node-2-smoke.py"],
    )

    assert result["ok"] is True, result
    assert result["executed_check_count"] == 2
    probes = [_payload_from_check(item) for item in result["selected_checks"]]
    assert probes[0]["value"] == 42 and probes[1]["value"] == 42
    assert probes[0]["envCache"] == probes[1]["envCache"]
    cache_root = Path(str(probes[0]["envCache"]))
    first_cache_dir = Path(str(probes[0]["cacheDir"]))
    second_cache_dir = Path(str(probes[1]["cacheDir"]))
    assert first_cache_dir.parent == cache_root
    assert second_cache_dir == first_cache_dir
    assert probes[0]["tmpdir"] != probes[1]["tmpdir"]
    assert probes[1]["entryCountBefore"] >= 1
    assert not cache_root.exists()


def test_catalog_canary_run_shares_compile_cache_and_cleans_up_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _write_env_probe_script(repo, "examples/catalog-a-smoke.py")
    _write_env_probe_script(repo, "examples/catalog-b-smoke.py")
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    _clear_compile_cache_env(monkeypatch, scratch)

    monkeypatch.setattr(
        runner,
        "build_catalog_canary_plan",
        lambda **_kwargs: {
            "schema_version": "catalog_canary_plan_v0",
            "source": "test",
            "profiles": [],
            "domain_profiles": [],
        },
    )
    monkeypatch.setattr(
        runner,
        "flatten_catalog_canary_checks",
        lambda _plan: [
            {"profile_id": "a", "command": "python3 examples/catalog-a-smoke.py"},
            {"profile_id": "b", "command": "python3 examples/catalog-b-smoke.py"},
        ],
    )

    result = runner.build_catalog_canary_run(
        catalog_path=catalog,
        repo_root=repo,
        check_limit=2,
        execute=True,
    )
    assert result["ok"] is True, result
    probes = [_payload_from_check(item) for item in result["selected_checks"]]
    caches = {probe["NODE_COMPILE_CACHE"] for probe in probes}
    assert len(caches) == 1
    assert not Path(str(next(iter(caches)))).exists()

    # Cleanup also runs when the execution loop raises mid-run.
    real_run_check = runner._run_check
    seen_caches: list[Path] = []

    def fail_on_second(
        check: dict[str, Any],
        *,
        timeout_seconds: float,
        check_index: int | None = None,
        check_count: int | None = None,
        repo_root: Path | None = None,
        cli_output_base_ref: str | None = None,
    ) -> dict[str, Any]:
        compile_cache_dir = runner._ACTIVE_NODE_COMPILE_CACHE.get()
        if isinstance(compile_cache_dir, Path):
            seen_caches.append(compile_cache_dir)
            if len(seen_caches) == 2:
                raise RuntimeError("simulated runner abort")
        return real_run_check(
            check,
            timeout_seconds=timeout_seconds,
            check_index=check_index,
            check_count=check_count,
            repo_root=repo_root,
            cli_output_base_ref=cli_output_base_ref,
        )

    monkeypatch.setattr(runner, "_run_check", fail_on_second)
    with pytest.raises(RuntimeError, match="simulated runner abort"):
        runner.build_catalog_canary_run(
            catalog_path=catalog,
            repo_root=repo,
            check_limit=2,
            execute=True,
        )
    assert len(seen_caches) == 2
    assert seen_caches[0] == seen_caches[1]
    assert not seen_caches[0].exists()


@pytest.mark.parametrize(
    ("env_key", "relative_value", "expect_preserved"),
    [
        ("NODE_DISABLE_COMPILE_CACHE", "1", False),
        ("NODE_V8_COVERAGE", "existing-v8-cov", False),
        ("NODE_COMPILE_CACHE", "caller-owned-cache", True),
    ],
)
def test_smoke_suite_respects_disable_coverage_and_caller_compile_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    relative_value: str,
    expect_preserved: bool,
) -> None:
    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _write_env_probe_script(repo, "examples/env-check-smoke.py")
    _clear_compile_cache_env(monkeypatch, scratch)
    value = relative_value if relative_value == "1" else str(tmp_path / relative_value)
    if expect_preserved:
        Path(value).mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv(env_key, value)

    result = runner.build_canary_smoke_suite_run(
        suite="default-public",
        repo_root=repo,
        parallel_jobs=1,
    )

    assert result["ok"] is True, result
    probe = _payload_from_check(result["selected_checks"][0])
    assert probe[env_key] == value
    assert probe["NODE_COMPILE_CACHE"] == (value if expect_preserved else None)
    assert not list(scratch.glob("loopx-smoke-compile-cache-*"))


@pytest.mark.parametrize("entry", ["smoke-suite", "catalog"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_cleanup_failure_preserves_completed_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, exit_code: int,
) -> None:
    from loopx.canary import planner

    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _clear_compile_cache_env(monkeypatch, scratch)
    script = repo / "examples" / "probe-smoke.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import sys\nprint('child stdout')\nprint('child stderr', file=sys.stderr)\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    real_remove = runner.remove_owned_tree

    def remove(path: Path) -> None:
        if path.name.startswith("loopx-smoke-compile-cache-"):
            raise PermissionError("cache directory is read-only")
        real_remove(path)

    monkeypatch.setattr(runner, "remove_owned_tree", remove)
    if entry == "smoke-suite":
        result = runner.build_canary_smoke_suite_run(repo_root=repo)
        render = runner.render_canary_smoke_suite_run_markdown
    else:
        catalog = repo / "catalog.md"
        catalog.write_text("| Family |\n| --- |\n| Cache fixture |\n", encoding="utf-8")
        monkeypatch.setitem(
            planner.FAMILY_CHECKS, "Cache fixture", [{"command": "python3 examples/probe-smoke.py"}],
        )
        result = runner.build_catalog_canary_run(
            repo_root=repo, catalog_path=catalog, families=["Cache fixture"], check_limit=1,
        )
        render = runner.render_catalog_canary_run_markdown

    assert result["ok"] is False
    assert result["executed_check_count"] == 1
    assert result["failure_count"] == 1 + int(exit_code != 0)
    check, = result["selected_checks"]
    assert check["returncode"] == exit_code
    assert check["ok"] is (exit_code == 0)
    assert "child stdout" in check["stdout_tail"]
    assert "child stderr" in check["stderr_tail"]
    failure, = result["cleanup_failures"]
    assert failure["ok"] is False
    assert "cache directory is read-only" in failure["reason"]
    assert "cache directory is read-only" in render(result)
    if entry == "smoke-suite":
        assert result["failures"] == ([check, failure] if exit_code else [failure])
    assert runner._ACTIVE_NODE_COMPILE_CACHE.get() is None
