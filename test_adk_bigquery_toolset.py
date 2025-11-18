"""
Test ADK 1.18.0 BigQueryToolset Availability and Capabilities
"""
from config.settings import settings
import sys
import os
from pathlib import Path
import logging
import asyncio

# Setup paths
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google.auth import default


async def test_bigquery_toolset():
    """Test if BigQueryToolset is available in ADK 1.18.0"""
    
    print("\n" + "="*60)
    print("Testing ADK BigQueryToolset Availability")
    print("="*60)
    
    # Test 1: Check if BigQueryToolset exists
    print("\n[Test 1] Checking BigQueryToolset imports...")
    try:
        from google.adk.tools.bigquery import BigQueryToolset, BigQueryCredentialsConfig
        print("✅ BigQueryToolset imported successfully!")
        print(f"   Location: google.adk.tools.bigquery.BigQueryToolset")
        
        # Check available methods
        print("\n   Available methods:")
        for method in dir(BigQueryToolset):
            if not method.startswith('_'):
                print(f"   - {method}")
        
    except ImportError as e:
        print(f"❌ Failed to import BigQueryToolset: {e}")
        print("   Trying alternative imports...")
        
        # Try alternative paths
        alternatives = [
            "google.adk.tools.bigquery",
            "google.adk.models.tools.bigquery",
            "google.cloud.bigquery",
        ]
        
        for alt_path in alternatives:
            try:
                exec(f"from {alt_path} import *")
                print(f"✅ Found at: {alt_path}")
            except:
                pass
    
    # Test 2: Try to initialize BigQueryToolset
    print("\n[Test 2] Attempting to initialize BigQueryToolset...")
    try:
        from google.adk.tools.bigquery import BigQueryToolset, BigQueryCredentialsConfig
        
        credentials, project_id = default()
        credentials_config = BigQueryCredentialsConfig(credentials=credentials)
        
        # BigQueryToolset only takes credentials_config (project_id comes from credentials)
        toolset = BigQueryToolset(
            credentials_config=credentials_config
        )
        print(f"✅ BigQueryToolset initialized successfully!")
        print(f"   Project: {settings.gcp_project_id}")
        print(f"   Dataset: {settings.bigquery_dataset}")
        
        # Test 3: Check available tools
        print("\n[Test 3] Checking available tools...")
        try:
            tools = await toolset.get_tools()
            print(f"✅ get_tools() works!")
            print(f"   Available tools: {len(tools)}")
            for tool in tools[:3]:  # Show first 3 tools
                print(f"     - {tool.name if hasattr(tool, 'name') else str(tool)}")
        except Exception as e:
            print(f"⚠️  get_tools() call: {e}")
        
        # Test 4: Direct client fallback (ADK toolset is primarily for LLM integration)
        print("\n[Test 4] Note: BigQueryToolset is designed for LLM tool-calling...")
        print("   For direct schema access, use direct BigQuery client (Test 5)")
        
        # Test 5: Try direct BigQuery access
        print("\n[Test 5] (skipped - direct client tested below)")
            
    except ImportError as e:
        print(f"❌ Failed to initialize BigQueryToolset: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        return False
    
    # Test 6: Alternative - Direct BigQuery client
    print("\n[Test 6] Testing Direct BigQuery Client (Fallback)...")
    try:
        from google.cloud import bigquery
        credentials, _ = default()
        client = bigquery.Client(project=settings.gcp_project_id)
        
        table_id = f"{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}"
        table = client.get_table(table_id)
        
        print(f"✅ Direct BigQuery Client works!")
        print(f"   Table: {settings.collections_table}")
        print(f"   Columns: {len(table.schema)}")
        print(f"   First 5 columns:")
        for field in table.schema[:5]:
            print(f"     - {field.name}: {field.field_type}")
        
    except Exception as e:
        print(f"❌ Direct BigQuery Client failed: {e}")
    
    # Test 7: Fetch column descriptions from INFORMATION_SCHEMA
    print("\n[Test 7] Testing Column Descriptions from INFORMATION_SCHEMA...")
    try:
        from google.cloud import bigquery
        credentials, _ = default()
        client = bigquery.Client(project=settings.gcp_project_id)
        
        # Query INFORMATION_SCHEMA for column descriptions
        query = f"""
        SELECT column_name, description
        FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
        WHERE table_name = @table_id AND column_name IS NOT NULL
        """
        
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("table_id", "STRING", settings.collections_table)
            ]
        )
        
        query_job = client.query(query, job_config=job_config)
        results = query_job.result()
        
        descriptions = {}
        for row in results:
            if row.description:
                descriptions[row.column_name] = row.description
        
        print(f"✅ INFORMATION_SCHEMA query works!")
        print(f"   Table: {settings.collections_table}")
        print(f"   Columns with descriptions: {len(descriptions)}")
        
        if descriptions:
            print(f"   Sample descriptions (first 5):")
            for i, (col_name, desc) in enumerate(list(descriptions.items())[:5]):
                print(f"     - {col_name}: {desc[:60]}{'...' if len(desc) > 60 else ''}")
        else:
            print(f"   ⚠️  No descriptions found in INFORMATION_SCHEMA")
            print(f"      Note: Descriptions may need to be explicitly set in BigQuery")
        
    except Exception as e:
        print(f"❌ INFORMATION_SCHEMA query failed: {e}")
        print(f"   Error details: {str(e)}")
    
    print("\n" + "="*60)
    print("Testing Complete")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(test_bigquery_toolset())
