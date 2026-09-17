(function (global) {
  'use strict';
  global.ETL_FEATURE_IMPORT_EXPORT = function () {
    return {
      bundleItems: [],
      bundleSelected: {},
      bundleImportResult: [],
      bundleImporting: false,
      bundleExporting: false,

      async loadBundleList() {
        try {
          this.bundleItems = await api('GET', '/api/bundle/list');
        } catch (e) {
          this.toast('error', 'Failed to load exportable items', e.message);
        }
      },

      bundleItemsByType(type) {
        return this.bundleItems.filter((i) => i.type === type);
      },

      toggleBundleItem(type, name) {
        const forType = { ...(this.bundleSelected[type] || {}) };
        forType[name] = !forType[name];
        this.bundleSelected = { ...this.bundleSelected, [type]: forType };
      },

      async exportBundle() {
        const selection = {};
        for (const type of Object.keys(this.bundleSelected)) {
          const names = Object.keys(this.bundleSelected[type] || {})
            .filter((n) => this.bundleSelected[type][n]);
          if (names.length) selection[type] = names;
        }
        if (!Object.keys(selection).length) {
          this.toast('error', 'Nothing selected', 'Select at least one item to export');
          return;
        }
        this.bundleExporting = true;
        try {
          const bundle = await api('POST', '/api/bundle/export', { selection });
          const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
          triggerDownload(blob, `bundle-${Date.now()}.json`);
          this.toast('success', 'Bundle exported');
        } catch (e) {
          this.toast('error', 'Export failed', e.message);
        } finally {
          this.bundleExporting = false;
        }
      },

      async importBundleFile(event) {
        const file = event.target.files && event.target.files[0];
        event.target.value = '';
        if (!file) return;
        this.bundleImporting = true;
        try {
          const text = await file.text();
          const bundle = JSON.parse(text);
          this.bundleImportResult = await api('POST', '/api/bundle/import', bundle);
          await this.loadBundleList();
          this.toast('success', 'Import complete');
        } catch (e) {
          this.toast('error', 'Import failed', e.message);
        } finally {
          this.bundleImporting = false;
        }
      },
    };
  };
})(window);
