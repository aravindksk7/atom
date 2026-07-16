from __future__ import annotations

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
