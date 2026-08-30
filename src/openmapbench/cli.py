from __future__ import annotations

import json
from pathlib import Path

import typer

from .evaluator import evaluate as evaluate_result
from .taskio import load_task

app = typer.Typer(no_args_is_help=True, help="OpenMapBench CLI")


@app.command()
def validate(task: Path) -> None:
    """Validate an OpenMapBench task specification."""
    spec = load_task(task)

    missing = [str(p) for p in spec.resolve_input_paths(task) if not p.exists()]
    typer.echo(f"valid: {spec.id}")
    if missing:
        typer.echo("warning: referenced input files are missing:")
        for path in missing:
            typer.echo(f"  - {path}")


@app.command("evaluate")
def evaluate_command(
    task: Path,
    candidate: Path = typer.Option(..., exists=True, dir_okay=False),
    reference: Path = typer.Option(..., exists=True, dir_okay=False),
) -> None:
    """Evaluate one candidate artifact against reference ground truth."""
    spec = load_task(task)
    result = evaluate_result(spec, candidate, reference)
    typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    raise typer.Exit(code=0 if result.success else 1)


if __name__ == "__main__":
    app()
