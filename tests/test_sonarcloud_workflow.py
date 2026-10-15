from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

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


def _assert_analysis_boundary(workflow: dict, caller: dict) -> None:
    # A trusted metadata-only cleanup event must not qualify Python or Sonar.
    # Analysis still depends on successful coverage in this run.
    assert set(workflow["on"]) == {"workflow_call"}
    sonar = caller["jobs"]["sonar"]
    assert sonar["needs"] == "pytest"
    assert sonar["uses"] == "./.github/workflows/sonarcloud.yml"
    # Without a status function GitHub retains the default success() check.
    assert sonar.get("if") == "github.event_name != 'merge_group'"
    for action in ("closed", "synchronize"):
        github = SimpleNamespace(event_name="pull_request_target",
                                 event=SimpleNamespace(action=action))
        context = {"github": github, "always": lambda: True}

        def condition(expression: str) -> bool:
            # These two shipped conditions use string comparisons and Boolean
            # operators only, as in the existing executable CI gate tests.
            source = expression.replace("&&", "and").replace("||", "or")
            source = source.replace("!cancelled()", "not cancelled()")
            return bool(eval(source, {"__builtins__": {}}, {**context, "cancelled": lambda: False}))

        classified = condition(caller["jobs"]["changes"]["if"])
        # If classification were reachable, assume its most permissive output.
        # Skipped jobs cannot supply that output to this run's dependencies.
        context["needs"] = SimpleNamespace(changes=SimpleNamespace(
            outputs=SimpleNamespace(python_tests="true" if classified else "")))
        assert not condition(caller["jobs"]["pytest"]["if"])


def test_sonar_reuses_same_run_coverage_and_isolated_trigger() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    caller = WORKFLOW.with_name("python-tests.yml").read_text(encoding="utf-8")

    _assert_analysis_boundary(yaml.load(workflow, Loader=yaml.BaseLoader),
                              yaml.load(caller, Loader=yaml.BaseLoader))
    assert "python -m pytest" not in workflow
    assert "name: python-coverage-xml" in workflow
    assert "run-id:" not in workflow
    assert "github-token:" not in workflow
    assert '"apps/**"' in caller
    assert '"sonar-project.properties"' in caller


@pytest.mark.parametrize("unsafe", ["trigger", "classification", "coverage", "status"])
def test_reachable_privileged_analysis_or_coverage_bypass_is_rejected(unsafe) -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    caller = yaml.load(WORKFLOW.with_name("python-tests.yml").read_text(), Loader=yaml.BaseLoader)
    if unsafe == "trigger":
        workflow["on"]["pull_request_target"] = {}
    elif unsafe == "classification":
        caller["jobs"]["changes"]["if"] = "always()"
    elif unsafe == "coverage":
        caller["jobs"]["pytest"]["if"] = "always()"
    else:
        caller["jobs"]["sonar"]["if"] = "always()"
    with pytest.raises(AssertionError):
        _assert_analysis_boundary(workflow, caller)


def test_separate_trusted_cleanup_does_not_activate_analysis() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    caller = yaml.load(WORKFLOW.with_name("python-tests.yml").read_text(), Loader=yaml.BaseLoader)
    maintenance = yaml.load(WORKFLOW.with_name("ci-maintenance.yml").read_text(), Loader=yaml.BaseLoader)
    assert "pull_request_target" not in caller["on"]
    assert maintenance["on"]["pull_request_target"]["types"] == ["closed", "synchronize"]
    for job in maintenance["jobs"].values():
        assert "uses" not in job and "secrets" not in job
        assert all(not step.get("uses", "").startswith("actions/checkout@") for step in job["steps"])
    _assert_analysis_boundary(workflow, caller)
