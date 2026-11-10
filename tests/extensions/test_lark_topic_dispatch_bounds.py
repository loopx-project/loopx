"""Bounded transport workers are not a second admission or replay owner."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading

import pytest

from loopx.extensions.lark.goal_topic_dispatch import ProfileEventDispatch


def test_one_busy_source_and_shared_session_leave_capacity_for_an_independent_role():
    active, release, independent, shared = [threading.Event() for _ in range(4)]
    observed = []

    def handle(line):
        observed.append(line)
        if line == "slow":
            active.set()
            assert release.wait(5)
        elif line == "other":
            independent.set()
        elif line == "shared":
            shared.set()

    dispatch = ProfileEventDispatch(handle, threading.Event(), workers=2, capacity=8)
    try:
        assert dispatch.submit(("topic-a", "session-a"), "slow")
        assert active.wait(5)
        # Rebinds retain source FIFO; a different source sharing the Session
        # must wait without using a worker blocked on a per-Session lock.
        assert dispatch.submit(("topic-a", "session-new"), "followup")
        assert dispatch.submit(("topic-b", "session-a"), "shared")
        assert dispatch.submit(("topic-c", "session-c"), "other")
        assert independent.wait(3)
        assert not shared.is_set()
        assert "followup" not in observed
    finally:
        release.set()
        dispatch.close()
    assert sorted(observed) == ["followup", "other", "shared", "slow"]
    assert observed[:2] == ["slow", "other"]


@pytest.mark.parametrize("stop_requested", [False, True])
def test_capacity_backpressures_and_stop_discards_only_waiting_transport_lines(
    stop_requested,
):
    active, release, submitting = [threading.Event() for _ in range(3)]
    stop = threading.Event()
    observed = []

    def handle(line):
        observed.append(line)
        if line == "active":
            active.set()
            assert release.wait(5)

    dispatch = ProfileEventDispatch(handle, stop, workers=1, capacity=2)
    try:
        assert dispatch.submit(("source",), "active")
        assert active.wait(5)
        assert dispatch.submit(("source",), "waiting")

        def submit():
            submitting.set()
            return dispatch.submit(("other",), "next")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(submit)
            try:
                assert submitting.wait(5)
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.1)
                if stop_requested:
                    stop.set()
                    assert future.result(timeout=2) is False
                else:
                    release.set()
                    assert future.result(timeout=3) is True
            finally:
                release.set()
    finally:
        release.set()
        dispatch.close()
    assert (
        observed == ["active"]
        if stop_requested
        else sorted(observed) == ["active", "next", "waiting"]
    )


def test_handler_failure_cancels_waiting_lines_and_surfaces_without_replay():
    active, release = threading.Event(), threading.Event()
    observed = []

    def handle(line):
        observed.append(line)
        active.set()
        assert release.wait(5)
        raise OSError("synthetic transport handler failure")

    dispatch = ProfileEventDispatch(handle, threading.Event(), workers=1, capacity=2)
    try:
        assert dispatch.submit(("source",), "first")
        assert active.wait(5)
        assert dispatch.submit(("source",), "waiting")
    finally:
        release.set()
        with pytest.raises(OSError, match="synthetic transport handler failure"):
            dispatch.close()
    assert observed == ["first"]


def test_unplanned_reader_failure_keeps_active_ownership_without_dispatching_waiting_lines():
    active, release = threading.Event(), threading.Event()
    observed = []

    def handle(line):
        observed.append(line)
        active.set()
        assert release.wait(5)

    dispatch = ProfileEventDispatch(handle, threading.Event(), workers=1, capacity=2)
    assert dispatch.submit(("source",), "active")
    assert active.wait(5)
    assert dispatch.submit(("source",), "waiting")
    with ThreadPoolExecutor(max_workers=1) as executor:
        closing = executor.submit(dispatch.close, cancel_pending=True)
        try:
            with pytest.raises(TimeoutError):
                closing.result(timeout=0.1)
        finally:
            release.set()
        closing.result(timeout=3)
    assert observed == ["active"]


def test_disabling_priority_reservation_restores_four_ordinary_workers():
    entered = [threading.Event() for _ in range(4)]
    release = threading.Event()
    observed = []

    def handle(line):
        observed.append(line)
        entered[int(line)].set()
        assert release.wait(5)

    dispatch = ProfileEventDispatch(
        handle, threading.Event(), workers=4, capacity=8, reserve_priority=True,
    )
    try:
        for index in range(4):
            assert dispatch.submit((f"source-{index}",), str(index))
        assert all(event.wait(3) for event in entered[:3])
        assert not entered[3].is_set()
        dispatch.set_priority_reservation(False)
        assert entered[3].wait(3)
    finally:
        release.set()
        dispatch.close()
    assert sorted(observed) == ["0", "1", "2", "3"]


def test_reserved_priority_admits_steering_when_ordinary_capacity_is_full():
    active, release, steered, submitting = [threading.Event() for _ in range(4)]
    observed = []

    def handle(line):
        observed.append(line)
        if line == "ordinary-0":
            active.set()
            assert release.wait(5)
        elif line == "steer":
            steered.set()

    dispatch = ProfileEventDispatch(
        handle, threading.Event(), workers=2, capacity=4, reserve_priority=True
    )
    try:
        assert dispatch.submit(("source",), "ordinary-0")
        assert active.wait(5)
        assert dispatch.submit(("source",), "ordinary-1")
        assert dispatch.submit(("source",), "ordinary-2")
        assert dispatch.ordinary_full

        def submit_overflow():
            submitting.set()
            return dispatch.submit(("source",), "ordinary-3")

        with ThreadPoolExecutor(max_workers=1) as executor:
            overflow = executor.submit(submit_overflow)
            try:
                assert submitting.wait(5)
                with pytest.raises(TimeoutError):
                    overflow.result(timeout=0.1)
                assert dispatch.submit(("steer.source",), "steer", priority=True)
                assert steered.wait(3)
                assert observed[:2] == ["ordinary-0", "steer"]
            finally:
                release.set()
            assert overflow.result(timeout=3) is True
    finally:
        release.set()
        dispatch.close()
    assert observed == ["ordinary-0", "steer", "ordinary-1", "ordinary-2", "ordinary-3"]
