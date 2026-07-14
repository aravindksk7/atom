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
