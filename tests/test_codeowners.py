"""Required technical reviews keep the automated owner on every path."""

from pathlib import Path

from loopx.capabilities.issue_fix.reviewer_recommendation import (
    _load_codeowners,
    _owners_for_path,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_codeowner_rule_retains_the_repository_wide_reviewer():
    source, rules = _load_codeowners(REPO_ROOT)
    assert source == ".github/CODEOWNERS"
    assert rules[0][0] == "*"
    for pattern, owners in rules:
        assert "@loopx-agent" in owners, pattern
        assert "@huangruiteng" in owners, pattern


def test_narrower_rules_and_new_paths_keep_existing_owners():
    _, rules = _load_codeowners(REPO_ROOT)
    for path, retained_owner in (
        ("future-module/new.ts", "@huangruiteng"),
        ("loopx/control_plane/new.ts", "@huangruiteng"),
        ("apps/presentation/dashboard/new.tsx", "@maxliux5"),
        ("loopx/extensions/lark/new.py", "@steven-kid"),
        (".github/CODEOWNERS", "@huangruiteng"),
        (".github/GOVERNANCE.md", "@huangruiteng"),
        ("scripts/ci/new.py", "@huangruiteng"),
        ("docs/product/release-readiness.md", "@huangruiteng"),
    ):
        _, owners = _owners_for_path(rules, path)
        assert retained_owner in owners, path
        assert "@loopx-agent" in owners, path
