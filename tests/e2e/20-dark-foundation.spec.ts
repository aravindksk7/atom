import { test, expect } from './fixtures';

/**
 * The app is dark-only. 672 hardcoded light Tailwind classes (bg-white,
 * text-slate-700, ...) used to render every non-auth modal as a white card
 * with dark inputs inside it. tailwind.config.js now maps the palette onto
 * dark CSS-variable tokens, so these assertions pin that mapping down.
 *
 * The e2e suite asserts behavior, not color -- nothing else in it would
 * catch a regression here.
 */

/** Parse "rgb(28, 33, 44)" / "rgb(28 33 44 / 0.5)" into [r,g,b]. */
function rgb(value: string): [number, number, number] {
  const parts = value.match(/\d+(\.\d+)?/g);
  if (!parts) throw new Error(`unparseable color: ${value}`);
  return [Number(parts[0]), Number(parts[1]), Number(parts[2])];
}

/** Perceived lightness 0-255, good enough to tell "dark surface" from "white". */
function lightness(value: string): number {
  const [r, g, b] = rgb(value);
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

test.describe('20 dark foundation', () => {
  test('bg-white resolves to a dark raised surface, not white', async ({ authedPage }) => {
    await authedPage.goto('/');
    const probe = await authedPage.evaluate(() => {
      const el = document.createElement('div');
      el.className = 'bg-white';
      document.body.appendChild(el);
      const bg = getComputedStyle(el).backgroundColor;
      el.remove();
      return bg;
    });
    expect(lightness(probe)).toBeLessThan(60);
  });

  test('body text classes resolve to light text', async ({ authedPage }) => {
    await authedPage.goto('/');
    const probe = await authedPage.evaluate(() => {
      const el = document.createElement('div');
      el.className = 'text-slate-700';
      document.body.appendChild(el);
      const color = getComputedStyle(el).color;
      el.remove();
      return color;
    });
    expect(lightness(probe)).toBeGreaterThan(150);
  });

  test('the contract modal is a dark card, not a white slab', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-contracts"]').click();
    await authedPage.locator('[data-testid="contracts-new-btn"]').click();
    const modal = authedPage.locator('[data-testid="contract-modal"] > div');
    await expect(modal).toBeVisible();
    const bg = await modal.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(lightness(bg)).toBeLessThan(60);
  });

  test('opacity utilities still work after the palette retarget', async ({ authedPage }) => {
    await authedPage.goto('/');
    const probe = await authedPage.evaluate(() => {
      const el = document.createElement('div');
      el.className = 'bg-black bg-opacity-40';
      document.body.appendChild(el);
      const bg = getComputedStyle(el).backgroundColor;
      el.remove();
      return bg;
    });
    // <alpha-value> substitution is what makes this work; without it the
    // utility silently produces a fully opaque color.
    expect(probe).toMatch(/rgba?\(.*0\.4\)?/);
  });

  /**
   * These tints carry status meaning — bg-emerald-50 is a pass, bg-rose-50 is a
   * failure, bg-amber-50 is a warning (see tab-adapters, tab-config,
   * tab-contracts). An !important block in styles.css used to flatten all of
   * them to one grey, which silently erased that signal while every other test
   * stayed green. Assert they stay distinguishable from each other.
   */
  test('status tints stay distinct from one another', async ({ authedPage }) => {
    await authedPage.goto('/');
    const tints = await authedPage.evaluate(() => {
      const read = (cls: string) => {
        const el = document.createElement('div');
        el.className = cls;
        document.body.appendChild(el);
        const bg = getComputedStyle(el).backgroundColor;
        el.remove();
        return bg;
      };
      return {
        emerald: read('bg-emerald-50'),
        rose: read('bg-rose-50'),
        amber: read('bg-amber-50'),
        sky: read('bg-sky-50'),
        indigo: read('bg-indigo-50'),
        slate: read('bg-slate-50'),
      };
    });
    const values = Object.values(tints);
    expect(new Set(values).size, `tints collapsed: ${JSON.stringify(tints)}`).toBe(values.length);
  });

  test('sidebar collapses to the icon rail below 1024px', async ({ authedPage }) => {
    await authedPage.setViewportSize({ width: 1440, height: 900 });
    await authedPage.goto('/');
    const sidebar = authedPage.locator('[data-testid="app-sidebar"]');
    await expect(sidebar).not.toHaveClass(/is-collapsed/);

    await authedPage.setViewportSize({ width: 1000, height: 900 });
    await expect(sidebar).toHaveClass(/is-collapsed/);

    // Above the breakpoint again, the user's own preference comes back.
    await authedPage.setViewportSize({ width: 1440, height: 900 });
    await expect(sidebar).not.toHaveClass(/is-collapsed/);
  });

  test('the page does not scroll horizontally at 1024px', async ({ authedPage }) => {
    await authedPage.setViewportSize({ width: 1024, height: 900 });
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    const overflow = await authedPage.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test('help content is fetched only when the Help tab is opened', async ({ authedPage }) => {
    const helpRequests: string[] = [];
    authedPage.on('request', (r) => {
      if (r.url().includes('help-content.js')) helpRequests.push(r.url());
    });

    await authedPage.goto('/');
    expect(helpRequests).toHaveLength(0);

    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    await expect.poll(() => helpRequests.length).toBe(1);

    // Content actually rendered, not just fetched.
    await expect(authedPage.locator('.help-nav-item').first()).toBeVisible();

    // Reopening must not refetch.
    await authedPage.locator('[data-testid="nav-tab-home"]').click();
    await authedPage.locator('[data-testid="nav-tab-help"]').click();
    await expect(authedPage.locator('.help-nav-item').first()).toBeVisible();
    expect(helpRequests).toHaveLength(1);
  });

  test('hidden tabs are not in the DOM', async ({ authedPage }) => {
    await authedPage.goto('/');
    // Home is active; the Logs tab body must not exist yet.
    await expect(authedPage.locator('[data-testid="global-logs-panel"]')).toHaveCount(0);

    await authedPage.locator('[data-testid="nav-tab-logs"]').click();
    await expect(authedPage.locator('[data-testid="global-logs-panel"]')).toHaveCount(1);

    await authedPage.locator('[data-testid="nav-tab-home"]').click();
    await expect(authedPage.locator('[data-testid="global-logs-panel"]')).toHaveCount(0);
  });

  /**
   * Lazy-mounting moved these errors rather than fixing them: asserting only on
   * boot passed while 18 jobModal.mf_*_preview_creds errors still fired the
   * moment the Launch tab mounted. Every tab has to be visited, or the gate
   * measures the mount timing instead of the bug.
   */
  test('no Alpine expression errors at boot or on any tab', async ({ authedPage }) => {
    const errors: string[] = [];
    authedPage.on('console', (m) => {
      if (/Alpine Expression Error/.test(m.text())) errors.push(m.text());
    });

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="home-view"]').waitFor();
    expect(errors, 'boot').toEqual([]);

    const tabs = ['config', 'adapters', 'aws', 'contracts', 'jobs', 'monitor',
                  'history', 'reports', 'scheduler-reports', 'differences',
                  'compare', 'logs', 'help'];
    for (const tab of tabs) {
      await authedPage.locator(`[data-testid="nav-tab-${tab}"]`).click();
      // Alpine flushes bindings on the next tick, so let the mount settle
      // before sampling — otherwise a clean read just means we looked early.
      await authedPage.waitForTimeout(150);
      expect(errors, `after opening the ${tab} tab`).toEqual([]);
    }
  });

  test('boot DOM is a fraction of the old eager-mount tree', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="home-view"]').waitFor();
    const nodes = await authedPage.evaluate(() => document.querySelectorAll('*').length);
    // Was 4070 with all 14 tabs eager. Home is ~45 nodes plus the shell.
    expect(nodes).toBeLessThan(1500);
  });

  test('initial page load stays inside the transfer budget', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="home-view"]').waitFor();

    const totalKB = await authedPage.evaluate(() => {
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
      const res = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
      const bytes = (nav?.transferSize ?? 0) + res.reduce((s, r) => s + (r.transferSize || 0), 0);
      return Math.round(bytes / 1024);
    });

    // Was ~1100 KB: 396 KB index.html + 201 KB Chart.js + 64 KB help-content
    // + the feature bundle, all uncompressed. gzip plus deferring the 64 KB
    // help payload should land well under half of that.
    expect(totalKB).toBeLessThan(500);
  });
});
