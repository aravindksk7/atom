"""atom - CI/CD command line client for the Atom API.

HTTP-only: this module must never import api.* or etl_framework.repository.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from etl_framework.cli import render
from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomClient,
    AtomConnectionError,
    AtomNotFoundError,
)

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_CANCELLED = 2
EXIT_ERROR = 3
EXIT_NOT_FOUND = 4
EXIT_CONNECTION = 5
EXIT_TIMEOUT = 6

# Mirror of etl_framework.repository.models.TERMINAL_STATUSES (CLI is HTTP-only,
# so it must not import the models module).
TERMINAL_STATUSES = frozenset(
    {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}
)

app = typer.Typer(help="Atom CI/CD command line client", no_args_is_help=True)


def _make_client(api_url: str, token: Optional[str]) -> AtomClient:
    return AtomClient(api_url, token=token)


@app.callback()
def main_options(
    ctx: typer.Context,
    api_url: str = typer.Option(..., "--api-url", envvar="ATOM_API_URL",
                                help="Atom API base URL, e.g. http://atom.internal:8000"),
    token: Optional[str] = typer.Option(None, "--token", envvar="ATOM_API_TOKEN",
                                        help="Bearer token for the Atom API"),
    output: str = typer.Option("text", "--output", help="Output style: text or json"),
):
    if output not in ("text", "json"):
        raise typer.BadParameter("--output must be 'text' or 'json'")
    ctx.obj = {"client": _make_client(api_url, token), "output": output}


def _fail(output: str, code: int, message: str) -> typer.Exit:
    if output == "json":
        print(json.dumps({"error": message, "exit_code": code}), file=sys.stderr)
    else:
        print(f"ERROR: {message}", file=sys.stderr)
    return typer.Exit(code)


class WaitTimeoutError(Exception):
    def __init__(self, run_id: str):
        super().__init__(f"timed out waiting for run {run_id}")
        self.run_id = run_id


def _resolve_selection(client: AtomClient, selection: str) -> int:
    if selection.isdigit():
        return int(selection)
    matches = [s for s in client.get_json("/api/selections")
               if s.get("name") == selection]
    if not matches:
        raise AtomNotFoundError(f"no job selection named {selection!r}")
    if len(matches) > 1:
        raise AtomAPIError(
            f"multiple selections named {selection!r}; use the numeric id"
        )
    return int(matches[0]["id"])


def _wait_for_run(client: AtomClient, run_id: str,
                  timeout: float, poll_interval: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = client.get_json(f"/api/runs/{run_id}/status")
        if status.get("status") in TERMINAL_STATUSES:
            return status
        if time.monotonic() >= deadline:
            raise WaitTimeoutError(run_id)
        time.sleep(poll_interval)


def _gate_exit_code(status: dict) -> int:
    if status.get("status") == "CANCELLED":
        return EXIT_CANCELLED
    if (status.get("error") or 0) > 0 or status.get("status") == "ERROR":
        return EXIT_ERROR
    if (status.get("failed") or 0) > 0 or status.get("status") == "FAILED":
        return EXIT_FAILED
    return EXIT_PASSED


def _write_artifacts(client: AtomClient, run_id: str,
                     junit_out: Optional[Path], json_out: Optional[Path],
                     html_out: Optional[Path]) -> None:
    if junit_out is not None:
        junit_out.write_bytes(client.get_bytes(f"/api/runs/{run_id}/junit"))
    if json_out is not None:
        detail = client.get_json(f"/api/runs/{run_id}")
        json_out.write_text(json.dumps(detail, indent=2, default=str),
                            encoding="utf-8")
    if html_out is not None:
        try:
            html_out.write_bytes(client.get_bytes(f"/api/runs/{run_id}/report"))
        except AtomNotFoundError:
            print(f"WARNING: no HTML report available for run {run_id}",
                  file=sys.stderr)


@app.command()
def run(
    ctx: typer.Context,
    selection: str = typer.Argument(..., help="Job selection id or exact name"),
    source_env: str = typer.Option(..., "--source-env",
                                   help="Source environment name"),
    target_env: str = typer.Option("", "--target-env",
                                   help="Target environment name"),
    ci_commit_sha: Optional[str] = typer.Option(None, "--ci-commit-sha"),
    ci_pipeline_url: Optional[str] = typer.Option(None, "--ci-pipeline-url"),
    ci_ref: Optional[str] = typer.Option(None, "--ci-ref"),
    junit_out: Optional[Path] = typer.Option(None, "--junit-out",
                                             help="Write JUnit XML here"),
    json_out: Optional[Path] = typer.Option(None, "--json-out",
                                            help="Write run detail JSON here"),
    html_out: Optional[Path] = typer.Option(None, "--html-out",
                                            help="Write HTML report here"),
    timeout: float = typer.Option(3600.0, "--timeout",
                                  help="Max seconds to wait for completion"),
    poll_interval: float = typer.Option(10.0, "--poll-interval",
                                        help="Seconds between status polls"),
    no_wait: bool = typer.Option(False, "--no-wait",
                                 help="Launch, print run id, exit 0"),
) -> None:
    """Launch a job selection, wait for it, and gate on the outcome."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        selection_id = _resolve_selection(client, selection)
        payload: dict = {"source_env": source_env, "target_env": target_env}
        ci_context = {k: v for k, v in {
            "commit_sha": ci_commit_sha,
            "pipeline_url": ci_pipeline_url,
            "ref": ci_ref,
        }.items() if v}
        if ci_context:
            payload["ci_context"] = ci_context
        launched = client.post_json(f"/api/selections/{selection_id}/launch",
                                    payload)
        run_id = launched["run_id"]
        if no_wait:
            print(json.dumps({"run_id": run_id}) if output == "json" else run_id)
            raise typer.Exit(EXIT_PASSED)
        status = _wait_for_run(client, run_id, timeout, poll_interval)
        _write_artifacts(client, run_id, junit_out, json_out, html_out)
        code = _gate_exit_code(status)
        if output == "json":
            print(json.dumps({
                "run_id": run_id, "verdict": status.get("status"),
                "exit_code": code, "passed": status.get("passed"),
                "failed": status.get("failed"), "error": status.get("error"),
            }))
        else:
            print(render.run_summary(status, code))
        raise typer.Exit(code)
    except WaitTimeoutError as exc:
        print(exc.run_id)
        raise _fail(output, EXIT_TIMEOUT, str(exc))
    except AtomNotFoundError as exc:
        raise _fail(output, EXIT_NOT_FOUND, str(exc))
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))


@app.command()
def report(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run id to fetch"),
    format: str = typer.Option("json", "--format",
                               help="junit, json, csv or html"),
    out: Optional[Path] = typer.Option(None, "--out",
                                       help="Write to file instead of stdout"),
) -> None:
    """Fetch results for a past run."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    if format not in ("junit", "json", "csv", "html"):
        raise typer.BadParameter("--format must be junit, json, csv or html")
    if format == "html" and out is None:
        raise typer.BadParameter("--out is required with --format html")
    try:
        if format == "json":
            content = json.dumps(
                client.get_json(f"/api/runs/{run_id}"), indent=2, default=str
            ).encode()
        elif format == "junit":
            content = client.get_bytes(f"/api/runs/{run_id}/junit")
        elif format == "csv":
            content = client.get_bytes(f"/api/runs/{run_id}/export")
        else:  # html
            content = client.get_bytes(f"/api/runs/{run_id}/report")
    except AtomNotFoundError as exc:
        raise _fail(output, EXIT_NOT_FOUND, str(exc))
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    if out is not None:
        out.write_bytes(content)
    else:
        sys.stdout.write(content.decode())


@app.command()
def selections(ctx: typer.Context) -> None:
    """List job selections."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        items = client.get_json("/api/selections")
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    if output == "json":
        print(json.dumps(items, default=str))
    else:
        print(render.selections_table(items))


@app.command()
def runs(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", min=1, help="Max runs to show"),
) -> None:
    """List recent runs."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        items = client.get_json("/api/runs")[:limit]
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    if output == "json":
        print(json.dumps(items, default=str))
    else:
        print(render.runs_table(items))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
