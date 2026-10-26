"""Opt-in local transport capture; quota and envelope owners remain unchanged."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import socket
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..control_plane.content_digest import BARE_SHA256_PATTERN
from ..control_plane.effect_runtime import EffectRuntimeRejected
from ..control_plane.quota.error_codes import QuotaCommandValidationError, QuotaDecisionTransportError
from ..control_plane.turn_driver.decision_capture import (
    validate_handoff_socket, CONTEXT_CAPTURE_READ_GUIDANCE, CONTEXT_CAPTURE_PAGE_GUIDANCE, context_capture_read_command,
    NATIVE_CONTEXT_PAGE_BYTES, NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
    read_context_capture_file as _read_capture,
)


def native_context_activation(args: argparse.Namespace) -> dict[str, Any]:
    from ..control_plane.turn_driver.codex_context import context_delivery_for_arguments

    try:
        return context_delivery_for_arguments(args)
    except ValueError as exc:
        raise QuotaCommandValidationError(str(exc)) from exc


def _capture_page(path: Path, raw: bytes, digest: str, *, offset: int, size: int,
                  output_limit: int | None, instruction: bool) -> dict[str, Any]:
    end = min(offset + size, len(raw))
    while end < len(raw) and raw[end] & 0xC0 == 0x80:
        end -= 1
    content = raw[offset:end].decode("utf-8")

    def page(text: str) -> dict[str, Any]:
        cursor = offset + len(text.encode("utf-8"))
        result: dict[str, Any] = {"ok": True, "schema_version": "context_capture_page_v1", "sha256": digest,
            "offset": offset, "next_offset": cursor if cursor < len(raw) else None,
            "total_bytes": len(raw), "eof": cursor == len(raw), "content": text}
        if instruction:
            result["read_instruction"] = CONTEXT_CAPTURE_PAGE_GUIDANCE
        if cursor < len(raw):
            result["next_command"] = context_capture_read_command(path, digest, offset=cursor, page_bytes=size,
                max_page_output_bytes=output_limit)
        return result

    def fits(result: dict[str, Any]) -> bool:
        # Match cli_runtime.print_payload exactly, including print's newline.
        return output_limit is None or len(json.dumps(result, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")) + 1 <= output_limit

    result = page(content)
    if fits(result):
        return result
    # EOF drops next_command: test it above before searching the monotone
    # non-EOF prefix sizes. Never return a zero-progress continuation.
    low, high = 1, len(content) - (end == len(raw))
    best = None
    while low <= high:
        middle = (low + high) // 2
        candidate = page(content[:middle])
        if fits(candidate):
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    if best is None:
        raise ValueError("max-page-output-bytes cannot fit page metadata and one UTF-8 character; increase the limit for this same capture")
    return best


def read_context_capture(args: argparse.Namespace) -> dict[str, Any]:
    """Bounded local IO over immutable JSON, not a new context/authority owner.

    Verify the complete byte digest on every page. The cursor addresses bytes
    in that exact capture, so a replacement cannot silently mix two decisions.
    EOF describes this page, never a model's complete-consumption receipt.
    """
    try:
        digest = getattr(args, "capture_sha256", None)
        value = getattr(args, "capture_file", None)
        offset, size = args.offset, args.page_bytes
        output_limit = getattr(args, "max_page_output_bytes", None)
        if output_limit is not None and (type(output_limit) is not int or output_limit <= 0
                or getattr(args, "format", None) != "json"):
            raise ValueError("max-page-output-bytes requires JSON output and a positive integer")
        from ..control_plane.turn_driver.codex_context import codex_context_directory

        context_dir = codex_context_directory(directory=getattr(args, "codex_context_dir", None),
            root=getattr(args, "codex_context_root", None))
        if context_dir:
            if value or digest or offset != 0:
                raise ValueError("session lookup starts at offset zero without capture-file or capture-sha256; follow next_command afterwards")
            from ..control_plane.turn_driver.codex_context import read_codex_context_reference

            value, digest = read_codex_context_reference(context_dir,
                goal_id=getattr(args, "goal_id", None), agent_id=getattr(args, "agent_id", None))
        if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None or not value:
            raise ValueError("context-read requires the original capture file and its exact SHA-256")
        if type(offset) is not int or offset < 0 or type(size) is not int or not 256 <= size <= 8192:
            raise ValueError("context-read requires a nonnegative byte offset and page-bytes between 256 and 8192")
        path, raw, _ = _read_capture(value, digest)
        if offset > len(raw) or (offset < len(raw) and raw[offset] & 0xC0 == 0x80):
            raise ValueError("capture offset is outside the file or not a UTF-8 boundary; use the returned next offset")
        return _capture_page(path, raw, digest, offset=offset, size=size,
            output_limit=output_limit, instruction=bool(context_dir))
    except (OSError, ValueError, RecursionError) as exc:
        # Recovery is presentation-only: keep failures separate from quota's
        # collection error path and retain the existing receipt/capture.
        return {"ok": False, "schema_version": "context_capture_page_v1",
            "reason": str(exc) if isinstance(exc, ValueError) else "original capture could not be read",
            "recovery": "Restore the original digest-bound capture; no guard, admission or spend was performed."}


def native_context_capture_view(projected: dict[str, Any], directory: Path, *,
                                context_directory: str | None = None) -> dict[str, Any]:
    """One paged presentation for ordinary and already-captured native calls.

    The guard/projector already ran. Only serialize its wire and prefetch the
    existing digest-checked reader; never collect or admit another decision.
    """
    from ..control_plane.turn_driver.decision_capture import write_decision_capture

    wire = directory / "delivery.json"
    write_decision_capture(wire, projected)
    reference = {"path": str(wire), "sha256": hashlib.sha256(wire.read_bytes()).hexdigest()}
    mode, target = "full", None
    if projected.get("schema_version") == "context_delivery_v1":
        mode, target = projected["mode"], projected["target"]
        if mode == "full":
            reference = {key: projected["recovery"][key] for key in ("path", "sha256")}
    reference["command"] = context_capture_read_command(Path(reference["path"]), reference["sha256"],
        page_bytes=NATIVE_CONTEXT_PAGE_BYTES, max_page_output_bytes=NATIVE_CONTEXT_PAGE_OUTPUT_BYTES)
    result = {"mode": mode, **reference, **({"target": target} if target else {})}
    page = read_context_capture(argparse.Namespace(capture_file=reference["path"],
        capture_sha256=reference["sha256"], offset=0, page_bytes=3072))
    if page["ok"]:
        if not page["eof"]:
            page["next_command"] = context_capture_read_command(Path(reference["path"]), reference["sha256"],
                offset=page["next_offset"], page_bytes=NATIVE_CONTEXT_PAGE_BYTES,
                max_page_output_bytes=NATIVE_CONTEXT_PAGE_OUTPUT_BYTES)
        result.update(page)
        from ..control_plane.turn_driver.codex_context import register_native_context_view

        register_native_context_view(context_directory,
            {**result, "ancestry": projected.get("ancestry", 0)})
    # The codec puts native continuity semantics before its change tree. Keep
    # this shared transport frame neutral instead of copying that instruction
    # or replacing an assumption with a claim of confirmed final input.
    presentation = ("This is the full current capture; after complete reading, continue from it."
        if mode == "full" else "Read the current continuity basis and instruction before its change tree.")
    result["instruction"] = (
        "Presentation of the original decision only; authority and freshness are unchanged. "
        + presentation + " "
        + ("This result includes page zero; consume it before next_command. command restarts "
           "at zero if the inline page is incomplete or unsupported. " if page["ok"] else "")
        + CONTEXT_CAPTURE_PAGE_GUIDANCE
    )
    return result


def project_saved_context_capture(args: argparse.Namespace) -> dict[str, Any]:
    """Present an immutable already-decided quota capture, without authority IO.

    Publication is delayed until this native tool runs, so the lifecycle owner
    can select its current continuity basis. Optional codec failure returns the
    original full read reference. An unsafe recovery selector fails explicitly
    with that same reference, never a replacement guard.
    Invalid run-mode opt-ins fail before that optional transport path.
    """
    try:
        activation = native_context_activation(args)
        path, raw, payload = _read_capture(args.capture_file, args.capture_sha256)
        identity = payload.get("agent_identity")
        receipt = payload.get("heartbeat_receipt")
        if (payload.get("ok") is not True or not isinstance(identity, dict) or not isinstance(receipt, dict)
                or not args.goal_id or not args.agent_id or not args.turn_instance_id
                or payload.get("goal_id") != args.goal_id or identity.get("agent_id") != args.agent_id
                or receipt.get("turn_instance_id") != args.turn_instance_id):
            raise ValueError("context-project requires the captured quota decision's exact Goal, Agent and Turn")
        if args.turn_envelope or args.decision_output_dir or args.decision_handoff_socket:
            raise ValueError("context-project presents the saved quota view; it does not change view or handoff")
    except QuotaCommandValidationError as exc:
        return {"ok": False, "reason": str(exc)}
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        return {"ok": False, "reason": str(exc) if isinstance(exc, ValueError) else "original capture could not be read",
            "recovery": "Restore the original digest-bound decision; no guard, admission or spend was performed."}

    reference = {"path": str(path), "sha256": args.capture_sha256,
        "command": context_capture_read_command(path, args.capture_sha256)}
    full_reference = dict(reference)
    mode = "full"
    target = None
    native_prepared = False
    native_view = None
    try:
        from ..control_plane.turn_driver.codex_context import codex_context_directory, project_codex_context

        if not activation["enabled"]:
            raise ValueError("context delivery is off")
        directory = codex_context_directory(directory=args.codex_context_dir,
            root=args.codex_context_root, create=True)
        if not directory:
            raise ValueError("native context is unavailable")
        # The session already owns a private recovery location. Callers need
        # only supply a separate cache root when they want another location.
        # Create a missing leaf privately; never chmod or follow an existing
        # leaf. Normal platform parent aliases (such as /tmp) remain usable.
        root = Path(args.decision_output_root or directory).expanduser().absolute()
        root = root.parent.resolve(strict=True) / root.name
        root.mkdir(mode=0o700, exist_ok=True)
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("saved projection needs a private physical capture root owned by this user")
        cache = Path(tempfile.mkdtemp(prefix="context-", dir=root))
        projected = project_codex_context(payload, directory=directory, capture_directory=cache,
            goal_id=args.goal_id, agent_id=args.agent_id, surface="quota_packet",
            capture_id=hashlib.sha256(raw).hexdigest(),
            continuity_generation=getattr(args, "_context_delivery_generation", None))
        native_prepared = True
        native_view = native_context_capture_view(projected, cache, context_directory=directory)
        reference = {key: native_view[key] for key in ("path", "sha256", "command")}
        mode, target = native_view["mode"], native_view.get("target")
    except (QuotaDecisionTransportError, OSError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, QuotaDecisionTransportError) or (activation["enabled"] and not native_prepared):
            if not isinstance(exc, QuotaDecisionTransportError):
                exc = QuotaDecisionTransportError(
                    "Current context recovery could not be prepared. Read the saved "
                    "original decision and receipt; do not rerun admission."
                )
            return {"ok": False, "error_code": exc.error_code,
                "reason": " ".join(str(exc).split())[:500],
                "heartbeat_receipt": receipt, "capture_sha256": args.capture_sha256,
                "recovery": full_reference,
                "instruction": "Context presentation was not published. Read recovery.command for the "
                    "original captured decision and preserve its Turn receipt; do not rerun admission. "
                    "Authority and freshness are unchanged. " + CONTEXT_CAPTURE_READ_GUIDANCE}
        # Off is inert; after native preparation an optional wire write can fall
        # back to the original reference without leaving an older selector live.
    result: dict[str, Any] = {"ok": True, "mode": mode, **reference, **({"target": target} if target else {}),
        "recovery": full_reference,
        "capture_sha256": args.capture_sha256,
        **({"context_delivery": activation} if activation["enabled"] else {}),
        "instruction": "Presentation of the original decision only; authority and freshness are unchanged. "
            + CONTEXT_CAPTURE_READ_GUIDANCE}
    if native_view is not None:
        result.update(native_view)
    return result


def prepare_quota_capture(args: argparse.Namespace, directory: Path | None) -> Path | None:
    """Validate transport options before allocating a new guard capture."""
    if directory is None and (
        getattr(args, "decision_output_dir", None) is not None
        or getattr(args, "decision_output_root", None) is not None
        or getattr(args, "decision_handoff_socket", None) is not None
        or getattr(args, "codex_context_dir", None) is not None
        or getattr(args, "codex_context_root", None) is not None
        or getattr(args, "context_delivery", "off") != "off"
    ):
        from .quota_context import validate_quota_command_context_request

        validate_quota_command_context_request(args)
        return prepare_decision_capture(args)
    return directory


def prepare_decision_capture(args: argparse.Namespace) -> Path | None:
    enabled = native_context_activation(args)["enabled"]
    if enabled and not (getattr(args, "codex_context_dir", None) or getattr(args, "codex_context_root", None)
                        or getattr(args, "decision_handoff_socket", None)):
        raise QuotaCommandValidationError("context-delivery on requires a native context location or explicit host handoff")
    requested = getattr(args, "decision_output_dir", None)
    root = getattr(args, "decision_output_root", None)
    handoff = getattr(args, "decision_handoff_socket", None)
    if enabled and (getattr(args, "codex_context_dir", None) is not None
            or getattr(args, "codex_context_root", None) is not None) and (
            args.format != "json" or (requested is None and root is None)):
        raise QuotaCommandValidationError(
            "native Codex context delivery needs JSON and a capture destination")
    if handoff is not None:
        validate_handoff_socket(handoff)
        if requested is None and root is None:
            raise QuotaCommandValidationError("decision handoff requires an explicit decision capture destination")
    if requested is None and root is None:
        return None
    if requested is not None and root is not None:
        raise QuotaCommandValidationError("choose one decision capture destination")
    if args.quota_command != "should-run" or not (
        getattr(args, "turn_instance_id", None)
        or bool(getattr(args, "begin_turn", False))
    ):
        raise QuotaCommandValidationError(
            "decision capture requires quota should-run and either "
            "--turn-instance-id or --begin-turn"
        )
    try:
        if root is not None:
            parent = Path(root).expanduser().absolute()
            if parent.is_symlink() or not parent.is_dir():
                raise OSError("capture root must be an existing directory, not a symlink")
            return Path(tempfile.mkdtemp(prefix="decision-", dir=parent))
        assert requested is not None  # root is absent; exactly one destination was checked above.
        directory = Path(requested).expanduser().absolute()
        # A new directory per invocation prevents overwriting a prior observation,
        # including a prior invocation in the same Turn. Existing symlinks fail too.
        directory.mkdir(mode=0o700)
    except OSError as exc:
        raise QuotaCommandValidationError(
            "decision capture needs a new directory or an existing writable root; "
            f"no guard was run: {exc}"
        ) from exc
    return directory


def capture_decision(directory: Path | None, payload: dict[str, object]) -> None:
    if directory is None:
        return
    from ..control_plane.turn_driver.decision_capture import write_decision_capture

    try:
        write_decision_capture(directory / "decision.json", payload, publish=os.replace)
    except (OSError, ValueError) as exc:
        raise QuotaDecisionTransportError(
            "Decision capture failed after guard evaluation; the Turn receipt may "
            "already exist. Do not treat this as missing admission or blindly rerun "
            f"the guard. Inspect the existing Turn receipt. Capture directory: {directory}"
        ) from exc


def bind_capture_selection_transport(payload: dict[str, Any], args: argparse.Namespace) -> None:
    """Retain caller display/capture options in the existing selection command.

    Only transport argv is added here; the typed quota owner still selects the
    action and owns admission. Each selection invocation allocates a new capture.
    """
    root = getattr(args, "decision_output_root", None)
    if root is None:
        return
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, dict) else None
    selection = cli.get("selection_command") if isinstance(cli, dict) else None
    if not isinstance(selection, dict):
        return
    template = selection.get("command_args_template")
    if not isinstance(template, str):
        return
    suffix = " --decision-output-root " + shlex.quote(str(Path(root).expanduser().absolute()))
    if getattr(args, "decision_handoff_socket", None):
        suffix += " --decision-handoff-socket " + shlex.quote(args.decision_handoff_socket)
    if getattr(args, "turn_envelope", False):
        suffix += " --turn-envelope"
    if getattr(args, "context_delivery", "off") != "off":
        suffix += " --context-delivery " + shlex.quote(args.context_delivery)
    if getattr(args, "codex_context_dir", None):
        suffix += " --codex-context-dir " + shlex.quote(args.codex_context_dir)
    if getattr(args, "codex_context_root", None):
        suffix += " --codex-context-root " + shlex.quote(args.codex_context_root)
    selection["command_args_template"] = template + suffix


def handoff_decision(directory: Path | None, socket_path: str | None,
                     context_delivery_enabled: bool = False) -> tuple[dict[str, Any], str] | None:
    """Withhold the original output until the Host releases these exact bytes.

    No second guard, admission, settlement or retry is performed here. A lost
    acknowledgement leaves the saved decision available for original-identity
    recovery; it must never be silently replaced by a later observation.
    """
    if socket_path is None:
        return None
    if directory is None:
        raise ValueError("decision handoff requires a captured decision")
    path = directory / "decision.json"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(120)
            channel.connect(str(validate_handoff_socket(socket_path)))
            channel.sendall(json.dumps({"path": str(path), "sha256": digest}).encode() + b"\n")
            with channel.makefile("rb") as stream:
                line = stream.readline(1025)
            response = json.loads(line)
            ordinary_release = {"released": True, "sha256": digest}
            if response not in (ordinary_release, {**ordinary_release, "context_delivery": True}):
                raise ValueError("Host did not release the original decision digest")
        if path.read_bytes() != raw:
            raise ValueError("captured decision changed during handoff")
    except (OSError, ValueError) as exc:
        raise QuotaDecisionTransportError(
            "Decision handoff failed after guard evaluation; inspect the original "
            f"Turn receipt and {path}. Do not blindly rerun the guard."
        ) from exc
    if response.get("context_delivery") is not True or not context_delivery_enabled:
        return None
    # Both the supported caller opt-in and explicit Host request are required.
    # The release itself does not prove
    # delivery/retention. Read one private snapshot, never an arbitrary pathname
    # supplied by a response or a model. Invalid optimization input selects full.
    request_path = directory / "delivery-request.json"
    try:
        from ..control_plane.effect_runtime import MAX_REQUEST_BYTES
        descriptor = os.open(request_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or
                    metadata.st_size > MAX_REQUEST_BYTES or metadata.st_uid != os.getuid()):
                return {}, digest
            raw = source.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return {}, digest
        request = json.loads(raw)
        return (request if isinstance(request, dict) else {}), digest
    except (OSError, ValueError, RecursionError):
        return {}, digest


def handle_native_context_command(args: argparse.Namespace, *, print_payload: Callable[..., None],
                                  registry_path: Path | None = None, runtime_root_arg: str | None = None) -> int | None:
    """Dispatch native presentation/lifecycle IO before any quota authority read."""
    if getattr(args, "max_page_output_bytes", None) is not None and args.quota_command != "context-read":
        print_payload({"ok": False, "reason": "max-page-output-bytes is only supported by JSON context-read"},
            args.format, lambda value: str(value["reason"]))
        return 1
    if registry_path is not None:
        from .context_machine import apply_context_machine_options

        try:
            apply_context_machine_options(args, registry_path=registry_path, runtime_root_arg=runtime_root_arg)
        except (OSError, ValueError, TypeError) as exc:
            print_payload({"ok": False, "reason": str(exc)}, args.format,
                lambda value: json.dumps(value, ensure_ascii=False, indent=2))
            return 1
    if args.quota_command in ("context-config", "context-hook"):
        try:
            activation = native_context_activation(args)
        except QuotaCommandValidationError as exc:
            payload = {"ok": False, "reason": str(exc)}
            print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
            return 1
    if args.quota_command == "context-config":
        from ..command_invocation import loopx_cli_argv
        from ..control_plane.turn_driver.codex_context import codex_context_configuration

        try:
            if not activation["enabled"]:
                payload = {"ok": True, "context_delivery": activation}
                print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
                return 0
            directory = getattr(args, "codex_context_root", None) or args.codex_context_dir
            if not directory:
                raise ValueError("context-config requires codex-context-root or codex-context-dir")
            machine_runtime = getattr(args, "_context_delivery_machine_runtime", None)
            cli_argv = loopx_cli_argv() + (["--runtime-root", machine_runtime] if machine_runtime else [])
            config = codex_context_configuration(directory, goal_id=args.goal_id, agent_id=args.agent_id,
                cli_argv=cli_argv, scenario=activation["scenario"], session_root=bool(getattr(args, "codex_context_root", None)),
                inherit_machine=bool(machine_runtime))
            payload = {"ok": True, "context_delivery": activation, "native_config": config,
                "activation": "Merge hooks and append developer_instructions to the existing native configuration; "
                    "review and trust only these exact hooks in Codex. Restart/resume with the resulting configuration. "
                    + ("Machine defaults are re-read on each invocation; generated hooks inherit the same machine runtime. "
                       "Omit per-command activation and location flags to retain that inheritance. " if machine_runtime else
                       "Pass --context-delivery on with the same declared host mode, context location and a decision-output-root to heartbeat-prompt/should-run. ")
                    +
                    "Normal completed full reads establish an explicit continuity assumption; no input diagnostics or provider change is required. "
                    "Known invalidation or incomplete reads keep full recovery. Set --context-delivery off and remove context options to restore ordinary output. "
                    "This command installs nothing and grants no execution authority."}
        except (OSError, ValueError, TypeError) as exc:
            payload = {"ok": False, "reason": str(exc)}
        print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    if args.quota_command == "context-hook":
        import sys
        from ..control_plane.effect_runtime import MAX_REQUEST_BYTES
        from ..control_plane.turn_driver.codex_context import (
            CONTEXT_LIFECYCLE_HOOKS, codex_context_directory, codex_context_hook,
        )

        # Native transport only: no registry discovery, guard or quota effects.
        try:
            if not activation["enabled"]:
                print("{}")
                return 0
            raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            event = json.loads(raw) if len(raw) <= MAX_REQUEST_BYTES else None
            if (isinstance(event, dict) and isinstance(event.get("session_id"), str) and event["session_id"]
                    and event.get("hook_event_name") in CONTEXT_LIFECYCLE_HOOKS):
                directory = codex_context_directory(directory=args.codex_context_dir,
                    root=getattr(args, "codex_context_root", None), session=event.get("session_id"), create=True)
                payload = codex_context_hook(directory, event)
            else:
                payload = {}
        except (OSError, ValueError, TypeError, RecursionError):
            payload = {}
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    if args.quota_command == "context-project":
        payload = project_saved_context_capture(args)
        print_payload(payload, args.format, lambda value: str(value.get("command", value.get("reason", ""))))
        return 0 if payload["ok"] else 1
    if args.quota_command == "context-read":
        # Presentation recovery reads the original capture, never current Goal
        # state. In particular it must not evaluate a guard or append a receipt.
        payload = read_context_capture(args)
        print_payload(payload, args.format, lambda value: str(value.get("content", value.get("reason", ""))))
        return 0 if payload["ok"] else 1
    return None


def read_replan_view(args: argparse.Namespace) -> dict[str, Any]:
    """Read a captured observation only; no registry, provider, guard or receipts."""
    from ..control_plane.quota.turn_envelope import _turn_envelope_result
    from ..command_invocation import current_loopx_cli_argv

    if not args.decision_file or not args.source_hash:
        raise ValueError("replan-view requires --decision-file and --source-hash")
    path = Path(args.decision_file).expanduser()
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    return _turn_envelope_result("replan_view", payload=payload, source_hash=args.source_hash,
                                 captured_decision_path=str(path), reader_argv=current_loopx_cli_argv(),
                                 stage=args.replan_stage, outcome_ref=args.outcome_ref)


def handle_replan_view(
    args: argparse.Namespace,
    print_payload: Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None],
) -> int:
    """Render the captured read without entering live quota dispatch or recovery."""
    try:
        view = read_replan_view(args)
    except (OSError, ValueError, EffectRuntimeRejected) as exc:
        view = {"ok": False, "reason": str(exc)}
    print_payload(view, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if view["ok"] else 1
