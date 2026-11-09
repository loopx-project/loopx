"""Bounded transient dispatch for one provider stream, not Chat admission authority."""

import hashlib
import json
import re
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import NamedTuple

from ...chat_manager import manager_channel
from .event_inbox import LarkTopicInboxIntake, LarkTopicInput
from .goal_channel_contracts import bindings_for_goal
from .goal_channel_targets import goal_channel_target_for_name
from .goal_topic_routing import IngressMode
from .manager_context import opaque_digest


MentionCapture = Callable[[Mapping[str, object]], dict[str, object]]


class _Work(NamedTuple):
    lane: tuple[str, ...]
    line: str
    priority: bool
    durable: bool


class ProfileEventDispatch:
    """Keep conversation order without letting one answer block every audience.

    A lane is only a transport scheduling key. The handler must read fresh
    binding/authority and persist through the existing Inbox/Chat owners.
    Durable inputs use a coalesced wake for their Inbox source. Only transient
    input bodies count against the pipe buffer bound.
    """

    def __init__(
        self,
        handle: Callable[[str], None],
        stop: threading.Event,
        *,
        workers: int = 4,
        capacity: int = 64,
        reserve_priority: bool = False,
    ) -> None:
        if workers < 1 or capacity < workers:
            raise ValueError("dispatch capacity must cover its positive worker limit")
        self._handle = handle
        self._stop = stop
        self._workers = workers
        self._capacity = capacity
        self._reserve_priority = reserve_priority and workers > 1
        self._condition = threading.Condition()
        self._waiting: dict[str, deque[_Work]] = {}
        self._ready: deque[str] = deque()
        self._active: dict[str, tuple[tuple[str, ...], bool]] = {}
        self._pending = 0
        self._ordinary_pending = 0
        self._priority_pending = 0
        self._closing = False
        self._cancelled = False
        self._failure: Exception | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="loopx-lark-dispatch"
        )

    @property
    def failed(self) -> bool:
        """Expose failure while the stream reader is idle; close still owns it."""

        with self._condition:
            return self._failure is not None

    @property
    def ordinary_full(self) -> bool:
        """Whether transient ordinary admission has reached its bound."""

        with self._condition:
            return self._full(False)

    def set_priority_reservation(self, enabled: bool) -> None:
        """Apply current binding eligibility without replacing the stream or work."""

        with self._condition:
            enabled = enabled and self._workers > 1
            if self._reserve_priority == enabled:
                return
            self._reserve_priority = enabled
            self._start_ready()
            self._condition.notify_all()

    def _full(self, priority: bool) -> bool:
        transient = self._ordinary_pending + self._priority_pending
        if not self._reserve_priority:
            return transient >= self._capacity
        if priority:
            return self._priority_pending >= self._workers
        return self._ordinary_pending >= self._capacity - 1 or transient >= self._capacity

    def submit(
        self, lane: tuple[str, ...], line: str, *, priority: bool = False,
        durable: bool = False,
    ) -> bool:
        if durable and priority:
            raise ValueError("Inbox source wakes use their ordinary lane")
        with self._condition:
            while not durable and self._full(priority) and not self._closing:
                if self._stop.is_set():
                    return False
                self._condition.wait(0.1)
            if self._failure is not None:
                raise self._failure
            if self._closing or self._stop.is_set():
                return False
            source = lane[0]
            queue = self._waiting.setdefault(source, deque())
            if durable and any(work.durable and work.line == line for work in queue):
                return True
            work = _Work(lane, line, priority, durable)
            queue.append(work)
            self._pending += 1
            if priority:
                self._priority_pending += 1
            elif not durable:
                self._ordinary_pending += 1
            if source not in self._active and source not in self._ready:
                if priority:
                    self._ready.appendleft(source)
                else:
                    self._ready.append(source)
            self._start_ready()
            return True

    def _start_ready(self) -> None:
        if self._stop.is_set():
            self._discard_waiting()
        # A stable source keeps FIFO across rebinds; an optional Session key
        # also fences different sources that share the same conversation owner.
        for _ in range(len(self._ready)):
            if self._cancelled or len(self._active) >= self._workers:
                break
            source = self._ready.popleft()
            work = self._waiting[source][0]
            lane, priority = work.lane, work.priority
            active_ordinary = sum(
                1 for _lane, is_priority in self._active.values() if not is_priority
            )
            # Keep one of the existing workers available for a verified live
            # mention while ordinary answer handlers wait on Chat completion.
            if self._reserve_priority and not priority and active_ordinary >= self._workers - 1:
                self._ready.append(source)
                continue
            occupied = {key for active, _is_priority in self._active.values() for key in active}
            if occupied.intersection(lane):
                self._ready.append(source)
                continue
            self._waiting[source].popleft()
            self._active[source] = (lane, priority)
            self._executor.submit(self._run, source, work)

    def _discard_waiting(self) -> None:
        self._cancelled = True
        for queue in self._waiting.values():
            for work in queue:
                self._retire(work)
        self._waiting.clear()
        self._ready.clear()

    def _retire(self, work: _Work) -> None:
        self._pending -= 1
        if work.priority:
            self._priority_pending -= 1
        elif not work.durable:
            self._ordinary_pending -= 1

    def _run(self, source: str, work: _Work) -> None:
        try:
            self._handle(work.line)
        except Exception as exc:
            with self._condition:
                if self._failure is None:
                    self._failure = exc
                self._closing = True
                self._discard_waiting()
        finally:
            with self._condition:
                self._retire(work)
                self._active.pop(source)
                queue = self._waiting.get(source)
                if queue:
                    if queue[0].priority:
                        self._ready.appendleft(source)
                    else:
                        self._ready.append(source)
                else:
                    self._waiting.pop(source, None)
                self._start_ready()
                self._condition.notify_all()

    def close(self, *, cancel_pending: bool = False) -> None:
        """Drain started handlers and transient lines, then release the listener."""

        with self._condition:
            self._closing = True
            # A new provider connection can wake these captured records. Do
            # not hold the old listener open to answer its whole durable backlog.
            for source, queue in self._waiting.items():
                self._waiting[source] = deque(work for work in queue if not work.durable)
                self._pending -= len(queue) - len(self._waiting[source])
            self._ready = deque(source for source in self._ready if self._waiting[source])
            self._condition.notify_all()
            while self._pending:
                if cancel_pending or self._stop.is_set():
                    self._discard_waiting()
                self._condition.wait(0.1)
        self._executor.shutdown(wait=True)
        if self._failure is not None:
            raise self._failure


@dataclass(frozen=True)
class TopicIntakeRoute:
    route: Mapping[str, object]
    targets: Mapping[str, object]
    digest: str
    inbox_key: str


def topic_inbox_key(profile: str, chat_id: str, topic_root: str) -> str:
    return hashlib.sha256(f"{profile}\0{chat_id}\0{topic_root}".encode("utf-8")).hexdigest()[:20]


def topic_intake_route(
    profile: str, route: Mapping[str, object], targets: Mapping[str, object],
    event: Mapping[str, object],
) -> TopicIntakeRoute | None:
    ingress_mode = route.get("ingress_mode", IngressMode.DIRECT_SESSION.value)
    if ingress_mode != IngressMode.DIRECT_SESSION.value and (
        ingress_mode not in {IngressMode.SESSION_QUEUE.value, IngressMode.LIVE_STEERING.value}
        or not route.get("session_id")
    ):
        return None
    target = goal_channel_target_for_name(targets, str(route.get("target_ref") or ""))
    identity = target.get("identity") if target is not None else None
    if not isinstance(identity, Mapping):
        return None
    chat_id = str(event.get("chat_id") or "")
    key = topic_inbox_key(
        str(route.get("app_ref") or ""), chat_id, str(route.get("topic_root_message_id") or "")
    )
    # Freeze the existing typed routing decision and the provider's bound Bot.
    # Capture is not authority to execute against a changed binding.
    digest = opaque_digest(
        profile, chat_id, identity.get("bot_app_id"), identity.get("bot_open_id"),
        json.dumps(dict(route), sort_keys=True),
    )
    return TopicIntakeRoute(route=route, targets=targets, digest=digest, inbox_key=key)


def profile_can_reserve_mention_worker(
    snapshot: Mapping[str, object],
    profile: str,
    *,
    bot_app_id: str,
    capture_mention: MentionCapture | None,
) -> bool:
    """Reserve a priority worker only when a bound Session can steer on @mention."""

    target_payload = snapshot.get("target_payload")
    target_payload = target_payload if isinstance(target_payload, Mapping) else {}
    binding_payloads = snapshot.get("binding_payloads")
    binding_payloads = binding_payloads if isinstance(binding_payloads, Mapping) else {}
    for goal_id, payload in binding_payloads.items():
        if not isinstance(payload, Mapping):
            continue
        for binding in bindings_for_goal(payload, str(goal_id)):
            if binding.get("enabled") is not True:
                continue
            session_id = str(binding.get("session_id") or "").strip()
            routing = binding.get("routing")
            routing = routing if isinstance(routing, Mapping) else {}
            ingress_mode = str(routing.get("ingress_mode") or "")
            if not session_id or ingress_mode not in {"live_steering", "session_queue"}:
                continue
            target = goal_channel_target_for_name(
                target_payload, str(binding.get("target_ref") or "")
            )
            if target is None or target.get("enabled") is not True:
                continue
            identity = target.get("identity")
            identity = identity if isinstance(identity, Mapping) else {}
            sender_profile = str(identity.get("sender_profile") or "").strip()
            same_app = bool(bot_app_id) and str(identity.get("bot_app_id") or "") == bot_app_id
            if sender_profile != profile and not same_app:
                continue
            if not (identity.get("bot_app_id") or identity.get("bot_open_id")):
                continue
            if capture_mention is not None:
                channel = target.get("channel")
                channel = channel if isinstance(channel, Mapping) else {}
                chat_id = str(channel.get("chat_id") or "")
                kind = str(routing.get("conversation_kind") or "goal")
                route = {
                    "goal_id": str(goal_id),
                    "connection_id": str(binding.get("connection_id") or ""),
                    "agent_id": str(binding.get("agent_id") or "codex"),
                    "session_id": session_id,
                    "conversation_kind": kind,
                    "manager_channel_id": (
                        manager_channel(
                            provider="lark",
                            audience=f"{sender_profile or 'default'}\0{chat_id}",
                        )
                        if kind == "manager"
                        else ""
                    ),
                    "ingress_mode": ingress_mode,
                }
                if capture_mention(route).get("binding_matches") is not True:
                    continue
            return True
    return False


class ProfileTopicIntake:
    """Turn durable Inbox inputs into coalesced wakes on the existing lanes."""

    def __init__(self, root: Path, provider_ref: str) -> None:
        self._root = root
        self._provider_ref = provider_ref
        self._sources: dict[str, LarkTopicInboxIntake] = {}
        self._pages: dict[tuple[str, tuple[str, ...]], deque[Path]] = {}

    def _open(self, config_path: Path) -> LarkTopicInboxIntake:
        return LarkTopicInboxIntake(
            project=self._root, config_path=config_path, provider_ref=self._provider_ref,
        )

    @staticmethod
    def _offer(dispatch: ProfileEventDispatch, inbox_key: str, lane: tuple[str, ...]) -> None:
        dispatch.submit(lane, json.dumps({"intake": inbox_key, "lane": lane}), durable=True)

    def retains(self, message_id: str) -> bool:
        return any(inbox.retains(message_id) for inbox in self._sources.values())

    def capture(
        self, dispatch: ProfileEventDispatch, route: TopicIntakeRoute, lane: tuple[str, ...],
        event: Mapping[str, object], mention: dict[str, object] | None,
        config_path: Callable[[], Path],
        *, live_turn_capture: dict[str, object] | None = None,
    ) -> bool:
        key = route.inbox_key
        if key not in self._sources:
            self._sources[key] = self._open(config_path())
        captured = self._sources[key].capture(
            event=event, route_ref=route.digest, lane=lane, mention_capture=mention,
            live_turn_capture=live_turn_capture,
        )
        self._offer(dispatch, key, lane)
        return captured

    def dispatch_next(
        self, dispatch: ProfileEventDispatch, inbox_key: str, lane: tuple[str, ...],
        stop: threading.Event, prepare: Callable[[str, LarkTopicInput], Callable[[], None]],
    ) -> None:
        if stop.is_set():
            return
        inbox = self._sources[inbox_key]
        key = (inbox_key, lane)
        page = self._pages.get(key)
        if page is None:
            page = deque(inbox.pending(lane, limit=32))
            if not page:
                return
            self._pages[key] = page
        path = page.popleft()
        packet = inbox.read(path)
        if packet is not None and not packet["started"] and packet["lane"] == lane:
            run = prepare(inbox_key, packet)
            if stop.is_set():
                return
            inbox.begin(path, packet)
            run()
            inbox.finish(path)
        if not page:
            self._pages.pop(key)
        # Check for the next page or a new capture after giving other sources
        # sharing this Session their usual place in the dispatcher.
        self._offer(dispatch, inbox_key, lane)

    def restore(
        self, dispatch: ProfileEventDispatch,
        matches: Callable[[str, LarkTopicInput], bool],
    ) -> None:
        config_dir = self._root / ".loopx" / "config" / "lark-goal-topics"
        for config_path in config_dir.glob("*.json"):
            key = config_path.stem
            if not re.fullmatch(r"[a-f0-9]{20}", key):
                continue
            state_dir = self._root / ".loopx" / "inbox" / "lark-goal-topics" / key / ".state" / "topic-intake"
            if not state_dir.is_dir():
                continue
            inbox = self._open(config_path)
            if not inbox.has_retained_input:
                continue
            self._sources[key] = inbox
            restored = set()
            for path in inbox.pending():
                packet = inbox.read(path)
                if packet is None or packet["lane"] in restored or not matches(key, packet):
                    continue
                self._offer(dispatch, key, packet["lane"])
                restored.add(packet["lane"])
