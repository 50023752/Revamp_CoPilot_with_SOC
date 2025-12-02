# ============================================
# Orion Copilot - Secure Production Dockerfile
# ============================================
# Security Features:
# - Non-root user execution
# - Minimal attack surface (slim base image)
# - No build tools in final image
# - Explicit dependency pinning via requirements.txt
# ============================================

# Use Python 3.12.3 slim image (minimal dependencies)
FROM python:3.12.3-slim

# Set working directory
WORKDIR /app

# Install system dependencies (build-time only)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    software-properties-common \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application
COPY . .

# ============================================
# SECURITY: Create non-root user
# ============================================
# Running as root is a security risk. Create a dedicated user.
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Change ownership of app files to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user for execution
USER appuser

# Expose port 8080 (Cloud Run default)
EXPOSE 8080

# Set environment variables for Cloud Run
ENV PORT=8080
ENV STREAMLIT_SERVER_PORT=8080
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Health check (runs as appuser)
HEALTHCHECK CMD curl --fail http://localhost:8080/_stcore/health || exit 1

# Run the Streamlit app as non-root user
CMD streamlit run streamlit_app_v2.py \
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
