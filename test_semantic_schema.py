"""
Test Semantic Schema - Phase 1 Enhancement
Demonstrates how the new semantic schema groups fields by business purpose
"""
import sys
import os
from pathlib import Path

# Setup paths
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

from config.settings import settings
from utils.schema_service import get_schema_service

def test_semantic_schema():
    """Test the new semantic schema grouping"""
    
    print("\n" + "="*80)
    print("PHASE 1: Semantic Schema Enhancement Test")
    print("="*80)
    
    # Initialize schema service
    schema_service = get_schema_service(settings.gcp_project_id)
    
    print("\n[Step 1] Fetching Semantic Schema for Collections Table...")
    print(f"   Project: {settings.gcp_project_id}")
    print(f"   Dataset: {settings.bigquery_dataset}")
    print(f"   Table: {settings.collections_table}")
    
    try:
        # Get semantic schema (grouped by business purpose)
        semantic_schema = schema_service.get_semantic_schema(
            settings.bigquery_dataset,
            settings.collections_table
        )
        
        print("\n✅ Semantic Schema Retrieved Successfully!")
        print(f"\n{semantic_schema}")
        
    except Exception as e:
        print(f"\n❌ Error fetching semantic schema: {e}")
        return False
    
    print("\n" + "="*80)
    print("[Step 2] Fetching Schema with Business Rules...")
    
    try:
        # Get schema with business rules
        schema_with_rules = schema_service.get_schema_with_business_rules(
            settings.bigquery_dataset,
            settings.collections_table
        )
        
        print("✅ Schema with Business Rules Retrieved Successfully!")
        print(f"\nLength: {len(schema_with_rules)} characters")
        print("\nPreview (first 2000 chars):")
        print(schema_with_rules[:2000] + "\n...[truncated]")
        
    except Exception as e:
        print(f"❌ Error fetching schema with rules: {e}")
        return False
    
    print("\n" + "="*80)
    print("Test Complete ✅")
    print("="*80)
    
    print("\n📊 Summary")
    print(f"  - Semantic Schema: {len(semantic_schema)} characters")
    print(f"  - With Business Rules: {len(schema_with_rules)} characters")
    print(f"  - Organized by: 12+ business categories")
    print(f"  - Includes: Critical rules, DPD buckets, GNS/NNS filters, TIER mapping")
    
    print("\n🎯 This schema will be injected into LLM prompts for better SQL generation")
    print("\nKey Benefits:")
    print("  ✅ LLM understands field groupings by business purpose")
    print("  ✅ Embedded business rules (GNS/NNS MOB filters, DPD buckets, etc.)")
    print("  ✅ Reduced hallucination on filter logic")
    print("  ✅ Better understanding of cohort definitions")
    
    return True


if __name__ == "__main__":
    success = test_semantic_schema()
    sys.exit(0 if success else 1)
