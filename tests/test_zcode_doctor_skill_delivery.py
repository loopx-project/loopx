"""ZCode facade diagnostics inspect the installer-owned files without mutation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx import doctor
from loopx.slash_command_files import MANAGED_MARKER_PREFIX
from loopx.slash_command_install import inspect_skill_facades, install_slash_commands


@pytest.fixture
def doctor_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep general doctor infrastructure and all host state in synthetic fixtures."""
    home = tmp_path / "home"
    zcode_home = home / ".zcode"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("ZCODE_HOME", str(zcode_home))
    monkeypatch.delenv("ZCODE_AGENTS_HOME", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.delenv("LOOPX_RELEASE_ROOT", raising=False)
    command = tmp_path / "bin" / "loopx"
    command.parent.mkdir()
    command.write_text("synthetic command; never executed", encoding="utf-8")
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(doctor, "default_runtime_route", lambda: {
        "selected_runtime_root": str(runtime), "status": "configured", "recommended_action": None,
    })
    monkeypatch.setattr(doctor, "resolve_command_path", lambda name: command if name == "loopx" else None)
    monkeypatch.setattr(doctor, "current_script_invocation_path", lambda: None)
    monkeypatch.setattr(doctor, "python_distribution_install", lambda _: {"available": False})
    monkeypatch.setattr(doctor.doctor_git, "trusted_release_ref_for_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(doctor, "latest_promotion_readiness_event", lambda _: {"available": False})
    monkeypatch.setattr(doctor, "probe_registry_write_path", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.collect_effect_runtime_readiness",
        lambda *, deep: {"ready": True, "status": "ready"},
    )
    monkeypatch.setattr(
        "loopx.capabilities.decision_context.freshness.collect_capture_host_diagnostics",
        lambda _: {"healthy": True, "hosts": []},
    )
    monkeypatch.setattr(
        "loopx.desktop_installation.desktop_installation_status",
        lambda _: {"apps": [], "status": "not_installed"},
    )
    monkeypatch.setattr(
        "loopx.zcode_goal_mode.diagnostics.collect_zcode_host_diagnostics",
        lambda **kwargs: {
            "cli": {"status": "not_discovered"},
            "desktop": {"status": "not_discovered"},
            "source_checkout": {"status": "not_selected"},
            "probe_boundary": "Synthetic host diagnostics; no host was executed.",
        },
    )
    return zcode_home


def _install(home: Path, *, cli_bin: str = "loopx") -> None:
    install_slash_commands(execute=True, surfaces=["zcode"], zcode_home=str(home), cli_bin=cli_bin)


def _forbid_codex_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("ZCode diagnostics must not read Codex skill roots")
    monkeypatch.setattr(doctor, "codex_skill_roots", forbidden)
    monkeypatch.setattr(doctor, "installed_skill_summary", forbidden)


def test_zcode_only_install_reports_ready_without_codex_dependency(
    doctor_context: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(doctor_context)
    _forbid_codex_readback(monkeypatch)
    before = {path: path.read_bytes() for path in doctor_context.rglob("SKILL.md")}
    payload = doctor.collect_doctor(agent_type="zcode")
    assert payload["skill_delivery"]["status"] == "ready"
    assert payload["skill_delivery"]["codex_skills_root_applicable"] is False
    assert payload["skill_delivery"]["skill_roots"] == [str(doctor_context / "skills")]
    assert payload["skill"]["path"] == str(doctor_context / "skills/loopx/SKILL.md")
    assert "loopx-project" not in payload["skills"]
    assert all(skill["readback_status"] == "ready" for skill in payload["skills"].values())
    assert payload["install_freshness"]["requires_upgrade"] is False
    assert before == {path: path.read_bytes() for path in doctor_context.rglob("SKILL.md")}


def test_codex_only_install_does_not_satisfy_zcode_surface(
    doctor_context: Path,
) -> None:
    codex_skills = doctor_context.parent / ".codex/skills"
    for name, phrases in doctor.REQUIRED_INSTALLED_SKILL_PHRASES.items():
        path = codex_skills / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("\n".join(phrases), encoding="utf-8")
    payload = doctor.collect_doctor(agent_type="zcode")
    assert payload["skill_delivery"]["status"] == "repair_recommended"
    assert all(skill["readback_status"] == "missing" for skill in payload["skills"].values())
    assert payload["install_freshness"]["requires_upgrade"] is True
    assert "--surface zcode" in payload["install_freshness"]["upgrade_command"]
    assert "--zcode-home" in payload["fix"]
    # Skill delivery remains an optional integration check, not CLI installation failure.
    skill_checks = [check for check in payload["checks"] if check["id"].startswith("installed_")]
    assert skill_checks and all(check["required"] is False for check in skill_checks)
    assert payload["ok"] is True


@pytest.mark.parametrize("damage,status", [
    ("remove_continuation_rule", "stale"), ("user_file", "user_owned"), ("invalid_utf8", "unreadable"),
])
def test_damaged_entry_reports_the_problem_and_preserves_files(
    doctor_context: Path, damage: str, status: str,
) -> None:
    _install(doctor_context)
    entry = doctor_context / "skills/loopx/SKILL.md"
    if damage == "remove_continuation_rule":
        text = entry.read_text(encoding="utf-8")
        assert "stop/gate rules" in text
        entry.write_text(text.replace("stop/gate rules", "ignore stopping conditions"), encoding="utf-8")
    elif damage == "user_file":
        entry.write_text("# My own loopx skill\n", encoding="utf-8")
    else:
        entry.write_bytes(b"invalid UTF-8: \xff")
    before = entry.read_bytes()
    payload = doctor.collect_doctor(agent_type="zcode")
    assert payload["skills"]["loopx"]["readback_status"] == status
    assert payload["skill_delivery"]["status"] == "repair_recommended"
    assert payload["install_freshness"]["requires_upgrade"] is True
    assert entry.read_bytes() == before
    markdown = doctor.render_doctor_markdown(payload)
    assert f"`{status}`" in markdown and str(entry) in markdown
    if damage == "user_file":
        assert "name collision" in markdown
        _install(doctor_context)
        assert entry.read_bytes() == before


def test_missing_global_facade_is_reported_when_task_entry_is_current(doctor_context: Path) -> None:
    _install(doctor_context)
    missing = doctor_context / "skills/loopx-global-summary/SKILL.md"
    missing.unlink()
    payload = doctor.collect_doctor(agent_type="zcode")
    assert payload["skills"]["loopx"]["readback_status"] == "ready"
    assert payload["skills"]["loopx-global-summary"]["readback_status"] == "missing"
    assert payload["skill_delivery"]["status"] == "repair_recommended"


@pytest.mark.parametrize("cli_bin", ["loopx-alt", "uv run loopx", '"C:/tool directory/loopx.exe"'])
def test_current_custom_cli_invocation_is_not_misclassified(tmp_path: Path, cli_bin: str) -> None:
    _install(tmp_path, cli_bin=cli_bin)
    summary = inspect_skill_facades(tmp_path / "skills")
    assert all(skill["readback_status"] == "ready" for skill in summary.values())
    assert summary["loopx"]["cli_bin"] == cli_bin


def test_inconsistent_custom_cli_invocation_is_stale(tmp_path: Path) -> None:
    _install(tmp_path, cli_bin="loopx-alt")
    entry = tmp_path / "skills/loopx/SKILL.md"
    text = entry.read_text(encoding="utf-8")
    assert text.count("loopx-alt") > 1
    entry.write_text(text.replace("loopx-alt", "different-command", 1), encoding="utf-8")
    assert inspect_skill_facades(tmp_path / "skills")["loopx"]["readback_status"] == "stale"



@pytest.mark.parametrize("prefix", [b"# User-owned facade\n", MANAGED_MARKER_PREFIX.encode("utf-8")])
def test_oversized_facade_is_preserved_without_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefix: bytes,
) -> None:
    entry = tmp_path / "skills/loopx/SKILL.md"
    entry.parent.mkdir(parents=True)
    content = prefix + b"x" * (1024 * 1024)
    entry.write_bytes(content)
    def forbidden(*args, **kwargs):
        raise AssertionError("An oversized facade must not enter the template matcher")
    monkeypatch.setattr("loopx.slash_command_install.re.fullmatch", forbidden)
    summary = inspect_skill_facades(tmp_path / "skills")["loopx"]
    assert summary["readback_status"] == "unreadable"
    assert summary["required_phrases"] is False
    assert "1 MiB diagnostic read limit" in summary["reason"]
    assert "file is preserved" in summary["reason"]
    assert entry.read_bytes() == content


def test_doctor_uses_legacy_home_and_current_home_precedence(
    doctor_context: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    legacy = tmp_path / "legacy-zcode"
    _install(legacy)
    monkeypatch.setenv("ZCODE_AGENTS_HOME", str(legacy))
    monkeypatch.delenv("ZCODE_HOME")
    assert doctor.collect_doctor(agent_type="zcode")["skill_delivery"]["status"] == "ready"
    monkeypatch.setenv("ZCODE_HOME", str(doctor_context))
    payload = doctor.collect_doctor(agent_type="zcode")
    assert payload["skill_delivery"]["status"] == "repair_recommended"
    assert payload["skill_delivery"]["skill_roots"] == [str(doctor_context / "skills")]


def test_non_zcode_diagnostics_keep_existing_skill_owner_and_required_semantics(doctor_context: Path) -> None:
    _install(doctor_context)
    default = doctor.collect_doctor()
    codex = doctor.collect_doctor(agent_type="codex-cli")
    assert "zcode" not in default and "zcode" not in codex
    assert default["skills"] == codex["skills"]
    assert default["skill"] == codex["skill"]
    assert default["checks"] == codex["checks"]
    assert default["skill_delivery"]["codex_skills_root_applicable"] is True
    assert codex["skill_delivery"]["status"] == "repair_recommended"
    assert "--surface zcode" not in default["fix"]
    assert "skill_repair_command" not in default["install_freshness"]


@pytest.mark.parametrize("agent_type,installation_only", [(None, False), ("codex-cli", False), ("zcode", True)])
def test_zcode_paths_require_explicit_host_scope_before_inspection(
    monkeypatch: pytest.MonkeyPatch, agent_type: str | None, installation_only: bool,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid host options must fail before installation or user-state inspection")
    monkeypatch.setattr(doctor, "default_runtime_route", forbidden)
    monkeypatch.setattr("loopx.release_candidate.collect_installation_doctor", forbidden)
    with pytest.raises(ValueError, match="--agent-type zcode"):
        doctor.collect_doctor(agent_type=agent_type, installation_only=installation_only, zcode_cli="fixture-cli")


def test_selected_host_paths_reach_only_the_zcode_diagnostic_owner(
    doctor_context: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    def collect(**kwargs):
        captured.update(kwargs)
        return {"synthetic_host_probe": True}
    monkeypatch.setattr("loopx.zcode_goal_mode.diagnostics.collect_zcode_host_diagnostics", collect)
    payload = doctor.collect_doctor(
        agent_type="z-code", zcode_cli="fixture-cli", zcode_desktop="fixture-desktop", zcode_source="fixture-source",
    )
    assert captured == {"cli_path": "fixture-cli", "desktop_path": "fixture-desktop", "source_root": "fixture-source"}
    assert payload["zcode"] == {"synthetic_host_probe": True}


def test_doctor_cli_parses_and_forwards_explicit_zcode_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from loopx.cli import build_parser
    from loopx.cli_commands import doctor as command
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    cli, desktop, source = (str(tmp_path / name) for name in ("cli", "desktop", "source"))
    args = build_parser().parse_args([
        "--runtime-root", str(runtime), "--format", "json", "doctor", "--agent-type", "zcode",
        "--zcode-cli", cli, "--zcode-desktop", desktop, "--zcode-source", source,
    ])
    captured = {}
    printed = []
    def collect(**kwargs):
        captured.update(kwargs)
        return {"ok": True}
    monkeypatch.setattr(command, "collect_doctor", collect)
    assert command.handle_doctor_command(
        args, lambda *values: printed.append(values), registry_path=registry,
    ) == 0
    assert captured == {
        "deep": False, "agent_type": "zcode", "installation_only": False,
        "registry_path": registry, "runtime_root_override": str(runtime),
        "zcode_cli": cli, "zcode_desktop": desktop, "zcode_source": source,
    }
    assert printed == [({"ok": True}, "json", doctor.render_doctor_markdown)]


@pytest.mark.parametrize("scope", [[], ["--agent-type", "codex-cli"], ["--installation-only"]])
def test_doctor_cli_rejects_zcode_paths_before_restart_or_collection(
    monkeypatch: pytest.MonkeyPatch, scope: list[str],
) -> None:
    from loopx.cli import build_parser
    from loopx.cli_commands import doctor as command
    args = build_parser().parse_args([
        "doctor", *scope, "--zcode-cli", "synthetic-cli", "--restart-runtime",
    ])
    def forbidden(*args, **kwargs):
        raise AssertionError("Wrong-scope options must fail before any restart, collection or output")
    monkeypatch.setattr(command, "collect_doctor", forbidden)
    monkeypatch.setattr("loopx.control_plane.effect_runtime.restart_effect_runtime", forbidden)
    with pytest.raises(ValueError, match="--agent-type zcode"):
        command.handle_doctor_command(args, forbidden)


def test_missing_loopx_command_preserves_install_recovery_before_zcode_skills(
    doctor_context: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "resolve_command_path", lambda _: None)
    monkeypatch.setattr(doctor, "current_script_invocation_path", lambda: None)
    generic = doctor.collect_doctor()
    payload = doctor.collect_doctor(agent_type="zcode")
    assert payload["ok"] is False
    assert payload["install_freshness"]["status"] == "missing"
    assert payload["install_freshness"]["upgrade_command"] == doctor.no_clone_upgrade_command(
        doctor_agent_type="zcode",
    )
    assert payload["fix"].startswith(generic["fix"] + "\nAfter restoring the LoopX installation/runtime, ")
    assert "--surface zcode" not in generic["fix"]
    assert payload["skill_delivery"]["repair_command"] in payload["fix"]
    assert payload["install_freshness"]["upgrade_command"] != payload["skill_delivery"]["repair_command"]


def test_unready_effect_runtime_preserves_general_recovery_before_zcode_skills(
    doctor_context: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(doctor_context)
    monkeypatch.setattr(
        "loopx.control_plane.effect_runtime.collect_effect_runtime_readiness",
        lambda *, deep: {"ready": False, "status": "missing"},
    )
    generic = doctor.collect_doctor()
    payload = doctor.collect_doctor(agent_type="zcode")
    assert payload["ok"] is False
    assert payload["skill_delivery"]["status"] == "ready"
    runtime_check = next(
        check for check in payload["checks"] if check["id"] == "typescript_effect_runtime_ready"
    )
    assert runtime_check["required"] is True and runtime_check["ok"] is False
    assert payload["fix"].startswith(generic["fix"] + "\nAfter restoring the LoopX installation/runtime, ")
    assert "--surface zcode" not in generic["fix"]
    assert payload["skill_delivery"]["repair_command"] in payload["fix"]


def test_current_zcode_facades_do_not_hide_release_package_version_mismatch(
    doctor_context: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from loopx import __version__
    from loopx.release_manifest import RELEASE_MANIFEST_FILENAME
    _install(doctor_context)
    release_root = tmp_path / "synthetic-release"
    release_root.mkdir()
    manifest_version = "0.0.0" if __version__ != "0.0.0" else "0.0.1"
    (release_root / RELEASE_MANIFEST_FILENAME).write_text(
        json.dumps({"package": {"version": manifest_version}, "source": {"ref": "stable"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(release_root))
    generic = doctor.collect_doctor()
    payload = doctor.collect_doctor(agent_type="zcode")
    freshness = payload["install_freshness"]
    assert payload["ok"] is True
    assert payload["skill_delivery"]["status"] == "ready"
    assert all(skill["readback_status"] == "ready" for skill in payload["skills"].values())
    assert freshness["status"] == "repair_recommended"
    assert freshness["requires_upgrade"] is True
    assert freshness["reason"] == "release manifest package version differs from runtime package version"
    assert freshness["manifest_package_version_matches_runtime"] is False
    assert freshness["upgrade_command"] == doctor.no_clone_upgrade_command(
        "stable", doctor_agent_type="zcode",
    )
    assert "--surface zcode" not in freshness["upgrade_command"]
    assert payload["fix"].startswith(generic["fix"])
    assert payload["skill_delivery"]["repair_command"] in payload["fix"][len(generic["fix"]):]


@pytest.mark.parametrize("installation_problem", ["package_mismatch", "aged_release"])
@pytest.mark.parametrize("facade_problem", ["missing", "stale"])
def test_mixed_installation_and_facade_problems_keep_both_recovery_owners(
    doctor_context: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    installation_problem: str, facade_problem: str,
) -> None:
    from loopx import __version__
    from loopx.release_manifest import RELEASE_MANIFEST_FILENAME
    _install(doctor_context)
    entry = doctor_context / "skills/loopx/SKILL.md"
    if facade_problem == "missing":
        entry.unlink()
    else:
        text = entry.read_text(encoding="utf-8")
        entry.write_text(text.replace("stop/gate rules", "ignore stopping conditions"), encoding="utf-8")
    before = {path: path.read_bytes() for path in doctor_context.rglob("SKILL.md")}
    release_root = (
        tmp_path / "releases/20000101T000000Z"
        if installation_problem == "aged_release" else tmp_path / "synthetic-release"
    )
    release_root.mkdir(parents=True)
    manifest_version = __version__
    if installation_problem == "package_mismatch":
        manifest_version = "0.0.0" if __version__ != "0.0.0" else "0.0.1"
    (release_root / RELEASE_MANIFEST_FILENAME).write_text(
        json.dumps({"package": {"version": manifest_version}, "source": {"ref": "stable"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(release_root))
    generic = doctor.collect_doctor()
    payload = doctor.collect_doctor(agent_type="zcode")
    freshness = payload["install_freshness"]
    assert payload["ok"] is True
    assert payload["skills"]["loopx"]["readback_status"] == facade_problem
    assert freshness["status"] == "repair_recommended"
    assert freshness["reason"] == "installed LoopX skills are missing or stale"
    assert freshness["requires_upgrade"] is True
    if installation_problem == "package_mismatch":
        assert freshness["manifest_package_version_matches_runtime"] is False
    else:
        assert freshness["release_age_hours"] > doctor.INSTALL_FRESHNESS_STALE_HOURS
    owner_command = doctor.no_clone_upgrade_command("stable", doctor_agent_type="zcode")
    repair_command = payload["skill_delivery"]["repair_command"]
    assert freshness["upgrade_command"].startswith(owner_command + "\n")
    assert repair_command in freshness["upgrade_command"][len(owner_command):]
    assert payload["fix"].startswith(generic["fix"] + "\nAfter restoring the LoopX installation/runtime, ")
    assert repair_command in payload["fix"][len(generic["fix"]):]
    markdown = doctor.render_doctor_markdown(payload)
    assert owner_command in markdown and repair_command in markdown
    assert before == {path: path.read_bytes() for path in doctor_context.rglob("SKILL.md")}

    # Skill repair cannot settle an independent installation problem.
    _install(doctor_context)
    skills_repaired = doctor.collect_doctor(agent_type="zcode")
    assert skills_repaired["skill_delivery"]["status"] == "ready"
    assert skills_repaired["install_freshness"]["requires_upgrade"] is True
    assert skills_repaired["install_freshness"]["upgrade_command"] == owner_command

    repaired_release = tmp_path / "repaired-release"
    repaired_release.mkdir()
    (repaired_release / RELEASE_MANIFEST_FILENAME).write_text(
        json.dumps({"package": {"version": __version__}, "source": {"ref": "stable"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOOPX_RELEASE_ROOT", str(repaired_release))
    recovered = doctor.collect_doctor(agent_type="zcode")
    assert recovered["ok"] is True
    assert recovered["skill_delivery"]["status"] == "ready"
    assert all(skill["readback_status"] == "ready" for skill in recovered["skills"].values())
    assert recovered["install_freshness"]["requires_upgrade"] is False
    assert recovered["install_freshness"]["manifest_package_version_matches_runtime"] is True
