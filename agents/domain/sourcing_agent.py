"""
Sourcing Domain Agent - SQL Generation Only
Generates SQL queries for loan application and approval analysis
Does NOT execute queries - delegates to QueryExecutionAgent
"""

from config.settings import settings
import logging
from datetime import datetime, timezone
from typing import ClassVar, Optional, AsyncGenerator
import uuid
import re

# Standard ADK imports
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.events import Event
from google.genai.types import Content, Part

# Imports for Context creation (Required for Option A)
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from contracts.sql_contracts import (
    SQLGenerationRequest,
    SQLGenerationResponse,
    QueryMetadata
)

logger = logging.getLogger(__name__)


class SourcingAgent(BaseAgent):
    """
    Sourcing Domain Agent - SQL Generation Only
    
    Responsibilities:
    - Interpret user intent for sourcing domain
    - Generate optimized BigQuery SQL queries
    - Return structured SQL + metadata
    - NO execution - delegates to QueryExecutionAgent
    """

    sql_generator: LlmAgent
    model_config = {"arbitrary_types_allowed": True}
    
    # SQL generation instruction
    INSTRUCTION_TEMPLATE: ClassVar[str] = f"""You are a BigQuery SQL expert for L&T Finance Two-Wheeler **sourcing/application data**.

**YOUR TASK**: Generate ONLY the SQL query based on the specific Question provided below.
**CRITICAL**: IGNORE any previous conversation context (greetings, other topics). Focus ONLY on the SQL task.

## Table Information
Table: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`

## Critical Business Rules

### 1. Date Filtering
- **Primary Date Field**: `LastModifiedDate` for ALL time-based filtering
- **"Last N months"**:
```sql
WHERE LastModifiedDate >= DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH), INTERVAL N MONTH)
  AND LastModifiedDate <= LAST_DAY(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))
```

### 2. Status Rules
- **Approved**: `BRE_Sanction_Result__c = 'ACCEPT'` or `ABND IS NOT NULL`
- **Rejected**: `REJECTED IS NOT NULL`

### 3. Application Counting
- Count: `COUNT(*)`
- Percentage: `ROUND(SAFE_DIVIDE(count, total) * 100, 2)`

### 4. Amount Conversions
- To Crores: `ROUND(SUM(amount) / 10000000, 2)`

### 5. Monthly Aggregation
- Group by: `DATE_TRUNC(LastModifiedDate, MONTH) as month`
- Format: `FORMAT_DATE('%b %Y', month)`

## Output Format

Generate ONLY the SQL query wrapped in ```sql ``` tags. No explanations before or after.

Example:
Question: "What is the approval rate for last 3 months?"
Response:
```sql
WITH monthly_data AS (
  SELECT 
    DATE_TRUNC(LastModifiedDate, MONTH) as month,
    COUNT(*) as total_applications,
    COUNT(CASE WHEN BRE_Sanction_Result__c = 'ACCEPT' OR ABND IS NOT NULL THEN 1 END) as approved_count
  FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`
  WHERE LastModifiedDate >= DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH), INTERVAL 3 MONTH)
    AND LastModifiedDate <= LAST_DAY(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))
  GROUP BY month
)
SELECT 
  FORMAT_DATE('%b %Y', month) as month,
  total_applications,
  approved_count,
  ROUND(SAFE_DIVIDE(approved_count, total_applications) * 100, 2) as approval_rate_pct
FROM monthly_data
ORDER BY month DESC
```
"""
    
    def __init__(self):
        # Create LLM agent for SQL generation (uses GOOGLE_API_KEY from environment)
        model_name = settings.gemini_flash_model
        if not model_name:
            logger.error("Failed to initialize Gemini: GEMINI_FLASH_MODEL is not set in settings.")
            raise ValueError("GEMINI_FLASH_MODEL is not set in settings.")
        
        sql_generator_agent = LlmAgent(
            name="SourcingSQLGenerator",
            model=model_name,
            instruction=self.INSTRUCTION_TEMPLATE,
            output_key="generated_sql"
        )

        super().__init__(
            name="SourcingAgent",
            sql_generator=sql_generator_agent,
            sub_agents=[sql_generator_agent]
        )        
        logger.info(f"SourcingAgent initialized with model: models/{model_name}")
    
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """
        Orchestration Logic.
        Delegates to self.generate_sql to ensure consistency with the Orchestrator.
        """
        # 1. Extract user question
        user_question = self._extract_question(ctx)
        logger.info(f"[{self.name}] Processing Question: {user_question}")
        
        # 2. Create Request
        request = SQLGenerationRequest(
            user_question=user_question,
            domain="SOURCING",
            conversation_context=ctx.session.state.get('conversation_history', []),
            session_id=ctx.session.id
        )
        
        try:
            # 3. Delegate to logic method (Pass context!)
            response = await self.generate_sql(request, context=ctx)
            
            # 4. Store response in session state
            ctx.session.state['sql_generation_response'] = response.model_dump()
            
            # 5. Yield event with SQL query
            yield Event(
                content=Content(parts=[Part(text=response.sql_query)]),
                author=self.name
            )
        except Exception as e:
            logger.error(f"Error in SourcingAgent: {e}")
            yield Event(
                content=Content(parts=[Part(text=f"Error generating SQL: {str(e)}")]),
                author=self.name
            )
    
    async def generate_sql(
            self, 
            request: SQLGenerationRequest, 
            context: Optional[InvocationContext] = None) -> SQLGenerationResponse:
        """
        Generates SQL by invoking the sub-agent via run_async.
        Compatible with Orchestrator calls and Unit Tests.
        """
        
        # 3. Invoke the Sub-Agent
        # We must iterate over the generator to allow the sub-agent to process
        accumulated_text = ""
        try:
            async for event in self.sql_generator.run_async(context):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            accumulated_text += part.text
        except Exception as e:
            logger.error(f"Sub-agent execution failed: {e}")
            raise

        # 4. Parse Result
        return self._parse_and_validate(accumulated_text, request.user_question)

    def _extract_last_message(self, ctx: InvocationContext) -> str:
        """Helper to get the actual latest user query."""
        if ctx.new_message and ctx.new_message.parts:
            return " ".join(p.text for p in ctx.new_message.parts if p.text)
        return str(ctx.current_input or "")

    def _parse_and_validate(self, llm_output: str, question: str) -> SQLGenerationResponse:
        """Logic to parse the LLM output into your structured response."""
        sql_match = re.search(r'```sql\s+(.*?)\s+```', llm_output, re.DOTALL | re.IGNORECASE)
        sql_query = sql_match.group(1).strip() if sql_match else llm_output.replace("```sql", "").replace("```", "").strip()
        
        return SQLGenerationResponse(
            sql_query=sql_query,
            metadata=QueryMetadata(
                domain="SOURCING",
                intent=question,
                generated_at=datetime.now(timezone.utc),
                filters_applied={},
            ),
            explanation="Generated via SourcingAgent",
            expected_columns=[], 
            formatting_hints={}
        )
    
    def _extract_question(self, ctx) -> str:
        """Extract user question from context"""
        if hasattr(ctx, 'new_message') and ctx.new_message and hasattr(ctx.new_message, 'parts'):
            return " ".join(
                part.text or "" for part in ctx.new_message.parts
                if hasattr(part, 'text') and part.text
            )
        elif hasattr(ctx, 'current_input') and ctx.current_input:
            return str(ctx.current_input)
        return ""
    
    def _extract_filters(self, question: str) -> dict:
        """Extract filters from question"""
        import re
        filters = {}
        
        # Time range
        time_match = re.search(r'last\s+(\d+)\s+(month|year|day)', question, re.IGNORECASE)
        if time_match:
            filters['time_range'] = f"last_{time_match.group(1)}_{time_match.group(2)}s"
        
        return filters
    
    def _infer_columns(self, sql_query: str) -> list:
        """Infer expected column names from SQL query"""
        import re
        
        # Extract column aliases from SELECT clause
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql_query, re.DOTALL | re.IGNORECASE)
        if not select_match:
            return []
        
        select_clause = select_match.group(1)
        
        # Extract column names (simple heuristic)
        columns = []
        for line in select_clause.split(','):
            # Look for "AS column_name" pattern
            as_match = re.search(r'\s+as\s+(\w+)', line, re.IGNORECASE)
            if as_match:
                columns.append(as_match.group(1))
        
        return columns


# Factory function
def create_sourcing_agent():
    """Create fresh SourcingAgent instance"""
    return SourcingAgent()
