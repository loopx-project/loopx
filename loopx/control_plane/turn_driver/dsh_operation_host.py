"""Explicitly opted-in SDK-owned operation host for managed Turns.

The ordinary DSH adapter/default plugin remain unchanged. This internal host
requires an admitted Todo request and explicit compiled module. It preserves
SDK stdio ownership and delegates all operation decisions to the existing owner.
"""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
import importlib.metadata
import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping

from ...chat_action_store import ChatActionStore
from ...file_lock import exclusive_file_lock
from ...dsh_goal_mode.turn_host_adapter import (
    build_result,
    build_sdk_config,
    normalize_runner_outcome,
    parse_model_json,
    render_prompt,
    extract_turn_authority,
    terminal_error_reason,
    terminal_output_budget_state,
)
from ..collaboration.goal_instance_scope import collaboration_goal_scope
from ..collaboration.operation_handoff import (
    agent_operation_action,
    managed_operation_binding_current,
    pending_operation_handoffs,
)
from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
from .codex_operation_host import operation_tool_handler
from .codex_sessions import (
    _lineage,
    _read_codex_cli_session_document,
    _session_path,
    _write_codex_cli_session,
    select_codex_cli_session,
)
from .dsh_operation_connection import DshOperationConnection
from .executor import LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION
from .host_failure import BuiltInHostError

DSH_OPERATION_TRANSPORT = "dsh-native-operation-tools-v0"


def _sdk_runtime_pin(runtime_bin: Path | None = None) -> dict[str, Any]:
    # The private process handle is fail-closed and qualified against this SDK.
    # A new SDK/runtime version needs its own real-path qualification first.
    sdk = importlib.metadata.distribution("deepseek-harness-sdk")
    if runtime_bin is not None:
        entry = runtime_bin.resolve(strict=True)
        package = entry.parent.parent
        manifest = json.loads((package / "package.json").read_text())
        modules = package.parent.parent
        if (
            sdk.version != "0.2.1a3"
            or manifest.get("name") != "@deepseek-ai/dsh"
            or manifest.get("version") != "0.2.1-alpha.3"
            or entry != package / "lib" / "bin.js"
            or modules.name != "node_modules"
        ):
            raise ValueError("explicit DSH operation runtime is not qualified")
        node = shutil.which("node")
        if node is None:
            raise ValueError("explicit DSH operation runtime requires Node")
        closure = hashlib.sha256()
        for path in sorted(modules.rglob("*")):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(modules):
                raise ValueError("DSH runtime closure contains an external file")
            closure.update(str(path.relative_to(modules)).encode() + b"\0")
            closure.update(bytes.fromhex(_digest(path)))
        return {
            "versions": {
                "deepseek-harness-sdk": sdk.version,
                "@deepseek-ai/dsh": manifest["version"],
            },
            "sdk_files": {
                str(path): _digest(Path(sdk.locate_file(path)))
                for path in sdk.files or ()
                if str(path).startswith("deepseek_harness/")
                and str(path).endswith(".py")
            },
            "launch_files": {
                str(entry): _digest(entry),
                str(Path(node).resolve()): _digest(Path(node)),
            },
            "npm_closure_sha256": closure.hexdigest(),
        }
    from deepseek_harness_runtime import resolve_bundled_launch_args

    versions = {
        name: importlib.metadata.version(name)
        for name in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin")
    }
    if set(versions.values()) != {"0.1.5rc1"}:
        raise ValueError(
            "DSH operation process adapter requires a qualified SDK/runtime version"
        )
    launch = resolve_bundled_launch_args()
    files = [Path(item).resolve() for item in launch if Path(item).is_file()]
    if not files:
        raise ValueError("DSH runtime executable pin is unavailable")
    return {
        "versions": versions,
        "launch_files": {str(path): _digest(path) for path in files},
    }


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _run_dsh_operation_host(
    request: Mapping[str, Any],
    *,
    runtime_root: Path,
    registry_path: Path,
    project: Path,
    dsh_home: Path,
    operation_module: Path,
    provider: str,
    model: str,
    reasoning_effort: str,
    max_tokens: int,
    timeout_seconds: float = 120,
    cordis: Path | None = None,
    source_route: Mapping[str, str] | None = None,
    goal_admission: FirstPartyHostGoalAdmission | None = None,
    confirmed_operation_id: str | None = None,
    on_notification: Any = None,
    runtime_bin: Path | None = None,
    env: Mapping[str, str] | None = None,
    owner: OwnedDshOperationHost,
) -> dict[str, Any]:
    """One owned SDK run; final prose cannot create an operation outcome."""
    if request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION:
        raise ValueError("unsupported LoopX Turn host request")
    authority = extract_turn_authority(request)
    if not all(
        isinstance(value, str) and value
        for value in (provider, model, reasoning_effort)
    ):
        raise ValueError("an explicit DSH operation profile is required")
    if (
        type(max_tokens) is not int
        or not 1 <= max_tokens <= 32768
        or not 0 < timeout_seconds <= 600
    ):
        raise ValueError("DSH operation run requires bounded token and time budgets")
    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

    lineage = _lineage(request)
    module = operation_module.resolve(strict=True)
    patch = cordis.resolve(strict=True) if cordis else None
    profile = {
        "transport": DSH_OPERATION_TRANSPORT,
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "max_tokens": max_tokens,
        "workspace": str(project.resolve()),
        "dsh_home": str(dsh_home.resolve()),
        "module": {
            "path": str(module),
            "sha256": _digest(module),
            "package_js": {
                path.name: _digest(path)
                for path in sorted(module.parent.glob("*.js"))
                if path.is_file()
            },
        },
        "patch": {"path": str(patch), "sha256": _digest(patch)} if patch else None,
        "runtime": _sdk_runtime_pin(runtime_bin),
    }
    if env is not None:
        # CLI-owned credentials stay out of state. Bind an explicitly resolved
        # endpoint without serializing its potentially private URL.
        profile["provider_endpoint_sha256"] = hashlib.sha256(
            env.get("DEEPSEEK_BASE_URL", "").encode()
        ).hexdigest()
    profile_digest = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    original = _read_codex_cli_session_document(runtime_root, lineage=lineage)
    binding = select_codex_cli_session(
        runtime_root, lineage=lineage, host="dsh", goal_admission=goal_admission
    )
    session_plan = request.get("session") or {}
    fresh = (session_plan.get("context_policy") or {}).get("mode") == "fresh"
    if fresh:
        binding = None
    elif original is not None and binding is None:
        raise ValueError(
            "existing session is not a qualified DSH operation binding; select fresh explicitly"
        )
    action = session_plan.get("action")
    if action not in {"start_new", "resume"} or (action == "resume") != (
        binding is not None
    ):
        raise ValueError("managed DSH session changed after Turn planning")
    if binding and (
        binding.get("operation_transport") != DSH_OPERATION_TRANSPORT
        or binding.get("operation_profile_digest") != profile_digest
    ):
        raise ValueError(
            "DSH operation profile changed; select fresh and obtain fresh approval"
        )
    can_restore = profile["runtime"]["versions"]["deepseek-harness-sdk"] == "0.2.1a3"
    if binding and owner.session_id is None and not can_restore:
        raise ValueError(
            "DSH SDK cannot resume a persisted session in a new process; retain the owned connection or qualify upstream resume"
        )
    session_id = binding["session_id"] if binding else "loopx-dsh-" + uuid.uuid4().hex
    if owner.session_id is not None and owner.session_id != session_id:
        raise ValueError("an owned DSH connection cannot switch session identity")
    route = {
        **lineage,
        "host_surface": "loopx-managed-dsh",
        "thread_id": session_id,
        "profile_digest": profile_digest,
    }
    store = ChatActionStore(runtime_root / "chat" / "actions")
    if confirmed_operation_id is not None:
        from ..collaboration.operation_wake import require_current_operation_scope

        proposal = store.load(confirmed_operation_id)
        if proposal is None or binding is None or action != "resume":
            raise ValueError("confirmed operation requires the original DSH session")
        parameters = proposal["normalized_parameters"]
        require_current_operation_scope(registry_path, parameters)
        store._agent_operation_plan(
            proposal,
            action="wake",
            binding_current=managed_operation_binding_current(runtime_root, parameters),
            launch_context={
                "host": "dsh",
                "operation_tools": True,
                "iteration_context": (session_plan.get("context_policy") or {}).get(
                    "mode"
                ),
            },
            executor_route={
                **route,
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
        )
    continuations: list[dict[str, Any]] = []

    def native_start(host_turn_id: str) -> None:
        for locator in continuations:
            agent_operation_action(
                runtime_root,
                registry_path,
                proposal_id=locator["operation_id"],
                actor={
                    **route,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "host_turn_id": host_turn_id,
                },
                action="observe_host_start",
                turn_key=request["turn_key"],
            )

    native_profile = {
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
    }
    owner.on_start = native_start
    if owner.connection is None:
        connection = owner.resources.enter_context(
            DshOperationConnection(
                session_id,
                native_profile,
                on_start=lambda turn: owner.native_start(turn),
            )
        )
        owner.connection = connection
        owner.session_id = session_id
        wrapper = connection.path.parent / "operation.mjs"
        wrapper.write_text(
            "import {apply} from " + json.dumps(module.as_uri()) + ";\n"
            "export const name='loopx-owned-operation';\n"
            "export const inject=['agents','tools','sessionProjections'];\n"
            "export function applyOwned(ctx) { apply(ctx,{socketPath:"
            + json.dumps(str(connection.path))
            + "}); }\n"
            "export {applyOwned as apply};\n",
            encoding="utf-8",
        )
        owned_patch = connection.path.parent / "operation.patch.yml"
        owned_patch.write_text(
            "- insert:\n    - id: loopx-owned-operation\n      name: "
            + json.dumps(wrapper.as_uri())
            + "\n",
            encoding="utf-8",
        )
        config = build_sdk_config(
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            workspace=project.resolve(),
            dsh_home=dsh_home.resolve(),
            max_tokens=max_tokens,
            cordis=patch,
            runtime_bin=str(runtime_bin.resolve()) if runtime_bin else None,
            request_timeout_seconds=timeout_seconds,
            env=env,
        )
        config["patches"] = (*config.get("patches", ()), str(owned_patch))
        harness = owner.resources.enter_context(
            DeepSeekHarness(DeepSeekHarnessConfig(**config))
        )
        owner.harness = harness
        process = getattr(harness.client, "_proc", None)
        if process is None or process.poll() is not None:
            raise ValueError("SDK owned process handle is unavailable")
        connection.bind(process.pid, owner.handle)
        if binding:
            # Official restore rejects absent/unreadable/occupied sessions. It
            # sends no prompt and never creates a replacement identity.
            harness.resume_session(session_id)
    connection, harness = owner.connection, owner.harness
    if harness is None or connection is None:
        raise ValueError("owned DSH connection is unavailable")
    process = getattr(harness.client, "_proc", None)
    if process is None or process.poll() is not None:
        raise ValueError("SDK owned process handle is unavailable")
    handler = operation_tool_handler(
        runtime_root=runtime_root,
        registry_path=registry_path,
        lineage=lineage,
        session_id=session_id,
        profile_digest=profile_digest,
        model=model,
        reasoning_effort=reasoning_effort,
        source_route=source_route,
        goal_admission=goal_admission,
        host="dsh",
    )

    def store_binding() -> None:
        with exclusive_file_lock(_session_path(runtime_root, lineage)):
            if (
                _read_codex_cli_session_document(runtime_root, lineage=lineage)
                != original
            ):
                raise ValueError("DSH binding changed during SDK initialization")
            _write_codex_cli_session(
                runtime_root,
                lineage=lineage,
                host="dsh",
                session_id=session_id,
                goal_ref=request.get("goal_ref"),
                operation_profile_digest=profile_digest,
                operation_model=model,
                operation_reasoning_effort=reasoning_effort,
            )

    if goal_admission is None:
        store_binding()
    else:
        goal_admission.accept_result(store_binding)
    owner.handler = handler
    with collaboration_goal_scope(
        registry_path,
        goal_id=lineage["goal_id"],
        agents=(lineage["agent_id"],),
        caller_goal_ref=request.get("goal_ref"),
        require_active=True,
    ) as scope:
        pending = pending_operation_handoffs(
            runtime_root,
            lineage["goal_id"],
            lineage["agent_id"],
            registry_path=registry_path,
            scope=scope,
            executor_route=route,
            cursor_scope=hashlib.sha256(
                json.dumps(route, sort_keys=True).encode()
            ).hexdigest(),
        )
    continuations.extend(
        {
            key: item[key]
            for key in (
                "operation_id",
                "payload_digest",
                "confirmation_digest",
                "claim_id",
            )
        }
        for item in pending["items"]
        if item["status"] == "authorized_pending"
    )

    def observe(notification: Any) -> None:
        connection.observe(notification)
        if on_notification is not None:
            on_notification(notification)

    prompt = render_prompt(authority) + (
        "\nUse loopx_operation for context/pending/prepare/inspect/consume/report. "
        "Prepare creates only a canonical proposal and may run before human confirmation. "
        "Only first successful consume with execution_allowed=true permits the exact domain effect. "
        "Consumed or unknown submissions require original evidence reconciliation, never retry. "
        "Final prose is not an operation outcome. Do not invent missing terms.\n"
        "Canonical locators for this binding: "
        + json.dumps(continuations, sort_keys=True)
    )
    timer = threading.Timer(timeout_seconds, owner.close)
    timer.daemon = True
    timer.start()
    try:
        outcome = normalize_runner_outcome(
            harness.run(prompt, session_id=session_id, on_notification=observe)
        )
    finally:
        timer.cancel()
    if (
        terminal_error_reason(outcome) is not None
        or terminal_output_budget_state(outcome) is not None
    ):
        raise BuiltInHostError(
            "dsh_operation_native_turn_failed",
            failure_kind="unknown",
            recovery_kind="resume_session",
        )
    return build_result(request, parse_model_json(outcome["final_response"]))


class OwnedDshOperationHost:
    """Own one SDK connection across separately admitted Turns.

    No background scheduler or approval store. SDK 0.1.5rc1 requires a retained
    connection; the qualified explicit runtime uses the official restore API.
    """

    def __init__(self) -> None:
        self.resources = ExitStack()
        self.connection: DshOperationConnection | None = None
        self.harness: Any = None
        self.session_id: str | None = None
        self.handler: Any = None
        self.on_start: Any = None
        self._entered = False
        self._lock = threading.Lock()

    def __enter__(self) -> OwnedDshOperationHost:
        if self._entered or self.session_id is not None:
            raise ValueError("owned DSH host cannot be reopened")
        self._entered = True
        return self

    def run(self, request: Mapping[str, Any], **options: Any) -> dict[str, Any]:
        if not self._entered or not self._lock.acquire(blocking=False):
            raise ValueError("owned DSH host is closed or already running")
        try:
            return _run_dsh_operation_host(request, owner=self, **options)
        except BaseException:
            # Failed initialization/restore must not leave a reusable client
            # whose next prompt could create an un-restored session instead.
            self.close()
            raise
        finally:
            self.handler = None
            self.on_start = None
            self._lock.release()

    def handle(self, *args: Any) -> dict[str, Any]:
        if not self._entered or self.handler is None:
            raise ValueError("no active admitted DSH Turn")
        return self.handler(*args)

    def native_start(self, turn: str) -> None:
        if not self._entered or self.on_start is None:
            raise ValueError("no active admitted DSH Turn")
        self.on_start(turn)

    def close(self) -> None:
        self._entered = False
        self.handler = None
        self.on_start = None
        self.resources.close()

    def __exit__(self, *_args: Any) -> None:
        self.close()


def run_dsh_operation_host(
    request: Mapping[str, Any], **options: Any
) -> dict[str, Any]:
    """Own one run; restore only through a qualified SDK/runtime's public API."""
    with OwnedDshOperationHost() as host:
        return host.run(request, **options)
