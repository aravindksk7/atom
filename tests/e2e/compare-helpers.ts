import { Page } from '@playwright/test';

export interface AdvancedOptions {
  backend?: 'pandas' | 'polars' | 'duckdb';
  floatTolerance?: string;
  datetimeTolerance?: string;
  mismatchRowLimit?: string;
  sampleFrac?: string;
  columnTolerances?: string;
  caseInsensitiveColumns?: string;
  whitespaceNormalizeColumns?: string;
  parallelColumns?: boolean;
}

/**
 * Fills the "Advanced Options" accordion shared by BO Report, Reconciliation
 * (Run/File vs Report), and SQL sub-tabs. `prefix` matches the data-testid
 * prefix added to each sub-tab's markup (e.g. "compare-bo", "compare-file", "compare-sql").
 */
export async function fillAdvancedOptions(page: Page, prefix: string, opts: AdvancedOptions) {
  const toggle = page.locator(`[data-testid="${prefix}-advanced-toggle"]`);
  if (!(await page.locator(`[data-testid="${prefix}-advanced-panel"]`).isVisible())) {
    await toggle.click();
  }
  if (opts.backend) await page.locator(`[data-testid="${prefix}-backend-select"]`).selectOption(opts.backend);
  if (opts.floatTolerance !== undefined) await page.locator(`[data-testid="${prefix}-float-tolerance-input"]`).fill(opts.floatTolerance);
  if (opts.datetimeTolerance !== undefined) await page.locator(`[data-testid="${prefix}-datetime-tolerance-input"]`).fill(opts.datetimeTolerance);
  if (opts.mismatchRowLimit !== undefined) await page.locator(`[data-testid="${prefix}-mismatch-row-limit-input"]`).fill(opts.mismatchRowLimit);
  if (opts.sampleFrac !== undefined) await page.locator(`[data-testid="${prefix}-sample-frac-input"]`).fill(opts.sampleFrac);
  if (opts.columnTolerances !== undefined) await page.locator(`[data-testid="${prefix}-column-tolerances-input"]`).fill(opts.columnTolerances);
  if (opts.caseInsensitiveColumns !== undefined) await page.locator(`[data-testid="${prefix}-case-insensitive-input"]`).fill(opts.caseInsensitiveColumns);
  if (opts.whitespaceNormalizeColumns !== undefined) await page.locator(`[data-testid="${prefix}-whitespace-normalize-input"]`).fill(opts.whitespaceNormalizeColumns);
  if (opts.parallelColumns !== undefined) {
    const cb = page.locator(`[data-testid="${prefix}-parallel-columns-checkbox"]`);
    if ((await cb.isChecked()) !== opts.parallelColumns) await cb.click();
  }
}
