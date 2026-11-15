"""Private original-capture and presentation IO; TS owns binding validation."""
from __future__ import annotations

from typing import Any, cast

import hashlib
import json
import os
import socket
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..quota.turn_envelope import bind_turn_decision_capture
from ..quota.error_codes import QuotaCommandValidationError
from ..content_digest import BARE_SHA256_PATTERN
from .native_context_profile import (
    NATIVE_CONTEXT_PAGE_BYTES as NATIVE_CONTEXT_PAGE_BYTES,
    NATIVE_CONTEXT_PAGE_OUTPUT_BYTES as NATIVE_CONTEXT_PAGE_OUTPUT_BYTES,
    NATIVE_CONTEXT_LARGE_OUTPUT_BYTES as NATIVE_CONTEXT_LARGE_OUTPUT_BYTES,
    NATIVE_CONTEXT_HISTORY_TOKENS as NATIVE_CONTEXT_HISTORY_TOKENS,
    NATIVE_CONTEXT_OUTPUT_ENV as NATIVE_CONTEXT_OUTPUT_ENV,
    native_context_page_budget as native_context_page_budget,
)


def private_capture_supported() -> bool:
    """Whether this host has the POSIX identity needed for private capture IO."""
    return os.name == "posix" and callable(getattr(os, "getuid", None))


def private_capture_uid() -> int:
    """Fail closed until this IO boundary has a qualified non-POSIX ACL reader.

    POSIX mode bits cannot establish Windows privacy. Check before creating or
    reading private captures, and before evaluating bounded settlement commands.
    """
    if not private_capture_supported():
        raise ValueError("private capture validation is unsupported on this platform; "
                         "use the same Turn command without --max-page-output-bytes")
    return cast(int, getattr(os, "getuid")())


def _decision_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def write_decision_capture(
    path: Path, payload: Mapping[str, Any], *, publish: Callable[[str, Path], None] = os.link,
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
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        opened = os.fstat(descriptor)
        entry = os.lstat(path)
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

    plan: dict[str, Any]
    path: Path
    observation: dict[str, Any]
    existed: bool

    def publish(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise ValueError("Turn decision capture must not be a symlink")
        if self.path.exists():
            # Python considers True == 1; original JSON evidence must retain
            # its value types as well as its content. Object key order is IO.
            text = read_turn_observation_text(self.path)
            # Compact objects start with a quoted key (or are empty). Pretty
            # captures can go straight to their order-independent comparison.
            compact = text.startswith('{"') or text == "{}\n"
            if not compact or text != _decision_json(self.observation):
                current = json.loads(text)
                if (json.dumps(current, sort_keys=True, allow_nan=False)
                        != json.dumps(self.observation, sort_keys=True, allow_nan=False)):
                    raise ValueError("Turn decision capture changed before publication")
        elif self.existed:
            raise ValueError("Original Turn decision capture is missing; restore the original observation")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_decision_capture(self.path, self.observation)
        return self.plan

    def publish_for_host(
        self, request: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Install the published original input without changing recovery bindings."""
        plan = self.publish()
        from ..quota.turn_envelope import COMPLETE_TURN_PLAN_SCHEMA_VERSION, turn_plan_host_source

        if plan.get("schema_version") == COMPLETE_TURN_PLAN_SCHEMA_VERSION:
            bound_request = {**request, **turn_plan_host_source(plan)}
            bound_request.pop("turn_envelope", None)
            return plan, bound_request
        bound_request = {**request, "turn_envelope": plan["turn_envelope"]}
        if "work_contract" in plan:
            contract = plan["work_contract"]
            bound_request["work_contract"] = dict(contract) if isinstance(contract, Mapping) else contract
        else:
            # Only an original historical absence can clear the current input.
            bound_request.pop("work_contract", None)
        from ..quota.turn_envelope import COMPLETE_TURN_HOST_REQUEST_SCHEMA, captured_turn_host_input

        if request.get("schema_version") == COMPLETE_TURN_HOST_REQUEST_SCHEMA:
            if "work_contract" in plan:
                bound_request = captured_turn_host_input(bound_request)
            else:
                # The immutable original observation, not a bad current source,
                # owns restoration of a historical request version.
                from ..quota.turn_envelope import LOOPX_TURN_HOST_REQUEST_SCHEMA

                bound_request["schema_version"] = LOOPX_TURN_HOST_REQUEST_SCHEMA
                bound_request.pop("source_decision_hash", None)
                bound_request.pop("detail_ref", None)
        return plan, bound_request


def stage_turn_decision_capture(
    plan: Mapping[str, Any], *, journal_path: Path,
    decision: Mapping[str, Any] | None, existing_journal: bool,
) -> StagedTurnDecisionCapture:
    """Validate under the journal lock before admission, without publishing IO.

    Existing journals must retain their original reference. A missing legacy
    observation cannot be reconstructed from today's guard. Already committed
    journals remain readable through ordinary no-effect replay.
    """
    from ..quota.turn_envelope import COMPLETE_TURN_PLAN_SCHEMA_VERSION, turn_plan_host_source

    current = plan.get("schema_version") == COMPLETE_TURN_PLAN_SCHEMA_VERSION
    envelope = turn_plan_host_source(plan) if current else plan["turn_envelope"]
    transaction = plan["transaction"]
    path = journal_path.parent / "decisions" / journal_path.name
    capture = envelope.get("detail_ref", {}).get("captured_decision")
    if existing_journal and not capture:
        raise ValueError("This Turn journal has no original decision capture; preserve it and "
                         "recover its pending effects before starting a fresh Turn")
    if path.is_symlink():
        raise ValueError("Turn decision capture must not be a symlink")
    existed = path.exists()
    if existed:
        observation = json.loads(read_turn_observation_text(path))
    elif existing_journal or capture:
        raise ValueError("Original Turn decision capture is missing; restore the original "
                         "observation, do not rerun the guard to replace it")
    else:
        if decision is None:
            raise ValueError("New Turn requires its full original decision")
        observation = dict(decision)
    from ...command_invocation import current_loopx_cli_argv

    bound = bind_turn_decision_capture(
        observation, None if current else envelope, captured_decision_path=str(path),
        turn_instance_id=str(transaction.get("turn_instance_id") or transaction["turn_key"]),
        turn_key=transaction["turn_key"],
        # New journals have a real immutable source and executable reader.
        # Legacy journals retain their recorded delivery/recovery route.
        reader_argv=current_loopx_cli_argv() if not existing_journal else None,
        complete_host_request={key: value for key, value in envelope.items() if key != "work_contract"}
            if current else None,
    )
    restored = ({**plan, **{key: bound[key] for key in ("source_decision_hash", "detail_ref")}}
                if current else {**plan, "turn_envelope": bound})
    if observation.get("schema_version") == "loopx_turn_envelope_v1":
        restored["work_contract"] = observation
    else:
        # Historical observations retain their original input contract. Never
        # pair an old capture with a freshly observed complete decision.
        restored.pop("work_contract", None)
    return StagedTurnDecisionCapture(restored, path, observation, existed)


def prepare_turn_decision_capture(
    plan: Mapping[str, Any], *, journal_path: Path,
    decision: Mapping[str, Any] | None, existing_journal: bool,
) -> dict[str, Any]:
    """Retain the existing immediate publication API for original-capture callers."""
    return stage_turn_decision_capture(plan, journal_path=journal_path,
        decision=decision, existing_journal=existing_journal).publish()


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
        with os.fdopen(open_regular_capture(_handoff_capture_root_file(endpoint)), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise ValueError("capture root binding must be private to the Host user")
            bound_root = stream.read(_HANDOFF_CAPTURE_ROOT_LIMIT + 1)
            if not bound_root or len(bound_root) > _HANDOFF_CAPTURE_ROOT_LIMIT:
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
            "Use the --decision-output-root named by the current wake."
        )


def validate_handoff_socket(value: str, *, capture_root: Path | None = None) -> Path:
    """A caller-owned local transport, not an authority or admission endpoint."""
    path = Path(value)
    try:
        if (os.name != "posix" or not hasattr(socket, "AF_UNIX") or not path.is_absolute() or
                path.resolve(strict=True) != path or
                not stat.S_ISSOCK(path.stat().st_mode) or
                path.parent.stat().st_mode & 0o077 or
                path.stat().st_uid != os.getuid()):
            raise OSError("expected a same-user socket in a private physical directory")
    except FileNotFoundError as exc:
        # A resumed session can remember an earlier wake's private endpoint.
        # Reject before any guard runs and name the current-wake remedy.
        raise QuotaCommandValidationError(
            "invalid decision handoff socket: the endpoint no longer exists; it belonged "
            "to a Host wake that has ended. No guard was run. Rerun with the "
            "--decision-output-root and --decision-handoff-socket named by the current "
            "wake, or without both when the current wake names none"
        ) from exc
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


def context_capture_read_command(path: Path, digest: str, *, offset: int = 0,
                                 page_bytes: int | None = None,
                                 max_page_output_bytes: int | None = None,
                                 select: list[str] | None = None,
                                 selection_sha256: str | None = None,
                                 list_fields: bool = False,
                                 list_depth: int | None = None,
                                 initial_read: bool = False,
                                 reference_file: Path | None = None,
                                 reader_argv: list[str] | None = None,
                                 compact: bool = False) -> str:
    from ...command_invocation import loopx_cli_argv
    from ...install_contract import render_host_cli_command

    if page_bytes is None and not compact:
        page_bytes = NATIVE_CONTEXT_PAGE_BYTES
        if max_page_output_bytes is None:
            max_page_output_bytes = NATIVE_CONTEXT_PAGE_OUTPUT_BYTES
    route = reader_argv if reader_argv is not None else loopx_cli_argv()
    return render_host_cli_command([*route, "--format", "json", "quota", "context-read",
        *(["--reference-file", str(reference_file)] if reference_file is not None
          else ["--capture-file", str(path), "--capture-sha256", digest]),
        *(["--offset", str(offset)] if offset or not compact else []),
        *(["--page-bytes", str(page_bytes)] if page_bytes is not None else []),
        *(["--initial-read"] if initial_read else []),
        *(["--list"] if list_fields else []),
        *(["--list-depth", str(list_depth)] if list_depth is not None else []),
        *(argument for pointer in select or [] for argument in ("--select", pointer)),
        *(["--selection-sha256", selection_sha256] if selection_sha256 is not None else []),
        *(["--max-page-output-bytes", str(max_page_output_bytes)] if max_page_output_bytes is not None else [])])


def write_context_read_reference(path: Path, digest: str, *, selection: dict[str, Any] | None = None,
                                 navigation: dict[str, Any] | None = None, observed_read: dict[str, Any] | None = None,
                                 selection_paths: list[list[str | int]] | None = None,
                                 reference_file: Path | None = None,
                                 reader_argv: list[str] | None = None) -> Path:
    """Immutable reader IO metadata, not a new identity or authority store.

    A short filename locates the document; only its complete stored digests are
    checked. A collision or replaced document fails closed, never overwrites.
    """
    payload: dict[str, Any] = {"schema_version": "context_capture_read_reference_v1", "path": str(path), "sha256": digest}
    if reader_argv is not None:
        payload["reader_argv"] = reader_argv
    if selection is not None:
        payload["selection"] = selection
    if navigation is not None:
        payload["navigation"] = navigation
    if observed_read is not None:
        payload["observed_read"] = observed_read
    if selection is not None and selection_paths is not None:
        from ..effect_runtime import MAX_REQUEST_BYTES

        # A large descriptor can still use the older selector replay route.
        with_paths = {**payload, "selection_paths": selection_paths}
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
        if existing["path"] != payload["path"]:
            # Readers resolve parent aliases, while retaining the exact leaf.
            # Compare that same spelling without rewriting immutable metadata
            # or treating a different leaf (including a hardlink) as identical.
            original = Path(existing["path"]).expanduser().absolute()
            current = path.expanduser().absolute()
            if original.parent.resolve(strict=True) / original.name == current.parent.resolve(strict=True) / current.name:
                existing["path"] = payload["path"]
        if json.dumps(existing, sort_keys=True) != json.dumps(payload, sort_keys=True):
            raise ValueError("original reader reference changed; restore it without rerunning admission") from None
    return reference


def read_context_read_reference(value: str) -> tuple[Path, dict[str, Any]]:
    """Read one private physical descriptor; the caller verifies capture bytes."""
    from ..effect_runtime import MAX_REQUEST_BYTES

    uid = private_capture_uid()
    path = Path(value).expanduser().absolute()
    path = path.parent.resolve(strict=True) / path.name
    if path.is_symlink():
        raise ValueError("reader reference must be a physical file, not a symlink")
    descriptor = open_regular_capture(path)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (info.st_uid != uid or info.st_mode & 0o077
                or info.st_size > MAX_REQUEST_BYTES):
            raise ValueError("reader reference must be a private regular file owned by this user")
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("reader reference exceeds the existing transport limit")
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or payload.get("schema_version") != "context_capture_read_reference_v1"
            or not isinstance(payload.get("path"), str) or not isinstance(payload.get("sha256"), str)
            or BARE_SHA256_PATTERN.fullmatch(payload["sha256"]) is None
            or ("selection" in payload and not isinstance(payload["selection"], dict))
            or ("selection_paths" in payload and (
                "selection" not in payload or not isinstance(payload["selection_paths"], list)))):
        raise ValueError("reader reference is incomplete or unsupported")
    if "reader_argv" in payload:
        from ...command_invocation import validate_context_reader_route

        # A compact reference may inherit one exact CLI override. Arbitrary
        # executable strings are not interpreted by the reader.
        validate_context_reader_route(payload["reader_argv"])
    return path, payload


def read_context_capture_file(value: str, digest: str, *, private: bool = False) -> tuple[Path, bytes, dict[str, Any]]:
    """One bounded physical-file/hash check for readers and native observations."""
    from ..effect_runtime import MAX_REQUEST_BYTES

    if not isinstance(digest, str) or BARE_SHA256_PATTERN.fullmatch(digest) is None or not value:
        raise ValueError("context-read requires the original capture file and its exact SHA-256")
    uid = private_capture_uid() if private else None
    path = Path(value).expanduser().absolute()
    # Parent aliases such as macOS /tmp are normal capture locations.
    # Resolve those once, but never follow a substituted final file.
    path = path.parent.resolve(strict=True) / path.name
    if path.is_symlink():
        raise ValueError("context-read requires the physical original capture path, not a symlink")
    descriptor = open_regular_capture(path)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if metadata.st_size > MAX_REQUEST_BYTES:
            raise ValueError("capture must be a regular JSON file within the existing transport size limit")
        if private and (metadata.st_uid != uid or metadata.st_mode & 0o077):
            raise ValueError("native context requires a private capture owned by this user")
        raw = stream.read(metadata.st_size + 1)
    if len(raw) != metadata.st_size or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("capture digest mismatch; restore the original capture, do not rerun admission")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("capture must contain a complete JSON object")
    return path, raw, payload


def capture_page_range(page: dict[str, Any], *, raw: bytes, digest: str) -> tuple[int, int] | None:
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
            or page.get("next_offset") != (None if end == len(raw) else end)
            or raw[start:end] != data):
        # Valid page JSON and a correct cursor can still carry the wrong body.
        return None
    return start, end


def capture_context_projection(payload: dict[str, Any], *, directory: Path,
                               read_guidance: str = CONTEXT_CAPTURE_READ_GUIDANCE,
                               filename: str = "projection.json") -> dict[str, Any]:
    """Save the full projection independently of the optional presentation codec."""
    from ...command_invocation import current_loopx_cli_argv

    if filename not in ("projection.json", "required-context.json"):
        raise ValueError("unsupported captured context filename")
    projection = directory / filename
    digest = write_decision_capture(projection, payload)
    return {
        "path": str(projection), "sha256": digest,
        "instruction": "Complete JSON for this captured view. Use it when required content is missing or uncertain; "
            "compaction alone does not require replay within an existing work Turn. "
            + read_guidance,
        "command": context_capture_read_command(projection, digest, reader_argv=current_loopx_cli_argv()),
    }


def capture_progressive_context(payload: dict[str, Any], *, directory: Path,
                                surface: str = "quota_packet",
                                selection_command_args_template: str | None = None,
                                captured_decision_path: str | None = None,
                                replan_reader_argv: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Publish the complete current source before optional stateless presentation.

    The typed owner applies the shrink guard. This neither creates a native
    journal nor requires/adopts an execution Turn. All recovery is same-capture.
    A caller may supply the exact decision it just verified for staged reads.
    """
    from ..quota.turn_envelope import prepare_progressive_context

    source = capture_context_projection(payload, directory=directory)
    transaction = prepare_progressive_context(payload, surface=surface, source_reference={
        **{key: source[key] for key in ("path", "sha256")},
        **({"selection_command_args_template": selection_command_args_template}
            if selection_command_args_template is not None else {}),
        **({"captured_decision_path": captured_decision_path} if captured_decision_path is not None else {}),
        **({"replan_reader_argv": replan_reader_argv} if replan_reader_argv is not None else {}),
        "source_path": source["path"],
        "command_template": source["command"] + " --select '<pointer>'",
    })
    if not transaction["projected"]:
        return payload, source
    required = transaction["context"]
    return required, capture_context_projection(required, directory=directory, filename="required-context.json")


def project_captured_context(
    payload: dict[str, Any], *, directory: Path | None, request: dict[str, Any] | None,
    goal_id: str, agent_id: str, surface: str, capture_id: str,
    captured_recovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt an admitted capture to the shared TS presentation owner.

    The caller-owned rendezvous may supply a continuity observation. It must
    either confirm intact final input or explicitly select native lifecycle
    assumptions. Unsupported ordinary CLI clients never supply this request. Every
    opt-in attempt saves the complete projected JSON for same-capture recovery;
    neither malformed requests nor optimization failures rerun the guard.
    A native caller may pass its already-published recovery reference, never a
    reference supplied in the external request.
    """
    if request is None or directory is None:
        return payload
    from ..effect_runtime import EffectRuntimeRemoteError, EffectRuntimeStartupError
    from ..quota.turn_envelope import prepare_context_delivery

    try:
        recovery = captured_recovery if captured_recovery is not None else capture_context_projection(
            payload, directory=directory)
        scope = request.get("scope")
        if not isinstance(scope, dict) or scope.get("goal_id") != goal_id or scope.get("agent_id") != agent_id:
            return payload
        retained = request.get("retained")
        continuity = request.get("continuity")
        if "native_input" in request:
            from .codex_input_trace import codex_context_in_active_input

            # An optional native observation can only narrow the caller's
            # retention claim. It never mints an ACK or enables tracing, and a
            # malformed/stale observation must not fall back to that claim.
            observation, baseline = request["native_input"], request.get("baseline")
            native_session = scope.get("session_id")
            if (not isinstance(observation, dict) or not isinstance(baseline, dict)
                    or not isinstance(native_session, str)
                    or not codex_context_in_active_input(observation, baseline=baseline,
                        session_id=native_session)):
                retained = None
                continuity = None
        delivery = prepare_context_delivery(payload, scope=scope,
            capture_id=capture_id, surface=surface,
            baseline=request.get("baseline"), retained=retained, continuity=continuity)
        delivery["recovery"] = recovery
        return delivery
    except (OSError, ValueError, TypeError, EffectRuntimeRemoteError, EffectRuntimeStartupError):
        # The original full payload is already available. An unavailable optional
        # codec/capture must not convert safe full delivery into an execution gate.
        return payload


def context_index_fields(payload: dict[str, Any], children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize metadata for TS-verified addresses, preserving Python JSON values."""
    fields = []
    for child in children:
        value: Any = payload
        for key in child["path"]:
            value = value[key]
        field = {"pointer": child["pointer"], "type":
            "object" if isinstance(value, dict) else "array" if isinstance(value, list)
            else "null" if value is None else "boolean" if isinstance(value, bool)
            else "string" if isinstance(value, str) else "number",
            "bytes": len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))}
        if isinstance(value, (dict, list)):
            field["count"] = len(value)
        fields.append(field)
    return fields


def _pointer_token(key: str | int) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def context_selection_transport(payload: dict[str, Any], select: list[str] | None, *,
                                list_fields: bool, list_depth: int | None) -> dict[str, Any]:
    """Supply the address shape for the TS selector. Values are read from the verified original."""
    # Root discovery also derives initial navigation from real decision values.
    if not select or list_fields and "" in select:
        return payload
    # Leave malformed requests to the typed owner, including error ordering.
    if not all(isinstance(pointer, str) for pointer in select):
        return payload

    paths: dict[str | None, Any] = {}
    for pointer in select:
        branch = paths
        for token in pointer.split("/")[1:]:
            branch = branch.setdefault(token, {})
        # None cannot collide with a JSON object key (including the empty key).
        branch[None] = True

    result: dict[str, Any] = {}
    pending: list[tuple[Any, Any, dict[str | None, Any], int]] = [(payload, result, paths, 0)]
    while pending:
        source, target, branch, depth = pending.pop()
        if list_fields and None in branch:
            depth = max(depth, list_depth or 1)
        if depth <= 0 and not any(key is not None for key in branch):
            continue
        for key, value in source.items() if isinstance(source, dict) else enumerate(source):
            token = _pointer_token(key)
            if depth <= 0 and token not in branch:
                continue
            if isinstance(value, (dict, list)):
                subtree = branch.get(token, {})
                # Length matters when validating array indices or listing children.
                child: Any = {} if isinstance(value, dict) else [0] * len(value)
                target[key] = child
                pending.append((value, child, subtree, depth - 1))
            else:
                # Every scalar has the same address behavior. A one-byte marker
                # also prevents short scalar arrays growing during transport.
                # Original types, precision and byte counts stay at the renderer.
                target[key] = 0
    return result


_GOAL_SOURCE_PREFIX = ["interaction_contract", "agent_channel", "work_context", "sources"]
_GOAL_NARRATIVE_PRESENTATION = (
    "The generated frontmatter objective appears once under ## Objective. "
    "Use --select on the same capture at this pointer for verbatim source text."
)


def _first_read_goal_text(payload: dict[str, Any], path: list[Any], value: Any) -> str | None:
    # Only render the typed selector's registered Goal text leaf. The source,
    # enclosing containers, exact detail reads and unknown shapes stay literal.
    if (not isinstance(value, str) or path[:4] != _GOAL_SOURCE_PREFIX
            or len(path) != 7 or type(path[4]) is not int or path[5:] != ["content", "text"]):
        return None
    interaction = payload["interaction_contract"]
    source = interaction["agent_channel"]["work_context"]["sources"][path[4]]
    content = source["content"]
    if (interaction.get("schema_version") != "loopx_interaction_contract_v0"
            or source.get("source") != "goal_state" or content.get("ok") is False
            or content.get("read_complete") is False or not content.get("state_file")
            or not (content.get("full_source") is True or content.get("excluded_todo_regions"))):
        return None
    from ..goals.active_state_metadata import goal_narrative_without_objective_mirror

    text = goal_narrative_without_objective_mirror(value)
    # Allow room for the presentation notice and immutable selection metadata.
    return text if text is not None and len(value) - len(text) > len(_GOAL_NARRATIVE_PRESENTATION) + 96 else None


def _context_selection_bytes(payload: dict[str, Any], selections: list[dict[str, Any]],
                             *, list_fields: bool, list_depth: int | None,
                             present_goal_narrative: bool,
                             text_append: dict[str, Any] | None = None) -> tuple[bytes, list[str]]:
    """Render TS-verified addresses from the original, digest-checked values."""
    if text_append is not None and (list_fields or not isinstance(text_append, dict)):
        raise ValueError("observed text append requires a selected value")
    views: list[dict[str, Any]] = []
    presented: list[str] = []
    append_rendered = False
    for item in selections:
        selected: Any = payload
        path, pointer = item["path"], item["pointer"]
        for key in path:
            if type(key) is str and isinstance(selected, dict) and key in selected:
                selected = selected[key]
            elif type(key) is int and isinstance(selected, list) and 0 <= key < len(selected):
                selected = selected[key]
            else:
                raise ValueError("selected address does not match the original capture")
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
            views.append({"pointer": pointer, "fields": context_index_fields(payload, item["children"])})
        else:
            text = _first_read_goal_text(payload, path, selected) if present_goal_narrative else None
            if text is None:
                views.append({"pointer": pointer, "value": selected})
            else:
                presented.append(pointer)
                views.append({"pointer": pointer, "value": text, "presentation": _GOAL_NARRATIVE_PRESENTATION})
    if text_append is not None and not append_rendered:
        raise ValueError("observed text append does not match a selected value")
    view_options = {"list_depth": list_depth} if list_depth is not None and list_depth != 1 else {}
    raw = json.dumps({"schema_version": "context_capture_index_v1" if list_fields else "context_capture_selection_v1",
        "selections": views, **view_options}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    return raw, presented


def context_selection_binding(select: list[str], *, list_fields: bool, list_depth: int | None,
                              sha256: str, presented: bool = False) -> dict[str, Any]:
    return {"select": select, "initial_read": False, "list_fields": list_fields, "list_depth": list_depth,
        "sha256": sha256, **({"present_goal_narrative": True} if presented else {})}


def initial_context_selection_bytes(payload: dict[str, Any], selections: list[dict[str, Any]], *,
                                    text_append: dict[str, Any] | None = None) -> tuple[bytes, list[str]]:
    return _context_selection_bytes(payload, selections, list_fields=False, list_depth=None,
        present_goal_narrative=True, text_append=text_append)


def write_initial_context_read_reference(path: Path, digest: str, payload: dict[str, Any],
                                        selections: list[dict[str, Any]], *,
                                        observed_read: dict[str, Any] | None = None) -> tuple[Path, bytes]:
    """Pin the shared rendered selection and return its exact rendered bytes."""
    text_append = observed_read.get("text_append") if observed_read else None
    raw, presented = initial_context_selection_bytes(payload, selections, text_append=text_append)
    reference = write_context_read_reference(path, digest,
        selection=context_selection_binding([item["pointer"] for item in selections], list_fields=False,
            list_depth=None, sha256=hashlib.sha256(raw).hexdigest(), presented=bool(presented)),
        selection_paths=[item["path"] for item in selections],
        observed_read=observed_read)
    return reference, raw


def _pinned_selection_plan(select: list[str] | None, paths: list[list[str | int]]) -> dict[str, Any]:
    """Replay TypeScript's resolved addresses from the existing reader reference."""
    if (not isinstance(select, list) or not select or len(select) != len(paths)
            or not all(isinstance(pointer, str) for pointer in select) or len(set(select)) != len(select)):
        raise ValueError("reader selection paths do not match its pinned addresses")
    selections = []
    for pointer, path in zip(select, paths, strict=True):
        if (not isinstance(path, list)
                or not all(type(key) in (str, int) for key in path)
                or pointer != "".join("/" + _pointer_token(key) for key in path)):
            raise ValueError("reader selection paths do not match its pinned addresses")
        selections.append({"pointer": pointer, "path": path})
    return {"selections": selections}


class ContextSelectionDigestChanged(ValueError):
    """The rendered selection no longer matches its recorded digest."""


def context_selection_view(payload: dict[str, Any], *, select: list[str] | None,
                           initial_read: bool, list_fields: bool, list_depth: int | None,
                           expected_sha256: str | None, preserve_selection_order: bool = False,
                           present_goal_narrative: bool | None = None,
                           selection_paths: list[list[str | int]] | None = None,
                           text_append: dict[str, Any] | None = None,
                           ) -> tuple[dict[str, Any], bytes | None, str | None]:
    """Render the digest-pinned view from a new or previously resolved plan."""
    if text_append is not None and (initial_read or list_fields or expected_sha256 is None):
        raise ValueError("observed text append requires a pinned value selection")
    if selection_paths is not None:
        if initial_read or list_fields or list_depth is not None or not preserve_selection_order or expected_sha256 is None:
            raise ValueError("reader selection paths require a pinned value selection")
        plan = _pinned_selection_plan(select, selection_paths)
    else:
        from ..quota.turn_envelope import _turn_envelope_result

        context = payload if initial_read else context_selection_transport(
            payload, select, list_fields=list_fields, list_depth=list_depth)
        selection = {"initial_read": True} if initial_read else {"select": select}
        depth = {"list_depth": list_depth} if list_depth is not None else {}
        order = {"preserve_selection_order": True} if preserve_selection_order else {}
        plan = _turn_envelope_result("context_select", context=context, list_fields=list_fields,
            recover_selection_errors=not initial_read, **selection, **depth, **order)
    if plan.get("rejected_selection"):
        return plan, None, None
    presentation = initial_read if present_goal_narrative is None else present_goal_narrative

    def render(plan: dict[str, Any], present: bool) -> tuple[bytes, str, list[str]]:
        raw, displayed = _context_selection_bytes(payload, plan["selections"], list_fields=list_fields,
            list_depth=list_depth, present_goal_narrative=present, text_append=text_append)
        return raw, hashlib.sha256(raw).hexdigest(), displayed

    raw, digest, displayed = render(plan, presentation)
    if selection_paths is None and preserve_selection_order and expected_sha256 != digest:
        # Historical explicit references may pin canonical output despite
        # retaining caller order. Only the same source and addresses can replay.
        plan = _turn_envelope_result("context_select", context=context,
            list_fields=list_fields, **selection, **depth)
        raw, digest, displayed = render(plan, presentation)
    if (expected_sha256 is not None and expected_sha256 != digest
            and initial_read and present_goal_narrative is None and displayed):
        # An unpinned initial cursor may have started before presentation was
        # enabled. Its original digest must still match the literal view.
        raw, digest, displayed = render(plan, False)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ContextSelectionDigestChanged("selected view digest changed; restore the original selection and reader")
    return {**plan, "presentation_pointers": displayed} if displayed else plan, raw, digest
