(function (global) {
  'use strict';
  // AWS feature slice (AWS tab: ad-hoc S3 checks — metadata, row count,
  // partition discovery, format validation — plus Glue Catalog compare).
  // Merged into the Alpine
  // component via the FEATURE_SLICES reduce in app.js.
  //
  // HTTP convention: uses the module-level `api(method, path, body)` helper
  // defined in app.js (same one adapters.js/compare.js/etc. call directly,
  // not via `this.`). It attaches the bearer token, returns the parsed JSON
  // body on success, and on a non-2xx response throws an Error carrying both
  // `.message` (a flattened display string via app.js's apiErrorMessage) and
  // `.detail` (the raw FastAPI `detail`). The aws_s3 routes put
  // `missing_in_target` / `extra_in_target` / `type_mismatches` on `detail`
  // for schema-validation errors; `_awsPost` reads them off `e.detail` into
  // awsError. See below.
  global.ETL_FEATURE_AWS = function () {
    return {
      // ===== STATE =====
      // Which AWS service sub-panel is active. Athena/Airflow are
      // placeholders until their backends land.
      awsService: 's3',
      awsConfigId: '',
      awsBucket: '',
      awsKey: '',
      awsPrefix: '',
      awsFmt: 'csv',
      awsExpectedSchemaRaw: '',   // JSON text, optional
      awsJobName: '',
      awsMinRows: '',
      awsMaxRows: '',
      awsExpectedColumnsRaw: '',
      awsMinPartitions: '',
      awsJobError: null,
      awsLoading: false,
      awsResult: null,            // { kind, data }
      // { message, missing?, extra?, typeMismatches? } — schema drift details
      // come from the error's `.detail`; null for other errors, so
      // downstream templates should tolerate them being absent.
      awsError: null,
      awsGlueSourceDatabase: '',
      awsGlueSourceTable: '',
      awsGlueTargetDatabase: '',
      awsGlueTargetTable: '',
      awsGlueCompareLocation: true,
      awsGlueCompareFormats: true,
      awsGlueComparePartitions: true,
      awsGlueResult: null,
      awsGlueError: null,
      awsGlueLoading: false,
      awsGlueJobName: '',
      awsAthenaDatabase: '',
      awsAthenaQuery: '',
      awsAthenaOutputLocation: '',
      awsAthenaWorkgroup: '',
      awsAthenaMaxRows: '100',
      awsAthenaMinRows: '',
      awsAthenaMaxRowsAssert: '',
      awsAthenaJobName: '',
      awsAthenaResult: null,
      awsAthenaError: null,
      awsAthenaLoading: false,

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
            typeMismatches: detail.type_mismatches || null,
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

      _awsDefaultJobName(kind) {
        const base = [kind, this.awsBucket, this.awsKey || this.awsPrefix]
          .filter(Boolean).join('_').replace(/[^a-z0-9_]+/gi, '_').toLowerCase();
        return base || kind;
      },

      _awsJobParams(common) {
        return Object.assign({ config_id: Number(this.awsConfigId), bucket: this.awsBucket }, common);
      },

      async _awsCreateJob(kind, params) {
        this.awsJobError = null;
        const name = (this.awsJobName || this._awsDefaultJobName(kind)).trim();
        try {
          await api('POST', '/api/jobs', {
            name,
            job_type: kind,
            params,
            key_columns: [],
          });
          if (this.loadJobs) await this.loadJobs();
          this.toast('success', 'S3 job created', name);
          this.awsJobName = '';
        } catch (e) {
          this.awsJobError = e.message;
          this.toast('error', 'S3 job creation failed', e.message);
        }
      },

      async awsCreateRowCountJob() {
        const params = this._awsJobParams({ key: this.awsKey, fmt: this.awsFmt });
        if (this.awsMinRows !== '') params.min_rows = Number(this.awsMinRows);
        if (this.awsMaxRows !== '') params.max_rows = Number(this.awsMaxRows);
        await this._awsCreateJob('s3_row_count', params);
      },

      async awsCreateFormatValidationJob() {
        let expected = null;
        if (this.awsExpectedSchemaRaw.trim()) {
          try {
            expected = JSON.parse(this.awsExpectedSchemaRaw);
          } catch (e) {
            this.awsJobError = 'expected_schema must be valid JSON';
            return;
          }
        }
        const params = this._awsJobParams({ key: this.awsKey, fmt: this.awsFmt });
        if (expected) params.expected_schema = expected;
        await this._awsCreateJob('s3_format_validation', params);
      },

      async awsCreatePartitionCheckJob() {
        const params = this._awsJobParams({ prefix: this.awsPrefix });
        const expectedColumns = this.awsExpectedColumnsRaw.split(',').map(s => s.trim()).filter(Boolean);
        if (expectedColumns.length) params.expected_columns = expectedColumns;
        if (this.awsMinPartitions !== '') params.min_partitions = Number(this.awsMinPartitions);
        await this._awsCreateJob('s3_partition_check', params);
      },

      _awsGlueParams() {
        return {
          config_id: Number(this.awsConfigId),
          source_database: this.awsGlueSourceDatabase,
          source_table: this.awsGlueSourceTable,
          target_database: this.awsGlueTargetDatabase,
          target_table: this.awsGlueTargetTable,
          compare_location: !!this.awsGlueCompareLocation,
          compare_formats: !!this.awsGlueCompareFormats,
          compare_partitions: !!this.awsGlueComparePartitions,
        };
      },

      _awsGlueRequiredFieldError() {
        if (!this.awsConfigId) return 'Select an AWS config before comparing Glue Catalog tables.';
        if (!this.awsGlueSourceDatabase) return 'Enter a source database before comparing Glue Catalog tables.';
        if (!this.awsGlueSourceTable) return 'Enter a source table before comparing Glue Catalog tables.';
        if (!this.awsGlueTargetDatabase) return 'Enter a target database before comparing Glue Catalog tables.';
        if (!this.awsGlueTargetTable) return 'Enter a target table before comparing Glue Catalog tables.';
        return null;
      },

      async awsGlueCompareTables() {
        this.awsGlueError = null;
        this.awsGlueResult = null;
        const missingFieldError = this._awsGlueRequiredFieldError();
        if (missingFieldError) {
          this.awsGlueError = missingFieldError;
          return;
        }
        try {
          this.awsGlueResult = await api('POST', '/api/aws/glue/compare-tables', this._awsGlueParams());
        } catch (e) {
          this.awsGlueError = e.message;
          this.toast('error', 'Glue compare failed', e.message);
        }
      },

      async awsCreateGlueCatalogCompareJob() {
        if (this.awsGlueLoading) return;
        this.awsGlueError = null;
        const missingFieldError = this._awsGlueRequiredFieldError();
        if (missingFieldError) {
          this.awsGlueError = missingFieldError;
          return;
        }
        const name = (this.awsGlueJobName || ['glue', this.awsGlueSourceDatabase, this.awsGlueSourceTable, this.awsGlueTargetDatabase, this.awsGlueTargetTable].filter(Boolean).join('_')).replace(/[^a-z0-9_]+/gi, '_').toLowerCase();
        this.awsGlueLoading = true;
        try {
          await api('POST', '/api/jobs', { name, job_type: 'aws_glue_catalog_compare', params: this._awsGlueParams(), key_columns: [] });
          if (this.loadJobs) await this.loadJobs();
          this.toast('success', 'Glue job created', name);
          this.awsGlueJobName = '';
        } catch (e) {
          this.awsGlueError = e.message;
          this.toast('error', 'Glue job creation failed', e.message);
        } finally {
          this.awsGlueLoading = false;
        }
      },

      _awsAthenaRequiredFieldError() {
        if (!this.awsConfigId) return 'Config is required';
        if (!this.awsAthenaQuery) return 'Query is required';
        if (!this.awsAthenaOutputLocation) return 'Output location is required';
        return null;
      },

      _awsAthenaParams() {
        const params = {
          config_id: Number(this.awsConfigId),
          database: this.awsAthenaDatabase || null,
          query: this.awsAthenaQuery,
          output_location: this.awsAthenaOutputLocation,
          workgroup: this.awsAthenaWorkgroup || null,
          max_rows: Number(this.awsAthenaMaxRows || 100),
        };
        if (this.awsAthenaMinRows !== '') params.min_rows = Number(this.awsAthenaMinRows);
        if (this.awsAthenaMaxRowsAssert !== '') params.max_rows_assert = Number(this.awsAthenaMaxRowsAssert);
        return params;
      },

      async awsAthenaRunQuery() {
        this.awsAthenaError = null;
        this.awsAthenaResult = null;
        const missing = this._awsAthenaRequiredFieldError();
        if (missing) { this.awsAthenaError = missing; return; }
        this.awsAthenaLoading = true;
        try {
          this.awsAthenaResult = await api('POST', '/api/aws/athena/run-query', this._awsAthenaParams());
        } catch (e) {
          this.awsAthenaError = e.message;
          this.toast('error', 'Athena query failed', e.message);
        } finally {
          this.awsAthenaLoading = false;
        }
      },

      async awsCreateAthenaQueryJob() {
        this.awsAthenaError = null;
        const missing = this._awsAthenaRequiredFieldError();
        if (missing) { this.awsAthenaError = missing; return; }
        const name = (this.awsAthenaJobName || ['athena', this.awsAthenaDatabase || 'query'].filter(Boolean).join('_')).replace(/[^a-z0-9_]+/gi, '_').toLowerCase();
        this.awsAthenaLoading = true;
        try {
          await api('POST', '/api/jobs', { name, job_type: 'aws_athena_query', params: this._awsAthenaParams(), key_columns: [] });
          if (this.loadJobs) await this.loadJobs();
          this.toast('success', 'Athena job created', name);
          this.awsAthenaJobName = '';
        } catch (e) {
          this.awsAthenaError = e.message;
          this.toast('error', 'Athena job creation failed', e.message);
        } finally {
          this.awsAthenaLoading = false;
        }
      },
    };
  };
})(window);
