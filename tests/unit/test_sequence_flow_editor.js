'use strict';
// The sequence editor keeps one flat step list and builds whichever input the
// chosen flow needs. These cover that payload and loading a saved version back.
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};
require(path.join(ROOT, 'frontend', 'features', 'sequences.js'));

function editor(steps) {
  const seq = window.ETL_FEATURE_SEQUENCES();
  seq.sequenceSteps = steps.map((id) => ({ step_id: id, job_name: 'j', depends_on: [], _stage: 0 }));
  return seq;
}

function run(name, fn) {
  fn();
  console.log('ok - ' + name);
}

run('serial and parallel send flow_mode without edges or client fields', () => {
  const seq = editor(['a', 'b']);
  seq.sequenceSteps[1].depends_on = ['a'];
  seq.sequenceFlow = 'parallel';
  assert.deepStrictEqual(seq.buildSequenceFlowInput(), {
    flow_mode: 'parallel',
    steps: [{ step_id: 'a', job_name: 'j' }, { step_id: 'b', job_name: 'j' }],
  });
});

run('custom sends stages in order and skips empty ones', () => {
  const seq = editor(['a', 'b', 'c']);
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.sequenceSteps[2]._stage = 1;
  assert.deepStrictEqual(seq.buildSequenceFlowInput(), {
    stages: [
      { mode: 'serial', steps: [{ step_id: 'a', job_name: 'j' }] },
      { mode: 'parallel', steps: [{ step_id: 'b', job_name: 'j' }, { step_id: 'c', job_name: 'j' }] },
    ],
  });
});

run('advanced graph sends raw edges', () => {
  const seq = editor(['a', 'b']);
  seq.sequenceAdvancedGraph = true;
  seq.sequenceSteps[1].depends_on = ['a'];
  assert.deepStrictEqual(seq.buildSequenceFlowInput(), {
    steps: [
      { step_id: 'a', job_name: 'j', depends_on: [] },
      { step_id: 'b', job_name: 'j', depends_on: ['a'] },
    ],
  });
});

run('a new step joins the end of its stage, keeping the flat list in stage order', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.addSequenceStep(0);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s._stage), [0, 0, 1]);
});

run('moving a stage keeps each step with its stage', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel'];
  seq.sequenceSteps[1]._stage = 1;
  seq.moveSequenceStage(1, -1);
  assert.deepStrictEqual(seq.sequenceStageModes, ['parallel', 'serial']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s.step_id), ['b', 'a']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s._stage), [0, 1]);
});

run('removing a stage renumbers later ones', () => {
  const seq = editor(['a', 'b', 'c']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.sequenceSteps[2]._stage = 2;
  seq.removeSequenceStage(1);
  assert.deepStrictEqual(seq.sequenceStageModes, ['serial', 'serial']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => [s.step_id, s._stage]), [['a', 0], ['c', 1]]);
});

run('a stage rule writes to the entry steps only', () => {
  const seq = editor(['a', 'b', 'c', 'd']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.sequenceSteps[2]._stage = 2;
  seq.sequenceSteps[3]._stage = 2;
  seq.setSequenceStageRule(2, 'all_done');
  assert.strictEqual(seq.sequenceSteps[2].trigger_rule, 'all_done');
  assert.strictEqual(seq.sequenceSteps[3].trigger_rule, undefined);
  assert.strictEqual(seq.sequenceStageRule(2), 'all_done');
});

run('a saved stage-shaped version loads into stages', () => {
  const seq = editor([]);
  seq.loadSequenceFlow({
    flow_mode: 'custom',
    steps: [],
    stages: [
      { mode: 'serial', steps: [{ step_id: 'a', job_name: 'j' }] },
      { mode: 'parallel', steps: [{ step_id: 'b', job_name: 'j' }, { step_id: 'c', job_name: 'j' }] },
    ],
  });
  assert.strictEqual(seq.sequenceFlow, 'custom');
  assert.strictEqual(seq.sequenceAdvancedGraph, false);
  assert.deepStrictEqual(seq.sequenceStageModes, ['serial', 'parallel']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s._stage), [0, 1, 1]);
});

run('a version that is not stage-shaped opens in advanced graph', () => {
  const seq = editor([]);
  seq.loadSequenceFlow({
    flow_mode: 'custom',
    stages: null,
    steps: [{ step_id: 'a', job_name: 'j', depends_on: [] }, { step_id: 'b', job_name: 'j', depends_on: ['a'] }],
  });
  assert.strictEqual(seq.sequenceAdvancedGraph, true);
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, ['a']);
});

run('leaving advanced graph on a non-stage graph needs confirmation', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceAdvancedGraph = true;
  seq.sequenceSteps[1].depends_on = ['a'];
  seq.sequenceDerived = { flow_mode: 'custom', stages: null };

  global.confirm = () => false;
  assert.strictEqual(seq.setSequenceAdvancedGraph(false), false);
  assert.strictEqual(seq.sequenceAdvancedGraph, true);
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, ['a']);

  global.confirm = () => true;
  assert.strictEqual(seq.setSequenceAdvancedGraph(false), true);
  assert.strictEqual(seq.sequenceAdvancedGraph, false);
  assert.strictEqual(seq.sequenceFlow, 'serial');
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, []);
});

run('entering advanced graph copies the compiled edges', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceCompiledSteps = [
    { step_id: 'a', depends_on: [] }, { step_id: 'b', depends_on: ['a'] },
  ];
  seq.setSequenceAdvancedGraph(true);
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, ['a']);
});

run('saved defaults merge, keeping other settings', () => {
  const seq = editor(['a']);
  seq.sequenceBaseDefaults = { source_env: 'qa', run_settings: { max_retries: 2, max_workers: 9 } };
  seq.sequenceMaxWorkers = 3;
  assert.deepStrictEqual(seq.buildSequenceDefaults(), {
    source_env: 'qa', run_settings: { max_retries: 2, max_workers: 3 },
  });
  seq.sequenceMaxWorkers = '';
  assert.deepStrictEqual(seq.buildSequenceDefaults(), {
    source_env: 'qa', run_settings: { max_retries: 2 },
  });
});

run('the preview reads compiled steps when there are any', () => {
  const seq = editor(['a', 'b']);
  seq.sequenceCompiledSteps = [
    { step_id: 'a', depends_on: [] }, { step_id: 'b', depends_on: [] },
  ];
  assert.strictEqual(seq.sequenceLevels.length, 1);
  assert.strictEqual(seq.sequenceLevels[0].steps.length, 2);
});

run('opening a saved version validates it, so the preview shows straight away', () => {
  const seq = editor([]);
  let validated = 0;
  seq.validateSequenceSteps = () => { validated += 1; };
  seq.selectedSequence = {
    name: 's', description: '', tags: [],
    versions: [{ flow_mode: 'serial', steps: [], stages: [{ mode: 'serial', steps: [{ step_id: 'a', job_name: 'j' }] }] }],
  };
  seq.openSequenceVersionEditor();
  assert.strictEqual(validated, 1);
});
