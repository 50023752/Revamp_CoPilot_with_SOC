"""
Query Execution Agent
The ONLY component authorized to execute SQL against BigQuery.
Handles execution pipeline: safety validation -> dry run -> execution -> error handling
Includes: Reflection (Self-Healing) Capabilities
"""

import logging
import asyncio
import time
import re
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime

# Google Cloud Imports
from google.cloud import bigquery
from google.auth import default
from google.api_core import retry, exceptions

# ADK Imports for Reflection
from google.adk.agents import LlmAgent
from config.settings import settings

# Project Imports
from contracts.sql_contracts import (
    SQLExecutionRequest,
    SQLExecutionResponse,
    ExecutionStatus,
    QueryMetadata
)
from utils.sql_safety_validator import SQLSafetyValidator
from utils.json_logger import get_json_logger

# AST Parsing
import sqlglot
from sqlglot import exp

logger = get_json_logger(__name__)


class SQLReflectionAgent:
    """
    Micro-agent responsible for fixing broken SQL based on BigQuery error messages.
    """
    REPAIR_PROMPT = """
    You are a BigQuery SQL Debugger.
    Your task is to FIX a broken SQL query based on the specific error message provided.
    
    ### INPUT DATA
    1. **Broken SQL**: 
    ```sql
    {broken_sql}
    ```
    
    2. **Error Message**: 
    "{error_message}"
    
    ### COMMON FIXES TO APPLY
    1. **Datatype Mismatch**: Use `CAST(col AS TYPE)` or `SAFE_CAST()` or `DATE()` conversion.
    2. **Group By**: Ensure non-aggregated SELECT columns appear in GROUP BY.
    3. **Syntax**: Fix trailing commas, typos, or invalid keywords (e.g., `AS` in GROUP BY).
    4. **Aliases**: Ensure aliases are not used in WHERE clauses (use original expression).
    
    ### OUTPUT
    Return ONLY the corrected SQL query inside triple backticks (```sql ... ```). 
    NO explanations.
    """

    def __init__(self, model_name: str):
        self.llm = LlmAgent(
            name="SQLRepairBot",
            model=model_name,
            instruction=self.REPAIR_PROMPT
        )

    def fix_sql_sync(self, broken_sql: str, error_message: str) -> str:
        """Synchronous wrapper to call async agent"""
        try:
            return asyncio.run(self._fix_sql_async(broken_sql, error_message))
        except RuntimeError:
            # Handle cases where loop is already running (Streamlit/Jupyter)
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(self._fix_sql_async(broken_sql, error_message))

    async def _fix_sql_async(self, broken_sql: str, error_message: str) -> str:
        # Update instruction dynamically
        instruction = self.REPAIR_PROMPT.replace("{broken_sql}", broken_sql)
        instruction = instruction.replace("{error_message}", error_message)
        self.llm.instruction = instruction

        fixed_sql = ""
        try:
            async for event in self.llm.run_async(None):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        fixed_sql += part.text or ""
        except Exception as e:
            logger.error(f"Reflection Agent failed: {e}")
            return broken_sql # Return original if repair fails

        # Extract SQL from code blocks
        match = re.search(r'```sql\s+(.*?)\s+```', fixed_sql, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return fixed_sql.replace("```sql", "").replace("```", "").strip()


class QueryExecutionAgent:
    """
    Centralized Query Execution Agent
    
    Responsibilities:
    - Validate SQL safety (block destructive queries)
    - Dry-run cost estimation
    - Execute queries with timeout and retry
    - Self-Healing (Reflection) on syntax errors
    - Track execution metrics
    """
    
    # BigQuery pricing: $6.25 per TB processed (as of 2024)
    COST_PER_TB_USD = 6.25
    BYTES_PER_TB = 1_099_511_627_776  # 1 TB in bytes
    MAX_RETRIES = 3 # Reflection loop cap
    
    def __init__(self, project_id: str, location: str = "asia-south1"):
        """
        Initialize Query Execution Agent
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

        # Initialize Reflection Agent
        # Default to flash model for speed/cost
        model_name = getattr(settings, 'gemini_flash_model', 'gemini-1.5-flash')
        self.reflection_agent = SQLReflectionAgent(model_name=model_name)
        
        logger.info(f"QueryExecutionAgent initialized for project: {project_id}")
    
    def execute(self, request: SQLExecutionRequest) -> SQLExecutionResponse:
        """
        Execute SQL query with full safety, monitoring, and SELF-HEALING pipeline.
        """
        start_time = time.time()
        current_sql = request.sql_query
        last_error = None

        # --- SELF-HEALING LOOP (Max 3 Attempts) ---
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                is_retry = attempt > 0
                if is_retry:
                    logger.info(f"🔄 Reflection Attempt {attempt}/{self.MAX_RETRIES}")

                # Step 1: Safety Validation (AST + Regex)
                # We validate on EVERY attempt to ensure the "Fix" isn't malicious
                self._validate_safety_strict(current_sql)

                logger.info(f"Query passed safety validation for domain: {request.metadata.domain}")
                
                # Step 2: Dry-run cost estimation
                # We use a dry_run that RAISES errors so we can catch them in this loop
                estimated_bytes = self._dry_run_raise(current_sql)
                
                estimated_cost = self._calculate_cost(estimated_bytes)
                logger.info(f"Estimated cost: ${estimated_cost:.4f} ({estimated_bytes:,} bytes)")
                
                # Return dry-run response if requested (only on first attempt generally)
                if request.dry_run:
                    return SQLExecutionResponse(
                        status=ExecutionStatus.DRY_RUN,
                        rows=[],
                        row_count=0,
                        columns=[],
                        execution_time_ms=(time.time() - start_time) * 1000,
                        bytes_processed=estimated_bytes,
                        estimated_cost_usd=estimated_cost,
                        error_message=None,
                        blocked_reason=None,
                        query_id=None
                    )
                
                # Step 3: Execute query with retry (Network/Transient errors handled internally)
                execution_result = self._execute_with_retry(
                    current_sql,
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
                    error_message=None,
                    blocked_reason=None,
                    query_id=execution_result['query_id'],
                    executed_at=datetime.utcnow()
                )
            
            except (exceptions.BadRequest, ValueError) as logic_error:
                # 400 Errors (Syntax, Types) or Safety Block
                error_msg = str(logic_error)
                last_error = error_msg

                # BLOCKER: Safety violation -> Fail immediately (No Retry)
                if "Blocked by safety" in error_msg or "Destructive SQL" in error_msg:
                    logger.warning(f"🛑 Blocked Unsafe Query: {error_msg}")
                    return SQLExecutionResponse(
                        status=ExecutionStatus.BLOCKED,
                        rows=[],
                        row_count=0,
                        columns=[],
                        execution_time_ms=(time.time() - start_time) * 1000,
                        bytes_processed=None,
                        estimated_cost_usd=None,
                        error_message=error_msg,
                        blocked_reason="Safety Violation",
                        query_id=None
                    )

                # STOPPER: Max retries reached
                if attempt == self.MAX_RETRIES:
                    logger.error(f"❌ Max retries reached. Final error: {error_msg}")
                    break

                # STOPPER: If error is not about Syntax/Types, reflection won't help
                non_fixable_patterns = ["400", "Syntax error", "No matching signature", "Type mismatch", "Invalid cast"]
                will_attempt_reflection = any(pat in error_msg for pat in ["400", "Syntax error", "No matching signature"])
                if not will_attempt_reflection:
                    logger.warning(f"Non-fixable error detected ({error_msg}). Skipping reflection.")
                    break

                # ACTION: Reflection
                logger.warning(f"⚠️ Query failed (Attempt {attempt}). Reflection decision: will_attempt_reflection={will_attempt_reflection}")
                logger.warning(f"Error: {error_msg}")
                
                try:
                    # Call the Reflection Agent to fix the SQL
                    logger.info("Invoking Reflection Agent to attempt to repair SQL")
                    current_sql = self.reflection_agent.fix_sql_sync(current_sql, error_msg)
                    logger.info(f"🔧 Reflection Agent generated new SQL (len={len(current_sql)})")
                except Exception as fix_err:
                    logger.error(f"Reflection Agent crashed: {fix_err}")
                    break 

            except Exception as e:
                # 500 Errors or Critical failures
                execution_time = (time.time() - start_time) * 1000
                logger.error(f"Critical execution failure: {e}", exc_info=True)
                return SQLExecutionResponse(
                    status=ExecutionStatus.FAILED,
                    rows=[],
                    row_count=0,
                    columns=[],
                    execution_time_ms=execution_time,
                    bytes_processed=None,
                    estimated_cost_usd=None,
                    error_message=str(e),
                    blocked_reason=None,
                    query_id=None
                )
        
        # Final Failure Return
        return SQLExecutionResponse(
            status=ExecutionStatus.FAILED,
            rows=[],
            row_count=0,
            columns=[],
            execution_time_ms=(time.time() - start_time) * 1000,
            bytes_processed=None,
            estimated_cost_usd=None,
            error_message=f"Execution failed after {self.MAX_RETRIES} attempts. Final Error: {last_error}",
            blocked_reason=None,
            query_id=None
        )

    def _validate_safety_strict(self, sql: str):
        """
        Helper to raise ValueError if SQL is unsafe.
        Uses AST first, then Regex fallback.
        """
        # 1. AST Scan (sqlglot)
        try:
            parsed = sqlglot.parse_one(sql)
            forbidden = (exp.Drop, exp.Delete, exp.Update, exp.Alter, exp.Truncate, exp.Insert, exp.Merge, exp.Create, exp.Grant, exp.Revoke)
            if any(node for node in parsed.walk() if isinstance(node, forbidden)):
                 raise ValueError("Destructive SQL detected via AST scan")
        except Exception as e:
             # If AST parsing fails, we log but rely on Regex (Parsing failure might just be complex SQL)
             logger.debug(f"AST Scan skipped due to parse error: {e}")

        # 2. Regex Scan (Legacy Validator)
        is_safe, blocked_reason = self.safety_validator.validate(sql)
        if not is_safe:
            raise ValueError(f"Blocked by safety validator: {blocked_reason}")

    def _dry_run_raise(self, sql_query: str) -> int:
        """
        Perform dry-run. Raises exception on error (unlike original _dry_run).
        """
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        query_job = self.client.query(sql_query, job_config=job_config)
        return query_job.total_bytes_processed

    def _execute_with_retry(
        self,
        sql_query: str,
        timeout_seconds: int,
        max_results: int
    ) -> dict:
        """
        Execute query with retry policy on TRANSIENT errors.
        """
        job_config = bigquery.QueryJobConfig(
            use_query_cache=True,
            use_legacy_sql=False
        )
        
        # Execute query with retry on transient errors (5xx, rate limits)
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


# Example usage and testing block (Preserved)
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
            generated_at=datetime.utcnow(),
            estimated_cost_bytes=None,
            aggregation_level=None,
            time_range=None
        ),
        dry_run=True  # Safe to test
    )
    
    response = agent.execute(test_request)
    logger.info(f"Status: {response.status}")
    logger.info(f"Estimated cost: ${response.estimated_cost_usd}")
    
    agent.close()