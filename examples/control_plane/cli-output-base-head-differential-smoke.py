#!/usr/bin/env python3
"""Compare agent-facing CLI output from one public fixture on base and head."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.cli_output_differential import (  # noqa: E402
    compare_cli_output_receipts,
    select_cli_output_base_ref,
)
from loopx.control_plane.testing.cli_output_budget import CLI_OUTPUT_BUDGET_BY_ID  # noqa: E402
from tests.control_plane.cli_output_fixture import budget_fixture_path  # noqa: E402


PROBE_TEST = REPO_ROOT / "tests" / "control_plane" / "test_cli_output_budget.py"
PROBE_RUNNER = REPO_ROOT / "examples" / "control_plane" / "cli-output-probe-runner.py"
FIXTURE_SUPPORT = REPO_ROOT / "tests" / "control_plane" / "cli_output_fixture.py"
SEMANTICS_SOURCE = (
    REPO_ROOT / "loopx" / "control_plane" / "testing" / "cli_output_semantics.py"
)


def _bounded_stage_timeout(
    deadline_monotonic: float | None,
    *,
    stage: str,
    default_timeout: float = 240.0,
) -> float:
    if deadline_monotonic is None:
        return default_timeout
    remaining = float(deadline_monotonic) - time.monotonic()
    if remaining <= 0.0:
        raise TimeoutError(f"deadline exhausted before {stage}")
    return min(default_timeout, remaining)


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    deadline_monotonic: float | None = None,
) -> bytes:
    timeout = _bounded_stage_timeout(
        deadline_monotonic,
        stage=" ".join(command[:3]) if command else "subprocess",
        default_timeout=240.0,
    )
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=input_bytes,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if completed.returncode != 0:
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{stdout[-4000:]}\n"
            f"stderr:\n{stderr[-4000:]}"
        )
    return completed.stdout


def _materialize_base_source(
    base_ref: str,
    base_root: Path,
    *,
    deadline_monotonic: float | None = None,
) -> None:
    """Export the pinned CLI source and its resources using a private Git index."""
    env = {**os.environ, "GIT_INDEX_FILE": str(base_root.with_suffix(".index"))}
    _run(
        ["git", "read-tree", base_ref],
        cwd=REPO_ROOT,
        env=env,
        deadline_monotonic=deadline_monotonic,
    )
    # Root files include build manifests. The probe itself comes from the
    # candidate, so docs, test fixtures and other product trees are unnecessary.
    paths = _run(
        ["git", "ls-files", "-z", "--", ":(top,glob)*", "loopx/", "packages/", "scripts/", "skills/"],
        cwd=REPO_ROOT,
        env=env,
        deadline_monotonic=deadline_monotonic,
    )
    if b"loopx/__init__.py" not in paths.split(b"\0"):
        raise ValueError(f"CLI comparison base {base_ref} has no LoopX Python source")
    base_root.mkdir()
    # Resolve checkout attributes from the historical index.
    _run(
        ["git", f"--work-tree={base_root}", "checkout-index",
         f"--prefix={base_root.as_posix()}/", "--stdin", "-z"],
        cwd=REPO_ROOT,
        env=env,
        input_bytes=paths,
        deadline_monotonic=deadline_monotonic,
    )


def _run_probe(
    *,
    source_root: Path,
    probe_runner: Path,
    probe_test: Path,
    semantics_source: Path,
    fixture_root: Path,
    receipt_path: Path,
    cwd: Path,
    enforce_budget: bool,
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    # This subprocess starts Python directly and pins product imports through
    # PYTHONPATH. Do not inherit the invoking launcher's release identity: that
    # would mix a base probe with head assets. Setting it to source_root is also
    # wrong here because rendered recovery commands would expose two different
    # checkout paths and turn path length into a false output regression.
    env.pop("LOOPX_RELEASE_ROOT", None)
    # Both revisions measure the same fixture, including implicit machine
    # defaults. Never let the operator's registry/configuration select a host.
    stable_root = budget_fixture_path(fixture_root)
    isolated_current = str(stable_root / "host-runtime")
    isolated_legacy = str(stable_root / "legacy-runtime")
    bootstrap = (
        "import pathlib, runpy, sys; from loopx import paths; "
        f"paths.DEFAULT_RUNTIME_ROOT=pathlib.Path({isolated_current!r}); "
        f"paths.LEGACY_RUNTIME_ROOT=pathlib.Path({isolated_legacy!r}); "
        "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    env["LOOPX_RUNTIME_ROOT"] = isolated_current
    command = [
            sys.executable, "-c", bootstrap,
            str(probe_runner),
            "--test-source",
            str(probe_test),
            "--semantics-source",
            str(semantics_source),
            "--fixture-root",
            str(fixture_root),
            "--receipt",
            str(receipt_path),
        ]
    if not enforce_budget:
        command.append("--measurement-only")
    _run(
        command,
        cwd=cwd,
        env=env,
    )


def _load_receipt(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"probe receipt must be an object: {path}")
    return payload


def _base_ceiling_observation(receipt: dict) -> dict:
    """Keep bounded base metrics when candidate enforcement stops the probe."""
    failures = []
    for row in receipt["rows"]:
        if row.get("variant_id") is not None:
            continue
        spec = CLI_OUTPUT_BUDGET_BY_ID.get(row["surface_id"])
        if spec is None or row["scenario"] not in spec.max_chars:
            continue
        limits = {
            "chars": spec.max_chars[row["scenario"]][row["format"]],
            "lines": spec.max_lines[row["scenario"]][row["format"]],
        }
        if any(row[key] > limit for key, limit in limits.items()):
            failures.append({
                "row_id": row["row_id"],
                "base": {key: row[key] for key in limits},
                "current_ceiling": limits,
            })
    return {
        "scope": "default_surface_baselines",
        "failure_count": len(failures),
        "rows": failures[:4],
        "omitted_count": max(0, len(failures) - 4),
    }


def _is_ancestor(
    ancestor: str,
    descendant: str,
    *,
    deadline_monotonic: float | None = None,
) -> bool:
    timeout = (
        _bounded_stage_timeout(
            deadline_monotonic,
            stage="git merge-base --is-ancestor",
            default_timeout=240.0,
        )
        if deadline_monotonic is not None
        else None
    )
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise RuntimeError(
        f"cannot compare CLI output refs {ancestor} and {descendant}: "
        f"{completed.stderr.strip()}"
    )


def _pin_probe_base_ref(
    base_ref: str,
    *,
    main_ref: str,
    deadline_monotonic: float | None = None,
) -> str:
    # CI may fetch a newer main after its candidate merge commit was created.
    # Compare that candidate's ancestry, not unrelated later output changes.
    timeout = (
        _bounded_stage_timeout(
            deadline_monotonic,
            stage="git pin base ref",
            default_timeout=240.0,
        )
        if deadline_monotonic is not None
        else None
    )
    command = (
        ["git", "merge-base", "--all", "--", base_ref, "HEAD"]
        if base_ref == main_ref else
        ["git", "rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}"]
    )
    completed = subprocess.run(
        command, cwd=REPO_ROOT, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    revisions = completed.stdout.splitlines()
    if completed.returncode != 0 or len(revisions) != 1:
        raise RuntimeError(
            f"cannot resolve one CLI output base for {base_ref}; "
            f"select an explicit --base-ref commit: {completed.stderr.strip()}"
        )
    return revisions[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-ref",
        default=os.environ.get("LOOPX_CLI_OUTPUT_BASE_REF", "origin/main"),
        help="Comparison base; the selected main branch uses its common ancestor with HEAD.",
    )
    parser.add_argument(
        "--main-ref",
        default=os.environ.get("LOOPX_CLI_OUTPUT_MAIN_REF", "origin/main"),
    )
    args = parser.parse_args()
    base_ref = select_cli_output_base_ref(
        args.base_ref,
        main_ref=args.main_ref,
        is_ancestor=_is_ancestor,
    )
    base_ref = _pin_probe_base_ref(base_ref, main_ref=args.main_ref)
    if not PROBE_TEST.is_file():
        raise RuntimeError(f"missing probe source: {PROBE_TEST.relative_to(REPO_ROOT)}")
    if not PROBE_RUNNER.is_file():
        raise RuntimeError(
            f"missing probe runner: {PROBE_RUNNER.relative_to(REPO_ROOT)}"
        )
    if not SEMANTICS_SOURCE.is_file():
        raise RuntimeError(
            f"missing probe semantics: {SEMANTICS_SOURCE.relative_to(REPO_ROOT)}"
        )

    with tempfile.TemporaryDirectory(prefix="loopx-cli-output-differential-") as temp:
        temp_root = Path(temp)
        base_root = temp_root / "base"
        probe_runner = temp_root / "cli_output_probe_runner.py"
        probe_test = temp_root / "cli_output_probe_fixture.py"
        semantics_source = temp_root / "cli_output_semantics.py"
        fixture_root = temp_root / "public-fixture"
        base_receipt = temp_root / "base-receipt.json"
        candidate_receipt = temp_root / "candidate-receipt.json"
        shutil.copy2(PROBE_RUNNER, probe_runner)
        # Freeze one caller workload for both arms. Each subprocess still
        # imports its own pinned product through source_root/PYTHONPATH.
        shutil.copy2(PROBE_TEST, probe_test)
        shutil.copy2(FIXTURE_SUPPORT, temp_root / FIXTURE_SUPPORT.name)
        shutil.copy2(SEMANTICS_SOURCE, semantics_source)

        _materialize_base_source(base_ref, base_root)
        _run_probe(
            source_root=base_root,
            probe_runner=probe_runner,
            probe_test=probe_test,
            semantics_source=semantics_source,
            fixture_root=fixture_root,
            receipt_path=base_receipt,
            cwd=temp_root,
            enforce_budget=False,
        )
        base_observation = _base_ceiling_observation(_load_receipt(base_receipt))
        if base_observation["failure_count"]:
            # Candidate assertions still run unchanged. These metrics are
            # attribution evidence, not a waiver for an over-budget base.
            print(
                "cli-output-base-current-ceilings "
                + json.dumps(
                    {"base_ref": base_ref, **base_observation},
                    sort_keys=True, separators=(",", ":"),
                ),
                flush=True,
            )
        _run_probe(
            source_root=REPO_ROOT,
            probe_runner=probe_runner,
            probe_test=probe_test,
            semantics_source=semantics_source,
            fixture_root=fixture_root,
            receipt_path=candidate_receipt,
            cwd=temp_root,
            enforce_budget=True,
        )

        comparison = compare_cli_output_receipts(
            _load_receipt(base_receipt),
            _load_receipt(candidate_receipt),
        )
        if not comparison["ok"]:
            failed = [
                {"row_id": row["row_id"], "failures": row["failures"]}
                for row in comparison["rows"]
                if row["status"] == "failed"
            ]
            raise AssertionError(
                f"agent-facing CLI base/head differential failed (base_ref={base_ref})\n"
                + json.dumps(failed, ensure_ascii=False, indent=2, sort_keys=True)
            )
        if comparison["review_required"]:
            review_signals = [
                {"row_id": row["row_id"], "review_signals": row["review_signals"]}
                for row in comparison["rows"]
                if row["review_signals"]
            ]
            print(
                "cli-output-base-head-differential review-signals "
                + json.dumps(review_signals, ensure_ascii=False, sort_keys=True)
            )
        print(
            "cli-output-base-head-differential-smoke ok "
            f"base_ref={base_ref} "
            f"base={comparison['base_row_count']} "
            f"candidate={comparison['candidate_row_count']} "
            f"candidate_only={comparison['candidate_only_row_count']} "
            f"review_required={comparison['review_row_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
