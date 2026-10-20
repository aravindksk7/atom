(function (global) {
  'use strict';
  const blankState = () => ({
    open: false, fileName: '', preview: null, loading: false, committing: false,
    error: '', createSelection: false, selectionName: '', expanded: {},
  });

  global.ETL_FEATURE_BULK_COMPARE = function () {
    return {
      bulkCompare: blankState(),

      openBulkCompareModal() {
        this.bulkCompare = { ...blankState(), open: true };
      },

      closeBulkCompareModal() {
        this.bulkCompare = blankState();
      },

      async downloadBulkCompareTemplate() {
        try {
          const { blob } = await apiBlob('/api/jobs/bulk-compare/template');
          triggerDownload(blob, 'bulk-compare-template.csv');
        } catch (e) {
          this.toast('error', 'Template download failed', e.message);
        }
      },

      _bulkFileToBase64(file) {
        return new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(file);
        });
      },

      async previewBulkCompareFile(event) {
        const file = event.target.files && event.target.files[0];
        event.target.value = '';
        if (!file) return;
        this.bulkCompare.fileName = file.name;
        this.bulkCompare.preview = null;
        this.bulkCompare.expanded = {};
        this.bulkCompare.error = '';
        this.bulkCompare.loading = true;
        try {
          const content_b64 = await this._bulkFileToBase64(file);
          this.bulkCompare.preview = await api('POST', '/api/jobs/bulk-compare/preview', {
            filename: file.name, content_b64,
          });
        } catch (e) {
          this.bulkCompare.error = e.message;
        } finally {
          this.bulkCompare.loading = false;
        }
      },

      toggleBulkCompareRow(rowNumber) {
        this.bulkCompare.expanded = {
          ...this.bulkCompare.expanded, [rowNumber]: !this.bulkCompare.expanded[rowNumber],
        };
      },

      bulkCompareCanImport() {
        const s = this.bulkCompare;
        return Boolean(
          s.preview && s.preview.rows.length && s.preview.invalid_count === 0 && !s.committing
          && (!s.createSelection || s.selectionName.trim())
        );
      },

      async commitBulkCompare() {
        if (!this.bulkCompareCanImport()) return;
        const s = this.bulkCompare;
        s.committing = true;
        s.error = '';
        try {
          const out = await api('POST', '/api/jobs/bulk-compare/commit', {
            jobs: s.preview.rows.map(r => r.job),
            selection_name: s.createSelection ? s.selectionName.trim() : null,
          });
          const selectionNote = out.selection ? ` and selection "${out.selection.name}"` : '';
          this.toast('success', 'Compare jobs imported', `${out.jobs.length} job(s)${selectionNote} saved.`);
          this.closeBulkCompareModal();
          if (this.loadJobs) await this.loadJobs();
          if (out.selection && this.loadJobSelections) await this.loadJobSelections();
        } catch (e) {
          s.error = e.message;
        } finally {
          s.committing = false;
        }
      },
    };
  };
})(window);
