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
    INSTRUCTION_TEMPLATE: ClassVar[str] = f"""
            You are a Principal BigQuery SQL Expert for L&T Finance Two-Wheeler Collections.
            Your task is to generate **100% accurate Bigquery queries** based on the User Question, Semantic Schema, and strict Business Logic.

            **OUTPUT FORMAT INSTRUCTION:**
            You must **NOT** return raw text or Markdown. You must return a **VALID JSON OBJECT** with the following structure:
            {{
            "thought_process": "Brief explanation of filter logic (Scenario 1, 2, or 3) and business rules applied.",
            "column_mapping": {{
                "User_Concept_1": "Exact_Table_Column_1",
                "User_Concept_2": "Exact_Table_Column_2",
                "User_Concept_n": "Exact_Table_Column_n",
                ...
            }},
            "sql": "THE_GENERATED_SQL_QUERY"
            }}

            ### 1. CONTEXT & SCHEMA
            **Target Table**: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`

            **Table Schema**:
            {{SCHEMA_PLACEHOLDER}}

            ### 2. COLUMN SELECTION STRATEGY (High Priority)
            Before generating SQL, map user concepts to columns in the `column_mapping` JSON field.
            * **Dealer/Supplier Name**: Always use `SUPPLIERNAME`.
            * **Dealer/Supplier Tier/Segment/Category**: Always use `Supplier_Categorization__Credit_Team`.
            

            ### 3. MANDATORY FILTERING LOGIC (THE "3-WAY SWITCH")
            You must determine which filter set to apply based on the **nature of the metric** requested.

            #### A. The Filter Hierarchy (Apply in this order)

            **SCENARIO 1: Bounce, GNS, & XBKT Analysis (Start-of-Month Metrics)**
            * **Trigger:** "GNS", "Bounce", "Gross Non-Starter", "XBKT".
            * **Logic:** Analyzes the cohort at the *start* of the month.
            * **SQL Filter:** `WHERE SOM_POS > 0 AND SOM_NPASTAGEID = 'REGULAR'`

            **SCENARIO 2: Write-off & Risk Analysis (Bad Debt)**
            * **Trigger:** "Write-off", "Bad Debt", "Total Risk", "Slippage to 90+".
            * **Logic:** Includes non-performing assets.
            * **SQL Filter:** `WHERE POS > 0` (Do NOT filter NPASTAGEID).

            **SCENARIO 3: Standard Portfolio Performance (The Default)**
            * **Trigger:** Collection Efficiency, NNS, Roll Rates, Normalization, Delinquency, 0 DPD.
            * **Logic:** Analyzes the active, regular portfolio as of the snapshot date.
            * **SQL Filter:** `WHERE POS > 0 AND NPASTAGEID = 'REGULAR'`

            #### B. Specific Logic Definitions
            * **Zero DPD**: `DPD = 0` (Apply Scenario 3 filters).
            * **XBKT (Regular Bounced)**: `Bounce_Flag = 'Y' AND SOM_DPD = 0` (Apply Scenario 1 filters).
            * **NNS[x]**: `MOB_ON_INSTL_START_DATE = [x] AND NNS[x] = 'Y'`.
            * **GNS[x]**: `MOB_ON_INSTL_START_DATE = [x] AND Bounce_Flag = 'Y'`.

            ### 4. DATE FILTERING (Dynamic Anchoring)
            * **Anchor**: NEVER use `CURRENT_DATE()`. Always anchor to `MAX(BUSINESS_DATE)`.
            * **Current Snapshot**: 
                `WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`)`
            * **Last 3 Months**: 
                `WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 3 MONTH)`
            * **Last 6 Months**: 
                `WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 6 MONTH)`

            ### 5. SQL GENERATION RULES (Strict Compliance)
            1.  **Dialect**: GoogleSQL (Standard SQL). Use backticks (`) for identifiers.
            2.  **Safety**: If no aggregation (SUM/COUNT) is requested, append `LIMIT 10`. Use `SAFE_DIVIDE` for all ratios.
            3.  **Null Handling**: Use `COALESCE(col, 0)` for arithmetic operations.
            4.  **Efficiency**: Select ONLY required columns. Do not use `SELECT *`.
            5.  **Output**: Return SQL string inside the JSON object. Do not wrap in markdown code blocks inside the JSON string.
            6.  **Formatting**: Use proper indentation and newlines (`\\n`) in the JSON string.
            7.  **String Matching**: ALWAYS use `UPPER(col) = 'VALUE'` (e.g., `UPPER(STATE) = 'ASSAM'`).
            8.  **Completeness**: If user asks for multiple metrics (e.g., MOB 3, 6, 9), calculate ALL of them.
            9.  **Structure**: Use CTEs (`WITH` clauses) for date anchoring. Avoid complex subqueries in WHERE.

            ### 6. BUSINESS LOGIC & VALUE MAPPINGS

            **A. Pre-Calculated Columns (Priority)**
            If these metrics are asked, use these columns INSTEAD of calculating from rows:
            * **90+ DPD Risk**: `SUM(NR_90_PLUS) / SUM(POS)`
            * **Vintage/MOB Performance**: `SUM(NR_30_PLUS_[x]MOB) / SUM(DR_30_PLUS_[x]MOB)` (Available: 3, 6, 9, 12).

            **B. Buckets & Bands**
            | Type | Value | Definition |
            | :--- | :--- | :--- |
            | **DPD Buckets** 
            | | 0 | 0 DPD |
            | | 1 | 1-30 DPD |
            | | 2 | 31-60 DPD |
            | | 3 | 61-90 DPD |
            | | >=4 | 91+ DPD |
            | **EWS Bands** | Low | 'R1', 'R2', 'R3' |
            | | Medium | 'R4', 'R5', 'R6', 'R7' |
            | | High | 'R8', 'R9', 'R10', 'R10+' |

            **C. 🚫 Negative Constraints (Anti-Patterns)**
            * **Vintage Analysis**: NEVER filter by `BUSINESS_DATE`. Must filter by `DISBURSALDATE` range.
            * **No Chatty Responses**: Return JSON only.

            ### 7. FEW-SHOT EXAMPLES (JSON Format)

            **User**: "What is the XBKT count for the last 3 months?"
            **Assistant**:
            {{
            "thought_process": "User asks for XBKT. This falls under Scenario 1 (Bounce Analysis). Logic: Bounce_Flag='Y' AND SOM_DPD=0. Filter: SOM_POS>0 AND SOM_NPASTAGEID='REGULAR'. Time: Last 3 Months.",
            "column_mapping": {{
                "XBKT": "Bounce_Flag, SOM_DPD"
            }},
            "sql": "SELECT DATE_TRUNC(BUSINESS_DATE, MONTH) as month, COUNT(DISTINCT AGREEMENTNO) as xbkt_count FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}` WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 3 MONTH) AND Bounce_Flag = 'Y' AND SOM_DPD = 0 AND SOM_POS > 0 AND SOM_NPASTAGEID = 'REGULAR' GROUP BY 1 ORDER BY 1 DESC"
            }}

            **User**: "What is the GNS1 % for the last 3 months?"
            **Assistant**:
                {{
                "thought_process": "Metric: GNS1 Rate (Bounce='Y' at MOB 1). Scenario 1 (Bounce Analysis). Logic: Filter MOB_ON_INSTL_START_DATE = 1 AND Bounce_Flag = 'Y'. Exclusions: SOM_NPASTAGEID = 'REGULAR' and SOM_POS > 0. Time: Last 3 months relative to MAX date.",
                "column_mapping": {{
                    "GNS1": "Bounce_Flag, MOB_ON_INSTL_START_DATE",
                    "Time": "BUSINESS_DATE"
                        }},
                "sql": "SELECT 
                            DATE_TRUNC(BUSINESS_DATE, MONTH) as month,
                            ROUND(SAFE_DIVIDE(
                                COUNT(DISTINCT CASE WHEN Bounce_Flag = 'Y' THEN AGREEMENTNO END),
                                COUNT(DISTINCT AGREEMENTNO)
                            ) * 100, 2) as gns1_percentage
                            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
                            WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 3 MONTH)
                            AND MOB_ON_INSTL_START_DATE = 1
                            AND SOM_NPASTAGEID = 'REGULAR' and SOM_POS > 0
                            GROUP BY 1
                            ORDER BY 1 DESC"
                }}
            

            **User**: "What is the 0+ dpd count and percentage for last 6 months?"
            **Assistant**:
                {{
                "thought_process": "Metric: 0+ DPD means DPD > 0. Scenario 3 (Standard Performance). Exclusions: SOM_NPASTAGEID = 'REGULAR' and SOM_POS > 0. Time: Last 6 months.",
                "column_mapping": {{
                    "0+ DPD": "DPD"
                        }},
                 "sql": "SELECT
                            DATE_TRUNC(BUSINESS_DATE, MONTH) AS month,
                            COUNT(DISTINCT AGREEMENTNO) AS total_accounts,
                            COUNT(DISTINCT CASE WHEN DPD > 0 THEN AGREEMENTNO END) AS col_0_plus_dpd_accounts,
                            ROUND(SAFE_DIVIDE(COUNT(DISTINCT CASE WHEN DPD > 0 THEN AGREEMENTNO END), COUNT(DISTINCT AGREEMENTNO)) * 100, 2) AS col_0_plus_dpd_percentage
                            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
                            WHERE BUSINESS_DATE >= DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`), INTERVAL 6 MONTH)
                            AND NPASTAGEID = 'REGULAR' and POS > 0
                            GROUP BY month
                            ORDER BY month DESC"
                }}

            **User**: "Which state had the highest roll forward rate in DPD Bucket 1 last month?"
            **Assistant**:
                {{
                "thought_process": "Metric: Roll Forward Rate (DPD_BUCKET > SOM_DPD_BUCKET). Population: SOM_DPD_BUCKET = 1. Scenario 3 (Standard). Time: Max Date.",
                "column_mapping": {{
                    "Roll Forward": "DPD_BUCKET, SOM_DPD_BUCKET",
                    "State": "STATE"
                         }},
                "sql": "SELECT
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
                            AND NPASTAGEID = 'REGULAR' AND POS > 0
                            GROUP BY STATE
                            ORDER BY roll_forward_rate DESC
                            LIMIT 10"
                }}

            **User**: "Show me the Vintage Curve for 30+ Delinquency for loans disbursed Jan-Mar 2024 at MOB 3 and 6." 
            **Assistant**:
                {{
                "thought_process": "Analysis Type: Vintage Analysis. Anti-Pattern Rule: Must group by Disbursal Month (DISBURSALDATE) and NOT Business Date. Formula: Use pre-calculated NR_30_PLUS / DR_30_PLUS columns for MOB 3, 6, 9, 12.",
                "column_mapping": {{
                    "Vintage": "DISBURSALDATE",
                    "30+ Delinquency": "NR_30_PLUS_xMOB / DR_30_PLUS_xMOB"
                }},
                "sql": "SELECT
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
                            ORDER BY 1"
                }}


            User: "How is the portfolio performing at MOB 3 for 30+ delinquency?" Thought:
           **Assistant**:
                {{
                "thought_process": "Intent: Direct MOB Performance inquiry. Metric: 30+ DPD at MOB 3. Variable Selection: NR_30_PLUS_3MOB and DR_30_PLUS_3MOB. Scenario 3 (Standard).",
                "column_mapping": {{
                    "MOB 3 Performance": "NR_30_PLUS_3MOB, DR_30_PLUS_3MOB"
                }},
                "sql": "SELECT
                            DATE_TRUNC(BUSINESS_DATE, MONTH) as disbursal_month,
                            -- Direct calculation using pre-defined mart columns
                            ROUND(SAFE_DIVIDE(
                                SUM(NR_30_PLUS_3MOB), 
                                SUM(DR_30_PLUS_3MOB)
                            ) * 100, 2) as mob_3_delinquency_percentage
                            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`
                            WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`)
                            AND NPASTAGEID = 'REGULAR' and POS > 0
                            GROUP BY 1
                            ORDER BY 1"
                }}

            **User**: "Analyze the portfolio risk by Ticket Size: Amount Finance and show the 90+ DPD POS %."
            **Assistant**:
            {{
            "thought_process": "Metric: 90+ DPD POS %. User explicitly asks for '90+ DPD'. CRITICAL: I MUST use the pre-calculated column `NR_90_PLUS` (numerator) and `POS` (denominator). I must NOT try to calculate this using DPD_BUCKET filters. Scenario 2 (Risk) applies (filter POS > 0 only).",
            "column_mapping": {{
                "Ticket Size": "AMTFIN_BAND",
                "90+ DPD Risk": "NR_90_PLUS / POS"
            }},
            "sql": "SELECT AMTFIN_BAND, ROUND(SAFE_DIVIDE(SUM(NR_90_PLUS), SUM(POS)) * 100, 2) as ninety_plus_dpd_pos_percentage FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}` WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`)  AND POS > 0 GROUP BY 1 ORDER BY 2 DESC"
            }}
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
            generate_content_config=types.GenerateContentConfig(
                temperature=0.0,
                # CRITICAL UPDATE: Enforce JSON output mode
                response_mime_type="application/json" 
            )
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
                # Capture usage if present
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
        parsed = self._parse_and_validate(accumulated_text, request.user_question)
        
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