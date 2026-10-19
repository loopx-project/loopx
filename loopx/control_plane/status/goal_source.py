"""Read existing Goal authorities through local/SSH host/container IO.

This is a read transport, not a registry, lifecycle or analysis authority.
The native CLI and Explore owner retain their semantics. A prepared source
scope never grants management or starts a worker, container, tunnel or daemon.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from threading import RLock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..content_digest import BARE_SHA256_PATTERN
from ...chat_completed_todos import CompletedTodoPages
from ...status_server import is_loopback_host
from .ssh_host_catalog import configured_ssh_host_aliases


_GOAL_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}")
_MAX_BYTES = 16 * 1024 * 1024
_READ_LOG = """
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
path = (root / 'goals' / sys.argv[2] / 'explore-result-log.jsonl').resolve()
path.relative_to(root)
if path.exists():
    with path.open('rb') as f:
        data = f.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024: raise ValueError('log too large')
    sys.stdout.buffer.write(data)
"""


class SourceScopeError(ValueError):
    """A request is outside this prepared source, before any IO."""


def _text(value, field, limit=4096):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(char in value for char in "\x00\r\n")):
        raise ValueError(f"Invalid source {field}")
    return value


def _run(argv, *, env=None):
    # Diagnostics can contain private paths; callers must not return stderr.
    result = subprocess.run(argv, env=env, capture_output=True, check=True, timeout=25)
    if len(result.stdout) > _MAX_BYTES:
        raise ValueError("Source read exceeds the display limit")
    return result.stdout.decode("utf-8")


@dataclass(frozen=True)
class GoalSource:
    id: str
    label: str
    registry: str
    runtime_root: str
    cli: str
    python: str
    path: str
    connection: str
    environment: str
    goal_id: str | None
    host_alias: str | None = None
    container_id: str | None = None

    def __post_init__(self):
        if self.connection not in {"local", "ssh"} or self.environment not in {"host", "container"}:
            raise ValueError("Invalid source connection or environment")
        if self.connection == "ssh":
            _text(self.host_alias, "SSH alias", 255)
        elif self.host_alias is not None:
            raise ValueError("Local connections do not have an SSH alias")
        if self.environment == "container":
            if not isinstance(self.container_id, str) or not BARE_SHA256_PATTERN.fullmatch(self.container_id):
                raise ValueError("A full pinned container id is required")
        elif self.container_id is not None:
            raise ValueError("Host environments do not have a container id")
        if self.goal_id is not None and (not isinstance(self.goal_id, str) or not _GOAL_ID.fullmatch(self.goal_id)):
            raise ValueError("Invalid source Goal id")
        for key in ("id", "label", "registry", "runtime_root", "cli", "python", "path"):
            _text(getattr(self, key), key, 48 if key == "label" else 4096)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}", self.id):
            raise ValueError("Invalid source id")
        path_type = Path if self.connection == "local" and self.container_id is None else PurePosixPath
        if any(not path_type(getattr(self, key)).is_absolute() for key in ("registry", "runtime_root", "cli", "python")):
            raise ValueError("Source paths must be absolute")

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("A source must be an object")
        raw = dict(raw)
        connection = raw.pop("connection", None)
        environment = raw.pop("environment", None)
        scope = raw.pop("scope", None)
        if not isinstance(connection, dict) or not isinstance(environment, dict):
            raise ValueError("Declare a connection and an execution environment")
        if connection.get("kind") == "local" and set(connection) == {"kind"}:
            host_alias = None
        elif connection.get("kind") == "ssh" and set(connection) == {"kind", "host_alias"}:
            host_alias = _text(connection["host_alias"], "SSH alias", 255)
        else:
            raise ValueError("Use a local connection or a configured SSH host alias")
        if environment.get("kind") == "host" and set(environment) == {"kind"}:
            container_id = None
        elif environment.get("kind") == "container" and set(environment) == {"kind", "container_id"}:
            container_id = environment["container_id"]
            if not isinstance(container_id, str) or not BARE_SHA256_PATTERN.fullmatch(container_id):
                raise ValueError("A full pinned container id is required")
        else:
            raise ValueError("Use a host or a pinned container environment")
        if isinstance(scope, dict) and set(scope) == {"all_registered"} and scope["all_registered"] is True:
            goal_id = None
        elif isinstance(scope, dict) and set(scope) == {"goal_id"}:
            goal_id = scope["goal_id"]
            if not isinstance(goal_id, str) or not _GOAL_ID.fullmatch(goal_id):
                raise ValueError("Invalid source Goal id")
        else:
            raise ValueError("Explicitly scope one Goal or all registered Goals")
        expected = {"id", "label", "registry", "runtime_root", "cli", "python", "path"}
        if set(raw) != expected:
            raise ValueError("Invalid or missing source manifest fields")
        return cls(**raw, connection=connection["kind"], environment=environment["kind"],
                   goal_id=goal_id, host_alias=host_alias, container_id=container_id)

    @property
    def namespace(self):
        # A reused display id cannot retain another authority's cache or cursor.
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def check_access(self, goal_id=None):
        selected = self.goal_id if goal_id is None else goal_id
        if selected is not None and (not isinstance(selected, str) or not _GOAL_ID.fullmatch(selected)):
            raise SourceScopeError("Invalid source Goal id")
        if self.goal_id is not None and selected != self.goal_id:
            raise SourceScopeError("Goal is not in this source")
        # Recheck even on cached reads; a removed alias is not a retained grant.
        if self.connection == "ssh" and self.host_alias not in configured_ssh_host_aliases():
            raise SourceScopeError("The configured SSH source is unavailable")
        return selected

    def describe(self):
        return {"id": self.id, "namespace": self.namespace, "label": self.label,
                "connection": self.connection, "environment": self.environment}

    def _execute(self, argv):
        if self.environment == "container":
            argv = ["docker", "exec", "-e", "LOOPX_USAGE_PING=0", "-e", f"PATH={self.path}",
                    self.container_id, *argv]
        if self.connection == "ssh":
            if self.environment == "host":
                argv = ["env", "LOOPX_USAGE_PING=0", f"PATH={self.path}", *argv]
            return _run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                         self.host_alias, shlex.join(argv)])
        env = {**os.environ, "LOOPX_USAGE_PING": "0"}
        if self.environment == "host":
            env["PATH"] = self.path
        return _run(argv, env=env)

    def read_status(self, goal_id=None):
        selected = self.check_access(goal_id)
        args = [self.cli, "--registry", self.registry, "--runtime-root", self.runtime_root,
                "--format", "json", "status"]
        if selected is not None:
            args += ["--goal-id", selected]
        status = json.loads(self._execute(args))
        if not isinstance(status, dict):
            raise ValueError("Invalid native source status")
        history = status.get("run_history")
        goals = history.get("goals") if isinstance(history, dict) else None
        if (status.get("ok") is not True or not isinstance(goals, list)
                or any(not isinstance(goal, dict) or not isinstance(goal.get("id"), str)
                       or not _GOAL_ID.fullmatch(goal["id"]) for goal in goals)):
            raise ValueError("Invalid native source status")
        if selected is not None and (len(goals) != 1 or goals[0]["id"] != selected):
            raise SourceScopeError("The source no longer exposes the selected Goal")
        if self.goal_id is None and (any(goal.get("registry_member") is not True for goal in goals)):
            raise SourceScopeError("Only registered Goals are in this source scope")
        return status

    def read_todo(self, goal_id, todo_id):
        self.check_access(goal_id)
        if not isinstance(todo_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", todo_id):
            raise SourceScopeError("Invalid source Task id")
        return json.loads(self._execute([
            self.cli, "--format", "json", "--registry", self.registry,
            "--runtime-root", self.runtime_root, "todo", "list",
            "--goal-id", goal_id, "--todo-id", todo_id,
        ]))

    def read_events(self, goal_id):
        """Read JSON objects; the capability-owned projector verifies semantics."""
        selected = self.check_access(goal_id)
        if selected is None:
            raise SourceScopeError("Choose a Goal before reading history")
        log = self._execute([self.python, "-c", _READ_LOG, self.runtime_root, selected])
        events = []
        for line in log.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("Invalid native Explore record")
            events.append(event)
        return events


class GoalSourceReader:
    """One source's bounded read cache and source-bound history snapshots."""

    def __init__(self, source, *, history_projector):
        self.source = source
        self._history_projector = history_projector
        self._lock = RLock()
        self._status = None
        self._read_at = 0.0
        self._selected = None
        self._pages = CompletedTodoPages()

    def _invalidate(self):
        self._status = None
        self._pages = CompletedTodoPages()

    def status(self, goal_id=None, *, fresh=False):
        with self._lock:
            try:
                selected = self.source.check_access(goal_id)
                if (fresh or self._status is None or selected != self._selected
                        or monotonic() - self._read_at >= 2):
                    self._status = None
                    self._status = self.source.read_status(selected)
                    self._selected, self._read_at = selected, monotonic()
                return deepcopy(self._status)
            except (ValueError, OSError, subprocess.SubprocessError):
                self._invalidate()
                raise

    def describe(self):
        return {"ok": True, "source": self.source.describe(), "read_only": True,
                "scope": {"goal_id": self.source.goal_id} if self.source.goal_id else {"all_registered": True},
                "operations": {"goals": True, "status": True, "history": True,
                               "lifecycle": False, "conversation": False, "analysis": False}}

    def registered_status(self, goal_id, *, fresh=False):
        with self._lock:
            status = self.status(goal_id, fresh=fresh)
            if not any(goal["id"] == goal_id and goal.get("registry_member") is True
                       for goal in status["run_history"]["goals"]):
                # A runtime-only historical row must not poison a recovered
                # registered Goal's display or model-admission cache.
                self._invalidate()
                raise SourceScopeError("The original Goal is no longer registered")
            return status

    def goals(self, goal_id=None, *, fresh=False):
        status = self.status(goal_id, fresh=fresh)
        return {"ok": True, "source": self.source.describe(),
                "items": status["run_history"]["goals"]}

    def history(self, goal_id, cursor=""):
        with self._lock:
            if goal_id is None:
                raise SourceScopeError("Choose a Goal before reading history")
            if not isinstance(cursor, str) or len(cursor) > 512:
                raise ValueError("history_cursor_expired")
            # Membership/SSH access is rechecked before any saved cursor is used.
            self.status(goal_id)
            try:
                page = self._pages.page(scope=(self.source.namespace, goal_id), cursor=cursor,
                    load=lambda: self._history_projector(self.source.read_events(goal_id), goal_id=goal_id))
                return {**page, "source": self.source.describe(), "goal_id": goal_id}
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                if str(exc) != "history_cursor_expired":
                    self._invalidate()
                raise


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def linked_source_goals(registry):
    """Project explicit source references without reading or creating a Goal."""
    raw = registry.get("goal_source_links", [])
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("Invalid Goal source catalog")
    sources = []
    for link in raw:
        if not isinstance(link, dict) or set(link) != {"parent_goal_id", "source"}:
            raise ValueError("Invalid Goal source link")
        source = GoalSource.parse(link["source"])
        if source.goal_id is None:
            raise ValueError("A child link must select one original Goal")
        sources.append({"goal_id": source.goal_id, "parent_goal_id": link["parent_goal_id"],
                        "namespace": source.namespace, "label": source.label})
    from ..effect_runtime import effect_runtime_result
    from ...registry import registry_goals

    return effect_runtime_result("presentation.goal_sources.project", {
        "local_goal_ids": [goal["id"] for goal in registry_goals(registry)], "links": sources,
    })["goals"]


class GoalSourceCatalog:
    """App IO over its existing registry's source links, not mirror Goals.

    The TS presentation owner qualifies organization/identity. Native sources
    retain all Task, state, history and execution authority. Analysis artifacts
    live locally under the same physical namespace used by prepared readers.
    """
    def __init__(self, registry_path, runtime_root, *, history_projector):
        self.registry_path = Path(registry_path)
        self.runtime_root = Path(runtime_root)
        self._history_projector = history_projector
        self._readers = {}
        self._lock = RLock()

    def resolve(self, goal_id, registry=None):
        from ...registry import load_registry

        registry = load_registry(self.registry_path) if registry is None else registry
        rows = linked_source_goals(registry)
        row = next((row for row in rows if row["id"] == goal_id), None)
        if row is None:
            return None
        raw = next(link["source"] for link in registry["goal_source_links"]
                   if link["source"]["scope"].get("goal_id") == goal_id)
        source = GoalSource.parse(raw)
        with self._lock:
            namespaces = {goal["source_access"]["namespace"] for goal in rows}
            self._readers = {key: value for key, value in self._readers.items() if key in namespaces}
            if source.namespace not in self._readers:
                self._readers[source.namespace] = GoalSourceReader(source, history_projector=self._history_projector)
            reader = self._readers[source.namespace]
        return row, reader

    def status(self, goal_id, registry=None):
        resolved = self.resolve(goal_id, registry)
        if resolved is None:
            return None
        row, reader = resolved
        status = reader.registered_status(goal_id)
        source_goal = next((goal for goal in status["run_history"]["goals"]
                            if goal["id"] == goal_id and goal.get("registry_member") is True), None)
        if source_goal is None:
            raise SourceScopeError("The original Goal is no longer registered")
        source_goal["native_parent_goal_id"] = source_goal.get("parent_goal_id")
        source_goal.update({key: row[key] for key in ("parent_goal_id", "source_access")})
        return status

    def analysis_scope(self, goal_id, *, namespace, fresh):
        resolved = self.resolve(goal_id)
        if resolved is None:
            return None
        row, reader = resolved
        if namespace != reader.source.namespace:
            raise SourceScopeError("Analysis source identity changed")
        reader.registered_status(goal_id, fresh=fresh)
        return self.runtime_root / "goal-sources" / row["source_access"]["namespace"]

    def update(self, *, parent_goal_id, raw_source):
        from ...registry import load_registry
        from ..projects.registry_codec import mutate_project_registry

        source = GoalSource.parse(raw_source)
        link = {"parent_goal_id": parent_goal_id, "source": raw_source}

        def proposed(registry):
            links = list(registry.get("goal_source_links", []))
            existing = next((item for item in links
                if item["source"]["scope"].get("goal_id") == source.goal_id), None)
            if existing is not None and existing != link:
                raise SourceScopeError("The original source identity and parent are already linked")
            if existing is None:
                links.append(link)
            return links

        before = load_registry(self.registry_path)
        linked_source_goals({**before, "goal_source_links": proposed(before)})
        # Qualify original membership after metadata admission, before writing.
        reader = GoalSourceReader(source, history_projector=lambda rows, **_: rows)
        rows = reader.goals(source.goal_id, fresh=True)["items"]
        if source.goal_id is None or not any(
            row["id"] == source.goal_id and row.get("registry_member") is True for row in rows
        ):
            raise SourceScopeError("Choose a registered original Goal")

        def apply(registry):
            registry["goal_source_links"] = proposed(registry)
            linked_source_goals(registry)

        mutate_project_registry(self.registry_path, operation="goal_source_links", reducer=apply)
        return {"ok": True, "goals": linked_source_goals(load_registry(self.registry_path))}

    def remove(self, *, parent_goal_id, goal_id, namespace):
        from ..projects.registry_codec import mutate_project_registry

        def apply(registry):
            links = []
            for link in registry.get("goal_source_links", []):
                source = GoalSource.parse(link["source"])
                if source.goal_id == goal_id and link["parent_goal_id"] == parent_goal_id:
                    if source.namespace != namespace:
                        raise SourceScopeError("The source link changed; read its current identity")
                else:
                    links.append(link)
            registry["goal_source_links"] = links
            linked_source_goals(registry)

        mutate_project_registry(self.registry_path, operation="goal_source_unlink", reducer=apply)


def read_endpoint(endpoint, operation, *, goal_id=None, cursor="", source_namespace=None):
    """CLI/Agent client for the same read endpoints consumed by the App adapter."""
    url = urlparse(endpoint)
    if (url.scheme != "http" or not is_loopback_host(url.hostname or "") or url.username
            or url.password or url.path not in {"", "/", "/status.json"} or url.fragment):
        raise ValueError("Use the prepared reader's loopback HTTP endpoint")
    # A pinned status URL is also a usable client endpoint. Reject unrelated
    # query options instead of silently dropping caller scope.
    endpoint_query = parse_qs(url.query, keep_blank_values=True)
    if endpoint_query:
        if set(endpoint_query) != {"source_namespace"} or len(endpoint_query["source_namespace"]) != 1:
            raise ValueError("Use the prepared reader's loopback HTTP endpoint")
        pinned = endpoint_query["source_namespace"][0]
        if source_namespace is not None and source_namespace != pinned:
            raise ValueError("Conflicting source identities")
        source_namespace = pinned
    if operation not in {"describe", "goals", "status", "history"}:
        raise ValueError("Only describe, goals, status and history reads are supported")
    base = f"{url.scheme}://{url.netloc}/api/goal-source"
    opener = build_opener(_NoRedirect())

    def read(path):
        try:
            with opener.open(Request(path), timeout=30) as response:
                body = response.read(_MAX_BYTES + 1)
        except HTTPError as exc:
            try:
                error = json.loads(exc.read(_MAX_BYTES + 1))
            except (ValueError, OSError):
                error = {}
            message = error.get("error") if isinstance(error, dict) else None
            raise ValueError(message if isinstance(message, str) and len(message) <= 600
                             else "Goal source read failed") from exc
        except (OSError, URLError) as exc:
            raise ValueError("Goal source read failed; keep the original source and retry") from exc
        if len(body) > _MAX_BYTES:
            raise ValueError("Source read exceeds the display limit")
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise ValueError("Invalid Goal source read response")
        return payload

    description = read(base)
    source = description.get("source")
    namespace = source.get("namespace") if isinstance(source, dict) else None
    if not isinstance(namespace, str) or not BARE_SHA256_PATTERN.fullmatch(namespace):
        raise ValueError("The reader did not expose a source identity")
    if source_namespace is not None and source_namespace != namespace:
        raise ValueError("Goal source changed; do not reuse the previous selection")
    if operation == "describe":
        return description
    query = {"source_namespace": namespace}
    if goal_id is not None:
        query["goal_id"] = goal_id
    if cursor:
        query["cursor"] = cursor
    payload = read(f"{base}/{operation}?{urlencode(query)}")
    if not isinstance(payload.get("source"), dict) or payload["source"].get("namespace") != namespace:
        raise ValueError("Goal source changed during the read")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("describe", "goals", "status", "history"))
    parser.add_argument("--endpoint", required=True, help="Prepared owner-local reader URL")
    parser.add_argument("--goal-id")
    parser.add_argument("--cursor", default="")
    parser.add_argument("--source-namespace", help="Bind a previous source description")
    args = parser.parse_args()
    try:
        result = read_endpoint(args.endpoint, args.operation, goal_id=args.goal_id, cursor=args.cursor,
                               source_namespace=args.source_namespace)
    except (ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
