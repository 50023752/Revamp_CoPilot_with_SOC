# Cloud Run Deployment Guide - Orion Copilot

This guide will help you deploy your Streamlit application (`streamlit_app_v2.py`) to Google Cloud Run.

## 📋 Prerequisites

1. **Google Cloud Account** with billing enabled
2. **gcloud CLI** installed and configured ([Install Guide](https://cloud.google.com/sdk/docs/install))
3. **Docker** installed (optional, for local testing)
4. **Required Permissions**:
   - Cloud Run Admin
   - Cloud Build Editor
   - Service Account User
   - Storage Admin (for Container Registry)

## 🚀 Quick Deployment (Automated)

### Option 1: One-Click Deploy (Windows)

```cmd
deploy.bat
```

### Option 2: One-Click Deploy (Linux/Mac)

```bash
chmod +x deploy.sh
./deploy.sh
```

This automated script will:
- ✅ Enable required GCP APIs
- ✅ Build the Docker container
- ✅ Push to Container Registry
- ✅ Deploy to Cloud Run
- ✅ Configure environment variables
- ✅ Return your application URL

---

## 🛠️ Manual Deployment Steps

### Step 1: Authenticate with Google Cloud

```cmd
gcloud auth login
gcloud config set project analytics-datapipeline-prod
```

### Step 2: Enable Required APIs

```cmd
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable artifactregistry.googleapis.com
```

### Step 3: Build and Submit Container

```cmd
gcloud builds submit --tag gcr.io/analytics-datapipeline-prod/orion-copilot:latest
```

**Note**: This step takes 5-10 minutes. It uploads your code and builds the Docker container in the cloud.

### Step 4: Deploy to Cloud Run

```cmd
gcloud run deploy orion-copilot ^
  --image gcr.io/analytics-datapipeline-prod/orion-copilot:latest ^
  --platform managed ^
  --region asia-south1 ^
  --allow-unauthenticated ^
  --memory 2Gi ^
  --cpu 2 ^
  --timeout 300s ^
  --max-instances 10 ^
  --port 8080
```

### Step 5: Set Environment Variables (Secret)

Your `GOOGLE_API_KEY` should be set as a **secret** for security:

```cmd
# Create secret
echo YOUR_API_KEY_HERE | gcloud secrets create google-api-key --data-file=-

# Grant Cloud Run access to the secret
gcloud secrets add-iam-policy-binding google-api-key ^
  --member=serviceAccount:YOUR-PROJECT-NUMBER-compute@developer.gserviceaccount.com ^
  --role=roles/secretmanager.secretAccessor

# Update Cloud Run to use the secret
gcloud run services update orion-copilot ^
  --region asia-south1 ^
  --update-secrets=GOOGLE_API_KEY=google-api-key:latest
```

**Find your project number:**
```cmd
gcloud projects describe analytics-datapipeline-prod --format="value(projectNumber)"
```

---

## 🔐 Security Best Practices

### Set API Key as Secret (Recommended)

Instead of storing `GOOGLE_API_KEY` in environment variables, use Google Secret Manager:

1. **Via Console** (Easiest):
   - Go to [Cloud Run Console](https://console.cloud.google.com/run)
   - Select your service `orion-copilot`
   - Click "Edit & Deploy New Revision"
   - Scroll to "Secrets" → "Reference a Secret"
   - Select or create `GOOGLE_API_KEY` secret
   - Save and deploy

2. **Via CLI** (see Step 5 above)

### Service Account Permissions

Ensure your Cloud Run service account has:
- `roles/bigquery.dataViewer` - Read BigQuery data
- `roles/bigquery.jobUser` - Run queries
- `roles/bigquery.dataEditor` - Write logs to BigQuery
- `roles/aiplatform.user` - Access Vertex AI

```cmd
gcloud projects add-iam-policy-binding analytics-datapipeline-prod ^
  --member=serviceAccount:YOUR-PROJECT-NUMBER-compute@developer.gserviceaccount.com ^
  --role=roles/bigquery.dataEditor
```

---

## 🧪 Local Testing (Optional)

Before deploying, test the Docker container locally:

### Build the image:
```cmd
docker build -t orion-copilot:local .
```

### Run locally:
```cmd
docker run -p 8080:8080 ^
  -e GCP_PROJECT_ID=analytics-datapipeline-prod ^
  -e GOOGLE_API_KEY=YOUR_API_KEY ^
  orion-copilot:local
```

### Open browser:
```
http://localhost:8080
```

---

## 📊 Monitoring and Logs

### View Logs
```cmd
gcloud run services logs read orion-copilot --region=asia-south1 --follow
```

### View Service Details
```cmd
gcloud run services describe orion-copilot --region=asia-south1
```

### Monitor Performance
- [Cloud Run Metrics Dashboard](https://console.cloud.google.com/run/detail/asia-south1/orion-copilot/metrics)

---

## 🔄 Update Deployment

After making code changes:

```cmd
# Rebuild and deploy
gcloud builds submit --tag gcr.io/analytics-datapipeline-prod/orion-copilot:latest
gcloud run deploy orion-copilot --image gcr.io/analytics-datapipeline-prod/orion-copilot:latest --region asia-south1
```

Or simply run the deployment script again:
```cmd
deploy.bat
```

---

## 🛑 Common Issues

### Issue 1: "Permission Denied" during deployment
**Solution**: Ensure you have Cloud Run Admin role
```cmd
gcloud projects add-iam-policy-binding analytics-datapipeline-prod ^
  --member=user:YOUR_EMAIL@domain.com ^
  --role=roles/run.admin
```

### Issue 2: Container fails to start
**Solution**: Check logs for errors
```cmd
gcloud run services logs read orion-copilot --region=asia-south1 --limit=50
```

### Issue 3: "Error loading BigQuery data"
**Solution**: Verify service account has BigQuery permissions (see Security section)

### Issue 4: Timeout errors
**Solution**: Increase timeout and memory
```cmd
gcloud run services update orion-copilot ^
  --region asia-south1 ^
  --timeout 600s ^
  --memory 4Gi
```

---

## 💰 Cost Optimization

Cloud Run charges based on:
- **CPU/Memory usage** (per 100ms)
- **Requests** (first 2M free/month)
- **Egress** (data transfer out)

To minimize costs:
- Set `--min-instances 0` (scale to zero when idle)
- Use `--cpu 1` for lighter workloads
- Set `--concurrency 80` to handle multiple requests per instance

**Estimated Cost**: ~$5-20/month for low-medium traffic

---

## 🎯 Production Checklist

Before going live:

- [ ] Set `GOOGLE_API_KEY` as a Secret (not env var)
- [ ] Configure custom domain (optional)
- [ ] Enable Cloud Run authentication if needed
- [ ] Set up Cloud Monitoring alerts
- [ ] Configure automatic backups for BigQuery logs
- [ ] Review and apply resource limits
- [ ] Test error handling and edge cases
- [ ] Set up CI/CD pipeline (optional, using `cloudbuild.yaml`)

---

## 📞 Support

- **Cloud Run Docs**: https://cloud.google.com/run/docs
- **Streamlit Deployment**: https://docs.streamlit.io/deploy/streamlit-community-cloud
- **GCP Support**: https://cloud.google.com/support

---

## 📝 Files Created for Deployment

| File | Purpose |
|------|---------|
| `Dockerfile` | Container definition |
| `.dockerignore` | Files to exclude from container |
| `.gcloudignore` | Files to exclude from Cloud Build |
| `deploy.bat` | Windows deployment script |
| `deploy.sh` | Linux/Mac deployment script |
| `cloudbuild.yaml` | CI/CD configuration (optional) |
| `DEPLOYMENT_GUIDE.md` | This guide |

---

**Ready to deploy? Run `deploy.bat` and your app will be live in ~10 minutes! 🚀**
