"""Thin pytest: skill delivery parity — packaged skills, readback lifecycle,
missing/stale detection, install dedupe, CWD isolation, delivery modes.

Covers GH-C79 core assertions.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from loopx import __version__
from loopx.skill_install_readback import (
    PACKAGED_HOST_SKILL_IDS,
    ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
    SKILL_INSTALL_READBACK_FILENAME,
    SKILL_INSTALL_READBACK_SCHEMA_VERSION,
    build_skill_install_readback,
    configured_host_skills_dir,
    hash_skill_tree,
    inspect_skill_install_readback,
    retire_duplicate_managed_skills,
    write_skill_install_readback,
)
from loopx.agent_onboarding import (
    REQUIRED_HOST_SKILL_IDS,
    build_agent_onboarding_packet,
)
from loopx.host_loop_activation import (
    HOST_MANAGED_SKILL_AGENT_TYPES,
    SUPPORTED_AGENT_TYPES,
    agent_type_uses_host_managed_skills,
    normalize_agent_type,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_PRIVATE_RE = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"\bBearer" + r"\s+[A-Za-z0-9._-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
]


def _public_safe(obj: object) -> bool:
    text = json.dumps(obj, sort_keys=True) if not isinstance(obj, str) else obj
    return not any(p.search(text) for p in _PRIVATE_RE)


def _write_fixture_skill(skills_dir: Path, skill_id: str, body: str = ""):
    root = skills_dir / skill_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        body or f"# {skill_id}\n\nSynthetic fixture.\n", encoding="utf-8")


# -- Packaged skill catalog ---------------------------------------------------

class TestPackagedSkills:
    def test_pr_program_is_packaged(self):
        assert "loopx-pr-program" in PACKAGED_HOST_SKILL_IDS
        assert "loopx-pr-program" in ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS
        assert "loopx-pr-program" in REQUIRED_HOST_SKILL_IDS
        assert REQUIRED_HOST_SKILL_IDS is ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS

    def test_benchmark_workflow_is_packaged(self):
        assert "loopx-benchmark" in PACKAGED_HOST_SKILL_IDS
        assert "loopx-benchmark" in REQUIRED_HOST_SKILL_IDS

    def test_packaged_skill_dirs_exist(self):
        skills_root = REPO_ROOT / "skills"
        for skill_id in PACKAGED_HOST_SKILL_IDS:
            assert (skills_root / skill_id / "SKILL.md").is_file(), skill_id
            assert (skills_root / skill_id / ".loopx-skill-scope").read_text(
                encoding="utf-8"
            ).strip() == "global", skill_id

    def test_required_skills_match_the_shipped_catalog(self):
        assert set(REQUIRED_HOST_SKILL_IDS) == {"loopx", *PACKAGED_HOST_SKILL_IDS}


# -- Skill install readback lifecycle -----------------------------------------

class TestReadbackLifecycle:
    def test_build_and_write(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            for sid in PACKAGED_HOST_SKILL_IDS:
                _write_fixture_skill(d, sid)

            rb = build_skill_install_readback(
                skills_dir=d, skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert rb["schema_version"] == SKILL_INSTALL_READBACK_SCHEMA_VERSION
            assert rb["owner"] == "loopx_install_script"
            assert rb["integration_mode"] == "fixed_install_script"
            assert rb["loopx_version"] == __version__
            assert sorted(rb["materialized_skill_ids"]) == sorted(
                PACKAGED_HOST_SKILL_IDS)
            assert len(rb["skills"]["items"]) == len(PACKAGED_HOST_SKILL_IDS)

            m = write_skill_install_readback(
                skills_dir=d, skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert m.is_file()
            assert m.name == SKILL_INSTALL_READBACK_FILENAME
            for skill_id in PACKAGED_HOST_SKILL_IDS:
                marker = json.loads(
                    (d / skill_id / ".loopx-skill-version.json").read_text(
                        encoding="utf-8"
                    )
                )
                assert marker == {
                    "integration_mode": "fixed_install_script",
                    "loopx_version": __version__,
                    "owner": "loopx_install_script",
                    "schema_version": "loopx_installed_skill_version_v0",
                    "skill_id": skill_id,
                }

            ins = inspect_skill_install_readback(
                skills_dir=d, required_skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert ins["ready"] is True
            assert ins["status"] == "ready_for_host_load"
            assert ins["integrity_ok"]
            assert ins["loopx_version"] == __version__
            assert ins["expected_loopx_version"] == __version__
            assert ins["loopx_version_matches"] is True
            assert not ins["version_marker_mismatches"]
            assert not ins["missing_skill_ids"]
            assert not ins["digest_mismatches"]
            assert _public_safe(rb)
            assert _public_safe(ins)

    def test_missing_skills_detected(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            created = {"loopx-pr-program", "loopx-project"}
            for sid in created:
                _write_fixture_skill(d, sid)

            ins = inspect_skill_install_readback(
                skills_dir=d, required_skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert not ins["ready"]
            missing = sorted(s for s in PACKAGED_HOST_SKILL_IDS if s not in created)
            assert ins["missing_skill_ids"] == missing

    def test_stale_content_detected(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            for sid in PACKAGED_HOST_SKILL_IDS:
                _write_fixture_skill(d, sid)
            write_skill_install_readback(
                skills_dir=d, skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)

            stale = "loopx-pr-program"
            (d / stale / "SKILL.md").write_text("# Modified!\n", encoding="utf-8")

            ins = inspect_skill_install_readback(
                skills_dir=d, required_skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert not ins["ready"]
            assert ins["status"] == "skill_digest_mismatch"
            assert stale in ins["digest_mismatches"]

    def test_active_runtime_version_mismatch_is_not_ready(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            for sid in PACKAGED_HOST_SKILL_IDS:
                _write_fixture_skill(d, sid)
            write_skill_install_readback(
                skills_dir=d, skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT, loopx_version="0.0.0")

            ins = inspect_skill_install_readback(
                skills_dir=d, required_skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert ins["ready"] is False
            assert ins["status"] == "loopx_version_mismatch"
            assert ins["loopx_version"] == "0.0.0"
            assert ins["expected_loopx_version"] == __version__
            assert ins["loopx_version_matches"] is False
            assert not ins["version_marker_mismatches"]

    def test_missing_skill_version_marker_is_not_ready(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            for sid in PACKAGED_HOST_SKILL_IDS:
                _write_fixture_skill(d, sid)
            write_skill_install_readback(
                skills_dir=d, skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            stale = "loopx-pr-program"
            (d / stale / ".loopx-skill-version.json").unlink()

            ins = inspect_skill_install_readback(
                skills_dir=d, required_skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert ins["ready"] is False
            assert ins["status"] == "loopx_version_mismatch"
            assert ins["version_marker_mismatches"] == [stale]

    def test_legacy_readback_requires_reinstall(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            for sid in PACKAGED_HOST_SKILL_IDS:
                _write_fixture_skill(d, sid)
            manifest_path = write_skill_install_readback(
                skills_dir=d, skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = "loopx_skill_install_readback_v0"
            manifest.pop("loopx_version")
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")

            ins = inspect_skill_install_readback(
                skills_dir=d, required_skill_ids=PACKAGED_HOST_SKILL_IDS,
                source_root=REPO_ROOT)
            assert ins["ready"] is False
            assert ins["status"] == "manifest_contract_invalid"

    def test_null_dir_status(self):
        ins = inspect_skill_install_readback(
            skills_dir=None, required_skill_ids=["loopx"],
            source_root=REPO_ROOT)
        assert not ins["ready"]
        assert ins["status"] == "skills_dir_not_configured"

    def test_rejects_crossed_install_profile(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            for sid in PACKAGED_HOST_SKILL_IDS:
                _write_fixture_skill(d, sid)

            with pytest.raises(ValueError, match="supported profile"):
                build_skill_install_readback(
                    skills_dir=d,
                    skill_ids=PACKAGED_HOST_SKILL_IDS,
                    source_root=REPO_ROOT,
                    owner="loopx_install_script",
                    integration_mode="python_distribution_cli",
                )

    @pytest.mark.parametrize("invalid", [
        {"owner": "unsupported"},
        {"integration_mode": "python_distribution_cli"},
        {"loopx_version": " "},
        {"skill_ids": [*PACKAGED_HOST_SKILL_IDS, "missing-skill"]},
    ])
    def test_invalid_write_preserves_existing_install(self, tmp_path, invalid):
        for sid in PACKAGED_HOST_SKILL_IDS:
            _write_fixture_skill(tmp_path, sid)
        options = dict(skills_dir=tmp_path, skill_ids=PACKAGED_HOST_SKILL_IDS,
                       source_root=REPO_ROOT)
        write_skill_install_readback(**options)
        before = hash_skill_tree(tmp_path)

        with pytest.raises((ValueError, FileNotFoundError)):
            write_skill_install_readback(**{**options, "loopx_version": "0.0.0",
                                           **invalid})

        assert hash_skill_tree(tmp_path) == before


# -- Install dedupe -----------------------------------------------------------

class TestInstallDedupe:
    def test_retire_duplicate_from_alternate_root(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".agents" / "skills"
            alt = Path(td) / ".codex" / "skills"
            target.mkdir(parents=True)
            alt.mkdir(parents=True)

            sid = "loopx-pr-program"
            body = "# loopx-pr-program\n\nFixture.\n"
            for root in (target, alt):
                _write_fixture_skill(root, sid, body)

            write_skill_install_readback(
                skills_dir=alt, skill_ids=[sid], source_root=REPO_ROOT)

            write_skill_install_readback(
                skills_dir=target, skill_ids=[sid], source_root=REPO_ROOT)

            # Dry-run
            dr = retire_duplicate_managed_skills(target, alternate_root=alt,
                                                  execute=False)
            assert dr["ok"]
            assert sid in dr["would_retire"]
            assert sid not in dr["skipped"]

            # Execute
            ex = retire_duplicate_managed_skills(target, alternate_root=alt,
                                                  execute=True)
            assert sid in ex["retired"]
            assert not (alt / sid).is_dir()
            assert (target / sid).is_dir()  # target preserved
            assert _public_safe(ex)


# -- CWD isolation ------------------------------------------------------------

class TestCwdIsolation:
    def test_skills_dir_from_env_not_cwd(self):
        assert configured_host_skills_dir(
            {"LOOPX_SKILLS_DIR": "/opt/skills"}) == Path("/opt/skills")
        assert configured_host_skills_dir(
            {"LOOPX_SKILLS_DIR": ""}) is None
        assert configured_host_skills_dir(
            {"LOOPX_SKILLS_DIR": "  "}) is None
        assert configured_host_skills_dir({}) is None

    def test_relative_expanded_to_home(self):
        resolved = configured_host_skills_dir(
            {"LOOPX_SKILLS_DIR": "~/.custom-skills"})
        assert resolved is not None
        assert resolved.is_absolute()
        assert str(resolved).endswith(".custom-skills")


# -- Skill delivery mode parity -----------------------------------------------

class TestSkillDeliveryModes:
    def test_host_managed_types(self):
        assert HOST_MANAGED_SKILL_AGENT_TYPES == {
            "ark-managed-agent",
            "deepseek-harness-native",
            "trae_app",
            "traex-cli",
            "other-agent",
        }

    def test_delivery_mode_per_agent_type(self):
        host_managed = HOST_MANAGED_SKILL_AGENT_TYPES
        for at in SUPPORTED_AGENT_TYPES:
            canonical = normalize_agent_type(at)
            assert agent_type_uses_host_managed_skills(canonical) == (
                canonical in host_managed)

    def test_onboarding_skill_delivery_contract(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            project.mkdir()
            reg = project / ".loopx" / "registry.json"
            reg.parent.mkdir(parents=True)
            reg.write_text(json.dumps({"goals": [{
                "id": "sg", "domain": "test", "status": "active",
                "repo": str(project),
                "adapter": {"kind": "generic_project_goal_v0",
                             "status": "connected"},
                "coordination": {"agent_model": "peer_v1",
                                 "registered_agents": ["a"]}}]}),
                encoding="utf-8")

            for at in ("codex-cli", "other-agent"):
                canonical = normalize_agent_type(at)
                onboard = build_agent_onboarding_packet(
                    project=project, agent_type=canonical,
                    goal_id="sg", agent_id="a")
                delivery = onboard["skill_delivery"]
                assert delivery["schema_version"] == "loopx_host_skill_delivery_v0"

                if canonical in HOST_MANAGED_SKILL_AGENT_TYPES:
                    assert delivery["mode"] == "host_managed"
                    assert delivery["host_readback_required"]
                    assert "loopx-pr-program" in delivery["required_skill_ids"]
                else:
                    assert delivery["mode"] == "surface_managed"
                    assert not delivery["host_readback_required"]
                assert _public_safe(delivery)


# -- Hash skill tree ----------------------------------------------------------

class TestHashSkillTree:
    def test_available_tree(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_fixture_skill(d, "loopx")
            tree = hash_skill_tree(d / "loopx")
            assert tree["available"]
            assert len(tree["sha256"]) == 64
            assert tree["file_count"] >= 1
            assert _public_safe(tree["sha256"])

    def test_missing_tree(self):
        with tempfile.TemporaryDirectory() as td:
            tree = hash_skill_tree(Path(td) / "nonexistent")
            assert not tree["available"]
            assert tree["sha256"] is None
            assert tree["file_count"] == 0


# -- Public safety ------------------------------------------------------------

class TestPublicSafety:
    def test_readback_json_public_safe(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            _write_fixture_skill(d, "loopx-pr-program")
            rb = build_skill_install_readback(
                skills_dir=d, skill_ids=["loopx-pr-program"],
                source_root=REPO_ROOT)
            assert _public_safe(rb)

    def test_dedup_json_public_safe(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills"
            d.mkdir()
            dr = retire_duplicate_managed_skills(d, execute=False)
            assert _public_safe(dr)
