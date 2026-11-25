"""Callback chain builders for NVIDIA GPU Operator atomic verbs and combos (CI17).

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from lib.python.core.abstract.callbacks import AbstractCallbacks
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.kubernetes.kubernetes import DEFAULT_COMMAND_TIMEOUT, K8SAuth
from lib.python.core.kubernetes.nvidia_gpu.callbacks import (
    NvidiaHealthCheck,
    NvidiaPauseOperator,
)
from lib.python.core.kubernetes.nvidia_gpu.models import GPUOperatorAction


def health_check_callbacks(
    action: GPUOperatorAction,
) -> AbstractCallbacks[DeliveryState, GPUOperatorAction]:
    """Compose the read-only ``NvidiaHealthCheck`` workflow chain.

    :param action: Prepared GPU operator action.
    :returns: Authentication and read-only health check callbacks.
    """
    return [
        K8SAuth(action.target, min(action.args.timeout, DEFAULT_COMMAND_TIMEOUT)),
        NvidiaHealthCheck(action),
    ]


def pause_operator_callbacks(
    action: GPUOperatorAction,
) -> AbstractCallbacks[DeliveryState, GPUOperatorAction]:
    """Compose the ``NvidiaPauseOperator`` plan/apply workflow chain.

    :param action: Prepared GPU operator action.
    :returns: Authentication, baseline health read, and pause/resume callbacks.
    """
    return [
        K8SAuth(action.target, min(action.args.timeout, DEFAULT_COMMAND_TIMEOUT)),
        NvidiaHealthCheck(action),
        NvidiaPauseOperator(action),
    ]
