"""Native observations become bounded owner activity, never a transcript."""
import json

from loopx.capabilities.native_chat.antigravity_activity import AntigravityActivity


def test_repeated_native_observations_do_not_invent_tool_completion_or_publish_reasoning(tmp_path):
    activity = AntigravityActivity(tmp_path)
    planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
               "plannerResponse": {"thinking": "hidden thought content", "toolCalls": [
                   {"name": "read_file", "arguments": {"secret": "private arguments"}}]}}
    events = activity.observe(1, planner)
    assert [(row["method"], row["step"]["kind"]) for row in events] == [
        ("item/started", "reasoning"), ("item/completed", "reasoning"), ("item/started", "tool")]
    assert activity.observe(1, planner) == []
    assert events[-1]["step"]["title"] == "read_file"
    assert "hidden thought" not in json.dumps(events) and "private arguments" not in json.dumps(events)
    assert not any(row["step"]["kind"] == "tool" and row["method"] == "item/completed" for row in events)


def test_command_redaction_failure_and_output_observation_use_shared_contract(tmp_path):
    activity = AntigravityActivity(tmp_path)
    native = {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_GENERATING",
              "runCommand": {"commandLine": f"cat {tmp_path}/source.txt --token secret-value /private/other.txt",
                             "combinedOutput": "private command output"}}
    rows = activity.observe(2, native)
    assert len(rows) == 1
    assert "source.txt" in rows[0]["step"]["title"]
    assert "secret-value" not in json.dumps(rows) and "private command output" not in json.dumps(rows)
    assert str(tmp_path) not in json.dumps(rows) and "/private/other.txt" not in json.dumps(rows)
    assert activity.observe(2, native) == []
    native["status"] = "CORTEX_STEP_STATUS_DONE"
    native["runCommand"]["exitCode"] = 3
    final = activity.observe(2, native)
    assert len(final) == 1 and final[0]["step"]["state"] == "failed"
    assert final[0]["step"]["exit_code"] == 3
    assert activity.observe(2, native) == []


def test_observed_native_file_view_redacts_uri_and_never_emits_contents(tmp_path):
    activity = AntigravityActivity(tmp_path)
    native = {"type": "CORTEX_STEP_TYPE_VIEW_FILE", "status": "CORTEX_STEP_STATUS_DONE",
              "viewFile": {"absolutePathUri": (tmp_path / "file name.txt").as_uri(), "content": "private file body"}}
    rows = activity.observe(3, native)
    assert [row["step"]["state"] for row in rows] == ["running", "completed"]
    assert all(row["step"]["verb"] == "read" and row["step"]["title"] == "file name.txt" for row in rows)
    assert str(tmp_path) not in json.dumps(rows) and "private file body" not in json.dumps(rows)
    assert activity.observe(3, native) == []
