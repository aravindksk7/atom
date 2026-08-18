(function (global) {
  'use strict';
  // Config feature slice (Config tab, incl. Security/API tokens and
  // Notifications/webhooks). Merged into the Alpine component via
  // the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_CONFIG = function () {
    return {
      // ===== STATE (extracted from app.js) =====
    // -----------------------------------------------------------
    // Config
    // -----------------------------------------------------------
    configs: [],
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
    showConfigModal: false,
    configModal: {},
    configValidation: null,

    // Schema Explorer
    schemaExplorerId: null,
    schemaExplorerConnection: '',
    schemaExplorerData: [],
    schemaExplorerLoading: false,
    schemaExpandedSchemas: {},
    schemaExpandedTables: {},
    schemaTablePreviews: {},

    // -----------------------------------------------------------
    // Config – YAML import
    // -----------------------------------------------------------
    yamlImportOpen: false,
    yamlImportText: '',
    yamlImporting: false,

    // -----------------------------------------------------------
    // Security – API tokens
    // -----------------------------------------------------------
    tokens: [],
    securityOpen: false,
    showCreateToken: false,
    newTokenName: '',
    newTokenRole: 'user',
    newTokenExpiresAt: '',
    createdToken: null,
    createdTokenHint: null,
    createdTokenRole: 'user',

    // -----------------------------------------------------------
    // Notifications – webhook hooks
    // -----------------------------------------------------------
    hooks: [],
    notifOpen: false,
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
    showHookModal: false,
    hookModal: { name: '', url: '', events: [], secret: '' },
    hookEventOptions: ['run.passed', 'run.failed', 'run.slow', 'run.error', 'run.completed', 'run.held', 'run.cancelled'],
      // ===== METHODS (extracted from app.js) =====
    // ===========================================================
    // CONFIG
    // ===========================================================
    async loadConfigs() {
      try { this.configs = await api('GET', '/api/configs'); } catch {}
    },

    openNewConfigModal() {
      this.configModal = {
        id: null, name: '', env_name: 'dev',
        db_type: 'mssql',
        db_driver: 'ODBC Driver 17 for SQL Server',
        db_host: 'localhost', db_port: 1433, db_name: '', db_user: '', db_password: '',
        db_connect_timeout: 15,
        bo_url: '', bo_user: '', bo_password: '', bo_auth_type: 'secEnterprise', bo_timeout: 60,
        bo_proxy_url: '', bo_verify_ssl: true,
        ds_url: '', ds_user: '', ds_password: '', ds_repository: '', ds_auth_type: 'secEnterprise', ds_timeout: 60,
        ds_proxy_url: '', ds_verify_ssl: true,
        automic_url: '', automic_user: '', automic_password: '',
        connections: [],
        apiEndpoints: [],
        apiBaseHost: '',
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },

    editConfig(cfg) {
      const d = cfg.config_data || {};
      this.configModal = {
        id: cfg.id, name: cfg.name, env_name: cfg.env_name,
        db_type: d.db_type || 'mssql',
        db_driver: d.db_driver || (d.db_type === 'oracle' ? 'oracledb' : (d.db_type === 'netezza' ? 'nzpy' : 'ODBC Driver 17 for SQL Server')),
        db_host: d.db_host || '', db_port: d.db_port || (d.db_type === 'oracle' ? 1521 : (d.db_type === 'netezza' ? 5480 : 1433)),
        db_name: d.db_name || '', db_user: d.db_user || '', db_password: d.db_password || '',
        db_connect_timeout: d.db_connect_timeout || 15,
        bo_url: d.bo_url || '', bo_user: d.bo_user || '', bo_password: d.bo_password || '',
        bo_auth_type: d.bo_auth_type || 'secEnterprise',
        bo_timeout: d.bo_timeout || 60,
        bo_proxy_url: d.bo_proxy_url || '',
        bo_verify_ssl: d.bo_verify_ssl !== false,
        ds_url: d.ds_url || '', ds_user: d.ds_user || '', ds_password: d.ds_password || '',
        ds_repository: d.ds_repository || '',
        ds_auth_type: d.ds_auth_type || 'secEnterprise',
        ds_timeout: d.ds_timeout || 60,
        ds_proxy_url: d.ds_proxy_url || '',
        ds_verify_ssl: d.ds_verify_ssl !== false,
        automic_url: d.automic_url || '', automic_user: d.automic_user || '',
        automic_password: d.automic_password || '',
        connections: Object.entries(d.connections || {}).map(([name, entry]) => ({
          name,
          db_type: entry.db_type || '',
          db_driver: entry.db_driver || '',
          db_host: entry.db_host || '',
          db_port: entry.db_port || '',
          db_name: entry.db_name || '',
          db_user: entry.db_user || '',
          db_password: entry.db_password || '',
          expanded: false,
        })),
        apiBaseHost: d.api_base_host || '',
        apiEndpoints: Object.entries(d.api_endpoints || {}).map(([name, entry]) => ({
          name,
          base_url: entry.base_url || '',
          path: entry.path || '',
          method: entry.method || 'GET',
          auth_type: entry.auth_type || 'none',
          api_key_header: entry.api_key_header || 'X-API-Key',
          api_key: entry.api_key || '',
          bearer_token: entry.bearer_token || '',
          basic_username: entry.basic_username || '',
          basic_password: entry.basic_password || '',
          sap_bo_logon_token: entry.sap_bo_logon_token || '',
          sap_bo_auth_type: entry.sap_bo_auth_type || 'secEnterprise',
          sap_bo_logon_url: entry.sap_bo_logon_url || '',
          headers_raw: Object.entries(entry.headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n'),
          query_params_raw: Object.entries(entry.query_params || {}).map(([k, v]) => `${k}=${v}`).join('\n'),
          body_raw: entry.body ? JSON.stringify(entry.body, null, 2) : '',
          timeout: entry.timeout ?? 30,
          verify_ssl: entry.verify_ssl !== false,
          response_format: entry.response_format || 'json',
          json_root_path: entry.json_root_path || '',
          pagination_type: entry.pagination_type || 'none',
          pagination_cursor_path: entry.pagination_cursor_path || '',
          pagination_cursor_param: entry.pagination_cursor_param || 'cursor',
          pagination_page_param: entry.pagination_page_param || 'page',
          pagination_size_param: entry.pagination_size_param || 'limit',
          pagination_page_size: entry.pagination_page_size ?? 100,
          pagination_max_pages: entry.pagination_max_pages ?? 50,
          expanded: false,
          previewResult: null,
          previewError: '',
          testResult: null,
          // Declared here (not just on the blank-endpoint factory) so Alpine
          // tracks them on endpoints loaded from a saved config too.
          exchange: null,
          exchangePretty: true,
          exchangeOpen: false,
        })),
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },

    _configDataFromModal() {
      const m = this.configModal;
      const data = {
        db_type: m.db_type || 'mssql',
        db_host: m.db_host || 'localhost',
        db_port: Number(m.db_port) || (m.db_type === 'oracle' ? 1521 : (m.db_type === 'netezza' ? 5480 : 1433)),
        db_name: m.db_name || '',
        db_user: m.db_user || '',
        db_password: m.db_password || '',
        db_driver: m.db_driver || (m.db_type === 'oracle' ? 'oracledb' : (m.db_type === 'netezza' ? 'nzpy' : 'ODBC Driver 17 for SQL Server')),
        db_pool_size: 5, db_pool_overflow: 10, db_pool_timeout: 30,
        db_pool_recycle: 3600,
        db_connect_timeout: Number(m.db_connect_timeout) || 15,
        bo_url: m.bo_url || '', bo_user: m.bo_user || '',
        bo_password: m.bo_password || '',
        bo_auth_type: m.bo_auth_type || 'secEnterprise',
        bo_timeout: Number(m.bo_timeout) || 60,
        bo_proxy_url: m.bo_proxy_url || '',
        bo_verify_ssl: m.bo_verify_ssl !== false,
        ds_url: m.ds_url || '', ds_user: m.ds_user || '',
        ds_password: m.ds_password || '',
        ds_repository: m.ds_repository || '',
        ds_auth_type: m.ds_auth_type || 'secEnterprise',
        ds_timeout: Number(m.ds_timeout) || 60,
        ds_proxy_url: m.ds_proxy_url || '',
        ds_verify_ssl: m.ds_verify_ssl !== false,
        automic_url: m.automic_url || '', automic_user: m.automic_user || '',
        automic_password: m.automic_password || '',
        automic_timeout: 30, automic_max_retries: 3,
      };
      if (m.connections && m.connections.length > 0) {
        data.connections = Object.fromEntries(
          m.connections
            .filter(c => c.name.trim())
            .map(c => [c.name.trim(), {
              ...(c.db_type ? { db_type: c.db_type } : {}),
              ...(c.db_driver ? { db_driver: c.db_driver } : {}),
              ...(c.db_host ? { db_host: c.db_host } : {}),
              ...(c.db_port ? { db_port: Number(c.db_port) } : {}),
              ...(c.db_name ? { db_name: c.db_name } : {}),
              ...(c.db_user ? { db_user: c.db_user } : {}),
              ...(c.db_password ? { db_password: c.db_password } : {}),
            }])
        );
      }
      if (m.apiBaseHost && m.apiBaseHost.trim()) {
        data.api_base_host = m.apiBaseHost.trim();
      }
      if (m.apiEndpoints && m.apiEndpoints.length > 0) {
        data.api_endpoints = Object.fromEntries(
          m.apiEndpoints
            .filter(e => e.name.trim() && (e.base_url.trim() || (e.path || '').trim()))
            .map(e => {
              const headers = {};
              (e.headers_raw || '').split('\n').forEach(line => {
                const idx = line.indexOf(':');
                if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              });
              const query_params = {};
              (e.query_params_raw || '').split('\n').forEach(line => {
                const idx = line.indexOf('=');
                if (idx > 0) query_params[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
              });
              let body = null;
              if (e.body_raw && e.body_raw.trim()) {
                try { body = JSON.parse(e.body_raw); } catch { body = null; }
              }
              return [e.name.trim(), {
                base_url: e.base_url.trim(),
                path: (e.path || '').trim(),
                method: e.method || 'GET',
                auth_type: e.auth_type || 'none',
                api_key_header: e.api_key_header || 'X-API-Key',
                api_key: e.api_key || '',
                bearer_token: e.bearer_token || '',
                basic_username: e.basic_username || '',
                basic_password: e.basic_password || '',
                sap_bo_logon_token: e.sap_bo_logon_token || '',
                sap_bo_auth_type: e.sap_bo_auth_type || 'secEnterprise',
                sap_bo_logon_url: e.sap_bo_logon_url || '',
                headers, query_params, body,
                timeout: Number(e.timeout) || 30,
                verify_ssl: e.verify_ssl !== false,
                response_format: e.response_format || 'json',
                json_root_path: e.json_root_path || '',
                pagination_type: e.pagination_type || 'none',
                pagination_cursor_path: e.pagination_cursor_path || '',
                pagination_cursor_param: e.pagination_cursor_param || 'cursor',
                pagination_page_param: e.pagination_page_param || 'page',
                pagination_size_param: e.pagination_size_param || 'limit',
                pagination_page_size: Number(e.pagination_page_size) || 100,
                pagination_max_pages: Number(e.pagination_max_pages) || 50,
              }];
            })
        );
      }
      return data;
    },

    addNamedConnection() {
      const idx = this.configModal.connections.length + 1;
      this.configModal.connections.push({
        name: `connection_${idx}`,
        db_type: '', db_driver: '', db_host: '', db_port: '', db_name: '', db_user: '', db_password: '',
        expanded: true,
      });
    },

    removeNamedConnection(idx) {
      this.configModal.connections.splice(idx, 1);
    },

    toggleNamedConnection(idx) {
      this.configModal.connections[idx].expanded = !this.configModal.connections[idx].expanded;
    },

    namedConnectionSummary(conn) {
      const parts = [conn.db_host, conn.db_name].filter(Boolean);
      return parts.length ? parts.join(' / ') : 'not configured';
    },

    addApiEndpoint() {
      const idx = this.configModal.apiEndpoints.length + 1;
      this.configModal.apiEndpoints.push({
        name: `endpoint_${idx}`, base_url: '', path: '', method: 'GET',
        auth_type: 'none', api_key_header: 'X-API-Key', api_key: '',
        bearer_token: '', basic_username: '', basic_password: '',
        sap_bo_logon_token: '', sap_bo_auth_type: 'secEnterprise', sap_bo_logon_url: '',
        headers_raw: '', query_params_raw: '', body_raw: '',
        timeout: 30, verify_ssl: true,
        response_format: 'json', json_root_path: '',
        pagination_type: 'none', pagination_cursor_path: '',
        pagination_cursor_param: 'cursor', pagination_page_param: 'page',
        pagination_size_param: 'limit', pagination_page_size: 100, pagination_max_pages: 50,
        expanded: true, previewResult: null, previewError: '', testResult: null,
        exchange: null, exchangePretty: true, exchangeOpen: false,
      });
    },

    removeApiEndpoint(idx) {
      this.configModal.apiEndpoints.splice(idx, 1);
    },

    toggleApiEndpoint(idx) {
      this.configModal.apiEndpoints[idx].expanded = !this.configModal.apiEndpoints[idx].expanded;
    },

    async testApiEndpoint(idx) {
      const m = this.configModal;
      const ep = m.apiEndpoints[idx];
      if (!m.id) { ep.testResult = { ok: false, message: 'Save the config first, then test.' }; return; }
      try {
        ep.testResult = await api('POST', '/api/adapters/rest-api/test', {
          config_id: m.id, endpoint_name: ep.name,
        });
        ep.exchange = ep.testResult.exchange || null;
      } catch (e) {
        ep.testResult = { ok: false, message: e.message };
        // The failing pull is the one worth inspecting, so keep whatever the
        // server managed to capture before it gave up.
        ep.exchange = e.detail?.exchange || null;
      }
    },

    async previewApiEndpoint(idx) {
      const m = this.configModal;
      const ep = m.apiEndpoints[idx];
      if (!m.id) { ep.previewError = 'Save the config first, then preview.'; return; }
      ep.previewError = '';
      try {
        ep.previewResult = await api('POST', '/api/adapters/rest-api/preview', {
          config_id: m.id, endpoint_name: ep.name, limit: 20,
        });
        ep.exchange = ep.previewResult.exchange || null;
      } catch (e) {
        ep.previewError = e.message;
        ep.exchange = e.detail?.exchange || null;
      }
    },

    exchangeBody(raw, pretty) {
      if (raw === null || raw === undefined) return '<NONE SENT>';
      if (!pretty) return raw;
      try {
        return JSON.stringify(JSON.parse(raw), null, 2);
      } catch (_) {
        // A body that will not parse as JSON is exactly the signal being
        // hunted here, so show it verbatim rather than erroring.
        return raw;
      }
    },

    async validateConfig() {
      try {
        this.configValidation = await api('POST', '/api/configs/validate', {
          env_name: this.configModal.env_name,
          config_data: this._configDataFromModal(),
        });
      } catch (e) {
        this.configValidation = { ok: false, errors: [{ field_name: 'request', message: e.message }] };
      }
    },

    async saveConfig() {
      const m = this.configModal;
      const config_data = this._configDataFromModal();
      try {
        if (m.id) {
          await api('PUT', `/api/configs/${m.id}`, { config_data, name: m.name, env_name: m.env_name });
        } else {
          await api('POST', '/api/configs', { name: m.name, env_name: m.env_name, config_data });
        }
        await this.loadConfigs();
        this.showConfigModal = false;
        this.toast('success', 'Config saved', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteConfig(id) {
      if (!confirm('Delete this configuration?')) return;
      try {
        await api('DELETE', `/api/configs/${id}`);
        await this.loadConfigs();
        this.toast('success', 'Config deleted');
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },

    async importYaml() {
      this.yamlImporting = true;
      try {
        const r = await api('POST', '/api/configs/import-yaml', { yaml_content: this.yamlImportText });
        this.yamlImportText = '';
        this.yamlImportOpen = false;
        await this.loadConfigs();
        this.toast('success', 'YAML imported', `${r.environments?.length || 0} environment(s)`);
      } catch (e) {
        this.toast('error', 'Import failed', e.message);
      } finally {
        this.yamlImporting = false;
      }
    },

    // ===========================================================
    // SECURITY – API TOKENS
    // ===========================================================
    async loadTokens() {
      try {
        this.tokens = await api('GET', '/api/tokens');
        return this.tokens;
      } catch {
        return [];
      }
    },

    async createToken(source = 'security') {
      const fromAuthWizard = source === 'auth';
      const name = (fromAuthWizard ? this.authTokenName : this.newTokenName).trim();
      if (!name) {
        if (fromAuthWizard) this.authError = 'Enter a token name';
        return;
      }
      try {
        const body = {
          name,
          is_admin: fromAuthWizard || this.newTokenRole === 'admin',
          expires_at: !fromAuthWizard && this.newTokenExpiresAt
            ? new Date(this.newTokenExpiresAt).toISOString()
            : null,
        };
        const resp = await api('POST', '/api/tokens', body);
        if (fromAuthWizard) {
          sessionStorage.setItem('etl_token', resp.raw_token);
          this.storedTokenValue = resp.raw_token;
          this.activeTokenName = resp.name || name;
          this.activeTokenIsAdmin = true;
          this.authInitialized = true;
          this.authTokenName = '';
          this.authError = '';
          this.authCreatedToken = resp.raw_token;
          await this.loadAll();
        } else {
          this.createdToken = resp.raw_token;
          this.createdTokenHint = resp.token_hint || null;
          this.createdTokenRole = resp.is_admin ? 'admin' : 'user';
          this.newTokenName = '';
          this.newTokenRole = 'user';
          this.newTokenExpiresAt = '';
          this.showCreateToken = false;
          this.toast('success', 'Access created', 'Copy and give this token to the intended user');
        }
        await this.loadTokens();
      } catch (e) {
        let msg = e.message;
        if (/already exists|duplicate|unique/i.test(msg)) {
          msg = 'A token with that name already exists';
        } else if (fromAuthWizard && e.status === 403) {
          msg = 'Token creation is restricted — paste an existing token or ask an admin to create one for you.';
        }
        if (fromAuthWizard) this.authError = msg;
        else this.toast('error', 'Create failed', msg);
      }
    },

    async revokeToken(id) {
      if (!confirm('Revoke this token? Any sessions using it will stop working.')) return;
      try {
        await api('DELETE', `/api/tokens/${id}`);
        await this.loadTokens();
        this.toast('success', 'Token revoked');
      } catch (e) {
        this.toast('error', 'Revoke failed', e.message);
      }
    },

    // ===========================================================
    // NOTIFICATIONS – WEBHOOK HOOKS
    // ===========================================================
    async loadHooks() {
      try { this.hooks = await api('GET', '/api/notifications'); } catch {}
    },

    openNewHookModal() {
      this.hookModal = { name: '', url: '', events: ['run.failed', 'run.error'], secret: '' };
      this.showHookModal = true;
    },

    toggleHookEvent(event) {
      const idx = this.hookModal.events.indexOf(event);
      if (idx >= 0) this.hookModal.events.splice(idx, 1);
      else this.hookModal.events.push(event);
    },

    async saveHook() {
      const m = this.hookModal;
      if (!m.name || !m.url || !m.events.length) return;
      try {
        await api('POST', '/api/notifications', {
          name: m.name, url: m.url,
          events: m.events,
          secret: m.secret || null,
        });
        await this.loadHooks();
        this.showHookModal = false;
        this.toast('success', 'Webhook saved', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteHook(id) {
      if (!confirm('Delete this webhook?')) return;
      try {
        await api('DELETE', `/api/notifications/${id}`);
        await this.loadHooks();
        this.toast('success', 'Webhook deleted');
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },

    async testHook(id) {
      try {
        await api('POST', `/api/notifications/${id}/test`);
        this.toast('success', 'Test ping sent');
      } catch (e) {
        this.toast('error', 'Ping failed', e.message);
      }
    },

    getSchemaConnections(configId) {
      const cfg = this.configs.find(c => c.id === configId);
      if (!cfg) return [];
      const d = cfg.config_data || {};
      const conns = [];
      const mainType = d.db_type || 'mssql';
      const mainHost = d.db_host || 'default';
      const mainDb = d.db_name || '';
      conns.push({ name: '', label: `Main DB: ${mainDb || mainHost} (${mainType})` });
      if (d.connections && typeof d.connections === 'object') {
        for (const [name, conn] of Object.entries(d.connections)) {
          const type = conn.db_type || mainType;
          const host = conn.db_host || mainHost;
          const db = conn.db_name || host;
          conns.push({ name, label: `Connection: ${name} (${db} / ${type})` });
        }
      }
      return conns;
    },

    async openSchemaExplorer(cfg, connectionName = null) {
      if (this.schemaExplorerId === cfg.id && (connectionName === null || connectionName === this.schemaExplorerConnection)) {
        this.closeSchemaExplorer();
        return;
      }
      this.schemaExplorerId = cfg.id;
      this.schemaExplorerConnection = connectionName || '';
      this.schemaExplorerData = [];
      this.schemaExpandedSchemas = {};
      this.schemaExpandedTables = {};
      this.schemaTablePreviews = {};
      this.schemaExplorerLoading = true;
      try {
        const qs = connectionName ? `?connection_name=${encodeURIComponent(connectionName)}` : '';
        this.schemaExplorerData = await api('GET', `/api/configs/${cfg.id}/schema${qs}`);
        const schemas = [...new Set(this.schemaExplorerData.map(t => t.schema))];
        this.schemaExpandedSchemas = Object.fromEntries(schemas.map(s => [s, true]));
      } catch (e) {
        this.toast('error', 'Schema load failed', e.message);
        this.schemaExplorerId = null;
      } finally {
        this.schemaExplorerLoading = false;
      }
    },

    closeSchemaExplorer() {
      this.schemaExplorerId = null;
      this.schemaExplorerConnection = '';
      this.schemaExplorerData = [];
      this.schemaTablePreviews = {};
    },

    toggleSchemaGroup(schema) {
      this.schemaExpandedSchemas[schema] = !this.schemaExpandedSchemas[schema];
    },

    toggleSchemaTable(key) {
      this.schemaExpandedTables[key] = !this.schemaExpandedTables[key];
    },

    async previewSchemaTable(configId, schema, table) {
      const key = `${schema}.${table}`;
      this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: 'loading' };
      try {
        const cfg = this.configs.find(c => c.id === configId);
        let dbType = 'mssql';
        if (cfg) {
          const d = cfg.config_data || {};
          const connName = this.schemaExplorerConnection;
          if (connName && d.connections && d.connections[connName]) {
            dbType = d.connections[connName].db_type || d.db_type || 'mssql';
          } else {
            dbType = d.db_type || 'mssql';
          }
        }
        let query = '';
        if (dbType === 'mssql') {
          query = `SELECT * FROM [${schema}].[${table}]`;
        } else {
          query = `SELECT * FROM "${schema}"."${table}"`;
        }
        const result = await api('POST', `/api/configs/${configId}/preview-query`, {
          query: query,
          limit: 50,
          connection_name: this.schemaExplorerConnection || undefined,
        });
        this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: result };
      } catch (e) {
        this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: `error:${e.message}` };
      }
    },

    useTableInJob(schema, table) {
      const cfg = this.configs.find(c => c.id === this.schemaExplorerId);
      let dbType = 'mssql';
      if (cfg) {
        const d = cfg.config_data || {};
        const connName = this.schemaExplorerConnection;
        if (connName && d.connections && d.connections[connName]) {
          dbType = d.connections[connName].db_type || d.db_type || 'mssql';
        } else {
          dbType = d.db_type || 'mssql';
        }
      }
      const query = dbType === 'mssql' ? `SELECT * FROM [${schema}].[${table}]` : `SELECT * FROM "${schema}"."${table}"`;
      sessionStorage.setItem('etl_pending_query', query);
      this.activeTab = 'launch';
      this.$nextTick(() => this.openNewJobModal());
      this.toast('info', 'Query pre-filled', 'Finish the job setup');
    },

    };
  };
})(window);
