"""Explicit SForge receiver composition; existing owners retain all authority.

This is an operator transport, not a checkpoint format or a second admission
rule. The artifact, canonical state, native session and judge each verify their own
participant before the ordinary worker can start.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import time
import tomllib

from loopx.control_plane.effect_runtime import effect_runtime_result
from benchmark.runtime.layout import _CODEX_HOME, _CONTROL, _SRC, _REGISTRY, _LOOPX_RUNTIME


_CHECKPOINT_VERIFY_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class WarmStart:
    root: Path
    sha256: str
    config: Path
    goal_id: str
    agent_id: str
    source_todo_id: str
    deadline_epoch: int
    normal_cadence: int = 6
    research_component_ids: tuple[str, ...] = ()
    fresh_replan: bool = False
    destination_config: Path | None = None
    research_sidecar: bool = False
    native_child_limit: int | None = None

    def _selected_research(self, metadata: dict):
        from .research_artifacts import selected_research_components
        return selected_research_components(metadata, self.research_component_ids, managed=self.research_sidecar)

    def verify(self, workspace: str) -> dict:
        from loopx.hosts.checkpoint_retention import checkpoint_bank_use
        with checkpoint_bank_use(self.root):
            return self._verify(workspace)

    def _verify(self, workspace: str) -> dict:
        if type(self.research_sidecar) is not bool:
            raise ValueError("research_sidecar requires an explicit boolean")
        if type(self.fresh_replan) is not bool:
            raise ValueError("fresh_replan requires an explicit boolean context treatment")
        if (type(self.normal_cadence) is not int or not 1 <= self.normal_cadence <= 6
                or type(self.deadline_epoch) is not int or not 0 < self.deadline_epoch < 2 ** 53
                or self.deadline_epoch <= time.time()
                or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", v)
                       for v in (self.goal_id, self.agent_id, self.source_todo_id))):
            raise ValueError("Warm start needs explicit source identities and its original open deadline")
        paths = (self.root, self.config)
        if self.destination_config is not None:
            paths += (self.destination_config,)
        if any(not path.is_absolute() or path.resolve(strict=True) != path for path in paths):
            raise ValueError("Warm start requires physical absolute participant paths")
        # Hashing a complete checkpoint can exceed the ordinary RPC budget.
        # This read allowance never extends the original execution deadline.
        remaining = self.deadline_epoch - time.time()
        if remaining <= 0:
            raise ValueError("Original warm start deadline expired before checkpoint verification")
        effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "verify", "root": str(self.root), "expected_sha256": self.sha256},
            timeout=min(_CHECKPOINT_VERIFY_TIMEOUT_SECONDS, remaining), large_local_snapshot=True)
        metadata = json.loads((self.root / "checkpoint.json").read_bytes())["point_metadata"]
        native, source = metadata["native_session"], metadata["loopx_state"]
        same = source["goal_id"] == self.goal_id and source["agent_id"] == self.agent_id
        if (native["guest_workspace"] != workspace or native["guest_home"] != _CODEX_HOME
                or not same):
            raise ValueError("Warm start paths, configuration or destination lineage differ")
        _loopx_components(self.root, metadata, Path(workspace))
        from loopx.hosts.codex_checkpoint import checkpoint_destination_config
        from .codex import Execution

        configuration, _ = checkpoint_destination_config(self.config, native["config_sha256"], self.destination_config)
        agents = tomllib.loads(configuration.decode("utf-8")).get("agents", {})
        if not isinstance(agents, dict):
            raise ValueError("Sealed checkpoint agents configuration must be a TOML table")
        retained_limit = Execution(mode="plain",
            native_child_limit=agents.get("max_concurrent_threads_per_session")).native_child_limit
        requested_limit = Execution(mode="plain", native_child_limit=self.native_child_limit).native_child_limit
        if retained_limit != requested_limit:
            raise ValueError("Requested native child limit differs from the sealed checkpoint")
        _workspace_component(self.root, metadata, Path(workspace))
        _research_components(self.root, metadata, Path(workspace), self._selected_research(metadata))
        context = metadata["research_context"]
        context_path = self.root / "components" / context["component_id"] / context["member_path"]
        if not context_path.resolve(strict=True).is_relative_to(self.root / "components"):
            raise ValueError("Retained research context is outside the checkpoint")
        raw = context_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != context["sha256"]:
            raise ValueError("Retained research context digest differs")
        if not any(row["todo_id"] == self.source_todo_id for row in json.loads(raw)["snapshot"]["todos"]):
            raise ValueError("Source Todo is absent from the retained research context")
        if self.deadline_epoch <= time.time():
            raise ValueError("Original warm start deadline expired during checkpoint verification")
        return metadata

    async def stage(self, environment, workspace: str, *,
                    effective_config: bytes | None = None,
                    provider_base_url: str | None = None) -> list[str]:
        from loopx.hosts.checkpoint_retention import checkpoint_bank_use
        with checkpoint_bank_use(self.root):
            return await self._stage(environment, workspace,
                effective_config=effective_config, provider_base_url=provider_base_url)

    async def _stage(self, environment, workspace: str, *,
                     effective_config: bytes | None = None,
                     provider_base_url: str | None = None) -> list[str]:
        metadata = self._verify(workspace)
        research = _research_components(self.root, metadata, Path(workspace), self._selected_research(metadata))
        requested = self.destination_config or self.config
        transport_config = None
        if effective_config is not None:
            from loopx.hosts.codex_checkpoint import checkpoint_destination_config

            # The runner renders its actual launch through the ordinary worker's
            # settings owner. Never change the operator's model/effort selection
            # merely because a destination was rendered successfully.
            before = tomllib.loads(requested.read_text(encoding="utf-8"))
            after = tomllib.loads(effective_config.decode("utf-8"))
            if any(before.get(key) != after.get(key) for key in ("model", "model_reasoning_effort")):
                raise ValueError("Checkpoint launch differs from its declared model and reasoning effort")
            with tempfile.TemporaryDirectory(prefix="checkpoint-route-") as folder:
                target = Path(folder).resolve() / "config.toml"
                target.write_bytes(effective_config)
                checkpoint_destination_config(self.config,
                    metadata["native_session"]["config_sha256"], target,
                    provider_base_url=provider_base_url)
            if effective_config != requested.read_bytes():
                transport_config = effective_config
        elif provider_base_url is not None and effective_config is None:
            raise ValueError("Checkpoint Host route requires its rendered destination settings")
        # Install tools first without creating the native home. Stage inert
        # participants outside the workspace so restoring its bytes cannot
        # replace the active controller, registry or installed executable.
        unit, config = f"{_CONTROL}/restored-point", f"{_CONTROL}/restored-config.toml"
        await environment.upload_dir(self.root, unit)
        await environment.upload_file(self.config, config)
        payload = dict(root=unit, sha256=self.sha256, config=config,
                       goal_id=self.goal_id, agent_id=self.agent_id,
                       source_todo_id=self.source_todo_id, deadline_epoch=self.deadline_epoch,
                       normal_cadence=self.normal_cadence)
        protected_files = [config, f"{_CONTROL}/warm-start.json"]
        if transport_config is not None:
            target = f"{_CONTROL}/restored-model-config.toml"
            with tempfile.TemporaryDirectory(prefix="checkpoint-route-") as folder:
                path = Path(folder) / "config.toml"
                path.write_bytes(transport_config)
                await environment.upload_file(path, target)
            payload["destination_config"] = target
            payload["provider_base_url"] = provider_base_url
            protected_files.append(target)
        elif self.destination_config is not None:
            target = f"{_CONTROL}/restored-model-config.toml"
            await environment.upload_file(self.destination_config, target)
            payload["destination_config"] = target
            protected_files.append(target)
        if research:
            payload["research_component_ids"] = [key for key, _, _ in research]
        if self.fresh_replan:
            payload["fresh_replan"] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_text(json.dumps(payload))
            await environment.upload_file(path, f"{_CONTROL}/warm-start.json")
        protected = shlex.join(protected_files)
        result = await environment.exec(
            # Modes are part of the sealed digest. Change transport ownership,
            # never widen modes (native stores commonly contain 0600 members).
            f"chown -R -P agent:agent {shlex.quote(unit)} && "
            f"chown agent:agent {protected} && chmod 0400 {protected}", user="root")
        if result.return_code:
            raise RuntimeError("Could not stage the verified checkpoint participants")
        # The root-only materializer preserves sealed modes. Its receiver must
        # transfer these selected data copies to the worker before continuation;
        # making private directories world-readable would change the artifact.
        state = _loopx_components(self.root, metadata, Path(workspace))
        return [str(target) for _, _, target in [*research, *state]]


# Provider-owned data locations; selecting a component never grants permission
# to restore a home, executable installation, registry or runtime authority.
_RESEARCH_ROOTS = (Path("/tmp"), Path("/home/workspace"))


def _workspace_component(unit: Path, metadata: dict, workspace: Path) -> Path:
    """Locate the task subtree inside one verified captured file root.

    Capture may seal a parent directory. Only the native workspace is restored;
    siblings remain inert unless independently selected as research data.
    Both preflight and materialization use this transport resolution.
    """
    matches = []
    for entry in metadata["worker_file_snapshot"]["components"]:
        root = Path(entry["source_path"])
        if workspace.is_relative_to(root):
            if (not root.is_absolute() or ".." in root.parts or str(root) != entry["source_path"]
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", entry["id"])):
                raise ValueError("Captured workspace root must have a canonical path and component ID")
            matches.append(unit / "components" / entry["id"] / workspace.relative_to(root))
    if len(matches) != 1:
        raise ValueError("Warm start requires one complete captured workspace")
    source = matches[0]
    if not source.is_dir() or source.resolve() != source:
        raise ValueError("Captured workspace must be a physical directory inside its component")
    return source


def _research_components(unit: Path, metadata: dict, workspace: Path, selected) -> list[tuple[str, Path, Path]]:
    """Bind explicit receiver selection to sealed worker files, not prose refs.

    Capture already accepts multiple components. This transport never discovers
    dependencies or assumes that all captured files are research data.
    """
    if (not isinstance(selected, (tuple, list)) or
            any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected)):
        raise ValueError("Research component selection must contain distinct captured IDs")
    result = []
    files = metadata["worker_file_snapshot"]["components"]
    for key in selected:
        matches = [entry for entry in files if entry["id"] == key]
        if len(matches) != 1 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", key):
            raise ValueError("Selected research component is absent from the captured worker files")
        target = Path(matches[0]["source_path"])
        if (not target.is_absolute() or ".." in target.parts or str(target) != matches[0]["source_path"] or
                not any(target != root and target.is_relative_to(root) for root in _RESEARCH_ROOTS) or
                target.is_relative_to(workspace) or workspace.is_relative_to(target) or
                target.is_relative_to(unit) or unit.is_relative_to(target) or
                target == workspace.with_name(workspace.name + ".before-checkpoint")):
            raise ValueError("Research component must be separate data below a provider research root")
        if any(target.is_relative_to(old) or old.is_relative_to(target) for _, _, old in result):
            raise ValueError("Selected research components overlap")
        source = unit / "components" / key
        if not source.exists() or source.is_symlink():
            raise ValueError("Selected research component has no verified artifact bytes")
        result.append((key, source, target))
    return result


def _check_research_destinations(entries):
    # Check every destination before touching the workspace. Receiver admission
    # owns this fresh, quiescent environment; concurrent writers are unsupported.
    for _, _, target in entries:
        if target.exists() or target.is_symlink():
            raise ValueError("Research destination already exists; preserve it and use a fresh receiver")
        if not target.parent.is_dir() or target.resolve() != target:
            raise ValueError("Research destination requires an existing physical parent")


def _loopx_components(unit: Path, metadata: dict, workspace: Path) -> list[tuple[str, Path, Path]]:
    """Resolve the backup owner's inventory for a fixed-path isolated replacement.

    This copies complete provider files, not a Todo projection or a rewritten
    journal. The ordinary registry, canonical snapshot and session owners still
    validate the restored values before a worker can obtain current admission.
    """
    state = metadata["loopx_state"]
    inventory = state["source_inventory"]
    result = []
    for key, target in (("runtime_root", Path(_LOOPX_RUNTIME)),
                        ("configuration_source_registry", Path(_REGISTRY))):
        entries = [entry for entry in inventory if entry["key"] == key]
        if len(entries) != 1 or entries[0]["source_path"] != str(target):
            raise ValueError("Full LoopX restore requires its original isolated runtime and registry paths")
        entry = entries[0]
        component = entry["component_id"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", component):
            raise ValueError("LoopX state component ID is invalid")
        source = unit / "components" / component
        if key == "configuration_source_registry":
            if state["registry_component_id"] != component:
                raise ValueError("LoopX registry inventory differs from its sealed registry participant")
            source = source / state["registry_member_path"]
        if source.resolve(strict=True) != source or not source.is_relative_to(unit / "components"):
            raise ValueError("LoopX state member is not a physical sealed participant")
        if (key == "runtime_root" and not source.is_dir()) or (key != "runtime_root" and not source.is_file()):
            raise ValueError("LoopX state participant kind differs")
        result.append((component, source, target))
    if any(entry["key"] not in {"runtime_root", "configuration_source_registry"}
           and not Path(entry["source_path"]).is_relative_to(workspace) for entry in inventory):
        raise ValueError("LoopX state inventory contains an unsupported external route")
    raw = result[1][1].read_bytes()
    if hashlib.sha256(raw).hexdigest() != state["registry_sha256"]:
        raise ValueError("LoopX source registry digest differs")
    registry = json.loads(raw)
    goals = registry.get("goals", [])
    if (registry.get("common_runtime_root") != _LOOPX_RUNTIME or len(goals) != 1
            or goals[0].get("id") != state["goal_id"] or goals[0].get("repo") != str(workspace)
            or state["agent_id"] not in goals[0].get("coordination", {}).get("registered_agents", [])):
        raise ValueError("Full LoopX restore requires the captured single-Goal registry and Agent")
    return result


def _check_loopx_destinations(entries):
    for _, source, target in entries:
        if target.resolve() != target or not target.parent.is_dir() or target.is_symlink():
            raise ValueError("LoopX restore destination is redirected or unavailable")
        # The ordinary installer creates the empty runtime directory. Never
        # replace a configured receiver, including an earlier partial restore.
        if target.exists() and (not source.is_dir() or not target.is_dir() or any(target.iterdir())):
            raise ValueError("LoopX receiver already has state; preserve the original attempt")


def _phase_read_timeout(declaration: dict, limit: float) -> float:
    deadline = declaration["deadline_epoch"]
    if type(deadline) is not int or not 0 < deadline < 2 ** 53:
        raise ValueError("Warm session requires its original integer phase deadline")
    remaining = deadline - time.time()
    if remaining <= 0:
        raise ValueError("Original warm start allowance expired before checkpoint admission")
    return min(limit, remaining)


def restore_workspace(declaration: dict, workspace: Path):
    """Copy task/research files only; this transport does not configure a Goal."""
    unit = Path(declaration["root"])
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": declaration["sha256"]},
        timeout=_phase_read_timeout(declaration, _CHECKPOINT_VERIFY_TIMEOUT_SECONDS), large_local_snapshot=True)
    _phase_read_timeout(declaration, _CHECKPOINT_VERIFY_TIMEOUT_SECONDS)
    manifest = json.loads((unit / "checkpoint.json").read_bytes())
    metadata = manifest["point_metadata"]
    research = _research_components(unit, metadata, workspace, declaration.get("research_component_ids", ()))
    _check_research_destinations(research)
    if str(workspace) != metadata["native_session"]["guest_workspace"]:
        raise ValueError("Captured and destination workspace paths differ")
    if (workspace.resolve() != workspace or workspace == Path("/")
            or unit.is_relative_to(workspace) or Path(_SRC).is_relative_to(workspace)
            or Path(_CODEX_HOME).is_relative_to(workspace)):
        raise ValueError("Checkpoint workspace overlaps the receiver's isolated control paths")
    source = _workspace_component(unit, metadata, workspace)
    # Reserve the original task-image directory rather than merging stale image
    # files into the captured research state. A partial attempt fails closed.
    original = workspace.with_name(workspace.name + ".before-checkpoint")
    if original.exists() or original.is_symlink():
        raise ValueError("Receiver workspace was already prepared; inspect the original attempt")
    # Overlay-backed task-image directories may reject rename with EXDEV.
    # shutil.move preserves a complete backup before removing that fresh image
    # tree; a partial attempt still cannot be reused.
    shutil.move(str(workspace), str(original))
    shutil.copytree(source, workspace, symlinks=True)
    for _, captured, target in research:
        if captured.is_dir():
            shutil.copytree(captured, target)
        else:
            # Exclusive creation refuses a late collision; leave partial output
            # for diagnosis. The workspace backup fences the entire attempt.
            with target.open("xb") as output, captured.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
            shutil.copystat(captured, target)
    if research:
        copied = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe",
            "components": [{"id": key, "source_path": str(target)} for key, _, target in research]},
            timeout=_phase_read_timeout(declaration, _CHECKPOINT_VERIFY_TIMEOUT_SECONDS), large_local_snapshot=True)
        if any(copied["source_witness"][key] != manifest["artifact_witness"][key] for key, _, _ in research):
            raise ValueError("Restored research files differ from the sealed artifacts; preserve this failed receiver")
    _phase_read_timeout(declaration, _CHECKPOINT_VERIFY_TIMEOUT_SECONDS)


def restore_worker(declaration: dict, workspace: Path):
    """Replace one stopped isolated worker with its complete retained state."""
    unit = Path(declaration["root"])
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": declaration["sha256"]},
        timeout=_phase_read_timeout(declaration, _CHECKPOINT_VERIFY_TIMEOUT_SECONDS), large_local_snapshot=True)
    manifest = json.loads((unit / "checkpoint.json").read_bytes())
    state = _loopx_components(unit, manifest["point_metadata"], workspace)
    _check_loopx_destinations(state)
    restore_workspace(declaration, workspace)
    for _, captured, target in state:
        if captured.is_dir():
            if target.exists():
                target.rmdir()  # Only the verified empty installer directory.
            shutil.copytree(captured, target)
        else:
            with target.open("xb") as output, captured.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
            shutil.copystat(captured, target)
    if state:
        copied = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe",
            "components": [{"id": key, "source_path": str(target)} for key, _, target in state]},
            timeout=_phase_read_timeout(declaration, _CHECKPOINT_VERIFY_TIMEOUT_SECONDS), large_local_snapshot=True)
        for key, captured, _ in state:
            member = captured.relative_to(unit / "components" / key).as_posix()
            expected = manifest["artifact_witness"][key]
            if member != ".":
                expected = [{**entry, "path": entry["path"][len(member):].lstrip("/")}
                    for entry in expected if entry["path"] == member or entry["path"].startswith(member + "/")]
            if copied["source_witness"][key] != expected:
                raise ValueError("Restored LoopX files differ from the sealed state; preserve this failed receiver")
    _phase_read_timeout(declaration, _CHECKPOINT_VERIFY_TIMEOUT_SECONDS)


def _prepare_native_skills(home: Path, installed: Path) -> None:
    """Leave skill installation to the ordinary worker after native fork.

    App-server initializes its own bundled skills while opening the restored
    session. Skills are not part of the imported native store. Preserve that
    generated directory outside the discovery path; never merge it into the
    installed skills or replace a link. The ordinary worker still owns the
    installed-skill identity check. Link immediately so subsequent native probes
    cannot recreate the directory. Call only after verified native admission.
    """
    if not installed.is_dir():
        raise ValueError("Warm receiver requires formally installed skills")
    skills = home / "skills"
    if skills.is_dir() and not skills.is_symlink():
        backup = home / ".loopx-native-skills-before-install"
        if backup.exists() or backup.is_symlink():
            raise ValueError("Native skills backup already exists; inspect the original receiver attempt")
        skills.rename(backup)
    if not skills.exists() and not skills.is_symlink():
        skills.symlink_to(installed.resolve(), target_is_directory=True)


def admit_warm_session(env: dict[str, str], declaration: dict) -> dict:
    """Recheck the full native participant within its original remaining phase.

    Mature histories need the same bounded read allowance at initial continuation
    and fresh-context revalidation. This retains source/cursor checks; it neither
    trusts a cached history digest nor grants another execution window.
    """
    from loopx.hosts.codex_checkpoint import admit_native_checkpoint_session

    timeout = _phase_read_timeout(declaration, 90)
    result = admit_native_checkpoint_session(env, Path(declaration["root"]), declaration["sha256"],
        Path(declaration["config"]), sandbox=env["LOOPX_CODEX_SANDBOX"], timeout=timeout,
        restore_loopx_state=True,
        **({"destination_config": Path(declaration["destination_config"])}
           if declaration.get("destination_config") is not None else {}),
        **({"provider_base_url": declaration["provider_base_url"]}
           if declaration.get("provider_base_url") is not None else {}))
    _phase_read_timeout(declaration, 90)
    return result


def prepare_context(env: dict[str, str], declaration: dict) -> dict:
    """Verify native continuation of the complete retained Goal; seed no work."""
    if any(env[key] != declaration[field] for key, field in
           (("LOOPX_GOAL_ID", "goal_id"), ("LOOPX_AGENT_ID", "agent_id"))):
        raise ValueError("Warm context requires its declared source lineage")
    _phase_read_timeout(declaration, 90)
    native = admit_warm_session(env, declaration)
    _prepare_native_skills(Path(env["CODEX_HOME"]), Path(env["LOOPX_SHARED_SKILLS"]))
    return {"loopx_state_restored": True, "session": native,
            "goal_id": declaration["goal_id"], "agent_id": declaration["agent_id"],
            "model_calls": 0, "execution_authority_granted": False,
            "research_continuation_qualified": False}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("workspace", "context"))
    parser.add_argument("--declaration", required=True, type=Path)
    args = parser.parse_args()
    declaration = json.loads(args.declaration.read_bytes())
    env = dict(os.environ)
    if args.action == "workspace":
        restore_worker(declaration, Path(env["LOOPX_PROJECT"]))
    else:
        print(json.dumps(prepare_context(env, declaration)))


if __name__ == "__main__":
    main()
