"""Isolated end-to-end demonstration of anti-entropy governance on a fixed refactor task.

For each arm the script executes a real isolated change inside a disposable git
sandbox: it seeds the case-study pre-state, writes the arm's fixed post-state,
captures the real unified diff with git, runs the case-study functional test
suite with pytest, and evaluates the anti-entropy oracle against the sandbox
workspace as the explicitly bound post-change source.

This is a deterministic demonstration on hand-authored post-state fixtures. It
launches no model runs and measures no agent-runtime behavior; functional-test
results below are real executions of the seeded case-study tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_STUDY_REL = "examples/anti_entropy/case_study"

sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.anti_entropy.cli import run_anti_entropy_verify  # noqa: E402

_CLIENT_POST = '''"""Consumer client migrated to the modern stream parser."""

from typing import Any
from .stream_parser import StreamMessageParser


class DataConsumer:
    """High-level data consumer."""

    def __init__(self) -> None:
        self._parser = StreamMessageParser()

    def process_message(self, raw_input: str) -> dict[str, Any]:
        result = self._parser.parse(raw_input)
        return {
            "status": "ok",
            "content": result["payload"],
            "length": result["size"],
        }
'''

_LEGACY_ZOMBIE_POST = '''# Deprecated: do not use
# def parse_legacy_frame(raw_data: str) -> dict[str, Any]:
#     return LegacyMessageParser().decode(raw_data)
class LegacyMessageParser:
    pass
'''

_CASE_STUDY_FILES = (
    "client.py",
    "legacy_parser.py",
    "stream_parser.py",
    "tests/test_client.py",
)

# Each arm is a fixed hand-authored post-state applied to the seeded pre-state.
# None means the file is deleted by this arm.
_ARM_POST_STATES: dict[str, dict[str, str | None]] = {
    "Arm 1 (Baseline Pattern: legacy file kept)": {
        f"{CASE_STUDY_REL}/client.py": _CLIENT_POST,
    },
    "Arm 2 (Prompt-Only Pattern: zombie + ghost comments)": {
        f"{CASE_STUDY_REL}/client.py": _CLIENT_POST,
        f"{CASE_STUDY_REL}/legacy_parser.py": _LEGACY_ZOMBIE_POST,
    },
    "Arm 3 (Anti-Entropy Pattern: clean /dev/null deletion)": {
        f"{CASE_STUDY_REL}/client.py": _CLIENT_POST,
        f"{CASE_STUDY_REL}/legacy_parser.py": None,
    },
}


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        env=env,
        text=False,
    )
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    return proc.returncode, output


def _seed_sandbox(sandbox: Path) -> None:
    """Seed the case-study pre-state and commit it as the sandbox baseline."""
    case_study_src = REPO_ROOT / CASE_STUDY_REL
    for rel in _CASE_STUDY_FILES:
        target = sandbox / CASE_STUDY_REL / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(case_study_src / rel, target)
    # The task spec is the authoritative input declaring the retirement basis.
    shutil.copy2(case_study_src / "task_spec.json", sandbox / CASE_STUDY_REL / "task_spec.json")
    _run(["git", "init", "-q"], sandbox)
    _run(["git", "add", "-A"], sandbox)
    rc, out = _run(
        [
            "git",
            "-c",
            "user.name=anti-entropy-demo",
            "-c",
            "user.email=demo@localhost",
            "commit",
            "-qm",
            "seed case-study pre-state",
        ],
        sandbox,
    )
    if rc != 0:
        raise RuntimeError(f"sandbox seed commit failed: {out}")


def _capture_diff(sandbox: Path) -> str:
    rc, diff_text = _run(["git", "diff"], sandbox)
    if rc != 0:
        raise RuntimeError(f"git diff failed in sandbox: {diff_text}")
    return diff_text


def _run_functional_tests(sandbox: Path) -> tuple[str, str]:
    test_rel = f"{CASE_STUDY_REL}/tests/test_client.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(sandbox)
    rc, output = _run([sys.executable, "-m", "pytest", test_rel, "-q"], sandbox, env=env)
    if rc == 0:
        return "passed (executed in sandbox)", output
    return f"failed (exit {rc}, executed in sandbox)", output


def evaluate_arm(name: str, post_state: dict[str, str | None], basis_data: dict) -> dict:
    sandbox = Path(tempfile.mkdtemp(prefix="ae_arm_"))
    try:
        _seed_sandbox(sandbox)
        for rel, content in post_state.items():
            target = sandbox / rel
            if content is None:
                target.unlink()
            else:
                target.write_text(content, encoding="utf-8")

        diff_text = _capture_diff(sandbox)
        tests_result, tests_output = _run_functional_tests(sandbox)
        result = run_anti_entropy_verify(
            diff_text=diff_text,
            basis_data=basis_data,
            goal_id=f"fixture-{abs(hash(name)) % 10000}",
            workspace=sandbox,
        )

        lines_added = sum(
            1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        lines_removed = sum(
            1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---")
        )

        return {
            "arm": name,
            "functional_tests": tests_result,
            "functional_tests_output_tail": tests_output.strip().splitlines()[-1:] or [""],
            "anti_entropy_verdict": result["verdict"],
            "retired_symbols": result["retired_symbols"],
            "blocking_findings": result["blocking_finding_count"],
            "advisory_findings": result["advisory_finding_count"],
            "findings": [f["message"] for f in result["receipt"]["findings"]],
            "net_diff_lines": lines_added - lines_removed,
            "source_evidence": result["receipt"]["source_evidence"],
        }
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def main() -> int:
    spec_path = REPO_ROOT / CASE_STUDY_REL / "task_spec.json"
    if not spec_path.is_file():
        print(f"Notice: task specification not found at {spec_path}, demonstration skipped.")
        return 0

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    basis = spec.get("retirement_basis", {})

    objective = spec.get("objective", "")
    target_symbols = basis.get("target_symbols", [])
    target_paths = basis.get("target_paths", [])

    print("================================================================================")
    print("Anti-Entropy Governance Isolated Sandbox Demonstration")
    print("================================================================================")
    print(f"Task Objective: {objective}")
    print(f"Declared Retirement Targets: {target_symbols}")
    print(f"Declared Target Paths to Delete: {target_paths}")
    print("Each arm: seeded git sandbox -> fixed post-state -> real git diff ->")
    print("real pytest run -> oracle verify bound to the sandbox workspace.")
    print("--------------------------------------------------------------------------------\n")

    for arm_name, post_state in _ARM_POST_STATES.items():
        res = evaluate_arm(arm_name, post_state, basis)
        print(f"[{res['arm']}]")
        print(f"  • Functional Tests:       {res['functional_tests']}")
        if res["functional_tests_output_tail"]:
            print(f"    - pytest tail:          {res['functional_tests_output_tail'][0]}")
        print(f"  • Anti-Entropy Verdict:   {res['anti_entropy_verdict']}")
        print(f"  • Evidence Binding:       {res['source_evidence']}")
        print(f"  • Net Diff Lines:         {res['net_diff_lines']:+} lines")
        print(f"  • Cleanly Retired:        {res['retired_symbols']}")
        print(f"  • Blocking Violations:    {res['blocking_findings']}")
        print(f"  • Advisory Hygiene Flaws: {res['advisory_findings']}")
        if res["findings"]:
            for f in res["findings"]:
                print(f"    - Finding: {f}")
        print()

    print("================================================================================")
    print("Demonstration Reading (oracle semantics only, no agent-runtime measurement):")
    print("1. Functional tests alone pass whenever the caller migrates, in every arm.")
    print("2. Only the oracle binds the declared retirement basis to complete post-change")
    print("   sources: kept legacy files and zombie definitions stay blocking (AE001),")
    print("   active references stay blocking (AE002), and 3+ line commented-out code")
    print("   blocks stay advisory (AE003).")
    print("3. Arm 3 achieves verified retirement: clean /dev/null deletion, zero blocking")
    print("   findings, retired symbols certified against the bound workspace.")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
