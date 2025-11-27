"""
Streamlit Frontend V2 - Orion Copilot with ADK Agentic Framework
Maintains original UI design with refactored agent backend
"""
import streamlit as st
import re
import os
import sys
import asyncio
from utils.json_logger import get_json_logger
from config.settings import settings
import uuid
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd
import io
import plotly.express as px
from dotenv import load_dotenv
from google.cloud import bigquery

# Load environment variables
load_dotenv()

# Setup paths
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Configure logging
logger = get_json_logger(__name__)

# Google Cloud configuration
PROJECT_ID = os.getenv("GCP_PROJECT_ID", settings.gcp_project_id or "analytics-datapipeline-prod")

# Page config
st.set_page_config(
    page_title="Orion Copilot", 
    page_icon="🪐", 
    layout="wide", 
    initial_sidebar_state="auto"
)

# Inject custom CSS
st.markdown("""
    <style>
        .block-container {
            padding-top: 1rem;
        }
        .domain-badge { 
            padding: 2px 8px; 
            border-radius: 4px; 
            font-weight: bold; 
            font-size: 0.8em; 
            color: white; 
        }
        .sourcing { background-color: #1f77b4; }
        .collections { background-color: #ff7f0e; }
        .disbursal { background-color: #2ca02c; }
        .unknown { background-color: #7f7f7f; }
    </style>
""", unsafe_allow_html=True)


# ---------------------- UTILITIES ----------------------

def markdown_table_to_df(markdown_table: str) -> pd.DataFrame:
    """Convert markdown table to DataFrame"""
    lines = [line for line in markdown_table.strip().split('\n') if line.strip()]
    if len(lines) < 2:
        raise ValueError("Not enough lines for a markdown table")

    raw_headers = lines[0].split('|')
    headers = [h.strip() for h in raw_headers[1:-1]] if len(raw_headers) > 2 else [h.strip() for h in raw_headers]

    rows = []
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.split('|')[1:-1]] if '|' in line else [cell.strip() for cell in line.split(',')]
        # Normalize row length to header length
        if len(cells) < len(headers):
            # pad with empty strings
            cells += [''] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            # truncate extras
            cells = cells[:len(headers)]
        rows.append(cells)

    try:
        return pd.DataFrame(rows, columns=headers)
    except Exception as e:
        # As a last resort, return a DataFrame without column names
        logger.warning(f"Could not construct DataFrame with headers: {e}")
        return pd.DataFrame(rows)


def extract_markdown_table(text: str) -> tuple:
    """Extract markdown table from text"""
    lines = text.split('\n')
    table_start = None
    table_end = None
    
    for i, line in enumerate(lines):
        if '|' in line and table_start is None:
            table_start = i
        elif table_start is not None and '|' not in line:
            table_end = i
            break
    
    if table_start is not None:
        if table_end is None:
            table_end = len(lines)
        before = '\n'.join(lines[:table_start])
        table = '\n'.join(lines[table_start:table_end])
        after = '\n'.join(lines[table_end:])
        return before, table, after
    
    return text, "", ""


def format_axis_title(col_name: str) -> str:
    """Format column name for chart axis"""
    return col_name.replace('_', ' ').title()


def extract_sql_from_text(text: str) -> str:
    """Extract SQL code block from text. Returns SQL string or empty string."""
    if not text:
        return ""
    # Prefer code-fenced SQL blocks - handle variants
    match = re.search(r'```(?:sql|googlesql|bigquery)\s+(.*?)\s+```', text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Generic code fence
    match = re.search(r'```[a-zA-Z0-9_-]*\s+(.*?)\s+```', text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Fallback: try to find first SELECT/WITH ... block
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r'^\s*(SELECT|WITH)\b', line, re.IGNORECASE):
            start = i
            break
    if start is not None:
        # collect until end or blank line
        collected = []
        for line in lines[start:]:
            collected.append(line)
        return '\n'.join(collected).strip()
    return ""


def log_to_bq(user_id: str, user_query: str, answer: str, interaction_id: str, 
              user_feedback: str = None, domain: str = None, table_name: str = None, project_id: str = PROJECT_ID):
    """Log interaction to BigQuery"""
    try:
        from google.auth import default
        from config.settings import settings
        credentials, _ = default()
        client = bigquery.Client(credentials=credentials, project=project_id)
        
        # Prefer configured logging dataset/table from environment or settings
        dataset_id = settings.logging_dataset
        table_id = settings.logging_table   
        # Allow passing domain/table for clarity
        domain_val = domain or 'UNKNOWN'
        table_name_val = table_name or ''
        table_ref = f"{project_id}.{dataset_id}.{table_id}"
        
        row = {
            "interaction_id": interaction_id,
            "user_id": user_id,
            "domain": domain_val,
            "source_table": table_name_val,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "generated_sql": extract_sql_from_text(answer),
            "user_query": user_query,
            "answer": answer,
            "user_feedback": user_feedback
        }
        
        # Ensure dataset exists
        try:
            dataset = client.get_dataset(dataset_id)
        except Exception:
            logger.info(f"Dataset {project_id}.{dataset_id} not found - creating it")
            dataset = bigquery.Dataset(f"{project_id}.{dataset_id}")
            dataset = client.create_dataset(dataset, exists_ok=True)

        # Ensure table exists (create simple schema based on row keys)
        try:
            client.get_table(table_ref)
        except Exception:
            logger.info(f"Table {table_ref} not found - creating with inferred schema")
            schema = [
                bigquery.SchemaField("interaction_id", "STRING"),
                bigquery.SchemaField("user_id", "STRING"),
                bigquery.SchemaField("domain", "STRING"),
                bigquery.SchemaField("source_table", "STRING"),
                bigquery.SchemaField("timestamp", "STRING"),
                bigquery.SchemaField("generated_sql", "STRING"),
                bigquery.SchemaField("user_query", "STRING"),
                bigquery.SchemaField("answer", "STRING"),
                bigquery.SchemaField("user_feedback", "STRING"),
            ]
            table = bigquery.Table(table_ref, schema=schema)
            client.create_table(table, exists_ok=True)

        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            logger.warning(f"BigQuery logging errors: {errors}")
        else:
            logger.info(f"Logged interaction: {interaction_id}")
    except Exception as e:
        logger.warning(f"Failed to log to BigQuery: {e}", exc_info=True)


# ---------------------- LAZY LOADING ----------------------

@st.cache_resource
def load_agent_and_settings():
    """Load agent and settings (cached)"""
    try:
        from agent_refactored import root_agent
        from config.settings import settings
        logger.info("✅ Agent loaded successfully")
        return root_agent, settings
    except ImportError:
        try:
            from agent import root_agent
            from config.settings import settings
            logger.warning("Using legacy agent (agent.py)")
            return root_agent, settings
        except Exception as e:
            logger.error(f"Failed to load agent: {e}")
            return None, None


@st.cache_resource
def get_session_service():
    """Get or create session service"""
    from google.adk.sessions import InMemorySessionService
    return InMemorySessionService()


def clear_session_by_id(session_id: str):
    """Clear a specific session"""
    session_service = get_session_service()
    if hasattr(session_service, '_sessions') and session_id in session_service._sessions:
        del session_service._sessions[session_id]
        logger.info(f"Cleared session: {session_id}")


# ---------------------- CORE LOGIC ----------------------

async def run_agent_query_async(user_question: str, session_id: str, history: list):
    """Run agent query asynchronously"""
    root_agent, settings = load_agent_and_settings()
    session_service = get_session_service()
    
    if not root_agent:
        raise ValueError("Agent failed to initialize")

    from google.adk.agents import InvocationContext, RunConfig
    from google.genai.types import Content, Part

    # Get or create session with robust error handling
    session = None
    if hasattr(session_service, '_sessions') and session_id in session_service._sessions:
        try:
            session = session_service._sessions[session_id]
            logger.info(f"Reusing session: {session_id}")
        except Exception:
            if hasattr(session_service, '_sessions'):
                session_service._sessions.pop(session_id, None)
    
    if session is None:
        try:
            session = await session_service.create_session(
                session_id=session_id,
                app_name="orion-copilot",
                user_id=st.session_state.get("username", "anonymous")
            )
            logger.info(f"Created session: {session_id}")
        except Exception as e:
            if "already exists" in str(e).lower():
                if hasattr(session_service, '_sessions'):
                    session_service._sessions.pop(session_id, None)
                session_id = str(uuid.uuid4())
                session = await session_service.create_session(
                    session_id=session_id,
                    app_name="orion-copilot",
                    user_id=st.session_state.get("username", "anonymous")
                )
            else:
                raise

    # Build context
    user_content = Content(parts=[Part(text=user_question)], role="user")
    ctx = InvocationContext(
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=root_agent,
        session=session,
        user_content=user_content,
        run_config=RunConfig()
    )

    # Run agent
    response_parts = []
    selected_domain = "Unknown"
    sql_query = ""

    async for event in root_agent.run_async(ctx):
        if hasattr(event, 'content') and event.content:
            if hasattr(event.content, 'parts'):
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_parts.append(part.text)
        
        # Extract domain
        if 'routing_response' in ctx.session.state:
            data = ctx.session.state['routing_response']
            # Support multiple shapes: dict from model_dump, pydantic model, enum, or nested dict
            try:
                sel = None
                if isinstance(data, dict):
                    sel = data.get('selected_domain') or data.get('selected_domain', None)
                else:
                    sel = getattr(data, 'selected_domain', None)

                # Enum -> value
                try:
                    from enum import Enum as _Enum
                    if isinstance(sel, _Enum):
                        sel = sel.value
                except Exception:
                    pass

                # Nested dict (edge cases)
                if isinstance(sel, dict):
                    sel = sel.get('value') or sel.get('name') or sel.get('selected_domain')

                if isinstance(sel, str):
                    selected_domain = sel
                elif hasattr(sel, 'value'):
                    selected_domain = getattr(sel, 'value')
                elif sel is not None:
                    selected_domain = str(sel)
            except Exception:
                # Keep earlier value if parsing fails
                pass
        
        # Extract SQL
        if 'sql_generation_response' in ctx.session.state:
            sql_data = ctx.session.state['sql_generation_response']
            try:
                if isinstance(sql_data, dict):
                    sql_query = sql_data.get('sql_query', '') or sql_data.get('sql', '') or sql_data.get('generated_sql', '')
                else:
                    sql_query = getattr(sql_data, 'sql_query', '') or getattr(sql_data, 'generated_sql', '') or ''
            except Exception:
                sql_query = ''

    response_text = "\n".join(response_parts) if response_parts else "No response generated."
    # Final fallback: if domain still unknown, try conversation history
    if (not selected_domain or selected_domain == "Unknown") and hasattr(ctx, 'session'):
        try:
            hist = ctx.session.state.get('conversation_history', [])
            if hist:
                last = hist[-1]
                if isinstance(last, dict):
                    selected_domain = last.get('domain', selected_domain)
        except Exception:
            pass
    return {
        "answer": response_text,
        "sql_query": sql_query,
        "domain": selected_domain
    }


def run_query_sync_wrapper(user_question: str, session_id: str, history: list):
    """Synchronous wrapper for async agent query"""
    try:
        return asyncio.run(run_agent_query_async(user_question, session_id, history))
    except Exception as e:
        logger.error(f"Query execution error: {e}", exc_info=True)
        return {
            "answer": f"Error: {str(e)}",
            "sql_query": "",
            "domain": "Unknown"
        }


# ---------------------- AUTHENTICATION ----------------------

def authenticate():
    """Handle user authentication"""
    st.sidebar.title("Login")

    if st.session_state.get("authenticated"):
        st.sidebar.success(f"Welcome, {st.session_state.get('username')}!")
        return True

    creds_str = os.getenv("USER_CREDENTIALS")
    username = st.sidebar.text_input("Username", key="auth_user")
    password = st.sidebar.text_input("Password", type="password", key="auth_pw")

    if st.sidebar.button("Login", key="auth_login"):
        user_credentials = [{"username": "admin", "password": "password"}]

        if creds_str:
            try:
                loaded_creds = json.loads(creds_str)
                if isinstance(loaded_creds, list):
                    user_credentials = loaded_creds
            except json.JSONDecodeError:
                st.sidebar.error("Invalid USER_CREDENTIALS JSON format.")
                return False

        for cred in user_credentials:
            if cred.get("username") == username and cred.get("password") == password:
                st.session_state["authenticated"] = True
                st.session_state["username"] = username
                st.sidebar.success(f"Welcome, {username}!")
                st.rerun()
                return True

        st.sidebar.error("Invalid username or password.")
        return False

    return False


# ---------------------- MAIN APP ----------------------

def main():
    """Main application"""
    
    # Initialize session state
    if "history" not in st.session_state:
        st.session_state.history = []
    if "last_answer" not in st.session_state:
        st.session_state.last_answer = ""
    if "last_sql" not in st.session_state:
        st.session_state.last_sql = ""
    if "last_domain" not in st.session_state:
        st.session_state.last_domain = ""
    if "df_for_chart" not in st.session_state:
        st.session_state.df_for_chart = None
    if "csv_bytes" not in st.session_state:
        st.session_state.csv_bytes = None
    if "last_user_query" not in st.session_state:
        st.session_state.last_user_query = ""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    # Sidebar
    with st.sidebar:
        st.title("Chat History")
        
        # Agent info
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
                    old_session_id = st.session_state.get("session_id")
                    if old_session_id:
                        clear_session_by_id(old_session_id)
                    st.session_state.session_id = str(uuid.uuid4())
                    st.session_state.history = []
                    st.rerun()
        
        st.divider()
        
        # Display history
        if st.session_state.history:
            for i, (q, a, _) in enumerate(reversed(st.session_state.history)):
                with st.expander(f"**{len(st.session_state.history) - i}**: {q[:40]}"):
                    st.markdown(f"**You:** {q}")
                    st.markdown(f"**Orion:**\n{a}")
        else:
            st.info("Your chat history will appear here.")

    # Main content
    st.title("Orion - The Nostradamus Copilot")

    # User input
    col1, col2 = st.columns([5, 1])
    with col1:
        st.markdown("##### Ask your question : ")
        user_query = st.text_area(
            "**Ask your question:**", 
            value="", 
            height=68, 
            key="user_query",
            label_visibility="collapsed"
        )

    with col2:
        st.text("")
        st.text("")
        st.text("")
        st.text("")
        submit_clicked = st.button("Submit Query", key="submit_query")
    
    if submit_clicked and user_query:
        # Reset state
        st.session_state.df_for_chart = None
        st.session_state.csv_bytes = None
        st.session_state.last_answer = ""
        st.session_state.last_sql = ""
        st.session_state.last_domain = ""
        
        # Add to history
        st.session_state.history.append((user_query, "⏳ Processing...", ""))
        
        message_placeholder = st.empty()
        message_placeholder.markdown("⏳ Processing your request...")

        try:
            # Call agent backend
            history_for_backend = st.session_state.history[:-1]
            result = run_query_sync_wrapper(
                user_query, 
                st.session_state.session_id,
                history_for_backend
            )
            
            answer = result.get("answer", "No response generated.")
            sql_query = result.get("sql_query", "")
            domain = result.get("domain", "Unknown")
            
            # Update state
            st.session_state.last_answer = answer
            st.session_state.last_sql = sql_query
            st.session_state.last_domain = domain
            st.session_state.last_user_query = user_query
            st.session_state.history[-1] = (user_query, answer, domain)
            
            message_placeholder.markdown(answer)
            
            # Generate CSV and DataFrame
            try:
                df = markdown_table_to_df(answer)
                # Augment results with domain and source_table for traceability
                domain_val = st.session_state.get('last_domain', '')
                src_table = ''
                if domain_val and domain_val.upper() == 'COLLECTIONS':
                    src_table = settings.collections_table
                elif domain_val and domain_val.upper() == 'SOURCING':
                    src_table = settings.sourcing_table
                elif domain_val and domain_val.upper() == 'DISBURSAL':
                    src_table = settings.disbursal_table

                try:
                    df['domain'] = domain_val
                    df['source_table'] = src_table
                    df['generated_sql'] = extract_sql_from_text(answer)
                except Exception:
                    # If df is not mutable in-place, create a copy with added columns
                    df = df.copy()
                    df['domain'] = domain_val
                    df['source_table'] = src_table
                    df['generated_sql'] = extract_sql_from_text(answer)

                st.session_state.df_for_chart = df
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False)
                st.session_state.csv_bytes = csv_buffer.getvalue().encode("utf-8")
            except Exception as e:
                logger.warning(f"Could not create DataFrame: {e}")

        except Exception as e:
            error_message = f"Error: {str(e)}"
            message_placeholder.error(error_message)
            logger.error(error_message, exc_info=True)
            st.session_state.history[-1] = (user_query, error_message, "Error")

        # Log interaction
        try:
            interaction_id = f"{st.session_state.get('username', 'anonymous')}-{datetime.now(timezone.utc).timestamp()}"
            st.session_state["interaction_id"] = interaction_id
            user_id = st.session_state.get("username", "anonymous")
            # Determine source table based on selected domain
            src_table = ''
            domain_val = st.session_state.get('last_domain', '')
            if domain_val and domain_val.upper() == 'COLLECTIONS':
                src_table = settings.collections_table
            elif domain_val and domain_val.upper() == 'SOURCING':
                src_table = settings.sourcing_table
            elif domain_val and domain_val.upper() == 'DISBURSAL':
                src_table = settings.disbursal_table

            log_to_bq(user_id, user_query, st.session_state.last_answer, interaction_id, project_id=PROJECT_ID, domain=domain_val, table_name=src_table)
        except Exception as e:
            logger.warning(f"Failed to log to BQ: {e}")
        
        st.rerun()

    # Show the answer (persistent)
    if st.session_state.last_answer:
        st.markdown("### Orion Answer")
        
        # Show domain badge
        if st.session_state.last_domain:
            domain_str = st.session_state.last_domain
            domain_class = domain_str.lower() if domain_str else "unknown"
            st.markdown(
                f'<span class="domain-badge {domain_class}">{domain_str}</span>', 
                unsafe_allow_html=True
            )
        
        answer_text = st.session_state.last_answer
        before, table_text, after = extract_markdown_table(answer_text)

        output_display, buttons = st.columns([5, 1])

        if before:
            output_display.markdown(before)
        if table_text:
            try:
                df = markdown_table_to_df(table_text)
                output_display.dataframe(df, use_container_width=True, hide_index=True)
            except Exception:
                output_display.markdown(table_text)

        if after:
            st.markdown(after)

        # View SQL
        if st.session_state.last_sql and st.session_state.last_sql.strip():
            with st.expander("View SQL Query", expanded=False):
                # Show domain and source table for context
                domain_display = st.session_state.get('last_domain', '')
                src_table_display = ''
                if domain_display and domain_display.upper() == 'COLLECTIONS':
                    src_table_display = settings.collections_table
                elif domain_display and domain_display.upper() == 'SOURCING':
                    src_table_display = settings.sourcing_table
                elif domain_display and domain_display.upper() == 'DISBURSAL':
                    src_table_display = settings.disbursal_table

                if domain_display or src_table_display:
                    st.markdown(f"**Domain:** {domain_display}  \\ **Source table:** {src_table_display}")
                st.code(st.session_state.last_sql, language="sql")

        # View Chart
        if st.session_state.df_for_chart is not None and not st.session_state.df_for_chart.empty:
            with st.expander("View Chart", expanded=False):
                df_for_chart = st.session_state.df_for_chart
                columns = df_for_chart.columns.tolist()
                
                col1, col2 = st.columns(2)
                selected_x_axis = col1.selectbox("Select X-Axis:", columns, index=0)
                
                y_axis_options = [col for col in columns if col != selected_x_axis]
                default_y_axes = [col for col in y_axis_options if pd.api.types.is_numeric_dtype(df_for_chart[col])]
                if not default_y_axes:
                    default_y_axes = y_axis_options

                selected_y_axes = col2.multiselect("Select Y-Axis (one or more):", y_axis_options, default=default_y_axes)

                if selected_x_axis and selected_y_axes:
                    try:
                        plot_df = df_for_chart.copy()
                        display_y_axes = [format_axis_title(col) for col in selected_y_axes]
                        rename_map = dict(zip(selected_y_axes, display_y_axes))
                        plot_df.rename(columns=rename_map, inplace=True)

                        fig = px.line(plot_df, x=selected_x_axis, y=display_y_axes, markers=True)
                        fig.update_xaxes(title_text=format_axis_title(selected_x_axis))
                        st.plotly_chart(fig, use_container_width=True)
                    except Exception as e:
                        st.error(f"Error creating chart: {e}")
                else:
                    st.info("Please select at least one Y-axis column.")


        # Feedback buttons
        with buttons:
            st.write(" ")
            st.write("Please submit feedback:")
            button1, button2 = st.columns([1, 1])
            if button1.button("👍", key="helpful_button", help="Helpful"):
                try:
                    log_to_bq(
                        user_id=st.session_state.get("username", "anonymous"),
                        user_query=st.session_state.last_user_query,
                        answer=st.session_state.last_answer,
                        user_feedback='positive',
                        interaction_id=st.session_state.get("interaction_id"),
                        domain=st.session_state.get('last_domain', ''),
                        table_name=(settings.collections_table if st.session_state.get('last_domain','').upper()=='COLLECTIONS' else (
                            settings.sourcing_table if st.session_state.get('last_domain','').upper()=='SOURCING' else (
                                settings.disbursal_table if st.session_state.get('last_domain','').upper()=='DISBURSAL' else ''))),
                    )
                    st.toast("Thanks for your feedback!", icon="👍")
                except Exception as e:
                    st.error(f"Failed to submit feedback: {e}")

            if button2.button("👎", key="not_helpful_button", help="Not Helpful"):
                try:
                    log_to_bq(     
                        user_id=st.session_state.get("username", "anonymous"),
                        user_query=st.session_state.last_user_query,
                        answer=st.session_state.last_answer,
                        user_feedback='negative',
                        interaction_id=st.session_state.get("interaction_id"),
                        domain=st.session_state.get('last_domain', ''),
                        table_name=(settings.collections_table if st.session_state.get('last_domain','').upper()=='COLLECTIONS' else (
                            settings.sourcing_table if st.session_state.get('last_domain','').upper()=='SOURCING' else (
                                settings.disbursal_table if st.session_state.get('last_domain','').upper()=='DISBURSAL' else ''))),
                    )
                    st.toast("Feedback noted. Thanks for helping us improve!", icon="👎")
                except Exception as e:
                    st.error(f"Failed to submit feedback: {e}")


# Entry point
if __name__ == "__main__":
    #main()
    #Uncomment for authentication
    if authenticate():
        main()
    else:
        st.info("Please log in to continue.")
        st.stop()
