(function (global) {
  'use strict';
  // AWS feature slice (AWS tab: ad-hoc S3 checks — metadata, row count,
  // partition discovery, format validation — plus Glue Catalog compare and
  // Athena query execution).
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
      // Which AWS service sub-panel is active. Airflow is a placeholder until
      // its backend lands.
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
      awsGlueJobs: [],
      awsGlueJobNameInput: '',
      awsGlueJobArgs: '{}',
      awsGlueJobExpectedStatus: 'SUCCEEDED',
      awsGlueJobPollInterval: '2',
      awsGlueJobMaxAttempts: '120',
      awsGlueJobRunName: '',
      awsGlueJobRunResult: null,
      awsGlueJobRunError: null,
      awsGlueJobRunLoading: false,
      awsAthenaDatabase: '',
      awsAthenaQuery: '',
      awsAthenaOutputLocation: '',
      awsAthenaWorkgroup: '',
      awsAthenaMaxRows: '100',
      awsAthenaMinRows: '',
      awsAthenaMaxRowsAssert: '',
      awsAthenaMetricAssertions: [],
      awsAthenaJobName: '',
      awsAthenaResult: null,
      awsAthenaError: null,
      awsAthenaLoading: false,

      awsAirflowDags: [],
      awsAirflowDagId: '',
      awsAirflowConf: '{}',
      awsAirflowExpectedStatus: 'success',
      awsAirflowTaskAssertions: [],
      awsAirflowPollInterval: '1',
      awsAirflowMaxAttempts: '60',
      awsAirflowJobName: '',
      awsAirflowResult: null,
      awsAirflowError: null,
      awsAirflowLoading: false,

      awsAirflowAddTaskAssertion() {
        this.awsAirflowTaskAssertions.push({ task_id: '', state: 'success' });
      },

      awsAirflowRemoveTaskAssertion(index) {
        this.awsAirflowTaskAssertions.splice(index, 1);
      },

      awsAthenaAddAssertion() {
        this.awsAthenaMetricAssertions.push({
          path: '',
          operator: '==',
          value: '',
          min: '',
          max: '',
          tolerance: '',
        });
      },

      awsAthenaRemoveAssertion(index) {
        this.awsAthenaMetricAssertions.splice(index, 1);
      },

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

      _awsGlueJobRunRequiredFieldError() {
        if (!this.awsConfigId) return 'Config is required';
        if (!(this.awsGlueJobNameInput || '').trim()) return 'Job name is required';
        return null;
      },

      _awsGlueJobArgsParsed() {
        const raw = (this.awsGlueJobArgs || '').trim();
        if (!raw || raw === '{}') return {};
        try {
          const parsed = JSON.parse(raw);
          if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
            return null;
          }
          return parsed;
        } catch (e) {
          return null;
        }
      },

      _awsGlueJobRunParams() {
        const args = this._awsGlueJobArgsParsed();
        if (args === null) {
          this.awsGlueJobRunError = 'Arguments JSON must be valid JSON';
          return null;
        }
        const pollInterval = String(this.awsGlueJobPollInterval || '').trim();
        const maxAttempts = String(this.awsGlueJobMaxAttempts || '').trim();
        return {
          config_id: Number(this.awsConfigId),
          arguments: args,
          poll_interval_seconds: pollInterval !== '' ? Number(pollInterval) : 2.0,
          max_attempts: maxAttempts !== '' ? Number(maxAttempts) : 120,
        };
      },

      async awsGlueLoadJobs() {
        if (this.awsGlueJobRunLoading) return;
        this.awsGlueJobRunError = null;
        if (!this.awsConfigId) {
          this.awsGlueJobRunError = 'Config is required';
          return;
        }
        this.awsGlueJobRunLoading = true;
        try {
          const data = await api('GET', '/api/aws/glue/jobs?config_id=' + encodeURIComponent(this.awsConfigId));
          this.awsGlueJobs = (data && data.jobs) || [];
        } catch (e) {
          this.awsGlueJobRunError = e.message;
          this.toast('error', 'Loading Glue jobs failed', e.message);
        } finally {
          this.awsGlueJobRunLoading = false;
        }
      },

      async awsGlueRunJob() {
        if (this.awsGlueJobRunLoading) return;
        this.awsGlueJobRunError = null;
        this.awsGlueJobRunResult = null;
        const missing = this._awsGlueJobRunRequiredFieldError();
        if (missing) { this.awsGlueJobRunError = missing; return; }
        const runParams = this._awsGlueJobRunParams();
        if (!runParams) return;
        this.awsGlueJobRunLoading = true;
        try {
          const jobName = encodeURIComponent((this.awsGlueJobNameInput || '').trim());
          this.awsGlueJobRunResult = await api('POST', '/api/aws/glue/jobs/' + jobName + '/run', runParams);
          this.toast('success', 'Glue job finished', (this.awsGlueJobRunResult && this.awsGlueJobRunResult.job_run_state) || 'Done');
        } catch (e) {
          this.awsGlueJobRunError = e.message;
          this.toast('error', 'Glue job run failed', e.message);
        } finally {
          this.awsGlueJobRunLoading = false;
        }
      },

      async awsCreateGlueJobRun() {
        if (this.awsGlueJobRunLoading) return;
        this.awsGlueJobRunError = null;
        const missing = this._awsGlueJobRunRequiredFieldError();
        if (missing) { this.awsGlueJobRunError = missing; return; }
        const runParams = this._awsGlueJobRunParams();
        if (!runParams) return;
        const name = ((this.awsGlueJobRunName || '').trim() || ['glue', (this.awsGlueJobNameInput || '').trim()].filter(Boolean).join('_')).replace(/[^a-z0-9_-]+/gi, '_').toLowerCase();
        const params = {
          config_id: Number(this.awsConfigId),
          job_name: (this.awsGlueJobNameInput || '').trim(),
          arguments: runParams.arguments,
          expected_status: this.awsGlueJobExpectedStatus || 'SUCCEEDED',
          poll_interval_seconds: runParams.poll_interval_seconds,
          max_attempts: runParams.max_attempts,
        };
        this.awsGlueJobRunLoading = true;
        try {
          await api('POST', '/api/jobs', { name, job_type: 'aws_glue_job_run', params, key_columns: [] });
          if (this.loadJobs) await this.loadJobs();
          this.toast('success', 'Glue job created', name);
          this.awsGlueJobRunName = '';
        } catch (e) {
          this.awsGlueJobRunError = e.message;
          this.toast('error', 'Glue job creation failed', e.message);
        } finally {
          this.awsGlueJobRunLoading = false;
        }
      },

      _awsAthenaRequiredFieldError() {
        if (!this.awsConfigId) return 'Config is required';
        if (!(this.awsAthenaQuery || '').trim()) return 'Query is required';
        if (!(this.awsAthenaOutputLocation || '').trim()) return 'Output location is required';
        return null;
      },

      _awsAthenaRunQueryParams() {
        const maxRows = String(this.awsAthenaMaxRows || '').trim();
        const params = {
          config_id: Number(this.awsConfigId),
          database: (this.awsAthenaDatabase || '').trim() || null,
          query: (this.awsAthenaQuery || '').trim(),
          output_location: (this.awsAthenaOutputLocation || '').trim(),
          workgroup: (this.awsAthenaWorkgroup || '').trim() || null,
          max_rows: Number(maxRows || 100),
        };
        return params;
      },

      _awsAthenaJobParams() {
        const params = this._awsAthenaRunQueryParams();
        const minRows = String(this.awsAthenaMinRows || '').trim();
        const maxRowsAssert = String(this.awsAthenaMaxRowsAssert || '').trim();
        if (minRows !== '') params.min_rows = Number(minRows);
        if (maxRowsAssert !== '') params.max_rows_assert = Number(maxRowsAssert);

        const metric_assertions = {};
        for (const ast of (this.awsAthenaMetricAssertions || [])) {
          const p = (ast.path || '').trim();
          if (!p) continue;
          const op = ast.operator || '==';
          const rawVal = (ast.value !== undefined && ast.value !== null) ? String(ast.value) : '';
          const parsedVal = (isNaN(Number(rawVal)) || rawVal.trim() === '') ? (ast.value ?? '') : Number(rawVal);

          if (op === 'between') {
            metric_assertions[p] = {
              operator: 'between',
              min: Number(ast.min),
              max: Number(ast.max),
            };
          } else if (op === '==' || op === '!=') {
            const tol = (ast.tolerance || '').trim();
            if (!tol) {
              if (op === '==') {
                metric_assertions[p] = parsedVal;
              } else {
                metric_assertions[p] = { operator: '!=', value: parsedVal };
              }
            } else {
              const tolVal = (tol.endsWith('%') || isNaN(Number(tol))) ? tol : Number(tol);
              metric_assertions[p] = { operator: op, value: parsedVal, tolerance: tolVal };
            }
          } else {
            metric_assertions[p] = { operator: op, value: Number(ast.value) };
          }
        }
        if (Object.keys(metric_assertions).length > 0) {
          params.metric_assertions = metric_assertions;
        }

        return params;
      },

      async awsAthenaRunQuery() {
        if (this.awsAthenaLoading) return;
        this.awsAthenaError = null;
        this.awsAthenaResult = null;
        const missing = this._awsAthenaRequiredFieldError();
        if (missing) { this.awsAthenaError = missing; return; }
        this.awsAthenaLoading = true;
        try {
          this.awsAthenaResult = await api('POST', '/api/aws/athena/run-query', this._awsAthenaRunQueryParams());
        } catch (e) {
          this.awsAthenaError = e.message;
          this.toast('error', 'Athena query failed', e.message);
        } finally {
          this.awsAthenaLoading = false;
        }
      },

      async awsCreateAthenaQueryJob() {
        if (this.awsAthenaLoading) return;
        this.awsAthenaError = null;
        const missing = this._awsAthenaRequiredFieldError();
        if (missing) { this.awsAthenaError = missing; return; }
        const name = ((this.awsAthenaJobName || '').trim() || ['athena', (this.awsAthenaDatabase || '').trim() || 'query'].filter(Boolean).join('_')).replace(/[^a-z0-9_]+/gi, '_').toLowerCase();
        this.awsAthenaLoading = true;
        try {
          await api('POST', '/api/jobs', { name, job_type: 'aws_athena_query', params: this._awsAthenaJobParams(), key_columns: [] });
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

      _awsAirflowRequiredFieldError() {
        if (!this.awsConfigId) return 'Config is required';
        if (!(this.awsAirflowDagId || '').trim()) return 'DAG ID is required';
        const raw = (this.awsAirflowConf || '').trim();
        if (raw) {
          try {
            JSON.parse(raw);
          } catch (e) {
            return 'Config JSON must be valid JSON';
          }
        }
        return null;
      },

      _awsAirflowRunParams() {
        let conf = {};
        const raw = (this.awsAirflowConf || '').trim();
        if (raw) conf = JSON.parse(raw);
        const pollInterval = String(this.awsAirflowPollInterval || '').trim();
        const maxAttempts = String(this.awsAirflowMaxAttempts || '').trim();
        return {
          config_id: Number(this.awsConfigId),
          conf,
          poll_interval_seconds: Number(pollInterval || 1),
          max_attempts: Number(maxAttempts || 60),
        };
      },

      _awsAirflowJobParams() {
        const params = this._awsAirflowRunParams();
        params.dag_id = (this.awsAirflowDagId || '').trim();
        if (this.awsAirflowExpectedStatus) {
          params.expected_status = this.awsAirflowExpectedStatus;
        }
        const task_assertions = {};
        for (const ta of (this.awsAirflowTaskAssertions || [])) {
          const tid = (ta.task_id || '').trim();
          if (tid) {
            task_assertions[tid] = ta.state || 'success';
          }
        }
        if (Object.keys(task_assertions).length > 0) {
          params.task_assertions = task_assertions;
        }
        return params;
      },

      async awsAirflowTriggerDag() {
        if (this.awsAirflowLoading) return;
        this.awsAirflowError = null;
        this.awsAirflowResult = null;
        const missing = this._awsAirflowRequiredFieldError();
        if (missing) { this.awsAirflowError = missing; return; }
        this.awsAirflowLoading = true;
        try {
          const dagId = encodeURIComponent((this.awsAirflowDagId || '').trim());
          this.awsAirflowResult = await api('POST', '/api/aws/airflow/dags/' + dagId + '/trigger', this._awsAirflowRunParams());
          this.toast('success', 'Airflow DAG triggered', this.awsAirflowResult && this.awsAirflowResult.dag_run_id);
        } catch (e) {
          this.awsAirflowError = e.message;
          this.toast('error', 'Triggering Airflow DAG failed', e.message);
        } finally {
          this.awsAirflowLoading = false;
        }
      },

      async awsAirflowLoadDags() {
        if (this.awsAirflowLoading) return;
        this.awsAirflowError = null;
        if (!this.awsConfigId) { this.awsAirflowError = 'Config is required'; return; }
        this.awsAirflowLoading = true;
        try {
          const data = await api('GET', '/api/aws/airflow/dags?config_id=' + encodeURIComponent(this.awsConfigId));
          this.awsAirflowDags = (data && data.dags) || [];
        } catch (e) {
          this.awsAirflowError = e.message;
          this.toast('error', 'Loading Airflow DAGs failed', e.message);
        } finally {
          this.awsAirflowLoading = false;
        }
      },

      async awsAirflowRunDag() {
        if (this.awsAirflowLoading) return;
        this.awsAirflowError = null;
        this.awsAirflowResult = null;
        const missing = this._awsAirflowRequiredFieldError();
        if (missing) { this.awsAirflowError = missing; return; }
        this.awsAirflowLoading = true;
        try {
          const dagId = encodeURIComponent((this.awsAirflowDagId || '').trim());
          this.awsAirflowResult = await api('POST', '/api/aws/airflow/dags/' + dagId + '/run', this._awsAirflowRunParams());
        } catch (e) {
          this.awsAirflowError = e.message;
          this.toast('error', 'Airflow DAG run failed', e.message);
        } finally {
          this.awsAirflowLoading = false;
        }
      },

      async awsCreateAirflowRunJob() {
        if (this.awsAirflowLoading) return;
        this.awsAirflowError = null;
        const missing = this._awsAirflowRequiredFieldError();
        if (missing) { this.awsAirflowError = missing; return; }
        const name = ((this.awsAirflowJobName || '').trim() || ['airflow', (this.awsAirflowDagId || '').trim() || 'dag'].filter(Boolean).join('_')).replace(/[^a-z0-9_]+/gi, '_').toLowerCase();
        this.awsAirflowLoading = true;
        try {
          await api('POST', '/api/jobs', { name, job_type: 'airflow_dag_run', params: this._awsAirflowJobParams(), key_columns: [] });
          if (this.loadJobs) await this.loadJobs();
          this.toast('success', 'Airflow job created', name);
          this.awsAirflowJobName = '';
        } catch (e) {
          this.awsAirflowError = e.message;
          this.toast('error', 'Airflow job creation failed', e.message);
        } finally {
          this.awsAirflowLoading = false;
        }
      },
    };
  };
})(window);
