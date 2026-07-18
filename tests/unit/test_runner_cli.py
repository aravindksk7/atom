from __future__ import annotations

import json
import subprocess
import sys

from etl_framework.runner.cli import build_parser, main


def test_runner_cli_parser_accepts_existing_arguments():
    args = build_parser().parse_args([
        "--config", "config.yaml",
        "--source-env", "dev",
        "--target-env", "prod",
        "--max-workers", "2",
        "--metrics-output", "metrics.json",
    ])
    assert args.config == "config.yaml"
    assert args.source_env == "dev"
    assert args.target_env == "prod"
    assert args.max_workers == 2
    assert args.metrics_output == "metrics.json"


def test_runner_cli_text_smoke(tmp_path, capsys):
    config = tmp_path / "envs.yaml"
    config.write_text(
        """
environments:
  dev:
    db_host: localhost
    db_password: secret
  prod:
    db_host: localhost
    db_password: secret
""".strip(),
        encoding="utf-8",
    )
    metrics = tmp_path / "metrics.json"

    exit_code = main([
        "--config", str(config),
        "--source-env", "dev",
        "--target-env", "prod",
        "--metrics-output", str(metrics),
    ])

    assert exit_code == 0
    assert metrics.exists()
    assert "No test cases registered yet" in capsys.readouterr().out


def test_gate_run_exit_codes(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from etl_framework.repository.database import Base
    from etl_framework.repository.models import TestRun
    from etl_framework.runner import cli

    engine = create_engine(f"sqlite:///{tmp_path / 'gate.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(TestRun(run_id="run-pass", status="COMPLETED", failed=0, error=0))
    session.add(TestRun(run_id="run-fail", status="COMPLETED", failed=2, error=0))
    session.add(TestRun(run_id="run-err", status="COMPLETED", failed=0, error=1))
    session.add(TestRun(run_id="run-cancel", status="CANCELLED"))
    session.commit()
    session.close()

    monkeypatch.setattr(cli, "_gate_session_factory", sessionmaker(bind=engine))

    assert cli.main(["--gate-run", "run-pass"]) == 0
    assert cli.main(["--gate-run", "run-fail"]) == 1
    assert cli.main(["--gate-run", "run-cancel"]) == 2
    assert cli.main(["--gate-run", "run-err"]) == 3
    assert cli.main(["--gate-run", "run-ghost"]) == 4


def _session_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_scheduler_stats_cli_json_report_exits_zero(monkeypatch, capsys):
    from etl_framework.runner import cli

    factory = _session_factory()
    monkeypatch.setattr(cli, "_stats_session_factory", factory)

    code = cli.main(["--scheduler-stats", "--output", "json"])

    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["window_days"] == 30
    assert body["gate"]["exit_code"] == 0


def test_scheduler_stats_cli_gate_returns_nonzero(monkeypatch, capsys):
    from etl_framework.runner import cli

    factory = _session_factory()
    monkeypatch.setattr(cli, "_stats_session_factory", factory)
    monkeypatch.setattr(
        "api.services.scheduler_stats.get_scheduler_runtime_snapshot",
        lambda: {"available": True, "running": False, "job_count": 0, "timezone": "UTC", "jobs": {}},
    )

    code = cli.main(["--scheduler-stats", "--fail-on-stopped", "--output", "json"])

    assert code == 1
    body = json.loads(capsys.readouterr().out)
    assert body["gate"]["status"] == "failed"


def test_scheduler_stats_cli_validates_days():
    from etl_framework.runner import cli

    try:
        cli.main(["--scheduler-stats", "--days", "0"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected parser validation failure")


def test_scheduler_stats_cli_validates_min_success_rate():
    from etl_framework.runner import cli

    try:
        cli.main(["--scheduler-stats", "--min-success-rate", "101"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected parser validation failure")


def test_scheduler_stats_module_entrypoint_outputs_json():
    result = subprocess.run(
        [sys.executable, "-m", "etl_framework.runner.cli", "--scheduler-stats", "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["window_days"] == 30
    assert body["gate"]["exit_code"] == 0


def test_scheduler_stats_cli_json_error_returns_one(monkeypatch, capsys):
    from etl_framework.runner import cli

    def broken_factory():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(cli, "_stats_session_factory", broken_factory)

    code = cli.main(["--scheduler-stats", "--output", "json"])

    assert code == 1
    body = json.loads(capsys.readouterr().out)
    assert body == {"error": "database unavailable", "exit_code": 1}


def test_scheduler_stats_cli_text_error_returns_one(monkeypatch, capsys):
    from etl_framework.runner import cli

    def broken_factory():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(cli, "_stats_session_factory", broken_factory)

    code = cli.main(["--scheduler-stats", "--output", "text"])

    assert code == 1
    assert "ERROR scheduler stats: database unavailable" in capsys.readouterr().out
