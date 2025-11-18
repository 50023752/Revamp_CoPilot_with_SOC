# Revamp CoPilot ADK - AI Agent Instructions

## Project Overview

This is a **Google Agent Development Kit (ADK)** based multi-agent system for analyzing L&T Finance Two-Wheeler loan data using natural language queries. The system uses a **refactored architecture** with separated SQL generation and execution for improved security, testability, and maintainability.

**Key Achievement**: Production-ready MVP with clean separation of concerns - Intent Routing → SQL Generation → Safe Execution.

## Architecture Pattern (REFACTORED)

### Multi-Agent System with Separated Execution

```
User Question → OrchestratorAgent
                     ↓
              IntentRouterAgent (domain classification)
                     ↓
     ┌───────────────┴────────────────┐
     ↓                ↓                ↓
SourcingAgent   CollectionsAgent   DisbursalAgent
     ↓                ↓                ↓
     SQL Generation ONLY (no execution)
                     ↓
              QueryExecutionAgent (ONLY component that executes SQL)
                     ↓
            Response Formatter → User
```

### Two Architectures Available

1. **Legacy (`agent.py`)**: Original architecture with mixed SQL generation/execution
2. **Refactored (`agent_refactored.py`)**: NEW architecture with separated concerns ⭐

**Use Refactored Architecture** for:
- New development
- Better security (single execution point)
- Easier testing (mockable components)
- Cost control (dry-run validation)

**Critical Files**:
- `agent_refactored.py`: NEW Refactored orchestrator with separated pipeline
- `agents/intent/router_agent.py`: Deterministic + LLM-based routing
- `agents/domain/`: SQL generation agents (NO execution)
  - `collections_agent.py`
  - `sourcing_agent.py`
  - `disbursal_agent.py`
- `agents/execution/query_execution_agent.py`: ONLY component that executes SQL
- `contracts/`: Pydantic models for agent communication
  - `sql_contracts.py`: SQLGenerationRequest/Response, SQLExecutionRequest/Response
  - `routing_contracts.py`: RoutingRequest/Response
- `utils/sql_safety_validator.py`: SQL safety validation (blocks destructive queries)
- `config/settings.py`: Pydantic settings from `.env` file

## ADK-Specific Patterns

### 1. Agent Types and When to Use Each

**Use `Agent` (LLM Agent)** for:
- Domain experts that need LLM reasoning (all 3 domain agents, router)
- Tool-calling agents (domain agents use BigQueryToolset)
- Pattern: `Agent(name=..., model=Gemini(...), instruction=..., tools=[...])`

**Use `BaseAgent` (Custom Agent)** for:
- Orchestration logic without direct LLM calls (OrchestratorAgent)
- Conditional routing between other agents
- Pattern: Override `async _run_async_impl(self, ctx)` and yield Events

### 2. Async Event Streaming Pattern

**ALL agent interactions use async generators**:
```python
async for event in agent.run_async(ctx):
    yield event  # Stream to parent or collect for processing
```

**Never** use synchronous `.run()` - causes event loop conflicts. For Streamlit integration, wrap in `asyncio.run()`.

### 3. Context and Session Management

**InvocationContext (`ctx`)** contains:
- `ctx.current_input`: User's question text
- `ctx.session.state`: Dict for storing routing decisions, conversation history
- `ctx.session.id`: Unique session identifier

**Store domain routing and conversation context**:
```python
ctx.session.state['selected_domain'] = domain
ctx.session.state['conversation_history'] = []  # List of {question, answer, sql, domain}
ctx.session.state['last_query_result'] = {...}  # For follow-up "show more" queries
```

ADK's `InMemorySessionService` handles session lifecycle automatically (TTL configured in settings).

### Multi-Turn Conversation Pattern

**Enable follow-up questions** by storing context in session state:

```python
# In OrchestratorAgent._run_async_impl
async def _run_async_impl(self, ctx):
    # Initialize conversation history if not exists
    if 'conversation_history' not in ctx.session.state:
        ctx.session.state['conversation_history'] = []
    
    user_question = ctx.current_input
    
    # Check if question references previous context
    is_followup = self._is_followup_question(user_question, ctx.session.state)
    
    if is_followup:
        # Augment question with previous context
        user_question = self._augment_with_context(user_question, ctx.session.state)
    
    # ... route and execute ...
    
    # Store interaction in history
    ctx.session.state['conversation_history'].append({
        'question': ctx.current_input,
        'answer': response,
        'domain': domain,
        'timestamp': datetime.utcnow().isoformat()
    })
```

**Follow-up question patterns to detect**:
- "show more", "tell me more", "expand on that"
- "what about [same domain different filter]"
- "compare that to", "how does that relate to"
- Pronouns: "those loans", "that metric", "same period"

**Context augmentation strategy**:
1. Include last question + answer in prompt
2. Include last SQL query if asking for modifications
3. Preserve domain routing (stay in same domain for clarifications)

### 4. Tool Integration

**Use ADK's BigQueryToolset** (not custom tools):
```python
from google.adk.tools.bigquery import BigQueryToolset, BigQueryCredentialsConfig

credentials, project_id = default()  # gcloud credentials
credentials_config = BigQueryCredentialsConfig(credentials=credentials)
bq_toolset = BigQueryToolset(credentials_config=credentials_config, ...)
```

**Available methods** (auto-exposed to LLM):
- `get_table_info(project_id, dataset_id, table_id)` - Schema discovery via BigQuery INFORMATION_SCHEMA
- `execute_sql(project_id, sql)` - Query execution
- `list_table_ids(project_id, dataset_id)` - Table listing

**Schema Discovery**: Agents call `get_table_info()` which fetches column names, types, and **descriptions from BigQuery metadata** (`INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`). Ensure your BigQuery tables have meaningful column descriptions populated.

## Critical Business Logic

### Agent Instruction Design Pattern

**Problem**: Long, documentation-style instructions (600+ lines) cause Gemini to respond with acknowledgments ("I understand, I'm ready") instead of executing tools.

**Solution**: Use concise, action-focused instructions (~20 lines) with:
1. **Clear role statement** (1 line)
2. **Critical rules as numbered list** (5-10 items)
3. **Explicit workflow** (3 steps: Generate → Execute → Return)
4. **Strong imperative directive** ("DO NOT acknowledge - EXECUTE IMMEDIATELY")

**Example** (COLLECTIONS_INSTRUCTION):
```python
COLLECTIONS_INSTRUCTION = f"""You are a BigQuery SQL expert for L&T Finance Two-Wheeler collections data.

Table: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.collections_table}`

CRITICAL RULES:
1. Date field: Use `BUSINESS_DATE` for ALL filtering
2. "Last N months" = `WHERE BUSINESS_DATE >= DATE_SUB...`
3. DPD 0+ = `SOM_DPD >= 0` (ALL accounts)
4. Count: `COUNT(DISTINCT AGREEMENTNO)`
5. Percentage: `ROUND(SAFE_DIVIDE(count, total) * 100, 2)`

WORKFLOW - For EVERY question:
1. Generate SQL query
2. Call execute_sql("{settings.gcp_project_id}", "YOUR_SQL")  
3. Return results

DO NOT acknowledge - EXECUTE IMMEDIATELY.
"""
```

**Why this works**:
- Gemini sees it as an ACTION PROMPT, not documentation
- No room for "understanding" - only execution
- Clear 1-2-3 workflow eliminates ambiguity
- Imperative tone ("DO NOT") creates urgency

**Anti-patterns to avoid**:
- ❌ "You have access to these tools..." (sounds like onboarding)
- ❌ "## Your Role" followed by long paragraphs (sounds like documentation)
- ❌ Multiple example queries (LLM treats as reference material, not commands)
- ❌ "Workflow: 1. Understand... 2. Fetch schema... 3. Generate... 4. Execute..." (too many steps)

### Domain-Specific SQL Patterns

**All domain agents follow this workflow**:
1. **Schema Discovery First**: Agents call `get_table_info()` before complex queries
2. **Monetary Formatting**: Always convert to crores with trailing zero removal:
   ```sql
   REGEXP_REPLACE(FORMAT('%.2f', amount/10000000), r'(\.\d*?[1-9])0+$|\.0+$', r'\1')
   ```
3. **Safe Division**: Use `SAFE_DIVIDE()` for all percentage calculations
4. **Date Filtering**: Use `LastModifiedDate` (Sourcing) or `BUSINESS_DATE` (Collections/Disbursal)

### Collections Agent - GNS/NNS Logic (CRITICAL)

**GNS1/NNS1 calculations MUST filter by MOB**:
```sql
-- CORRECT: GNS1 percentage
WHERE MOB_ON_INSTL_START_DATE = 1  -- Filter for 1st EMI accounts
```
Without this filter, GNS percentages are mathematically incorrect (denominator includes all MOBs).

### Standardized Band Definitions

**Amount Finance Bands** (used across Sourcing/Collections):
- `< 10K`, `>= 10K to < 25K`, `>= 25K to < 50K`, ..., `>= 250K`
- Never create custom bands - use exact CASE statements from agent instructions

## Development Workflows

### Local Testing
```bash
# 1. Configure environment
copy .env.example .env  # Edit with GCP credentials

# 2. Authenticate
gcloud auth application-default login

# 3. Run validation (ALWAYS before deployment)
python tests\validate_setup.py

# 4. Start agent (uses ADK's built-in server)
python main.py
```

### Environment Configuration (.env)
**Required variables**:
- `GCP_PROJECT_ID`, `BIGQUERY_DATASET`
- `SOURCING_TABLE`, `COLLECTIONS_TABLE`, `DISBURSAL_TABLE`
- `GOOGLE_API_KEY` (for Gemini API)

**Defaults in `settings.py`**:
- Models: `gemini-2.0-flash` (agents/router)
- Region: `asia-south1`
- Query limits: 1000 rows max

### Testing Pattern

**Run domain-specific tests**:
```bash
python tests\test_bigquery_access.py  # Validates table access
python tests\test_vertex_ai.py        # Validates Gemini API
```

**Streamlit UI for manual testing**:
```bash
streamlit run streamlit_app.py
# Features session history, domain badge display, formatted SQL output
```

## Common Pitfalls

### 1. Event Loop Conflicts
**Symptom**: `RuntimeError: Event loop is closed`  
**Fix**: Always use `async for event in agent.run_async(ctx)` - never `.run()`

### 2. Pydantic Field Errors in Custom Agents
**Symptom**: `TypeError: BaseAgent.__init__() got unexpected keyword argument`  
**Fix**: Use `object.__setattr__(self, '_field', value)` to store non-Pydantic attributes in custom agents

### 3. Router Returning Full Sentences
**Symptom**: Domain extraction fails because router returns explanations  
**Fix**: Router instruction emphasizes "Return ONLY the domain name" + fallback keyword matching in `OrchestratorAgent._fallback_routing()`

### 4. BigQuery Permission Errors
**Symptom**: `403 Forbidden` on query execution  
**Fix**: Service account needs `roles/bigquery.dataViewer` + `roles/bigquery.jobUser`

### 5. BigQuery Quota and Timeout Errors

**Quota exceeded** (`429 Too Many Requests`):
```python
# Implement exponential backoff in domain agent instructions
# OR reduce concurrent queries by limiting session concurrency
settings.max_query_rows = 500  # Reduce result size
```

**Query timeout** (> 30 seconds):
```python
# In agent instruction, add optimization hints:
# - Partition pruning: Always filter by date columns
# - Limit early: Add LIMIT before expensive operations
# - Avoid SELECT *: Only select needed columns
```

**Sample timeout handling pattern**:
```python
from google.cloud.exceptions import GoogleCloudError

async def _run_async_impl(self, ctx):
    try:
        async for event in domain_agent.run_async(ctx):
            yield event
    except GoogleCloudError as e:
        if 'timeout' in str(e).lower():
            yield Event(content="⏱️ Query timed out. Try narrowing date range or filters.")
        elif 'quota' in str(e).lower():
            yield Event(content="📊 Query quota exceeded. Please try again in a moment.")
        else:
            raise
```

## Code Conventions

### File Organization
- **Agents**: Define one agent per logical domain, use descriptive variable names (`sourcing_agent`, not `agent1`)
- **Instructions**: Store as multi-line f-strings with actual table names interpolated from `settings`
- **Tools**: Prefer ADK built-in toolsets over custom `@Tool` decorators

### Logging
```python
logger.info(f"Received question: {user_question}")
logger.info(f"Routed to domain: {domain}")
```
All logs go to `copilot_mvp.log` + stdout (configured in `main.py`)

### Error Handling
Wrap orchestration logic in try-except to yield user-friendly error Events:
```python
except Exception as e:
    logger.error(f"Error in orchestration: {str(e)}", exc_info=True)
    yield Event(content=f"❌ I encountered an error: {str(e)}...")
```

## Deployment

**Cloud Run deployment** (automated):
```bash
.\scripts\deploy.bat
# Builds container, deploys to asia-south1, sets env vars from .env
```

**Container pattern**: Dockerfile uses Python 3.10, installs from `requirements.txt`, runs `main.py`

## Key Dependencies

- `google-adk>=1.18.0` - Core framework (auto-pulls compatible versions of BigQuery, Vertex AI clients)
- `pydantic-settings` - Type-safe config management
- `python-dotenv` - .env file loading

**Version compatibility**: Let `google-adk` dictate dependency versions - don't pin `google-cloud-aiplatform` or `google-cloud-bigquery` separately.

## When Modifying This Codebase

1. **Adding new domain agent**: Copy pattern from `domain_agents.py`, update router instruction with new keywords
2. **Changing SQL logic**: Update agent instructions (NOT code) - LLM adapts automatically
3. **New BigQuery table**: Add to `.env` + `settings.py`, reference in agent instruction, **populate column descriptions in BigQuery**
4. **Performance issues**: Enable Gemini context caching (Phase 2) or switch critical agents to `gemini-2.0-pro`

## Phase 2: Semantic Schema Graph (Future Enhancement)

**Why add a semantic schema graph?**

A semantic schema graph captures **relationships between tables** and **business meaning of joins**, which BigQuery metadata doesn't provide:

```python
# Example: Semantic relationships not in INFORMATION_SCHEMA
schema_graph = {
    "sourcing_to_collections": {
        "join_key": "AGREEMENTNO",
        "relationship": "1-to-many",
        "business_meaning": "Each application can have multiple collection records (monthly snapshots)"
    },
    "collections_to_disbursal": {
        "join_key": "AGREEMENTNO", 
        "relationship": "1-to-1",
        "business_meaning": "Disbursed applications have exactly one disbursal record"
    }
}
```

**Accuracy improvements from schema graph**:

1. **Cross-domain questions**: "Show approval rate vs GNS1 by branch" requires joining sourcing + collections
   - Current: Router picks one domain, misses cross-table insight
   - With graph: Router sees relationship, generates multi-table query

2. **Implicit filters**: "Show collections for approved loans"
   - Current: Agent doesn't know approved = exists in sourcing with status
   - With graph: Agent knows to filter `WHERE AGREEMENTNO IN (SELECT ...)`

3. **Aggregation level**: "Average DPD per application"
   - Current: Might double-count if collections has monthly snapshots
   - With graph: Agent knows cardinality, applies `DISTINCT` or date filtering

4. **Foreign key inference**: "Show manufacturer-wise recovery rate"
   - Current: Agent doesn't know manufacturer is in sourcing, not collections
   - With graph: Auto-joins to get manufacturer dimension

**Implementation pattern** (Phase 2):
```python
# agents/schema_graph.py
SCHEMA_RELATIONSHIPS = {
    "tables": {
        "sourcing": {"grain": "application", "primary_key": "AGREEMENTNO"},
        "collections": {"grain": "agreement-month", "primary_key": ["AGREEMENTNO", "BUSINESS_DATE"]},
        "disbursal": {"grain": "agreement", "primary_key": "AGREEMENTNO"}
    },
    "joins": [
        {
            "tables": ["sourcing", "collections"],
            "join_keys": {"sourcing": "AGREEMENTNO", "collections": "AGREEMENTNO"},
            "join_type": "LEFT",  # Not all applications reach collections
            "business_rule": "Use latest BUSINESS_DATE for point-in-time collection status"
        }
    ]
}

# Add to router instruction to enable cross-domain routing
# Add to domain agent instructions as <schema_context> for multi-table queries
```

**When to implement**: After validating single-domain accuracy in Phase 1. Most questions (~80%) are single-domain.

## Refactored Architecture (NEW)

### Separation of Concerns

The refactored architecture cleanly separates SQL generation from execution:

**QueryExecutionAgent - Single Point of Execution**:
- ONLY component with BigQuery client access
- Mandatory SQL safety validation (blocks DELETE/UPDATE/TRUNCATE/DROP)
- Dry-run cost estimation before execution
- Retry policy with exponential backoff
- Timeout handling and structured error responses
- Query logging and performance metrics

```python
# Example: Using Query Execution Agent
from agents.execution.query_execution_agent import QueryExecutionAgent
from contracts.sql_contracts import SQLExecutionRequest, QueryMetadata

agent = QueryExecutionAgent(project_id=settings.gcp_project_id)

request = SQLExecutionRequest(
    sql_query="SELECT * FROM table LIMIT 10",
    project_id=settings.gcp_project_id,
    metadata=QueryMetadata(domain="COLLECTIONS", intent="Test query"),
    dry_run=False,  # Set to True for cost estimation only
    max_results=1000,
    timeout_seconds=300
)

response = agent.execute(request)  # Returns SQLExecutionResponse
```

**Domain Agents - SQL Generation Only**:
- No BigQuery client - delegates to QueryExecutionAgent
- Returns structured `SQLGenerationResponse` with SQL + metadata
- Easier to test (no BigQuery dependency)

```python
# Example: Domain agent generates SQL without executing
from agents.domain.collections_agent import CollectionsAgent
from contracts.sql_contracts import SQLGenerationRequest

agent = CollectionsAgent()

request = SQLGenerationRequest(
    user_question="What is 0+ dpd count for last 3 months?",
    domain="COLLECTIONS",
    conversation_context=[],
    session_id="session_123"
)

response = await agent.generate_sql(request)
# response.sql_query contains generated SQL
# response.metadata contains query context
```

### Contract-Based Communication

**All inter-agent communication uses typed Pydantic models**:

```python
# Routing contracts
from contracts.routing_contracts import RoutingRequest, RoutingResponse, DomainType

# SQL contracts
from contracts.sql_contracts import (
    SQLGenerationRequest,
    SQLGenerationResponse,
    SQLExecutionRequest,
    SQLExecutionResponse,
    ExecutionStatus,
    QueryMetadata
)
```

**Benefits**:
- Type safety (IDE autocomplete, validation)
- Self-documenting interfaces
- Easy to mock for testing
- Versioning and compatibility tracking

### SQL Safety Validation

**All queries pass through SQLSafetyValidator before execution**:

```python
from utils.sql_safety_validator import SQLSafetyValidator

is_safe, error = SQLSafetyValidator.validate(sql_query)
if not is_safe:
    # Query blocked: returns error message
    # e.g., "Blocked: Query contains destructive keyword 'DELETE'"
```

**Blocked operations**:
- DELETE, UPDATE, TRUNCATE, DROP, ALTER
- INSERT, MERGE, CREATE, REPLACE
- GRANT, REVOKE
- Multiple statements (SQL injection prevention)

### Testing Strategy

**Test SQL generation separately from execution**:

```python
# Unit test - No BigQuery required
@pytest.mark.asyncio
async def test_collections_sql_generation():
    agent = CollectionsAgent()
    request = SQLGenerationRequest(
        user_question="What is 0+ dpd count?",
        domain="COLLECTIONS",
        session_id="test"
    )
    
    response = await agent.generate_sql(request)
    
    # Test SQL logic without BigQuery
    assert "SOM_DPD >= 0" in response.sql_query
    assert "COUNT(DISTINCT AGREEMENTNO)" in response.sql_query

# Integration test - Mock BigQuery client
def test_execution_with_mock():
    agent = QueryExecutionAgent(project_id="test")
    agent.client = Mock()  # Inject mock BigQuery client
    
    # Test execution logic without real queries
    response = agent.execute(request)
    assert response.status == ExecutionStatus.SUCCESS
```

### Folder Structure

```
agents/
├── intent/                  # Routing & Intent Detection
│   └── router_agent.py      # IntentRouterAgent (hybrid routing)
│
├── domain/                  # SQL Generation (No Execution)
│   ├── collections_agent.py
│   ├── sourcing_agent.py
│   └── disbursal_agent.py
│
└── execution/               # SQL Execution (No Generation)
    └── query_execution_agent.py  # QueryExecutionAgent (ONLY executes SQL)

contracts/                   # Structured Interfaces
├── sql_contracts.py         # SQL request/response models
└── routing_contracts.py     # Routing models

utils/                       # Shared Utilities
└── sql_safety_validator.py  # SQL safety validation

tests/                       # Unit & Integration Tests
└── test_refactored_architecture.py
```

### Migration Path

**Both architectures coexist**:

```python
# Legacy architecture (still works)
from agent import root_agent  # Original mixed architecture

# New refactored architecture (recommended)
from agent_refactored import root_agent  # Separated concerns
```

**When to use each**:
- **Use refactored** for: New features, testing, production deployments
- **Use legacy** for: Backward compatibility, comparison testing

### Key Differences

| Aspect | Legacy | Refactored |
|--------|--------|-----------|
| SQL Execution | Mixed in domain agents | Dedicated QueryExecutionAgent |
| Security | No validation | Mandatory safety checks |
| Testing | Requires BigQuery | Mockable components |
| Cost Control | None | Dry-run estimation |
| Monitoring | Scattered | Centralized |
| Contracts | Unstructured | Typed Pydantic models |

See `ARCHITECTURE_DESIGN.md` and `REFACTORED_README.md` for detailed documentation.

