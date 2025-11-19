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
import tempfile
import os

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
- '0' for 0 dpd
- '1' for 1-30 DPD
- '2' for 31-60 DPD
- '3' for 61-90 DPD
- '4' for 91-120 DPD
- '5' for 121+ DPD

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
DO NOT name columns starting with numbers or special characters.
    DO NOT produce any acknowledgements, confirmations, or conversational text. If you understand, do NOT reply 'Okay' or 'I am ready'. Reply only with the SQL block.

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

Question: "What is the 0+ dpd count and percentage for last 6 months?"
Response:
```sql
SELECT
  DATE_TRUNC(BUSINESS_DATE, MONTH) AS month,
  COUNT(DISTINCT AGREEMENTNO) AS total_accounts,
  COUNT(DISTINCT CASE WHEN SOM_DPD > 0 THEN AGREEMENTNO END) AS col_0_plus_dpd_accounts,
  ROUND(SAFE_DIVIDE(COUNT(DISTINCT CASE WHEN SOM_DPD > 0 THEN AGREEMENTNO END), COUNT(DISTINCT AGREEMENTNO)) * 100, 2) AS col_0_plus_dpd_percentage
FROM
  `analytics-datapipeline-prod.aiml_cj_nostd_mart.TW_NOSTD_MART_HIST`
WHERE
  BUSINESS_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH)
  AND BUSINESS_DATE <= CURRENT_DATE()
GROUP BY
  month
ORDER BY
  month DESC;
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
        # First, if we have an InvocationContext with a session, populate the
        # session state so ADK's instructions preprocessor can inject the
        # `SCHEMA_PLACEHOLDER` safely without raising KeyError.
        if context is not None and hasattr(context, 'session') and hasattr(context.session, 'state'):
            try:
                context.session.state['SCHEMA_PLACEHOLDER'] = schema_info
            except Exception:
                # Non-fatal: fall back to direct string replacement below
                logger.debug("Failed to set session state SCHEMA_PLACEHOLDER; will use fallback replacement")

        # Fallback: replace both double-brace and single-brace placeholders
        # directly in the instruction. Handle cases where the agent's
        # `instruction` may be a callable/provider rather than a plain string.
        try:
            if isinstance(original_instruction, str):
                instr_text = original_instruction
            else:
                # Avoid invoking callables that may require arguments.
                instr_text = str(original_instruction)
        except Exception:
            instr_text = str(original_instruction)

        if "{{SCHEMA_PLACEHOLDER}}" in instr_text:
            instr_text = instr_text.replace("{{SCHEMA_PLACEHOLDER}}", schema_info)
        if "{SCHEMA_PLACEHOLDER}" in instr_text:
            instr_text = instr_text.replace("{SCHEMA_PLACEHOLDER}", schema_info)

        # Add explicit user question and a brief last Q/A snippet (if available)
        user_q = request.user_question if hasattr(request, 'user_question') else ''
        last_qa = ''
        try:
            history = context.session.state.get('conversation_history', []) if context is not None and hasattr(context, 'session') else []
            if history:
                last = history[-1]
                last_qa = f"Previous Q: {last.get('question', '')} -- Previous A: {last.get('answer', '')}"
        except Exception:
            last_qa = ''

        enhanced_instruction = instr_text + "\n\n" + f"USER_QUESTION: {user_q}\nLAST_QA_SNIPPET: {last_qa}\n"
        self.sql_generator.instruction = enhanced_instruction
        # Debug: persist the exact instruction sent (truncated in logs)
        try:
            instr_snippet = (enhanced_instruction[:2000] + '...') if len(enhanced_instruction) > 2000 else enhanced_instruction
            logger.debug(f"Instruction sent to LLM (first 2000 chars): {instr_snippet}")
            tmp_dir = tempfile.gettempdir()
            instr_path = os.path.join(tmp_dir, f"llm_instruction_{uuid.uuid4().hex}.txt")
            with open(instr_path, 'w', encoding='utf-8') as f:
                f.write(enhanced_instruction)
            logger.info(f"Full instruction saved to: {instr_path}")
        except Exception:
            logger.debug("Failed to save instruction to temp file")
        
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
            # Save any partial LLM output for debugging
            try:
                tmp_dir = tempfile.gettempdir()
                resp_path = os.path.join(tmp_dir, f"llm_response_partial_{uuid.uuid4().hex}.txt")
                with open(resp_path, 'w', encoding='utf-8') as f:
                    f.write(accumulated_text)
                logger.info(f"Partial LLM response saved to: {resp_path}")
                try:
                    print('\n' + '='*40)
                    print('PARTIAL LLM RESPONSE START')
                    print('='*40)
                    print(accumulated_text)
                    print('='*40)
                    print('PARTIAL LLM RESPONSE END')
                    print('='*40 + '\n')
                except Exception:
                    logger.debug('Failed to print partial LLM response to stdout')
            except Exception:
                logger.debug("Failed to save partial LLM response")
            raise
        finally:
            # Restore original instruction
            self.sql_generator.instruction = original_instruction
            # Clear injected schema from session state to avoid persistence
            try:
                if context is not None and hasattr(context, 'session') and hasattr(context.session, 'state'):
                    if 'SCHEMA_PLACEHOLDER' in context.session.state:
                        del context.session.state['SCHEMA_PLACEHOLDER']
            except Exception:
                logger.debug("Failed to clear SCHEMA_PLACEHOLDER from session state")

        # 4. Parse Result
        # Debug: save full raw LLM output for inspection before parsing
        try:
            tmp_dir = tempfile.gettempdir()
            resp_path = os.path.join(tmp_dir, f"llm_response_full_{uuid.uuid4().hex}.txt")
            with open(resp_path, 'w', encoding='utf-8') as f:
                f.write(accumulated_text)
            logger.info(f"Full LLM response saved to: {resp_path}")
            if len(accumulated_text) > 2000:
                logger.debug(f"Raw LLM response (first 2000 chars): {accumulated_text[:2000]}")
                try:
                    print('\n' + '='*80)
                    print('RAW LLM RESPONSE START')
                    print('='*80)
                    print(accumulated_text)
                    print('='*80)
                    print('RAW LLM RESPONSE END')
                    print('='*80 + '\n')
                except Exception:
                    logger.debug('Failed to print full LLM response to stdout')
        except Exception:
            logger.debug("Failed to save full LLM response to temp file")

        parsed = self._parse_and_validate(accumulated_text, request.user_question)
        # Post-parse check: ensure SQL starts with SELECT or WITH
        sql_head = parsed.sql_query.strip()[:10].upper()
        if not (sql_head.startswith('SELECT') or sql_head.startswith('WITH')):
            logger.warning(f"Parsed SQL does not start with SELECT/WITH. Head: {sql_head}")
            try:
                logger.warning(f"Inspect LLM response: {resp_path}")
            except Exception:
                logger.warning("Please inspect the raw LLM response saved above to see why the model returned non-SQL text.")

        return parsed

    def _extract_last_message(self, ctx: InvocationContext) -> str:
        """Helper to get the actual latest user query."""
        if ctx.new_message and ctx.new_message.parts:
            return " ".join(p.text for p in ctx.new_message.parts if p.text)
        return str(ctx.current_input or "")

    def _parse_and_validate(self, llm_output: str, question: str) -> SQLGenerationResponse:
        """Logic to parse the LLM output into your structured response."""
        # Prefer code-fenced SQL blocks. If not found, try to extract the
        # first statement that starts with SELECT or WITH as a fallback.
        sql_match = re.search(r'```sql\s+(.*?)\s+```', llm_output, re.DOTALL | re.IGNORECASE)
        if sql_match:
            sql_query = sql_match.group(1).strip()
        else:
            # Remove common acknowledgment lines and try to find SQL lines
            cleaned = llm_output.strip()
            # Find first occurrence of a line starting with SELECT/WITH
            lines = cleaned.splitlines()
            start_idx = None
            for i, line in enumerate(lines):
                if re.match(r'^\s*(SELECT|WITH)\b', line, re.IGNORECASE):
                    start_idx = i
                    break

            if start_idx is not None:
                # Collect until a blank line or end
                collected = []
                for line in lines[start_idx:]:
                    if line.strip() == '' and collected:
                        break
                    collected.append(line)
                sql_query = "\n".join(collected).strip()
            else:
                # Last resort: strip any code fences and return what's left
                sql_query = cleaned.replace("```sql", "").replace("```", "").strip()
        
        # Post-process: sanitize common aliasing issues produced by LLMs
        def _sanitize_aliases(sql: str) -> (str, bool):
            changed = False

            # 1) Convert `AS 'alias'` or `as 'alias'` to `AS `alias``
            def _as_repl(m):
                nonlocal changed
                alias = m.group(1)
                new_alias = alias
                if alias and alias[0].isdigit():
                    new_alias = f"col_{alias}"
                changed = True
                return f"AS `{new_alias}`"

            sql, n1 = re.subn(r"\bAS\s+'([A-Za-z0-9_]+)'", _as_repl, sql, flags=re.IGNORECASE)
            if n1:
                changed = True

            # 2) Convert trailing single-quoted aliases after expressions: e.g. `expr 'alias',` -> `expr AS `alias`,`
            def _trail_repl(m):
                nonlocal changed
                expr = m.group('expr')
                alias = m.group('alias')
                suffix = m.group('suffix') or ''
                new_alias = alias
                if alias and alias[0].isdigit():
                    new_alias = f"col_{alias}"
                changed = True
                return f"{expr} AS `{new_alias}`{suffix}"

            sql, n2 = re.subn(r"(?P<expr>[^,\n]+?)\s+'(?P<alias>[A-Za-z0-9_]+)'(?P<suffix>\s*(,|\n|$))", _trail_repl, sql)
            if n2:
                changed = True

            return sql, changed

        sanitized_sql, modified = _sanitize_aliases(sql_query)
        if modified:
            logger.info("Sanitized SQL aliases produced by LLM to conform to BigQuery syntax")

        return SQLGenerationResponse(
            sql_query=sanitized_sql,
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
