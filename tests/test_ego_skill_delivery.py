from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.workflow_skill_install import workflow_skill_install
from loopx.workflow_skills import ego_delivery as delivery


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    vendor = tmp_path / "app/version-one/ego-browser"
    vendor.mkdir(parents=True)
    (vendor / "SKILL.md").write_text(
        "---\nname: ego-browser\ndescription: Browser operations.\n---\n"
        "# Ego\nClaim only when the user explicitly asks.\n"
        "Finish once; protect user tabs.\n"
    )
    (vendor / "references").mkdir()
    (vendor / "references/api.md").write_text("Version-one API.\n")
    source = home / ".local/share/ego/ego-skills"
    source.parent.mkdir(parents=True)
    source.symlink_to(vendor, target_is_directory=True)
    entry = home / ".agents/skills/ego-browser"
    entry.parent.mkdir(parents=True)
    entry.symlink_to(source, target_is_directory=True)
    return home, vendor, source, entry, home / ".codex/skills"


def test_refresh_copies_installed_skill_without_editing_app_and_reuses_idle_task(host):
    _, vendor, _, entry, skills = host
    original = (vendor / "SKILL.md").read_bytes()
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills)["status"]
        == "would_refresh"
    )
    assert entry.is_symlink()
    result = delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert result["ready"] and result["status"] == "refreshed"
    assert not entry.is_symlink()
    assert (vendor / "SKILL.md").read_bytes() == original
    text = (entry / "SKILL.md").read_text()
    assert "same already-authorized task" in text
    assert "without another ownership-confirmation question" in text
    assert "Stop immediately for active user interaction" in text
    assert "explicit pause" in text and "unrelated space" in text
    assert "create one replacement without asking only when" in text
    assert "recorded successful `finish({ keep: [] })`" in text
    assert "confirms that numeric id is absent" in text
    assert "Absence alone does not prove normal closure" in text
    assert "A refused claim never permits replacement" in text
    assert "never recovery from a stuck, blocked, timed-out" in text
    assert "single space for subsequent rounds" in text
    assert "Finish exactly\nonce per space lifetime" in text
    assert "grants no new operation" in text
    assert "Finish once; protect user tabs." in text
    assert (entry / "references/api.md").read_text() == "Version-one API.\n"
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)["status"]
        == "unchanged"
    )


def test_loopx_refresh_tracks_new_vendor_version_and_rollback_follows_current_source(
    host, tmp_path, monkeypatch
):
    _, _, source, entry, skills = host
    delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    next_vendor = tmp_path / "app/version-two/ego-browser"
    next_vendor.mkdir(parents=True)
    (next_vendor / "SKILL.md").write_text(
        "---\nname: ego-browser\ndescription: Browser operations.\n---\nNew API\n"
    )
    source.unlink()
    source.symlink_to(next_vendor, target_is_directory=True)
    monkeypatch.setattr(delivery, "__version__", "next-loopx")
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills)["status"]
        == "would_refresh"
    )
    assert delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)[
        "ready"
    ]
    assert "New API" in (entry / "SKILL.md").read_text()
    assert not (entry / "references/api.md").exists()
    marker = json.loads((entry / ".loopx-ego-skill.json").read_text())
    assert marker["loopx_version"] == "next-loopx"
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills, uninstall=True)[
            "status"
        ]
        == "would_restore"
    )
    assert not entry.is_symlink()
    assert (
        delivery.reconcile_ego_browser_skill(
            skills_dir=skills, execute=True, uninstall=True
        )["status"]
        == "restored"
    )
    assert entry.is_symlink() and entry.resolve() == next_vendor
    assert "LoopX: continuing" not in (entry / "SKILL.md").read_text()


def test_guidance_correction_refreshes_existing_install_without_version_bump(
    host, monkeypatch
):
    _, vendor, _, entry, skills = host
    upstream = (vendor / "SKILL.md").read_bytes()
    current_policy = delivery._POLICY
    # The old policy is a completed managed installation, not a user edit.
    # Corrected guidance must replace it even when Ego and LoopX versions match.
    with monkeypatch.context() as old:
        old.setattr(delivery, "_POLICY", "\n\nPrevious managed continuation policy.\n")
        assert workflow_skill_install(skills_dir=skills, execute=True)["ok"]
    assert (
        workflow_skill_install(skills_dir=skills)["ego_browser"]["status"]
        == "would_refresh"
    )
    result = workflow_skill_install(skills_dir=skills, execute=True)
    assert result["ok"] and result["ego_browser"]["status"] == "refreshed"
    assert (
        entry / "SKILL.md"
    ).read_text() == upstream.decode().rstrip() + current_policy
    marker = json.loads((entry / ".loopx-ego-skill.json").read_text())
    assert (
        marker["policy_sha256"] == hashlib.sha256(current_policy.encode()).hexdigest()
    )
    assert (
        workflow_skill_install(skills_dir=skills)["ego_browser"]["status"]
        == "unchanged"
    )
    assert (vendor / "SKILL.md").read_bytes() == upstream


@pytest.mark.parametrize("uninstall", [False, True])
def test_user_edits_are_preserved_during_upgrade_and_rollback(host, uninstall):
    _, _, _, entry, skills = host
    delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    local = entry / "references/api.md"
    local.write_text("My customization\n")
    result = delivery.reconcile_ego_browser_skill(
        skills_dir=skills, execute=True, uninstall=uninstall
    )
    assert result["status"] == "preserved_modified" and not result["ready"]
    assert "Reconcile these edits" in result["reason"]
    assert local.read_text() == "My customization\n"


@pytest.mark.parametrize(
    "phase,uninstall", [("staged", False), ("parked", False), ("parked", True)]
)
def test_edits_during_skill_publication_are_preserved(
    host, monkeypatch, phase, uninstall
):
    _, vendor, _, entry, skills = host
    delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    (vendor / "references/api.md").write_text("New upstream API\n")
    copytree, replace = delivery.shutil.copytree, Path.replace

    def after_copy(source, target, *args, **kwargs):
        result = copytree(source, target, *args, **kwargs)
        if Path(source) == vendor and phase == "staged":
            (entry / "references/api.md").write_text("Independent user edit\n")
        return result

    def after_park(source, target):
        result = replace(source, target)
        if source == entry and phase == "parked":
            (Path(target) / "references/api.md").write_text("Independent user edit\n")
        return result

    monkeypatch.setattr(delivery.shutil, "copytree", after_copy)
    monkeypatch.setattr(Path, "replace", after_park)
    result = delivery.reconcile_ego_browser_skill(
        skills_dir=skills, execute=True, uninstall=uninstall
    )
    assert not result["ready"], (
        "Concurrent local edits cannot be reported as a ready refresh"
    )
    assert (entry / "references/api.md").read_text() == "Independent user edit\n"


def test_unmanaged_copy_is_not_overwritten(host):
    _, _, _, entry, skills = host
    entry.unlink()
    entry.mkdir()
    (entry / "SKILL.md").write_text("Personal browser skill\n")
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)["status"]
        == "preserved_unmanaged"
    )
    assert (entry / "SKILL.md").read_text() == "Personal browser skill\n"


def test_explicit_directory_does_not_mutate_shared_skill_or_install_absent_ego(
    host, tmp_path
):
    _, vendor, _, shared, _ = host
    custom = tmp_path / "another-host/skills"
    custom.mkdir(parents=True)
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=custom, execute=True)["status"]
        == "not_installed"
    )
    assert shared.is_symlink() and shared.resolve() == vendor
    explicit = custom / "ego-browser"
    explicit.symlink_to(vendor, target_is_directory=True)
    assert delivery.reconcile_ego_browser_skill(skills_dir=custom, execute=True)[
        "ready"
    ]
    assert shared.is_symlink()
    assert not explicit.is_symlink()


@pytest.mark.parametrize("contents", ["broken", "---\nname: ego-browser-other\n---\n"])
def test_invalid_upstream_preserves_link_and_reports_failure(host, contents):
    _, vendor, _, entry, skills = host
    (vendor / "SKILL.md").write_text(contents)
    result = delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert not result["ok"] and result["status"] == "invalid_source"
    assert entry.is_symlink()


def test_missing_upstream_is_visible_and_does_not_remove_managed_copy(host):
    _, _, source, entry, skills = host
    delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    source.unlink()
    result = delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert result["status"] == "source_unavailable" and not result["ready"]
    assert "workflow-skills --install" in result["reason"]
    assert (entry / "SKILL.md").is_file()


def test_failed_atomic_publish_restores_original_vendor_link(host, monkeypatch):
    _, vendor, _, entry, skills = host
    replace = Path.replace

    def fail_candidate(path, target):
        if path.name == "candidate":
            raise OSError("publish interrupted")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_candidate)
    with pytest.raises(OSError, match="publish interrupted"):
        delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert entry.is_symlink() and entry.resolve() == vendor
    assert not [p for p in entry.parent.glob(".loopx-ego-*") if p.is_dir()]
    assert not list(entry.parent.glob(".ego-browser.loopx-previous.*"))


def test_failed_restore_retains_original_outside_temporary_cleanup(host, monkeypatch):
    _, vendor, _, entry, skills = host
    replace = Path.replace

    def fail_publish_and_restore(path, target):
        if path.name == "candidate" or ".loopx-previous." in path.name:
            raise OSError("interrupted twice")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_publish_and_restore)
    with pytest.raises(OSError, match="interrupted twice"):
        delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    backups = list(entry.parent.glob(".ego-browser.loopx-previous.*"))
    assert len(backups) == 1 and backups[0].resolve() == vendor
    assert (backups[0] / "SKILL.md").is_file()
    result = delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert not result["ok"] and result["status"] == "recovery_required"
    assert str(backups[0]) in result["reason"]


def test_default_host_follows_its_existing_managed_profile_alias(host):
    home, _, _, entry, skills = host
    delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    profile_copy = home / ".other-codex/skills/ego-browser"
    profile_copy.parent.mkdir(parents=True)
    entry.replace(profile_copy)
    entry.symlink_to(profile_copy, target_is_directory=True)
    result = delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert result["status"] == "unchanged" and result["path"] == str(profile_copy)
    assert entry.is_symlink()


def test_workflow_upgrade_and_uninstall_reconcile_same_installed_ego(host):
    _, vendor, _, entry, skills = host
    preview = workflow_skill_install(skills_dir=skills)
    assert preview["ego_browser"]["status"] == "would_refresh"
    assert entry.is_symlink()
    installed = workflow_skill_install(skills_dir=skills, execute=True)
    assert installed["ok"] and installed["ego_browser"]["ready"]
    assert (
        workflow_skill_install(skills_dir=skills)["ego_browser"]["status"]
        == "unchanged"
    )
    removed = workflow_skill_install(skills_dir=skills, execute=True, uninstall=True)
    assert removed["ok"] and removed["ego_browser"]["status"] == "restored"
    assert entry.resolve() == vendor


def test_windows_installer_includes_ego_in_existing_filesystem_rollback(host, tmp_path):
    from loopx import windows_install

    _, vendor, _, entry, skills = host
    backup = tmp_path / "backups"
    backup.mkdir()
    paths = windows_install._skill_mutation_paths(skills)
    assert entry in paths
    snapshots = windows_install._snapshot_paths(paths, backup)
    windows_install._install_skills(
        Path(__file__).resolve().parents[1], skills, "2026-01-01T00:00:00Z"
    )
    assert not entry.is_symlink()
    assert "LoopX: continuing" in (entry / "SKILL.md").read_text()
    windows_install._restore_paths(snapshots)
    assert entry.is_symlink() and entry.resolve() == vendor


def test_actual_cli_install_readback_and_rollback_use_isolated_host(host):
    home, vendor, _, entry, skills = host
    env = {
        **os.environ,
        "HOME": str(home),
        "CODEX_HOME": str(skills.parent),
        "LOOPX_USAGE_PING": "0",
    }

    def cli(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "workflow-skills",
                "--format",
                "json",
                *args,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        return json.loads(result.stdout)

    assert cli()["ego_browser"]["status"] == "would_refresh"
    assert cli("--install")["ego_browser"]["ready"]
    assert cli()["ego_browser"]["status"] == "unchanged"
    assert cli("--uninstall")["ego_browser"]["status"] == "restored"
    assert entry.is_symlink() and entry.resolve() == vendor


def test_unresolved_ego_rollback_does_not_remove_other_workflow_skills(host):
    _, _, _, entry, skills = host
    assert workflow_skill_install(skills_dir=skills, execute=True)["ok"]
    (entry / "SKILL.md").write_text("User changed the policy\n")
    result = workflow_skill_install(skills_dir=skills, execute=True, uninstall=True)
    assert not result["ok"] and result["ego_browser"]["status"] == "preserved_modified"
    assert (skills / "loopx-project/SKILL.md").is_file()


def test_invalid_ego_preflight_stops_before_workflow_skill_mutation(host):
    _, vendor, _, entry, skills = host
    (vendor / "SKILL.md").write_text("Invalid upstream\n")
    result = workflow_skill_install(skills_dir=skills, execute=True)
    assert not result["ok"] and result["ego_browser"]["status"] == "invalid_source"
    assert entry.is_symlink()
    assert not skills.exists()


def _profile_copy_with_reset_shared_entry(host):
    _, _, source, entry, skills = host
    delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    managed = skills / "ego-browser"
    managed.parent.mkdir(parents=True, exist_ok=True)
    entry.replace(managed)
    entry.symlink_to(source, target_is_directory=True)
    return entry, managed, skills


def test_refresh_reconnects_shared_entry_reset_by_browser_reinstall(host):
    entry, managed, skills = _profile_copy_with_reset_shared_entry(host)
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills)["status"]
        == "would_refresh"
    )
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)["status"]
        == "refreshed"
    )
    assert entry.resolve() == managed
    assert (
        "without another ownership-confirmation question"
        in (entry / "SKILL.md").read_text()
    )
    assert (
        delivery.reconcile_ego_browser_skill(skills_dir=skills)["status"] == "unchanged"
    )


def test_profile_refresh_preserves_custom_shared_alias(host):
    home, _, _, _, _ = host
    entry, _, skills = _profile_copy_with_reset_shared_entry(host)
    custom = home / "custom-browser"
    custom.mkdir()
    (custom / "SKILL.md").write_text("Personal policy\n")
    entry.unlink()
    entry.symlink_to(custom, target_is_directory=True)
    delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert entry.resolve() == custom
    assert (entry / "SKILL.md").read_text() == "Personal policy\n"


def test_reconnect_preserves_alias_changed_during_staging(host, monkeypatch):
    home, _, _, _, _ = host
    entry, _, skills = _profile_copy_with_reset_shared_entry(host)
    custom = home / "custom-browser"
    custom.mkdir()
    (custom / "SKILL.md").write_text("Personal policy\n")
    symlink_to = Path.symlink_to

    def change_shared_alias(path, target, **kwargs):
        result = symlink_to(path, target, **kwargs)
        if path.name == "alias":
            entry.unlink()
            symlink_to(entry, custom, target_is_directory=True)
        return result

    monkeypatch.setattr(Path, "symlink_to", change_shared_alias)
    result = delivery.reconcile_ego_browser_skill(skills_dir=skills, execute=True)
    assert not result["ready"] and result["status"] == "preserved_unmanaged"
    assert entry.resolve() == custom
    assert (entry / "SKILL.md").read_text() == "Personal policy\n"


def test_windows_rollback_preserves_reset_shared_entry_and_managed_profile(
    host, tmp_path
):
    from loopx import windows_install

    entry, managed, skills = _profile_copy_with_reset_shared_entry(host)
    source = host[2]
    original = (managed / "SKILL.md").read_bytes()
    paths = windows_install._skill_mutation_paths(skills)
    assert entry in paths and managed in paths
    backup = tmp_path / "alias-backup"
    backup.mkdir()
    snapshots = windows_install._snapshot_paths(paths, backup)
    windows_install._install_skills(
        Path(__file__).resolve().parents[1], skills, "2026-01-01T00:00:00Z"
    )
    assert entry.resolve() == managed
    windows_install._restore_paths(snapshots)
    assert entry.resolve() == source.resolve()
    assert (managed / "SKILL.md").read_bytes() == original
