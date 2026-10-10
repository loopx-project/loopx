"""Release-only paid model evaluation; production Chat prompt/parser, public fixtures.

Not for routine PR checks or heartbeats. Explicit --live opt-in is required.
No tools, dispatch, state writes or worker launch. This qualifies semantic intake,
not dynamic discovery or completed work. Credentials remain in process memory.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import tempfile
import urllib.request

from loopx.chat import CHAT_REVIEW_CLOSE_TAG, CHAT_REVIEW_OPEN_TAG, parse_agent_response
from loopx.chat_agent import CodexChatAgentSession, _turn_prompt
from loopx.control_plane.operator_provider import operator_provider_environ


def score(case, response):
    observed = "handoff" if response.get("context_handoff") else "draft" if response.get("goal_draft") else "answer"
    errors = []
    message = response.get("message")
    if not isinstance(message, str) or not message.strip():
        errors.append("missing_answer")
    if observed != case["expected"]:
        errors.append(f"expected_{case['expected']}_got_{observed}")
    if response.get("proposals") or response.get("protected_action"):
        errors.append("unrequested_action")
    if case["expected"] in {"draft", "handoff"} and response.get("gate"):
        errors.append("redundant_gate")
    if case.get("target"):
        handoff = response.get("context_handoff") or {}
        if any(handoff.get(k) != v for k, v in case["target"].items()):
            errors.append("wrong_recipient")
    if case.get("ready"):
        draft = response.get("goal_draft") or {}
        if not draft.get("completion_criteria") or draft.get("question"):
            errors.append("unnecessary_clarification")
    # Object/version/source pointers are stable evidence, not routing keywords.
    # Human review still judges the factual conclusion and usefulness of prose.
    for ref in case.get("required_evidence_refs", []):
        if ref not in str(response.get("message") or ""):
            errors.append("missing_evidence_ref")
    brief = (response.get("context_handoff") or {}).get("brief")
    for ref in case.get("required_handoff_refs", []):
        if not isinstance(brief, dict) or ref not in json.dumps(brief, ensure_ascii=False):
            errors.append("missing_handoff_ref")
    # Keep the visible answer and parsed handoff for factual review. Never save
    # raw provider payloads, tool events, credentials or provider error text.
    # Structural success alone cannot establish the correctness of these claims.
    review_response = {k: response[k] for k in ("message", "context_handoff", "goal_draft") if k in response}
    return {"id": case["id"], "passed": not errors, "observed": observed, "errors": errors,
            "review_response": review_response}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow paid model requests")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", choices=["operator-api", "codex"], default="operator-api")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("chat-intake.public.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this evaluation sends public fixtures to a paid API")
    env = operator_provider_environ(args.runtime_root)
    key = env.get("DEEPSEEK_API_KEY")
    if args.provider == "operator-api" and not key:
        parser.error("Configure the machine operator model credential first")
    endpoint = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    fixture = args.cases.read_bytes()
    suite = json.loads(fixture)
    cases = [{**case, "context": suite["contexts"][case["context_ref"]]} for case in suite["cases"]]
    if not 1 <= len(cases) <= 30:
        parser.error("Use a bounded suite of 1–30 cases")

    def run(case):
        started = time.monotonic()
        # Match the real Codex adapter's input normalization for both providers.
        message = " ".join(case["request"].split())
        context = json.dumps(case["context"], ensure_ascii=False)
        prompt = _turn_prompt(message, context_summary=context)
        prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        if args.provider == "codex":
            protocol_warning = False

            def observe(event, _payload):
                nonlocal protocol_warning
                # Keep only the fact; provider payloads can contain secrets.
                if event == "protocol.warning":
                    protocol_warning = True

            try:
                with tempfile.TemporaryDirectory(prefix="loopx-public-intake-") as work:
                    with CodexChatAgentSession.start(
                        codex_bin="codex", work_dir=Path(work), goal_id="intake-evaluation",
                        objective=json.dumps(case["context"], ensure_ascii=False), model=args.model,
                        reasoning_effort="high", hard_timeout_sec=180,
                    ) as session:
                        session.context_summary = context
                        row = score(case, session.send(message, on_event=observe))
                        if protocol_warning:
                            row["passed"] = False
                            row["errors"].append("protocol_warning")
            except Exception as error:
                row = {"id": case["id"], "passed": False, "errors": [type(error).__name__]}
            row["seconds"] = round(time.monotonic() - started, 2)
            row["prompt_sha256"] = prompt_sha256
            print(json.dumps(row), flush=True)
            return row
        body = {"model": args.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 8192, "temperature": 0}
        request = urllib.request.Request(endpoint + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + str(key)})
        try:
            with urllib.request.urlopen(request, timeout=90) as stream:
                result = json.load(stream)
            content = result["choices"][0]["message"]["content"]
            response = parse_agent_response(content)
            row = score(case, response)
            if result["choices"][0].get("finish_reason") != "stop":
                row["passed"] = False
                row["errors"].append("incomplete_generation")
            if CHAT_REVIEW_OPEN_TAG not in content or CHAT_REVIEW_CLOSE_TAG not in content:
                row["passed"] = False
                row["errors"].append("missing_envelope")
            else:
                raw = json.loads(content.rsplit(CHAT_REVIEW_OPEN_TAG, 1)[1].split(CHAT_REVIEW_CLOSE_TAG, 1)[0])
                if raw.get("goal_draft") and any(raw.get(k) for k in ("context_handoff", "protected_action", "proposals", "gate")):
                    row["passed"] = False
                    row["errors"].append("competing_intents_suppressed_by_host")
            row["usage"] = result.get("usage", {})
        except Exception as error:
            # Provider errors can contain secrets, URLs or echoed prompts. Store only type.
            row = {"id": case["id"], "passed": False, "errors": [type(error).__name__]}
        row["seconds"] = round(time.monotonic() - started, 2)
        row["prompt_sha256"] = prompt_sha256
        print(json.dumps({k: row[k] for k in ("id", "passed", "errors")}), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(run, cases * args.repeats))
    report = {"model": args.model, "provider": args.provider, "cases_sha256": hashlib.sha256(fixture).hexdigest(),
              "prompt_sha256": hashlib.sha256(_turn_prompt("").encode()).hexdigest(),
              "request_settings": {"reasoning_effort": "high", "runtime_profile": "restricted"} if args.provider == "codex" else {"temperature": 0, "max_tokens": 8192, "thinking": "provider_default"},
              "passed": sum(row["passed"] for row in rows), "total": len(rows), "results": rows,
              "review_required": True,
              "boundary": "Fixed public context with production prompt/parser; synthetic authoritative observations test intake, not real fact lookup. Top-level prompt_sha256 identifies the template; each result hashes its effective prompt including context and normalized request. Scoring requires a nonempty answer and checks effects, recipients and evidence pointers; review factual conclusions separately. Codex uses the real restricted Chat adapter and fails on its protocol warnings; operator-api also checks raw envelope integrity. No dynamic discovery, dispatch or work completion qualification."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
