"""
Schema Service - Fetch and cache BigQuery table schemas
Provides column names, types, and descriptions to LLM agents
Includes semantic grouping for business context
"""

import logging
from typing import Dict, Optional, List
from google.cloud import bigquery
from google.auth import default
import json

logger = logging.getLogger(__name__)

# Global schema cache to avoid repeated BigQuery API calls
_SCHEMA_CACHE: Dict[str, Dict] = {}


class SchemaService:
    """Service to fetch and format BigQuery table schemas"""
    
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize BigQuery client"""
        try:
            credentials, _ = default()
            self.client = bigquery.Client(credentials=credentials, project=self.project_id)
            logger.info(f"SchemaService initialized for project: {self.project_id}")
        except Exception as e:
            logger.error(f"Failed to initialize BigQuery client: {e}")
            raise
    
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
            SELECT column_name, description
            FROM `{self.project_id}.{dataset_id}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
            WHERE table_name = @table_id AND column_name IS NOT NULL
            """
            
            job_config = self.client.QueryJobConfig(
                query_parameters=[
                    self.client.query_job.ScalarQueryParameter("table_id", "STRING", table_id)
                ]
            )
            
            query_job = self.client.query(query, job_config=job_config)
            results = query_job.result()
            
            descriptions = {}
            for row in results:
                if row.description:
                    descriptions[row.column_name] = row.description
            
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
            output += "## " + categories["⚠️ CRITICAL RULES - MUST FOLLOW"] + "\n"
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
        Get semantic schema PLUS embedded business rules and common filters
        This combines semantic schema + actionable business rules for SQL generation
        """
        semantic = self.get_semantic_schema(dataset_id, table_id)
        
        # Add business rules section
        business_rules = """

---

## 📖 Common Business Rules & Filters

### Performance Cohort Definitions
- **3-Month Cohort**: Accounts that have completed 3 months from installment start → `WHERE MOB_3M_Flag = 1`
- **6-Month Cohort**: Accounts that have completed 6 months from installment start → `WHERE MOB_6M_Flag = 1`
- **12-Month Cohort**: Accounts that have completed 12 months from installment start → `WHERE MOB_12M_Flag = 1`

### GNS/NNS Calculations (CRITICAL - Must Filter by MOB)
- **GNS1 (Gross Non-Starter at 1st EMI)**: 
  - ONLY VALID for `MOB_ON_INSTL_START_DATE = 1`
  - Count: `COUNT(CASE WHEN GNS1 = 'Y' THEN AGREEMENTNO END) WHERE MOB_ON_INSTL_START_DATE = 1`
  
- **NNS1 (Net Non-Starter at 1st EMI)**:
  - ONLY VALID for `MOB_ON_INSTL_START_DATE = 1`
  - Count: `COUNT(CASE WHEN NNS1 = 'Y' THEN AGREEMENTNO END) WHERE MOB_ON_INSTL_START_DATE = 1`

- **GNS2, GNS3, GNS4, GNS5, GNS6** → Filter by `MOB_ON_INSTL_START_DATE = 2, 3, 4, 5, 6` respectively
- **NNS2, NNS3, NNS4, NNS5, NNS6** → Filter by `MOB_ON_INSTL_START_DATE = 2, 3, 4, 5, 6` respectively

### DPD Bucket Grouping (EXACT Buckets - Do Not Modify)
When using `SOM_DPD_BUCKET` or `DPD_BUCKET`, use EXACTLY these buckets:
- '0' → Current (0 DPD)
- '1' → 1-30 days past due
- '2' → 31-60 days past due
- '3' → 61-90 days past due
- '4' → 91-120 days past due (NPA threshold is 90+)
- '5' → 120+ days past due (NPA threshold is 90+)

### EWS Model Output Banding (EXACT Groups - Do Not Modify)
When using `EWS_Model_Output_Band`, group values into EXACTLY these bands:
- 'Low' → R1, R2, R3
- 'Medium' → R4, R5, R6, R7
- 'High' → R8, R9, R10 and above

### Common Filter Conditions
- **Exclude Written-Off**: `WHERE NPASTAGEID != 'WO_SALE' OR NPASTAGEID IS NULL`
- **Exclude Deceased**: `WHERE NPASTAGEID != 'DECEASED' OR NPASTAGEID IS NULL`
- **Active Regular Accounts**: `WHERE SOM_NPASTAGEID = 'REGULAR'`
- **Current Portfolio Snapshot**: `WHERE BUSINESS_DATE = (SELECT MAX(BUSINESS_DATE) FROM table)`
- **Zero DPD Accounts**: `WHERE SOM_DPD = 0`
- **0+ DPD (Delinquent)**: `WHERE SOM_DPD > 0`
- **30+ DPD**: `WHERE SOM_DPD > 30`
- **60+ DPD**: `WHERE SOM_DPD > 60`
- **90+ DPD (NPA)**: `WHERE SOM_DPD > 90`

### Time Aggregation Patterns
- **By Month**: `GROUP BY DATE_TRUNC(BUSINESS_DATE, MONTH) as month` then `ORDER BY month DESC`
- **By Branch**: `GROUP BY BRANCHNAME, REGION` for geographic analysis
- **By Product**: `GROUP BY PRODUCTNAME` for product-level metrics

### Common KPI Formulas
- **0+ DPD Percentage**: `ROUND(SAFE_DIVIDE(COUNT(CASE WHEN SOM_DPD > 0 THEN 1 END), COUNT(*)) * 100, 2)`
- **GNS1 Percentage**: `ROUND(SAFE_DIVIDE(COUNT(CASE WHEN GNS1 = 'Y' THEN 1 END), COUNT(*)) * 100, 2)` 
  (WITH filter `WHERE MOB_ON_INSTL_START_DATE = 1`)
- **NNS1 Percentage**: `ROUND(SAFE_DIVIDE(COUNT(CASE WHEN NNS1 = 'Y' THEN 1 END), COUNT(*)) * 100, 2)`
  (WITH filter `WHERE MOB_ON_INSTL_START_DATE = 1`)
- **Recovery Rate**: `SAFE_DIVIDE(EMI_COLLECTION + ODC_COLLECTION + CBC_COLLECTION, TOTAL_CHARGES_DUE) * 100`

### Date Filtering Patterns
- **Last N Months**: `WHERE BUSINESS_DATE >= DATE_SUB(CURRENT_DATE(), INTERVAL N MONTH)`
- **Current Month**: `WHERE DATE_TRUNC(BUSINESS_DATE, MONTH) = DATE_TRUNC(CURRENT_DATE(), MONTH)`
- **Since Disbursal**: `WHERE BUSINESS_DATE >= DATE(DISBURSALDATE)`
- **Before Maturity**: `WHERE BUSINESS_DATE <= DATE(MATURITYDATE)`
"""
        
        return semantic + business_rules
    
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
