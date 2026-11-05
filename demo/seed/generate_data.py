"""Deterministic seed data for the ATOM regression demo.

Run as a script, it writes files to the shared volume and loads the simulated
Production and Test systems. ``build_dataset`` is pure, so the seeded differences
can be unit tested without any of those systems.

Differences seeded (everything else is identical between Test and Production):

    fee_amount   intentionally changed in the even-numbered feeds and in the trades
                 table: the expected difference a reviewer accepts
    gl_005       a record missing from Test
    gl_008       an additional record in Test
    gl_003       one wrong notional (XML)
    trades       one wrong notional
    positions    one row missing from Postgres, compared with the CrushFTP file
    BO summary   the Production amount total differs from the live report
    BO daily     its Production copy arrives late (bo-late/), which errors the first
                 run: the unintended failure the demo restarts from
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

BUSINESS_DATE = "2026-06-03"
PRIOR_DATE = "2026-06-02"
# ETL numbers in each of the four sets.
ETL_SETS = {1: range(1, 4), 2: range(4, 10), 3: range(10, 15), 4: range(15, 19)}
ROWS_PER_FEED = 20
TRADE_ROWS = 200
POSITION_ROWS = 60
BALANCE_ROWS = 40
# These ETLs write a second CSV feed, so 18 ETLs produce 20 file pairs.
ETLS_WITH_EXTRA_FEED = {4, 10}

MISSING_RECORD_FEED = "gl_005"
ADDITIONAL_RECORD_FEED = "gl_008"
WRONG_VALUE_FEED = "gl_003"
LATE_BO_BASELINE = "daily_sales"

BOOKS = ("EQ-LDN", "EQ-NYC", "FX-LDN", "FI-SGP", "CR-FRA")
CURRENCIES = ("USD", "EUR", "GBP", "SGD")
TRADERS = ("A.KHAN", "B.OSEI", "C.WU", "D.ROSSI")


@dataclass(frozen=True)
class Feed:
    name: str
    etl: str
    fmt: str
    prod_rows: list[dict] = field(repr=False)
    test_rows: list[dict] = field(repr=False)

    @property
    def file_name(self) -> str:
        return f"{self.name}.{self.fmt}"


@dataclass(frozen=True)
class Dataset:
    business_date: str
    etls: list[str]
    feeds: list[Feed]
    trades_prod: list[dict]
    trades_test: list[dict]
    positions_prod: list[dict]
    positions_test: list[dict]
    balances: list[dict]
    bo_baselines: dict[str, list[dict]]


def etl_name(number: int) -> str:
    set_number = next(n for n, numbers in ETL_SETS.items() if number in numbers)
    return f"SET{set_number}_ETL_{number:03d}"


def _trade_rows(rng: random.Random, count: int, prefix: str) -> list[dict]:
    return [
        {
            "trade_id": f"{prefix}-{index:05d}",
            "book": rng.choice(BOOKS),
            "currency": rng.choice(CURRENCIES),
            "notional": round(rng.uniform(10_000, 5_000_000), 2),
            "fee_amount": round(rng.uniform(5, 900), 2),
            "trader": rng.choice(TRADERS),
        }
        for index in range(1, count + 1)
    ]


def _apply_fee_change(rows: list[dict]) -> list[dict]:
    """The change under test: the new fee model raises fee_amount by 10%."""
    return [{**row, "fee_amount": round(row["fee_amount"] * 1.1, 2)} for row in rows]


def _with_wrong_notional(rows: list[dict], index: int) -> list[dict]:
    changed = [dict(row) for row in rows]
    changed[index]["notional"] = round(changed[index]["notional"] + 12_500.00, 2)
    return changed


def _feed_test_rows(name: str, number: int, prod_rows: list[dict]) -> list[dict]:
    if name == MISSING_RECORD_FEED:
        return [dict(row) for row in prod_rows[:7] + prod_rows[8:]]
    if name == ADDITIONAL_RECORD_FEED:
        extra = {**prod_rows[0], "trade_id": f"{name.upper()}-99999"}
        return [dict(row) for row in prod_rows] + [extra]
    if name == WRONG_VALUE_FEED:
        return _with_wrong_notional(prod_rows, 4)
    if number % 2 == 0:
        return _apply_fee_change(prod_rows)
    return [dict(row) for row in prod_rows]


def _build_feed(rng: random.Random, name: str, number: int, fmt: str) -> Feed:
    prod_rows = _trade_rows(rng, ROWS_PER_FEED, name.upper())
    return Feed(
        name=name,
        etl=etl_name(number),
        fmt=fmt,
        prod_rows=prod_rows,
        test_rows=_feed_test_rows(name, number, prod_rows),
    )


def _bo_baselines() -> dict[str, list[dict]]:
    """Production copies of the three reports in docker/sapbo-mock/server.py. Each
    matches the mock's live rows, except the deliberately different amount total."""
    return {
        "sales_orders": [
            {"id": 1, "sku": "A100", "amount": 25.50, "status": "SHIPPED"},
            {"id": 2, "sku": "B200", "amount": 50.00, "status": "OPEN"},
            {"id": 3, "sku": "C300", "amount": 75.00, "status": "SHIPPED"},
        ],
        "sales_summary": [
            {"metric": "orders", "value": 3},
            {"metric": "amount", "value": 151.50},
        ],
        # Doc 1003 answers its date prompt with these rows for 2026-06-03 only.
        "daily_sales": [
            {"id": 4, "sku": "D400", "amount": 10.25, "status": "OPEN"},
            {"id": 5, "sku": "E500", "amount": 99.99, "status": "SHIPPED"},
        ],
    }


def build_dataset(business_date: str = BUSINESS_DATE) -> Dataset:
    rng = random.Random(f"atom-demo-{business_date}")
    numbers = [number for group in ETL_SETS.values() for number in group]
    feeds: list[Feed] = []
    for number in numbers:
        fmt = "xml" if number % 3 == 0 else "csv"
        feeds.append(_build_feed(rng, f"gl_{number:03d}", number, fmt))
        if number in ETLS_WITH_EXTRA_FEED:
            feeds.append(_build_feed(rng, f"gl_{number:03d}_adj", number, "csv"))

    trades_prod = _trade_rows(rng, TRADE_ROWS, "TRD")
    trades_test = _with_wrong_notional(_apply_fee_change(trades_prod), 41)

    positions_prod = [
        {
            "position_id": f"POS-{index:04d}",
            "book": rng.choice(BOOKS),
            "currency": rng.choice(CURRENCIES),
            "quantity": rng.randint(100, 90_000),
            "market_value": round(rng.uniform(5_000, 9_000_000), 2),
        }
        for index in range(1, POSITION_ROWS + 1)
    ]
    positions_test = [dict(row) for i, row in enumerate(positions_prod) if i != 17]

    balances = [
        {
            "account_id": f"ACC-{index:04d}",
            "currency": rng.choice(CURRENCIES),
            "balance": round(rng.uniform(-2_000_000, 9_000_000), 2),
        }
        for index in range(1, BALANCE_ROWS + 1)
    ]

    return Dataset(
        business_date=business_date,
        etls=[etl_name(number) for number in numbers],
        feeds=feeds,
        trades_prod=trades_prod,
        trades_test=trades_test,
        positions_prod=positions_prod,
        positions_test=positions_test,
        balances=balances,
        bo_baselines=_bo_baselines(),
    )


def _csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _xml_bytes(rows: list[dict]) -> bytes:
    root = ElementTree.Element("records")
    for row in rows:
        record = ElementTree.SubElement(root, "record")
        for column, value in row.items():
            ElementTree.SubElement(record, column).text = str(value)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def feed_bytes(feed: Feed, rows: list[dict]) -> bytes:
    return _xml_bytes(rows) if feed.fmt == "xml" else _csv_bytes(rows)


def write_local_files(dataset: Dataset, root: Path) -> None:
    """Everything other services read from the shared volume."""
    for feed in dataset.feeds:
        folder = root / "templates" / feed.etl
        folder.mkdir(parents=True, exist_ok=True)
        (folder / feed.file_name).write_bytes(feed_bytes(feed, feed.test_rows))

    jobs = {name: "succeeded" for name in dataset.etls}
    (root / "sapds-jobs.json").write_text(json.dumps(jobs, indent=2), encoding="utf-8")

    crushftp = root / "crushftp"
    prod = crushftp / "prod" / dataset.business_date
    prod.mkdir(parents=True, exist_ok=True)
    (prod / "positions.csv").write_bytes(_csv_bytes(dataset.positions_prod))
    # The file watcher at the start of Set 1 waits here. The demo script delivers the
    # arrival signal on screen, which holds the run until the presenter is ready.
    (crushftp / "inbound" / dataset.business_date).mkdir(parents=True, exist_ok=True)

    bo = root / "bo" / dataset.business_date
    late = root / "bo-late" / dataset.business_date
    bo.mkdir(parents=True, exist_ok=True)
    late.mkdir(parents=True, exist_ok=True)
    for name, rows in dataset.bo_baselines.items():
        folder = late if name == LATE_BO_BASELINE else bo
        (folder / f"{name}.csv").write_bytes(_csv_bytes(rows))


def _retry(action, description: str, attempts: int = 60, delay: float = 2.0):
    """Services report healthy before they accept work, and the S3 simulator needs a moment."""
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - any failure here means "not ready yet"
            if attempt == attempts:
                raise RuntimeError(f"{description} was not ready: {exc}") from exc
            time.sleep(delay)


def upload_baselines(
    dataset: Dataset, endpoint_url: str, bucket: str, published_bucket: str, key: str, secret: str,
) -> None:
    import boto3

    client = boto3.client(
        "s3", endpoint_url=endpoint_url, aws_access_key_id=key,
        aws_secret_access_key=secret, region_name="us-east-1",
    )
    _retry(lambda: client.create_bucket(Bucket=bucket), "S3 simulator")
    client.create_bucket(Bucket=published_bucket)
    for feed in dataset.feeds:
        client.put_object(
            Bucket=bucket,
            Key=f"{dataset.business_date}/{feed.fmt}/{feed.file_name}",
            Body=feed_bytes(feed, feed.prod_rows),
        )


def load_trades(dsn: str, schema: str, rows: list[dict], business_date: str, prior_rows: list[dict]) -> None:
    import psycopg

    with _retry(lambda: psycopg.connect(dsn), f"Database for {schema}") as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(f"DROP TABLE IF EXISTS {schema}.gl_trades")
        cur.execute(
            f"CREATE TABLE {schema}.gl_trades ("
            "business_date date NOT NULL, trade_id text NOT NULL, book text, currency text, "
            "notional double precision, fee_amount double precision, trader text, "
            "PRIMARY KEY (business_date, trade_id))"
        )
        for date_value, date_rows in ((business_date, rows), (PRIOR_DATE, prior_rows)):
            cur.executemany(
                f"INSERT INTO {schema}.gl_trades VALUES ('{date_value}', "
                "%(trade_id)s, %(book)s, %(currency)s, %(notional)s, %(fee_amount)s, %(trader)s)",
                date_rows,
            )


def load_positions(dsn: str, rows: list[dict], business_date: str) -> None:
    import psycopg

    with _retry(lambda: psycopg.connect(dsn), "Postgres") as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS positions")
        cur.execute(
            "CREATE TABLE positions ("
            "business_date date NOT NULL, position_id text NOT NULL, book text, currency text, "
            "quantity bigint, market_value double precision, PRIMARY KEY (business_date, position_id))"
        )
        cur.executemany(
            f"INSERT INTO positions VALUES ('{business_date}', "
            "%(position_id)s, %(book)s, %(currency)s, %(quantity)s, %(market_value)s)",
            rows,
        )


def load_balances(dsn: str, schema: str, rows: list[dict], business_date: str) -> None:
    import psycopg

    with _retry(lambda: psycopg.connect(dsn), f"Database for {schema}") as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.gl_balances")
        cur.execute(
            f"CREATE TABLE {schema}.gl_balances (business_date date NOT NULL, account_id text NOT NULL, "
            "currency text, balance double precision, PRIMARY KEY (business_date, account_id))"
        )
        cur.executemany(
            f"INSERT INTO {schema}.gl_balances VALUES ('{business_date}', "
            "%(account_id)s, %(currency)s, %(balance)s)",
            rows,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("DEMO_DATA_ROOT", "/demo-data"))
    args = parser.parse_args()

    dataset = build_dataset()
    write_local_files(dataset, Path(args.root))
    upload_baselines(
        dataset,
        os.environ["DEMO_S3_ENDPOINT"], os.environ["DEMO_S3_BUCKET"], os.environ["DEMO_S3_PUBLISHED_BUCKET"],
        os.environ["DEMO_S3_KEY"], os.environ["DEMO_S3_SECRET"],
    )
    qa, prod = os.environ["DEMO_POSTGRES_DSN"], os.environ["DEMO_ORACLE_SIM_DSN"]
    # The prior day's rows are different on purpose: a query ignoring the business
    # date would return them too, and the row counts would no longer match.
    prior = _trade_rows(random.Random("atom-demo-prior"), 25, "TRD-PRIOR")
    load_trades(qa, "qa_owner", dataset.trades_test, dataset.business_date, prior)
    load_trades(prod, "prod_owner", dataset.trades_prod, dataset.business_date, prior)
    load_positions(qa, dataset.positions_test, dataset.business_date)
    load_positions(prod, dataset.positions_prod, dataset.business_date)
    load_balances(qa, "qa_owner", dataset.balances, dataset.business_date)
    load_balances(prod, "prod_owner", dataset.balances, dataset.business_date)
    print(
        f"Seeded {len(dataset.etls)} ETLs and {len(dataset.feeds)} feeds "
        f"for {dataset.business_date}",
        flush=True,
    )


if __name__ == "__main__":
    main()
