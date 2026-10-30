"""Registration and cross-language parity checks for the DSH observer provider."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loopx.capabilities.catalog import (
    build_capability_catalog_packet,
    build_capability_detail_packet,
)
from loopx.capabilities.reliability_diagnostics import (
    CAPABILITY_ID,
    DSH_PROVIDER_ID,
    ENVELOPE_FIELDS,
    OBSERVER_ENVELOPE_SCHEMA_VERSION,
    OBSERVER_STATS_SCHEMA_VERSION,
    EnvelopeRejection,
    ObserverEnvelopeError,
    ObserverEventKind,
    dsh_fixture_records,
    normalize_observer_envelope,
)
from loopx.capabilities.reliability_diagnostics.intake import STATS_FIELDS
from loopx.public_safe_text import (
    HOME_RELATIVE_PATH_PATTERN,
    LOCAL_PATH_SURFACE_PATTERN,
    PATH_PREFIX_LOCAL_PATTERN,
    SECRET_LIKE_SURFACE_PATTERN,
)

ROOT = Path(__file__).resolve().parents[2]
OBSERVER_TS = ROOT / "packages/dsh-loopx-plugin/src/observer.ts"
DRIVER_TS = ROOT / "packages/dsh-loopx-plugin/src/driver.ts"
CORDIS_PATCH = ROOT / "packages/dsh-loopx-plugin/cordis.patch.yml"
PACKAGE_JSON = ROOT / "packages/dsh-loopx-plugin/package.json"
TSDOWN_CONFIG = ROOT / "packages/dsh-loopx-plugin/tsdown.config.ts"
PUBLIC_SAFETY_FIXTURE = ROOT / (
    "tests/fixtures/control_plane/reliability_diagnostics_public_safety_v0.json"
)
PUBLIC_SAFETY_CASES = json.loads(PUBLIC_SAFETY_FIXTURE.read_text(encoding="utf-8"))


def test_catalog_declares_dsh_provider_without_claiming_readiness() -> None:
    packet = build_capability_catalog_packet()
    summary = next(
        item for item in packet["capabilities"] if item["id"] == CAPABILITY_ID
    )
    assert summary["provider_id"] == "loopx-core"
    assert summary["implementation_provider_count"] == 1
    provider = next(
        item for item in packet["providers"] if item["id"] == DSH_PROVIDER_ID
    )
    assert provider == {
        "id": DSH_PROVIDER_ID,
        "origin": "extension",
        "declared": True,
        "installed": False,
        "enabled": False,
        "ready": False,
    }

    detail = build_capability_detail_packet(CAPABILITY_ID)["capability"]
    assert detail["default_enabled"] is False
    [implementation] = detail["implementation_providers"]
    assert implementation["provider_id"] == DSH_PROVIDER_ID
    assert implementation["protocol"] == OBSERVER_ENVELOPE_SCHEMA_VERSION
    assert implementation["provider_state"] == {
        "declared": True,
        "installed": False,
        "enabled": False,
        "ready": False,
    }


def test_typescript_observer_shares_field_names_and_has_no_control_path() -> None:
    source = OBSERVER_TS.read_text(encoding="utf-8")
    assert f"'{OBSERVER_ENVELOPE_SCHEMA_VERSION}'" in source
    assert f"'{OBSERVER_STATS_SCHEMA_VERSION}'" in source
    assert f"'{CAPABILITY_ID}'" in source
    assert f"'{DSH_PROVIDER_ID}'" in source
    for field in ENVELOPE_FIELDS | STATS_FIELDS:
        assert re.search(rf"\b{field}\b", source), field
    for kind in ObserverEventKind:
        assert f"'{kind.value}'" in source, kind

    # Physically separate from the continuation driver and its send path.
    assert "from './driver" not in source
    assert "from './cli" not in source
    assert "from './managed-runtime" not in source
    assert ".send(" not in source
    assert ".inbox" not in source
    assert "@deepseek-ai/dsh-agent" not in source
    assert "ctx.on('agent/" not in source
    assert "outbound_endpoints: []" in source
    assert "observation_entered_worker_context: false" in source
    assert "observation_entered_scheduler_inputs: false" in source


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in ("tool_name", "error_class")
        for value in PUBLIC_SAFETY_CASES["unsafe_summary_tokens"]
    ],
)
def test_shared_counterfactual_rejects_unsafe_summary_token(
    field: str,
    value: str,
) -> None:
    record = {
        **dsh_fixture_records()[5],
        "summary": {"turn": 1, "step": 1, field: value},
        "source_refs": {"event_seq": "5", "tool_call_id": "call-safe"},
    }
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is EnvelopeRejection.PUBLIC_SAFETY_VIOLATION


@pytest.mark.parametrize("value", PUBLIC_SAFETY_CASES["unsafe_identity_tokens"])
def test_shared_counterfactual_rejects_unsafe_source_ref(value: str) -> None:
    record = {
        **dsh_fixture_records()[5],
        "summary": {"turn": 1, "step": 1, "tool_name": "bash"},
        "source_refs": {"event_seq": "5", "tool_call_id": value},
    }
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is EnvelopeRejection.PUBLIC_SAFETY_VIOLATION


@pytest.mark.parametrize("value", PUBLIC_SAFETY_CASES["invalid_source_ref_tokens"])
def test_shared_counterfactual_rejects_local_path_source_ref_shape(value: str) -> None:
    record = {
        **dsh_fixture_records()[5],
        "summary": {"turn": 1, "step": 1, "tool_name": "bash"},
        "source_refs": {"event_seq": "5", "tool_call_id": value},
    }
    with pytest.raises(ObserverEnvelopeError) as excinfo:
        normalize_observer_envelope(record)
    assert excinfo.value.reason is EnvelopeRejection.SOURCE_REF_INVALID


@pytest.mark.parametrize("value", PUBLIC_SAFETY_CASES["safe_summary_tokens"])
def test_shared_counterfactual_allows_safe_summary_token(value: str) -> None:
    record = {
        **dsh_fixture_records()[5],
        "summary": {"turn": 1, "step": 1, "tool_name": value},
        "source_refs": {"event_seq": "5", "tool_call_id": "call-safe"},
    }
    assert normalize_observer_envelope(record).summary["tool_name"] == value


@pytest.mark.parametrize("value", PUBLIC_SAFETY_CASES["safe_identity_tokens"])
def test_shared_counterfactual_allows_safe_source_ref(value: str) -> None:
    record = {
        **dsh_fixture_records()[5],
        "summary": {"turn": 1, "step": 1, "tool_name": "bash"},
        "source_refs": {"event_seq": "5", "tool_call_id": value},
    }
    assert normalize_observer_envelope(record).source_refs["tool_call_id"] == value


def _typescript_arms(constant: str) -> list[str]:
    """Return the regex bodies of one `observer.ts` shape list, in file order.

    The producer writes one literal per line so each body is the text between
    the opening slash and the `/iu` flags. The order is read from the file
    because the canonical comparison rebuilds its alternation in that order, so
    reordering the arms reads as a failure here even though it changes no
    verdict. That is the point: the list is a copy, and a copy is compared as
    text.
    """

    source = OBSERVER_TS.read_text(encoding="utf-8")
    block = source.split(f"const {constant} = [", 1)[1].split("] as const", 1)[0]
    arms = []
    for line in block.splitlines():
        arm = line.strip().rstrip(",")
        if not arm:
            continue
        assert arm.startswith("/") and arm.endswith("/iu"), arm
        arms.append(arm[1 : -len("/iu")])
    return arms


def _reduced_pattern_source(source: str) -> str:
    """Drop the escapes that only one spelling of the same regex needs.

    `public_safe_text.py` writes `\\"` inside raw strings, a redundant escape of
    `"`, and a JavaScript regex literal must escape a `/` that appears outside a
    character class. Neither escape changes either regex's meaning, so reducing
    both lets the two owners be compared as exact text. Only an odd-length
    backslash run escapes those characters, which keeps `[\\\\/]` intact.
    """

    return re.sub(r'(?<!\\)((?:\\\\)*)\\([/"])', r"\1\2", source)


def test_typescript_mirror_equals_the_canonical_credential_arms() -> None:
    """Refs #7729: the producer's fail-closed check is a copy of the canonical
    owner's shapes, and a scan that recognizes only some issuers certifies the
    rest as public-safe. Rebuilding the alternation from the mirror's own arms
    makes a missing, narrowed, reordered or invented arm fail here -- which is
    where the canonical owner's next issuer addition lands.
    """

    rebuilt = "(?i)(?:" + "|".join(_typescript_arms("SECRET_LIKE_SURFACES")) + ")"
    assert _reduced_pattern_source(rebuilt) == _reduced_pattern_source(
        SECRET_LIKE_SURFACE_PATTERN.pattern
    )


def test_typescript_mirror_equals_the_canonical_local_path_surfaces() -> None:
    """Refs #7729: the mirror narrowed the drive arm to two directory names, so
    every other drive path reached the ledger. The absolute, drive and UNC
    alternatives share one lookbehind in the canonical owner, so the mirror
    spells that lookbehind on each of the three.
    """

    lookbehind = r"(?<![:/A-Za-z0-9])"
    mirrored = [
        _reduced_pattern_source(arm) for arm in _typescript_arms("LOCAL_PATH_SURFACES")
    ]
    absolute = [arm for arm in mirrored if arm.startswith(lookbehind)]
    assert len(absolute) == 3
    rebuilt = (
        lookbehind + "(?:" + "|".join(arm[len(lookbehind) :] for arm in absolute) + ")"
    )
    assert rebuilt == _reduced_pattern_source(LOCAL_PATH_SURFACE_PATTERN.pattern)
    for pattern in (HOME_RELATIVE_PATH_PATTERN, PATH_PREFIX_LOCAL_PATTERN):
        assert _reduced_pattern_source(pattern.pattern) in mirrored
    # The file-URL arm stays at least as broad as the canonical `file://`.
    assert r"\bfile:/+" in mirrored


def test_observer_is_a_separate_default_off_plugin_row() -> None:
    driver = DRIVER_TS.read_text(encoding="utf-8")
    observer = OBSERVER_TS.read_text(encoding="utf-8")
    patch = CORDIS_PATCH.read_text(encoding="utf-8")
    build = TSDOWN_CONFIG.read_text(encoding="utf-8")
    manifest = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

    assert "observer" not in driver.lower()
    assert "export const inject: readonly string[] = []" in observer
    assert "ctx.on('session/created'" in observer
    assert "ctx.on('session/event'" in observer
    assert "ctx.on('session/disposed'" in observer
    assert "loopx-shadow-observer" in patch
    assert "name: dsh-loopx-plugin/observer" in patch
    assert "observer: 'build-temp/host/observer.js'" in build
    assert manifest["exports"]["./observer"] == {
        "types": "./lib/types/observer.d.ts",
        "default": "./lib/observer.js",
    }
