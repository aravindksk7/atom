# SAP DS Auth Type — Design

**Date**: 2026-08-08
**Status**: Approved

## Problem

SAP BO's config (`EnvironmentConfig.bo_auth_type`) lets the user pick the CMS
auth type (Enterprise / Windows AD / LDAP / SAP R/3) used to log in. SAP DS's
config has no equivalent — `DSRestClient.login()` always sends only
`userName`/`password`, with no auth type. Add the same field to DS, mirroring
BO's existing pattern end to end (model, client, UI, tests).

## Design

**`etl_framework/config/models.py`** — `EnvironmentConfig`:
- New field `ds_auth_type: str = "secEnterprise"`, placed next to the other
  `ds_*` fields.
- New validator `validate_ds_auth_type`, identical shape to
  `validate_bo_auth_type`: value must be one of
  `{"secEnterprise", "secWinAD", "secLDAP", "secSAPR3"}`.

**`etl_framework/sap_ds/client.py`** — `DSRestClient`:
- `__init__` stores `self._auth_type = env_config.ds_auth_type`.
- `login()` payload gains `"authType": self._auth_type` alongside
  `userName`/`password`. Best-effort key name, same caveat already documented
  at the top of the file (DS Administrator API shapes aren't verified against
  a live server).

**`frontend/partials/tab-config.html`**:
- In the DS `grid-2` block (next to SAP DS URL / DS User / DS Password), add
  a "DS Auth Type" `<select>` bound to `configModal.ds_auth_type`, with the
  same 4 `<option>`s as the existing BO Auth Type select (same labels:
  Enterprise / Windows AD (on-premises) / LDAP / SAP R/3).

**`frontend/features/config.js`**:
- Add `ds_auth_type: 'secEnterprise'` default alongside `bo_auth_type` in the
  three places it's threaded through: modal init (new config), modal load
  (edit existing config — `d.ds_auth_type || 'secEnterprise'`), and the
  save-mapping (`m.ds_auth_type || 'secEnterprise'`).

## Testing

- `tests/unit/test_config_models_ds.py`: validator accepts the 4 valid
  values, rejects others, defaults to `secEnterprise`.
- `tests/unit/test_ds_rest_client.py`: `login()` payload includes
  `authType` matching `env_config.ds_auth_type`.
- No e2e coverage added — `tests/e2e/21-accessibility.spec.ts` already
  exercises the config modal generically; a new `<select>` doesn't need its
  own e2e test per existing convention (BO Auth Type has none either).

## Out of scope

- No change to SAP DS server-side auth handling — this only threads the
  field through config → client → request payload, same as BO.
- No migration for existing saved configs; `ds_auth_type` defaults to
  `secEnterprise` for configs saved before this change, same default the DS
  client already implicitly used.
