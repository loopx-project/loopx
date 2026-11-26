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
                 "control_plane/coordination/sqlite_authority_store.ts",
                 "extensions/lark/extension.toml"):
        assert (staged / "loopx" / name).read_bytes() == (REPOSITORY / "loopx" / name).read_bytes()
    assert not (staged / ".loopx").exists()
    assert not (staged / "tests").exists()
    recall = staged / "loopx/capabilities/agent_turn_recall"
    for name in ("core.py", "cli.py"):
        assert not (recall / name).exists()
    for name in ("catalog_entry.py", "runtime.py"):
        assert (recall / name).is_file()
    review = staged / "loopx/capabilities/pr_review_queue"
    assert {path.name for path in review.iterdir()} == {
        "__init__.py", "catalog_entry.py", "goal_configuration.py", "machine_defaults.py",
        "order.py", "review_body.py", "scheduling.py", "README.md",
    }
    assert not (staged / "loopx/cli_commands/pr_review.py").exists()
    assert not (staged / "loopx/pr_review.py").exists()
    assert not (staged / "loopx/pr_review_merge_readiness.py").exists()


def test_core_staging_has_zero_unconditional_optional_wheel_imports(tmp_path):
    import ast

    prepare = _prepare_module()
    staged = prepare.prepare_core_source(tmp_path / "source")
    optional_sources = set().union(*prepare._optional_capability_sources(
        prepare._tracked_loopx_sources(REPOSITORY)
    ).values())

    def _mod_name(path: Path) -> str:
        parts = list(path.with_suffix("").parts)
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    optional_modules = {_mod_name(p) for p in optional_sources if p.suffix == ".py"}

    def _imported_targets(node: ast.AST, mod_parts: list[str]) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            base = mod_parts[:-node.level] if node.level else []
            mod = ".".join([*base, *([node.module] if node.module else [])])
            return [mod, *(f"{mod}.{a.name}" for a in node.names if mod)]
        return []

    def _scan(stmts: list[ast.stmt], mod_parts: list[str], offenders: list[str], rel: str) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                for target in _imported_targets(stmt, mod_parts):
                    if target in optional_modules:
                        offenders.append(f"{rel}:{stmt.lineno}->{target}")
            elif isinstance(stmt, ast.ClassDef):
                _scan(stmt.body, mod_parts, offenders, rel)
            elif isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.While)):
                if isinstance(stmt, ast.If) and ast.unparse(stmt.test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
                    continue
                _scan(stmt.body, mod_parts, offenders, rel)
                if not isinstance(stmt, (ast.With, ast.AsyncWith)):
                    _scan(stmt.orelse, mod_parts, offenders, rel)
            elif isinstance(stmt, ast.Try):
                handlers = [ast.unparse(h.type) if h.type else "" for h in stmt.handlers]
                if any("ModuleNotFoundError" in h or "ImportError" in h for h in handlers):
                    continue
                _scan(stmt.body, mod_parts, offenders, rel)
                _scan(stmt.orelse, mod_parts, offenders, rel)
                _scan(stmt.finalbody, mod_parts, offenders, rel)

    offenders: list[str] = []
    for py_file in sorted((staged / "loopx").rglob("*.py")):
        rel = py_file.relative_to(staged)
        parts = list(rel.with_suffix("").parts)
        mod_parts = parts[:-1] if parts[-1] == "__init__" else parts
        _scan(ast.parse(py_file.read_text(encoding="utf-8")).body, mod_parts, offenders, rel.as_posix())
    assert offenders == []


def test_installed_core_retains_module_imports_without_optional_wheels(core_python, tmp_path):
    script = '''
import importlib, importlib.metadata as m
try:
    m.distribution("loopx-capability-issue-fix")
except m.PackageNotFoundError:
    pass
else:
    raise SystemExit(0)
for name in (
    "loopx.cli_commands.update_artifact",
    "loopx.capabilities.reward_memory.cli",
    "loopx.capabilities.content_ops.cli",
    "loopx.extensions.lark.reviewer_notification",
    "loopx.capabilities.issue_fix.pr_lifecycle",
    "loopx.capabilities.native_chat.github_pr_evidence",
):
    importlib.import_module(name)
from loopx.cli_commands.update_artifact import standalone_source
assert "REPOSITORY_PATTERN = " in standalone_source()
'''
    checked = subprocess.run(
        [core_python, "-I", "-c", script], cwd=tmp_path,
        env=_environment(core_python), capture_output=True, text=True, timeout=45,
    )
    assert checked.returncode == 0, (checked.stdout, checked.stderr)


@pytest.fixture
def core_python(tmp_path):
    value = os.environ.get("LOOPX_CORE_TEST_PYTHON")
    if not value:
        pytest.skip("set LOOPX_CORE_TEST_PYTHON to a clean installed core environment")
    executable = Path(value).absolute()
    assert executable.is_file(), executable
    # Every selected qualification caller must use the installed Core owner,
    # including when the separate identity test was not selected.
    script = '''import importlib.metadata as m,json,loopx
from pathlib import Path
installed=m.distribution('loopx-core')
assert installed.version==loopx.__version__
try:m.distribution('loopx')
except m.PackageNotFoundError:pass
else:raise AssertionError('the ordinary distribution must not be installed')
direct_url=json.loads(installed.read_text('direct_url.json') or '{}')
assert not direct_url.get('dir_info',{}).get('editable',False),'editable Core is not installed qualification'
recorded=next((entry for entry in installed.files or () if entry.as_posix()=='loopx/__init__.py'),None)
assert recorded is not None,'the installed Core RECORD must own loopx/__init__.py'
package=Path(loopx.__file__).resolve()
record_path=Path(installed.locate_file(recorded)).resolve()
assert record_path==package,('imported namespace is not owned by installed Core',str(package),str(record_path))
assert package.is_relative_to(Path(installed.locate_file('')).resolve()),'Core namespace resolves outside its installed location'
'''
    probe = subprocess.run([str(executable), "-I", "-c", script], cwd=tmp_path,
        env=_environment(str(executable)), capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0, (
        "LOOPX_CORE_TEST_PYTHON must select a clean installed loopx-core distribution",
        probe.stdout, probe.stderr,
    )
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


def _resume_without_adapter(arguments, turn_key, *, needs_validator):
    omit = {"--host-adapter-command-json", "--host-adapter-request-schema"}
    if not needs_validator:
        omit.add("--validation-command-json")
    return [
        argument for index, argument in enumerate(arguments)
        if argument not in omit and (index == 0 or arguments[index - 1] not in omit)
    ] + ["--resume-turn-key", turn_key]


def test_installed_core_identity_help_and_unavailable_workspace(core_python, tmp_path):
    script = '''import importlib.util as u,json,loopx
from pathlib import Path
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
    assert not any(
        command.startswith(("loopx dashboard", "loopx chat", "loopx canary", "loopx explore"))
        for command in commands
    )
    _run(core_python, ["--version"], tmp_path, environment={"PATH": str(tmp_path)})
    _run(core_python, ["--format", "json", "--version"], tmp_path)
    invalid = _run(core_python, ["--format", "invalid", "--version"], tmp_path, expected=2)
    assert "invalid choice" in invalid.stderr
    for blocked in (["dashboard"], ["explore", "--help"], ["explore", "turn-context", "--goal-id", GOAL, "--agent-id", AGENT]):
        refusal = _run(core_python, blocked, tmp_path, expected=2)
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


@pytest.mark.parametrize("arguments", [
    ["capability", "--help"],
    ["capability", "list", "--format", "json"],
    ["capability", "show", "benchmark-toolkit", "--format", "json"],
    ["capability", "show", "issue-fix", "--format", "json"],
])
def test_installed_core_discovers_capabilities_without_benchmark_execution(core_python, tmp_path, arguments):
    from tests.capabilities.test_benchmark_toolkit_loading import DISCOVERY_SCRIPT

    result = subprocess.run([core_python, "-I", "-c", DISCOVERY_SCRIPT,
        json.dumps(["--registry", str(tmp_path / "registry.json"),
                    "--runtime-root", str(tmp_path / "runtime"), *arguments])],
        cwd=tmp_path, env=_environment(core_python, {"PATH": ""}),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    if arguments[-1] == "json":
        assert json.loads(result.stdout)["ok"] is True
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


def test_installed_core_reports_optional_turn_recall(core_python, tmp_path):
    detail = _run(core_python, ["capability", "show", "agent-turn-recall", "--format", "json"], tmp_path)
    record = json.loads(detail.stdout)["capability"]
    assert record["provider_id"] == "loopx"
    assert record["default_enabled"] is False
    assert record["provider_state"] == {"declared": True, "installed": False, "enabled": False, "ready": False}
    assert "loopx-capability-agent-turn-recall" in record["next_real_step"]
    refusal = _run(core_python, ["agent-turn-recall", "--help"], tmp_path, expected=2)
    assert "unavailable in the standalone core" in refusal.stderr
    assert "loopx-capability-agent-turn-recall" in refusal.stderr
    assert not list(tmp_path.iterdir())


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
    return _prepare_task(tmp_path, monkeypatch, core_python, provider=request.param, claim=True)


def _prepare_task(tmp_path, monkeypatch, core_python, *, provider, claim):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    registry = project / ".loopx/registry.json"
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]

    if provider == "file":
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
    assert created["storage_selection"]["provider"] == provider
    assert created["storage_selection"]["handoff_mode"] == "hard_lease"
    command("configure-goal", "--goal-id", GOAL, "--registered-agent", AGENT,
        "--quota-compute", "1", "--execute")
    added = command("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Produce and independently validate squares.json.", "--priority", "P1",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--operation-id", "core-create-task")
    if claim:
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


def test_installed_core_ordinary_turn_defers_explore_evaluation(core_python, task, tmp_path, monkeypatch):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    workspace, runtime, _registry, arguments = task
    isolated = tmp_path / "loads"
    isolated.mkdir()
    isolate_sqlite_runtime(isolated, monkeypatch)
    trace, observer = isolated / "modules.txt", isolated / "observe.mjs"
    observer.write_text("""import {appendFileSync} from "node:fs";
import {registerHooks} from "node:module";
const tracked = new Set(["turn_envelope.ts", "yardstick_decision.ts", "explore_result_writeback.ts",
  "explore_turn_context.ts", "explore_measurements.ts"]);
registerHooks({load(url, context, next) {
  const name = url.split("/").at(-1);
  if (tracked.has(name)) appendFileSync(process.env.LOOPX_TEST_MODULE_TRACE, name + "\\n");
  return next(url, context);
}});
""", encoding="utf-8")
    monkeypatch.setenv("LOOPX_TEST_MODULE_TRACE", str(trace))
    monkeypatch.setenv("NODE_OPTIONS", f"{os.environ['NODE_OPTIONS']} --import={observer.as_uri()}")
    result = json.loads(_run(core_python, arguments, workspace).stdout)
    key = _assert_committed(core_python, workspace, runtime, result)
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", key], workspace).stdout)
    assert replay["resume_turn_key"] == key
    assert not any(replay["effects"][name] for name in ("host_invoked", "state_written", "quota_spent"))
    assert set(trace.read_text(encoding="utf-8").splitlines()) == {"turn_envelope.ts"}


def test_installed_core_goal_lifecycle_preserves_work_and_resumes_execution(core_python, task):
    workspace, runtime, registry, arguments = task
    host_started = workspace / "lifecycle-host-started"
    host_script = workspace / "lifecycle-host.py"
    host_script.write_text(
        "import pathlib\npathlib.Path('lifecycle-host-started').write_text('started')\n" + HOST_SOURCE,
    )
    arguments = list(arguments)
    arguments[arguments.index("--host-adapter-command-json") + 1] = json.dumps(
        [core_python, "-I", str(host_script)],
    )
    todo_argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "todo", "list", "--goal-id", GOAL]
    original_todo = json.loads(_run(core_python, todo_argv, workspace).stdout)["todos"][0]
    todo_id = original_todo["todo_id"]
    lease_argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "task-lease", "inspect", "--goal-id", GOAL, "--todo-id", todo_id]
    original_lease = json.loads(_run(core_python, lease_argv, workspace).stdout)["lease"]
    global_registry = runtime / "registry.global.json"
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "goal-lifecycle", "--goal-id", GOAL]
    for operation, state in (("stop", "stopped"), ("resume", "active")):
        before = (registry.read_bytes(), global_registry.read_bytes())
        preview = json.loads(_run(core_python, [*prefix, "--operation", operation], workspace).stdout)
        assert preview["dry_run"] and not preview["written"]
        assert (registry.read_bytes(), global_registry.read_bytes()) == before
        refused = json.loads(_run(core_python, [*prefix, "--operation", operation,
            "--execute"], workspace, expected=1).stdout)
        assert refused["written"] is False
        stale = json.loads(_run(core_python, [*prefix, "--operation", operation,
            "--actor-kind", "owner", "--expected-state-fingerprint", "0" * 64,
            "--execute"], workspace, expected=1).stdout)
        assert stale["error_kind"] == "goal_action_stale" and stale["written"] is False
        assert (registry.read_bytes(), global_registry.read_bytes()) == before
        result = json.loads(_run(core_python, [*prefix, "--operation", operation,
            "--actor-kind", "owner", "--expected-state-fingerprint", preview["observed_state_fingerprint"],
            "--execute"], workspace).stdout)
        assert result["written"] and result["readback"]["verified"]
        for path in (registry, global_registry):
            goal = next(goal for goal in json.loads(path.read_text())["goals"] if goal["id"] == GOAL)
            assert goal["activation"]["state"] == state
        assert json.loads(_run(core_python, todo_argv, workspace).stdout)["todos"][0] == original_todo
        assert json.loads(_run(core_python, lease_argv, workspace).stdout)["lease"] == original_lease
        if state == "stopped":
            blocked = json.loads(_run(core_python, arguments, workspace, expected=1).stdout)
            assert blocked["ok"] is False, blocked
            # An entered executor preserves uncertainty in its error response.
            # Independent fixture observations below establish actual effects.
            for effect in ("host_invoked", "state_written", "quota_spent"):
                value = blocked["effects"][effect]
                assert value is False or value is None, blocked
        assert _spends(runtime) == 0
        assert not host_started.exists()
        assert not (workspace / "host-invocations").exists()
        assert not (workspace / "squares.json").exists()
    result = json.loads(_run(core_python, arguments, workspace).stdout)
    key = _assert_committed(core_python, workspace, runtime, result)
    assert host_started.read_text() == "started"
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", key], workspace).stdout)
    _assert_committed(core_python, workspace, runtime, replay)
    assert replay["effects"]["host_invoked"] is False
    assert replay["effects"]["state_written"] is False
    assert replay["effects"]["quota_spent"] is False


@pytest.mark.parametrize("operation", ["stop", "resume"])
@pytest.mark.parametrize("actor", ["owner", "controller"])
def test_installed_core_goal_lifecycle_respects_outer_controller(core_python, task, operation, actor):
    workspace, runtime, registry, arguments = task
    before = (registry.read_bytes(), (runtime / "registry.global.json").read_bytes())
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "goal-lifecycle", "--goal-id", GOAL, "--operation", operation, "--actor-kind", actor]
    environment = {"LOOPX_KUNLUNCODE_OUTER_CONTROLLER": "1", "LOOPX_KUNLUNCODE_OUTER_GOAL_ID": GOAL}
    preview = json.loads(_run(core_python, prefix, workspace, environment=environment).stdout)
    assert preview["written"] is False
    blocked = _run(core_python, [*prefix, "--execute"], workspace, environment=environment, expected=2)
    assert blocked.stdout == ""
    assert json.loads(blocked.stderr)["error_code"] == "kunluncode_outer_controller_write_owned"
    assert (registry.read_bytes(), (runtime / "registry.global.json").read_bytes()) == before
    assert _spends(runtime) == 0 and not (workspace / "host-invocations").exists()


@pytest.mark.parametrize("ingest", [False, True])
def test_installed_core_completes_turn_with_configured_optional_recall(core_python, task, ingest):
    from tests.control_plane.reward_memory_host_fixture import enable_live_memory

    workspace, runtime, registry, arguments = task
    enable_live_memory(registry, ingest=ingest)
    host_index = arguments.index("--host-adapter-command-json") + 1
    host = workspace / "recall-host.py"
    host.write_text(HOST_SOURCE.replace(
        "request=json.load(sys.stdin)\n", """request=json.load(sys.stdin)
pathlib.Path('recall-readback.json').write_text(json.dumps(request.get('reward_memory_recall')))
""",
    ))
    arguments[host_index] = json.dumps([core_python, "-I", str(host)])
    execution = subprocess.run([core_python, "-I", "-m", "loopx.entrypoint", *arguments],
        cwd=workspace, env=_environment(core_python), capture_output=True, text=True, timeout=90)
    assert execution.returncode == 0, (execution.stdout, execution.stderr)
    _assert_committed(core_python, workspace, runtime, json.loads(execution.stdout))
    recall = json.loads((workspace / "recall-readback.json").read_text())
    assert recall["status"] == "runtime_unavailable"
    assert recall["reason_code"] == "automatic_recall_not_installed"
    assert recall["provider_call_count"] == 0 and recall["context"] is None
    assert recall["experiment"]["automatic_ingest"] is ingest
    assert ("outcome_ingest_reconciliation" in recall) is ingest


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


@pytest.mark.parametrize("provider", ["sqlite", "file"])
def test_installed_core_prepares_generic_cli_first_turn(core_python, tmp_path, monkeypatch, provider):
    workspace, runtime, registry, arguments = _prepare_task(
        tmp_path, monkeypatch, core_python, provider=provider, claim=False,
    )
    configure_cadence = '''import sys
from loopx.control_plane.effect_runtime import effect_runtime_result

runtime, goal, agent = sys.argv[1:]
result = effect_runtime_result("quota.automation_cadence.manage", {
    "runtime_root": runtime, "goal_id": goal, "agent_id": agent,
    "operation": "configure", "min_interval_minutes": 1440,
    "expected_revision": 0, "owner_reference": "fixture-owner", "execute": True,
})
assert result["ok"] and result["min_interval_minutes"] == 1440
'''
    configured = subprocess.run([core_python, "-I", "-c", configure_cadence, str(runtime), GOAL, AGENT],
        cwd=workspace, env=_environment(core_python), capture_output=True, text=True, timeout=30)
    assert configured.returncode == 0, configured.stderr

    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    before = json.loads(_run(core_python, [*prefix, "quota", "should-run", "--goal-id", GOAL,
        "--agent-id", AGENT, "--context-delivery", "off"], workspace).stdout)
    preparation = before["execution_preparation"]
    assert preparation["state"] == "claim_required" and preparation["reason_code"] == "task_lease_missing"
    lease_args = [*prefix, "task-lease", "inspect", "--goal-id", GOAL, "--todo-id", preparation["todo_id"]]

    refused = json.loads(_run(core_python, [*arguments, "--host-adapter-command-json", "[]"],
                              workspace, expected=1).stdout)
    assert not any(value is True for value in refused["effects"].values())
    assert json.loads(_run(core_python, lease_args, workspace).stdout)["active"] is False
    assert not (workspace / "host-invocations").exists() and _spends(runtime) == 0

    result = json.loads(_run(core_python, arguments, workspace).stdout)
    key = _assert_committed(core_python, workspace, runtime, result)
    assert result["admission"]["reason"] == "resumed_unstarted_reservation"
    replay = json.loads(_run(core_python, [*arguments, "--resume-turn-key", key], workspace).stdout)
    assert replay["replayed"] is True and all(value is False for value in replay["effects"].values())
    _assert_committed(core_python, workspace, runtime, replay)


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
    resume = _resume_without_adapter(arguments, key, needs_validator=False)
    replay = json.loads(_run(core_python, resume, workspace).stdout)
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
@pytest.mark.parametrize("provider", ["sqlite", "file"])
@pytest.mark.parametrize("lease_change", [None, "renew", "release"])
def test_installed_core_recovers_confirmed_result_after_process_loss(
    core_python, tmp_path, monkeypatch, provider, lease_change,
):
    workspace, runtime, registry, arguments = _prepare_task(
        tmp_path, monkeypatch, core_python, provider=provider, claim=False,
    )
    pause = workspace / "pause-validator"
    pause.touch()
    adapter = workspace / "original-adapter.py"
    adapter.write_text(HOST_SOURCE)
    arguments[arguments.index("--host-adapter-command-json") + 1] = json.dumps([core_python, "-I", str(adapter)])
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
    adapter.unlink()
    assert not journal.get("quota_spend")
    assert _spends(runtime) == 0
    resume = _resume_without_adapter(arguments, journal["turn_key"], needs_validator=True)
    if lease_change:
        todo_id = journal["plan"]["work_contract"]["selected_todo"]["todo_id"]
        prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
        lease = json.loads(_run(core_python, [*prefix, "task-lease", "inspect", "--goal-id", GOAL,
            "--todo-id", todo_id], workspace).stdout)["lease"]
        _run(core_python, [*prefix, "task-lease", lease_change, "--goal-id", GOAL,
            "--todo-id", todo_id, "--owner", AGENT, "--idempotency-key", lease["idempotency_key"],
            "--expected-version", str(lease["version"])], workspace)
        rejected = json.loads(_run(core_python, resume, workspace, expected=1).stdout)
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
    journal_path = runtime / "goals" / GOAL / "turns" / f"{journal['turn_key'].removeprefix('sha256:')}.json"
    before = journal_path.read_bytes()
    replay = _resume_without_adapter(arguments, journal["turn_key"], needs_validator=False)
    missing_validator = json.loads(_run(core_python, replay, workspace, expected=1).stdout)
    assert missing_validator["status"] == "unavailable"
    assert "--validation-command-json" in missing_validator["reason"]
    assert all(value is False for value in missing_validator["effects"].values())
    assert journal_path.read_bytes() == before
    result = json.loads(_run(core_python, resume, workspace).stdout)
    _assert_committed(core_python, workspace, runtime, result)
    assert result["effects"]["host_invoked"] is False
    settled = json.loads(_run(core_python, replay, workspace).stdout)
    assert settled["replayed"] is True and all(value is False for value in settled["effects"].values())
    assert (workspace / "host-invocations").read_text().splitlines() == ["called"]


def test_installed_core_resume_without_adapter_refuses_new_host_attempt(core_python, task):
    workspace, runtime, _, arguments = task
    host_script = workspace / "invalid-adapter.py"
    host_script.write_text(HOST_SOURCE.replace("'result_kind':'validated_completion'", "'result_kind':'invalid'"))
    arguments[arguments.index("--host-adapter-command-json") + 1] = json.dumps([core_python, "-I", str(host_script)])
    launch = json.loads(_run(core_python, arguments, workspace, expected=1).stdout)
    assert launch["validation_stage"] == "host_result_contract" and launch["effects"]["host_invoked"] is True
    key = launch["resume_turn_key"]
    journal = runtime / "goals" / GOAL / "turns" / f"{key.removeprefix('sha256:')}.json"
    before = journal.read_bytes()
    recovery = _resume_without_adapter(arguments, key, needs_validator=True)
    refused = json.loads(_run(core_python, [*recovery, "--retry-failed-turn"], workspace, expected=1).stdout)
    assert refused["status"] == "unavailable"
    assert "--host-adapter-command-json" in refused["reason"]
    assert all(value is False for value in refused["effects"].values())
    assert journal.read_bytes() == before
    assert _spends(runtime) == 0
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
    from tests.control_plane.host_process_fixture import assert_host_task_context
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
    binary.write_text(binary.read_text().replace("prompt = sys.stdin.read()",
        'prompt = sys.stdin.read()\npathlib.Path("worker-prompt.txt").write_text(prompt, encoding="utf-8")'
    ).replace('output_path = ',
        'pathlib.Path("squares.json").write_text(json.dumps([n*n for n in range(8)]))\noutput_path = '))
    home = tmp_path / "codex-home"
    home.mkdir()
    prompt_path = tmp_path / "worker-prompt.txt"
    environment = {
        "FAKE_CODEX_LOG": str(log), "CODEX_HOME": str(home),
        "FAKE_CODEX_PROMPT_CAPTURE": str(prompt_path),
    }
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
    prompt = (workspace / "worker-prompt.txt").read_text(encoding="utf-8")
    instructions, task_context_json = prompt.split("Turn request:\n", 1)
    assert "one_step means one work segment, not one operation" in instructions
    assert "largest coherent work segment you can reliably complete and verify with your capabilities" in instructions
    assert "same verifiable outcome in one Todo" in instructions
    assert "dependency, approval or validation boundaries" in instructions
    assert "isolate the smallest blocked Todo" in instructions
    assert "exact blocking action and verification check" in instructions
    assert "continue runnable independent work" in instructions
    model_input = json.loads(task_context_json)
    capture = Path(model_input["detail_ref"]["captured_decision"]["path"])
    original = json.loads(capture.read_text(encoding="utf-8"))
    for field in ("agent_todo_command_template", "user_action_command_template", "user_gate_command_template"):
        command_template = original["todo_write_hint"][field]
        assert command_template.startswith("loopx todo add")
        assert command_template not in prompt
    assert original["promotion_readiness_warning"]["requires_readiness_run"] is True
    spend_policy = original["heartbeat_recommendation"]["spend_policy"]
    assert "append exactly one heartbeat spend" in spend_policy
    assert "one_step: mode, not an operation limit" in spend_policy
    assert "append exactly one heartbeat spend" not in prompt
    captured_bytes = capture.read_bytes()
    assert original["execution_preparation"]["lease_proof"]["expected_version"] > 0
    assert original["interaction_contract"]["cli_channel"]["execution_preparation"] == original["execution_preparation"]
    model_task = model_input["task_context"]
    assert "promotion_readiness_warning" not in model_task
    assert "execution_preparation" not in model_task
    assert "execution_preparation" not in model_task["interaction_contract"]["agent_channel"]
    context = assert_host_task_context(model_input, original)
    assert "todo_write_hint" not in context
    assert "spend_policy" not in context["heartbeat_recommendation"]
    assert context["execution_profile"] == original["execution_profile"]
    users = context["interaction_contract"]["agent_channel"]["work_context"]["user_todos"]
    model_proof = users["authority_read"]["todo_read_model"]
    assert model_proof["schema_version"] == "loopx_todo_domain_read_record_v0"
    assert model_proof["records_sha256"] and "contract_fields" not in model_proof
    original_reader = json.loads(_run(core_python, [*prefix, "todo", "list", "--goal-id", GOAL, "--role", "user",
        "--status", "open", "--agent-id", AGENT], workspace).stdout)
    assert original_reader["authority_read"]["todo_read_model"]["contract_fields"]
    assert len(log.read_text().splitlines()) == 1 and _spends(runtime) == 1
    resume_command = [item for index, item in enumerate(command)
                      if item != "--todo-id" and (index == 0 or command[index - 1] != "--todo-id")]
    replay = json.loads(_run(core_python, [*resume_command, "--resume-turn-key", result["resume_turn_key"],
        "--validation-command-json", json.dumps([core_python, "-I", "-c", VALIDATOR_SOURCE])],
        workspace, environment=environment).stdout)
    model_input = json.loads(prompt_path.read_text(encoding="utf-8").split("Turn request:\n", 1)[1])
    capture = Path(model_input["detail_ref"]["captured_decision"]["path"])
    original = json.loads(capture.read_text(encoding="utf-8"))
    context = model_input["task_context"]
    assert original["agent_todo_summary"]["monitor_due_items"] == []
    assert original["user_todo_summary"]["gate_open_items"] == []
    assert "monitor_due_items" not in context["agent_todo_summary"]
    assert "gate_open_items" not in context["user_todo_summary"]
    assert (
        context["agent_todo_summary"]["first_executable_items"]
        == original["agent_todo_summary"]["first_executable_items"]
    )
    assert context["user_todo_summary"]["work_counts"] == original["user_todo_summary"]["work_counts"]
    assert model_input["source_decision_hash"] == original["contract_binding"]["content_hash"]
    assert replay["replayed"] is True and all(value is False for value in replay["effects"].values())
    assert len(log.read_text().splitlines()) == 1 and _spends(runtime) == 1
    assert capture.read_bytes() == captured_bytes
