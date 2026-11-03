# Docker Test Baseline

Recorded 2026-11-03 in `atom-test:local` (Python 3.12, Debian bookworm), on base commit `b21bc68` plus only the Docker test files. No live backends were running, and `E2E_LIVE_BACKENDS` was unset.

A test listed here is not a regression from the sequence flow modes work. Any other failure is.

Causes below come from each failure's first error. Only spec 51 was rerun, so a timeout may be a flake rather than a permanent failure.

## Python: `tests/unit tests/integration`

22 failed, 3322 passed, 42 skipped (9m33s).

**Cause of the failures traced in the output:** `bash` reads the Windows CRLF checkout through the bind mount, giving `set: pipefail\r: invalid option name`. All failures are shell-script tests, unrelated to sequences.

`tests/unit/test_post_gitlab_status.py` (12):

- `test_posts_commit_status_with_namespaced_context`
- `test_skips_entirely_when_not_a_gitlab_job`
- `test_skips_when_commit_sha_missing`
- `test_commit_status_http_error_warns_but_exits_zero`
- `test_sequence_target_id_context`
- `test_updates_existing_sticky_comment_when_marker_found`
- `test_creates_sticky_comment_when_marker_absent`
- `test_comment_body_is_marker_then_summary`
- `test_branch_pipeline_skips_comment_silently`
- `test_missing_note_body_file_still_posts_commit_status`
- `test_empty_note_body_file_skips_comment`
- `test_notes_list_failure_warns_and_exits_zero`

`tests/unit/test_run_atom_target_signals.py` (10):

- `test_posts_pending_before_launching`
- `test_posts_success_with_counts_after_a_passing_run`
- `test_gate_code_maps_to_gitlab_state[0-success]`
- `test_gate_code_maps_to_gitlab_state[1-failed]`
- `test_gate_code_maps_to_gitlab_state[2-canceled]`
- `test_gate_code_maps_to_gitlab_state[3-failed]`
- `test_gate_code_maps_to_gitlab_state[4-failed]`
- `test_gate_code_maps_to_gitlab_state[5-failed]`
- `test_gate_code_maps_to_gitlab_state[6-failed]`
- `test_non_json_atom_output_falls_back_to_state_as_description`

## Playwright: full suite

11 failed, 265 passed, 43 skipped (18.5m).

| Spec and test | First error |
|---|---|
| `05-adapters.spec.ts:107` Automic lookup against a fake config | 30s test timeout |
| `08c-compare-sql.spec.ts:92` submitting empty shows Config A required | `.toast-title` not found |
| `08e-compare-mismatch-diff.spec.ts:46` invalid run UUID | 30s test timeout |
| `11-help.spec.ts:212` domains retain multi-guide coverage | Expected `true`, received `false` |
| `17-timezone-display.spec.ts:71` contract breach history timestamp | Contract row not found, 30s timeout |
| `26-compare-save-as-job.spec.ts:173` Full HTML Report download | 30s test timeout |
| `41-live-run-atom-target.spec.ts:99` gates FAILED on a job selection | CRLF: `invalid option name`, exit 2 instead of 1 |
| `41-live-run-atom-target.spec.ts:138` gates FAILED on an execution sequence | CRLF: `invalid option name`, exit 2 instead of 1 |
| `42-ci-trigger-token.spec.ts:90` drives run-atom-target.sh | CRLF: `invalid option name`, exit 2 instead of 1 |
| `43-live-docker-matrix-save-as-job.spec.ts:46` Matrix compare job | 30s test timeout |
| `51-multi-day-sequence-recovery.spec.ts:340` recovers a stopped sequence | Unstable, see below |

### Spec 51

`recovers a stopped multi-day sequence without rerunning successful ingestion` failed differently in two runs on the same code:

- Full suite, line 422: strict-mode violation, because the job name matches two `tbody` elements (`history-run-steps` and the results table).
- Alone, line 418: 60s timeout waiting for the `Restart from failure` button.

The other 6 tests in that file pass. Task 14 still has to add the Advanced graph line to this spec, and those 6 tests are what show that edit works.
