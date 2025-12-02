# 🚀 Quick Start - Secure Deployment (Multi-User)

## TL;DR - 3 Steps to Deploy

### Step 1: Setup (One-Time)
```bash
# Windows
cd deployment
setup_security.bat
cd ..

# Linux/Mac
cd deployment
chmod +x setup_security.sh && ./setup_security.sh
cd ..
```

### Step 2: Generate Team Password Hashes

**Option A: Interactive Mode (Recommended)**
```bash
python scripts\generate_multi_user_creds.py
```
Follow the prompts to enter passwords for each team, then copy the generated JSON.

**Option B: Manual Mode**
```bash
python utils\auth_utils.py RiskTeamPassword123
python utils\auth_utils.py CreditTeamPassword456
python utils\auth_utils.py CollectionTeamPassword789
```
**Copy each hash output**

### Step 3: Deploy
Edit `deployment\deploy.sh` or `deployment\deploy.bat` and replace:
```bash
USER_CREDENTIALS='[
  {"username": "risk_team_user", "password_hash": "REPLACE_WITH_RISK_TEAM_HASH"},
  {"username": "credit_team_user", "password_hash": "REPLACE_WITH_CREDIT_TEAM_HASH"},
  {"username": "collection_team_user", "password_hash": "REPLACE_WITH_COLLECTION_TEAM_HASH"}
]'
```

Then run:
```bash
# Windows
cd deployment
deploy.bat

# Linux/Mac
cd deployment
chmod +x deploy.sh
./deploy.sh
```

---

## Login Credentials

**URL:** (provided after deployment)

**Available Users:**
| Username | Team | Password |
|----------|------|----------|
| `risk_team_user` | Risk Team | (password you set for risk team) |
| `credit_team_user` | Credit Team | (password you set for credit team) |
| `collection_team_user` | Collection Team | (password you set for collection team) |

---

## Security Features ✅

- ✅ SHA-256 password hashing (no plain text)
- ✅ Multi-user authentication (team-based access)
- ✅ Artifact Registry (modern container storage)
- ✅ Default compute service account (linked to your email)
- ✅ Non-root container execution
- ✅ Environment variable secrets (not in code)

---

## Common Commands

### View Logs
```bash
gcloud run services logs read orion-copilot --region=asia-south1 --follow
```

### Update Password
```bash
# Generate new hash for a specific team
python utils\auth_utils.py NewRiskTeamPassword

# Update USER_CREDENTIALS in deployment\deploy.bat or deployment\deploy.sh with new hash
# Redeploy
cd deployment
deploy.bat  # or deploy.sh
```

### Add More Users
Edit USER_CREDENTIALS in `deployment\deploy.sh` or `deployment\deploy.bat`:
```bash
USER_CREDENTIALS='[
  {"username": "risk_team_user", "password_hash": "hash1..."},
  {"username": "credit_team_user", "password_hash": "hash2..."},
  {"username": "collection_team_user", "password_hash": "hash3..."},
  {"username": "new_team_user", "password_hash": "hash4..."}
]'
```

### Redeploy After Code Changes
```bash
# Just run deploy script again
cd deployment
deploy.bat  # or deploy.sh
```

---

## Troubleshooting

### "User Credentials Not Configured" Error
- Edit `deployment\deploy.sh` or `deployment\deploy.bat`
- Replace `USER_CREDENTIALS` placeholders with actual hashes
- Use `python scripts\generate_multi_user_creds.py` for easy setup

### "Repository not found" Error
- Run `deployment\setup_security.bat` or `deployment\setup_security.sh` first
- Creates required Artifact Registry repository

### "Permission denied" Error
- Verify you have Cloud Run Admin role
- Run: `gcloud auth login`
- Ensure your email-linked service account has BigQuery and Vertex AI permissions

### Login Fails
- Verify username is exactly as configured (case-sensitive)
- Check password matches what you hashed for that specific team user
- Review logs for error details

---

## Need More Details?

📖 Read **SECURITY_GUIDE.md** for comprehensive documentation

💡 **Pro Tip:** Bookmark this page for quick reference!
