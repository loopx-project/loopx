"""Reader references hide repeated digests without weakening byte verification."""
import hashlib
import json
from pathlib import Path
import shlex
import sys

import pytest

from loopx.cli_commands.quota_capture import model_context_reference, native_context_capture_view, read_context_capture
from loopx.control_plane.turn_driver.codex_context import _context_pages, _verified_context_pages
from loopx.control_plane.turn_driver.decision_capture import (
    capture_page_range, read_context_read_reference, write_context_read_reference,
)
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload


def read(command, **overrides):
    from loopx.cli import build_parser

    args = build_parser().parse_args(shlex.split(command)[1:])
    for key, value in overrides.items():
        setattr(args, key, value)
    return read_context_capture(args)


def pinned_initial_view(payload, directory):
    base = native_context_capture_view(payload, directory)
    from loopx.control_plane.quota import turn_envelope

    plan = turn_envelope._turn_envelope_result("context_select", context=payload, initial_read=True)
    capture = {key: base["recovery"][key] for key in ("path", "sha256")}
    return native_context_capture_view(payload, directory, captured_recovery=base["recovery"],
        max_page_output_bytes=64000,
        initial_read_plan={"initial": True, "plan": plan, "capture": capture})


def fixture(tmp_path):
    payload = {"ok": True, "interaction_contract": {"schema_version": "loopx_interaction_contract_v0"},
        "goal_boundary": {"stop": "Do not extend the original budget."},
        "selected_todo": {"todo_id": "todo_fixture", "text": "Compare 原始条件。" * 900},
        "criterion": {"value": 9007199254740993, "stop": "No publication is authorized."}}
    view = pinned_initial_view(payload, tmp_path)
    return payload, view, model_context_reference(view)


@pytest.mark.parametrize("listing", [False, True])
@pytest.mark.parametrize("selectors", [
    [""], ["/a~1b~0c/0/number"], ["/a~1b~0c", "/a~1b~0c/0/number"],
    ["/", "/0/01"], ["/scalar/child"], ["/null/child"], ["/empty/child"],
    ["/a~1b~0c/3"], ["/a~1b~0c/01"], ["/a~1b~0c/-1"],
    ["/a~1b~0c/9007199254740992"], ["/a~1b~0c/0/absent/child"],
    ["/a~1b~0c/0/number", "/missing"], ["/invalid~2"], ["invalid"],
])
def test_selected_structure_preserves_original_reader_and_recovery(tmp_path, monkeypatch, selectors, listing):
    from loopx.control_plane.turn_driver import decision_capture

    payload = {"a/b~c": [{"number": 10**45 + 17}, {"tail": "完整原值"}],
        "": "empty-key", "0": {"01": -0.0}, "scalar": "source text", "null": None,
        "empty": {}, "unread": {f"sibling_{index}": {"value": index} for index in range(200)}}
    source = tmp_path / "source.json"
    original = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
    source.write_bytes(original)
    source.chmod(0o600)
    command = shlex.join(["loopx", "--format", "json", "quota", "context-read", "--capture-file", str(source),
        "--capture-sha256", hashlib.sha256(original).hexdigest(), "--page-bytes", "64000",
        "--max-page-output-bytes", "64000"])
    # The unpruned transport independently exercises the original typed owner,
    # including its failed-batch precedence and nearest-container remediation.
    with monkeypatch.context() as old:
        old.setattr(decision_capture, "context_selection_transport", lambda value, *args, **kwargs: value)
        expected = read(command, select=selectors, list_fields=listing, list_depth=2 if listing else None)
    observed = read(command, select=selectors, list_fields=listing, list_depth=2 if listing else None)
    assert observed == expected
    assert source.read_bytes() == original
    if not listing and selectors == ["/a~1b~0c/0/number"]:
        assert json.loads(observed["content"])["selections"][0]["value"] == 10**45 + 17
    if not observed["ok"] and "recovery_command" in observed:
        assert read(observed["recovery_command"])["ok"], "Recovery must still discover the complete original parent"


def test_scoped_discovery_keeps_unrelated_values_out_of_the_runtime_request(tmp_path, monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    sizes = []
    evaluate = turn_envelope.effect_runtime_projection_result

    def measure(method, request):
        if request.get("operation") == "context_select":
            sizes.append(len(json.dumps(request, ensure_ascii=False).encode()))
        return evaluate(method, request)

    monkeypatch.setattr(turn_envelope, "effect_runtime_projection_result", measure)
    template = b'{"selected":{"answer":42},"unrelated":""}'
    raw = template[:-2] + b"x" * 100_000 + template[-2:]
    source = tmp_path / "decision.json"
    source.write_bytes(raw)
    command = shlex.join(["loopx", "--format", "json", "quota", "context-read",
        "--capture-file", str(source), "--capture-sha256", hashlib.sha256(raw).hexdigest()])
    page = read(command, select=["/selected"], list_fields=True)
    assert page["ok"] and page["eof"], page
    assert sizes and sum(sizes) < len(raw) // 100
    assert json.loads(page["content"])["selections"] == [{"pointer": "/selected", "fields": [
        {"pointer": "/selected/answer", "type": "number", "bytes": 2}]}]


def assert_section_read_only(page, machine):
    raw = Path(machine["path"]).read_bytes()
    reference = {"path": machine["path"], "sha256": machine["sha256"]}
    pages = _context_pages(page)
    assert pages == [page]
    assert capture_page_range(page, raw=raw, digest=machine["sha256"]) is None
    observed = _verified_context_pages({"source_recovery": reference, "recovery": reference}, pages)
    assert len(observed) == 1, "a valid selected page must reach the section-read owner"
    assert observed[0]["source_sha256"] == machine["sha256"]
    # With no terminal range in this observation or the journal, verified
    # bytes can contribute coverage while section digests are deferred.
    if page["eof"]:
        assert observed[0]["read_sections"]
    else:
        assert observed[0]["read_sections"] == {}
    assert capture_page_range(observed[0], raw=raw, digest=machine["sha256"]) is None


@pytest.mark.parametrize("mode", ["full", "delta"])
@pytest.mark.parametrize("reference", [False, True], ids=["explicit", "navigation"])
def test_policy_oracle_reads_complete_current_recovery(tmp_path, mode, reference):
    payload, machine, model = fixture(tmp_path)
    # Full and delta transports both expose the current complete recovery.
    # A decoy transport/baseline must never replace those facts in a policy test.
    view = dict(model if reference else machine, mode=mode)
    view["path"] = str(tmp_path / "unavailable-delta.json")
    view["baseline_reference"] = {"path": str(tmp_path / "old-capture.json")}
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    assert read_cli_policy_payload(json.dumps(view), returncode=0) == payload
    assert {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == before


@pytest.mark.parametrize("returncode", [0, 1])
def test_policy_oracle_preserves_inline_output_and_failed_commands(tmp_path, returncode):
    inline = {"ok": returncode == 0, "decision": "skip", "error": "original failure"}
    assert read_cli_policy_payload(json.dumps(inline), returncode=returncode) == inline
    if returncode:
        _, _, model = fixture(tmp_path)
        failed = {**model, "ok": False, "error": "command failed after capture"}
        # Keep the failure even if a complete successful capture is available.
        assert read_cli_policy_payload(json.dumps(failed), returncode=returncode) == failed


@pytest.mark.parametrize("damage", ["digest", "capture_symlink", "reference_symlink"])
def test_policy_oracle_rejects_replaced_current_capture(tmp_path, damage):
    _, machine, model = fixture(tmp_path)
    if damage == "digest":
        capture = Path(machine["path"])
        capture.write_bytes(capture.read_bytes() + b" ")
    else:
        target = Path(machine["path"] if damage == "capture_symlink" else model["recovery"]["reference_file"])
        saved = target.with_suffix(".original")
        target.rename(saved)
        target.symlink_to(saved)
    with pytest.raises(ValueError, match="digest mismatch|physical.*symlink"):
        read_cli_policy_payload(json.dumps(model), returncode=0)


def test_native_navigation_keeps_digests_in_immutable_reference(tmp_path):
    payload, machine, model = fixture(tmp_path)
    raw = Path(machine["path"]).read_bytes()
    assert json.loads(raw) == payload
    assert machine["sha256"] == hashlib.sha256(raw).hexdigest()
    assert machine["sha256"] not in json.dumps(model)
    assert "--capture-sha256" not in json.dumps(model)
    _, descriptor = read_context_read_reference(model["reference_file"])
    assert descriptor["path"] == machine["path"] and descriptor["sha256"] == machine["sha256"]
    assert descriptor["navigation"] == machine  # complete machine metadata remains recoverable
    assert Path(model["reference_file"]).stat().st_mode & 0o077 == 0
    # Same immutable source and full-page observer compatibility.
    page = read(model["command"])
    assert page["schema_version"] == "context_capture_page_v1"
    assert capture_page_range(page, raw=raw, digest=machine["sha256"]) == (0, len(raw))


def test_selected_continuations_hide_both_digests_and_preserve_exact_values(tmp_path):
    payload, machine, model = fixture(tmp_path)
    page = read(model["recovery"]["command"], select=["/selected_todo", "/criterion"],
        max_page_output_bytes=3000, page_bytes=3000)
    parts = []
    while True:
        assert page["ok"] and page["schema_version"] == "context_capture_selection_page_v1"
        assert not any(key in page for key in ("sha256", "selection_sha256", "selected_pointers"))
        assert_section_read_only(page, machine)
        parts.append(page["content"])
        if page["eof"]:
            break
        command = page["next_command"]
        assert "--selection-sha256" not in command and "--capture-sha256" not in command
        assert page["next_offset"] == len("".join(parts).encode())
        page = read(command)
    values = {item["pointer"]: item["value"] for item in json.loads("".join(parts))["selections"]}
    assert values == {"/selected_todo": payload["selected_todo"], "/criterion": payload["criterion"]}
    _, descriptor = read_context_read_reference(page["reference_file"])
    assert descriptor["sha256"] == machine["sha256"] and len(descriptor["selection"]["sha256"]) == 64
    changed = read(model["command"], reference_file=page["reference_file"], select=["/criterion"])
    assert changed["ok"] is False and "cannot change selectors" in changed["reason"]


def test_explicit_initial_cursor_rejects_changed_selection_profile(tmp_path, monkeypatch):
    _, machine, _ = fixture(tmp_path)
    first = read(machine["first_read_command"], max_page_output_bytes=3000, page_bytes=3000)
    assert first["ok"] and not first["eof"]
    cursor = shlex.split(first["next_command"])
    assert {"--initial-read", "--selection-sha256"} <= set(cursor)
    from loopx.control_plane.quota import turn_envelope

    original = turn_envelope._turn_envelope_result

    def changed(operation, **kwargs):
        result = original(operation, **kwargs)
        result["selections"].pop()
        return result

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", changed)
    rejected = read(first["next_command"])
    assert rejected["ok"] is False and "digest changed" in rejected["reason"]
    assert "content" not in rejected


def test_pinned_initial_reference_rejects_changed_selection_addresses(tmp_path):
    _, machine, model = fixture(tmp_path)
    first = read(model["first_read_command"], max_page_output_bytes=3000, page_bytes=3000)
    assert first["ok"] and not first["eof"]
    _, descriptor = read_context_read_reference(first["reference_file"])
    assert descriptor["selection_paths"] != [["criterion"]]
    # Matching pointer and path metadata is insufficient: the immutable
    # reference also pins the digest of the original, ordered selection.
    changed = write_context_read_reference(Path(machine["path"]), machine["sha256"],
        selection={**descriptor["selection"], "select": ["/criterion"]},
        selection_paths=[["criterion"]])
    rejected = read(first["next_command"], reference_file=str(changed))
    assert rejected["ok"] is False and "digest changed" in rejected["reason"]
    assert "content" not in rejected


@pytest.mark.parametrize("legacy", [False, True], ids=["pinned-addresses", "historical-profile"])
def test_initial_reference_navigation_change_preserves_original_read_boundary(tmp_path, monkeypatch, legacy):
    payload, machine, model = fixture(tmp_path)
    expected = read(model["first_read_command"])
    assert expected["ok"] and expected["eof"]
    first = read(model["first_read_command"], max_page_output_bytes=3000, page_bytes=3000)
    assert first["ok"] and not first["eof"]
    reference = Path(first["reference_file"])
    if legacy:
        # Historical documents stored the navigation recipe, not its addresses.
        _, descriptor = read_context_read_reference(str(reference))
        reference = write_context_read_reference(Path(machine["path"]), machine["sha256"], selection={
            **descriptor["selection"], "select": None, "initial_read": True})
    reference_before, capture_before = reference.read_bytes(), Path(machine["path"]).read_bytes()
    from loopx.control_plane.quota import turn_envelope

    original = turn_envelope._turn_envelope_result

    def changed_navigation(operation, **kwargs):
        if kwargs.get("initial_read"):
            kwargs = {**kwargs, "initial_read": False, "select": ["/criterion"]}
        return original(operation, **kwargs)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", changed_navigation)
    # The explicit recipe replans; the prepared model cursor keeps the
    # addresses selected when this immutable capture was presented.
    current = read(machine["first_read_command"])
    assert json.loads(current["content"])["selections"] == [{"pointer": "/criterion", "value": payload["criterion"]}]
    assert read(model["first_read_command"])["content"] == expected["content"]
    following = read(first["next_command"], reference_file=str(reference))
    if legacy:
        assert following["ok"] is False and "digest changed" in following["reason"]
        assert "content" not in following
        recovered = read(model["recovery"]["command"], select=["/goal_boundary", "/selected_todo"])
        assert recovered["ok"] and recovered["eof"]
        values = {row["pointer"]: row["value"] for row in json.loads(recovered["content"])["selections"]}
    else:
        parts = [first["content"]]
        while True:
            assert following["ok"] and following["schema_version"] == "context_capture_selection_page_v1"
            assert_section_read_only(following, machine)
            assert following["offset"] == len("".join(parts).encode())
            parts.append(following["content"])
            if following["eof"]:
                break
            following = read(following["next_command"])
        assert "".join(parts) == expected["content"]
        values = {row["pointer"]: row["value"] for row in json.loads("".join(parts))["selections"]}
    assert values["/goal_boundary"] == payload["goal_boundary"]
    assert values["/selected_todo"] == payload["selected_todo"]
    assert reference.read_bytes() == reference_before
    assert Path(machine["path"]).read_bytes() == capture_before


@pytest.mark.parametrize("layout", ["recorded", "legacy_canonical"])
def test_pinned_explicit_replay_keeps_the_digest_bound_order_and_original_values(tmp_path, monkeypatch, layout):
    from loopx.control_plane.quota import turn_envelope

    payload = {"z": {"text": "保留完整来源🙂" * 240, "numbers": [2**60 + 1, 1.0, -0.0], "tail": "Stop before effects."},
        "\ue000": None, "😀": {"unknown_constraint": False}}
    machine = native_context_capture_view(payload, tmp_path)
    path, digest = Path(machine["path"]), machine["sha256"]
    pointers = ["/z", "/\ue000", "/z", "/😀"]
    order = ["/z", "/\ue000", "/😀"] if layout == "recorded" else ["/z", "/😀", "/\ue000"]
    expected = json.dumps({"schema_version": "context_capture_selection_v1", "selections": [
        {"pointer": pointer, "value": payload[pointer[1:]]} for pointer in order]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    reference = write_context_read_reference(path, digest, selection={"select": pointers, "initial_read": False,
        "list_fields": False, "list_depth": None, "sha256": hashlib.sha256(expected).hexdigest()})
    before = {f: f.read_bytes() for f in tmp_path.iterdir() if f.is_file()}
    invoke = turn_envelope._turn_envelope_result
    requests = []

    def observe(operation, **kwargs):
        requests.append(kwargs)
        return invoke(operation, **kwargs)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", observe)
    command = f"loopx --format json quota context-read --reference-file {shlex.quote(str(reference))} --page-bytes 3000 --max-page-output-bytes 3500"
    content, pages, observed_pages = b"", 0, []
    while True:
        page = read(command)
        assert page["ok"] and page["offset"] == len(content), page
        assert _context_pages(page) == [page]
        assert capture_page_range(page, raw=path.read_bytes(), digest=digest) is None
        observed_pages.append(page)
        content += page["content"].encode()
        pages += 1
        if page["eof"]:
            break
        command = page["next_command"]
    assert pages > 1 and content == expected
    assert len(requests) == pages * (2 if layout == "legacy_canonical" else 1)
    assert requests[0]["preserve_selection_order"] is True
    if layout == "legacy_canonical":
        assert all(not request.get("preserve_selection_order", False) for request in requests[1::2])
    for page in observed_pages:
        assert_section_read_only(page, machine)
    assert {f: f.read_bytes() for f in before} == before


def test_resolved_initial_addresses_replay_without_replanning_the_initial_profile(tmp_path, monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    _, machine, model = fixture(tmp_path)
    page = read(model["first_read_command"], page_bytes=3000, max_page_output_bytes=3500)
    initial = b""
    while True:
        assert page["ok"] and page["offset"] == len(initial), page
        initial += page["content"].encode()
        if page["eof"]:
            break
        page = read(page["next_command"])
    addresses = [row["pointer"] for row in json.loads(initial)["selections"]]
    # The existing metadata writer can pin the exact resolved selection,
    # independently of which producer supplied those addresses.
    pinned = write_context_read_reference(Path(machine["path"]), machine["sha256"], selection={
        "select": addresses, "initial_read": False, "list_fields": False, "list_depth": None,
        "sha256": hashlib.sha256(initial).hexdigest()})
    before = {f: f.read_bytes() for f in tmp_path.iterdir() if f.is_file()}
    first = read(model["command"], reference_file=str(pinned), page_bytes=3000, max_page_output_bytes=3500)
    assert first["ok"] and not first["eof"]
    invoke = turn_envelope._turn_envelope_result

    def changed_initial(operation, **kwargs):
        assert not kwargs.get("initial_read"), "Pinned addresses must not rerun a changed initial profile"
        return invoke(operation, **kwargs)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", changed_initial)
    replay, page = first["content"].encode(), first
    while not page["eof"]:
        page = read(page["next_command"])
        assert page["ok"] and page["offset"] == len(replay), page
        replay += page["content"].encode()
    assert replay == initial
    assert {f: f.read_bytes() for f in before} == before


@pytest.mark.parametrize("kind", ["initial", "historical_initial", "pinned", "fallback_initial", "fallback_presented_initial"])
def test_native_hook_records_frozen_selection_without_full_input_retention(tmp_path, monkeypatch, kind):
    from loopx.control_plane.turn_driver import codex_context as context
    from loopx.control_plane.turn_driver.decision_capture import context_capture_read_command
    from test_codex_context_lifecycle import setup, project, deliver
    from test_progressive_native_context import payload, outcome

    event = setup(tmp_path, monkeypatch)
    original = payload()
    original["interaction_contract"] = {"schema_version": "loopx_interaction_contract_v0",
        "agent_channel": {"work_context": {"sources": []}}}
    original["selected_todo"] = {"todo_id": "todo_work", "claimed_by": "agent",
        "text": "Read the complete original requirement. " * 400}
    outcome(original)["requirements"][0]["description"] += " Keep the original Stop." * 400
    narrative_pointer = "/interaction_contract/agent_channel/work_context/sources/0/content/text"
    if kind == "fallback_presented_initial":
        objective = "Preserve the accepted outcome and original Stop. " * 40
        narrative = (f"---\nobjective: {json.dumps(objective)}\n---\n\n# Active Goal State\n\n"
                     f"## Objective\n\n> {objective}\n\n## Stop\n\nKeep the original permission boundary.\n")
        original["interaction_contract"]["agent_channel"]["work_context"]["sources"] = [{
            "source": "goal_state", "content": {"state_file": "goal.md", "full_source": True,
                "text": narrative, "revision": "sha256:" + hashlib.sha256(narrative.encode()).hexdigest()}}]
    project(tmp_path, payload=original)
    source = context._state(tmp_path)["source_recovery"]
    path, digest = Path(source["path"]), source["sha256"]
    reference = write_context_read_reference(path, digest)
    command = context_capture_read_command(path, digest, reference_file=reference)
    if kind == "pinned":
        pointers = ["/stop", "/planning_horizon"]
        expected = json.dumps({"schema_version": "context_capture_selection_v1", "selections": [
            {"pointer": pointer, "value": original[pointer[1:]]} for pointer in pointers]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        reference = write_context_read_reference(path, digest, selection={"select": pointers,
            "initial_read": False, "list_fields": False, "list_depth": None,
            "sha256": hashlib.sha256(expected).hexdigest()})
        page = read(command, reference_file=str(reference), page_bytes=3000, max_page_output_bytes=3500)
    elif kind in {"fallback_initial", "fallback_presented_initial"}:
        from loopx.cli_commands import quota_context_reader

        def unavailable(*args, **kwargs):
            raise OSError("synthetic metadata write failure")

        # Exercise the real reader fallback. It carries a selection digest
        # without the explicit pointer list or a reader reference.
        with monkeypatch.context() as blocked:
            blocked.setattr(quota_context_reader, "write_context_read_reference", unavailable)
            page = read(command, initial_read=True, page_bytes=3000, max_page_output_bytes=3500)
        assert page.get("selection_sha256") and "selected_pointers" not in page
        assert "reference_file" not in page and "--initial-read" in page["next_command"]
    else:
        page = read(command, initial_read=True, page_bytes=3000, max_page_output_bytes=3500)
        if kind == "historical_initial":
            _, descriptor = read_context_read_reference(page["reference_file"])
            reference = write_context_read_reference(path, digest, selection={
                **descriptor["selection"], "select": None, "initial_read": True})
            page = read(command, reference_file=str(reference), page_bytes=3000, max_page_output_bytes=3500)
    pages = []
    while True:
        assert page["ok"], page
        pages.append(page)
        if page["eof"]:
            break
        page = read(page["next_command"])
    assert len(pages) > 2
    before = path.read_bytes()
    deliver(tmp_path, event, pages[1:])
    assert not context._state(tmp_path).get("confirmed_read_sections"), "EOF cannot hide unread earlier pages"
    if kind in {"fallback_initial", "fallback_presented_initial"}:
        first = pages[0]
        deliver(tmp_path, event, [{**first, "selection_sha256": "0" * 64},
            {**first, "content": "!" + first["content"][1:]}])
        assert not context._state(tmp_path).get("confirmed_read_sections")
    deliver(tmp_path, event, pages[:1])
    state = context._state(tmp_path)
    assert state["confirmed_read_sections"]
    assert state["consumed_pointers"]
    assert "candidate" not in state, "section coverage cannot establish full model-input retention"
    if kind == "fallback_presented_initial":
        selected = json.loads("".join(page["content"] for page in pages))["selections"]
        narrative_row, = (row for row in selected if row["pointer"] == narrative_pointer)
        assert narrative_row["presentation"] and narrative_row["value"] != narrative
        assert narrative_pointer not in state["consumed_pointers"], "display edits cannot certify literal source text"
        literal = read(command, select=[narrative_pointer], page_bytes=64000, max_page_output_bytes=64000)
        assert literal["ok"] and literal["eof"]
        assert json.loads(literal["content"])["selections"][0]["value"] == narrative
        deliver(tmp_path, event, [literal])
        state = context._state(tmp_path)
        assert narrative_pointer in state["consumed_pointers"]
    assert path.read_bytes() == before
    _, warm = project(tmp_path, "after-selected-read", payload=original)
    current = json.loads(Path(warm["recovery"]["path"]).read_bytes())
    unread = current["context_details"]["unread_context_index"]["pointers"]
    for pointer in state["consumed_pointers"]:
        assert not any(item == pointer or item.startswith(pointer + "/") for item in unread)
    assert "candidate" not in context._state(tmp_path)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_inline_initial_reader_of_real_authority_preserves_original_effect(tmp_path, monkeypatch, provider):
    from loopx.control_plane.turn_driver import codex_context as context
    from test_codex_context_lifecycle import setup, project, deliver
    from test_replan_successor_guard_reentry import AGENT, GOAL, _fixture

    call, _, history = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    added = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the original source.", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "reader-source-verification")
    decision = call("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", "inline-initial-source", "--todo-id", added["todo_id"])
    assert decision["effective_action"] == "normal_run"
    effect = decision["heartbeat_receipt"]["settlement_identity"]
    history_before = history.read_bytes()
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    event = setup(native, monkeypatch)
    project(native, payload=decision)
    source = context._state(native)["source_recovery"]
    path = Path(source["path"])
    before = path.read_bytes()
    arguments = ["quota", "context-read", "--capture-file", str(path),
        "--capture-sha256", source["sha256"], "--initial-read",
        "--page-bytes", "4500", "--max-page-output-bytes", "5500"]
    pages = []
    while True:
        page = call(*arguments)
        assert page["ok"] and page.get("selection_sha256"), page
        assert "selected_pointers" not in page and "reference_file" not in page
        pages.append(page)
        if page["eof"]:
            break
        words = shlex.split(page["next_command"])
        arguments = words[words.index("quota"):]
    assert len(pages) > 1
    facts = {row["pointer"]: row["value"] for row in json.loads(
        "".join(page["content"] for page in pages))["selections"]}
    assert facts["/heartbeat_receipt"]["settlement_identity"] == effect
    assert facts["/selected_todo"] == decision["selected_todo"]
    deliver(native, event, pages[:-1])
    assert not context._state(native).get("consumed_pointers")
    deliver(native, event, pages[-1:])
    state = context._state(native)
    assert state["consumed_pointers"] and state["confirmed_read_sections"]
    assert "candidate" not in state and "retained" not in state
    assert path.read_bytes() == before and history.read_bytes() == history_before


@pytest.mark.parametrize("damage", ["content", "foreign_capture", "index", "null_pointers"])
def test_native_selection_observer_rejects_unverified_or_discovery_only_reads(tmp_path, damage):
    _, machine, model = fixture(tmp_path)
    reference = {"path": machine["path"], "sha256": machine["sha256"]}
    state = {"source_recovery": reference, "recovery": reference}
    page = read(model["command"], select=["/criterion"])
    assert_section_read_only(page, machine)
    if damage == "content":
        page = {**page, "content": page["content"][:-1] + "!",
            "read_sections": {"/criterion": "a" * 64}, "source_sha256": machine["sha256"]}
    elif damage == "foreign_capture":
        other = tmp_path / "foreign"
        other.mkdir(mode=0o700)
        _, _, foreign = fixture(other)
        page = read(foreign["command"], select=["/criterion"])
    elif damage == "index":
        page = read(model["command"], select=[""], list_fields=True)
    else:
        page = read(model["command"], initial_read=True)
        page.pop("reference_file", None)
        page["selected_pointers"] = None
    assert _verified_context_pages(state, _context_pages(page)) == []


@pytest.mark.parametrize("selection", ["fields", "index", "initial"])
def test_selected_read_survives_unwritable_reference_metadata(tmp_path, monkeypatch, selection):
    import errno
    from loopx.cli_commands import quota_context_reader

    payload, machine, model = fixture(tmp_path)
    before = Path(machine["path"]).read_bytes()
    blocked_paths = []

    def unavailable(path, *args, **kwargs):
        blocked_paths.append(path)
        raise OSError(errno.ENOSPC, "synthetic metadata unavailable")

    monkeypatch.setattr(quota_context_reader, "write_context_read_reference", unavailable)
    options = {"fields": {"select": ["/selected_todo", "/criterion"]},
               "index": {"list_fields": True}, "initial": {"initial_read": True}}[selection]
    page = read(model["command"], max_page_output_bytes=3500, page_bytes=3000, **options)
    content = []
    while True:
        assert page["ok"] and page["schema_version"] == "context_capture_page_v1"
        assert page["sha256"] == machine["sha256"] and page["selection_sha256"]
        assert capture_page_range(page, raw=before, digest=machine["sha256"]) is None
        content.append(page["content"])
        if page["eof"]:
            break
        assert "--capture-sha256" in page["next_command"] and "--selection-sha256" in page["next_command"]
        page = read(page["next_command"])
    values = json.loads("".join(content))["selections"]
    if selection == "fields":
        assert {item["pointer"]: item["value"] for item in values} == {
            "/selected_todo": payload["selected_todo"], "/criterion": payload["criterion"]}
    elif selection == "index":
        assert {item["pointer"] for item in values[0]["fields"]} == {"/" + key for key in payload}
    else:
        assert next(item["value"] for item in values if item["pointer"] == "/goal_boundary") == payload["goal_boundary"]
    assert blocked_paths == [Path(machine["path"])]
    assert Path(machine["path"]).read_bytes() == before


@pytest.mark.parametrize("damage", ["capture", "selection", "symlink", "permissions"])
def test_reference_damage_fails_closed(tmp_path, damage):
    _, machine, model = fixture(tmp_path)
    page = read(model["command"], select=["/criterion"])
    reference = Path(page["reference_file"])
    if damage == "capture":
        Path(machine["path"]).write_text('{"changed":true}\n')
    elif damage == "selection":
        payload = json.loads(reference.read_text())
        payload["selection"]["sha256"] = "0" * 64
        reference.write_text(json.dumps(payload))
    elif damage == "symlink":
        original = reference.with_suffix(".saved")
        reference.rename(original)
        reference.symlink_to(original)
    else:
        reference.chmod(0o644)
    rejected = read(model["command"], reference_file=str(reference))
    assert rejected["ok"] is False and "content" not in rejected


@pytest.mark.skipif(sys.platform == "win32", reason="Private reader references require POSIX")
@pytest.mark.parametrize("damage", ["capture", "selection", "unsafe_route", "different_launcher"])
def test_compact_recovery_reference_rejects_changed_source_or_route(tmp_path, damage):
    from loopx.cli import build_parser

    source = tmp_path / "decision.json"
    source.write_text('{"heartbeat_receipt":{"turn_instance_id":"original"}}\n')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    launcher = tmp_path / "launcher with spaces"
    launcher.touch()
    route = [str(launcher), "--cli-bin", str(launcher)]
    original = write_context_read_reference(source, digest, reference_file=tmp_path / "r", reader_argv=route)
    parser = build_parser()

    def invoke(reference, *extra):
        args = parser.parse_args(["--format", "json", "quota", "context-read",
            "--reference-file", str(reference), "--max-page-output-bytes", "650", *extra])
        return read_context_capture(args, reader_argv=route)

    assert invoke(original, "--select", "/heartbeat_receipt")["ok"]
    [selected] = tmp_path.glob("r.*")
    if damage == "capture":
        source.write_bytes(source.read_bytes() + b" ")
    elif damage == "selection":
        metadata = json.loads(selected.read_text())
        metadata["selection"]["sha256"] = "0" * 64
        selected.write_text(json.dumps(metadata))
    else:
        metadata = json.loads(selected.read_text())
        metadata["reader_argv"] = ([str(launcher), "-c", "raise SystemExit()"] if damage == "unsafe_route"
            else [str(tmp_path / "another CLI")])
        selected.write_text(json.dumps(metadata))
    rejected = invoke(selected)
    assert rejected["ok"] is False and "content" not in rejected
    expected = {
        "capture": "digest", "selection": "digest",
        "unsafe_route": "invalid CLI route", "different_launcher": "original LoopX",
    }
    assert expected[damage] in rejected["reason"]


def test_reference_publication_never_overwrites_an_existing_document(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text('{}\n')
    original = write_context_read_reference(path, "a" * 64)
    assert write_context_read_reference(path, "a" * 64) == original
    with pytest.raises(ValueError, match="reference changed"):
        write_context_read_reference(path, "b" * 64)


@pytest.mark.parametrize("alias_first", [False, True])
def test_reference_reuses_a_parent_alias_without_rewriting(tmp_path, alias_first):
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    path = physical / "capture.json"
    raw = b'{"ok":true,"last_stop":"Keep the original budget."}\n'
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    spellings = [path, alias / path.name]
    if alias_first:
        spellings.reverse()
    first = write_context_read_reference(spellings[0], digest)
    before = first.read_bytes()
    second = write_context_read_reference(spellings[1], digest)
    assert second.resolve() == first.resolve()
    assert first.read_bytes() == before
    from loopx.control_plane.turn_driver.decision_capture import context_capture_read_command

    page = read(context_capture_read_command(path, digest, reference_file=second))
    assert page["ok"] and page["eof"] and json.loads(page["content"]) == json.loads(raw)
    assert first.read_bytes() == before


@pytest.mark.parametrize("damage", ["digest", "other_file", "leaf_symlink", "hardlink", "selection", "navigation", "extra"])
def test_parent_alias_reuse_preserves_other_reference_checks(tmp_path, damage):
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    path = physical / "capture.json"
    path.write_text('{}\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    reference = write_context_read_reference(path, digest)
    payload = json.loads(reference.read_bytes())
    if damage == "digest":
        digest = "0" * 64
    elif damage in {"other_file", "leaf_symlink", "hardlink"}:
        other = physical / "different.json"
        if damage == "leaf_symlink":
            other.symlink_to(path)
        elif damage == "hardlink":
            other.hardlink_to(path)
        else:
            other.write_bytes(path.read_bytes())
        payload["path"] = str(other)
    else:
        payload[damage] = {"changed": True}
    reference.write_text(json.dumps(payload))
    before = reference.read_bytes()
    with pytest.raises(ValueError, match="reference changed"):
        write_context_read_reference(alias / path.name, digest)
    assert reference.read_bytes() == before


@pytest.mark.parametrize("parent_alias", [False, True], ids=["physical", "parent-alias"])
def test_native_warm_opaque_reference_and_current_recovery(tmp_path, monkeypatch, parent_alias):
    from loopx.control_plane.turn_driver import codex_context as context
    from test_codex_context_lifecycle import setup, project
    from test_progressive_native_context import payload as native_payload, outcome
    from test_quota_settlement_cli import _run_cli

    parent = tmp_path / "physical"
    parent.mkdir(mode=0o700)
    physical = parent / "session"
    physical.mkdir(mode=0o700)
    root = physical
    if parent_alias:
        root = tmp_path / "alias"
        root.symlink_to(parent, target_is_directory=True)
        root = root / "session"
    event = setup(root, monkeypatch)
    payload = native_payload()
    payload["interaction_contract"] = {"schema_version": "loopx_interaction_contract_v0",
        "agent_channel": {"work_context": {"sources": []}}}
    outcome(payload)["requirements"][0]["description"] = "Keep every original requirement. " * 600
    payload["last"] = {"value": 9007199254740991, "stop": "Do not extend the budget. 不发布。"}
    _, cold = project(root, "cold", payload=payload)
    cold_view = native_context_capture_view(cold, root / "cold", context_directory=str(root),
        max_page_output_bytes=12000)
    cold_model = model_context_reference(cold_view)
    reference = Path(cold_model["recovery"]["reference_file"])
    descriptor_before = reference.read_bytes()
    _, warm = project(root, "warm", payload=payload)
    assert warm["mode"] == "delta" and warm["continuity_basis"] == "captured_file", warm.get("reason")
    warm_view = native_context_capture_view(warm, root / "warm", context_directory=str(root),
        max_page_output_bytes=12000)
    warm_model = model_context_reference(warm_view)
    assert "reference_file" in warm_model and warm_model["mode"] == "delta"
    assert reference.read_bytes() == descriptor_before
    assert Path(warm_model["baseline_reference"]["reference_file"]).resolve() == reference.resolve()
    for file in (root / "cold").iterdir():
        if not file.name.endswith(".reader.json"):
            file.unlink()

    def read_cli(command):
        parts, offset = [], 0
        while True:
            words = shlex.split(command)
            code, page = _run_cli(root / "no-registry.json", root / "no-runtime",
                *words[words.index("quota"):], "--context-delivery", "off", cwd=physical)
            assert code == 0 and page["ok"] and page["offset"] == offset, page
            parts.append(page["content"])
            if page["eof"]:
                return json.loads("".join(parts))
            offset = page["next_offset"]
            command = page["next_command"]

    expected = json.loads(Path(warm["recovery"]["path"]).read_bytes())
    assert read_cli(warm_model["recovery"]["command"]) == expected
    context.codex_context_hook(str(root), {**event, "source": "compact"})
    assert "candidate" not in context._state(root)
    assert read_cli(warm_model["recovery"]["command"]) == expected
    _, after_compact = project(root, "after", payload=payload)
    assert after_compact["mode"] == "full"
    fresh = model_context_reference(native_context_capture_view(after_compact, root / "after",
        context_directory=str(root), max_page_output_bytes=12000))
    current = read_cli(fresh["recovery"]["command"])
    assert current["last"] == payload["last"]
    assert outcome(current)["requirements"][0]["description"] == outcome(payload)["requirements"][0]["description"]
    assert reference.read_bytes() == descriptor_before
    assert not (root / "no-registry.json").exists() and not (root / "no-runtime").exists()
