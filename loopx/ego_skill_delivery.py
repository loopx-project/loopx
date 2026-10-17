"""Refresh the installed Ego host skill without writing the browser bundle.

This filesystem adapter belongs to workflow-skill delivery. It supplies Agent
guidance, not browser-control authority or a new capability/provider lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import __version__
from .file_lock import exclusive_file_lock


_MARKER = ".loopx-ego-skill.json"
_OWNER = "loopx_ego_host_skill_v1"
_POLICY = """

## LoopX: continuing an authorized browser task

This section overrides only a blanket requirement above or in API references
to ask again before reclaiming a user-owned or inactive TaskSpace, including
an entry-time user-control message caused by your own normal handback.

For the same already-authorized task, reuse its known numeric TaskSpace and
Page without another ownership-confirmation question when your previous normal
finish, handoff, or pause between rounds left that space available to the user.
Keep the successful handback receipt and the task identity/Page URL in task
context. This exception requires that provenance, continuing task authority,
and no subsequent user pause, control reservation or observed intervention.
Any user-owned step from the handoff must already be resolved.
Ownership metadata alone proves neither activity nor inactivity.

Before constructing a TaskSpace handle, call `listTaskSpaces()` and match its
numeric id and task identity. `taskSpace(id)` may reconcile tabs immediately
and reject a normally finished, user-owned space before you can inspect it.
For this qualified continuation, use the documented `claimTaskSpace(id)` once
before resolving Pages; use `taskSpace(id)` when already Agent-owned. If an
initial handle lookup already returned the generic user-control/ended message,
the same known handback permits that one claim; the message alone is not a new
live takeover. Do not loop claims or create a replacement space.

After normal finish, kept tabs can be unmanaged and old Page labels can be
absent. Read `task.tabs()`, match the known Page URL, and `task.adopt(tab.page)`
when it has no label. Reuse its returned label; do not assume the old `p1` still
exists or create another tab merely because a label was lost. For example,
after establishing the provenance and authority above:

```js
const space = (await listTaskSpaces()).find(s => s.id === knownSpaceId);
if (!space || space.taskId !== knownTaskId) throw new Error("Task identity changed");
const task = space.ownership === "agent"
  ? await taskSpace(knownSpaceId) : await claimTaskSpace(knownSpaceId);
const tab = (await task.tabs()).find(t => t.url === knownPageUrl);
if (!tab) throw new Error("Original Page is unavailable");
const page = tab.label ? task.page(tab.label) : await task.adopt(tab.page);
console.log(await page.snapshot());
```

Stop immediately for active user interaction, an unexpected live ownership
change, an explicit pause, or a user reservation of browser control. Do not
reclaim after a new user-control stop during Agent-owned work, or after the
one qualified claim is refused. An unresolved permission prompt,
device chooser, login or verification still belongs to its existing owner.
If this is an unrelated space, its task identity is unverified, or a current
user-control reservation remains unresolved, ask about that specific choice.

This continuation guidance grants no new operation, account, publishing,
installation, paid-execution or permission authority. Keep the upstream
one-space-per-goal, Page protection, handoff, error and finish-once rules.
"""


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == _MARKER:
            continue
        if path.is_symlink():
            content = b"link\0" + os.fsencode(os.readlink(path))
        elif path.is_file():
            content = b"file\0" + path.read_bytes()
        else:
            continue
        digest.update(relative.encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def ego_skill_mutation_paths(skills_dir: Path) -> list[Path]:
    """Only inspect the shared Ego entry for the default personal skill root."""
    root = skills_dir.expanduser().resolve()
    entry = root / "ego-browser"
    codex_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "skills"
    default_root = root == codex_root.expanduser().resolve()

    def present(path: Path) -> bool:
        return (
            path.exists()
            or path.is_symlink()
            or any(path.parent.glob(f".{path.name}.loopx-previous.*"))
        )

    if not present(entry) and default_root:
        entry = Path.home() / ".agents/skills/ego-browser"
    if default_root and entry.is_symlink():
        resolved = entry.resolve()
        if resolved.is_relative_to(Path.home().resolve()):
            try:
                marker = json.loads((resolved / _MARKER).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = None
            if isinstance(marker, dict) and marker.get("owner") == _OWNER:
                entry = resolved
    paths = [entry] if present(entry) else []
    shared = Path.home() / ".agents/skills/ego-browser"
    if default_root and paths and entry != shared and not entry.is_symlink():
        try:
            marker = json.loads((entry / _MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = None
        vendor = (Path.home() / ".local/share/ego/ego-skills").resolve()
        if (
            isinstance(marker, dict)
            and marker.get("owner") == _OWNER
            and shared.is_symlink()
            and shared.resolve() == vendor
        ):
            paths.append(shared)
    return paths


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _entry_version(entry: Path) -> str:
    """Bind this publication to the entry checked before staging, not its name."""
    digest = hashlib.sha256()
    for path in [entry, *sorted(entry.rglob("*"))]:
        metadata = path.lstat()
        digest.update(
            json.dumps(
                [
                    path.relative_to(entry).as_posix(),
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_mtime_ns,
                ]
            ).encode()
        )
    if entry.is_symlink():
        digest.update(os.fsencode(os.readlink(entry)))
    else:
        digest.update(_digest(entry).encode())
        marker = entry / _MARKER
        if marker.is_file():
            digest.update(marker.read_bytes())
    return digest.hexdigest()


def _publish(candidate: Path, entry: Path, *, expected_version: str) -> bool:
    # Keep the original outside the temporary staging directory: a failure of
    # both publish and restore must leave it available for recovery.
    backups = list(entry.parent.glob(f".{entry.name}.loopx-previous.*"))
    if backups:
        raise RuntimeError(
            f"Ego skill recovery required; inspect the preserved original at {backups[0]}"
        )
    if _entry_version(entry) != expected_version:
        return False
    backup = entry.with_name(f".{entry.name}.loopx-previous.{os.getpid()}")
    entry.replace(backup)
    if _entry_version(backup) != expected_version:
        # A user can edit without taking our publisher lock, including through
        # a file handle opened before the move. Keep that changed original.
        if entry.exists() or entry.is_symlink():
            raise RuntimeError(
                f"Ego skill changed during publish; original preserved at {backup}"
            )
        backup.replace(entry)
        return False
    try:
        candidate.replace(entry)
    except BaseException:
        if entry.exists() or entry.is_symlink():
            raise RuntimeError(
                f"Ego skill publish interrupted; original preserved at {backup}"
            )
        backup.replace(entry)
        raise
    if _entry_version(backup) != expected_version:
        raise RuntimeError(
            f"Ego skill changed during publish; original preserved at {backup}"
        )
    _remove(backup)
    return True


def reconcile_ego_browser_skill(
    *,
    skills_dir: Path,
    execute: bool = False,
    uninstall: bool = False,
) -> dict[str, Any]:
    paths = ego_skill_mutation_paths(skills_dir)
    if not paths:
        return {
            "ok": True,
            "ready": False,
            "status": "not_installed",
            "reason": "No installed Ego skill in this host scope; browser installation is unchanged.",
        }
    entry = paths[0]
    if execute:
        with exclusive_file_lock(entry.parent / ".loopx-ego-skill"):
            result = _reconcile(entry, execute=True, uninstall=uninstall)
            if not uninstall and result.get("ready") and len(paths) > 1:
                shared = paths[1]
                vendor = (Path.home() / ".local/share/ego/ego-skills").resolve()
                # A browser reinstall may restore its pristine shared link.
                # Recheck before replacing it; custom aliases are never owned.
                shared_version = _entry_version(shared)
                if shared.is_symlink() and shared.resolve() == vendor:
                    with tempfile.TemporaryDirectory(
                        prefix=".loopx-ego-alias-", dir=shared.parent
                    ) as temporary:
                        alias = Path(temporary) / "alias"
                        alias.symlink_to(entry, target_is_directory=True)
                        if not _publish(alias, shared, expected_version=shared_version):
                            return {
                                **result,
                                "ready": False,
                                "status": "preserved_unmanaged",
                                "reason": "Shared Ego entry changed during refresh; its custom target was preserved.",
                            }
                    result.update(
                        status="refreshed",
                        reason="Reconnected the upstream shared Ego link to its current managed host skill.",
                    )
                elif not shared.is_symlink() or shared.resolve() != entry.resolve():
                    result.update(
                        ready=False,
                        status="preserved_unmanaged",
                        reason="Shared Ego entry changed during refresh; its custom target was preserved.",
                    )
            return result
    result = _reconcile(entry, execute=False, uninstall=uninstall)
    if not uninstall and result.get("ready") and len(paths) > 1:
        result.update(
            ready=False,
            status="would_refresh",
            reason="Reconnect Ego's pristine shared link to the current managed host skill.",
        )
    return result


def _reconcile(entry: Path, *, execute: bool, uninstall: bool) -> dict[str, Any]:
    # Ego owns this stable link across browser versions. Never infer an App
    # directory or overwrite a custom source when this installation is absent.
    vendor_ref = Path.home() / ".local/share/ego/ego-skills"
    vendor = vendor_ref.resolve()
    result: dict[str, Any] = {"ok": True, "path": str(entry), "ready": False}
    backups = list(entry.parent.glob(f".{entry.name}.loopx-previous.*"))
    if backups:
        return {
            **result,
            "ok": False,
            "status": "recovery_required",
            "reason": f"A failed publish preserved the original at {backups[0]}; restore that entry before refreshing. No browser files were changed.",
        }
    entry_version = _entry_version(entry)
    try:
        marker = (
            json.loads((entry / _MARKER).read_text(encoding="utf-8"))
            if not entry.is_symlink()
            else None
        )
    except (OSError, ValueError):
        marker = None
    managed = isinstance(marker, dict) and marker.get("owner") == _OWNER
    pristine_link = entry.is_symlink() and entry.resolve() == vendor
    if not managed and not pristine_link:
        return {
            **result,
            "status": "preserved_unmanaged",
            "reason": "Custom or unrecognized Ego skill preserved. Keep its policy manually, or restore the upstream Ego skill link before refreshing.",
        }
    if managed and _digest(entry) != marker.get("tree_sha256"):
        return {
            **result,
            "ok": not uninstall,
            "status": "preserved_modified",
            "reason": "Ego skill changed locally after LoopX delivery; no files were overwritten. Reconcile these edits before refreshing or rolling back.",
        }
    if uninstall and not managed:
        return {
            **result,
            "status": "unmanaged",
            "reason": "No LoopX Ego policy to remove.",
        }
    try:
        upstream = (vendor / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {
            **result,
            "ok": not uninstall,
            "status": "source_unavailable",
            "reason": "Installed Ego skill source is unavailable; existing entry preserved. Restore Ego's skill installation and rerun workflow-skills --install.",
        }
    frontmatter = upstream.split("---", 2)
    named_ego = (
        len(frontmatter) == 3
        and not frontmatter[0].strip()
        and any(
            line.partition(":")[0].strip() == "name"
            and line.partition(":")[2].strip().strip("\"'") == "ego-browser"
            for line in frontmatter[1].splitlines()
        )
    )
    if (vendor / _MARKER).exists() or not named_ego:
        return {
            **result,
            "ok": False,
            "status": "invalid_source",
            "reason": "Ego source is not an upstream ego-browser skill; entry preserved.",
        }
    if entry.parent.resolve().is_relative_to(vendor):
        return {
            **result,
            "ok": False,
            "status": "invalid_source",
            "reason": "Ego source contains the delivery destination; entry preserved.",
        }
    source_digest = _digest(vendor)
    policy_digest = hashlib.sha256(_POLICY.encode()).hexdigest()
    if (
        not uninstall
        and managed
        and marker.get("source_sha256") == source_digest
        and marker.get("policy_sha256") == policy_digest
        and marker.get("loopx_version") == __version__
    ):
        return {
            **result,
            "ready": True,
            "status": "unchanged",
            "reason": "Installed Ego API and LoopX continuation guidance are current.",
        }
    if not execute:
        return {
            **result,
            "status": "would_restore" if uninstall else "would_refresh",
            "reason": "Restore the current upstream skill link."
            if uninstall
            else "Copy the installed Ego skill and apply scoped task-continuation guidance.",
        }
    with tempfile.TemporaryDirectory(
        prefix=".loopx-ego-", dir=entry.parent
    ) as temporary:
        candidate = Path(temporary) / "candidate"
        if uninstall:
            candidate.symlink_to(vendor_ref, target_is_directory=True)
        else:
            shutil.copytree(vendor, candidate)
            if _digest(vendor) != source_digest:
                raise RuntimeError(
                    "Ego source changed during skill copy; existing entry preserved. Retry after the browser upgrade finishes."
                )
            candidate.chmod(candidate.stat().st_mode | 0o700)
            (candidate / "SKILL.md").chmod(
                (candidate / "SKILL.md").stat().st_mode | 0o200
            )
            (candidate / "SKILL.md").write_text(
                upstream.rstrip() + _POLICY, encoding="utf-8"
            )
            (candidate / _MARKER).write_text(
                json.dumps(
                    {
                        "owner": _OWNER,
                        "loopx_version": __version__,
                        "source_sha256": source_digest,
                        "policy_sha256": policy_digest,
                        "tree_sha256": _digest(candidate),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        if not _publish(candidate, entry, expected_version=entry_version):
            return {
                **result,
                "ok": not uninstall,
                "status": "preserved_modified",
                "reason": "Ego skill changed during refresh; the original was preserved. Reconcile these edits before retrying.",
            }
    return {
        **result,
        "ready": not uninstall,
        "status": "restored" if uninstall else "refreshed",
        "reason": "Restored Ego's current upstream skill link."
        if uninstall
        else "Updated the local Ego skill; browser files and user-control boundaries are unchanged.",
    }
