"""Operator actions for typed Chat endpoint availability refusals.

The endpoint readback owns the blocking fact. These messages tell a surface
what clears it without selecting an endpoint or starting a native runtime.
"""

AGENT_ENDPOINT_UNAVAILABLE = "agent_endpoint_unavailable"
AGENT_ENDPOINT_NEXT_ACTIONS = {
    "oar_runtime_unavailable": (
        "The LoopX service needs the optional OAR runtime for this endpoint. "
        "Activate Node.js 24+ and npm in the same service environment, run "
        "`python -m loopx.hosts.oar install`, then verify "
        "`python -m loopx.hosts.oar status` before retrying."
    ),
    "dsh_runtime_unavailable": (
        "The LoopX Chat service interpreter cannot import deepseek_harness. "
        "Run `loopx doctor` in that service environment and check python.executable; "
        "install `loopx[deepseek-harness]` in the same environment, then restart LoopX Chat."
    ),
    "operator_credential_unconfigured": (
        "Set the managed executor credential (DEEPSEEK_API_KEY, with "
        "DEEPSEEK_BASE_URL when the endpoint is not the provider default) in the "
        "LoopX Chat service environment, then restart it."
    ),
    "invalid_reasoning_effort": (
        "Fix the configured reasoning effort (LOOPX_MANAGER_REASONING_EFFORT or "
        "LOOPX_TURN_REASONING_EFFORT) and restart LoopX Chat."
    ),
}
