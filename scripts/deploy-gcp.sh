#!/usr/bin/env bash
# Deploy ATOM Application Container to Google Cloud Run
# Jira Issue: ATOM-GCP-101
# Xray Test Entity: ATOM-T104
set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${2:-us-central1}"
SERVICE_NAME="${3:-atom-etl-framework}"

if ! command -v gcloud &> /dev/null; then
  echo "Error: gcloud CLI not found. Please install the Google Cloud SDK and run 'gcloud auth login'." >&2
  exit 1
fi

if [[ -z "$PROJECT_ID" ]]; then
  echo "Error: No active GCP project. Usage: ./scripts/deploy-gcp.sh <PROJECT_ID> [REGION] [SERVICE_NAME]" >&2
  exit 1
fi

echo "=========================================================="
echo " Deploying ATOM Container to Google Cloud Run"
echo " Project:  $PROJECT_ID"
echo " Region:   $REGION"
echo " Service:  $SERVICE_NAME"
echo "=========================================================="

echo "[1/3] Enabling Google Cloud APIs..."
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com --project "$PROJECT_ID"

echo "[2/3] Building container image and deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --project "$PROJECT_ID"

echo "[3/3] Deployment complete!"
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format "value(status.url)")
echo "Application URL: $SERVICE_URL"
