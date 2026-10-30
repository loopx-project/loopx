def native_capture_wire(descriptor, *, recovery=None):
    """Independent saved-byte oracle; full delivery needs no duplicate wire file.

    Native requests must still consume every returned page. This fixture reads
    the exact capture separately to verify those actual model-visible bytes.
    """
    import hashlib
    from pathlib import Path
    from loopx.control_plane.quota.turn_envelope import prepare_context_delivery

    raw = Path(descriptor["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
    if descriptor["mode"] != "full":
        return json.loads(raw)
    target = descriptor["target"]
    wire = prepare_context_delivery(json.loads(raw),
        scope={key: target[key] for key in ("goal_id", "agent_id", "session_id", "context_epoch", "continuity_generation")
            if key in target}, capture_id=target["capture_id"], surface=target["surface"])
    assert wire["target"] == target
    wire["recovery"] = recovery if recovery is not None else descriptor["recovery"]
    return wire
