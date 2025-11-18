"""
Contracts Package
Defines structured interfaces for inter-agent communication
"""

from .sql_contracts import (
    SQLGenerationRequest,
    SQLGenerationResponse,
    SQLExecutionRequest,
    SQLExecutionResponse,
    QueryMetadata,
    ExecutionStatus
)

from .routing_contracts import (
    RoutingRequest,
    RoutingResponse,
    DomainType
)

__all__ = [
    "SQLGenerationRequest",
    "SQLGenerationResponse",
    "SQLExecutionRequest",
    "SQLExecutionResponse",
    "QueryMetadata",
    "ExecutionStatus",
    "RoutingRequest",
    "RoutingResponse",
    "DomainType"
]
