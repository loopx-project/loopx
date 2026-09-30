from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "sonarcloud.yml"


def _workflow_steps(workflow: str) -> list[str]:
    return re.findall(
        r"(?ms)^      - name: .+?(?=^      - name: |\Z)",
        workflow,
    )


def test_missing_sonar_token_reaches_a_successful_skip_step() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "if: ${{ secrets.SONAR_TOKEN != '' }}" not in workflow
    assert "id: sonar-token" in workflow
    assert "available=false" in workflow
    assert "if: steps.sonar-token.outputs.available != 'true'" in workflow
    assert "non-blocking analysis skipped" in workflow


def _assert_token_guards(workflow: str) -> None:
    sensitive_actions = (
        "uses: actions/checkout@",
        "uses: actions/download-artifact@",
        "uses: SonarSource/sonarqube-scan-action@",
    )
    steps = _workflow_steps(workflow)
    for action in sensitive_actions:
        matching_steps = [step for step in steps if action in step]
        assert matching_steps
        assert all(
            "if: steps.sonar-token.outputs.available == 'true'" in step
            for step in matching_steps
        )


def test_sonar_steps_remain_guarded_by_the_token() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    _assert_token_guards(workflow)
    assert workflow.count("SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}") == 2
    assert "\n    env:\n      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}" not in workflow


def test_each_missing_analysis_guard_is_rejected() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    guard = "        if: steps.sonar-token.outputs.available == 'true'\n"
    fragments = workflow.split(guard)
    assert len(fragments) > 1
    for index in range(len(fragments) - 1):
        mutant = guard.join(fragments[:index + 1]) + guard.join(fragments[index + 1:])
        with pytest.raises(AssertionError):
            _assert_token_guards(mutant)


def test_sonar_reuses_same_run_coverage_without_a_privileged_trigger() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    caller = WORKFLOW.with_name("python-tests.yml").read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert "workflow_run:" not in workflow
    assert "pull_request_target:" not in workflow + caller
    assert "python -m pytest" not in workflow
    assert "name: python-coverage-xml" in workflow
    assert "run-id:" not in workflow
    assert "github-token:" not in workflow
    sonar = yaml.safe_load(caller)["jobs"]["sonar"]
    # Coverage comes from this run's pytest job, handed to the local reusable
    # workflow. The only condition may skip merge-queue refs; a status function
    # such as always() would let analysis run without that coverage.
    assert sonar["needs"] == "pytest"
    assert sonar["uses"] == "./.github/workflows/sonarcloud.yml"
    assert sonar.get("if") == "github.event_name != 'merge_group'"
    assert '"apps/**"' in caller
    assert '"sonar-project.properties"' in caller
