"""
Collections Domain Agent
Generates SQL queries for collections/delinquency analysis
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
from utils.schema_service import get_schema_service

logger = logging.getLogger(__name__)


class CollectionsAgent(BaseAgent):
    """
    Collections Domain Agent - SQL Generation Only
    
    Responsibilities:
    - Interpret user intent for collections domain
    - Generate optimized BigQuery SQL queries
    - Return structured SQL + metadata
    - NO execution - delegates to QueryExecutionAgent
    """

    sql_generator: LlmAgent
    model_config = {"arbitrary_types_allowed": True}
    
    # SQL generation instruction (schema injected dynamically)
    INSTRUCTION_TEMPLATE: ClassVar[str] = f"""You are a BigQuery SQL expert for L&T Finance Two-Wheeler **collections data**.

**YOUR TASK**: Generate ONLY the SQL query based on the specific Question provided below.
**CRITICAL**: IGNORE any previous conversation context (greetings, other topics). Focus ONLY on the SQL task.

## Table Information
Table: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`

## Actual Table Schema (from BigQuery) - SEMANTIC VIEW
This schema is grouped by business purpose. Read carefully.
{{SCHEMA_PLACEHOLDER}}

## Key SQL Generation Rules

### 1. ALWAYS Check MOB Filter for GNS/NNS Calculations
- GNS1, GNS2, GNS3, GNS4, GNS5, GNS6 → Use ONLY with corresponding MOB_ON_INSTL_START_DATE = 1, 2, 3, 4, 5, 6
- NNS1, NNS2, NNS3, NNS4, NNS5, NNS6 → Use ONLY with corresponding MOB_ON_INSTL_START_DATE = 1, 2, 3, 4, 5, 6
- EXAMPLE CORRECT: `WHERE MOB_ON_INSTL_START_DATE = 1 AND GNS1 = 'Y'`
- EXAMPLE WRONG: `WHERE GNS1 = 'Y'` (missing MOB filter causes wrong results!)

### 2. DPD Buckets MUST Use Exact Values
When filtering by SOM_DPD_BUCKET or DPD_BUCKET, ALWAYS use these EXACT values:
- '0' for current
- '1-2' for 1-2 DPD
- '3-5' for 3-5 DPD
- '6-10' for 6-10 DPD
- '11+' for 11+ DPD

### 3. EWS Band Grouping
When grouping EWS_Model_Output_Band values:
- 'Low' includes: R1, R2, R3
- 'Medium' includes: R4, R5, R6, R7
- 'High' includes: R8, R9, R10+

### 4. TIER Classification
When using TIER field, convert to standard tiers:
- Tier 1 = METRO
- Tier 2 = URBAN, SEMI_URBAN
- Tier 3 = RURAL

### 5. Date Filtering Best Practices
- Use BUSINESS_DATE for all time-based filtering (most reliable)
- Last 3 months: `WHERE BUSINESS_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)`
- Last 6 months: `WHERE BUSINESS_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH)`
- Current portfolio: `WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM table)`

### 6. Common Account Status Filters
- Exclude written-offs: `WHERE NPASTAGEID != 'WO_SALE' OR NPASTAGEID IS NULL`
- Regular accounts: `WHERE SOM_NPASTAGEID = 'REGULAR'`
- Zero DPD: `WHERE ZERODPD_FLAG = 'Y' OR SOM_DPD = 0`

## Output Format

Generate ONLY the SQL query wrapped in ```sql ``` tags. No explanations before or after.

Example:
Question: "What is the GNS1 count and percentage for last 3 months by branch?"
Response:
```sql
SELECT 
  BRANCHNAME,
  COUNT(DISTINCT CASE WHEN MOB_ON_INSTL_START_DATE = 1 AND GNS1 = 'Y' THEN AGREEMENTNO END) as gns1_count,
  COUNT(DISTINCT CASE WHEN MOB_ON_INSTL_START_DATE = 1 THEN AGREEMENTNO END) as total_mob1_accounts,
  ROUND(
    SAFE_DIVIDE(
      COUNT(DISTINCT CASE WHEN MOB_ON_INSTL_START_DATE = 1 AND GNS1 = 'Y' THEN AGREEMENTNO END),
      COUNT(DISTINCT CASE WHEN MOB_ON_INSTL_START_DATE = 1 THEN AGREEMENTNO END)
    ) * 100, 2
  ) as gns1_percentage
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
WHERE BUSINESS_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)
  AND MOB_ON_INSTL_START_DATE >= 1
GROUP BY BRANCHNAME
ORDER BY gns1_count DESC
```
"""
    
    def __init__(self):
        # Create LLM agent for SQL generation (uses GOOGLE_API_KEY from environment)
        model_name = settings.gemini_flash_model
        if not model_name:
            logger.error("Failed to initialize Gemini: GEMINI_FLASH_MODEL is not set in settings.")
            raise ValueError("GEMINI_FLASH_MODEL is not set in settings.")
        
        sql_generator_agent = LlmAgent(
            name="CollectionsSQLGenerator",
            model=model_name,
            instruction=self.INSTRUCTION_TEMPLATE,
            output_key="generated_sql"
        )

        super().__init__(
            name="CollectionsAgent",
            sql_generator=sql_generator_agent,
            sub_agents=[sql_generator_agent]
        )        
        logger.info(f"CollectionsAgent initialized with model: models/{model_name}")

    
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
            domain="COLLECTIONS",
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
            logger.error(f"Error in CollectionsAgent: {e}")
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
        Phase 1 Enhancement: Uses semantic schema with business rules
        """

        # 1. Fetch actual schema from BigQuery with business context
        try:
            schema_service = get_schema_service(settings.gcp_project_id)
            # Use new semantic schema with business rules (Phase 1)
            schema_info = schema_service.get_schema_with_business_rules(
                settings.bigquery_dataset,
                settings.collections_table
            )
            logger.info(f"Injecting semantic schema: {len(schema_info)} characters")
        except Exception as e:
            logger.warning(f"Schema fetch failed, using fallback: {e}")
            schema_info = "Schema unavailable - infer from business rules"
        
        # 2. Inject schema into LLM agent's instruction (update the sub-agent)
        original_instruction = self.sql_generator.instruction
        enhanced_instruction = original_instruction.replace(
            "{{SCHEMA_PLACEHOLDER}}",
            schema_info
        )
        self.sql_generator.instruction = enhanced_instruction
        
        # 3. Invoke the Sub-Agent
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
        finally:
            # Restore original instruction
            self.sql_generator.instruction = original_instruction

        # 4. Parse Result
        return self._parse_and_validate(accumulated_text, request.user_question)

    def _extract_last_message(self, ctx: InvocationContext) -> str:
        """Helper to get the actual latest user query."""
        if ctx.new_message and ctx.new_message.parts:
            return " ".join(p.text for p in ctx.new_message.parts if p.text)
        return str(ctx.current_input or "")

    def _parse_and_validate(self, llm_output: str, question: str) -> SQLGenerationResponse:
        """Logic to parse the LLM output into your structured response."""
        # ... (Reuse your regex logic here) ...
        sql_match = re.search(r'```sql\s+(.*?)\s+```', llm_output, re.DOTALL | re.IGNORECASE)
        sql_query = sql_match.group(1).strip() if sql_match else llm_output.replace("```sql", "").replace("```", "").strip()
        
        return SQLGenerationResponse(
            sql_query=sql_query,
            metadata=QueryMetadata(
                domain="COLLECTIONS",
                intent=question,
                generated_at=datetime.now(timezone.utc),
                filters_applied={}, # You can implement your filter extraction here
            ),
            explanation="Generated via CollectionsAgent",
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
        
        # DPD bucket
        dpd_match = re.search(r'(\d+)\+', question)
        if dpd_match:
            filters['dpd_bucket'] = f"{dpd_match.group(1)}+"
        
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
def create_collections_agent():
    """Create fresh CollectionsAgent instance"""
    return CollectionsAgent()
