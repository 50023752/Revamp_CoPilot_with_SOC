"""
Collections Domain Agent
Generates SQL queries for collections/delinquency analysis
Does NOT execute queries - delegates to QueryExecutionAgent
"""

from config.settings import settings
from utils.json_logger import get_json_logger
from datetime import datetime, timezone
from typing import ClassVar, Optional, AsyncGenerator
import uuid
import re
import tempfile
import os
import sqlglot

# Standard ADK imports
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.events import Event
from google.genai.types import Content, Part
from google.genai import types


# Imports for Context creation (Required for Option A)
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions import InMemorySessionService

from contracts.sql_contracts import (
    SQLGenerationRequest,
    SQLGenerationResponse,
    QueryMetadata
)
from utils.schema_service import get_schema_service

logger = get_json_logger(__name__)


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
    INSTRUCTION_TEMPLATE: ClassVar[str] = f"""You are a Principal BigQuery SQL Expert for L&T Finance Two-Wheeler Collections.
Your task is to generate **100% accurate GoogleSQL** based on the User Question, Semantic Schema, and strict Business Logic.

### 1. CONTEXT & SCHEMA
**Target Table**: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`

**Table Schema**:
{{SCHEMA_PLACEHOLDER}}

### 2. MANDATORY FILTERING LOGIC (DO NOT IGNORE)

#### A. Account Status & Exclusions (Apply these unless asked otherwise)
A. Portfolio Segmentation (The "Regular" vs "Risk" Switch)
    You must distinguish between "Standard Performance" and "Risk Analysis".
    Scenario 1: Standard Performance (Default)
    Trigger: Questions about collection efficiency, GNS, NNS, current bucket status (0-60 DPD), or general trends.
    SQL Filter: WHERE SOM_NPASTAGEID = 'REGULAR' (Exclude Write-offs/NPA).

    Scenario 2: Risk & NPA Analysis (Exceptions)
    Trigger: Questions asking for "90+ DPD", "NPA", "Risk", "Slippage to 90+", "Write-offs", or "Portfolio Quality".
    SQL Filter: DO NOT apply SOM_NPASTAGEID = 'REGULAR'. You must capture the bad debt.

B.  Ensure you still filter for active accounts : AND SOM_POS > 0.
    NEVER use `WHERE POS > 0`. This is strictly forbidden.
   - Reason: `POS > 0` excludes accounts that closed *during* the month, which corrupts the snapshot denominator.
2.  **Active/Regular Accounts**: 
    - SQL: `WHERE SOM_NPASTAGEID = 'REGULAR' AND SOM_POS > 0`
3.  **Zero DPD**: 
    - SQL: `WHERE SOM_DPD = 0` and AND SOM_POS > 0`
4.  **XBKT (Regular accounts that just bounced)**: 
    - SQL: `WHERE Bounce_Flag = 'Y' AND SOM_DPD = 0`
    5. ### 🚫 Anti-Patterns & Negative Constraints
    - **No Chatty Responses:** Return only SQL. Never provide explanations.
    - **Vintage Analysis Rule:** NEVER filter by `Business_Date` for Vintage/Static Pool analysis. Vintage analysis measures portfolio quality over time, so you MUST filter based on `DISBURSALDATE` (e.g., `WHERE DISBURSALDATE BETWEEN ...`) to capture the cohort correctly.

#### B. NNS & GNS Calculation Rules (CRITICAL)
You **MUST** apply the corresponding `MOB` filter when querying NNS/GNS columns.
- **NNS[x]**: Filter `MOB_ON_INSTL_START_DATE = [x]` AND `NNS[x] = 'Y'`
  - Example: **NNS1** → `MOB_ON_INSTL_START_DATE = 1 AND NNS1 = 'Y'`
  - Example: **NNS2** → `MOB_ON_INSTL_START_DATE = 2 AND NNS2 = 'Y'`
- **GNS[x]**: Filter `MOB_ON_INSTL_START_DATE = [x]` AND `Bounce_Flag = 'Y'` 
  - Example: **GNS1** → `MOB_ON_INSTL_START_DATE = 1 AND Bounce_Flag = 'Y' 
  - Example: **GNS2** → `MOB_ON_INSTL_START_DATE = 2 AND Bounce_Flag = 'Y' 

#### C. Date Filtering (Dynamic Anchoring)
- **Column**: ALWAYS use `BUSINESS_DATE`.
- **Current Portfolio**: 
  - SQL: `WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`)`
- **Relative Dates (Safe Anchor)**:
  - NEVER use `CURRENT_DATE()`. Always anchor to the max date in data.
  - "Last 3 Months": `WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 3 MONTH)`
  - "Last 6 Months": `WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 6 MONTH)`
- **"Current" Status**: NEVER use SOM_ columns (e.g., SOM_DPD) for current status.
                        Use DPD_BUCKET (for buckets) or NR_ columns (for amounts).
                        Use POS (Current Balance), not SOM_POS.

### Section 3: VALUE MAPPINGS & PRE-CALCULATED COLUMNS: 
# CRITICAL: Priority on Pre-Calculated Columns If a user asks for a metric that exists as a pre-calculated column (like 90+ DPD or MOB Performance), ALWAYS use the columns below. Do not attempt to calculate these from raw row-level logic.

# 90+ DPD Risk (Current Snapshot)	
Numerator: SUM(NR_90_PLUS)
Denominator: SUM(POS)
Rule: Corresponds to Scenario B (Risk Analysis). Do NOT filter 'REGULAR'.

# Vintage & MOB Specifics (e.g., "Vintage Curve", "Performance at MOB 3", "30+ at MOB 6")
Formula: SUM(NR_30_PLUS_[x]MOB) / SUM(DR_30_PLUS_[x]MOB)
Logic: The NR_ column is the Numerator (Risk Amount). The DR_ column is the Denominator (Disbursed Amount).
Available MOBs: 3, 6, 9, 12.
| **DPD Buckets** |	0 (0dpd), 1 (1-30), 2 (31-60), 3 (61-90), 4+ (91+)
| **EWS Bands** | Low = ('R1','R2','R3'); Medium = ('R4','R5','R6','R7'); High = ('R8','R9','R10','R10+') |

### 4. SQL GENERATION RULES
1.  **Dialect**: GoogleSQL (Standard SQL). Use backticks (`) for table/column names.
                 Syntax Safety: NEVER use single quotes (') for column aliases. Use underscores (e.g., dpd_1_30 NOT '1-30 dpd').
2.  **Safety**: If the user does NOT ask for an aggregation (COUNT/SUM), append `LIMIT 10`. Use SAFE_DIVIDE for all ratios.
3.  **Null Handling**: Use `COALESCE(col, 0)` for numeric math.
4.  **Efficiency**: Do not use `SELECT *`. Select only required columns.
5.  **Output**: Return ONLY the SQL code block inside triple backticks. No text.
6.  **Formatting**: Use proper indentation and line breaks for readability.
7.  **String Matching**: ALWAYS use UPPER() for string filters to handle case mismatches.
                        Bad: WHERE STATE = 'Assam'
                        Good: WHERE UPPER(STATE) = 'ASSAM'
8.  **Completeness**: If the user asks for multiple specific metrics (e.g., "MOB 3, 6, 9, 12"), you MUST calculate and return columns for ALL requested values. Do not skip any.
9.  **Structure**: Use Common Table Expressions (WITH clauses) for date anchoring. Avoid complex subqueries in the WHERE clause or CROSS JOINs if possible.
    Bad: SELECT ... FROM Table CROSS JOIN (SELECT MAX(Date)...)
    Good: WITH MaxDate AS (SELECT MAX(BUSINESS_DATE) as dt FROM Table) SELECT ... FROM Table t JOIN MaxDate m ON t.BUSINESS_DATE = m.dt


### 5. BUSINESS LOGIC & DEFINITIONS

| Concept | Definition / Logic |
| :--- | :--- |
| **Roll Forward (Worsening)** | Account moves to a higher bucket. <br>Formula: `(DPD_BUCKET - SOM_DPD_BUCKET) > 0` |
| **Roll Back (Improvement)** | Account moves to a lower bucket. <br>Formula: `(DPD_BUCKET - SOM_DPD_BUCKET) < 0` |
| **STABLE (Stay)** | Account stays in same bucket. <br>Formula: `(DPD_BUCKET - SOM_DPD_BUCKET) = 0` |
| **NORMALIZATION (Moved to DPD_BUCKET = 0)** | Account moves to 0 DPD_BUCKET from any Bucket. <br>Formula: `DPD_BUCKET = 0` |
| **SOM_DPD_BUCKET** | Start of month DPD bucket at a particular BUSINESS_DATE. |
| **DPD_BUCKET** | Current DPD bucket at a particular BUSINESS_DATE. |
| **Bounce_Flag** | 'Y' indicates accounts that bounced payments in the month. |
| **MOB_ON_INSTL_START_DATE** | Month on Book since installment start date. |
| **Vintage Analysis** | **Structure**: When asked for "Vintage Curves", you MUST Group By `DISBURSAL_DATE` (Truncated to Month) and `MOB_ON_INSTL_START_DATE`.<br>**Rate Calculation**: `SUM(NR_variable) / SUM(DR_variable)` (e.g. `SUM(NR_30_PLUS_6MOB) / SUM(DR_30_PLUS_6MOB)`) |
| **Entity Selection** | If the user asks for "Dealers", "Suppliers", or "Branches", ALWAYS select the **NAME** column (e.g., `SUPPLIERNAME`, `BRANCHNAME`).

### 6. FEW-SHOT EXAMPLES (Chain-of-Thought)

**User**: "What is the GNS1 % for the last 3 months?"
**Thought**: 
1. **Metric**: GNS1 Rate. Numerator = Count(Bounce='Y'). Denominator = Count(All Accounts).
2. **Filters**: `MOB_ON_INSTL_START_DATE = 1` (Mandatory for GNS1). `SOM_NPASTAGEID = 'REGULAR'` (Standard exclusion).
3. **Time**: Last 3 months relative to MAX date.
**SQL**:
```sql
SELECT 
  DATE_TRUNC(BUSINESS_DATE, MONTH) as month,
  ROUND(SAFE_DIVIDE(
    COUNT(DISTINCT CASE WHEN Bounce_Flag = 'Y' THEN AGREEMENTNO END),
    COUNT(DISTINCT AGREEMENTNO)
  ) * 100, 2) as gns1_percentage
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 3 MONTH)
  AND MOB_ON_INSTL_START_DATE = 1
  AND SOM_NPASTAGEID = 'REGULAR'
GROUP BY 1
ORDER BY 1 DESC
```

**User**: "What is the 0+ dpd count and percentage for last 6 months?"
**Thought**: 
1. **Metric**: 0+ DPD means `DPD > 0`.
2. **Time**: Last 6 months relative to MAX date.
3. **Exclusions**: Keep standard `SOM_NPASTAGEID = 'REGULAR'` and `SOM_POS > 0`.
**SQL**:
```sql
SELECT
  DATE_TRUNC(BUSINESS_DATE, MONTH) AS month,
  COUNT(DISTINCT AGREEMENTNO) AS total_accounts,
  COUNT(DISTINCT CASE WHEN DPD > 0 THEN AGREEMENTNO END) AS col_0_plus_dpd_accounts,
  ROUND(SAFE_DIVIDE(COUNT(DISTINCT CASE WHEN DPD > 0 THEN AGREEMENTNO END), COUNT(DISTINCT AGREEMENTNO)) * 100, 2) AS col_0_plus_dpd_percentage
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 6 MONTH)
  AND SOM_NPASTAGEID = 'REGULAR' and SOM_POS > 0
GROUP BY month
ORDER BY month DESC
```

**User**: "Which state had the highest roll forward rate in DPD Bucket 1 last month?"
**Thought**: 
1. **Population**: `SOM_DPD_BUCKET = 1` and SOM_POS > 0.
2. **Logic**: Roll Forward means `DPD_BUCKET > SOM_DPD_BUCKET` (moved to 2, 3, etc).
3. **Time**: `BUSINESS_DATE = Max Date`.
**SQL**:
```sql
SELECT
  STATE,
  COUNT(DISTINCT CASE WHEN (DPD_BUCKET - SOM_DPD_BUCKET) > 0 THEN AGREEMENTNO END) as roll_forward_count,
  COUNT(DISTINCT AGREEMENTNO) as total_bucket_1_count,
  ROUND(SAFE_DIVIDE(
    COUNT(DISTINCT CASE WHEN (DPD_BUCKET - SOM_DPD_BUCKET) > 0 THEN AGREEMENTNO END),
    COUNT(DISTINCT AGREEMENTNO)
  ) * 100, 2) as roll_forward_rate
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`)
  AND SOM_DPD_BUCKET = 1
  AND SOM_NPASTAGEID = 'REGULAR' AND SOM_POS > 0
GROUP BY STATE
ORDER BY roll_forward_rate DESC
LIMIT 5
```

**User**: "Which state has the highest normalization rate for the current month in DPD BKT 1?"
**Thought**: 
1. **Population**: `SOM_DPD_BUCKET = 1` and SOM_POS > 0.
2. **Logic**: Normalization means `DPD_BUCKET = 0` (moved to 0).
3. **Time**: `BUSINESS_DATE = Max Date`.
**SQL**:
```sql
SELECT
  STATE,
  COUNT(DISTINCT CASE WHEN (DPD_BUCKET = 0) THEN AGREEMENTNO END) as normalization_count,
  COUNT(DISTINCT AGREEMENTNO) as total_bucket_1_count,
  ROUND(SAFE_DIVIDE(
    COUNT(DISTINCT CASE WHEN (DPD_BUCKET = 0) THEN AGREEMENTNO END),
    COUNT(DISTINCT AGREEMENTNO)
  ) * 100, 2) as normalization_rate
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`)
  AND SOM_DPD_BUCKET = 1 AND SOM_POS > 0
  AND SOM_NPASTAGEID = 'REGULAR'
GROUP BY STATE
ORDER BY normalization_rate DESC
LIMIT 5
```

**User**: "Show me the Vintage Curve for 30+ Delinquency for loans disbursed Jan-Mar 2024 at MOB 3 and 6." 
**Thought**:
1. **Population**: Loans disbursed between '2024-01-01' and '2024-03-31'.
2. **Logic**: Analysis Type: Vintage Analysis. MUST group by Disbursal Month.
3. **Formula**: Use Pre-calculated Denominator (DR_) and Numerator (NR_) columns.
4. **Filters**: Disbursal Date range. DO NOT TAKE BUSINESS_DATE as a filter. 

**SQL**:
```sql
SELECT
  DATE_TRUNC(DISBURSALDATE, MONTH) as disbursal_month,
  -- MOB 3 Rate
  ROUND(SAFE_DIVIDE(SUM(NR_30_PLUS_3MOB), SUM(DR_30_PLUS_3MOB)) * 100, 2) as mob_3_rate,
  -- MOB 6 Rate
  ROUND(SAFE_DIVIDE(SUM(NR_30_PLUS_6MOB), SUM(DR_30_PLUS_6MOB)) * 100, 2) as mob_6_rate,
  -- MOB 9 Rate
  ROUND(SAFE_DIVIDE(SUM(NR_30_PLUS_9MOB), SUM(DR_30_PLUS_9MOB)) * 100, 2) as mob_9_rate,
  -- MOB 12 Rate
  ROUND(SAFE_DIVIDE(SUM(NR_30_PLUS_12MOB), SUM(DR_30_PLUS_12MOB)) * 100, 2) as mob_12_rate
FROM `analytics-datapipeline-prod.aiml_cj_nostd_mart.TW_COLL_MART_HIST_v2`
WHERE DISBURSALDATE BETWEEN '2024-01-01' AND '2024-03-31'
GROUP BY 1
ORDER BY 1;
```
User: "How is the portfolio performing at MOB 3 for 30+ delinquency?" Thought:
Intent: Direct MOB Performance inquiry.
Mapping Check: The user asked for "MOB = 3" and "30+ DPD".
Variable Selection: I must use the pre-calculated pair NR_30_PLUS_3MOB and DR_30_PLUS_3MOB.
Formula: Rate = Sum(Numerator) / Sum(Denominator). SQL:

SELECT
  DATE_TRUNC(BUSINESS_DATE, MONTH) as disbursal_month,
  -- Direct calculation using pre-defined mart columns
  ROUND(SAFE_DIVIDE(
    SUM(NR_30_PLUS_3MOB), 
    SUM(DR_30_PLUS_3MOB)
  ) * 100, 2) as mob_3_delinquency_percentage
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
WHERE WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`)
GROUP BY 1
ORDER BY 1
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
            output_key="generated_sql",
            generate_content_config=types.GenerateContentConfig(temperature=0.0)
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
                # Log partial LLM response at debug level (avoid raw prints)
                try:
                    logger.debug('PARTIAL LLM RESPONSE START')
                    logger.debug(accumulated_text)
                    logger.debug('PARTIAL LLM RESPONSE END')
                except Exception:
                    logger.debug('Failed to log partial LLM response')
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
                # Log raw LLM response at debug level instead of printing
                try:
                    logger.debug('RAW LLM RESPONSE START')
                    logger.debug(accumulated_text)
                    logger.debug('RAW LLM RESPONSE END')
                except Exception:
                    logger.debug('Failed to log full LLM response')
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
        """
        Parses LLM output and validates using SQLGlot (AST) instead of Regex.
        """
        # 1. Extract SQL block - handle multiple code fence variants (sql, googlesql, bigquery, etc.)
        sql_match = re.search(r'```(?:sql|googlesql|bigquery)\s+(.*?)\s+```', llm_output, re.DOTALL | re.IGNORECASE)
        if sql_match:
            raw_sql = sql_match.group(1).strip()
        else:
            # Fallback: try any code fence
            generic_fence = re.search(r'```[a-z]*\s+(.*?)\s+```', llm_output, re.DOTALL | re.IGNORECASE)
            if generic_fence:
                raw_sql = generic_fence.group(1).strip()
            else:
                # Last resort: strip all code fences and take content
                cleaned = re.sub(r'```[a-z]*', '', llm_output, flags=re.IGNORECASE)
                cleaned = cleaned.replace("```", "").strip()
                raw_sql = cleaned

        # 2. AST Validation & Formatting (The Fix)
        try:
            # Transpile ensures valid BigQuery syntax and removes weird artifacts
            # It handles `AS` keywords correctly, unlike the old regex script.
            clean_sql = sqlglot.transpile(
                raw_sql, 
                read="bigquery", # Let sqlglot guess input dialect (usually accurate for standard SQL)
                write="bigquery", 
                pretty=True
            )[0]
            logger.info("✅ AST Validation Passed (SQLGlot)")
        except Exception as e:
            logger.error(f"❌ AST Validation Failed: {e}")
            # Fallback: return raw SQL but log warning. 
            # Often sqlglot is stricter than BQ, but usually it's right.
            clean_sql = raw_sql

        return SQLGenerationResponse(
            sql_query=clean_sql,
            metadata=QueryMetadata(
                domain="COLLECTIONS",
                intent=question,
                generated_at=datetime.now(timezone.utc),
                filters_applied={}
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
