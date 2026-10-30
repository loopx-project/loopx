import datetime
import importlib
import time

_original_datetime = datetime.datetime
_epoch = 1793235600


class FixedDateTime(_original_datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(_epoch, tz)

    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(_epoch, datetime.timezone.utc).replace(tzinfo=None)


datetime.datetime = FixedDateTime
time.time = lambda: _epoch
rollout_event_log = importlib.import_module("loopx.rollout_event_log")

_original_event = rollout_event_log.build_rollout_event
_event_counter = 0


def stable_event(**kwargs):
    global _event_counter
    _event_counter += 1
    event = _original_event(**kwargs)
    event["event_id"] = f"controlled-event-{_event_counter:08d}"
    return event


rollout_event_log.build_rollout_event = stable_event

# Capture allocation is fixture randomness, not a latency clock. Each measured
# invocation gets the same disposable path; the harness resets it beforehand.
import os
import pathlib
import tempfile
_original_mkdtemp = tempfile.mkdtemp
_capture_number = 0

def capture_mkdtemp(suffix=None, prefix=None, dir=None):
    global _capture_number
    if prefix == "decision-" and dir is not None and pathlib.Path(dir).name == "captures":
        _capture_number += 1
        destination = pathlib.Path(dir) / ("decision-" + os.environ.get("C2_CAPTURE_LABEL", "measured")
            + "-" + str(_capture_number).zfill(4))
        destination.mkdir(mode=0o700)
        return str(destination)
    return _original_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)

tempfile.mkdtemp = capture_mkdtemp
