"""Local broker adapter contracts and simulations."""

from .converters import build_broker_requests_from_child_orders, broker_fills_to_execution_fills, execution_fills_to_broker_fills
from .file_adapter import FileInstructionBrokerAdapter
from .models import (
    BrokerAdapterConfig,
    BrokerBatchSummary,
    BrokerFillRecord,
    BrokerOrderEvent,
    BrokerOrderRecord,
    BrokerOrderRequest,
    BrokerOrderStatus,
    BrokerReconciliationIssue,
    BrokerReconciliationReport,
    BrokerSubmitResult,
    TERMINAL_STATUSES,
)
from .protocol import BrokerAdapter
from .reconciliation import reconcile_broker_batch
from .report import write_broker_report
from .simulated import SimulatedBrokerAdapter
from .state_machine import BrokerStateError, validate_transition
from .store import LocalBrokerStore

__all__ = [
    "BrokerAdapter",
    "BrokerAdapterConfig",
    "BrokerBatchSummary",
    "BrokerFillRecord",
    "BrokerOrderEvent",
    "BrokerOrderRecord",
    "BrokerOrderRequest",
    "BrokerOrderStatus",
    "BrokerReconciliationIssue",
    "BrokerReconciliationReport",
    "BrokerStateError",
    "BrokerSubmitResult",
    "FileInstructionBrokerAdapter",
    "LocalBrokerStore",
    "SimulatedBrokerAdapter",
    "TERMINAL_STATUSES",
    "broker_fills_to_execution_fills",
    "build_broker_requests_from_child_orders",
    "execution_fills_to_broker_fills",
    "reconcile_broker_batch",
    "validate_transition",
    "write_broker_report",
]
