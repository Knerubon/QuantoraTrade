"""PAPER execution lifecycle and deterministic local adapter."""

from quantora_trade.execution.durable import (
    DurablePaperAdapter,
    PaperOrderReconciliationRequired,
    PaperOrderRepository,
)
from quantora_trade.execution.lifecycle import (
    ORDER_TRANSITIONS,
    InvalidOrderTransition,
    require_transition,
)
from quantora_trade.execution.models import (
    Fill,
    InstrumentExecutionSnapshot,
    OrderEvent,
    OrderStatus,
    PaperOrder,
    PaperOrderRequest,
    PaperQuote,
)
from quantora_trade.execution.paper import (
    DeterministicPaperAdapter,
    IdempotencyConflict,
    PaperFillPolicy,
    request_hash,
)
from quantora_trade.execution.runtime import (
    PaperRuntime,
    PaperRuntimeEvent,
    PaperRuntimeEventKind,
    PaperRuntimeEventPort,
    PaperRuntimeProjectionError,
)
from quantora_trade.execution.service import (
    PaperAdapterPort,
    PaperBrokerOrderResult,
    PaperBrokerPort,
    PaperExecutionInput,
    PaperExecutionInputPort,
)

__all__ = [
    "ORDER_TRANSITIONS",
    "DeterministicPaperAdapter",
    "DurablePaperAdapter",
    "Fill",
    "IdempotencyConflict",
    "InstrumentExecutionSnapshot",
    "InvalidOrderTransition",
    "OrderEvent",
    "OrderStatus",
    "PaperAdapterPort",
    "PaperBrokerOrderResult",
    "PaperBrokerPort",
    "PaperExecutionInput",
    "PaperExecutionInputPort",
    "PaperFillPolicy",
    "PaperOrder",
    "PaperOrderReconciliationRequired",
    "PaperOrderRepository",
    "PaperOrderRequest",
    "PaperQuote",
    "PaperRuntime",
    "PaperRuntimeEvent",
    "PaperRuntimeEventKind",
    "PaperRuntimeEventPort",
    "PaperRuntimeProjectionError",
    "request_hash",
    "require_transition",
]
