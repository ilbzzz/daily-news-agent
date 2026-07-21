# Gemini Enterprise Setup & Deployment Guide: Daily Top News Summary AI Agent

This step-by-step guide details how to build, test, deploy via **Agent Runtime
(Vertex AI Reasoning Engine)**, and register the **Daily Top News Summary AI
Agent** on Google Cloud Platform (GCP) and **Gemini Enterprise Agent Platform**.

--------------------------------------------------------------------------------

## 1. Prerequisites & Environment Setup

### Required Tools

Ensure you have the following installed on your machine:

-   **Python 3.10+**
-   **Google Cloud SDK (`gcloud`)**
-   **Terraform (>= 1.0.0)**
-   **`uv`** (recommended, install via `curl -LsSf
    https://astral.sh/uv/install.sh | sh`)
-   **`agents-cli`** (`uv tool install google-agents-cli`)

### Required GCP IAM Permissions

-   `roles/aiplatform.admin` (Vertex AI / Agent Runtime Administration)
-   `roles/datastore.owner` (Firestore Database Administration)
-   `roles/secretmanager.admin` (Secret Manager Administration)
-   `roles/cloudscheduler.admin` (Cloud Scheduler Job Creation)
-   `roles/discoveryengine.admin` or `roles/discoveryengine.editor` (Gemini
    Enterprise Registration)

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

### Configure Service Agent IAM Roles

The Reasoning Engine and Custom Code Service Agents require access to Firestore and Secret Manager to run database operations and retrieve configs at runtime.

Run the following commands in your terminal:

```bash
# 1. Set your Project ID and Project Number
export PROJECT_ID="your-gcp-project-id"
export PROJECT_NUMBER="your-gcp-project-number"

# 2. Grant Firestore permissions to the Service Agents
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-cc.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/datastore.user"

# 3. Grant Secret Manager access to the Reasoning Engine Service Agent
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

--------------------------------------------------------------------------------

## 2. Local Setup & Building

### Step 2.1: Clone and Environment Setup

```bash
# Clone the repository and navigate into the project directory
git clone https://github.com/ilbzzz/daily-news-agent.git
cd daily-news-agent

# Create Python Virtual Environment
uv venv
source .venv/bin/activate

# Install Project Dependencies
uv pip install -e .

```

Set local dry-run environment variables to run tests without triggering actual
SendGrid emails:

```bash
# Run Unit Test Suite
DRY_RUN_MODE=true uv run python -m unittest discover -s tests/unit
```

--------------------------------------------------------------------------------

## 3. Deployment Architectures & Terraform Setup

You can deploy the agent in one of two ways depending on your production
requirements:

### Option A: Managed Agent Runtime (Vertex AI Reasoning Engine) - RECOMMENDED

-   **Architecture**: The conversational agent is hosted fully managed in Vertex
    AI Agent Runtime. Conversational registration queries go through Vertex AI.
-   **Triggering the Cron**: Since the Reasoning Engine does not expose direct
    HTTP endpoints, Cloud Scheduler is configured to invoke the Vertex AI
    `predict` API endpoint using Google OAuth authentication. The payload
    instructs the agent to trigger the daily news pipeline tool.

#### Terraform Configuration for Option A:

Configure `terraform/terraform.tfvars` with:

```hcl
gcp_project = "your-gcp-project-id"
gcp_region  = "us-central1"
# For Option A, you will need to update the scheduler job in main.tf manually to target the reasoning engine API after deployment (see Step 4.5).
```

### Option B: Cloud Run (FastAPI Web Service)

-   **Architecture**: The FastAPI app is built as a Docker container and
    deployed to Cloud Run. It exposes standard A2A endpoints and a dedicated
    `/run-pipeline` POST endpoint.
-   **Triggering the Cron**: Cloud Scheduler triggers the `/run-pipeline`
    endpoint directly on the Cloud Run URL using OIDC authentication.

#### Terraform Configuration for Option B:

Configure `terraform/terraform.tfvars` with:

```hcl
gcp_project        = "your-gcp-project-id"
gcp_region         = "us-central1"
cloud_run_endpoint = "https://your-cloud-run-url-after-deploy.a.run.app/run-pipeline"
```

### Step 3.2: Initialize and Apply Infrastructure

> **Cloud Shell Note**: If running in Cloud Shell, export your OAuth2 token
> first to bypass Cloud Shell metadata daemon timeouts: `export
> GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)`

```bash
cd terraform
terraform init
terraform plan
# For Option A, you can run apply first to set up Secret Manager and Firestore, and defer Scheduler setup, or create a dummy job and update it later.
terraform apply -auto-approve
cd ..
```

--------------------------------------------------------------------------------

## 4. Deploying to GCP

### Step 4.1: Store SendGrid API Key in Secret Manager

```bash
echo -n "SG.your_actual_sendgrid_api_key" | gcloud secrets versions add sendgrid-api-key --data-file=-
```

*(If testing without a SendGrid account, pass a placeholder string like
`SG.dummy_key` and set `DRY_RUN_MODE=true` in the environment).*

### Step 4.2: Ensure `.gcloudignore` Excludes Heavy Build Artifacts

Ensure `.gcloudignore` and `.gitignore` exist in the repository so `.venv`,
`__pycache__`, and build caches are not packaged into the deployment payload
(which must be < 8 MB):

```bash
# Verify ignore files are present
ls -a .gcloudignore .gitignore
```

### Step 4.3: Deployment Execution

#### Deploying to Managed Agent Runtime (Option A)

The agent dynamically retrieves the SendGrid API key from Secret Manager at runtime, so you do not need to pass it during deployment. However, you must configure `DRY_RUN_MODE=false` and specify your verified sender email address:

```bash
agents-cli deploy \
  --project ${PROJECT_ID} \
  --region us-central1 \
  --deployment-target agent_runtime \
  --update-env-vars DRY_RUN_MODE=false,SENDGRID_SENDER_EMAIL="your_verified_sender@domain.com" \
  --no-confirm-project \
  --no-wait
```

#### Deploying to Cloud Run (Option B)

```bash
agents-cli deploy \
  --project ${PROJECT_ID} \
  --region us-central1 \
  --deployment-target cloud_run \
  --update-env-vars DRY_RUN_MODE=false,SENDGRID_SENDER_EMAIL="your_verified_sender@domain.com" \
  --no-confirm-project \
  --no-wait
```

### Step 4.4: Verify Deployment Status

Check the status of your background operation:

```bash
# Check status (replace deployment target as appropriate)
agents-cli deploy --status --deployment-target agent_runtime
```

Once deployment completes, `agents-cli` auto-generates
`deployment_metadata.json`.

-   For **agent_runtime**, it contains `remote_agent_runtime_id` (e.g.
    `projects/${PROJECT_NUMBER}/locations/us-central1/reasoningEngines/1966737680288972800`).
-   For **cloud_run**, it contains the assigned Cloud Run URL (e.g.,
    `https://daily-news-agent-runner.a.run.app`).

### Step 4.5: Update Cloud Scheduler (Option A only)

If you deployed to **Agent Runtime**, update your Cloud Scheduler job to invoke
the Reasoning Engine predict API:

-   **Target URL**:
    `https://us-central1-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/us-central1/reasoningEngines/${REASONING_ENGINE_ID}:predict`
-   **Method**: `POST`
-   **Body**:

    ```json
    {
      "instances": [
        {
          "input": "Run daily news digest pipeline for all users"
        }
      ]
    }
    ```

-   **Auth**: Use OAuth Token (with a service account that has
    `roles/aiplatform.user` permission on the project).

--------------------------------------------------------------------------------

## 5. Registering with Gemini Enterprise Agent Platform

Depending on your deployment target, register the agent using the corresponding
command:

### Publishing Option A (Agent Runtime)

```bash
# Extract the Agent Runtime ID from deployment_metadata.json
export AGENT_RUNTIME_ID=$(jq -r '.remote_agent_runtime_id' deployment_metadata.json)

agents-cli publish gemini-enterprise \
  --registration-type adk \
  --agent-runtime-id ${AGENT_RUNTIME_ID} \
  --gemini-enterprise-app-id projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/daily-news-app_1784599201724 \
  --display-name "Daily Top News Summary AI Agent"
```

### Publishing Option B (Cloud Run)

```bash
# Extract the Cloud Run URL from deployment_metadata.json
export CLOUD_RUN_URL=$(jq -r '.url' deployment_metadata.json)

agents-cli publish gemini-enterprise \
  --registration-type a2a \
  --agent-uri ${CLOUD_RUN_URL} \
  --gemini-enterprise-app-id projects/${PROJECT_ID}/locations/global/collections/default_collection/engines/daily-news-app_1784599201724 \
  --display-name "Daily Top News Summary AI Agent"
```

--------------------------------------------------------------------------------

## 6. End-to-End Verification & Operation

### Step 6.1: Register a Test User

Register a test profile in Firestore:

```bash
uv run python -c "
from app.agent import UserOnboardingAgent
from google.cloud import firestore

db = firestore.Client()
agent = UserOnboardingAgent(db_client=db)
res = agent.register_user(
    email='testuser@example.com',
    topic='Artificial Intelligence',
    timezone_raw='America/New_York'
)
print(res)
"
```

--------------------------------------------------------------------------------

## 7. Troubleshooting

Issue                                                       | Cause                                                   | Solution
:---------------------------------------------------------- | :------------------------------------------------------ | :-------
**`Request payload size exceeds the limit: 8388608 bytes`** | Unfiltered virtualenvs or caches packaged during deploy | Create `.gcloudignore` and exclude `venv/`, `.venv/`, `__pycache__/`, `.terraform/`.
**`google.auth.exceptions.RefreshError` in Cloud Shell**    | Cloud Shell metadata server token refresh failure       | Run `gcloud auth application-default login` or `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)`.
**`No deployment target configured` on `--status`**         | Missing `--deployment-target` flag                      | Pass `--deployment-target agent_runtime` when checking status.
**SendGrid 401 Unauthorized**                               | Invalid API Key                                         | Verify key in Secret Manager or set `DRY_RUN_MODE=true` in environment configuration.
**Stale Lock Stuck Records**                                | Unhandled pipeline failure                              | Pipelines automatically clean up records stuck > 30 mins via `cleanup_stale_locks()`.
**Gemini Enterprise Registration Error**                    | Missing Discovery Engine Editor                         | Verify user has `roles/discoveryengine.editor` or `roles/discoveryengine.admin` permissions in GCP Console.
