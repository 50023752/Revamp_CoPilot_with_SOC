# ✅ Pre-Deployment Security Checklist

Use this checklist before deploying to production.

---

## 🔐 Prerequisites

- [ ] Google Cloud CLI (`gcloud`) installed and authenticated
  ```bash
  gcloud auth login
  gcloud config set project analytics-datapipeline-prod
  ```

- [ ] Python 3.12+ installed locally
  ```bash
  python --version  # Should be 3.12+
  ```

- [ ] Required GCP permissions:
  - [ ] Cloud Run Admin
  - [ ] Artifact Registry Admin
  - [ ] Service Account Admin
  - [ ] IAM Security Admin

---

## 🏗️ Infrastructure Setup

- [ ] Run security setup script
  ```bash
  # Windows
  setup_security.bat
  
  # Linux/Mac
  chmod +x setup_security.sh && ./setup_security.sh
  ```

- [ ] Verify Artifact Registry repository created
  ```bash
  gcloud artifacts repositories describe orion-copilot-repo --location=asia-south1
  ```

- [ ] Verify service account created
  ```bash
  gcloud iam service-accounts describe orion-copilot-sa@analytics-datapipeline-prod.iam.gserviceaccount.com
  ```

- [ ] Verify IAM permissions granted
  ```bash
  gcloud projects get-iam-policy analytics-datapipeline-prod \
    --flatten="bindings[].members" \
    --filter="bindings.members:orion-copilot-sa"
  ```
  Should show: `bigquery.dataEditor`, `bigquery.jobUser`, `aiplatform.user`

---

## 🔑 Authentication Configuration

- [ ] Generate password hash
  ```bash
  python utils/auth_utils.py "YourSecurePassword123"
  ```

- [ ] Copy hash from output
  ```
  Password Hash (SHA-256):
  ------------------------------------------------------------
  a9c7b8e4f2d1c3e5a7b9c1d3e5f7a9b1c3d5e7f9a1b3c5d7e9f1a3b5c7d9e1f3
  ------------------------------------------------------------
  ```

- [ ] Update deployment script with hash
  - **For Windows:** Edit `deploy.bat` line ~35
  - **For Linux/Mac:** Edit `deploy.sh` line ~60
  
  Replace:
  ```bash
  ADMIN_PASSWORD_HASH="REPLACE_WITH_YOUR_SHA256_HASH"
  ```
  
  With:
  ```bash
  ADMIN_PASSWORD_HASH="a9c7b8e4f2d1c3e5a7b9c1d3e5f7a9b1c3d5e7f9a1b3c5d7e9f1a3b5c7d9e1f3"
  ```

- [ ] **IMPORTANT:** Verify hash is NOT committed to Git
  ```bash
  git status
  # deploy.sh and deploy.bat should be listed in .gitignore or manually excluded
  ```

---

## 📦 Code Review

- [ ] No plain text passwords in any Python files
  ```bash
  # Search for potential hardcoded passwords
  grep -ri "password.*=" --include="*.py" .
  # Should only find variable assignments, not literal strings
  ```

- [ ] No API keys in code
  ```bash
  grep -ri "api.*key.*=" --include="*.py" .
  # Should only reference environment variables
  ```

- [ ] `streamlit_app_v2.py` uses `utils.auth_utils`
  ```python
  from utils.auth_utils import authenticate_user
  ```

- [ ] Dockerfile creates non-root user
  ```dockerfile
  USER appuser
  ```

---

## 🐳 Container Configuration

- [ ] Dockerfile builds successfully locally (optional test)
  ```bash
  docker build -t orion-copilot:test .
  ```

- [ ] Dockerfile exposes only port 8080
  ```dockerfile
  EXPOSE 8080
  ```

- [ ] No secrets in Dockerfile
  ```bash
  grep -i "password\|secret\|key" Dockerfile
  # Should only find ENV variable names, not values
  ```

---

## 🚀 Deployment Configuration

- [ ] `deploy.sh` or `deploy.bat` configured correctly:
  - [ ] `PROJECT_ID` matches your GCP project
  - [ ] `REGION` is `asia-south1`
  - [ ] `REPO_NAME` is `orion-copilot-repo`
  - [ ] `SERVICE_ACCOUNT` is `orion-copilot-sa@PROJECT_ID.iam.gserviceaccount.com`
  - [ ] `ADMIN_PASSWORD_HASH` is set to your hash

- [ ] Environment variables are properly set
  ```bash
  # Check deploy script includes:
  grep "ADMIN_PASSWORD_HASH" deploy.sh  # or deploy.bat
  grep "GCP_PROJECT_ID" deploy.sh
  grep "BIGQUERY_DATASET" deploy.sh
  ```

---

## 🧪 Pre-Flight Tests

- [ ] Python dependencies install correctly
  ```bash
  pip install -r requirements.txt
  ```

- [ ] No syntax errors in Python files
  ```bash
  python -m py_compile streamlit_app_v2.py
  python -m py_compile utils/auth_utils.py
  ```

- [ ] Password hash generator works
  ```bash
  python utils/auth_utils.py "TestPassword"
  # Should output a 64-character hex string
  ```

---

## 📊 Monitoring Setup (Optional but Recommended)

- [ ] Create log-based metric for failed logins
  ```bash
  gcloud logging metrics create failed_logins \
    --description="Failed login attempts" \
    --log-filter='resource.type="cloud_run_revision"
      resource.labels.service_name="orion-copilot"
      jsonPayload.message=~"Failed login attempt"'
  ```

- [ ] Set up alerting policy (via Cloud Console)
  - Navigate to Monitoring > Alerting
  - Create alert for `failed_logins > 5 per minute`

---

## 🔒 Final Security Review

- [ ] All secrets are environment variables (not in code)
- [ ] Service account has minimal required permissions
- [ ] Container runs as non-root user
- [ ] Using Artifact Registry (not gcr.io)
- [ ] SHA-256 hashing implemented for passwords
- [ ] No sensitive data in Git repository
- [ ] Documentation is complete and accessible

---

## 🚦 Ready to Deploy?

If all checkboxes above are ✅, you're ready to deploy!

### Deploy Now:

**Windows:**
```cmd
deploy.bat
```

**Linux/Mac:**
```bash
chmod +x deploy.sh
./deploy.sh
```

---

## ✅ Post-Deployment Verification

After deployment completes:

- [ ] Service is running
  ```bash
  gcloud run services describe orion-copilot --region=asia-south1
  ```

- [ ] Service URL is accessible
  ```bash
  # Visit the URL provided in deployment output
  ```

- [ ] Login page appears
  - [ ] Username field visible
  - [ ] Password field visible (masked input)

- [ ] Login works with correct credentials
  - [ ] Username: `admin`
  - [ ] Password: (your password from Step 2)
  - [ ] Successfully authenticated

- [ ] Login fails with wrong credentials
  - [ ] Wrong password shows error message
  - [ ] No sensitive information leaked in error

- [ ] Container runs as non-root
  ```bash
  gcloud run services describe orion-copilot \
    --region=asia-south1 \
    --format="value(spec.template.spec.containers[0].securityContext)"
  ```

- [ ] Service account is custom (not default)
  ```bash
  gcloud run services describe orion-copilot \
    --region=asia-south1 \
    --format="value(spec.template.spec.serviceAccountName)"
  ```
  Should show: `orion-copilot-sa@analytics-datapipeline-prod.iam.gserviceaccount.com`

- [ ] Password hash is set as environment variable
  ```bash
  gcloud run services describe orion-copilot \
    --region=asia-south1 \
    --format="value(spec.template.spec.containers[0].env)"
  ```
  Should include `ADMIN_PASSWORD_HASH`

- [ ] Logs are accessible and clean
  ```bash
  gcloud run services logs read orion-copilot --region=asia-south1 --limit=20
  ```

---

## 🎉 Success!

If all post-deployment checks pass:

✅ **Your application is securely deployed!**

📖 Refer to `SECURITY_GUIDE.md` for ongoing maintenance  
🚨 Refer to `SECURITY_GUIDE.md` for incident response  
🔄 Schedule password rotation in 90 days

---

## 🚨 Rollback Procedure (If Needed)

If deployment fails or issues are detected:

1. **Note the issue**
   ```bash
   gcloud run services logs read orion-copilot --region=asia-south1 --limit=50 > error_logs.txt
   ```

2. **Roll back to previous version** (if applicable)
   ```bash
   gcloud run services update-traffic orion-copilot \
     --region=asia-south1 \
     --to-revisions=PREVIOUS_REVISION=100
   ```

3. **Fix the issue locally**
   - Review error logs
   - Correct configuration
   - Re-run this checklist

4. **Redeploy**
   ```bash
   deploy.bat  # or deploy.sh
   ```

---

**Checklist Version:** 1.0  
**Last Updated:** December 2, 2025  
**Owner:** DevOps & Security Team
