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
from datetime import datetime, timezone, date
from typing import Tuple, List, Dict, Any, Optional

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
# 2. SETUP & IMPORTS
# ==========================================
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

try:
    from config.settings import settings
    from agent import root_agent
except ImportError as e:
    print(f"{Fore.RED}❌ Critical Error: Could not load agent/settings. {e}{Style.RESET_ALL}")
    sys.exit(1)

# ==========================================
# 3. THE "GEMINI JUDGE" ENGINE (Updated)
# ==========================================
class SemanticDataJudge:
    def __init__(self):
        # Use the same SDK as your project
        location = getattr(settings, 'vertex_ai_location', 'us-central1')
        # Prefer explicit API key if provided (AI Studio), else use vertexai client
        api_key = getattr(settings, 'google_api_key', None)
        if api_key:
            # AI Studio style client using API key
            try:
                self.client = genai.Client(api_key=api_key)
            except Exception:
                self.client = genai.Client(vertexai=True, project=settings.gcp_project_id, location=location)
        else:
            self.client = genai.Client(vertexai=True, project=settings.gcp_project_id, location=location)

        # Model from settings (falls back to default in settings.py)
        self.model_name = getattr(settings, 'gemini_pro_model', 'gemini-2.5-pro')
        
    def evaluate(self, question: str, df_ref: pd.DataFrame, df_bot: pd.DataFrame) -> Tuple[bool, str]:
        """
        Hybrid Evaluation: 
        1. Tries strict match.
        2. If fails, asks Gemini if the data is semantically equivalent.
        """
        # --- TIER 1: FAST DETERMINISTIC CHECK ---
        if df_ref.empty and df_bot.empty: return True, "Exact Match (Empty)"
        if df_ref.empty: return False, "Fail: Reference Empty but Bot returned data"
        if df_bot.empty: return False, "Fail: Bot returned No Data"

        try:
            # Attempt loose numeric comparison if shapes match
            if df_ref.shape == df_bot.shape:
                # Sort first to ensure alignment
                ref_s = df_ref.sort_values(by=list(df_ref.columns)).reset_index(drop=True)
                bot_s = df_bot.sort_values(by=list(df_bot.columns)).reset_index(drop=True)
                
                # Compare numeric values strictly
                np.testing.assert_almost_equal(
                    ref_s.select_dtypes(include='number').values, 
                    bot_s.select_dtypes(include='number').values
                )
                return True, "Numeric Exact Match"
        except:
            pass

        # --- TIER 2: GEMINI SEMANTIC JUDGE ---
        ref_str = df_ref.to_markdown(index=False)
        bot_str = df_bot.head(10).to_markdown(index=False) # Check top 10 rows to save tokens

        prompt = f"""
        Act as a Senior Data QA Analyst. Determine if the 'Bot Generated Data' matches the 'Reference Data'.
        
        User Question: "{question}"

        Reference Data (Ground Truth):
        {ref_str}

        Bot Generated Data:
        {bot_str}

        **Evaluation Rules:**
        1. **Ignore Headers:** 'cnt' == 'count' == 'total'.
        2. **Ignore Formatting:** '5.5%' == '0.055'.
        3. **Ignore Extra Info:** If Bot adds helpful context columns, it PASSES.
        4. **Ignore Sort Order:** Rows can be in any order.
        5. **Pass Criteria:** Do the numbers in the Bot Data answer the user's question consistently with the Reference?

        Respond in valid JSON: {{ "match": boolean, "reason": "string" }}
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

            # Robust parsing for different response shapes
            raw_text = None
            if hasattr(response, 'text'):
                raw_text = response.text
            elif hasattr(response, 'output_text'):
                raw_text = response.output_text
            elif isinstance(response, str):
                raw_text = response
            else:
                # Try to stringify
                raw_text = str(response)

            try:
                result = json.loads(raw_text)
            except Exception:
                clean_text = raw_text.replace('```json', '').replace('```', '').strip()
                result = json.loads(clean_text)

            return result.get("match", False), f"LLM Judge: {result.get('reason', 'Unknown')}"
            
        except Exception as e:
            return False, f"LLM Judge Error: {str(e)}"

# ==========================================
# 4. CORE COMPONENT: BigQuery Execution
# ==========================================
class BigQueryExecutor:
    def __init__(self, project_id: str):
        self.client = bigquery.Client(project=project_id)
        self.project_id = project_id

    def run(self, sql: str) -> Tuple[pd.DataFrame, str, float]:
        if not isinstance(sql, str) or not sql.strip():
            return pd.DataFrame(), "No SQL Generated", 0.0

        start_ts = time.time()
        try:
            # Dry Run
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
            self.client.query(sql, job_config=job_config)
            
            # Real Run
            df = self.client.query(sql).to_dataframe()
            duration = time.time() - start_ts
            
            # Cleanup headers
            df.columns = [c.lower().strip() for c in df.columns]
            return df, "", duration
        except Exception as e:
            return pd.DataFrame(), str(e), (time.time() - start_ts)

    def log_to_bq(self, results: List[Dict]):
        if not results:
            return

        dataset_id = settings.logging_dataset
        table_id = "evaluation_history_v4"
        table_ref = f"{self.project_id}.{dataset_id}.{table_id}"

        def _normalize_record(record: Dict) -> Dict:
            new_rec = {}
            for k, v in record.items():
                if pd.isna(v):
                    new_rec[k] = None
                elif isinstance(v, (np.integer, int)):
                    new_rec[k] = int(v)
                elif isinstance(v, (np.floating, float)):
                    new_rec[k] = float(v)
                elif isinstance(v, (np.bool_, bool)):
                    new_rec[k] = bool(v)
                elif isinstance(v, (datetime, date, pd.Timestamp)):
                    new_rec[k] = v.isoformat()
                else:
                    new_rec[k] = str(v)
            return new_rec

        clean_results = [_normalize_record(r) for r in results]

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
            bigquery.SchemaField("error_message", "STRING"),
        ]

        try:
            # Ensure dataset exists
            self.client.create_dataset(dataset_id, exists_ok=True)

            # Ensure table exists
            try:
                self.client.get_table(table_ref)
            except Exception:
                table = bigquery.Table(table_ref, schema=schema)
                self.client.create_table(table)

            errors = self.client.insert_rows_json(table_ref, clean_results)
            if not errors:
                logger.info(f"✅ Logged {len(clean_results)} records to BigQuery ({table_id})")
            else:
                # Write detailed error dump for analysis
                try:
                    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    err_path = os.path.join(os.getcwd(), f"tests/bq_insert_errors_{ts}.json")
                    os.makedirs(os.path.dirname(err_path), exist_ok=True)
                    with open(err_path, 'w', encoding='utf-8') as ef:
                        json.dump({"errors": errors, "rows": clean_results}, ef, default=str, indent=2)
                    logger.error(f"BQ Insert Errors written to: {err_path}")
                except Exception:
                    logger.exception("Failed to write BQ insert error dump")

                # Attempt fallback: load_table_from_dataframe (handles schema differences better)
                try:
                    df_fallback = pd.DataFrame(clean_results)
                    load_job = self.client.load_table_from_dataframe(df_fallback, table_ref)
                    load_job.result()  # wait
                    logger.info(f"✅ Fallback load_table_from_dataframe succeeded for {len(df_fallback)} rows into {table_ref}")
                except Exception as le:
                    logger.error(f"Fallback dataframe load also failed: {le}")
        except Exception as e:
            logger.warning(f"BQ Log Error: {e}")

# ==========================================
# 5. AGENT RUNNER
# ==========================================
async def get_agent_response(user_question: str) -> Tuple[str, str]:
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

    try:
        async for _ in root_agent.run_async(ctx): pass
    except:
        return "", "Crash"

    sql = ""
    if 'sql_generation_response' in ctx.session.state:
        data = ctx.session.state['sql_generation_response']
        try:
            if isinstance(data, dict):
                sql = data.get('sql_query') or data.get('sql') or data.get('generated_sql') or data.get('value') or data.get('text', '')
            else:
                # Support response objects with attributes
                sql = getattr(data, 'sql_query', None) or getattr(data, 'generated_sql', None) or getattr(data, 'value', None) or getattr(data, 'text', '') or ''
        except:
            sql = ''

    domain = "Unknown"
    if 'routing_response' in ctx.session.state:
        data = ctx.session.state['routing_response']
        try:
            sel = None
            if isinstance(data, dict):
                sel = data.get('selected_domain') or data.get('name') or data.get('domain')
            else:
                sel = getattr(data, 'selected_domain', None) or getattr(data, 'name', None) or getattr(data, 'domain', None)

            if sel is not None:
                # support enum-like objects
                if hasattr(sel, 'value'):
                    domain = sel.value
                elif hasattr(sel, 'name'):
                    domain = sel.name
                else:
                    domain = str(sel)
        except:
            pass

    return str(sql).strip(), str(domain)

# ==========================================
# 6. MAIN STRESS TEST LOOP
# ==========================================
async def run_stress_test(runs: int = 20):
    file_name = 'golden_question_bank.csv'
    if not os.path.exists(file_name):
        print(f"{Fore.RED}❌ Missing {file_name}{Style.RESET_ALL}")
        return

    df_gold = pd.read_csv(file_name)
    # Preflight BigQuery credentials check
    def _bq_credentials_preflight():
        # Prefer explicit service account file
        gac_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if gac_path:
            if not os.path.exists(gac_path):
                print(f"{Fore.RED}❌ GOOGLE_APPLICATION_CREDENTIALS is set but file not found: {gac_path}{Style.RESET_ALL}")
                print("Set the env var to a valid service-account JSON or run 'gcloud auth application-default login'.")
                return False
            else:
                logger.info("Using GOOGLE_APPLICATION_CREDENTIALS: %s", gac_path)
        else:
            logger.info("GOOGLE_APPLICATION_CREDENTIALS not set; will attempt Application Default Credentials (ADC).")
            print("If running locally, run: gcloud auth application-default login")

        # Try to get default credentials to surface obvious errors early
        try:
            creds, project = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            logger.info("Detected credentials type: %s", type(creds))
        except Exception as e:
            print(f"{Fore.RED}❌ Failed to obtain Google credentials: {e}{Style.RESET_ALL}")
            print("Remediation: set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file, or run 'gcloud auth application-default login'.")
            return False

        # Try a minimal BigQuery call to detect metadata-server parsing problems
        try:
            test_client = bigquery.Client(project=settings.gcp_project_id)
            # lightweight call
            list(test_client.list_datasets(max_results=1))
            logger.info("BigQuery credentials appear functional for project %s", settings.gcp_project_id)
            return True
        except Exception as e:
            print(f"{Fore.RED}❌ BigQuery credential check failed: {e}{Style.RESET_ALL}")
            print("Common fixes:")
            print(" - If running locally, run: gcloud auth application-default login")
            print(" - Or set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file with BigQuery roles")
            print(" - Ensure service account has BigQuery Job User and BigQuery Data Editor roles")
            return False

    ok = _bq_credentials_preflight()
    if not ok:
        print(f"{Fore.RED}Aborting stress test due to BigQuery credential problems{Style.RESET_ALL}")
        return

    executor = BigQueryExecutor(settings.gcp_project_id)
    judge = SemanticDataJudge()
    
    batch_id = uuid.uuid4().hex[:6]
    all_results = []

    print(f"{Fore.CYAN}🔥 Starting Stress Test: {len(df_gold)} Questions x {runs} Runs{Style.RESET_ALL}")
    print(f"📝 Batch ID: {batch_id}\n")

    # Preflight diagnostics
    try:
        model_name = getattr(settings, 'gemini_pro_model', None)
        print(f"⚙️  Preflight: Project={settings.gcp_project_id} Dataset={settings.logging_dataset} JudgeModel={model_name}")
    except Exception:
        print("⚠️  Preflight: Could not read settings for project/dataset/model")

    for idx, (index, row) in enumerate(df_gold.iterrows(), start=1):
        qid = int(idx)
        question = row.get('Questions', '')
        if not question or pd.isna(question): continue
        
        exp_domain = row.get('Domain', 'Unknown')
        ref_sql_raw = row.get('reference_sql') or row.get('refrence_sql', '')
        
        # 1. Ground Truth
        print(f"{Style.BRIGHT}🔹 Question {qid}: {question[:60]}...{Style.RESET_ALL}")
        
        ref_df = pd.DataFrame()
        ref_err = None
        
        if not ref_sql_raw or pd.isna(ref_sql_raw):
            ref_err = "Missing Reference SQL"
            print(f"   {Fore.YELLOW}⚠ Skipping Ground Truth (Missing SQL){Style.RESET_ALL}")
        else:
            try:
                formatted_ref = ref_sql_raw.format(settings=settings)
            except:
                formatted_ref = ref_sql_raw
            
            ref_df, ref_err, _ = executor.run(formatted_ref)
            if ref_err:
                print(f"   {Fore.RED}❌ Reference SQL Failed: {ref_err[:50]}{Style.RESET_ALL}")

        # 2. Run Loop
        q_stats = {"pass": 0, "fail": 0, "error": 0}
        sys.stdout.write(f"   Running {runs} iterations: [")
        
        for i in range(1, runs + 1):
            # Agent
            bot_sql, bot_domain = await get_agent_response(question)
            
            # Execute
            bot_df, bot_err, duration = executor.run(bot_sql)

            # Evaluation
            status = "FAIL"
            reason = ""
            
            if ref_err:
                status = "SKIP"
                reason = "Bad Ref SQL"
            elif bot_err:
                status = "ERROR"
                reason = f"SQL Error: {bot_err[:50]}"
            else:
                # CALL THE GEMINI JUDGE
                is_match, judge_reason = judge.evaluate(question, ref_df, bot_df)
                status = "PASS" if is_match else "FAIL"
                reason = judge_reason

            dom_match = str(exp_domain).lower().strip() == str(bot_domain).lower().strip()
            if status == "PASS" and not dom_match:
                reason += " (Domain Mismatch)"

            # Visuals
            if status == "PASS": 
                q_stats["pass"] += 1
                sys.stdout.write(f"{Fore.GREEN}•{Style.RESET_ALL}")
            elif status == "ERROR": 
                q_stats["error"] += 1
                sys.stdout.write(f"{Fore.RED}E{Style.RESET_ALL}")
            else: 
                q_stats["fail"] += 1
                sys.stdout.write(f"{Fore.YELLOW}x{Style.RESET_ALL}")
            sys.stdout.flush()

            all_results.append({
                "run_id": batch_id,
                "timestamp": datetime.now(timezone.utc),
                "question_id": qid,
                "run_number": i,
                "question": question,
                "expected_domain": exp_domain,
                "actual_domain": bot_domain,
                "domain_match": dom_match,
                "status": status,
                "reason": reason,
                "latency_seconds": round(duration, 2),
                "latency": round(duration, 2),
                "generated_sql": bot_sql,
                "error_message": bot_err if bot_err else None
            })

        print(f"] {Fore.CYAN}{q_stats['pass']}/{runs} Passed{Style.RESET_ALL}")

    # ==========================================
    # 7. REPORTING
    # ==========================================
    if all_results:
        df_res = pd.DataFrame(all_results)
        
        print(f"\n\n{Fore.WHITE}{'='*80}")
        print(f"📢  STAKEHOLDER RELIABILITY REPORT  (Batch: {batch_id})")
        print(f"{'='*80}{Style.RESET_ALL}")

        pass_rate = (df_res['status'] == 'PASS').mean() * 100
        avg_lat = df_res['latency'].mean()
        print(f"\n{Style.BRIGHT}💎 SYSTEM RELIABILITY: {Fore.GREEN if pass_rate > 80 else Fore.RED}{pass_rate:.1f}%{Style.RESET_ALL}")
        print(f"⏱️  Average Latency: {avg_lat:.2f}s")

        print(f"\n{Style.BRIGHT}📊 QUESTION MATRIX:{Style.RESET_ALL}")
        print(f"{'-'*100}")
        print(f"{'ID':<3} | {'Question':<40} | {'Pass%':<7} | {'Latency':<7} | {'Top Failure Reason'}")
        print(f"{'-'*100}")

        for qid, group in df_res.groupby('question_id'):
            q_text = group['question'].iloc[0][:38] + ".."
            p_rate = (group['status'] == 'PASS').mean() * 100
            avg_t = group['latency'].mean()
            
            fails = group[group['status'] != 'PASS']
            top_reason = fails['reason'].value_counts().index[0][:35] if not fails.empty else "N/A"

            color = Fore.GREEN if p_rate >= 95 else (Fore.YELLOW if p_rate >= 80 else Fore.RED)
            print(f"{qid:<3} | {q_text:<40} | {color}{p_rate:>5.1f}%{Style.RESET_ALL} | {avg_t:>5.2f}s | {top_reason}")
        
        print(f"{'-'*100}\n")
        
        csv_name = f"stress_test_report_{batch_id}.csv"
        df_res.to_csv(csv_name, index=False)
        print(f"📄 CSV Saved: {csv_name}")
        executor.log_to_bq(all_results)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="Runs per question")
    args = parser.parse_args()
    asyncio.run(run_stress_test(args.runs))