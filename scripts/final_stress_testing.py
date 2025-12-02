import asyncio
import pandas as pd
import numpy as np
import uuid
import sys
import os
import warnings
import logging
import argparse
import json
import time
import traceback
from datetime import datetime, timezone
from typing import Tuple, List, Dict, Any

# 1. Load Env & Config
from dotenv import load_dotenv
load_dotenv()

from google.cloud import bigquery
from google import genai
from google.genai import types
from google.auth import default as google_auth_default
from google.adk.sessions import InMemorySessionService
from google.adk.agents import InvocationContext, RunConfig
from google.genai.types import Content, Part
from google.api_core import exceptions as google_exceptions
from colorama import Fore, Style, init

# Initialize
warnings.filterwarnings('ignore')
init(autoreset=True)

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("stress_test.log")]
)
logger = logging.getLogger(__name__)

# ==========================================
# SETUP & IMPORTS
# ==========================================
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

try:
    from config.settings import settings
    from agent import root_agent
except ImportError as e:
    print(f"{Fore.RED}❌ Critical Error: Could not load agent/settings. {e}{Style.RESET_ALL}")
    sys.exit(1)

def calculate_consistency(series):
    """Calculates percentage of the most frequent identical SQL generation."""
    if len(series) == 0: return 0.0
    # Convert to string to ensure hashability (handle potential None/NaN)
    series = series.astype(str)
    modes = series.mode()
    if modes.empty: return 0.0
    most_freq = modes[0]
    count = (series == most_freq).sum()
    return (count / len(series)) * 100

# ==========================================
# COMPONENT 1: THE STRONG GEMINI JUDGE
# ==========================================
class SemanticDataJudge:
    def __init__(self):
        location = getattr(settings, 'gcp_region', 'asia-south1')
        api_key = getattr(settings, 'google_api_key', None)
        
        if api_key:
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = genai.Client(vertexai=True, project=settings.gcp_project_id, location=location)

        self.model_name = getattr(settings, 'gemini_pro_model', 'gemini-2.5-pro')
        
    def evaluate(self, question: str, df_ref: pd.DataFrame, df_bot: pd.DataFrame) -> Tuple[bool, str]:
        # TIER 1: STRICT MATCH (Fastest)
        if df_ref.empty and df_bot.empty: return True, "Exact Match (Both Empty)"
        if df_ref.empty: return False, "Fail: Reference is Empty but Bot returned data"
        if df_bot.empty: return False, "Fail: Bot returned No Data"

        try:
            # Numeric Exact Match Check
            if df_ref.shape == df_bot.shape:
                ref_nums = df_ref.select_dtypes(include='number').values
                bot_nums = df_bot.select_dtypes(include='number').values
                if ref_nums.shape == bot_nums.shape:
                    if np.allclose(ref_nums, bot_nums, equal_nan=True):
                        return True, "Numeric Exact Match"
        except:
            pass

        # TIER 2: OPTIMIZED LLM JUDGE
        ref_str = df_ref.head(20).to_markdown(index=False)
        bot_str = df_bot.head(20).to_markdown(index=False)

        # OPTIMIZED PROMPT (Addressing your specific rules)
        prompt = f"""
        Act as a **Senior Data Quality Engineer**. Compare the "Reference Data" (Ground Truth) vs "Candidate Data" (AI Output).
        
        User Question: "{question}"

        ### Reference Data (Correct):
        {ref_str}

        ### Candidate Data (To Evaluate):
        {bot_str}

        ### Evaluation Criteria (Leniency Applied):
        1. **Semantic Equivalence:** "50%" == "0.5" == "50.0" is a PASS.
        2. **Column Mapping:** Ignore column header names. Look at values.
        3. **Row Composition:** Ignore sort order unless ranking was explicitly asked.
        4. **Superset Data (The "Extra Month" Rule):** If Candidate contains ALL the Reference rows *plus* extra rows (e.g., an extra month of data), this is a PASS. Do not fail for providing more history than requested.
        5. **Sample Matching (The "AgreementNo" Rule):** If the question asks for a list of specific IDs (like AgreementNo) and the values don't match exactly but the *structure* and *logic* of the query seem correct (e.g. a different sample of 10 random customers), marks as PASS with a note.
        6. **Numeric Tolerance:** Allow for floating point differences up to 1%.

        ### Output Format:
        Return ONLY valid JSON:
        {{
            "match": boolean, 
            "reason": "concise explanation",
            "failure_type": "None" | "Data_Mismatch" | "Empty_Result" | "Hallucination"
        }}
        """

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0 
                )
            )
            
            txt = response.text if hasattr(response, 'text') else str(response)
            clean_json = txt.replace('```json', '').replace('```', '').strip()
            result = json.loads(clean_json)
            
            # Append failure type to reason for better visibility in CSV
            reason_text = f"LLM: {result.get('reason', 'N/A')}"
            if result.get('failure_type') and result.get('failure_type') != 'None':
                reason_text += f" [{result.get('failure_type')}]"

            return result.get("match", False), reason_text
            
        except Exception as e:
            return False, f"Judge Error: {str(e)}"

# ==========================================
# COMPONENT 2: BIGQUERY EXECUTOR (Paranoid Mode)
# ==========================================
class BigQueryExecutor:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.dataset_id = settings.logging_dataset
        self.table_id = "evaluation_history_v5"
        # NOTE: self.client is NOT created here to prevent stale tokens.

    def get_fresh_client(self):
        """Creates a fresh client for every operation to avoid timeouts."""
        return bigquery.Client(project=self.project_id)

    def run(self, sql: str) -> Tuple[pd.DataFrame, str, float]:
        if not sql or not isinstance(sql, str):
            return pd.DataFrame(), "No SQL Generated", 0.0

        start_ts = time.time()
        try:
            # 1. Create Fresh Connection
            client = self.get_fresh_client()
            
            # 2. Execute
            clean_sql = sql.replace('```sql', '').replace('```', '').strip()
            df = client.query(clean_sql).to_dataframe()
            
            # 3. Normalize
            df.columns = [str(c).lower().strip() for c in df.columns] 
            return df, "", (time.time() - start_ts)
            
        except Exception as e:
            return pd.DataFrame(), str(e), (time.time() - start_ts)

    def log_batch(self, results: List[Dict]):
        if not results: return

        table_ref = f"{self.project_id}.{self.dataset_id}.{self.table_id}"
        logger.info(f"📤 Uploading {len(results)} rows to {table_ref}...")

        try:
            # 1. Create Fresh Client (Fixes 'AttributeError')
            client = self.get_fresh_client()
            df = pd.DataFrame(results)

            # 2. Pre-Upload Cleaning
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            for col in ['question', 'expected_domain', 'actual_domain', 'status', 'reason', 'generated_sql', 'error_message', 'thought_process', 'column_selected']:
                if col in df.columns:
                    df[col] = df[col].astype(str)

            # 3. Safe Upload (No Partitioning Enforcement)
            job_config = bigquery.LoadJobConfig(
                write_disposition="WRITE_APPEND",
                schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION]
            )

            job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
            job.result()
            
            logger.info(f"✅ BQ Success: {len(results)} rows.")
            print(f"{Fore.GREEN}✅ Logged to BigQuery ({table_ref}){Style.RESET_ALL}")

        except Exception as e:
            logger.error(f"❌ BQ Load Failed: {e}")
            print(f"{Fore.RED}❌ BQ Upload Failed: {e}{Style.RESET_ALL}")
            try:
                fail_file = f"reports/bq_failed_{int(time.time())}.csv"
                df.to_csv(fail_file, index=False)
            except: pass

# ==========================================
# COMPONENT 3: AGENT RUNNER (Session State Extraction)
# ==========================================
async def get_agent_response(user_question: str) -> Dict[str, Any]:
    max_retries = 3
    base_delay = 2

    for attempt in range(max_retries):
        try:
            session_service = InMemorySessionService()
            session = await session_service.create_session(
                session_id=str(uuid.uuid4()), app_name="stress-test", user_id="admin"
            )

            ctx = InvocationContext(
                session_service=session_service,
                invocation_id=str(uuid.uuid4()),
                agent=root_agent,
                session=session,
                user_content=Content(parts=[Part(text=user_question)], role="user"),
                run_config=RunConfig()
            )

            # 1. Run Agent (We ignore the return value as it has no metadata)
            async for _ in root_agent.run_async(ctx): pass

            # 2. Extract Data from SESSION STATE
            sql = ""
            domain = "Unknown"
            input_tokens = 0
            output_tokens = 0
            thought_process = ""
            column_selected = "[]"
            
            # A. Extract SQL & Token Metadata
            if 'sql_generation_response' in ctx.session.state:
                data = ctx.session.state['sql_generation_response']
                
                # Handle if data is a Pydantic model (Object) or Dict
                if isinstance(data, dict):
                    sql = data.get('sql_query') or data.get('generated_sql') or ""
                    
                    # Capture Thought Process
                    thought_process = data.get('explanation') or data.get('metadata', {}).get('raw_thought_process') or ""
                    
                    # Capture Column Mapping / Expected Columns
                    # We accept either direct mapping or expected columns list
                    cols = data.get('expected_columns') or data.get('column_mapping') or []
                    column_selected = str(cols)

                    meta = data.get('metadata', {})
                    input_tokens = int(meta.get('input_tokens', 0))
                    output_tokens = int(meta.get('output_tokens', 0))
                else:
                    # Pydantic Object Access
                    sql = getattr(data, 'sql_query', "")
                    thought_process = getattr(data, 'explanation', "")
                    
                    # Capture Column Mapping
                    cols = getattr(data, 'expected_columns', [])
                    if not cols and hasattr(data, 'column_mapping'):
                         cols = getattr(data, 'column_mapping')
                    column_selected = str(cols)

                    meta = getattr(data, 'metadata', None)
                    if meta:
                        input_tokens = int(getattr(meta, 'input_tokens', 0))
                        output_tokens = int(getattr(meta, 'output_tokens', 0))
                        if not thought_process:
                             thought_process = getattr(meta, 'raw_thought_process', "")

            # B. Extract Domain
            if 'routing_response' in ctx.session.state:
                data = ctx.session.state['routing_response']
                if isinstance(data, dict):
                    domain_raw = data.get('selected_domain', 'Unknown')
                    domain = str(domain_raw).split('.')[-1]
                else:
                    domain_raw = getattr(data, 'selected_domain', 'Unknown')
                    domain = str(domain_raw).split('.')[-1]

            return {
                "sql": str(sql).strip(),
                "domain": str(domain),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "thought_process": str(thought_process),
                "column_selected": column_selected,
                "error": None
            }

        except (google_exceptions.ResourceExhausted, google_exceptions.ServiceUnavailable) as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(base_delay * (2 ** attempt))
            else:
                return {"sql": "", "domain": "Unknown", "input_tokens": 0, "output_tokens": 0, "thought_process": "", "column_selected": "[]", "error": f"Max Retries: {e}"}
        except Exception as e:
            return {"sql": "", "domain": "Unknown", "input_tokens": 0, "output_tokens": 0, "thought_process": "", "column_selected": "[]", "error": f"Crash: {e}"}

# ==========================================
# MAIN LOOP (BATCH MODE: Q1..Q14 -> Save -> Repeat)
# ==========================================
async def run_stress_test(total_batches: int = 3):
    if not os.path.exists('golden_question_bank.csv'):
        print(f"{Fore.RED}❌ Missing golden_question_bank.csv{Style.RESET_ALL}")
        return

    os.makedirs('reports', exist_ok=True)
    executor = BigQueryExecutor(settings.gcp_project_id)
    judge = SemanticDataJudge()
    
    try:
        df_gold = pd.read_csv('golden_question_bank.csv')
        df_gold = df_gold.loc[:, ~df_gold.columns.duplicated()]
    except Exception as e:
        print(f"❌ CSV Error: {e}")
        return
    
    execution_id = uuid.uuid4().hex[:6]
    global_results = [] 

    print(f"{Fore.CYAN}🚀 Starting Test: {total_batches} Batches | ID: {execution_id}{Style.RESET_ALL}\n")

    # --- OUTER LOOP: BATCHES (Fixes "Stuck on Q1" issue) ---
    for batch_num in range(1, total_batches + 1):
        print(f"\n{Fore.MAGENTA}📦 Batch {batch_num}/{total_batches}...{Style.RESET_ALL}")
        current_batch_results = []
        
        # --- INNER LOOP: QUESTIONS ---
        for idx, row in df_gold.iterrows():
            try:
                qid = idx + 1
                question = row.get('Questions')
                ref_sql = row.get('reference_sql') or row.get('refrence_sql') # Typo Fix
                exp_domain = row.get('Domain', 'Unknown')
                
                if pd.isna(question): continue

                sys.stdout.write(f"\r   Q{qid}: {str(question)[:40]}... ")
                sys.stdout.flush()

                # 1. Ground Truth
                ref_df, ref_err, _ = executor.run(ref_sql)

                # 2. Agent
                agent_res = await get_agent_response(question)
                
                # 3. Execute
                if agent_res['error']:
                    bot_df = pd.DataFrame()
                    bot_err = agent_res['error']
                    duration = 0.0
                else:
                    bot_df, bot_err, duration = executor.run(agent_res['sql'])

                # 4. Judge
                status = "FAIL"
                reason = ""
                if ref_err: status, reason = "SKIP", "Ref Broken"
                elif bot_err: status, reason = "ERROR", f"SQL Error: {str(bot_err)[:30]}"
                else:
                    match, r = judge.evaluate(question, ref_df, bot_df)
                    status = "PASS" if match else "FAIL"
                    reason = r

                symbol = f"{Fore.GREEN}✔{Style.RESET_ALL}" if status == "PASS" else (f"{Fore.YELLOW}⚠{Style.RESET_ALL}" if status=="SKIP" else f"{Fore.RED}✘{Style.RESET_ALL}")
                sys.stdout.write(f"{symbol} ({duration:.1f}s)")
                sys.stdout.flush()

                row_data = {
                    "run_id": execution_id,
                    "batch_number": int(batch_num),
                    "timestamp": datetime.now(timezone.utc),
                    "question_id": int(qid),
                    "question": str(question),
                    "expected_domain": str(exp_domain),
                    "actual_domain": str(agent_res['domain']),
                    "status": status,
                    "reason": reason,
                    "latency_seconds": float(round(duration, 2)),
                    "input_tokens": int(agent_res['input_tokens']),
                    "output_tokens": int(agent_res['output_tokens']),
                    "generated_sql": str(agent_res['sql']),
                    "thought_process": str(agent_res.get('thought_process', '')),
                    "column_selected": str(agent_res.get('column_selected', '')),
                    "error_message": str(bot_err) if bot_err else None
                }
                current_batch_results.append(row_data)
                global_results.append(row_data)
                
                await asyncio.sleep(0.5)

            except Exception as e:
                sys.stdout.write(f"{Fore.RED} CRASH: {e}{Style.RESET_ALL}\n")
                continue

        # End of Batch Upload
        print(f"\n")
        executor.log_batch(current_batch_results)

    # Final Report
    if global_results:
        df_all = pd.DataFrame(global_results)
        csv_path = f"reports/stress_full_{execution_id}.csv"
        df_all.to_csv(csv_path, index=False)
        
        # 1. Calculate Domain Correctness (Boolean)
        df_all['domain_match'] = df_all['expected_domain'].astype(str).str.upper() == df_all['actual_domain'].astype(str).str.upper()

        # 2. Advanced Aggregation
        df_grouped = df_all.groupby('question_id').agg(
            Pass_Pct=('status', lambda x: (x == 'PASS').mean() * 100),
            Dom_Pct=('domain_match', 'mean'),
            Cons_Pct=('generated_sql', calculate_consistency),
            Lat_Avg=('latency_seconds', 'mean'),
            Lat_P95=('latency_seconds', lambda x: x.quantile(0.95)),
            Cost=('input_tokens', 'mean')
        ).reset_index()
        
        # 3. Print Enhanced Table
        print(f"\n{Fore.WHITE}{'='*100}")
        print(f"📊 SUMMARY ({total_batches} Batches) | Run ID: {execution_id}")
        print(f"{'='*100}{Style.RESET_ALL}")
        
        # Header
        print(f"{'ID':<4} | {'Pass%':<6} | {'Dom%':<6} | {'Cons%':<6} | {'Lat(Avg)':<8} | {'Lat(P95)':<8} | {'Cost':<6}")
        print("-" * 100)
        
        for _, row in df_grouped.iterrows():
            # Color coding for Pass%
            p = row['Pass_Pct']
            color = Fore.GREEN if p > 80 else (Fore.YELLOW if p > 50 else Fore.RED)
            
            # Format Domain % (multiply by 100 here as mean returns 0-1)
            d_pct = row['Dom_Pct'] * 100
            
            print(f"{int(row['question_id']):<4} | "
                  f"{color}{p:>5.1f}%{Style.RESET_ALL} | "
                  f"{d_pct:>5.1f}% | "
                  f"{row['Cons_Pct']:>5.1f}% | "
                  f"{row['Lat_Avg']:>8.2f} | "
                  f"{row['Lat_P95']:>8.2f} | "
                  f"{int(row['Cost']):<6}")
        
        print(f"\n{Fore.CYAN}💾 Detailed report saved to: {csv_path}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}🔑 Batch Execution ID: {execution_id}{Style.RESET_ALL}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    try:
        asyncio.run(run_stress_test(args.runs))
    except KeyboardInterrupt:
        print("\nStopped by user.")