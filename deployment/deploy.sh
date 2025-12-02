#!/bin/bash
set -e

# ============================================
# Configuration
# ============================================
PROJECT_ID="analytics-datapipeline-prod"
REGION="asia-south1"
SERVICE_NAME="orion-copilot"
GCS_BUCKET="aiml-cj"
REPO_NAME="orion-copilot-repo"

# 1. Generate Git Tag
if git rev-parse --short HEAD > /dev/null 2>&1; then
  TAG=$(git rev-parse --short HEAD)
else
  TAG="manual-$(date +%s)"
fi

# ============================================
# SECURITY: User Credentials (JSON)
# ============================================
# NOTE: We define this as a standard bash string. 
# We will pass it to Python later to handle the formatting safely.
USER_CREDENTIALS='[{"username":"risk_team_user","password_hash":"dc3ebd167176ae342e035e74cc1eaa5f43e0b62159dc3a7af6d2489386613d7f"},{"username":"credit_team_user","password_hash":"49f7fefb5768c0c6dae7f9b64a6f190639948054ebcc21b90737be9e6c6933b7"},{"username":"admin","password_hash":"8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"}, {"username":"analytics_team_user","password_hash":"a0359600ea16251d7ebe060617c15b459e11c896cabd0d1fa6e50c545c4ae009"}]'

echo "🚀 Deploying Orion Copilot"
echo "========================================"
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo "Tag:     ${TAG}"
echo "========================================"

# 2. Set Project
gcloud config set project ${PROJECT_ID}

# 3. Generate Env Vars File (The Python Fix)
# We use python to dump the YAML. This handles quotes/newlines perfectly.
echo "📝 Generating environment configuration..."
python3 -c "
import yaml
import os

data = {
    'GCP_PROJECT_ID': '${PROJECT_ID}',
    'GCP_REGION': '${REGION}',
    'BIGQUERY_DATASET': 'aiml_cj_nostd_mart',
    'BIGQUERY_LOCATION': 'asia-south1',
    'VERTEX_AI_LOCATION': 'asia-south1',
    'SOURCING_TABLE': 'Sourcing_Data_11_Nov_25',
    'COLLECTIONS_TABLE': 'TW_COLL_MART_HIST_v2',
    'DISBURSAL_TABLE': 'TW_NOSTD_MART_REALTIME_UPDATED',
    'GEMINI_PRO_MODEL': 'gemini-2.5-pro',
    'GEMINI_FLASH_MODEL': 'gemini-2.5-flash',
    'LLM_TEMPERATURE': '0.1',
    'LLM_MAX_OUTPUT_TOKENS': '8192',
    'LOGGING_DATASET': 'aiml_cj_nostd_mart',
    'LOGGING_TABLE': 'adk_copilot_logs',
    'ADK_SESSION_BACKEND': 'in-memory',
    'ADK_LOG_LEVEL': 'INFO',
    'USER_CREDENTIALS': '${USER_CREDENTIALS}',
    'GOOGLE_API_KEY': 'AIzaSyA7k6a3Kl2zk4GEyZgm1O909tsOViq6620'
}

with open('env_vars.yaml', 'w') as f:
    yaml.dump(data, f, default_flow_style=False)
"

# 4. Build & Push
echo ""
echo "🏗️  Building image..."
cd .. 
gcloud builds submit \
  --config=deployment/cloudbuild.yaml \
  --gcs-log-dir=gs://${GCS_BUCKET}/orion-copilot/logs \
  --gcs-source-staging-dir=gs://${GCS_BUCKET}/orion-copilot/source \
  --substitutions=_REPO_NAME=${REPO_NAME},_SERVICE_NAME=${SERVICE_NAME},_REGION=${REGION},_TAG=${TAG} \
  .
cd deployment

# 5. Deploy to Cloud Run using the Env File
echo ""
echo "🚀 Deploying service..."
gcloud run deploy ${SERVICE_NAME} \
  --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:${TAG} \
  --platform managed \
  --region ${REGION} \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300s \
  --port 8080 \
  --env-vars-file env_vars.yaml

# Cleanup sensitive file
rm env_vars.yaml

echo ""
echo "✅ Deployment Complete!"
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region=${REGION} --format='value(status.url)')
echo "🌐 URL: ${SERVICE_URL}"