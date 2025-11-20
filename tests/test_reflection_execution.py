import asyncio
import sys
import os
from unittest.mock import MagicMock, patch
from datetime import datetime

# Ensure repo root is on sys.path when running the test as a script
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agents.execution.query_execution_agent import QueryExecutionAgent, SQLReflectionAgent
from contracts.sql_contracts import SQLExecutionRequest, QueryMetadata, ExecutionStatus


def test_reflection_fix_and_success(monkeypatch):
    # Create agent with dummy project
    agent = QueryExecutionAgent(project_id="test-project")

    # Create a request with intentionally broken SQL
    broken_sql = "SELECT * FROM (SELECT 1 as col) WHERE"
    request = SQLExecutionRequest(
        sql_query=broken_sql,
        project_id="test-project",
        metadata=QueryMetadata(domain="COLLECTIONS", intent="test", generated_at=datetime.utcnow()),
        dry_run=False,
        timeout_seconds=30,
        max_results=10
    )

    # Mock safety validator to allow initial attempt (simulate it won't block)
    agent.safety_validator.validate = MagicMock(return_value=(True, None))

    # Mock dry_run to raise BadRequest on the first SQL, then succeed on second
    class DummyBadRequest(Exception):
        pass

    call = {"count": 0}

    def dry_run_raise(sql):
        call["count"] += 1
        if call["count"] == 1:
            raise exceptions.BadRequest("Syntax error at end of input")
        return 1024  # bytes

    # Patch the exceptions.BadRequest and agent._dry_run_raise
    import google.api_core.exceptions as _exc
    monkeypatch.setattr("agents.execution.query_execution_agent.exceptions.BadRequest", _exc.BadRequest)

    agent._dry_run_raise = dry_run_raise

    # Mock execute to return a successful result after reflection
    def execute_with_retry(sql, timeout_seconds, max_results):
        return {
            'rows': [[1]],
            'row_count': 1,
            'columns': ['col'],
            'bytes_processed': 1024,
            'query_id': 'q1'
        }

    agent._execute_with_retry = execute_with_retry

    # Mock reflection agent to return a fixed SQL on first call
    agent.reflection_agent.fix_sql_sync = MagicMock(return_value="SELECT * FROM (SELECT 1 as col)")

    # Run execute
    response = agent.execute(request)

    # Assert success after reflection
    assert response.status == ExecutionStatus.SUCCESS
    assert response.row_count == 1
    assert response.query_id == 'q1'


if __name__ == '__main__':
    # Provide a minimal monkeypatch replacement so the test can be run standalone
    class SimpleMonkeyPatch:
        def setattr(self, target, value):
            """Support setting attributes by dotted-path string.

            This resolves the longest importable module prefix then walks
            remaining attribute names via getattr to set the final attribute.

            Example supported targets:
              - "agents.execution.query_execution_agent.exceptions.BadRequest"
              - "module.submodule.attr"
            """
            if not isinstance(target, str):
                raise TypeError("SimpleMonkeyPatch only supports dotted-string targets")

            parts = target.split('.')
            # Find the longest prefix that can be imported as a module
            for i in range(len(parts), 0, -1):
                module_name = '.'.join(parts[:i])
                try:
                    mod = __import__(module_name, fromlist=['*'])
                except ModuleNotFoundError:
                    continue
                except Exception:
                    # Other import issues - re-raise to signal test author
                    raise

                # Success: walk remaining attribute parts (if any)
                obj = mod
                for attr in parts[i: -1]:
                    obj = getattr(obj, attr)

                final_attr = parts[-1]
                setattr(obj, final_attr, value)
                return

            # If we couldn't import any prefix, raise informative error
            raise ImportError(f"Could not resolve importable module in target '{target}'")

    monkey = SimpleMonkeyPatch()
    asyncio.run(test_reflection_fix_and_success(monkey))
