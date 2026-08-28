# SAP DS Auth Type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ds_auth_type` field to SAP DS config, mirroring the existing `bo_auth_type` pattern end to end (model validation, client login payload, config UI, tests).

**Architecture:** Same 4-value enum (`secEnterprise`/`secWinAD`/`secLDAP`/`secSAPR3`) already used for `bo_auth_type` and `sap_bo_auth_type`. Threaded through `EnvironmentConfig` → `DSRestClient.login()` payload → config modal UI → save/load mapping. No new files.

**Tech Stack:** Python/Pydantic (`etl_framework/config/models.py`), `requests`-based REST client (`etl_framework/sap_ds/client.py`), Alpine.js frontend (`frontend/partials/tab-config.html`, `frontend/features/config.js`), pytest.

Spec: `docs/superpowers/specs/2026-08-08-sap-ds-auth-type-design.md`

---

### Task 1: Config model — `ds_auth_type` field + validator

**Files:**
- Modify: `etl_framework/config/models.py:50-56` (add field), `etl_framework/config/models.py:113-119` (add validator after `validate_bo_auth_type`)
- Test: `tests/unit/test_config_models_ds.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config_models_ds.py`:

```python
def test_ds_auth_type_defaults_to_sec_enterprise():
    cfg = EnvironmentConfig(name="test", db_host="localhost", db_password="secret")
    assert cfg.ds_auth_type == "secEnterprise"


def test_ds_auth_type_can_be_set():
    cfg = EnvironmentConfig(
        name="test", db_host="localhost", db_password="secret",
        ds_auth_type="secWinAD",
    )
    assert cfg.ds_auth_type == "secWinAD"


def test_ds_auth_type_rejects_invalid_value():
    with pytest.raises(ValueError, match="must be one of"):
        EnvironmentConfig(
            name="test", db_host="localhost", db_password="secret",
            ds_auth_type="not_a_real_type",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_config_models_ds.py -v`
Expected: the 3 new tests FAIL — `test_ds_auth_type_defaults_to_sec_enterprise` and `test_ds_auth_type_can_be_set` fail because `ds_auth_type` isn't a recognized field (Pydantic raises `ValidationError: Extra inputs are not permitted` for the "can be set" case, and `AttributeError` for the default-read case); `test_ds_auth_type_rejects_invalid_value` fails because no validator raises "must be one of".

Note: per [[rtk-pytest-stale-cache]], run raw `python -m pytest`, not `rtk`, to avoid a stale cached summary.

- [ ] **Step 3: Add the field and validator**

In `etl_framework/config/models.py`, change line 50-56 from:

```python
    ds_url: str = ""
    ds_user: str = ""
    ds_password: str = ""
    ds_repository: str = ""
    ds_timeout: int = 60
    ds_proxy_url: str = ""
    ds_verify_ssl: bool = True
```

to:

```python
    ds_url: str = ""
    ds_user: str = ""
    ds_password: str = ""
    ds_repository: str = ""
    ds_auth_type: str = "secEnterprise"
    ds_timeout: int = 60
    ds_proxy_url: str = ""
    ds_verify_ssl: bool = True
```

Then add a validator right after `validate_bo_auth_type` (currently lines 113-119):

```python
    @field_validator("ds_auth_type")
    @classmethod
    def validate_ds_auth_type(cls, v: str) -> str:
        valid = {"secEnterprise", "secWinAD", "secLDAP", "secSAPR3"}
        if v not in valid:
            raise ValueError(f"must be one of {sorted(valid)}, got {v!r}")
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_config_models_ds.py -v`
Expected: all tests PASS (the 4 pre-existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/config/models.py tests/unit/test_config_models_ds.py
git commit -m "feat(config): add ds_auth_type field to EnvironmentConfig"
```

---

### Task 2: DS client — send `authType` in login payload

**Files:**
- Modify: `etl_framework/sap_ds/client.py:40-54` (store `_auth_type`), `etl_framework/sap_ds/client.py:56-78` (`login()`)
- Test: `tests/unit/test_ds_rest_client.py:65-80` (update existing assertion), add new test

- [ ] **Step 1: Write/update the failing tests**

In `tests/unit/test_ds_rest_client.py`, update `test_login_posts_credentials_and_stores_token` (currently lines 65-80) — change the final assertion from:

```python
    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload == {"userName": "admin", "password": "dspass"}
```

to:

```python
    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload == {"userName": "admin", "password": "dspass", "authType": "secEnterprise"}
```

Then add a new test right after it:

```python
def test_login_sends_configured_auth_type(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_auth_type": "secLDAP"})
    client = DSRestClient(cfg)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-DS-SessionToken": "tok"}
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        client.login()

    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload["authType"] == "secLDAP"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_ds_rest_client.py -v`
Expected: `test_login_posts_credentials_and_stores_token` FAILS (actual payload has no `authType` key); `test_login_sends_configured_auth_type` FAILS with `KeyError: 'authType'`.

- [ ] **Step 3: Implement**

In `etl_framework/sap_ds/client.py`, in `__init__` (currently lines 40-54), add after `self._default_repository = env_config.ds_repository`:

```python
        self._default_repository = env_config.ds_repository
        self._auth_type = env_config.ds_auth_type
```

In `login()` (currently lines 56-78), change the payload from:

```python
        payload = {
            "userName": self._user if username is None else username,
            "password": self._password if password is None else password,
        }
```

to:

```python
        payload = {
            "userName": self._user if username is None else username,
            "password": self._password if password is None else password,
            "authType": self._auth_type,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_ds_rest_client.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run full unit suite to check for regressions**

Run: `python -m pytest tests/unit -v`
Expected: all PASS (no other test asserts the old 2-key DS login payload).

- [ ] **Step 6: Commit**

```bash
git add etl_framework/sap_ds/client.py tests/unit/test_ds_rest_client.py
git commit -m "feat(sap_ds): send configured auth type in DS login payload"
```

---

### Task 3: Config UI — DS Auth Type select

**Files:**
- Modify: `frontend/partials/tab-config.html:757-769`

- [ ] **Step 1: Add the select**

Change the DS `grid-2` block (currently lines 757-769) from:

```html
        <div class="grid-2">
          <div><label  class="field-label" for="a11y-config-sap-ds-url">SAP DS URL</label><input x-model="configModal.ds_url" class="field-input" placeholder="http://ds-server:8080" id="a11y-config-sap-ds-url" /></div>
          <div><label  class="field-label" for="a11y-config-ds-user">DS User</label><input x-model="configModal.ds_user" class="field-input" id="a11y-config-ds-user" /></div>
          <div><label  class="field-label" for="a11y-config-ds-password">DS Password</label><input x-model="configModal.ds_password" type="password" class="field-input" id="a11y-config-ds-password" /></div>
          <div><label  class="field-label" for="a11y-config-ds-repository">DS Repository</label><input x-model="configModal.ds_repository" class="field-input" placeholder="DS_REPO" id="a11y-config-ds-repository" /></div>
          <div><label  class="field-label" for="a11y-config-ds-timeout-s">DS Timeout (s)</label><input x-model="configModal.ds_timeout" type="number" class="field-input" placeholder="60" id="a11y-config-ds-timeout-s" /></div>
          <div><label  class="field-label" for="a11y-config-ds-proxy-url">DS Proxy URL</label><input x-model="configModal.ds_proxy_url" class="field-input" placeholder="http://proxy.company:8080" id="a11y-config-ds-proxy-url" /></div>
          <label class="flex items-center gap-2 text-sm text-slate-700 mt-6">
            <input x-model="configModal.ds_verify_ssl" type="checkbox" class="rounded border-slate-300" aria-label="configmodal ds verify ssl" />
            Verify DS SSL certificate
          </label>
        </div>
```

to:

```html
        <div class="grid-2">
          <div><label  class="field-label" for="a11y-config-sap-ds-url">SAP DS URL</label><input x-model="configModal.ds_url" class="field-input" placeholder="http://ds-server:8080" id="a11y-config-sap-ds-url" /></div>
          <div><label  class="field-label" for="a11y-config-ds-user">DS User</label><input x-model="configModal.ds_user" class="field-input" id="a11y-config-ds-user" /></div>
          <div><label  class="field-label" for="a11y-config-ds-password">DS Password</label><input x-model="configModal.ds_password" type="password" class="field-input" id="a11y-config-ds-password" /></div>
          <div>
            <label  class="field-label" for="a11y-config-ds-auth-type">DS Auth Type</label>
            <select x-model="configModal.ds_auth_type" class="field-input" id="a11y-config-ds-auth-type">
              <option value="secEnterprise">Enterprise</option>
              <option value="secWinAD">Windows AD (on-premises)</option>
              <option value="secLDAP">LDAP</option>
              <option value="secSAPR3">SAP R/3</option>
            </select>
          </div>
          <div><label  class="field-label" for="a11y-config-ds-repository">DS Repository</label><input x-model="configModal.ds_repository" class="field-input" placeholder="DS_REPO" id="a11y-config-ds-repository" /></div>
          <div><label  class="field-label" for="a11y-config-ds-timeout-s">DS Timeout (s)</label><input x-model="configModal.ds_timeout" type="number" class="field-input" placeholder="60" id="a11y-config-ds-timeout-s" /></div>
          <div><label  class="field-label" for="a11y-config-ds-proxy-url">DS Proxy URL</label><input x-model="configModal.ds_proxy_url" class="field-input" placeholder="http://proxy.company:8080" id="a11y-config-ds-proxy-url" /></div>
          <label class="flex items-center gap-2 text-sm text-slate-700 mt-6">
            <input x-model="configModal.ds_verify_ssl" type="checkbox" class="rounded border-slate-300" aria-label="configmodal ds verify ssl" />
            Verify DS SSL certificate
          </label>
        </div>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/partials/tab-config.html
git commit -m "feat(frontend): add DS Auth Type select to config modal"
```

---

### Task 4: Config UI — wire `ds_auth_type` through JS state

**Files:**
- Modify: `frontend/features/config.js:64` (new-config defaults), `frontend/features/config.js:87-89` (edit-load mapping), `frontend/features/config.js:164-169` (save mapping)

- [ ] **Step 1: Add default in `openNewConfigModal`**

Change line 64 from:

```js
        ds_url: '', ds_user: '', ds_password: '', ds_repository: '', ds_timeout: 60,
        ds_proxy_url: '', ds_verify_ssl: true,
```

to:

```js
        ds_url: '', ds_user: '', ds_password: '', ds_repository: '', ds_auth_type: 'secEnterprise', ds_timeout: 60,
        ds_proxy_url: '', ds_verify_ssl: true,
```

- [ ] **Step 2: Add load mapping in `editConfig`**

Change lines 87-89 from:

```js
        ds_url: d.ds_url || '', ds_user: d.ds_user || '', ds_password: d.ds_password || '',
        ds_repository: d.ds_repository || '',
        ds_timeout: d.ds_timeout || 60,
```

to:

```js
        ds_url: d.ds_url || '', ds_user: d.ds_user || '', ds_password: d.ds_password || '',
        ds_repository: d.ds_repository || '',
        ds_auth_type: d.ds_auth_type || 'secEnterprise',
        ds_timeout: d.ds_timeout || 60,
```

- [ ] **Step 3: Add save mapping in `_configDataFromModal`**

Change lines 164-167 from:

```js
        ds_url: m.ds_url || '', ds_user: m.ds_user || '',
        ds_password: m.ds_password || '',
        ds_repository: m.ds_repository || '',
        ds_timeout: Number(m.ds_timeout) || 60,
```

to:

```js
        ds_url: m.ds_url || '', ds_user: m.ds_user || '',
        ds_password: m.ds_password || '',
        ds_repository: m.ds_repository || '',
        ds_auth_type: m.ds_auth_type || 'secEnterprise',
        ds_timeout: Number(m.ds_timeout) || 60,
```

- [ ] **Step 4: Manual smoke check**

Run: `rtk proxy npx playwright test tests/e2e/21-accessibility.spec.ts` (per [[rtk-playwright-mangled-output]], use `rtk proxy` so output isn't truncated/JSON-forced)
Expected: PASS — confirms the config modal still renders/labels correctly with the new field present (this spec already walks the config modal's form controls generically).

- [ ] **Step 5: Commit**

```bash
git add frontend/features/config.js
git commit -m "feat(frontend): wire ds_auth_type through config modal load/save"
```

---

### Task 5: Final full-suite check

- [ ] **Step 1: Run full unit suite**

Run: `python -m pytest tests/unit -v`
Expected: all PASS.

- [ ] **Step 2: Confirm no leftover references to the old 2-key DS login payload**

Run: `grep -rn "userName.*password" tests/ etl_framework/sap_ds/` (or equivalent Grep tool call)
Expected: only the updated payload construction in `client.py` and the updated assertions in `test_ds_rest_client.py` — no stray test still expecting the 2-key payload.
