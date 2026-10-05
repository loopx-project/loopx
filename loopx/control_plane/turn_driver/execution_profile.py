"""The one managed execution profile: provider, model and reasoning effort.

The managed Turn host is *selected* separately (see :mod:`.host_binding`); this
module owns the profile that runs on it. LoopX ships one product default per
field and the operator may override each field with at most one environment
variable, so the resolved profile is always a decided value rather than an
inference. A configured operator credential authenticates this profile; it never
chooses it.

The resolution is a pure function of an environment mapping so a caller can read
back what a governed Turn *would* use, with the source of every field, before
anything launches. The steward channel resolves its own model and effort from
this same profile, so the interactive channel and the bounded Turn cannot drift
onto two different managed models.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...reasoning_effort import REASONING_EFFORTS
from ..operator_credential import env_text

MANAGED_EXECUTION_PROFILE_SCHEMA_VERSION = "managed_execution_profile_v0"

# Shipped product defaults for the managed executor. These are a product
# decision recorded once; the environment can override a field, but nothing is
# discovered and no credential participates in the choice.
MANAGED_PROVIDER_DEFAULT = "deepseek-official"
# The canonical id of DeepSeek-V4.1-Flash. The retired ``deepseek-v4-flash``
# spelling is still accepted by the vendor endpoint and remains available as an
# explicit override, but no shipped surface may name a retired id by default.
MANAGED_MODEL_DEFAULT = "deepseek-flash"
MANAGED_REASONING_EFFORT_DEFAULT = "high"

PROVIDER_ENV_VAR = "LOOPX_TURN_PROVIDER"
MODEL_ENV_VAR = "LOOPX_TURN_MODEL"
REASONING_EFFORT_ENV_VAR = "LOOPX_TURN_REASONING_EFFORT"
# The dsh adapter resolved these two names into the same fields before the
# profile was named. They keep working as the adapter's own compatibility
# spelling, at lower precedence than the canonical LoopX variable, and the
# readback names whichever variable actually applied.
LEGACY_PROVIDER_ENV_VAR = "DSH_PROVIDER"
LEGACY_MODEL_ENV_VAR = "DSH_MODEL"

PROFILE_SOURCE_PRODUCT_DEFAULT = "product_default"
PROFILE_SOURCE_ENV_OVERRIDE = "env_override"
# One caller-supplied value (a CLI flag on the invoking command) outranks the
# environment for that field only.
PROFILE_SOURCE_EXPLICIT_ARGUMENT = "explicit_argument"

# One typed reason for a profile LoopX cannot launch: the provider rejects an
# effort outside its vocabulary, so refusing here beats spending a Turn on a
# request the endpoint is known to reject.
INVALID_REASONING_EFFORT = "invalid_reasoning_effort"


def _resolve_field(
    *,
    default: str,
    env_var: str,
    legacy_env_var: str | None,
    environ: Mapping[str, str] | None,
) -> tuple[str, str, str]:
    """Return the resolved value, its source, and the variable that set it."""

    explicit = env_text(env_var, environ)
    if explicit:
        return explicit, PROFILE_SOURCE_ENV_OVERRIDE, env_var
    if legacy_env_var:
        legacy = env_text(legacy_env_var, environ)
        if legacy:
            return legacy, PROFILE_SOURCE_ENV_OVERRIDE, legacy_env_var
    return default, PROFILE_SOURCE_PRODUCT_DEFAULT, ""


def managed_execution_profile(
    environ: Mapping[str, str] | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Return the resolved managed execution profile and every field's source.

    ``reasoning_effort_supported`` is the only derived field: it records whether
    LoopX could launch this profile at all. Resolution never raises and never
    falls back to a different effort, so a caller that has to fail closed reads
    one explicit verdict instead of catching a configuration error.

    An explicit ``provider``/``model``/``reasoning_effort`` argument wins over
    the environment for that field and is reported as ``explicit_argument``, so
    the readback a command publishes matches the values that same command hands
    to the host adapter.
    """

    # Keep the arguments under names of their own: the resolution below reuses
    # ``provider``/``model``/``reasoning_effort`` for the resolved values, and
    # testing those would read a decided default as a caller override.
    explicit_provider, explicit_model, explicit_effort = (
        provider,
        model,
        reasoning_effort,
    )
    provider, provider_source, provider_env = _resolve_field(
        default=MANAGED_PROVIDER_DEFAULT,
        env_var=PROVIDER_ENV_VAR,
        legacy_env_var=LEGACY_PROVIDER_ENV_VAR,
        environ=environ,
    )
    model, model_source, model_env = _resolve_field(
        default=MANAGED_MODEL_DEFAULT,
        env_var=MODEL_ENV_VAR,
        legacy_env_var=LEGACY_MODEL_ENV_VAR,
        environ=environ,
    )
    effort, effort_source, effort_env = _resolve_field(
        default=MANAGED_REASONING_EFFORT_DEFAULT,
        env_var=REASONING_EFFORT_ENV_VAR,
        legacy_env_var=None,
        environ=environ,
    )
    if explicit_provider:
        provider, provider_source, provider_env = (
            explicit_provider,
            PROFILE_SOURCE_EXPLICIT_ARGUMENT,
            "",
        )
    if explicit_model:
        model, model_source, model_env = (
            explicit_model,
            PROFILE_SOURCE_EXPLICIT_ARGUMENT,
            "",
        )
    if explicit_effort:
        effort, effort_source, effort_env = (
            explicit_effort,
            PROFILE_SOURCE_EXPLICIT_ARGUMENT,
            "",
        )
    return {
        "schema_version": MANAGED_EXECUTION_PROFILE_SCHEMA_VERSION,
        "provider": provider,
        "provider_source": provider_source,
        "provider_env_var": provider_env,
        "model": model,
        "model_source": model_source,
        "model_env_var": model_env,
        "reasoning_effort": effort,
        "reasoning_effort_source": effort_source,
        "reasoning_effort_env_var": effort_env,
        "reasoning_effort_supported": effort in REASONING_EFFORTS,
    }


def managed_profile_unavailable_reason(profile: Mapping[str, Any]) -> str | None:
    """Return the typed reason a resolved profile cannot launch, if any."""

    if profile.get("reasoning_effort_supported") is False:
        return INVALID_REASONING_EFFORT
    return None


def managed_execution_profile_line(profile: Mapping[str, Any]) -> str:
    """Return the one-line profile an agent-facing payload carries.

    The agent-facing output budget is a contract, and it will not grant a nested
    object per planned Turn on the hot path. Repeating shipped constants is not
    information either, so this readback stays one line: the model and the
    reasoning effort that would run, in that order. Whatever the resolved values
    are, they are the values -- an owner-set model appears here as itself rather
    than as the shipped default.

    The provider is named only when it is not the shipped one. It is a constant
    in the ordinary case, but dropping it when the operator resolved a different
    provider would make the line claim a profile the Turn would not use.

    The field-by-field form, with each value's source and the variable that set
    it, belongs to the surfaces a person reads while configuring LoopX, not to
    every plan an agent parses.
    """

    provider = str(profile.get("provider") or "")
    model = str(profile.get("model") or "")
    effort = str(profile.get("reasoning_effort") or "")
    if provider and provider != MANAGED_PROVIDER_DEFAULT:
        return f"{provider}/{model}@{effort}"
    return f"{model}@{effort}"


def require_supported_reasoning_effort(effort: str) -> str:
    """Return the effort when the provider vocabulary accepts it.

    The launch boundary calls this so a profile that cannot run fails as a typed
    contract refusal instead of a silent provider error.
    """

    if effort not in REASONING_EFFORTS:
        raise ValueError(
            f"unsupported reasoning effort {effort!r}; expected one of "
            + ", ".join(REASONING_EFFORTS)
        )
    return effort
