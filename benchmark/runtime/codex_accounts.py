"""Prepare a private login for an explicitly authorized benchmark continuation.

The optional native account provider owns credential IO and TypeScript quota
selection. This host-side composition never switches the desktop login or
launches/replays a worker. Run it on the native account owner's host, then use
the resulting CODEX_AUTH_JSON_PATH with the ordinary checkpoint receiver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


def _private(path: Path) -> None:
    if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents))
            or not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError("Private login participant is not trusted")


def prepare(*, source_login: Path, destination: Path, slots: list[str],
            operation_id: str, run_id: str, deadline_epoch: int,
            execute: bool = False, native=None, clock=time.time) -> dict:
    # No credential provider is discovered unless the operator invokes this
    # explicit path. Ordinary benchmark launches retain their existing auth.
    from loopx_codex_provider_routing.native import policy
    from loopx_codex_provider_routing.native_host import HostError, NativeHost, discover, read_usage

    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", operation_id)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id)
            or type(deadline_epoch) is not int or not 0 < deadline_epoch < 2**53
            or deadline_epoch <= clock() or not destination.is_absolute()
            or any(p.is_symlink() for p in (destination, *destination.parents))
            or not 1 <= len(slots) <= 16 or len(set(slots)) != len(slots)
            or any(not isinstance(s, str) or not s for s in slots)):
        raise ValueError("Explicit source, slots, operation and unexpired original deadline required")
    _private(source_login)
    native = native or NativeHost(discover())
    source = native.accounts.read_private(source_login)
    identity = native.accounts.identity(source)
    binding = dict(operation_id=operation_id, run_id=run_id, slots=slots,
                   source_identity=identity, original_deadline_epoch=deadline_epoch)
    receipt_path, login_path = destination / "continuation-login.json", destination / "auth.json"
    if destination.exists():
        # A missing or uncertain completion is inspected, never overwritten.
        _private(receipt_path)
        _private(login_path)
        prior = json.loads(receipt_path.read_bytes())
        if (prior.get("binding") != binding
                or hashlib.sha256(native.accounts.read_private(login_path)).hexdigest()
                != prior.get("login_sha256")):
            raise ValueError("Continuation destination changed; inspect the retained attempt")
        return {**prior["result"], "idempotent": True,
                "fresh_allowance_required_before_launch": True}

    before = native.status()
    expected = before["selection_revision"]
    if (before["recovery_required"] or before["current_login_matches"] is not True
            or not set(slots) <= set(before["slots"])):
        raise ValueError("Native account ownership is unavailable")
    identities, observations = {}, []
    for slot in slots:
        path = native.manager.auth if slot == before["active_slot"] else native.manager.slot(slot)
        raw = native.accounts.read_private(path)
        identities[slot] = native.accounts.identity(raw)
        try:
            usage = read_usage(raw)
            observation = dict(slot=slot, observed_at=clock(), status="ok", usage=usage)
        except HostError as error:
            observation = dict(slot=slot, observed_at=clock(),
                status="auth_required" if error.code == "auth_required" else "unavailable")
        observations.append(observation)
    source_slots = [slot for slot, value in identities.items() if value == identity]
    if len(source_slots) != 1 or native.status()["selection_revision"] != expected:
        raise ValueError("Source login or native selection changed")
    decision = policy(dict(now=clock(), policy=dict(enabled=True, slots=slots, cooldown_seconds=300),
        active=source_slots[0], observations=observations, last_switch_at=None,
        recovery_required=False, login_matches=True))
    result = dict(decision=decision, source_slot=source_slots[0], prepared=False,
                  idempotent=False, native_selection_changed=False, model_calls=0)
    if decision["action"] != "switch" or not execute:
        return result
    target = decision["target"]
    path = native.manager.auth if target == before["active_slot"] else native.manager.slot(target)
    raw = native.accounts.read_private(path)
    if (native.accounts.identity(raw) != identities[target]
            or native.status()["selection_revision"] != expected
            or clock() >= min(deadline_epoch, decision["valid_until"])):
        raise ValueError("Selected login or quota observation changed")
    # The existing native reader supplies the exact login; no token parser,
    # OAuth implementation, refresh, global selection or second account vault.
    destination.mkdir(mode=0o700, exist_ok=False)
    fd = os.open(login_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    result.update(prepared=True, selected_slot=target,
                  fresh_allowance_required_before_launch=True)
    receipt = dict(binding=binding, result=result, login_sha256=hashlib.sha256(raw).hexdigest())
    fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-login", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--slots", nargs="+", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--deadline-epoch", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(source_login=args.source_login, destination=args.destination,
            slots=args.slots, operation_id=args.operation_id, run_id=args.run_id,
            deadline_epoch=args.deadline_epoch, execute=args.execute)
        print(json.dumps(dict(ok=True, **result)))
        return 0
    except Exception:
        # Native exceptions can contain private paths or transport diagnostics.
        print(json.dumps(dict(ok=False, code="continuation_login_unavailable")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
