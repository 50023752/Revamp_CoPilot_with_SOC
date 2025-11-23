"""
Schema Service - Fetch and cache BigQuery table schemas
Provides column names, types, and descriptions to LLM agents
Includes semantic grouping for business context
"""

from utils.json_logger import get_json_logger
from typing import Dict, Optional, List
from google.cloud import bigquery
from google.auth import default
import json

logger = get_json_logger(__name__)

# Global schema cache to avoid repeated BigQuery API calls
_SCHEMA_CACHE: Dict[str, Dict] = {}


class SchemaService:
    """Service to fetch and format BigQuery table schemas"""
    
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize BigQuery client with proper credential handling"""
        try:
            # Use default credentials with explicit scopes to avoid compute engine metadata issues
            credentials, project = default(scopes=['https://www.googleapis.com/auth/bigquery'])
            
            # Use project from credentials if not explicitly set
            actual_project = self.project_id or project
            
            self.client = bigquery.Client(
                credentials=credentials, 
                project=actual_project,
                default_query_job_config=bigquery.QueryJobConfig(
                    use_query_cache=True,
                    use_legacy_sql=False
                )
            )
            logger.info(f"SchemaService initialized for project: {actual_project}")
        except Exception as e:
            logger.error(f"Failed to initialize BigQuery client: {e}", exc_info=True)
            # Try fallback: use application default without compute engine
            try:
                from google.auth.credentials import AnonymousCredentials
                import os
                
                # Check if GOOGLE_APPLICATION_CREDENTIALS is set
                if 'GOOGLE_APPLICATION_CREDENTIALS' in os.environ:
                    logger.info("Retrying with service account from GOOGLE_APPLICATION_CREDENTIALS")
                    credentials, project = default(scopes=['https://www.googleapis.com/auth/bigquery'])
                    self.client = bigquery.Client(credentials=credentials, project=self.project_id or project)
                    logger.info(f"SchemaService initialized with service account for project: {self.project_id}")
                else:
                    logger.error("No valid credentials found. Set GOOGLE_APPLICATION_CREDENTIALS or run 'gcloud auth application-default login'")
                    raise ValueError("BigQuery client initialization failed - no valid credentials")
            except Exception as fallback_error:
                logger.error(f"Fallback initialization also failed: {fallback_error}", exc_info=True)
                raise RuntimeError(
                    "Failed to initialize BigQuery client. "
                    "Ensure GOOGLE_APPLICATION_CREDENTIALS is set or run 'gcloud auth application-default login'"
                ) from e
    
    def get_schema_and_sample(
        self, 
        dataset_id: str, 
        table_id: str, 
        limit: int = 3,
        include_samples: bool = False
    ) -> Dict:
        """
        Fetch table schema and optionally sample rows
        Fetches column descriptions from INFORMATION_SCHEMA for accurate metadata
        
        Args:
            dataset_id: BigQuery dataset ID
            table_id: BigQuery table ID
            limit: Number of sample rows (default: 3)
            include_samples: Whether to include sample data (default: False)
        
        Returns:
            Dict with 'schema' (str) and optionally 'sample_rows' (list)
        """
        cache_key = f"{self.project_id}.{dataset_id}.{table_id}"
        
        # Check cache first
        if cache_key in _SCHEMA_CACHE and not include_samples:
            logger.debug(f"Using cached schema for {table_id}")
            return _SCHEMA_CACHE[cache_key]
        
        try:
            table_ref = f"{self.project_id}.{dataset_id}.{table_id}"
            table = self.client.get_table(table_ref)
            
            # Fetch descriptions from INFORMATION_SCHEMA for accuracy
            descriptions = self._fetch_column_descriptions(dataset_id, table_id)
            
            # Build schema string with field information
            schema_info = "Column Name | Type | Description/Notes\n"
            schema_info += "-" * 100 + "\n"
            
            for field in table.schema:
                field_type = field.field_type
                desc = field.description if field.description else ""
                
                # If description is empty, try fetching from INFORMATION_SCHEMA
                if not desc and field.name in descriptions:
                    desc = descriptions[field.name]
                
                # Build notes for type casting and critical fields
                notes = []
                
                # Highlight TIMESTAMP fields (critical for date filtering)
                if field_type == "TIMESTAMP":
                    notes.append("⚠️ TIMESTAMP - use DATE() for date comparisons")
                
                # Highlight key ID/primary fields
                if field.name.endswith('NO') or field.name.endswith('ID'):
                    notes.append("Key field")
                
                # Highlight date fields
                if 'DATE' in field.field_type or 'Date' in field.name:
                    notes.append("Date field")
                
                # Combine description and notes
                full_desc = desc if desc else "N/A"
                if notes:
                    full_desc = f"{full_desc} [{', '.join(notes)}]" if desc else f"[{', '.join(notes)}]"
                
                schema_info += f"{field.name} | {field_type} | {full_desc}\n"
            
            result = {"schema": schema_info}
            
            # Optionally fetch sample rows
            if include_samples:
                query = f"SELECT * FROM `{table_ref}` LIMIT {limit}"
                df = self.client.query(query).result().to_dataframe()
                result["sample_rows"] = df.to_dict(orient="records")
                logger.info(f"Fetched schema + {limit} samples for {table_id}")
            else:
                logger.info(f"Fetched schema for {table_id}: {len(table.schema)} columns")
            
            # Cache schema only (not samples)
            _SCHEMA_CACHE[cache_key] = {"schema": schema_info}
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching schema for {table_id}: {e}", exc_info=True)
            return {
                "schema": f"Error: Could not fetch schema - {str(e)}",
                "sample_rows": []
            }
    
    def _fetch_column_descriptions(self, dataset_id: str, table_id: str) -> Dict[str, str]:
        """
        Fetch column descriptions from INFORMATION_SCHEMA
        This ensures we get descriptions even if not loaded into field.description
        """
        try:
            query = f"""
            SELECT DISTINCT
              column_name,
              description
            FROM `{self.project_id}.{dataset_id}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
            WHERE table_schema = @dataset_id
              AND table_name = @table_id
              AND column_name IS NOT NULL
            """
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("dataset_id", "STRING", dataset_id),
                    bigquery.ScalarQueryParameter("table_id", "STRING", table_id)
                ]
            )
            
            query_job = self.client.query(query, job_config=job_config)
            results = query_job.result()
            
            descriptions = {}
            for row in results:
                if row.description and row.column_name:
                    descriptions[row.column_name] = str(row.description)
            
            logger.debug(f"Fetched {len(descriptions)} descriptions from INFORMATION_SCHEMA")
            return descriptions
        except Exception as e:
            logger.warning(f"Could not fetch descriptions from INFORMATION_SCHEMA: {e}")
            return {}
    
    def get_compact_schema(self, dataset_id: str, table_id: str) -> str:
        """
        Get compact schema format (column names and types only)
        Optimized for token efficiency
        """
        full_schema = self.get_schema_and_sample(dataset_id, table_id, include_samples=False)
        schema_text = full_schema.get("schema", "")
        
        # Extract just column names and types (skip descriptions for compactness)
        lines = schema_text.split('\n')[2:]  # Skip header
        compact_lines = []
        
        for line in lines:
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2:
                    compact_lines.append(f"{parts[0]}: {parts[1]}")
        
        return "\n".join(compact_lines)
    
    def get_critical_fields_only(self, dataset_id: str, table_id: str) -> str:
        """
        Extract only critical fields (TIMESTAMP, DATE, ID fields, Amount fields)
        Ultra-compact for minimal token usage
        Intelligently selects fields that matter for queries
        """
        full_schema = self.get_schema_and_sample(dataset_id, table_id, include_samples=False)
        schema_text = full_schema.get("schema", "")
        
        lines = schema_text.split('\n')[2:]
        critical_lines = []
        
        for line in lines:
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 2:
                    col_name = parts[0]
                    col_type = parts[1]
                    
                    # Include if: TIMESTAMP, DATE, ID/NO fields, Amount fields, Flag fields
                    is_temporal = 'TIMESTAMP' in col_type or 'DATE' in col_type
                    is_key = col_name.endswith('ID') or col_name.endswith('NO')
                    is_amount = 'AMOUNT' in col_name.upper() or 'VALUE' in col_name.upper()
                    is_flag = 'FLAG' in col_name.upper() or col_name.endswith('_Ind')
                    is_dpd = 'DPD' in col_name.upper()
                    is_count = 'COUNT' in col_name.upper()
                    is_rate = 'RATE' in col_name.upper() or 'PERCENTAGE' in col_name.upper() or 'PCT' in col_name.upper()
                    
                    if is_temporal or is_key or is_amount or is_flag or is_dpd or is_count or is_rate:
                        # Extract notes if present
                        notes = ""
                        if len(parts) >= 3 and '[' in parts[2]:
                            notes = f" {parts[2]}"
                        critical_lines.append(f"{col_name}: {col_type}{notes}")
        
        return "\n".join(critical_lines)
    
    def get_semantic_schema(self, dataset_id: str, table_id: str) -> str:
        """
        Get schema grouped by BUSINESS PURPOSE with semantic meaning
        This is the Phase 1 enhancement - groups fields intelligently for LLM understanding
        """
        full_schema = self.get_schema_and_sample(dataset_id, table_id, include_samples=False)
        schema_text = full_schema.get("schema", "")
        
        # Parse all fields
        all_fields = self._parse_schema_table(schema_text)
        
        # Categorize fields by business purpose
        categories = {
            "🔑 Identity & Agreement Fields": [],
            "📅 Time & Temporal Fields": [],
            "🎯 Performance Cohorts (3M/6M/12M)": [],
            "📊 Delinquency & DPD Metrics": [],
            "💰 Amount & Financial Fields": [],
            "✅ Collections Status & Flags": [],
            "🚨 Risk & Early Warning Indicators": [],
            "👤 Customer Demographics": [],
            "📱 Contact & Address Information": [],
            "🏦 Loan Product & Asset Details": [],
            "💳 EMI & Installment Details": [],
            "🔄 Collections & Payment Activity": [],
            "📋 Allocation & Team Assignment": [],
            "⚠️ CRITICAL RULES - MUST FOLLOW": []
        }
        
        critical_rules = []
        
        # Intelligent categorization
        for field in all_fields:
            name = field['name']
            type_info = field['type']
            desc = field['description']
            
            # Ensure desc is a string (handle list or None cases)
            if isinstance(desc, list):
                desc = " ".join(str(d) for d in desc)
            elif desc is None:
                desc = "N/A"
            else:
                desc = str(desc)
            
            # Extract critical rules from description
            if 'CRITICAL RULE' in desc:
                critical_rules.append({
                    'field': name,
                    'rule': desc
                })
            
            # Identity fields
            if 'AGREEMENT' in name or name in ['CUSTOMERID', 'UCID', 'BRANCHID', 'SUPPLIERID']:
                categories["🔑 Identity & Agreement Fields"].append(f"  {name}: {type_info} | {desc}")
            
            # Temporal fields  
            elif 'DATE' in name or 'DATE' in type_info:
                categories["📅 Time & Temporal Fields"].append(f"  {name}: {type_info} | {desc}")
            
            # Performance Cohorts (3M, 6M, 12M)
            elif any(pat in name for pat in ['MOB_3M', 'MOB_6M', 'MOB_12M', '3M', '6M', '12M']):
                categories["🎯 Performance Cohorts (3M/6M/12M)"].append(f"  {name}: {type_info} | {desc}")
            
            # DPD metrics
            elif 'DPD' in name or 'Delinquency' in name or 'SLIPPAGE' in name:
                categories["📊 Delinquency & DPD Metrics"].append(f"  {name}: {type_info} | {desc}")
            
            # Amount fields
            elif any(pat in name for pat in ['AMOUNT', 'POS', 'EMI', 'CHARGES', 'COST', 'VALUE', 'LOSS']):
                categories["💰 Amount & Financial Fields"].append(f"  {name}: {type_info} | {desc}")
            
            # Collections & payment flags
            elif any(pat in name for pat in ['GNS', 'NNS', 'COLLECTION', 'PAID', 'STATUS', 'CLEAR', 'BUCKET']):
                categories["✅ Collections Status & Flags"].append(f"  {name}: {type_info} | {desc}")
            
            # Risk indicators
            elif any(pat in name for pat in ['EWS', 'RISK', 'SCORE', 'BAND']):
                categories["🚨 Risk & Early Warning Indicators"].append(f"  {name}: {type_info} | {desc}")
            
            # Customer demographics
            elif any(pat in name for pat in ['CUSTOMER', 'GENDER', 'OCCUPATION', 'CITY', 'STATE', 'ZONE', 'ADDRESS', 'ZIPCODE']):
                categories["👤 Customer Demographics"].append(f"  {name}: {type_info} | {desc}")
            
            # Contact information
            elif any(pat in name for pat in ['MOBILE', 'CONTACT', 'PHONE', 'REFERENCE', 'NOMINEE', 'COAPPLICANT']):
                categories["📱 Contact & Address Information"].append(f"  {name}: {type_info} | {desc}")
            
            # Loan & product details
            elif any(pat in name for pat in ['PRODUCT', 'MANUFACTURER', 'ASSET', 'TENURE', 'LTV', 'RATE', 'SBU', 'SCHEME']):
                categories["🏦 Loan Product & Asset Details"].append(f"  {name}: {type_info} | {desc}")
            
            # EMI & installment
            elif any(pat in name for pat in ['INSTL', 'EMI', 'PRINCOMP', 'INTCOMP', 'DUE', 'BILLED']):
                categories["💳 EMI & Installment Details"].append(f"  {name}: {type_info} | {desc}")
            
            # Collections activity
            elif any(pat in name for pat in ['COLLECTION', 'RECEIPT', 'CHEQUE', 'BOUNCE', 'MANDATE', 'BANK']):
                categories["🔄 Collections & Payment Activity"].append(f"  {name}: {type_info} | {desc}")
            
            # Allocation & team
            elif any(pat in name for pat in ['ALLOC', 'TC_', 'TL_', 'SUPERVISOR', 'AGENCY', 'RCM', 'ZCM', 'CENTER']):
                categories["📋 Allocation & Team Assignment"].append(f"  {name}: {type_info} | {desc}")
        
        # Build output
        output = "# Collections Table Schema - Semantic View\n\n"
        output += "**Use this to understand available fields grouped by business purpose**\n\n"
        
        # Add each category
        for category, fields in categories.items():
            if fields:
                output += f"## {category}\n"
                for field_info in fields:
                    output += f"{field_info}\n"
                output += "\n"
        
        # Add critical rules section
        if critical_rules:
            output += "## ⚠️ CRITICAL RULES - MUST FOLLOW\n"
            for rule_info in critical_rules:
                output += f"\n**{rule_info['field']}:**\n{rule_info['rule']}\n"
        
        return output
    
    def _parse_schema_table(self, schema_text: str) -> List[Dict]:
        """Parse markdown table format to list of field dicts"""
        lines = schema_text.split('\n')[2:]  # Skip header and separator
        fields = []
        
        for line in lines:
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 3:
                    fields.append({
                        'name': parts[0],
                        'type': parts[1],
                        'description': parts[2] if len(parts) > 2 else 'N/A'
                    })
        
        return fields
    
    def get_schema_with_business_rules(self, dataset_id: str, table_id: str) -> str:
        """
        Get semantic schema (business rules are in domain agent prompts)
        Returns only the semantic schema - business logic is embedded in agent instructions
        """
        # Business rules and KPI formulas are now in the domain agent prompts
        # to avoid duplication and token waste. This method returns semantic schema only.
        return self.get_semantic_schema(dataset_id, table_id)
    
    def clear_cache(self):
        """Clear the schema cache"""
        global _SCHEMA_CACHE
        _SCHEMA_CACHE.clear()
        logger.info("Schema cache cleared")


# Global instance (lazy initialization)
_schema_service: Optional[SchemaService] = None


def get_schema_service(project_id: str) -> SchemaService:
    """Get or create global schema service instance"""
    global _schema_service
    if _schema_service is None:
        _schema_service = SchemaService(project_id)
    return _schema_service
