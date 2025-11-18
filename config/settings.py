"""
Configuration Management for Revamp CoPilot ADK
Loads and validates environment variables
"""

import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # GCP Configuration
    gcp_project_id: str = Field(..., validation_alias='GCP_PROJECT_ID')
    gcp_region: str = Field(default='asia-south1', validation_alias='GCP_REGION')
    
    # BigQuery Configuration
    bigquery_dataset: str = Field(..., validation_alias='BIGQUERY_DATASET')
    bigquery_location: str = Field(default='asia-south1', validation_alias='BIGQUERY_LOCATION')
    
    # Table Names
    sourcing_table: str = Field(default='NA', validation_alias='SOURCING_TABLE')
    collections_table: str = Field(default='TW_NOSTD_MART_HIST', validation_alias='COLLECTIONS_TABLE')
    disbursal_table: str = Field(default='TW_NOSTD_MART_REALTIME_UPDATED', validation_alias='DISBURSAL_TABLE')
    
    # Vertex AI Configuration
    vertex_ai_location: str = Field(default='asia-south1', validation_alias='VERTEX_AI_LOCATION')
    gemini_pro_model: str = Field(default='gemini-2.0-pro', validation_alias='GEMINI_PRO_MODEL')
    gemini_flash_model: str = Field(default='gemini-2.0-flash', validation_alias='GEMINI_FLASH_MODEL')
    
    # LLM Parameters
    llm_temperature: float = Field(default=0.1, validation_alias='LLM_TEMPERATURE')
    llm_top_p: float = Field(default=0.95, validation_alias='LLM_TOP_P')
    llm_top_k: int = Field(default=40, validation_alias='LLM_TOP_K')
    llm_max_output_tokens: int = Field(default=8192, validation_alias='LLM_MAX_OUTPUT_TOKENS')
    
    # ADK Configuration
    adk_session_backend: str = Field(default='in-memory', validation_alias='ADK_SESSION_BACKEND')
    adk_log_level: str = Field(default='INFO', validation_alias='ADK_LOG_LEVEL')
    adk_session_ttl: int = Field(default=3600, validation_alias='ADK_SESSION_TTL')
    
    # Application Settings
    app_name: str = Field(default='copilot-mvp', validation_alias='APP_NAME')
    app_port: int = Field(default=8080, validation_alias='APP_PORT')
    app_debug: bool = Field(default=False, validation_alias='APP_DEBUG')
    
    # Query Settings
    max_query_rows: int = Field(default=1000, validation_alias='MAX_QUERY_ROWS')
    query_timeout_seconds: int = Field(default=30, validation_alias='QUERY_TIMEOUT_SECONDS')
    
    # Authentication
    google_application_credentials: Optional[str] = Field(
        default=None, 
        validation_alias='GOOGLE_APPLICATION_CREDENTIALS'
    )
    google_api_key: Optional[str] = Field(
        default=None,
        validation_alias='GOOGLE_API_KEY'
    )
    
    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'
        case_sensitive = False


def load_settings() -> Settings:
    """Load and return application settings"""
    return Settings()


# Global settings instance
settings = load_settings()
