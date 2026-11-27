"""Pre-merge validation bounded parallelism, cross-section dedup, and shared cache tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from loopx.canary import planner, premerge, runner
from loopx.canary.planner import DEFAULT_CATALOG_PATH
from loopx.canary.premerge import (
    build_premerge_validation_gate,
    default_premerge_parallel_jobs,
    render_premerge_validation_gate_markdown,
)
from loopx.canary.runner import build_canary_smoke_suite_run, premerge_check_reuse_key
from loopx.cli import main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _clear_compile_cache_env(monkeypatch: pytest.MonkeyPatch, scratch: Path) -> None:
    for var in ("NODE_COMPILE_CACHE", "NODE_DISABLE_COMPILE_CACHE", "NODE_V8_COVERAGE"):
        monkeypatch.delenv(var, raising=False)
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))


def _write_repo_fixture(
    tmp_path: Path,
    *,
    shared_exit_code: int = 0,
    include_unique_risk_script: bool = True,
    include_second_shared_script: bool = False,
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    examples = repo / "examples" / "canary"
    examples.mkdir(parents=True)
    counter_file = tmp_path / "shared_probe_count.txt"
    counter_file.write_text("0\n", encoding="utf-8")

    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    (examples / "catalog-planner-smoke.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"counter = Path({str(counter_file)!r})\n"
        "count = int(counter.read_text(encoding='utf-8').strip()) + 1\n"
        "counter.write_text(f'{count}\\n', encoding='utf-8')\n"
        "print(json.dumps({'script': 'shared', 'count': count, 'cache': os.environ.get('NODE_COMPILE_CACHE')}))\n"
        + (
            f"print('shared probe failed', file=sys.stderr)\nsys.exit({shared_exit_code})\n"
            if shared_exit_code != 0
            else ""
        ),
        encoding="utf-8",
    )
    if include_second_shared_script:
        (examples / "catalog-run-e2e-smoke.py").write_text(
            "import json, os\n"
            "print(json.dumps({'script': 'shared2', 'cache': os.environ.get('NODE_COMPILE_CACHE')}))\n",
            encoding="utf-8",
        )
    if include_unique_risk_script:
        (examples / "smoke-suite-runner-smoke.py").write_text(
            "import json, os\n"
            "print(json.dumps({'script': 'risk_only', 'cache': os.environ.get('NODE_COMPILE_CACHE')}))\n",
            encoding="utf-8",
        )

    catalog = repo / "docs" / "concepts" / "interaction-pattern-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes(DEFAULT_CATALOG_PATH.read_bytes())

    _git(repo, "init")
    _git(repo, "config", "user.name", "Canary Fixture")
    _git(repo, "config", "user.email", "canary@example.invalid")
    _git(repo, "add", "tracked.txt", "examples", "docs")
    _git(repo, "commit", "-m", "fixture")
    return repo, counter_file


def _wire_catalog_and_risk_scripts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    catalog_scripts: list[str],
) -> None:
    monkeypatch.setitem(
        planner.FAMILY_CHECKS,
        "Canary fixture",
        [{"command": f"python3 {script}"} for script in catalog_scripts],
    )
    monkeypatch.setattr(
        premerge,
        "classify_premerge_surfaces",
        lambda files, **_kwargs: {
            "changed_files": list(files),
            "changed_file_count": len(files),
            "python_files": [f for f in files if f.endswith(".py")],
            "surfaces": ["canary_runner"],
            "risk_profiles": ["canary-runner"],
            "manual_holds": [],
            "public_boundary_scan_recommended": False,
        },
    )
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
            {"profile_id": "catalog-fixture", "family": "Canary fixture", "command": f"python3 {script}"}
            for script in catalog_scripts
        ],
    )


def test_default_premerge_parallel_jobs_and_cli_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for cpu_count, expected in ((None, 1), (2, 1), (4, 2), (8, 4), (16, 4)):
        monkeypatch.setattr(premerge.os, "cpu_count", lambda count=cpu_count: count)
        assert default_premerge_parallel_jobs() == expected
        assert default_premerge_parallel_jobs(fail_fast=True) == 1

    monkeypatch.setattr(premerge.os, "cpu_count", lambda: 12)
    repo, _ = _write_repo_fixture(tmp_path)

    default_gate = build_premerge_validation_gate(
        repo_root=repo,
        changed_files=["tracked.txt"],
        base_ref="HEAD",
        execute=False,
    )
    assert default_gate["parallel_jobs"] == 4
    assert default_gate["validation_summary"]["parallel_jobs"] == 4
    assert default_gate["reused_check_count"] == 0
    assert default_gate["validation_summary"]["reused_check_count"] == 0

    serial_gate = build_premerge_validation_gate(
        repo_root=repo,
        changed_files=["tracked.txt"],
        base_ref="HEAD",
        execute=False,
        parallel_jobs=1,
    )
    assert serial_gate["parallel_jobs"] == 1
    assert serial_gate["validation_summary"]["parallel_jobs"] == 1

    fail_fast_gate = build_premerge_validation_gate(
        repo_root=repo,
        changed_files=["tracked.txt"],
        base_ref="HEAD",
        execute=False,
        fail_fast=True,
        parallel_jobs=4,
    )
    assert fail_fast_gate["parallel_jobs"] == 1
    assert fail_fast_gate["validation_summary"]["parallel_jobs"] == 1

    monkeypatch.chdir(repo)
    code = main([
        "--format", "json",
        "canary", "premerge",
        "--changed-file", "tracked.txt",
        "--git-diff-base", "HEAD",
        "--no-execute",
        "-j", "1",
    ])
    assert code == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["parallel_jobs"] == 1
    assert cli_payload["validation_summary"]["parallel_jobs"] == 1

    code_jobs = main([
        "--format", "json",
        "canary", "premerge",
        "--changed-file", "tracked.txt",
        "--git-diff-base", "HEAD",
        "--no-execute",
        "--jobs", "3",
    ])
    assert code_jobs == 0
    cli_payload_jobs = json.loads(capsys.readouterr().out)
    assert cli_payload_jobs["parallel_jobs"] == 3


def test_premerge_check_reuse_key_tracks_argv_and_cli_output_base_ref() -> None:
    key_main = premerge_check_reuse_key(
        {"ok": True, "argv": ["python3", "examples/canary/smoke.py"]},
        cli_output_base_ref="origin/main",
    )
    key_main_again = premerge_check_reuse_key(
        {"ok": True, "argv": ["python3", "examples/canary/smoke.py"]},
        cli_output_base_ref="origin/main",
    )
    key_head = premerge_check_reuse_key(
        {"ok": True, "argv": ["python3", "examples/canary/smoke.py"]},
        cli_output_base_ref="HEAD",
    )
    key_none = premerge_check_reuse_key(
        {"ok": True, "argv": ["python3", "examples/canary/smoke.py"]},
        cli_output_base_ref=None,
    )
    assert key_main == key_main_again
    assert key_main != key_head
    assert key_head != key_none
    assert key_none[1] == ""


@pytest.mark.parametrize("shared_exit_code", [0, 3])
def test_premerge_dedups_shared_check_pass_and_fail_and_shares_compile_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shared_exit_code: int,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _clear_compile_cache_env(monkeypatch, scratch)
    repo, counter_file = _write_repo_fixture(
        tmp_path,
        shared_exit_code=shared_exit_code,
        include_unique_risk_script=True,
    )
    _wire_catalog_and_risk_scripts(
        monkeypatch,
        catalog_scripts=["examples/canary/catalog-planner-smoke.py"],
    )

    payload = build_premerge_validation_gate(
        repo_root=repo,
        changed_files=["tracked.txt"],
        base_ref="HEAD",
        execute=True,
        parallel_jobs=2,
    )

    expected_ok = shared_exit_code == 0
    assert payload["ok"] is expected_ok, payload
    assert payload["gate"]["merge_gate_passed"] is expected_ok
    assert int(counter_file.read_text(encoding="utf-8").strip()) == 1

    catalog_run = payload["catalog_run"]
    risk_run = payload["risk_profile_run"]
    assert risk_run is not None
    assert catalog_run["reused_check_count"] == 0
    assert catalog_run["executed_check_count"] == 1
    assert risk_run["selected_check_count"] == 2
    assert risk_run["reused_check_count"] == 1
    assert risk_run["executed_check_count"] == 1
    assert payload["reused_check_count"] == 1
    assert payload["validation_summary"]["reused_check_count"] == 1
    assert payload["validation_summary"]["executed_check_count"] == 2

    reused_item = next(
        item for item in risk_run["selected_checks"]
        if item["normalized"]["script"] == "examples/canary/catalog-planner-smoke.py"
    )
    risk_only_item = next(
        item for item in risk_run["selected_checks"]
        if item["normalized"]["script"] == "examples/canary/smoke-suite-runner-smoke.py"
    )
    assert catalog_run["selected_checks"][0]["profile_id"] == "catalog-fixture"
    assert reused_item["reused"] is True
    assert reused_item["reused_from_section"] == "catalog_canaries"
    assert reused_item["duration_seconds"] == 0.0
    assert reused_item["profile_id"] == "smoke-suite"
    assert reused_item["returncode"] == shared_exit_code
    assert reused_item["ok"] is expected_ok
    assert reused_item["status"] == ("passed" if expected_ok else "failed")
    if not expected_ok:
        assert "shared probe failed" in reused_item["stderr_tail"]
        assert payload["gate"]["status"] == "failed"

    catalog_probe = json.loads(str(catalog_run["selected_checks"][0]["stdout_tail"]).strip())
    risk_probe = json.loads(str(risk_only_item["stdout_tail"]).strip())
    assert catalog_probe["cache"] == risk_probe["cache"]
    cache_path = Path(str(catalog_probe["cache"]))
    assert cache_path.parent == scratch
    assert cache_path.name.startswith("loopx-smoke-compile-cache-")
    assert not cache_path.exists()
    assert runner._ACTIVE_NODE_COMPILE_CACHE.get() is None

    markdown = render_premerge_validation_gate_markdown(payload)
    assert "- reused_checks: `1`" in markdown
    assert "(reused)" in markdown


def test_all_reused_section_skips_executor_and_tracked_path_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _clear_compile_cache_env(monkeypatch, scratch)
    repo, counter_file = _write_repo_fixture(
        tmp_path,
        shared_exit_code=0,
        include_unique_risk_script=False,
        include_second_shared_script=True,
    )
    _wire_catalog_and_risk_scripts(
        monkeypatch,
        catalog_scripts=[
            "examples/canary/catalog-planner-smoke.py",
            "examples/canary/catalog-run-e2e-smoke.py",
        ],
    )

    real_tracked = runner._tracked_change_paths
    real_executor = runner.ThreadPoolExecutor
    tracked_calls = 0
    executor_calls = 0

    def spy_tracked(*args: Any, **kwargs: Any) -> tuple[bool, list[bytes], str]:
        nonlocal tracked_calls
        tracked_calls += 1
        return real_tracked(*args, **kwargs)

    class SpyExecutor:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            nonlocal executor_calls
            executor_calls += 1
            self._inner = real_executor(*args, **kwargs)

        def __enter__(self) -> Any:
            return self._inner.__enter__()

        def __exit__(self, *args: Any) -> Any:
            return self._inner.__exit__(*args)

    monkeypatch.setattr(runner, "_tracked_change_paths", spy_tracked)
    monkeypatch.setattr(runner, "ThreadPoolExecutor", SpyExecutor)

    payload = build_premerge_validation_gate(
        repo_root=repo,
        changed_files=["tracked.txt"],
        base_ref="HEAD",
        execute=True,
        parallel_jobs=2,
    )

    assert payload["ok"] is True, payload
    assert int(counter_file.read_text(encoding="utf-8").strip()) == 1
    # Catalog ran 2 checks concurrently (executor_calls=1, before/after tracked_change_paths=2);
    # risk_profile_run reused both checks and made 0 tracked_change_paths / ThreadPoolExecutor calls.
    assert tracked_calls == 2
    assert executor_calls == 1
    risk_run = payload["risk_profile_run"]
    assert risk_run is not None
    assert risk_run["selected_check_count"] == 2
    assert risk_run["executed_check_count"] == 0
    assert risk_run["reused_check_count"] == 2
    assert risk_run["side_effect_guard"]["enforcement_reason"] == "all_checks_reused"
    assert payload["validation_summary"]["runs"]["risk_profile_run"]["executed_check_count"] == 0
    assert payload["validation_summary"]["runs"]["risk_profile_run"]["reused_check_count"] == 2


def test_reused_results_ignore_entries_with_tracked_side_effects(tmp_path: Path) -> None:
    repo, _ = _write_repo_fixture(tmp_path, include_unique_risk_script=False)
    cmd = "python3 examples/canary/catalog-planner-smoke.py"
    normalized = runner.normalize_canary_command(cmd, repo_root=repo)
    key = premerge_check_reuse_key(normalized, cli_output_base_ref="HEAD")
    bad_prior = {
        "ok": False,
        "status": "passed",
        "returncode": 0,
        "stdout_tail": "stale",
        "stderr_tail": "",
        "normalized": normalized,
        "tracked_side_effects": ["tracked.txt"],
    }
    suite_run = build_canary_smoke_suite_run(
        repo_root=repo,
        scripts=["catalog-planner-smoke.py"],
        execute=True,
        cli_output_base_ref="HEAD",
        reusable_results_by_command={key: bad_prior},
    )
    assert suite_run["reused_check_count"] == 0
    assert suite_run["executed_check_count"] == 1


def test_premerge_cleans_shared_compile_cache_on_exception_and_surfaces_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _clear_compile_cache_env(monkeypatch, scratch)
    repo, _ = _write_repo_fixture(tmp_path, include_unique_risk_script=True)
    _wire_catalog_and_risk_scripts(
        monkeypatch,
        catalog_scripts=["examples/canary/catalog-planner-smoke.py"],
    )

    seen_caches: list[Path] = []
    real_suite_run = runner.build_canary_smoke_suite_run
    call_count = 0

    def abort_on_risk_section(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        active = runner._ACTIVE_NODE_COMPILE_CACHE.get()
        assert isinstance(active, Path)
        seen_caches.append(active)
        if call_count == 2:
            raise RuntimeError("simulated risk section crash")
        return real_suite_run(*args, **kwargs)

    monkeypatch.setattr(premerge, "build_canary_smoke_suite_run", abort_on_risk_section)
    with pytest.raises(RuntimeError, match="simulated risk section crash"):
        build_premerge_validation_gate(
            repo_root=repo,
            changed_files=["tracked.txt"],
            base_ref="HEAD",
            execute=True,
            parallel_jobs=2,
        )
    assert len(seen_caches) == 2
    assert seen_caches[0] == seen_caches[1]
    assert not seen_caches[0].exists()
    assert runner._ACTIVE_NODE_COMPILE_CACHE.get() is None

    # Restore real suite runner and test outer cleanup PermissionError surfacing.
    monkeypatch.setattr(premerge, "build_canary_smoke_suite_run", real_suite_run)
    real_remove = runner.remove_owned_tree

    def fail_cache_cleanup(path: Path) -> None:
        if path.name.startswith("loopx-smoke-compile-cache-"):
            raise PermissionError("readonly compile cache")
        real_remove(path)

    monkeypatch.setattr(runner, "remove_owned_tree", fail_cache_cleanup)
    failed_gate = build_premerge_validation_gate(
        repo_root=repo,
        changed_files=["tracked.txt"],
        base_ref="HEAD",
        execute=True,
        parallel_jobs=2,
    )
    assert failed_gate["ok"] is False
    assert failed_gate["gate"]["status"] == "failed"
    assert failed_gate["gate"]["merge_gate_passed"] is False
    assert failed_gate["validation_summary"]["failure_count"] >= 1
    assert any("readonly compile cache" in str(item.get("reason")) for item in failed_gate["cleanup_failures"])
    assert "readonly compile cache" in render_premerge_validation_gate_markdown(failed_gate)
    assert runner._ACTIVE_NODE_COMPILE_CACHE.get() is None
