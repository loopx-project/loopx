"""Typed action orchestration for the owner-local LoopX Chat control plane."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping, Protocol, Sequence

from .agent_registry import (
    agent_profile_for_goal,
    registered_agent_ids_for_goal,
)
from .control_plane.projects.identity import GoalCreationConflictError
from .control_plane.coordination.new_goal_storage_transport import initialize_goal_storage_target
from .chat import build_todo_review_preview
from .chat.action_normalization import ChatActionNormalizationMixin
from .chat_action_store import (
    ACTION_KINDS as SUPPORTED_ACTION_KINDS,
    ActionConflictError,
    ChatActionStore,
)
from .chat.goal_lifecycle_actions import ChatGoalLifecycleActionMixin
from .chat.monitor_actions import ChatMonitorActionMixin
from .chat_store import ChatSessionStore, TERMINAL_TURN_STATES
from .chat.todo_actions import ChatTodoActionMixin
from .chat.todo_source import ChatTodoSource, resolve_chat_todo_source
from .control_plane.goals.acceptance_actions import GoalAcceptanceActionMixin
from .control_plane.goals.goal_objective_actions import GoalObjectiveActionMixin
from .configure_goal import configure_goal
from .control_plane.goals.configure_goal_service import (
    bind_goal_agent_with_global_sync,
    read_goal_agent_binding_with_source_route,
)
from .control_plane.runtime.time import now_utc, parse_timestamp
from .control_plane.scheduler.monitor_todo import monitor_next_due_at
from .control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable, read_canonical_todos_if_promoted
from .control_plane.todos.contract import TODO_DECISION_OUTCOME_VALUES
from .history import load_registry
from .host_loop_activation import build_host_loop_activation_packet
from .kiro_cli_goal_mode import KIRO_CLI_CHAT_AGENT_ID
from .paths import resolve_runtime_root
from .public_safe_text import OPAQUE_ID_PATTERN as _OPAQUE_ID
from .quota import build_quota_should_run
from .registry import registry_goals
from .control_plane.todos.mutation_api import add_goal_todo
from .file_lock import try_exclusive_file_lock


CHAT_ACTION_RESPONSE_SCHEMA_VERSION = "loopx_chat_action_response_v1"

# Runtime Endpoint ids and durable Goal agent ids are chosen independently, so
# a family token collapses both onto the host that produced them: Endpoint
# `codex` has to resolve to a registered `codex-main-control`. Every host that
# owns a built-in Endpoint needs a row here, or selecting it in the workspace
# raises `agent_binding_required` for an agent the user did register. A host
# absent from the table resolves to its own id, which only matches an
# identically named agent — correct as a fallback, wrong as a built-in's only
# behavior. Matching is bounded at a `-` delimiter, so this table lists family
# roots rather than free prefixes: `kiro-worker-1` joins the Kiro family while
# `kiroscope-worker` keeps its own identity. Keep the rule in this one table.
_AGENT_FAMILY_ROOTS: tuple[tuple[str, str], ...] = (
    ("codex", "codex"),
    ("claude", "claude-code"),
    ("kiro", KIRO_CLI_CHAT_AGENT_ID),
)
_MONITOR_CADENCE = re.compile(
    r"^(?P<count>[1-9][0-9]{0,4})(?P<unit>s|m|h|d)$",
    re.IGNORECASE,
)


class _RuntimeController(Protocol):
    def capabilities(self) -> Sequence[Mapping[str, object]]: ...

    def open_session(
        self, *, goal_id: str, goal_instance_id: str | None = None, agent_id: str, work_dir: Path,
        objective: str, mode: str, session_id: str | None = None, channel_id: str,
        agent_goal_id: str, managed_agent_id: str = ...,
    ) -> tuple[dict[str, object], bool]: ...

    def submit_turn(
        self, *, session_id: str, client_turn_id: str, message: str, work_dir: Path,
        objective: str, display_message: str = ...,
    ) -> tuple[dict[str, object], bool]: ...

    def resume_session_queue(self, *, session_id: str, work_dir: Path, objective: str) -> object: ...


class ProtectedActionGate(ActionConflictError):
    """A typed preview is valid while its canonical write needs an explicit gate."""

    def __init__(
        self, action_kind: str, *, gate: Mapping[str, object] | None = None
    ) -> None:
        self.action_kind = action_kind
        self.gate = dict(
            gate
            or {
                "kind": "protected_action",
                "summary": f"{action_kind} needs an explicit canonical LoopX write service.",
                "next_action": "Keep this preview and complete the protected transition through its canonical LoopX service.",
            }
        )
        self.proposal: dict[str, object] | None = None
        super().__init__(str(self.gate["summary"]))


class _AgentUnavailableError(ValueError):
    """Availability refusal from the existing endpoint eligibility owner.

    Tool, trust and workspace admission failures remain ordinary ValueErrors;
    callers must not infer this narrower condition from their message text.
    """

    def __init__(self, message: str, *, capability: Mapping[str, object] | None = None) -> None:
        self.capability = dict(capability) if capability is not None else None
        super().__init__(message)


def _as_dict(value: object) -> dict[str, object]:
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _opaque(value: object, *, field: str) -> str:
    token = str(value or "").strip()
    if not _OPAQUE_ID.fullmatch(token):
        raise ValueError(f"{field} must be a compact opaque id")
    return token


def _text(value: object, *, field: str, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or len(text) > limit:
        raise ValueError(f"{field} must be bounded visible text")
    return text


def _digest(payload: object) -> str:
    stable = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _receipt_id(payload: object) -> str:
    return _digest(payload)[:32]


def goal_workspace_ref(root: Path) -> str:
    """The Goal-creation token for one server-configured workspace root."""

    return f"workspace-{hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:12]}"


def goal_workspace_labels(directories: Sequence[Path]) -> dict[Path, str]:
    """Owner-facing names for workspace directories, without checkout paths.

    A directory is named by its own name; parent names are added, one at a
    time, only while two different directories would otherwise read the same.
    The filesystem anchor is never shown.
    """

    unique = list(dict.fromkeys(directories))
    parts = {path: path.parts[1:] if path.anchor else path.parts for path in unique}
    depth = dict.fromkeys(unique, 1)
    while True:
        labels = {path: "/".join(parts[path][-depth[path]:]) or str(path) for path in unique}
        readers: dict[str, list[Path]] = {}
        for path, label in labels.items():
            readers.setdefault(label, []).append(path)
        ambiguous = [path for group in readers.values() if len(group) > 1
                     for path in group if depth[path] < len(parts[path])]
        if not ambiguous:
            return labels
        for path in ambiguous:
            depth[path] += 1


def _normalize_cadence(value: object) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "hourly": "1h",
        "daily": "1d",
        "every_hour": "1h",
        "every_day": "1d",
    }
    if candidate in aliases:
        return aliases[candidate]
    verbose = re.fullmatch(
        r"every_(?P<count>[1-9][0-9]{0,4})_(?P<unit>seconds?|minutes?|hours?|days?)",
        candidate,
    )
    if verbose:
        units = {"s": "s", "m": "m", "h": "h", "d": "d"}
        return f"{verbose.group('count')}{units[verbose.group('unit')[0]]}"
    compact = candidate.replace(" ", "")
    match = _MONITOR_CADENCE.fullmatch(compact)
    if match:
        return f"{int(match.group('count'))}{match.group('unit').lower()}"
    raise ValueError("cadence must look like 30m, 2h, 1d, hourly, or daily")


def _monitor_metadata(parameters: Mapping[str, object], *, schedule: bool = True) -> dict[str, str]:
    metadata = {
        key: str(parameters[key])
        for key in ("target_key", "cadence")
        if parameters.get(key)
    }
    cadence = metadata.get("cadence")
    if cadence and schedule:
        next_due_at = monitor_next_due_at(
            generated_at=now_utc().isoformat(),
            cadence=cadence,
        )
        if next_due_at:
            metadata["next_due_at"] = next_due_at
    stop_condition = str(parameters.get("stop_condition") or "").strip()
    if stop_condition:
        if parse_timestamp(stop_condition) is not None:
            metadata["expires_at"] = stop_condition
        elif stop_condition.lower() in {
            "watch_only",
            "watch-only",
            "watch",
            "continuous",
            "never",
        }:
            metadata["watch_only"] = "true"
    return metadata


def _monitor_text(parameters: Mapping[str, object]) -> str | None:
    target = str(parameters.get("target") or "").strip()
    stop_condition = str(parameters.get("stop_condition") or "").strip()
    if target and stop_condition:
        return f"{target} (stop when {stop_condition})"
    if target:
        return target
    if stop_condition:
        return f"Monitor until {stop_condition}"
    return None


class ChatActionService(
    GoalObjectiveActionMixin,
    GoalAcceptanceActionMixin,
    ChatActionNormalizationMixin,
    ChatGoalLifecycleActionMixin,
    ChatMonitorActionMixin,
    ChatTodoActionMixin,
):
    """Validate previews and route applies through canonical LoopX services."""

    def __init__(
        self,
        *,
        store: ChatActionStore,
        registry_path: Path,
        chat_store: ChatSessionStore | None = None,
        runtime_controller: _RuntimeController | None = None,
        workspace_roots: Sequence[Path] = (),
        runtime_root_override: str | Path | None = None,
    ) -> None:
        self.store = store
        self.registry_path = Path(registry_path).expanduser().resolve()
        self.chat_store = chat_store
        self.runtime_controller = runtime_controller
        if runtime_controller is not None:
            setattr(runtime_controller, "original_task_action_store", store)
        # The runtime root the serving Chat selected (its --runtime-root). A
        # creation on a registry that does not exist yet has no recorded root
        # to follow, so the first Goal must persist where the service already
        # keeps its own state, never in an implicit default beside it.
        self.runtime_root_override = (
            str(runtime_root_override) if runtime_root_override else None
        )
        self.workspace_roots = tuple(
            Path(root).expanduser().resolve() for root in workspace_roots
        )

    def _turn_store(self) -> ChatSessionStore | None:
        if self.chat_store is not None:
            return self.chat_store
        raw_store = getattr(self.runtime_controller, "store", None)
        return raw_store if isinstance(raw_store, ChatSessionStore) else None

    def _stale_proposal(self, proposal_id: str, fingerprint: str) -> dict[str, object]:
        return {
            "proposal": self.store.apply(proposal_id, current_state_fingerprint=fingerprint, receipt={}),
            "turn": None,
        }

    def project_team_plan_preview(self, preview: Mapping[str, object]) -> dict[str, object]:
        """Offer an admitted steward team preview as the card a surface lists.

        An admitted preview is a validated proposal, not yet a confirmation: the
        product surfaces list *typed actions*, so the manager channel projects
        the preview it already admitted into exactly one `team.plan` proposal.
        The projection creates no work and grants nothing -- confirming that
        card is still the only apply, and the apply re-validates the same
        payload with the host's own facts. It is idempotent per plan, so a
        replayed Turn reuses the card instead of stacking a second one.
        """

        goal_id = str(preview.get("goal_id") or "")
        if not goal_id:
            raise ValueError("a team plan preview must name the Goal it staffs")
        lanes = preview.get("lanes")
        lane_count = len(lanes) if isinstance(lanes, Sequence) else 0
        return self.preview(
            {
                "action_kind": "team.plan",
                "summary": (
                    f"确认 {goal_id} 的 {lane_count} 条 lane 团队计划"
                    if lane_count
                    else f"确认 {goal_id} 的团队计划"
                ),
                # The card is read by the manager channel that produced it, and
                # it stays scoped to the one Goal the preview named.
                "context": {"kind": "manager", "goal_id": goal_id},
                "normalized_parameters": {
                    "goal_id": goal_id,
                    "plan": dict(preview),
                    "requested_by": "manager",
                },
                "idempotency_key": "team-plan:" + _digest(dict(preview)),
            }
        )

    def _registry(self) -> dict[str, object]:
        return load_registry(self.registry_path)

    def _goal(self, goal_id: str) -> dict[str, object]:
        goal = next(
            (
                goal
                for goal in registry_goals(self._registry())
                if str(goal.get("id") or "") == goal_id
            ),
            None,
        )
        if goal is None:
            raise ValueError("goal_id was not found in the active LoopX registry")
        return goal

    def _registry_fingerprint(self) -> str:
        """Identity of the registry a proposal was previewed against.

        A fresh install has no registry until its first Goal is created. That
        absence is a stable fact to fingerprint, so the first creation can be
        reviewed and confirmed from the page; a registry that appears between
        preview and apply still reads as a change and asks for a new review.
        """

        try:
            content = self.registry_path.read_bytes()
        except FileNotFoundError:
            return "registry-absent"
        except OSError as exc:
            raise ValueError("the active LoopX registry is unavailable") from exc
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _monitor_create_fingerprint(source: ChatTodoSource) -> str:
        return _digest({
            "source_identity": source.source_identity,
            "registry_sha256": source.registry_sha256,
        })

    def _creation_agent_eligibility(
        self, agent_id: str, *, project: Path | None = None, host_session: bool = False
    ) -> dict[str, object]:
        """Availability refusal for a Goal's first Agent becomes a typed gate.

        A fresh install often has no usable Agent yet. The owner who clicks
        "Create a Goal" needs the reason in their own language and the way to
        fix it, not a protocol sentence: the preview answers with a gate that
        names the Agent and, when the catalog states it, why it is unavailable.
        Nothing is written and nothing is granted; a working Agent still goes
        through the same eligibility check.

        ``host_session`` (``open_session=false``): the host that asked runs the
        Goal's conversation on its own copy of the Agent, so this core's endpoint
        for it need not be live; it must still be registered and able to run tools.
        """

        try:
            return self._agent_eligibility(agent_id, project=project, require_healthy=not host_session)
        except _AgentUnavailableError as exc:
            row = exc.capability
            gate: dict[str, object] = {
                "kind": "agent_unavailable",
                "agent_id": agent_id,
                "registered": row is not None,
                "summary": f"The selected Agent ({agent_id}) is not available to start this Goal.",
                "next_action": "Resolve the reported availability issue, then preview this request again.",
                "detail": str(exc),
            }
            if row is not None:
                for field in ("unavailable_reason", "executable", "environment_variable"):
                    value = row.get(field)
                    if isinstance(value, str) and value:
                        gate[field] = value
            raise ProtectedActionGate("goal.create", gate=gate) from exc

    def _agent_eligibility(
        self,
        agent_id: str,
        *,
        project: Path | None = None,
        requires_tools: bool = True,
        require_healthy: bool = True,
    ) -> dict[str, object]:
        if self.runtime_controller is None:
            return {
                "agent_id": agent_id,
                "available": True,
                "tool_calls": True,
                "trust_scope": "read_only",
                "source": "configuration_only",
            }
        capabilities = self.runtime_controller.capabilities()
        row = next(
            (
                item
                for item in capabilities
                if isinstance(item, Mapping)
                and str(item.get("agent_id") or "") == agent_id
            ),
            None,
        )
        if row is None:
            raise _AgentUnavailableError("selected Agent endpoint is not registered")
        if require_healthy and row.get("available") is not True:
            raise _AgentUnavailableError("selected Agent endpoint is not healthy", capability=row)
        if requires_tools and row.get("tool_calls") is not True:
            raise ValueError("selected Agent endpoint cannot execute Goal tools")
        if str(row.get("trust_scope") or "") not in {"read_only", "workspace_write"}:
            raise ValueError("selected Agent endpoint has an incompatible trust scope")
        endpoint_registry = getattr(self.runtime_controller, "endpoint_registry", None)
        endpoint = (
            endpoint_registry.get(agent_id) if endpoint_registry is not None else None
        )
        if (
            endpoint is not None
            and project is not None
            and endpoint.location == "remote"
        ):
            mapped = endpoint.mapped_work_dir(project)
            if mapped == project and not endpoint.workspace_mapping:
                raise ValueError("remote Agent endpoint needs a workspace mapping")
        return dict(row)

    @staticmethod
    def _agent_family(value: str) -> str:
        """Classify an id into a host family, bounded at a delimiter.

        A bare prefix match silently swallowed unrelated operator ids:
        ``kiroscope-worker`` and ``codexplorer`` are not the Kiro or Codex
        family, but they matched, and a single false match is enough for
        ``_resolve_goal_agent`` to bind a built-in endpoint to the wrong
        durable identity instead of raising ``agent_binding_required``. The
        family token must therefore be the whole id or end at a ``-``
        delimiter, which still accepts every documented shape (``kiro``,
        ``kiro-cli``, ``kiro-worker-1``) because ids are normalized first.
        """
        token = value.strip().lower().replace("_", "-")
        for prefix, family in _AGENT_FAMILY_ROOTS:
            if token == prefix or token.startswith(f"{prefix}-"):
                return family
        return token

    def _resolve_goal_agent(
        self, goal_id: str, endpoint_id: str, *, goal: dict[str, object] | None = None,
    ) -> str:
        """Resolve a runtime Endpoint to the Goal's durable peer identity."""

        self._agent_eligibility(endpoint_id)
        if goal is None:
            goal = self._goal(goal_id)
        registered = registered_agent_ids_for_goal(goal)
        if endpoint_id in registered:
            return endpoint_id
        endpoint_family = self._agent_family(endpoint_id)
        matches: list[str] = []
        for agent_id in registered:
            profile = agent_profile_for_goal(goal, agent_id) or {}
            aliases = {
                agent_id,
                str(profile.get("endpoint_id") or ""),
                str(profile.get("adapter_kind") or ""),
                str(profile.get("provider") or ""),
            }
            if any(
                self._agent_family(alias) == endpoint_family
                for alias in aliases
                if alias
            ):
                matches.append(agent_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ProtectedActionGate(
                "agent.bind",
                gate={
                    "kind": "agent_identity_selection_required",
                    "summary": "这个 Endpoint 对应多个 Goal Agent 身份，请先选择一个身份。",
                    "next_action": "在 Agent 设置中确认绑定身份后，再生成 Todo 预览。",
                    "endpoint_id": endpoint_id,
                    "candidate_agent_ids": matches,
                },
            )
        raise ProtectedActionGate(
            "agent.bind",
            gate={
                "kind": "agent_binding_required",
                "summary": "所选 Agent Endpoint 尚未绑定到这个 Goal。",
                "next_action": "先确认 Agent 绑定预览，再继续创建或改派 Todo。",
                "endpoint_id": endpoint_id,
                "goal_id": goal_id,
            },
        )

    def _goal_state_fingerprint(self, goal_id: str) -> str:
        basis = self._canonical_update_basis(goal_id)
        if basis is not None:
            return _digest({"goal_id": goal_id, "canonical_update_basis": basis})
        goal = self._goal(goal_id)
        project = Path(str(goal.get("repo") or "")).expanduser().resolve()
        state_file = Path(str(goal.get("state_file") or ""))
        if not state_file.is_absolute():
            state_file = project / state_file
        try:
            state_digest = hashlib.sha256(state_file.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError("the active Goal state is unavailable") from exc
        return _digest(
            {
                "registry": self._registry_fingerprint(),
                "goal_id": goal_id,
                "state": state_digest,
            }
        )

    @staticmethod
    def _allowed_parameters(
        parameters: Mapping[str, object],
        *,
        allowed: set[str],
    ) -> dict[str, object]:
        unknown = set(parameters) - allowed
        if unknown:
            raise ValueError(f"unknown typed action parameter: {sorted(unknown)[0]}")
        return {str(key): value for key, value in parameters.items()}

    def _session_fingerprint(self, session_id: str, goal_id: str) -> str:
        if self.chat_store is None:
            raise ValueError("Chat Session state is unavailable")
        session = self.chat_store.load_session(session_id)
        if session is None or session.get("status") == "closed":
            raise ValueError("chat session was not found")
        if str(session.get("goal_id") or "") != goal_id:
            raise ValueError("chat session does not belong to the selected Goal")
        return _digest(
            {
                "session_id": session_id,
                "goal_id": goal_id,
                "agent_id": session.get("agent_id"),
                "status": session.get("status"),
                "active_turn_id": session.get("active_turn_id"),
            }
        )

    def _project_for_goal_create(
        self, proposal: Mapping[str, object], *, progress: Mapping[str, object] | None = None
    ) -> tuple[Path, dict[str, object]]:
        parameters = proposal.get("normalized_parameters")
        context = proposal.get("context")
        if not isinstance(parameters, Mapping) or not isinstance(context, Mapping):
            raise ValueError("typed Chat action proposal is malformed")
        workspace_ref = str(parameters.get("workspace_ref") or "current")
        if context.get("binding_id"):
            # The native steward freezes its configured workspace. Existing
            # Goals and the single-Goal fallback cannot redirect a commission.
            steward_roots = [
                root for root in self.workspace_roots
                if root.is_dir() and (root / ".git").exists() and workspace_ref == goal_workspace_ref(root)
            ]
            if len(steward_roots) != 1:
                raise ValueError("the bound steward workspace is unavailable")
            return steward_roots[0], {
                "id": "", "repo": str(steward_roots[0]), "domain": "project-goal-control-plane",
                "adapter": {"kind": "generic_project_goal_v0"},
            }
        goals = registry_goals(self._registry())
        # Resume from server-owned identity, before mutable single-Goal routing.
        # No caller path or a different creation operation can redirect it.
        if progress:
            existing = next((goal for goal in goals if goal.get("id") == parameters.get("goal_id")), None)
            if existing is not None and existing.get("creation_operation_id") == proposal.get("proposal_id"):
                project = Path(str(existing.get("repo") or "")).expanduser().resolve()
                if not project.is_dir() or _digest(str(project)) != progress["workspaceDigest"]:
                    raise ValueError("The original creation workspace is unavailable or changed; restore it before retrying")
                source = next((goal for goal in goals if goal.get("id") == existing.get("parent_goal_id")), existing)
                return project, source
        # Explicit destinations outrank conversation context and legacy fallback.
        # A missing/stale choice must ask again, never silently select another root.
        context_goal_id = str(context.get("goal_id") or "").strip()
        source_id = context_goal_id if workspace_ref == "current" else workspace_ref
        source_goal = next((goal for goal in goals if str(goal.get("id") or "") == source_id), None)
        if source_goal is None and workspace_ref == "current" and len(goals) == 1:
            source_goal = goals[0]
        if source_goal is None:
            workspace_candidates = [root for root in self.workspace_roots if root.is_dir()]
            token_map = {goal_workspace_ref(root): root for root in workspace_candidates}
            selected = (
                workspace_candidates[0]
                if workspace_ref == "current" and len(workspace_candidates) == 1
                else token_map.get(workspace_ref)
            )
            if selected is not None:
                return selected, {
                    "id": "", "domain": "project-goal-control-plane", "repo": str(selected),
                    "adapter": {"kind": "generic_project_goal_v0"},
                }
            available = [root for root in self.workspace_roots if root.is_dir()]
            labels = self._workspace_labels_for(available)
            gate_candidates = [{"workspace_ref": goal_workspace_ref(root), "label": labels[root]} for root in available]
            raise ProtectedActionGate(
                "goal.create",
                gate={
                    "kind": "workspace_selection_required",
                    "summary": "Select one server-configured workspace before creating this Goal.",
                    "next_action": (
                        "Choose an available workspace or a registered Goal workspace, then review again. "
                        "To use another directory, restart the Dashboard with `loopx dashboard --scan-path <directory>`, once per project."
                    ),
                    "candidates": gate_candidates,
                },
            )
        if workspace_ref not in {"current", str(source_goal.get("id") or "")}:
            raise ValueError("workspace_ref must be current or an existing Goal id")
        project = Path(str(source_goal.get("repo") or "")).expanduser().resolve()
        if not project.is_dir():
            raise ProtectedActionGate(
                "goal.create",
                gate={
                    "kind": "workspace_unavailable",
                    "summary": "The selected server-side Goal workspace is unavailable.",
                    "next_action": "Restore the registered workspace and regenerate this preview.",
                },
            )
        return project, source_goal

    def _workspace_labels_for(self, available: Sequence[Path]) -> dict[Path, str]:
        directories = list(available)
        for goal in registry_goals(self._registry()):
            repo = str(goal.get("repo") or "").strip()
            if repo:
                directories.append(Path(repo).expanduser().resolve())
        return goal_workspace_labels(directories)

    def workspace_labels(self) -> dict[Path, str]:
        """Names for every directory a Goal creation can target.

        Configured roots and registered Goal workspaces are named together, so
        two choices never read the same in the form, a gate or a review.
        """

        return self._workspace_labels_for([root for root in self.workspace_roots if root.is_dir()])

    def _goal_workspace_binding(
        self, project: Path, source_goal: Mapping[str, object], *, requested_ref: str
    ) -> dict[str, object]:
        """The workspace a confirmed creation will use, as the owner reviews it.

        ``workspace_ref`` is the explicit reference apply resolves, so a
        default request is confirmed against the workspace it resolved to.
        """

        goal_id = str(source_goal.get("id") or "")
        label = self.workspace_labels().get(project) or project.name
        return {
            "schema_version": "loopx_goal_workspace_binding_v0",
            "workspace_ref": goal_id or goal_workspace_ref(project),
            "label": label,
            "source": "goal_workspace" if goal_id else "configured_workspace",
            "selection": "default" if requested_ref == "current" else "explicit",
            **({"goal_id": goal_id} if goal_id else {}),
        }

    def _apply_goal_create(
        self, proposal_id: str, proposal: dict[str, object], parameters: dict[str, object]
    ) -> dict[str, object]:
        from .control_plane.effect_runtime import effect_runtime_result

        def require_first_turn_readback(turn: Mapping[str, object]) -> None:
            plan = effect_runtime_result("presentation.action_review_plan.compile", {
                "proposal": {**proposal, "first_turn_readback": turn},
            })
            if plan.get("reason") == "readback_unverified":
                raise ValueError("The original first Turn ended before Host dispatch; inspect its original Session instead of starting another")

        current_fingerprint = self._registry_fingerprint()
        heartbeat = _as_dict(parameters.get("heartbeat"))
        goal_id = str(parameters["goal_id"])
        existing_goal = next(
            (goal for goal in registry_goals(self._registry()) if str(goal.get("id") or "") == goal_id),
            None,
        )
        creation_plan = effect_runtime_result("presentation.action_review_plan.compile", {"proposal": proposal})
        progress = _as_dict(creation_plan.get("creationProgress"))
        checkpoint_steps = _as_dict(_as_dict(proposal.get("checkpoint")).get("steps"))
        if checkpoint_steps and not progress:
            raise ValueError("Goal creation checkpoints do not match the original request; inspect the recorded operation")
        project, source_goal = self._project_for_goal_create(proposal, progress=progress)
        workspace_digest = _digest(str(project))
        conflict = ProtectedActionGate(
            "goal.create",
            gate={
                "kind": "goal_id_conflict",
                "summary": "The Goal id is not bound to this creation operation and workspace.",
                "next_action": "Inspect the existing Goal; choose another Goal id for a new creation.",
            },
        )
        if existing_goal is not None:
            workspace_receipt = _as_dict(checkpoint_steps.get("workspace_validated"))
            if (
                Path(str(existing_goal.get("repo") or "")).expanduser().resolve() != project
                or existing_goal.get("creation_operation_id") != proposal_id
                or workspace_receipt.get("workspace_digest") != workspace_digest
            ):
                raise conflict
        elif current_fingerprint != proposal.get("expected_state_fingerprint"):
            return self._stale_proposal(proposal_id, current_fingerprint)
        self._agent_eligibility(
            str(parameters.get("agent_id") or "codex"),
            project=project,
            require_healthy=parameters.get("open_session") is not False,
        )
        self.store.save_checkpoint(
            proposal_id,
            step="workspace_validated",
            receipt={
                "outcome": "workspace_validated",
                "workspace_ref": str(parameters.get("workspace_ref") or "current"),
                "workspace_digest": workspace_digest,
            },
        )
        runtime_root = resolve_runtime_root(
            self._registry(), self.runtime_root_override, registry_path=self.registry_path
        ).resolve()
        objective = str(parameters.get("objective") or parameters["title"])
        if existing_goal is not None:
            # Registry publication precedes storage initialization. Resume the
            # frozen target through its TS owner before any downstream effects;
            # re-running Markdown bootstrap could overwrite a promoted Goal.
            initialize_goal_storage_target(runtime_root, existing_goal, registry_path=self.registry_path)
            result: dict[str, object] = {"ok": True}
        else:
            try:
                from .bootstrap import bootstrap_project

                result = bootstrap_project(
                    project=project, creation_operation_id=proposal_id, registry_path=self.registry_path,
                    runtime_root=runtime_root, goal_id=goal_id, objective=objective,
                    domain=str(source_goal.get("domain") or "project-goal-control-plane"),
                    role="primary", parent_goal_id=str(source_goal.get("id") or "") or None,
                    state_file=None, goal_doc=None,
                    adapter_kind=str(_as_dict(source_goal.get("adapter")).get("kind") or "generic_project_goal_v0"),
                    adapter_status="connected", display_name=str(parameters.get("title") or "").strip() or None,
                    next_probe=None, spawn_allowed=False, max_children=0, allowed_domains=[],
                    write_scope=[], preserve_todos=True, force=False, dry_run=False, sync_global=False,
                )
            except GoalCreationConflictError as exc:
                raise conflict from exc
        if not result.get("ok"):
            raise ProtectedActionGate(
                "goal.create",
                gate={
                    "kind": "goal_bootstrap_blocked",
                    "summary": "Canonical Goal bootstrap did not complete.",
                    "next_action": "Repair the project registry write path and retry this preview.",
                },
            )
        self.store.save_checkpoint(proposal_id, step="goal_bootstrapped", receipt={"outcome": "goal_bootstrapped", "goal_id": goal_id})
        agent_id = str(parameters.get("agent_id") or "").strip()
        if agent_id and not progress.get("agentId"):
            configure_goal(registry_path=self.registry_path, goal_id=goal_id, registered_agents=[agent_id], execute=True)
            self.store.save_checkpoint(
                proposal_id, step="agent_bound", receipt={"outcome": "agent_bound", "goal_id": goal_id, "agent_id": agent_id}
            )
        raw_todo_ids = progress.get("todoIds")
        todo_ids: list[str] = [str(item) for item in raw_todo_ids] if isinstance(raw_todo_ids, Sequence) and not isinstance(raw_todo_ids, (str, bytes)) else []
        creation_execution = _as_dict(creation_plan.get("creationExecution"))
        if creation_execution:
            from .chat.goal_creation_execution import retain_creation_write_approval
            retain_creation_write_approval(self, proposal_id, creation_execution)
        # Canonical File/SQLite own replay after a committed write loses its
        # response. Historical target-only creation retains its old adapter.
        raw_initial = parameters.get("initial_todos")
        initial_todos = list(raw_initial) if isinstance(raw_initial, Sequence) and not isinstance(raw_initial, (str, bytes)) else []
        pending_todos = initial_todos if "todoIds" not in progress else []
        authority = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id) if pending_todos else None
        raw_write_scope = creation_execution.get("writeScope")
        write_scopes = [str(item) for item in raw_write_scope] if isinstance(raw_write_scope, Sequence) and not isinstance(raw_write_scope, (str, bytes)) else None
        for index, todo_text in enumerate(pending_todos):
            todo = add_goal_todo(
                registry_path=self.registry_path, goal_id=goal_id, role="agent", text=str(todo_text),
                task_class="advancement_task", action_kind="advance",
                claimed_by=agent_id or None, agent_id=agent_id or None, dry_run=False,
                operation_id=f"goal-create:{proposal_id}:todo:{index}" if authority is not None else None,
                required_write_scopes=write_scopes or None,
            )
            if todo.get("todo_id"):
                todo_ids.append(str(todo["todo_id"]))
        self.store.save_checkpoint(proposal_id, step="todos_created", receipt={"outcome": "todos_created", "todo_ids": todo_ids})
        completion_plan = effect_runtime_result("presentation.action_review_plan.compile", {"proposal": self.store.load(proposal_id)})
        completion = _as_dict(completion_plan.get("creationOutcome"))
        completion_progress = _as_dict(completion_plan.get("creationProgress"))
        if completion and not completion_progress.get("completionRecorded"):
            from .control_plane.goals.acceptance import inspect_goal_acceptance, refine_goal_outcome

            basis = _as_dict(completion_progress.get("completionBasis"))
            if not basis:
                current = inspect_goal_acceptance(
                    registry_path=self.registry_path, runtime_root=str(runtime_root), goal_id=goal_id, agent_id=agent_id
                )
                basis = {"providerRevision": current["provider_revision"], "operationId": completion["operationId"]}
                # Persist the original CAS before sending the canonical write.
                # An uncertain response must replay this request, not new intent.
                self.store.save_checkpoint(proposal_id, step="goal_completion_prepared", receipt={
                    "outcome": "goal_completion_prepared", "goal_id": goal_id,
                    "operation_id": basis["operationId"], "provider_revision": basis["providerRevision"],
                })
            retained = refine_goal_outcome(
                registry_path=self.registry_path, runtime_root=str(runtime_root),
                goal_id=goal_id, agent_id=agent_id, refinement=_as_dict(completion["refinement"]),
                operation_id=str(basis["operationId"]), expected_provider_revision=str(basis["providerRevision"]), execute=True,
            )
            self.store.save_checkpoint(proposal_id, step="goal_completion_recorded", receipt={
                "outcome": "goal_completion_recorded", "goal_id": goal_id,
                "operation_id": basis["operationId"], "provider_revision": retained["provider_revision"],
            })
        projected = self._goal(goal_id)
        if agent_id and agent_id not in registered_agent_ids_for_goal(projected):
            raise ValueError("Goal projection did not retain the selected Agent binding")
        from functools import partial
        from .chat.session_open import read_goal_creation_session, recover_goal_creation_session, require_regular_session_paths, goal_creation_display_message

        turn_store = self._turn_store()
        raw_goal_instance_id = projected.get("goal_instance_id")
        read_original_session = partial(
            read_goal_creation_session, self.store, turn_store,
            proposal_id=proposal_id, goal_id=goal_id, agent_id=agent_id,
            workspace=project, goal_instance_id=str(raw_goal_instance_id) if raw_goal_instance_id is not None else None,
        )

        turn_result: dict[str, object] | None = None
        session_id = ""
        # A caller that owns its conversation layer opens the first Session itself.
        open_session = parameters.get("open_session") is not False
        first_turn_message = (
            f"/goal start --tokens {parameters['native_token_budget']} {objective}"
            if parameters.get("native_token_budget")
            else (
                f"开始推进 Goal {goal_id}。先核对目标边界和现有 Todo，"
                f"首个 Todo：{'；'.join(str(item) for item in initial_todos[:3]) or '按目标边界建立首个可验证进展'}。"
                "然后直接推进并报告可验证结果；遇到权限边界时停止并提出明确 Gate。"
            )
        )
        opening_session_id = _receipt_id({"goal_create": proposal_id, "goal_id": goal_id})
        managed_agent_raw = _as_dict(creation_plan.get("creationSession")).get("managedAgentId")
        expected_managed_agent = str(managed_agent_raw) if managed_agent_raw else None
        first_turn_info = _as_dict(progress.get("firstTurn"))
        progress_session_id = str(progress.get("sessionId") or "")
        if open_session and not first_turn_info and not progress_session_id and turn_store is not None:
            require_regular_session_paths(turn_store, opening_session_id)
            original = turn_store.load_session(opening_session_id)
            if original is not None:
                read_original_session(session_id=opening_session_id, expected_managed_agent=expected_managed_agent)
            elif turn_store._session_dir(opening_session_id).exists():
                raise ValueError("The original first Session is unreadable; restore it before retrying")
            elif existing_goal is not None:
                # Historical opening responses carried no operation-bound id.
                # Recover only an unambiguous original; never choose latest.
                original = recover_goal_creation_session(
                    turn_store, client_turn_id=f"goal-start-{proposal_id}", goal_id=goal_id, agent_id=agent_id
                )
            if original is not None:
                read_original_session(session_id=original["session_id"])
                progress_session_id = str(original["session_id"])
        session: dict[str, object] = {}
        if not open_session:
            pass
        elif first_turn_info:
            session_id = str(first_turn_info["sessionId"])
            turn_id = str(first_turn_info["turnId"])
            session = read_original_session(session_id=session_id)
            turn = turn_store.load_turn(session_id, turn_id) if turn_store is not None else None
            if turn is None:
                raise ValueError("The recorded first Turn is unavailable; restore its original Session instead of starting another")
            if turn.get("message") != first_turn_message or turn.get("client_turn_id") != f"goal-start-{proposal_id}":
                raise ValueError("The original first Turn request changed; retain its recorded operation")
            require_first_turn_readback(turn)
            turn_result = {"turn_id": turn_id, "status": str(turn["status"]), "created": False}
        elif progress_session_id:
            if turn_store is None:
                raise ValueError("The original first Session is unavailable; restore it before retrying")
            original_session = read_original_session(session_id=progress_session_id)
            recovered_session, turn = self._read_task_start(
                turn_store, goal_id=goal_id, agent_id=agent_id, channel_id=original_session["channel_id"],
                client_turn_id=f"goal-start-{proposal_id}", checkpoint={"session_id": progress_session_id},
            )
            if recovered_session is not None:
                session = recovered_session
            if turn is not None:
                if turn.get("message") != first_turn_message:
                    raise ValueError("The original first Turn request changed; retain its recorded operation")
                require_first_turn_readback(turn)
                if turn.get("status") in TERMINAL_TURN_STATES or turn.get("started_at") or turn.get("upstream_turn_id"):
                    session_id = str(session["session_id"])
                    turn_result = {"turn_id": turn["turn_id"], "status": str(turn["status"]), "created": False}
                    self.store.save_checkpoint(proposal_id, step="first_turn_started", receipt={
                        "outcome": "first_turn_started", "session_id": session_id, "turn_id": turn["turn_id"],
                    })
        first_turn_gate: dict[str, object] | None = None
        if open_session and agent_id and self.runtime_controller is not None and turn_result is None:
            try:
                from .status import collect_status

                caps = self.runtime_controller.capabilities()
                status_payload = collect_status(
                    registry_path=self.registry_path,
                    runtime_root_override=str(runtime_root) if runtime_root else None,
                    limit=5, goal_id=goal_id, available_capabilities=caps,
                )
                guard = build_quota_should_run(status_payload, goal_id=goal_id, agent_id=agent_id or None, available_capabilities=caps)
            except Exception as exc:
                raise ValueError(
                    "The first Turn's Goal admission could not be verified. "
                    "Retain this creation and retry when its admission source is available."
                ) from exc
            if guard.get("should_run") is not True:
                first_turn_gate = {
                    "kind": "goal_quota_gate",
                    "summary": str(guard.get("reason") or "The new Goal is not eligible for its first Agent Turn.")[:600],
                    "next_action": str(guard.get("recommended_action") or "Resolve the projected Goal Gate and retry the first Turn.")[:600],
                    "quota_state": str(guard.get("state") or "waiting"),
                }
        if open_session and agent_id and self.runtime_controller is not None and first_turn_gate is None and turn_result is None:
            if progress_session_id:
                session = read_original_session(session_id=progress_session_id)
            else:
                session, _resumed = self.runtime_controller.open_session(
                    goal_id=goal_id,
                    goal_instance_id=str(projected["goal_instance_id"]) if projected.get("goal_instance_id") else None,
                    agent_id=agent_id, work_dir=project, objective=objective, mode="new",
                    session_id=opening_session_id, channel_id=f"goal.{goal_id}", agent_goal_id=goal_id,
                    **({"managed_agent_id": expected_managed_agent} if expected_managed_agent else {}),
                )
            session_id = _opaque(session.get("session_id"), field="session_id")
            if not progress_session_id and session_id != opening_session_id:
                raise ValueError("The original first Session opening returned another operation")
            session = read_original_session(
                session_id=session_id, expected_managed_agent=expected_managed_agent if not progress_session_id else None,
            )
            self.store.save_checkpoint(proposal_id, step="first_session_opened", receipt={
                "outcome": "first_session_opened", "session_id": session_id, "channel_id": session["channel_id"],
                **({"managed_agent_id": session["managed_agent_id"], "goal_instance_id": session.get("goal_instance_id")}
                   if session.get("managed_agent_id") is not None else {}),
                **({"upstream_thread_id": session.get("upstream_thread_id")} if creation_execution else {}),
            })
            first_turn, created = self.runtime_controller.submit_turn(
                session_id=session_id, client_turn_id=f"goal-start-{proposal_id}",
                message=first_turn_message, work_dir=project, objective=objective,
                **({"display_message": goal_creation_display_message(
                    turn_store, session_id=session_id, client_turn_id=f"goal-start-{proposal_id}", objective=objective
                )} if expected_managed_agent and turn_store is not None else {}),
            )
            require_first_turn_readback(first_turn)
            turn_result = {
                "turn_id": _opaque(first_turn.get("turn_id"), field="turn_id"),
                "status": str(first_turn.get("status") or "queued"),
                "created": created,
            }
            self.store.save_checkpoint(proposal_id, step="first_turn_started", receipt={
                "outcome": "first_turn_started", "session_id": session_id, "turn_id": turn_result["turn_id"],
            })
        elif first_turn_gate is not None:
            self.store.save_checkpoint(proposal_id, step="first_turn_gated", receipt={"outcome": "first_turn_gated", "gate": first_turn_gate})
            if creation_execution:
                # An applied receipt is immutable. Keep this confirmed start
                # retryable until its original Session/request is retained;
                # quota denial cannot silently discard the requested work.
                raise ValueError(
                    "The original Goal and Task are retained, but their first Turn admission is held. "
                    + str(first_turn_gate["summary"])
                    + " Retry this same creation when its allowance is ready."
                )
        if session_id:
            session = read_original_session(session_id=session_id)
        original_task_request = None
        if session_id and parameters.get("start_execution") is True:
            from .chat.goal_creation_execution import retain_original_task_request
            original_task_request = retain_original_task_request(self, proposal_id, session)
        child_gate: dict[str, object] | None = first_turn_gate
        if heartbeat.get("enabled") is True:
            heartbeat_parameters: dict[str, object] = {
                "goal_id": goal_id, "agent_id": agent_id or "codex", "operation": "bind",
                "cadence": str(heartbeat.get("cadence") or "1d"), "timezone": str(heartbeat.get("timezone") or "UTC"),
                "stop_condition": str(parameters.get("stop_condition") or "goal_complete"),
            }
            child_gate = self._heartbeat_gate(heartbeat_parameters).gate
            self.store.save_checkpoint(proposal_id, step="heartbeat_gate_ready", receipt={"outcome": "heartbeat_gate_ready", "gate": child_gate})
        resource_ids: dict[str, object] = {
            "goal_id": goal_id,
            **({"goal_instance_id": str(projected["goal_instance_id"])} if projected.get("goal_instance_id") else {}),
            **({"creation_operation_id": str(projected["creation_operation_id"])} if projected.get("creation_operation_id") else {}),
            **({"agent_id": agent_id} if agent_id else {}),
            **({"todo_ids": todo_ids} if todo_ids else {}),
            **({"session_id": session_id} if session_id else {}),
            **({"managed_agent_id": session["managed_agent_id"], "channel_id": session["channel_id"],
                "goal_instance_id": session.get("goal_instance_id")}
               if session_id and session.get("managed_agent_id") is not None else {}),
            **({"turn_id": turn_result["turn_id"]} if turn_result else {}),
            # Without an opened Session the caller still needs the managed Agent's address.
            **({"managed_agent_id": expected_managed_agent, "channel_id": f"agent.{expected_managed_agent}",
                "goal_instance_id": projected.get("goal_instance_id")}
               if not session_id and not open_session and expected_managed_agent else {}),
        }
        receipt: dict[str, object] = {
            "receipt_id": _receipt_id({"proposal_id": proposal_id, "goal_id": goal_id}),
            "outcome": "goal_created",
            "projection_verified": True,
            "resource_ids": resource_ids,
            **({"child_gate": child_gate} if child_gate else {}),
            "step_receipts": _as_dict(_as_dict(self.store.load(proposal_id)).get("checkpoint")).get("steps", {}),
            **({"original_task_request": original_task_request} if original_task_request is not None else {}),
        }
        stored = self.store.apply(proposal_id, current_state_fingerprint=str(proposal["expected_state_fingerprint"]), receipt=receipt)
        if original_task_request is not None and self.runtime_controller is not None:
            self.runtime_controller.resume_session_queue(session_id=session_id, work_dir=project, objective=objective)
        return {"proposal": stored, "turn": turn_result, "gate": child_gate}

    def _apply_agent_bind(
        self, proposal_id: str, proposal: dict[str, object], parameters: dict[str, object]
    ) -> dict[str, object]:
        goal_id = str(parameters["goal_id"])
        agent_id = str(parameters["agent_id"])
        expected_revision = str(proposal["expected_state_fingerprint"])
        result = bind_goal_agent_with_global_sync(
            registry_path=self.registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            expected_revision=expected_revision,
        )
        if result.get("status") == "stale":
            return self._stale_proposal(proposal_id, str(result["actual_revision"]))
        if not result.get("ok") or not result.get("projection_verified"):
            raise ValueError(str(result.get("error") or "Agent binding did not verify in source and shared registries"))
        receipt: dict[str, object] = {
            "receipt_id": _receipt_id({"proposal_id": proposal_id, "goal_id": goal_id, "agent_id": agent_id}),
            "outcome": "agent_bound" if result.get("status") == "bound" else "agent_already_bound",
            "projection_verified": True,
            "resource_ids": {"goal_id": goal_id, "agent_id": agent_id},
        }
        return {"proposal": self.store.apply(proposal_id, current_state_fingerprint=expected_revision, receipt=receipt), "turn": None}

    def _heartbeat_gate(self, parameters: dict[str, object]) -> ProtectedActionGate:
        goal_id = str(parameters["goal_id"])
        agent_id = str(parameters["agent_id"])
        registered = registered_agent_ids_for_goal(self._goal(goal_id))
        if agent_id not in registered:
            raise ValueError("heartbeat Agent must be registered for the Goal")
        packet = build_host_loop_activation_packet(
            agent_type="codex-app",
            goal_id=goal_id,
            agent_id=agent_id,
            registered_agents=registered,
        )
        operation = str(parameters.get("operation") or "bind")
        gate_receipt = _receipt_id({"goal_id": goal_id, "agent_id": agent_id, "operation": operation, "packet": packet})
        return ProtectedActionGate(
            "heartbeat.bind",
            gate={
                "kind": "host_activation_required",
                "summary": "The Codex App host owns heartbeat automation creation.",
                "next_action": "Use automation_update with the canonical activation packet, then verify the installed automation before retrying.",
                "operation": operation,
                "gate_receipt": gate_receipt,
                "desired_configuration": {
                    key: parameters[key]
                    for key in ("cadence", "timezone", "stop_condition", "notification_policy")
                    if parameters.get(key) is not None
                },
                "activation_packet": packet,
            },
        )

    def _apply_monitor_create(
        self, proposal_id: str, proposal: dict[str, object], parameters: dict[str, object]
    ) -> dict[str, object]:
        goal_id = str(parameters["goal_id"])
        source = resolve_chat_todo_source(self, goal_id)
        current_fingerprint = self._monitor_create_fingerprint(source)
        if current_fingerprint != proposal.get("expected_state_fingerprint"):
            return self._stale_proposal(proposal_id, current_fingerprint)
        agent_id = str(parameters["agent_id"])
        if agent_id not in registered_agent_ids_for_goal(source.goal):
            raise ValueError("monitor Agent must be registered for the Goal")
        authority = read_canonical_todos_if_promoted(runtime_root=source.runtime_root, goal_id=goal_id)
        revision = authority.get("provider_revision") if authority else None
        if not isinstance(revision, str) or not revision:
            raise ValueError("The Goal's Todo authority revision is unavailable")
        stop_condition = str(parameters.get("stop_condition") or "").strip()
        resume_when = (
            stop_condition
            if stop_condition and parse_timestamp(stop_condition) is None
            and stop_condition.lower() not in {"watch_only", "watch-only", "watch", "continuous", "never"}
            else None
        )
        metadata = _monitor_metadata(parameters, schedule=False)
        if not (metadata.get("expires_at") or resume_when or metadata.get("watch_only")):
            metadata["watch_only"] = "true"
        try:
            result = add_goal_todo(
                **source.writer_scope(),
                routing_registry_witness=source.routing_registry_witness,
                expected_registry_sha256=source.registry_sha256,
                expected_provider_revision=revision,
                goal_id=goal_id,
                role="agent",
                text=_monitor_text(parameters) or str(parameters["target"]),
                task_class="continuous_monitor",
                action_kind="observe",
                task_repository=(
                    str(parameters["task_repository"])
                    if parameters.get("task_repository")
                    else None
                ),
                claimed_by=agent_id,
                agent_id=agent_id,
                resume_when=resume_when,
                monitor_metadata=metadata,
                dry_run=False,
            )
        except LocalCoordinationAuthorityUnavailable as error:
            if error.code not in {"provider_revision_mismatch", "authority_source_changed"}:
                raise
            return self._stale_proposal(
                proposal_id,
                _digest({"conflict": error.code, "expected": current_fingerprint}),
            )
        todo_id = _opaque(result.get("todo_id"), field="todo_id")
        signal_scope = parameters.get("signal_scope")
        if signal_scope is not None:
            if not isinstance(signal_scope, Mapping):
                raise ValueError("signal_scope must be a mapping")
            from .capabilities.native_chat.steward_monitor import write_steward_monitor_scope
            target_key = str(parameters.get("target_key") or "").strip()
            if not target_key:
                raise ValueError("signal_scope requires target_key")
            write_steward_monitor_scope(source.runtime_root, goal_id, target_key, _as_dict(signal_scope))
        receipt: dict[str, object] = {
            "receipt_id": _receipt_id({"proposal_id": proposal_id, "goal_id": goal_id, "todo_id": todo_id}),
            "outcome": "monitor_already_exists" if result.get("already_exists") else "monitor_created",
            "projection_verified": True,
            "resource_ids": {"goal_id": goal_id, "todo_id": todo_id, "agent_id": agent_id},
        }
        return {"proposal": self.store.apply(proposal_id, current_state_fingerprint=current_fingerprint, receipt=receipt), "turn": None}

    def _apply_team_plan(
        self, proposal_id: str, proposal: dict[str, object], parameters: dict[str, object]
    ) -> dict[str, object]:
        """Create each ready lane's first bounded Todo through the Todo owner."""

        from .control_plane.work_items.governed_transition_proposal import (
            steward_team_plan_basis_agent,
        )
        from .control_plane.work_items.team_plan_adapter import settle_team_plan

        goal_id = str(parameters["goal_id"])
        plan = parameters.get("plan")
        if not isinstance(plan, Mapping):
            raise ValueError("team plan proposal is malformed")
        expected = str(proposal.get("expected_state_fingerprint") or "")
        try:
            settlement = settle_team_plan(
                registry_path=self.registry_path, goal_id=goal_id,
                agent_id=None, proposal={**dict(plan), "proposal_id": proposal_id},
                expected_state_fingerprint=expected,
                basis_agent_id=steward_team_plan_basis_agent(plan),
            )
        except (OSError, ValueError, RuntimeError) as error:
            error_code = getattr(error, "diagnostic_code", None) or getattr(error, "code", None)
            observed = getattr(error, "current_fingerprint", None)
            if error_code == "team_plan_preview_stale" and observed and observed != expected:
                return self._stale_proposal(proposal_id, str(observed))
            if error_code == "team_plan_no_staffable_lane":
                # The typed owner refused a plan whose every lane is a gap:
                # confirming it could only create nothing, so the outcome is
                # this failure and the plan's lanes and reasons stay in the
                # card the owner confirmed.
                return {"proposal": self.store.mark_failed(proposal_id,
                    error_code="team_plan_no_staffable_lane",
                    message=(f"none of the plan's {len(plan.get('lanes') or [])} lane(s) "
                             "can be staffed by this host, so confirming it created no work")),
                    "turn": None}
            return {"proposal": self.store.mark_failed(proposal_id,
                error_code=("team_plan_projection_pending" if error_code == "team_plan_projection_pending" else "team_plan_commit_failed"),
                message=("Tasks committed; display readback is pending. Retry this same plan to recover the result."
                         if error_code == "team_plan_projection_pending" else
                         "The batch could not be confirmed. Retry this same plan to recover its commit result.")),
                "turn": None}
        current_fingerprint = expected
        lane_todo_ids = [str(item) for item in (settlement.get("lane_todo_ids") or [])]
        intent_basis = str(settlement.get("intent_basis") or "")
        gap_count = int(settlement.get("gap_count") or 0)
        # Recovery describes this attempt; original staffing gaps remain in the
        # receipt so readback never implies that retry created the missing work.
        if str(settlement.get("action") or "") == "reused":
            outcome = "team_plan_commit_recovered"
        elif gap_count:
            outcome = "team_plan_partially_applied"
        else:
            outcome = "team_plan_applied"
        receipt: dict[str, object] = {
            "receipt_id": _receipt_id({"proposal_id": proposal_id, "goal_id": goal_id, "lane_todo_ids": lane_todo_ids}),
            "outcome": outcome,
            "projection_verified": True,
            "resource_ids": {
                "goal_id": goal_id,
                "todo_id": str(settlement.get("todo_id") or ""),
                "lane_todo_ids": lane_todo_ids,
            },
        }
        lane_settlements = settlement.get("lane_settlements")
        if lane_settlements:
            # Which lane each created Todo is, who runs it, the priority it
            # carries and the acceptance it was confirmed to end on, so the
            # owner's readback still names the commitment and not just the work.
            receipt["lanes"] = [dict(item) for item in lane_settlements]
        if gap_count:
            receipt["gap_count"] = gap_count
            receipt["gap_lanes"] = settlement.get("gap_lanes", [])
        if intent_basis:
            # The canonical revision these lanes were created against, so the
            # owner's readback can name what the work advances.
            receipt["intent_basis"] = intent_basis
        return {"proposal": self.store.apply(proposal_id, current_state_fingerprint=current_fingerprint, receipt=receipt), "turn": None}

    def _basis_or_goal_fingerprint(self, goal_id: str, basis: object) -> str:
        return (
            _digest({"goal_id": goal_id, "canonical_update_basis": basis})
            if basis is not None
            else self._goal_state_fingerprint(goal_id)
        )

    def preview(self, request: Mapping[str, object]) -> dict[str, object]:
        unknown = set(request) - {
            "action_kind",
            "summary",
            "normalized_parameters",
            "context",
            "idempotency_key",
        }
        if unknown:
            raise ValueError(f"unknown typed action field: {sorted(unknown)[0]}")
        action_kind = str(request.get("action_kind") or "").strip()
        if action_kind not in SUPPORTED_ACTION_KINDS:
            raise ValueError(f"unsupported action_kind: {action_kind or '<empty>'}")
        parameters = request.get("normalized_parameters")
        context = request.get("context")
        if not isinstance(parameters, Mapping) or not isinstance(context, Mapping):
            raise ValueError("normalized_parameters and context must be objects")
        normalized = self._normalize(action_kind, parameters)
        if action_kind == "goal.update" and "reason" in normalized:
            return self._prepare_objective_preview(
                parameters=normalized, context=context, summary=request.get("summary"),
                idempotency_key=_opaque(request.get("idempotency_key"), field="idempotency_key"),
            )
        canonical_update_basis = None
        workspace_binding = None
        if action_kind == "goal.create":
            project, source_goal = self._project_for_goal_create(
                {
                    "normalized_parameters": normalized,
                    "context": dict(context),
                }
            )
            agent_id = str(normalized.get("agent_id") or "codex")
            eligibility = self._creation_agent_eligibility(
                agent_id, project=project, host_session=normalized.get("open_session") is False
            )
            workspace_binding = self._goal_workspace_binding(
                project, source_goal, requested_ref=str(normalized.get("workspace_ref") or "current")
            )
            normalized["workspace_ref"] = workspace_binding["workspace_ref"]
        else:
            eligibility = None
        if action_kind == "goal.acceptance.check":
            if context.get("kind") != "goal" or context.get("goal_id") != normalized["goal_id"]:
                raise ValueError("check plan context must match its target Goal")
            fingerprint, preview = self._acceptance_preview(normalized)
            if "resolved_document" in preview:
                # Owner-produced review snapshot only; apply re-resolves the
                # original reference against the native reviewed-context digest.
                normalized["resolved_document"] = preview["resolved_document"]
            evidence = ["The existing typed acceptance owner validated this plan without changing configuration or executing commands."]
            permission = "local_command_execution"
        elif action_kind == "operation.execute":
            if (
                context.get("kind") != "goal"
                or context.get("goal_id") != normalized["goal_id"]
            ):
                raise ValueError("operation proposal context must match its Goal")
            fingerprint = _digest(normalized)
            evidence = [
                "The provider-neutral operation envelope and immutable digests validated.",
                "Execution remains unavailable until an authenticated transport claims this exact request.",
            ]
            permission = "protected"
        elif action_kind == "todo.create":
            source = resolve_chat_todo_source(self, normalized["goal_id"])
            canonical_preview = build_todo_review_preview(
                **source.writer_scope(),
                goal_id=normalized["goal_id"],
                text=normalized["text"],
                priority=normalized.get("priority"),
                source_identity=source.source_identity,
            )
            fingerprint = str(canonical_preview["preview_id"])
            evidence = ["Canonical LoopX Todo dry-run validated the proposal."]
            permission = "durable_write"
        elif action_kind == "monitor.create":
            source = resolve_chat_todo_source(self, normalized["goal_id"])
            fingerprint = self._monitor_create_fingerprint(source)
            evidence = ["Canonical LoopX contracts validated the bounded request shape."]
            permission = "durable_write"
        elif action_kind == "run.correct":
            fingerprint = self._session_fingerprint(
                normalized["session_id"], normalized["goal_id"]
            )
            evidence = ["The recoverable Goal and Agent Chat Session is available."]
            permission = "scoped_correction"
        elif action_kind == "goal.lifecycle":
            lifecycle_preview = self._goal_lifecycle_preview(normalized)
            fingerprint = str(lifecycle_preview["state_fingerprint"])
            canonical_update_basis = lifecycle_preview.get("source_basis")
            evidence = [
                "The lifecycle transition is bound to the current authoritative Goal source."
            ]
            permission = "durable_write"
        elif action_kind == "team.plan":
            # A plan is reviewed against this Goal's registration facts *and*
            # the intent its lanes would advance, so both are bound here and
            # re-read at apply. Binding only the registry let an owner objective
            # change leave a confirmed plan applicable.
            from .control_plane.work_items.governed_transition_proposal import (
                steward_team_plan_basis_agent,
            )
            from .control_plane.work_items.team_plan_adapter import (
                team_plan_state_fingerprint,
            )

            fingerprint = team_plan_state_fingerprint(
                registry_path=self.registry_path,
                goal_id=str(normalized["goal_id"]),
                basis_agent_id=steward_team_plan_basis_agent(normalized["plan"]),
            )
            evidence = [
                "The plan was validated against this Goal's registered Agents and the host's advancement action kinds.",
                "Applying it creates the first bounded Todo of each ready lane, through the canonical Todo owner.",
            ]
            permission = "durable_write"
        elif action_kind in {"todo.update", "monitor.update"}:
            if normalized.get("operation") != "run_now":
                canonical_update_basis = self._canonical_update_basis(normalized["goal_id"],
                    completion_todo_id=normalized["todo_id"] if normalized.get("operation") in {"complete", "stop"} else None)
            if action_kind == "todo.update" or normalized.get("operation") != "run_now":
                run = self._run_todo_update if action_kind == "todo.update" else self._run_monitor_update
                canonical_preview = run(normalized, dry_run=True, basis=canonical_update_basis)
                if canonical_preview.get("ok") is not True:
                    raise ValueError(
                        str(
                            canonical_preview.get("error")
                            or "Todo transition failed canonical dry-run validation"
                        )
                    )
            goal_fingerprint = self._basis_or_goal_fingerprint(normalized["goal_id"], canonical_update_basis)
            if (
                action_kind == "monitor.update"
                and normalized.get("operation") == "run_now"
            ):
                session_id = normalized.get("session_id")
                fingerprint = (
                    _digest(
                        {
                            "goal": goal_fingerprint,
                            "session": self._session_fingerprint(
                                session_id, normalized["goal_id"]
                            ),
                        }
                    )
                    if session_id
                    else goal_fingerprint
                )
            else:
                fingerprint = goal_fingerprint
            evidence = [
                "Canonical LoopX Todo dry-run validated the requested transition."
                if normalized.get("operation") != "run_now"
                else "The monitor execution request is bound to the current Goal state."
            ]
            permission = "durable_write"
        elif action_kind == "gate.resolve" and normalized["decision"] in TODO_DECISION_OUTCOME_VALUES:
            canonical_update_basis = self._canonical_update_basis(
                normalized["goal_id"], completion_todo_id=normalized["todo_id"], decision=True)
            try:
                canonical_preview = self._run_gate_resolve(
                    normalized, dry_run=True, basis=canonical_update_basis)
            except LocalCoordinationAuthorityUnavailable as error:
                raise ValueError(str(error)) from error
            if canonical_preview.get("ok") is not True:
                raise ValueError(str(canonical_preview.get("error")
                                     or "Decision failed canonical dry-run validation"))
            fingerprint = self._basis_or_goal_fingerprint(normalized["goal_id"], canonical_update_basis)
            evidence = ["Canonical LoopX User completion dry-run validated this decision."]
            permission = "durable_write"
        elif action_kind == "pr.merge":
            from .capabilities.native_chat.steward_pr_merge import pr_merge_preview

            normalized, evidence, fingerprint = pr_merge_preview(
                normalized, digest_fn=_digest, runner=subprocess.run
            )
            permission = "protected"
        elif action_kind == "agent.bind":
            binding = read_goal_agent_binding_with_source_route(
                registry_path=self.registry_path,
                goal_id=str(normalized["goal_id"]),
            )
            fingerprint = str(binding["revision"])
            evidence = [
                "The Agent binding was validated against the canonical source Goal peer set."
            ]
            permission = "durable_write"
        else:
            fingerprint = self._registry_fingerprint()
            evidence = [
                "Canonical LoopX contracts validated the bounded request shape."
            ]
            permission = "durable_write"
        if eligibility is not None:
            evidence.extend(
                [
                    "The selected Agent endpoint is healthy and tool-capable.",
                    "The selected Agent trust scope and workspace route are compatible.",
                ]
            )
        proposal = self.store.create_preview(
            action_kind=action_kind,
            summary=_text(request.get("summary"), field="summary", limit=600),
            normalized_parameters=normalized,
            context=dict(context),
            expected_state_fingerprint=fingerprint,
            permission_classification=permission,
            validation_evidence=evidence,
            available_transitions=(
                ["cancel"]
                if action_kind == "operation.execute"
                else ["apply", "cancel"]
            ),
            idempotency_key=_opaque(
                request.get("idempotency_key"), field="idempotency_key"
            ),
            canonical_update_basis=canonical_update_basis,
            workspace_binding=workspace_binding,
        )
        return (
            self.store.arm_operation(str(proposal["proposal_id"]))
            if action_kind == "operation.execute"
            else proposal
        )

    def load(self, proposal_id: str) -> dict[str, object] | None:
        return self.store.load(proposal_id)

    def cancel(self, proposal_id: str) -> dict[str, object]:
        return self.store.cancel(proposal_id)

    def _reject_or_defer(self, proposal_id: str, *, verb: str, mark: bool) -> dict[str, object]:
        proposal = self.store.load(proposal_id)
        if proposal is not None and proposal.get("action_kind") == "operation.execute":
            raise ProtectedActionGate(
                "operation.execute",
                gate={
                    "kind": "authenticated_operation_decision_required",
                    "summary": "Operation decisions must come from a bound authenticated surface.",
                    "next_action": f"Use the original operation card to {verb} this request.",
                },
            )
        return self.store.mark_rejected(proposal_id) if mark else self.store.mark_deferred(proposal_id)

    def reject(self, proposal_id: str) -> dict[str, object]:
        return self._reject_or_defer(proposal_id, verb="confirm or reject", mark=True)

    def defer(self, proposal_id: str) -> dict[str, object]:
        return self._reject_or_defer(proposal_id, verb="decide", mark=False)

    def regenerate(self, proposal_id: str) -> dict[str, object]:
        proposal = self.store.load(proposal_id)
        if proposal is None:
            raise KeyError("typed Chat action proposal was not found")
        if proposal.get("action_kind") == "operation.execute":
            raise ProtectedActionGate(
                "operation.execute",
                gate={
                    "kind": "new_operation_required",
                    "summary": "Material operation changes require a new immutable request.",
                    "next_action": "Prepare a new operation instead of regenerating this one.",
                },
            )
        steps = _as_dict(proposal.get("checkpoint")).get("steps")
        action_kind = proposal.get("action_kind")
        if (action_kind == "goal.create" and steps) or (
            action_kind == "todo.create" and isinstance(steps, dict) and "todo_created" in steps
        ):
            raise ActionConflictError("Creation already recorded effects; recover the original operation instead of regenerating")
        if proposal.get("status") not in {"stale", "failed", "gated", "rejected"}:
            raise ActionConflictError(
                f"proposal in {proposal.get('status')} state cannot be regenerated"
            )
        regenerated = self.preview(
            {
                "action_kind": proposal.get("action_kind"),
                "summary": proposal.get("summary"),
                "normalized_parameters": proposal.get("normalized_parameters"),
                "context": proposal.get("context"),
                "idempotency_key": f"regenerate-{proposal_id}-{_digest(proposal)[:16]}",
            }
        )
        return self.store.link_regeneration(
            str(regenerated["proposal_id"]), regenerated_from=proposal_id
        )

    def apply(self, proposal_id: str, *, steward_context: dict[str, object] | None = None,
              steward_confirmed_at: str | None = None) -> dict[str, object]:
        result = self._apply_owner_action(proposal_id, steward_context=steward_context,
            steward_confirmed_at=steward_confirmed_at)
        # A committed action changes current facts. Existing queue readers may
        # reobserve their retained requests; this content-free hint performs no
        # transport, Task admission, permission change or new submission.
        wakeup = getattr(self.runtime_controller, "turn_release_wakeup", None)
        proposal_payload = result.get("proposal")
        if isinstance(proposal_payload, Mapping) and proposal_payload.get("status") == "applied" and wakeup is not None:
            wakeup.notify()
        return result

    def _apply_owner_action(self, proposal_id: str, *, steward_context: dict[str, object] | None = None,
              steward_confirmed_at: str | None = None) -> dict[str, object]:
        proposal = self.store.load(proposal_id)
        if proposal is None:
            raise KeyError("typed Chat action proposal was not found")
        if proposal.get("status") == "applied" and self._pending_applied_task_receipt(proposal) is None:
            return {
                "proposal": proposal,
                "turn": self._turn_from_receipt(proposal.get("receipt")),
            }
        if (proposal.get("context") or {}).get("binding_id"):
            if steward_context is None:
                raise ProtectedActionGate(str(proposal["action_kind"]), gate={"kind": "authenticated_steward_confirmation_required",
                    "summary": "Confirm this proposal from its original owner private conversation.",
                    "next_action": "Use the exact /confirm command in the originating Bot before it expires."})
            from .capabilities.native_chat.steward_pr_merge import steward_pr_merge_extra_auth
            from .control_plane.effect_runtime import effect_runtime_result
            runtime_root = resolve_runtime_root(
                self._registry(), self.runtime_root_override, registry_path=self.registry_path
            ).resolve()
            effect_runtime_result("collaboration.steward.authorize_creation", {
                "context": steward_context,
                "proposal": proposal,
                "now": steward_confirmed_at or now_utc().isoformat(),
                **steward_pr_merge_extra_auth(
                    self.registry_path, runtime_root, steward_context, proposal
                ),
            })
        if proposal.get("action_kind") == "operation.execute":
            raise ProtectedActionGate(
                "operation.execute",
                gate={
                    "kind": "authenticated_operation_confirmation_required",
                    "summary": "A local apply request cannot attest a human operation confirmation.",
                    "next_action": "Confirm the exact request through its authenticated operation card.",
                },
            )
        parameters = proposal.get("normalized_parameters")
        action = proposal.get("action_kind")
        if isinstance(parameters, dict) and (
            action == "todo.create" or (action == "goal.create" and parameters.get("start_execution") is True)
        ):
            key = _opaque(proposal_id, field="proposal_id")
            with try_exclusive_file_lock(
                self.store.root / "task-starts" / f"{key}.json", operation="confirm_task_start",
            ) as held:
                if held is None:
                    raise ActionConflictError("The original Task confirmation is in progress. Retry it when it finishes.")
                return self._apply_confirmed_action(proposal_id)
        return self._apply_confirmed_action(proposal_id)

    def _apply_confirmed_action(self, proposal_id: str) -> dict[str, object]:
        proposal = self.store.start_apply(proposal_id)
        if proposal["status"] == "applied":
            return {"proposal": proposal, "turn": self._recover_applied_task(proposal)}
        action_kind = str(proposal.get("action_kind") or "")
        parameters = proposal.get("normalized_parameters")
        if not isinstance(parameters, dict):
            raise ValueError("typed Chat action proposal is malformed")
        if action_kind == "goal.acceptance.check":
            return self._apply_acceptance_check(proposal_id)
        if action_kind == "goal.create":
            return self._apply_goal_create(proposal_id, proposal, parameters)
        if action_kind == "goal.lifecycle":
            return self._apply_goal_lifecycle(proposal_id, proposal, parameters)
        if action_kind == "agent.bind":
            return self._apply_agent_bind(proposal_id, proposal, parameters)
        if action_kind == "heartbeat.bind":
            raise self._heartbeat_gate(parameters)
        if action_kind == "monitor.create":
            return self._apply_monitor_create(proposal_id, proposal, parameters)
        if action_kind == "team.plan":
            return self._apply_team_plan(proposal_id, proposal, parameters)
        if action_kind == "todo.update" or (
                action_kind == "gate.resolve" and parameters.get("decision") in TODO_DECISION_OUTCOME_VALUES):
            return self._apply_todo_update(proposal_id, proposal, parameters)
        if action_kind == "monitor.update":
            return self._apply_monitor_update(proposal_id, proposal, parameters)
        if action_kind == "pr.merge":
            from .capabilities.native_chat.steward_pr_merge import apply_pr_merge
            return apply_pr_merge(
                self, proposal_id, proposal, parameters,
                digest_fn=_digest, gate_cls=ProtectedActionGate, runner=subprocess.run,
            )
        if action_kind == "gate.resolve":
            raise ProtectedActionGate(
                action_kind,
                gate={
                    "kind": "decision_outcome_required",
                    "summary": "Deferring records no decision; the request stays open.",
                    "next_action": "Approve, reject or cancel the request when ready.",
                },
            )
        if action_kind == "goal.update":
            if "reason" in parameters:
                return self._apply_objective_revision(proposal_id)
            raise ProtectedActionGate(
                action_kind,
                gate={
                    "kind": "canonical_authority_required",
                    "summary": f"{action_kind} requires a narrower canonical authority contract.",
                    "next_action": "Keep this preview and resolve the action through the Goal configuration or Gate lifecycle surface.",
                },
            )
        if action_kind == "todo.create":
            return self._apply_todo_create(proposal_id, proposal, parameters)
        if action_kind == "run.correct":
            if self.runtime_controller is None or self.chat_store is None:
                raise ValueError("Chat Runtime Controller is unavailable")
            current_fingerprint = self._session_fingerprint(
                str(parameters["session_id"]), str(parameters["goal_id"])
            )
            if current_fingerprint != proposal.get("expected_state_fingerprint"):
                return self._stale_proposal(proposal_id, current_fingerprint)
            goal = self._goal(str(parameters["goal_id"]))
            project = Path(str(goal.get("repo") or ".")).expanduser().resolve()
            if not project.is_dir():
                raise ValueError("the Goal project root is unavailable")
            client_turn_id = str(
                parameters.get("client_turn_id") or f"action-{proposal_id}"
            )
            turn, created = self.runtime_controller.submit_turn(
                session_id=str(parameters["session_id"]),
                client_turn_id=client_turn_id,
                message=str(parameters["message"]),
                work_dir=project,
                objective=str(goal.get("domain") or parameters["goal_id"]),
            )
            turn_id = _opaque(turn.get("turn_id"), field="turn_id")
            run_receipt: dict[str, object] = {
                "receipt_id": _receipt_id(
                    {"proposal_id": proposal_id, "session_id": parameters["session_id"], "turn_id": turn_id}
                ),
                "outcome": "turn_created" if created else "turn_already_exists",
                "projection_verified": True,
                "resource_ids": {
                    "goal_id": str(parameters["goal_id"]),
                    "session_id": str(parameters["session_id"]),
                    "turn_id": turn_id,
                },
            }
            stored = self.store.apply(
                proposal_id,
                current_state_fingerprint=current_fingerprint,
                receipt=run_receipt,
            )
            return {
                "proposal": stored,
                "turn": {
                    "turn_id": turn_id,
                    "status": str(turn.get("status") or "queued"),
                    "created": created,
                },
            }
        raise ValueError(f"unsupported action_kind: {action_kind}")

    def _turn_from_receipt(self, receipt: object) -> dict[str, object] | None:
        if not isinstance(receipt, dict) or not isinstance(
            receipt.get("resource_ids"), dict
        ):
            return None
        resource_ids = receipt["resource_ids"]
        if not resource_ids.get("turn_id"):
            return None
        session_id = resource_ids.get("session_id")
        turn_id = str(resource_ids["turn_id"])
        status = "accepted"
        if receipt.get("outcome") == "task_execution_started" and session_id:
            chat = self._turn_store()
            turn = chat.load_turn(str(session_id), turn_id) if chat is not None else None
            if turn and turn.get("session_id") == session_id and turn.get("turn_id") == turn_id:
                status = str(turn.get("status") or status)
        return {
            "session_id": str(session_id) if session_id else None,
            "turn_id": turn_id,
            "status": status,
            "created": receipt.get("outcome") == "turn_created",
        }
