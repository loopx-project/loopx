"""Qualification must distinguish actual execution from pytest's zero exit."""
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
