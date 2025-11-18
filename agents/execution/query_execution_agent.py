"""
Query Execution Agent
The ONLY component authorized to execute SQL against BigQuery.
Handles execution pipeline: safety validation -> dry run -> execution -> error handling
"""

import logging
from typing import Optional
from google.cloud import bigquery
from google.auth import default
from google.api_core import retry
from datetime import datetime
import time

from contracts.sql_contracts import (
    SQLExecutionRequest,
    SQLExecutionResponse,
    ExecutionStatus,
    QueryMetadata
)
from utils.sql_safety_validator import SQLSafetyValidator

logger = logging.getLogger(__name__)


class QueryExecutionAgent:
    """
    Centralized Query Execution Agent
    
    Responsibilities:
    - Validate SQL safety (block destructive queries)
    - Dry-run cost estimation
    - Execute queries with timeout and retry
    - Track execution metrics (time, bytes, cost)
    - Return structured responses with error handling
    - Query logging and audit trail
    """
    
    # BigQuery pricing: $6.25 per TB processed (as of 2024)
    COST_PER_TB_USD = 6.25
    BYTES_PER_TB = 1_099_511_627_776  # 1 TB in bytes
    
    def __init__(self, project_id: str, location: str = "asia-south1"):
        """
        Initialize Query Execution Agent
        
        Args:
            project_id: GCP project ID
            location: BigQuery dataset location
        """
        self.project_id = project_id
        self.location = location
        
        # Initialize BigQuery client
        credentials, _ = default()
        self.client = bigquery.Client(
            credentials=credentials,
            project=project_id,
            location=location
        )
        
        # Initialize safety validator
        self.safety_validator = SQLSafetyValidator()
        
        logger.info(f"QueryExecutionAgent initialized for project: {project_id}")
    
    def execute(self, request: SQLExecutionRequest) -> SQLExecutionResponse:
        """
        Execute SQL query with full safety and monitoring pipeline
        
        Pipeline:
        1. Safety validation
        2. Dry-run cost estimation
        3. Query execution with retry
        4. Error handling
        5. Structured response
        
        Args:
            request: SQLExecutionRequest with query and parameters
            
        Returns:
            SQLExecutionResponse with results or error details
        """
        start_time = time.time()
        
        # Step 1: Safety Validation
        is_safe, blocked_reason = self.safety_validator.validate(request.sql_query)
        if not is_safe:
            logger.warning(f"Query blocked: {blocked_reason}")
            logger.warning(f"Blocked query: {request.sql_query[:200]}...")
            return SQLExecutionResponse(
                status=ExecutionStatus.BLOCKED,
                blocked_reason=blocked_reason,
                error_message=f"Query blocked by safety validator: {blocked_reason}"
            )
        
        logger.info(f"Query passed safety validation for domain: {request.metadata.domain}")
        
        # Step 2: Dry-run cost estimation (if requested or always before execution)
        estimated_bytes, dry_run_error = self._dry_run(request.sql_query)
        
        if dry_run_error:
            logger.error(f"Dry-run failed: {dry_run_error}")
            return SQLExecutionResponse(
                status=ExecutionStatus.FAILED,
                error_message=f"Query validation failed: {dry_run_error}"
            )
        
        estimated_cost = self._calculate_cost(estimated_bytes)
        logger.info(f"Estimated cost: ${estimated_cost:.4f} ({estimated_bytes:,} bytes)")
        
        # Return dry-run response if requested
        if request.dry_run:
            return SQLExecutionResponse(
                status=ExecutionStatus.DRY_RUN,
                bytes_processed=estimated_bytes,
                estimated_cost_usd=estimated_cost
            )
        
        # Step 3: Execute query with timeout and retry
        try:
            execution_result = self._execute_with_retry(
                request.sql_query,
                timeout_seconds=request.timeout_seconds,
                max_results=request.max_results
            )
            
            execution_time = (time.time() - start_time) * 1000  # Convert to milliseconds
            
            logger.info(
                f"Query executed successfully: {execution_result['row_count']} rows, "
                f"{execution_time:.2f}ms, {execution_result['bytes_processed']:,} bytes"
            )
            
            return SQLExecutionResponse(
                status=ExecutionStatus.SUCCESS,
                rows=execution_result['rows'],
                row_count=execution_result['row_count'],
                columns=execution_result['columns'],
                execution_time_ms=execution_time,
                bytes_processed=execution_result['bytes_processed'],
                estimated_cost_usd=self._calculate_cost(execution_result['bytes_processed']),
                query_id=execution_result['query_id'],
                executed_at=datetime.utcnow()
            )
            
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            error_msg = str(e)
            
            # Determine if it's a timeout error
            if 'timeout' in error_msg.lower() or 'deadline' in error_msg.lower():
                status = ExecutionStatus.TIMEOUT
                logger.error(f"Query timeout after {execution_time:.2f}ms: {error_msg}")
            else:
                status = ExecutionStatus.FAILED
                logger.error(f"Query execution failed: {error_msg}", exc_info=True)
            
            return SQLExecutionResponse(
                status=status,
                error_message=error_msg,
                execution_time_ms=execution_time
            )
    
    def _dry_run(self, sql_query: str) -> tuple[Optional[int], Optional[str]]:
        """
        Perform dry-run to estimate bytes processed
        
        Args:
            sql_query: SQL query to dry-run
            
        Returns:
            Tuple of (estimated_bytes, error_message)
        """
        try:
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
            query_job = self.client.query(sql_query, job_config=job_config)
            
            # Dry-run doesn't actually execute, just validates and estimates
            estimated_bytes = query_job.total_bytes_processed
            return estimated_bytes, None
            
        except Exception as e:
            return None, str(e)
    
    def _execute_with_retry(
        self,
        sql_query: str,
        timeout_seconds: int,
        max_results: int
    ) -> dict:
        """
        Execute query with retry policy
        
        Args:
            sql_query: SQL query to execute
            timeout_seconds: Query timeout
            max_results: Maximum rows to return
            
        Returns:
            Dictionary with execution results
        """
        job_config = bigquery.QueryJobConfig(
            use_query_cache=True,
            use_legacy_sql=False
        )
        
        # Execute query with retry on transient errors
        @retry.Retry(
            predicate=retry.if_transient_error,
            initial=1.0,  # 1 second initial delay
            maximum=10.0,  # 10 seconds max delay
            multiplier=2.0,  # Exponential backoff
            deadline=timeout_seconds  # Total timeout
        )
        def _run_query():
            return self.client.query(sql_query, job_config=job_config)
        
        query_job = _run_query()
        
        # Wait for results with timeout
        results = query_job.result(timeout=timeout_seconds, max_results=max_results)
        
        # Extract results
        rows = []
        columns = [field.name for field in results.schema] if results.schema else []
        
        for row in results:
            row_dict = {col: row[col] for col in columns}
            rows.append(row_dict)
        
        return {
            'rows': rows,
            'row_count': len(rows),
            'columns': columns,
            'bytes_processed': query_job.total_bytes_processed or 0,
            'query_id': query_job.job_id
        }
    
    def _calculate_cost(self, bytes_processed: int) -> float:
        """
        Calculate estimated cost based on bytes processed
        
        Args:
            bytes_processed: Number of bytes processed
            
        Returns:
            Estimated cost in USD
        """
        if not bytes_processed:
            return 0.0
        
        # BigQuery free tier: First 1 TB per month is free
        # For this calculation, we'll show the full cost
        cost = (bytes_processed / self.BYTES_PER_TB) * self.COST_PER_TB_USD
        return round(cost, 6)  # Round to 6 decimal places
    
    def get_query_history(self, limit: int = 10) -> list:
        """
        Get recent query history for audit trail
        
        Args:
            limit: Number of recent queries to retrieve
            
        Returns:
            List of recent query jobs
        """
        jobs = self.client.list_jobs(max_results=limit, all_users=False)
        
        history = []
        for job in jobs:
            if isinstance(job, bigquery.QueryJob):
                history.append({
                    'job_id': job.job_id,
                    'query': job.query,
                    'created': job.created.isoformat() if job.created else None,
                    'state': job.state,
                    'bytes_processed': job.total_bytes_processed,
                    'error': job.errors[0]['message'] if job.errors else None
                })
        
        return history
    
    def close(self):
        """Close BigQuery client connection"""
        self.client.close()
        logger.info("QueryExecutionAgent client closed")


# Example usage
if __name__ == "__main__":
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from config.settings import settings
    
    # Initialize agent
    agent = QueryExecutionAgent(
        project_id=settings.gcp_project_id,
        location=settings.bigquery_location
    )
    
    # Test query
    test_request = SQLExecutionRequest(
        sql_query="SELECT COUNT(*) as count FROM `project.dataset.table` LIMIT 10",
        project_id=settings.gcp_project_id,
        metadata=QueryMetadata(
            domain="COLLECTIONS",
            intent="Test query",
            generated_at=datetime.utcnow()
        ),
        dry_run=True  # Safe to test
    )
    
    response = agent.execute(test_request)
    print(f"Status: {response.status}")
    print(f"Estimated cost: ${response.estimated_cost_usd}")
    
    agent.close()
