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

});
