from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (WORKFLOW_ROOT / ".github/workflows/python-tests.yml").read_text(encoding="utf-8")


def test_dashboard_acceptance_and_kernel_checks_run_independently() -> None:
    kernel = WORKFLOW.split("  kernel-static-checks:\n", 1)[1].split(
        "  dashboard-acceptance:\n", 1,
    )[0]
    dashboard = WORKFLOW.split("  dashboard-acceptance:\n", 1)[1].split(
        "  checks:\n", 1,
    )[0]
    aggregate = WORKFLOW.split("  checks:\n", 1)[1].split(
        "  node-minimum-compatibility:\n", 1,
    )[0]

    assert "python -m ruff check" in kernel
    assert "python -m mypy" in kernel
    assert "cli-output-budget-regression-smoke.py" in kernel
    assert "personal-workspace-browser-smoke.mjs" not in kernel
    assert "test:dashboard:coverage" not in kernel

    assert "test:dashboard:coverage" in dashboard
    assert "personal-workspace-browser-smoke.mjs" in dashboard
    assert "python -m ruff check" not in dashboard
    assert "python -m mypy" not in dashboard

    assert "if: always() && needs.changes.outputs.core_tests == 'true'" in aggregate
    assert "needs: [changes, kernel-static-checks, dashboard-acceptance]" in aggregate
    assert "needs.kernel-static-checks.result" in aggregate
    assert "needs.dashboard-acceptance.result" in aggregate


@pytest.mark.parametrize("kernel", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("dashboard", ["success", "failure", "cancelled", "skipped"])
def test_checks_aggregate_requires_both_parallel_lanes(
    kernel: str, dashboard: str,
) -> None:
    gate = WORKFLOW.split("name: Require kernel and Dashboard qualification", 1)[1]
    script = gate.split("run: |", 1)[1].split("\n\n  node-minimum-compatibility:", 1)[0]
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={
            **os.environ,
            "DASHBOARD_RESULT": dashboard,
            "KERNEL_RESULT": kernel,
        },
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == (kernel == dashboard == "success")


def test_minimum_node_lane_exercises_sqlite_without_a_skip_list() -> None:
    minimum = WORKFLOW.split("  node-minimum-compatibility:\n", 1)[1].split(
        "  node-forward-compatibility:\n", 1,
    )[0]

    assert 'node-version: "22.22.3"' in minimum
    assert 'LOOPX_TEST_REQUIRE_SQLITE_QUALIFIED: "1"' in minimum
    assert "tests/control_plane_ts/sqlite_runtime_admission.test.ts" in minimum
    assert "tests/control_plane_ts/deferred_hard_lease_lifecycle.test.ts" in minimum
    assert "for test in tests/control_plane_ts/*.test.ts" not in minimum
    assert "--test-name-pattern" not in minimum
    assert "shard" not in minimum


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", ""])
def test_stage2c_gate_requires_all_lanes(result: str) -> None:
    gate = WORKFLOW.split("  stage2c-correctness-e2e:", 1)[1].split("  windows-powershell:", 1)[0]
    assert "if: always()" in gate
    assert "needs: [changes, stage2c-suite]" in gate
    assert "if: always() && needs.changes.outputs.stage2c_tests == 'true'" in gate
    assert "STAGE2C_RESULT: ${{ needs.stage2c-suite.result }}" in gate
    script = gate.split("run: ", 1)[1].strip()
    actual = subprocess.run(
        ["bash", "-e", "-c", script],
        env={**os.environ, "STAGE2C_RESULT": result}, check=False,
    )
    assert (actual.returncode == 0) == (result == "success")
    suite = WORKFLOW.split("  stage2c-suite:", 1)[1].split("  stage2c-correctness-e2e:", 1)[0]
    assert "fail-fast: false" in suite
    for entry in ("{suite: e2e, shard: 1}", "{suite: e2e, shard: 2}", "{suite: mutants, shard: 0}", "{suite: installed, shard: 0}"):
        assert entry in suite
    assert "if: needs.changes.outputs.stage2c_tests == 'true'" in suite.split("    steps:", 1)[0]
    steps = {step.splitlines()[0]: step for step in suite.split("      - name: ")[1:]}
    for name, lane in [
        ("Qualify real CLI, mixed writers, process death, and recovery", "e2e"),
        ("Reject deliberate correctness regressions", "mutants"),
        ("Build independently installed distributions", "installed"),
        ("Qualify wheel outside the repository", "installed"),
        ("Qualify sdist outside the repository", "installed"),
    ]:
        assert f"if: matrix.suite == '{lane}'" in steps[name]
    assert "--case" not in steps["Reject deliberate correctness regressions"]
    artifact = steps["Retain bounded acceptance evidence"]
    assert "if: always()" in artifact
    assert "name: stage2c-correctness-evidence-${{ matrix.suite }}-${{ matrix.shard }}" in artifact
    assert "if-no-files-found: error" in artifact


def test_stage2c_workers_preserve_module_state_and_execute_every_row(tmp_path: Path) -> None:
    step = WORKFLOW.split("name: Qualify real CLI, mixed writers, process death, and recovery", 1)[1]
    command = step.split("run: ", 1)[1].splitlines()[0]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTEST", "COVERAGE", "COV_CORE"))}
    env["PYTHONPATH"] = str(WORKFLOW_ROOT)
    visited = set()
    for shard in (1, 2):
        root = tmp_path / f"shard-{shard}"
        root.mkdir()
        args = shlex.split(command.replace("${{ matrix.shard }}", str(shard)))
        args[0] = sys.executable
        for name in ("first", "second", "third", "fourth"):
            (root / f"test_{name}.py").write_text(
                "import os\nfrom pathlib import Path\nimport pytest\n"
                "pytestmark = pytest.mark.stage2c_e2e\n"
                "@pytest.fixture(scope='module')\ndef state():\n    return []\n"
                "@pytest.mark.parametrize('row', range(4))\n"
                "def test_order(state, row):\n"
                "    assert state == list(range(row))\n    state.append(row)\n"
                f"    with Path('{name}.visits').open('a') as stream:\n"
                "        stream.write(f'{os.getpid()}:{row}\\n')\n", encoding="utf-8")
        (root / "pytest.ini").write_text("[pytest]\nmarkers = stage2c_e2e\n")
        actual = subprocess.run(args, cwd=root, env=env, capture_output=True,
                                text=True, timeout=60, check=False)
        assert actual.returncode == 0, actual.stdout + actual.stderr
        assert len(ET.parse(root / "stage2c-e2e.xml").findall(".//testcase")) == 8
        workers = set()
        for report in root.glob("*.visits"):
            assert report.stem not in visited
            visited.add(report.stem)
            rows = [line.split(":") for line in report.read_text().splitlines()]
            assert [row for _, row in rows] == ["0", "1", "2", "3"]
            assert len({pid for pid, _ in rows}) == 1
            workers.add(rows[0][0])
        assert len(workers) == 2
    assert visited == {"first", "second", "third", "fourth"}


def test_module_sharding_is_deterministic_and_balances_complete_modules() -> None:
    from scripts.ci.module_shard import assign_modules
    counts = {"large": 100, "medium": 60, "small": 40, "tiny": 1}
    expected = {"large": 1, "medium": 2, "small": 2, "tiny": 1}
    assert assign_modules(counts, 2) == expected
    assert assign_modules(dict(reversed(list(counts.items()))), 2) == expected
    assert assign_modules(counts, 1) == dict.fromkeys(counts, 1)
    with pytest.raises(ValueError):
        assign_modules(counts, 0)


@pytest.mark.parametrize("checks", ["success", "failure", "cancelled", "skipped"])
@pytest.mark.parametrize("shards", ["success", "failure", "cancelled", "skipped"])
def test_required_pytest_check_rejects_incomplete_upstream_jobs(
    checks: str, shards: str,
) -> None:
    # Execute the actual gate, including the runner's fail-fast shell behavior.
    gate = WORKFLOW.split("name: Require every upstream check", 1)[1]
    script = gate.split("run: |", 1)[1].split("      - uses:", 1)[0]
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={**os.environ, "CHECKS_RESULT": checks, "SHARDS_RESULT": shards},
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == (checks == shards == "success")
    assert "if: always() && needs.changes.outputs.python_tests == 'true'" in WORKFLOW
    assert "needs: [changes, checks, test-shard]" in WORKFLOW


def test_merge_gate_runs_on_all_prs_and_checks_every_core_aggregate() -> None:
    trigger = WORKFLOW.split("  pull_request:", 1)[1].split("  push:", 1)[0]
    assert "paths:" not in trigger and "paths-ignore:" not in trigger
    gate = WORKFLOW.split("  merge-gate:", 1)[1]
    assert "if: always()" in gate
    assert (
        "needs: [changes, checks, pytest, node-minimum-compatibility, "
        "stage2c-correctness-e2e, windows-powershell, presentation]"
    ) in gate
    assert "NEEDS_JSON: ${{ toJSON(needs) }}" in gate
    assert "run: python scripts/ci/review_gate.py verify" in gate
    assert "continue-on-error" not in gate
    for name, output in (("checks", "core_tests"), ("test-shard", "python_tests"), ("stage2c-suite", "stage2c_tests"), ("windows-powershell", "python_tests"), ("presentation", "presentation_tests")):
        job = WORKFLOW.split(f"  {name}:\n", 1)[1].split("    steps:", 1)[0]
        if name == "checks":
            assert "needs: [changes, kernel-static-checks, dashboard-acceptance]" in job
            assert "if: always() && needs.changes.outputs.core_tests == 'true'" in job
        else:
            assert "needs: changes" in job
            assert f"if: needs.changes.outputs.{output} == 'true'" in job


def test_presentation_exemption_retains_real_frontend_checks_and_force_full() -> None:
    job = WORKFLOW.split("  presentation:\n", 1)[1].split("  merge-gate:\n", 1)[0]
    assert "npm run build:chat" in job
    assert "npm run smoke:personal-workspace-packaged" in job
    assert "status --short --untracked-files=all -- loopx/web/chat" in job
    assert "continue-on-error" not in job
    assert "labels.*.name, 'ci:full'" in WORKFLOW
    assert "labeled, unlabeled" in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW
    assert "--force-full" in WORKFLOW
    assert "impact-shadow" not in WORKFLOW
    assert "matrix:\n        shard: [1, 2, 3, 4]" in WORKFLOW


def test_four_shards_execute_each_test_once_and_merge_portable_coverage(
    tmp_path: Path,
) -> None:
    # Real pytest-split + xdist + coverage, in four distinct checkout roots.
    # Each shard alone misses a function; their union must cover the whole file.
    shard_step = WORKFLOW.split("name: Run test shard", 1)[1]
    template = shard_step.split("run: >-", 1)[1].split("      - name:", 1)[0]
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COVERAGE", "COV_CORE", "PYTEST"))
    }
    seen: list[set[str]] = []
    for shard in (1, 2, 3, 4):
        root = tmp_path / f"checkout-{shard}"
        root.mkdir()
        (root / "ci_subject.py").write_text(
            "def first():\n    return 1\n\ndef second():\n    return 2\n",
            encoding="utf-8",
        )
        (root / "test_subject.py").write_text(
            "from ci_subject import first, second\n"
            "def test_first_one():\n    assert first() == 1\n"
            "def test_first_two():\n    assert first() == 1\n"
            "def test_second_one():\n    assert second() == 2\n"
            "def test_second_two():\n    assert second() == 2\n",
            encoding="utf-8",
        )
        (root / "pyproject.toml").write_text(
            '[tool.coverage.run]\nsource = ["ci_subject"]\nrelative_files = true\n',
            encoding="utf-8",
        )
        args = shlex.split(template.replace("${{ matrix.shard }}", str(shard)))
        args[0] = sys.executable
        args[args.index("--cov=loopx")] = "--cov=ci_subject"
        result = subprocess.run(
            [*args, "--junitxml=results.xml"], cwd=root, env=env,
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        cases = ET.parse(root / "results.xml").findall(".//testcase")
        seen.append({case.attrib["name"] for case in cases})
        partial = subprocess.run(
            [sys.executable, "-m", "coverage", "report", "--fail-under=100"],
            cwd=root, env=env, capture_output=True, check=False,
        )
        assert partial.returncode == 2
        destination = tmp_path / "coverage-shards" / f"python-coverage-{shard}"
        destination.mkdir(parents=True)
        (root / ".coverage").rename(destination / ".coverage")

    assert all(seen)
    assert sum(map(len, seen)) == len(set.union(*seen)) == 4
    assert set.union(*seen) == {"test_first_one", "test_first_two", "test_second_one", "test_second_two"}
    # Reuse the real aggregate shell commands, with a 100% synthetic oracle.
    step = WORKFLOW.split("name: Combine complete coverage", 1)[1]
    script = step.split("run: |", 1)[1].split("      - uses:", 1)[0]
    script = script.replace("python -m", f"{shlex.quote(sys.executable)} -m")
    script = script.replace("--fail-under=19.6", "--fail-under=100")
    root = tmp_path / "checkout-1"
    (tmp_path / "coverage-shards").rename(root / "coverage-shards")
    for shard in (1, 2, 3, 4):
        data = root / "coverage-shards" / f"python-coverage-{shard}" / ".coverage"
        held = data.with_name("held")
        data.rename(held)
        missing = subprocess.run(
            ["bash", "-e", "-c", script], cwd=root, env=env,
            capture_output=True, check=False,
        )
        assert missing.returncode != 0
        assert not (root / "coverage.xml").exists()
        held.rename(data)
    result = subprocess.run(
        ["bash", "-e", "-c", script], cwd=root, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ET.parse(root / "coverage.xml").getroot().attrib["line-rate"] == "1"
    # The combine consumed the inputs: replay with absent artifacts must fail.
    missing = subprocess.run(
        ["bash", "-e", "-c", script], cwd=root, env=env,
        capture_output=True, check=False,
    )
    assert missing.returncode != 0
    assert re.search(r"shard: \[1, 2, 3, 4\]", WORKFLOW)
    assert "include-hidden-files: true" in WORKFLOW
    assert "--cov-fail-under" not in template
