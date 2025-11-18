# Refactored Architecture - Quick Start Guide

## 🎯 What Changed?

Introduced **Query Execution Agent** - the ONLY component that executes SQL against BigQuery.

### New Pipeline

```
User Question
     ↓
IntentRouterAgent → Domain Classification
     ↓
DomainAgent → SQL Generation (NO execution)
     ↓
QueryExecutionAgent → SQL Execution (safety validated)
     ↓
OrchestratorAgent → Response Formatting
     ↓
User Response
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run Refactored Agent

```bash
# Use the new refactored agent
python agent_refactored.py
```

### 3. Test with Streamlit

```bash
streamlit run streamlit_app.py
```

The orchestrator will automatically use the refactored architecture.

## 📁 New Folder Structure

```
agents/
├── intent/           # Routing logic
│   └── router_agent.py
├── domain/           # SQL generation (no execution)
│   ├── collections_agent.py
│   ├── sourcing_agent.py
│   └── disbursal_agent.py
└── execution/        # SQL execution (no generation)
    └── query_execution_agent.py

contracts/            # Typed interfaces
├── sql_contracts.py
└── routing_contracts.py

utils/                # Shared utilities
└── sql_safety_validator.py

tests/                # Unit tests
└── test_refactored_architecture.py
```

## 🔒 Security Features

### SQL Safety Validator

Blocks destructive operations:

```python
from utils.sql_safety_validator import SQLSafetyValidator

is_safe, error = SQLSafetyValidator.validate("DELETE FROM table")
# Returns: (False, "Blocked: Query contains destructive keyword 'DELETE'")
```

**Blocked Keywords**:
- DELETE, UPDATE, TRUNCATE, DROP, ALTER
- INSERT, MERGE, CREATE, REPLACE
- GRANT, REVOKE

### Single Execution Point

Only `QueryExecutionAgent` can execute SQL:

```python
# ✅ ALLOWED
execution_agent = QueryExecutionAgent(project_id="...")
response = execution_agent.execute(request)

# ❌ BLOCKED - Domain agents can't execute
collections_agent = CollectionsAgent()
# No .execute() method, only .generate_sql()
```

## 📊 Cost Control

### Dry-Run Estimation

```python
from contracts.sql_contracts import SQLExecutionRequest

request = SQLExecutionRequest(
    sql_query="SELECT * FROM huge_table",
    project_id="my-project",
    metadata=metadata,
    dry_run=True  # ✅ Estimate cost without executing
)

response = execution_agent.execute(request)
print(f"Estimated cost: ${response.estimated_cost_usd}")
print(f"Bytes to process: {response.bytes_processed:,}")
```

### Query Timeout

```python
request = SQLExecutionRequest(
    sql_query=sql,
    project_id="my-project",
    metadata=metadata,
    timeout_seconds=60  # ✅ Fail fast on slow queries
)
```

## 🧪 Testing

### Test SQL Generation (No BigQuery Required)

```python
import pytest
from agents.domain.collections_agent import CollectionsAgent

@pytest.mark.asyncio
async def test_sql_generation():
    agent = CollectionsAgent()
    request = SQLGenerationRequest(
        user_question="What is 0+ dpd count?",
        domain="COLLECTIONS",
        session_id="test"
    )
    
    response = await agent.generate_sql(request)
    
    # ✅ Test SQL logic without BigQuery
    assert "SOM_DPD >= 0" in response.sql_query
    assert "COUNT(DISTINCT AGREEMENTNO)" in response.sql_query
```

### Test Execution with Mock

```python
from unittest.mock import Mock

def test_execution_with_mock():
    agent = QueryExecutionAgent(project_id="test")
    
    # Mock BigQuery client
    mock_job = Mock()
    mock_job.total_bytes_processed = 1_000_000
    agent.client.query = Mock(return_value=mock_job)
    
    # ✅ Test execution logic without real queries
    response = agent.execute(request)
```

### Run All Tests

```bash
pytest tests/test_refactored_architecture.py -v
```

## 📝 Using Contracts

### Routing Request

```python
from contracts.routing_contracts import RoutingRequest, DomainType

request = RoutingRequest(
    user_question="What is the 0+ dpd count?",
    conversation_context=[],
    session_id="session_123"
)

router = IntentRouterAgent()
response = router.route(request)

print(f"Domain: {response.selected_domain.value}")
print(f"Confidence: {response.confidence_score}")
print(f"Keywords: {response.matched_keywords}")
```

### SQL Generation Request

```python
from contracts.sql_contracts import SQLGenerationRequest

request = SQLGenerationRequest(
    user_question="Show me approval rate by branch",
    domain="SOURCING",
    conversation_context=[],
    session_id="session_123"
)

agent = SourcingAgent()
response = await agent.generate_sql(request)

print(f"SQL: {response.sql_query}")
print(f"Metadata: {response.metadata}")
```

### SQL Execution Request

```python
from contracts.sql_contracts import SQLExecutionRequest

request = SQLExecutionRequest(
    sql_query="SELECT * FROM table LIMIT 10",
    project_id="my-gcp-project",
    metadata=QueryMetadata(
        domain="COLLECTIONS",
        intent="Get data",
        generated_at=datetime.utcnow()
    ),
    dry_run=False,
    max_results=1000,
    timeout_seconds=300
)

execution_agent = QueryExecutionAgent(project_id="my-gcp-project")
response = execution_agent.execute(request)

if response.status == ExecutionStatus.SUCCESS:
    print(f"Rows: {response.row_count}")
    print(f"Cost: ${response.estimated_cost_usd}")
    print(f"Time: {response.execution_time_ms}ms")
```

## 🔧 Adding New Domain

### 1. Create Domain Agent

```python
# agents/domain/new_domain_agent.py

class NewDomainAgent(BaseAgent):
    INSTRUCTION_TEMPLATE = """
    Generate SQL for new domain...
    """
    
    async def generate_sql(self, request):
        # Generate SQL only, no execution
        return SQLGenerationResponse(sql_query=sql, ...)
```

### 2. Add Routing Rules

```python
# agents/intent/router_agent.py

IntentRouterAgent.ROUTING_RULES[DomainType.NEW_DOMAIN] = {
    'keywords': ['keyword1', 'keyword2'],
    'patterns': [r'pattern1'],
    'description': 'Domain description'
}
```

### 3. Register in Orchestrator

```python
# agent_refactored.py

def _get_domain_agent(self, domain):
    if domain == DomainType.NEW_DOMAIN:
        return create_new_domain_agent()
```

## 🐛 Debugging

### Enable Debug Logging

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
```

### Check Routing Decision

```python
routing_response = ctx.session.state.get('routing_response')
print(f"Routed to: {routing_response['selected_domain']}")
print(f"Confidence: {routing_response['confidence_score']}")
print(f"Reasoning: {routing_response['reasoning']}")
```

### Check Generated SQL

```python
sql_response = ctx.session.state.get('sql_generation_response')
print(f"SQL: {sql_response['sql_query']}")
print(f"Metadata: {sql_response['metadata']}")
```

### Check Execution Results

```python
execution_response = ctx.session.state.get('execution_response')
print(f"Status: {execution_response['status']}")
print(f"Rows: {execution_response['row_count']}")
print(f"Error: {execution_response.get('error_message')}")
```

## 📚 Documentation

- **Architecture Design**: See [ARCHITECTURE_DESIGN.md](ARCHITECTURE_DESIGN.md)
- **Contract Specifications**: See [contracts/](contracts/)
- **Testing Strategy**: See [tests/](tests/)
- **Original Architecture**: See [.github/copilot-instructions.md](.github/copilot-instructions.md)

## 🔄 Migration from Old Architecture

### Comparison

| Feature | Old (`agent.py`) | New (`agent_refactored.py`) |
|---------|------------------|----------------------------|
| SQL Generation | Mixed with execution | Separate domain agents |
| SQL Execution | Multiple agents | Single execution agent |
| Security | No validation | Mandatory safety checks |
| Cost Control | None | Dry-run estimation |
| Testing | Requires BigQuery | Mockable components |
| Monitoring | Scattered | Centralized |

### Running Both Versions

```python
# Old version
from agent import root_agent as old_agent

# New version
from agent_refactored import root_agent as new_agent

# Test both
response_old = await old_agent.run_async(ctx)
response_new = await new_agent.run_async(ctx)
```

## 🎓 Best Practices

### 1. Always Use Contracts

```python
# ❌ DON'T
agent.execute(sql, "my-project", 300, False)

# ✅ DO
request = SQLExecutionRequest(
    sql_query=sql,
    project_id="my-project",
    timeout_seconds=300,
    dry_run=False,
    metadata=metadata
)
agent.execute(request)
```

### 2. Test SQL Generation Separately

```python
# ✅ Fast unit test
async def test_sql_logic():
    response = await agent.generate_sql(request)
    assert "expected_pattern" in response.sql_query

# ✅ Integration test with mock
def test_execution_logic():
    agent.client = mock_client
    response = agent.execute(request)
```

### 3. Use Dry-Run for Expensive Queries

```python
# Estimate cost first
dry_run_request = SQLExecutionRequest(..., dry_run=True)
estimate = agent.execute(dry_run_request)

if estimate.estimated_cost_usd > MAX_COST:
    # Reject or optimize query
    return
```

### 4. Monitor Execution Metrics

```python
response = agent.execute(request)

logger.info(f"Query executed:")
logger.info(f"  Rows: {response.row_count:,}")
logger.info(f"  Time: {response.execution_time_ms:.2f}ms")
logger.info(f"  Bytes: {response.bytes_processed:,}")
logger.info(f"  Cost: ${response.estimated_cost_usd:.6f}")
```

## ❓ FAQ

**Q: Can domain agents execute SQL?**  
A: No. Domain agents only generate SQL. Execution is delegated to `QueryExecutionAgent`.

**Q: How do I add a new domain?**  
A: Create domain agent, add routing rules, register in orchestrator. See "Adding New Domain" above.

**Q: What SQL operations are blocked?**  
A: DELETE, UPDATE, TRUNCATE, DROP, ALTER, INSERT, MERGE, CREATE, GRANT, REVOKE.

**Q: Can I bypass safety validation?**  
A: No. All queries pass through `SQLSafetyValidator` before execution.

**Q: How do I test without BigQuery?**  
A: Test SQL generation with domain agents (no BigQuery). Test execution with mocked BigQuery client.

**Q: What's the performance impact?**  
A: Minimal. Dry-run validation adds ~100ms. Benefit: prevents expensive queries from executing.

## 🚨 Troubleshooting

### Query Blocked Error

```
Error: Blocked: Query contains destructive keyword 'DELETE'
```

**Solution**: Remove destructive operations. Only SELECT/WITH queries allowed.

### Timeout Error

```
Error: Query timeout after 300000ms
```

**Solution**: Increase timeout or optimize query:
```python
request = SQLExecutionRequest(..., timeout_seconds=600)
```

### Cost Estimate Too High

```
Estimated cost: $50.00 (blocked)
```

**Solution**: Optimize query:
- Add partition filters (date ranges)
- Select specific columns (avoid SELECT *)
- Add LIMIT clause early
- Use CTEs to reduce scanned data

## 📞 Support

For issues or questions, see:
- Architecture docs: [ARCHITECTURE_DESIGN.md](ARCHITECTURE_DESIGN.md)
- Test examples: [tests/test_refactored_architecture.py](tests/test_refactored_architecture.py)
- Contract specs: [contracts/](contracts/)
