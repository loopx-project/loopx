from __future__ import annotations

import hashlib
import json
import os
import operator
import queue
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PREFIX = "LOOPX_PYTEST_FAILURE "


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "private-fixture-value"
SAMPLE = '''import pytest

@pytest.fixture
def setup_failure():
    raise ValueError("private-fixture-value")

@pytest.fixture
def teardown_failure():
    yield
    raise RuntimeError("private-fixture-value")

@pytest.mark.parametrize("payload", ["private-fixture-value"])
def test_assertion(payload):
    assert payload == "private-fixture-value-wrong", payload

def test_setup(setup_failure):
    pass

def test_teardown(teardown_failure):
    pass

def test_pass():
    assert True

@pytest.mark.skip(reason="private-fixture-value")
def test_skip():
    pass

@pytest.mark.xfail(reason="private-fixture-value")
def test_xfail():
    assert False

@pytest.mark.xfail(strict=True, reason="private-fixture-value")
def test_strict_xpass():
    pass

def test_subtest(subtests):
    with subtests.test(private="private-fixture-value"):
        assert False
'''


def _environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTEST", "COVERAGE", "COV_CORE"))}
    return {**env, "PYTHONPATH": str(ROOT), "LOOPX_USAGE_PING": "0"}


def _fixture(root, source=SAMPLE):
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_sample.py").write_text(source, encoding="utf-8")
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")


def _command(root, verbosity, workers, enabled):
    return [sys.executable, "-m", "pytest", verbosity, "-n", str(workers),
            *(["-p", "scripts.ci.pytest_timing"] if enabled else []),
            "--rootdir", str(root), "--confcutdir", str(root),
            "--junitxml=out.xml", "tests/test_sample.py"]


def _records(output):
    return [json.loads(line[len(PREFIX):]) for line in output.splitlines()
            if line.startswith(PREFIX)]


@pytest.mark.parametrize("verbosity", ["-q", "-v"])
@pytest.mark.parametrize("workers", [0, 2])
def test_failure_observer_preserves_outcomes_and_hides_payloads(
    tmp_path, verbosity, workers,
):
    outcomes = []
    for enabled in (False, True):
        root = tmp_path / str(enabled)
        _fixture(root)
        result = subprocess.run(_command(root, verbosity, workers, enabled),
                                cwd=root, env=_environment(), capture_output=True,
                                text=True, timeout=45, check=False)
        assert result.returncode == 1, result.stdout + result.stderr
        outcomes.append(sorted((case.get("name"), tuple(child.tag for child in case))
                               for case in ET.parse(root / "out.xml").iter("testcase")))
        records = _records(result.stdout)
        if not enabled:
            assert records == []
            continue
        # pytest reports both the failing subtest and its failed parent phase.
        # Retain both observations without interpreting them as two tests.
        assert len(records) == 6, result.stdout
        assert sum(row["subtest"] for row in records) == 1
        assert {row["phase"] for row in records} == {"setup", "call", "teardown"}
        serialized = json.dumps(records)
        assert PRIVATE not in serialized
        assert str(root) not in serialized
        assert "Captured" not in serialized
        assertion = next(row for row in records if row["display_nodeid"].endswith("test_assertion"))
        full_node = f"tests/test_sample.py::test_assertion[{PRIVATE}]"
        assert assertion["test_id"] == hashlib.sha256(full_node.encode()).hexdigest()
        assert assertion["exception_type"] == "AssertionError"
        assert assertion["frames"][-1] == {
            "path": "tests/test_sample.py", "line": 14,
            "assertion": "payload == '<literal>'",
        }
        assert all(len(row["frames"]) <= 8 for row in records)
    assert outcomes[0] == outcomes[1]


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned process-group interruption")
def test_failure_record_is_flushed_before_later_test_and_hard_interruption(tmp_path):
    _fixture(tmp_path, '''import time
def test_failed():
    assert False, "private-fixture-value"
def test_later():
    print("LATER_TEST_STARTED", flush=True)
    while True:
        time.sleep(.1)
''')
    command = _command(tmp_path, "-q", 0, True) + ["-s"]
    proc = subprocess.Popen(command, cwd=tmp_path, env=_environment(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    output = queue.Queue()
    reader = threading.Thread(target=lambda: [output.put(line) for line in proc.stdout], daemon=True)
    reader.start()
    lines = []
    try:
        while not any("LATER_TEST_STARTED" in line for line in lines):
            lines.append(output.get(timeout=15))
        rows = _records("".join(lines))
        assert len(rows) == 1
        assert rows[0]["exception_type"] == "AssertionError"
        assert rows[0]["frames"][-1]["assertion"] == "False"
        assert PRIVATE not in json.dumps(rows)
        assert not (tmp_path / "out.xml").exists()
        assert not any("FAILURES" in line for line in lines)
        assert proc.poll() is None
    finally:
        # This disposable process group is ours; never touch another pytest.
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        reader.join(timeout=5)
        proc.stdout.close()
    assert proc.returncode < 0


def test_failure_source_filter_excludes_external_paths_and_source_literals(tmp_path):
    from scripts.ci.pytest_failure_context import _source_context, _source_path

    _fixture(tmp_path, 'def test_value():\n    assert "private-fixture-value" == 987654321\n')
    assert _source_path("tests/test_sample.py", tmp_path) == "tests/test_sample.py"
    assert _source_path(tmp_path / "tests/test_sample.py", tmp_path) == "tests/test_sample.py"
    assert _source_path(tmp_path.parent / "private.py", tmp_path) is None
    assert _source_path("../private.py", tmp_path) is None
    assert _source_path("private.py", tmp_path) is None
    assertions = _source_context(tmp_path, "tests/test_sample.py")[0]
    assert assertions[2] == "'<literal>' == '<literal>'"


@pytest.mark.parametrize("source", ["not valid Python", "assert " + "1+" * 1500 + "1"])
def test_unrenderable_source_does_not_replace_failed_report(tmp_path, source):
    from scripts.ci.pytest_failure_context import _source_context

    _fixture(tmp_path, source)
    assert _source_context(tmp_path, "tests/test_sample.py")[0] == {}


@pytest.mark.skipif(sys.version_info < (3, 14), reason="Python 3.14 template-string syntax")
def test_template_string_source_cannot_bypass_literal_redaction(tmp_path):
    from scripts.ci.pytest_failure_context import _source_context

    _fixture(tmp_path, '''def test_template():
    assert t'{helper("private-fixture-value")}' == expected
''')
    assertion = _source_context(tmp_path, "tests/test_sample.py")[0][2]
    assert PRIVATE not in assertion
    assert "<literal>" in assertion


@pytest.mark.parametrize("workers", [0, 2])
@pytest.mark.parametrize("target", ["source", "context", "emit", "type"])
def test_diagnostic_exception_preserves_failed_exit_and_complete_junit(tmp_path, workers, target):
    outcomes = []
    for enabled in (False, True):
        root = tmp_path / str(enabled)
        _fixture(root)
        patch = {
            "source": "pytest_failure_context._source_context = unavailable",
            "context": "pytest_timing.failure_context = unavailable",
            "type": "pytest_timing.exception_type_name = unavailable",
            "emit": "pytest_timing.TimingObserver.emit = broken_emit",
        }[target]
        (root / "conftest.py").write_text(
            "from scripts.ci import pytest_timing, pytest_failure_context\n"
            "original_emit = pytest_timing.TimingObserver.emit\n"
            "def unavailable(*args, **kwargs):\n"
            "    raise RecursionError('private-fixture-value')\n"
            "def broken_emit(self, **row):\n"
            "    if row.get('_prefix') == pytest_failure_context.PREFIX:\n"
            "        unavailable()\n"
            "    return original_emit(self, **row)\n"
            "def pytest_configure(config):\n"
            f"    {patch}\n", encoding="utf-8")
        result = subprocess.run(_command(root, "-q", workers, enabled), cwd=root,
                                env=_environment(), capture_output=True, text=True,
                                timeout=45, check=False)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "INTERNALERROR" not in result.stdout + result.stderr
        outcomes.append(sorted((case.get("name"), tuple(child.tag for child in case))
                               for case in ET.parse(root / "out.xml").iter("testcase")))
        records = _records(result.stdout)
        if enabled and target in {"source", "type"}:
            assert len(records) == 6
            if target == "source":
                assert all(frame["assertion"] is None for row in records for frame in row["frames"])
            else:
                assert all(row["exception_type"] is None for row in records)
        else:
            assert records == []
        assert PRIVATE not in json.dumps(records)
        if enabled:
            # Optional context failure cannot corrupt the existing timing owner.
            log = root / "job.log"
            log.write_text(result.stdout, encoding="utf-8")
            reader = subprocess.run(["node", str(ROOT / "scripts/ci/pytest_timing.ts"), str(log)],
                                    capture_output=True, text=True, timeout=10, check=False)
            assert reader.returncode == 0, reader.stderr
            summary = json.loads(reader.stdout)
            assert summary["execution_complete"] is True
            assert summary["pytest_exit_code"] == 1
            assert summary["finished_cases"] == summary["selected_cases"] == 8
    assert outcomes[0] == outcomes[1]


class _KnownFixtureError(ValueError):
    pass


def test_exception_names_exclude_dynamic_payload_and_injection(monkeypatch):
    import builtins
    from scripts.ci.pytest_failure_context import exception_type_name

    assert exception_type_name(ValueError, ROOT) == "ValueError"
    assert exception_type_name(pytest.fail.Exception, ROOT) == "Failed"
    assert exception_type_name(_KnownFixtureError, ROOT) == "_KnownFixtureError"
    for name in (PRIVATE.replace("-", "_"), "\n" + PREFIX + '{"forged":true}'):
        dynamic = type(name, (Exception,), {"__module__": __name__})
        assert exception_type_name(dynamic, ROOT) is None
        with monkeypatch.context() as scoped:
            scoped.setattr(builtins, name, dynamic, raising=False)
            scoped.setattr(pytest.fail, "Exception", dynamic)
            assert exception_type_name(dynamic, ROOT) is None


def test_exception_metaclass_equality_cannot_impersonate_builtin_identity():
    from scripts.ci.pytest_failure_context import exception_type_name

    class EqualToBuiltin(type):
        def __hash__(cls):
            return hash(ValueError)

        def __eq__(cls, other):
            return other is ValueError

    dynamic = EqualToBuiltin(PRIVATE.replace("-", "_"), (Exception,), {"__module__": __name__})
    assert operator.eq(dynamic, ValueError)  # the fixture really spoofs equality
    assert dynamic is not ValueError
    assert exception_type_name(dynamic, ROOT) is None


def test_known_pytest_exception_rename_keeps_frozen_public_name():
    from scripts.ci.pytest_failure_context import exception_type_name

    original = pytest.fail.Exception
    public_name = original.__name__
    try:
        original.__name__ = PRIVATE.replace("-", "_")
        assert exception_type_name(original, ROOT) == "Failed"
    finally:
        # __name__ is a type slot, absent from the class's own __dict__;
        # restore it explicitly rather than asking monkeypatch to delete it.
        original.__name__ = public_name


def test_failure_frames_and_generated_json_are_bounded_and_payload_free(tmp_path):
    from scripts.ci.pytest_failure_context import failure_context
    from scripts.ci.pytest_timing import TimingObserver

    _fixture(tmp_path, 'def test_value():\n    assert "private-fixture-value" == 987654321\n')
    location = SimpleNamespace(path="tests/test_sample.py", lineno=2, message=PRIVATE)
    entries = [SimpleNamespace(reprfileloc=location)] * 12
    entries += [SimpleNamespace(reprfileloc=SimpleNamespace(
        path="tests/test_sample.py\n" + PREFIX, lineno=2))]
    report = SimpleNamespace(
        failed=True, nodeid="tests/test_sample.py::test_value[" + PRIVATE + "\n" + PREFIX + "]",
        longrepr=SimpleNamespace(reprtraceback=SimpleNamespace(reprentries=entries)),
        when="call", outcome="failed", duration=0.1, loopx_failure_type="AssertionError",
        sections=[("Captured stdout", PRIVATE)],
    )
    context = failure_context(report, tmp_path)
    assert context["frames_truncated"] is True
    assert len(context["frames"]) == 8
    assert all(row == {"path": "tests/test_sample.py", "line": 2,
                       "assertion": "'<literal>' == '<literal>'"} for row in context["frames"])
    lines = []
    reporter = SimpleNamespace(
        ensure_newline=lambda: None, write_line=lines.append,
        _tw=SimpleNamespace(width_of_current_line=0, flush=lambda: None),
    )
    config = SimpleNamespace(rootpath=tmp_path,
                             pluginmanager=SimpleNamespace(get_plugin=lambda name: reporter))
    TimingObserver(config).pytest_runtest_logreport(report)
    assert len(lines) == 2  # one diagnostic and the original ordinary timing phase
    assert len(_records("\n".join(lines))) == 1
    assert PRIVATE not in "\n".join(lines)
    assert all("\n" not in line and len(line) < 6000 for line in lines)
    assert all(isinstance(json.loads(line.split(" ", 1)[1]), dict) for line in lines)


@pytest.mark.parametrize("value", ["x" * 10000, "\n" + PREFIX, {"payload": PRIVATE}, 7])
def test_corrupt_extra_metadata_cannot_inject_or_expand_failure_record(tmp_path, value):
    from scripts.ci.pytest_failure_context import failure_context

    report = SimpleNamespace(longrepr=None, loopx_failure_type=value)
    context = failure_context(report, tmp_path)
    assert context == {"exception_type": None, "frames": [], "frames_truncated": False}
    assert len(json.dumps(context)) < 100


@pytest.mark.skipif(os.name != "posix", reason="POSIX synthetic symlink")
def test_source_context_cannot_follow_symlink_outside_root(tmp_path):
    from scripts.ci.pytest_failure_context import _source_path

    root = tmp_path / "source"
    _fixture(root)
    outside = tmp_path / "private.py"
    outside.write_text(PRIVATE, encoding="utf-8")
    (root / "tests/escape.py").symlink_to(outside)
    assert _source_path("tests/escape.py", root) is None
