"""
Jira Issue: ATOM-GCP-101
Xray Key: ATOM-T101
Test Plan: ATOM-TP-GCP-01
Test Cycle: ATOM-TC-GCP-01
Traceability Matrix: REQ-GCP-DEPLOY -> ATOM-GCP-101 -> ATOM-T101

BDD Scenario:
  Given the ETL framework application repository
  When the application container Dockerfile and Cloud Build specification are inspected
  Then the container must be configured for Google Cloud Run with dynamic PORT binding,
       production dependencies, and a health probe endpoint.
"""

from pathlib import Path
import re
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_production_dockerfile_exists_and_configured():
    """
    @jira: ATOM-GCP-101
    @xray: ATOM-T101
    Verify production Dockerfile exists with Cloud Run compatibility.
    """
    dockerfile_path = REPO_ROOT / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile must exist in repo root"

    content = dockerfile_path.read_text(encoding="utf-8")
    assert "FROM python:3.12" in content
    assert "requirements.txt" in content
    assert "uvicorn api.main:app" in content
    # Google Cloud Run injects $PORT (default 8080)
    assert "PORT" in content or "8080" in content


def test_cloudbuild_configuration_valid():
    """
    @jira: ATOM-GCP-101
    @xray: ATOM-T102
    Verify cloudbuild.yaml exists and defines build & deploy steps for Cloud Run.
    """
    cloudbuild_path = REPO_ROOT / "cloudbuild.yaml"
    assert cloudbuild_path.exists(), "cloudbuild.yaml must exist in repo root"

    content = yaml.safe_load(cloudbuild_path.read_text(encoding="utf-8"))
    assert "steps" in content
    steps = content["steps"]
    assert len(steps) >= 1
    # Check that a step uses gcr.io/cloud-builders/docker or gcloud run deploy
    step_names = [s.get("name", "") for s in steps]
    assert any("docker" in name or "gcloud" in name for name in step_names)


def test_dockerignore_excludes_unneeded_artifacts():
    """
    @jira: ATOM-GCP-101
    @xray: ATOM-T103
    Verify .dockerignore excludes heavy dev caches and test results.
    """
    dockerignore_path = REPO_ROOT / ".dockerignore"
    assert dockerignore_path.exists()
    content = dockerignore_path.read_text(encoding="utf-8")
    assert "node_modules" in content
    assert ".git" in content
    assert ".pytest_cache" in content
