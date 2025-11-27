"""
SQL Contracts for Domain Agent <-> Execution Agent Communication
Defines structured request/response formats for SQL generation and execution
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime


class ExecutionStatus(str, Enum):
    """Execution status enumeration"""
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"  # Blocked by safety validator
    DRY_RUN = "dry_run"


class QueryMetadata(BaseModel):
    """Metadata about the SQL query"""
    domain: str = Field(..., description="Domain that generated the query (SOURCING/COLLECTIONS/DISBURSAL)")
    intent: str = Field(..., description="User's original intent/question")
    generated_at: datetime = Field(default_factory=datetime.utcnow, description="Timestamp when query was generated")
    estimated_cost_bytes: Optional[int] = Field(None, description="Estimated bytes to be processed (for cost estimation)")
    filters_applied: Dict[str, Any] = Field(default_factory=dict, description="Filters extracted from user query")
    aggregation_level: Optional[str] = Field(None, description="Monthly/Daily/Yearly aggregation")
    time_range: Optional[Dict[str, str]] = Field(None, description="Start and end dates for the query")

class SQLGenerationRequest(BaseModel):
    """Request contract for Domain Agents to generate SQL"""
    user_question: str = Field(..., description="Natural language question from user")
    domain: str = Field(..., description="Target domain (SOURCING/COLLECTIONS/DISBURSAL)")
    conversation_context: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="Previous conversation history for follow-up questions"
    )
    session_id: str = Field(..., description="Session identifier for tracking")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_question": "What is the 0+ dpd count and percentage for last 3 months?",
                "domain": "COLLECTIONS",
                "conversation_context": [],
                "session_id": "session_20251117_123456"
            }
        }


class SQLGenerationResponse(BaseModel):
    """Response contract from Domain Agents with generated SQL"""
    sql_query: str = Field(..., description="Generated BigQuery SQL query")
    metadata: QueryMetadata = Field(..., description="Query metadata and context")
    explanation: Optional[str] = Field(None, description="Natural language explanation of what the query does")
    expected_columns: List[str] = Field(default_factory=list, description="Expected column names in result set")
    formatting_hints: Dict[str, str] = Field(
        default_factory=dict,
        description="Hints for formatting results (e.g., {'amount': 'crores', 'percentage': '2_decimal'})"
    )
    
    @field_validator('sql_query')
    @classmethod
    def validate_sql_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("SQL query cannot be empty")
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "sql_query": "SELECT month, COUNT(*) as count FROM table GROUP BY month",
                "metadata": {
                    "domain": "COLLECTIONS",
                    "intent": "Get monthly count",
                    "generated_at": "2025-11-17T12:00:00",
                    "filters_applied": {"time_range": "last_3_months"}
                },
                "explanation": "This query aggregates records by month",
                "expected_columns": ["month", "count"],
                "formatting_hints": {"count": "comma_separated"}
            }
        }


class SQLExecutionRequest(BaseModel):
    """Request contract for Query Execution Agent"""
    sql_query: str = Field(..., description="SQL query to execute")
    project_id: str = Field(..., description="GCP project ID for BigQuery")
    metadata: QueryMetadata = Field(..., description="Query metadata from generation phase")
    dry_run: bool = Field(default=False, description="If True, only estimate cost without executing")
    max_results: int = Field(default=1000, description="Maximum number of rows to return")
    timeout_seconds: int = Field(default=300, description="Query timeout in seconds")
    
    class Config:
        json_schema_extra = {
            "example": {
                "sql_query": "SELECT * FROM table LIMIT 100",
                "project_id": "my-gcp-project",
                "metadata": {
                    "domain": "COLLECTIONS",
                    "intent": "Get data",
                    "generated_at": "2025-11-17T12:00:00"
                },
                "dry_run": False,
                "max_results": 1000,
                "timeout_seconds": 300
            }
        }


class SQLExecutionResponse(BaseModel):
    """Response contract from Query Execution Agent"""
    status: ExecutionStatus = Field(..., description="Execution status")
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="Query result rows")
    row_count: int = Field(default=0, description="Total number of rows returned")
    columns: List[str] = Field(default_factory=list, description="Column names in result set")
    execution_time_ms: Optional[float] = Field(None, description="Query execution time in milliseconds")
    bytes_processed: Optional[int] = Field(None, description="Actual bytes processed by BigQuery")
    estimated_cost_usd: Optional[float] = Field(None, description="Estimated cost in USD")
    error_message: Optional[str] = Field(None, description="Error message if execution failed")
    blocked_reason: Optional[str] = Field(None, description="Reason query was blocked (if status=BLOCKED)")
    query_id: Optional[str] = Field(None, description="BigQuery job ID for tracking")
    executed_at: datetime = Field(default_factory=datetime.utcnow, description="Execution timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "rows": [{"month": "2025-11", "count": 1000}],
                "row_count": 1,
                "columns": ["month", "count"],
                "execution_time_ms": 1234.56,
                "bytes_processed": 1048576,
                "estimated_cost_usd": 0.005,
                "query_id": "bquxjob_12345",
                "executed_at": "2025-11-17T12:00:00"
            }
        }
