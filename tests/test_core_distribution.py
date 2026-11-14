"""Opt-in installed-core qualification through real CLI processes and SQLite.

Build/install the standalone wheel in a clean environment and set
LOOPX_CORE_TEST_PYTHON to that environment's Python. All task preparation and
product invocation run from the installed artifact, isolated from this checkout.
"""
from __future__ import annotations

import importlib.util
import csv
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
GOAL = "loopx-turn-fixture"
AGENT = "codex-fixture"
HOST_SOURCE = '''import json,pathlib,sys
request=json.load(sys.stdin)
pathlib.Path('squares.json').write_text(json.dumps([n*n for n in range(8)]))
with pathlib.Path('host-invocations').open('a') as f:f.write('called\\n')
json.dump({'schema_version':'loopx_turn_result_v0','turn_key':request['turn_key'],
'result_kind':'validated_completion','completed_phases':['host_execute','typed_result'],
'classification':'verified_squares','recommended_action':'Read the checked result.',
'next_action':'Return the verified result.','delivery_batch_scale':'implementation',
'delivery_outcome':'outcome_progress','vision_unchanged_reason':'The requested squares remain the objective.',
'summary':'Produced squared integers zero through seven.',
'work_evidence':['artifact:squares.json'],'validation_evidence':['check:independent-squares']},sys.stdout)
'''
VALIDATOR_SOURCE = '''import json,os,pathlib,sys,time
json.load(sys.stdin)
if pathlib.Path('pause-validator').exists():
 pathlib.Path('validator-ready').write_text(str(os.getpid()))
 while pathlib.Path('pause-validator').exists():time.sleep(0.05)
raise SystemExit(0 if json.loads(pathlib.Path('squares.json').read_text())==[0,1,4,9,16,25,36,49] else 7)
'''


def _prepare_module():
    spec = importlib.util.spec_from_file_location(
        "core_distribution_prepare", REPOSITORY / "packages/loopx-core/prepare.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_core_staging_excludes_workspace_and_reuses_kernel_bytes(tmp_path):
    staged = _prepare_module().prepare_core_source(tmp_path / "source")
    for name in ("chat", "web", "canary", "cli.py", "chat.py"):
        assert not (staged / "loopx" / name).exists()
    assert not list((staged / "loopx").glob("chat_*.py"))
    for name in ("entrypoint.py", "control_plane/turn_driver/executor.py",
                 "control_plane/coordination/sqlite_authority_store.ts"):
        assert (staged / "loopx" / name).read_bytes() == (REPOSITORY / "loopx" / name).read_bytes()
    assert not (staged / ".loopx").exists()
    assert not (staged / "tests").exists()


@pytest.fixture
def core_python():
    value = os.environ.get("LOOPX_CORE_TEST_PYTHON")
    if not value:
        pytest.skip("set LOOPX_CORE_TEST_PYTHON to a clean installed core environment")
    executable = Path(value).absolute()
    assert executable.is_file(), executable
    return str(executable)


def _command(python, arguments):
    # -I excludes PYTHONPATH and the current directory. No product import may
    # resolve to the parent test's editable source checkout.
    source = "import json,sys;from loopx.entrypoint import main;code=main(json.loads(sys.argv[1]));"
    if any(arguments[index:index + 2] == ["turn", "run-once"] for index in range(len(arguments) - 1)):
        # These task fixtures leave reward memory disabled. Check the executed
        # CLI and replay rather than inferring their closure from helper tests.
        source += ("assert 'loopx.capabilities.reward_memory.runtime_hooks' not in sys.modules;"
                   "assert 'loopx.capabilities.reward_memory.read_authority' not in sys.modules;")
    return [python, "-I", "-c", source + "raise SystemExit(code)", json.dumps(arguments)]


def _environment(python, overrides=None):
    env = {**os.environ, "PATH": str(Path(python).parent) + os.pathsep + os.environ.get("PATH", ""),
           **(overrides or {}), "LOOPX_USAGE_PING": "0"}
    env.pop("PYTHONPATH", None)
    return env


def _run(python, arguments, cwd, *, environment=None, expected=0):
    result = subprocess.run(_command(python, arguments), cwd=cwd,
        env=_environment(python, environment),
        capture_output=True, text=True, timeout=90)
    assert result.returncode == expected, (result.stdout, result.stderr)
    return result


def test_installed_core_identity_help_and_unavailable_workspace(core_python, tmp_path):
    script = '''import importlib.metadata as m,importlib.util as u,json,loopx
from pathlib import Path
assert m.version('loopx-core')==loopx.__version__
try:m.distribution('loopx')
except m.PackageNotFoundError:pass
else:raise AssertionError('the ordinary distribution must not be installed')
for name in ('loopx.cli','loopx.chat','loopx.web','loopx.canary'):assert u.find_spec(name) is None,name
from loopx.release_manifest import _distribution_runtime_fingerprint
assert _distribution_runtime_fingerprint() is not None
from loopx import doctor
assert doctor.python_distribution_install(Path(doctor.__file__))['distribution_name']=='loopx-core'
print(json.dumps({'package':str(Path(loopx.__file__).resolve())}))
'''
    probe = subprocess.run([core_python, "-I", "-c", script], cwd=tmp_path,
        env=_environment(core_python), capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0, probe.stderr
    assert not Path(json.loads(probe.stdout)["package"]).is_relative_to(REPOSITORY)
    help_text = _run(core_python, ["--help"], tmp_path, environment={"PATH": str(tmp_path)}).stdout
    assert "loopx connect --help" in help_text and "loopx turn run-once --help" in help_text
    assert "preset list" not in help_text and "loopx start-goal --guided" in help_text
    reference = json.loads(_run(core_python, ["commands", "--format", "json"], tmp_path).stdout)
    commands = [entry["command"] for group in reference["groups"] for entry in group["commands"]]
    assert "loopx configure-goal --help" in commands
    assert not any(command.startswith(("loopx dashboard", "loopx chat", "loopx canary")) for command in commands)
    _run(core_python, ["--version"], tmp_path, environment={"PATH": str(tmp_path)})
    _run(core_python, ["--format", "json", "--version"], tmp_path)
    invalid = _run(core_python, ["--format", "invalid", "--version"], tmp_path, expected=2)
    assert "invalid choice" in invalid.stderr
    refusal = _run(core_python, ["dashboard"], tmp_path, expected=2)
    assert "unavailable in the standalone core" in refusal.stderr
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("host_surface", ("shell", "traex-cli"))
def test_installed_core_guided_start_identity_and_readback(core_python, tmp_path, monkeypatch, host_surface):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    if host_surface == "traex-cli":
        monkeypatch.setenv("TRAECLI_THREAD_ID", "core-session")
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    prefix = ["--runtime-root", str(runtime), "--format", "json"]
    objective = 'Produce an inventory report; preserve "quoted" source names and 总计.'
    start = [*prefix, "start-goal", "--guided", "--project", str(project),
             "--goal-id", GOAL, "--host-surface", host_surface, "--goal-text", objective]

    def command(*arguments, expected=0):
        return json.loads(_run(core_python, list(arguments), project, expected=expected).stdout)

    preview = command(*start)
    assert preview["goal_text"] == objective
    assert preview["guided_transaction"]["writes_now"] is False
    assert preview["guided_transaction"]["spends_quota_now"] is False
    assert preview["recommended_next_step"]["identity_selection_gate"]["default_action"] == "register_fresh_agent"
    assert not list(project.iterdir()) and not runtime.exists()
    # These are the real bootstrap and identity owners advertised by the
    # preview, running from the standalone wheel with the full facade absent.
    created = command(*prefix, "bootstrap", "--project", str(project), "--goal-id", GOAL,
                      "--objective", objective, "--write-scope", "docs/**")
    assert created["storage_selection"]["provider"] == "sqlite"
    assert created["global_sync"]["ok"] is True
    registered = command(*prefix, "register-agent", "--goal-id", GOAL,
                         "--agent-id", AGENT, "--require-new", "--execute")
    assert registered["registration_readback"]["verified"] is True
    assert registered["global_sync"]["ok"] is True
    collision = command(*prefix, "register-agent", "--goal-id", GOAL,
                        "--agent-id", AGENT, "--require-new", "--execute", expected=1)
    assert collision["ok"] is False and collision["written"] is False
    binding = command(*prefix, "bind-agent-thread", "--goal-id", GOAL,
                      "--agent-id", AGENT, "--thread-id", "core-session", "--host-surface", host_surface, "--execute")
    assert binding["global_sync"]["ok"] is True
    assert binding["registration_readback"]["verified"] is True
    resumed = command(*start) if host_surface == "traex-cli" else command(*start, "--thread-id", "core-session")
    assert resumed["agent_id"] == AGENT
    assert resumed["project_connection"]["goal_found"] is True
    assert "identity_selection_gate" not in resumed["guided_transaction"]
    assert resumed["goal_text"] == objective
    assert resumed["thread_id"] == "core-session"
    pack = command(*prefix, "bootstrap-command-pack", "--project", str(project),
                   "--goal-id", GOAL, "--agent-id", AGENT, "--host-surface", host_surface, "--goal-text", objective)
    assert pack["goal_start_contract"]["goal_text"] == objective
    assert pack["goal_start_contract"]["explicit_invocation_confirms_project_local_state_writes"] is True


@pytest.mark.parametrize("explicit_route", [False, True])
def test_installed_core_generated_commands_keep_their_distribution(
    core_python, tmp_path, monkeypatch, explicit_route,
):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    prefix = ["--runtime-root", str(runtime), "--format", "json"]
    environment = {"TRAECLI_THREAD_ID": "core-session"}

    def command(*arguments):
        return json.loads(_run(core_python, list(arguments), project,
                               environment=environment).stdout)

    command(*prefix, "bootstrap", "--project", str(project), "--goal-id", GOAL,
            "--objective", "Verify the inventory report.", "--write-scope", "docs/**")
    command(*prefix, "register-agent", "--goal-id", GOAL,
            "--agent-id", AGENT, "--require-new", "--execute")
    command(*prefix, "bind-agent-thread", "--goal-id", GOAL, "--agent-id", AGENT,
            "--thread-id", "core-session", "--host-surface", "traex-cli", "--execute")
    route = ["--cli-bin", "loopx"] if explicit_route else []
    pack = command(*prefix, *route, "bootstrap-command-pack", "--project", str(project),
                   "--goal-id", GOAL, "--host-surface", "traex-cli")
    assert pack["agent_id"] == AGENT
    # Execute the complete returned command, rather than discarding its
    # executable and replaying only the arguments through the test's Python.
    arguments = shlex.split(pack["host_loop_activation"]["activation_input_command"])
    if os.name == "nt" and arguments[0] == "&":
        arguments = arguments[1:]
    foreign = tmp_path / "foreign" / "loopx"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("", encoding="utf-8")
    (foreign / "entrypoint.py").write_text(
        "from pathlib import Path\n"
        "Path('foreign-cli-used').write_text('foreign package', encoding='utf-8')\n"
        "def main(argv=None): return 91\n",
        encoding="utf-8",
    )
    inherited = _environment(core_python, environment)
    inherited["PYTHONPATH"] = str(foreign.parent)
    result = subprocess.run(arguments, cwd=project, env=inherited,
                            capture_output=True, text=True, timeout=45)
    marker = project / "foreign-cli-used"
    if explicit_route:
        assert result.returncode == 91 and marker.exists(), (result.stdout, result.stderr)
    else:
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert not marker.exists()
        payload = json.loads(result.stdout)
        assert payload["ok"] and payload["goal_id"] == GOAL and payload["agent_id"] == AGENT
        doctor_command = shlex.split(pack["commands"]["doctor"])
        if os.name == "nt" and doctor_command[0] == "&":
            doctor_command = doctor_command[1:]
        diagnosed = subprocess.run(
            [*doctor_command, "--installation-only", "--deep", "--format", "json"],
            cwd=project, env=inherited, capture_output=True, text=True, timeout=90,
        )
        assert diagnosed.returncode == 0, (diagnosed.stdout, diagnosed.stderr)
        assert json.loads(diagnosed.stdout)["ok"]
        assert not marker.exists()


@pytest.mark.parametrize("route", ["help", "preview", "catalog"])
def test_installed_core_start_metadata_defers_issue_fix_execution(core_python, tmp_path, route):
    arguments = ["start-goal", "--help"] if route == "help" else [
        "--runtime-root", str(tmp_path / "runtime"), "--format", "json",
        "start-goal", "--guided", "--project", str(tmp_path / "project"),
        "--goal-id", GOAL, "--host-surface", "shell", "--goal-text", "Verify the complete report."]
    script = '''import json,sys
if sys.argv[2]=='catalog':
 from loopx.capabilities.issue_fix.catalog_entry import ISSUE_FIX_CATALOG_ENTRY
 assert ISSUE_FIX_CATALOG_ENTRY['default_enabled'] is False
else:
 from loopx.entrypoint import main
 try:code=main(json.loads(sys.argv[1]))
 except SystemExit as error:code=error.code
 assert code==0
for name in ('workflow_plan','candidate_preflight','acceptance_loop','metadata_preview','repository_context'):
 assert 'loopx.capabilities.issue_fix.'+name not in sys.modules,name
'''
    result = subprocess.run([core_python, "-I", "-c", script, json.dumps(arguments), route],
        cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not list(tmp_path.iterdir())


def test_installed_core_catalog_entries_have_executable_parsers(core_python, tmp_path):
    reference = json.loads(_run(core_python, ["commands", "--format", "json"], tmp_path).stdout)
    for group in reference["groups"]:
        for entry in group["commands"]:
            for command in re.split(r" / (?=loopx )", entry["command"]):
                tokens = shlex.split(command)[1:]
                prefix = []
                for token in tokens:
                    if token.startswith(("-", "<")):
                        break
                    prefix.append(token)
                _run(core_python, [*prefix, "--help"], tmp_path)


def test_installed_core_deep_doctor_verifies_distribution_ownership(core_python, tmp_path, monkeypatch):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    console = Path(core_python).parent / ("loopx.exe" if os.name == "nt" else "loopx")
    arguments = ["doctor", "--installation-only", "--deep", "--format", "json"]
    result = subprocess.run([str(console), *arguments], cwd=tmp_path,
        env=_environment(core_python), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    paths = payload["release_candidate"]["representative_cli"]["package_paths"]
    assert "loopx/cli.py" not in paths["required"]
    assert "loopx/cli_runtime.py" in paths["required"]
    # An executable copied outside this distribution's RECORD must still fail.
    copied = tmp_path / console.name
    shutil.copy2(console, copied)
    rejected = subprocess.run([str(copied), *arguments], cwd=tmp_path,
        env=_environment(core_python), capture_output=True, text=True, timeout=90)
    assert rejected.returncode == 1, (rejected.stdout, rejected.stderr)
    checks = {check["id"]: check for check in json.loads(rejected.stdout)["checks"]}
    assert checks["command_package_same_distribution"]["ok"] is False


def test_installed_core_deep_doctor_rejects_missing_runtime_file(core_python, tmp_path, monkeypatch):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    probe = subprocess.run([core_python, "-I", "-c",
        "from pathlib import Path;import loopx;print(Path(loopx.__file__).parent/'history.py')"],
        capture_output=True, text=True, check=True)
    original = Path(probe.stdout.strip())
    backup = original.with_suffix(".py.core-test-backup")
    original.rename(backup)
    try:
        console = Path(core_python).parent / ("loopx.exe" if os.name == "nt" else "loopx")
        result = subprocess.run([str(console), "doctor", "--installation-only", "--deep", "--format", "json"],
            cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, timeout=90)
        assert result.returncode == 1, (result.stdout, result.stderr)
        checks = {check["id"]: check for check in json.loads(result.stdout)["checks"]}
        assert checks["representative_cli_imports"]["ok"] is False
        assert checks["representative_distribution_paths"]["ok"] is False
    finally:
        backup.rename(original)


@pytest.mark.parametrize("damage", ["missing_module", "redirected_module", "missing_console", "missing_console_file"])
def test_installed_core_module_doctor_requires_recorded_entrypoints(core_python, tmp_path, monkeypatch, damage):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    probe = subprocess.run([core_python, "-I", "-c",
        "from importlib.metadata import distribution;"
        "print(next(str(p.locate()) for p in distribution('loopx-core').files "
        "if str(p).endswith('.dist-info/RECORD')))"],
        capture_output=True, text=True, check=True)
    record = Path(probe.stdout.strip())
    original = record.read_bytes()
    rows = list(csv.reader(original.decode("utf-8").splitlines()))
    changed = []
    for row in rows:
        if damage == "missing_console" and Path(row[0]).name.lower() in {"loopx", "loopx.exe", "loopx-script.py"}:
            continue
        if row[0] == "loopx/entrypoint.py" and damage in {"missing_module", "redirected_module"}:
            if damage == "missing_module":
                continue
            row[0] = "loopx/unrecorded-entrypoint.py"
        changed.append(row)
    try:
        console = Path(core_python).parent / ("loopx.exe" if os.name == "nt" else "loopx")
        backup = console.with_suffix(".core-test-backup")
        if damage == "missing_console_file":
            console.rename(backup)
        with record.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(changed)
        result = subprocess.run(
            [core_python, "-I", "-m", "loopx.entrypoint", "doctor", "--installation-only", "--deep", "--format", "json"],
            cwd=tmp_path, env=_environment(core_python), capture_output=True, text=True, timeout=90,
        )
        assert result.returncode == 1, (result.stdout, result.stderr)
        checks = {check["id"]: check for check in json.loads(result.stdout)["checks"]}
        assert checks["command_package_same_distribution"]["ok"] is False
    finally:
        record.write_bytes(original)
        if damage == "missing_console_file" and backup.exists():
            backup.rename(console)


@pytest.fixture(params=["sqlite", "file"])
def task(tmp_path, monkeypatch, core_python, request):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    registry = project / ".loopx/registry.json"
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    if request.param == "file":
        # Configure the existing installed owner before creating synthetic
        # state; never migrate or edit a live Goal for qualification.
        setup = '''import json,sys
from pathlib import Path
from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.machine_configuration.contract import MACHINE_CONFIGURATION_SCHEMA
from loopx.capabilities.machine_configuration.store import configure_machine_configuration
configuration={'schema_version':MACHINE_CONFIGURATION_SCHEMA,'namespaces':{'goal_storage':{
'schema_version':'loopx_goal_storage_defaults_v1','new_goal_provider':'file',
'canonical_creation':True,'new_goal_handoff_mode':'hard_lease'}}}
arguments={'runtime_root':Path(sys.argv[1]),'configuration':configuration,
'registry':build_builtin_machine_configuration_registry()}
preview=configure_machine_configuration(**arguments)
result=configure_machine_configuration(**arguments,execute=True,expected_plan_revision=preview['plan_revision'])
assert result['readback_verified']
'''
        configured = subprocess.run([core_python, "-I", "-c", setup, str(runtime)], cwd=project,
            env=_environment(core_python), capture_output=True, text=True, timeout=45)
        assert configured.returncode == 0, configured.stderr

    def command(*arguments):
        return json.loads(_run(core_python, [*prefix, *arguments], project).stdout)

    created = command("connect", "--project", str(project), "--goal-id", GOAL,
        "--objective", "Produce and independently check squared integers zero through seven.",
        "--write-scope", "docs/**", "--no-global-sync")
    assert created["storage_selection"]["provider"] == request.param
    assert created["storage_selection"]["handoff_mode"] == "hard_lease"
    command("configure-goal", "--goal-id", GOAL, "--registered-agent", AGENT,
        "--quota-compute", "1", "--execute")
    added = command("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Produce and independently validate squares.json.", "--priority", "P1",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--operation-id", "core-create-task")
    command("todo", "claim", "--goal-id", GOAL, "--todo-id", added["todo_id"],
        "--agent-id", AGENT, "--claimed-by", AGENT, "--claim-operation-id", "core-new-task")
    workspace = project / "docs/isolated-work"
    workspace.mkdir(parents=True)
    arguments = ["--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "turn", "run-once", "--host", "generic-cli",
        "--goal-id", GOAL, "--agent-id", AGENT, "--project", str(workspace),
        "--execution-mode", "isolated-headless",
        "--host-adapter-request-schema", "loopx_turn_host_request_v1",
        "--host-adapter-command-json", json.dumps([core_python, "-I", "-c", HOST_SOURCE]),
        "--validation-command-json", json.dumps([core_python, "-I", "-c", VALIDATOR_SOURCE]),
        "--no-global-sync", "--execute"]
    return workspace, runtime, registry, arguments


def _spends(runtime):
    index = runtime / "goals" / GOAL / "runs/index.jsonl"
    return sum(json.loads(line).get("classification") == "quota_slot_spent"
               for line in index.read_text().splitlines()) if index.exists() else 0


def _assert_committed(core_python, workspace, runtime, result):
    assert result["status"] == "committed", result
    assert result["validation"]["status"] == "passed"
    assert _spends(runtime) == 1
    assert json.loads((workspace / "squares.json").read_text()) == [0, 1, 4, 9, 16, 25, 36, 49]
    assert (workspace / "host-invocations").read_text().splitlines() == ["called"]
    journals = [p for p in (runtime / "goals" / GOAL / "turns").glob("*.json")
                if not p.name.endswith(".lock.holder.json")]
    assert len(journals) == 1
    journal = json.loads(journals[0].read_text())
    assert journal["status"] == "committed"
    contract = journal["plan"]["work_contract"]
    readback = json.loads(_run(core_python, ["--registry", contract["registry"],
        "--runtime-root", str(runtime), "--format", "json", "todo", "list", "--goal-id", GOAL,
        "--todo-id", contract["selected_todo"]["todo_id"]], workspace).stdout)
    assert readback["todo"]["status"] == "done"
    return result["resume_turn_key"]


@pytest.mark.parametrize("checkpoint", [None, "continue", "no_change"])
def test_installed_core_executes_checks_and_replays_once(core_python, task, checkpoint):
    workspace, runtime, registry, arguments = task
    if checkpoint is not None:
        vision = {
            "schema_version": "goal_vision_replan_contract_v0",
            "state": "active",
            "vision_patch": {"acceptance_summary": "Independently check squares and preserve exact replay."},
            "path_delta": {
                "schema_version": "goal_path_delta_v0", "outcome": checkpoint,
                "prior_assumption": "The requested squares still needed a result check.",
                "observed_reality": "The requested artifact is available for independent validation.",
                "retained": ["The original squares request and acceptance."],
                "evidence_refs": ["artifact:squares.json"],
            },
        }
        host_index = arguments.index("--host-adapter-command-json") + 1
        host_script = workspace / "checkpoint-host.py"
        host_script.write_text(HOST_SOURCE.replace(
            "'vision_unchanged_reason':'The requested squares remain the objective.'",
            f"'path_delta_mode':'unchanged','agent_vision_json':{json.dumps(vision)!r}",
        ))
        arguments[host_index] = json.dumps([core_python, "-I", str(host_script)])
    history = json.loads(_run(core_python, ["--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "history", "trajectory-hygiene", "--goal-id", GOAL], workspace).stdout)
    assert history["ok"] is True
    result = json.loads(_run(core_python, arguments, workspace).stdout)
    key = _assert_committed(core_python, workspace, runtime, result)
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", key], workspace).stdout)
    _assert_committed(core_python, workspace, runtime, replay)
    assert replay["effects"]["host_invoked"] is False
    assert replay["effects"]["state_written"] is False
    assert replay["effects"]["quota_spent"] is False
    inspected = json.loads(_run(core_python, ["--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "turn", "inspect-journal", "--goal-id", GOAL,
        "--agent-id", AGENT, "--turn-key", key], workspace).stdout)
    assert inspected["ok"] is True
    if checkpoint is not None:
        journal_path = runtime / "goals" / GOAL / "turns" / f"{key.removeprefix('sha256:')}.json"
        journal = json.loads(journal_path.read_text())
        assert journal["host_result"]["result_kind"] == "validated_completion"
        assert journal["host_result"]["agent_vision"]["path_delta"]["outcome"] == checkpoint


@pytest.mark.skipif(os.name == "nt", reason="this qualification injects a POSIX SIGKILL")
@pytest.mark.parametrize("lease_change", [None, "renew", "release"])
def test_installed_core_recovers_confirmed_result_after_process_loss(core_python, task, lease_change):
    workspace, runtime, registry, arguments = task
    pause = workspace / "pause-validator"
    pause.touch()
    process = subprocess.Popen(_command(core_python, arguments), cwd=workspace,
        env=_environment(core_python), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    journal = None
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(str(process.communicate()))
            for path in (runtime / "goals" / GOAL / "turns").glob("*.json"):
                if path.name.endswith(".lock.holder.json"):
                    continue
                candidate = json.loads(path.read_text())
                if "typed_result" in candidate.get("completed_phases", []) and (workspace / "validator-ready").exists():
                    journal = candidate
                    break
            if journal:
                break
            time.sleep(0.05)
        assert journal is not None, "the confirmed host result was not durably observed"
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        pause.unlink(missing_ok=True)
        process.communicate(timeout=10)
    assert not journal.get("quota_spend")
    assert _spends(runtime) == 0
    if lease_change:
        todo_id = journal["plan"]["work_contract"]["selected_todo"]["todo_id"]
        prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
        lease = json.loads(_run(core_python, [*prefix, "task-lease", "inspect", "--goal-id", GOAL,
            "--todo-id", todo_id], workspace).stdout)["lease"]
        _run(core_python, [*prefix, "task-lease", lease_change, "--goal-id", GOAL,
            "--todo-id", todo_id, "--owner", AGENT, "--idempotency-key", lease["idempotency_key"],
            "--expected-version", str(lease["version"])], workspace)
        rejected = json.loads(_run(core_python, [*arguments, "--resume-turn-key", journal["turn_key"]],
            workspace, expected=1).stdout)
        assert rejected["status"] == "failed"
        assert rejected["effects"]["host_invoked"] is False
        assert rejected["effects"]["state_written"] is False
        assert rejected["effects"]["quota_spent"] is False
        assert _spends(runtime) == 0
        readback = json.loads(_run(core_python, [*prefix, "todo", "list", "--goal-id", GOAL,
            "--todo-id", todo_id], workspace).stdout)
        assert readback["todo"]["status"] == "open"
        assert (workspace / "host-invocations").read_text().splitlines() == ["called"]
        return
    result = json.loads(_run(core_python, [*arguments, "--resume-turn-key", journal["turn_key"]], workspace).stdout)
    _assert_committed(core_python, workspace, runtime, result)
    assert result["effects"]["host_invoked"] is False
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", journal["turn_key"]], workspace).stdout)
    assert replay["effects"]["quota_spent"] is False
    assert (workspace / "host-invocations").read_text().splitlines() == ["called"]


def test_installed_core_rejects_unverified_artifact_without_settlement(core_python, task):
    workspace, runtime, _, arguments = task
    invalid = list(arguments)
    host = invalid.index("--host-adapter-command-json") + 1
    invalid[host] = json.dumps([core_python, "-I", "-c", HOST_SOURCE.replace(
        "[n*n for n in range(8)]", "[0,1,4,9,16,25,36,50]",
    )])
    result = json.loads(_run(core_python, invalid, workspace, expected=1).stdout)
    assert result["status"] == "failed"
    assert result["effects"]["host_invoked"] is True
    assert result["effects"]["state_written"] is False
    assert result["effects"]["quota_spent"] is False
    assert _spends(runtime) == 0
    assert (workspace / "host-invocations").read_text().splitlines() == ["called"]


def test_installed_core_codex_prepares_only_an_admitted_valid_request(core_python, task, tmp_path, monkeypatch):
    from tests.test_loopx_turn_codex_cli import _fake_codex

    workspace, runtime, registry, _ = task
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    todo = json.loads(_run(core_python, [*prefix, "todo", "list", "--goal-id", GOAL], workspace).stdout)
    todo_id = todo["todos"][0]["todo_id"]
    lease = json.loads(_run(core_python, [*prefix, "task-lease", "inspect", "--goal-id", GOAL,
        "--todo-id", todo_id], workspace).stdout)["lease"]
    _run(core_python, [*prefix, "task-lease", "release", "--goal-id", GOAL, "--todo-id", todo_id,
        "--owner", AGENT, "--idempotency-key", lease["idempotency_key"],
        "--expected-version", str(lease["version"])], workspace)
    binary, log = _fake_codex(tmp_path)
    binary.write_text(binary.read_text().replace('output_path = ',
        'pathlib.Path("squares.json").write_text(json.dumps([n*n for n in range(8)]))\noutput_path = '))
    home = tmp_path / "codex-home"
    home.mkdir()
    environment = {"FAKE_CODEX_LOG": str(log), "CODEX_HOME": str(home)}
    command = [*prefix, "turn", "run-once", "--host", "codex-cli", "--goal-id", GOAL,
        "--agent-id", AGENT, "--todo-id", todo_id, "--project", str(workspace),
        "--codex-bin", str(binary), "--codex-model", "fixture-model", "--codex-reasoning-effort", "high",
        "--codex-sandbox", "read-only", "--no-global-sync", "--execute"]
    refused = json.loads(_run(core_python, [*command, "--turn-instance-id", "invalid-request",
        "--validation-command-json", "{"], workspace, environment=environment, expected=1).stdout)
    assert all(value is False for value in refused["effects"].values())
    held = json.loads(_run(core_python, [*prefix, "task-lease", "inspect", "--goal-id", GOAL,
        "--todo-id", todo_id], workspace).stdout)
    assert held["active"] is False and not log.exists() and _spends(runtime) == 0
    result = json.loads(_run(core_python, [*command, "--turn-instance-id", "admitted-request",
        "--validation-command-json", json.dumps([core_python, "-I", "-c", VALIDATOR_SOURCE])],
        workspace, environment=environment).stdout)
    assert result["status"] == "committed" and result["validation"]["status"] == "passed", result
    assert json.loads((workspace / "squares.json").read_text()) == [0, 1, 4, 9, 16, 25, 36, 49]
    assert len(log.read_text().splitlines()) == 1 and _spends(runtime) == 1
    resume_command = [item for index, item in enumerate(command)
                      if item != "--todo-id" and (index == 0 or command[index - 1] != "--todo-id")]
    replay = json.loads(_run(core_python, [*resume_command, "--resume-turn-key", result["resume_turn_key"],
        "--validation-command-json", json.dumps([core_python, "-I", "-c", VALIDATOR_SOURCE])],
        workspace, environment=environment).stdout)
    assert replay["replayed"] is True and all(value is False for value in replay["effects"].values())
    assert len(log.read_text().splitlines()) == 1 and _spends(runtime) == 1
