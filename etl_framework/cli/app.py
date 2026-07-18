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


@app.command()
def run(ctx: typer.Context) -> None:
    """Launch a job selection, wait for it, and gate on the outcome."""
    raise typer.Exit(EXIT_PASSED)


@app.command()
def report(ctx: typer.Context) -> None:
    """Fetch results for a past run."""
    raise typer.Exit(EXIT_PASSED)


@app.command()
def selections(ctx: typer.Context) -> None:
    """List job selections."""
    raise typer.Exit(EXIT_PASSED)


@app.command()
def runs(ctx: typer.Context) -> None:
    """List recent runs."""
    raise typer.Exit(EXIT_PASSED)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
