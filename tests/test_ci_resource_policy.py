"""CI ownership, background cadence and PR/publication concurrency contracts."""
from __future__ import annotations

import re
import json
import subprocess
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
            event=SimpleNamespace(action="opened", number=pr, pull_request=SimpleNamespace(number=pr), release=SimpleNamespace(tag_name=tag)),
        ),
        "inputs": SimpleNamespace(tag=tag),
        "cancelled": lambda: False,
        "needs": SimpleNamespace(chat_bundle=SimpleNamespace(result="success"), changes=SimpleNamespace(outputs=SimpleNamespace(**{
            key: str(backend).lower()
            for key in (
                "backend_tests",
                "core_tests",
                "frontend_tests",
                "compat_tests",
                "python_tests",
                "stage2c_tests",
                "presentation_tests",
            )
        }))),
    }


def expression(value: str | bool, context: dict) -> str | bool:
    """Evaluate the boolean/string subset used by these CI admission conditions."""
    if isinstance(value, bool):
        return value

    def evaluate(source: str):
        source = source.replace("needs.chat-bundle", "needs.chat_bundle")
        return eval(  # noqa: S307 - repository-owned expressions, no builtins.
            re.sub(r"!(?!=)", " not ", source.strip()).replace("&&", " and ").replace("||", " or "),
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


def test_main_finishes_active_qualification_and_keeps_only_the_latest_pending_successor() -> None:
    policy = workflow("python-tests")
    assert "schedule" in policy[True]
    concurrency = policy["concurrency"]
    contexts = [event(name) for name in ("push", "pull_request", "schedule", "workflow_dispatch", "merge_group")]
    for context in contexts:
        if context["github"].event_name != "pull_request":
            del context["github"].event.number
    assert len({expression(concurrency["group"], context) for context in contexts}) == 5
    assert [expression(concurrency["cancel-in-progress"], context) for context in contexts] == [False, True, False, False, False]
    assert concurrency["queue"] == "single"


@pytest.mark.parametrize("job_id", ["checks", "pytest", "stage2c-correctness-e2e", "merge-gate"])
@pytest.mark.parametrize("name", ["push", "pull_request", "pull_request_target", "schedule", "workflow_dispatch", "merge_group"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("planned", [False, True])
def test_cancelled_main_aggregates_stop_while_other_event_gates_remain_required(
    job_id: str, name: str, cancelled: bool, planned: bool,
) -> None:
    context = event(name, backend=planned)
    # always() also models failed upstream work: these jobs must still run
    # their real rejection commands unless the main push itself is cancelled.
    context.update(always=lambda: True, cancelled=lambda: cancelled)
    expected = planned if job_id != "merge-gate" else name != "pull_request_target"
    if name == "push" and cancelled:
        expected = False
    assert expression(workflow("python-tests")["jobs"][job_id]["if"], context) is expected


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


@pytest.mark.parametrize("action", ["opened", "reopened", "edited", "synchronize", "closed"])
def test_diff_inspection_uses_trusted_code_and_read_only_scope(action: str) -> None:
    policy = workflow("ci-maintenance")
    job = policy["jobs"]["inspect-pr-diff"]
    context = event("pull_request_target")
    context["github"].event.action = action
    assert action in policy[True]["pull_request_target"]["types"]
    assert expression(job["if"], context) is (action != "closed")
    assert policy["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert job["timeout-minutes"] <= 2
    checkout, inspect = job["steps"]
    assert checkout["uses"] == "actions/checkout@v7"
    assert checkout["with"] == {
        "ref": "${{ github.workflow_sha }}",
        "fetch-depth": 1,
        "sparse-checkout": "scripts/ci",
        "persist-credentials": False,
    }
    assert inspect["run"] == "python3 scripts/ci/review_gate.py inspect-pr-diff"
    assert inspect["env"] == {"GH_TOKEN": "${{ github.token }}"}


def test_published_diff_verification_supplies_required_merge_gate() -> None:
    jobs = workflow("python-tests")["jobs"]
    changes = jobs["changes"]
    classify = next(step for step in changes["steps"] if step.get("id") == "classify")
    assert changes["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert "--base \"$BASE_SHA\" --head \"$HEAD_SHA\" --verify-pr-diff" in classify["run"]
    assert "changes" in jobs["merge-gate"]["needs"]
    assert any(step.get("run") == "python scripts/ci/review_gate.py verify"
               for step in jobs["merge-gate"]["steps"])


@pytest.mark.parametrize("mode", ["delayed", "never", "changed"])
def test_obsolete_run_cancellation_waits_for_verified_completion(mode: str) -> None:
    script = workflow("ci-maintenance")["jobs"]["cancel-obsolete-pr"]["steps"][0]["with"]["script"]
    harness = r'''
let input = ''; for await (const chunk of process.stdin) input += chunk;
const {script, mode} = JSON.parse(input);
let clock = 0; const calls = [], notes = [];
Date.now = () => clock;
globalThis.setTimeout = callback => {clock += 1000; callback();};
const pr = {state:'open', head:{sha:'current', ref:'fixture', label:'owner:fixture', repo:{id:1}}};
const context = {repo:{owner:'owner', repo:'fixture'}, payload:{number:1, action:'synchronize',
  before:'old', after:'current', pull_request:pr}};
const github = {rest:{pulls:{get:async()=>({data:mode==='changed' && clock>=5000
  ? {...pr,head:{...pr.head,sha:'newer'}} : pr}),list:'pulls'},actions:{listWorkflowRuns:'runs',
  cancelWorkflowRun:async params=>calls.push(['cancel',params.run_id]),
  getWorkflowRun:async()=>({data:{status:mode==='delayed' && clock>=35000?'completed':'in_progress'}})}},
  paginate:async endpoint=>endpoint==='pulls' ? [] : [{id:7,status:'in_progress',head_repository:{id:1},
    head_branch:'fixture',head_sha:'old',created_at:'2026-01-01T00:00:00Z',pull_requests:[{number:1}]}],
  request:async(_route,params)=>calls.push(['force',params.run_id])};
let error = null;
try {await new (Object.getPrototypeOf(async function(){}).constructor)('github','context','core',script)
  (github,context,{info:message=>notes.push(message)});} catch(e) {error=e.message;}
process.stdout.write(JSON.stringify({clock,calls,notes,error}));
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", harness],
        input=json.dumps({"script": script, "mode": mode}), text=True,
        capture_output=True, check=True, timeout=10,
    )
    observed = json.loads(result.stdout)
    assert observed["calls"] == [["cancel", 7], ["force", 7]]
    if mode == "delayed":
        assert observed["error"] is None
        assert 35000 <= observed["clock"] <= 61000
        assert any("Retired obsolete" in note for note in observed["notes"])
    elif mode == "never":
        assert "has not completed" in observed["error"]
        assert observed["clock"] <= 62000
    else:
        assert observed["error"] is None
        assert observed["clock"] <= 6000
        assert not any("Retired obsolete" in note for note in observed["notes"])


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
    ("release-artifacts", ["loopx/cli.py"], False),
    ("release-artifacts", ["README.md"], False),
    ("release-artifacts", ["docs/development/testing-and-quality.md"], False),
    ("release-artifacts", ["apps/presentation/dashboard/src/App.tsx"], False),
    ("release-artifacts", ["scripts/release_artifacts.py"], True),
    ("release-artifacts", ["pyproject.toml"], True),
    ("package-smoke", ["loopx/cli.py"], False),
    ("package-smoke", ["README.md"], False),
    ("package-smoke", ["packages/loopx-core/prepare.py"], True),
    ("package-smoke", ["apps/presentation/dashboard/src/App.tsx"], True),
    ("package-smoke", ["scripts/chat_bundle.py"], True),
    ("package-smoke", ["tests/test_core_benchmark_distribution.py"], True),
    ("package-smoke", ["loopx/extensions/runtime.py"], True),
    ("package-smoke", ["pyproject.toml"], True),
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


def test_nightly_schedules_exist_and_release_publication_never_fires_on_schedule() -> None:
    for name in ("python-tests", "release-artifacts", "package-smoke"):
        triggers = workflow(name)[True]
        assert "schedule" in triggers and triggers["schedule"], name

    release = workflow("release-artifacts")
    for job_name in ("upload-release", "publish-pypi"):
        cond = release["jobs"][job_name]["if"]
        assert "github.event_name == 'release'" in cond
        assert "github.event_name == 'workflow_dispatch'" in cond
        assert "github.event_name != 'pull_request'" not in cond
    build_attest = next(
        step for step in release["jobs"]["build"]["steps"]
        if step.get("name") == "Attest release packages and checksum manifest"
    )
    assert "github.event_name == 'release'" in build_attest["if"]
    assert "github.event_name == 'workflow_dispatch'" in build_attest["if"]

    package_smoke = workflow("package-smoke")
    assert package_smoke["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_package_smoke_prepares_each_distribution_output_once_before_workspace() -> None:
    steps = workflow("package-smoke")["jobs"]["core"]["steps"]
    prepare_outputs: list[str] = []
    for step in steps:
        normalized = re.sub(r"\\\n\s*", " ", str(step.get("run", "")))
        for line in normalized.splitlines():
            if "packages/loopx-core/prepare.py" in line:
                match = re.search(r'--output\s+"([^"]+)"|--output\s+(\S+)', line)
                assert match is not None, line
                prepare_outputs.append(match.group(1) or match.group(2))
    assert len(prepare_outputs) == len(set(prepare_outputs)), prepare_outputs
    assert prepare_outputs.count("$RUNNER_TEMP/benchmark-source") == 1
    toolkit_indices = [
        idx for idx, step in enumerate(steps)
        if "loopx-capability-benchmark-toolkit" in str(step.get("run", ""))
    ]
    workspace_indices = [
        idx for idx, step in enumerate(steps)
        if "loopx-workspace" in str(step.get("run", ""))
    ]
    assert len(toolkit_indices) == 1
    assert len(workspace_indices) == 1
    assert toolkit_indices[0] < workspace_indices[0]
