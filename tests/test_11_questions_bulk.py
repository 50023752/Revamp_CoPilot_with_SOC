"""
Bulk Test Script - 11 Questions x 100 Runs
Tests agent accuracy, consistency, and performance across key business questions
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd
from typing import Dict, List
import uuid

# Setup paths
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from utils.json_logger import get_json_logger
from google.cloud import bigquery
from google.auth import default

logger = get_json_logger(__name__)

# Test questions with expected domain
TEST_QUESTIONS = [
    {
        "id": 1,
        "question": "What is GNS4 trend for last 4 months?",
        "expected_domain": "COLLECTIONS",
        "category": "GNS_Metrics"
    },
    {
        "id": 2,
        "question": "Give me top 10 dealer names that have the highest NNS3 percentage and cases are more than 30 per dealer? Show the number of cases.",
        "expected_domain": "COLLECTIONS",
        "category": "NNS_Metrics"
    },
    {
        "id": 3,
        "question": "Share me the dealer which has the lowest banking hit ratio in applications?",
        "expected_domain": "SOURCING",
        "category": "Banking_Metrics"
    },
    {
        "id": 4,
        "question": "Which state has the highest roll forward rate for the current month in DPD BKT 2?",
        "expected_domain": "COLLECTIONS",
        "category": "Roll_Forward"
    },
    {
        "id": 5,
        "question": "SO which FLS in Gujrat state has the highest disbursement number in last month?",
        "expected_domain": "SOURCING",
        "category": "Disbursement"
    },
    {
        "id": 6,
        "question": "Show me the DMI having highest RC pendency?",
        "expected_domain": "SOURCING",
        "category": "RC_Pendency"
    },
    {
        "id": 7,
        "question": "Show me the performance of TVS XL model where loan amount < 60000?",
        "expected_domain": "COLLECTIONS",
        "category": "Product_Performance"
    },
    {
        "id": 8,
        "question": "What is the disbursal trend of Superbikes?",
        "expected_domain": "SOURCING",
        "category": "Disbursement"
    },
    {
        "id": 9,
        "question": "SHow me the bounce rate on 0 dpd for Assam?",
        "expected_domain": "COLLECTIONS",
        "category": "Bounce_Rate"
    },
    {
        "id": 10,
        "question": "What is the rejection based on CIBIL score below 650?",
        "expected_domain": "SOURCING",
        "category": "CIBIL_Rejection"
    },
    {
        "id": 11,
        "question": "What is the rejection rate for applications where CIBIL score is between 300-650 in October 2025? Also list out the reason of rejections for these cases.",
        "expected_domain": "SOURCING",
        "category": "CIBIL_Rejection"
    }
]


async def run_single_test(question_data: Dict, run_number: int, session_id: str) -> Dict:
    """Run a single test iteration"""
    from agent import root_agent
    from google.adk.sessions import InMemorySessionService
    from google.adk.agents import InvocationContext, RunConfig
    from google.genai.types import Content, Part
    
    session_service = InMemorySessionService()
    
    try:
        # Create session
        session = await session_service.create_session(
            session_id=session_id,
            app_name="bulk-test",
            user_id="test_runner"
        )
        
        # Build context
        user_content = Content(parts=[Part(text=question_data["question"])], role="user")
        ctx = InvocationContext(
            session_service=session_service,
            invocation_id=str(uuid.uuid4()),
            agent=root_agent,
            session=session,
            user_content=user_content,
            run_config=RunConfig()
        )
        
        # Track timing
        start_time = datetime.now(timezone.utc)
        
        # Run agent
        response_parts = []
        selected_domain = "Unknown"
        sql_query = ""
        
        async for event in root_agent.run_async(ctx):
            if hasattr(event, 'content') and event.content:
                if hasattr(event.content, 'parts'):
                    for part in event.content.parts:
                        if hasattr(part, 'text') and part.text:
                            response_parts.append(part.text)
            
            # Extract domain
            if 'routing_response' in ctx.session.state:
                data = ctx.session.state['routing_response']
                if isinstance(data, dict):
                    selected_domain = data.get('selected_domain', 'Unknown')
                else:
                    selected_domain = getattr(data, 'selected_domain', 'Unknown')
                    if hasattr(selected_domain, 'value'):
                        selected_domain = selected_domain.value
            
            # Extract SQL
            if 'sql_generation_response' in ctx.session.state:
                sql_data = ctx.session.state['sql_generation_response']
                if isinstance(sql_data, dict):
                    sql_query = sql_data.get('sql_query', '')
        
        end_time = datetime.now(timezone.utc)
        duration_ms = (end_time - start_time).total_seconds() * 1000
        
        response_text = "\n".join(response_parts) if response_parts else "No response"
        
        # Determine success
        domain_correct = selected_domain.upper() == question_data["expected_domain"]
        sql_generated = bool(sql_query and sql_query.strip())
        
        return {
            "question_id": question_data["id"],
            "run_number": run_number,
            "question": question_data["question"],
            "expected_domain": question_data["expected_domain"],
            "actual_domain": selected_domain,
            "domain_correct": domain_correct,
            "sql_generated": sql_generated,
            "sql_query": sql_query[:500] if sql_query else "",  # Truncate for storage
            "response_length": len(response_text),
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": question_data["category"],
            "success": domain_correct and sql_generated
        }
        
    except Exception as e:
        logger.error(f"Test failed for Q{question_data['id']} run {run_number}: {e}", exc_info=True)
        return {
            "question_id": question_data["id"],
            "run_number": run_number,
            "question": question_data["question"],
            "expected_domain": question_data["expected_domain"],
            "actual_domain": "ERROR",
            "domain_correct": False,
            "sql_generated": False,
            "sql_query": "",
            "response_length": 0,
            "duration_ms": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": question_data["category"],
            "success": False,
            "error": str(e)
        }


async def run_bulk_test(num_runs: int = 100, save_to_bq: bool = True):
    """Run all questions N times and collect metrics"""
    logger.info(f"Starting bulk test: {len(TEST_QUESTIONS)} questions x {num_runs} runs = {len(TEST_QUESTIONS) * num_runs} total tests")
    
    all_results = []
    
    for run_num in range(1, num_runs + 1):
        logger.info(f"=== Run {run_num}/{num_runs} ===")
        
        for question_data in TEST_QUESTIONS:
            session_id = f"bulk_test_{question_data['id']}_run{run_num}_{uuid.uuid4().hex[:8]}"
            
            result = await run_single_test(question_data, run_num, session_id)
            all_results.append(result)
            
            status = "✅" if result["success"] else "❌"
            logger.info(f"  {status} Q{question_data['id']} - Domain: {result['actual_domain']} ({result['duration_ms']:.0f}ms)")
        
        # Progress update every 10 runs
        if run_num % 10 == 0:
            completed = len(all_results)
            success_count = sum(1 for r in all_results if r["success"])
            logger.info(f"Progress: {completed} tests completed, {success_count} successful ({success_count/completed*100:.1f}%)")
    
    # Generate summary
    df = pd.DataFrame(all_results)
    
    summary = {
        "total_tests": len(all_results),
        "successful_tests": df["success"].sum(),
        "success_rate": f"{df['success'].mean() * 100:.2f}%",
        "avg_duration_ms": f"{df['duration_ms'].mean():.2f}",
        "domain_accuracy": f"{df['domain_correct'].mean() * 100:.2f}%",
        "sql_generation_rate": f"{df['sql_generated'].mean() * 100:.2f}%",
        "by_question": df.groupby("question_id").agg({
            "success": ["count", "sum", "mean"],
            "duration_ms": "mean",
            "domain_correct": "mean"
        }).to_dict(),
        "by_category": df.groupby("category").agg({
            "success": ["count", "sum", "mean"],
            "duration_ms": "mean"
        }).to_dict()
    }
    
    # Save results
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    
    # Save to CSV
    csv_path = project_root / f"tests/results/bulk_test_{timestamp}.csv"
    csv_path.parent.mkdir(exist_ok=True)
    df.to_csv(csv_path, index=False)
    logger.info(f"Results saved to: {csv_path}")
    
    # Save summary to JSON
    summary_path = project_root / f"tests/results/summary_{timestamp}.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    logger.info(f"Summary saved to: {summary_path}")
    
    # Print summary
    print("\n" + "="*80)
    print("BULK TEST SUMMARY")
    print("="*80)
    print(f"Total Tests: {summary['total_tests']}")
    print(f"Successful: {summary['successful_tests']} ({summary['success_rate']})")
    print(f"Domain Accuracy: {summary['domain_accuracy']}")
    print(f"SQL Generation Rate: {summary['sql_generation_rate']}")
    print(f"Avg Duration: {summary['avg_duration_ms']}ms")
    print("="*80)
    
    # Per-question summary
    print("\nPER-QUESTION RESULTS:")
    for q in TEST_QUESTIONS:
        q_results = df[df["question_id"] == q["id"]]
        success_rate = q_results["success"].mean() * 100
        avg_time = q_results["duration_ms"].mean()
        print(f"Q{q['id']:2d} ({q['expected_domain']:12s}): {success_rate:5.1f}% success, {avg_time:6.0f}ms avg - {q['question'][:60]}...")
    
    # Save to BigQuery if requested
    if save_to_bq:
        try:
            credentials, _ = default()
            client = bigquery.Client(credentials=credentials, project=settings.gcp_project_id)
            
            # Ensure test results table exists
            dataset_id = settings.logging_dataset
            table_id = "bulk_test_results"
            table_ref = f"{settings.gcp_project_id}.{dataset_id}.{table_id}"
            
            # Create table if not exists
            schema = [
                bigquery.SchemaField("question_id", "INTEGER"),
                bigquery.SchemaField("run_number", "INTEGER"),
                bigquery.SchemaField("question", "STRING"),
                bigquery.SchemaField("expected_domain", "STRING"),
                bigquery.SchemaField("actual_domain", "STRING"),
                bigquery.SchemaField("domain_correct", "BOOLEAN"),
                bigquery.SchemaField("sql_generated", "BOOLEAN"),
                bigquery.SchemaField("sql_query", "STRING"),
                bigquery.SchemaField("response_length", "INTEGER"),
                bigquery.SchemaField("duration_ms", "FLOAT"),
                bigquery.SchemaField("timestamp", "STRING"),
                bigquery.SchemaField("category", "STRING"),
                bigquery.SchemaField("success", "BOOLEAN"),
            ]
            
            try:
                client.get_table(table_ref)
            except:
                table = bigquery.Table(table_ref, schema=schema)
                client.create_table(table, exists_ok=True)
                logger.info(f"Created table: {table_ref}")
            
            # Insert results
            errors = client.insert_rows_json(table_ref, all_results)
            if errors:
                logger.warning(f"BigQuery insert errors: {errors}")
            else:
                logger.info(f"Saved {len(all_results)} results to BigQuery: {table_ref}")
        except Exception as e:
            logger.error(f"Failed to save to BigQuery: {e}", exc_info=True)
    
    return df, summary


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run bulk test of 11 questions")
    parser.add_argument("--runs", type=int, default=100, help="Number of runs per question (default: 100)")
    parser.add_argument("--no-bq", action="store_true", help="Skip saving to BigQuery")
    
    args = parser.parse_args()
    
    try:
        df, summary = asyncio.run(run_bulk_test(num_runs=args.runs, save_to_bq=not args.no_bq))
        print("\n✅ Bulk test completed successfully!")
    except KeyboardInterrupt:
        print("\n⚠️ Test interrupted by user")
    except Exception as e:
        logger.error(f"Bulk test failed: {e}", exc_info=True)
        print(f"\n❌ Bulk test failed: {e}")
        sys.exit(1)
