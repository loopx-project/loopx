"""Supported host seam for an authorized restart of managed Chat services.

The runtime owner keeps accepted-work observation and ingress locking. This
facade grants no stop authority and does not copy its admission decisions.
"""

from ..runtime_activation import (
    RuntimeActivationDeferred as RuntimeActivationDeferred,
    managed_chat_activation_guard as managed_chat_activation_guard,
)

__all__ = ["RuntimeActivationDeferred", "managed_chat_activation_guard"]
