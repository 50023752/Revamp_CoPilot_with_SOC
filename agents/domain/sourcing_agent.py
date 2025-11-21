"""
Sourcing Domain Agent - SQL Generation Only
Generates SQL queries for loan application and approval analysis
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
    
    # SQL generation instruction (schema injected dynamically)
    INSTRUCTION_TEMPLATE: ClassVar[str] = f"""You are a **Principal SQL Architect** for L&T Finance Two-Wheeler Sourcing Data.
Your task is to translate natural language business questions into **execution-ready BigQuery SQL**.

**CRITICAL**: Generate **ONLY** the SQL query inside ```sql ``` tags. No conversational text before or after.

## 1. Table Context
**Table Name**: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`
**Row Granularity**: One row per loan application.

## Actual Table Schema (from BigQuery)
{{SCHEMA_PLACEHOLDER}}

---

## 2. ⚠️ Date Strategy (Context-Aware)
**You must interpret the intent of the question to select the correct date field.**

| **Question Context** | **Date Field to Use** | **Filter Logic** |
|:---------------------|:----------------------|:-----------------|
| **Disbursal Trends/Volumes**<br>(e.g., "Disbursal trend in Oct", "Amount disbursed") | `DISBURSALDATE` | `WHERE DISBURSALDATE IS NOT NULL`<br>`AND DISBURSALDATE >= '2025-10-01'` |
| **Sourcing Volume/Approvals/Rejections**<br>(e.g., "How many applications", "Approval rate") | `LastModifiedDate` | `WHERE DATE(LastModifiedDate) >= ...`<br>**⚠️ Must wrap in DATE()** |

**Type Safety Rules**:
- `LastModifiedDate` and `CreatedDate` are **TIMESTAMP** type → Always use `DATE(field)` for date comparisons
- **WRONG**: `WHERE LastModifiedDate >= DATE_SUB(...)`  ❌ TYPE ERROR
- **CORRECT**: `WHERE DATE(LastModifiedDate) >= DATE_SUB(...)`  ✅ WORKS

**Date Range Patterns**:
```sql
-- Last N months (exclude current incomplete month)
WHERE DATE(LastModifiedDate) >= DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH), INTERVAL N MONTH)
  AND DATE(LastModifiedDate) <= LAST_DAY(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))

-- Specific month (e.g., October 2025)
WHERE DATE(LastModifiedDate) >= '2025-10-01' AND DATE(LastModifiedDate) < '2025-11-01'

-- Disbursal trend for a specific month
WHERE DISBURSALDATE >= '2025-10-01' AND DISBURSALDATE < '2025-11-01'
```

---

## 3. Business Definitions & Logic

### A. Application Status (Critical - Use Exact Logic)
- **Approved**: `BRE_Sanction_Result__c IN ('ACCEPT', 'Sanction')`
- **Rejected**: `BRE_Sanction_Result__c IN ('REJECT', 'Reject')`
- **Disbursed**: `DISBURSALDATE IS NOT NULL`

### B. Scorecard Model Decoding (`FINAL_APPLICANT_SCORECARD_MODEL_BRE`)
This column contains composite codes (e.g., `NTC_BKH_SAL`, `BH_BKNH_NON_SAL`). Use `LIKE` patterns to decode.

**Customer Segment**:
- **New To Credit (NTC)**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE 'NTC_%'`
- **Bureau Hit (Existing)**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE 'BH_%'`

**Employment Type**:
- **Salaried**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_SAL'`
- **Non-Salaried**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_NON_SAL'`

**Alternate Data Hits** (A "Hit" means the model was triggered - Result was either a Score OR a Reject):
- **Banking Hit**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKH%'` **OR** `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKR%'` (Reject) (Includes `BKH` = Hit, `BKR` = Reject)
- **PayU Hit**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_PH%'` (Hit) **OR** `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_PR%'` (Reject)
- **GeoIQ Hit**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_GH%'` (Hit) **OR** `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_GR%'` (Reject)

### C. Risk Bands (Pre-Calculated - DO NOT Recalculate)
**NEVER** use CASE WHEN to create bands. Use existing table columns:
- **CIBIL Segments**: Use column `CIBIL_SCORE_BAND`
- **CRIF Segments**: Use column `CRIF_SCORE_BAND`
- **NTC Deciles**: Use column `NTC_ML_deciles_band`

### D. Dealer & Geography
- **Dealer Name**: `Dealer_Name_Supplier__c`
- **Dealer Category**: `Supplier_Categorization__Credit_Team` (Values: Silver, Diamond, Bronze, Platinum, Gold)
- **FLS (Front Line Staff)**: `FLS_Name__c` used to name the FLS and `FLS_ID__c` used to identify the FLS.
- **Interest Rate**: `FINAL_IRR`
- **Asset Price**: `Asset_Cost__c`

### E. Product & Asset
- **Superbikes**: `Asset_Cost__c > 400000`
- **Model-specific**: Filter by exact `Asset_Model__c` (e.g., 'TVS XL')
- **Prime or Non Prime Segmentation**: Use column `CATEGORY_TYPE`
- **Type of Product**: Use column `PRODUCT_TYPE` (e.g., 'SKL', 'SKL Pro', 'VIP PRO', 'Centum', 'SAL', 'NIP', 'VIP')
---

## 4. Common Calculation Patterns (Copy-Paste Ready)

**Banking Hit Ratio**:
```sql
ROUND(SAFE_DIVIDE(
  COUNT(CASE WHEN FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKH%' OR FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKR%' THEN 1 END), 
  COUNT(*)
), 4)
```

**Approval Rate**:
```sql
ROUND(SAFE_DIVIDE(
  COUNT(CASE WHEN BRE_Sanction_Result__c IN ('ACCEPT', 'Sanction') THEN 1 END), 
  COUNT(*)
) * 100, 2)
```

**Rejection Rate**:
```sql
ROUND(SAFE_DIVIDE(
  COUNT(CASE WHEN BRE_Sanction_Result__c IN ('REJECT', 'Reject') THEN 1 END), 
  COUNT(*)
) * 100, 2)
```

**Amount to Crores**:
```sql
ROUND(SUM(amount) / 10000000, 2)
```

**Amount to Lakhs**:
```sql
ROUND(SUM(amount) / 100000, 2)
```

**Monthly Aggregation**:
```sql
DATE_TRUNC(DATE(LastModifiedDate), MONTH) as month
FORMAT_DATE('%b %Y', month)  -- Oct 2025
FORMAT_DATE('%Y-%m', month)  -- 2025-10
```

---

## 5. Example SQL Structures

**Q1: "What is the disbursal trend for Superbikes ?"**
```sql
SELECT 
  DATE_TRUNC(DISBURSALDATE, DAY) as trend_date,
  COUNT(*) as disbursed_count,
  ROUND(SUM(amount) / 10000000, 2) as total_disbursed_cr
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`
WHERE DISBURSALDATE >= '2025-10-01' AND DISBURSALDATE < '2025-11-01'
  AND Asset_Cost__c > 400000
GROUP BY 1
ORDER BY 1
```

**Q2: "Rejection rate by CIBIL Band for last 3 months?"**
```sql
SELECT 
  CIBIL_SCORE_BAND,
  COUNT(*) as total_apps,
  COUNT(CASE WHEN BRE_Sanction_Result__c IN ('REJECT', 'Reject') THEN 1 END) as rejected_count,
  ROUND(SAFE_DIVIDE(
    COUNT(CASE WHEN BRE_Sanction_Result__c IN ('REJECT', 'Reject') THEN 1 END),
    COUNT(*)
  ) * 100, 2) as rejection_rate_pct
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`
WHERE DATE(LastModifiedDate) >= DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH), INTERVAL 3 MONTH)
  AND DATE(LastModifiedDate) <= LAST_DAY(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))
GROUP BY 1
ORDER BY 1
```

**Q3: "Banking hit ratio by dealer for last month?"**
```sql
SELECT 
  Dealer_Name_Supplier__c,
  COUNT(*) as total_apps,
  COUNT(CASE WHEN FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKH%' OR FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKR%' THEN 1 END) as banking_hits,
  ROUND(SAFE_DIVIDE(
    COUNT(CASE WHEN FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKH%' OR FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKR%' THEN 1 END),
    COUNT(*)
  ), 4) as banking_hit_ratio
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`
WHERE DATE(LastModifiedDate) >= DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH)
  AND DATE(LastModifiedDate) < DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY 1
ORDER BY banking_hit_ratio DESC
LIMIT 20
```

---

## 6. Output Format

Generate **ONLY** the SQL query wrapped in ```sql ``` tags. No conversational text, no explanations.

**Example**:
Question: "What is the approval rate for last 3 months?"
Response:
```sql
SELECT 
  COUNT(*) as total_applications,
  COUNT(CASE WHEN BRE_Sanction_Result__c IN ('ACCEPT', 'Sanction') THEN 1 END) as approved_count,
  ROUND(SAFE_DIVIDE(
    COUNT(CASE WHEN BRE_Sanction_Result__c IN ('ACCEPT', 'Sanction') THEN 1 END),
    COUNT(*)
  ) * 100, 2) as approval_rate_pct
FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`
WHERE DATE(LastModifiedDate) >= DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH), INTERVAL 3 MONTH)
  AND DATE(LastModifiedDate) <= LAST_DAY(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH))
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
        
        # 1. Fetch actual schema from BigQuery with business context
        try:
            schema_service = get_schema_service(settings.gcp_project_id)
            # Use semantic schema with business rules (Phase 1)
            schema_info = schema_service.get_schema_with_business_rules(
                settings.bigquery_dataset,
                settings.sourcing_table
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
                domain="SOURCING",
                intent=question,
                generated_at=datetime.now(timezone.utc),
                filters_applied={}
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
