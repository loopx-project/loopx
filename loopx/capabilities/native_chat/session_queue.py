"""The existing same-Session FIFO, preparation and recovery consumer."""
from pathlib import Path
from typing import Any
import threading

from ...chat_store import CHAT_SESSION_MODE_ATTACHED, TERMINAL_TURN_STATES


def _is_observation_only_monitor_turn(turn: dict[str, Any] | None) -> bool:
    """Return True only for native no-model monitor turns."""
    if not isinstance(turn, dict) or turn.get("loopx_execution") is not True:
        return False
    request = turn.get("loopx_request")
    return isinstance(request, dict) and request.get("work_kind") == "monitor"


def drain_session_queue(controller: Any, *, session_id: str, work_dir: Path, objective: str) -> None:
    wakeup = threading.Event()
    with controller.turn_release_wakeup(wakeup):
        _drain_session_queue_events(controller, session_id=session_id, work_dir=work_dir,
            objective=objective, wakeup=wakeup)


def _drain_session_queue_events(controller: Any, *, session_id: str, work_dir: Path,
                                objective: str, wakeup: threading.Event) -> None:
    try:
        while not controller.closed.is_set():
            # Register and clear before reading durable state. A signal
            # during this read remains visible when a pending request waits.
            wakeup.clear()
            session = controller.store.load_session(session_id)
            if (
                controller.closed.is_set()
                or session is None
                or session.get("status") == "closed"
                or session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED
            ):
                return
            turn = None
            active_turn_id = str(session.get("active_turn_id") or "")
            if active_turn_id:
                try:
                    controller.recover_exited_turn(session_id, active_turn_id)
                except OSError:
                    # Storage can still be unavailable. Retry only the
                    # non-executing lifecycle repair, never the old input.
                    controller.closed.wait(0.25)
                    continue
                active = controller.store.load_turn(session_id, active_turn_id)
                if active and isinstance((active.get("loopx_request") or {}).get("creation_request"), dict):
                    if active.get("status") == "queued":
                        from .creation_queue import consume_creation_request
                        try:
                            pending = consume_creation_request(controller, session_id)
                        except (OSError, ValueError, RuntimeError):
                            pending = True
                        if not pending:
                            # The native worker can terminalize after this
                            # queue cached its queued state. Reconcile the
                            # current FIFO instead of retiring with ordinary
                            # messages stranded behind the released operation.
                            current = controller.store.load_session(session_id)
                            latest = controller.store.load_turn(session_id, active_turn_id)
                            if current is None:
                                return
                            if (current.get("active_turn_id") != active_turn_id
                                    or latest and latest.get("status") in TERMINAL_TURN_STATES):
                                continue
                            # A still-active unknown operation grants no
                            # dispatch. Keep its worker until a state signal.
                            wakeup.wait()
                            continue
                    if active.get("status") not in TERMINAL_TURN_STATES:
                        # An admitted or unknown native operation retains
                        # its original worker/result; never restore it as an
                        # ordinary queued message or start another model.
                        wakeup.wait()
                        continue
                if (active and active.get("status") == "queued" and "_acceptance" not in active
                        and active.get("started_at") is None and active.get("upstream_turn_id") is None
                        and active.get("delta_count") == 0):
                    # A crash can leave the queue claim before native
                    # dispatch. Keep that exact claim and pass through the
                    # normal preparation and execution fences below.
                    turn = active
                else:
                    with controller.lock:
                        attached = controller.adapters.get(session_id)
                        live_worker = controller._has_live_turn_worker(session, active)
                    if not live_worker and not _is_observation_only_monitor_turn(active):
                        if attached is None or not attached.healthcheck():
                            try:
                                controller._ensure_adapter(
                                    session,
                                    work_dir=work_dir,
                                    objective=objective,
                                )
                            except Exception as exc:
                                controller._fail_queue_preparation(session_id, active_turn_id, exc)
                            else:
                                continue
                    active = controller.store.load_turn(session_id, active_turn_id)
                    if active and active.get("status") not in TERMINAL_TURN_STATES:
                        controller.closed.wait(0.05)
                        continue
                    controller.store.release_active_turn(session_id, active_turn_id)
            refreshed = controller.store.load_session(session_id)
            if refreshed is None:
                return
            with controller.lock:
                controller.session_queue_wakeups.discard(session_id)
            if turn is None:
                turn = controller.store.claim_next_queued_turn(session_id)
            if turn is None:
                from .creation_queue import consume_creation_request
                try:
                    pending = consume_creation_request(controller, session_id)
                except (OSError, ValueError, RuntimeError):
                    pending = True
                if pending:
                    # The request lives in its creation receipt. Existing
                    # action/Turn signals and startup discovery re-read it;
                    # pending checks do not create a failed native Turn.
                    wakeup.wait()
                    continue
                with controller.lock:
                    if session_id in controller.session_queue_wakeups:
                        continue
                    worker = controller.session_queue_threads.get(session_id)
                    if worker is threading.current_thread():
                        controller.session_queue_workers.discard(session_id)
                        controller.session_queue_threads.pop(session_id, None)
                return
            turn_id = str(turn["turn_id"])
            try:
                if turn.get("origin") == "peer" or "collaboration_request" in turn:
                    from .conversation_bindings import OriginalConversationRequests
                    OriginalConversationRequests(controller).validate_turn(session_id, turn)
                # An empty queue needs no provider preparation. Claim real
                # work first, keeping the existing enqueue/retirement fence
                # above and the Turn's execution/stop checks below.
                current = controller.store.load_session(session_id)
                if current is None or current.get("active_turn_id") != turn_id:
                    return
                if _is_observation_only_monitor_turn(turn):
                    from .steward_monitor import StewardObservationAdapter

                    adapter: Any = StewardObservationAdapter(current.get("upstream_thread_id"))
                else:
                    adapter = controller._ensure_adapter(
                        current,
                        work_dir=work_dir,
                        objective=objective,
                    )
                if (controller.store.load_session(session_id) or {}).get("active_turn_id") != turn_id:
                    return
            except Exception as exc:
                # The claimed request still owns a terminal outcome when
                # workspace preparation or adapter restoration fails.
                controller._fail_queue_preparation(session_id, turn_id, exc)
                continue
            done_event = threading.Event()
            with controller.lock:
                controller.turn_done_events[(session_id, turn_id)] = done_event
            try:
                controller._run_turn(
                    session_id=session_id,
                    turn_id=turn_id,
                    message=str(turn.get("message") or ""),
                    attachments=turn.get("attachments") or [],
                    adapter=adapter,
                    done_event=done_event,
                )
            except OSError:
                # _run_turn has released its execution fence/done event.
                # A failed error checkpoint must not kill this queue worker;
                # the next iteration reconciles the original durable state.
                controller.closed.wait(0.25)
    finally:
        with controller.lock:
            worker = controller.session_queue_threads.get(session_id)
            if worker is threading.current_thread():
                controller.session_queue_workers.discard(session_id)
                controller.session_queue_threads.pop(session_id, None)
                controller.session_queue_wakeups.discard(session_id)
