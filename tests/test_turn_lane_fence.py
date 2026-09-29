"""One executing bounded Turn per Turn lane.

The fence is a process-level lock, so these tests hold the lane the same way a
running Turn does and assert what the second Turn is told. Admission and release
after a settled Turn are covered end to end by the public dsh smokes, which run
one Turn and then its replay through the same entry.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

from loopx.control_plane.turn_driver.executor import run_loopx_turn_once
from loopx.file_lock import _safe_label, lock_holder_path
from loopx.control_plane.turn_driver import lane_fence
from loopx.control_plane.turn_driver.lane_fence import (
    REMEDY_WAIT_FOR_IN_FLIGHT_TURN,
    single_executor_per_turn_lane,
    TURN_LANE_ABSENT,
    TURN_LANE_DEAD,
    TURN_LANE_FOREIGN_HOST,
    TURN_LANE_IN_FLIGHT,
    TURN_LANE_LIVE,
    TURN_LANE_OPERATION,
    TURN_LANE_RELEASED,
    TURN_LANE_UNREADABLE,
    turn_lane_holder_readback,
    turn_lane_liveness,
    turn_lane_singleflight,
    turn_lane_target,
)

GOAL_ID = "lane-fence-goal"
AGENT_ID = "lane-fence-agent"


def _plan(*, agent_id: str = AGENT_ID) -> dict:
    return {
        "host": {"kind": "dsh", "execution_mode": "isolated-headless"},
        "route": {"kind": "ready_for_host", "would_invoke_host": True},
        "turn_envelope": {"agent_id": agent_id, "goal_id": GOAL_ID},
        "transaction": {"turn_key": "sha256:" + "1" * 64},
    }


def _execute(tmp_path: Path, *, execute: bool = True, plan: dict | None = None) -> dict:
    return run_loopx_turn_once(
        plan or _plan(),
        host_argv=["python3", "-c", "raise SystemExit(0)"],
        project=tmp_path,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        timeout_seconds=1.0,
        execute=execute,
    )


def test_a_second_executing_turn_refuses_while_one_holds_the_lane(
    tmp_path: Path,
) -> None:
    with turn_lane_singleflight(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, plan=_plan()
    ) as held:
        assert held is not None
        payload = _execute(tmp_path)

    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["reason"] == TURN_LANE_IN_FLIGHT
    assert payload["remediation"] == [REMEDY_WAIT_FOR_IN_FLIGHT_TURN]
    assert payload["effects"] == {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    assert payload["quota_slot_spend_count"] == 0
    # The refusal names the holder, so an operator can tell what to wait for.
    assert payload["in_flight"]["agent_id"] == AGENT_ID
    assert payload["in_flight"]["operation"] == TURN_LANE_OPERATION
    assert isinstance(payload["in_flight"]["pid"], int)
    assert payload["in_flight"]["acquired_at"]
    # A refusal is a readback, not an invocation of the planned host.
    assert payload["host"] == {"executable": "not_invoked", "kind": "dsh"}


def test_a_preview_never_takes_the_lane(tmp_path: Path) -> None:
    with turn_lane_singleflight(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, plan=_plan()
    ) as held:
        assert held is not None
        payload = _execute(tmp_path, execute=False)

    assert payload["ok"] is True
    assert payload["status"] == "preview"


def test_the_lane_is_goal_and_agent_scoped(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    same_lane = turn_lane_target(runtime_root=root, goal_id=GOAL_ID, plan=_plan())
    other_agent = turn_lane_target(
        runtime_root=root, goal_id=GOAL_ID, plan=_plan(agent_id="another-agent")
    )
    other_goal = turn_lane_target(
        runtime_root=root, goal_id="another-goal", plan=_plan()
    )

    assert same_lane == turn_lane_target(
        runtime_root=root, goal_id=GOAL_ID, plan=_plan()
    )
    assert len({same_lane, other_agent, other_goal}) == 3
    assert AGENT_ID in same_lane.name
    # A plan without an agent identity still gets a lane instead of no fence.
    unattributed = turn_lane_target(
        runtime_root=root, goal_id=GOAL_ID, plan={"turn_envelope": {}}
    )
    assert unattributed not in {same_lane, other_agent, other_goal}


def test_the_holder_readback_stays_public_safe(tmp_path: Path) -> None:
    target = turn_lane_target(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, plan=_plan()
    )
    with turn_lane_singleflight(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, plan=_plan()
    ):
        holder = turn_lane_holder_readback(target)

    assert holder["agent_id"] == AGENT_ID
    assert holder["operation"] == TURN_LANE_OPERATION
    assert isinstance(holder["pid"], int)
    # The machine name travels with the pid: two hosts can share one runtime
    # root, and a pid without its host is not an actionable identity.
    assert holder["host"] == _safe_label(socket.gethostname(), fallback="unknown")
    assert set(holder) == {"agent_id", "operation", "pid", "acquired_at", "host"}
    # The private lock identity and the runtime path never leave the process.
    assert str(tmp_path) not in str(holder)
    assert turn_lane_holder_readback(tmp_path / "absent.lane") == {}


def _lane(tmp_path: Path) -> Path:
    return turn_lane_target(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, plan=_plan()
    )


def _rewrite_holder(target: Path, **changes: object) -> None:
    """Edit the holder record the way a crash or another machine would leave it."""

    holder_path = lock_holder_path(target)
    record = json.loads(holder_path.read_text(encoding="utf-8"))
    record.pop("released_at", None)
    record.update(changes)
    holder_path.write_text(json.dumps(record), encoding="utf-8")


def test_liveness_follows_the_lane_from_absent_to_live_to_released(
    tmp_path: Path,
) -> None:
    target = _lane(tmp_path)
    assert turn_lane_liveness(target) == {"state": TURN_LANE_ABSENT, "holder": {}}

    with turn_lane_singleflight(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, plan=_plan()
    ) as held:
        assert held is not None
        live = turn_lane_liveness(target)

    assert live["state"] == TURN_LANE_LIVE
    # The holder is the same public-safe readback a refusal names.
    assert live["holder"] == turn_lane_holder_readback(target) | {"pid": os.getpid()}
    assert live["holder"]["pid"] == os.getpid()
    assert str(tmp_path) not in json.dumps(live)
    # A clean exit is a release, whatever the pid does afterwards.
    assert turn_lane_liveness(target)["state"] == TURN_LANE_RELEASED


def test_liveness_fails_closed_on_dead_foreign_and_unreadable_holders(
    tmp_path: Path,
) -> None:
    target = _lane(tmp_path)
    with turn_lane_singleflight(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, plan=_plan()
    ):
        pass

    # A killed Turn never writes released_at; its pid is gone on this machine.
    dead_pid = os.getpid()
    while True:
        dead_pid += 1
        try:
            os.kill(dead_pid, 0)
        except ProcessLookupError:
            break
        except OSError:
            continue
        if dead_pid > os.getpid() + 100_000:
            raise AssertionError("no free pid found near this process")
    _rewrite_holder(target, pid=dead_pid)
    assert turn_lane_liveness(target)["state"] == TURN_LANE_DEAD

    # A holder on another machine cannot be pid-checked here, even if that pid
    # happens to be alive on this one.
    _rewrite_holder(target, pid=os.getpid(), host="another-machine")
    foreign = turn_lane_liveness(target)
    assert foreign["state"] == TURN_LANE_FOREIGN_HOST
    assert foreign["holder"]["host"] == "another-machine"

    # A lock file with no parseable record is mid-acquisition or corrupt: not
    # absent, and not evidence of anything.
    lock_holder_path(target).write_text("", encoding="utf-8")
    assert turn_lane_liveness(target) == {"state": TURN_LANE_UNREADABLE, "holder": {}}
    lock_holder_path(target).write_text("{}", encoding="utf-8")
    assert turn_lane_liveness(target)["state"] == TURN_LANE_UNREADABLE


def test_the_liveness_probe_never_refuses_a_concurrent_executing_turn(
    tmp_path: Path, monkeypatch
) -> None:
    """The probe reads a record; it never takes the lane, not even for an instant."""

    fence_calls: list[str] = []
    real_fence = lane_fence.try_exclusive_file_lock

    def counting_fence(*args, **kwargs):
        fence_calls.append(str(kwargs.get("operation")))
        return real_fence(*args, **kwargs)

    monkeypatch.setattr(lane_fence, "try_exclusive_file_lock", counting_fence)
    target = _lane(tmp_path)
    turn_lane_liveness(target)
    turn_lane_holder_readback(target)
    assert fence_calls == []

    # The executing entry is the real fence wrapper run-once --execute goes
    # through; only the Turn body is a stand-in that holds the lane a moment.
    @single_executor_per_turn_lane(
        lambda plan, record, **kwargs: {**record, "effects": kwargs["effects"]}
    )
    def executing_turn(plan, *, runtime_root, goal_id, execute):
        time.sleep(0.3)
        return {"status": "committed", "held": turn_lane_liveness(target)["state"]}

    observed: set[str] = set()
    stop = threading.Event()

    def probe() -> None:
        while not stop.is_set():
            observed.add(turn_lane_liveness(target)["state"])

    prober = threading.Thread(target=probe, daemon=True)
    prober.start()
    try:
        payload = executing_turn(
            _plan(), runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, execute=True
        )
    finally:
        stop.set()
        prober.join(timeout=5)

    # The Turn took the fence exactly once and was never told the lane was busy.
    assert fence_calls == [TURN_LANE_OPERATION]
    assert payload == {"status": "committed", "held": TURN_LANE_LIVE}
    assert payload.get("reason") != TURN_LANE_IN_FLIGHT
    assert observed <= {TURN_LANE_ABSENT, TURN_LANE_LIVE, TURN_LANE_RELEASED, TURN_LANE_UNREADABLE}
    assert TURN_LANE_LIVE in observed
    assert turn_lane_liveness(target)["state"] == TURN_LANE_RELEASED
