"""An intentionally exempt job must be skipped; all required jobs must pass."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import yaml
from review_gate import CHECK_OUTPUT, JOB_OUTPUT, requires_core_tests, verify, verify_checks

# Independent acceptance: client-only work retains compiled frontend/browser
# qualification but does not claim backend conformance or coverage.
PROFILE_FLAGS = {
    "docs": dict(core_tests=False, backend_tests=False, python_tests=False, stage2c_tests=False, presentation_tests=False),
    "presentation": dict(core_tests=True, backend_tests=False, python_tests=False, stage2c_tests=False, presentation_tests=True),
    "full": dict(core_tests=True, backend_tests=True, python_tests=True, stage2c_tests=True, presentation_tests=False),
}
PROFILE_JOBS = {
    "docs": set(),
    "presentation": {"checks", "presentation", "dashboard-acceptance", "chat-bundle-browser"},
    "full": {"checks", "pytest", "node-minimum-compatibility", "stage2c-correctness-e2e",
             "windows-powershell", "kernel-static-checks", "typescript-coverage",
             "dashboard-acceptance", "chat-bundle-browser"},
}


def needs(kind="full", *, presentation=False, jobs=JOB_OUTPUT):
    flags = {**PROFILE_FLAGS[kind], "presentation_tests": kind == "presentation" or presentation}
    required = PROFILE_JOBS[kind] | ({"presentation"} if presentation else set())
    return {"changes": {"result": "success", "outputs": {"change_kind": kind,
        **{key: str(value).lower() for key, value in flags.items()}}},
        **{job: {"result": "success" if job in required else "skipped"} for job in jobs}}


class GateTests(unittest.TestCase):
    def test_closed_pr_cancels_same_lane_and_skips_qualification(self):
        workflow = yaml.load(
            (Path(__file__).resolve().parents[2] / ".github/workflows/python-tests.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        maintenance = yaml.load(
            (Path(__file__).resolve().parents[2] / ".github/workflows/ci-maintenance.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        self.assertIn("closed", workflow["on"]["pull_request"]["types"])
        self.assertNotIn("pull_request_target", workflow["on"])
        self.assertEqual(maintenance["on"], {"pull_request_target": {"types": ["closed", "synchronize"]}})
        self.assertNotEqual(workflow["name"], maintenance["name"])
        concurrency = workflow["concurrency"]
        jobs = workflow["jobs"]

        # These workflow clauses use only string comparisons and Boolean logic.
        # Exercise the configured clauses against GitHub's documented ref change.
        def expression(value, action, *, number=17, event="pull_request"):
            ref = "refs/heads/main" if action == "closed" or event != "pull_request" else f"refs/pull/{number}/merge"
            github = SimpleNamespace(event_name=event, ref=ref, event=SimpleNamespace(
                action=action, number=number, pull_request=None))
            return eval(value.strip().removeprefix("${{").removesuffix("}}").strip()
                        .replace("&&", "and").replace("||", "or"),
                        {"__builtins__": {}}, {"github": github, "always": lambda: True,
                            "format": lambda template, number: template.format(number)})

        def lane(action, **kwargs):
            return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(expression(match[0], action, **kwargs)),
                          concurrency["group"])

        self.assertEqual(lane("synchronize"), lane("closed"))
        self.assertNotEqual(lane("closed"), lane("closed", number=18))
        self.assertNotEqual(lane("closed"), lane("closed", event="push"))
        for action in ("opened", "synchronize", "reopened", "closed"):
            self.assertTrue(expression(concurrency["cancel-in-progress"], action))
            for name in ("changes", "merge-gate"):
                self.assertEqual(bool(expression(jobs[name]["if"], action)), action != "closed")
        for event in ("push", "schedule", "workflow_dispatch", "merge_group"):
            for name in ("changes", "merge-gate"):
                self.assertTrue(expression(jobs[name]["if"], "", event=event))
        self.assertEqual([name for name, job in jobs.items() if not job.get("needs")],
                         ["changes"])
        for name in ("changes", "merge-gate"):
            self.assertFalse(expression(jobs[name]["if"], "closed", event="pull_request_target"))
        self.assertEqual(list(maintenance["jobs"]), ["cancel-obsolete-pr"])
        cleanup = maintenance["jobs"]["cancel-obsolete-pr"]
        self.assertTrue(expression(cleanup["if"], "closed", event="pull_request_target"))
        self.assertTrue(expression(cleanup["if"], "synchronize", event="pull_request_target"))
        self.assertFalse(expression(cleanup["if"], "closed"))
        self.assertFalse(expression(cleanup["if"], "reopened", event="pull_request_target"))
        self.assertNotEqual(lane("closed", event="pull_request_target"),
                            lane("closed", number=18, event="pull_request_target"))
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(maintenance["permissions"], {"contents": "read"})
        self.assertEqual(cleanup["permissions"],
                         {"actions": "write", "contents": "read", "pull-requests": "read"})
        self.assertEqual(len(cleanup["steps"]), 1)
        self.assertEqual(cleanup["steps"][0]["uses"], "actions/github-script@v8")
        for name, job in jobs.items():
            if "always()" in job.get("if", "") and name != "merge-gate":
                self.assertIn("needs.changes.outputs.", job["if"])
                self.assertIn("== 'true'", job["if"])

    def test_trusted_cleanup_retires_obsolete_heads_without_skipping_required_gates(self):
        workflow = yaml.load(
            (Path(__file__).resolve().parents[2] / ".github/workflows/ci-maintenance.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        script = workflow["jobs"]["cancel-obsolete-pr"]["steps"][0]["with"]["script"]
        # Execute the shipped transport script, independently supplying legal and
        # illegal lifecycle fixtures. No token or untrusted PR code is involved.
        harness = r"""
import assert from 'node:assert/strict';
const script = JSON.parse(process.argv[1]);
const execute = new (Object.getPrototypeOf(async function(){}).constructor)(
  'github', 'context', 'core', 'setTimeout', script);
const pr = {state:'closed', closed_at:'2026-01-02T00:00:00Z',
  head:{repo:{id:7}, ref:'fix', label:'contributor:fix', sha:'head'}};
const old = {id:1, status:'queued', head_repository:{id:7}, head_branch:'fix',
  head_sha:'head', created_at:'2026-01-01T00:00:00Z', pull_requests:[]};
async function run({first=pr, current=first, open=[], runs=[old], ordinaryDone=false,
                    conflict=false, stuck=false, failure=false, action='closed',
                    eventHead=first.head.sha, beforeForce=current,
                    beforeCancelOpen=open, beforeForceOpen=beforeCancelOpen,
                    refreshFailure=false}={}) {
  const cancelled=[], forced=[], queries=[]; let reads=0;
  let branchScope=open, openReads=0;
  const github = {rest:{pulls:{get:async()=>({data:reads++ ? (reads > 2 ? beforeForce : current) : first}), list:'pulls'},
    actions:{listWorkflowRuns:'runs',
      cancelWorkflowRun:async p=>{cancelled.push(p.run_id); if(conflict || failure)
        throw Object.assign(new Error('provider failure'), {status:conflict ? 409 : 403});
        branchScope=beforeForceOpen;},
      getWorkflowRun:async p=>({data:{status:(conflict || ordinaryDone ||
        (forced.includes(p.run_id) && !stuck)) ? 'completed' : 'queued'}})}},
    paginate:async (method,params)=>{queries.push([method,params]);
      if(method==='pulls') {
        if(openReads++ && refreshFailure) throw new Error('branch read unavailable');
        return branchScope;
      }
      branchScope=beforeCancelOpen; return runs;},
    request:async (route,p)=>{assert.equal(route,
      'POST /repos/{owner}/{repo}/actions/runs/{run_id}/force-cancel'); forced.push(p.run_id);}};
  await execute(github, {repo:{owner:'owner',repo:'repo'},payload:{number:17,action,pull_request:{head:{sha:eventHead}}}},
    {info:()=>{}}, fn=>fn());
  return {cancelled,forced,queries};
}
const selected = await run({runs:[old,
  {...old,id:2,pull_requests:[{number:17}],head_sha:'previous'},
  {...old,id:3,status:'completed'}, {...old,id:4,head_repository:{id:8}},
  {...old,id:5,created_at:'2026-01-03T00:00:00Z'},
  {...old,id:6,pull_requests:[{number:18}]}, {...old,id:7,head_sha:'unknown'},
  {...old,id:8,head_branch:'another'}, {...old,id:9,created_at:'invalid'},
  {...old,id:10,head_sha:'previous',created_at:'2026-01-03T00:00:00Z',pull_requests:[{number:17}]}]});
// A delayed webhook can create qualification after the PR has closed. Only
// its exact closed head may cross the close-time cutoff, never an older head.
assert.deepEqual(selected.cancelled,[1,2,5]); assert.deepEqual(selected.forced,[1,2,5]);
assert.equal(selected.queries[1][1].workflow_id,'python-tests.yml');
assert.equal(selected.queries[1][1].event,'pull_request');
assert.equal(selected.queries[1][1].branch,'fix');
for (const fixture of [{first:{...pr,state:'open'}}, {current:{...pr,state:'open'}},
  {current:{...pr,closed_at:'2026-01-04T00:00:00Z'}},
  {current:{...pr,head:{...pr.head,sha:'new'}}}, {open:[{number:18}]}]) {
  assert.deepEqual((await run(fixture)).cancelled,[]);
}
const active = {...pr,state:'open',closed_at:null};
const associatedOld = {...old,head_sha:'previous',pull_requests:[{number:17}]};
const updated = await run({action:'synchronize',first:active,open:[{number:17}],
  runs:[associatedOld, {...associatedOld,id:2,head_sha:'head'},
    {...associatedOld,id:3,pull_requests:[]}, {...associatedOld,id:4,pull_requests:[{number:18}]},
    {...associatedOld,id:5,head_repository:{id:8}}, {...associatedOld,id:6,head_branch:'another'},
    {...associatedOld,id:7,head_sha:''}, {...associatedOld,id:8,status:'completed'}]});
assert.deepEqual(updated.cancelled,[1]); assert.deepEqual(updated.forced,[1]);
for (const fixture of [{eventHead:'stale'}, {current:pr},
  {current:{...active,head:{...active.head,sha:'new'}}}, {open:[{number:18}]},
  {current:{...active,head:{...active.head,repo:{id:8}}}},
  {current:{...active,head:{...active.head,ref:'another'}}}]) {
  assert.deepEqual((await run({action:'synchronize',first:active,runs:[associatedOld],...fixture})).cancelled,[]);
}
const changedDuringCancel = await run({action:'synchronize',first:active,runs:[associatedOld],
  beforeForce:{...active,head:{...active.head,sha:'previous'}}});
assert.deepEqual(changedDuringCancel.cancelled,[1]); assert.deepEqual(changedDuringCancel.forced,[]);
for (const fixture of [{beforeCancelOpen:[{number:18}]},
  {action:'synchronize',first:active,open:[{number:17}],runs:[associatedOld],
   beforeCancelOpen:[{number:17},{number:18}]}]) {
  assert.deepEqual((await run(fixture)).cancelled,[]);
}
for (const fixture of [{beforeForceOpen:[{number:18}]},
  {action:'synchronize',first:active,open:[{number:17}],runs:[associatedOld],
   beforeForceOpen:[{number:17},{number:18}]}]) {
  const changedBranch = await run(fixture);
  assert.deepEqual(changedBranch.cancelled,[1]);
  assert.deepEqual(changedBranch.forced,[]);
}
await assert.rejects(run({refreshFailure:true}),/branch read unavailable/);
// A fresh retry after the sibling closes can retire the original run.
assert.deepEqual((await run()).forced,[1]);
const movedOwner = await run({current:{...pr,head:{...pr.head,label:'moved:fix'}}});
assert.deepEqual(movedOwner.forced,[1]);
assert.ok(movedOwner.queries.filter(([method])=>method==='pulls').slice(1)
  .every(([,params])=>params.head==='moved:fix'));
assert.deepEqual((await run({action:'reopened',first:active})).cancelled,[]);
assert.deepEqual((await run({ordinaryDone:true})).forced,[]);
assert.deepEqual((await run({conflict:true})).forced,[]);
await assert.rejects(run({stuck:true}),/has not completed/);
await assert.rejects(run({failure:true}),/provider failure/);
await assert.rejects(run({first:{...pr,head:{...pr.head,repo:null}}}),/no verifiable source/);
"""
        subprocess.run(["node", "--input-type=module", "-e", harness, json.dumps(script)], check=True)

    def test_summary_runs_the_real_renderer_without_changing_the_gate_verdict(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.load((root / ".github/workflows/python-tests.yml").read_text(), Loader=yaml.BaseLoader)
        gate = workflow["jobs"]["merge-gate"]
        verdict = next(step for step in gate["steps"] if step.get("id") == "verdict")
        report = gate["steps"][-1]
        self.assertEqual(gate["permissions"], {"contents": "read", "actions": "read"})
        self.assertNotIn("continue-on-error", gate)
        self.assertNotIn("continue-on-error", verdict)
        self.assertEqual(verdict["run"], "python scripts/ci/review_gate.py verify")
        self.assertEqual(report["if"], "always()")
        self.assertEqual(report["continue-on-error"], "true")
        self.assertEqual(report["timeout-minutes"], "1")
        self.assertEqual(report["env"]["GATE_RESULT"], "${{ steps.verdict.outcome }}")
        # Execute the workflow adapter and real TS renderer. Stub only the
        # GitHub transport, including unavailable evidence, never the verdict.
        harness = r"""
import assert from 'node:assert/strict';
const execute = new (Object.getPrototypeOf(async function(){}).constructor)(
  'github', 'context', 'core', JSON.parse(process.argv[1]));
for (const unavailable of [false, true]) {
  let markdown='', writes=0; const warnings=[];
  const github={rest:{actions:{listJobsForWorkflowRunAttempt:'attempt-jobs'}},
    paginate:async(method,params)=>{
      assert.equal(method,'attempt-jobs');
      const {request,...identity}=params;
      assert.ok(request.signal instanceof AbortSignal);
      assert.deepEqual(identity,{owner:'example',repo:'project',run_id:42,attempt_number:2,per_page:100});
      if(unavailable) throw new Error('provider detail must not leak');
      return [{id:10,run_id:42,run_attempt:2,name:'test-shard (1)',status:'completed',conclusion:'failure',
        steps:[{name:'Run tests',conclusion:'failure'}]}];
    }};
  const summary={addRaw:value=>{markdown+=value;return summary;},write:async()=>{writes++;}};
  await execute(github,{repo:{owner:'example',repo:'project'},runId:42,sha:'merge-sha',
    payload:{pull_request:{head:{sha:'source-sha'},base:{sha:'base-sha'}}}},
    {summary,warning:value=>warnings.push(value)});
  assert.equal(writes,1); assert.equal(warnings.length,unavailable?1:0);
  assert.match(markdown,/Gate step: failure/);
  assert.match(markdown,/Source head: source-sha/);
  assert.match(markdown,/Workflow checkout: merge-sha/);
  assert.match(markdown,/Event base: base-sha/);
  assert.match(markdown,unavailable?/Job evidence unavailable/:/Failing jobs[\s\S]*Run tests/);
  assert.doesNotMatch(markdown,/provider detail|safe to merge/);
}
"""
        subprocess.run(["node", "--input-type=module", "-e", harness, json.dumps(report["with"]["script"])],
                       cwd=root, check=True, env={**os.environ, "GITHUB_WORKSPACE": str(root),
                           "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ATTEMPT": "2",
                           "GATE_RESULT": "failure", "NEEDS_JSON": json.dumps(needs())})

    def test_legal_profiles_and_policy_rehearsal(self):
        for kind in ("docs", "presentation", "full"):
            verify(needs(kind))
            verify_checks(needs(kind, jobs=CHECK_OUTPUT))
        verify(needs("full", presentation=True))
        self.assertFalse(requires_core_tests(["README.md", "docs/guide.md"]))
        self.assertTrue(requires_core_tests(["loopx/prompt.md"]))
        self.assertTrue(requires_core_tests([]))

    def test_every_missing_failure_cancel_or_unexpected_skip_is_rejected(self):
        self.check_results(JOB_OUTPUT, verify)
        self.check_results(CHECK_OUTPUT, verify_checks)

    def check_results(self, jobs, verifier):
        for kind in ("docs", "presentation", "full"):
            good = needs(kind, jobs=jobs)
            for job in jobs:
                for state in ("success", "skipped", "failure", "cancelled", "neutral", None):
                    if state == good[job]["result"]:
                        continue
                    value = copy.deepcopy(good)
                    value[job]["result"] = state
                    with self.subTest(kind=kind, job=job, state=state), self.assertRaises(ValueError):
                        verifier(value)
            for job in ("changes", *jobs):
                value = copy.deepcopy(good)
                del value[job]
                with self.assertRaises(ValueError):
                    verifier(value)

    def test_bad_and_contradictory_classification_cannot_skip_work(self):
        for kind in ("docs", "presentation", "full"):
            for field in ("change_kind", "core_tests", "backend_tests", "python_tests", "stage2c_tests", "presentation_tests"):
                for replacement in (None, "", True, "unknown"):
                    value = needs(kind)
                    value["changes"]["outputs"][field] = replacement
                    with self.assertRaises((ValueError, TypeError)):
                        verify(value)
        value = needs("full")
        value["changes"]["outputs"]["stage2c_tests"] = "false"
        with self.assertRaises(ValueError):
            verify(value)
        for state in ("failure", "skipped", "cancelled"):
            value = needs()
            value["changes"]["result"] = state
            with self.assertRaises(ValueError):
                verify(value)
        with self.assertRaises(ValueError):
            verify({**needs(), "extra": {"result": "success"}})

    def test_backend_flag_cannot_waive_a_full_pr_or_create_client_coverage(self):
        for kind in ("docs", "presentation", "full"):
            for jobs, verifier in ((JOB_OUTPUT, verify), (CHECK_OUTPUT, verify_checks)):
                value = needs(kind, jobs=jobs)
                outputs = value["changes"]["outputs"]
                outputs["backend_tests"] = "false" if outputs["backend_tests"] == "true" else "true"
                with self.assertRaisesRegex(ValueError, "contradictory"):
                    verifier(value)


class PythonShardAdmissionTests(unittest.TestCase):
    """Check the shipped workflow's admission scope and executable gate."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(
            (Path(__file__).resolve().parents[2] / ".github/workflows/python-tests.yml").read_text(),
            Loader=yaml.BaseLoader,
        )

    def script(self, job, step):
        return next(row["run"] for row in self.workflow["jobs"][job]["steps"]
                    if row.get("name") == step)

    def test_pr_shards_wait_for_core_qualification_without_waiving_failures(self):
        job = self.workflow["jobs"]["test-shard"]
        self.assertEqual(job["needs"], ["changes", "chat-bundle", "checks"])
        # Evaluate the shipped expression over independent admission cases.
        # The explicit status predicate is required to retain non-PR diagnostic
        # traversal after failed checks; GitHub otherwise adds success().
        source = job["if"].strip().removeprefix("${{").removesuffix("}}")
        self.assertIn("!cancelled()", source)
        source = source.replace("!cancelled()", "not cancelled()")
        source = source.replace("&&", " and ").replace("||", " or ")
        source = source.replace("needs.chat-bundle", "needs.chat_bundle")
        source = " ".join(source.split())
        for event in ("pull_request", "push", "schedule", "workflow_dispatch", "merge_group"):
            for checks in ("success", "failure", "cancelled", "skipped", ""):
                for ready in (True, False):
                    for cancelled in (True, False):
                        context = {
                            "github": SimpleNamespace(event_name=event),
                            "needs": SimpleNamespace(
                                changes=SimpleNamespace(result="success", outputs=SimpleNamespace(python_tests="true")),
                                chat_bundle=SimpleNamespace(result="success" if ready else "failure"),
                                checks=SimpleNamespace(result=checks),
                            ),
                            "cancelled": lambda: cancelled,
                        }
                        expected = ready and not cancelled and (event != "pull_request" or checks == "success")
                        with self.subTest(event=event, checks=checks, ready=ready, cancelled=cancelled):
                            self.assertEqual(eval(source, {"__builtins__": {}}, context), expected)  # noqa: S307
                        # Failed classification and exempt lanes never acquire
                        # a long-test slot, even if other prerequisites passed.
                        for changes_result, selected in (("failure", "true"), ("skipped", "true"), ("success", "false"), ("success", "")):
                            context["needs"].changes = SimpleNamespace(
                                result=changes_result, outputs=SimpleNamespace(python_tests=selected))
                            self.assertFalse(eval(source, {"__builtins__": {}}, context))  # noqa: S307
        # No cycle may make the prerequisite aggregate wait for its consumer.
        def ancestors(name):
            dependencies = self.workflow["jobs"][name].get("needs", [])
            if isinstance(dependencies, str):
                dependencies = [dependencies]
            return set(dependencies).union(*(ancestors(dep) for dep in dependencies))
        self.assertNotIn("test-shard", ancestors("checks"))

    def test_required_python_gate_rejects_cancelled_overflow_and_missing_shards(self):
        script = self.script("pytest", "Require every upstream check")
        for checks in ("success", "failure", "cancelled", "skipped", ""):
            for shards in ("success", "failure", "cancelled", "skipped", ""):
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                    env={**os.environ, "CHECKS_RESULT": checks, "SHARDS_RESULT": shards},
                    capture_output=True,
                )
                with self.subTest(checks=checks, shards=shards):
                    self.assertEqual(result.returncode == 0,
                                     checks == shards == "success")
        gate = self.workflow["jobs"]["pytest"]
        self.assertEqual(gate["if"], "always() && needs.changes.outputs.python_tests == 'true'")
        self.assertEqual(gate["needs"], ["changes", "checks", "test-shard"])

    def test_required_python_gate_reads_every_nonempty_coverage_partition(self):
        script = self.script("pytest", "Combine complete coverage and enforce the existing floor")
        # Execute the workflow's file guard, before the coverage tool itself.
        guard = script.split("python -m coverage combine", 1)[0]
        for absent in (None, 1, 2, 3, 4, 5, 6):
            for empty in (False, True):
                with tempfile.TemporaryDirectory() as directory:
                    for shard in range(1, 7):
                        path = Path(directory) / f"coverage-shards/python-coverage-{shard}/.coverage"
                        path.parent.mkdir(parents=True)
                        if shard != absent or empty:
                            path.write_bytes(b"" if shard == absent else b"partition")
                    result = subprocess.run(
                        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", guard],
                        cwd=directory, capture_output=True,
                    )
                    with self.subTest(absent=absent, empty=empty):
                        self.assertEqual(result.returncode == 0, absent is None)

    def test_long_shards_share_groups_across_current_heads_without_replacement(self):
        job = self.workflow["jobs"]["test-shard"]
        fields = job.get("concurrency", {})
        self.assertEqual(fields.get("queue"), "max")
        self.assertEqual(fields.get("cancel-in-progress"), "false")
        template = fields["group"]
        # A ref/run/PR component would silently restore per-run admission.
        self.assertEqual(re.findall(r"\$\{\{\s*(.*?)\s*\}\}", template), ["matrix.shard"])
        groups = {re.sub(r"\$\{\{.*?\}\}", str(shard), template).casefold()
                  for shard in range(1, 7)}
        self.assertEqual(len(groups), 6)
        self.assertEqual(job["strategy"]["matrix"]["shard"], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(job["strategy"]["fail-fast"], "false")
        # GateTests executes the current workflow's PR lifecycle expressions,
        # including closed events whose ref changes to main. Keep that semantic
        # qualification instead of requiring the superseded ref-only spelling.
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotIn("secrets.", json.dumps(job))


if __name__ == "__main__":
    unittest.main()
