"""Qualification must distinguish actual execution from pytest's zero exit."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from benchmark.tests.qualify_checkpoint_joint import require_one_pass


@pytest.mark.parametrize("cases,passes", [
    ('<testcase name="continued"/>', True),
    ('', False),
    ('<testcase><skipped message="missing prerequisite"/></testcase>', False),
    ('<testcase><failure/></testcase>', False),
    ('<testcase><error/></testcase>', False),
    ('<testcase/><testcase/>', False),
])
def test_qualification_requires_one_executed_success(tmp_path, cases, passes):
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    if passes:
        require_one_pass(report)
    else:
        with pytest.raises(ValueError, match="actual pass and no skip"):
            require_one_pass(report)


@pytest.fixture
def source_checkout(tmp_path):
    """Real immutable source identity; no installed package or active Goal."""
    source = tmp_path / "source"
    source.mkdir()
    for package in ("loopx", "benchmark", "tests"):
        (source / package).mkdir()
        # Benchmark/tests are namespace packages in the actual checkout.
        if package == "loopx":
            (source / package / "__init__.py").write_text("")
        (source / package / "member.py").write_text("")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "loopx", "benchmark", "tests"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Synthetic source"], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    return source, revision


def _check_source(source, revision, cwd, *, after_import=""):
    helper = Path(__file__).with_name("qualify_checkpoint_joint.py").resolve()
    # Isolated interpreter retains real Python import precedence. Loading the
    # helper by file reproduces a correct entrypoint with foreign dependencies.
    script = """import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('qualification_under_test', sys.argv[1])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
import loopx, benchmark, tests
exec(sys.argv[4])
print(helper.require_source_checkout(Path(sys.argv[2]), sys.argv[3]))
"""
    return subprocess.run([sys.executable, "-B", "-c", script, str(helper), str(source), revision, after_import],
                          cwd=cwd, env={**os.environ, "PYTHONPATH": str(source)}, text=True, capture_output=True)


def test_source_pin_accepts_actual_imports_from_another_working_directory(source_checkout, tmp_path):
    source, revision = source_checkout
    result = _check_source(source, revision, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == revision


@pytest.mark.parametrize("package", ["loopx", "benchmark", "tests"])
def test_pythonpath_cannot_hide_a_foreign_working_directory_package(source_checkout, tmp_path, package):
    source, revision = source_checkout
    foreign = tmp_path / "foreign"
    (foreign / package).mkdir(parents=True)
    (foreign / package / "__init__.py").write_text("")
    result = _check_source(source, revision, foreign)
    assert result.returncode != 0
    assert f"Imported {package} does not match" in result.stderr


def test_source_pin_rejects_already_loaded_foreign_submodule(source_checkout, tmp_path):
    source, revision = source_checkout
    foreign = tmp_path / "foreign.py"
    foreign.write_text("# Synthetic foreign implementation\n")
    result = _check_source(source, revision, tmp_path, after_import=(
        "from types import ModuleType; module=ModuleType('loopx.shadow'); "
        f"module.__file__={str(foreign)!r}; sys.modules['loopx.shadow']=module"))
    assert result.returncode != 0
    assert "Imported loopx.shadow does not match" in result.stderr


def test_source_pin_accepts_unused_namespace_search_path(source_checkout, tmp_path):
    source, revision = source_checkout
    foreign = tmp_path / "foreign"
    (foreign / "benchmark").mkdir(parents=True)
    result = _check_source(source, revision, foreign)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == revision


def test_source_pin_rejects_foreign_member_loaded_through_namespace(source_checkout, tmp_path):
    source, revision = source_checkout
    foreign = tmp_path / "foreign"
    (foreign / "benchmark").mkdir(parents=True)
    (foreign / "benchmark" / "foreign_member.py").write_text("# Foreign implementation\n")
    result = _check_source(source, revision, foreign, after_import="import benchmark.foreign_member")
    assert result.returncode != 0
    assert "Imported benchmark.foreign_member does not match" in result.stderr


@pytest.mark.parametrize("fault", ["revision", "dirty", "short_pin"])
def test_source_pin_rejects_unattributable_checkout(source_checkout, tmp_path, fault):
    source, revision = source_checkout
    if fault == "revision":
        revision = "0" * 40
    elif fault == "dirty":
        (source / "untracked.py").write_text("# Synthetic source mutation\n")
    else:
        revision = revision[:12]
    result = _check_source(source, revision, tmp_path)
    assert result.returncode != 0
    assert {"revision": "does not match its declared immutable revision",
            "dirty": "clean dedicated source checkout", "short_pin": "explicit full commit SHA"}[fault] in result.stderr


def test_source_pin_is_rechecked_after_late_import(source_checkout, tmp_path):
    source, revision = source_checkout
    foreign = tmp_path / "foreign.py"
    foreign.write_text("# A dependency loaded only after preflight\n")
    result = _check_source(source, revision, tmp_path, after_import=(
        f"helper.require_source_checkout(Path({str(source)!r}), {revision!r}); "
        "from types import ModuleType; module=ModuleType('benchmark.late'); "
        f"module.__file__={str(foreign)!r}; sys.modules['benchmark.late']=module"))
    assert result.returncode != 0
    assert "Imported benchmark.late does not match" in result.stderr
