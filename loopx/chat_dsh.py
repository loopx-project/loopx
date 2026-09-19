"""The steward channel's managed-host transport: one bounded segment per turn.

The managed host runs one bounded DeepSeek Harness work segment per request and
does not promise an interactive or cross-turn host session, so this adapter does
not pretend to hold one. Every Chat turn starts one segment on the resolved
managed execution profile, hands it the channel's bounded history plus the
current message, and returns the final assistant message as the answer.

What this transport deliberately does **not** claim, because a caller must not
infer it:

* no partial streaming: the answer arrives as one final message;
* no cross-turn host session: the visible history is Chat-side context, and each
  segment is a fresh one;
* no tool authority: the segment is pinned read-only (see
  ``STEWARD_SEGMENT_ENV``), so a model that reaches for a tool is refused by the
  dsh sandbox itself, and the answer must come from the evidence LoopX supplies.

The managed host cannot cancel a segment once it has started, so this adapter
holds **one** executor slot per binding: a segment start while the previous
segment is still running is refused with a typed error instead of quietly
starting a second executor, and an interrupted segment's answer is discarded
rather than folded back into the channel's visible history.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import threading
from pathlib import Path
from typing import Any
import uuid

from .chat import parse_agent_response
from .chat_agent import CodexChatAgentError, CodexChatTimeoutError, _turn_prompt
from .control_plane.turn_driver.host_binding import dsh_output_token_budget
from .dsh_goal_mode.turn_host_adapter import (
    normalize_runner_outcome,
    resolve_dsh_home,
    run_dsh_turn,
    terminal_output_budget_state,
)

EventSink = Callable[[str, dict[str, Any]], None]

# The visible history a segment may see. It is Chat context, never a host
# session: the adapter sends the same bounded window the CLI endpoints send.
HISTORY_LIMIT = 12

# An interactive channel answers from the evidence LoopX supplies and delegates
# work; it does not get write or shell authority of its own. dsh composes its
# sandbox from `DSH_PERMISSION_MODE` (its `read-only` preset), so pinning that
# variable is what makes the boundary machine-enforced instead of polite. A
# segment that tries a write is refused by the sandbox, exactly like the
# read-only Codex steward the channel ran on before it had this transport.
STEWARD_SEGMENT_ENV = {"DSH_PERMISSION_MODE": "read-only"}

MANAGED_HOST_CHAT_TIMEOUT = "managed_host_chat_timeout"
MANAGED_HOST_CHAT_FAILED = "managed_host_chat_failed"
# A second executor for one binding is refused, not queued or run in parallel:
# the managed host has no cancel, so the previous segment can only be waited
# out. The code names the state the channel is in, not a user mistake.
MANAGED_HOST_CHAT_SEGMENT_IN_FLIGHT = "managed_host_chat_segment_in_flight"

# How long a new segment waits for the previous one to exit before it refuses.
# It is a hand-off window for a segment that is already finishing, not a
# deadline: a segment that is still running past it stays the binding's single
# executor, and the caller reads one typed refusal.
SEGMENT_HANDOFF_GRACE_SEC = 5.0


@dataclass
class _SegmentSlot:
    """The binding's single managed-executor slot.

    The slot is occupied until the segment's thread actually exits, including
    after a channel timeout: the segment keeps running in the host, so the next
    start must still see one executor as long as that is true.
    """

    thread: threading.Thread | None = None
    interrupted: bool = False


@dataclass
class DshChatAdapter:
    """Chat transport for the managed host, one bounded segment per turn."""

    objective: str
    work_dir: Path
    provider: str
    model: str
    reasoning_effort: str
    session_id: str = field(default_factory=lambda: "dsh-chat-" + uuid.uuid4().hex)
    history: list[dict[str, Any]] = field(default_factory=list)
    timeout_sec: float = 600.0
    max_tokens: int | None = None
    cordis: Path | None = None
    runtime_bin: str | None = None
    runner: Callable[..., Any] | None = None
    # Only the operator credential pair the segment authenticates with, already
    # resolved from the machine store and the service environment by the channel
    # that owns the runtime root. A segment runs as its own process, so the pair
    # has to be handed to it rather than read again there -- and nothing else
    # travels with it, because the segment has no authority beyond its answer.
    credential: Mapping[str, str] | None = None
    _slot: _SegmentSlot | None = field(default=None, init=False, repr=False)
    _slot_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    @property
    def upstream_thread_id(self) -> str:
        return self.session_id

    def capabilities(self) -> dict[str, Any]:
        return {
            "adapter_kind": "deepseek_harness_segment",
            "streaming": False,
            "resume": True,
            "interrupt": False,
            # No tool authority is granted by the channel. The segment answers
            # from the evidence LoopX supplies and delegates work back.
            "tool_calls": False,
            "trust_scope": "read_only",
        }

    def _prompt(self, message: str) -> str:
        """Compose the segment input: objective, visible history, Turn prompt.

        A segment is fresh, so the visible history is part of its input rather
        than something the host remembers. Only the bounded window is sent.
        """

        history_lines = [
            f"{item.get('role', 'user')}: {str(item.get('content') or '').strip()}"
            for item in self.history[-HISTORY_LIMIT:]
            if str(item.get("content") or "").strip()
        ]
        history_block = (
            "\nPrevious visible Chat messages:\n" + "\n".join(history_lines)
            if history_lines
            else ""
        )
        return f"{self.objective.strip()}{history_block}\n\n{_turn_prompt(message)}"

    def _run_segment(self, prompt: str) -> dict[str, Any]:
        budget = dsh_output_token_budget(self.max_tokens)
        if not budget["valid"]:
            raise CodexChatAgentError(
                "The managed host requires a positive per-request output-token limit.",
                gate=None,
                error_code="invalid_output_token_limit",
            )
        runner = self.runner or run_dsh_turn
        # The dsh sandbox's own setting wins, so a caller cannot widen the
        # segment's authority by passing a credential mapping that also carries
        # DSH_PERMISSION_MODE.
        segment_env = dict(self.credential or {})
        segment_env.update(STEWARD_SEGMENT_ENV)
        outcome = runner(
            prompt=prompt,
            session_id=self.session_id,
            workspace=self.work_dir,
            session_root=resolve_dsh_home(self.work_dir),
            provider=self.provider,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            max_tokens=budget["max_tokens"],
            cordis=self.cordis,
            runtime_bin=self.runtime_bin,
            request_timeout_seconds=self.timeout_sec,
            env=segment_env,
        )
        if isinstance(outcome, str):
            return {"final_response": outcome, "finish_reason": None, "events": []}
        if not isinstance(outcome, dict):
            raise CodexChatAgentError(
                "The managed host returned an unreadable segment result.",
                gate=None,
                error_code=MANAGED_HOST_CHAT_FAILED,
            )
        return normalize_runner_outcome(outcome)

    def _claim_segment_slot(self) -> _SegmentSlot:
        """Reserve the binding's one executor slot, or refuse a second executor.

        Only the slot hand-off is serialized, not the segment itself: the caller
        blocks in ``start_turn`` while its own segment runs, so holding a lock
        here would make the second caller wait on the lock instead of reading the
        typed refusal. A previous segment that exited frees the slot; one that is
        still running after the hand-off window keeps it.
        """

        with self._slot_lock:
            previous = self._slot
            thread = previous.thread if previous is not None else None
            if thread is not None and thread.is_alive():
                thread.join(SEGMENT_HANDOFF_GRACE_SEC)
                if thread.is_alive():
                    raise CodexChatAgentError(
                        "The managed host is still running the previous segment; "
                        "a second executor for this binding is refused.",
                        gate=None,
                        error_code=MANAGED_HOST_CHAT_SEGMENT_IN_FLIGHT,
                    )
            slot = _SegmentSlot()
            self._slot = slot
            return slot

    def start_turn(self, message: str, event_sink: EventSink) -> dict[str, Any]:
        slot = self._claim_segment_slot()
        event_sink("turn.started", {"upstream_turn_id": self.session_id})
        event_sink(
            "agent.phase",
            {"phase": "managed_host_segment", "label": "正在托管宿主上处理"},
        )
        prompt = self._prompt(message)
        outcome: dict[str, Any] = {}
        failure: list[BaseException] = []

        def run() -> None:
            try:
                outcome.update(self._run_segment(prompt))
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
                failure.append(exc)

        worker = threading.Thread(
            target=run, name="loopx-dsh-chat-segment", daemon=True
        )
        slot.thread = worker
        worker.start()
        worker.join(self.timeout_sec)
        if worker.is_alive():
            # The segment is bounded by the caller's deadline, not by the host's
            # willingness to stop: the channel reports a typed timeout instead of
            # waiting on the steward forever.
            raise CodexChatTimeoutError(
                "The managed host did not finish this segment within the channel timeout.",
                gate=None,
                error_code=MANAGED_HOST_CHAT_TIMEOUT,
            )
        if failure:
            error = failure[0]
            if isinstance(error, CodexChatAgentError):
                raise error
            raise CodexChatAgentError(
                "The managed host failed to run this segment.",
                gate=None,
                error_code=MANAGED_HOST_CHAT_FAILED,
            ) from error
        budget_state = terminal_output_budget_state(outcome)
        if budget_state is not None:
            raise CodexChatAgentError(
                "The managed host exhausted its per-request output budget. "
                "No answer was accepted; inspect the session before a bounded recovery.",
                gate=None,
                error_code=f"dsh_output_budget_exhausted_{budget_state}",
            )
        raw = str(outcome.get("final_response") or "").strip()
        if not raw:
            # An empty final message after a terminal provider error is the
            # provider's verdict, not an answer: the channel fails closed.
            raise CodexChatAgentError(
                "The managed host returned no answer for this segment.",
                gate=None,
                error_code=MANAGED_HOST_CHAT_FAILED,
            )
        response = parse_agent_response(
            raw,
            protected_paths=[self.work_dir],
            team_plan_context=getattr(self, "team_plan_context", None),
        )
        if slot.interrupted:
            # The channel discarded this turn, so its answer never happened: the
            # segment still exits as the binding's executor, but folding its
            # text into the visible history would let an abandoned answer read
            # as the steward's most recent statement.
            return response
        self.history.extend(
            [
                {"role": "user", "content": message},
                {"role": "assistant", "content": str(response.get("message") or "")},
            ]
        )
        del self.history[:-HISTORY_LIMIT]
        event_sink("answer.delta", {"text": str(response.get("message") or "")})
        event_sink("answer.final", {"response": response})
        return response

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        # The segment owns its own runtime and exits on its request timeout, so
        # there is no retained session to interrupt and no host-side cancel to
        # call. What the channel can do is mark the running segment as
        # abandoned, so its later answer is discarded instead of becoming the
        # channel's newest visible statement. The slot stays occupied until the
        # segment exits, because it is still the binding's one executor.
        del turn_id
        with self._slot_lock:
            slot = self._slot
            if slot is not None and slot.thread is not None and slot.thread.is_alive():
                slot.interrupted = True

    def close_session(self) -> None:
        return None

    def healthcheck(self) -> bool:
        return True
