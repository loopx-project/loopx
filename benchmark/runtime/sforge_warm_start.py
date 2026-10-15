"""Explicit SForge receiver composition; existing owners retain all authority.

This is an operator transport, not a checkpoint format or a second admission
rule. The artifact, native session, Todo handoff and judge each verify their own
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

from loopx.control_plane.effect_runtime import effect_runtime_result
from benchmark.runtime.layout import _CODEX_HOME, _CONTROL, _SRC


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

    def verify(self, workspace: str) -> dict:
        if (type(self.normal_cadence) is not int or not 1 <= self.normal_cadence <= 6
                or type(self.deadline_epoch) is not int or not 0 < self.deadline_epoch < 2 ** 53
                or self.deadline_epoch <= time.time()
                or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", v)
                       for v in (self.goal_id, self.agent_id, self.source_todo_id))):
            raise ValueError("Warm start needs fresh explicit identities and its original open deadline")
        if any(not path.is_absolute() or path.resolve(strict=True) != path
               for path in (self.root, self.config)):
            raise ValueError("Warm start requires physical absolute participant paths")
        # Hashing a complete checkpoint can exceed the ordinary RPC budget.
        # This read allowance never extends the original execution deadline.
        remaining = self.deadline_epoch - time.time()
        if remaining <= 0:
            raise ValueError("Original warm start deadline expired before checkpoint verification")
        effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "verify", "root": str(self.root), "expected_sha256": self.sha256},
            timeout=min(_CHECKPOINT_VERIFY_TIMEOUT_SECONDS, remaining))
        metadata = json.loads((self.root / "checkpoint.json").read_bytes())["point_metadata"]
        native, source = metadata["native_session"], metadata["loopx_state"]
        if (native["guest_workspace"] != workspace or native["guest_home"] != _CODEX_HOME
                or source["goal_id"] == self.goal_id or source["agent_id"] == self.agent_id
                or hashlib.sha256(self.config.read_bytes()).hexdigest() != native["config_sha256"]):
            raise ValueError("Warm start paths, configuration or destination lineage differ")
        roots = [c for c in metadata["worker_file_snapshot"]["components"]
                 if c["source_path"] == workspace]
        if len(roots) != 1 or not (self.root / "components" / roots[0]["id"]).is_dir():
            raise ValueError("Warm start requires one complete captured workspace")
        _research_components(self.root, metadata, Path(workspace), self.research_component_ids)
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

    async def stage(self, environment, workspace: str):
        self.verify(workspace)
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
        if self.research_component_ids:
            payload["research_component_ids"] = list(self.research_component_ids)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_text(json.dumps(payload))
            await environment.upload_file(path, f"{_CONTROL}/warm-start.json")
        result = await environment.exec(
            # Modes are part of the sealed digest. Change transport ownership,
            # never widen modes (native stores commonly contain 0600 members).
            f"chown -R -P agent:agent {shlex.quote(unit)} && "
            f"chown agent:agent {config} {_CONTROL}/warm-start.json && "
            f"chmod 0400 {config} {_CONTROL}/warm-start.json", user="root")
        if result.return_code:
            raise RuntimeError("Could not stage the verified checkpoint participants")


# Provider-owned data locations; selecting a component never grants permission
# to restore a home, executable installation, registry or runtime authority.
_RESEARCH_ROOTS = (Path("/tmp"), Path("/home/workspace"))


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


def restore_workspace(declaration: dict, workspace: Path):
    """Materialize one isolated receiver workspace; never import its registry."""
    unit = Path(declaration["root"])
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": declaration["sha256"]})
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
    roots = [c for c in metadata["worker_file_snapshot"]["components"]
             if c["source_path"] == str(workspace)]
    if len(roots) != 1:
        raise ValueError("Checkpoint must declare exactly one full workspace")
    source = unit / "components" / roots[0]["id"]
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
            "components": [{"id": key, "source_path": str(target)} for key, _, target in research]})
        if any(copied["source_witness"][key] != manifest["artifact_witness"][key] for key, _, _ in research):
            raise ValueError("Restored research files differ from the sealed artifacts; preserve this failed receiver")


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


def prepare_context(env: dict[str, str], declaration: dict) -> dict:
    """Join actual session + research owners on one freshly bootstrapped Goal."""
    from benchmark.runtime.codex import Execution
    from loopx.hosts.codex_checkpoint import admit_native_checkpoint_session
    from loopx.hosts.research_checkpoint import adopt_todo_context
    from benchmark.runtime.worker import loopx_command
    import subprocess

    unit, digest = Path(declaration["root"]), declaration["sha256"]
    if any(env[key] != declaration[field] for key, field in
           (("LOOPX_GOAL_ID", "goal_id"), ("LOOPX_AGENT_ID", "agent_id"))):
        raise ValueError("Warm context requires its declared destination lineage")
    if declaration["deadline_epoch"] <= time.time():
        raise ValueError("Original warm start allowance expired during installation")
    execution = Execution(context="resume", sandbox=env["LOOPX_CODEX_SANDBOX"])
    native = admit_native_checkpoint_session(env, unit, digest, Path(declaration["config"]),
                                            sandbox=execution.sandbox, timeout=30)
    _prepare_native_skills(Path(env["CODEX_HOME"]), Path(env["LOOPX_SHARED_SKILLS"]))
    def cli(*args):
        result = subprocess.run([*loopx_command(env), *args], env=env,
            cwd=env["LOOPX_PROJECT"], text=True, capture_output=True, timeout=60, check=True)
        value = json.loads(result.stdout)
        if value.get("ok") is not True:
            raise ValueError("Checkpoint destination operation failed: " + args[0])
        return value
    goal, agent = declaration["goal_id"], declaration["agent_id"]
    # Preparation is actual current work, not imported completion. The normal
    # model/guard must inspect the retained evidence and settle this Todo.
    todo = cli("todo", "add", "--goal-id", goal, "--role", "agent", "--claimed-by", agent,
        "--operation-id", "checkpoint-preparation", "--required-capability", "shell",
        "--task-class", "advancement_task", "--text",
        "Read the full retained checkpoint research and pending decision, verify the current code, "
        "incumbent and remaining native opportunities. Do not modify candidates or submit during "
        "this preparation. Settle actual findings under current authority, then complete the first "
        "current replan before choosing a complete research experiment.")["todo_id"]
    key = "checkpoint-preparation"
    lease = cli("task-lease", "acquire", "--goal-id", goal, "--todo-id", todo,
                "--owner", agent, "--idempotency-key", key)["lease"]
    revision = cli("todo", "list", "--goal-id", goal, "--todo-id", todo)["authority_read"]["provider_revision"]
    context = adopt_todo_context(env, unit, digest,
        source_todo_id=declaration["source_todo_id"], todo_id=todo,
        operation_id="checkpoint-context", expected_revision=revision,
        lease_key=key, lease_version=lease["version"])
    # The first work guard owns the next execution lease. Context adoption is
    # finished before this lease is released; no historical lease is imported.
    cli("task-lease", "release", "--goal-id", goal, "--todo-id", todo,
        "--owner", agent, "--idempotency-key", key, "--expected-version", str(lease["version"]))
    cli("configure-goal", "--goal-id", goal, "--execution-replan-after-turns", "1", "--execute")
    return {"session": native, "context": context, "preparation_todo_id": todo,
            "model_calls": 0, "research_continuation_qualified": False}



def finish_preparation(env: dict[str, str]) -> dict:
    """Restore the declared cadence only after the existing joint replan gate."""
    from benchmark.runtime.checkpoint_continuation import restored_capture_gate
    from benchmark.runtime.worker import loopx_command
    import subprocess
    declaration = json.loads(Path(env["LOOPX_WARM_START"]).read_bytes())
    if any(env[key] != declaration[field] for key, field in
           (("LOOPX_GOAL_ID", "goal_id"), ("LOOPX_AGENT_ID", "agent_id"))):
        raise ValueError("Warm preparation destination lineage differs")
    basis = {"checkpoint_sha256": declaration["sha256"],
             "goal_id": declaration["goal_id"], "agent_id": declaration["agent_id"],
             "normal_cadence": declaration["normal_cadence"]}
    receipt = Path(env["LOOPX_WARM_START"]).with_name("warm-preparation-complete.json")
    if receipt.exists():
        value = json.loads(receipt.read_bytes())
        if any(value.get(key) != expected for key, expected in basis.items()):
            raise ValueError("Warm preparation receipt refers to another checkpoint")
        return value
    gate = restored_capture_gate(env)
    if not gate.get("capture_allowed") or gate.get("checkpoint_sha256") != declaration["sha256"]:
        return {"preparation_complete": False}
    command = [*loopx_command(env), "configure-goal", "--goal-id", env["LOOPX_GOAL_ID"],
               "--execution-replan-after-turns", str(declaration["normal_cadence"]), "--execute"]
    raw = subprocess.check_output(command, env=env, cwd=env["LOOPX_PROJECT"], timeout=60, text=True)
    value = json.loads(raw)
    if value.get("ok") is not True:
        raise ValueError("Could not restore the declared normal cadence after checkpoint preparation")
    result = {**basis, "preparation_complete": True, "review_turn_id": gate["review_turn_id"]}
    with receipt.open("x") as stream:
        json.dump(result, stream)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("workspace", "context"))
    parser.add_argument("--declaration", required=True, type=Path)
    args = parser.parse_args()
    declaration = json.loads(args.declaration.read_bytes())
    env = dict(os.environ)
    if args.action == "workspace":
        restore_workspace(declaration, Path(env["LOOPX_PROJECT"]))
    else:
        print(json.dumps(prepare_context(env, declaration)))


if __name__ == "__main__":
    main()
