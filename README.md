# 🪐 Orion Copilot - Multi-Agent SQL Assistant

Enterprise-grade AI-powered SQL query assistant with multi-user authentication, built on Google ADK framework.

## 📁 Project Structure

```
Revamp_CoPilot_with_SOC/
├── 📱 streamlit_app_v2.py          # Main Streamlit application
├── 📋 requirements.txt              # Python dependencies
├── 🔒 .env                          # Environment variables (local only)
│
├── 📂 agents/                       # AI Agent implementations
│   ├── domain/                      # Domain-specific SQL agents
│   │   ├── collections_agent.py    # Collections domain queries
│   │   ├── sourcing_agent.py       # Sourcing domain queries
│   │   └── disbursal_agent.py      # Disbursal domain queries
│   ├── execution/                   # Query execution agents
│   │   └── query_execution_agent.py
│   ├── intent/                      # Intent routing agents
│   │   └── router_agent.py
│   └── schema/                      # Schema management
│       └── schema_service.py
│
├── 📂 config/                       # Application configuration
│   ├── __init__.py
│   └── settings.py                  # Environment settings loader
│
├── 📂 contracts/                    # Data contracts & types
│   ├── __init__.py
│   ├── routing_contracts.py        # Intent routing types
│   └── sql_contracts.py            # SQL generation types
│
├── 📂 utils/                        # Utility modules
│   ├── __init__.py
│   ├── auth_utils.py               # SHA-256 authentication
│   ├── json_logger.py              # Structured JSON logging
│   ├── sql_safety_validator.py     # SQL injection prevention
│   └── schema_service.py           # Schema utilities
│
├── 📂 deployment/                   # Deployment configurations
│   ├── deploy.sh                   # Linux/Mac deployment script
│   ├── deploy.bat                  # Windows deployment script
│   ├── setup_security.sh           # Security setup (Linux/Mac)
│   ├── setup_security.bat          # Security setup (Windows)
│   ├── Dockerfile                  # Container definition
│   ├── .dockerignore               # Docker exclusions
│   ├── .gcloudignore              # Cloud Build exclusions
│   └── cloudbuild.yaml             # Cloud Build CI/CD config
│
├── 📂 scripts/                      # Utility scripts
│   ├── generate_multi_user_creds.py # Multi-user credential generator
│   ├── final_stress_testing.py     # Stress testing & evaluation
│   ├── run_evals.py                # Evaluation runner
│   ├── upload_to_bq.py             # BigQuery data uploader
│   └── test_token_capture.py       # Token usage tests
│
├── 📂 data/                         # Data files
│   ├── golden_question_bank.csv    # Test question bank
│   └── collections_final_Schema.json # Schema definitions
│
├── 📂 docs/                         # Documentation
│   ├── QUICK_START.md              # 🚀 Start here!
│   ├── DEPLOYMENT_GUIDE.md         # Detailed deployment guide
│   ├── DEPLOYMENT_CHECKLIST.md     # Pre-deployment checklist
│   ├── SECURITY_GUIDE.md           # Security best practices
│   ├── SECURITY_REFACTORING_SUMMARY.md # Security improvements
│   ├── MULTI_USER_SETUP.md         # Multi-user auth guide
│   └── REFACTORED_README.md        # Architecture documentation
│
├── 📂 tests/                        # Test suites
│   ├── __init__.py
│   ├── config_settings.py
│   └── test_refactored_architecture.py
│
├── 📂 reports/                      # Generated reports (gitignored)
│   └── (stress test results)
│
├── 📂 architecture/                 # Architecture documents
│   └── (design docs)
│
└── 📂 .streamlit/                   # Streamlit configuration
    └── config.toml
```

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Google Cloud CLI (`gcloud`)
- Access to GCP project `analytics-datapipeline-prod`

### 1. Setup Environment

```bash
# Clone the repository
cd c:\Users\50023752\Desktop\Multi_Agent_Copilot\Revamp_CoPilot_with_SOC

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env  # Edit with your values
```

### 2. Local Development

```bash
# Run Streamlit app locally
streamlit run streamlit_app_v2.py
```

### 3. Deploy to Cloud Run

```bash
# Windows
cd deployment
setup_security.bat
cd ..
python scripts\generate_multi_user_creds.py
cd deployment
deploy.bat

# Linux/Mac
cd deployment
chmod +x setup_security.sh deploy.sh
./setup_security.sh
cd ..
python scripts/generate_multi_user_creds.py
cd deployment
./deploy.sh
```

**📖 For detailed instructions, see [`docs/QUICK_START.md`](docs/QUICK_START.md)**

## 🔐 Security Features

- ✅ **SHA-256 Password Hashing** - No plain text passwords
- ✅ **Multi-User Authentication** - Team-based access control
- ✅ **SQL Injection Prevention** - AST-based validation
- ✅ **Non-Root Container** - Runs as `appuser`
- ✅ **Artifact Registry** - Modern container storage
- ✅ **Environment Variables** - Secrets not in code

## 👥 Multi-User Teams

| Username | Team | Use Case |
|----------|------|----------|
| `risk_team_user` | Risk Team | Risk analysis & assessment |
| `credit_team_user` | Credit Team | Credit scoring & decisions |
| `collection_team_user` | Collection Team | Collections & recovery |

## 🏗️ Architecture

### Tech Stack
- **Frontend:** Streamlit
- **Backend:** Google ADK (Agent Development Kit)
- **LLM:** Google Gemini 2.5 Pro/Flash
- **Database:** BigQuery
- **Deployment:** Cloud Run
- **Container:** Docker (Python 3.12 slim)

### Agent Hierarchy
```
User Query
    ↓
Intent Router Agent (determines domain)
    ↓
Domain Agent (Collections/Sourcing/Disbursal)
    ↓
Query Execution Agent (validates & executes)
    ↓
Results + Metadata
```

## 📊 Features

### Core Capabilities
- Natural language to SQL conversion
- Domain-specific query optimization
- Multi-table join support
- Aggregation and filtering
- Date range queries
- Top-N queries

### Enterprise Features
- Multi-user authentication
- Audit logging to BigQuery
- Query safety validation
- Token usage tracking
- Error handling & retry logic
- Structured JSON logging

## 🔧 Configuration

Key environment variables (in `.env`):

```bash
# GCP Configuration
GCP_PROJECT_ID=analytics-datapipeline-prod
GCP_REGION=asia-south1

# BigQuery
BIGQUERY_DATASET=aiml_cj_nostd_mart
BIGQUERY_LOCATION=asia-south1

# Gemini Models
GEMINI_PRO_MODEL=gemini-2.5-pro
GEMINI_FLASH_MODEL=gemini-2.5-flash

# Authentication (set via deployment)
USER_CREDENTIALS='[{"username": "...", "password_hash": "..."}]'
```

## 🧪 Testing

### Run Stress Tests
```bash
python scripts/final_stress_testing.py --runs 10 --questions data/golden_question_bank.csv
```

### Run Unit Tests
```bash
pytest tests/
```

### Generate Multi-User Credentials
```bash
python scripts/generate_multi_user_creds.py
```

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [`docs/QUICK_START.md`](docs/QUICK_START.md) | 3-step deployment guide |
| [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) | Comprehensive deployment docs |
| [`docs/SECURITY_GUIDE.md`](docs/SECURITY_GUIDE.md) | Security best practices |
| [`docs/MULTI_USER_SETUP.md`](docs/MULTI_USER_SETUP.md) | Multi-user auth setup |
| [`docs/DEPLOYMENT_CHECKLIST.md`](docs/DEPLOYMENT_CHECKLIST.md) | Pre-deployment checks |

## 🛠️ Development

### Adding a New Domain Agent
1. Create agent in `agents/domain/new_domain_agent.py`
2. Register in `agents/intent/router_agent.py`
3. Update schema in `agents/schema/schema_service.py`
4. Add tests in `tests/`

### Adding New Users
1. Generate hash: `python scripts/generate_multi_user_creds.py`
2. Update `USER_CREDENTIALS` in `deployment/deploy.bat`
3. Redeploy: `cd deployment && deploy.bat`

## 📈 Monitoring

### View Logs
```bash
gcloud run services logs read orion-copilot --region=asia-south1 --follow
```

### Check Metrics
```bash
gcloud run services describe orion-copilot --region=asia-south1
```

### Query Audit Logs (BigQuery)
```sql
SELECT * FROM `analytics-datapipeline-prod.aiml_cj_nostd_mart.adk_copilot_logs`
WHERE DATE(timestamp) = CURRENT_DATE()
ORDER BY timestamp DESC
```

## 🤝 Contributing

1. Create feature branch
2. Make changes
3. Run tests: `pytest tests/`
4. Update documentation
5. Submit for review

## 📝 License

Internal use only - Analytics Data Pipeline Production

## 🆘 Support

- **Issues:** Check `docs/` for troubleshooting
- **Security:** See `docs/SECURITY_GUIDE.md`
- **Deployment:** See `docs/DEPLOYMENT_CHECKLIST.md`

## 🎯 Roadmap

- [ ] Role-based access control (RBAC)
- [ ] Query history search
- [ ] Saved query templates
- [ ] Custom dashboard builder
- [ ] Multi-factor authentication (MFA)
- [ ] Session timeout policies

---

**Last Updated:** December 2, 2025  
**Version:** 2.0.0 (Multi-User Secure Edition)  
**Status:** ✅ Production Ready
