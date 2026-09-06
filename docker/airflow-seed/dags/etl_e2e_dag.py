from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="etl_orders_daily",
    description="E2E fixture DAG for live Airflow tests",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["e2e"],
) as dag:
    extract_orders = BashOperator(task_id="extract_orders", bash_command="echo extracting orders")
    transform_orders = BashOperator(task_id="transform_orders", bash_command="echo transforming orders")
    extract_orders >> transform_orders
