"""Command-line interface for popup-ai."""

import asyncio
import logging
import signal
from pathlib import Path
from typing import Annotated

import ray
import typer
from rich.console import Console

from popup_ai import __version__
from popup_ai.config import Settings, load_settings

app = typer.Typer(
    name="popup-ai",
    help="Real-time audio transcription and annotation overlay system",
    no_args_is_help=False,
)
console = Console()

# Valid actor names
VALID_ACTORS = {"audio", "transcriber", "annotator", "overlay"}


def version_callback(value: bool) -> None:
    if value:
        console.print(f"popup-ai version {__version__}")
        raise typer.Exit()


def parse_actors(actors_str: str | None) -> set[str] | None:
    """Parse comma-separated actor list.

    Returns None if no actors specified (use defaults).
    Returns set of actor names if specified.
    """
    if not actors_str:
        return None
    actors = {a.strip().lower() for a in actors_str.split(",")}
    invalid = actors - VALID_ACTORS
    if invalid:
        console.print(
            f"[red]Invalid actor(s): {', '.join(invalid)}[/red]\n"
            f"Valid actors: {', '.join(sorted(VALID_ACTORS))}"
        )
        raise typer.Exit(code=1)
    return actors


def apply_actor_flags(
    settings: Settings, actors: set[str] | None, no_flags: dict[str, bool]
) -> None:
    """Apply --actors and --no-<actor> flags to settings.

    Args:
        settings: Settings object to modify
        actors: Set of actors to enable (if specified, all others disabled)
        no_flags: Dict of {actor_name: should_disable} from --no-<actor> flags
    """
    if actors is not None:
        # --actors was specified: enable only those actors
        settings.pipeline.audio_enabled = "audio" in actors
        settings.pipeline.transcriber_enabled = "transcriber" in actors
        settings.pipeline.annotator_enabled = "annotator" in actors
        settings.pipeline.overlay_enabled = "overlay" in actors
    else:
        # Apply individual --no-<actor> flags
        if no_flags.get("audio"):
            settings.pipeline.audio_enabled = False
        if no_flags.get("transcriber"):
            settings.pipeline.transcriber_enabled = False
        if no_flags.get("annotator"):
            settings.pipeline.annotator_enabled = False
        if no_flags.get("overlay"):
            settings.pipeline.overlay_enabled = False


def setup_logging(level: str) -> None:
    """Configure logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", callback=version_callback, is_eager=True),
    ] = False,
    headless: Annotated[
        bool,
        typer.Option("--headless", help="Run without UI (for automation)"),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config file"),
    ] = None,
    actors: Annotated[
        str | None,
        typer.Option(
            "--actors",
            help="Comma-separated list of actors to enable (audio,transcriber,annotator,overlay)",
        ),
    ] = None,
    no_audio: Annotated[
        bool,
        typer.Option("--no-audio", help="Disable audio ingest actor"),
    ] = False,
    no_transcriber: Annotated[
        bool,
        typer.Option("--no-transcriber", help="Disable transcriber actor"),
    ] = False,
    no_annotator: Annotated[
        bool,
        typer.Option("--no-annotator", help="Disable annotator actor"),
    ] = False,
    no_overlay: Annotated[
        bool,
        typer.Option("--no-overlay", help="Disable overlay actor"),
    ] = False,
) -> None:
    """popup-ai: Real-time audio transcription and annotation overlay system.

    Starts the pipeline with optional UI. Use --headless for automation.

    Actor control examples:

        popup-ai --actors audio,transcriber    # Only audio + transcription

        popup-ai --no-overlay                  # Full pipeline except OBS overlay

        popup-ai --no-annotator --no-overlay   # Just audio + transcription
    """
    if ctx.invoked_subcommand is not None:
        return

    # Load settings
    settings = load_settings()
    settings.pipeline.headless = headless
    setup_logging(settings.pipeline.log_level)

    # Apply actor flags
    parsed_actors = parse_actors(actors)
    no_flags = {
        "audio": no_audio,
        "transcriber": no_transcriber,
        "annotator": no_annotator,
        "overlay": no_overlay,
    }
    apply_actor_flags(settings, parsed_actors, no_flags)

    # Show enabled actors
    enabled = []
    if settings.pipeline.audio_enabled:
        enabled.append("audio")
    if settings.pipeline.transcriber_enabled:
        enabled.append("transcriber")
    if settings.pipeline.annotator_enabled:
        enabled.append("annotator")
    if settings.pipeline.overlay_enabled:
        enabled.append("overlay")

    if not enabled:
        console.print("[yellow]Warning: No actors enabled. Pipeline will be empty.[/yellow]")
    else:
        console.print(f"[dim]Enabled actors: {', '.join(enabled)}[/dim]")

    if config:
        console.print(f"[dim]Using config: {config}[/dim]")

    # Initialize Logfire observability
    if settings.logfire.enabled:
        from popup_ai.observability import configure_logfire

        configure_logfire(
            sample_rate=settings.logfire.sample_rate,
            environment=settings.logfire.environment,
        )
        console.print(f"[dim]Logfire: {settings.logfire.dashboard_url}[/dim]")

    # Initialize Ray with dashboard and log_to_driver
    console.print("[bold blue]Initializing Ray...[/bold blue]")
    context = ray.init(
        ignore_reinit_error=True,
        logging_level=logging.INFO,
        log_to_driver=True,
        include_dashboard=True,
    )

    # Show dashboard URL
    dashboard_url = getattr(context, "dashboard_url", None)
    if dashboard_url:
        if not dashboard_url.startswith("http"):
            dashboard_url = f"http://{dashboard_url}"
        console.print(f"[dim]Ray Dashboard: {dashboard_url}[/dim]")

    if headless:
        # Run pipeline without UI
        console.print("[bold blue]Starting popup-ai in headless mode...[/bold blue]")
        asyncio.run(run_headless(settings))
    else:
        # Run with UI
        console.print("[bold blue]Starting popup-ai with UI...[/bold blue]")
        run_with_ui(settings)


async def run_headless(settings: Settings) -> None:
    """Run pipeline in headless mode."""
    from popup_ai.actors.supervisor import PipelineSupervisor

    console.print("[yellow]Starting pipeline supervisor...[/yellow]")

    # Create and start supervisor
    supervisor = PipelineSupervisor.remote(settings)

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def signal_handler(sig: int, frame: object) -> None:
        console.print("\n[yellow]Shutting down...[/yellow]")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await supervisor.start.remote()
        console.print("[green]Pipeline running. Press Ctrl+C to stop.[/green]")

        # Wait for shutdown signal
        await shutdown_event.wait()

    finally:
        console.print("[yellow]Stopping pipeline...[/yellow]")
        await supervisor.stop.remote()
        ray.shutdown()
        console.print("[green]Goodbye![/green]")


def run_with_ui(settings: Settings) -> None:
    """Run with NiceGUI UI."""
    try:
        from popup_ai.ui.app import run_app

        run_app(settings)
    except ImportError as e:
        console.print(
            "[bold red]Error:[/bold red] nicegui not installed. "
            "Install with: uv pip install 'popup-ai[ui]'"
        )
        console.print(f"[dim]Import error: {e}[/dim]")
        raise typer.Exit(code=1) from None


@app.command()
def status() -> None:
    """Show pipeline status."""
    console.print("[bold]Pipeline Status[/bold]")

    if not ray.is_initialized():
        console.print("[yellow]Ray not initialized. Pipeline not running.[/yellow]")
        return

    console.print("[green]Ray is running[/green]")
    # TODO: Get supervisor status and display


if __name__ == "__main__":
    app()
