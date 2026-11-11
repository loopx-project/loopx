from __future__ import annotations

import json
from pathlib import Path

import pytest

import loopx.capabilities.project_skill_delivery.core as project_skill_delivery
from loopx.capabilities.material_lifecycle.project_skill import (
    MATERIAL_SKILL_MANAGED_MARKER,
    install_project_material_skill,
    inspect_project_material_skill,
    project_material_skill_target,
    uninstall_project_material_skill,
)
from loopx.capabilities.project_skill_delivery import (
    PROJECT_SKILL_SURFACES,
    install_project_skill,
    inspect_project_skill,
    project_skill_target,
    uninstall_project_skill,
)


def _source(root: Path, *, body: str = "version one\n") -> Path:
    source = root / "source" / "loopx-material"
    source.mkdir(parents=True)
    (source / ".loopx-skill-scope").write_text("project\n", encoding="utf-8")
    (source / "SKILL.md").write_text(body, encoding="utf-8")
    return source


def _project(root: Path) -> Path:
    project = root / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text("{}\n", encoding="utf-8")
    return project


def test_project_skill_install_is_preview_first_and_read_back(tmp_path: Path) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    target = project_material_skill_target(project)

    preview = install_project_material_skill(
        project,
        source_root=source,
    )
    assert preview["mode"] == "preview"
    assert preview["status"] == "missing"
    assert preview["changed"] is True
    assert not target.exists()

    applied = install_project_material_skill(
        project,
        execute=True,
        source_root=source,
    )
    assert applied["status"] == "current"
    assert applied["managed"] is True
    assert applied["changed"] is True
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "version one\n"
    marker = json.loads(
        (target / MATERIAL_SKILL_MANAGED_MARKER).read_text(encoding="utf-8")
    )
    assert marker["delivery"] == "project_managed_copy"
    assert marker["source_digest"] == applied["source_digest"]

    no_change = install_project_material_skill(
        project,
        execute=True,
        source_root=source,
    )
    assert no_change["status"] == "current"
    assert no_change["changed"] is False


def test_project_skill_upgrades_only_an_unmodified_managed_copy(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    target = project_material_skill_target(project)
    install_project_material_skill(project, execute=True, source_root=source)

    (source / "SKILL.md").write_text("version two\n", encoding="utf-8")
    stale = inspect_project_material_skill(project, source_root=source)
    assert stale["status"] == "stale"

    upgraded = install_project_material_skill(
        project,
        execute=True,
        source_root=source,
    )
    assert upgraded["status"] == "current"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "version two\n"

    (target / "SKILL.md").write_text("local edit\n", encoding="utf-8")
    modified = inspect_project_material_skill(project, source_root=source)
    assert modified["status"] == "locally_modified"
    with pytest.raises(ValueError, match="locally_modified"):
        install_project_material_skill(
            project,
            execute=True,
            source_root=source,
        )


def test_project_skill_refuses_unmanaged_target_and_unconnected_project(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    target = project_material_skill_target(project)
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user owned\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unmanaged"):
        install_project_material_skill(
            project,
            execute=True,
            source_root=source,
        )

    unconnected = tmp_path / "unconnected"
    unconnected.mkdir()
    with pytest.raises(ValueError, match="not connected"):
        install_project_material_skill(
            unconnected,
            execute=True,
            source_root=source,
        )


def test_project_skill_rejects_a_surface_root_symlink_escape(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".agents").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the project boundary"):
        install_project_material_skill(
            project,
            execute=True,
            source_root=source,
        )
    assert not (outside / "skills").exists()


def test_project_skill_uninstall_is_preview_first_and_fails_closed(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    target = project_material_skill_target(project)
    install_project_material_skill(project, execute=True, source_root=source)

    preview = uninstall_project_material_skill(project, source_root=source)
    assert preview["mode"] == "preview"
    assert preview["changed"] is True
    assert target.exists()

    removed = uninstall_project_material_skill(
        project,
        execute=True,
        source_root=source,
    )
    assert removed["status"] == "missing"
    assert removed["changed"] is True
    assert not target.exists()


def test_generic_delivery_supports_every_registered_surface(
    tmp_path: Path,
) -> None:
    """The old name pinned four surfaces as the supported set; Kiro CLI's
    workspace skills root joined it, so the assertion is now the registry itself
    rather than a hand-listed subset that silently misses a new host."""
    source = _source(tmp_path)
    project = _project(tmp_path)
    surfaces = PROJECT_SKILL_SURFACES
    assert "antigravity" in surfaces
    assert "kiro-cli" in surfaces
    assert project_skill_target(project, "loopx-material", "antigravity") == (
        project.resolve() / ".agent" / "skills" / "loopx-material"
    )

    preview = install_project_skill(
        project,
        "loopx-material",
        surfaces=surfaces,
        source_root=source,
    )
    assert preview["changed_surfaces"] == list(surfaces)
    assert all(item["status"] == "missing" for item in preview["surfaces"])

    applied = install_project_skill(
        project,
        "loopx-material",
        surfaces=surfaces,
        execute=True,
        source_root=source,
    )
    assert applied["status"] == "current"
    assert all(item["status"] == "current" for item in applied["surfaces"])
    for surface in surfaces:
        target = project_skill_target(project, "loopx-material", surface)
        marker = json.loads(
            (target / MATERIAL_SKILL_MANAGED_MARKER).read_text(encoding="utf-8")
        )
        assert marker["surface"] == surface

    inspected = inspect_project_skill(
        project,
        "loopx-material",
        surfaces=surfaces,
        source_root=source,
    )
    assert inspected["managed"] is True

    removed = uninstall_project_skill(
        project,
        "loopx-material",
        surfaces=surfaces,
        execute=True,
        source_root=source,
    )
    assert removed["status"] == "missing"
    assert all(item["status"] == "missing" for item in removed["surfaces"])


def test_multi_surface_readback_failure_rolls_back_every_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    surfaces = ("codex", "claude-code", "opencode")
    real_inspect = project_skill_delivery.inspect_project_skill
    calls = 0

    def fail_second_inspection(*args, **kwargs):
        nonlocal calls
        calls += 1
        payload = real_inspect(*args, **kwargs)
        if calls == 2:
            payload["surfaces"][0]["status"] = "locally_modified"
        return payload

    monkeypatch.setattr(
        project_skill_delivery,
        "inspect_project_skill",
        fail_second_inspection,
    )
    with pytest.raises(ValueError, match="readback failed"):
        install_project_skill(
            project,
            "loopx-material",
            surfaces=surfaces,
            execute=True,
            source_root=source,
        )
    for surface in surfaces:
        assert not project_skill_target(project, "loopx-material", surface).exists()


def test_multi_surface_uninstall_readback_failure_restores_every_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _source(tmp_path)
    project = _project(tmp_path)
    surfaces = ("codex", "claude-code", "opencode")
    install_project_skill(
        project,
        "loopx-material",
        surfaces=surfaces,
        execute=True,
        source_root=source,
    )
    real_inspect = project_skill_delivery.inspect_project_skill
    calls = 0

    def fail_second_inspection(*args, **kwargs):
        nonlocal calls
        calls += 1
        payload = real_inspect(*args, **kwargs)
        if calls == 2:
            payload["surfaces"][0]["status"] = "current"
        return payload

    monkeypatch.setattr(
        project_skill_delivery,
        "inspect_project_skill",
        fail_second_inspection,
    )
    with pytest.raises(ValueError, match="readback failed"):
        uninstall_project_skill(
            project,
            "loopx-material",
            surfaces=surfaces,
            execute=True,
            source_root=source,
        )
    for surface in surfaces:
        assert project_skill_target(project, "loopx-material", surface).exists()


@pytest.mark.parametrize("scope", [None, "", "unknown", "global project"])
def test_explicit_project_delivery_rejects_undeclared_scope(tmp_path, scope):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("# Synthetic workflow\n")
    if scope is not None:
        (source / ".loopx-skill-scope").write_text(scope)
    with pytest.raises(ValueError, match="scope"):
        inspect_project_skill(tmp_path / "project", "example", source_root=source)
    assert not (tmp_path / "project").exists()
