import { test, expect } from './fixtures';

// The REST-API endpoint form's request/response inspector. Test and Preview
// hand back a redacted exchange (api/services/api_exchange.py); these tests
// pin the frontend state that keeps it and the panel that renders it.

const SAMPLE_EXCHANGE = {
  request: {
    method: 'GET',
    url: 'https://api.example.com/orders?limit=1',
    headers: { Accept: 'application/json', Authorization: '<31 chars, redacted>' },
    body: null,
    truncated: false,
  },
  response: {
    status: 502,
    elapsed_ms: 41,
    bytes: 88,
    content_type: 'text/html',
    redirects: 0,
    headers: { 'Content-Type': 'text/html' },
    body: '<html>proxy interstitial</html>',
    truncated: false,
    binary: false,
  },
};

async function waitForAlpineFlush(page) {
  await page.evaluate(
    () => new Promise((resolve) => window.Alpine.nextTick(() => requestAnimationFrame(() => resolve(undefined))))
  );
}

async function openConfigModalWithOneEndpoint(page) {
  await page.goto('/');
  await page.locator('[data-testid="nav-tab-config"]').click();
  await page.locator('[data-testid="config-new-btn"]').click();
  await page.evaluate(() => {
    const app = window.Alpine.$data(document.body);
    app.configModal.id = 4242;
    app.addApiEndpoint();
    app.configModal.apiEndpoints[0].name = 'orders';
  });
}

test.describe('01b API exchange inspector', () => {
  test('a blank endpoint tracks the exchange fields reactively', async ({ authedPage }) => {
    await openConfigModalWithOneEndpoint(authedPage);

    const ep = await authedPage.evaluate(() => {
      const app = window.Alpine.$data(document.body);
      const endpoint = app.configModal.apiEndpoints[0];
      return {
        keys: Object.keys(endpoint),
        exchange: endpoint.exchange,
        exchangePretty: endpoint.exchangePretty,
        exchangeOpen: endpoint.exchangeOpen,
      };
    });

    expect(ep.keys).toEqual(expect.arrayContaining(['exchange', 'exchangePretty', 'exchangeOpen']));
    expect(ep.exchange).toBeNull();
    expect(ep.exchangePretty).toBe(true);
    expect(ep.exchangeOpen).toBe(false);
  });

  test('an endpoint loaded from a saved config tracks the exchange fields too', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-config"]').click();

    const ep = await authedPage.evaluate(() => {
      const app = window.Alpine.$data(document.body);
      app.editConfig({
        id: 1,
        name: 'loaded',
        env_name: 'dev',
        config_data: { api_endpoints: { orders: { base_url: 'https://api.example.com/orders' } } },
      });
      const endpoint = app.configModal.apiEndpoints[0];
      return {
        keys: Object.keys(endpoint),
        exchange: endpoint.exchange,
        exchangePretty: endpoint.exchangePretty,
        exchangeOpen: endpoint.exchangeOpen,
      };
    });

    expect(ep.keys).toEqual(expect.arrayContaining(['exchange', 'exchangePretty', 'exchangeOpen']));
    expect(ep.exchange).toBeNull();
    expect(ep.exchangePretty).toBe(true);
    expect(ep.exchangeOpen).toBe(false);
  });

  test('exchangeBody pretty-prints JSON, passes unparseable bodies through, and names a dropped body', async ({ authedPage }) => {
    await authedPage.goto('/');

    const out = await authedPage.evaluate(() => {
      const app = window.Alpine.$data(document.body);
      return {
        missing: app.exchangeBody(null, true),
        undef: app.exchangeBody(undefined, true),
        raw: app.exchangeBody('{"a":1}', false),
        pretty: app.exchangeBody('{"a":1}', true),
        // The body that will not parse is exactly the signal being hunted, so it
        // must render as-is rather than blow up the panel.
        notJson: app.exchangeBody('<html>proxy interstitial</html>', true),
        empty: app.exchangeBody('', true),
      };
    });

    expect(out.missing).toBe('<NONE SENT>');
    expect(out.undef).toBe('<NONE SENT>');
    expect(out.raw).toBe('{"a":1}');
    expect(out.pretty).toBe('{\n  "a": 1\n}');
    expect(out.notJson).toBe('<html>proxy interstitial</html>');
    expect(out.empty).toBe('');
  });

  test('Test keeps the exchange on success and on failure', async ({ authedPage }) => {
    await openConfigModalWithOneEndpoint(authedPage);

    await authedPage.route('**/api/adapters/rest-api/test', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, message: 'Connection successful', latency_ms: 12, exchange: SAMPLE_EXCHANGE }),
      })
    );
    await authedPage.evaluate(() => window.Alpine.$data(document.body).testApiEndpoint(0));
    await expect
      .poll(() => authedPage.evaluate(() => window.Alpine.$data(document.body).configModal.apiEndpoints[0].exchange?.response?.status))
      .toBe(502);

    // A 4xx/5xx from the endpoint itself: the exchange rides the error detail.
    await authedPage.unroute('**/api/adapters/rest-api/test');
    await authedPage.route('**/api/adapters/rest-api/test', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
    );
    await authedPage.evaluate(() => window.Alpine.$data(document.body).testApiEndpoint(0));
    await expect
      .poll(() => authedPage.evaluate(() => window.Alpine.$data(document.body).configModal.apiEndpoints[0].exchange))
      .toBeNull();
  });

  test('Preview keeps the exchange from a 502 detail so the failing pull is visible', async ({ authedPage }) => {
    await openConfigModalWithOneEndpoint(authedPage);

    await authedPage.route('**/api/adapters/rest-api/preview', (route) =>
      route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: { message: 'Cannot parse API response as json', exchange: SAMPLE_EXCHANGE },
        }),
      })
    );
    await authedPage.evaluate(() => window.Alpine.$data(document.body).previewApiEndpoint(0));

    await expect
      .poll(() => authedPage.evaluate(() => window.Alpine.$data(document.body).configModal.apiEndpoints[0].previewError))
      .toContain('Cannot parse API response as json');
    await expect
      .poll(() => authedPage.evaluate(() => window.Alpine.$data(document.body).configModal.apiEndpoints[0].exchange?.response?.status))
      .toBe(502);
  });

  test('the inspector panel renders the request as sent and the response as received', async ({ authedPage }) => {
    await openConfigModalWithOneEndpoint(authedPage);
    await authedPage.evaluate((exchange) => {
      const ep = window.Alpine.$data(document.body).configModal.apiEndpoints[0];
      ep.exchange = exchange;
      ep.exchangeOpen = true;
    }, SAMPLE_EXCHANGE);
    await waitForAlpineFlush(authedPage);

    const panel = authedPage.locator('[data-testid="api-exchange-panel-0"]');
    await expect(panel).toBeVisible();

    // The status must never be colour-only.
    await expect(authedPage.locator('[data-testid="api-exchange-status-0"]')).toContainText('502');

    const request = authedPage.locator('[data-testid="api-exchange-request-0"]');
    await expect(request).toContainText('GET');
    await expect(request).toContainText('https://api.example.com/orders?limit=1');
    // The dropped request body is the signal, so it gets its own marker.
    await expect(authedPage.locator('[data-testid="api-exchange-request-none-0"]')).toContainText('NONE SENT');

    const response = authedPage.locator('[data-testid="api-exchange-response-0"]');
    await expect(response).toContainText('proxy interstitial');
    await expect(response).toContainText('text/html');

    // Redaction is by header NAME pattern and is deliberately incomplete - the
    // copy must not let a masked value read as proof everything was caught.
    await expect(panel).toContainText('redacted by name');

    // Long content scrolls inside its own pane, never the modal.
    const overflow = await authedPage
      .locator('[data-testid="api-exchange-request-0"] .exchange-pre-wrap')
      .first()
      .evaluate((el) => window.getComputedStyle(el).overflowX);
    expect(overflow).toBe('auto');
  });

  test('the disclosure toggles exchangeOpen and reports its state', async ({ authedPage }) => {
    await openConfigModalWithOneEndpoint(authedPage);
    await authedPage.evaluate((exchange) => {
      window.Alpine.$data(document.body).configModal.apiEndpoints[0].exchange = exchange;
    }, SAMPLE_EXCHANGE);
    await waitForAlpineFlush(authedPage);

    const toggle = authedPage.locator('[data-testid="api-exchange-toggle-0"]');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(authedPage.locator('[data-testid="api-exchange-panel-0"]')).toBeVisible();
  });

  test('the Pretty/Raw toggle reformats the response body in place', async ({ authedPage }) => {
    await openConfigModalWithOneEndpoint(authedPage);
    await authedPage.evaluate(() => {
      const ep = window.Alpine.$data(document.body).configModal.apiEndpoints[0];
      ep.exchange = {
        request: { method: 'GET', url: 'https://api.example.com/orders', headers: {}, body: null, truncated: false },
        response: {
          status: 200, elapsed_ms: 5, bytes: 9, content_type: 'application/json',
          redirects: 0, headers: {}, body: '{"a":1}', truncated: false, binary: false,
        },
      };
      ep.exchangeOpen = true;
    });
    await waitForAlpineFlush(authedPage);

    const body = authedPage.locator('[data-testid="api-exchange-response-body-0"]');
    await expect(body).toHaveText('{\n  "a": 1\n}');

    await authedPage.locator('[data-testid="api-exchange-pretty-toggle-0"]').click();
    await expect(body).toHaveText('{"a":1}');
  });
});
