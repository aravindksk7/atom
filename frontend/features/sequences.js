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
      sequenceEditorOpen: false,
      sequenceEditorMode: 'create',    // 'create' | 'version'
      sequenceMeta: { name: '', description: '', tags_raw: '' },
      sequenceSteps: [],               // array of SequenceStepRef-shaped objects
      sequenceIssues: [],              // [{step_id, field, message}]
      sequenceOrder: [],               // topological step_id order when valid
      sequenceSaving: false,

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

      // Steps grouped by dependency depth, for the read-only graph preview.
      get sequenceLevels() {
        const depth = {};
        const byId = {};
        for (const s of this.sequenceSteps) byId[s.step_id] = s;
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
        for (const s of this.sequenceSteps) {
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
      },

      async selectSequence(sequence) {
        try {
          this.selectedSequence = await api('GET', `/api/sequences/${sequence.id}`);
          this.sequenceUsage = await api('GET', `/api/sequences/${sequence.id}/usage`);
        } catch {
          this.selectedSequence = null;
          this.sequenceUsage = { selections: [], schedules: [] };
        }
      },

      // ===== EDITING =====
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
        this.sequenceSteps = [this.newSequenceStep()];
        this.sequenceIssues = [];
        this.sequenceOrder = [];
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
        this.sequenceSteps = JSON.parse(JSON.stringify(latest ? latest.steps : []));
        this.sequenceIssues = [];
        this.sequenceOrder = [];
        this.sequenceEditorOpen = true;
      },

      addSequenceStep() {
        this.sequenceSteps.push(this.newSequenceStep());
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

      async validateSequenceSteps() {
        try {
          const result = await api('POST', '/api/sequences/validate', {
            steps: this.sequenceSteps,
          });
          this.sequenceIssues = result.errors || [];
          this.sequenceOrder = result.order || [];
        } catch {
          this.sequenceIssues = [];
          this.sequenceOrder = [];
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
        try {
          if (this.sequenceEditorMode === 'create') {
            const created = await api('POST', '/api/sequences', {
              name: this.sequenceMeta.name,
              description: this.sequenceMeta.description,
              tags,
              steps: this.sequenceSteps,
            });
            await this.loadSequences();
            await this.selectSequence(created);
          } else {
            await api('POST', `/api/sequences/${this.selectedSequence.id}/versions`, {
              steps: this.sequenceSteps,
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
    };
  };
})(window);
