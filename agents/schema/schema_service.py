"""
Schema Service - Fetches and caches BigQuery table schema
Works with both ADK BigQueryToolset and direct BigQuery client
"""
from utils.json_logger import get_json_logger
import time
from typing import Dict, Optional
from google.cloud import bigquery
from google.auth import default

logger = get_json_logger(__name__)


class SchemaService:
    """
    Manages BigQuery schema fetching and caching.
    
    - Fetches schema from INFORMATION_SCHEMA
    - Caches results to minimize API calls
    - Includes sample data (5 rows) for LLM context
    - Formats schema as readable markdown table
    """
    
    def __init__(self, project_id: str, dataset_id: str, cache_ttl: int = 300):
        """
        Initialize SchemaService
        
        Args:
            project_id: GCP project ID
            dataset_id: BigQuery dataset ID
            cache_ttl: Cache time-to-live in seconds (default: 5 min)
        """
        self.project_id = project_id
        self.dataset_id = dataset_id
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, tuple] = {}  # {table_id: (schema_info, timestamp)}
        
        # Initialize BigQuery client
        try:
            credentials, _ = default()
            self.client = bigquery.Client(
                credentials=credentials, 
                project=project_id
            )
            logger.info(f"SchemaService initialized for {project_id}.{dataset_id}")
        except Exception as e:
            logger.error(f"Failed to initialize BigQuery client: {e}")
            self.client = None
    
    def get_table_schema_md(self, table_id: str) -> str:
        """
        Get table schema as markdown formatted string
        
        Format:
            Column Name | Data Type | Description
            ------------|-----------|-------------
            field1      | STRING    | Description here
            field2      | TIMESTAMP | Description here
        """
        # Check cache
        if table_id in self._cache:
            schema_info, cached_at = self._cache[table_id]
            if time.time() - cached_at < self.cache_ttl:
                logger.debug(f"Schema cache hit for {table_id}")
                return schema_info
        
        if not self.client:
            return "Schema unavailable - BigQuery client not initialized"
        
        try:
            # Fetch table metadata
            table_ref = f"{self.project_id}.{self.dataset_id}.{table_id}"
            table = self.client.get_table(table_ref)
            
            # Build markdown table
            schema_lines = [
                "| Column Name | Data Type | Description |",
                "|-------------|-----------|-------------|"
            ]
            
            for field in table.schema:
                desc = field.description if field.description else "N/A"
                # Escape pipe characters in description
                desc = desc.replace("|", "\\|")
                schema_lines.append(
                    f"| {field.name} | {field.field_type} | {desc} |"
                )
            
            schema_info = "\n".join(schema_lines)
            
            # Cache it
            self._cache[table_id] = (schema_info, time.time())
            logger.info(f"Fetched schema for {table_id}: {len(table.schema)} columns")
            
            return schema_info
            
        except Exception as e:
            logger.error(f"Error fetching schema for {table_id}: {e}")
            return f"Schema unavailable: {str(e)}"
    
    def get_table_schema_list(self, table_id: str) -> str:
        """
        Get table schema as comma-separated list of "column: type"
        
        Useful for compact prompt injection
        Example: "Columns: name (STRING), age (INT64), created (TIMESTAMP)"
        """
        if not self.client:
            return "Schema unavailable"
        
        try:
            table_ref = f"{self.project_id}.{self.dataset_id}.{table_id}"
            table = self.client.get_table(table_ref)
            
            columns = [f"{field.name} ({field.field_type})" for field in table.schema]
            return "Columns: " + ", ".join(columns)
            
        except Exception as e:
            logger.error(f"Error fetching schema list for {table_id}: {e}")
            return "Schema unavailable"
    
    def get_table_schema_with_samples(self, table_id: str, sample_limit: int = 5) -> Dict:
        """
        Get schema AND sample data (for context-rich prompt injection)
        
        Returns:
            {
                "schema": "markdown table",
                "columns": ["col1", "col2", ...],
                "sample_rows": [{"col1": val, "col2": val}, ...],
                "row_count": 12345
            }
        """
        if not self.client:
            return {"error": "BigQuery client not initialized"}
        
        try:
            table_ref = f"{self.project_id}.{self.dataset_id}.{table_id}"
            table = self.client.get_table(table_ref)
            
            # Get schema
            schema_md = self.get_table_schema_md(table_id)
            columns = [field.name for field in table.schema]
            
            # Get sample rows
            query = f"SELECT * FROM `{table_ref}` LIMIT {sample_limit}"
            df = self.client.query(query).result().to_dataframe()
            sample_rows = df.to_dict(orient="records")
            
            result = {
                "schema": schema_md,
                "columns": columns,
                "sample_rows": sample_rows,
                "row_count": table.num_rows,
                "table_id": table_id
            }
            
            logger.info(f"Fetched schema + samples for {table_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error fetching schema + samples: {e}")
            return {"error": str(e)}
    
    def get_timestamp_columns(self, table_id: str) -> list:
        """Get list of TIMESTAMP columns (critical for type casting)"""
        if not self.client:
            return []
        
        try:
            table_ref = f"{self.project_id}.{self.dataset_id}.{table_id}"
            table = self.client.get_table(table_ref)
            
            timestamp_cols = [
                field.name for field in table.schema 
                if field.field_type == "TIMESTAMP"
            ]
            
            return timestamp_cols
            
        except Exception as e:
            logger.error(f"Error fetching TIMESTAMP columns: {e}")
            return []
    
    def clear_cache(self, table_id: Optional[str] = None):
        """Clear cache for specific table or all tables"""
        if table_id:
            if table_id in self._cache:
                del self._cache[table_id]
                logger.info(f"Cleared cache for {table_id}")
        else:
            self._cache.clear()
            logger.info("Cleared all schema cache")


# ---------------------- USAGE EXAMPLES ----------------------

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    
    # For testing - set these in .env
    from config.settings import settings
    
    service = SchemaService(
        settings.gcp_project_id,
        settings.bigquery_dataset
    )
    
    # Example 1: Get schema as markdown
    logger.info("=== Example 1: Schema as Markdown ===")
    collections_schema = service.get_table_schema_md(settings.collections_table)
    logger.info(collections_schema)

    # Example 2: Get schema as compact list
    logger.info("=== Example 2: Schema as Compact List ===")
    schema_list = service.get_table_schema_list(settings.collections_table)
    logger.info(schema_list)

    # Example 3: Get schema with samples
    logger.info("=== Example 3: Schema with Sample Data ===")
    schema_with_samples = service.get_table_schema_with_samples(settings.collections_table)
    logger.info(f"Columns: {schema_with_samples['columns']}")
    logger.info(f"Sample rows: {len(schema_with_samples['sample_rows'])}")
    logger.info(f"Total rows in table: {schema_with_samples['row_count']}")

    # Example 4: Get TIMESTAMP columns
    logger.info("=== Example 4: TIMESTAMP Columns ===")
    timestamp_cols = service.get_timestamp_columns(settings.collections_table)
    logger.info(f"TIMESTAMP fields: {timestamp_cols}")

    # Example 5: Using in a prompt
    logger.info("=== Example 5: Augmented Prompt (for agent) ===")
    schema_md = service.get_table_schema_md(settings.collections_table)
    prompt = f"""
You are a BigQuery SQL expert for collections data.

## Table Schema
{schema_md}

## CRITICAL Rules
1. Use column names EXACTLY as they appear in the schema
2. Check column types - if TIMESTAMP, use DATE() casting for date filtering
3. Generate ONLY the SQL wrapped in ```sql ``` tags

## Question
{{'What is 0+ dpd for last 6 months?'}}

Response:
    """
    logger.info(prompt)
