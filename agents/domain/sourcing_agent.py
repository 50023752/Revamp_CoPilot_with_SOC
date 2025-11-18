"""
Sourcing Domain Agent - SQL Generation Only
Generates SQL queries for loan application and approval analysis
"""

from config.settings import settings
import logging
from datetime import datetime
from typing import ClassVar
from google.adk.agents import BaseAgent
from google.adk.models.google_llm import Gemini
from google.adk.events import Event
from google.genai.types import Content, Part

from contracts.sql_contracts import SQLGenerationRequest, SQLGenerationResponse, QueryMetadata

logger = logging.getLogger(__name__)


class SourcingAgent(BaseAgent):
    """Sourcing Domain Agent - SQL Generation Only"""
    
    INSTRUCTION_TEMPLATE: ClassVar[str] = f"""You are a BigQuery SQL expert for L&T Finance Two-Wheeler **sourcing/application data**.

Generate ONLY the SQL query wrapped in ```sql ``` tags.

Table: `{settings.gcp_project_id}.{settings.bigquery_dataset}.{settings.sourcing_table}`

Key Rules:
- Date field: `LastModifiedDate`
- Approved: `BRE_Sanction_Result__c = 'ACCEPT'` or `ABND IS NOT NULL`
- Rejected: `REJECTED IS NOT NULL`
- Count: `COUNT(*)`
- Amounts to crores: `ROUND(SUM(amount) / 10000000, 2)`
- Use SAFE_DIVIDE for percentages
"""
    
    def __init__(self):
        super().__init__(name="SourcingAgent")
        try:
            model_name = settings.gemini_flash_model
            if not model_name:
                logger.error("Failed to initialize Gemini: GEMINI_FLASH_MODEL is not set in settings.")
                raise ValueError("GEMINI_FLASH_MODEL is not set in settings.")
            llm_instance = Gemini(model=model_name)
            object.__setattr__(self, '_llm', llm_instance)
            logger.info(f"SourcingAgent initialized with Gemini model: {model_name}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini: {e}")
            raise
        logger.info("SourcingAgent initialized (SQL generation only)")
    
    async def _run_async_impl(self, ctx):
        user_question = self._extract_question(ctx)
        request = SQLGenerationRequest(
            user_question=user_question,
            domain="SOURCING",
            conversation_context=ctx.session.state.get('conversation_history', []),
            session_id=ctx.session.id
        )
        response = await self.generate_sql(request)
        ctx.session.state['sql_generation_response'] = response.model_dump()
        yield Event(content=Content(parts=[Part(text=response.sql_query)]), author="SourcingAgent")
    
    async def generate_sql(self, request: SQLGenerationRequest) -> SQLGenerationResponse:
        import re
        from google.adk.models.llm_request import LlmRequest
        
        prompt = f"{self.INSTRUCTION_TEMPLATE}\n\nQuestion: {request.user_question}"
        
        # Create LlmRequest with proper ADK structure
        llm_request = LlmRequest(
            contents=[Content(parts=[Part(text=prompt)])]
        )
        
        # Generate SQL using Gemini's async streaming API
        llm_response_parts = []
        async for response_chunk in object.__getattribute__(self, '_llm').generate_content_async(
            llm_request, stream=True
        ):
            if hasattr(response_chunk, 'text') and response_chunk.text:
                llm_response_parts.append(response_chunk.text)
        
        llm_response = "".join(llm_response_parts)
        sql_match = re.search(r'```sql\s+(.*?)\s+```', llm_response, re.DOTALL | re.IGNORECASE)
        
        if not sql_match:
            raise ValueError("Failed to generate valid SQL query")
        
        sql_query = sql_match.group(1).strip()
        
        return SQLGenerationResponse(
            sql_query=sql_query,
            metadata=QueryMetadata(
                domain="SOURCING",
                intent=request.user_question,
                generated_at=datetime.utcnow()
            ),
            explanation=f"Query for sourcing analysis: {request.user_question}",
            expected_columns=[],
            formatting_hints={'percentage': '2_decimal', 'amount': 'crores'}
        )
    
    def _extract_question(self, ctx) -> str:
        if hasattr(ctx, 'new_message') and ctx.new_message and hasattr(ctx.new_message, 'parts'):
            return " ".join(part.text or "" for part in ctx.new_message.parts if hasattr(part, 'text') and part.text)
        elif hasattr(ctx, 'current_input') and ctx.current_input:
            return str(ctx.current_input)
        return ""


def create_sourcing_agent():
    return SourcingAgent()
