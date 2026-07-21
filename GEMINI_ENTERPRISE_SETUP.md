# Gemini Enterprise Setup & Deployment Guide: Daily Top News Summary AI Agent

This step-by-step guide details how to build, test, deploy via **Agent Runtime (Vertex AI Reasoning Engine)**, and register the **Daily Top News Summary AI Agent** on Google Cloud Platform (GCP) and **Gemini Enterprise Agent Platform**.

--------------------------------------------------------------------------------

## 1. Prerequisites & Environment Setup

### Required Tools

Ensure you have the following installed on your machine:

- **Python 3.9+**
- **Google Cloud SDK (`gcloud`)**
- **Terraform (>= 1.0.0)**
- **`agents-cli`** (`uv tool install google-agents-cli` or `pip install google-agents-cli`)

### Required GCP IAM Permissions

- `roles/aiplatform.admin` (Vertex AI / Agent Runtime Administration)
- `roles/datastore.owner` (Firestore Database Administration)
- `roles/secretmanager.admin` (Secret Manager Administration)
- `roles/cloudscheduler.admin` (Cloud Scheduler Job Creation)
- `roles/discoveryengine.admin` or `roles/discoveryengine.editor` (Gemini Enterprise Registration)

### Enable GCP APIs & Initialize Database

Set your GCP Project ID environment variable and execute the setup commands:

```bash
# Set your GCP Project ID environment variable
export PROJECT_ID="your-gcp-project-id"

# Enable required GCP APIs
gcloud services enable \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  discoveryengine.googleapis.com

# Create Default Firestore Database (if not already initialized in project)
gcloud firestore databases create --location=us-central1 --type=firestore-native
```

--------------------------------------------------------------------------------

## 2. Local Setup & Building

### Step 2.1: Clone and Environment Setup

```bash
# Clone the repository and navigate into the project directory
git clone https://github.com/ilbzzz/daily-news-agent.git
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
export GCP_PROJECT="${PROJECT_ID}"

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
gcp_project = "your-gcp-project-id" # Set your actual GCP Project ID
gcp_region  = "us-central1"
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

## 4. Deploying to Agent Runtime (Vertex AI Reasoning Engine)

### Step 4.1: Store SendGrid API Key in Secret Manager

```bash
echo -n "SG.your_actual_sendgrid_api_key" | gcloud secrets versions add sendgrid-api-key --data-file=-
```

*(If testing without a SendGrid account, pass a placeholder string like `SG.dummy_key` and set `DRY_RUN_MODE=true`).*

### Step 4.2: Ensure `.gcloudignore` Excludes Heavy Build Artifacts

Ensure `.gcloudignore` and `.gitignore` exist in the repository so `.venv`, `__pycache__`, and build caches are not packaged into the deployment payload (which must be < 8 MB):

```bash
# Verify ignore files are present
ls -a .gcloudignore .gitignore
```

### Step 4.3: Deploy to Agent Runtime via `agents-cli`

Deploy the source code directly to Vertex AI Agent Runtime:

```bash
agents-cli deploy \
  --project ${PROJECT_ID} \
  --region us-central1 \
  --deployment-target agent_runtime \
  --no-confirm-project \
  --no-wait
```

### Step 4.4: Verify Deployment Status

Check the status of your background operation:

> **Note for Cloud Shell**: Run `gcloud auth application-default login` if needed to authenticate Application Default Credentials.

```bash
agents-cli deploy --status --deployment-target agent_runtime
```

Once deployment completes, `agents-cli` auto-generates `deployment_metadata.json` containing the assigned `remote_agent_runtime_id` (e.g. `projects/${PROJECT_NUMBER}/locations/us-central1/reasoningEngines/1966737680288972800`).

--------------------------------------------------------------------------------

## 5. Registering with Gemini Enterprise Agent Platform

### Step 5.1: Publish to Gemini Enterprise via `agents-cli`

Publish your Agent Runtime Reasoning Engine deployment to your Gemini Enterprise App:

```bash
# Extract the Agent Runtime ID from deployment_metadata.json
export AGENT_RUNTIME_ID=$(jq -r '.remote_agent_runtime_id' deployment_metadata.json)

agents-cli publish gemini-enterprise \
  --registration-type adk \
  --agent-runtime-id ${AGENT_RUNTIME_ID} \
  --gemini-enterprise-app-id projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/daily-news-app_1784599201724 \
  --display-name "Daily Top News Summary AI Agent"
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

--------------------------------------------------------------------------------

## 7. Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| **`Request payload size exceeds the limit: 8388608 bytes`** | Unfiltered virtualenvs or caches packaged during deploy | Create `.gcloudignore` and exclude `venv/`, `.venv/`, `__pycache__/`, `.terraform/`. |
| **`google.auth.exceptions.RefreshError` in Cloud Shell** | Cloud Shell metadata server token refresh failure | Run `gcloud auth application-default login` or `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)`. |
| **`No deployment target configured` on `--status`** | Missing `--deployment-target` flag | Pass `--deployment-target agent_runtime` when checking status. |
| **SendGrid 401 Unauthorized** | Invalid API Key | Verify key in Secret Manager or set `DRY_RUN_MODE=true` in environment configuration. |
| **Stale Lock Stuck Records** | Unhandled pipeline failure | Pipelines automatically clean up records stuck > 30 mins via `cleanup_stale_locks()`. |
| **Gemini Enterprise Registration Error** | Missing Discovery Engine Editor | Verify user has `roles/discoveryengine.editor` or `roles/discoveryengine.admin` permissions in GCP Console. |
