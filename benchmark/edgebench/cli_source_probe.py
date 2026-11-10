"""Read a paused source in an isolated, credential-free observer namespace.

The source itself executes no probe while paused. Existing native and settlement
owners read their copied paths; a passive native reader is not a model request.
This adapter never creates a checkpoint point, registration, or work allowance.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import tarfile
import tempfile

from .checkpoint_worker import _guest_path


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def _archive_response(container, path, *, metadata_only=False):
    """Own the actual HTTP response, including before the first body read.

    Docker's GET holds its container archive lock until EOF or response close.
    Closing an unstarted SDK generator does neither. HEAD is the native stat
    route; GET here retains the caller's socket timeout and always closes.
    """
    from docker.utils import decode_json_header

    api = container.client.api
    url = api._url("/containers/{0}/archive", container.id)
    response = (api.head(url, params={"path": path}, timeout=api.timeout, allow_redirects=False)
                if metadata_only else api._get(url, params={"path": path}, stream=True,
                    headers={"Accept-Encoding": "identity"}, allow_redirects=False))
    try:
        api._raise_for_status(response)
        stat = decode_json_header(response.headers["x-docker-container-path-stat"])
        yield (() if metadata_only else response.iter_content(2 * 1024 * 1024), stat)
    finally:
        response.close()


def _path_stat(container, path):
    with _archive_response(container, path, metadata_only=True) as (_, stat):
        return stat


def archive_witness(container, components: list[dict]) -> str:
    """Hash declared bytes and link identities without following task symlinks."""
    result = []
    for component in components:
        remote = _guest_path(component["source_path"])
        members = []
        with _archive_response(container, str(remote)) as (chunks, stat), tempfile.TemporaryFile() as spool:
            for block in chunks:
                spool.write(block)
            spool.seek(0)
            with tarfile.open(fileobj=spool) as archive:
                names = set()
                for entry in archive:
                    name = PurePosixPath(entry.name)
                    if (name.is_absolute() or ".." in name.parts or not name.parts or
                            name.parts[0] != remote.name or str(name) in names or
                            not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk())):
                        raise ValueError("Source observation archive has invalid members")
                    names.add(str(name))
                    item = {"path": str(name), "mode": entry.mode, "kind": entry.type.decode("ascii")}
                    if entry.isfile():
                        item.update(bytes=entry.size, sha256=hashlib.file_digest(archive.extractfile(entry), "sha256").hexdigest())
                    elif entry.issym() or entry.islnk():
                        item["target"] = entry.linkname  # Data only, never resolve or extract it on the controller.
                    members.append(item)
        result.append({"id": component["id"], "source_path": str(remote),
                       "root_link": stat.get("linkTarget", ""), "members": sorted(members, key=lambda m: m["path"])})
    return _canonical(result)


def _copy(container, observer, remote: str, immutable_roots=()) -> None:
    path = _guest_path(remote)
    if observer.exec_run(["mkdir", "-p", str(path.parent)], user="root").exit_code:
        raise RuntimeError("Observer parent preparation failed")
    with _archive_response(container, str(path)) as (chunks, _), tempfile.TemporaryFile() as spool:
        for block in chunks:
            spool.write(block)
        spool.seek(0)
        with tarfile.open(fileobj=spool) as archive:
            names = set()
            for entry in archive:
                name = PurePosixPath(entry.name)
                if (name.is_absolute() or ".." in name.parts or not name.parts or
                        name.parts[0] != path.name or str(name) in names or
                        not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk())):
                    raise ValueError("Observer archive contains an invalid member")
                names.add(str(name))
                if entry.issym() or entry.islnk():
                    absolute = posixpath.normpath(posixpath.join(str(path.parent / name.parent), entry.linkname))
                    if (not immutable_roots or not any(absolute == root or absolute.startswith(root + "/")
                                                     for root in immutable_roots)):
                        raise ValueError("Observer link leaves its declared immutable source roots")
        spool.seek(0)
        if not observer.put_archive(str(path.parent), spool):
            raise RuntimeError("Observer source transport was not confirmed")


def _file_bytes(container, path):
    with _archive_response(container, path) as (chunks, _), tempfile.TemporaryFile() as spool:
        for block in chunks:
            spool.write(block)
        spool.seek(0)
        with tarfile.open(fileobj=spool) as archive:
            entries = archive.getmembers()
            if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 2 * 1024 * 1024:
                raise ValueError("Source observation requires one bounded physical JSON file")
            return archive.extractfile(entries[0]).read()


def _json_file(container, path):
    return json.loads(_file_bytes(container, path))


def source_goal_control(container, plan, registry_path):
    """Existing current Goal activation, never a cached profile's on flag.

    The operation caller must also compose its ordinary resource/phase/owner
    Stop observation. This read neither grants work nor overrides that control.
    """
    from loopx.control_plane.projects.registry_codec import decode_registry_snapshot
    from loopx.control_plane.goals.activation import goal_is_stopped

    document = decode_registry_snapshot(Path(registry_path), _file_bytes(container, registry_path))
    matches = [g for g in document.get("goals", []) if g.get("id") == plan["goal_id"]]
    if len(matches) != 1:
        return "unknown"
    return "stop" if goal_is_stopped(matches[0]) else "continue"


def process_observation(container, allowed_command_sha256: list[str]) -> list[int]:
    """Only exact controller-declared idle command identities are accepted.

    Do not infer model, tool, or idle roles from process-name substrings. The
    native owner separately verifies a completed, stable source conversation.
    """
    table = container.top(ps_args="-eo pid,args")
    titles = table["Titles"]
    pid_index = titles.index("PID")
    commands = [i for i, value in enumerate(titles) if value in {"COMMAND", "CMD", "ARGS"}]
    if len(commands) != 1:
        raise ValueError("Source process observation is unavailable")
    unknown = []
    for row in table["Processes"]:
        digest = hashlib.sha256(row[commands[0]].encode()).hexdigest()
        if digest not in allowed_command_sha256:
            unknown.append(int(row[pid_index]))
    return sorted(unknown)


class PausedSourceProbe:
    """Concrete observer for the existing heartbeat CLI source boundary.

    All paths and idle command identities come from the original owned runtime.
    Immutable roots supply its Python, Node, native executable and installed
    kernel; they must not include a credential home. Native store participants
    are explicit, matching the existing checkpoint reader's closed allowlist.
    """

    def __init__(self, client, *, declaration: dict, components: list[dict],
                 immutable_roots: list[str], allowed_idle_commands: list[str],
                 user: str, observer_memory: str = "512m"):
        self.client, self.declaration = client, declaration
        self.components, self.immutable_roots = components, immutable_roots
        self.allowed = allowed_idle_commands
        self.user, self.observer_memory = user, observer_memory
        for row in components:
            _guest_path(row["source_path"])
        for path in immutable_roots:
            _guest_path(path)
        home = PurePosixPath(declaration["native_home"])
        # Copy only the native owner's declared stores/config/hooks, never auth
        # or the entire credential home, even if the caller supplied that root.
        paths = [PurePosixPath(r["source_path"]) for r in components] + list(map(PurePosixPath, immutable_roots))
        if any(p == home or p in home.parents for p in paths):
            raise ValueError("Observer must not copy the native credential home")
        for path in paths:
            if home not in path.parents:
                continue
            name = path.relative_to(home).parts[0]
            if name not in {"sessions", "archived_sessions", "session_index.jsonl", "config.toml", "hooks.json", "loopx-context"} and not re.fullmatch(
                    r"(?:state|thread_history)_[0-9]+\.sqlite(?:-wal|-journal)?", name):
                raise ValueError("Observer native paths must use the checkpoint reader's store allowlist")
        required = [declaration[key] for key in ("registry", "runtime_root", "scheduler_state", "workspace", "profile_path", "phase_path")]
        required += [declaration["scheduler_state"] + ".host-attempt", str(home / "config.toml"), str(home / "hooks.json")]
        required += declaration["authority_paths"]
        for required_path in required:
            if not any(required_path == row["source_path"] or required_path.startswith(row["source_path"] + "/") for row in components):
                raise ValueError("Source witness omits a required work, authority, profile or phase participant")
        self.native_paths = declaration["native_store_paths"]
        if (not isinstance(self.native_paths, list) or not self.native_paths or
                not any(re.fullmatch(r"state_[0-9]+\.sqlite", PurePosixPath(p).name) for p in self.native_paths)):
            raise ValueError("Observer needs the qualified native checkpoint store inventory")
        for path in self.native_paths:
            if PurePosixPath(path).parent != home or not any(row["source_path"] == path for row in components):
                raise ValueError("Native store inventory differs from the declared physical participants")

    def _components(self, container):
        from docker.errors import NotFound

        result = [*self.components, *({"id": "immutable_" + str(i), "source_path": path}
                                   for i, path in enumerate(self.immutable_roots))]
        for row in self.components:
            if not row["source_path"].endswith(".sqlite"):
                continue
            for suffix in ("-wal", "-journal"):
                path = row["source_path"] + suffix
                try:
                    _path_stat(container, path)
                except NotFound:
                    continue
                result.append({"id": row["id"] + suffix, "source_path": path})
        return result

    def witness(self, container, _plan):
        if self.declaration["registry"] != _plan["registry_path"]:
            raise ValueError("Source observation must use the exact owned registry route")
        return archive_witness(container, self._components(container))

    def preflight(self, container, _plan):
        from docker.errors import NotFound

        if self.declaration["registry"] != _plan["registry_path"]:
            raise ValueError("Source preflight differs from its owned registry route")
        try:
            _path_stat(container, self.declaration["ready_path"])
        except NotFound:
            ready = False
        else:
            ready = True
        state = _json_file(container, self.declaration["scheduler_state"])
        pending = _json_file(container, self.declaration["scheduler_state"] + ".host-attempt")
        return {"source_control": source_goal_control(container, _plan, self.declaration["registry"]),
                "ready_present": ready, "unknown_process_ids": process_observation(container, self.allowed),
                "scheduler": {"host_attempt": state.get("host_attempt"), "host_pending": pending["pending"],
                              "last_wake_status": state.get("last_wake_status"), "last_wake_rc": state.get("last_wake_rc")}}

    def __call__(self, container, plan):
        container.reload()
        if not container.attrs["State"]["Paused"]:
            raise ValueError("Source observation requires the physical writer barrier")
        for row in self._components(container):
            path = row["source_path"]
            if any(target == path or target.startswith(path + "/") for target in (plan["launcher"], plan["new_target"])):
                raise ValueError("Protected source participants must exclude the owned launcher and new release")
        before = self.witness(container, plan)
        from docker.errors import NotFound

        ready = self.declaration["ready_path"]
        try:
            _path_stat(container, ready)
        except NotFound:
            ready_present = False
        else:
            ready_present = True
        unknown = process_observation(container, self.allowed)
        facts = {"probe_status": "unavailable", "ready_present": ready_present,
                 "unknown_process_ids": unknown, "source_witness_before": before}
        if ready_present or unknown:
            facts["source_witness_after"] = self.witness(container, plan)
            return facts
        observer = self.client.containers.run(container.attrs["Image"], ["sleep", "180"],
            entrypoint=[], detach=True, network_disabled=True, mem_limit=self.observer_memory,
            pids_limit=128, nano_cpus=1_000_000_000,
            labels={"loopx.source-activation-observer": plan["activation_id"]})
        try:
            for path in self.immutable_roots:
                _copy(container, observer, path, self.immutable_roots)
            reads = [self.declaration[key] for key in ("registry", "runtime_root", "scheduler_state")]
            reads += self.declaration["authority_paths"]
            reads += [self.declaration["scheduler_state"] + ".host-attempt",
                      str(PurePosixPath(self.declaration["native_home"]) / "config.toml")]
            reads += [row["source_path"] for row in self._components(container)
                      if row["source_path"] in self.native_paths or any(row["source_path"] == p + suffix
                          for p in self.native_paths for suffix in ("-wal", "-journal"))]
            for path in dict.fromkeys(reads):
                _copy(container, observer, path)
            if observer.exec_run(["mkdir", "-p", self.declaration["workspace"]], user="root").exit_code:
                raise RuntimeError("Observer workspace namespace was not prepared")
            script = Path(__file__).with_name("cli_source_probe_entry.py")
            declaration = {**self.declaration, **{key: plan[key] for key in (
                "goal_id", "agent_id", "todo_id", "turn_instance_id", "session_id")}}
            with tempfile.TemporaryFile() as spool:
                with tarfile.open(fileobj=spool, mode="w") as archive:
                    for name, payload in (("probe.py", script.read_bytes()),
                                          ("declaration.json", json.dumps(declaration).encode())):
                        entry = tarfile.TarInfo(name)
                        entry.size, entry.mode = len(payload), 0o444
                        archive.addfile(entry, io.BytesIO(payload))
                spool.seek(0)
                if not observer.put_archive("/tmp", spool):
                    raise RuntimeError("Observer read program was not staged")
            result = observer.exec_run([declaration["python"], "-I", "-B", "/tmp/probe.py", "/tmp/declaration.json"],
                user=self.user, environment={"PATH": declaration["path"], "HOME": declaration["home"],
                    "PYTHONDONTWRITEBYTECODE": "1", "LOOPX_USAGE_PING": "0"})
            if result.exit_code == 0:
                facts.update(json.loads(result.output))
            else:
                facts["observer_exit_code"] = result.exit_code  # Never expose raw native output or credentials.
        finally:
            observer.remove(force=True)
        facts["source_witness_after"] = self.witness(container, plan)
        return facts
