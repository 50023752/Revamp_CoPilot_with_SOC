# 🔒 Security Guide - Orion Copilot Cloud Run Deployment

## Overview

This guide documents the enterprise-grade security improvements implemented for deploying Orion Copilot to Google Cloud Run. The refactored architecture addresses critical security concerns while maintaining functionality.

---

## 🎯 Security Objectives Achieved

### 1. ✅ SHA-256 Password Hashing
**Problem:** Plain text passwords stored in code  
**Solution:** SHA-256 hashing with secure comparison

- Passwords never stored in plain text
- Hash stored as environment variable (not in repository)
- Constant-time comparison prevents timing attacks
- Easy password rotation without code changes

### 2. ✅ Environment Variable Security
**Problem:** Secrets hardcoded in application  
**Solution:** Runtime configuration via environment variables

- `ADMIN_PASSWORD_HASH` injected at deployment time
- No secrets committed to version control
- Separation of configuration and code
- Follows 12-factor app principles

### 3. ✅ Dedicated Service Account (Least Privilege)
**Problem:** Using default Compute Engine service account  
**Solution:** Custom service account with minimal permissions

- Service account: `orion-copilot-sa@PROJECT_ID.iam.gserviceaccount.com`
- Only granted required roles:
  - `roles/bigquery.dataEditor` - Read/write BigQuery data
  - `roles/bigquery.jobUser` - Run BigQuery queries
  - `roles/aiplatform.user` - Access Vertex AI models
- No excessive permissions (e.g., no project editor, no secret access)

### 4. ✅ Artifact Registry (Modern Container Storage)
**Problem:** Using deprecated Container Registry (gcr.io)  
**Solution:** Migrated to Artifact Registry (pkg.dev)

- Container images: `asia-south1-docker.pkg.dev/PROJECT_ID/orion-copilot-repo/SERVICE_NAME`
- Better security controls and vulnerability scanning
- Improved access management with IAM
- Future-proof (gcr.io is deprecated)

### 5. ✅ Non-Root Container Execution
**Problem:** Container running as root user  
**Solution:** Dedicated non-privileged user (appuser)

- Container runs as `appuser` (not root)
- Reduces attack surface if container is compromised
- Follows Docker security best practices
- Implemented in Dockerfile with proper file ownership

---

## 🏗️ Architecture Changes

### Before (Insecure)
```python
# Plain text password in code ❌
def check_password():
    if password == "MySecretPass":  # INSECURE!
        return True
```

```bash
# Deprecated container registry ❌
gcr.io/my-project/my-app

# Default service account ❌
# (Runs with excessive permissions)
```

### After (Secure)
```python
# SHA-256 hashed password comparison ✅
from utils.auth_utils import authenticate_user

if authenticate_user(username, password):
    st.session_state["authenticated"] = True
```

```bash
# Modern Artifact Registry ✅
asia-south1-docker.pkg.dev/PROJECT_ID/orion-copilot-repo/orion-copilot

# Dedicated service account ✅
--service-account orion-copilot-sa@PROJECT_ID.iam.gserviceaccount.com
```

---

## 📦 New Files Created

| File | Purpose |
|------|---------|
| `utils/auth_utils.py` | SHA-256 hashing and authentication logic |
| `setup_security.sh` | Automated security prerequisite setup (Linux/Mac) |
| `setup_security.bat` | Automated security prerequisite setup (Windows) |
| `SECURITY_GUIDE.md` | This comprehensive security documentation |

## 📝 Modified Files

| File | Changes |
|------|---------|
| `streamlit_app_v2.py` | Updated `authenticate()` function to use SHA-256 hashing |
| `Dockerfile` | Added non-root user (`appuser`) with proper file ownership |
| `deploy.sh` | Updated to use Artifact Registry, service account, and password hash |
| `deploy.bat` | Updated to use Artifact Registry, service account, and password hash |

---

## 🚀 Deployment Workflow

### Step 1: Prerequisites Setup (One-Time)

Run the security setup script to create required GCP resources:

**Windows:**
```cmd
setup_security.bat
```

**Linux/Mac:**
```bash
chmod +x setup_security.sh
./setup_security.sh
```

This script will:
- ✅ Enable required Google Cloud APIs
- ✅ Create Artifact Registry repository (`orion-copilot-repo`)
- ✅ Create service account (`orion-copilot-sa`)
- ✅ Grant minimal required IAM permissions

### Step 2: Generate Password Hash

Generate a SHA-256 hash of your desired password:

```bash
python utils/auth_utils.py YourSecurePassword123
```

**Example Output:**
```
Password Hash (SHA-256):
------------------------------------------------------------
a9c7b8e4f2d1c3e5a7b9c1d3e5f7a9b1c3d5e7f9a1b3c5d7e9f1a3b5c7d9e1f3
------------------------------------------------------------
```

**Copy this hash** - you'll need it in the next step.

### Step 3: Update Deployment Script

Edit your deployment script and replace the placeholder:

**In `deploy.sh` (Linux/Mac):**
```bash
# Line ~60
ADMIN_PASSWORD_HASH="REPLACE_WITH_YOUR_SHA256_HASH"
```

**In `deploy.bat` (Windows):**
```bat
REM Line ~35
set ADMIN_PASSWORD_HASH=REPLACE_WITH_YOUR_SHA256_HASH
```

Replace with your actual hash:
```bash
ADMIN_PASSWORD_HASH="a9c7b8e4f2d1c3e5a7b9c1d3e5f7a9b1c3d5e7f9a1b3c5d7e9f1a3b5c7d9e1f3"
```

### Step 4: Deploy to Cloud Run

**Windows:**
```cmd
deploy.bat
```

**Linux/Mac:**
```bash
chmod +x deploy.sh
./deploy.sh
```

The script will:
- ✅ Build container and push to Artifact Registry
- ✅ Deploy to Cloud Run with security configurations
- ✅ Inject password hash as environment variable
- ✅ Configure service account with least privilege
- ✅ Display service URL

---

## 🔐 Authentication Flow

### Login Process

1. **User visits the application**
2. **Login form appears** (sidebar)
   - Username field
   - Password field (masked input)
3. **User enters credentials**
4. **Application flow:**
   ```
   User Input → SHA-256 Hash → Compare with ADMIN_PASSWORD_HASH → Grant/Deny Access
   ```
5. **If successful:**
   - Session state set to authenticated
   - Username stored in session
   - User can access the application
6. **If failed:**
   - Error message displayed
   - Failed attempt logged (for monitoring)
   - Session remains unauthenticated

### Default Credentials

**Username:** `admin`  
**Password:** Whatever you hashed in Step 2

### Multi-User Support

For multiple users, use JSON format in environment variables:

```bash
USER_CREDENTIALS='[
  {"username": "admin", "password_hash": "hash1..."},
  {"username": "analyst", "password_hash": "hash2..."}
]'
```

Deploy with:
```bash
--set-env-vars "USER_CREDENTIALS=${USER_CREDENTIALS}"
```

---

## 🛡️ Security Best Practices

### ✅ DO

1. **Rotate passwords regularly**
   - Generate new hash: `python utils/auth_utils.py NewPassword`
   - Update environment variable in Cloud Run console
   - Redeploy service

2. **Use strong passwords**
   - Minimum 12 characters
   - Mix of uppercase, lowercase, numbers, symbols
   - Example: `MySecure2024Pass!@#`

3. **Monitor authentication logs**
   ```bash
   gcloud run services logs read orion-copilot --region=asia-south1 --filter="User authenticated"
   ```

4. **Review IAM permissions regularly**
   ```bash
   gcloud projects get-iam-policy analytics-datapipeline-prod \
     --flatten="bindings[].members" \
     --filter="bindings.members:orion-copilot-sa"
   ```

5. **Keep dependencies updated**
   ```bash
   pip list --outdated
   ```

### ❌ DON'T

1. **Never commit password hashes to Git**
   - Use `.gitignore` for any files containing hashes
   - Store hashes only in deployment scripts (not in repo)

2. **Never use the same password across environments**
   - Development: One password
   - Production: Different password

3. **Never grant excessive permissions**
   - Don't use `roles/editor` or `roles/owner`
   - Stick to minimal required roles

4. **Never expose the hash in logs**
   - Ensure logging doesn't capture environment variables
   - Review logs for accidental hash exposure

---

## 🔍 Security Validation Checklist

After deployment, verify these security controls:

- [ ] Container runs as non-root user (`appuser`)
  ```bash
  gcloud run services describe orion-copilot --region=asia-south1 --format="value(spec.template.spec.containers[0].securityContext)"
  ```

- [ ] Service account is custom (not default)
  ```bash
  gcloud run services describe orion-copilot --region=asia-south1 --format="value(spec.template.spec.serviceAccountName)"
  ```
  Should show: `orion-copilot-sa@PROJECT_ID.iam.gserviceaccount.com`

- [ ] Password hash is set as environment variable
  ```bash
  gcloud run services describe orion-copilot --region=asia-south1 --format="value(spec.template.spec.containers[0].env[?(@.name=='ADMIN_PASSWORD_HASH')].value)"
  ```
  Should show: Your SHA-256 hash

- [ ] Image is in Artifact Registry (not gcr.io)
  ```bash
  gcloud run services describe orion-copilot --region=asia-south1 --format="value(spec.template.spec.containers[0].image)"
  ```
  Should show: `asia-south1-docker.pkg.dev/...`

- [ ] Login requires correct credentials
  - Try logging in with wrong password → Should fail
  - Try logging in with correct password → Should succeed

---

## 📊 Monitoring & Auditing

### View Authentication Logs

```bash
# All authentication events
gcloud run services logs read orion-copilot \
  --region=asia-south1 \
  --filter="jsonPayload.message=~'authenticated|login'"

# Failed login attempts only
gcloud run services logs read orion-copilot \
  --region=asia-south1 \
  --filter="jsonPayload.message=~'Failed login'"
```

### Set Up Alerting (Recommended)

Create a log-based metric for failed logins:

```bash
gcloud logging metrics create failed_logins \
  --description="Failed login attempts to Orion Copilot" \
  --log-filter='resource.type="cloud_run_revision"
    resource.labels.service_name="orion-copilot"
    jsonPayload.message=~"Failed login attempt"'
```

---

## 🔄 Password Rotation Procedure

### When to Rotate

- Every 90 days (recommended)
- After employee departure
- Suspected credential compromise
- Compliance requirements

### How to Rotate

1. **Generate new hash:**
   ```bash
   python utils/auth_utils.py NewSecurePassword456
   ```

2. **Update Cloud Run service:**
   ```bash
   gcloud run services update orion-copilot \
     --region=asia-south1 \
     --update-env-vars "ADMIN_PASSWORD_HASH=<new-hash>"
   ```

3. **Verify new password works:**
   - Visit application URL
   - Log in with new password
   - Confirm access granted

4. **Communicate change to users**

---

## 🚨 Incident Response

### Suspected Credential Compromise

1. **Immediately rotate password** (see above)
2. **Review access logs:**
   ```bash
   gcloud run services logs read orion-copilot \
     --region=asia-south1 \
     --filter="timestamp>=\"2025-12-01T00:00:00Z\"" \
     --format=json > audit_logs.json
   ```
3. **Check for unauthorized access patterns**
4. **Update security policies if needed**

### Container Vulnerability Detected

1. **Scan container for vulnerabilities:**
   ```bash
   gcloud artifacts docker images scan \
     asia-south1-docker.pkg.dev/PROJECT_ID/orion-copilot-repo/orion-copilot:latest
   ```

2. **Review scan results:**
   ```bash
   gcloud artifacts docker images list-vulnerabilities \
     asia-south1-docker.pkg.dev/PROJECT_ID/orion-copilot-repo/orion-copilot:latest
   ```

3. **Update dependencies in `requirements.txt`**
4. **Rebuild and redeploy**

---

## 📚 Additional Resources

- [Cloud Run Security Best Practices](https://cloud.google.com/run/docs/securing/managing-access)
- [Artifact Registry Documentation](https://cloud.google.com/artifact-registry/docs)
- [IAM Service Accounts](https://cloud.google.com/iam/docs/service-accounts)
- [Container Security Best Practices](https://cloud.google.com/solutions/best-practices-for-operating-containers)

---

## 🎓 Security Training Topics

For teams deploying this application:

1. **Password Management**
   - Why hashing is critical
   - How SHA-256 works
   - Password rotation procedures

2. **Least Privilege Principle**
   - Why custom service accounts matter
   - How to audit IAM permissions
   - When to grant additional roles

3. **Container Security**
   - Non-root user benefits
   - Vulnerability scanning
   - Dependency management

4. **Incident Response**
   - Detecting unauthorized access
   - Log analysis techniques
   - Communication protocols

---

**Last Updated:** December 2, 2025  
**Maintained By:** Cloud Security Team  
**Questions?** Contact your security administrator
