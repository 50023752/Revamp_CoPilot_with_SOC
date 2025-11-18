"""
Unit Tests for Refactored Architecture
Tests SQL generation separately from execution.
"""
import sys
import os
import pytest
import pytest_asyncio
from unittest.mock import Mock, patch
from datetime import datetime, timezone

# --- PATH FIX: Add project root to sys.path ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import settings

# Check configuration for skipping tests if keys are missing
HAS_API_KEY = bool(settings.google_api_key)
HAS_MODEL = bool(settings.gemini_flash_model)

if HAS_API_KEY:
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key

from agents.intent.router_agent import IntentRouterAgent
from agents.domain.collections_agent import CollectionsAgent
from agents.execution.query_execution_agent import QueryExecutionAgent
from contracts.routing_contracts import RoutingRequest, DomainType
from contracts.sql_contracts import (
    SQLGenerationRequest,
    SQLExecutionRequest,
    ExecutionStatus,
    QueryMetadata
)
from utils.sql_safety_validator import SQLSafetyValidator


class TestSQLSafetyValidator:
    """Test SQL safety validation logic (Sync)"""
    
    def test_allow_select_query(self):
        sql = "SELECT * FROM table WHERE id = 1"
        is_valid, error = SQLSafetyValidator.validate(sql)
        assert is_valid is True
        assert error is None
    
    def test_block_delete_query(self):
        sql = "DELETE FROM table WHERE id = 1"
        is_valid, error = SQLSafetyValidator.validate(sql)
        assert is_valid is False
        assert "DELETE" in error
    
    def test_block_drop_query(self):
        sql = "DROP TABLE table"
        is_valid, error = SQLSafetyValidator.validate(sql)
        assert is_valid is False
        assert "DROP" in error


@pytest.mark.skipif(not (HAS_API_KEY and HAS_MODEL), reason="LLM configuration missing")
class TestIntentRouter:
    """Test intent routing logic (Async - Requires Real LLM)"""
    
    @pytest_asyncio.fixture
    async def router(self):
        # We create the real agent. It handles its own LlmAgent setup now.
        return IntentRouterAgent()
    
    @pytest.mark.asyncio
    async def test_route_collections_dpd_question(self, router):
        request = RoutingRequest(
            user_question="What is the 0+ dpd count for last month?",
            conversation_context=[],
            session_id="test_session"
        )
        # We await the result. The agent creates a temp context internally if None is passed.
        response = await router.route(request) 
        
        assert response.selected_domain == DomainType.COLLECTIONS
        # Flexible assertion for confidence or keywords
        assert response.confidence_score >= 0.6 or 'dpd' in response.matched_keywords
    
    @pytest.mark.asyncio
    async def test_route_sourcing_approval_question(self, router):
        request = RoutingRequest(
            user_question="Show me approval rate by branch",
            conversation_context=[],
            session_id="test_session"
        )
        response = await router.route(request)
        
        assert response.selected_domain == DomainType.SOURCING
    
    @pytest.mark.asyncio
    async def test_followup_question_routing(self, router):
        # Setup context simulating a previous turn
        prev_context = [
            {'question': 'Show me dpd trend', 'domain': 'COLLECTIONS'}
        ]
        
        request = RoutingRequest(
            user_question="What about last 6 months?",
            conversation_context=prev_context,
            session_id="test_session"
        )
        
        response = await router.route(request)
        
        assert response.is_followup is True
        assert response.selected_domain == DomainType.COLLECTIONS


@pytest.mark.skipif(not (HAS_API_KEY and HAS_MODEL), reason="LLM configuration missing")
class TestCollectionsAgent:
    """Test Collections SQL generation (Async - Requires Real LLM)"""
    
    @pytest_asyncio.fixture
    async def agent(self):
        return CollectionsAgent()
        
    @pytest.mark.asyncio
    async def test_generate_sql_for_dpd_question(self, agent):
        request = SQLGenerationRequest(
            user_question="What is the 0+ dpd count for last 3 months?",
            domain="COLLECTIONS",
            conversation_context=[],
            session_id="test_session"
        )
        
        # This calls the LlmAgent internally
        response = await agent.generate_sql(request)
        
        assert response.sql_query is not None
        assert len(response.sql_query) > 10
        assert "SELECT" in response.sql_query.upper()
        assert response.metadata.domain == "COLLECTIONS"


class TestQueryExecutionAgent:
    """Test Query Execution Agent (Mocked BigQuery - No Cost)"""
    
    @pytest.fixture
    def mock_bq_client(self):
        """Mock BigQuery client to prevent real API calls"""
        with patch('google.cloud.bigquery.Client') as mock:
            yield mock
    
    def test_safety_validation_blocks_destructive_query(self, mock_bq_client):
        # The agent will use the mocked client
        agent = QueryExecutionAgent(project_id="test-project")
        
        request = SQLExecutionRequest(
            sql_query="DELETE FROM table WHERE id = 1",
            project_id="test-project",
            metadata=QueryMetadata(
                domain="COLLECTIONS",
                intent="Test",
                generated_at=datetime.now(timezone.utc) 
            )
        )
        
        response = agent.execute(request)
        
        assert response.status == ExecutionStatus.BLOCKED
        assert response.blocked_reason is not None
        assert "DELETE" in response.blocked_reason
    
    def test_dry_run_returns_cost_estimate(self, mock_bq_client):
        agent = QueryExecutionAgent(project_id="test-project")
        
        # Configure the mock to return a specific job object
        mock_job = Mock()
        mock_job.total_bytes_processed = 1_000_000  # 1 MB
        # Ensure the query method returns this mock job
        agent.client.query.return_value = mock_job
        
        request = SQLExecutionRequest(
            sql_query="SELECT * FROM table LIMIT 10",
            project_id="test-project",
            metadata=QueryMetadata(
                domain="COLLECTIONS",
                intent="Test",
                generated_at=datetime.now(timezone.utc)
            ),
            dry_run=True
        )
        
        response = agent.execute(request)
        
        assert response.status == ExecutionStatus.DRY_RUN
        assert response.bytes_processed == 1_000_000
        assert response.estimated_cost_usd is not None

# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])