"""Machine-owned runtime profile for the persistent LoopX manager."""

from .machine_profile import (
    MANAGER_RUNTIME_PROFILE_SCHEMA,
    effective_manager_runtime_profile,
    load_bound_manager_runtime_profile,
    load_effective_manager_runtime_profile,
    manager_runtime_capability_projection,
    manager_runtime_machine_configuration_namespace,
    manager_runtime_session_fields,
    normalize_manager_runtime_profile,
)

__all__ = [
    "MANAGER_RUNTIME_PROFILE_SCHEMA",
    "effective_manager_runtime_profile",
    "load_bound_manager_runtime_profile",
    "load_effective_manager_runtime_profile",
    "manager_runtime_capability_projection",
    "manager_runtime_machine_configuration_namespace",
    "manager_runtime_session_fields",
    "normalize_manager_runtime_profile",
]
