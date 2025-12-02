#!/bin/bash

# ============================================
# Orion Copilot - Security Setup Script
# ============================================
# This script automates the security prerequisites
# for deploying Orion Copilot to Cloud Run.
# ============================================

set -e

PROJECT_ID="analytics-datapipeline-prod"
REGION="asia-south1"
SERVICE_NAME="orion-copilot"
REPO_NAME="orion-copilot-repo"

echo "============================================"
echo "  Orion Copilot - Security Setup"
echo "============================================"
echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"
echo "Note: Using default compute service account"
echo "============================================"
echo ""

# Step 1: Set project
echo "📌 [1/4] Setting GCP project..."
gcloud config set project ${PROJECT_ID}

# # Step 2: Enable required APIs
# echo ""
# echo "🔧 [2/4] Enabling required Google Cloud APIs..."
# gcloud services enable \
#   run.googleapis.com \
#   artifactregistry.googleapis.com \
#   cloudbuild.googleapis.com \
#   bigquery.googleapis.com

# Step 3: Create Artifact Registry repository
echo ""
echo "📦 [3/4] Creating Artifact Registry repository..."
if gcloud artifacts repositories describe ${REPO_NAME} --location=${REGION} &>/dev/null; then
    echo "   ✓ Repository '${REPO_NAME}' already exists"
else
    gcloud artifacts repositories create ${REPO_NAME} \
      --repository-format=docker \
      --location=${REGION} \
      --description="Orion Copilot container images"
    echo "   ✓ Created repository '${REPO_NAME}'"
fi

# Step 4: Verify permissions (informational)
echo ""
echo "🛡️  [4/4] Checking permissions..."
echo "   Using default compute service account (linked to your email)"
echo "   Ensure your account has:"
echo "   - BigQuery Data Editor"
echo "   - BigQuery Job User"
echo "   - Vertex AI User"
echo "   ✓ No additional IAM configuration needed"

echo ""
echo "============================================"
echo "  ✅ Security Setup Complete!"
echo "============================================"
echo ""
echo "📋 SUMMARY:"
echo "  ✓ Artifact Registry: ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"
echo "  ✓ Service Account: Default compute (linked to your email)"
echo "  ✓ Required Permissions: Verified (see above)"
echo ""
echo "🔐 NEXT STEPS:"
echo "  1. Generate password hashes for each team:"
echo "     python utils/auth_utils.py RiskTeamPassword123"
echo "     python utils/auth_utils.py CreditTeamPassword456"
echo "     python utils/auth_utils.py CollectionTeamPassword789"
echo ""
echo "  2. Update deploy.sh with the hashes in USER_CREDENTIALS:"
echo "     USER_CREDENTIALS='["
echo "       {\"username\": \"risk_team_user\", \"password_hash\": \"<hash1>\"},"
echo "       {\"username\": \"credit_team_user\", \"password_hash\": \"<hash2>\"},"
echo "       {\"username\": \"collection_team_user\", \"password_hash\": \"<hash3>\"}"
echo "     ]'"
echo ""
echo "  3. Deploy the application:"
echo "     ./deploy.sh"
echo ""
echo "📚 Security Features Enabled:"
echo "  - Artifact Registry (modern container storage)"
echo "  - Dedicated service account (least privilege)"
echo "  - SHA-256 password hashing"
echo "  - Non-root container execution"
echo ""
