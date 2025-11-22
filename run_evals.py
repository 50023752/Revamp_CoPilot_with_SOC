import asyncio
import pandas as pd
import uuid
import sys
import os
import warnings
import logging
from datetime import datetime, timezone
from typing import Tuple, List, Dict, Any
from google.cloud import bigquery
from google.adk.sessions import InMemorySessionService
from google.adk.agents import InvocationContext, RunConfig
from google.genai.types import Content, Part
from colorama import Fore, Style, init
import argparse

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
# 1. AGENT & SETTINGS SETUP
# ==========================================
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

try:
    from config.settings import settings
    from agent import root_agent
    logger.info(f"✅ Loaded Agent & Settings (Project: {settings.gcp_project_id})")
except ImportError as e:
    logger.error(f"❌ CRITICAL ERROR: Could not import agent module. {e}")
    sys.exit(1)

# ==========================================
# 2. BIGQUERY JUDGE (The Core Logic)
# ==========================================
class BigQueryJudge:
    def __init__(self, project_id: str):
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id

    def execute(self, sql: str) -> Tuple[pd.DataFrame, str, float]:
        """Executes SQL. Returns (DataFrame, ErrorMessage, DurationSeconds)."""
        if not sql or not sql.strip():
            return pd.DataFrame(), "No SQL Generated", 0.0

        start_ts = datetime.now()
        try:
            # Dry Run first
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
            self.client.query(sql, job_config=job_config)
            
            # Real Run
            query_job = self.client.query(sql)
            df = query_job.to_dataframe()
            
            duration = (datetime.now() - start_ts).total_seconds()
            
            # Normalize columns
            df.columns = [c.lower() for c in df.columns]
            return df, "", duration
            
        except Exception as e:
            duration = (datetime.now() - start_ts).total_seconds()
            return pd.DataFrame(), str(e), duration

    def check_equality(self, df_ref: pd.DataFrame, df_bot: pd.DataFrame) -> Tuple[bool, str]:
        """Compares two DataFrames robustly."""
        if df_ref.empty and df_bot.empty: return True, "Both Empty (Match)"
        if df_ref.empty or df_bot.empty: return False, "One Empty (Mismatch)"
        if df_ref.shape != df_bot.shape: return False, f"Shape Mismatch: {df_ref.shape} vs {df_bot.shape}"

        try:
            # Sort columns
            df_ref = df_ref.reindex(sorted(df_ref.columns), axis=1)
            df_bot = df_bot.reindex(sorted(df_bot.columns), axis=1)
            
            # Sort rows deterministically
            sort_cols = list(df_ref.columns)
            df_ref_sorted = df_ref.sort_values(by=sort_cols).reset_index(drop=True)
            df_bot_sorted = df_bot.sort_values(by=sort_cols).reset_index(drop=True)
            
            pd.testing.assert_frame_equal(df_ref_sorted, df_bot_sorted, check_dtype=False, check_column_type=False)
            return True, "Exact Match"
        except AssertionError:
            return False, "Content Mismatch"
        except Exception as e:
            return False, f"Comparison Error: {e}"

    def log_results_to_bq(self, results: List[Dict]):
        """Writes bulk results to BigQuery for dashboards."""
        if not results:
            return
            
        dataset_id = settings.logging_dataset
        table_id = "evaluation_history"
        table_ref = f"{self.project_id}.{dataset_id}.{table_id}"
        
        # Enhanced Schema including Domain Checks
        schema = [
            bigquery.SchemaField("run_id", "STRING"),
            bigquery.SchemaField("timestamp", "TIMESTAMP"),
            bigquery.SchemaField("question_id", "INTEGER"),
            bigquery.SchemaField("run_number", "INTEGER"),
            bigquery.SchemaField("question", "STRING"),
            # Domain Metrics
            bigquery.SchemaField("expected_domain", "STRING"),
            bigquery.SchemaField("actual_domain", "STRING"),
            bigquery.SchemaField("domain_match", "BOOLEAN"),
            # SQL/Data Metrics
            bigquery.SchemaField("status", "STRING"),  # PASS / FAIL / ERROR
            bigquery.SchemaField("reason", "STRING"),
            bigquery.SchemaField("latency_seconds", "FLOAT"),
            bigquery.SchemaField("generated_sql", "STRING"),
            bigquery.SchemaField("error_message", "STRING")
        ]

        try:
            self.client.create_dataset(dataset_id, exists_ok=True)
            
            try:
                table = self.client.get_table(table_ref)
                # Auto-update schema: add missing fields
                existing = {f.name for f in table.schema}
                new_fields = [f for f in schema if f.name not in existing]
                
                if new_fields:
                    logger.info(f"Adding missing fields to BQ: {[f.name for f in new_fields]}")
                    table.schema = list(table.schema) + new_fields
                    self.client.update_table(table, ["schema"])
            except Exception:
                # Create table
                table = bigquery.Table(table_ref, schema=schema)
                self.client.create_table(table)
                logger.info(f"✅ Created table {table_ref}")
            
            # Insert
            errors = self.client.insert_rows_json(table_ref, results)
            if errors:
                logger.error(f"BQ Insert Errors: {errors}")
            else:
                logger.info(f"✅ Logged {len(results)} records to {table_ref}")
                
        except Exception as e:
            logger.error(f"Failed to log to BigQuery: {e}")

# ==========================================
# 3. AGENT RUNNER (Updated with Domain Extraction)
# ==========================================
async def get_agent_response(user_question: str) -> Tuple[str, str]:
    """Returns (SQL, Domain)"""
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
    except Exception as e:
        logger.debug(f"Agent crash: {e}")
        return "", "Error"

    # 1. Extract SQL
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

    # 2. Extract Domain
    domain = "Unknown"
    if 'routing_response' in ctx.session.state:
        data = ctx.session.state['routing_response']
        try:
            sel = None
            if isinstance(data, dict):
                sel = data.get('selected_domain') or data.get('name')
            else:
                sel = getattr(data, 'selected_domain', None)

            # Handle Enum or Object
            if hasattr(sel, 'value'): 
                domain = sel.value
            elif hasattr(sel, 'name'): 
                domain = sel.name
            else: 
                domain = str(sel)
        except: pass

    return (sql.strip() if isinstance(sql, str) else ""), str(domain)

# ==========================================
# 4. MULTI-RUN ORCHESTRATOR
# ==========================================
async def run_bulk_test(runs_per_question: int = 3):
    file_name = 'golden_question_bank.csv'
    if not os.path.exists(file_name):
        logger.error(f"File {file_name} not found.")
        return

    df_gold = pd.read_csv(file_name)
    judge = BigQueryJudge(settings.gcp_project_id)
    batch_id = uuid.uuid4().hex[:8]
    all_results = []
    
    print(f"{Fore.CYAN}🚀 Starting Bulk Eval (Data + Domain): {len(df_gold)} Qs x {runs_per_question} Runs{Style.RESET_ALL}")
    
    for idx, (index, row) in enumerate(df_gold.iterrows()):
        qid = idx + 1
        question = row['Questions']
        expected_domain = row.get('Domain', 'Unknown') # Read Domain from CSV
        raw_ref = row.get('refrence_sql') or row.get('reference_sql', '')
        
        # Format Reference SQL
        try:
            ref_sql = raw_ref.format(settings=settings)
        except:
            ref_sql = raw_ref

        # Ground Truth Check
        print(f"\n🔸 {Style.BRIGHT}Q{qid}: {question[:50]}... {Style.RESET_ALL}[Exp: {expected_domain}]")
        ref_df, ref_err, _ = judge.execute(ref_sql)
        
        if ref_err:
            print(f"   {Fore.RED}❌ Bad Reference SQL{Style.RESET_ALL}")
            continue

        # Run Loop
        for run_i in range(1, runs_per_question + 1):
            sys.stdout.write(f"\r   Run {run_i}/{runs_per_question}...")
            sys.stdout.flush()
            
            # A. Get Agent Response (SQL + Domain)
            bot_sql, bot_domain = await get_agent_response(question)
            
            # B. Domain Check
            # Normalize strings for comparison
            domain_match = str(expected_domain).lower().strip() == str(bot_domain).lower().strip()
            
            # C. SQL Data Check
            bot_df, bot_err, duration = judge.execute(bot_sql)
            
            status = "FAIL"
            reason = ""
            
            if bot_err:
                status = "ERROR"
                reason = f"SQL Error: {bot_err}"
            else:
                is_match, msg = judge.check_equality(ref_df, bot_df)
                if is_match:
                    status = "PASS"
                    reason = "Exact Match"
                else:
                    status = "FAIL"
                    reason = msg

            # Warn if Domain Failed but Data Passed (Rare but possible)
            if status == "PASS" and not domain_match:
                reason += " (⚠️ Domain Mismatch)"

            record = {
                "run_id": batch_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
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
                "error_message": bot_err if bot_err else None
            }
            all_results.append(record)
            
        print(f" {Fore.GREEN}Done{Style.RESET_ALL}")

    # Logging
    judge.log_results_to_bq(all_results)
    
    # Summary
    df_res = pd.DataFrame(all_results)
    print(f"\n{Fore.YELLOW}📊 SUMMARY (Batch: {batch_id}){Style.RESET_ALL}")
    print("="*70)
    
    summary = df_res.groupby('question_id').agg(
        domain_acc=('domain_match', lambda x: x.mean() * 100),
        data_pass_rate=('status', lambda x: (x == 'PASS').mean() * 100),
        avg_latency=('latency_seconds', 'mean')
    )
    
    print(summary.to_markdown(floatfmt=".1f"))
    print(f"\n📄 Detailed results saved to BQ and 'bulk_results_{batch_id}.csv'")
    df_res.to_csv(f"bulk_results_{batch_id}.csv", index=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="Runs per question")
    args = parser.parse_args()
    
    asyncio.run(run_bulk_test(runs_per_question=args.runs))