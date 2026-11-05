(function (global) {
  'use strict';
  // Sequences feature slice (Sequences tab: saved execution sequence CRUD,
  // step/dependency editing, live DAG validation). Merged into the Alpine
  // component via the FEATURE_SLICES reduce in app.js.
  global.ETL_FEATURE_SEQUENCES = function () {
    return {
      // ===== STATE =====
      sequences: [],
      sequencesLoading: false,
      selectedSequence: null,          // detail payload from GET /api/sequences/{id}
      sequenceUsage: { selections: [], schedules: [] },
      sequenceRuns: [],                // recent runs with elapsed time, from GET /api/sequences/{id}/runs
      _sequenceSelectToken: 0,         // guards against responses for an earlier selection
      showLaunchSequenceModal: false,
      launchSequenceModal: {},
      sequenceEditorOpen: false,
      sequenceEditorMode: 'create',    // 'create' | 'version'
      sequenceMeta: { name: '', description: '', tags_raw: '' },
      sequenceSteps: [],               // array of SequenceStepRef-shaped objects
      sequenceIssues: [],              // [{step_id, field, message}]
      sequenceOrder: [],               // topological step_id order when valid
      sequenceSaving: false,
      // Flow editing. Steps stay one flat list in stage order; in Custom each
      // carries a client-only _stage index that is stripped before sending.
      sequenceFlow: 'serial',          // 'serial' | 'parallel' | 'custom'
      sequenceAdvancedGraph: false,    // edit raw depends_on instead of a flow
      sequenceStageModes: ['serial'],  // mode of each stage, by index
      sequenceCompiledSteps: [],       // steps with edges, as returned by validate
      sequenceDerived: null,           // { flow_mode, stages } from validate
      sequenceBaseDefaults: {},        // latest version's defaults, merged on save
      sequenceMaxWorkers: '',          // blank means no stored max_workers
      _sequenceValidateToken: 0,       // guards against out-of-order validate responses

      // Preconditions are edited as a flat form and converted on save, because
      // the API wants absent gates to be null rather than empty objects.
      sequencePreconditions: {
        time_window_enabled: false, time_window_start: '', time_window_end: '',
        weekdays_enabled: false, weekdays: [],
        require_run_enabled: false, require_run_job: '', require_run_hours: 24,
      },
      weekdayOptions: [
        { value: 0, label: 'Mon' }, { value: 1, label: 'Tue' }, { value: 2, label: 'Wed' },
        { value: 3, label: 'Thu' }, { value: 4, label: 'Fri' }, { value: 5, label: 'Sat' },
        { value: 6, label: 'Sun' },
      ],

      // ===== DERIVED =====
      get sequenceIsValid() {
        return this.sequenceSteps.length > 0 && this.sequenceIssues.length === 0;
      },

      sequenceIssuesFor(stepId) {
        return this.sequenceIssues.filter((i) => i.step_id === stepId);
      },

      get sequenceGlobalIssues() {
        return this.sequenceIssues.filter((i) => !i.step_id);
      },

      get sequenceUsesStages() {
        return !this.sequenceAdvancedGraph && this.sequenceFlow === 'custom';
      },

      // The groups the editor renders: one per stage, or one holding every step.
      get sequenceStepGroups() {
        if (!this.sequenceUsesStages) {
          return [{ index: 0, mode: null, steps: this.sequenceSteps }];
        }
        return this.sequenceStageModes.map((mode, index) => ({
          index, mode, steps: this.sequenceSteps.filter((s) => s._stage === index),
        }));
      },

      get sequencePinnedSequential() {
        const settings = this.sequenceBaseDefaults.run_settings || {};
        return settings.execution_mode === 'sequential';
      },

      get selectedSequenceFlowLabel() {
        const versions = (this.selectedSequence && this.selectedSequence.versions) || [];
        const latest = versions[versions.length - 1];
        const labels = { serial: 'Serial', parallel: 'Parallel', custom: 'Custom' };
        return latest ? labels[latest.flow_mode] || 'Custom' : '';
      },

      sequenceStepShowsRule(step) {
        if (this.sequenceAdvancedGraph) return (step.depends_on || []).length > 0;
        if (this.sequenceFlow === 'parallel') return false;
        if (this.sequenceFlow === 'serial') return this.sequenceSteps[0] !== step;
        const group = this.sequenceStepGroups[step._stage];
        return Boolean(group) && group.mode === 'serial' && group.steps[0] !== step;
      },

      // Steps grouped by dependency depth, for the read-only graph preview.
      get sequenceLevels() {
        const steps = this.sequenceCompiledSteps.length ? this.sequenceCompiledSteps : this.sequenceSteps;
        const depth = {};
        const byId = {};
        for (const s of steps) byId[s.step_id] = s;
        const resolveDepth = (id, seen) => {
          if (depth[id] !== undefined) return depth[id];
          if (seen.has(id)) return 0;              // cycle: validation reports it
          seen.add(id);
          const step = byId[id];
          const parents = (step && step.depends_on) || [];
          const value = parents.length
            ? 1 + Math.max(...parents.map((p) => (byId[p] ? resolveDepth(p, seen) : 0)))
            : 0;
          depth[id] = value;
          return value;
        };
        const levels = [];
        for (const s of steps) {
          const d = resolveDepth(s.step_id, new Set());
          (levels[d] = levels[d] || []).push(s);
        }
        return levels.map((steps, index) => ({ index, steps: steps || [] }));
      },

      // ===== LOADING =====
      async loadSequences() {
        this.sequencesLoading = true;
        try {
          this.sequences = await api('GET', '/api/sequences');
        } catch { this.sequences = []; }
        this.sequencesLoading = false;
        // The tab keeps its selection, and a run may have finished since then.
        if (this.selectedSequence) this.loadSequenceRuns(this.selectedSequence.id);
      },

      async selectSequence(sequence) {
        const token = ++this._sequenceSelectToken;
        this.sequenceRuns = [];
        try {
          const detail = await api('GET', `/api/sequences/${sequence.id}`);
          if (token !== this._sequenceSelectToken) return;
          this.selectedSequence = detail;
          const usage = await api('GET', `/api/sequences/${sequence.id}/usage`);
          if (token !== this._sequenceSelectToken) return;
          this.sequenceUsage = usage;
        } catch {
          if (token !== this._sequenceSelectToken) return;
          this.selectedSequence = null;
          this.sequenceUsage = { selections: [], schedules: [] };
          return;
        }
        await this.loadSequenceRuns(sequence.id);
      },

      async loadSequenceRuns(sequenceId) {
        const token = this._sequenceSelectToken;
        let runs = [];   // the detail is still useful without its history
        try {
          runs = await api('GET', `/api/sequences/${sequenceId}/runs?limit=20`);
        } catch { /* see above */ }
        if (token === this._sequenceSelectToken) this.sequenceRuns = runs;
      },

      openSequenceRun(runId) {
        this.selectedRun = null;   // an earlier run's detail must not stand in for this one
        this.onTabEnter('history');
        this.viewRunDetail(runId);
      },

      // ===== EDITING =====
      emptyPreconditions() {
        return {
          time_window_enabled: false, time_window_start: '', time_window_end: '',
          weekdays_enabled: false, weekdays: [],
          require_run_enabled: false, require_run_job: '', require_run_hours: 24,
        };
      },

      loadPreconditionsForm(stored) {
        const form = this.emptyPreconditions();
        if (!stored) return form;
        if (stored.time_window) {
          form.time_window_enabled = true;
          form.time_window_start = stored.time_window.start;
          form.time_window_end = stored.time_window.end;
        }
        if (stored.weekdays) {
          form.weekdays_enabled = true;
          form.weekdays = [...stored.weekdays];
        }
        if (stored.require_run_success) {
          form.require_run_enabled = true;
          form.require_run_job = stored.require_run_success.job_name;
          form.require_run_hours = stored.require_run_success.within_hours;
        }
        return form;
      },

      buildPreconditionsPayload() {
        const f = this.sequencePreconditions;
        const payload = {};
        if (f.time_window_enabled && f.time_window_start && f.time_window_end) {
          payload.time_window = { start: f.time_window_start, end: f.time_window_end };
        }
        if (f.weekdays_enabled && f.weekdays.length) {
          payload.weekdays = [...f.weekdays].sort((a, b) => a - b);
        }
        if (f.require_run_enabled && f.require_run_job) {
          payload.require_run_success = {
            job_name: f.require_run_job,
            within_hours: Number(f.require_run_hours) || 1,
          };
        }
        // No gates enabled means no preconditions at all, which the API
        // expresses as null -- an empty object would be a meaningless gate set.
        return Object.keys(payload).length ? payload : null;
      },

      toggleWeekday(day) {
        const at = this.sequencePreconditions.weekdays.indexOf(day);
        if (at === -1) this.sequencePreconditions.weekdays.push(day);
        else this.sequencePreconditions.weekdays.splice(at, 1);
      },

      newSequenceStep() {
        return {
          step_id: '', job_name: '', depends_on: [],
          trigger_rule: 'all_success',
          hold_after: false, wait_seconds: 0, condition: null,
          max_retries: null, retry_delay_seconds: null,
          on_failure: 'skip_downstream',
        };
      },

      slugifyStepId(name) {
        return String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
      },

      openSequenceCreate() {
        this.sequenceEditorMode = 'create';
        this.sequenceMeta = { name: '', description: '', tags_raw: '' };
        this.sequenceFlow = 'serial';
        this.sequenceAdvancedGraph = false;
        this.sequenceStageModes = ['serial'];
        this.sequenceSteps = [this.newSequenceStep()];
        this.sequencePreconditions = this.emptyPreconditions();
        this.sequenceBaseDefaults = {};
        this.sequenceMaxWorkers = '';
        this.sequenceIssues = [];
        this.sequenceOrder = [];
        this.sequenceCompiledSteps = [];
        this.sequenceDerived = null;
        this.sequenceEditorOpen = true;
      },

      openSequenceVersionEditor() {
        if (!this.selectedSequence) return;
        const latest = this.selectedSequence.versions[this.selectedSequence.versions.length - 1];
        this.sequenceEditorMode = 'version';
        this.sequenceMeta = {
          name: this.selectedSequence.name,
          description: this.selectedSequence.description,
          tags_raw: (this.selectedSequence.tags || []).join(', '),
        };
        this.loadSequenceFlow(latest || { steps: [], stages: null });
        this.sequencePreconditions = this.loadPreconditionsForm(latest ? latest.preconditions : null);
        this.sequenceBaseDefaults = (latest && latest.defaults) || {};
        const workers = (this.sequenceBaseDefaults.run_settings || {}).max_workers;
        this.sequenceMaxWorkers = workers === undefined ? '' : workers;
        this.sequenceIssues = [];
        this.sequenceOrder = [];
        this.sequenceCompiledSteps = [];
        this.sequenceDerived = null;
        this.sequenceEditorOpen = true;
        // Fills the execution order preview and reports any problem with the saved version.
        this.validateSequenceSteps();
      },

      _applySequenceStages(flowMode, stages) {
        this.sequenceFlow = flowMode;
        this.sequenceAdvancedGraph = false;
        this.sequenceStageModes = stages.map((stage) => stage.mode);
        this.sequenceSteps = stages.flatMap((stage, index) => stage.steps.map((step) => ({
          ...JSON.parse(JSON.stringify(step)), depends_on: [], _stage: index,
        })));
      },

      loadSequenceFlow(version) {
        if (version.stages) {
          this._applySequenceStages(version.flow_mode, version.stages);
          return;
        }
        this.sequenceFlow = 'custom';
        this.sequenceAdvancedGraph = true;
        this.sequenceStageModes = ['serial'];
        this.sequenceSteps = JSON.parse(JSON.stringify(version.steps || []));
      },

      setSequenceFlow(flow) {
        if (flow === 'custom' && this.sequenceFlow !== 'custom') {
          this.sequenceStageModes = [this.sequenceFlow === 'parallel' ? 'parallel' : 'serial'];
          for (const step of this.sequenceSteps) step._stage = 0;
        }
        this.sequenceFlow = flow;
        this.validateSequenceSteps();
      },

      // Returns whether the switch happened, so the checkbox can snap back.
      setSequenceAdvancedGraph(on) {
        if (on) {
          const edges = {};
          for (const s of this.sequenceCompiledSteps) edges[s.step_id] = s.depends_on || [];
          for (const step of this.sequenceSteps) step.depends_on = [...(edges[step.step_id] || [])];
          this.sequenceAdvancedGraph = true;
        } else if (this.sequenceDerived && this.sequenceDerived.stages) {
          this._applySequenceStages(this.sequenceDerived.flow_mode, this.sequenceDerived.stages);
        } else {
          if (!confirm('This graph does not fit serial or staged flows. Switching will replace its dependencies with a serial flow. Continue?')) {
            return false;
          }
          this.sequenceAdvancedGraph = false;
          this.sequenceFlow = 'serial';
          this.sequenceStageModes = ['serial'];
          for (const step of this.sequenceSteps) {
            step.depends_on = [];
            step._stage = 0;
          }
        }
        this.validateSequenceSteps();
        return true;
      },

      // ===== STAGES =====
      addSequenceStage() {
        this.sequenceStageModes.push('serial');
        this.addSequenceStep(this.sequenceStageModes.length - 1);
      },

      removeSequenceStage(index) {
        this.sequenceStageModes.splice(index, 1);
        this.sequenceSteps = this.sequenceSteps.filter((s) => s._stage !== index);
        for (const step of this.sequenceSteps) {
          if (step._stage > index) step._stage -= 1;
        }
        this.validateSequenceSteps();
      },

      moveSequenceStage(index, delta) {
        const target = index + delta;
        if (target < 0 || target >= this.sequenceStageModes.length) return;
        const modes = this.sequenceStageModes;
        [modes[index], modes[target]] = [modes[target], modes[index]];
        for (const step of this.sequenceSteps) {
          if (step._stage === index) step._stage = target;
          else if (step._stage === target) step._stage = index;
        }
        // Array.prototype.sort is stable, so order inside a stage is kept.
        this.sequenceSteps.sort((a, b) => a._stage - b._stage);
        this.validateSequenceSteps();
      },

      setSequenceStageMode(index, mode) {
        this.sequenceStageModes[index] = mode;
        this.validateSequenceSteps();
      },

      _sequenceStageEntrySteps(index) {
        const steps = this.sequenceSteps.filter((s) => s._stage === index);
        return this.sequenceStageModes[index] === 'parallel' ? steps : steps.slice(0, 1);
      },

      sequenceStageRule(index) {
        const entry = this._sequenceStageEntrySteps(index)[0];
        return (entry && entry.trigger_rule) || 'all_success';
      },

      setSequenceStageRule(index, rule) {
        for (const step of this._sequenceStageEntrySteps(index)) step.trigger_rule = rule;
        this.validateSequenceSteps();
      },

      addSequenceStep(stageIndex = 0) {
        const step = this.newSequenceStep();
        step._stage = stageIndex;
        if (this.sequenceUsesStages) {
          // Insert after the stage's last step, to keep the flat list in stage order.
          const before = this.sequenceSteps.filter((s) => s._stage <= stageIndex).length;
          this.sequenceSteps.splice(before, 0, step);
        } else {
          this.sequenceSteps.push(step);
        }
        this.validateSequenceSteps();
      },

      removeSequenceStep(index) {
        const removed = this.sequenceSteps[index];
        this.sequenceSteps.splice(index, 1);
        // Drop any edges that pointed at the removed step so the user is not
        // left staring at an "unknown step" error they did not cause.
        for (const step of this.sequenceSteps) {
          step.depends_on = (step.depends_on || []).filter((d) => d !== removed.step_id);
        }
        this.validateSequenceSteps();
      },

      onSequenceJobPicked(step) {
        if (!step.step_id) step.step_id = this.slugifyStepId(step.job_name);
        this.validateSequenceSteps();
      },

      // Candidate parents: every other step that already has an id.
      sequenceParentOptions(step) {
        return this.sequenceSteps
          .filter((s) => s !== step && s.step_id)
          .map((s) => s.step_id);
      },

      toggleSequenceDependency(step, parentId) {
        step.depends_on = step.depends_on || [];
        const at = step.depends_on.indexOf(parentId);
        if (at === -1) step.depends_on.push(parentId);
        else step.depends_on.splice(at, 1);
        this.validateSequenceSteps();
      },

      _sequenceStepPayload(step, keepEdges) {
        const { _stage, depends_on, ...rest } = step;
        return keepEdges ? { ...rest, depends_on: depends_on || [] } : rest;
      },

      // The API rejects depends_on alongside a flow, so only raw graphs send it.
      buildSequenceFlowInput() {
        if (this.sequenceAdvancedGraph) {
          return { steps: this.sequenceSteps.map((s) => this._sequenceStepPayload(s, true)) };
        }
        if (this.sequenceFlow === 'custom') {
          return {
            stages: this.sequenceStepGroups
              .filter((group) => group.steps.length)
              .map((group) => ({
                mode: group.mode,
                steps: group.steps.map((s) => this._sequenceStepPayload(s, false)),
              })),
          };
        }
        return {
          flow_mode: this.sequenceFlow,
          steps: this.sequenceSteps.map((s) => this._sequenceStepPayload(s, false)),
        };
      },

      buildSequenceDefaults() {
        const runSettings = { ...(this.sequenceBaseDefaults.run_settings || {}) };
        const workers = this.sequenceMaxWorkers;
        if (workers === '' || workers === null || workers === undefined) delete runSettings.max_workers;
        else runSettings.max_workers = Number(workers);
        return { ...this.sequenceBaseDefaults, run_settings: runSettings };
      },

      async validateSequenceSteps() {
        // Every step/dependency edit fires a new validate call without
        // waiting for the previous one. With many steps, a user wiring up
        // dependencies can have several in flight at once, and network
        // responses are not guaranteed to resolve in request order. A
        // token guard drops any response that isn't from the most recent
        // call, so a slow, stale response can never clobber the result of
        // a later edit (which would otherwise leave sequenceIssues stuck
        // non-empty and silently block Save).
        const token = ++this._sequenceValidateToken;
        try {
          const result = await api('POST', '/api/sequences/validate', this.buildSequenceFlowInput());
          if (token !== this._sequenceValidateToken) return;
          this.sequenceIssues = result.errors || [];
          this.sequenceOrder = result.order || [];
          this.sequenceCompiledSteps = result.steps || [];
          this.sequenceDerived = { flow_mode: result.flow_mode, stages: result.stages };
        } catch {
          if (token !== this._sequenceValidateToken) return;
          this.sequenceIssues = [];
          this.sequenceOrder = [];
          this.sequenceCompiledSteps = [];
          this.sequenceDerived = null;
        }
      },

      // ===== SAVING =====
      async saveSequence() {
        if (!this.sequenceIsValid) return;
        // A blank retry box means "inherit the run settings", which the API
        // expresses as null -- '' and NaN both fail schema validation.
        for (const step of this.sequenceSteps) {
          for (const key of ['max_retries', 'retry_delay_seconds']) {
            const value = step[key];
            if (value === '' || value === undefined || Number.isNaN(value)) step[key] = null;
          }
        }
        this.sequenceSaving = true;
        const tags = this.sequenceMeta.tags_raw
          .split(',').map((t) => t.trim()).filter(Boolean);
        const preconditions = this.buildPreconditionsPayload();
        try {
          if (this.sequenceEditorMode === 'create') {
            const created = await api('POST', '/api/sequences', {
              name: this.sequenceMeta.name,
              description: this.sequenceMeta.description,
              tags,
              ...this.buildSequenceFlowInput(),
              preconditions,
              defaults: this.buildSequenceDefaults(),
            });
            await this.loadSequences();
            await this.selectSequence(created);
          } else {
            await api('POST', `/api/sequences/${this.selectedSequence.id}/versions`, {
              ...this.buildSequenceFlowInput(),
              preconditions,
              defaults: this.buildSequenceDefaults(),
            });
            await this.loadSequences();
            await this.selectSequence(this.selectedSequence);
          }
          this.sequenceEditorOpen = false;
        } catch (err) {
          const detail = err && err.detail;
          this.sequenceIssues = Array.isArray(detail)
            ? detail
            : [{ step_id: null, field: 'steps', message: String((detail && detail.message) || err) }];
        }
        this.sequenceSaving = false;
      },

      async archiveSequence(sequence) {
        if (!confirm(`Archive sequence "${sequence.name}"?`)) return;
        try {
          await api('DELETE', `/api/sequences/${sequence.id}`);
          if (this.selectedSequence && this.selectedSequence.id === sequence.id) {
            this.selectedSequence = null;
          }
          await this.loadSequences();
        } catch (err) {
          alert((err && err.detail) || 'Could not archive this sequence.');
        }
      },

      // ===== LAUNCH =====
      // A saved sequence is otherwise only runnable indirectly (wrapped in a
      // Job Selection, or attached to a Schedule) -- this is the direct
      // "launch it now" path, mirroring openLaunchSelectionModal/
      // launchSelection in launch.js (batchProgress, pollBatch, cancelBatch,
      // dateCustomVariables, _batchOptionsFromModal, and loadCustomVariables
      // all come from that same merged Alpine component -- see FEATURE_SLICES
      // in app.js -- so they don't need to be redefined here).
      openLaunchSequenceModal(seq) {
        if (!seq) return;
        const latest = seq.versions && seq.versions.length ? seq.versions[seq.versions.length - 1] : null;
        const defaults = (latest && latest.defaults) || {};
        this.launchSequenceModal = {
          sequence_id: seq.id,
          source_env: defaults.source_env || 'dev',
          target_env: defaults.target_env || '',
          variableOverridesRaw: '',
          repeat_enabled: false,
          batch_variable_name: '',
          batch_start_value: new Date().toISOString().slice(0, 10),
          batch_iterations: 5,
          batch_step_days: 1,
          batch_weekend_policy: 'skip',
          batch_stop_on_failure: false,
          max_workers: '',
          default_max_workers: (defaults.run_settings || {}).max_workers || 4,
        };
        if (!this.customVariables?.length && this.loadCustomVariables) this.loadCustomVariables();
        this.showLaunchSequenceModal = true;
      },

      async launchSequence() {
        const m = this.launchSequenceModal;
        const body = { source_env: m.source_env, target_env: m.target_env || '' };
        if (m.max_workers !== '' && m.max_workers !== null && m.max_workers !== undefined) {
          body.max_workers = Number(m.max_workers);
        }
        const variable_overrides = {};
        (m.variableOverridesRaw || '').split('\n').forEach((line) => {
          const idx = line.indexOf('=');
          if (idx > 0) variable_overrides[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        });
        if (Object.keys(variable_overrides).length > 0) body.variable_overrides = variable_overrides;
        try {
          if (m.repeat_enabled) {
            body.batch = this._batchOptionsFromModal(m);
            const batch = await api('POST', `/api/sequences/${m.sequence_id}/launch-batch`, body);
            this.batchProgress = batch;
            this.selectedBatchId = batch.batch_id;
            this.batchProgressError = '';
            this.rememberRecentBatch(batch.batch_id);
            this.pollBatch(batch.batch_id);
            this.showLaunchSequenceModal = false;
            this.toast('success', 'Batch started', `${batch.completed} / ${batch.iterations} complete`);
            return;
          }
          const run = await api('POST', `/api/sequences/${m.sequence_id}/launch`, body);
          this.showLaunchSequenceModal = false;
          this.toast('success', 'Launched', `Run ${run.run_id} started`);
          setTimeout(() => this.loadRuns && this.loadRuns(), 1000);
        } catch (e) {
          this.toast('error', 'Launch failed', e.message);
        }
      },
    };
  };
})(window);
