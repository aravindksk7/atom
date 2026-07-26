(function (global) {
  'use strict';
  // AWS feature slice (AWS tab: ad-hoc S3 checks — metadata, row count,
  // partition discovery, format validation). Merged into the Alpine
  // component via the FEATURE_SLICES reduce in app.js.
  //
  // HTTP convention: uses the module-level `api(method, path, body)` helper
  // defined in app.js (same one adapters.js/compare.js/etc. call directly,
  // not via `this.`). It attaches the bearer token, returns the parsed JSON
  // body on success, and on a non-2xx response throws an Error carrying both
  // `.message` (a flattened display string via app.js's apiErrorMessage) and
  // `.detail` (the raw FastAPI `detail`). The aws_s3 routes put
  // `missing_in_target` / `extra_in_target` on `detail` for schema-validation
  // errors; `_awsPost` reads them off `e.detail` into awsError. See below.
  global.ETL_FEATURE_AWS = function () {
    return {
      // ===== STATE =====
      // Which AWS service sub-panel is active. Glue/Athena/Airflow are
      // placeholders until their backends land.
      awsService: 's3',
      awsConfigId: '',
      awsBucket: '',
      awsKey: '',
      awsPrefix: '',
      awsFmt: 'csv',
      awsExpectedSchemaRaw: '',   // JSON text, optional
      awsLoading: false,
      awsResult: null,            // { kind, data }
      // { message, missing?, extra? } — missing/extra come from the error's
      // `.detail` (schema-validation drift); null for other errors, so
      // downstream templates should tolerate them being absent.
      awsError: null,

      _awsReset() {
        this.awsResult = null;
        this.awsError = null;
      },

      async _awsPost(path, payload) {
        this.awsLoading = true;
        this._awsReset();
        try {
          return await api('POST', '/api/aws/s3/' + path, payload);
        } catch (e) {
          const detail = e.detail || {};
          this.awsError = {
            message: e.message,
            missing: detail.missing_in_target || null,
            extra: detail.extra_in_target || null,
          };
          this.toast('error', 'AWS S3 check failed', e.message);
          return null;
        } finally {
          this.awsLoading = false;
        }
      },

      async awsRunMetadata() {
        if (!this.awsConfigId || !this.awsBucket || !this.awsKey) return;
        const d = await this._awsPost('metadata', {
          config_id: Number(this.awsConfigId), bucket: this.awsBucket, key: this.awsKey,
        });
        if (d) this.awsResult = { kind: 'metadata', data: d };
      },

      async awsRunRowCount() {
        if (!this.awsConfigId || !this.awsBucket || !this.awsKey) return;
        const d = await this._awsPost('row-count', {
          config_id: Number(this.awsConfigId), bucket: this.awsBucket,
          key: this.awsKey, fmt: this.awsFmt,
        });
        if (d) this.awsResult = { kind: 'row_count', data: d };
      },

      async awsRunPartitions() {
        if (!this.awsConfigId || !this.awsBucket || !this.awsPrefix) return;
        const d = await this._awsPost('partitions', {
          config_id: Number(this.awsConfigId), bucket: this.awsBucket, prefix: this.awsPrefix,
        });
        if (d) this.awsResult = { kind: 'partitions', data: d };
      },

      async awsRunValidateFormat() {
        if (!this.awsConfigId || !this.awsBucket || !this.awsKey) return;
        let expected = null;
        if (this.awsExpectedSchemaRaw.trim()) {
          try {
            expected = JSON.parse(this.awsExpectedSchemaRaw);
          } catch (e) {
            this._awsReset();
            this.awsError = { message: 'expected_schema must be valid JSON' };
            return;
          }
        }
        const d = await this._awsPost('validate-format', {
          config_id: Number(this.awsConfigId), bucket: this.awsBucket,
          key: this.awsKey, fmt: this.awsFmt, expected_schema: expected,
        });
        if (d) this.awsResult = { kind: 'validate_format', data: d };
      },
    };
  };
})(window);
