import streamlit as st
import requests
import os
import ast
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
import uuid
import json
from dotenv import load_dotenv
from google.cloud import bigquery
import pandas as pd
import io
import plotly.express as px
from utils import *


# Load environment variables from .env file
load_dotenv()

# Ensure project root on path for local imports
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Configure logging for frontend
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Cloud project configuration
PROJECT_ID = os.environ.get("PROJECT_ID", "analytics-datapipeline-prod")

# Get server URL from environment variable, with a default
MCP_SERVER_BASE_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8000")
MCP_SERVER_URL = f"{MCP_SERVER_BASE_URL.rstrip('/')}/mcp/query"



# --- Authentication ---
def authenticate():
    """Handles user authentication via the sidebar."""
    st.sidebar.title("🔐 Login")

    if st.session_state.get("authenticated"):
        st.sidebar.success(f"Welcome, {st.session_state.get('username')}!")
        return True

    creds_str = os.getenv("USER_CREDENTIALS")
    
    username = st.sidebar.text_input("Username", key="auth_user")
    password = st.sidebar.text_input("Password", type="password", key="auth_pw")

    if st.sidebar.button("Login", key="auth_login"):
        user_credentials = [{"username": "admin", "password": "password"}] # Default credentials

        # Try loading from environment variable
        if creds_str:
            try:
                loaded_creds = json.loads(creds_str)
                if isinstance(loaded_creds, list):
                    user_credentials = loaded_creds
                else:
                    st.sidebar.error("USER_CREDENTIALS must be a JSON list of {username, password} objects.")
                    return False
            except json.JSONDecodeError:
                st.sidebar.error("Invalid USER_CREDENTIALS JSON format.")
                return False

        # Check credentials
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


# --- Main Application ---
def main():
    """Main function to run the Streamlit app."""
    st.set_page_config(page_title="Orion Copilot", page_icon="🪐", layout="wide", initial_sidebar_state="auto")

    

    # Inject custom CSS to reduce top padding and show domain badge
    st.markdown(
        """
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

    # --- Initialize session state ---
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

    # --- Sidebar ---
    with st.sidebar:
        st.title("Chat History")
        # Add small controls for cache/session parity with v2
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear Cache"):
                try:
                    st.cache_resource.clear()
                except Exception:
                    try:
                        st.experimental_memo_clear()
                    except Exception:
                        pass
                st.rerun()
        with col2:
            if st.button("New Session"):
                st.session_state.session_id = str(uuid.uuid4())
                st.session_state.history = []
                st.rerun()

        if st.session_state.history:
            # Display history in reverse order (most recent first)
            for i, item in enumerate(reversed(st.session_state.history)):
                # history items may be (q, a) or (q, a, domain)
                if isinstance(item, (list, tuple)) and len(item) == 3:
                    q, a, d = item
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    q, a = item[0], item[1]
                    d = ""
                else:
                    q, a, d = str(item), "", ""

                with st.expander(f"**{len(st.session_state.history) - i}**: {q[:40]}"):
                    st.markdown(f"**You:** {q}")
                    if d:
                        st.markdown(f"**Domain:** {d}")
                    st.markdown(f"**Orion:**\n{a}")
        else:
            st.info("Your chat history will appear here.")

    # --- Main Content ---
    st.title("Orion - The Nostradamus Copilot")

    # User input
    col1, col2 = st.columns([5, 1])
    with col1:
        st.markdown("##### Ask your question : ")
        user_query = st.text_area("**Ask your question:**", value="", height=68, key="user_query",label_visibility  = "collapsed")

    with col2:
        # Add empty lines to push the button down, aligning it with the text area input field
        st.text("")
        st.text("")
        st.text("")
        st.text("")
        submit_clicked = st.button("Submit Query", key="submit_query")
    
    if submit_clicked and user_query:
        # Append the new question to history
        st.session_state.df_for_chart = None
        st.session_state.csv_bytes = None
        st.session_state.last_answer = ""
        st.session_state.last_sql = ""
        st.session_state.history.append((user_query, "⏳ Processing..."))
        
        message_placeholder = st.empty()
        message_placeholder.markdown("⏳ Processing your request...")

        try:
            # Prepare payload with the current question and previous history
            # The backend expects history as a list of [question, answer] tuples
            history_for_backend = st.session_state.history[:-1]

            # --- Call ADK agent backend (direct) ---
            try:
                # Lazy load and run the ADK agent (async wrapper)
                from google.adk.sessions import InMemorySessionService

                async def run_agent_query_async(user_question: str, session_id: str, history: list):
                    # Load agent and settings
                    try:
                        from agent_refactored import root_agent
                        from config.settings import settings
                    except Exception:
                        try:
                            from agent import root_agent
                            from config.settings import settings
                        except Exception as e:
                            raise RuntimeError(f"Failed to load agent: {e}")

                    # Build invocation context lazily to avoid hard dependency at import
                    from google.adk.agents import InvocationContext, RunConfig
                    try:
                        from google.genai.types import Content, Part
                    except Exception:
                        # Fallback if genai types not present
                        Content = None
                        Part = None

                    session_service = InMemorySessionService()
                    session = None
                    if hasattr(session_service, '_sessions') and session_id in session_service._sessions:
                        try:
                            session = session_service._sessions[session_id]
                        except Exception:
                            session_service._sessions.pop(session_id, None)

                    if session is None:
                        session = await session_service.create_session(
                            session_id=session_id,
                            app_name="orion-copilot",
                            user_id=st.session_state.get("username", "anonymous")
                        )

                    # Build context
                    if Content and Part:
                        user_content = Content(parts=[Part(text=user_question)], role="user")
                    else:
                        # Minimal shim if types not available
                        class _C:
                            def __init__(self, text):
                                self.text = text
                        user_content = _C(user_question)

                    ctx = InvocationContext(
                        session_service=session_service,
                        invocation_id=str(uuid.uuid4()),
                        agent=root_agent,
                        session=session,
                        user_content=user_content,
                        run_config=RunConfig()
                    )

                    response_parts = []
                    selected_domain = "Unknown"
                    sql_query = ""

                    async for event in root_agent.run_async(ctx):
                        if hasattr(event, 'content') and event.content:
                            if hasattr(event.content, 'parts'):
                                for part in event.content.parts:
                                    if hasattr(part, 'text') and part.text:
                                        response_parts.append(part.text)

                        if 'routing_response' in ctx.session.state:
                            data = ctx.session.state['routing_response']
                            if isinstance(data, dict):
                                selected_domain = data.get('selected_domain', 'Unknown')
                            else:
                                selected_domain = getattr(data, 'selected_domain', 'Unknown')
                                if hasattr(selected_domain, 'value'):
                                    selected_domain = selected_domain.value

                        if 'sql_generation_response' in ctx.session.state:
                            sql_data = ctx.session.state['sql_generation_response']
                            if isinstance(sql_data, dict):
                                sql_query = sql_data.get('sql_query', '')

                    response_text = "\n".join(response_parts) if response_parts else "No response generated."
                    return {"answer": response_text, "sql_query": sql_query, "domain": selected_domain}

                # Run the async agent runner synchronously
                result = asyncio.run(run_agent_query_async(user_query, st.session_state.session_id, history_for_backend))

                answer = result.get("answer", "No result generated.")
                sql_query = result.get("sql_query", "")
                domain = result.get("domain", "")

                message_placeholder.markdown(answer)
                st.session_state.last_sql = sql_query
                st.session_state.last_answer = answer
                st.session_state.last_domain = domain
                st.session_state.last_user_query = user_query

                if domain:
                    st.session_state.history[-1] = (user_query, answer, domain)
                else:
                    st.session_state.history[-1] = (user_query, answer)

                # Generate CSV and DataFrame for chart
                try:
                    df = markdown_table_to_df(answer)
                    st.session_state.df_for_chart = df
                    csv_buffer = io.StringIO()
                    df.to_csv(csv_buffer, index=False)
                    st.session_state.csv_bytes = csv_buffer.getvalue().encode("utf-8")
                except Exception as e:
                    logger.warning(f"Could not create DataFrame from answer: {e}")

            except Exception as e:
                logger.error(f"Agent call failed: {e}")
                message_placeholder.error(f"Agent error: {e}")
                st.session_state.history[-1] = (user_query, f"Agent error: {e}")

        except requests.exceptions.RequestException as e:
            error_message = f"Failed to connect to MCP server: {e}"
            message_placeholder.error(error_message)
            logger.error(error_message)
            st.session_state.history[-1] = (user_query, error_message)
        except Exception as e:
            error_message = f"An unexpected error occurred: {e}"
            message_placeholder.error(error_message)
            logger.error(error_message, exc_info=True)
            st.session_state.history[-1] = (user_query, error_message)

        # Log interaction (after getting the answer)
        try:
            interaction_id = f"{st.session_state.get('username', 'anonymous')}-{datetime.now(timezone.utc).timestamp()}"
            st.session_state["interaction_id"] = interaction_id
            
            # Get user id from Streamlit session (fallback to anonymous)
            user_id = st.session_state.get("username", "anonymous")
            log_to_bq(user_id, user_query=user_query, answer=st.session_state.last_answer, interaction_id=interaction_id, project_id=PROJECT_ID)
        except Exception as e:
            logger.warning(f"Failed to log to BQ: {e}")
        
        st.rerun()

    # --- Show the answer (persistent) ---
    if st.session_state.last_answer:
        st.markdown("### Orion Answer")
        # Show domain badge if available
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
                output_display.markdown(table_text)  # fallback

        if after:
            st.markdown(after)

        # ✅ View SQL collapsible section
        if st.session_state.last_sql is not None and st.session_state.last_sql.strip():
            with st.expander("View SQL Query", expanded=False):
                if st.session_state.last_sql:
                    st.code(st.session_state.last_sql, language="sql")
                else:
                    st.info("No SQL query available.")

        # ✅ View Chart collapsible section
        if st.session_state.df_for_chart is not None and not st.session_state.df_for_chart.empty:
            with st.expander("View Chart", expanded=False):
                df_for_chart = st.session_state.get("df_for_chart")

                if df_for_chart is not None and not df_for_chart.empty:
                    columns = df_for_chart.columns.tolist()
                    
                    # --- UI for axis selection ---
                    col1, col2 = st.columns(2)
                    selected_x_axis = col1.selectbox("Select X-Axis:", columns, index=0)
                    
                    y_axis_options = [col for col in columns if col != selected_x_axis]
                    
                    # Fallback to all other columns if none are numeric
                    default_y_axes = [col for col in y_axis_options if pd.api.types.is_numeric_dtype(df_for_chart[col])]
                    if not default_y_axes:
                        default_y_axes = y_axis_options

                    selected_y_axes = col2.multiselect("Select Y-Axis (one or more):", y_axis_options, default=default_y_axes)

                    # --- Generate Chart ---
                    if selected_x_axis and selected_y_axes:
                        try:
                            plot_df = df_for_chart.copy()
                            display_y_axes = [format_axis_title(col) for col in selected_y_axes]
                            rename_map = dict(zip(selected_y_axes, display_y_axes))
                            plot_df.rename(columns=rename_map, inplace=True)

                            fig = px.line(
                                plot_df,
                                x=selected_x_axis,
                                y=display_y_axes,
                                markers=True
                            )
                            fig.update_xaxes(title_text=format_axis_title(selected_x_axis))
                            st.plotly_chart(fig, use_container_width=True)

                        except Exception as e:
                            st.error(f"Error while creating chart: {e}")
                    else:
                        st.info("Please select at least one Y-axis column to generate a chart.")
                else:
                    st.info("No data available to generate a chart.")

        # ✅ Download CSV — no rerun wipe
        csv_bytes = st.session_state.get("csv_bytes")

        if csv_bytes:
            with buttons:
                st.write("")
                st.download_button(
                    label="⬇️ Download CSV",
                    data=csv_bytes,
                    file_name="copilot_output.csv",
                    mime="text/csv",
                    key="download_csv_button",
                    help="Download the output table as CSV"
                )

        # ✅ Feedback buttons — state-safe and logs to BQ
        with buttons:
            st.write(" ")
            st.write("Please submit feedback:")
            button1, button2 = st.columns([1, 1])
            if button1.button("👍", key="helpful_button", help="Helpful"):
                try:
                    log_to_bq(
                        user_id = st.session_state.get("username", "anonymous"),
                        user_query=st.session_state.last_user_query,
                        answer=st.session_state.last_answer,
                        user_feedback='positive',
                        interaction_id=st.session_state.get("interaction_id"),
                    )
                    st.toast("Thanks for your feedback!", icon="👍")
                except Exception as e:
                    st.error(f"Failed to submit feedback: {e}")

            if button2.button("👎", key="not_helpful_button", help="Not Helpful"):
                try:
                    log_to_bq(     
                        user_id = st.session_state.get("username", "anonymous"),
                        user_query=st.session_state.last_user_query,
                        answer=st.session_state.last_answer,
                        user_feedback='negative',
                        interaction_id=st.session_state.get("interaction_id"),
                    )
                    st.toast("Feedback noted. Thanks for helping us improve!", icon="👎")
                except Exception as e:
                    st.error(f"Failed to submit feedback: {e}")

# --- Entry Point ---
if __name__ == "__main__":
    main()
    # if authenticate():
    #     main()
    # else:
    #     st.info("Please log in to continue.")
    #     st.stop()