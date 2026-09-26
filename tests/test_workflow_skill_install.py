from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from loopx.capabilities.project_skill_delivery import canonical_project_skill_source
from loopx import file_lock
from loopx import skill_install_readback
from loopx import workflow_skill_install as install_module
from loopx.skill_install_readback import (
    ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
    PACKAGED_HOST_SKILL_IDS,
    PYTHON_DISTRIBUTION_SKILL_INSTALL_MODE,
    PYTHON_DISTRIBUTION_SKILL_INSTALL_OWNER,
    SKILL_INSTALL_READBACK_FILENAME,
    SKILL_VERSION_MARKER_FILENAME,
)
from loopx.workflow_skill_install import (
    render_workflow_skill_install_markdown,
    resolve_workflow_skill_source,
    workflow_skill_install,
)


def test_source_checkout_contains_packaged_workflow_skills() -> None:
    source = resolve_workflow_skill_source()

    assert source["available"] is True
    assert source["kind"] == "source_checkout"
    assert "loopx-benchmark" in PACKAGED_HOST_SKILL_IDS
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        assert (Path(source["skills_root"]) / skill_id / "SKILL.md").is_file()


def test_entry_metadata_repair_is_previewed_recorded_and_uninstalled(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    workflow_skill_install(skills_dir=skills_dir, execute=True)
    metadata = skills_dir / "loopx/agents/openai.yaml"
    assert 'display_name: "LoopX"' in metadata.read_text()
    assert "allow_implicit_invocation: false" in metadata.read_text()
    metadata.unlink()
    # Model an older install whose receipt legitimately omitted metadata.
    skill_install_readback.write_skill_install_readback(
        skills_dir=skills_dir,
        skill_ids=ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
        source_root=Path(__file__).resolve().parents[1],
    )
    preview = workflow_skill_install(skills_dir=skills_dir, execute=False)
    assert preview["before"]["ready"] is True
    assert preview["entry"]["status"] == "unchanged"
    assert preview["entry"]["metadata_status"] == "would_create"
    assert preview["install_required"] is True
    assert not metadata.exists()
    result = workflow_skill_install(skills_dir=skills_dir, execute=True)
    assert result["ok"] and result["after"]["ready"]
    assert 'display_name: "LoopX"' in metadata.read_text()
    assert workflow_skill_install(skills_dir=skills_dir, execute=False)["install_required"] is False
    assert workflow_skill_install(skills_dir=skills_dir, execute=True, uninstall=True)["ok"]
    assert not metadata.exists()


def test_pip_target_distribution_finds_filtered_data_file_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_root = tmp_path / "runtime" / "site-packages"
    module_file = distribution_root / "loopx" / "workflow_skill_install.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("# fixture\n", encoding="utf-8")
    skills_root = distribution_root / "share" / "loopx" / "skills"
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        skill_dir = skills_root / skill_id
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")

    class TargetDistribution:
        version = "0.5.3"
        files: tuple[Path, ...] = ()

        @staticmethod
        def locate_file(path: str) -> Path:
            return distribution_root / path

    monkeypatch.setattr(install_module, "__file__", str(module_file))
    monkeypatch.setattr(
        install_module,
        "distribution",
        lambda package: TargetDistribution() if package == "loopx" else None,
    )

    source = resolve_workflow_skill_source()

    assert source["available"] is True
    assert source["kind"] == "python_distribution"
    assert source["skills_root"] == skills_root
    assert source["source_root"] == distribution_root
    assert canonical_project_skill_source("loopx-self-repair") == skills_root / "loopx-self-repair"
    assert source["distribution_version"] == "0.5.3"


def test_install_is_idempotent_and_uninstall_removes_managed_skills(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "host skills"

    installed = workflow_skill_install(skills_dir=skills_dir, execute=True)

    assert installed["ok"] is True
    assert installed["after"]["ready"] is True
    assert sorted(installed["after"]["materialized_skill_ids"]) == sorted(
        ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS
    )
    assert set(installed["installed"]) == set(PACKAGED_HOST_SKILL_IDS)
    assert (skills_dir / "loopx-benchmark" / "SKILL.md").is_file()
    assert "'" in installed["rollback_command"]
    manifest = json.loads(
        (skills_dir / SKILL_INSTALL_READBACK_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["owner"] == PYTHON_DISTRIBUTION_SKILL_INSTALL_OWNER
    assert manifest["integration_mode"] == PYTHON_DISTRIBUTION_SKILL_INSTALL_MODE
    assert manifest["loopx_version"] == install_module.__version__
    for skill_id in ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS:
        marker = json.loads(
            (skills_dir / skill_id / SKILL_VERSION_MARKER_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        assert marker["skill_id"] == skill_id
        assert marker["loopx_version"] == install_module.__version__

    repeated = workflow_skill_install(skills_dir=skills_dir, execute=True)

    assert repeated["ok"] is True
    assert set(repeated["installed"].values()) == {"unchanged"}
    assert repeated["entry"]["status"] == "unchanged"

    removed = workflow_skill_install(
        skills_dir=skills_dir,
        execute=True,
        uninstall=True,
    )

    assert removed["ok"] is True
    assert sorted(removed["result"]["removed"]) == sorted(
        ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS
    )
    assert not (skills_dir / SKILL_INSTALL_READBACK_FILENAME).exists()


def test_install_upgrades_legacy_unversioned_skills(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    installed = workflow_skill_install(skills_dir=skills_dir, execute=True)
    assert installed["ok"] is True

    manifest_path = skills_dir / SKILL_INSTALL_READBACK_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "loopx_skill_install_readback_v0"
    manifest.pop("loopx_version")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    for skill_id in ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS:
        (skills_dir / skill_id / SKILL_VERSION_MARKER_FILENAME).unlink()

    preview = workflow_skill_install(skills_dir=skills_dir)
    upgraded = workflow_skill_install(skills_dir=skills_dir, execute=True)

    assert preview["before"]["status"] == "manifest_contract_invalid"
    assert preview["install_required"] is True
    assert upgraded["ok"] is True
    assert upgraded["after"]["loopx_version_matches"] is True
    assert not upgraded["after"]["version_marker_mismatches"]
    assert set(upgraded["installed"].values()) == {"unchanged"}
    for skill_id in ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS:
        assert (skills_dir / skill_id / SKILL_VERSION_MARKER_FILENAME).is_file()


def test_inspect_markdown_reports_installed_and_active_versions(
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    assert workflow_skill_install(skills_dir=skills_dir, execute=True)["ok"] is True

    inspected = workflow_skill_install(skills_dir=skills_dir)
    markdown = render_workflow_skill_install_markdown(inspected)

    assert f"- installed_loopx_version: `{install_module.__version__}`" in markdown
    assert f"- active_loopx_version: `{install_module.__version__}`" in markdown
    assert "- loopx_version_matches: `True`" in markdown


@pytest.mark.parametrize("skill_id", ["loopx-project", "loopx"])
def test_corrupt_version_marker_allows_inspection_and_reinstall(
    tmp_path: Path, skill_id: str,
) -> None:
    skills_dir = tmp_path / "skills"
    assert workflow_skill_install(skills_dir=skills_dir, execute=True)["ok"] is True
    (skills_dir / skill_id / SKILL_VERSION_MARKER_FILENAME).write_bytes(b"\xff")

    inspected = workflow_skill_install(skills_dir=skills_dir)
    assert inspected["before"]["status"] == "loopx_version_mismatch"
    assert inspected["before"]["version_marker_mismatches"] == [skill_id]
    assert inspected["install_required"] is True

    removed = workflow_skill_install(
        skills_dir=skills_dir, execute=True, uninstall=True,
    )
    assert removed["result"]["preserved_modified"] == [skill_id]
    repaired = workflow_skill_install(skills_dir=skills_dir, execute=True)
    assert repaired["ok"] is True
    assert repaired["after"]["version_marker_mismatches"] == []


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_failed_marker_write_preserves_readback_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    skills_dir = tmp_path / "skills"
    assert workflow_skill_install(skills_dir=skills_dir, execute=True)["ok"] is True
    replace = skill_install_readback.os.replace
    dump = skill_install_readback.json.dump

    def fail_marker_write(payload, handle, **kwargs):
        if payload.get("skill_id") == "loopx":
            handle.write("{")
            raise OSError("synthetic marker failure")
        return dump(payload, handle, **kwargs)

    def fail_marker_replace(source, target):
        if target == skills_dir / "loopx" / SKILL_VERSION_MARKER_FILENAME:
            raise OSError("synthetic marker failure")
        return replace(source, target)

    with monkeypatch.context() as patch:
        if failure == "write":
            patch.setattr(skill_install_readback.json, "dump", fail_marker_write)
        else:
            patch.setattr(skill_install_readback.os, "replace", fail_marker_replace)
        with pytest.raises(OSError, match="synthetic marker failure"):
            workflow_skill_install(skills_dir=skills_dir, execute=True)

    assert workflow_skill_install(skills_dir=skills_dir)["before"]["ready"] is True
    assert workflow_skill_install(skills_dir=skills_dir, execute=True)["ok"] is True


def test_uninstall_preserves_locally_modified_skill(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    installed = workflow_skill_install(skills_dir=skills_dir, execute=True)
    assert installed["ok"] is True

    modified = skills_dir / "loopx-project" / "SKILL.md"
    modified.write_text(modified.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")

    removed = workflow_skill_install(
        skills_dir=skills_dir,
        execute=True,
        uninstall=True,
    )

    assert removed["ok"] is False
    assert removed["result"]["preserved_modified"] == ["loopx-project"]
    assert modified.is_file()
    assert (skills_dir / SKILL_INSTALL_READBACK_FILENAME).is_file()


class _ByteRangeLockBackend:
    """Stand-in for ``msvcrt`` so the Windows lock branch runs on any host."""

    LK_NBLCK = 1
    LK_UNLCK = 0

    def __init__(self) -> None:
        self.modes: list[int] = []

    def locking(self, file_descriptor: int, mode: int, length: int) -> None:
        self.modes.append(mode)


def test_install_takes_the_windows_lock_branch_without_fcntl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _ByteRangeLockBackend()
    monkeypatch.setattr(file_lock, "fcntl", None)
    monkeypatch.setattr(file_lock, "msvcrt", backend)
    skills_dir = tmp_path / "skills"

    installed = workflow_skill_install(skills_dir=skills_dir, execute=True)

    assert installed["ok"] is True
    assert installed["after"]["ready"] is True
    assert backend.modes == [backend.LK_NBLCK, backend.LK_UNLCK]


def test_install_serializes_on_the_shared_workflow_skill_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills_dir = tmp_path / "skills"
    held: list[Path] = []
    locked: list[Path] = []
    real_readback = install_module.write_skill_install_readback

    @contextmanager
    def recording_lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        locked.append(path)
        held.append(path)
        try:
            yield path
        finally:
            held.pop()

    def recording_readback(**kwargs: Any) -> Any:
        assert held, "the install readback was written outside the lock"
        return real_readback(**kwargs)

    monkeypatch.setattr(install_module, "exclusive_file_lock", recording_lock)
    monkeypatch.setattr(install_module, "write_skill_install_readback", recording_readback)

    installed = workflow_skill_install(skills_dir=skills_dir, execute=True)

    assert installed["ok"] is True
    assert locked == [skills_dir / ".loopx-workflow-skills"]
    assert held == []


def test_inspect_does_not_create_target(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"

    inspected = workflow_skill_install(skills_dir=skills_dir)

    assert inspected["ok"] is True
    assert inspected["operation"] == "inspect"
    assert inspected["install_required"] is True
    assert not skills_dir.exists()


def test_install_and_inspect_dsh_native_entry(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"

    installed = workflow_skill_install(
        skills_dir=skills_dir,
        execute=True,
        host_surface="deepseek-harness-native",
    )

    assert installed["ok"] is True
    assert installed["host_surface"] == "deepseek-harness-native"
    entry = (skills_dir / "loopx" / "SKILL.md").read_text(encoding="utf-8")
    assert "--host-surface deepseek-harness-native" in entry
    assert '--thread-id "$DSH_SESSION_ID"' in entry

    inspected = workflow_skill_install(
        skills_dir=skills_dir,
        host_surface="deepseek-harness-native",
    )
    assert inspected["install_required"] is False
    assert inspected["entry"]["status"] == "unchanged"

    generic = workflow_skill_install(skills_dir=skills_dir)
    assert generic["install_required"] is True
    assert generic["entry"]["status"] == "updated"


@pytest.mark.parametrize("layout", ["share/loopx/skills", "skills"])
@pytest.mark.parametrize("with_meipass", [True, False])
def test_frozen_bundle_install_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_meipass: bool, layout: str,
) -> None:
    canonical = Path(resolve_workflow_skill_source()["skills_root"])
    bundle = tmp_path / "application bundle"
    bundled_skills = bundle / layout
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        shutil.copytree(canonical / skill_id, bundled_skills / skill_id)
    shutil.copy2(Path(install_module.__file__).with_name("lexical_retrieval.py"),
                 bundled_skills / "loopx-self-repair/scripts/lexical_retrieval.py")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    if with_meipass:
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    else:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(install_module, "__file__", str(bundle / "loopx" / "workflow_skill_install.py"))

    def unexpected_distribution(name: str) -> None:
        pytest.fail("a frozen process must not consult ambient Python distributions")

    monkeypatch.setattr(install_module, "distribution", unexpected_distribution)
    target = tmp_path / "host skills"
    preview = workflow_skill_install(skills_dir=target)
    assert preview["ok"] is True
    assert preview["source"]["kind"] == "frozen_bundle"
    assert canonical_project_skill_source("loopx-self-repair") == bundled_skills / "loopx-self-repair"
    assert not target.exists()
    installed = workflow_skill_install(skills_dir=target, execute=True)
    assert installed["ok"] is True
    assert installed["after"]["ready"] is True
    manifest = json.loads(
        (target / SKILL_INSTALL_READBACK_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["source"]["kind"] == "frozen_bundle"
    assert (
        installed["after"]["source_revision"]
        == install_module.__version__
    )
    assert installed["after"]["source_revision_matches"] is True
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        assert install_module.hash_skill_tree(
            target / skill_id,
            ignored_relative_paths=(SKILL_VERSION_MARKER_FILENAME,),
        ) == install_module.hash_skill_tree(
            bundled_skills / skill_id,
            ignored_relative_paths=(SKILL_VERSION_MARKER_FILENAME,),
        )
    repeated = workflow_skill_install(skills_dir=target, execute=True)
    assert set(repeated["installed"].values()) == {"unchanged"}
    monkeypatch.setattr(install_module, "__version__", "999.0.0")
    upgraded_bundle = workflow_skill_install(skills_dir=target)
    assert upgraded_bundle["ok"] is True
    assert upgraded_bundle["install_required"] is True
    assert upgraded_bundle["before"]["source_revision_matches"] is False
    # Uninstall must remain usable even if a subsequent bundle loses its data.
    shutil.rmtree(bundled_skills)
    removed = workflow_skill_install(skills_dir=target, execute=True, uninstall=True)
    assert removed["ok"] is True
    assert sorted(removed["result"]["removed"]) == sorted(ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS)


def test_frozen_bundle_missing_shared_scorer_does_not_borrow_ambient_code(tmp_path, monkeypatch):
    canonical = Path(resolve_workflow_skill_source()["skills_root"])
    bundle = tmp_path / "incomplete bundle"
    for skill_id in PACKAGED_HOST_SKILL_IDS:
        shutil.copytree(canonical / skill_id, bundle / "skills" / skill_id)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    result = workflow_skill_install(skills_dir=tmp_path / "host skills", execute=True)
    assert result["ok"] is False
    assert result["source"]["kind"] == "missing"


@pytest.mark.parametrize("partial", [False, True])
def test_frozen_missing_data_does_not_fall_back_to_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: bool,
) -> None:
    bundle = tmp_path / "bundle"
    if partial:
        sentinel = bundle / "share/loopx/skills/loopx-project/SKILL.md"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("# incomplete bundle\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    target = tmp_path / "host skills"
    result = workflow_skill_install(skills_dir=target, execute=True)
    assert result["ok"] is False
    assert result["source"]["kind"] == "missing"
    with pytest.raises(ValueError, match="frozen LoopX bundle"):
        canonical_project_skill_source("loopx-self-repair")
    assert "--add-data" in result["reason"]
    assert not target.exists()


def test_unfrozen_runtime_ignores_meipass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resolve_workflow_skill_source()["kind"] == "source_checkout"


@pytest.mark.parametrize("complete_wheel_layout", [True, False])
def test_frozen_bundle_layout_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, complete_wheel_layout: bool,
) -> None:
    for layout in ("share/loopx/skills", "skills"):
        for skill_id in PACKAGED_HOST_SKILL_IDS:
            if layout.startswith("share/") and not complete_wheel_layout and skill_id != "loopx-project":
                continue
            path = tmp_path / layout / skill_id / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"# {layout}: {skill_id}\n", encoding="utf-8")
        if not layout.startswith("share/") or complete_wheel_layout:
            scorer = tmp_path / layout / "loopx-self-repair/scripts/lexical_retrieval.py"
            scorer.parent.mkdir(parents=True)
            scorer.write_text("# bundled scorer\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    source = resolve_workflow_skill_source()
    expected = "share/loopx/skills" if complete_wheel_layout else "skills"
    assert source["available"] is True
    assert source["skills_root"] == tmp_path / expected
