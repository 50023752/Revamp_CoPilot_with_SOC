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
import json

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
    INSTRUCTION_TEMPLATE: ClassVar[str] = f"""
            You are a **Principal SQL Architect** for L&T Finance Two-Wheeler Sourcing Data.
            Your task is to translate natural language business questions into **execution-ready BigQuery SQL** based on strict Business Logic and Schema.

            **OUTPUT FORMAT INSTRUCTION:**
            You must **NOT** return raw text or Markdown. You must return a **VALID JSON OBJECT** with the following structure:
            {{
            "thought_process": "Brief explanation of date logic (Sourcing vs Disbursal), column selection, and business rules applied.",
            "column_mapping": {{
                "User_Concept_1": "Exact_Table_Column_1",
                "User_Concept_2": "Exact_Table_Column_2"
            }},
            "sql": "THE_GENERATED_SQL_QUERY"
            }}

            ### 1. CONTEXT & SCHEMA
            **Target Table**: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`
            **Row Granularity**: One row per loan application.

            **Table Schema**:
            {{SCHEMA_PLACEHOLDER}}

            ### 2. COLUMN SELECTION STRATEGY (High Priority)
            Before generating SQL, map user concepts to columns in the `column_mapping` JSON field.

            * **CRIF Index (EXPLICIT ONLY)**: Always Use `Crif_Index__c` **ONLY** if the user explicitly asks for "CRIF Index.
            * **Datasutram score (EXPLICIT ONLY)**: Always use `Datasutram_score__c`. 5. **Type Casting**: Columns stored as STRING that contain numbers (e.g., `Datasutram_score__c`) MUST be cast using `SAFE_CAST(col AS INT64)` before numeric comparison.
                                    - Wrong: `Datasutram_score__c > 500`
                                    - Right: `SAFE_CAST(Datasutram_score__c AS INT64) > 500`
            * **Dealer/Supplier Name**: Always use `Dealer_Name_Supplier__c`.
            * **Dealer/Supplier Tier/Segment/Category**: Always use `Supplier_Categorization__Credit_Team`.
            * **FLS**: Use `FLS_Name__c` (Name) or `FLS_ID__c` (ID).
            * **Scorecard Models live for disbursal**: Use `FINAL_APPLICANT_SCORECARD_MODEL_BRE`

            ### 3. DATE LOGIC & FILTERING (The "2-Way Switch")
            You must interpret the user's intent to select the correct date field and logic.

            **⚠️ DEFAULT TIME WINDOW RULE:**
            If the user **does not** specify a time range (e.g., "What is the approval rate?"), you MUST default to **Last 3 Months**.

            #### SCENARIO A: Sourcing, Approvals, Rejections, Volume
            * **Trigger:** "How many applications", "Approval Rate", "Login Volume", "Rejection Trend".
            * **Date Field:** `LastModifiedDate`.
            * **Type Safety:** You **MUST** wrap this column in `DATE()` function (e.g. `DATE(LastModifiedDate)`).
            * **Last 3 Months (DEFAULT)**:
                `WHERE DATE(LastModifiedDate) >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)`
            * **Last 6 Months**:
                `WHERE DATE(LastModifiedDate) >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH)`
            * **Current Month**:
                `WHERE DATE(LastModifiedDate) >= DATE_TRUNC(CURRENT_DATE(), MONTH)`

            #### SCENARIO B: Disbursals, Amount Disbursed
            * **Trigger:** "Disbursal trend", "Amount disbursed", "Loans funded".
            * **Date Field:** `DISBURSALDATE`.
            * **Type Safety:** No wrapping needed. **MUST** filter `IS NOT NULL`.
            * **Last 3 Months (DEFAULT)**:
                `WHERE DISBURSALDATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH) AND DISBURSALDATE IS NOT NULL`
            * **Last 6 Months**:
                `WHERE DISBURSALDATE >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH) AND DISBURSALDATE IS NOT NULL`

            ### 4. BUSINESS LOGIC & DEFINITIONS

            **A. Application Status**
            * **Approved**: `BRE_Sanction_Result__c IN ('ACCEPT', 'Sanction')`
            * **Rejected**: `BRE_Sanction_Result__c IN ('REJECT', 'Reject')`

            **B. Risk Bands (Pre-Calculated - DO NOT RECALCULATE)**
            * **NEVER** create custom CASE WHEN buckets for scores. Use the columns provided:
                * **CIBIL**: `CIBIL_SCORE_BAND`
                * **CRIF**: `CRIF_SCORE_BAND`
                * **NTC**: `NTC_ML_deciles_band`

            **C. Scorecard Model Decoding**
            * **NTC**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE 'NTC_%'`
            * **Bureau Hit**: `FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE 'BH_%'`
            * **Salaried**: `LIKE '%_SAL'` | **Non-Salaried**: `LIKE '%_NON_SAL'`

            **D. Alternate Data Hits**
            * **Banking Hit**: `LIKE '%_BKH%'` OR `LIKE '%_BKR%'`
            * **PayU Hit**: `LIKE '%_PH%'` OR `LIKE '%_PR%'`
            * **GeoIQ Hit**: `LIKE '%_GH%'` OR `LIKE '%_GR%'`

            ### 5. SQL GENERATION RULES
            1.  **Dialect**: GoogleSQL (Standard SQL). Use backticks (`).
            2.  **Safety**: `LIMIT 10` for non-aggregations. `SAFE_DIVIDE` ratios. `COALESCE(col, 0)` for math.
            3.  **Efficiency**: Select ONLY required columns.
            4.  **Formatting**: Return SQL string inside the JSON object.

            ### 6. FEW-SHOT EXAMPLES (JSON Format)

            **User**: "What is the approval rate by Risk Band for the last 3 months?"
            **Assistant**:
            {{
            "thought_process": "Metric: Approval Rate by Risk. User did NOT specify CRIF Index, so using default 'CIBIL_SCORE_BAND'. Scenario A (Sourcing).",
            "column_mapping": {{
                "Risk Band": "CIBIL_SCORE_BAND (Default)",
                "Approval Status": "BRE_Sanction_Result__c"
            }},
            "sql": "SELECT CIBIL_SCORE_BAND, COUNT(*) as total_apps, COUNT(CASE WHEN BRE_Sanction_Result__c IN ('ACCEPT', 'Sanction') THEN 1 END) as approved_count, ROUND(SAFE_DIVIDE(COUNT(CASE WHEN BRE_Sanction_Result__c IN ('ACCEPT', 'Sanction') THEN 1 END), COUNT(*)) * 100, 2) as approval_rate_pct FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}` WHERE DATE(LastModifiedDate) >= DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH), INTERVAL 3 MONTH) GROUP BY 1 ORDER BY 1"
            }}

            **User**: "What is the average CRIF Index for Salaried customers?"
            **Assistant**:
            {{
            "thought_process": "Metric: Avg Score. User explicitly asked for 'CRIF Index'. Using 'crif_index'. Segment: Salaried (from Scorecard Model).",
            "column_mapping": {{
                "CRIF Index": "crif_index",
                "Salaried": "FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_SAL'"
            }},
            "sql": "SELECT ROUND(AVG(crif_index), 2) as avg_crif_score FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}` WHERE DATE(LastModifiedDate) >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH) AND FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_SAL'"
            }}

            **User**: "Show me the disbursal trend for Superbikes in Oct 2025."
            **Assistant**:
            {{
            "thought_process": "Metric: Disbursal Trend. Scenario B (Disbursal). Date: DISBURSALDATE. Filter: Asset_Cost > 400k (Superbike).",
            "column_mapping": {{
                "Superbike": "Asset_Cost__c > 400000",
                "Disbursal Date": "DISBURSALDATE"
            }},
            "sql": "SELECT DATE_TRUNC(DISBURSALDATE, DAY) as trend_date, COUNT(*) as disbursed_count, ROUND(SUM(amount) / 10000000, 2) as total_disbursed_cr FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}` WHERE DISBURSALDATE >= '2025-10-01' AND DISBURSALDATE < '2025-11-01' AND Asset_Cost__c > 400000 GROUP BY 1 ORDER BY 1"
            }}

            **User**: "What is the banking hit ratio by dealer category?"
            **Assistant**:
            {{
            "thought_process": "Metric: Banking Hit Ratio (BKH+BKR)/Total. Dimension: Dealer Category. Scenario A.",
            "column_mapping": {{
                "Banking Hit": "LIKE '%_BKH%' OR LIKE '%_BKR%'",
                "Dealer Category": "Supplier_Categorization__Credit_Team"
            }},
            "sql": "SELECT Supplier_Categorization__Credit_Team as dealer_category, COUNT(*) as total_apps, COUNT(CASE WHEN FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKH%' OR FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKR%' THEN 1 END) as banking_hits, ROUND(SAFE_DIVIDE(COUNT(CASE WHEN FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKH%' OR FINAL_APPLICANT_SCORECARD_MODEL_BRE LIKE '%_BKR%' THEN 1 END), COUNT(*)), 4) as banking_hit_ratio FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}` WHERE DATE(LastModifiedDate) >= DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH) GROUP BY 1 ORDER BY banking_hit_ratio DESC"
            }}
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
            output_key="generated_sql",
            generate_content_config=types.GenerateContentConfig(
                temperature=0.0,
                # CRITICAL UPDATE: Enforce JSON output mode
                response_mime_type="application/json"
            )
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
        
        if context is not None and hasattr(context, 'session') and hasattr(context.session, 'state'):
            try:
                context.session.state['SCHEMA_PLACEHOLDER'] = schema_info
            except Exception:
                logger.debug("Failed to set session state SCHEMA_PLACEHOLDER; will use fallback replacement")

        try:
            if isinstance(original_instruction, str):
                instr_text = original_instruction
            else:
                instr_text = str(original_instruction)
        except Exception:
            instr_text = str(original_instruction)

        if "{{SCHEMA_PLACEHOLDER}}" in instr_text:
            instr_text = instr_text.replace("{{SCHEMA_PLACEHOLDER}}", schema_info)
        if "{SCHEMA_PLACEHOLDER}" in instr_text:
            instr_text = instr_text.replace("{SCHEMA_PLACEHOLDER}", schema_info)

        # Add explicit user question and a brief last Q/A snippet
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
        
        # Debug logs for instruction
        try:
            instr_snippet = (enhanced_instruction[:2000] + '...') if len(enhanced_instruction) > 2000 else enhanced_instruction
            logger.debug(f"Instruction sent to LLM (first 2000 chars): {instr_snippet}")
        except Exception:
            pass
        
        # 3. Invoke the Sub-Agent
        accumulated_text = ""
        total_input_tokens = 0
        total_output_tokens = 0

        try:
            async for event in self.sql_generator.run_async(context):
                # Capture Token Metadata
                if hasattr(event, 'usage_metadata') and event.usage_metadata:
                    total_input_tokens = event.usage_metadata.prompt_token_count or 0
                    total_output_tokens = event.usage_metadata.candidates_token_count or 0

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
            # Clear injected schema from session state
            try:
                if context is not None and hasattr(context, 'session') and hasattr(context.session, 'state'):
                    if 'SCHEMA_PLACEHOLDER' in context.session.state:
                        del context.session.state['SCHEMA_PLACEHOLDER']
            except Exception:
                pass

        # 4. Parse Result
        # Pass captured tokens to parsing function
        parsed = self._parse_and_validate(
            accumulated_text, 
            request.user_question, 
            input_tokens=total_input_tokens, 
            output_tokens=total_output_tokens
        )
        
        # Post-parse check
        sql_head = parsed.sql_query.strip()[:10].upper()
        if not (sql_head.startswith('SELECT') or sql_head.startswith('WITH')):
            logger.warning(f"Parsed SQL does not start with SELECT/WITH. Head: {sql_head}")

        return parsed

    def _extract_last_message(self, ctx: InvocationContext) -> str:
        """Helper to get the actual latest user query."""
        if ctx.new_message and ctx.new_message.parts:
            return " ".join(p.text for p in ctx.new_message.parts if p.text)
        return str(ctx.current_input or "")

    def _extract_json_substring(self, text: str) -> str:
        """
        Finds the first valid JSON object in a string by locating the outer {}.
        """
        text = text.strip()
        
        # 1. Fast path: It's already pure JSON
        if text.startswith("{") and text.endswith("}"):
            return text

        # 2. Markdown Cleanup: Remove ```json ... ```
        if "```" in text:
            # Remove line starting with ```json
            text = re.sub(r'^```[a-zA-Z]*\s*', '', text, flags=re.MULTILINE)
            # Remove ending ```
            text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
            text = text.strip()

        # 3. Brute Force: Find the first '{' and the last '}'
        start_idx = text.find("{")
        end_idx = text.rfind("}")

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            return text[start_idx : end_idx + 1]
        
        return text # Return original if no brackets found (let JSONDecodeError catch it)

    def _parse_and_validate(self, llm_output: str, question: str, input_tokens: int = 0, output_tokens: int = 0) -> SQLGenerationResponse:
        """
        Parses LLM output (JSON) and validates the extracted SQL.
        """
        raw_sql = ""
        thought_process = "No thought process returned."
        flattened_mapping = [] # Safe initialization
        
        # --- NEW JSON PARSING LOGIC START ---
        try:
            # 1. robust extraction using helper
            json_text = self._extract_json_substring(llm_output)
            
            # 2. Parse JSON
            response_data = json.loads(json_text)
            
            # 3. Extract Fields
            raw_sql = response_data.get("sql", "").strip()
            thought_process = response_data.get("thought_process", "")
            column_mapping = response_data.get("column_mapping", {})
            
            # Flatten dict for consistency
            if isinstance(column_mapping, dict):
                flattened_mapping = [f"{k}: {v}" for k, v in column_mapping.items()]
            
            logger.info(f"🧠 Model Thought: {thought_process}")
            logger.info(f"🗺️ Column Mapping: {column_mapping}")

        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON Parse Failed: {e}. Raw Output snippet: {llm_output[:100]}...")
            
            # Fallback 1: Try to extract SQL via Regex
            sql_match = re.search(r'SELECT\s+.*', llm_output, re.DOTALL | re.IGNORECASE)
            if sql_match:
                raw_sql = sql_match.group(0)
            else:
                # Fallback 2: aggressive cleanup
                raw_sql = llm_output.replace("```sql", "").replace("```", "").strip()
            
            # Fallback 3: Try to capture thought process via Regex if JSON failed
            tp_match = re.search(r'(?:thought_process|explanation)["\s:]+(.*?)(?:sql|column_mapping|```)', llm_output, re.DOTALL | re.IGNORECASE)
            if tp_match:
                thought_process = tp_match.group(1).strip().strip('"').strip(',')
        # --- NEW JSON PARSING LOGIC END ---

        # 4. AST Validation & Formatting (SQLGlot)
        try:
            if not raw_sql:
                # If raw_sql is still empty, look for a SELECT keyword one last time
                if "SELECT" in llm_output.upper():
                    raw_sql = llm_output[llm_output.upper().find("SELECT"):]
                else:
                    raise ValueError("Empty SQL extracted from response")

            # Transpile ensures valid BigQuery syntax
            clean_sql = sqlglot.transpile(
                raw_sql, 
                read="bigquery", 
                write="bigquery", 
                pretty=True
            )[0]
            logger.info("✅ AST Validation Passed (SQLGlot)")
        except Exception as e:
            logger.error(f"❌ AST Validation Failed: {e}")
            # Fallback: return raw SQL but log warning. 
            clean_sql = raw_sql

        return SQLGenerationResponse(
            sql_query=clean_sql,
            metadata=QueryMetadata(
                domain="COLLECTIONS" if "Collections" in self.name else "SOURCING",
                intent=question,
                generated_at=datetime.now(timezone.utc),
                filters_applied={},
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                raw_thought_process=thought_process
            ),
            explanation=thought_process, 
            expected_columns=flattened_mapping, 
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