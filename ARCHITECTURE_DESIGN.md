# Refactored Architecture - Design Rationale

## Overview

This document explains the design decisions behind the refactored ADK 1.18 multi-domain agent system with separated Query Execution.

## Architecture Principles

### 1. Separation of Concerns (SOC)

**Problem**: Original architecture mixed SQL generation with execution in domain agents, making it difficult to:
- Test SQL generation logic independently
- Enforce security policies consistently
- Monitor query performance centrally
- Implement cross-cutting concerns (retry, timeout, cost tracking)

**Solution**: Clean separation into distinct responsibilities:

```
┌─────────────────────────────────────────────────────────────┐
│                    OrchestratorAgent                        │
│              (Coordination & Workflow)                      │
└─────────────┬───────────────────────────────────────────────┘
              │
              ├──> IntentRouterAgent
              │    (Domain Classification)
              │
              ├──> DomainAgent (Sourcing/Collections/Disbursal)
              │    (SQL Generation ONLY)
              │
              └──> QueryExecutionAgent
                   (SQL Execution ONLY)
```

### 2. Single Responsibility Principle

**Each component has ONE job**:

| Component | Responsibility | What it DOESN'T do |
|-----------|---------------|-------------------|
| **IntentRouterAgent** | Route questions to domains | Generate SQL or execute queries |
| **DomainAgent** | Generate optimized SQL + metadata | Execute queries or access BigQuery |
| **QueryExecutionAgent** | Execute SQL safely with retry/timeout | Generate SQL or interpret intent |
| **OrchestratorAgent** | Coordinate pipeline flow | Generate SQL or execute queries |

### 3. Security by Design

**Problem**: Multiple agents with BigQuery access = multiple attack surfaces

**Solution**: 
- **Single Point of Execution**: Only `QueryExecutionAgent` has BigQuery client
- **Mandatory Safety Validation**: All queries pass through `SQLSafetyValidator`
- **Blocked Operations**: DELETE, UPDATE, TRUNCATE, DROP, ALTER, INSERT, MERGE
- **Injection Prevention**: Multi-statement detection, comment sanitization

```python
# BEFORE (Security Risk)
class DomainAgent:
    def __init__(self):
        self.bq_client = bigquery.Client()  # ❌ Multiple BQ clients
    
    async def run(self, question):
        sql = self.generate_sql(question)
        return self.bq_client.query(sql)  # ❌ No safety checks

# AFTER (Secure)
class DomainAgent:
    async def run(self, question):
        return SQLGenerationResponse(sql_query=sql)  # ✅ Returns SQL only

class QueryExecutionAgent:
    def execute(self, request):
        # ✅ Centralized safety validation
        is_safe, reason = SQLSafetyValidator.validate(request.sql_query)
        if not is_safe:
            return SQLExecutionResponse(status=BLOCKED, blocked_reason=reason)
        
        # ✅ Only component with BigQuery access
        return self.client.query(request.sql_query)
```

### 4. Testability

**Problem**: Testing query execution requires BigQuery access, slow and expensive

**Solution**: Test SQL generation and execution independently

```python
# Test SQL Generation (Fast, No BigQuery)
def test_collections_sql_generation():
    agent = CollectionsAgent()
    response = agent.generate_sql("What is 0+ dpd count?")
    assert "SOM_DPD >= 0" in response.sql_query  # ✅ Pure logic test

# Test Execution with Mock (Fast, No Cost)
def test_execution_with_mock():
    mock_client = Mock()
    agent = QueryExecutionAgent(project_id="test")
    agent.client = mock_client  # ✅ Inject mock
    
    # Test safety, retry, timeout logic without real queries
```

### 5. Observability

**Centralized Monitoring Points**:

```python
# QueryExecutionAgent provides single monitoring point
class QueryExecutionAgent:
    def execute(self, request):
        # Log EVERY query execution
        logger.info(f"Executing query for domain: {request.metadata.domain}")
        logger.info(f"Estimated cost: ${cost}")
        logger.info(f"Timeout: {request.timeout_seconds}s")
        
        # Track metrics
        execution_time = measure_time()
        bytes_processed = query_job.total_bytes_processed
        
        # Single place to add monitoring/alerting
        if bytes_processed > THRESHOLD:
            alert_expensive_query()
```

### 6. Extensibility

**Adding New Domains** (Easy):

1. Create new domain agent:
```python
class NewDomainAgent(BaseAgent):
    INSTRUCTION_TEMPLATE = "..."
    async def generate_sql(self, request): ...
```

2. Add routing rules:
```python
IntentRouterAgent.ROUTING_RULES[DomainType.NEW_DOMAIN] = {
    'keywords': ['keyword1', 'keyword2'],
    'patterns': [r'pattern1', r'pattern2']
}
```

3. Register in orchestrator:
```python
def _get_domain_agent(self, domain):
    if domain == DomainType.NEW_DOMAIN:
        return create_new_domain_agent()
```

**No changes needed** to execution agent or safety validator!

### 7. Performance Optimization

**Query Cost Control**:

```python
# Dry-run BEFORE execution
estimated_bytes = agent._dry_run(sql_query)
estimated_cost = agent._calculate_cost(estimated_bytes)

if estimated_cost > MAX_COST:
    # Reject expensive queries
    return SQLExecutionResponse(
        status=BLOCKED,
        blocked_reason=f"Query too expensive: ${estimated_cost:.2f}"
    )
```

**Retry with Exponential Backoff**:

```python
@retry.Retry(
    predicate=retry.if_transient_error,
    initial=1.0,
    maximum=10.0,
    multiplier=2.0,
    deadline=timeout_seconds
)
def _run_query():
    return self.client.query(sql_query)
```

## Folder Structure

```
Revamp_Copilot_ADK/
├── agents/
│   ├── intent/                  # Routing & Intent Detection
│   │   ├── __init__.py
│   │   └── router_agent.py      # IntentRouterAgent
│   │
│   ├── domain/                  # SQL Generation (No Execution)
│   │   ├── __init__.py
│   │   ├── collections_agent.py # CollectionsAgent
│   │   ├── sourcing_agent.py    # SourcingAgent
│   │   └── disbursal_agent.py   # DisbursalAgent
│   │
│   └── execution/               # SQL Execution (No Generation)
│       ├── __init__.py
│       └── query_execution_agent.py  # QueryExecutionAgent
│
├── contracts/                   # Structured Interfaces
│   ├── __init__.py
│   ├── sql_contracts.py         # SQL request/response models
│   └── routing_contracts.py     # Routing request/response models
│
├── utils/                       # Shared Utilities
│   ├── __init__.py
│   └── sql_safety_validator.py  # SQL safety validation
│
├── tests/                       # Unit & Integration Tests
│   ├── __init__.py
│   └── test_refactored_architecture.py
│
├── agent_refactored.py          # NEW: Refactored Orchestrator
├── agent.py                     # OLD: Original orchestrator
└── config/
    └── settings.py
```

## Contract-Based Communication

### Why Pydantic Models?

**Type Safety**:
```python
# BEFORE (Unstructured)
domain_agent.run(ctx)  # ❌ No idea what it returns
execution_agent.execute(sql, project_id, timeout=300)  # ❌ Easy to miss params

# AFTER (Typed Contracts)
request = SQLExecutionRequest(
    sql_query=sql,
    project_id=project_id,
    metadata=metadata,
    timeout_seconds=300  # ✅ IDE autocomplete, type checking
)
response: SQLExecutionResponse = agent.execute(request)  # ✅ Known structure
```

**Validation**:
```python
class SQLGenerationResponse(BaseModel):
    sql_query: str
    
    @field_validator('sql_query')
    def validate_sql_not_empty(cls, v):
        if not v.strip():
            raise ValueError("SQL cannot be empty")  # ✅ Fail fast
```

**Documentation**:
```python
class SQLExecutionRequest(BaseModel):
    """Request contract for Query Execution Agent"""
    sql_query: str = Field(..., description="SQL query to execute")
    timeout_seconds: int = Field(default=300, description="Query timeout")
    
    class Config:
        json_schema_extra = {
            "example": {  # ✅ Self-documenting
                "sql_query": "SELECT * FROM table",
                "timeout_seconds": 300
            }
        }
```

## Scaling Strategy

### Horizontal Scaling

**Stateless Agents** = Easy to scale:

```python
# Each request creates fresh agent instances
router = create_intent_router_agent()  # ✅ No shared state
domain_agent = create_collections_agent()  # ✅ Thread-safe
```

**Shared Execution Agent** handles connection pooling:

```python
# Singleton with connection pool
execution_agent = QueryExecutionAgent(project_id="...")
# BigQuery client manages connection pool internally
```

### Vertical Scaling (Query Optimization)

**Domain agents optimize SQL**:
- Partition pruning (date filters)
- Column selection (avoid SELECT *)
- Early LIMIT clauses
- CTE optimization

**Execution agent monitors**:
- Bytes processed
- Execution time
- Cost estimation

## Migration Path

### Phase 1: Parallel Running (Current)

```python
# Old agent still works
from agent import root_agent  # Original

# New agent ready for testing
from agent_refactored import root_agent  # Refactored
```

### Phase 2: Gradual Migration

1. Test refactored agent in staging
2. Compare results with original
3. Monitor performance metrics
4. Switch production traffic 10% → 50% → 100%

### Phase 3: Cleanup

1. Remove old `agent.py`
2. Rename `agent_refactored.py` → `agent.py`
3. Archive old domain agents

## Benefits Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Security** | Multiple BQ clients, no validation | Single execution point, mandatory validation |
| **Testability** | Requires BigQuery for tests | Pure logic tests, mockable execution |
| **Observability** | Scattered logging | Centralized monitoring |
| **Extensibility** | Modify multiple files | Add routing rule + domain agent |
| **Maintainability** | 600+ line agents | Focused 100-line components |
| **Performance** | No cost control | Dry-run estimation, query blocking |

## ADK 1.18 Alignment

**Uses ADK patterns**:
- ✅ `BaseAgent` for custom agents
- ✅ `Event` streaming for async communication
- ✅ `InvocationContext` for session management
- ✅ Pydantic models for structured data

**Doesn't rely on**:
- ❌ ADK's automatic function calling (unreliable for SQL)
- ❌ ADK's BigQueryToolset (direct client for control)

## Future Enhancements

1. **Query Caching**: Cache expensive query results
2. **Cost Budgets**: Per-session query cost limits
3. **Query Templates**: Reusable SQL patterns
4. **Result Pagination**: Stream large result sets
5. **Multi-Table Joins**: Cross-domain queries via semantic schema graph
6. **Query Explanation**: EXPLAIN PLAN before execution
7. **Audit Trail**: Persistent query history with user tracking
