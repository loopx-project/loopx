"""Finance value-discovery extension."""

from importlib import import_module

from .attribution import (
    EXPLAINED_BETA_COMPONENTS,
    FINANCE_BETA_ATTRIBUTION_INPUT_SCHEMA_VERSION,
    FINANCE_BETA_ATTRIBUTION_REPLAY_SCHEMA_VERSION,
    FINANCE_BETA_ATTRIBUTION_SCHEMA_VERSION,
    build_finance_beta_attribution,
    replay_finance_beta_attribution,
)
from .contract import (
    FINANCE_CASE_CONTRACT_SCHEMA_VERSION,
    FINANCE_CASE_EVALUATION_SCHEMA_VERSION,
    FINANCE_CASE_INPUT_SCHEMA_VERSION,
    validate_finance_case_contract,
)
from .contract_liquidity import (
    FINANCE_CONTRACT_LIQUIDITY_EVALUATION_SCHEMA_VERSION,
    FINANCE_CONTRACT_LIQUIDITY_INPUT_SCHEMA_VERSION,
    evaluate_finance_contract_liquidity,
)
from .gates import evaluate_finance_case_gates
from .metric_packs import (
    FINANCE_METRIC_PACK_EVALUATION_SCHEMA_VERSION,
    FINANCE_METRIC_PACK_INPUT_SCHEMA_VERSION,
    FINANCE_METRIC_PACK_REPLAY_SCHEMA_VERSION,
    build_finance_metric_pack_evaluation,
    list_finance_metric_packs,
    replay_finance_metric_pack_evaluation,
)
from .operation_request import (
    FINANCE_TRANSACTION_APPROVAL_INPUT_SCHEMA_VERSION,
    FINANCE_TRANSACTION_APPROVAL_PACKET_SCHEMA_VERSION,
    build_finance_transaction_approval_packet,
)
from .reducer import (
    EVIDENCE_AXES,
    FINANCE_VALUE_DISCOVERY_CARD_SCHEMA_VERSION,
    FINANCE_VALUE_DISCOVERY_EXTENSION_PROTOCOL,
    FINANCE_VALUE_DISCOVERY_INPUT_SCHEMA_VERSION,
    FINANCE_VALUE_DISCOVERY_PACKET_SCHEMA_VERSION,
    build_finance_value_discovery_packet,
    render_finance_value_discovery_markdown,
)
from .replay import (
    FINANCE_CASE_REPLAY_RECEIPT_SCHEMA_VERSION,
    build_finance_case_evaluation,
    replay_finance_case_evaluation,
)

__all__ = [
    "EVIDENCE_AXES",
    "EXPLAINED_BETA_COMPONENTS",
    "FINANCE_BETA_ATTRIBUTION_INPUT_SCHEMA_VERSION",
    "FINANCE_BETA_ATTRIBUTION_REPLAY_SCHEMA_VERSION",
    "FINANCE_BETA_ATTRIBUTION_SCHEMA_VERSION",
    "FINANCE_CASE_CONTRACT_SCHEMA_VERSION",
    "FINANCE_CASE_EVALUATION_SCHEMA_VERSION",
    "FINANCE_CASE_INPUT_SCHEMA_VERSION",
    "FINANCE_CASE_REPLAY_RECEIPT_SCHEMA_VERSION",
    "FINANCE_CONTRACT_LIQUIDITY_EVALUATION_SCHEMA_VERSION",
    "FINANCE_CONTRACT_LIQUIDITY_INPUT_SCHEMA_VERSION",
    "FINANCE_METRIC_PACK_EVALUATION_SCHEMA_VERSION",
    "FINANCE_METRIC_PACK_INPUT_SCHEMA_VERSION",
    "FINANCE_METRIC_PACK_REPLAY_SCHEMA_VERSION",
    "FINANCE_RESEARCH_DASHBOARD_INPUT_SCHEMA_VERSION",
    "FINANCE_RESEARCH_DASHBOARD_PACKET_SCHEMA_VERSION",
    "FINANCE_TRANSACTION_APPROVAL_INPUT_SCHEMA_VERSION",
    "FINANCE_TRANSACTION_APPROVAL_PACKET_SCHEMA_VERSION",
    "FINANCE_VALUE_DISCOVERY_CARD_SCHEMA_VERSION",
    "FINANCE_VALUE_DISCOVERY_EXTENSION_PROTOCOL",
    "FINANCE_VALUE_DISCOVERY_INPUT_SCHEMA_VERSION",
    "FINANCE_VALUE_DISCOVERY_PACKET_SCHEMA_VERSION",
    "build_finance_beta_attribution",
    "build_finance_case_evaluation",
    "build_finance_metric_pack_evaluation",
    "build_finance_research_dashboard_packet",
    "build_finance_transaction_approval_packet",
    "build_finance_value_discovery_packet",
    "evaluate_finance_case_gates",
    "evaluate_finance_contract_liquidity",
    "list_finance_metric_packs",
    "render_finance_value_discovery_markdown",
    "replay_finance_beta_attribution",
    "replay_finance_case_evaluation",
    "replay_finance_metric_pack_evaluation",
    "validate_finance_case_contract",
]


def __getattr__(name: str) -> object:
    if name in {
        "FINANCE_RESEARCH_DASHBOARD_INPUT_SCHEMA_VERSION",
        "FINANCE_RESEARCH_DASHBOARD_PACKET_SCHEMA_VERSION",
        "build_finance_research_dashboard_packet",
    }:
        return getattr(import_module(".dashboard", __name__), name)
    raise AttributeError(name)
