"""
Routing Contracts for Intent Detection and Domain Routing
Defines structured interfaces for the Router Agent
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


class DomainType(str, Enum):
    """Domain types for routing"""
    SOURCING = "SOURCING"
    COLLECTIONS = "COLLECTIONS"
    DISBURSAL = "DISBURSAL"
    UNKNOWN = "UNKNOWN"


class RoutingRequest(BaseModel):
    """Request contract for Router Agent"""
    user_question: str = Field(..., description="User's natural language question")
    conversation_context: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="Previous conversation history for context-aware routing"
    )
    session_id: str = Field(..., description="Session identifier")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_question": "What is the 0+ dpd count for last month?",
                "conversation_context": [
                    {
                        "question": "Show me collections data",
                        "domain": "COLLECTIONS"
                    }
                ],
                "session_id": "session_20251117_123456"
            }
        }


class RoutingResponse(BaseModel):
    """Response contract from Router Agent"""
    selected_domain: DomainType = Field(..., description="Selected domain for the question")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence in routing decision (0-1)")
    is_followup: bool = Field(default=False, description="Whether this is a follow-up question")
    matched_keywords: List[str] = Field(default_factory=list, description="Keywords that influenced the decision")
    reasoning: Optional[str] = Field(None, description="Explanation of routing decision")
    alternative_domains: List[DomainType] = Field(
        default_factory=list,
        description="Other possible domains if confidence is low"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "selected_domain": "COLLECTIONS",
                "confidence_score": 0.95,
                "is_followup": False,
                "matched_keywords": ["dpd", "0+", "collections"],
                "reasoning": "Question contains DPD keywords specific to collections domain",
                "alternative_domains": []
            }
        }
