"""CI ownership, background cadence and PR/publication concurrency contracts."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())


def event(name: str, *, pr: int = 101, tag: str = "", backend: bool = True) -> dict:
    return {
        "github": SimpleNamespace(
            event_name=name,
            ref=f"refs/pull/{pr}/merge" if name == "pull_request" else "refs/heads/main",
            # Pull-request webhooks expose number at the payload root as well
            # as inside pull_request; the shared concurrency owner uses both.
            event=SimpleNamespace(number=pr, pull_request=SimpleNamespace(number=pr), release=SimpleNamespace(tag_name=tag)),
        ),
        "inputs": SimpleNamespace(tag=tag),
        "needs": SimpleNamespace(changes=SimpleNamespace(outputs=SimpleNamespace(backend_tests=str(backend).lower()))),
    }


def expression(value: str | bool, context: dict) -> str | bool:
    """Evaluate the boolean/string subset used by these concurrency conditions."""
    if isinstance(value, bool):
        return value

    def evaluate(source: str):
        return eval(  # noqa: S307 - repository-owned expressions, no builtins.
            source.strip().replace("&&", " and ").replace("||", " or "),
            {"__builtins__": {}, "format": lambda template, *args: template.format(*args)},
            context,
        )

    match = re.fullmatch(r"\$\{\{(.*?)\}\}", value)
    if match:
        return evaluate(match[1])
    if "${{" not in value:
        return evaluate(value)
    return re.sub(r"\$\{\{(.*?)\}\}", lambda item: str(evaluate(item[1])), value)


@pytest.mark.parametrize("name", ["dco", "ark-turn", "release-artifacts", "desktop-release-artifacts", "desktop-updater"])
def test_pr_supersession_is_scoped_to_one_pr_and_never_cancels_publication(name: str) -> None:
    concurrency = workflow(name)["concurrency"]
    first, second = event("pull_request", pr=101), event("pull_request", pr=102)
    assert expression(concurrency["group"], first) != expression(concurrency["group"], second)
    assert expression(concurrency["group"], first) == expression(concurrency["group"], event("pull_request", pr=101))
    assert expression(concurrency["cancel-in-progress"], first) is True
    for name in ("release", "schedule", "workflow_dispatch", "merge_group"):
        context = event(name, tag="v1.2.4")
        assert expression(concurrency["cancel-in-progress"], context) is False
        assert expression(concurrency["group"], first) != expression(concurrency["group"], context)


def test_signed_desktop_prs_do_not_share_either_publication_channel() -> None:
    concurrency = workflow("desktop-updater")["concurrency"]
    groups = [expression(concurrency["group"], context) for context in (
        event("pull_request", pr=101), event("pull_request", pr=102),
        event("release", tag="v1.2.4"), event("schedule"),
    )]
    assert len(set(groups)) == 4
    assert expression(concurrency["group"], event("schedule")) == expression(concurrency["group"], event("workflow_dispatch"))


def test_main_supersession_does_not_cancel_nightly_or_manual_full_qualification() -> None:
    policy = workflow("python-tests")
    assert "schedule" in policy[True]
    concurrency = policy["concurrency"]
    contexts = [event(name) for name in ("push", "pull_request", "schedule", "workflow_dispatch", "merge_group")]
    for context in contexts:
        if context["github"].event_name != "pull_request":
            del context["github"].event.number
    assert len({expression(concurrency["group"], context) for context in contexts}) == 5
    assert [expression(concurrency["cancel-in-progress"], context) for context in contexts] == [True, True, False, False, False]


@pytest.mark.parametrize("owner,name,prefix", [
    ("python-tests", "pull_request", "python-tests-pull_request"),
    ("ci-maintenance", "pull_request_target", "ci-maintenance"),
])
def test_python_pr_supersession_uses_the_payload_number(owner: str, name: str, prefix: str) -> None:
    concurrency = workflow(owner)["concurrency"]
    first, second = event(name, pr=101), event(name, pr=102)
    # pull_request_target uses the base ref, so the payload number must keep
    # distinct PRs apart even when their refs match.
    first["github"].ref = second["github"].ref = "refs/heads/main"
    assert expression(concurrency["group"], first) == f"{prefix}-pr-101"
    assert expression(concurrency["group"], second) == f"{prefix}-pr-102"
    assert expression(concurrency["cancel-in-progress"], first) is True


def test_cleanup_concurrency_cannot_replace_current_qualification() -> None:
    qualification = expression(workflow("python-tests")["concurrency"]["group"], event("pull_request"))
    cleanup = expression(workflow("ci-maintenance")["concurrency"]["group"], event("pull_request_target"))
    assert qualification != cleanup


def test_dashboard_unit_coverage_runs_once_while_all_browser_shards_remain_required() -> None:
    job = workflow("python-tests")["jobs"]["dashboard-browser"]
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == [1, 2, 3]
    assert job["strategy"]["fail-fast"] is False
    unit_steps = [step for step in job["steps"] if "npm run test:dashboard:coverage" in step.get("run", "")]
    assert len(unit_steps) == 1
    unit = unit_steps[0]
    assert [shard for shard in shards if expression(unit["if"], {"matrix": SimpleNamespace(shard=shard)})] == [1]
    browser = next(step for step in job["steps"] if "personal-workspace-browser-smoke.mjs" in step.get("run", ""))
    assert "if" not in browser
    assert browser["env"]["LOOPX_PERSONAL_WORKSPACE_SHARD"] == "${{ matrix.shard }}/3"
    for step in (unit, browser):
        assert "continue-on-error" not in step
    assert "continue-on-error" not in job


def test_required_merge_gate_workflow_is_not_suppressed_by_path_filters() -> None:
    # A required workflow omitted by a path filter can leave a PR pending. The
    # stable gate always starts; job-level exemptions handle unrelated changes.
    triggers = workflow("python-tests")[True]
    assert "pull_request" in triggers
    assert "paths" not in triggers["pull_request"]
    assert "paths-ignore" not in triggers["pull_request"]


@pytest.mark.parametrize("name,paths,expected", [
    ("release-artifacts", ["loopx/cli.py"], True),
    ("release-artifacts", ["docs/development/testing-and-quality.md"], False),
    ("release-artifacts", ["apps/presentation/dashboard/src/App.tsx"], True),
    ("desktop-release-artifacts", ["loopx/cli.py"], False),
    ("desktop-release-artifacts", ["apps/desktop/loopx-control-plane/src-tauri/src/main.rs"], True),
    ("desktop-release-artifacts", ["apps/presentation/dashboard/src/App.tsx"], True),
    ("desktop-updater", ["apps/presentation/dashboard/src/App.tsx"], False),
    ("desktop-updater", ["apps/desktop/loopx-control-plane/src-tauri/src/main.rs"], True),
    ("ark-turn", ["packages/loopx-ark-turn/src/loopx_ark_turn/adapter.py"], True),
    ("ark-turn", ["apps/presentation/dashboard/src/App.tsx", "docs/guide.md"], False),
    ("ark-turn", ["loopx/control_plane/turn_driver/runtime.py", "apps/presentation/dashboard/src/App.tsx"], True),
    ("frontstage-pages", ["docs/guide.md"], True),
    ("frontstage-pages", ["loopx/cli.py"], False),
])
def test_specialized_workflows_trigger_for_related_pr_paths(name: str, paths: list[str], expected: bool) -> None:
    patterns = workflow(name)[True]["pull_request"]["paths"]
    # Current filters use only literal paths and */**. In GitHub syntax a
    # single star cannot cross a directory; a double star can.
    assert all(not any(char in pattern for char in "!?+[") for pattern in patterns)
    regexes = [re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*") for pattern in patterns]
    assert any(re.fullmatch(regex, path) for regex in regexes for path in paths) is expected


@pytest.mark.parametrize("name", ["push", "pull_request", "merge_group", "schedule", "workflow_dispatch"])
@pytest.mark.parametrize("backend", [True, False])
def test_future_node_probe_is_background_only(name: str, backend: bool) -> None:
    probe = workflow("python-tests")["jobs"]["node-forward-compatibility"]
    assert probe["continue-on-error"] is True
    assert expression(probe["if"], event(name, backend=backend)) == (backend and name in {"schedule", "workflow_dispatch"})


def test_daily_smokes_keep_every_shard_and_verify_one_shared_source_artifact() -> None:
    policy = workflow("full-public-smokes")
    assert set(policy[True]) == {"schedule", "workflow_dispatch"}
    jobs = policy["jobs"]
    producer, consumers = jobs["chat-bundle"], jobs["full-public-smokes"]
    assert consumers["needs"] == "chat-bundle"
    assert consumers["strategy"]["max-parallel"] == 2
    assert consumers["strategy"]["fail-fast"] is False
    assert [row["shard"] for row in consumers["strategy"]["matrix"]["include"]] == list(range(5))
    build = [step for step in producer["steps"] if "chat_bundle.py build" in step.get("run", "")]
    assert len(build) == 1
    assert not any("chat_bundle.py build" in step.get("run", "") for step in consumers["steps"])
    upload = next(step for step in producer["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    download = next(step for step in consumers["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert upload["with"]["name"] == download["with"]["name"] == "full-public-chat-${{ github.sha }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == download["with"]["path"] == "loopx/web/chat/"
    verify = next(step for step in consumers["steps"] if step.get("run") == "python scripts/chat_bundle.py verify --source")
    run = next(step for step in consumers["steps"] if step.get("name") == "Run full-public shard")
    # Demo-readiness skips when the Dashboard compiler is absent. Shared build
    # output must not remove the consumer's local npm runtime preparation.
    dependencies = next(step for step in consumers["steps"]
                        if step.get("run") == "npm --prefix apps/presentation/dashboard ci --ignore-scripts")
    node = next(step for step in consumers["steps"] if step.get("uses", "").startswith("actions/setup-node@"))
    assert consumers["steps"].index(node) < consumers["steps"].index(dependencies) < consumers["steps"].index(run)
    assert "continue-on-error" not in dependencies
    assert consumers["steps"].index(download) < consumers["steps"].index(verify) < consumers["steps"].index(run)
    assert "continue-on-error" not in verify
    assert jobs["smoke-fleet-health"]["needs"] == "full-public-smokes"
