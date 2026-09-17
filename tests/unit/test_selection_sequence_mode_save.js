'use strict';
// Regression test: a Job Selection whose "Jobs come from" is set to "A
// saved execution sequence" has the exact same Live Connections toggle
// and Saved Config picker in its edit modal as an inline-job-list
// selection -- but saveSelection() only ever put run_settings/config_id
// on the PUT body inside the `else` (inline) branch. Editing a
// sequence-sourced selection, flipping Live Connections on, picking a
// config, and hitting Save sent neither field, so the backend carried
// the OLD version's run_settings/config_id forward unchanged: the change
// looked saved (no error, modal closed) but reverted right back.
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};
global.localStorage = { getItem() { return null; }, setItem() {} };
global.location = { search: '', pathname: '/', hash: '#launch' };
global.history = { replaceState() {} };
require(path.join(ROOT, 'frontend', 'features', 'launch.js'));
require(path.join(ROOT, 'frontend', 'features', 'sequences.js'));

async function main() {
  const requests = [];
  global.api = async (method, url, body) => {
    requests.push({ method, url, body });
    return { id: 42 };
  };

  const comp = Object.assign(window.ETL_FEATURE_LAUNCH(), {
    toast() {},
    loadJobSelections: async () => {},
  });

  comp.selectionModalEditing = true;
  comp.selectionModal = {
    id: 42, name: 'seq-set', description: '', tags: '',
    use_live_connections: true,   // user just flipped this on
    config_id: 7,                 // user just picked a saved config
  };
  comp.selectionSourceMode = 'sequence';
  comp.selectionSequenceRef = { sequence_id: 3, sequence_version: null };
  comp.selectedSelectionJobNames = [];

  await comp.saveSelection();

  assert.strictEqual(requests.length, 1);
  const { body } = requests[0];
  assert.ok(
    body.run_settings && body.run_settings.use_live_connections === true,
    'enabling Live Connections on a sequence-sourced selection must reach the PUT body: got ' + JSON.stringify(body)
  );
  assert.strictEqual(
    body.config_id, 7,
    'picking a saved config on a sequence-sourced selection must reach the PUT body: got ' + JSON.stringify(body)
  );

  console.log('ok - saveSelection sends run_settings/config_id in sequence mode too');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
