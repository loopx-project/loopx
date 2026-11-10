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


_TURN_ASSIGNMENT = re.compile(
    r"LOOPX_TURN=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z)"
)
_TURN_PLACEHOLDER = "${LOOPX_TURN:?}"
_TURN_ARGUMENT = re.compile(r'(?<!\S)--turn-instance-id[ \t]+"' + re.escape(_TURN_PLACEHOLDER) + r'"(?=[ \t]|$)')


def _argv(command: str, depth: int = 0) -> list[str] | None:
    if depth > 2 or "\r" in command:
        return None
    bound_turn = None
    if "\n" in command:
        # The managed heartbeat assigns one literal UTC Turn ID before the
        # quota guard. Resolve only its quoted argument, without running shell.
        assignment, _, command = command.partition("\n")
        command = command.removesuffix("\n")
        match = _TURN_ASSIGNMENT.fullmatch(assignment)
        if (match is None or "\n" in command or "#" in command or command.count(_TURN_PLACEHOLDER) != 1
                or not _TURN_ARGUMENT.search(command)):
            return None
        bound_turn = match[1]
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return None
    # Keep quoting/escaping until expansion syntax is ruled out. shlex's argv
    # alone cannot distinguish an unquoted glob from a literal quoted filename.
    unquoted = re.sub(r"'(?:[^']*)'|\"(?:\\.|[^\"\\])*\"|\\.", "", command)
    if re.search(r"[\*?\[\]{}~]", unquoted):
        return None
    if not words or any(word and set(word) <= set(";&|()<>") for word in words):
        return None
    if bound_turn is not None:
        if words.count("--turn-instance-id") != 1:
            return None
        index = words.index("--turn-instance-id") + 1
        if words[index:index + 1] != [_TURN_PLACEHOLDER]:
            return None
        words[index] = bound_turn
        from ...cli_runtime import GLOBAL_OPTIONS_WITH_VALUE

        args = words[1:]
        executable = words[0].rsplit("/", 1)[-1]
        if executable != "loopx":
            if (not re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable)
                    or args[:1] != ["-m"] or args[1:2] not in (["loopx"], ["loopx.cli"], ["loopx.entrypoint"])):
                return None
            args = args[2:]
        while args and args[0] in GLOBAL_OPTIONS_WITH_VALUE:
            if len(args) < 2:
                return None
            args = args[2:]
        if args[:2] != ["quota", "should-run"]:
            return None

    # Expansion could select another executable or hide shell work. Quoted
    # expansions are conservatively unassigned too; no string is evaluated.
    if any("$" in word or "`" in word for word in words):
        return None
    if words[0].rsplit("/", 1)[-1] in {"bash", "sh"}:
        if bound_turn is None and len(words) == 3 and words[1] in {"-c", "-lc"}:
            return _argv(words[2], depth + 1)
        return None
    return words


def _literal_exec_argv(script: object) -> list[str] | None:
    """Recognize one complete literal exec wrapper, never arbitrary JavaScript.

    Accept the stored-result and directly returned forms of one exec_command.
    Accept only JSON-valued object properties and one return; calls, spreads,
    interpolation, multiple commands and computed properties remain unknown.
    """
    if not isinstance(script, str):
        return None
    invocation = r"await\s+tools\.exec_command\(\s*\{(?P<options>.*)\}\s*\)"
    wrappers = (
        r"const\s+(?P<result>[A-Za-z_]\w*)\s*=\s*" + invocation
        + r"\s*;\s*text\(\s*(?P=result)(?:\.output)?\s*\)",
        r"text\(\s*" + invocation + r"\s*\)",
        r"text\(\s*\(\s*" + invocation + r"\s*\)\.output\s*\)",
    )
    for wrapper in wrappers:
        match = re.fullmatch(wrapper + r"\s*;?", script.strip(), re.S)
        if match is not None:
            break
    else:
        return None
    rest, values = match["options"].strip(), {}
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
