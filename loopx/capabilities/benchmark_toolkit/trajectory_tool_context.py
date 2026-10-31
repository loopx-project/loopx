"""Codex capture IO: project text lengths and simple shell argv, never execute.

The TS trajectory owner classifies commands and reduces observations. Compound
shell, Python/JS orchestration and tool continuations stay unassigned. This is an
inventory of appended input text, not the context replayed at every inference.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex


def _argv(command: str, depth: int = 0) -> list[str] | None:
    if depth > 2 or "\n" in command or "\r" in command:
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return None
    if not words or any(word and set(word) <= set(";&|()<>") for word in words):
        return None
    # Expansion could select another executable or hide shell work. Quoted
    # expansions are conservatively unassigned too; no string is evaluated.
    if any("$" in word or "`" in word for word in words):
        return None
    if words[0].rsplit("/", 1)[-1] in {"bash", "sh"}:
        return _argv(words[2], depth + 1) if len(words) == 3 and words[1] in {"-c", "-lc"} else None
    return words


def _literal_exec_argv(script: object) -> list[str] | None:
    """Recognize one complete literal exec wrapper, never arbitrary JavaScript.

    Codex's custom exec tool commonly wraps one exec_command in const/await/text.
    Accept only JSON-valued object properties and one return; calls, spreads,
    interpolation, multiple commands and computed properties remain unknown.
    """
    if not isinstance(script, str):
        return None
    match = re.fullmatch(
        r"\s*const\s+([A-Za-z_]\w*)\s*=\s*await\s+tools\.exec_command\(\s*\{(.*)\}\s*\)\s*;"
        r"\s*text\(\s*\1(?:\.output)?\s*\)\s*;?\s*", script, re.S,
    )
    if match is None:
        return None
    rest, values = match[2].strip(), {}
    decoder = json.JSONDecoder()
    while rest:
        key = re.match(r'(?:"([A-Za-z_]\w*)"|([A-Za-z_]\w*))\s*:\s*', rest)
        if key is None:
            return None
        name = key[1] or key[2]
        if name in values:
            return None
        try:
            value, end = decoder.raw_decode(rest[key.end():])
        except ValueError:
            return None
        if not isinstance(value, (str, int, float, bool, type(None))):
            return None
        values[name] = value
        rest = rest[key.end() + end:].strip()
        if rest and not rest.startswith(','):
            return None
        rest = rest[1:].strip() if rest else rest
    command = values.get('cmd')
    return _argv(command) if isinstance(command, str) else None


def project_tool_context(events: list[dict], *, first_line: int = 1) -> list[dict]:
    """Keep only response_item text metadata; event_msg mirrors are excluded."""
    projected = []
    for number, event in enumerate(events, first_line):
        if event.get("type") != "response_item":
            continue
        item = event.get("payload")
        if not isinstance(item, dict):
            raise ValueError("Invalid captured response item")
        kind = item.get("type")
        if kind in {"function_call", "custom_tool_call"}:
            argv = None
            if kind == "custom_tool_call" and item.get("name") in {"exec", "functions.exec"}:
                argv = _literal_exec_argv(item.get("input"))
            elif kind == "function_call" and item.get("name") in {"exec_command", "functions.exec_command", "shell_command", "functions.shell_command"}:
                try:
                    args = json.loads(item.get("arguments", ""))
                    command = args.get("cmd", args.get("command")) if isinstance(args, dict) else None
                    argv = _argv(command) if isinstance(command, str) else None
                except (ValueError, TypeError):
                    pass
            projected.append({"line": number, "kind": "call", "call_id": item.get("call_id"), "argv": argv})
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            output = item.get("output")
            if isinstance(output, str):
                texts, missing = [output], 0
            elif isinstance(output, list):
                texts = [part['text'] for part in output if isinstance(part, dict) and isinstance(part.get('text'), str)]
                missing = len(output) - len(texts)
            else:
                projected.append({"line": number, "kind": "unmeasured"})
                continue
            raw = ''.join(texts).encode('utf-8')
            projected.append({"line": number, "kind": "output", "call_id": item.get("call_id"),
                              "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                              "unmeasured_parts": missing})
        elif kind == "message" and item.get("role") in {"user", "developer", "system"}:
            content = item.get("content")
            if not isinstance(content, list):
                projected.append({"line": number, "kind": "unmeasured"})
                continue
            texts = [part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
            projected.append({"line": number, "kind": "message", "bytes": sum(len(t.encode("utf-8")) for t in texts),
                              "unmeasured_parts": len(content) - len(texts)})
    return projected
