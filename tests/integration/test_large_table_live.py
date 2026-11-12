"""Live Oracle / PostgreSQL / SQL Server: streaming and pushdown reconciliation.

Every scenario runs the same query through the in-memory engine (the
reference answer) and through ``streaming``, ``pushdown`` and ``auto``, and
requires identical counts. Pushdown scenarios also check how many buckets it
had to fetch, which is the whole point of the strategy.

Start: docker compose -f docker-compose.integration.yml up -d --wait oracle postgres sqlserver
Run:   RUN_LIVE_LARGE_TABLE_TESTS=1 python -m pytest tests/integration/test_large_table_live.py -v
Stop:  docker compose -f docker-compose.integration.yml down -v --remove-orphans
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

oracledb = pytest.importorskip("oracledb")
psycopg = pytest.importorskip("psycopg")
pyodbc = pytest.importorskip("pyodbc")

from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine
from etl_framework.reconciliation import large_table
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.runner.state import TestStatus

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_LARGE_TABLE_TESTS") != "1",
    reason="set RUN_LIVE_LARGE_TABLE_TESTS=1 and start oracle, postgres and sqlserver "
           "from docker-compose.integration.yml",
)

ORA = dict(host=os.getenv("LIVE_ORACLE_HOST", "127.0.0.1"), port=int(os.getenv("LIVE_ORACLE_PORT", "1521")),
           service="FREEPDB1", user="e2e_src", password="Oracle_Test_12345")
PG = dict(host=os.getenv("LIVE_POSTGRES_HOST", "127.0.0.1"), port=int(os.getenv("LIVE_POSTGRES_PORT", "25432")),
          user="atom", password="Atom_Test_12345", db="atom_live")
MS = dict(host=os.getenv("LIVE_SQLSERVER_HOST", "127.0.0.1"), port=int(os.getenv("LIVE_SQLSERVER_PORT", "14333")),
          user="sa", password="Atom_Test_12345!", db="atom_large",
          driver=os.getenv("LIVE_SQLSERVER_ODBC_DRIVER", "ODBC Driver 18 for SQL Server"))

ROWS = int(os.getenv("LIVE_LARGE_TABLE_ROWS", "20000"))
BASE_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)

FULL_QUERY = "SELECT id, code, amount, ratio, qty, created, updated_tz, flag, note FROM recon_orders"
# SQL Server's datetimeoffset is not readable through pyodbc, so the table
# there has no updated_tz and cross-database queries leave it out.
PORTABLE_QUERY = "SELECT id, code, amount, ratio, qty, created, flag, note FROM recon_orders"
STRATEGIES = ("streaming", "pushdown", "auto")


# ---------------------------------------------------------------------------
# Connections and readiness
# ---------------------------------------------------------------------------

def _ora():
    return oracledb.connect(user=ORA["user"], password=ORA["password"],
                            dsn=f"{ORA['host']}:{ORA['port']}/{ORA['service']}")


def _pg():
    return psycopg.connect(host=PG["host"], port=PG["port"], user=PG["user"],
                           password=PG["password"], dbname=PG["db"], autocommit=True)


def _ms(database="master"):
    return pyodbc.connect(
        f"DRIVER={{{MS['driver']}}};SERVER={MS['host']},{MS['port']};DATABASE={database};"
        f"UID={MS['user']};PWD={MS['password']};TrustServerCertificate=yes;Connect Timeout=5;",
        autocommit=True,
    )


def _wait(connect, name):
    last = None
    for _ in range(90):
        try:
            connect().close()
            return
        except Exception as exc:  # noqa: BLE001 - waiting for container start-up
            last = exc
            time.sleep(2)
    pytest.fail(f"{name} did not become ready: {last}")


@pytest.fixture(scope="module", autouse=True)
def _ready():
    _wait(_ora, "Oracle")
    _wait(_pg, "PostgreSQL")
    if MS["driver"] not in pyodbc.drivers():
        pytest.skip(f"ODBC driver {MS['driver']!r} is not installed")
    _wait(_ms, "SQL Server")
    with _ms() as conn:
        conn.cursor().execute(f"IF DB_ID('{MS['db']}') IS NULL CREATE DATABASE {MS['db']}")


def _engine_for(kind):
    if kind == "oracle":
        cfg = EnvironmentConfig(name="oracle", db_type="oracle", db_host=ORA["host"], db_port=ORA["port"],
                                db_name=ORA["service"], db_user=ORA["user"], db_password=ORA["password"])
    elif kind == "postgres":
        cfg = EnvironmentConfig(name="postgres", db_type="postgresql", db_host=PG["host"], db_port=PG["port"],
                                db_name=PG["db"], db_user=PG["user"], db_password=PG["password"],
                                db_sslmode="disable")
    else:
        cfg = EnvironmentConfig(name="sqlserver", db_type="mssql", db_host=MS["host"], db_port=MS["port"],
                                db_name=MS["db"], db_user=MS["user"], db_password=MS["password"],
                                db_driver=MS["driver"])
    return DBEngine(cfg)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _rows(n=ROWS):
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "id": i,
            "code": f"C{i % 977:04d}",
            "amount": Decimal(i * 7 % 100000) / Decimal(100) + Decimal("0.0125"),
            "ratio": (i % 1000) / 7.0,
            "qty": None if i % 97 == 0 else i % 500 - 250,          # negatives and NULLs
            "created": (BASE_TS + timedelta(seconds=i * 37)).replace(tzinfo=None),
            "updated_tz": BASE_TS + timedelta(seconds=i * 11, microseconds=(i * 7919) % 1_000_000),
            "flag": i % 3 == 0,
            "note": None if i % 11 == 0 else ("café ✓ " if i % 13 == 0 else "note ") + str(i),
        })
    return rows


def _seed_oracle(rows, *, extra_ddl=""):
    with _ora() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN EXECUTE IMMEDIATE 'DROP TABLE recon_orders PURGE'; "
                    "EXCEPTION WHEN OTHERS THEN NULL; END;")
        cur.execute(
            "CREATE TABLE recon_orders (id NUMBER(10) NOT NULL, code VARCHAR2(20) NOT NULL, "
            "amount NUMBER(12,4), ratio BINARY_DOUBLE, qty NUMBER(10), created DATE, "
            "updated_tz TIMESTAMP(6) WITH TIME ZONE, flag NUMBER(1), note VARCHAR2(200)"
            f"{extra_ddl}, PRIMARY KEY (id, code))"
        )
        # oracledb binds datetime as DATE (whole seconds) unless told otherwise.
        cur.setinputsizes(updated=oracledb.DB_TYPE_TIMESTAMP)
        cur.executemany(
            "INSERT INTO recon_orders (id, code, amount, ratio, qty, created, updated_tz, flag, note) "
            "VALUES (:id, :code, :amount, :ratio, :qty, :created, "
            "FROM_TZ(CAST(:updated AS TIMESTAMP), '+00:00'), :flag, :note)",
            [{**{k: r[k] for k in ("id", "code", "amount", "ratio", "qty", "created", "note")},
              "updated": r["updated_tz"].replace(tzinfo=None), "flag": int(r["flag"])} for r in rows],
            batcherrors=False,
        )
        conn.commit()


def _seed_postgres(rows, *, extra_ddl=""):
    with _pg() as conn:
        conn.execute("DROP TABLE IF EXISTS recon_orders")
        conn.execute(
            "CREATE TABLE recon_orders (id integer NOT NULL, code varchar(20) NOT NULL, "
            "amount numeric(12,4), ratio double precision, qty integer, created timestamp, "
            "updated_tz timestamptz, flag boolean, note varchar(200)"
            f"{extra_ddl}, PRIMARY KEY (id, code))"
        )
        with conn.cursor() as cur:
            with cur.copy("COPY recon_orders (id, code, amount, ratio, qty, created, updated_tz, flag, note) "
                          "FROM STDIN") as copy:
                for r in rows:
                    copy.write_row([r[k] for k in ("id", "code", "amount", "ratio", "qty", "created",
                                                   "updated_tz", "flag", "note")])


def _seed_sqlserver(rows):
    with _ms(MS["db"]) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS dbo.recon_orders")
        cur.execute(
            "CREATE TABLE dbo.recon_orders (id INT NOT NULL, code VARCHAR(20) NOT NULL, "
            "amount DECIMAL(12,4), ratio FLOAT, qty INT, created DATETIME2(0), flag BIT, "
            "note NVARCHAR(200), PRIMARY KEY (id, code))"
        )
        cur.fast_executemany = True
        cur.executemany(
            "INSERT INTO dbo.recon_orders (id, code, amount, ratio, qty, created, flag, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(r["id"], r["code"], r["amount"], r["ratio"], r["qty"], r["created"], r["flag"], r["note"])
             for r in rows],
        )


def _mutate(rows):
    """Target-side copy with one difference of every kind, plus noise that
    must NOT count as a difference."""
    target = [dict(r) for r in rows]
    by_id = {r["id"]: r for r in target}
    by_id[10]["amount"] += Decimal("0.0001")                   # smallest NUMBER(12,4) step
    by_id[20]["ratio"] += 1e-12                                # inside the 1e-9 tolerance: equal
    by_id[30]["ratio"] += 1e-6                                 # outside: different
    by_id[40]["qty"] = None                                    # value -> NULL
    by_id[97]["qty"] = 5                                       # NULL -> value
    by_id[50]["created"] += timedelta(seconds=1)
    by_id[60]["updated_tz"] += timedelta(microseconds=1)       # microsecond precision
    by_id[70]["flag"] = not by_id[70]["flag"]
    by_id[13]["note"] = by_id[13]["note"].replace("é", "e")    # non-ASCII only change
    by_id[80]["note"] = by_id[80]["note"] + " "               # trailing space
    for missing in (100, 200, 300):                            # missing in target
        target.remove(by_id[missing])
    for extra in (ROWS + 1, ROWS + 2):                         # missing in source
        target.append({**rows[0], "id": extra})
    return target


# ---------------------------------------------------------------------------
# Running and comparing
# ---------------------------------------------------------------------------

def _run(source, target, query, strategy, caplog=None, **kwargs):
    kwargs.setdefault("key_columns", ["id"])
    src_engine, tgt_engine = _engine_for(source), _engine_for(target)
    try:
        engine = ReconciliationEngine(src_engine, tgt_engine, strategy=strategy,
                                      stream_batch_size=5_000, **kwargs)
        return engine.reconcile(query, f"{source}->{target}:{strategy}")
    finally:
        src_engine.dispose()
        tgt_engine.dispose()


def _counts(result):
    return {
        "status": result.status,
        "source_rows": result.source_row_count,
        "target_rows": result.target_row_count,
        "matched": result.matched_count,
        "missing_in_target": result.missing_in_target_count,
        "missing_in_source": result.missing_in_source_count,
        "value_mismatches": result.value_mismatch_count,
        "by_column": result.mismatch_by_column,
    }


def _assert_parity(source, target, query, caplog, **kwargs):
    expected = _counts(_run(source, target, query, "in_memory", **kwargs))
    results = {}
    for strategy in STRATEGIES:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="etl_framework.reconciliation.large_table"):
            results[strategy] = _run(source, target, query, strategy, **kwargs)
        assert _counts(results[strategy]) == expected, strategy
        results[strategy + "_log"] = caplog.text
    return expected, results


def _differing(log_text):
    match = re.search(r"\(pushdown\): src=\d+ tgt=\d+ buckets=\d+ differing=(\d+)", log_text)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def test_oracle_to_postgres_identical_needs_no_row_fetch(caplog):
    rows = _rows()
    _seed_oracle(rows)
    _seed_postgres(rows)
    expected, results = _assert_parity("oracle", "postgres", FULL_QUERY, caplog)
    assert expected["status"] == TestStatus.PASSED
    assert expected["matched"] == ROWS
    # The canonical text renders every column type the same way on both
    # databases, so no bucket differs and no row leaves either database.
    assert _differing(results["pushdown_log"]) == 0
    assert "fetched" not in results["pushdown_log"]


def test_oracle_to_postgres_every_difference_kind(caplog):
    rows = _rows()
    target = _mutate(rows)
    _seed_oracle(rows)
    _seed_postgres(target)
    expected, results = _assert_parity("oracle", "postgres", FULL_QUERY, caplog)
    assert expected["missing_in_target"] == 3
    assert expected["missing_in_source"] == 2
    assert expected["by_column"] == {
        "<row>": 5, "amount": 1, "ratio": 1, "qty": 2, "created": 1,
        "updated_tz": 1, "flag": 1, "note": 2,
    }
    # 9 changed rows + 5 missing/extra keys touch at most 14 buckets; the
    # tolerance-only change on id 20 may add one more.
    differing = _differing(results["pushdown_log"])
    assert differing is not None and 0 < differing <= 15


def test_postgres_to_oracle_direction(caplog):
    rows = _rows()
    _seed_oracle(_mutate(rows))
    _seed_postgres(rows)
    expected, _ = _assert_parity("postgres", "oracle", FULL_QUERY, caplog)
    # Target (Oracle) lost 3 rows and gained 2.
    assert expected["missing_in_target"] == 3 and expected["missing_in_source"] == 2


def test_oracle_empty_string_is_null(caplog):
    """Oracle stores '' as NULL; PostgreSQL keeps ''. Every strategy must
    report that as a difference, as the in-memory engine does."""
    rows = _rows(500)
    target = [dict(r) for r in rows]
    target[4]["note"] = ""
    source = [dict(r) for r in rows]
    source[4]["note"] = ""                                   # becomes NULL in Oracle
    _seed_oracle(source)
    _seed_postgres(target)
    expected, _ = _assert_parity("oracle", "postgres", FULL_QUERY, caplog)
    assert expected["by_column"] == {"note": 1}


def test_postgres_to_postgres(caplog):
    rows = _rows()
    _seed_postgres(rows)
    expected, results = _assert_parity("postgres", "postgres", FULL_QUERY, caplog)
    assert expected["status"] == TestStatus.PASSED
    assert _differing(results["pushdown_log"]) == 0


def test_sqlserver_to_postgres(caplog):
    rows = _rows()
    _seed_sqlserver(rows)
    _seed_postgres(_mutate(rows))
    expected, results = _assert_parity("sqlserver", "postgres", PORTABLE_QUERY, caplog)
    assert expected["missing_in_target"] == 3
    differing = _differing(results["pushdown_log"])
    assert differing is not None and 0 < differing <= 15


def test_sqlserver_to_oracle_identical(caplog):
    rows = _rows()
    _seed_sqlserver(rows)
    _seed_oracle(rows)
    expected, results = _assert_parity("sqlserver", "oracle", PORTABLE_QUERY, caplog)
    assert expected["status"] == TestStatus.PASSED
    assert _differing(results["pushdown_log"]) == 0


def test_composite_key(caplog):
    rows = _rows()
    target = _mutate(rows)
    _seed_oracle(rows)
    _seed_postgres(target)
    expected, _ = _assert_parity("oracle", "postgres", FULL_QUERY, caplog, key_columns=["id", "code"])
    assert expected["missing_in_target"] == 3


def test_exclusions_tolerances_and_schema_diff(caplog):
    rows = _rows(2_000)
    target = _mutate(rows)
    _seed_oracle(rows)
    _seed_postgres(target, extra_ddl=", only_in_pg integer")
    query = "SELECT * FROM recon_orders"
    expected, results = _assert_parity(
        "oracle", "postgres", query, caplog,
        exclude_columns=["note"], column_tolerances={"ratio": 1e-3}, datetime_tolerance_seconds=2,
    )
    assert "note" not in expected["by_column"]
    assert "ratio" not in expected["by_column"]                # 1e-6 change now tolerated
    assert "created" not in expected["by_column"]              # 1 s shift within 2 s
    assert results["pushdown"].schema_diff == {"missing_in_target": [], "extra_in_target": ["only_in_pg"]}


def test_unsupported_column_type_falls_back_to_streaming(caplog):
    """INTERVAL has no canonical rendering, so pushdown streams instead."""
    with _ora() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN EXECUTE IMMEDIATE 'DROP TABLE recon_iv PURGE'; EXCEPTION WHEN OTHERS THEN NULL; END;")
        cur.execute("CREATE TABLE recon_iv (id NUMBER(10) PRIMARY KEY, span INTERVAL DAY TO SECOND)")
        cur.executemany("INSERT INTO recon_iv VALUES (:1, NUMTODSINTERVAL(:2, 'SECOND'))",
                        [(i, i * 61) for i in range(1, 201)])
        conn.commit()
    with _pg() as conn:
        conn.execute("DROP TABLE IF EXISTS recon_iv")
        conn.execute("CREATE TABLE recon_iv (id integer PRIMARY KEY, span interval)")
        conn.execute("INSERT INTO recon_iv SELECT i, i * 61 * INTERVAL '1 second' FROM generate_series(1, 200) i")
    expected, results = _assert_parity("oracle", "postgres", "SELECT id, span FROM recon_iv", caplog)
    assert expected["status"] == TestStatus.PASSED
    assert "pushdown unavailable" in results["pushdown_log"]
    assert "cannot hash" in results["pushdown_log"]


# ---------------------------------------------------------------------------
# Large objects: BLOB / CLOB / bytea / varbinary
# ---------------------------------------------------------------------------

def _sys():
    return oracledb.connect(user="sys", password=ORA["password"],
                            dsn=f"{ORA['host']}:{ORA['port']}/{ORA['service']}",
                            mode=oracledb.AUTH_MODE_SYSDBA)


def _dbms_crypto(grant: bool):
    with _sys() as conn:
        verb = "GRANT EXECUTE ON SYS.DBMS_CRYPTO TO" if grant else "REVOKE EXECUTE ON SYS.DBMS_CRYPTO FROM"
        try:
            conn.cursor().execute(f"{verb} {ORA['user']}")
        except oracledb.DatabaseError as exc:
            if "ORA-01927" not in str(exc):          # revoking what was never granted
                raise


def _lob_rows(n=3_000, *, empty=b""):
    """``empty`` is what the every-50th "empty blob" row holds. Oracle stores
    an empty BLOB as NULL (as it does ''), so Oracle scenarios pass None."""
    rows = []
    for i in range(1, n + 1):
        if i % 50 == 0:
            payload = None
        elif i % 50 == 1:
            payload = empty
        elif i % 500 == 7:
            payload = bytes(range(256)) * 1200                   # ~300 KB, not valid UTF-8
        else:
            payload = f"payload-{i}".encode() * (i % 7 + 1)
        doc = None if i % 40 == 0 else ("Ünïcode ✓ " * (i % 5 + 1)) + str(i)
        rows.append({"id": i, "payload": payload, "doc": doc})
    return rows


def _seed_lobs_oracle(rows):
    with _ora() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN EXECUTE IMMEDIATE 'DROP TABLE recon_lobs PURGE'; EXCEPTION WHEN OTHERS THEN NULL; END;")
        cur.execute("CREATE TABLE recon_lobs (id NUMBER(10) PRIMARY KEY, payload BLOB, doc CLOB)")
        cur.setinputsizes(payload=oracledb.DB_TYPE_BLOB, doc=oracledb.DB_TYPE_CLOB)
        cur.executemany("INSERT INTO recon_lobs VALUES (:id, :payload, :doc)", rows)
        conn.commit()


def _seed_lobs_postgres(rows):
    with _pg() as conn:
        conn.execute("DROP TABLE IF EXISTS recon_lobs")
        conn.execute("CREATE TABLE recon_lobs (id integer PRIMARY KEY, payload bytea, doc text)")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO recon_lobs VALUES (%(id)s, %(payload)s, %(doc)s)", rows)


def _seed_lobs_sqlserver(rows):
    with _ms(MS["db"]) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS dbo.recon_lobs")
        cur.execute("CREATE TABLE dbo.recon_lobs (id INT PRIMARY KEY, payload VARBINARY(MAX), doc NVARCHAR(MAX))")
        cur.executemany("INSERT INTO dbo.recon_lobs VALUES (?, ?, ?)",
                        [(r["id"], r["payload"], r["doc"]) for r in rows])


def _lob_changes(rows):
    target = [dict(r) for r in rows]
    by_id = {r["id"]: r for r in target}
    by_id[7]["payload"] = by_id[7]["payload"][:-1] + b"\x00"    # last byte of a 300 KB blob
    by_id[2]["payload"] = None                                   # value -> NULL
    by_id[50]["payload"] = b"now set"                            # NULL -> value
    by_id[51]["payload"] = b"was empty"                          # empty -> value
    by_id[3]["doc"] = by_id[3]["doc"].replace("Ü", "U")         # one non-ASCII character
    return target


LOB_QUERY = "SELECT id, payload, doc FROM recon_lobs"


def test_blob_clob_pushdown_hashes_in_database(caplog):
    _dbms_crypto(True)
    rows = _lob_rows(empty=None)
    _seed_lobs_oracle(rows)
    _seed_lobs_postgres(rows)
    expected, results = _assert_parity("oracle", "postgres", LOB_QUERY, caplog)
    assert expected["status"] == TestStatus.PASSED
    assert _differing(results["pushdown_log"]) == 0              # BLOB = bytea, CLOB = text


def test_blob_clob_differences(caplog):
    _dbms_crypto(True)
    rows = _lob_rows(empty=None)
    _seed_lobs_oracle(rows)
    _seed_lobs_postgres(_lob_changes(rows))
    expected, results = _assert_parity("oracle", "postgres", LOB_QUERY, caplog)
    assert expected["by_column"] == {"payload": 4, "doc": 1}
    assert 0 < _differing(results["pushdown_log"]) <= 5
    # Pushdown never pulls the blobs themselves: mismatches show their MD5.
    blob = next(m for m in results["pushdown"].mismatches
                if m.column_name == "payload" and m.key_values["id"] == 7)
    assert isinstance(blob.source_value, str) and len(blob.source_value) == 32
    # Streaming moved the real bytes and reports them.
    streamed = next(m for m in results["streaming"].mismatches
                    if m.column_name == "payload" and m.key_values["id"] == 7)
    assert isinstance(streamed.source_value, (bytes, bytearray))


def test_blob_without_dbms_crypto_grant_streams(caplog):
    _dbms_crypto(False)
    try:
        rows = _lob_rows(500, empty=None)
        _seed_lobs_oracle(rows)
        _seed_lobs_postgres(_lob_changes(rows))
        expected, results = _assert_parity("oracle", "postgres", LOB_QUERY, caplog)
        assert expected["by_column"] == {"payload": 4, "doc": 1}
        assert "GRANT EXECUTE ON SYS.DBMS_CRYPTO" in results["pushdown_log"]
    finally:
        _dbms_crypto(True)


def test_varbinary_to_bytea(caplog):
    rows = _lob_rows()
    _seed_lobs_sqlserver(rows)
    _seed_lobs_postgres(_lob_changes(rows))
    expected, results = _assert_parity("sqlserver", "postgres", LOB_QUERY, caplog)
    assert expected["by_column"] == {"payload": 4, "doc": 1}
    assert 0 < _differing(results["pushdown_log"]) <= 5


def test_many_differing_buckets_filter_client_side(caplog, monkeypatch):
    """Past MAX_SERVER_SIDE_BUCKETS the fetch reads everything once and keeps
    only differing buckets locally; the answer must not change."""
    monkeypatch.setattr(large_table, "MAX_SERVER_SIDE_BUCKETS", 2)
    rows = _rows()
    target = [dict(r) for r in rows]
    for r in target[::500]:
        r["qty"] = (r["qty"] or 0) + 1
    _seed_oracle(rows)
    _seed_postgres(target)
    expected, results = _assert_parity("oracle", "postgres", FULL_QUERY, caplog)
    assert expected["by_column"] == {"qty": len(target[::500])}
    assert _differing(results["pushdown_log"]) > 2


def test_few_buckets_still_correct(caplog):
    rows = _rows(3_000)
    _seed_oracle(rows)
    _seed_postgres(_mutate(rows))
    _assert_parity("oracle", "postgres", FULL_QUERY, caplog, pushdown_buckets=1)


def test_empty_tables(caplog):
    _seed_oracle([])
    _seed_postgres([])
    expected, _ = _assert_parity("oracle", "postgres", FULL_QUERY, caplog)
    assert expected["status"] == TestStatus.PASSED
    assert expected["source_rows"] == 0


def test_one_side_empty(caplog):
    _seed_oracle(_rows(1_000))
    _seed_postgres([])
    expected, _ = _assert_parity("oracle", "postgres", FULL_QUERY, caplog)
    assert expected["missing_in_target"] == 1_000


# ---------------------------------------------------------------------------
# Scale (opt-in): rows generated inside each database
# ---------------------------------------------------------------------------

SCALE_ROWS = int(os.getenv("LIVE_LARGE_TABLE_SCALE_ROWS", "1000000"))


@pytest.mark.skipif(os.getenv("RUN_LIVE_LARGE_TABLE_SCALE") != "1",
                    reason="set RUN_LIVE_LARGE_TABLE_SCALE=1 for the million-row scenario")
def test_scale_memory_stays_bounded(caplog):
    import tracemalloc

    n = SCALE_ROWS
    with _ora() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN EXECUTE IMMEDIATE 'DROP TABLE recon_big PURGE'; "
                    "EXCEPTION WHEN OTHERS THEN NULL; END;")
        cur.execute(
            "CREATE TABLE recon_big NOLOGGING AS SELECT id, "
            "MOD(id * 7, 100000) / 100 AS amount, 'note ' || id AS note, "
            "DATE '2024-01-01' + id / 86400 AS created "
            f"FROM (SELECT ROWNUM AS id FROM dual CONNECT BY LEVEL <= 1000) a, "
            f"(SELECT ROWNUM AS r FROM dual CONNECT BY LEVEL <= {max(1, n // 1000)}) b"
        )
        cur.execute("UPDATE recon_big SET id = ROWNUM")
        cur.execute("UPDATE recon_big SET amount = MOD(id * 7, 100000) / 100, note = 'note ' || id, "
                    "created = DATE '2024-01-01' + id / 86400")
        conn.commit()
    with _pg() as conn:
        conn.execute("DROP TABLE IF EXISTS recon_big")
        conn.execute(
            "CREATE TABLE recon_big AS SELECT id, "
            "CAST(MOD(id * 7, 100000) AS numeric) / 100 AS amount, 'note ' || id AS note, "
            "TIMESTAMP '2024-01-01' + id * INTERVAL '1 second' AS created "
            f"FROM generate_series(1, {max(1, n // 1000) * 1000}) AS id"
        )
        conn.execute("UPDATE recon_big SET amount = amount + 1 WHERE id % 100000 = 0")
    query = "SELECT id, amount, note, created FROM recon_big"
    changed = max(1, n // 1000) * 1000 // 100000

    peaks = {}
    for strategy in ("in_memory", "streaming", "pushdown"):
        caplog.clear()
        tracemalloc.start()
        with caplog.at_level(logging.INFO, logger="etl_framework.reconciliation.large_table"):
            result = _run("oracle", "postgres", query, strategy)
        peaks[strategy] = tracemalloc.get_traced_memory()[1] / 2**20
        tracemalloc.stop()
        assert result.value_mismatch_count == changed, strategy
        assert result.mismatch_by_column == {"amount": changed}
        if strategy == "pushdown":
            assert _differing(caplog.text) <= changed
    print(f"peak python memory MiB for {n} rows: {peaks}")
    # The in-memory path holds every row of both sides (plus copies) in pandas.
    assert peaks["streaming"] < peaks["in_memory"] / 5
    assert peaks["pushdown"] < peaks["in_memory"] / 5


def test_oracle_empty_blob_is_null(caplog):
    """Oracle keeps neither '' nor an empty BLOB: both become NULL, while
    PostgreSQL keeps them. Every strategy reports that, like in-memory."""
    _dbms_crypto(True)
    rows = [{"id": 1, "payload": b"", "doc": ""}, {"id": 2, "payload": b"x", "doc": "x"}]
    _seed_lobs_oracle(rows)
    _seed_lobs_postgres(rows)
    expected, _ = _assert_parity("oracle", "postgres", LOB_QUERY, caplog)
    assert expected["by_column"] == {"payload": 1, "doc": 1}


# ---------------------------------------------------------------------------
# Through the application: Launch run and SQL Compare (Compare tab / compare jobs)
# ---------------------------------------------------------------------------

def _app_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine)


ORACLE_CREDS = {"db_type": "oracle", "db_host": ORA["host"], "db_port": ORA["port"], "db_name": ORA["service"],
                "db_user": ORA["user"], "db_password": ORA["password"]}
PG_CREDS = {"db_type": "postgresql", "db_host": PG["host"], "db_port": PG["port"], "db_name": PG["db"],
            "db_user": PG["user"], "db_password": PG["password"], "db_sslmode": "disable"}


@pytest.mark.parametrize("strategy", ["in_memory", "streaming", "pushdown", "auto"])
def test_launch_run_with_live_connections(strategy, caplog):
    """Launch tab path: RunExecutor wraps each DBEngine in SQLAlchemyQueryEngine;
    the wrapper must expose batches and the dialect or pushdown never runs."""
    from api.schemas import JobDefinition, RunSettings
    from api.services.run_executor import RunExecutor

    rows = _rows(5_000)
    _seed_oracle(rows)
    _seed_postgres(_mutate(rows))
    job = JobDefinition(name="orders_live", job_type="reconciliation", query=FULL_QUERY, key_columns=["id"])
    executor = RunExecutor(
        db=_app_db(), run_id=f"live-{strategy}", source_env="oracle", target_env="postgres", job_sequence=[],
        run_settings=RunSettings(use_live_connections=True, reconciliation_strategy=strategy,
                                 stream_batch_size=1_000, chunk_size=0),
        config_snapshot={"source_credentials": {"name": "oracle", **ORACLE_CREDS},
                         "target_credentials": {"name": "postgres", **PG_CREDS}},
    )
    with caplog.at_level(logging.INFO, logger="etl_framework.reconciliation.large_table"):
        result = executor._build_case(job)()
    assert result.missing_in_target_count == 3 and result.missing_in_source_count == 2
    assert result.mismatch_by_column["qty"] == 2
    if strategy in ("pushdown", "auto"):
        assert 0 < _differing(caplog.text) <= 15, caplog.text
        assert "pushdown unavailable" not in caplog.text


def _sql_compare_request(db, strategy, key_columns=("id",), **advanced):
    from api.schemas import SQLCompareRequest
    from etl_framework.repository.repository import ConfigRepository

    repo = ConfigRepository(db)
    ora = repo.create(f"ORA_{strategy}", "prod", ORACLE_CREDS)
    pg = repo.create(f"PG_{strategy}", "prod", PG_CREDS)
    return SQLCompareRequest(
        config_id_a=ora.id, config_id_b=pg.id,
        # Different SQL per side, upper-case names on Oracle, key typed upper case.
        query_a="SELECT ID, CODE, AMOUNT, QTY, NOTE FROM RECON_ORDERS",
        query_b="SELECT id, code, amount, qty, note FROM public.recon_orders",
        key_columns=list(key_columns),
        advanced={"reconciliation_strategy": strategy, "stream_batch_size": 1_000, **advanced},
    ), repo


@pytest.mark.parametrize("strategy", ["streaming", "pushdown", "auto"])
def test_sql_compare_large_strategy_matches_in_memory(strategy, caplog):
    from api.services.compare_service import CompareService

    rows = _rows(5_000)
    _seed_oracle(rows)
    _seed_postgres(_mutate(rows))
    db = _app_db()
    req, repo = _sql_compare_request(db, strategy)
    baseline_req = req.model_copy(update={"advanced": req.advanced.model_copy(update={"reconciliation_strategy": "in_memory"})})
    service = CompareService(db, repo)
    expected = _counts(service.compare_sql(baseline_req))
    with caplog.at_level(logging.INFO, logger="etl_framework.reconciliation.large_table"):
        actual = service.compare_sql(req)
    assert _counts(actual) == expected
    assert expected["missing_in_target"] == 3
    assert {k for m in actual.mismatches for k in m.key_values} == {"id"}
    if strategy != "streaming":
        assert _differing(caplog.text) is not None and "pushdown unavailable" not in caplog.text


def test_sql_compare_large_strategy_requires_keys():
    from fastapi import HTTPException
    from api.services.compare_service import CompareService

    db = _app_db()
    req, repo = _sql_compare_request(db, "auto", key_columns=())
    with pytest.raises(HTTPException) as exc:
        CompareService(db, repo).compare_sql(req)
    assert exc.value.status_code == 422


def test_compare_job_inherits_run_strategy(caplog):
    """A bulk-imported SQL compare job has no strategy of its own; the Launch
    run's Large-table Strategy applies to it."""
    from api.schemas import JobDefinition, RunSettings
    from api.services.run_executor import RunExecutor

    rows = _rows(5_000)
    _seed_oracle(rows)
    _seed_postgres(_mutate(rows))
    db = _app_db()
    req, _ = _sql_compare_request(db, "in_memory")
    job = JobDefinition(name="cmp_job", job_type="compare",
                        params={"compare_type": "sql", "request": req.model_dump(exclude={"advanced"})})
    executor = RunExecutor(
        db=db, run_id="live-cmp", source_env="oracle", target_env="postgres", job_sequence=[],
        run_settings=RunSettings(use_live_connections=True, reconciliation_strategy="auto"),
        config_snapshot={},
    )
    with caplog.at_level(logging.INFO, logger="etl_framework.reconciliation.large_table"):
        result = executor._build_case(job)()
    assert result.query_name == "cmp_job"
    assert result.missing_in_target_count == 3
    assert _differing(caplog.text) is not None


def test_sql_compare_large_strategy_upper_case_key():
    """A key typed as Oracle shows it (ID) finds SQLAlchemy's lower-cased id on
    Oracle and PostgreSQL's id. The in-memory SQL compare rejects this as
    ambiguous; the large-table path matches names regardless of case."""
    from api.services.compare_service import CompareService

    rows = _rows(2_000)
    _seed_oracle(rows)
    _seed_postgres(_mutate(rows))
    db = _app_db()
    req, repo = _sql_compare_request(db, "auto", key_columns=("ID",))
    result = CompareService(db, repo).compare_sql(req)
    assert result.missing_in_target_count == 3 and result.missing_in_source_count == 2
    assert {k for m in result.mismatches for k in m.key_values} == {"ID"}
