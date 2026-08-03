import AxeBuilder from '@axe-core/playwright';
import { Locator, Page } from '@playwright/test';
import { test, expect } from './fixtures';

const wcag21Tags = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

const tabs = [
  'home',
  'config',
  'adapters',
  'aws',
  'contracts',
  'jobs',
  'monitor',
  'history',
  'reports',
  'scheduler-reports',
  'differences',
  'compare',
  'logs',
  'help',
];

async function runAxe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(wcag21Tags).analyze();
  expect(results.violations).toEqual([]);
}

async function hasVisibleFocusIndicator(page: Page, selector: string) {
  return page.locator(selector).evaluate((el) => {
    const style = window.getComputedStyle(el);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
      boxShadow: style.boxShadow,
    };
  });
}

async function expectVisibleFocusIndicator(page: Page, selector: string) {
  const locator = page.locator(selector);
  await locator.focus();
  await expect(locator).toBeFocused();
  const focusStyle = await hasVisibleFocusIndicator(page, selector);
  expect(
    focusStyle.outlineStyle !== 'none' ||
    focusStyle.outlineWidth !== '0px' ||
    focusStyle.boxShadow !== 'none'
  ).toBeTruthy();
}

type DialogCase = {
  name: string;
  statePath: string;
  refName: string;
  dialog: string;
  view?: string;
  firstFocus?: string;
  lastFocus?: string;
  prepare?: (page: Page) => Promise<void>;
};

const dialogCases: DialogCase[] = [
  {
    name: 'auth',
    statePath: 'showAuthModal',
    refName: 'authDialog',
    dialog: '[data-testid="auth-modal"] [role="dialog"]',
    firstFocus: '[aria-label="Close API access setup"]',
    lastFocus: '[data-testid="auth-activate-btn"]',
  },
  {
    name: 'drawer',
    statePath: 'drawer.show',
    refName: 'drawerDialog',
    dialog: '[x-ref="drawerDialog"]',
    firstFocus: '[aria-label="Close mismatch details"]',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.drawer = {
          show: false,
          loading: false,
          runId: '00000000-0000-0000-0000-000000000001',
          result: { id: 'result-1', query_name: 'orders', mismatch_count: 0, column_stats: [] },
          rows: [],
          offset: 0,
        };
      });
    },
  },
  {
    name: 'contract',
    statePath: 'showContractModal',
    refName: 'contractDialog',
    dialog: '[data-testid="contract-modal"] [role="dialog"]',
    firstFocus: '[data-testid="contract-modal-name-input"]',
    lastFocus: '[data-testid="contract-modal-save-btn"]',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.contractModal = { name: '', source_job: '', owner: '', sla_hours: 4, consumers_raw: '', breach_severity: 'error', version: '1.0' };
        app.contractModalEditing = false;
      });
    },
  },
  {
    name: 'help',
    statePath: 'showingHelp',
    refName: 'helpDialog',
    dialog: '[x-ref="helpDialog"]',
    firstFocus: '[aria-label="Close help"]',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.helpTitle = 'Accessibility help';
        app.helpContent = 'Keyboard and focus behavior.';
      });
    },
  },
  {
    name: 'bulk decision',
    statePath: 'bulkDecisionForm.open',
    refName: 'bulkDecisionDialog',
    dialog: '[x-ref="bulkDecisionDialog"]',
    firstFocus: '[aria-label="Close bulk decision"]',
    lastFocus: '[x-ref="bulkDecisionDialog"] .btn-primary',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.bulkDecisionForm = { open: false, mode: 'bulk-accept', note: '', saving: false };
        app.selectedRun = { run_id: '00000000-0000-0000-0000-000000000002' };
      });
    },
  },
  {
    name: 'mismatch decision',
    statePath: 'mismatchDecisionForm.open',
    refName: 'mismatchDecisionDialog',
    dialog: '[data-testid="decision-modal"] [role="dialog"]',
    firstFocus: '[aria-label="Close mismatch decision"]',
    lastFocus: '[data-testid="decision-modal-confirm-btn"]',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.mismatchDecisionForm = { open: false, scope: 'drawer', decision: 'accept', note: '', saving: false };
      });
    },
  },
  {
    name: 'config',
    statePath: 'showConfigModal',
    refName: 'configDialog',
    dialog: '[data-testid="config-modal"] [role="dialog"]',
    view: 'config',
    firstFocus: '[data-testid="config-modal-name-input"]',
    lastFocus: '[data-testid="config-modal-validate-btn"]',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.configModal = {
          id: null,
          name: '',
          env_name: 'dev',
          db_host: 'localhost',
          db_port: 1433,
          db_name: '',
          db_user: '',
          db_password: '',
          db_connect_timeout: 15,
          bo_url: '',
          bo_user: '',
          bo_password: '',
          bo_auth_type: 'secEnterprise',
          bo_timeout: 60,
          bo_proxy_url: '',
          bo_verify_ssl: true,
          ds_url: '',
          ds_user: '',
          ds_password: '',
          ds_repository: '',
          ds_timeout: 60,
          ds_proxy_url: '',
          ds_verify_ssl: true,
          automic_url: '',
          automic_user: '',
          automic_password: '',
          connections: [],
          apiEndpoints: [],
          apiBaseHost: '',
        };
        app.configValidation = null;
      });
    },
  },
  {
    name: 'hook',
    statePath: 'showHookModal',
    refName: 'hookDialog',
    dialog: '[x-ref="hookDialog"]',
    view: 'config',
    firstFocus: '#a11y-config-name',
    lastFocus: '[x-ref="hookDialog"] .btn-primary',
    prepare: async (page) => page.evaluate(() => window.Alpine.$data(document.body).hookModal = { name: '', url: '', events: ['run.failed', 'run.error'], secret: '' }),
  },
  {
    name: 'step release',
    statePath: 'stepReleaseModal.show',
    refName: 'stepReleaseDialog',
    dialog: '[x-ref="stepReleaseDialog"]',
    view: 'config',
    firstFocus: '[x-ref="stepReleaseDialog"] input[type="radio"][value="approve"]',
    lastFocus: '[x-ref="stepReleaseDialog"] .btn-primary',
    prepare: async (page) => {
      await page.evaluate(() => {
        window.Alpine.$data(document.body).stepReleaseModal = {
          show: false,
          runId: '00000000-0000-0000-0000-000000000003',
          stepIndex: 0,
          releasedBy: 'A11y reviewer',
          note: 'Verified',
          action: 'approve',
        };
      });
    },
  },
  {
    name: 'job',
    statePath: 'showJobModal',
    refName: 'jobDialog',
    dialog: '[data-testid="job-modal"] [role="dialog"]',
    view: 'jobs',
    firstFocus: '[data-testid="job-modal-tab-basic"]',
    lastFocus: '[data-testid="job-modal-validate-definition-btn"]',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.openNewJobModal();
        app.showJobModal = false;
      });
    },
  },
  {
    name: 'schedule',
    statePath: 'showScheduleModal',
    refName: 'scheduleDialog',
    dialog: '[x-ref="scheduleDialog"]',
    view: 'jobs',
    firstFocus: '#a11y-launch-schedule-name',
    lastFocus: '[x-ref="scheduleDialog"] .btn-primary',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.openNewScheduleModal();
        app.showScheduleModal = false;
      });
    },
  },
  {
    name: 'BO job',
    statePath: 'showBOJobModal',
    refName: 'boJobDialog',
    dialog: '[x-ref="boJobDialog"]',
    view: 'adapters',
    firstFocus: '#a11y-adapters-job-name',
    lastFocus: '[x-ref="boJobDialog"] .btn-primary',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.boJobForm = { name: 'bo-a11y', title: 'BO A11y', doc_id: 'doc-1', report_id: 'report-1', key_columns_raw: 'id', format: 'xlsx' };
      });
    },
  },
  {
    name: 'job selection',
    statePath: 'showSelectionModal',
    refName: 'selectionDialog',
    dialog: '[x-ref="selectionDialog"]',
    view: 'jobs',
    firstFocus: '#a11y-launch-name',
    lastFocus: '[x-ref="selectionDialog"] .btn-primary',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.openNewSelectionModal();
        app.showSelectionModal = false;
      });
    },
  },
  {
    name: 'launch selection',
    statePath: 'showLaunchSelectionModal',
    refName: 'launchSelectionDialog',
    dialog: '[x-ref="launchSelectionDialog"]',
    view: 'jobs',
    firstFocus: '#a11y-launch-jobs-cancel-save-launch-job-selection-source-env',
    lastFocus: '[x-ref="launchSelectionDialog"] .btn-primary',
    prepare: async (page) => page.evaluate(() => window.Alpine.$data(document.body).launchSelectionModal = { selection_id: 'sel-1', source_env: 'dev', target_env: 'prod' }),
  },
  {
    name: 'CI integration',
    statePath: 'showCiIntegrationModal',
    refName: 'ciIntegrationDialog',
    dialog: '[x-ref="ciIntegrationDialog"]',
    view: 'jobs',
    firstFocus: '[x-ref="ciIntegrationDialog"] .btn-primary',
    lastFocus: '[x-ref="ciIntegrationDialog"] .btn-secondary',
    prepare: async (page) => page.evaluate(() => window.Alpine.$data(document.body).ciIntegrationModal = { selectionName: 'Nightly', yamlSnippet: 'atom: test' }),
  },
  {
    name: 'selection runs',
    statePath: 'showSelectionRunsModal',
    refName: 'selectionRunsDialog',
    dialog: '[x-ref="selectionRunsDialog"]',
    view: 'jobs',
    firstFocus: '[x-ref="selectionRunsDialog"] .btn-secondary',
    lastFocus: '[x-ref="selectionRunsDialog"] .btn-primary',
    prepare: async (page) => {
      await page.evaluate(() => {
        const app = window.Alpine.$data(document.body);
        app.selectionRunsPanel = { id: 'sel-1', name: 'Nightly' };
        app.selectionRuns = [];
        app.compareRunIds = [];
      });
    },
  },
];

async function getAlpineState(page: Page, path: string) {
  return page.evaluate((statePath) => {
    const app = window.Alpine.$data(document.body);
    return statePath.split('.').reduce((value, key) => value?.[key], app);
  }, path);
}

async function setAlpineState(page: Page, path: string, value: unknown) {
  await page.evaluate(
    ({ statePath, nextValue }) => {
      const app = window.Alpine.$data(document.body);
      const segments = statePath.split('.');
      const key = segments.pop();
      const target = segments.reduce((value, segment) => value?.[segment], app);
      target[key] = nextValue;
    },
    { statePath: path, nextValue: value }
  );
}

async function waitForAlpineFlush(page: Page) {
  await page.evaluate(
    () => new Promise((resolve) => window.Alpine.nextTick(() => requestAnimationFrame(() => resolve(undefined))))
  );
}

async function openDialogFromState(page: Page, dialogCase: DialogCase, trigger: Locator) {
  await trigger.focus();
  await page.evaluate(({ statePath, view }) => {
    const app = window.Alpine.$data(document.body);
    if (view) app.currentView = view;
    const refName = Object.entries(app._dialogFocus || {}).find(([, value]) => value?.state === statePath)?.[0];
    if (refName) app._dialogFocus[refName].trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }, { statePath: dialogCase.statePath, view: dialogCase.view });
  await waitForAlpineFlush(page);
  await dialogCase.prepare?.(page);
  await setAlpineState(page, dialogCase.statePath, true);
  await waitForAlpineFlush(page);
  const dialog = page.locator(dialogCase.dialog);
  await expect(dialog, `${dialogCase.name} dialog should open`).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  const refName = await page.evaluate((statePath) => {
    const app = window.Alpine.$data(document.body);
    return Object.entries(app._dialogFocus || {}).find(([, value]) => value?.state === statePath)?.[0] || '';
  }, dialogCase.statePath);
  expect(refName).toBe(dialogCase.refName);
  return dialog;
}

async function expectDialogTrapEscapeRestore(page: Page, dialogCase: DialogCase) {
  await test.step(dialogCase.name, async () => {
    await page.goto('/');
    const trigger = page.locator('[data-testid="nav-tab-home"]');
    await openDialogFromState(page, dialogCase, trigger);

    if (dialogCase.firstFocus) await page.locator(dialogCase.firstFocus).focus();

    const lastFocus = dialogCase.lastFocus ? page.locator(dialogCase.lastFocus) : null;
    if (lastFocus) {
      await lastFocus.focus();
      await page.keyboard.press('Tab');
      await expect
        .poll(() => page.locator(dialogCase.dialog).evaluate((dialog) => dialog.contains(document.activeElement)))
        .toBe(true);
    }

    await page.keyboard.press('Escape');
    await expect(page.locator(dialogCase.dialog), `${dialogCase.name} dialog should close`).toBeHidden();
    await expect(getAlpineState(page, dialogCase.statePath)).resolves.toBeFalsy();
    await expect
      .poll(() => page.evaluate(() => document.activeElement?.getAttribute('data-testid')))
      .toBe('nav-tab-home');
  });
}

async function setHomeRecentRuns(page: Page) {
  await page.route('**/api/runs', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { run_id: 'a11y-recent-run', status: 'PASSED', source_env: 'dev', target_env: 'prod', started_at: '2026-08-03T00:00:00Z' },
      ]),
    })
  );
  await page.goto('/');
  await page.evaluate(() => {
    const app = window.Alpine.$data(document.body);
    app.viewRunDetail = (runId) => {
      app.currentView = 'history';
      app.selectedRun = { run_id: runId };
    };
  });
}

test.describe('21 accessibility', () => {
  test('has no automated WCAG 2.1 A/AA violations across all primary tabs', async ({ authedPage }) => {
    await authedPage.goto('/');

    for (const tab of tabs) {
      await test.step(`axe scan: ${tab}`, async () => {
        await authedPage.locator(`[data-testid="nav-tab-${tab}"]`).click();
        await expect(authedPage.locator(`[data-testid="nav-tab-${tab}"]`)).toHaveClass(/active/);
        await runAxe(authedPage);
      });
    }
  });

  test('keyboard focus exposes a visible focus ring on navigation controls', async ({ authedPage }) => {
    await authedPage.goto('/');

    await expectVisibleFocusIndicator(authedPage, '[data-testid="nav-tab-home"]');

    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await expectVisibleFocusIndicator(authedPage, '[data-testid="compare-subtab-recon"]');

    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    await expectVisibleFocusIndicator(authedPage, '.help-nav-item >> nth=0');
  });

  test('toast stack is a polite live region and error toasts announce as alerts', async ({ authedPage }) => {
    await authedPage.goto('/');

    const toastStack = authedPage.locator('.toast-stack');
    await expect(toastStack).toHaveAttribute('role', 'status');
    await expect(toastStack).toHaveAttribute('aria-live', 'polite');

    await authedPage.evaluate(() => {
      window.Alpine.$data(document.body).toast('error', 'Accessibility probe', 'Error toasts use alert role');
    });

    const toast = authedPage.locator('.toast-error').filter({ hasText: 'Accessibility probe' });
    await expect(toast).toBeVisible();
    await expect(toast).toHaveAttribute('role', 'alert');
  });

  test('dialogs trap focus, close on Escape, and restore focus to the trigger', async ({ authedPage }) => {
    for (const dialogCase of dialogCases) {
      await expectDialogTrapEscapeRestore(authedPage, dialogCase);
    }
  });

  test('keyboard reachability covers native controls and former non-focusable click handlers', async ({ authedPage }) => {
    await authedPage.goto('/');

    await authedPage.locator('[data-testid="nav-tab-config"]').focus();
    await authedPage.keyboard.press('Enter');
    await expect(authedPage.locator('[data-testid="nav-tab-config"]')).toHaveClass(/active/);

    await authedPage.locator('[data-testid="nav-tab-home"]').focus();
    await authedPage.keyboard.press('Space');
    await expect(authedPage.locator('[data-testid="nav-tab-home"]')).toHaveClass(/active/);

    await setHomeRecentRuns(authedPage);
    await expect(authedPage.locator('[data-testid="home-recent-run-row-a11y-recent-run"]')).toBeVisible();
    await authedPage.locator('[data-testid="nav-tab-home"]').click();
    await authedPage.locator('[data-testid="home-recent-run-row-a11y-recent-run"]').focus();
    await authedPage.keyboard.press('Enter');
    await expect(authedPage.locator('[data-testid="nav-tab-history"]')).toHaveClass(/active/);
    await expect(authedPage.evaluate(() => window.Alpine.$data(document.body).selectedRun.run_id)).resolves.toBe('a11y-recent-run');

    await authedPage.locator('[data-testid="nav-tab-compare"]').click();
    await authedPage.locator('[data-testid="compare-subtab-recon"]').click();
    await authedPage.evaluate(() => {
      const app = window.Alpine.$data(document.body);
      app.compareSubTab = 'recon';
      app.reconMode = 'file';
      app.fileCompareResult = {
        status: 'FAILED',
        passed: 0,
        failed: 1,
        run_id: '00000000-0000-0000-0000-000000000004',
        results: [{ id: 'file-1', query_name: 'orders_file', status: 'FAILED', source_row_count: 1, target_row_count: 1, value_mismatch_count: 1, missing_in_target_count: 0, missing_in_source_count: 0 }],
      };
    });
    await authedPage.route('**/api/runs/00000000-0000-0000-0000-000000000004/results/file-1/mismatches?limit=100&offset=0', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    );
    await waitForAlpineFlush(authedPage);
    await expect(authedPage.locator('[data-testid="compare-file-row-orders_file"]')).toBeVisible();
    await authedPage.locator('[data-testid="compare-file-row-orders_file"]').focus();
    await authedPage.keyboard.press('Enter');
    await expect
      .poll(() => authedPage.evaluate(() => Boolean(window.Alpine.$data(document.body).fileExpandedDiffs.orders_file?.open)))
      .toBe(true);

    await authedPage.locator('[data-testid="nav-tab-contracts"]').click();
    await authedPage.evaluate(() => {
      const app = window.Alpine.$data(document.body);
      app.contractsLoading = false;
      app.contracts = [{ name: 'contract_a11y', source_job: 'orders', owner: 'team@example.com', sla_hours: 4, consumers: [], breach_severity: 'error', version: '1.0' }];
      app.contractStatusMap = { contract_a11y: { status: 'OK' } };
      app.selectContract = (contract) => { app.selectedContract = contract; };
    });
    await waitForAlpineFlush(authedPage);
    await authedPage.locator('[data-testid="contract-row-contract_a11y"]').focus();
    await authedPage.keyboard.press('Enter');
    await expect(authedPage.evaluate(() => window.Alpine.$data(document.body).selectedContract.name)).resolves.toBe('contract_a11y');
  });
});
