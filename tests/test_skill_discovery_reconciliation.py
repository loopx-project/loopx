"""Discovery migration keeps one managed route and preserves unrelated skills."""

import json
from pathlib import Path

import pytest

from loopx import skill_install_readback as readback
from loopx.slash_command_install import install_slash_commands
from loopx.workflow_skill_install import workflow_skill_install

MARKER = "<!-- loopx-managed-slash-command:v1 command=/loopx surface=codex-skills -->\n"


def skill(root: Path, name: str, text: str = MARKER) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(readback, "_user_home", lambda: tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-profile"))
    target = tmp_path / "codex-profile" / "skills"
    alternate = tmp_path / ".agents" / "skills"
    return target, alternate


def test_install_reconciles_both_roots_and_retires_codex_aliases(roots):
    target, alternate = roots
    for root in roots:
        skill(root, "loopx")
        skill(root, "loopx-pr-review")
        skill(root, "loop-global-summary")
    # Cross-host import can rewrite the surface label and omit metadata.
    for path in alternate.glob("*/SKILL.md"):
        path.write_text(MARKER.replace("codex-skills", "Codex-skills"))
    rich = "# LoopX PR Review\n\nRun `loopx pr-review` first\n"
    skill(target, "loopx-pr-review", rich)
    readback.write_skill_install_readback(
        skills_dir=target, skill_ids=["loopx-pr-review"], source_root=target
    )
    original = {
        p: p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()
    }
    preview = install_slash_commands(execute=False, surfaces=["codex"])
    assert "loopx" in preview["codex_skill_reconciliation"]["would_retire"]
    assert all(p.read_bytes() == content for p, content in original.items())
    result = install_slash_commands(execute=True, surfaces=["codex"])
    assert set(result["codex_skill_reconciliation"]["retired"]) == {
        "loopx",
        "loopx-pr-review",
        "loop-global-summary",
    }
    assert not list(alternate.glob("*/SKILL.md"))
    assert not (target / "loop-global-summary" / "SKILL.md").exists()
    assert (target / "loopx-pr-review" / "SKILL.md").read_text() == rich
    assert (target / "loopx-global-summary" / "SKILL.md").exists()
    again = install_slash_commands(execute=True, surfaces=["codex"])
    assert again["codex_skill_reconciliation"]["retired"] == []


@pytest.mark.parametrize(
    "case",
    [
        "user_replacement",
        "sole_copy",
        "rich_workflow",
        "attachment",
        "user_metadata",
        "modified_receipt",
        "missing_hash",
        "symlink",
        "same_root",
        "untrusted_manifest",
    ],
)
def test_reconciliation_preserves_unowned_or_irreplaceable_content(tmp_path, case):
    target, alternate = tmp_path / "target", tmp_path / "alternate"
    sid = "loopx"
    skill(target, sid)
    candidate = skill(alternate, sid)
    if case == "user_replacement":
        (target / sid / "SKILL.md").write_text("Different user workflow")
    elif case == "sole_copy":
        (target / sid / "SKILL.md").unlink()
    elif case == "rich_workflow":
        candidate.write_text("# LoopX PR Review\n")
        readback.write_skill_install_readback(
            skills_dir=alternate, skill_ids=[sid], source_root=tmp_path
        )
    elif case == "attachment":
        (candidate.parent / "notes.md").write_text("User notes")
    elif case == "user_metadata":
        (candidate.parent / "agents").mkdir()
        (candidate.parent / "agents/openai.yaml").write_text("interface: {}")
    elif case == "modified_receipt":
        readback.write_skill_install_readback(
            skills_dir=alternate, skill_ids=[sid], source_root=tmp_path
        )
        candidate.write_text(MARKER + "User customization")
    elif case in {"missing_hash", "untrusted_manifest"}:
        candidate.write_text("User workflow")
        manifest = {
            "owner": readback.SKILL_INSTALL_OWNER
            if case == "missing_hash"
            else "unknown",
            "materialized_skill_ids": [sid],
        }
        (alternate / readback.SKILL_INSTALL_READBACK_FILENAME).write_text(
            json.dumps(manifest)
        )
    elif case == "symlink":
        candidate.parent.rename(tmp_path / "linked")
        (alternate / sid).symlink_to(tmp_path / "linked", target_is_directory=True)
    elif case == "same_root":
        alternate = target
        candidate = target / sid / "SKILL.md"
    before = candidate.read_bytes()
    result = readback.retire_duplicate_managed_skills(
        target, alternate_root=alternate, execute=True
    )
    assert result["retired"] == []
    assert candidate.read_bytes() == before


def test_workflow_install_also_reconciles(roots):
    target, alternate = roots
    skill(alternate, "loopx")
    result = workflow_skill_install(skills_dir=target, execute=True)
    assert result["ok"]
    assert result["skill_reconciliation"]["retired"] == ["loopx"]
    assert (target / "loopx" / "SKILL.md").exists()


def test_custom_home_does_not_touch_another_codex_profile(roots):
    target, alternate = roots
    old = target.parents[1] / ".codex" / "skills"
    candidate = skill(old, "loopx")
    skill(alternate, "loopx")
    install_slash_commands(execute=True, surfaces=["codex"])
    assert candidate.exists()
    assert not (alternate / "loopx").exists()


def test_user_alias_is_preserved_and_deprecated_host_aliases_are_retired(tmp_path):
    """A user-owned alias skill is never touched, and the deprecated managed
    alias is not republished to another host root: a cross-host import would
    otherwise copy it into the shared ~/.agents/skills root next to the
    canonical facade and make one outcome resolve twice."""

    codex, claude = tmp_path / "codex", tmp_path / "claude"
    custom = skill(codex / "skills", "loop-global-summary", "User skill")
    deprecated = skill(claude / "skills", "loop-global-summary")
    install_slash_commands(
        execute=True,
        surfaces=["codex", "claude-code"],
        codex_home=str(codex),
        claude_home=str(claude),
    )
    assert custom.read_text() == "User skill"
    assert not deprecated.exists()
    assert (claude / "skills/loopx-global-summary/SKILL.md").exists()


def test_retirement_updates_receipt_without_blessing_modified_survivor(tmp_path):
    target, alternate = tmp_path / "target", tmp_path / "alternate"
    for root in (target, alternate):
        skill(root, "loopx")
        skill(root, "loopx-pr-review")
        readback.write_skill_install_readback(
            skills_dir=root, skill_ids=["loopx", "loopx-pr-review"], source_root=tmp_path)
    receipt = alternate / readback.SKILL_INSTALL_READBACK_FILENAME
    before = json.loads(receipt.read_text())
    (alternate / "loopx-pr-review/SKILL.md").write_text(MARKER + "User edit")
    result = readback.retire_duplicate_managed_skills(target, alternate_root=alternate, execute=True)
    assert result["retired"] == ["loopx"]
    after = json.loads(receipt.read_text())
    assert after["materialized_skill_ids"] == ["loopx-pr-review"]
    assert after["skills"]["items"] == {"loopx-pr-review": before["skills"]["items"]["loopx-pr-review"]}
    assert after["skills"]["digest"] == readback._skills_digest(after["skills"]["items"])
