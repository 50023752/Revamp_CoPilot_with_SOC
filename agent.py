"""
Refactored Orchestrator Agent
Coordinates: Intent Router → Domain Agent (SQL Gen) → Query Execution → Response Formatter
"""
import sys
import os

# Path fix
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import settings
import logging
import re
from datetime import datetime

# Pydantic imports
from typing import Callable, Any
from pydantic import PrivateAttr

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai.types import Content, Part

from agents.intent.router_agent import create_intent_router_agent
from agents.domain.collections_agent import create_collections_agent
from agents.domain.sourcing_agent import create_sourcing_agent
from agents.domain.disbursal_agent import create_disbursal_agent
from agents.execution.query_execution_agent import QueryExecutionAgent

from contracts.routing_contracts import DomainType, RoutingRequest
from contracts.sql_contracts import SQLGenerationRequest, SQLExecutionRequest

logger = logging.getLogger(__name__)


class OrchestratorAgent(BaseAgent):
    """
    Refactored Orchestrator Agent
    """
    # Use PrivateAttr for internal components to keep Pydantic happy
    _create_router: Callable = PrivateAttr()
    _create_collections: Callable = PrivateAttr()
    _create_sourcing: Callable = PrivateAttr()
    _create_disbursal: Callable = PrivateAttr()
    _execution_agent: QueryExecutionAgent = PrivateAttr()

    def __init__(self):
        super().__init__(name="OrchestratorAgent")
        
        # Initialize factories
        self._create_router = create_intent_router_agent
        self._create_collections = create_collections_agent
        self._create_sourcing = create_sourcing_agent
        self._create_disbursal = create_disbursal_agent
        
        # Initialize Execution Agent
        self._execution_agent = QueryExecutionAgent(
            project_id=settings.gcp_project_id,
            location=settings.bigquery_location
        )
        
        logger.info("✅ OrchestratorAgent initialized")
    
    async def _run_async_impl(self, ctx):
        """
        Main orchestration pipeline
        """
        # Initialize conversation history
        if 'conversation_history' not in ctx.session.state:
            ctx.session.state['conversation_history'] = []
        
        # Extract user question
        user_question = self._extract_question(ctx)
        logger.info(f"📝 User question: {user_question}")
        
        try:
            # ========================================
            # STEP 1: INTENT ROUTING
            # ========================================
            router = self._create_router()
            routing_request = RoutingRequest(
                user_question=user_question,
                conversation_context=ctx.session.state['conversation_history'],
                session_id=ctx.session.id
            )
            
            # --- FIX 1: Pass 'context=ctx' ---
            # We modified the router to require context for its LlmAgent sub-agent
            routing_response = await router.route(routing_request, context=ctx)
            
            domain = routing_response.selected_domain
            
            logger.info(
                f"🎯 Routed to: {domain.value} "
                f"(confidence: {routing_response.confidence_score:.2f})"
            )
            
            # ========================================
            # STEP 2: SQL GENERATION (Domain Agent)
            # ========================================
            domain_agent = self._get_domain_agent(domain)
            
            sql_gen_request = SQLGenerationRequest(
                user_question=user_question,
                domain=domain.value,
                conversation_context=ctx.session.state['conversation_history'],
                session_id=ctx.session.id
            )
            
            logger.info(f"🔧 Generating SQL with {domain.value} agent...")
            
            # --- FIX 2: Pass 'context=ctx' ---
            # We modified the Collections agent to require context for history masking
            sql_gen_response = await domain_agent.generate_sql(sql_gen_request, context=ctx)
            
            logger.info(f"✅ SQL generated")
            
            # ========================================
            # STEP 3: SQL EXECUTION
            # ========================================
            execution_request = SQLExecutionRequest(
                sql_query=sql_gen_response.sql_query,
                project_id=settings.gcp_project_id,
                metadata=sql_gen_response.metadata,
                dry_run=False,
                max_results=settings.max_query_rows,
                timeout_seconds=300
            )
            
            logger.info("⚡ Executing SQL query...")
            execution_response = self._execution_agent.execute(execution_request)
            
            if execution_response.status.value != "success":
                logger.error(f"❌ Execution failed: {execution_response.error_message}")
                yield Event(
                    content=Content(parts=[Part(
                        text=f"❌ Query execution failed: {execution_response.error_message}"
                    )]),
                    author="OrchestratorAgent"
                )
                return
            
            logger.info(f"✅ Query executed: {execution_response.row_count} rows")
            
            # ========================================
            # STEP 4: RESPONSE FORMATTING
            # ========================================
            formatted_response = self._format_response(
                sql_gen_response,
                execution_response
            )
            
            # Stream response to user
            yield Event(
                content=Content(parts=[Part(text=formatted_response)]),
                author="OrchestratorAgent"
            )
            
            # ========================================
            # STEP 5: UPDATE HISTORY
            # ========================================
            ctx.session.state['conversation_history'].append({
                'question': user_question,
                'domain': domain.value,
                'sql_query': sql_gen_response.sql_query,
                'row_count': execution_response.row_count,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            if len(ctx.session.state['conversation_history']) > 5:
                ctx.session.state['conversation_history'] = \
                    ctx.session.state['conversation_history'][-5:]
            
        except Exception as e:
            logger.error(f"❌ Orchestration error: {str(e)}", exc_info=True)
            yield Event(
                content=Content(parts=[Part(text=f"❌ Error: {str(e)}")]),
                author="OrchestratorAgent"
            )
    
    def _get_domain_agent(self, domain: DomainType):
        """Get domain agent instance based on routing decision"""
        if domain == DomainType.COLLECTIONS:
            return self._create_collections()
        elif domain == DomainType.SOURCING:
            return self._create_sourcing()
        elif domain == DomainType.DISBURSAL:
            return self._create_disbursal()
        else:
            raise ValueError(f"Unknown domain: {domain}")
    
    def _format_response(self, sql_response, execution_response) -> str:
        """Format execution results as markdown table"""
        output = []
        
        if sql_response.explanation:
            output.append(f"**{sql_response.explanation}**\n")
        
        if execution_response.rows:
            columns = execution_response.columns
            output.append("**Query Results:**\n")
            output.append("| " + " | ".join(columns) + " |")
            output.append("| " + " | ".join(["---"] * len(columns)) + " |")
            
            for row in execution_response.rows[:20]:
                row_values = [str(row.get(col, "")) for col in columns]
                output.append("| " + " | ".join(row_values) + " |")
            
            if execution_response.row_count > 20:
                output.append(f"\n*Showing 20 of {execution_response.row_count} total rows*")
        else:
            output.append("No results found.")
        
        output.append(f"\n---\n**Stats:** {execution_response.row_count} rows, {execution_response.execution_time_ms:.0f}ms")
        output.append(f"\n```sql\n{sql_response.sql_query}\n```")
        
        return "\n".join(output)
    
    def _extract_question(self, ctx) -> str:
        # Same logic as Router
        if hasattr(ctx, 'user_content') and ctx.user_content:
            if hasattr(ctx.user_content, 'parts'):
                return " ".join(p.text or "" for p in ctx.user_content.parts if hasattr(p, 'text') and p.text)
        
        if hasattr(ctx, 'new_message') and ctx.new_message:
             if hasattr(ctx.new_message, 'parts'):
                return " ".join(p.text or "" for p in ctx.new_message.parts if hasattr(p, 'text') and p.text)
                
        return ""



# ADK discovery - export root agent
root_agent = OrchestratorAgent()