"""Command-line interface for popup-ai."""

from typing import Annotated

import typer
from rich.console import Console

from popup_ai import __version__
from popup_ai.modes import RunMode

app = typer.Typer(
    name="popup-ai",
    help="Real-time audio transcription and annotation overlay system",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"popup-ai version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-v", callback=version_callback, is_eager=True),
    ] = False,
) -> None:
    """popup-ai: Real-time audio transcription and annotation overlay system."""
    pass


@app.command()
def run(
    mode: Annotated[
        RunMode,
        typer.Option(
            "--mode",
            "-m",
            help="Pipeline run mode",
        ),
    ] = RunMode.FULL,
    config: Annotated[
        str | None,
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
) -> None:
    """Run the popup-ai pipeline in the specified mode."""
    console.print(f"[bold blue]Starting popup-ai in {mode.value} mode...[/bold blue]")

    match mode:
        case RunMode.INGEST:
            console.print("[yellow]Ingest mode:[/yellow] SRT → PCM stream")
        case RunMode.RECORD:
            console.print("[yellow]Record mode:[/yellow] SRT → audio file")
        case RunMode.TRANSCRIBE:
            console.print("[yellow]Transcribe mode:[/yellow] file → transcript")
        case RunMode.ANNOTATE:
            console.print("[yellow]Annotate mode:[/yellow] text → annotation")
        case RunMode.FULL:
            console.print("[yellow]Full pipeline:[/yellow] SRT → transcript → annotation → overlay")

    if config:
        console.print(f"[dim]Using config: {config}[/dim]")

    console.print("[green]Pipeline ready (not yet implemented)[/green]")


@app.command()
def ui() -> None:
    """Launch the admin UI (requires [ui] extras)."""
    try:
        from nicegui import ui as nicegui_ui  # noqa: F401

        console.print("[bold green]Launching admin UI...[/bold green]")
        console.print("[yellow]Admin UI not yet implemented[/yellow]")
    except ImportError as e:
        console.print(
            "[bold red]Error:[/bold red] nicegui not installed. "
            "Install with: uv pip install 'popup-ai[ui]'"
        )
        raise typer.Exit(code=1) from e


if __name__ == "__main__":
    app()
