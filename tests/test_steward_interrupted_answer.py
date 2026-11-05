"""Original worker reviews settled native output; business is never replayed."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import time

import pytest
from test_antigravity_steward_adapter import language_server  # noqa: F401
from test_native_steward_private import steward  # noqa: F401

from loopx.capabilities.native_chat import antigravity_adapter as native
from loopx.capabilities.native_chat import external_conversations
from loopx.extensions.lark.conversation_identity import identity_ref


@pytest.mark.parametrize("fault", [None, "terminal-without-execution", "foreign-terminal", "busy", "changed", "truncated", "wrong-hash", "async", "unknown", "final", "reasoning",
    "file-read", "file-only", "file-running", "file-foreign", "file-changed", "file-remote", "file-missing-content",
    "file-missing-uri", "file-unknown-field", "file-oversize"])
def test_interrupted_native_inventory_is_exact_stable_and_contains_no_reasoning(monkeypatch, fault):
    # The real matcher is exercised in the original-worker integration below;
    # this bounded native inventory fixture isolates index/effect/read stability.
    assert callable(native.manager_input_matches)
    prompt = 'original synthetic input'
    meta = {"createdAt": "2026-01-01T00:00:05Z", "executionId": "original"}
    steps = [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "userInput": {"items": [{"text": prompt}]}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "plannerResponse": {"thinking": "private reasoning must not be returned", "toolCalls": []}},
        {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "runCommand": {"commandLine": "read existing result", "combinedOutput": "already observed", "blocking": True, "exitCode": 0}},
        {"type": "CORTEX_STEP_TYPE_ERROR_MESSAGE", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "errorMessage": {"error": {"errorCode": 429}}},
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
         "userInput": {"items": [{"text": "later independent question"}]}}]
    if isinstance(fault, str) and fault.startswith("file-"):
        view = {"type": "CORTEX_STEP_TYPE_VIEW_FILE", "status": "CORTEX_STEP_STATUS_DONE", "metadata": meta,
            "viewFile": {"absolutePathUri": "file:///authorized/existing-result.txt", "content": "Original visible file result"}}
        steps.insert(3, view)
        if fault == "file-only":
            steps.pop(2)
        elif fault == "file-running":
            view["status"] = "CORTEX_STEP_STATUS_RUNNING"
        elif fault == "file-foreign":
            view["metadata"] = {**meta, "executionId": "another-execution"}
        elif fault == "file-remote":
            view["viewFile"]["absolutePathUri"] = "https://untrusted.invalid/result.txt"
        elif fault == "file-missing-content":
            view["viewFile"].pop("content")
        elif fault == "file-missing-uri":
            view["viewFile"].pop("absolutePathUri")
        elif fault == "file-unknown-field":
            view["newEffect"] = {}
        elif fault == "file-oversize":
            view["viewFile"]["content"] = "x" * (256 * 1024)
    if fault == "async":
        steps[2]["runCommand"]["blocking"] = False
    elif fault == "unknown":
        steps[2]["type"] = "FUTURE_NATIVE_OPERATION"
    elif fault == "final":
        steps[1]["plannerResponse"]["response"] = "A visible answer must use original result adoption"
    elif fault == "reasoning":
        steps[2]["status"] = "CORTEX_STEP_STATUS_RUNNING"
    elif fault == "terminal-without-execution":
        steps[3]["metadata"] = {"createdAt": meta["createdAt"]}
    elif fault == "foreign-terminal":
        steps[3]["metadata"] = {**meta, "executionId": "another-execution"}
    trajectory = {"trajectory": {"cascadeId": "native"}, "numTotalSteps": len(steps),
                  "status": "RUNNING" if fault == "busy" else "CASCADE_RUN_STATUS_IDLE"}
    calls = []
    def read(_url, _token, method, _body, **_):
        calls.append(method)
        if method == "GetCascadeTrajectory":
            return deepcopy(trajectory)
        assert method == "GetCascadeTrajectorySteps"
        result = deepcopy(steps[:-1] if fault == "truncated" else steps)
        if fault == "changed" and calls.count(method) > 1:
            result[2]["runCommand"]["combinedOutput"] = "changed"
        if fault == "file-changed" and calls.count(method) > 1:
            result[3]["viewFile"]["content"] = "changed"
        return {"steps": result}
    monkeypatch.setattr(native, "discover_antigravity_language_server", lambda: {"url": "synthetic", "csrf_token": "synthetic"})
    monkeypatch.setattr(native, "_rpc_post", read)
    monkeypatch.setattr(native, "manager_input_matches", lambda text, **_: text == prompt)
    proof = native.AntigravityAdapter.interrupted_answer_observation(cascade_id="native", request_ref="f" * 24,
        message="original question", started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:10Z",
        input_anchor={"thread_id": "native", "start_step_index": 0,
            "input_sha256": "0" * 64 if fault == "wrong-hash" else hashlib.sha256(prompt.encode()).hexdigest()})
    if fault in {None, "terminal-without-execution", "file-read", "file-only"}:
        assert proof["end_step_index"] == len(steps) - 1 and proof["total_steps"] == len(steps)
        if fault != "file-only":
            assert proof["visible_results"][0]["combinedOutput"] == "already observed"
        if fault in {"file-read", "file-only"}:
            assert proof["visible_results"][-1] == {"file_uri": "file:///authorized/existing-result.txt",
                "content": "Original visible file result"}
        assert "thinking" not in json.dumps(proof) and "private reasoning" not in json.dumps(proof)
    else:
        assert proof is None
    assert not set(calls) - {"GetCascadeTrajectory", "GetCascadeTrajectorySteps"}


@pytest.mark.parametrize("fault", [None, "stop", "source-changed", "no-capacity", "review-action", "review-gate", "queue-change", "already-codex", "post-enqueue-crash", "lost-ack", "new-question-during-answer", "restricted-review", "primary-quota-unreadable", "native-tail", "authenticated-host", "file-read", "file-running"])
def test_original_worker_answers_interrupted_steward_question_once(tmp_path, language_server, monkeypatch, fault):  # noqa: F811
    import loopx.chat_manager as manager_module
    import loopx.chat_runtime as runtime_module
    from test_chat_manager_model_adoption import configure
    from loopx.chat_manager import manager_executor_allocation

    monkeypatch.setenv("LOOPX_MANAGER_MODEL", "gemini-4-argon-high")
    monkeypatch.setenv("LOOPX_MANAGER_REASONING_EFFORT", "high")
    fixture = steward.__wrapped__(tmp_path)
    store, runtime, provider, transport, binding, capture, workspace = next(fixture)
    try:
        binding = transport.bindings.configure(transport_ref="steward-app", project_ref=binding["project_ref"],
            executor_endpoint_id="antigravity", context_kind="steward")
        language_server.update(quota_error_indices=[0], quota_fraction=0)
        event = provider.event("steward-app", "interrupted-answer", "Explain the existing validation results")
        transport.admit("steward-app", event)
        ref = identity_ref(binding["provider_ref"], event["message_id"])
        original = transport.core.read_request(ref)
        failed = runtime.wait_for_turn(session_id=original["session_id"], turn_id=original["turn_id"], timeout_sec=15)
        assert failed["status"] == "failed" and failed["delta_count"] == 0
        original_bytes = store._turn_path(original["session_id"], original["turn_id"]).read_bytes()
        path = transport.root / f"{ref}.json"
        transport.reconcile_request(path)
        notice = json.loads(path.read_text())["deliveries"]["terminal"]
        post = native._rpc_post
        native_tail = False
        def visible_tools(*args, **kwargs):
            method = args[2]
            if method == "GetCascadeTrajectory":
                packet = post(*args, **kwargs)
                packet["numTotalSteps"] += 4 if native_tail else 3 if fault in {"file-read", "file-running"} else 2
                return packet
            if method != "GetCascadeTrajectorySteps":
                return post(*args, **kwargs)
            args = (*args[:3], {**args[3], "startIndex": 0}, *args[4:])
            packet = post(*args, **kwargs)
            user, error = packet["steps"][:2]
            meta = deepcopy(user["metadata"])
            planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": 1}},
                "plannerResponse": {"thinking": "never forward this", "toolCalls": []}}
            tool = {"type": "CORTEX_STEP_TYPE_RUN_COMMAND", "status": "CORTEX_STEP_STATUS_DONE",
                "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": 2}},
                "runCommand": {"commandLine": "read existing artifact", "cwd": str(workspace),
                    "blocking": True, "exitCode": 0, "combinedOutput": "Original verified output, not a new execution"}}
            error["metadata"]["sourceTrajectoryStepInfo"] = {"stepIndex": 3}
            steps = [user, planner, tool, error]
            if fault in {"file-read", "file-running"}:
                error["metadata"]["sourceTrajectoryStepInfo"] = {"stepIndex": 4}
                steps.insert(3, {"type": "CORTEX_STEP_TYPE_VIEW_FILE",
                    "status": "CORTEX_STEP_STATUS_RUNNING" if fault == "file-running" else "CORTEX_STEP_STATUS_DONE",
                    "metadata": {**meta, "sourceTrajectoryStepInfo": {"stepIndex": 3}},
                    "viewFile": {"absolutePathUri": (workspace / "original-result.txt").as_uri(),
                        "content": "Original visible file result"}})
            if native_tail:
                for index, kind, payload in [(4, "CORTEX_STEP_TYPE_USER_INPUT", {"userInput": {"items": [{"text": "Another native question"}]}}),
                        (5, "CORTEX_STEP_TYPE_ERROR_MESSAGE", {"errorMessage": {"error": {"errorCode": 429}}})]:
                    steps.append({"type": kind, "status": "CORTEX_STEP_STATUS_DONE", "metadata": {
                        **meta, "executionId": "other-execution", "sourceTrajectoryStepInfo": {"stepIndex": index}}, **payload})
            return {"steps": steps}
        monkeypatch.setattr(native, "_rpc_post", visible_tools)
        if fault == "native-tail":
            observe = native.AntigravityAdapter.interrupted_answer_observation
            observed = []
            def later_native_question(**kwargs):
                nonlocal native_tail
                native_tail = bool(observed)
                result = observe(**kwargs)
                observed.append(result)
                return result
            monkeypatch.setattr(native.AntigravityAdapter, "interrupted_answer_observation", later_native_question)
        artifact = workspace / "original-result.txt"
        artifact.write_text("Original result must not change")
        configure(runtime.coordination_runtime_root, model="gemini-4-argon-high", effort="high", endpoint="antigravity", policy="flexible")
        if fault == "restricted-review":
            from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
            from loopx.capabilities.machine_configuration.store import configure_machine_configuration, read_stored_machine_configuration
            registry = build_builtin_machine_configuration_registry()
            configuration = deepcopy(read_stored_machine_configuration(runtime.coordination_runtime_root))
            configuration["namespaces"]["manager_runtime"] = {"schema_version": "manager_runtime_profile_v0", "runtime_profile": "restricted"}
            preview = configure_machine_configuration(runtime_root=runtime.coordination_runtime_root,
                registry=registry, configuration=configuration, execute=False)
            configure_machine_configuration(runtime_root=runtime.coordination_runtime_root,
                registry=registry, configuration=configuration, execute=True, expected_plan_revision=preview["plan_revision"])
        session = store.load_session(original["session_id"])
        host_reads = runtime.manager_runtime_profile(session["channel_id"], steward_context=session["steward_context"])["runtime_profile"] == "trusted_owner"
        capabilities = [{"agent_id": name, "available": True, "adapter_kind": kind} for name, kind in
            [("antigravity", "antigravity_ls"), ("codex", "codex_app_server")]]
        monkeypatch.setattr(runtime, "capabilities", lambda: capabilities)
        allocation = manager_executor_allocation(runtime, environ={}, endpoint_capabilities=[
            {**item, "available": item["agent_id"] == "codex"} for item in capabilities])
        def route(*_):
            return None if fault == "no-capacity" else {"allocation": allocation, "blocked_primary": None, "observed_at": time.time()}
        monkeypatch.setattr(manager_module, "manager_session_quota_route", route)
        monkeypatch.setattr(runtime_module, "manager_session_quota_route", route)
        if fault == "primary-quota-unreadable":
            monkeypatch.setattr(native.AntigravityAdapter, "metadata_review_capacity",
                lambda **_: pytest.fail("a verified API-primary alternate must not probe unrelated primary quota"))
        api_calls = []
        auth_before = None
        if fault == "authenticated-host":
            from loopx.capabilities.native_chat import codex_auth
            runtime.codex_home.mkdir(parents=True, exist_ok=True)
            auth = runtime.codex_home / "auth.json"
            auth.write_text('{"auth_mode":"chatgpt","fixture":"original-host-login"}')
            auth_before = auth.read_bytes()
            def api_route(runtime_root, home, *, consumer, model):
                api_calls.append((runtime_root, home, consumer, model))
                return codex_auth.CodexApiTransport("http://127.0.0.1:1/steward/v1", "synthetic-loopback-token")
            monkeypatch.setattr(codex_auth, "native_api_transport", api_route)
        fake = Path(runtime.codex_bin)
        source = fake.read_text().replace('result = {"thread": {"id": "durable-thread"}}', '''result = {"thread": {"id": "durable-thread"}, "model": "gpt-6.1-sol"}
        permissions = request.get("params", {}).get("permissions")
        if permissions:
            result.update(activePermissionProfile={"id": permissions}, runtimeWorkspaceRoots=[request["params"]["cwd"]])''')
        if fault == "authenticated-host":
            source = source.replace('"model": "gpt-6.1-sol"}', '"model": "gpt-6.1-sol", "modelProvider": request.get("params", {}).get("modelProvider")}')
        if fault in {"review-action", "review-gate"}:
            response = {"message": "The existing evidence supports only this limited answer.", "proposals": [],
                "gate": {"kind": "host_tool_gate", "summary": "Missing original validation evidence", "next_action": "Keep original question owed"} if fault == "review-gate" else None}
            if fault == "review-action":
                response["unfinished_work"] = {"remaining": "Do not admit this work", "dependency": "original owner", "effects_settled": True}
            begin = source.index("        response = 'Visible runtime response.")
            end = source.index('        print(json.dumps({"method": "item/agentMessage/delta"', begin)
            source = source[:begin] + "        response = " + repr('<loopx-review-json>' + json.dumps(response) + '</loopx-review-json>') + "\n" + source[end:]
        fake.write_text(source)
        new_question = []
        if fault == "new-question-during-answer":
            from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
            start = CodexAppServerAdapter.start_turn
            def independent_question(adapter, message, event_sink):
                result = start(adapter, message, event_sink)
                if adapter.session.answer_review:
                    later = provider.event("steward-app", "independent-during-answer", "A separate newer question")
                    transport.admit("steward-app", later)
                    new_question.append(transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"])))
                return result
            monkeypatch.setattr(CodexAppServerAdapter, "start_turn", independent_question)
        if fault == "already-codex":
            later = provider.event("steward-app", "later-normal-before-review", "A separate newer question")
            transport.admit("steward-app", later)
            later_row = transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
            assert runtime.wait_for_turn(session_id=later_row["session_id"], turn_id=later_row["turn_id"], timeout_sec=15)["status"] == "completed"
            transport.reconcile_request(transport.root / f"{later_row['request_ref']}.json")
            assert store.load_session(original["session_id"])["agent_id"] == "codex"
        class Ahead(datetime):
            @classmethod
            def now(cls, tz=None):
                return super().now(tz) + timedelta(seconds=65)
        monkeypatch.setattr(external_conversations, "datetime", Ahead)
        if fault == "stop":
            store.append_event(original["session_id"], original["turn_id"], kind="turn.interrupt_requested", payload={})
        elif fault == "source-changed":
            provider.messages[event["message_id"]]["content"] = "Changed source"
        elif fault == "queue-change":
            store.create_queued_turn(original["session_id"], client_turn_id="later-correction", message="Changed requirement", origin="lark")
        elif fault == "post-enqueue-crash":
            enqueue = runtime.enqueue_turn
            def crash(**kwargs):
                enqueue(**kwargs)
                raise OSError("unknown enqueue response")
            monkeypatch.setattr(runtime, "enqueue_turn", crash)
            with pytest.raises(OSError, match="unknown enqueue response"):
                transport.reconcile_request(path)
            monkeypatch.setattr(runtime, "enqueue_turn", enqueue)
        elif fault == "lost-ack":
            provider.lose_next_reply = True
        transport.reconcile_request(path)
        recovered = transport.core.read_request(ref)
        if fault in {"stop", "source-changed", "no-capacity", "queue-change", "file-running"}:
            assert recovered["turn_id"] == original["turn_id"] and not recovered.get("context_recovery")
        else:
            assert recovered["context_recovery"]["answer_review"] is True
            done = runtime.wait_for_turn(session_id=recovered["session_id"], turn_id=recovered["turn_id"], timeout_sec=15)
            assert done["status"] == ("failed" if fault == "review-action" else "completed"), done
            transport.reconcile_request(path)
            current = transport.core.read_request(ref)
            assert current["delivery_verified"]
            for later in new_question:
                assert runtime.wait_for_turn(session_id=later["session_id"], turn_id=later["turn_id"], timeout_sec=15)["status"] == "completed"
                transport.reconcile_request(transport.root / f"{later['request_ref']}.json")
            assert transport.health()[binding["binding_id"]]["pending_count"] == int(fault in {"review-action", "review-gate"})
            packets = [json.loads(line) for line in capture.read_text().splitlines()]
            starts = [p["params"] for p in packets if p.get("method") == "turn/start"]
            assert len(starts) == (2 if fault in {"already-codex", "new-question-during-answer"} else 1)
            reviews = [p for p in starts if "Core admitted ONE read-only answer review" in p["input"][0]["text"]]
            assert len(reviews) == 1
            profile = "loopx_steward_host_read" if host_reads else "loopx_workspace_only_read"
            assert reviews[0]["permissions"] == profile
            assert "Original verified output" in reviews[0]["input"][0]["text"]
            if fault == "file-read":
                assert "Original visible file result" in reviews[0]["input"][0]["text"]
            assert "never forward this" not in reviews[0]["input"][0]["text"]
            opens = [p["params"] for p in packets if p.get("method") == "thread/start"]
            review_opens = [p for p in opens if p.get("permissions") == profile]
            assert len(review_opens) == 1
            assert [t["name"] for t in review_opens[0]["dynamicTools"]] == ["loopx_manager_read"]
            assert review_opens[0]["config"]["features"]["shell_tool"] is host_reads
            assert review_opens[0]["config"]["features"]["apply_patch_freeform"] is False
            assert review_opens[0]["config"]["features"]["apps"] is False
            permissions = review_opens[0]["config"]["permissions"][profile]
            assert permissions["network"]["enabled"] is False
            assert permissions["filesystem"][":root"] == ("read" if host_reads else "deny")
            assert permissions["filesystem"][":workspace_roots"]["."] == "read"
            if fault == "authenticated-host":
                assert api_calls == [(runtime.coordination_runtime_root, runtime.codex_home, "steward", "gpt-6.1-sol")]
                assert review_opens[0]["modelProvider"] == "loopx_account_api"
                assert review_opens[0]["config"]["model_providers"]["loopx_account_api"]["base_url"] == "http://127.0.0.1:1/steward/v1"
                assert (runtime.codex_home / "auth.json").read_bytes() == auth_before
            after = capture.read_bytes()
            transport.reconcile_request(path)
            assert capture.read_bytes() == after
            if fault is None:
                later = provider.event("steward-app", "later-normal", "A separate normal question")
                transport.admit("steward-app", later)
                next_row = transport.core.read_request(identity_ref(binding["provider_ref"], later["message_id"]))
                assert runtime.wait_for_turn(session_id=next_row["session_id"], turn_id=next_row["turn_id"], timeout_sec=15)["status"] == "completed"
                assert runtime.adapters[original["session_id"]].session.read_only_review is False
                assert runtime.adapters[original["session_id"]].session.answer_review is False
                assert store.load_session(original["session_id"])["answer_review"] is False
        assert store._turn_path(original["session_id"], original["turn_id"]).read_bytes() == original_bytes
        assert json.loads(path.read_text())["deliveries"]["terminal"] == notice
        assert artifact.read_text() == "Original result must not change"
        assert language_server["sent_count"] == 1
    finally:
        runtime.close()
        fixture.close()
