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
from datetime import datetime, timezone, date
from typing import Tuple, List, Dict, Any, Optional

# 1. Load Environment Variables
from dotenv import load_dotenv
load_dotenv()

from google.cloud import bigquery
from google.adk.sessions import InMemorySessionService
from google.adk.agents import InvocationContext, RunConfig
from google.genai.types import Content, Part
from colorama import Fore, Style, init

# Initialize environment
warnings.filterwarnings('ignore')
init(autoreset=True)

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bulk_eval.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ==========================================
# 2. PRE-FLIGHT CHECKS
# ==========================================
def check_environment():
    required_vars = ["GCP_PROJECT_ID"]
    missing = [v for v in required_vars if not os.getenv(v)]
    # Double check settings
    try:
        from config.settings import settings
        if settings.gcp_project_id: return
    except: pass
    
    if missing:
        logger.error(f"❌ CRITICAL: Missing environment variables: {missing}")
        sys.exit(1)

# ==========================================
# 3. IMPORTS & SETUP
# ==========================================
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

try:
    from config.settings import settings
    from agent import root_agent
    logger.info(f"✅ Loaded Agent & Settings (Project: {settings.gcp_project_id})")
except ImportError as e:
    logger.error(f"❌ CRITICAL ERROR: {e}")
    sys.exit(1)

check_environment()

# ==========================================
# 4. BIGQUERY JUDGE
# ==========================================
class BigQueryJudge:
    def __init__(self, project_id: str):
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id

    def execute(self, sql: str) -> Tuple[pd.DataFrame, str, float]:
        """Executes SQL. Returns (DataFrame, ErrorMessage, DurationSeconds)."""
        if not isinstance(sql, str) or not sql.strip():
            return pd.DataFrame(), "No SQL Generated", 0.0

        start_ts = datetime.now()
        try:
            # Dry Run (Cost Check / Syntax Check)
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
            self.client.query(sql, job_config=job_config)
            
            # Real Run
            query_job = self.client.query(sql)
            df = query_job.to_dataframe()
            
            duration = (datetime.now() - start_ts).total_seconds()
            # Normalize column names to lowercase for comparison
            df.columns = [c.lower() for c in df.columns]
            return df, "", duration
            
        except Exception as e:
            duration = (datetime.now() - start_ts).total_seconds()
            return pd.DataFrame(), str(e), duration

    def check_equality(self, df_ref: pd.DataFrame, df_bot: pd.DataFrame) -> Tuple[bool, str]:
        """Compares two DataFrames robustly."""
        if df_ref.empty and df_bot.empty: return True, "Both Empty (Match)"
        if df_ref.empty: return False, "Ref Empty / Bot Has Data"
        if df_bot.empty: return False, "Bot Returned No Data"
        
        if df_ref.shape != df_bot.shape: return False, f"Shape Mismatch: Ref{df_ref.shape} vs Bot{df_bot.shape}"

        try:
            # 1. Sort columns alphabetically
            df_ref = df_ref.reindex(sorted(df_ref.columns), axis=1)
            df_bot = df_bot.reindex(sorted(df_bot.columns), axis=1)
            
            # 2. Sort rows deterministically (by all columns)
            sort_cols = list(df_ref.columns)
            df_ref_sorted = df_ref.sort_values(by=sort_cols).reset_index(drop=True)
            df_bot_sorted = df_bot.sort_values(by=sort_cols).reset_index(drop=True)
            
            # 3. Compare
            pd.testing.assert_frame_equal(df_ref_sorted, df_bot_sorted, check_dtype=False, check_column_type=False)
            return True, "Exact Match"
        except AssertionError:
            return False, "Data Content Mismatch"
        except Exception as e:
            return False, f"Comparison Error: {e}"

    def _normalize_record(self, record: Dict) -> Dict:
        """Converts numpy/pandas types to JSON-serializable Python types."""
        new_rec = {}
        for k, v in record.items():
            if pd.isna(v): new_rec[k] = None
            elif isinstance(v, (np.integer, int)): new_rec[k] = int(v)
            elif isinstance(v, (np.floating, float)): new_rec[k] = float(v)
            elif isinstance(v, (np.bool_, bool)): new_rec[k] = bool(v)
            elif isinstance(v, (datetime, date, pd.Timestamp)): new_rec[k] = v.isoformat()
            else: new_rec[k] = str(v)
        return new_rec

    def log_results_to_bq(self, results: List[Dict]):
        if not results: return

        dataset_id = settings.logging_dataset
        table_id = "evaluation_history_v4" # Version 4 for safety
        table_ref = f"{self.project_id}.{dataset_id}.{table_id}"
        
        clean_results = [self._normalize_record(r) for r in results]

        schema = [
            bigquery.SchemaField("run_id", "STRING"),
            bigquery.SchemaField("timestamp", "TIMESTAMP"),
            bigquery.SchemaField("question_id", "INTEGER"),
            bigquery.SchemaField("run_number", "INTEGER"),
            bigquery.SchemaField("question", "STRING"),
            bigquery.SchemaField("expected_domain", "STRING"),
            bigquery.SchemaField("actual_domain", "STRING"),
            bigquery.SchemaField("domain_match", "BOOLEAN"),
            bigquery.SchemaField("status", "STRING"), 
            bigquery.SchemaField("reason", "STRING"),
            bigquery.SchemaField("latency_seconds", "FLOAT"),
            bigquery.SchemaField("generated_sql", "STRING"),
            bigquery.SchemaField("error_message", "STRING")
        ]

        try:
            self.client.create_dataset(dataset_id, exists_ok=True)
            try:
                self.client.get_table(table_ref)
            except Exception:
                table = bigquery.Table(table_ref, schema=schema)
                self.client.create_table(table)
            
            errors = self.client.insert_rows_json(table_ref, clean_results)
            if not errors:
                logger.info(f"✅ Logged {len(clean_results)} records to BigQuery ({table_id})")
            else:
                logger.error(f"BQ Insert Errors: {errors}")
        except Exception as e:
            logger.error(f"BQ Logging Failed: {e}")

# ==========================================
# 5. AGENT INTERFACE
# ==========================================
async def get_agent_response(user_question: str) -> Tuple[str, str]:
    session_service = InMemorySessionService()
    session_id = str(uuid.uuid4())
    session = await session_service.create_session(
        session_id=session_id, app_name="bulk-eval", user_id="tester"
    )
    
    ctx = InvocationContext(
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=root_agent,
        session=session,
        user_content=Content(parts=[Part(text=user_question)], role="user"),
        run_config=RunConfig()
    )

    try:
        async for _ in root_agent.run_async(ctx): pass
    except Exception:
        return "", "Crash"

    sql = ""
    if 'sql_generation_response' in ctx.session.state:
        data = ctx.session.state['sql_generation_response']
        try:
            if isinstance(data, dict):
                sql = data.get('sql_query') or data.get('sql') or data.get('generated_sql', '')
            elif hasattr(data, 'value'):
                sql = data.value
            else:
                sql = getattr(data, 'sql_query', '') or getattr(data, 'generated_sql', '')
        except: pass

    domain = "Unknown"
    if 'routing_response' in ctx.session.state:
        data = ctx.session.state['routing_response']
        try:
            sel = None
            if isinstance(data, dict):
                sel = data.get('selected_domain') or data.get('name')
            else:
                sel = getattr(data, 'selected_domain', None)

            if sel is not None:
                if hasattr(sel, 'value'): domain = sel.value
                elif hasattr(sel, 'name'): domain = sel.name
                else: domain = str(sel)
        except: pass

    return (str(sql).strip(), str(domain))

# ==========================================
# 6. REPORTING ENGINE (The Insights)
# ==========================================
def print_evaluation_summary(df_res: pd.DataFrame, batch_id: str):
    if df_res.empty:
        print("No results to display.")
        return

    print(f"\n\n{Fore.YELLOW}{'='*80}")
    print(f"📊  EVALUATION REPORT  (Batch: {batch_id})")
    print(f"{'='*80}{Style.RESET_ALL}")

    # --- 1. TOP LEVEL METRICS ---
    total_runs = len(df_res)
    
    # Domain Accuracy
    domain_acc_pct = df_res['domain_match'].mean() * 100
    
    # SQL Validity (Did it run without error?)
    sql_valid_pct = (df_res['status'] != 'ERROR').mean() * 100
    
    # Data Accuracy (Did it match ground truth?)
    data_match_pct = (df_res['status'] == 'PASS').mean() * 100
    
    # Latency
    avg_latency = df_res['latency_seconds'].mean()
    p95_latency = df_res['latency_seconds'].quantile(0.95)

    print(f"\n{Style.BRIGHT}📌 GLOBAL METRICS:{Style.RESET_ALL}")
    print(f"   • Total Executions:   {total_runs}")
    print(f"   • Domain Accuracy:    {Fore.CYAN}{domain_acc_pct:>6.1f}%{Style.RESET_ALL}")
    print(f"   • SQL Syntax Valid:   {Fore.BLUE}{sql_valid_pct:>6.1f}%{Style.RESET_ALL}  (Queries that executed successfully)")
    print(f"   • {Style.BRIGHT}Data Match Rate:{Style.RESET_ALL}    {Fore.GREEN}{data_match_pct:>6.1f}%{Style.RESET_ALL}  (Strict Golden Data match)")
    print(f"   • Avg Latency:        {avg_latency:>6.2f}s")
    print(f"   • P95 Latency:        {p95_latency:>6.2f}s")

    # --- 2. QUESTION-WISE BREAKDOWN ---
    print(f"\n{Style.BRIGHT}📌 DETAILED QUESTION ANALYSIS:{Style.RESET_ALL}")
    
    # Prepare Aggregation
    summary = df_res.groupby('question_id').agg(
        Question=('question', 'first'),
        Exp_Domain=('expected_domain', 'first'),
        Domain_Acc=('domain_match', lambda x: x.mean() * 100),
        SQL_Valid=('status', lambda x: (x != 'ERROR').mean() * 100),
        Data_Match=('status', lambda x: (x == 'PASS').mean() * 100),
        Avg_Time=('latency_seconds', 'mean')
    ).reset_index()

    # Print Table Header
    print(f"{'-'*110}")
    print(f"{'ID':<3} | {'Question (Truncated)':<40} | {'Domain':<10} | {'Dom %':<6} | {'SQL %':<6} | {'Data %':<6} | {'Time':<5}")
    print(f"{'-'*110}")

    for _, row in summary.iterrows():
        q_text = (row['Question'][:37] + '...') if len(str(row['Question'])) > 37 else str(row['Question'])
        
        # Color coding the Data Match %
        dm_val = row['Data_Match']
        dm_color = Fore.GREEN if dm_val == 100 else (Fore.YELLOW if dm_val > 50 else Fore.RED)
        
        print(f"{row['question_id']:<3} | {q_text:<40} | {str(row['Exp_Domain'])[:10]:<10} | "
              f"{row['Domain_Acc']:>5.0f}% | {row['SQL_Valid']:>5.0f}% | "
              f"{dm_color}{dm_val:>5.0f}%{Style.RESET_ALL} | {row['Avg_Time']:>4.1f}s")
    print(f"{'-'*110}\n")

# ==========================================
# 7. MAIN RUNNER
# ==========================================
async def run_bulk_test(runs_per_question: int = 3):
    file_name = 'golden_question_bank.csv'
    if not os.path.exists(file_name):
        logger.error(f"File {file_name} not found.")
        return

    try:
        df_gold = pd.read_csv(file_name)
    except Exception as e:
        logger.error(f"Failed to read CSV: {e}")
        return

    judge = BigQueryJudge(settings.gcp_project_id)
    batch_id = uuid.uuid4().hex[:8]
    all_results = []
    
    print(f"{Fore.CYAN}🚀 Starting Evaluation: {len(df_gold)} Questions x {runs_per_question} Runs{Style.RESET_ALL}")
    
    for idx, (index, row) in enumerate(df_gold.iterrows()):
        qid = idx + 1
        question = row.get('Questions')
        
        if pd.isna(question) or str(question).strip() == "":
            continue # Skip empty rows
            
        question = str(question)
        expected_domain = row.get('Domain', 'Unknown')
        
        # Handle Reference SQL
        raw_ref = row.get('reference_sql') or row.get('refrence_sql')
        ref_sql = str(raw_ref).strip() if not pd.isna(raw_ref) else ""

        print(f"🔸 Q{qid}...", end="", flush=True)

        # Get Ground Truth
        ref_df, ref_err = pd.DataFrame(), None
        if not ref_sql:
            ref_err = "Missing Reference SQL"
        else:
            try:
                formatted_ref = ref_sql.format(settings=settings)
            except:
                formatted_ref = ref_sql
            
            ref_df, ref_err, _ = judge.execute(formatted_ref)
        
        # Run Iterations
        for run_i in range(1, runs_per_question + 1):
            print(".", end="", flush=True) # Progress dot
            
            duration = 0.0
            bot_err = None
            
            # Execution
            bot_sql, bot_domain = await get_agent_response(question)
            
            # Domain Check
            clean_exp = str(expected_domain).lower().strip()
            clean_act = str(bot_domain).lower().strip()
            domain_match = (clean_exp == clean_act)
            
            # Data Check
            status = "FAIL"
            reason = ""
            
            if ref_err:
                 status = "SKIP"
                 reason = "Bad Reference SQL"
            else:
                bot_df, bot_err, duration = judge.execute(bot_sql)
                
                if bot_err:
                    status = "ERROR"
                    reason = f"SQL Error: {str(bot_err)[:100]}"
                else:
                    is_match, msg = judge.check_equality(ref_df, bot_df)
                    status = "PASS" if is_match else "FAIL"
                    reason = msg

            all_results.append({
                "run_id": batch_id,
                "timestamp": datetime.now(timezone.utc),
                "question_id": qid,
                "run_number": run_i,
                "question": question,
                "expected_domain": expected_domain,
                "actual_domain": bot_domain,
                "domain_match": domain_match,
                "status": status,
                "reason": reason,
                "latency_seconds": round(duration, 2),
                "generated_sql": bot_sql,
                "error_message": str(bot_err) if bot_err else None
            })
            
        print(" Done")

    # Finalize
    if all_results:
        judge.log_results_to_bq(all_results)
        df_res = pd.DataFrame(all_results)
        
        # Save CSV
        csv_path = f"bulk_results_{batch_id}.csv"
        df_res.to_csv(csv_path, index=False)
        
        # Print Insight Report
        print_evaluation_summary(df_res, batch_id)
        print(f"📄 Full CSV Report: {csv_path}")
    else:
        print("❌ No results generated.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="Runs per question")
    args = parser.parse_args()
    asyncio.run(run_bulk_test(runs_per_question=args.runs))