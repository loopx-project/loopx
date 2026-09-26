from __future__ import annotations

from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import shlex
import sys
import tempfile
from typing import Any, Iterator, Mapping

from . import __version__
from .file_lock import exclusive_file_lock
from .skill_install_readback import (
    ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
    PACKAGED_HOST_SKILL_IDS,
    PYTHON_DISTRIBUTION_SKILL_INSTALL_MODE,
    PYTHON_DISTRIBUTION_SKILL_INSTALL_OWNER,
    SKILL_INSTALL_OWNERS,
    SKILL_INSTALL_READBACK_FILENAME,
    SKILL_VERSION_MARKER_FILENAME,
    hash_skill_tree,
    inspect_skill_install_readback,
    retire_duplicate_managed_skills,
    write_skill_install_readback,
)
from .slash_command_install import materialize_loopx_entry_skill


WORKFLOW_SKILL_INSTALL_SCHEMA_VERSION = "loopx_workflow_skill_install_v0"
MANAGED_ENTRY_STATUSES = {
    "created",
    "updated",
    "unchanged",
    "upgraded_legacy_managed",
}
MANAGED_ENTRY_PREVIEW_STATUSES = {*MANAGED_ENTRY_STATUSES, "would_create"}
_PACKAGED_SKILL_SENTINEL = PurePosixPath(
    "share/loopx/skills/loopx-project/SKILL.md"
)
# `exclusive_file_lock` appends `.lock`, so the sibling it guards keeps the
# lock file at the path installs have always used.
_INSTALL_LOCK_STEM = ".loopx-workflow-skills"


def _valid_source_root(path: Path, *, require_bundled_scorer: bool = False) -> bool:
    return all(
        (path / skill_id / "SKILL.md").is_file()
        for skill_id in PACKAGED_HOST_SKILL_IDS
    ) and (not require_bundled_scorer or
           (path / "loopx-self-repair/scripts/lexical_retrieval.py").is_file())


def resolve_workflow_skill_source() -> dict[str, Any]:
    """Find canonical skills belonging to the active runtime installation."""

    checkout_root = Path(__file__).resolve().parents[1]
    # A frozen executable must not borrow skills from an unrelated checkout or
    # Python installation, even when its own bundle omitted the data files.
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", checkout_root)).resolve()
        # Prefer the existing wheel data layout, but preserve the checkout-like
        # layout that older frozen bundles could already discover.
        bundled_skills = next(
            (
                candidate
                for candidate in (
                    bundle_root / "share" / "loopx" / "skills",
                    bundle_root / "skills",
                )
                if _valid_source_root(candidate, require_bundled_scorer=True)
            ),
            None,
        )
        available = bundled_skills is not None
        return {
            "available": available,
            "kind": "frozen_bundle" if available else "missing",
            "skills_root": bundled_skills if available else None,
            "source_root": bundle_root,
            "distribution_version": __version__ if available else None,
            "reason": (
                "workflow skills are available from the frozen application bundle"
                if available
                else "the frozen LoopX bundle does not contain the packaged workflow skills; "
                "rebuild it with the complete share/loopx/skills tree from the same LoopX "
                "version (PyInstaller --add-data SOURCE:share/loopx/skills)"
            ),
        }

    checkout_skills = checkout_root / "skills"
    if _valid_source_root(checkout_skills):
        return {
            "available": True,
            "kind": "source_checkout",
            "skills_root": checkout_skills,
            "source_root": checkout_root,
            "reason": "workflow skills are available from the source checkout",
        }

    try:
        installed = distribution("loopx")
    except PackageNotFoundError:
        installed = None
    if installed is not None:
        for item in installed.files or ():
            item_path = PurePosixPath(item.as_posix())
            if not item_path.as_posix().endswith(_PACKAGED_SKILL_SENTINEL.as_posix()):
                continue
            sentinel = Path(item.locate()).resolve()
            skills_root = sentinel.parents[1]
            if _valid_source_root(skills_root):
                return {
                    "available": True,
                    "kind": "python_distribution",
                    "skills_root": skills_root,
                    "source_root": Path(installed.locate_file("")).resolve(),
                    "distribution_version": installed.version,
                    "reason": "workflow skills are available from the installed Python distribution",
                }

        # Standard pip rewrites wheel ``data_files`` into ``<target>/share``
        # for ``--target`` installs, but keeps ``../../share`` RECORD rows.
        # ``importlib.metadata.files`` filters those parent-relative rows, so
        # validate the public distribution root fallback explicitly.
        distribution_root = Path(installed.locate_file("")).resolve()
        target_skills = distribution_root / "share" / "loopx" / "skills"
        if _valid_source_root(target_skills):
            return {
                "available": True,
                "kind": "python_distribution",
                "skills_root": target_skills,
                "source_root": distribution_root,
                "distribution_version": installed.version,
                "reason": "workflow skills are available from the installed Python distribution",
            }

    return {
        "available": False,
        "kind": "missing",
        "skills_root": None,
        "source_root": checkout_root,
        "reason": (
            "the active LoopX installation does not contain the packaged workflow skills; "
            "upgrade LoopX before installing host skills"
        ),
    }


def default_workflow_skills_dir(env: Mapping[str, str] | None = None) -> Path:
    source_env = os.environ if env is None else env
    configured = str(source_env.get("CODEX_HOME") or "").strip()
    codex_root = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return codex_root / "skills"


@contextmanager
def _exclusive_install_lock(skills_dir: Path) -> Iterator[None]:
    skills_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(
        skills_dir / _INSTALL_LOCK_STEM,
        operation="workflow_skill_install",
    ):
        yield


def _install_one_skill(source: Path, target: Path) -> str:
    # Wheel/frozen skill data already includes this resource. A source install
    # materializes the same canonical file, then uses the existing hash/atomic
    # install path so repeated installs and drift readback include the scorer.
    if source.name == "loopx-self-repair" and not (source / "scripts/lexical_retrieval.py").is_file():
        with tempfile.TemporaryDirectory(prefix="loopx-repair-skill-") as staging:
            bundle = Path(staging) / source.name
            shutil.copytree(source, bundle)
            shutil.copy2(Path(__file__).with_name("lexical_retrieval.py"),
                         bundle / "scripts/lexical_retrieval.py")
            return _install_one_skill(bundle, target)
    ignored = (SKILL_VERSION_MARKER_FILENAME,)
    if target.is_dir() and hash_skill_tree(
        source, ignored_relative_paths=ignored
    ) == hash_skill_tree(target, ignored_relative_paths=ignored):
        return "unchanged"

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp.", dir=target.parent)
    )
    backup: Path | None = None
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True)
        if target.exists():
            backup = target.with_name(f".{target.name}.previous.{os.getpid()}")
            if backup.exists():
                shutil.rmtree(backup)
            target.replace(backup)
        temporary.replace(target)
    except BaseException:
        if not target.exists() and backup and backup.exists():
            backup.replace(target)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if backup and backup.exists():
            shutil.rmtree(backup)
    return "updated" if backup else "created"


def _load_manifest(skills_dir: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            (skills_dir / SKILL_INSTALL_READBACK_FILENAME).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _uninstall_managed_skills(skills_dir: Path) -> dict[str, Any]:
    manifest = _load_manifest(skills_dir)
    if not manifest or manifest.get("owner") not in SKILL_INSTALL_OWNERS:
        return {
            "ok": False,
            "removed": [],
            "preserved_modified": [],
            "reason": "no LoopX-managed workflow-skill readback is available",
        }

    skills = manifest.get("skills")
    items = skills.get("items") if isinstance(skills, dict) else {}
    if not isinstance(items, dict):
        items = {}
    managed_ids = [
        str(item)
        for item in manifest.get("materialized_skill_ids") or []
        if str(item).strip()
    ]
    removed: list[str] = []
    preserved_modified: list[str] = []
    for skill_id in managed_ids:
        target = skills_dir / skill_id
        recorded = items.get(skill_id)
        if not target.exists():
            continue
        if not isinstance(recorded, dict) or recorded != hash_skill_tree(target):
            preserved_modified.append(skill_id)
            continue
        shutil.rmtree(target)
        removed.append(skill_id)

    manifest_path = skills_dir / SKILL_INSTALL_READBACK_FILENAME
    if not preserved_modified:
        manifest_path.unlink(missing_ok=True)
    return {
        "ok": not preserved_modified,
        "removed": removed,
        "preserved_modified": preserved_modified,
        "reason": (
            "removed LoopX-managed workflow skills"
            if not preserved_modified
            else "preserved workflow skills whose content changed after installation"
        ),
    }


def workflow_skill_install(
    *,
    skills_dir: Path | None = None,
    execute: bool = False,
    uninstall: bool = False,
    cli_bin: str = "loopx",
    host_surface: str | None = None,
) -> dict[str, Any]:
    target_root = (skills_dir or default_workflow_skills_dir()).expanduser().resolve()
    source = resolve_workflow_skill_source()
    source_root = Path(source["source_root"])
    frozen_source_revision = (
        str(source["distribution_version"])
        if source.get("kind") == "frozen_bundle"
        and source.get("distribution_version")
        else None
    )
    before = inspect_skill_install_readback(
        skills_dir=target_root,
        required_skill_ids=ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
        source_root=source_root,
        expected_source_revision_override=frozen_source_revision,
        expected_loopx_version_override=__version__,
    )
    if uninstall:
        result = (
            _uninstall_managed_skills(target_root)
            if execute
            else {
                "ok": True,
                "removed": [],
                "preserved_modified": [],
                "reason": "uninstall preview; pass --uninstall to execute",
            }
        )
        return {
            "ok": result["ok"],
            "schema_version": WORKFLOW_SKILL_INSTALL_SCHEMA_VERSION,
            "operation": "uninstall" if execute else "uninstall_preview",
            "skills_dir": str(target_root),
            "source": _public_source(source),
            "before": before,
            "result": result,
        }

    if not source.get("available"):
        return {
            "ok": False,
            "schema_version": WORKFLOW_SKILL_INSTALL_SCHEMA_VERSION,
            "operation": "install" if execute else "inspect",
            "skills_dir": str(target_root),
            "source": _public_source(source),
            "before": before,
            "reason": source["reason"],
        }

    entry_preview = materialize_loopx_entry_skill(
        skills_dir=target_root,
        execute=False,
        cli_bin=cli_bin,
        host_surface=host_surface,
    )

    if not execute:
        install_command = [
            "loopx",
            "workflow-skills",
            "--install",
            "--skills-dir",
            str(target_root),
        ]
        if host_surface is not None:
            install_command.extend(["--host-surface", host_surface])
        return {
            "ok": True,
            "schema_version": WORKFLOW_SKILL_INSTALL_SCHEMA_VERSION,
            "operation": "inspect",
            "skills_dir": str(target_root),
            "source": _public_source(source),
            "before": before,
            "entry": entry_preview,
            "host_surface": host_surface,
            "install_required": (
                not before.get("ready")
                or entry_preview.get("status") != "unchanged"
                or entry_preview.get("metadata_status") in {
                    "would_create", "updated", "upgraded_legacy_managed",
                }
            ),
            "install_command": shlex.join(install_command),
        }

    if entry_preview["status"] not in MANAGED_ENTRY_PREVIEW_STATUSES:
        return {
            "ok": False,
            "schema_version": WORKFLOW_SKILL_INSTALL_SCHEMA_VERSION,
            "operation": "install",
            "skills_dir": str(target_root),
            "source": _public_source(source),
            "before": before,
            "entry": entry_preview,
            "reason": "the managed $loopx entry skill cannot be materialized safely",
        }

    installed: dict[str, str] = {}
    with _exclusive_install_lock(target_root):
        for skill_id in PACKAGED_HOST_SKILL_IDS:
            installed[skill_id] = _install_one_skill(
                Path(source["skills_root"]) / skill_id,
                target_root / skill_id,
            )
        entry = materialize_loopx_entry_skill(
            skills_dir=target_root,
            execute=True,
            cli_bin=cli_bin,
            host_surface=host_surface,
        )
        if entry["status"] not in MANAGED_ENTRY_STATUSES:
            return {
                "ok": False,
                "schema_version": WORKFLOW_SKILL_INSTALL_SCHEMA_VERSION,
                "operation": "install",
                "skills_dir": str(target_root),
                "source": _public_source(source),
                "installed": installed,
                "entry": entry,
                "reason": "the managed $loopx entry skill could not be materialized",
            }
        write_skill_install_readback(
            skills_dir=target_root,
            skill_ids=ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
            source_root=source_root,
            source_kind_override=(
                "frozen_bundle" if frozen_source_revision else None
            ),
            source_revision_override=frozen_source_revision,
            owner=PYTHON_DISTRIBUTION_SKILL_INSTALL_OWNER,
            integration_mode=PYTHON_DISTRIBUTION_SKILL_INSTALL_MODE,
            loopx_version=__version__,
        )

    reconciliation = retire_duplicate_managed_skills(target_root, execute=True)
    after = inspect_skill_install_readback(
        skills_dir=target_root,
        required_skill_ids=ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
        source_root=source_root,
        expected_source_revision_override=frozen_source_revision,
        expected_loopx_version_override=__version__,
    )
    return {
        "ok": bool(after.get("ready")),
        "schema_version": WORKFLOW_SKILL_INSTALL_SCHEMA_VERSION,
        "operation": "install",
        "skills_dir": str(target_root),
        "source": _public_source(source),
        "host_surface": host_surface,
        "installed": installed,
        "entry": entry,
        "after": after,
        "skill_reconciliation": reconciliation,
        "rollback_command": shlex.join(
            [
                "loopx",
                "workflow-skills",
                "--uninstall",
                "--skills-dir",
                str(target_root),
            ]
        ),
    }


def _public_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(source.get("available")),
        "kind": source.get("kind"),
        "distribution_version": source.get("distribution_version"),
        "reason": source.get("reason"),
    }


def render_workflow_skill_install_markdown(payload: Mapping[str, Any]) -> str:
    source = payload.get("source") or {}
    readback = payload.get("after") or payload.get("before") or {}
    lines = [
        "# LoopX Workflow Skills",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- operation: `{payload.get('operation')}`",
        f"- skills_dir: `{payload.get('skills_dir')}`",
        f"- source: `{source.get('kind')}`",
        f"- ready_before: `{(payload.get('before') or {}).get('ready')}`",
        f"- installed_loopx_version: `{readback.get('loopx_version')}`",
        f"- active_loopx_version: `{readback.get('expected_loopx_version')}`",
        f"- loopx_version_matches: `{readback.get('loopx_version_matches')}`",
    ]
    if payload.get("install_command"):
        lines.append(f"- install: `{payload['install_command']}`")
    if payload.get("rollback_command"):
        lines.append(f"- rollback: `{payload['rollback_command']}`")
    result = payload.get("result") or {}
    if result:
        lines.extend(
            [
                f"- removed: `{','.join(result.get('removed') or [])}`",
                f"- preserved_modified: `{','.join(result.get('preserved_modified') or [])}`",
                f"- reason: {result.get('reason')}",
            ]
        )
    if payload.get("reason"):
        lines.append(f"- reason: {payload['reason']}")
    return "\n".join(lines) + "\n"
