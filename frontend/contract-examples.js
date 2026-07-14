/* Example data contracts for the Contracts tab.
 *
 * Each example follows the shape of the leading industry standards
 * (Data Contract Specification by Andrew Jones / Bitol Open Data Contract
 * Standard): info + ownership + SLA, an explicit `schema` with field-level
 * types and `examples`, `quality` rules, and `terms`/usage notes.
 *
 * The app stores descriptive contracts and derives DQ rules live from the
 * linked source_job, so the `schema`/`quality` blocks here are the
 * prescriptive intent the user should configure on the source job. The
 * `contract` block maps 1:1 to the New Contract form.
 */
window.CONTRACT_EXAMPLES = [
  {
    id: 'orders',
    title: 'Orders contract',
    summary: 'Ownership, SLA and quality for the nightly e-commerce orders dataset.',
    contract: {
      name: 'orders_v1',
      source_job: 'orders_etl',
      owner: 'data-eng@company.com',
      sla_hours: 4,
      consumers_raw: 'finance, ops, analytics',
      breach_severity: 'error',
      version: '1.0',
    },
    schema: [
      { name: 'order_id', type: 'string', required: true, description: 'Unique order identifier', example: 'ORD-100431' },
      { name: 'customer_id', type: 'string', required: true, description: 'Owning customer', example: 'CUST-2291' },
      { name: 'amount', type: 'decimal', required: true, description: 'Order total, >= 0', example: '129.90' },
      { name: 'status', type: 'string', required: true, description: 'One of pending/paid/shipped', example: 'paid' },
      { name: 'created_at', type: 'timestamp', required: true, description: 'Order creation time (UTC)', example: '2026-07-12T09:14:00Z' },
    ],
    quality: [
      'order_id is unique (key column)',
      'order_id, customer_id, amount, status, created_at are not null',
      'amount >= 0',
      'status in (pending, paid, shipped)',
      'at least one row produced per run',
    ],
    usage:
      '1. In the Config tab, give the source job these DQ params so the contract can derive them:\n' +
      '   null_check_columns: [order_id, customer_id, amount, status, created_at]\n' +
      '   key_columns: [order_id]\n' +
      '2. Click "Use example" to create orders_v1, then the contract auto-derives the rules above.\n' +
      '3. When orders_etl FAILs/ERRORs a breach opens and a contract.breached webhook fires to data-eng@company.com.\n' +
      '4. Consumers read the live contract: GET /api/contracts/orders_v1/rules  and  /schema .',
  },
  {
    id: 'payments',
    title: 'Payments reconciliation contract',
    summary: 'Tight SLA for the finance reconciliation feed consumed by reporting.',
    contract: {
      name: 'payments_v1',
      source_job: 'payments_reconciliation',
      owner: 'finance-data@company.com',
      sla_hours: 2,
      consumers_raw: 'finance, reporting',
      breach_severity: 'error',
      version: '1.0',
    },
    schema: [
      { name: 'txn_id', type: 'string', required: true, description: 'Unique transaction id', example: 'TXN-88421' },
      { name: 'account_id', type: 'string', required: true, description: 'Settlement account', example: 'ACC-771' },
      { name: 'amount', type: 'decimal', required: true, description: 'Settled amount', example: '-42.10' },
      { name: 'currency', type: 'string', required: true, description: 'ISO 4217 code', example: 'USD' },
      { name: 'settled_at', type: 'timestamp', required: true, description: 'Settlement time (UTC)', example: '2026-07-12T08:00:00Z' },
    ],
    quality: [
      'txn_id is unique (key column)',
      'txn_id, account_id, amount, currency, settled_at are not null',
      'currency matches ^[A-Z]{3}$',
      'at least one row produced per run',
    ],
    usage:
      '1. On payments_reconciliation set: null_check_columns: [txn_id, account_id, amount, currency, settled_at], key_columns: [txn_id].\n' +
      '2. "Use example" creates payments_v1 with a 2h SLA — breaches escalate after 2h of downtime.\n' +
      '3. Reporting team subscribes as a consumer; they poll GET /api/contracts/payments_v1/status for OK/BREACHED/OVERDUE.\n' +
      '4. On recovery the breach auto-resolves and fires contract.resolved with duration_hours + met_sla.',
  },
  {
    id: 'user_signups',
    title: 'User signups contract',
    summary: 'Daily product event feed; warn-only severity so growth is alerted but not blocked.',
    contract: {
      name: 'user_signups_v1',
      source_job: 'user_events_etl',
      owner: 'growth-data@company.com',
      sla_hours: 24,
      consumers_raw: 'growth, marketing',
      breach_severity: 'warn',
      version: '1.0',
    },
    schema: [
      { name: 'event_id', type: 'string', required: true, description: 'Unique event id', example: 'EV-55120' },
      { name: 'user_id', type: 'string', required: true, description: 'Signing-up user', example: 'U-3391' },
      { name: 'channel', type: 'string', required: false, description: 'Acquisition channel', example: 'organic' },
      { name: 'signup_at', type: 'timestamp', required: true, description: 'Signup time (UTC)', example: '2026-07-11T17:42:00Z' },
    ],
    quality: [
      'event_id is unique (key column)',
      'event_id, user_id, signup_at are not null',
      'at least one row produced per run',
    ],
    usage:
      '1. On user_events_etl set: null_check_columns: [event_id, user_id, signup_at], key_columns: [event_id].\n' +
      '2. "Use example" creates user_signups_v1 with breach_severity=warn — failures warn growth but do not block the pipeline.\n' +
      '3. Daily cadence: 24h SLA means a missed day escalates. Marketing reads GET /api/contracts/user_signups_v1/schema for the latest columns.',
  },
  {
    id: 'inventory',
    title: 'Inventory snapshot contract',
    summary: 'Intraday warehouse snapshot with PII-free operational fields for ops.',
    contract: {
      name: 'inventory_v1',
      source_job: 'inventory_snapshot',
      owner: 'ops-data@company.com',
      sla_hours: 12,
      consumers_raw: 'warehouse, ops',
      breach_severity: 'error',
      version: '1.0',
    },
    schema: [
      { name: 'sku', type: 'string', required: true, description: 'Stock keeping unit', example: 'SKU-10293' },
      { name: 'warehouse_id', type: 'string', required: true, description: 'Fulfilment centre', example: 'WH-EAST' },
      { name: 'on_hand', type: 'integer', required: true, description: 'Units in stock, >= 0', example: '340' },
      { name: 'snapshot_at', type: 'timestamp', required: true, description: 'Snapshot time (UTC)', example: '2026-07-12T12:00:00Z' },
    ],
    quality: [
      'sku + warehouse_id is unique (key column)',
      'sku, warehouse_id, on_hand, snapshot_at are not null',
      'on_hand >= 0',
      'at least one row produced per run',
    ],
    usage:
      '1. On inventory_snapshot set: null_check_columns: [sku, warehouse_id, on_hand, snapshot_at], key_columns: [sku, warehouse_id].\n' +
      '2. "Use example" creates inventory_v1 (12h SLA) so ops is alerted within half a day of a stale feed.\n' +
      '3. Warehouse team consumes the contract via GET /api/contracts/inventory_v1/status before publishing downstream dashboards.',
  },
];
