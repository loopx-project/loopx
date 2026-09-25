"""Lossless fragmentation for interface-budgeted project-agent handoffs.

A handoff text normally fits the ``project_agent_handoff`` interface budget
(16 lines / 1800 characters by default). When the prepared text still exceeds
the budget, this module splits it into ordered, independently verifiable
shards instead of dropping sections:

* complete text fields remain complete; transport arrays carry every shard;
* every shard starts with a single envelope line carrying a stable content
  set id, its sequence index/total, a per-shard payload checksum, a previous
  shard hash chain, and the full-content digest;
* reassembly validates every shard payload, the sequence/hash chain, set
  consistency and the full-content digest, failing explicitly on missing
  shards, out-of-order delivery, duplicate/conflicting imports, or tampered
  content;
* fenced code blocks are never torn open across shards (an unfinished fence
  is closed and re-opened with strip-only transport markers), and over-long
  single lines are wrapped with a continuation marker and rejoined exactly.

When the input fits the budget, :func:`split_handoff_text` returns it verbatim
as the only element, with no envelope, so the common path stays byte-identical
to the legacy single-text handoff.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ...handoff_budget import handoff_budget_contract


SHARD_FORMAT_VERSION = "1"

ENVELOPE_PREFIX = "<!--loopx-handoff "
ENVELOPE_RE = re.compile(
    r"^<!--loopx-handoff "
    r"v=(?P<v>\d+) "
    r"id=(?P<id>[0-9a-f]{16}) "
    r"i=(?P<i>\d+) "
    r"n=(?P<n>\d+) "
    r"c=(?P<c>[0-9a-f]{16}) "
    r"p=(?P<p>-|[0-9a-f]{16}) "
    r"d=(?P<d>[0-9a-f]{64})-->$"
)
SHARD_TITLE_PREFIXES = ("【交接分片 ", "【给项目 Agent · 交接分片 ")

# Transport-only markers. They never occur in prepared handoff content; the
# splitter rejects input that already contains them (fail closed).
LINE_CONTINUATION_MARKER = "@@loopx-handoff:cont@@"
FENCE_OPEN_MARKER = "# loopx-handoff:fence-open"
FENCE_RESUME_MARKER = "# loopx-handoff:fence-resume"

# The envelope is one physical line of bounded length; reserving a fixed
# header budget keeps payload packing independent of index/total digit width.
ENVELOPE_CHAR_RESERVE = 200

MANIFEST_SCHEMA_VERSION = "project_agent_handoff_shard_v1"


class HandoffShardError(ValueError):
    """A handoff shard failed envelope, integrity, order, or set validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HandoffShard:
    set_id: str
    index: int
    total: int
    payload: str
    chunk_hash: str
    prev_hash: str | None
    digest_hex: str


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _budget_limits(max_lines: int | None, max_chars: int | None) -> tuple[int, int]:
    contract = handoff_budget_contract()
    line_limit = int(max_lines if max_lines is not None else contract["max_lines"])
    char_limit = int(max_chars if max_chars is not None else contract["max_chars"])
    if line_limit < 2 or char_limit <= ENVELOPE_CHAR_RESERVE + 32:
        raise HandoffShardError(
            "budget",
            f"handoff shard budget too small: max_lines={line_limit}, max_chars={char_limit}",
        )
    return line_limit, char_limit


def _envelope_line(
    *,
    set_id: str,
    index: int,
    total: int,
    chunk_hash: str,
    prev_hash: str | None,
    digest_hex: str,
) -> str:
    prev_field = prev_hash or "-"
    return (
        f"{ENVELOPE_PREFIX}v={SHARD_FORMAT_VERSION} id={set_id} "
        f"i={index} n={total} c={chunk_hash} p={prev_field} d={digest_hex}-->"
    )


def _assert_no_transport_markers(lines: list[str]) -> None:
    for line in lines:
        if (
            line.startswith(ENVELOPE_PREFIX)
            or line.startswith(LINE_CONTINUATION_MARKER)
            or line.lstrip().startswith(SHARD_TITLE_PREFIXES)
        ):
            raise HandoffShardError(
                "reserved_marker",
                "handoff content contains a reserved loopx-handoff transport marker",
            )
        if line in (FENCE_OPEN_MARKER, FENCE_RESUME_MARKER):
            raise HandoffShardError(
                "reserved_marker",
                "handoff content contains a reserved loopx-handoff fence marker",
            )


@dataclass
class _FenceUnit:
    opener: str
    content: list[str]


def _parse_units(lines: list[str]) -> list[_FenceUnit | str]:
    units: list[_FenceUnit | str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            closer = next(
                (candidate for candidate in lines[index + 1 :] if candidate == "```"),
                None,
            )
            if closer is None:
                raise HandoffShardError(
                    "structure",
                    "cannot fragment handoff with an unterminated fenced code block",
                )
            closer_index = lines.index("```", index + 1)
            units.append(
                _FenceUnit(opener=line, content=lines[index + 1 : closer_index])
            )
            index = closer_index + 1
        else:
            units.append(line)
            index += 1
    return units


class _ShardPacker:
    def __init__(self, *, max_lines: int, max_chars: int) -> None:
        self.payload_max_lines = max_lines - 1
        self.payload_max_chars = max_chars - ENVELOPE_CHAR_RESERVE
        self.shards: list[list[str]] = []
        self.current: list[str] = []
        self.fence_active = False
        self.fence_opener = ""

    def _payload_text(self) -> str:
        return "\n".join(self.current)

    def _reserved_lines(self) -> int:
        return 2 if self.fence_active else 0

    def _reserved_chars(self) -> int:
        # Flushing an unfinished fence appends FENCE_OPEN_MARKER + closer.
        if not self.fence_active:
            return 0
        return len(FENCE_OPEN_MARKER) + 1 + len("```")

    def _fits(self, physical_line: str) -> bool:
        if len(self.current) + 1 + self._reserved_lines() > self.payload_max_lines:
            return False
        added = len(physical_line) + (1 if self.current else 0)
        return (
            len(self._payload_text()) + added + self._reserved_chars()
            <= self.payload_max_chars
        )

    def _flush(self) -> None:
        if not self.current:
            return
        if self.fence_active:
            self.current.append(FENCE_OPEN_MARKER)
            self.current.append("```")
        self.shards.append(self.current)
        self.current = []

    def _begin_resume(self) -> None:
        self.current.append(self.fence_opener)
        self.current.append(FENCE_RESUME_MARKER)

    def place(self, physical_line: str, *, in_fence: bool) -> None:
        if self._fits(physical_line):
            self.current.append(physical_line)
            return
        self._flush()
        if in_fence and self.fence_active:
            self._begin_resume()
        if not self._fits(physical_line):
            raise HandoffShardError(
                "structure",
                "handoff line cannot fit into a single shard payload budget",
            )
        self.current.append(physical_line)

    def _prepare_width(self, *, continuation: bool) -> int:
        """Flush when necessary and return the max content width for one line."""

        prefix_len = len(LINE_CONTINUATION_MARKER) if continuation else 0

        def room_now() -> int:
            used = len(self._payload_text()) + (1 if self.current else 0)
            return self.payload_max_chars - used - self._reserved_chars() - prefix_len

        def slot_now() -> bool:
            return (
                len(self.current) + 1 + self._reserved_lines() <= self.payload_max_lines
            )

        if not slot_now() or room_now() <= 0:
            self._flush()
            if self.fence_active:
                self._begin_resume()
        width = room_now()
        if width <= 0:
            raise HandoffShardError(
                "structure",
                "handoff line cannot fit into a single shard payload budget",
            )
        return width

    def emit_line(self, line: str) -> None:
        if line == "":
            self.place("", in_fence=self.fence_active)
            return
        rest = line
        continuation = False
        while rest:
            width = self._prepare_width(continuation=continuation)
            cut = min(width, len(rest))
            if cut < len(rest):
                window = rest[:cut]
                break_at = max(window.rfind(" "), window.rfind("\t"))
                if break_at >= cut // 2:
                    cut = break_at + 1
            chunk = rest[:cut]
            rest = rest[cut:]
            physical = (LINE_CONTINUATION_MARKER + chunk) if continuation else chunk
            self.place(physical, in_fence=self.fence_active)
            continuation = True

    def emit_fence(self, fence: _FenceUnit) -> None:
        # The opener must land in a shard that still has room for the fence
        # close framing (open marker + closer) if the fence spills later.
        used_chars = len(self._payload_text()) + (1 if self.current else 0)
        framing_chars = len(FENCE_OPEN_MARKER) + 1 + len("```")
        needs_lines = len(self.current) + 1 + 2 <= self.payload_max_lines
        needs_chars = (
            used_chars + len(fence.opener) + framing_chars <= self.payload_max_chars
        )
        if self.current and not (needs_lines and needs_chars):
            self._flush()
        self.current.append(fence.opener)
        self.fence_active = True
        self.fence_opener = fence.opener
        for content_line in fence.content:
            self.emit_line(content_line)
        self.place("```", in_fence=True)
        self.fence_active = False

    def finish(self) -> list[str]:
        if self.fence_active:
            raise HandoffShardError(
                "structure", "unterminated fence while finishing shards"
            )
        self._flush()
        return ["\n".join(lines) for lines in self.shards]


def split_handoff_text(
    text: str,
    *,
    max_lines: int | None = None,
    max_chars: int | None = None,
) -> list[str]:
    """Fragment an over-budget handoff text into verifiable ordered shards.

    Returns ``[text]`` unchanged (no envelope) when the text already fits the
    budget. Otherwise returns two or more shard texts; each shard fits the
    same interface budget and shard 0 is the prefix of the original content.
    """

    if not isinstance(text, str):
        raise HandoffShardError("input", "handoff text must be a string")
    if text.endswith("\n"):
        raise HandoffShardError("input", "handoff text must not end with a newline")
    line_limit, char_limit = _budget_limits(max_lines, max_chars)
    lines = text.split("\n")
    if len(lines) <= line_limit and len(text) <= char_limit:
        return [text]

    _assert_no_transport_markers(lines)
    units = _parse_units(lines)
    max_opener_len = max(
        (len(unit.opener) for unit in units if isinstance(unit, _FenceUnit)),
        default=0,
    )
    if max_opener_len > ENVELOPE_CHAR_RESERVE:
        raise HandoffShardError(
            "structure", "fence opener cannot fit shard framing budget"
        )

    packer = _ShardPacker(max_lines=line_limit, max_chars=char_limit)
    for unit in units:
        if isinstance(unit, _FenceUnit):
            packer.emit_fence(unit)
        else:
            packer.emit_line(unit)
    payloads = packer.finish()
    if len(payloads) < 2:
        raise HandoffShardError(
            "structure", "overflow handoff produced no continuation shard"
        )

    digest_hex = _sha256_hex(text)
    set_id = digest_hex[:16]
    total = len(payloads)
    shards: list[str] = []
    prev_hash: str | None = None
    for index, payload in enumerate(payloads):
        chunk_hash = _sha256_hex(payload)[:16]
        envelope = _envelope_line(
            set_id=set_id,
            index=index,
            total=total,
            chunk_hash=chunk_hash,
            prev_hash=prev_hash,
            digest_hex=digest_hex,
        )
        shard = envelope + "\n" + payload
        if len(shard.split("\n")) > line_limit or len(shard) > char_limit:
            raise HandoffShardError(
                "structure",
                f"generated shard {index}/{total} exceeds the interface budget",
            )
        if len(envelope) >= ENVELOPE_CHAR_RESERVE:
            raise HandoffShardError(
                "structure", "shard envelope exceeds reserved header budget"
            )
        shards.append(shard)
        prev_hash = chunk_hash

    # Encoder self-check: the generated shards must reassemble byte-for-byte.
    restored = reassemble_handoff_shards(shards)
    if restored != text:
        raise HandoffShardError("structure", "fragment encoder round-trip mismatch")
    return shards


def parse_handoff_shard(text: str) -> HandoffShard:
    """Parse one shard text and verify its per-shard payload checksum."""

    if not isinstance(text, str) or not text:
        raise HandoffShardError("envelope", "handoff shard must be a non-empty string")
    if text.endswith("\n"):
        raise HandoffShardError("envelope", "handoff shard must not end with a newline")
    first_line, separator, payload = text.partition("\n")
    if not separator or not payload:
        raise HandoffShardError(
            "envelope", "handoff shard needs an envelope line and payload"
        )
    match = ENVELOPE_RE.match(first_line)
    if match is None:
        raise HandoffShardError("envelope", "malformed handoff shard envelope")
    if match.group("v") != SHARD_FORMAT_VERSION:
        raise HandoffShardError("envelope", "unsupported handoff shard format version")
    if _sha256_hex(payload)[:16] != match.group("c"):
        raise HandoffShardError(
            "integrity",
            f"handoff shard {match.group('i')} payload checksum mismatch",
        )
    digest_hex = match.group("d")
    set_id = match.group("id")
    if set_id != digest_hex[:16]:
        raise HandoffShardError(
            "envelope", "handoff shard set id does not bind content digest"
        )
    prev_field = match.group("p")
    return HandoffShard(
        set_id=set_id,
        index=int(match.group("i")),
        total=int(match.group("n")),
        payload=payload,
        chunk_hash=match.group("c"),
        prev_hash=None if prev_field == "-" else prev_field,
        digest_hex=digest_hex,
    )


def _decode_fence_groups(physical: list[str]) -> list[str]:
    """Collapse transport fence wrapping, then join wrapped physical lines."""

    logical: list[str] = []
    pending: list[str] | None = None
    index = 0
    while index < len(physical):
        line = physical[index]
        if line.startswith("```"):
            closer_index = next(
                (
                    candidate
                    for candidate in range(index + 1, len(physical))
                    if physical[candidate] == "```"
                ),
                None,
            )
            if closer_index is None:
                raise HandoffShardError(
                    "structure", "unterminated fence in shard payload"
                )
            content = physical[index + 1 : closer_index]
            if any(
                marker in content[1:-1]
                for marker in (FENCE_OPEN_MARKER, FENCE_RESUME_MARKER)
            ):
                raise HandoffShardError(
                    "structure", "fence transport marker at invalid position"
                )
            starts_resume = bool(content) and content[0] == FENCE_RESUME_MARKER
            ends_open = bool(content) and content[-1] == FENCE_OPEN_MARKER
            inner = content[1:] if starts_resume else list(content)
            inner = inner[:-1] if ends_open else inner
            if starts_resume:
                if pending is None:
                    raise HandoffShardError(
                        "structure",
                        "fence resume marker without a preceding fence part",
                    )
                pending.extend(inner)
            else:
                if pending is not None:
                    raise HandoffShardError(
                        "structure", "fence continuation marker was not resumed"
                    )
                pending = list(inner)
            if not ends_open:
                logical.append(line)
                logical.extend(pending)
                logical.append("```")
                pending = None
            index = closer_index + 1
            continue
        if line in (FENCE_OPEN_MARKER, FENCE_RESUME_MARKER):
            raise HandoffShardError(
                "structure", "fence transport marker outside fenced block"
            )
        if pending is not None:
            raise HandoffShardError(
                "structure", "non-fence line interleaved with a split fenced block"
            )
        logical.append(line)
        index += 1
    if pending is not None:
        raise HandoffShardError("structure", "fence open marker without a resume shard")

    joined: list[str] = []
    for line in logical:
        if line.startswith(LINE_CONTINUATION_MARKER):
            if not joined or joined[-1].startswith("```"):
                raise HandoffShardError(
                    "structure",
                    "line continuation marker without a preceding line part",
                )
            joined[-1] += line[len(LINE_CONTINUATION_MARKER) :]
        else:
            joined.append(line)
    return joined


def _decode_shards(shards: list[HandoffShard]) -> str:
    # The sole caller has validated set, cardinality and arrival order.
    set_id = shards[0].set_id
    ordered = shards

    previous_hash: str | None = None
    for shard in ordered:
        if shard.prev_hash != previous_hash:
            expected_label = previous_hash or "-"
            raise HandoffShardError(
                "out_of_order",
                f"handoff shard {shard.index} hash chain breaks "
                f"(expected p={expected_label}, got {shard.prev_hash or '-'})",
            )
        previous_hash = shard.chunk_hash

    physical = "\n".join(shard.payload for shard in ordered).split("\n")
    logical = _decode_fence_groups(physical)
    restored = "\n".join(logical)
    if _sha256_hex(restored) != shards[0].digest_hex:
        raise HandoffShardError(
            "digest",
            f"reassembled handoff fragment set {set_id} failed the full-content digest check",
        )
    return restored


def reassemble_handoff_shards(
    shard_texts: Iterable[str],
) -> str:
    """Parse, verify and concatenate handoff shards back to the original text.

    Shard texts must arrive in sequence
    order (0, 1, ..., n-1); out-of-order arrival raises
    :class:`HandoffShardError`. Missing shards, duplicate shards, foreign-set
    shards, checksum and hash-chain failures, and full-content digest
    mismatches always raise.
    """

    parsed = [parse_handoff_shard(text) for text in shard_texts]
    if not parsed:
        raise HandoffShardError("missing", "no handoff shards provided")
    if len({(s.set_id, s.total, s.digest_hex) for s in parsed}) != 1:
        raise HandoffShardError(
            "set_mismatch", "handoff input mixes different fragment sets"
        )
    arrival = [shard.index for shard in parsed]
    total = parsed[0].total
    if any(index < 0 or index >= total for index in arrival):
        raise HandoffShardError(
            "unexpected_index",
            f"handoff import has shard indices outside 0..{total - 1}: {arrival}",
        )
    if len(set(arrival)) != len(arrival):
        raise HandoffShardError(
            "duplicate",
            f"duplicate handoff shard index in import: {arrival}",
        )
    if len(parsed) > total:
        raise HandoffShardError(
            "unexpected_index",
            f"more handoff shards than declared total {total}: {arrival}",
        )
    if len(parsed) < total:
        present = set(arrival)
        # Work scales with received input, never with an untrusted declared total.
        first_missing = next(
            index for index in range(len(parsed) + 1) if index not in present
        )
        raise HandoffShardError(
            "missing",
            f"handoff fragment set {parsed[0].set_id} missing shard index {first_missing}; "
            f"received {len(parsed)} of {total}",
        )
    if arrival != sorted(arrival):
        raise HandoffShardError(
            "out_of_order",
            f"handoff shards arrived out of sequence: {arrival}",
        )
    return _decode_shards(parsed)


def restore_handoff_text(value: str | Iterable[str]) -> str:
    """Restore a handoff from either the legacy single text or shard form.

    A string without a shard envelope is returned verbatim (within-budget
    handoffs carry no envelope). A string containing one or more embedded
    shards is extracted and reassembled with full verification; an iterable
    of shard texts is reassembled strictly.
    """

    if isinstance(value, str):
        lines = value.split("\n")
        envelopes = [
            index
            for index, line in enumerate(lines)
            if line.startswith(ENVELOPE_PREFIX)
        ]
        titles = [
            index
            for index, line in enumerate(lines)
            if line.lstrip().startswith(SHARD_TITLE_PREFIXES)
        ]
        if any(
            ENVELOPE_PREFIX in line and not line.startswith(ENVELOPE_PREFIX)
            for line in lines
        ):
            raise HandoffShardError(
                "envelope", "handoff shard envelope is not at the start of a line"
            )
        if titles and (
            len(titles) != len(envelopes)
            or any(index + 1 not in envelopes for index in titles)
        ):
            raise HandoffShardError(
                "envelope", "handoff fragment title is missing its verifiable envelope"
            )
        if envelopes:
            return reassemble_handoff_shards(extract_handoff_shards(value))
        return value
    shard_texts = list(value)
    if not shard_texts:
        raise HandoffShardError("missing", "no handoff text or shards provided")
    first_line = shard_texts[0].split("\n", 1)[0]
    if ENVELOPE_RE.match(first_line):
        return reassemble_handoff_shards(shard_texts)
    if len(shard_texts) == 1:
        return shard_texts[0]
    raise HandoffShardError(
        "envelope",
        "multiple handoff parts provided without shard envelopes",
    )


def extract_handoff_shards(text: str) -> list[str]:
    """Extract shard blocks embedded in a larger text (e.g. a full packet).

    Each shard spans from its envelope line to the line before the next
    envelope; surrounding relay framing is trimmed using the payload
    checksum, so framing text cannot contaminate reassembly.
    """

    lines = text.split("\n")
    starts = [
        index for index, line in enumerate(lines) if line.startswith(ENVELOPE_PREFIX)
    ]
    if any(not ENVELOPE_RE.match(lines[index]) for index in starts):
        raise HandoffShardError(
            "envelope",
            "malformed handoff envelope; obtain the unchanged producer output",
        )
    if not starts:
        raise HandoffShardError("envelope", "no handoff shard envelope found")
    extracted: list[str] = []
    for position, start in enumerate(starts):
        limit = starts[position + 1] if position + 1 < len(starts) else len(lines)
        candidate: str | None = None
        for end in range(limit, start, -1):
            probe = "\n".join(lines[start:end])
            try:
                parse_handoff_shard(probe)
            except HandoffShardError:
                continue
            candidate = probe
            break
        if candidate is None:
            raise HandoffShardError(
                "integrity",
                f"could not verify handoff shard starting at line {start + 1}",
            )
        extracted.append(candidate)
    return extracted


def build_handoff_shard_manifest(
    original_text: str, shard_texts: list[str]
) -> dict[str, Any]:
    """Structured projection of a fragmented handoff for JSON surfaces."""

    parsed = [parse_handoff_shard(text) for text in shard_texts]
    if len(parsed) < 2:
        raise HandoffShardError("envelope", "manifest requires a fragmented handoff")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "set_id": parsed[0].set_id,
        "total": parsed[0].total,
        "original_line_count": len(original_text.split("\n")),
        "original_char_count": len(original_text),
        "shards": [
            {
                "index": shard.index,
                "line_count": len(shard_text.split("\n")),
                "char_count": len(shard_text),
            }
            for shard, shard_text in zip(parsed, shard_texts)
        ],
    }


def render_handoff_transport(text: str, shards: list[str]) -> str:
    """Render complete plain text or an ordered transport set, never both."""
    if not shards:
        return text
    return "\n\n".join(
        f"【交接分片 {index + 1}/{len(shards)}；收齐后用 loopx handoff restore 校验；恢复不授予执行权限】\n{shard}"
        for index, shard in enumerate(shards)
    )
