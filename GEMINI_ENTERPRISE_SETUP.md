# Gemini Enterprise Setup & Deployment Guide: Daily Top News Summary AI Agent

This step-by-step guide details how to build, test, deploy, and register the
**Daily Top News Summary AI Agent** on Google Cloud Platform (GCP) and **Gemini
Enterprise Agent Platform**.

--------------------------------------------------------------------------------

## 1. Prerequisites & Environment Setup

### Required Tools

Ensure you have the following installed on your machine:

- **Python 3.9+**
- **Google Cloud SDK (`gcloud`)**
- **Terraform (>= 1.0.0)**
- **`agents-cli`** (`uv tool install google-agents-cli` or `pip install google-agents-cli`)

### Required GCP IAM Permissions

- `roles/run.admin` (Cloud Run Service Administration)
- `roles/datastore.owner` (Firestore Database Administration)
- `roles/secretmanager.admin` (Secret Manager Administration)
- `roles/cloudscheduler.admin` (Cloud Scheduler Job Creation)
- `roles/discoveryengine.admin` or `roles/discoveryengine.editor` (Gemini Enterprise Registration)

### Enable GCP APIs & Initialize Database

Execute the following `gcloud` commands to enable required services, initialize database locations, and grant Cloud Build permissions:

```bash
# Enable required GCP APIs
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  discoveryengine.googleapis.com \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

# Create Default Firestore Database (if not already initialized in project)
gcloud firestore databases create --location=us-central1 --type=firestore-native

# Initialize App Engine / Cloud Scheduler project location (required once per GCP project)
gcloud app create --region=us-central1 || true

# Grant required Cloud Build permissions to default compute service account
export PROJECT_NUMBER=$(gcloud projects describe your-gcp-project-id --format="value(projectNumber)")

gcloud projects add-iam-policy-binding your-gcp-project-id \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/storage.admin"

gcloud projects add-iam-policy-binding your-gcp-project-id \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder"

gcloud projects add-iam-policy-binding your-gcp-project-id \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding your-gcp-project-id \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding your-gcp-project-id \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

--------------------------------------------------------------------------------

## 2. Local Setup & Building

### Step 2.1: Clone and Environment Setup

```bash
# Clone the repository and navigate into the project directory
git clone <your-github-repo-url>/daily-news-agent.git
cd daily-news-agent

# Create Python Virtual Environment
python3 -m venv venv
source venv/bin/activate

# Install Project Dependencies
pip install -e .[dev]
```

### Step 2.2: Local Configuration & Unit Testing

Set local dry-run environment variables to run tests without triggering actual SendGrid emails:

```bash
export DRY_RUN_MODE=true
export GCP_PROJECT="your-gcp-project-id"

# Run Unit Test Suite
python3 -m unittest discover -s tests/unit -p "test_*.py"
```

--------------------------------------------------------------------------------

## 3. Infrastructure Provisioning via Terraform

Provision the Secret Manager key, Firestore composite indexes (`user_due_index`, `stale_lock_index`), Cloud Scheduler service account, and Cloud Scheduler trigger using Terraform.

### Step 3.1: Configure Variables

Create a `terraform/terraform.tfvars` file by copying the template [`terraform/terraform.tfvars.example`](terraform/terraform.tfvars.example):

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Configure your project-specific values in `terraform/terraform.tfvars`:

```hcl
gcp_project                       = "your-gcp-project-id"
gcp_region                        = "us-central1"
cloud_run_endpoint                = "https://daily-news-agent-runner-xyz.a.run.app/run-pipeline"
```

### Step 3.2: Initialize and Apply Infrastructure

> **Cloud Shell Note**: If running in Cloud Shell, export your OAuth2 token first to bypass Cloud Shell metadata daemon timeouts:
> `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)`

```bash
cd terraform
terraform init
terraform plan
terraform apply -auto-approve
cd ..
```

--------------------------------------------------------------------------------

## 4. Deploying the Service to Cloud Run

### Step 4.1: Store SendGrid API Key in Secret Manager

```bash
echo -n "SG.your_actual_sendgrid_api_key" | gcloud secrets versions add sendgrid-api-key --data-file=-
```

*(If testing without a SendGrid account, pass a placeholder string like `SG.dummy_key` and set `DRY_RUN_MODE=true` in Cloud Run).*

### Step 4.2: Build and Deploy to Cloud Run

Deploy the application web service to Cloud Run. Cloud Run automatically uses the included [`Dockerfile`](Dockerfile) to build the container:

```bash
gcloud run deploy daily-news-agent-runner \
  --source . \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars DRY_RUN_MODE=false,FIRESTORE_COLLECTION=users,GCP_PROJECT=your-gcp-project-id,SENDGRID_SENDER_EMAIL=digest@newsagent.ai \
  --set-secrets SENDGRID_API_KEY=sendgrid-api-key:latest
```

### Step 4.3: Verify Deployment Endpoints

Test the Cloud Run health check and trigger endpoints:

```bash
# Health Check Endpoint
curl -X GET https://daily-news-agent-runner-xyz.a.run.app/healthz

# Response:
# {"status": "healthy", "service": "daily_news_agent"}
```

--------------------------------------------------------------------------------

## 5. Registering with Gemini Enterprise Agent Platform

### Step 5.1: Grant Discovery Engine Invoker IAM Permissions

Grant the Gemini Enterprise Discovery Engine service account permission to invoke the Cloud Run service:

```bash
export PROJECT_NUMBER=$(gcloud projects describe your-gcp-project-id --format="value(projectNumber)")

gcloud run services add-iam-policy-binding daily-news-agent-runner \
  --region us-central1 \
  --member "serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role "roles/run.servicesInvoker"
```

### Step 5.2: Publish to Gemini Enterprise via `agents-cli`

Publish the agent to your Gemini Enterprise App:

#### Option A: ADK Reasoning Engine Mode (Default)

```bash
agents-cli publish gemini-enterprise \
  --gemini-enterprise-app-id projects/your-gcp-project-id/locations/global/collections/default_collection/engines/daily-news-app \
  --display-name "Daily Top News Summary AI Agent" \
  --description "Personalized daily news digest agent delivering 8:00 AM summaries." \
  --tool-description "Searches verified news, synthesizes takeaways, and dispatches HTML email digests."
```

#### Option B: A2A (Agent-to-Agent) Mode on Cloud Run

```bash
agents-cli publish gemini-enterprise \
  --registration-type a2a \
  --agent-card-url https://daily-news-agent-runner-xyz.a.run.app/.well-known/agent-card.json \
  --gemini-enterprise-app-id projects/your-gcp-project-id/locations/global/collections/default_collection/engines/daily-news-app \
  --display-name "Daily Top News Summary AI Agent (A2A)"
```

--------------------------------------------------------------------------------

## 6. End-to-End Verification & Operation

### Step 6.1: Register a Test User

Register a test profile in Firestore:

```python
from app.agent import UserOnboardingAgent
from google.cloud import firestore

db = firestore.Client()
agent = UserOnboardingAgent(db_client=db)
agent.register_user(
    email="testuser@example.com",
    topic="Artificial Intelligence",
    timezone_raw="America/New_York"
)
```

### Step 6.2: Trigger Manual Pipeline Run

Trigger the pipeline POST endpoint to verify search, HTML rendering, and email dispatch:

```bash
curl -X POST https://daily-news-agent-runner-xyz.a.run.app/run-pipeline
```

--------------------------------------------------------------------------------

## 7. Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| **`invalid token JSON from metadata: EOF`** | Cloud Shell metadata server token cache expired | Run `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)` in Cloud Shell. |
| **Cloud Build Permission Denied** | Compute SA missing IAM builder roles | Grant `storage.admin`, `cloudbuild.builds.builder`, `artifactregistry.writer`, and `logging.logWriter` to `${PROJECT_NUMBER}-compute@developer.gserviceaccount.com`. |
| **HTTP 403 on Cloud Scheduler Trigger** | Missing IAM OIDC permissions | Ensure `roles/run.invoker` is granted to `daily-news-scheduler@<project-id>.iam.gserviceaccount.com`. |
| **SendGrid 401 Unauthorized** | Invalid API Key | Verify key in Secret Manager or set `DRY_RUN_MODE=true` in Cloud Run env vars. |
| **Stale Lock Stuck Records** | Cloud Run crash/timeout | Pipelines automatically clean up records stuck > 30 mins via `cleanup_stale_locks()`. |
| **Gemini Enterprise Registration Error** | Missing Discovery Engine Editor | Verify user has `roles/discoveryengine.editor` permissions in GCP Console. |
