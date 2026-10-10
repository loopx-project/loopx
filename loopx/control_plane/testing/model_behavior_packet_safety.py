"""Expose typed scheduler wire contents to the actor packet's safety scanner."""
from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from collections.abc import Mapping
from typing import Any

_FACTS_FLAG = "--scheduler-host-facts-chunk"
# Match the existing native follow-up decoder, without invoking its effects.
_MAX_ENCODED_CHARS = 4_096
_MAX_INFLATED_BYTES = 16_384


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _validation_copy(value: Any) -> Any:
    # Unlike deepcopy, do not preserve aliases: validating a typed argv must
    # never exempt an alias of that list exposed in an unrelated packet field.
    if isinstance(value, Mapping):
        return {key: _validation_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_validation_copy(item) for item in value]
    return value


def _decode_facts(chunks: list[str]) -> dict[str, Any]:
    try:
        if sum(map(len, chunks)) > _MAX_ENCODED_CHARS:
            raise ValueError("encoded boundary")
        encoded = "".join(chunks)
        if not encoded or not re.fullmatch(r"[A-Za-z0-9_+/-]+", encoded):
            raise ValueError("encoded alphabet")
        compressed = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        # The shipped scheduler emits standard Base64 to avoid secret-shaped
        # URL-safe chunks. Native CLI also accepts the older URL-safe form.
        # Validate either canonical alphabet, not mixed alphabets or pad bits;
        # the decoded envelope still receives the full confidentiality scan.
        if encoded not in {
            base64.b64encode(compressed).decode().rstrip("="),
            base64.urlsafe_b64encode(compressed).decode().rstrip("="),
        }:
            raise ValueError("non-canonical base64")
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, _MAX_INFLATED_BYTES + 1)
        if len(raw) > _MAX_INFLATED_BYTES or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise ValueError("inflated boundary or incomplete/trailing stream")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if not isinstance(payload, dict) or payload.get("schema_version") != "loopx_scheduler_host_followup_hint_v0":
            raise ValueError("hint schema")
        facts = payload.get("host_facts")
        if not isinstance(facts, dict) or facts.get("schema_version") != "loopx_scheduler_heartbeat_host_facts_v0":
            raise ValueError("facts schema")
        if not isinstance(payload.get("before"), dict) or not isinstance(payload.get("use_current_hint"), bool):
            raise ValueError("hint shape")
        return payload
    except (ValueError, binascii.Error, zlib.error, RecursionError):
        # Do not echo untrusted bytes or parsed values into a public failure.
        raise ValueError("scheduler host facts must be bounded canonical compressed JSON") from None


def _matching_route_command(args: list[Any], command: str) -> bool:
    # The native scheduler binder prepends these route options. Recognize only
    # that transport prefix, not a quota token hidden inside an arbitrary argv.
    # This does not validate CLI admission; all option values remain in the
    # confidentiality view and receive the ordinary recursive safety scan.
    index = 0
    seen: set[str] = set()
    while index < len(args):
        item = args[index]
        if not isinstance(item, str):
            return False
        option, separator, value = item.partition("=")
        if option not in ("--registry", "--runtime-root"):
            break
        if option in seen:
            return False
        seen.add(option)
        if not separator:
            index += 1
            if index == len(args) or not isinstance(args[index], str):
                return False
            value = args[index]
        if not value or value.startswith("-"):
            return False
        index += 1
    return args[index:index + 2] == ["quota", command]


def _expose_chunks(args: list[Any], *, command: str, schema_valid: bool) -> None:
    positions: list[int] = []
    chunks: list[str] = []
    index = 0
    encoded_chars = 0
    while index < len(args):
        item = args[index]
        prefix_length = 0
        if item == _FACTS_FLAG:
            index += 1
            if index == len(args) or not isinstance(args[index], str):
                raise ValueError("scheduler host facts chunk value is missing")
            item = args[index]
        elif isinstance(item, str) and item.startswith(_FACTS_FLAG + "="):
            prefix_length = len(_FACTS_FLAG) + 1
        else:
            index += 1
            continue
        chunk_length = len(item) - prefix_length
        if not chunk_length:
            raise ValueError("scheduler host facts chunk value is missing")
        encoded_chars += chunk_length
        if encoded_chars > _MAX_ENCODED_CHARS:
            raise ValueError("scheduler host facts exceed the encoded boundary")
        positions.append(index)
        chunks.append(item[prefix_length:])
        index += 1
    if not chunks:
        return  # Legacy, unencoded hints still receive ordinary recursive scanning.
    if not schema_valid or not _matching_route_command(args, command):
        raise ValueError("scheduler host facts require the matching typed hint and command")
    payload = _decode_facts(chunks)
    # Only the validation view changes. Scan the entire decoded envelope once,
    # including extensions; retain every non-transport argument for scanning.
    for position in positions:
        args[position] = ""
    args[positions[0]] = payload


def scheduler_transport_validation_view(packet: Mapping[str, Any], *, arm: str) -> dict[str, Any]:
    """Decode only real packet fields; dotted key names cannot imitate a path.

    This is a confidentiality view, not scheduler admission or execution. The
    caller must recursively scan it, and must send the original packet onward.
    """
    view: dict[str, Any] = _validation_copy(packet)
    if arm == "full_packet":
        scheduler = view.get("scheduler_hint")
    else:
        scheduler = view.get("scheduler")
    if not isinstance(scheduler, Mapping):
        return view
    app_automation = scheduler.get("app_automation") or scheduler.get("codex_app")
    if not isinstance(app_automation, Mapping):
        return view
    if arm == "full_packet":
        for kind, command in (("ack", "scheduler-ack-current"), ("failure", "scheduler-fail-current")):
            hint = app_automation.get(kind + "_hint")
            args = hint.get("cli_args") if isinstance(hint, Mapping) else None
            if isinstance(hint, Mapping) and isinstance(args, list):
                _expose_chunks(args, command=command,
                    schema_valid=scheduler.get("schema_version") == "scheduler_hint_v0"
                    and hint.get("schema_version") == f"app_automation_scheduler_{kind}_hint_v0")
    else:
        args = app_automation.get("ack_cli_args")
        if isinstance(args, list):
            _expose_chunks(args, command="scheduler-ack-current",
                schema_valid=packet.get("schema_version") == "loopx_turn_envelope_v0")
    return view
