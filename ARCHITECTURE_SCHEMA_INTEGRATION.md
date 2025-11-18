"""
COMPREHENSIVE ARCHITECTURE PLAN - Revamp CoPilot with Schema Integration
=========================================================================

Current Issues:
- Agents are just following example queries from prompt template
- No actual schema knowledge being passed to LLM
- TIMESTAMP casting fixes don't help if LLM doesn't know field types
- Session management issues resolved, but agent intelligence issues remain

Proposed Solution: Dynamic Schema Injection Architecture
========================================================

## Architecture Option 1: Direct BigQuery Toolset Integration (RECOMMENDED)

```
User Question
    ↓
OrchestratorAgent
    ├─→ IntentRouterAgent (route to domain)
    │
    └─→ [SOURCING/COLLECTIONS/DISBURSAL]Agent
        ├─→ [NEW] Fetch Schema via BigQueryToolset.get_table_info()
        ├─→ [NEW] Format schema for LLM: "Columns: [col1: TYPE, col2: TYPE, ...]"
        ├─→ [NEW] Inject schema + user question → LLM
        ├─→ LLM generates SQL (aware of actual column types)
        └─→ QueryExecutionAgent (execute)
```

**Why This Works:**
- ✅ LLM sees actual schema, not just examples
- ✅ Type mismatches (TIMESTAMP vs DATE) prevented at source
- ✅ LLM can discover columns (not limited to hardcoded examples)
- ✅ Works for new tables without code changes

**Implementation Steps:**
1. Add BigQueryToolset to each domain agent's `__init__`
2. In `generate_sql()`, fetch table schema before calling LLM
3. Inject schema into prompt context
4. Cache schema (5 min TTL) to avoid repeated API calls

---

## Architecture Option 2: Two-Stage Generation (Schema Discovery → SQL)

```
User Question
    ↓
Stage 1: Schema Discovery Agent
    ├─→ LLM gets: full schema + question
    └─→ LLM returns: "relevant columns: [col1, col2, col3]"
        ↓
Stage 2: SQL Generation Agent  
    ├─→ LLM gets: filtered schema + question
    └─→ LLM returns: SQL query
        ↓
    QueryExecutionAgent (execute)
```

**Pros:**
- ✅ LLM self-selects relevant columns (smarter)
- ✅ Avoids token waste on irrelevant columns

**Cons:**
- ❌ 2x API calls (slower, more expensive)
- ❌ More complex orchestration

---

## Architecture Option 3: Hybrid - Static + Dynamic

```
STATIC (in code):
  INSTRUCTION_TEMPLATE: "Here are common columns you'll use:
    - LastModifiedDate (TIMESTAMP) - use DATE() casting
    - BRE_Sanction_Result__c (STRING) - status field
    - AssetCost (FLOAT) - amount field
  "

DYNAMIC (runtime):
  + Fetch TIMESTAMP-only columns from BigQuery
  + Inject: "TIMESTAMP fields: LastModifiedDate, CreatedDate"
  + Fetch sample values (5 rows) to show data patterns
```

**Pros:**
- ✅ Fast (minimal schema fetching)
- ✅ Familiar columns from hardcoded instruction
- ✅ Runtime discovery for safety

**Cons:**
- ❌ Still relies on hardcoded knowledge
- ❌ Not self-healing for new tables

---

## RECOMMENDED: Option 1 + Caching

### Implementation Plan:

### Step 1: Add Schema Service
```python
# agents/schema/schema_service.py

class SchemaService:
    def __init__(self, project_id, dataset_id):
        self.project_id = project_id
        self.dataset_id = dataset_id
        self._cache = {}  # {table_id: (schema_info, timestamp)}
        self.cache_ttl = 300  # 5 minutes
    
    def get_table_schema(self, table_id: str) -> dict:
        """Get schema with 5-min cache"""
        if table_id in self._cache:
            schema, cached_at = self._cache[table_id]
            if time.time() - cached_at < self.cache_ttl:
                return schema
        
        # Fetch from BigQuery
        from google.adk.tools.bigquery import BigQueryToolset
        
        toolset = BigQueryToolset(...)
        schema_str = toolset.get_table_info(
            self.project_id, 
            self.dataset_id, 
            table_id
        )
        
        # Cache it
        self._cache[table_id] = (schema_str, time.time())
        return schema_str
```

### Step 2: Update Domain Agents
```python
# agents/domain/collections_agent.py

class CollectionsAgent(BaseAgent):
    
    def __init__(self):
        # ... existing init ...
        self.schema_service = SchemaService(
            settings.gcp_project_id,
            settings.bigquery_dataset
        )
    
    async def generate_sql(self, request, context=None):
        # FETCH SCHEMA (1-2ms with cache)
        schema_info = self.schema_service.get_table_schema(
            settings.collections_table
        )
        
        # AUGMENT PROMPT WITH SCHEMA
        prompt = f"""
        {self.INSTRUCTION_TEMPLATE}
        
        ## Actual Table Schema
        {schema_info}
        
        ## Question
        {request.user_question}
        """
        
        # ... invoke LLM with augmented prompt ...
```

### Step 3: Update LlmAgent Instruction
```python
INSTRUCTION_TEMPLATE = """
You are a BigQuery SQL expert for collections data.

## Dynamic Schema Will Be Provided Below
The actual table columns and types are listed in "Actual Table Schema" section.

## Rules
1. ALWAYS use columns exactly as they appear in the schema
2. ALWAYS check column types - if TIMESTAMP, use DATE() casting
3. ALWAYS use column types for correct operations
4. Generate ONLY the SQL, no explanation
"""
```

---

## Full Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                          Streamlit App                          │
│  (streamlit_app_v2.py)                                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                    User Question
                           │
          ┌────────────────▼───────────────────┐
          │   OrchestratorAgent (Main)         │
          │   - Manages conversation flow      │
          │   - Handles errors                 │
          └────────────────┬───────────────────┘
                           │
          ┌────────────────▼──────────────────┐
          │  IntentRouterAgent                │
          │  - Routes to SOURCING/            │
          │    COLLECTIONS/DISBURSAL          │
          └────────────────┬───────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────▼──────────┐  ┌────▼──────────┐  ┌───▼──────────┐
   │ Sourcing      │  │ Collections   │  │ Disbursal    │
   │ Agent         │  │ Agent         │  │ Agent        │
   ├───────────────┤  ├───────────────┤  ├──────────────┤
   │ [NEW]         │  │ [NEW]         │  │ [NEW]        │
   │ SchemaService │  │ SchemaService │  │ SchemaService│
   │   ↓           │  │   ↓           │  │   ↓          │
   │ BigQuery      │  │ BigQuery      │  │ BigQuery     │
   │ (get schema)  │  │ (get schema)  │  │ (get schema) │
   │   ↓           │  │   ↓           │  │   ↓          │
   │ [NEW]         │  │ [NEW]         │  │ [NEW]        │
   │ Inject        │  │ Inject        │  │ Inject       │
   │ schema to     │  │ schema to     │  │ schema to    │
   │ prompt        │  │ prompt        │  │ prompt       │
   │   ↓           │  │   ↓           │  │   ↓          │
   │ LlmAgent      │  │ LlmAgent      │  │ LlmAgent     │
   │ (generate SQL)│  │ (generate SQL)│  │ (generate SQL)
   └────┬──────────┘  └────┬──────────┘  └───┬──────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
          ┌────────────────▼──────────────────┐
          │  QueryExecutionAgent              │
          │  - SQL Safety Validation          │
          │  - Execute Query                  │
          │  - Return Results                 │
          └────────────────┬───────────────────┘
                           │
                    Response to User
```

---

## Implementation Checklist

### Phase 1: Schema Service (Current)
- [ ] Create `agents/schema/schema_service.py`
- [ ] Implement caching with TTL
- [ ] Test with BigQueryToolset (via test script)
- [ ] Test with direct BigQuery client (fallback)

### Phase 2: Agent Integration
- [ ] Update `SourcingAgent.__init__` to use SchemaService
- [ ] Update `CollectionsAgent.__init__` to use SchemaService
- [ ] Update `DisbursalAgent.__init__` to use SchemaService
- [ ] Update `generate_sql()` in all agents to fetch schema
- [ ] Augment prompts with schema

### Phase 3: Testing
- [ ] Test query: "What is 0+ dpd for last 6 months?" (Collections)
- [ ] Test query: "Show approval rate for last 3 months" (Sourcing)
- [ ] Test query: "Disbursal amount trend" (Disbursal)
- [ ] Verify schema is injected (check logs)
- [ ] Verify SQL uses correct column types

### Phase 4: Monitoring
- [ ] Log schema fetch times (cached vs uncached)
- [ ] Track LLM token usage (with vs without schema)
- [ ] Monitor BigQuery API quota usage

---

## Expected Benefits

| Aspect | Before | After |
|--------|--------|-------|
| Agent Intelligence | Template-bound | Schema-aware |
| Type Errors | Common | Rare |
| Column Discovery | Limited to examples | Automatic |
| New Tables Support | Manual code update | Auto-detect |
| Token Usage | Lower | Slightly higher |
| Query Accuracy | ~70% | ~95%+ |

---

## Testing Command

```bash
# Test BigQueryToolset availability
python test_adk_bigquery_toolset.py

# Run agent with schema injection
python main.py

# Test via Streamlit
streamlit run streamlit_app_v2.py
```

---

## Risk Mitigation

**Risk**: Schema fetch adds latency
**Mitigation**: 5-minute cache, fallback to hardcoded schema

**Risk**: BigQuery quota exceeded
**Mitigation**: INFORMATION_SCHEMA queries are metadata-only, very cheap

**Risk**: Schema changes break queries
**Mitigation**: LLM sees current schema, auto-adapts

---

## Next Steps

1. Run `test_adk_bigquery_toolset.py` to check ADK capabilities
2. If BigQueryToolset available → Implement Option 1
3. If not available → Use direct BigQuery client (Option 1 still works)
4. Start with Collections agent (most critical)
5. Roll out to Sourcing and Disbursal
"""
