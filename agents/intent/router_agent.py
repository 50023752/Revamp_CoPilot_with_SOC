"""
Intent Router Agent
Routes user questions to appropriate domain agents using deterministic keyword matching
and LLM-based classification via a standard LlmAgent sub-agent.
"""
import sys
import os
import logging
import re
from typing import Dict, List, Optional, ClassVar
import asyncio

# Standard ADK imports
from google.adk.agents import BaseAgent, LlmAgent
from google.adk.events import Event
from google.adk.agents.invocation_context import InvocationContext
from google.genai.types import Content, Part

# Project imports
# (Ensure your path fixing block remains at the top of your file if needed)
from config.settings import settings
from contracts.routing_contracts import (
    RoutingRequest,
    RoutingResponse,
    DomainType
)

logger = logging.getLogger(__name__)

class IntentRouterAgent(BaseAgent):
    """
    Intent Router with hybrid routing strategy.
    Uses 'LlmAgent' for classification to ensure correct client context.
    """
    
    # Define the sub-agent explicitly for Pydantic
    classifier: LlmAgent
    model_config = {"arbitrary_types_allowed": True}

    # ---------------------------------------------------------
    # 1. ROUTING RULES (Deterministic / Keyword)
    # ---------------------------------------------------------
    ROUTING_RULES: ClassVar[Dict] = {
        DomainType.COLLECTIONS: {
            'keywords': [
                'dpd', 'delinquency', 'delinquent', 'recovery', 'collection',
                'overdue', 'payment', 'outstanding', 'pos', 'portfolio',
                'bucket', 'nns', 'gns', 'mob', '0+', '30+', '60+', '90+',
                '120+', 'emi', 'installment', 'arrears'
            ],
            'patterns': [
                r'\b\d+\+',      # Matches "0+", "30+"
                r'\bgns\d+',     # Matches "gns1"
                r'\bnns\d+',     # Matches "nns1"  
            ],
        },
        DomainType.SOURCING: {
            'keywords': [
                'application', 'approval', 'sourcing', 'acquisition',
                'apply', 'customer', 'segment', 'product', 'conversion',
                'funnel', 'bre', 'sanction', 'manufacturer', 'dealer',
                'branch', 'rejected', 'accepted', 'abnd', 'asset cost'
            ],
            'patterns': [
                r'\bapproval\s+rate\b',
                r'\bconversion\s+rate\b',
                r'\bapplication\s+volume\b',
            ],
        },
        DomainType.DISBURSAL: {
            'keywords': [
                'disbursal', 'disbursement', 'payout', 'transfer',
                'neft', 'rtgs', 'imps', 'fund', 'processing',
                'disbursed', 'payment mode'
            ],
            'patterns': [
                r'\bdisburs(al|ement)\s+(amount|count|trend)\b',
                r'\bpayment\s+mode\b',
            ],
        }
    }
    
    # If score is >= 0.6, we route immediately without LLM
    CONFIDENCE_THRESHOLD: ClassVar[float] = 0.6

    # INSTRUCTION FOR THE SUB-AGENT
    CLASSIFIER_INSTRUCTION: ClassVar[str] = """
    You are an intent classifier for a finance analytics system.
    Classify the user's input into exactly one of these domains:
    1. SOURCING (Loans, applications, approvals, acquisition)
    2. COLLECTIONS (Delinquency, DPD, recovery, overdue payments)
    3. DISBURSAL (Payouts, fund transfers, payment modes)

    Response Format: Return ONLY the single word of the domain (e.g., "SOURCING"). Do not add punctuation or explanation.
    """

    def __init__(self):
        model_name = settings.gemini_flash_model
        if not model_name:
            raise ValueError("GEMINI_FLASH_MODEL is not set in settings.")

        # Initialize the sub-agent using LlmAgent (Standard ADK way)
        classifier_agent = LlmAgent(
            name="IntentClassifier",
            model=model_name,
            instruction=self.CLASSIFIER_INSTRUCTION,
            output_key="classification_result"
        )

        super().__init__(
            name="IntentRouterAgent",
            classifier=classifier_agent,
            sub_agents=[classifier_agent]
        )
        logger.info(f"IntentRouterAgent initialized with model: {model_name}")

    async def _run_async_impl(self, ctx: InvocationContext):
        """
        ADK Orchestration Entry Point
        """
        user_question = self._extract_question(ctx)
        
        request = RoutingRequest(
            user_question=user_question,
            conversation_context=ctx.session.state.get('conversation_history', []),
            session_id=ctx.session.id
        )
        
        # Pass the context to route() so it can invoke the sub-agent
        routing_response = await self.route(request, context=ctx)
        
        ctx.session.state['routing_response'] = routing_response.model_dump()
        
        yield Event(
            content=Content(parts=[Part(text=routing_response.selected_domain.value)]),
            author="IntentRouterAgent"
        )

    async def route(self, request: RoutingRequest, context: Optional[InvocationContext] = None) -> RoutingResponse:
        """
        Route question to domain using hybrid strategy.
        """
        question_lower = request.user_question.lower()
        
        # 1. Check Follow-up
        if self._is_followup(request) and request.conversation_context:
            previous_domain = request.conversation_context[-1].get('domain', 'UNKNOWN')
            return RoutingResponse(
                selected_domain=DomainType(previous_domain),
                confidence_score=0.95,
                is_followup=True,
                matched_keywords=[],
                reasoning="Follow-up question"
            )
        
        # 2. Deterministic Keyword Matching
        keyword_scores = self._calculate_keyword_scores(question_lower)
        
        # Find the best match
        if keyword_scores:
            best_domain = max(keyword_scores, key=keyword_scores.get)
            best_score = keyword_scores[best_domain]
            
            # If score meets threshold, return immediately (FAST PATH)
            if best_score >= self.CONFIDENCE_THRESHOLD:
                matched_keywords = self._get_matched_keywords(question_lower, best_domain)
                logger.info(f"Deterministic routing: {best_domain.value} (Score: {best_score:.2f})")
                return RoutingResponse(
                    selected_domain=best_domain,
                    confidence_score=best_score,
                    is_followup=False,
                    matched_keywords=matched_keywords,
                    reasoning="Strong keyword match"
                )
        
        # 3. LLM Classification (SLOW PATH - Fallback)
        # If keywords failed or were ambiguous, ask the AI.
        if context:
            llm_domain = await self._llm_classify_with_agent(request.user_question, context)
        else:
            logger.warning("No context provided for LLM routing, falling back to default.")
            llm_domain = DomainType.SOURCING

        matched_keywords = self._get_matched_keywords(question_lower, llm_domain)
        
        return RoutingResponse(
            selected_domain=llm_domain,
            confidence_score=0.7,
            is_followup=False,
            matched_keywords=matched_keywords,
            reasoning="LLM-based classification",
            alternative_domains=list(keyword_scores.keys()) if keyword_scores else []
        )

    async def _llm_classify_with_agent(self, question: str, ctx: InvocationContext) -> DomainType:
        """
        Uses the LlmAgent sub-agent to classify.
        """
        # NOTE: We rely on the Orchestrator to have set ctx.current_input correctly.
        # We do NOT manually wipe history here to avoid ADK version errors.
        
        response_text = ""
        try:
            async for event in self.classifier.run_async(ctx):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            response_text += part.text
        except Exception as e:
            logger.error(f"LLM Classification failed: {e}")
            return self._fallback_routing(question)

        # Parse Response
        cleaned_response = response_text.strip().upper()
        
        # Check if response matches any known domain
        for domain in DomainType:
            if domain.value in cleaned_response:
                return domain
        
        logger.warning(f"LLM returned unclear response: {cleaned_response}")
        return self._fallback_routing(question)

    def _calculate_keyword_scores(self, question: str) -> Dict[DomainType, float]:
        """
        Calculate match scores based on confidence weights.
        
        Scoring Logic:
        - Regex Pattern Match: +0.8 (Instant high confidence)
        - Exact Keyword Match: +0.3 (Accumulative)
        
        Example: "0+ dpd" -> 0.8 (pattern) + 0.3 (keyword) = 1.1 -> Capped at 1.0
        """
        scores = {}
        
        for domain, rules in self.ROUTING_RULES.items():
            score = 0.0
            
            # 1. Patterns (High Confidence Triggers)
            for pattern in rules.get('patterns', []):
                # re.search is enough, we don't need to count every occurrence
                if re.search(pattern, question, re.IGNORECASE):
                    score += 0.8
                    logger.info(f"Pattern match found for {domain.value}: {pattern}")
                    break # One strong pattern is enough
            
            # 2. Keywords (Accumulative Confidence)
            matches = 0
            for keyword in rules['keywords']:
                # Use simple inclusion check
                if keyword.lower() in question:
                    matches += 1
            
            # Add 0.3 per keyword match
            if matches > 0:
                score += (matches * 0.3)
            
            # Cap the score at 1.0 (100% confidence)
            if score > 0:
                scores[domain] = min(1.0, score)
                
        return scores
    
    def _get_matched_keywords(self, question: str, domain: DomainType) -> List[str]:
        matched = []
        rules = self.ROUTING_RULES[domain]
        for keyword in rules['keywords']:
            if keyword.lower() in question:
                matched.append(keyword)
        for pattern in rules.get('patterns', []):
            if re.search(pattern, question, re.IGNORECASE):
                matched.append(pattern)
        return matched[:5]
    
    def _is_followup(self, request: RoutingRequest) -> bool:
        if not request.conversation_context: return False
        question_lower = request.user_question.lower()
        followup_patterns = ['show me', 'what about', 'for those', 'those', 'these', 'it', 'same']
        if any(p in question_lower for p in followup_patterns): return True
        return False
    
    def _fallback_routing(self, question: str) -> DomainType:
        scores = self._calculate_keyword_scores(question.lower())
        if not scores: return DomainType.SOURCING
        return max(scores, key=scores.get)

    def _extract_question(self, ctx) -> str:
        """Extracts text from ADK 1.18.0 Context"""
        # Priority 1: user_content (The standard for this version)
        if hasattr(ctx, 'user_content') and ctx.user_content:
            if hasattr(ctx.user_content, 'parts'):
                return " ".join(p.text or "" for p in ctx.user_content.parts if hasattr(p, 'text') and p.text)
        
        # Priority 2: new_message (Older versions or event triggers)
        if hasattr(ctx, 'new_message') and ctx.new_message:
             if hasattr(ctx.new_message, 'parts'):
                return " ".join(p.text or "" for p in ctx.new_message.parts if hasattr(p, 'text') and p.text)

        return ""

# Factory function
def create_intent_router_agent():
    return IntentRouterAgent()

if __name__ == "__main__":
    async def main():
        try:
            agent = create_intent_router_agent()
            print("Agent created successfully.")
        except Exception as e:
            print(f"Initialization failed: {e}")
            
    asyncio.run(main())