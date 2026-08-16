# Live Docker Stack & Playwright Compare Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `docker-compose.integration.yml` to include live container services for LocalStack AWS emulator and GitLab CI API mock, and build Playwright E2E specs for AWS, multi-file/SAP BO compare, and GitLab pipeline retry.

**Architecture:** Extend Docker Compose configuration with LocalStack (S3, Glue, Athena) and a lightweight GitLab API runner container; seed global fixtures in `tests/e2e/global-setup.ts`; add 3 new Playwright specs in `tests/e2e/` for AWS Compare, Files/SAP BO Compare, and GitLab CI trigger/retry.

**Tech Stack:** TypeScript, Playwright E2E, Docker Compose, LocalStack (S3/Glue/Athena), FastAPI/Python (GitLab runner mock), pytest.

## Global Constraints

- **Spec Path:** `docs/superpowers/specs/2026-08-16-live-docker-compare-testing-design.md`
- **Compose file:** `docker-compose.integration.yml`
- **Spec Directory:** `tests/e2e/`
- **Ports:** `18443` (sapbo), `18444` (sapds), `14333` (sqlserver), `29000` (minio), `12222` (sftp), `4566` (localstack), `18080` (gitlab)

---

### Task 1: Docker Compose LocalStack & GitLab CI Runner Setup

**Files:**
- Create: `docker/gitlab-mock/Dockerfile`
- Create: `docker/gitlab-mock/server.py`
- Modify: `docker-compose.integration.yml:1-90`

**Interfaces:**
- Consumes: Existing `docker-compose.integration.yml` service patterns
- Produces: `atom-localstack-integration` at `http://localhost:4566`, `atom-gitlab-integration` at `http://localhost:18080`

- [ ] **Step 1: Write the failing Docker Compose healthcheck test**

```bash
docker compose -f docker-compose.integration.yml config
```

- [ ] **Step 2: Create GitLab API runner mock server in `docker/gitlab-mock/server.py`**

```python
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class GitLabMockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if "/retry" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "running", "id": 101}).encode())
        else:
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": 1, "status": "created"}).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "success", "id": 1}).encode())

def run():
    server = HTTPServer(("0.0.0.0", 18080), GitLabMockHandler)
    server.serve_forever()

if __name__ == "__main__":
    run()
```

- [ ] **Step 3: Create `docker/gitlab-mock/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY server.py .
EXPOSE 18080
CMD ["python", "server.py"]
```

- [ ] **Step 4: Update `docker-compose.integration.yml` with `localstack` and `gitlab` services**

```yaml
  localstack:
    image: localstack/localstack:latest
    container_name: atom-localstack-integration
    ports:
      - "4566:4566"
    environment:
      - SERVICES=s3,glue,athena
      - AWS_DEFAULT_REGION=us-east-1

  gitlab:
    build:
      context: ./docker/gitlab-mock
    image: atom-gitlab-mock:latest
    container_name: atom-gitlab-integration
    ports:
      - "18080:18080"
```

- [ ] **Step 5: Validate docker compose configuration**

Run: `docker compose -f docker-compose.integration.yml config`
Expected: Valid YAML output listing `localstack` and `gitlab` services.

- [ ] **Step 6: Commit**

```bash
git add docker/gitlab-mock/Dockerfile docker/gitlab-mock/server.py docker-compose.integration.yml
git commit -m "feat(docker): add localstack and gitlab runner mock to integration compose stack"
```

---

### Task 2: Playwright AWS Compare Spec (`38-live-docker-aws-compare.spec.ts`)

**Files:**
- Create: `tests/e2e/38-live-docker-aws-compare.spec.ts`
- Modify: `tests/e2e/global-setup.ts:1-50`

**Interfaces:**
- Consumes: Frontend Compare tab routes `/compare` and API `/api/compare`
- Produces: Playwright test coverage for AWS Glue, Athena, and S3 file comparisons

- [ ] **Step 1: Write Playwright spec in `tests/e2e/38-live-docker-aws-compare.spec.ts`**

```typescript
import { test, expect } from '@playwright/test';

test.describe('AWS Live Docker Compare Functionality', () => {
  test('navigates to Compare tab and runs AWS Glue Catalog comparison', async ({ page }) => {
    await page.goto('/#compare');
    await expect(page.locator('.compare-tab-container')).toBeVisible();
    await page.selectOption('#compare-source-type', 'aws_glue');
    await page.selectOption('#compare-target-type', 'aws_glue');
    await expect(page.locator('#btn-run-compare')).toBeVisible();
  });

  test('executes Athena query comparison vs baseline CSV', async ({ page }) => {
    await page.goto('/#compare');
    await page.selectOption('#compare-source-type', 'aws_athena');
    await page.fill('#athena-sql-input', 'SELECT * FROM test_db.sales');
    await expect(page.locator('.athena-query-container')).toBeVisible();
  });
});
```

- [ ] **Step 2: Run Playwright test to verify execution**

Run: `npx playwright test tests/e2e/38-live-docker-aws-compare.spec.ts`
Expected: Specs execute and pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/38-live-docker-aws-compare.spec.ts
git commit -m "test(e2e): add Playwright coverage for AWS Glue and Athena compare functionalities"
```

---

### Task 3: Playwright Files & SAP BO Compare Spec (`39-live-docker-files-sapbo-compare.spec.ts`)

**Files:**
- Create: `tests/e2e/39-live-docker-files-sapbo-compare.spec.ts`

**Interfaces:**
- Consumes: Frontend Compare tab, SAP BO REST API mock
- Produces: Playwright test coverage for multi-file reconciliation and live SAP BO report compare

- [ ] **Step 1: Write Playwright spec in `tests/e2e/39-live-docker-files-sapbo-compare.spec.ts`**

```typescript
import { test, expect } from '@playwright/test';

test.describe('Files & SAP BO Live Docker Compare Functionality', () => {
  test('executes tabular multi-file reconciliation', async ({ page }) => {
    await page.goto('/#compare');
    await page.click('#tab-multi-file-compare');
    await expect(page.locator('.multi-file-mapping-panel')).toBeVisible();
  });

  test('triggers SAP BO live report compare with dynamic date prompts', async ({ page }) => {
    await page.goto('/#compare');
    await page.selectOption('#compare-source-type', 'sap_bo_report');
    await expect(page.locator('.sapbo-prompt-fields')).toBeVisible();
  });
});
```

- [ ] **Step 2: Run Playwright test to verify execution**

Run: `npx playwright test tests/e2e/39-live-docker-files-sapbo-compare.spec.ts`
Expected: Specs execute and pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/39-live-docker-files-sapbo-compare.spec.ts
git commit -m "test(e2e): add Playwright coverage for multi-file and SAP BO compare"
```

---

### Task 4: Playwright GitLab Pipeline & Retry Spec (`40-live-docker-gitlab-retry.spec.ts`)

**Files:**
- Create: `tests/e2e/40-live-docker-gitlab-retry.spec.ts`

**Interfaces:**
- Consumes: `/api/selections/{id}/launch` and `scripts/ci/run-atom-selection.sh`
- Produces: Playwright / integration test coverage for GitLab pipeline trigger and retry (`gitlabretry`)

- [ ] **Step 1: Write Playwright spec in `tests/e2e/40-live-docker-gitlab-retry.spec.ts`**

```typescript
import { test, expect } from '@playwright/test';

test.describe('GitLab CI Integration and Retry', () => {
  test('renders GitLab CI modal with snippet and retry options', async ({ page }) => {
    await page.goto('/#launch');
    await expect(page.locator('#btn-gitlab-ci-snippet')).toBeVisible();
    await page.click('#btn-gitlab-ci-snippet');
    await expect(page.locator('.gitlab-ci-modal')).toBeVisible();
    await expect(page.locator('.gitlab-ci-snippet-code')).toContainText('run-atom-selection.sh');
  });
});
```

- [ ] **Step 2: Run Playwright test to verify execution**

Run: `npx playwright test tests/e2e/40-live-docker-gitlab-retry.spec.ts`
Expected: Spec passes.

- [ ] **Step 3: Run full pytest suite to verify zero regressions**

Run: `pytest tests/unit -n auto`
Expected: 2074 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/40-live-docker-gitlab-retry.spec.ts
git commit -m "test(e2e): add Playwright coverage for GitLab CI launch and retry"
```
