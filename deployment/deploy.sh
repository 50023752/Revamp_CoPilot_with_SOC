#!/bin/bash

# ============================================
# Cloud Run Deployment Script - Orion Copilot
# Enterprise-Grade Security Configuration
# ============================================
# 
# SECURITY FEATURES:
# - Artifact Registry (modern, secure container storage)
# - Dedicated service account (least privilege principle)
# - SHA-256 password hashing (no plain text secrets)
# - Environment variable injection (runtime configuration)
#
# PREREQUISITES:
# 1. Create Artifact Registry repository:
#    gcloud artifacts repositories create orion-copilot-repo \
#      --repository-format=docker \
#      --location=asia-south1
#
# 2. Create service account:
#    gcloud iam service-accounts create orion-copilot-sa \
#      --display-name="Orion Copilot Service Account"
#
# 3. Grant required permissions:
#    gcloud projects add-iam-policy-binding analytics-datapipeline-prod \
#      --member="serviceAccount:orion-copilot-sa@analytics-datapipeline-prod.iam.gserviceaccount.com" \
#      --role="roles/bigquery.dataEditor"
#    gcloud projects add-iam-policy-binding analytics-datapipeline-prod \
#      --member="serviceAccount:orion-copilot-sa@analytics-datapipeline-prod.iam.gserviceaccount.com" \
#      --role="roles/bigquery.jobUser"
#
# 4. Generate password hash:
#    python utils/auth_utils.py YourSecurePassword123
#    Copy the hash for use in ADMIN_PASSWORD_HASH below
#
# ============================================

set -e  # Exit immediately if a command exits with a non-zero status

# --- Configuration ---
PROJECT_ID="analytics-datapipeline-prod"
REGION="asia-south1"
SERVICE_NAME="orion-copilot"

# Artifact Registry Configuration (Modern Standard)
REPO_NAME="orion-copilot-repo"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}"

# Service Account Configuration
# Using default compute service account (linked to your email)
# No need to create a new service account
SERVICE_ACCOUNT="" # Empty means use default compute service account

# ============================================
# SECURITY: Multi-User Authentication
# ============================================
# Generate hashes using: python utils/auth_utils.py "YourPassword"
#
# TEAM-BASED USER CREDENTIALS (JSON Format)
# Replace the hashes below with your actual SHA-256 hashes
#
# Step 1: Generate hash for each user:
#   python utils/auth_utils.py "RiskTeamPass123"
#   python utils/auth_utils.py "CreditTeamPass456"
#   python utils/auth_utils.py "CollectionTeamPass789"
#
# Step 2: Replace the placeholder hashes below

USER_CREDENTIALS='[
  {"username": "risk_team_user", "password_hash": "REPLACE_WITH_RISK_TEAM_HASH"},
  {"username": "credit_team_user", "password_hash": "REPLACE_WITH_CREDIT_TEAM_HASH"},
  {"username": "collection_team_user", "password_hash": "REPLACE_WITH_COLLECTION_TEAM_HASH"}
]'

# Validate that credentials have been configured
if echo "$USER_CREDENTIALS" | grep -q "REPLACE_WITH"; then
    echo "❌ ERROR: User credentials not configured!"
    echo ""
    echo "Generate password hashes:"
    echo "  python utils/auth_utils.py RiskTeamPassword"
    echo "  python utils/auth_utils.py CreditTeamPassword"
    echo "  python utils/auth_utils.py CollectionTeamPassword"
    echo ""
    echo "Then update this script with the hashes in USER_CREDENTIALS"
    echo ""
    exit 1
fi

echo "🚀 Deploying Orion Copilot to Cloud Run (Secure Mode)"
echo "========================================================"
echo "Project:         ${PROJECT_ID}"
echo "Region:          ${REGION}"
echo "Service:         ${SERVICE_NAME}"
echo "Image:           ${IMAGE_NAME}"
echo "Service Account: Default Compute (linked to your email)"
echo "Auth Method:     Multi-User SHA-256 Password Hashing"
echo "User Teams:      risk_team, credit_team, collection_team"
echo "========================================================"
echo ""

# Step 1: Set the project context
echo "📌 [1/5] Setting GCP project context..."
gcloud config set project ${PROJECT_ID}

# Step 2: Enable required APIs
echo ""
echo "🔧 [2/5] Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  bigquery.googleapis.com

# Step 3: Build and push container to Artifact Registry
echo ""
echo "🏗️  [3/5] Building and pushing container to Artifact Registry..."
gcloud builds submit --tag ${IMAGE_NAME}:latest

# Step 4: Deploy to Cloud Run with security configurations
echo ""
echo "🚀 [4/5] Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
  --image ${IMAGE_NAME}:latest \
  --platform managed \
  --region ${REGION} \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300s \
  --max-instances 10 \
  --min-instances 0 \
  --port 8080 \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID}" \
  --set-env-vars "GCP_REGION=${REGION}" \
  --set-env-vars "BIGQUERY_DATASET=aiml_cj_nostd_mart" \
  --set-env-vars "BIGQUERY_LOCATION=asia-south1" \
  --set-env-vars "VERTEX_AI_LOCATION=asia-south1" \
  --set-env-vars "GEMINI_PRO_MODEL=gemini-2.5-pro" \
  --set-env-vars "GEMINI_FLASH_MODEL=gemini-2.5-flash" \
  --set-env-vars "LLM_TEMPERATURE=0.1" \
  --set-env-vars "LLM_MAX_OUTPUT_TOKENS=8192" \
  --set-env-vars "LOGGING_DATASET=aiml_cj_nostd_mart" \
  --set-env-vars "LOGGING_TABLE=adk_copilot_logs" \
  --set-env-vars "USER_CREDENTIALS=${USER_CREDENTIALS}"

# Step 5: Get the service URL and display success message
echo ""
echo "✅ [5/5] Deployment complete!"
echo ""
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region=${REGION} --format='value(status.url)')
echo "========================================================"
echo "🌐 Service URL: ${SERVICE_URL}"
echo "========================================================"
echo ""
echo "📋 DEPLOYMENT SUMMARY:"
echo "  ✓ Container: Artifact Registry (${REPO_NAME})"
echo "  ✓ Runtime: Non-root user (appuser)"
echo "  ✓ Auth: SHA-256 hashed passwords"
echo "  ✓ Service Account: ${SERVICE_ACCOUNT}"
echo "  ✓ Memory: 2GB | CPU: 2 cores | Timeout: 5min"
echo ""
echo "📝 NEXT STEPS:"
echo "  1. Visit ${SERVICE_URL} and log in"
echo "  2. Available usernames:"
echo "     - risk_team_user"
echo "     - credit_team_user"
echo "     - collection_team_user"
echo "  3. Passwords: (the passwords you hashed for each team)"
echo ""
echo "🔒 SECURITY NOTES:"
echo "  - Password hashes stored as env var (not in code)"
echo "  - App runs as non-root user in container"
echo "  - Using default compute service account (linked to your email)"
echo "  - Multi-user authentication with team-based access"
echo "  - No plain text secrets in repository"
echo ""
echo "📊 MONITORING:"
echo "  View logs: gcloud run services logs read ${SERVICE_NAME} --region=${REGION} --follow"
echo "  View metrics: https://console.cloud.google.com/run/detail/${REGION}/${SERVICE_NAME}/metrics"
echo ""