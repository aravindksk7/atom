"""Example transform test — copy this pattern for real business transforms."""
import pandas as pd

from etl_framework.transform_testing.harness import TransformCase

DAILY_REVENUE_SQL = """
SELECT
    order_date,
    SUM(amount) FILTER (WHERE status <> 'CANCELLED') AS revenue
FROM orders
GROUP BY order_date
ORDER BY order_date
"""


def test_cancelled_orders_excluded_from_revenue():
    mismatches = TransformCase(
        transform_sql=DAILY_REVENUE_SQL,
        inputs={"orders": pd.DataFrame({
            "order_date": ["2026-07-01", "2026-07-01", "2026-07-02"],
            "amount": [100.0, 50.0, 75.0],
            "status": ["COMPLETE", "CANCELLED", "COMPLETE"],
        })},
        expected=pd.DataFrame({
            "order_date": ["2026-07-01", "2026-07-02"],
            "revenue": [100.0, 75.0],
        }),
        key_columns=["order_date"],
    ).run()
    assert mismatches == [], f"Transform diverged: {mismatches}"
