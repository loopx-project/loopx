"""A validated result may complete only the task body that its Turn admitted."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from loopx.cli import main
from loopx.control_plane.turn_driver import executor
from loopx.cli_commands.turn_todo_writeback import captured_todo_text_sha256
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane import canonical_authority_fixture
from tests.test_loopx_turn_driver import (
    _completion_host_and_validation_scripts,
    _turn_journal,
    _turn_run_once_completion_argv,
    _write_live_fixture,
)


@pytest.mark.parametrize(
    "carrier",
    ["canonical_detail", "deduplicated_text", "elided_context", "recommendation_ref"],
)
def test_captured_todo_hash_uses_complete_admitted_bytes(carrier: str) -> None:
    text = "保留空白与全部要求。 " * 50 + "  Keep the final checksum.\n"
    selected = {"todo_id": "todo_example", "text_source_verified": True, "text": text}
    envelope = {
        "action": {"selected_todo": selected},
        "work_context": {"complete": True, "selected_todo_ref": "selected_todo"},
    }
    if carrier == "canonical_detail":
        selected["text"] = "A display prefix"
        envelope["work_context"] = {
            "complete": True,
            "sources": [
                {
                    "source": "selected_todo",
                    "content": {"todo": {"todo_id": "todo_example", "text": text}},
                }
            ],
        }
    elif carrier == "elided_context":
        envelope.pop("work_context")
    elif carrier == "recommendation_ref":
        selected.pop("text")
        selected["text_ref"] = "action.recommended_action"
        envelope["action"]["recommended_action"] = text
    assert (
        captured_todo_text_sha256(envelope, todo_id="todo_example")
        == sha256(text.encode("utf-8")).hexdigest()
    )


@pytest.mark.parametrize(
    "failure",
    [
        "missing_full_source",
        "ambiguous_source",
        "wrong_identity",
        "false_witness",
        "unknown_reference",
    ],
)
def test_captured_todo_hash_refuses_unproven_previews(failure: str) -> None:
    selected = {
        "todo_id": "todo_example",
        "text_source_verified": True,
        "text": "A display prefix",
    }
    envelope = {
        "action": {"selected_todo": selected},
        "work_context": {"complete": True},
    }
    if failure == "ambiguous_source":
        record = {
            "source": "selected_todo",
            "content": {
                "todo": {"todo_id": "todo_example", "text": "Full requirement"}
            },
        }
        envelope["work_context"]["sources"] = [record, record]
    elif failure == "wrong_identity":
        selected["todo_id"] = "todo_other"
    elif failure == "false_witness":
        selected["text_source_verified"] = False
    elif failure == "unknown_reference":
        envelope.pop("work_context")
        selected.pop("text")
        selected["text_ref"] = "unregistered.reference"
    with pytest.raises(ValueError):
        captured_todo_text_sha256(envelope, todo_id="todo_example")


def test_context_witness_preserves_legacy_requests_and_markdown_obligations() -> None:
    from loopx.presentation.renderers.turn_envelope_markdown import work_context_lines

    envelope = {
        "action": {"selected_todo": {"todo_id": "todo_example", "text": "Old preview"}}
    }
    assert captured_todo_text_sha256(envelope, todo_id="todo_example") is None
    context = {
        "complete": True,
        "selected_todo_ref": "selected_todo",
        "instruction": "Read every requirement; stop before release.",
    }
    assert work_context_lines(context) == work_context_lines(
        {**context, "selected_todo_text_verified": True}
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("accepted_before_upgrade", [False, True])
def test_native_legacy_turn_recovers_history_but_cannot_complete_without_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    accepted_before_upgrade: bool,
) -> None:
    import loopx.cli_commands.turn_run_once as command
    from loopx.control_plane.work_items import context_readback
    from loopx.control_plane.todos.mutation_api import complete_goal_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    prefix = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
    ]
    actual_context = context_readback.attach_work_context

    def legacy_context(*args, **kwargs):
        actual_context(*args, **kwargs)
        # Reproduce the supported pre-witness producer, including its original
        # action signature rather than editing a signed journal after capture.
        args[0]["interaction_contract"]["agent_channel"]["work_context"].pop(
            "selected_todo_text_verified", None
        )

    monkeypatch.setattr(context_readback, "attach_work_context", legacy_context)
    host, validator = _completion_host_and_validation_scripts()
    argv = _turn_run_once_completion_argv(project, runtime, registry, host, validator)
    actual_write = command.write_turn_validated_completion
    results = []

    def recover_old_writer(**kwargs):
        assert kwargs["expected_todo_text_sha256"] is None
        if not results:
            # Old writers omitted the commitment. Commit through the actual
            # lifecycle API, then lose its response before the journal checkpoint.
            legacy = {
                k: v for k, v in kwargs.items() if k != "expected_todo_text_sha256"
            }
            result = complete_goal_todo(
                role="agent", completion_identity_source="turn_settlement", **legacy
            )
            assert result["ok"] and result["provider_status"] == "applied", result
            results.append(result)
            raise OSError("Synthetic old-writer response loss")
        result = actual_write(**kwargs)
        results.append(result)
        return result

    if accepted_before_upgrade:
        monkeypatch.setattr(
            command, "write_turn_validated_completion", recover_old_writer
        )
    code, result = _cli([], *argv)
    assert code == 1, result
    if not accepted_before_upgrade:
        assert result["result_kind"] == "writeback_failed", result
        assert "lacks its admitted full task source" in result["reason"]
        assert result["effects"] == {
            "host_invoked": True,
            "state_written": False,
            "quota_spent": False,
            "scheduler_acknowledged": False,
        }
        assert _todo(prefix)["todo"]["status"] == "open"
        return
    assert result["error"] == "Synthetic old-writer response loss", result
    journal = _turn_journal(runtime)
    assert (
        "text_source_verified"
        not in journal["plan"]["turn_envelope"]["action"]["selected_todo"]
    )
    resumed_code, resumed = _cli(
        [], *argv[:-1], "--resume-turn-key", str(journal["turn_key"]), "--execute"
    )
    assert resumed_code == 0 and resumed["status"] == "committed", resumed
    assert resumed["effects"]["host_invoked"] is False
    assert results[1]["provider_status"] == "replayed", results[1]
    assert _todo(prefix)["todo"]["status"] == "done"


def _cli(prefix: list[str], *args: str) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([*prefix, *args])
    return code, json.loads(output.getvalue())


def _todo(prefix: list[str], todo_id: str = "todo_fixture0001") -> dict:
    code, result = _cli(
        prefix,
        "todo",
        "list",
        "--goal-id",
        "loopx-turn-fixture",
        "--todo-id",
        todo_id,
    )
    assert code == 0, result
    return result


def _update(
    prefix: list[str], operation: str, *fields: str, todo_id: str = "todo_fixture0001"
) -> None:
    current = _todo(prefix, todo_id)
    code, result = _cli(
        prefix,
        "todo",
        "update",
        "--goal-id",
        "loopx-turn-fixture",
        "--todo-id",
        todo_id,
        "--agent-id",
        "codex-fixture",
        "--update-operation-id",
        operation,
        "--update-expected-provider-revision",
        current["authority_read"]["provider_revision"],
        *fields,
    )
    assert code == 0 and result["changed"], json.dumps(result)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["requirements", "note", "other_todo"])
@pytest.mark.parametrize("no_followup", [False, True])
def test_native_turn_completion_checks_full_source_and_preserves_unrelated_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    change: str,
    no_followup: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(
        tmp_path,
        provider=provider,
        todo_metadata_extra="no_followup=true" if no_followup else "",
        extra_agent_todo_lines=(
            "- [ ] [P3] Inspect an independent artifact.",
            "  <!-- loopx:todo todo_id=todo_fixture0002 status=open priority=P3 "
            "task_class=advancement_task action_kind=fixture claimed_by=codex-fixture -->",
        )
        if change == "other_todo"
        else (),
    )
    prefix = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
    ]
    # Changing only a distant suffix must not disappear into a 500-character
    # selected-task preview, a display title, or normalized whitespace.
    original = (
        "[P0] Export public rows. " + "Retain each row. " * 50 + "Output version one."
    )
    replacement = (
        original.removesuffix("Output version one.") + "Output a verified checksum."
    )
    _update(prefix, "declare-full-requirements", "--text", original)
    host, validator = _completion_host_and_validation_scripts()
    host = host.replace(
        "request = json.load(sys.stdin)",
        """request = json.load(sys.stdin)
pathlib.Path("request.json").write_text(json.dumps(request), encoding="utf-8")
calls = pathlib.Path("host-calls.txt")
calls.write_text(calls.read_text() + "x" if calls.exists() else "x")""",
    )
    argv = _turn_run_once_completion_argv(project, runtime, registry, host, validator)
    real_validator = executor._run_task_validator

    def change_after_real_validation(*args, **kwargs):
        receipt = real_validator(*args, **kwargs)
        assert receipt["ok"], receipt
        if change == "requirements":
            _update(prefix, "replace-requirements", "--text", replacement)
        elif change == "note":
            _update(prefix, "record-observation", "--note", "A concurrent observation.")
        else:
            _update(
                prefix,
                "independent-work",
                "--text",
                "[P3] Inspect another independent artifact.",
                todo_id="todo_fixture0002",
            )
        return receipt

    monkeypatch.setattr(executor, "_run_task_validator", change_after_real_validation)
    code, result = _cli([], *argv)
    request = json.loads((project / "request.json").read_text())
    captured_todo = request["turn_envelope"]["action"]["selected_todo"]
    assert captured_todo["todo_id"] == "todo_fixture0001"
    assert captured_todo["text_source_verified"] is True
    current = _todo(prefix)["todo"]
    if change != "requirements":
        assert code == 0 and result["status"] == "committed", result
        assert current["status"] == "done"
        assert result["effects"]["quota_spent"] is True
        return

    assert code == 1, result
    failure_kind = "terminal_closeout_failed" if no_followup else "writeback_failed"
    assert result["status"] == "failed" and result["result_kind"] == failure_kind, (
        json.dumps(result)
    )
    assert result["effects"] == {
        "host_invoked": True,
        # Final no-followup commits writeback and spend before its terminal
        # phase. A terminal refusal preserves that accepted settlement prefix.
        "state_written": no_followup,
        "quota_spent": no_followup,
        "scheduler_acknowledged": False,
    }
    assert current["status"] == "open" and current["text"] == replacement
    assert (project / "completion-artifact.txt").read_text() == "completed"
    journal = _turn_journal(runtime)
    assert "durable_writeback" not in journal.get("effect_attempts", {})
    # A cached old result remains refused without another Host call or spend.
    monkeypatch.setattr(executor, "_run_task_validator", real_validator)
    retry = [
        *argv[:-1],
        "--resume-turn-key",
        str(journal["turn_key"]),
        "--retry-failed-turn",
        "--execute",
    ]
    retry_code, retried = _cli([], *retry)
    assert retry_code == 1 and retried["result_kind"] == failure_kind, json.dumps(
        retried
    )
    assert retried["effects"]["host_invoked"] is False
    assert retried["effects"]["quota_spent"] is False
    assert (project / "host-calls.txt").read_text() == "x"
    assert _todo(prefix)["todo"]["status"] == "open"

    # The newly admitted task needs a matching artifact and independent check.
    fresh_host = host.replace(
        'write_text("completed",', 'write_text("checksum:verified",'
    )
    fresh_validator = validator.replace('== "completed"', '== "checksum:verified"')
    fresh = _turn_run_once_completion_argv(
        project, runtime, registry, fresh_host, fresh_validator
    )
    fresh_code, accepted = _cli(
        [], *fresh[:-1], "--turn-instance-id", "fresh-checksum-turn", "--execute"
    )
    assert fresh_code == 0 and accepted["status"] == "committed", accepted
    assert accepted["effects"]["quota_spent"] is True
    assert _todo(prefix)["todo"]["status"] == "done"
    assert (project / "completion-artifact.txt").read_text() == "checksum:verified"
    replay_code, replay = _cli(
        [],
        *fresh[:-1],
        "--resume-turn-key",
        str(accepted["receipt"]["turn_key"]),
        "--execute",
    )
    assert replay_code == 0 and replay["replayed"] is True, replay
    assert not any(replay["effects"].values())
    assert (project / "host-calls.txt").read_text() == "xx"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_turn_recovers_accepted_completion_before_checking_new_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    import loopx.cli_commands.turn_run_once as command

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    prefix = [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
    ]
    host, validator = _completion_host_and_validation_scripts()
    argv = _turn_run_once_completion_argv(project, runtime, registry, host, validator)
    actual_completion = command.write_turn_validated_completion
    completions: list[dict] = []

    def lose_response_after_commit(**kwargs):
        completion = actual_completion(**kwargs)
        completions.append(completion)
        if len(completions) == 1:
            assert completion["ok"] and completion["status"] == "done", completion
            # The public authoring path intentionally forbids replacing a done
            # task. Inject a later synthetic provider head in this disposable
            # store to challenge the recovery adapter, retaining its receipt.
            module = (
                Path(canonical_authority_fixture.__file__).resolve().parents[2]
                / "loopx/control_plane/coordination"
            )
            script = (
                f"import {{openLocalAuthorityStore}} from {json.dumps((module / 'local_authority_provider.ts').as_uri())};"
                f"import {{prepareCoordinationProjectionCommit}} from {json.dumps((module / 'coordination_projection.ts').as_uri())};"
                "let text='';for await(const chunk of process.stdin)text+=chunk;const input=JSON.parse(text);"
                "const store=await openLocalAuthorityStore(input.root,input.goal);const head=await store.loadAuthority();"
                "const todo=head.head.todos.find(row=>row.todo_id==='todo_fixture0001');"
                "const result=await store.commitAuthority(prepareCoordinationProjectionCommit({"
                "goal_id:input.goal,operation_id:'fixture-later-source',expected_provider_revision:head.provider_revision,"
                "projection:head.head,mutations:[{kind:'todo_upsert',todo:{...todo,text:input.text}}]}));"
                "if(result.status!=='applied')throw new Error(JSON.stringify(result));"
            )
            changed = subprocess.run(
                [
                    "node",
                    "--no-warnings",
                    "--experimental-strip-types",
                    "--input-type=module",
                    "-e",
                    script,
                ],
                input=json.dumps(
                    {
                        "root": str(runtime),
                        "goal": "loopx-turn-fixture",
                        "text": "[P0] A later requirement after acceptance.",
                    }
                ),
                text=True,
                capture_output=True,
                timeout=30,
            )
            assert changed.returncode == 0, changed.stderr
            raise OSError("Synthetic response loss after canonical acceptance")
        return completion

    monkeypatch.setattr(
        command, "write_turn_validated_completion", lose_response_after_commit
    )
    code, failed = _cli([], *argv)
    assert (
        code == 1
        and failed.get("error") == "Synthetic response loss after canonical acceptance"
    ), failed
    journal = _turn_journal(runtime)
    resumed_code, resumed = _cli(
        [], *argv[:-1], "--resume-turn-key", str(journal["turn_key"]), "--execute"
    )
    assert resumed_code == 0 and resumed["status"] == "committed", resumed
    assert resumed["effects"]["host_invoked"] is False
    assert completions[1]["provider_status"] == "replayed", completions[1]
    assert _todo(prefix)["todo"]["text"] == "[P0] A later requirement after acceptance."
