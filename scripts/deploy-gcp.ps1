# Deploy ATOM Application Container to Google Cloud Run
# Jira Issue: ATOM-GCP-101
# Xray Test Entity: ATOM-T104
param(
    [Parameter(Mandatory=$false)]
    [string]$ProjectId,

    [Parameter(Mandatory=$false)]
    [string]$Region = "us-central1",

    [Parameter(Mandatory=$false)]
    [string]$ServiceName = "atom-etl-framework"
)

$ErrorActionPreference = "Stop"

# Step 0: Preflight verification
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Google Cloud SDK ('gcloud') is not found in PATH." -ForegroundColor Red
    Write-Host "Please install the Google Cloud CLI:" -ForegroundColor Yellow
    Write-Host "  winget install Google.CloudSDK"
    Write-Host "  or download from: https://cloud.google.com/sdk/docs/install"
    Write-Host "Then authenticate:"
    Write-Host "  gcloud auth login"
    Write-Host "  gcloud auth application-default login"
    exit 1
}

# Resolve active project if not explicitly supplied
if (-not $ProjectId) {
    $ProjectId = (gcloud config get-value project 2>$null).Trim()
    if (-not $ProjectId) {
        Write-Host "[ERROR] No GCP Project configured." -ForegroundColor Red
        Write-Host "Please provide -ProjectId <YOUR_PROJECT_ID> or run: gcloud config set project <YOUR_PROJECT_ID>" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Deploying ATOM Container to Google Cloud Run" -ForegroundColor Cyan
Write-Host " Project:  $ProjectId" -ForegroundColor Cyan
Write-Host " Region:   $Region" -ForegroundColor Cyan
Write-Host " Service:  $ServiceName" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Enable required GCP services
Write-Host "`n[1/3] Enabling Google Cloud APIs (Cloud Run, Artifact Registry, Cloud Build)..." -ForegroundColor Yellow
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com --project $ProjectId

# 2. Deploy directly via Cloud Run source-to-container build
Write-Host "`n[2/3] Building container image and deploying to Cloud Run..." -ForegroundColor Yellow
gcloud run deploy $ServiceName `
    --source . `
    --region $Region `
    --platform managed `
    --allow-unauthenticated `
    --port 8080 `
    --project $ProjectId

Write-Host "`n[3/3] Cloud Run Deployment Complete!" -ForegroundColor Green
$ServiceUrl = (gcloud run services describe $ServiceName --region $Region --project $ProjectId --format "value(status.url)")
Write-Host "Application is live at: $ServiceUrl" -ForegroundColor Green
