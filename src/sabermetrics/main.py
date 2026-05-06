"""CLI interface for Sabermetrics."""

import click


@click.group()
@click.version_option(version="0.1.0")
def cli() -> None:
    """Sabermetrics for Magic — Commander/EDH deck optimization."""


@cli.command()
@click.argument("commander_name")
@click.option("--user-intent", default=None, help="Optional build direction.")
@click.option("--force-refresh", is_flag=True, help="Force profile regeneration.")
def profile(commander_name: str, user_intent: str | None, force_refresh: bool) -> None:
    """Generate or retrieve a commander profile."""
    click.echo("Not implemented yet")


@cli.command()
@click.argument("commander_name")
@click.option("--budget", type=float, default=None, help="Budget in USD.")
@click.option("--power", type=int, default=None, help="Power target (1-5).")
@click.option("--strategy", default=None, help="Strategy override.")
@click.option("--user-intent", default=None, help="Optional build direction.")
@click.option(
    "--output-format",
    type=click.Choice(["json", "text", "moxfield", "archidekt"]),
    default=None,
    help="Output format.",
)
def build(
    commander_name: str,
    budget: float | None,
    power: int | None,
    strategy: str | None,
    user_intent: str | None,
    output_format: str | None,
) -> None:
    """Generate an optimized deck for a commander."""
    click.echo("Not implemented yet")


@cli.command(name="refresh-set")
@click.argument("set_code")
def refresh_set(set_code: str) -> None:
    """Refresh data for a newly released set."""
    click.echo("Not implemented yet")


@cli.command(name="search-rules")
@click.argument("query")
@click.option("--top-k", type=int, default=5, help="Number of results.")
def search_rules(query: str, top_k: int) -> None:
    """Search reference material (rules, etc.)."""
    click.echo("Not implemented yet")


@cli.command()
@click.option("--port", type=int, default=5000, help="Server port.")
@click.option("--host", default="127.0.0.1", help="Server host.")
def serve(port: int, host: str) -> None:
    """Start the Flask UI server."""
    click.echo("Not implemented yet")


@cli.command()
@click.option(
    "--period",
    type=click.Choice(["day", "week", "month", "year"]),
    default="month",
    help="Report period.",
)
def report(period: str) -> None:
    """Show cost and usage report."""
    click.echo("Not implemented yet")


@cli.command()
def health() -> None:
    """Show status of all data sources."""
    click.echo("Not implemented yet")


@cli.command()
@click.option("--source", default=None, help="Specific source to sync.")
@click.option("--full", is_flag=True, help="Full refresh instead of incremental.")
def sync(source: str | None, full: bool) -> None:
    """Sync data from external sources."""
    click.echo("Not implemented yet")
