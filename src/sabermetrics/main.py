"""CLI interface for Sabermetrics."""

import json
from pathlib import Path

import click


def _default_db_path() -> Path:
    """Resolve the default database path."""
    return Path("data/sabermetrics.db")


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
    import json as _json
    import sqlite3

    db_path = _default_db_path()

    # Look up commander by name
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT id, name FROM cards WHERE name LIKE ? AND is_legal_commander = 1 LIMIT 1",
        (f"%{commander_name}%",),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        click.echo(f"Commander not found: {commander_name}")
        return

    commander_id, full_name = row
    click.echo(f"Generating profile for {full_name}...")

    from sabermetrics.reasoning.profiler import ProfileManager, ProfileRequest

    manager = ProfileManager(db_path)
    request = ProfileRequest(
        commander_id=commander_id,
        user_intent=user_intent,
        force_refresh=force_refresh,
    )

    try:
        result = manager.generate_profile(request)
        click.echo(f"\nCache hit: {result.cache_hit}")
        click.echo(f"Cost: ${result.generation_cost_usd:.4f}")
        click.echo(f"Time: {result.generation_time_seconds:.1f}s")
        click.echo(f"\nArchetype: {result.profile.strategic_profile.primary_archetype}")
        click.echo(f"Game plan: {result.profile.strategic_profile.game_plan_summary}")
        click.echo(f"Power range: {result.profile.strategic_profile.power_indicators.estimated_floor_bracket}"
                    f"-{result.profile.strategic_profile.power_indicators.estimated_ceiling_bracket}")
    except Exception as e:
        click.echo(f"Profile generation failed: {e}")


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
    import sqlite3

    from sabermetrics.config import settings

    db_path = _default_db_path()

    # Resolve commander by name
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT id, name FROM cards WHERE name LIKE ? AND is_legal_commander = 1 LIMIT 1",
        (f"%{commander_name}%",),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        click.echo(f"Commander not found: {commander_name}")
        return

    commander_id, full_name = row
    click.echo(f"Building deck for {full_name}...")

    from sabermetrics.pipeline.deck_builder import DeckBuildRequest, DeckBuilder
    from sabermetrics.pipeline.formatters import format_deck

    builder = DeckBuilder(db_path)
    request = DeckBuildRequest(
        commander_id=commander_id,
        budget_usd=budget or settings.user.default_budget_usd,
        power_target=power or settings.user.default_power_target,
        strategy=strategy,
        user_intent=user_intent,
    )

    try:
        result = builder.build(request)

        # Display summary
        click.echo(f"\nDeck generated: {len(result.deck.cards)} cards + commander")
        click.echo(f"Total price: ${result.deck.composition.total_price_usd:.2f}")
        click.echo(f"Bracket: {result.deck.classification.estimated_bracket}")
        click.echo(f"Time: {result.total_time_seconds:.1f}s")
        click.echo(f"Cost: ${result.total_cost_usd:.4f}")

        # Output in requested format
        fmt = output_format or settings.output.deck_format
        output = format_deck(result.deck, fmt)
        click.echo(f"\n{output}")

    except Exception as e:
        click.echo(f"Deck generation failed: {e}")
        raise


@cli.command(name="refresh-set")
@click.argument("set_code")
def refresh_set(set_code: str) -> None:
    """Refresh data for a newly released set."""
    import subprocess
    import sys

    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    script = scripts_dir / "quarterly_set_refresh.py"

    if not script.exists():
        click.echo(f"Script not found: {script}")
        return

    click.echo(f"Running quarterly set refresh for {set_code}...")
    result = subprocess.run(
        [sys.executable, str(script), set_code],
        env={**__import__("os").environ, "PYTHONPATH": str(scripts_dir.parent / "src")},
    )
    if result.returncode != 0:
        click.echo("Refresh completed with errors (check data/logs/)")
    else:
        click.echo("Refresh completed successfully.")


@cli.command(name="search-rules")
@click.argument("query")
@click.option("--top-k", type=int, default=5, help="Number of results.")
def search_rules(query: str, top_k: int) -> None:
    """Search reference material (rules, etc.)."""
    from sabermetrics.reference_layer.retriever import ReferenceQuery, ReferenceRetriever

    db_path = _default_db_path()
    retriever = ReferenceRetriever(db_path)
    rq = ReferenceQuery(query_text=query, top_k=top_k)
    results = retriever.retrieve(rq)

    if not results:
        click.echo("No results found.")
        return

    for i, r in enumerate(results, 1):
        click.echo(f"\n--- Result {i} [{r.similarity_score:.3f}] ---")
        click.echo(f"Source: {r.document} / {r.section or 'N/A'}")
        click.echo(r.content[:300])


@cli.command()
@click.option("--port", type=int, default=5000, help="Server port.")
@click.option("--host", default="127.0.0.1", help="Server host.")
def serve(port: int, host: str) -> None:
    """Start the Flask UI server."""
    from sabermetrics.ui.app import run_server

    db_path = _default_db_path()
    run_server(host=host, port=port, db_path=db_path)


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
    from sabermetrics.ingestion.health import SourceHealthMonitor

    db_path = _default_db_path()
    monitor = SourceHealthMonitor(db_path)
    records = monitor.get_health_report()

    if not records:
        click.echo("No source health data recorded yet.")
        return

    click.echo(f"{'Source':<15} {'Last Success':<22} {'Last Failure':<22} {'Failures':>8}")
    click.echo("-" * 70)
    for rec in records:
        source = rec.get("source", "?")
        last_ok = rec.get("last_successful_sync", "-") or "-"
        last_fail = rec.get("last_failed_sync", "-") or "-"
        failures = rec.get("consecutive_failures", 0)
        click.echo(f"{source:<15} {str(last_ok):<22} {str(last_fail):<22} {failures:>8}")


@cli.command(name="build-kb")
@click.option("--skip-ingest", is_flag=True, help="Skip Game Knights deck ingestion.")
@click.option("--skip-fetch", is_flag=True, help="Skip EDHREC article fetching.")
def build_kb(skip_ingest: bool, skip_fetch: bool) -> None:
    """Build the deckbuilding knowledge base."""
    import subprocess
    import sys

    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    script = scripts_dir / "build_deckbuilding_kb.py"

    if not script.exists():
        click.echo(f"Script not found: {script}")
        return

    db_path = _default_db_path()
    cmd = [sys.executable, str(script), "--db-path", str(db_path)]
    if skip_ingest:
        cmd.append("--skip-ingest")
    if skip_fetch:
        cmd.append("--skip-fetch")

    click.echo("Building deckbuilding knowledge base...")
    result = subprocess.run(
        cmd,
        env={**__import__("os").environ, "PYTHONPATH": str(scripts_dir.parent / "src")},
    )
    if result.returncode != 0:
        click.echo("Knowledge base build completed with errors (check logs)")
    else:
        click.echo("Knowledge base built successfully.")


@cli.command(name="index-mechanics")
@click.option("--skip-download", is_flag=True, help="Use cached article files.")
@click.option("--force-reindex", is_flag=True, help="Delete existing and re-index all.")
def index_mechanics(skip_download: bool, force_reindex: bool) -> None:
    """Scrape and index WotC set mechanics articles into RAG."""
    import subprocess
    import sys

    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    script = scripts_dir / "index_set_mechanics.py"

    if not script.exists():
        click.echo(f"Script not found: {script}")
        return

    db_path = _default_db_path()
    data_dir = db_path.parent

    cmd = [
        sys.executable, str(script),
        "--db-path", str(db_path),
        "--data-dir", str(data_dir),
    ]
    if skip_download:
        cmd.append("--skip-download")
    if force_reindex:
        cmd.append("--force-reindex")

    click.echo("Indexing set mechanics articles...")
    result = subprocess.run(
        cmd,
        env={**__import__("os").environ, "PYTHONPATH": str(scripts_dir.parent / "src")},
    )
    if result.returncode != 0:
        click.echo("Mechanics indexing completed with errors (check logs)")
    else:
        click.echo("Set mechanics articles indexed successfully.")


@cli.command()
@click.option("--source", default=None, help="Specific source to sync.")
@click.option("--full", is_flag=True, help="Full refresh instead of incremental.")
def sync(source: str | None, full: bool) -> None:
    """Sync data from external sources."""
    from sabermetrics.ingestion.scryfall import ScryfallIngestion
    from sabermetrics.ingestion.topdeck import TopDeckIngestion
    from sabermetrics.ingestion.edhrec import EDHRECIngestion
    from sabermetrics.ingestion.spellbook import SpellbookIngestion
    from sabermetrics.ingestion.mtgapi import MtgApiIngestion
    from sabermetrics.ingestion.moxfield import MoxfieldIngestion
    from sabermetrics.ingestion.archidekt import ArchidektIngestion
    from sabermetrics.ingestion.deckstats import DeckstatsIngestion

    db_path = _default_db_path()

    all_sources = {
        "scryfall": ScryfallIngestion(db_path),
        "topdeck": TopDeckIngestion(db_path),
        "edhrec": EDHRECIngestion(db_path),
        "spellbook": SpellbookIngestion(db_path),
        "mtgapi": MtgApiIngestion(db_path),
        "moxfield": MoxfieldIngestion(db_path),
        "archidekt": ArchidektIngestion(db_path),
        "deckstats": DeckstatsIngestion(db_path),
    }

    if source:
        if source not in all_sources:
            click.echo(f"Unknown source '{source}'. Available: {', '.join(all_sources)}")
            return
        sources_to_sync = {source: all_sources[source]}
    else:
        sources_to_sync = all_sources

    for name, src in sources_to_sync.items():
        click.echo(f"Syncing {name}...")
        try:
            result = src.sync(full=full)
            click.echo(
                f"  {name}: ingested={result.items_ingested}, "
                f"updated={result.items_updated}, "
                f"failed={result.items_failed}, "
                f"success={result.success}"
            )
            if result.errors:
                for err in result.errors[:5]:
                    click.echo(f"  Error: {err}")
        except Exception as e:
            click.echo(f"  {name}: FAILED - {e}")
