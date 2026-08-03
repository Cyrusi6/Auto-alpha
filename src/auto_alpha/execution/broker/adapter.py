"""Local broker adapter contracts and simulations."""

from auto_alpha.execution.broker.adapter_converters import build_broker_requests_from_child_orders
from auto_alpha.execution.broker.adapter_converters import broker_fills_to_execution_fills
from auto_alpha.execution.broker.adapter_converters import execution_fills_to_broker_fills
from auto_alpha.execution.broker.adapter_file_adapter import FileInstructionBrokerAdapter
from auto_alpha.execution.broker.adapter_models import BrokerAdapterConfig
from auto_alpha.execution.broker.adapter_models import BrokerBatchSummary
from auto_alpha.execution.broker.adapter_models import BrokerFillRecord
from auto_alpha.execution.broker.adapter_models import BrokerOrderEvent
from auto_alpha.execution.broker.adapter_models import BrokerOrderRecord
from auto_alpha.execution.broker.adapter_models import BrokerOrderRequest
from auto_alpha.execution.broker.adapter_models import BrokerOrderStatus
from auto_alpha.execution.broker.adapter_models import BrokerReconciliationIssue
from auto_alpha.execution.broker.adapter_models import BrokerReconciliationReport
from auto_alpha.execution.broker.adapter_models import BrokerSubmitResult
from auto_alpha.execution.broker.adapter_models import TERMINAL_STATUSES
from auto_alpha.execution.broker.adapter_protocol import BrokerAdapter
from auto_alpha.execution.broker.adapter_reconciliation import reconcile_broker_batch
from auto_alpha.execution.broker.adapter_report import write_broker_report
from auto_alpha.execution.broker.adapter_simulated import SimulatedBrokerAdapter
from auto_alpha.execution.broker.adapter_state_machine import BrokerStateError
from auto_alpha.execution.broker.adapter_state_machine import validate_transition
from auto_alpha.execution.broker.adapter_store import LocalBrokerStore

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
