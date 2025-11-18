"""
Streamlit Frontend for ADK V1 Application
Matches structure: root/agent.py
"""
from config.settings import settings
import os
import sys
import asyncio
import logging
import uuid
from pathlib import Path
from datetime import datetime

import streamlit as st

# 1. SETUP PATHS
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. PAGE CONFIG
st.set_page_config(
    page_title="ADK CoPilot", 
    page_icon="🤖", 
    layout="wide"
)

# ---------------------- LAZY LOADING ----------------------

@st.cache_resource
def load_agent_and_settings():
    """
    Loads the Agent and Settings.
    
    CRITICAL: This function bridges Streamlit Secrets -> OS Environment 
    BEFORE importing 'config.settings'. This allows Pydantic Settings 
    to auto-load the configuration correctly in deployment.
    """
    """
    Loads the Agent and Settings.
    Robustly handles missing secrets by falling back to Environment Variables.
    """
    try:
        # 1. Load .env file (Prioritize local .env if present)
        try:
            from dotenv import load_dotenv
            load_dotenv() 
        except ImportError:
            pass

        # 2. Bridge Streamlit Secrets (Use try-except to prevent crash)
        try:
            keys_to_bridge = [
                "GOOGLE_API_KEY", 
                "GEMINI_FLASH_MODEL", 
                "GCP_PROJECT_ID", 
                "BIGQUERY_DATASET"
            ]
            
            for key in keys_to_bridge:
                if key in st.secrets:
                    os.environ[key] = st.secrets[key]
                    
        except Exception:
            # This is expected in Cloud Shell if no secrets.toml exists
            logger.info("No Streamlit secrets found. Using standard Environment Variables.")

        # 3. NOW Import Settings and Agent
        from config.settings import settings
        from agent import root_agent
        
        # 4. Validation
        if not settings.google_api_key:
             logger.error("CRITICAL: GOOGLE_API_KEY not found in Settings.")
             st.error("API Key configuration missing. Please set GOOGLE_API_KEY.")
             return None, None

        return root_agent, settings

    except Exception as e:
        logger.error(f"Error loading resources: {e}", exc_info=True)
        return None, None

@st.cache_resource
def get_session_service():
    from google.adk.sessions import InMemorySessionService
    return InMemorySessionService()

def clear_session_by_id(session_id: str):
    """Clear a specific session from the service"""
    session_service = get_session_service()
    if hasattr(session_service, '_sessions') and session_id in session_service._sessions:
        del session_service._sessions[session_id]
        logger.info(f"Cleared session: {session_id}")
        return True
    return False

# ---------------------- CORE LOGIC ----------------------

async def run_agent_query_async(user_question: str, session_id: str):
    """
    Async worker that interacts with the ADK Agent
    """
    root_agent, settings = load_agent_and_settings()
    session_service = get_session_service()
    
    if not root_agent:
        raise ValueError("Agent failed to initialize. Please check if 'agent.py' exists.")

    # Import ADK types locally
    from google.adk.agents import InvocationContext, RunConfig
    from google.adk.sessions import Session
    from google.genai.types import Content, Part

    # 1. Get/Create Session
    # Check if session exists in the service's internal storage
    try:
        # InMemorySessionService stores sessions internally
        # Try to create first, catch AlreadyExistsError if it exists
        session = await session_service.create_session(
            session_id=session_id,
            app_name="streamlit-copilot",
            user_id="streamlit-user"
        )
        logger.info(f"Created new session: {session_id}")
    except Exception as e:
        # If session already exists, retrieve it from the internal storage
        if "already exists" in str(e).lower():
            # Access internal session storage (InMemorySessionService stores in _sessions dict)
            if hasattr(session_service, '_sessions') and session_id in session_service._sessions:
                session = session_service._sessions[session_id]
                logger.info(f"Resuming existing session: {session_id}")
            else:
                # If can't retrieve, create with new ID
                logger.warning(f"Session {session_id} exists but couldn't retrieve. Creating new session.")
                session_id = str(uuid.uuid4())
                session = await session_service.create_session(
                    session_id=session_id,
                    app_name="streamlit-copilot",
                    user_id="streamlit-user"
                )
                logger.info(f"Created fallback session: {session_id}")
        else:
            raise

    # 2. Build Context
    user_content = Content(parts=[Part(text=user_question)], role="user")
    
    # The ADK uses 'user_content' to determine the input.
    ctx = InvocationContext(
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=root_agent,
        session=session,
        user_content=user_content,
        run_config=RunConfig()
    )

    # 3. Run Agent
    response_parts = []
    selected_domain = "Unknown"

    async for event in root_agent.run_async(ctx):
        # Extract Text
        if hasattr(event, 'content') and event.content:
            if hasattr(event.content, 'parts'):
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_parts.append(part.text)
        
        # Extract Domain State
        if 'routing_response' in ctx.session.state:
            data = ctx.session.state['routing_response']
            if isinstance(data, dict):
                selected_domain = data.get('selected_domain', 'Unknown')
            else:
                selected_domain = getattr(data, 'selected_domain', 'Unknown')
                if hasattr(selected_domain, 'value'):
                     selected_domain = selected_domain.value

    response_text = "\n".join(response_parts) if response_parts else "No response generated."
    return response_text, selected_domain

def run_query_sync_wrapper(user_question: str, session_id: str):
    try:
        return asyncio.run(run_agent_query_async(user_question, session_id))
    except Exception as e:
        logger.error(f"Execution Error: {e}", exc_info=True)
        return f"❌ System Error: {str(e)}", None

# ---------------------- UI LAYOUT ----------------------

def main():
    st.markdown("""
        <style>
            .domain-badge { padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8em; color: white; }
            .sourcing { background-color: #1f77b4; }
            .collections { background-color: #ff7f0e; }
            .disbursal { background-color: #2ca02c; }
            .unknown { background-color: #7f7f7f; }
        </style>
    """, unsafe_allow_html=True)

    with st.sidebar:
        st.title("Configuration")
        root_agent, settings = load_agent_and_settings()
        
        if settings:
            model_name = getattr(settings, 'gemini_flash_model', 'Unknown')
            st.success(f"Model: `{model_name}`")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Clear Cache"):
                    st.cache_resource.clear()
                    st.rerun()
            with col2:
                if st.button("New Session"):
                    # Clear old session from service
                    old_session_id = st.session_state.get("session_id")
                    if old_session_id:
                        clear_session_by_id(old_session_id)
                    # Create new session ID
                    st.session_state.session_id = str(uuid.uuid4())
                    st.session_state.history = []
                    st.rerun()
        else:
            st.error("Agent failed to load.")
            st.stop()
        
        st.divider()
        st.subheader("History")
        
        if "history" in st.session_state and st.session_state.history:
            for i, chat in enumerate(reversed(st.session_state.history)):
                if i > 5: break
                q_short = (chat["question"][:30] + '..') if len(chat["question"]) > 30 else chat["question"]
                st.caption(f"**Q:** {q_short}")
        else:
            st.caption("No history yet.")

    st.title("💬 Enterprise Data CoPilot")
    
    if "history" not in st.session_state:
        st.session_state.history = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    for chat in st.session_state.history:
        with st.chat_message("user"):
            st.markdown(chat["question"])
        with st.chat_message("assistant"):
            domain = chat.get("domain", "Unknown")
            domain_str = domain.value if hasattr(domain, 'value') else str(domain)
            cls = domain_str.lower() if domain_str else "unknown"
            
            st.markdown(f'<span class="domain-badge {cls}">{domain_str}</span>', unsafe_allow_html=True)
            st.markdown(chat["answer"])

    if prompt := st.chat_input("Ask about Sourcing, Collections, or Disbursals..."):
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response_text, domain = run_query_sync_wrapper(prompt, st.session_state.session_id)
                
                domain_str = domain.value if hasattr(domain, 'value') else str(domain)
                domain_cls = domain_str.lower()
                
                st.markdown(f'<span class="domain-badge {domain_cls}">{domain_str}</span>', unsafe_allow_html=True)
                st.markdown(response_text)
                
                st.session_state.history.append({
                    "question": prompt,
                    "answer": response_text,
                    "domain": domain_str
                })

if __name__ == "__main__":
    main()