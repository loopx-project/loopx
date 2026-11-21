"""Private original-capture and presentation IO; TS owns binding validation."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import tempfile

from ..content_digest import BARE_SHA256_PATTERN
from ..quota.error_codes import QuotaCommandValidationError
from ..quota.turn_envelope import bind_turn_decision_capture
from .native_context_profile import (
    NATIVE_CONTEXT_HISTORY_TOKENS as NATIVE_CONTEXT_HISTORY_TOKENS,
    NATIVE_CONTEXT_LARGE_OUTPUT_BYTES as NATIVE_CONTEXT_LARGE_OUTPUT_BYTES,
    NATIVE_CONTEXT_OUTPUT_ENV as NATIVE_CONTEXT_OUTPUT_ENV,
    NATIVE_CONTEXT_PAGE_BYTES as NATIVE_CONTEXT_PAGE_BYTES,
    NATIVE_CONTEXT_PAGE_OUTPUT_BYTES as NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
    native_context_page_budget as native_context_page_budget,
)

Payload = dict[str, object]
PayloadView = Mapping[str, object]


def _as_mapping(value: object) -> PayloadView:
    return value if isinstance(value, Mapping) else {}


def _read_physical_bytes(
    value: str, *, max_bytes: int, private_uid: int | None,
    symlink_error: str, size_error: str, private_error: str,
    stat_size_error: str | None = None, exact_size: bool = False,
) -> tuple[Path, bytes]:
    path = Path(value).expanduser().absolute()
    # Parent aliases such as macOS /tmp are normal capture locations.
    # Resolve those once, but never follow a substituted final file.
    path = path.parent.resolve(strict=True) / path.name
    if path.is_symlink():
        raise ValueError(symlink_error)
    with os.fdopen(open_regular_capture(path), "rb") as stream:
        info = os.fstat(stream.fileno())
        if info.st_size > max_bytes:
            raise ValueError(stat_size_error or size_error)
        if private_uid is not None and (info.st_uid != private_uid or info.st_mode & 0o077):
            raise ValueError(private_error)
        raw = stream.read(info.st_size + 1 if exact_size else max_bytes + 1)
    if len(raw) > max_bytes or (exact_size and len(raw) != info.st_size):
        raise ValueError(size_error)
    return path, raw


def _lookup_path(root: object, path: Iterable[object], *, missing_message: str | None = None) -> object:
    value = root
    for key in path:
        if isinstance(value, Mapping) and type(key) is str and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        elif missing_message is not None:
            raise ValueError(missing_message)
        else:
            raise KeyError(key)
    return value


def private_capture_supported() -> bool:
    """Whether this host has the POSIX identity needed for private capture IO."""
    return os.name == "posix" and callable(getattr(os, "getuid", None))


def private_capture_uid() -> int:
    """Fail closed until this IO boundary has a qualified non-POSIX ACL reader.

    POSIX mode bits cannot establish Windows privacy. Check before creating or
    reading private captures, and before evaluating bounded settlement commands.
    """
    getuid = getattr(os, "getuid", None) if os.name == "posix" else None
    uid = getuid() if callable(getuid) else None
    if not isinstance(uid, int):
        raise ValueError("private capture validation is unsupported on this platform; "
                         "use the same Turn command without --max-page-output-bytes")
    return uid


def _decision_json(payload: PayloadView) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def write_decision_capture(
    path: Path, payload: PayloadView, *, publish: Callable[[str, Path], None] = os.link,
) -> str:
    """Publish complete private JSON once and return its exact byte digest.

    Digest the bytes we publish, rather than reopening the file solely to hash
    it. Independent readers still verify the original bytes on every read.
    """
    raw = _decision_json(payload).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".decision-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        publish(temporary, path)
        return hashlib.sha256(raw).hexdigest()
    finally:
        Path(temporary).unlink(missing_ok=True)


def open_regular_capture(path: Path) -> int:
    """Bind a regular file to its directory entry before reading.

    Nonblocking open avoids waiting on FIFOs under the journal lock. Checking
    descriptor identity also rejects substituted entries on hosts without
    O_NOFOLLOW. Callers retain their own size and privacy checks.
    """
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        opened, entry = os.fstat(descriptor), os.lstat(path)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("capture must be a regular file")
        if stat.S_ISLNK(entry.st_mode):
            raise ValueError("capture must not be a symlink")
        if (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("capture changed while it was opened")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def read_turn_observation_text(path: Path) -> str:
    with os.fdopen(open_regular_capture(path), encoding="utf-8") as source:
        return source.read()


@dataclass(frozen=True)
class StagedTurnDecisionCapture:
    """Validated private IO, published only after the native start is admitted."""

    plan: Payload
    path: Path
    observation: Payload
    existed: bool

    def publish(self) -> Payload:
        if self.path.is_symlink():
            raise ValueError("Turn decision capture must not be a symlink")
        if self.path.exists():
            # Python considers True == 1; original JSON evidence must retain
            # its value types as well as its content. Object key order is IO.
            text = read_turn_observation_text(self.path)
            # Compact objects start with a quoted key (or are empty). Pretty
            # captures can go straight to their order-independent comparison.
            if (not (text.startswith('{"') or text == "{}\n") or text != _decision_json(self.observation)) and (
                    json.dumps(json.loads(text), sort_keys=True, allow_nan=False)
                    != json.dumps(self.observation, sort_keys=True, allow_nan=False)):
                raise ValueError("Turn decision capture changed before publication")
        elif self.existed:
            raise ValueError("Original Turn decision capture is missing; restore the original observation")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_decision_capture(self.path, self.observation)
        return self.plan

    def publish_for_host(self, request: PayloadView) -> tuple[Payload, Payload]:
        """Install the published original input without changing recovery bindings."""
        plan = self.publish()
        from ..quota.turn_envelope import (
            COMPLETE_TURN_HOST_REQUEST_SCHEMA, COMPLETE_TURN_PLAN_SCHEMA_VERSION,
            LOOPX_TURN_HOST_REQUEST_SCHEMA, captured_turn_host_input, turn_plan_host_source,
        )

        if plan.get("schema_version") == COMPLETE_TURN_PLAN_SCHEMA_VERSION:
            bound_request: Payload = {**request, **turn_plan_host_source(plan)}
            bound_request.pop("turn_envelope", None)
            return plan, bound_request
        bound_request = {**request, "turn_envelope": plan["turn_envelope"]}
        if "work_contract" in plan:
            contract = plan["work_contract"]
            bound_request["work_contract"] = dict(contract) if isinstance(contract, Mapping) else contract
        else:
            # Only an original historical absence can clear the current input.
            bound_request.pop("work_contract", None)
        if request.get("schema_version") == COMPLETE_TURN_HOST_REQUEST_SCHEMA:
            if "work_contract" in plan:
                bound_request = dict(captured_turn_host_input(bound_request))
            else:
                # The immutable original observation, not a bad current source,
                # owns restoration of a historical request version.
                bound_request["schema_version"] = LOOPX_TURN_HOST_REQUEST_SCHEMA
                bound_request.pop("source_decision_hash", None)
                bound_request.pop("detail_ref", None)
        return plan, bound_request


def stage_turn_decision_capture(
    plan: PayloadView, *, journal_path: Path, decision: PayloadView | None, existing_journal: bool,
) -> StagedTurnDecisionCapture:
    """Validate under the journal lock before admission, without publishing IO.

    Existing journals must retain their original reference. A missing legacy
    observation cannot be reconstructed from today's guard. Already committed
    journals remain readable through ordinary no-effect replay.
    """
    from ..quota.turn_envelope import COMPLETE_TURN_PLAN_SCHEMA_VERSION, turn_plan_host_source

    current = plan.get("schema_version") == COMPLETE_TURN_PLAN_SCHEMA_VERSION
    envelope = _as_mapping(turn_plan_host_source(plan) if current else plan["turn_envelope"])
    transaction = _as_mapping(plan["transaction"])
    path = journal_path.parent / "decisions" / journal_path.name
    capture = _as_mapping(envelope.get("detail_ref")).get("captured_decision")
    if existing_journal and not capture:
        raise ValueError("This Turn journal has no original decision capture; preserve it and "
                         "recover its pending effects before starting a fresh Turn")
    if path.is_symlink():
        raise ValueError("Turn decision capture must not be a symlink")
    existed = path.exists()
    if existed:
        loaded = json.loads(read_turn_observation_text(path))
        observation: Payload = dict(loaded) if isinstance(loaded, Mapping) else {}
    elif existing_journal or capture:
        raise ValueError("Original Turn decision capture is missing; restore the original "
                         "observation, do not rerun the guard to replace it")
    else:
        if decision is None:
            raise ValueError("New Turn requires its full original decision")
        observation = dict(decision)
    from ...command_invocation import current_loopx_cli_argv

    turn_key = str(transaction["turn_key"])
    bound = bind_turn_decision_capture(
        observation, None if current else envelope, captured_decision_path=str(path),
        turn_instance_id=str(transaction.get("turn_instance_id") or turn_key), turn_key=turn_key,
        # New journals have a real immutable source and executable reader.
        # Legacy journals retain their recorded delivery/recovery route.
        reader_argv=current_loopx_cli_argv() if not existing_journal else None,
        complete_host_request=({k: v for k, v in envelope.items() if k != "work_contract"} if current else None),
    )
    restored: Payload = ({**plan, "source_decision_hash": bound["source_decision_hash"], "detail_ref": bound["detail_ref"]}
                         if current else {**plan, "turn_envelope": bound})
    if observation.get("schema_version") == "loopx_turn_envelope_v1":
        restored["work_contract"] = observation
    else:
        # Historical observations retain their original input contract. Never
        # pair an old capture with a freshly observed complete decision.
        restored.pop("work_contract", None)
    return StagedTurnDecisionCapture(restored, path, observation, existed)


def prepare_turn_decision_capture(
    plan: PayloadView, *, journal_path: Path, decision: PayloadView | None, existing_journal: bool,
) -> Payload:
    """Retain the existing immediate publication API for original-capture callers."""
    return stage_turn_decision_capture(
        plan, journal_path=journal_path, decision=decision, existing_journal=existing_journal).publish()


_HANDOFF_CAPTURE_ROOT_LIMIT = 4096


def _handoff_capture_root_file(endpoint: Path) -> Path:
    return endpoint.with_name(endpoint.name + ".capture-root")


def bind_handoff_capture_root(socket_path: str, capture_root: Path) -> None:
    """Publish the current Host's transport destination beside its private socket."""
    endpoint = validate_handoff_socket(socket_path)
    raw = os.fsencode(capture_root)
    if capture_root.resolve() != capture_root or len(raw) > _HANDOFF_CAPTURE_ROOT_LIMIT:
        raise ValueError("decision handoff requires a physical absolute capture root")
    binding = _handoff_capture_root_file(endpoint)
    with os.fdopen(os.open(binding, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(raw)


def _validate_handoff_capture_root(endpoint: Path, requested_root: Path) -> None:
    try:
        _, bound_root = _read_physical_bytes(
            str(_handoff_capture_root_file(endpoint)), max_bytes=_HANDOFF_CAPTURE_ROOT_LIMIT,
            private_uid=os.getuid(), symlink_error="capture root binding must not be a symlink",
            size_error="invalid capture root binding size",
            private_error="capture root binding must be private to the Host user")
        if not bound_root:
            raise ValueError("invalid capture root binding size")
    except FileNotFoundError:
        return  # Earlier Hosts still validate the capture when accepting it.
    except (OSError, ValueError) as exc:
        raise QuotaCommandValidationError(f"invalid decision handoff binding; no guard was run: {exc}") from exc
    try:
        if requested_root.resolve(strict=True) != requested_root:
            raise ValueError("capture root must remain a physical absolute directory")
    except (OSError, ValueError, RuntimeError) as exc:
        raise QuotaCommandValidationError(f"invalid decision handoff root; no guard was run: {exc}") from exc
    if bound_root != os.fsencode(requested_root):
        raise QuotaCommandValidationError(
            "decision handoff capture root differs from the current Host wake; no guard was run. "
            "Use the --decision-output-root named by the current wake.")


def validate_handoff_socket(value: str, *, capture_root: Path | None = None) -> Path:
    """A caller-owned local transport."""
    path = Path(value)
    try:
        if (os.name != "posix" or not hasattr(socket, "AF_UNIX") or not path.is_absolute()
                or path.resolve(strict=True) != path or not stat.S_ISSOCK(path.stat().st_mode)
                or path.parent.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid()):
            raise OSError("expected a same-user socket in a private physical directory")
    except FileNotFoundError as exc:
        # A resumed session can remember an earlier wake's private endpoint.
        # Reject before any guard runs and name the current-wake remedy.
        raise QuotaCommandValidationError(
            "invalid decision handoff socket: the endpoint no longer exists; it belonged "
            "to a Host wake that has ended. No guard was run. Rerun with the "
            "--decision-output-root and --decision-handoff-socket named by the current "
            "wake, or without both when the current wake names none") from exc
    except OSError as exc:
        raise QuotaCommandValidationError(f"invalid decision handoff socket: {exc}") from exc
    if capture_root is not None:
        _validate_handoff_capture_root(path, capture_root)
    return path


CODE_MODE_EXEC_RESULT_FORWARDER = "text(result.exit_code === 0 ? result.output : result)"
CONTEXT_CAPTURE_CODE_MODE_GUIDANCE = (
    "In code-mode, forward exec_command results with `" + CODE_MODE_EXEC_RESULT_FORWARDER + "`. "
    "Keep a running command's session_id and finish it with write_stdin; "
    "for a yielded functions.exec cell_id use functions.wait. "
    "Running output or empty stdout does not prove completion; preserve failures. "
)

_CONTEXT_CAPTURE_PAGE_START = (
    "Follow next_command through eof and combine content in byte-offset order. "
    + CONTEXT_CAPTURE_CODE_MODE_GUIDANCE +
    "The reader verifies the original capture SHA-256; keep its returned digest and byte cursors. "
    "Do not hash or retype the packet. "
)
_CONTEXT_CAPTURE_PAGE_END = (
    "Legacy default pages can be batched to save round trips; start with at most two "
    "and inspect every output. A host may truncate inside valid JSON or omit a whole output. "
    "Consume all model-visible content in UTF-8 byte-offset order with complete coverage through total_bytes. "
    "If a range or output is missing, truncated or uncertain, reread the affected range "
    "of this same capture with fewer pages or a smaller --page-bytes; do not trust a preview or EOF alone. "
    "Do not rerun admission or ask the owner. File existence and page EOF are not retention proof."
)
CONTEXT_CAPTURE_SELECTION_GUIDANCE = (
    "For known required pointers, repeat --select in one call without listing again. "
    "For unknown pointers, use --list on the nearest known parent; add --list-depth 2 "
    "when nested discovery helps, expanding only needed branches. "
    "Size batches against the combined output budget; avoid selecting both a parent and its children. "
    "A missing pointer rejects the batch: inspect its parent in this capture before retrying. "
)

# A durable capture is a source library, not an instruction to import it all.
CONTEXT_CAPTURE_NAVIGATION_GUIDANCE = (
    "The complete decision is saved locally. Choose the sections needed for the current decision; "
    "you do not need to read the whole file or drain all pages before working. "
    + CONTEXT_CAPTURE_SELECTION_GUIDANCE +
    "Read applicable goals, acceptance criteria, constraints and stop conditions before dependent work; "
    "read the current operation/settlement contract before effects, and supporting evidence when verification needs it. "
    "An index is navigation, not evidence of reading or fresh authority. "
    "Keep the original Turn/capture/effect identities. After compaction recover only missing necessary facts "
    "from this capture; never rerun admission or repeat effects just to recover context. "
    "Saved-file deltas may use unread baseline files; this is not a claim of retained model input. "
    "Selected/index reads never establish full model-context retention. "
)

# Native paging verifies capture bytes; the existing codec separately declares
# its continuity basis. Generic/off readers retain their original obligations
# without claiming that a native input observer exists.
CONTEXT_CAPTURE_PAGE_GUIDANCE = _CONTEXT_CAPTURE_PAGE_START + (
    "For --max-page-output-bytes, read one output per call if retained-history limits are unknown. "
) + _CONTEXT_CAPTURE_PAGE_END
CONTEXT_CAPTURE_READ_GUIDANCE = (
    "Follow next_command through eof and combine content in byte-offset order. "
    + CONTEXT_CAPTURE_CODE_MODE_GUIDANCE +
    "Small batches can save model round trips; start with at most two default pages "
    "and inspect every output. A host may truncate inside valid JSON or omit a whole output. "
    "Check each model-visible content's UTF-8 byte length against next_offset (total_bytes at eof), "
    "complete coverage and the original SHA-256. If incomplete, reread the affected range "
    "of this same capture with fewer pages or a smaller --page-bytes; do not trust a preview or EOF alone. "
    "Do not rerun admission or ask the owner. File existence and page EOF are not retention proof."
)


def context_capture_read_command(
    path: Path, digest: str, *, offset: int = 0, page_bytes: int | None = None,
    max_page_output_bytes: int | None = None, select: list[str] | None = None,
    selection_sha256: str | None = None, list_fields: bool = False, list_depth: int | None = None,
    initial_read: bool = False, reference_file: Path | None = None,
    reader_argv: list[str] | None = None, compact: bool = False,
) -> str:
    from ...command_invocation import loopx_cli_argv
    from ...install_contract import render_host_cli_command

    if page_bytes is None and not compact:
        page_bytes = NATIVE_CONTEXT_PAGE_BYTES
        if max_page_output_bytes is None:
            max_page_output_bytes = NATIVE_CONTEXT_PAGE_OUTPUT_BYTES
    route = reader_argv if reader_argv is not None else loopx_cli_argv()
    return render_host_cli_command([
        *route, "--format", "json", "quota", "context-read",
        *(["--reference-file", str(reference_file)] if reference_file is not None
          else ["--capture-file", str(path), "--capture-sha256", digest]),
        *(["--offset", str(offset)] if offset or not compact else []),
        *(["--page-bytes", str(page_bytes)] if page_bytes is not None else []),
        *(["--initial-read"] if initial_read else []), *(["--list"] if list_fields else []),
        *(["--list-depth", str(list_depth)] if list_depth is not None else []),
        *(arg for pointer in select or [] for arg in ("--select", pointer)),
        *(["--selection-sha256", selection_sha256] if selection_sha256 is not None else []),
        *(["--max-page-output-bytes", str(max_page_output_bytes)] if max_page_output_bytes is not None else []),
    ])


def write_context_read_reference(
    path: Path, digest: str, *, selection: PayloadView | None = None, navigation: PayloadView | None = None,
    observed_read: PayloadView | None = None, selection_paths: Sequence[Sequence[str | int]] | None = None,
    reference_file: Path | None = None, reader_argv: list[str] | None = None,
) -> Path:
    """Immutable reader IO metadata.

    A short filename locates the document; only its complete stored digests are
    checked. A collision or replaced document fails closed, never overwrites.
    """
    payload: Payload = {"schema_version": "context_capture_read_reference_v1", "path": str(path), "sha256": digest, **{
        k: v for k, v in (("reader_argv", reader_argv), ("selection", selection),
                          ("navigation", navigation), ("observed_read", observed_read)) if v is not None}}
    if selection is not None and selection_paths is not None:
        from ..effect_runtime import MAX_REQUEST_BYTES

        # A large descriptor can still use the older selector replay route.
        with_paths: Payload = {**payload, "selection_paths": selection_paths}
        if len(_decision_json(with_paths).encode("utf-8")) <= MAX_REQUEST_BYTES:
            payload = with_paths
    suffix = ("." + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
              if selection is not None or navigation is not None else "")
    if reference_file is not None and selection is not None:
        reference_file = reference_file.with_name(reference_file.name + suffix)
    reference = reference_file or path.with_name(path.stem + suffix + ".reader.json")
    try:
        write_decision_capture(reference, payload)
    except FileExistsError:
        existing = read_context_read_reference(str(reference))[1]
        existing_path = existing.get("path")
        if isinstance(existing_path, str) and existing_path != payload["path"]:
            # Readers resolve parent aliases, while retaining the exact leaf.
            # Compare that same spelling without rewriting immutable metadata
            # or treating a different leaf (including a hardlink) as identical.
            orig, curr = Path(existing_path).expanduser().absolute(), path.expanduser().absolute()
            if orig.parent.resolve(strict=True) / orig.name == curr.parent.resolve(strict=True) / curr.name:
                existing["path"] = payload["path"]
        if json.dumps(existing, sort_keys=True) != json.dumps(payload, sort_keys=True):
            raise ValueError("original reader reference changed; restore it without rerunning admission") from None
    return reference


def read_context_read_reference(value: str) -> tuple[Path, Payload]:
    """Read one private physical descriptor; the caller verifies capture bytes."""
    from ..effect_runtime import MAX_REQUEST_BYTES

    path, raw = _read_physical_bytes(value, max_bytes=MAX_REQUEST_BYTES, private_uid=private_capture_uid(),
        symlink_error="reader reference must be a physical file, not a symlink",
        size_error="reader reference exceeds the existing transport limit",
        private_error="reader reference must be a private regular file owned by this user",
        stat_size_error="reader reference must be a private regular file owned by this user")
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or payload.get("schema_version") != "context_capture_read_reference_v1"
            or not isinstance(payload.get("path"), str) or not isinstance(payload.get("sha256"), str)
            or BARE_SHA256_PATTERN.fullmatch(payload["sha256"]) is None
            or ("selection" in payload and not isinstance(payload["selection"], dict))
            or ("selection_paths" in payload
                and ("selection" not in payload or not isinstance(payload["selection_paths"], list)))):
        raise ValueError("reader reference is incomplete or unsupported")
    if "reader_argv" in payload:
        from ...command_invocation import validate_context_reader_route

        # A compact reference may inherit one exact CLI override. Arbitrary
        # executable strings are not interpreted by the reader.
        validate_context_reader_route(payload["reader_argv"])
    return path, payload


def read_context_capture_file(value: str, digest: str, *, private: bool = False) -> tuple[Path, bytes, Payload]:
    """One bounded physical-file/hash check for readers and native observations."""
    from ..effect_runtime import MAX_REQUEST_BYTES

    if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None or not value:
        raise ValueError("context-read requires the original capture file and its exact SHA-256")
    path, raw = _read_physical_bytes(value, max_bytes=MAX_REQUEST_BYTES,
        private_uid=(private_capture_uid() if private else None),
        symlink_error="context-read requires the physical original capture path, not a symlink",
        size_error="capture must be a regular JSON file within the existing transport size limit",
        private_error="native context requires a private capture owned by this user", exact_size=True)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("capture digest mismatch; restore the original capture, do not rerun admission")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("capture must contain a complete JSON object")
    return path, raw, payload


def capture_page_range(page: PayloadView, *, raw: bytes, digest: str) -> tuple[int, int] | None:
    """Verify reported page bytes, independently of a host's retention claim."""
    if ("selection_sha256" in page or "selected_pointers" in page
            or page.get("schema_version") != "context_capture_page_v1" or page.get("sha256") != digest):
        return None
    start, content = page.get("offset"), page.get("content")
    if type(start) is not int or start < 0 or not isinstance(content, str) or page.get("ok") is not True:
        return None
    data = content.encode("utf-8")
    end = start + len(data)
    if (page.get("total_bytes") != len(raw) or end > len(raw) or not data
            or page.get("eof") is not (end == len(raw))
            or page.get("next_offset") != (None if end == len(raw) else end) or raw[start:end] != data):
        # Valid page JSON and a correct cursor can still carry the wrong body.
        return None
    return start, end


def capture_context_projection(
    payload: PayloadView, *, directory: Path, read_guidance: str = CONTEXT_CAPTURE_READ_GUIDANCE,
    filename: str = "projection.json",
) -> Payload:
    """Save the full projection independently of the optional presentation codec."""
    from ...command_invocation import current_loopx_cli_argv

    if filename not in ("projection.json", "required-context.json"):
        raise ValueError("unsupported captured context filename")
    projection = directory / filename
    digest = write_decision_capture(projection, payload)
    return {
        "path": str(projection), "sha256": digest,
        "instruction": "Complete JSON for this captured view. Use it when required content is missing or uncertain; "
            "compaction alone does not require replay within an existing work Turn. " + read_guidance,
        "command": context_capture_read_command(projection, digest, reader_argv=current_loopx_cli_argv()),
    }


def capture_progressive_context(
    payload: PayloadView, *, directory: Path, surface: str = "quota_packet",
    selection_command_args_template: str | None = None, captured_decision: Mapping[str, str] | None = None,
    replan_reader_argv: list[str] | None = None,
) -> tuple[PayloadView, Payload]:
    """Publish the complete current source before optional stateless presentation.

    The typed owner applies the shrink guard. This neither creates a native
    journal nor requires/adopts an execution Turn. All recovery is same-capture.
    A caller may supply the exact decision it just verified for staged reads.
    """
    from ..quota.turn_envelope import prepare_progressive_context

    source = capture_context_projection(payload, directory=directory)
    optional: Payload = {k: v for k, v in (
        ("selection_command_args_template", selection_command_args_template),
        ("captured_decision", dict(captured_decision) if captured_decision is not None else None),
        ("replan_reader_argv", replan_reader_argv),
    ) if v is not None}
    transaction = prepare_progressive_context(payload, surface=surface, source_reference={
        "path": source["path"], "sha256": source["sha256"], "source_path": source["path"],
        "command_template": f"{source['command']} --select '<pointer>'", **optional})
    if not transaction["projected"]:
        return payload, source
    raw_required = transaction["context"]
    required: Payload = dict(raw_required) if isinstance(raw_required, Mapping) else {}
    return required, capture_context_projection(required, directory=directory, filename="required-context.json")


def project_captured_context(
    payload: Payload, *, directory: Path | None, request: PayloadView | None,
    goal_id: str, agent_id: str, surface: str, capture_id: str, captured_recovery: PayloadView | None = None,
) -> Payload:
    """Adapt an admitted capture to the shared TS presentation owner.

    The caller-owned rendezvous may supply a continuity observation. It must
    either confirm intact final input or explicitly select native lifecycle
    assumptions. Unsupported ordinary CLI clients never supply this request. Every
    opt-in attempt saves the complete projected JSON for same-capture recovery.

    A native caller may pass its already-published recovery reference, never a
    reference supplied in the external request.
    """
    if request is None or directory is None:
        return payload
    from ..effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
    from ..quota.turn_envelope import prepare_context_delivery

    try:
        recovery = (dict(captured_recovery) if captured_recovery is not None
                    else capture_context_projection(payload, directory=directory))
        scope = request.get("scope")
        if not isinstance(scope, dict) or scope.get("goal_id") != goal_id or scope.get("agent_id") != agent_id:
            return payload
        raw_opts = {k: request.get(k) for k in ("baseline", "retained", "continuity")}
        if "native_input" in request:
            from .codex_input_trace import codex_context_in_active_input

            # An optional native observation can only narrow the caller's
            # retention claim. It never mints an ACK or enables tracing, and a
            # malformed/stale observation must not fall back to that claim.
            obs, native_session, raw_base = request["native_input"], scope.get("session_id"), raw_opts["baseline"]
            if (not isinstance(obs, dict) or not isinstance(raw_base, dict) or not isinstance(native_session, str)
                    or not codex_context_in_active_input(obs, baseline=raw_base, session_id=native_session)):
                raw_opts["retained"] = raw_opts["continuity"] = None
        if any(val is not None and not isinstance(val, Mapping) for val in raw_opts.values()):
            return payload
        opts = {k: val if isinstance(val, Mapping) else None for k, val in raw_opts.items()}
        delivery = dict(prepare_context_delivery(
            payload, scope=scope, capture_id=capture_id, surface=surface,
            baseline=opts["baseline"], retained=opts["retained"], continuity=opts["continuity"]))
        delivery["recovery"] = recovery
        return delivery
    except (OSError, ValueError, TypeError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        # The original full payload is already available. An unavailable optional
        # codec/capture must not convert safe full delivery into an execution gate.
        return payload


def context_index_fields(payload: PayloadView, children: Sequence[PayloadView]) -> list[Payload]:
    """Serialize metadata for TS-verified addresses, preserving Python JSON values."""
    fields: list[Payload] = []
    for child in children:
        raw_path = child["path"]
        assert isinstance(raw_path, Iterable) and not isinstance(raw_path, (str, bytes))
        value = _lookup_path(payload, raw_path)
        kind = ("object" if isinstance(value, dict) else "array" if isinstance(value, list)
                else "null" if value is None else "boolean" if isinstance(value, bool)
                else "string" if isinstance(value, str) else "number")
        fields.append({"pointer": child["pointer"], "type": kind,
            "bytes": len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            **({"count": len(value)} if isinstance(value, (dict, list)) else {})})
    return fields


def _pointer_token(key: str | int) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def context_selection_transport(
    payload: Payload, select: list[str] | None, *, list_fields: bool, list_depth: int | None,
) -> Payload:
    """Supply the address shape for the TS selector. Values are read from the verified original."""
    # Root discovery also derives initial navigation from real decision values.
    if not select or list_fields and "" in select or not all(isinstance(pointer, str) for pointer in select):
        return payload
    paths: dict[str | None, object] = {}
    for pointer in select:
        branch = paths
        for token in pointer.split("/")[1:]:
            if isinstance(nxt := branch.setdefault(token, {}), dict):
                branch = nxt
        # None cannot collide with a JSON object key (including the empty key).
        branch[None] = True
    result: Payload = {}
    pending: list[tuple[Mapping[str, object] | list[object], Payload | list[object], dict[str | None, object], int]] = [
        (payload, result, paths, 0)]
    while pending:
        source, target, branch, depth = pending.pop()
        if list_fields and None in branch:
            depth = max(depth, list_depth or 1)
        if depth <= 0 and not any(key is not None for key in branch):
            continue
        for key, value in (source.items() if isinstance(source, Mapping) else enumerate(source)):
            token = _pointer_token(key)
            if depth <= 0 and token not in branch:
                continue
            if isinstance(value, (dict, list)):
                raw_sub = branch.get(token)
                # Length matters when validating array indices or listing children.
                child: Payload | list[object] = {} if isinstance(value, dict) else [0] * len(value)
                pending.append((value, child, raw_sub if isinstance(raw_sub, dict) else {}, depth - 1))
                entry: object = child
            else:
                # Every scalar has the same address behavior. A one-byte marker
                # also prevents short scalar arrays growing during transport.
                # Original types, precision and byte counts stay at the renderer.
                entry = 0
            if isinstance(target, dict) and isinstance(key, str):
                target[key] = entry
            elif isinstance(target, list) and isinstance(key, int):
                target[key] = entry
    return result


_GOAL_SOURCE_PREFIX = ["interaction_contract", "agent_channel", "work_context", "sources"]
_GOAL_NARRATIVE_PRESENTATION = (
    "The generated frontmatter objective appears once under ## Objective. "
    "Use --select on the same capture at this pointer for verbatim source text."
)


def _first_read_goal_text(payload: PayloadView, path: Sequence[object], value: object) -> str | None:
    # Only render the typed selector's registered Goal text leaf. The source,
    # enclosing containers, exact detail reads and unknown shapes stay literal.
    if (not isinstance(value, str) or list(path[:4]) != _GOAL_SOURCE_PREFIX
            or len(path) != 7 or type(path[4]) is not int or list(path[5:]) != ["content", "text"]):
        return None
    interaction = _as_mapping(payload.get("interaction_contract"))
    sources = _as_mapping(_as_mapping(interaction.get("agent_channel")).get("work_context")).get("sources")
    if not isinstance(sources, list) or not (0 <= path[4] < len(sources)):
        return None
    source = _as_mapping(sources[path[4]])
    content = _as_mapping(source.get("content"))
    if (interaction.get("schema_version") != "loopx_interaction_contract_v0"
            or source.get("source") != "goal_state" or content.get("ok") is False
            or content.get("read_complete") is False or not content.get("state_file")
            or not (content.get("full_source") is True or content.get("excluded_todo_regions"))):
        return None
    from ..goals.active_state_metadata import goal_narrative_without_objective_mirror

    text = goal_narrative_without_objective_mirror(value)
    # Allow room for the presentation notice and immutable selection metadata.
    return text if text is not None and len(value) - len(text) > len(_GOAL_NARRATIVE_PRESENTATION) + 96 else None


def _context_selection_bytes(
    payload: PayloadView, selections: Sequence[PayloadView], *, list_fields: bool, list_depth: int | None,
    present_goal_narrative: bool, text_append: PayloadView | None = None,
) -> tuple[bytes, list[str]]:
    """Render TS-verified addresses from the original, digest-checked values."""
    if text_append is not None and (list_fields or not isinstance(text_append, dict)):
        raise ValueError("observed text append requires a selected value")
    views: list[Payload] = []
    presented: list[str] = []
    append_rendered = False
    for item in selections:
        path, pointer = item["path"], str(item["pointer"])
        assert isinstance(path, Iterable) and not isinstance(path, (str, bytes))
        path_list = list(path)
        selected = _lookup_path(payload, path_list, missing_message="selected address does not match the original capture")
        if text_append is not None and pointer == text_append.get("pointer"):
            prefix = text_append.get("prefix_bytes")
            raw_text = selected.encode("utf-8") if isinstance(selected, str) else b""
            if type(prefix) is not int or not 0 < prefix < len(raw_text):
                raise ValueError("observed text append is outside the selected value")
            views.append({"pointer": pointer, "value": raw_text[prefix:].decode("utf-8"),
                          "presentation": "append_to_observed_text", "prefix_bytes": prefix})
            presented.append(pointer)
            append_rendered = True
        elif list_fields:
            raw_children = item["children"]
            children = [c for c in raw_children if isinstance(c, Mapping)] if isinstance(raw_children, Sequence) else []
            views.append({"pointer": pointer, "fields": context_index_fields(payload, children)})
        else:
            text = _first_read_goal_text(payload, path_list, selected) if present_goal_narrative else None
            views.append({"pointer": pointer, "value": selected} if text is None
                         else {"pointer": pointer, "value": text, "presentation": _GOAL_NARRATIVE_PRESENTATION})
            if text is not None:
                presented.append(pointer)
    if text_append is not None and not append_rendered:
        raise ValueError("observed text append does not match a selected value")
    opts = {"list_depth": list_depth} if list_depth is not None and list_depth != 1 else {}
    raw = json.dumps({"schema_version": "context_capture_index_v1" if list_fields else "context_capture_selection_v1",
        "selections": views, **opts}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    return raw, presented


def context_selection_binding(
    select: list[str], *, list_fields: bool, list_depth: int | None, sha256: str, presented: bool = False,
) -> Payload:
    return {"select": select, "initial_read": False, "list_fields": list_fields, "list_depth": list_depth,
            "sha256": sha256, **({"present_goal_narrative": True} if presented else {})}


def initial_context_selection_bytes(
    payload: PayloadView, selections: Sequence[PayloadView], *, text_append: PayloadView | None = None,
) -> tuple[bytes, list[str]]:
    return _context_selection_bytes(payload, selections, list_fields=False, list_depth=None,
                                    present_goal_narrative=True, text_append=text_append)


def write_initial_context_read_reference(
    path: Path, digest: str, payload: PayloadView, selections: Sequence[PayloadView],
    *, observed_read: PayloadView | None = None,
) -> tuple[Path, bytes]:
    """Pin the shared rendered selection and return its exact rendered bytes."""
    raw_append = observed_read.get("text_append") if observed_read else None
    raw, presented = initial_context_selection_bytes(
        payload, selections, text_append=(raw_append if isinstance(raw_append, Mapping) else None))
    selection_paths = [[k for k in rp if type(k) in (str, int)] for item in selections
        if isinstance(rp := item.get("path"), Sequence) and not isinstance(rp, (str, bytes))]
    reference = write_context_read_reference(
        path, digest, selection_paths=selection_paths, observed_read=observed_read,
        selection=context_selection_binding([str(item["pointer"]) for item in selections], list_fields=False,
            list_depth=None, sha256=hashlib.sha256(raw).hexdigest(), presented=bool(presented)))
    return reference, raw


def _pinned_selection_plan(select: list[str] | None, paths: Sequence[Sequence[str | int]]) -> Payload:
    """Replay TypeScript's resolved addresses from the existing reader reference."""
    if (not isinstance(select, list) or not select or len(select) != len(paths)
            or not all(isinstance(pointer, str) for pointer in select) or len(set(select)) != len(select)):
        raise ValueError("reader selection paths do not match its pinned addresses")
    selections: list[Payload] = []
    for pointer, path in zip(select, paths, strict=True):
        if (not isinstance(path, list) or not all(type(key) in (str, int) for key in path)
                or pointer != "".join("/" + _pointer_token(key) for key in path)):
            raise ValueError("reader selection paths do not match its pinned addresses")
        selections.append({"pointer": pointer, "path": path})
    return {"selections": selections}


class ContextSelectionDigestChanged(ValueError):
    """The rendered selection no longer matches its recorded digest."""


def context_selection_view(
    payload: Payload, *, select: list[str] | None, initial_read: bool, list_fields: bool, list_depth: int | None,
    expected_sha256: str | None, preserve_selection_order: bool = False, present_goal_narrative: bool | None = None,
    selection_paths: Sequence[Sequence[str | int]] | None = None, text_append: PayloadView | None = None,
) -> tuple[Payload, bytes | None, str | None]:
    """Render the digest-pinned view from a new or previously resolved plan."""
    if text_append is not None and (initial_read or list_fields or expected_sha256 is None):
        raise ValueError("observed text append requires a pinned value selection")
    if selection_paths is not None:
        if initial_read or list_fields or list_depth is not None or not preserve_selection_order or expected_sha256 is None:
            raise ValueError("reader selection paths require a pinned value selection")
        plan = _pinned_selection_plan(select, selection_paths)
    else:
        from ..quota.turn_envelope import _turn_envelope_result

        context: Payload = payload if initial_read else context_selection_transport(
            payload, select, list_fields=list_fields, list_depth=list_depth)
        plan_args: Payload = {**({"initial_read": True} if initial_read else {"select": select}),
                              **({"list_depth": list_depth} if list_depth is not None else {})}
        plan = dict(_turn_envelope_result("context_select", context=context, list_fields=list_fields,
            recover_selection_errors=not initial_read, **plan_args,
            **({"preserve_selection_order": True} if preserve_selection_order else {})))
    if plan.get("rejected_selection"):
        return plan, None, None
    presentation = initial_read if present_goal_narrative is None else present_goal_narrative

    def render(rendered_plan: PayloadView, present: bool) -> tuple[bytes, str, list[str]]:
        raw_sels = rendered_plan.get("selections")
        sels = [s for s in raw_sels if isinstance(s, Mapping)] if isinstance(raw_sels, Sequence) else []
        raw, displayed = _context_selection_bytes(payload, sels, list_fields=list_fields, list_depth=list_depth,
                                                  present_goal_narrative=present, text_append=text_append)
        return raw, hashlib.sha256(raw).hexdigest(), displayed

    raw, digest, displayed = render(plan, presentation)
    if selection_paths is None and preserve_selection_order and expected_sha256 != digest:
        # Historical explicit references may pin canonical output despite
        # retaining caller order. Only the same source and addresses can replay.
        plan = dict(_turn_envelope_result("context_select", context=context, list_fields=list_fields, **plan_args))
        raw, digest, displayed = render(plan, presentation)
    if (expected_sha256 is not None and expected_sha256 != digest
            and initial_read and present_goal_narrative is None and displayed):
        # An unpinned initial cursor may have started before presentation was
        # enabled. Its original digest must still match the literal view.
        raw, digest, displayed = render(plan, False)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ContextSelectionDigestChanged("selected view digest changed; restore the original selection and reader")
    return {**plan, "presentation_pointers": displayed} if displayed else plan, raw, digest
