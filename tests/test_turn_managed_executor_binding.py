"""The managed executor readback names the executor and whether it can launch."""

from __future__ import annotations

import json

import pytest

from loopx.control_plane.turn_driver.host_binding import (
    DEFAULT_DSH_OUTPUT_TOKEN_LIMIT,
    DSH_OUTPUT_TOKEN_BUDGET_SCHEMA_VERSION,
    DSH_RUNTIME_MODULE,
    DSH_RUNTIME_UNAVAILABLE,
    EXECUTOR_KIND_GENERIC,
    EXECUTOR_KIND_INDIVIDUAL,
    EXECUTOR_KIND_MANAGED,
    INDIVIDUAL_TURN_HOST,
    MANAGED_EXECUTOR_BINDING_SCHEMA_VERSION,
    MANAGED_RUNTIME_PROBE_SCHEMA_VERSION,
    MANAGED_TURN_HOST,
    OPERATOR_CREDENTIAL_UNCONFIGURED,
    OUTPUT_TOKEN_LIMIT_SCOPE,
    REMEDY_CONFIGURE_DSH_RUNTIME,
    REMEDY_CONFIGURE_OPERATOR_CREDENTIAL,
    REMEDY_CORRECT_EXECUTION_PROFILE,
    REMEDY_SELECT_INDIVIDUAL_HOST,
    RUNTIME_PROBE_SCOPE_INTERPRETER,
    managed_executor_unavailable_payload,
    managed_executor_binding,
    managed_executor_binding_from_host_args,
    resolve_default_turn_host,
    turn_host_arg_option,
)
from loopx.control_plane.turn_driver.execution_profile import (
    INVALID_REASONING_EFFORT,
    MANAGED_MODEL_DEFAULT,
    MANAGED_PROVIDER_DEFAULT,
    MANAGED_REASONING_EFFORT_DEFAULT,
    managed_execution_profile,
    managed_execution_profile_line,
)

_NO_RUNTIME = lambda _module: False  # noqa: E731 - tiny probe fixture
_RUNTIME = lambda _module: True  # noqa: E731 - tiny probe fixture


def test_trusted_host_args_project_the_same_last_explicit_profile_without_raw_argv():
    binding = managed_executor_binding_from_host_args(
        [
            "--host",
            "generic-cli",
            "--host=dsh",
            "--dsh-provider=fixture-provider",
            "--dsh-model",
            "fixture-model",
            "--dsh-reasoning-effort=max",
        ],
        environ={"DEEPSEEK_API_KEY": "fixture-operator-token"},
        module_probe=_RUNTIME,
    )

    assert binding["executor"] == "dsh"
    assert binding["execution_profile"] == "fixture-provider/fixture-model@max"
    assert binding["available"] is True
    assert "host_args" not in binding
    assert "fixture-operator-token" not in json.dumps(binding)
    assert (
        turn_host_arg_option(
            ["--host", "generic-cli", "--host=dsh"], "--host"
        )
        == "dsh"
    )
    assert turn_host_arg_option(["--host=dsh"], "--host") == "dsh"
    assert (
        turn_host_arg_option(
            ["--dsh-model", "fixture-model", "--host", "dsh"], "--host"
        )
        == "dsh"
    )
    assert turn_host_arg_option(["--host", "--dsh-model", "fixture"], "--host") is None
    assert (
        turn_host_arg_option(
            ["--host", "--dsh-model", "fixture", "--host=dsh"], "--host"
        )
        is None
    )
    assert turn_host_arg_option(["--dsh-model", "fixture"], "--host") is None


def test_managed_executor_reports_the_operator_credential_and_endpoint():
    binding = managed_executor_binding(
        "dsh",
        environ={
            "DEEPSEEK_API_KEY": "sk-operator",
            "DEEPSEEK_BASE_URL": "https://example.invalid",
        },
        module_probe=_RUNTIME,
    )

    assert binding == {
        "schema_version": MANAGED_EXECUTOR_BINDING_SCHEMA_VERSION,
        "executor": "dsh",
        "executor_kind": EXECUTOR_KIND_MANAGED,
        "credential_env": "DEEPSEEK_API_KEY",
        "endpoint_env": "DEEPSEEK_BASE_URL",
        # The agent-facing payload names what would run, not the shipped
        # constants it agrees with.
        "execution_profile": (
            f"{MANAGED_MODEL_DEFAULT}@{MANAGED_REASONING_EFFORT_DEFAULT}"
        ),
        "output_token_budget": {
            "schema_version": DSH_OUTPUT_TOKEN_BUDGET_SCHEMA_VERSION,
            "scope": OUTPUT_TOKEN_LIMIT_SCOPE,
            "max_tokens": DEFAULT_DSH_OUTPUT_TOKEN_LIMIT,
            "valid": True,
            "source": "product_default",
            "final_response_reserve_supported": False,
            "hard_tool_budget_supported": False,
        },
        "operator_credential_bound": True,
        "available": True,
        "unavailable_reason": None,
        "unavailable_remediation": [],
        "runtime_probe": {
            "schema_version": MANAGED_RUNTIME_PROBE_SCHEMA_VERSION,
            "module": DSH_RUNTIME_MODULE,
            "scope": RUNTIME_PROBE_SCOPE_INTERPRETER,
            "available": True,
        },
    }


def test_the_runtime_verdict_states_what_it_is_a_claim_about():
    """A process-level answer must not read as a machine-level fact."""

    present = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_RUNTIME,
    )
    absent = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_NO_RUNTIME,
    )
    individual = managed_executor_binding("codex-cli")

    # The probe follows the same seam the verdict does, so a caller that
    # injects one gets both facts from one answer.
    assert present["runtime_probe"]["available"] is True
    assert absent["runtime_probe"]["available"] is False
    assert absent["runtime_probe"]["scope"] == RUNTIME_PROBE_SCOPE_INTERPRETER
    assert absent["runtime_probe"]["module"] == DSH_RUNTIME_MODULE
    # An individual executor probes no runtime, and the field is still present
    # so no reader branches on its absence.
    assert individual["runtime_probe"] is None
    # This readback travels into the Turn execution payload, so it must stay
    # public-safe: no absolute path, no credential value.
    serialized = json.dumps(absent["runtime_probe"])
    assert "/" not in serialized and "sk-operator" not in serialized, serialized


def test_managed_executor_fails_closed_when_the_runtime_is_missing():
    binding = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_NO_RUNTIME,
    )

    assert binding["available"] is False
    assert binding["unavailable_reason"] == DSH_RUNTIME_UNAVAILABLE
    assert binding["unavailable_remediation"] == [
        REMEDY_CONFIGURE_DSH_RUNTIME,
        REMEDY_SELECT_INDIVIDUAL_HOST,
    ]


def test_the_refusal_names_the_operator_reachable_exits():
    """A typed reason alone leaves the operator to guess the way out."""

    binding = managed_executor_binding("dsh", environ={}, module_probe=_RUNTIME)
    refusal = managed_executor_unavailable_payload(
        {"managed_executor": binding},
        execute=True,
        host_projection={"host": "dsh"},
    )

    assert binding["unavailable_remediation"] == [
        REMEDY_CONFIGURE_OPERATOR_CREDENTIAL,
        REMEDY_SELECT_INDIVIDUAL_HOST,
    ]
    assert refusal is not None
    assert refusal["reason"] == OPERATOR_CREDENTIAL_UNCONFIGURED
    # The exits travel as data: the credential variable to set and the host to
    # select instead, so no caller has to re-derive them from the reason.
    assert refusal["remediation"] == binding["unavailable_remediation"]
    assert refusal["remediation_host"] == "codex-cli"
    assert refusal["remediation_env_vars"] == ["DEEPSEEK_API_KEY"]


def test_a_refused_execution_profile_names_its_own_remedy():
    binding = managed_executor_binding(
        "dsh",
        environ={
            "DEEPSEEK_API_KEY": "sk-operator",
            "LOOPX_TURN_REASONING_EFFORT": "turbo",
        },
        module_probe=_RUNTIME,
    )

    assert binding["unavailable_reason"] == INVALID_REASONING_EFFORT
    assert binding["unavailable_remediation"] == [
        REMEDY_CORRECT_EXECUTION_PROFILE,
        REMEDY_SELECT_INDIVIDUAL_HOST,
    ]


def test_a_launchable_executor_offers_no_remedy():
    binding = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_RUNTIME,
    )

    assert binding["available"] is True
    assert binding["unavailable_remediation"] == []


def test_configured_runner_hook_makes_the_managed_host_launchable():
    binding = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        dsh_runner_configured=True,
        module_probe=_NO_RUNTIME,
    )

    assert binding["available"] is True
    assert binding["unavailable_reason"] is None


def test_managed_executor_reports_an_unconfigured_credential_without_inventing_one():
    binding = managed_executor_binding(
        "dsh",
        environ={},
        dsh_runner_configured=True,
        module_probe=_RUNTIME,
    )

    assert binding["credential_env"] is None
    assert binding["endpoint_env"] is None


def test_managed_selection_without_the_operator_credential_fails_closed():
    """The managed host authenticates with the operator credential or not at all."""

    binding = managed_executor_binding("dsh", environ={}, module_probe=_RUNTIME)

    assert binding["executor_kind"] == EXECUTOR_KIND_MANAGED
    assert binding["operator_credential_bound"] is False
    assert binding["available"] is False
    assert binding["unavailable_reason"] == OPERATOR_CREDENTIAL_UNCONFIGURED


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_credential_counts_as_unconfigured(blank):
    binding = managed_executor_binding(
        "dsh", environ={"DEEPSEEK_API_KEY": blank}, module_probe=_RUNTIME
    )

    assert binding["credential_env"] is None
    assert binding["available"] is False
    assert binding["unavailable_reason"] == OPERATOR_CREDENTIAL_UNCONFIGURED


def test_configured_runner_hook_counts_as_an_operator_credential_boundary():
    binding = managed_executor_binding(
        "dsh",
        environ={},
        dsh_runner_configured=True,
        module_probe=_NO_RUNTIME,
    )

    assert binding["operator_credential_bound"] is True
    assert binding["available"] is True
    assert binding["runtime_probe"] == {
        "schema_version": MANAGED_RUNTIME_PROBE_SCHEMA_VERSION,
        "scope": "configured_runner",
        "module": None,
        "available": True,
    }


def test_individual_and_generic_executors_are_not_operator_credential_bound():
    for host in ("codex-cli", "generic-cli"):
        binding = managed_executor_binding(host, environ={"DEEPSEEK_API_KEY": "sk-x"})

        assert binding["operator_credential_bound"] is False, binding


@pytest.mark.parametrize(
    ("host", "expected_kind"),
    [
        ("codex-cli", EXECUTOR_KIND_INDIVIDUAL),
        ("claude-code", EXECUTOR_KIND_INDIVIDUAL),
        ("generic-cli", EXECUTOR_KIND_GENERIC),
    ],
)
def test_other_hosts_make_no_launch_claim_and_carry_no_operator_env(
    host, expected_kind
):
    binding = managed_executor_binding(
        host,
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_RUNTIME,
    )

    assert binding["executor_kind"] == expected_kind
    assert binding["available"] is None
    assert binding["unavailable_reason"] is None
    assert binding["credential_env"] is None
    assert binding["endpoint_env"] is None
    assert binding["operator_credential_bound"] is False


@pytest.mark.parametrize(
    "environ, expected_host, expected_kind",
    [
        ({}, INDIVIDUAL_TURN_HOST, EXECUTOR_KIND_INDIVIDUAL),
        (
            {"DEEPSEEK_API_KEY": "sk-operator"},
            MANAGED_TURN_HOST,
            EXECUTOR_KIND_MANAGED,
        ),
        ({"DEEPSEEK_API_KEY": "   "}, INDIVIDUAL_TURN_HOST, EXECUTOR_KIND_INDIVIDUAL),
    ],
)
def test_default_resolution_reads_back_the_executor_it_selected(
    environ, expected_host, expected_kind
):
    default_host = resolve_default_turn_host(environ)
    binding = managed_executor_binding(
        default_host,
        environ=environ,
        module_probe=_RUNTIME,
    )

    # The default follows the operator credential, and the readback names the
    # executor that default resolved to. Whether a managed executor may run here
    # stays a separate, explicitly projected fact.
    assert default_host == expected_host
    assert binding["executor_kind"] == expected_kind
    if expected_kind == EXECUTOR_KIND_MANAGED:
        assert binding["available"] is True
        assert binding["unavailable_reason"] is None


def test_endpoint_without_credential_does_not_select_the_managed_default():
    environ = {"DEEPSEEK_BASE_URL": "https://example.invalid"}
    binding = managed_executor_binding("codex-cli", environ=environ)

    assert resolve_default_turn_host(environ) == INDIVIDUAL_TURN_HOST
    assert binding["endpoint_env"] is None


def test_managed_binding_projects_the_execution_profile_and_its_source():
    binding = managed_executor_binding(
        "dsh",
        environ={
            "DEEPSEEK_API_KEY": "sk-operator",
            "LOOPX_TURN_MODEL": "fixture-model",
            "LOOPX_TURN_REASONING_EFFORT": "max",
        },
        module_probe=_RUNTIME,
    )

    # An owner-set value appears as itself, so it is never read as the shipped
    # default; the shipped provider is not restated.
    assert binding["execution_profile"] == "fixture-model@max"
    # An explicit argument outranks the environment and says so.
    overridden = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator", "LOOPX_TURN_MODEL": "fixture-model"},
        module_probe=_RUNTIME,
        model="cli-model",
    )["execution_profile"]
    assert overridden == "cli-model@high"


def test_a_deviating_provider_is_named_in_the_profile_line():
    # Dropping the provider unconditionally would let the line claim a profile
    # this Turn would not use, so a non-shipped provider is spelled out.
    binding = managed_executor_binding(
        "dsh",
        environ={
            "DEEPSEEK_API_KEY": "sk-operator",
            "LOOPX_TURN_PROVIDER": "fixture-provider",
        },
        module_probe=_RUNTIME,
    )

    assert binding["execution_profile"] == "fixture-provider/deepseek-v4-flash@high"


def test_non_managed_hosts_carry_no_execution_profile():
    for host in ("codex-cli", "generic-cli"):
        binding = managed_executor_binding(
            host, environ={"DEEPSEEK_API_KEY": "sk-operator"}, module_probe=_RUNTIME
        )

        assert binding["execution_profile"] is None, binding
        assert binding.get("output_token_budget") is None, binding


def test_managed_binding_projects_the_per_request_output_budget() -> None:
    defaulted = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_RUNTIME,
    )
    explicit = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_RUNTIME,
        max_tokens=5_000,
    )

    assert defaulted["output_token_budget"] == {
        "schema_version": DSH_OUTPUT_TOKEN_BUDGET_SCHEMA_VERSION,
        "scope": "per_model_request",
        "max_tokens": DEFAULT_DSH_OUTPUT_TOKEN_LIMIT,
        "valid": True,
        "source": "product_default",
        "final_response_reserve_supported": False,
        "hard_tool_budget_supported": False,
    }
    assert explicit["output_token_budget"]["max_tokens"] == 5_000
    assert explicit["output_token_budget"]["source"] == "explicit_argument"


@pytest.mark.parametrize("invalid", [0, -1, True])
def test_invalid_output_token_limit_fails_closed(invalid: object) -> None:
    binding = managed_executor_binding(
        "dsh",
        environ={"DEEPSEEK_API_KEY": "sk-operator"},
        module_probe=_RUNTIME,
        max_tokens=invalid,  # type: ignore[arg-type]
    )

    assert binding["available"] is False
    assert binding["unavailable_reason"] == "invalid_output_token_limit"
    assert binding["output_token_budget"]["valid"] is False
    assert binding["output_token_budget"]["max_tokens"] is None


def test_an_unsupported_effort_fails_closed_before_launch():
    # The endpoint rejects an effort outside its vocabulary, so the readback
    # refuses instead of letting a bounded Turn spend itself on that request.
    binding = managed_executor_binding(
        "dsh",
        environ={
            "DEEPSEEK_API_KEY": "sk-operator",
            "LOOPX_TURN_REASONING_EFFORT": "turbo",
        },
        module_probe=_RUNTIME,
    )

    # The refused effort is visible next to the typed verdict, so a reader does
    # not have to re-derive which configured value the endpoint rejected.
    assert binding["execution_profile"] == "deepseek-v4-flash@turbo"
    assert binding["available"] is False
    assert binding["unavailable_reason"] == INVALID_REASONING_EFFORT


def test_a_missing_launch_fact_still_outranks_the_profile_verdict():
    binding = managed_executor_binding(
        "dsh",
        environ={"LOOPX_TURN_REASONING_EFFORT": "turbo"},
        module_probe=_RUNTIME,
    )

    # One typed reason per readback: the missing credential is the fact that
    # decides whether this executor launches at all.
    assert binding["unavailable_reason"] == OPERATOR_CREDENTIAL_UNCONFIGURED
    assert binding["execution_profile"] == "deepseek-v4-flash@turbo"


def test_the_agent_facing_profile_line_stays_one_bounded_line():
    shipped = managed_execution_profile_line(managed_execution_profile({}))

    assert shipped == f"{MANAGED_MODEL_DEFAULT}@{MANAGED_REASONING_EFFORT_DEFAULT}"
    assert MANAGED_PROVIDER_DEFAULT not in shipped
    # The budget this line has to fit is per planned Turn, so it must stay far
    # below the object form's ~360 characters.
    assert len(shipped) < 64
