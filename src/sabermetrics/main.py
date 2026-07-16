"""CLI interface for Sabermetrics."""

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


def _report_pulled_corpus(db_path: Path, commander_query: str) -> None:
    """Print visible, honest reporting for a commander's pulled deck corpus.

    Surfaces popularity_rank range, creator-tag / bracket coverage, and the tag
    distribution — the Phase 2 requirement that the popularity-proxy bias and
    label sparsity stay visible, not hidden inside a downstream aggregate.
    """
    import json
    import sqlite3
    from collections import Counter

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT d.popularity_rank AS rank, d.power_tier AS bracket, "
            "d.archetype_tags AS tags "
            "FROM decks d JOIN cards c ON d.commander_id = c.id "
            "WHERE d.source = 'archidekt' AND c.name LIKE ? "
            "ORDER BY d.popularity_rank",
            (f"%{commander_query}%",),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return

    n = len(rows)
    ranks = [r["rank"] for r in rows if r["rank"] is not None]
    tagged = [r for r in rows if r["tags"] and r["tags"] != "[]"]
    bracketed = [r for r in rows if r["bracket"] is not None]

    tag_counter: Counter[str] = Counter()
    for r in tagged:
        try:
            for t in json.loads(r["tags"]):
                tag_counter[t] += 1
        except (json.JSONDecodeError, TypeError):
            pass

    rank_span = f"{min(ranks)}–{max(ranks)}" if ranks else "n/a"
    click.echo(f"  corpus now: {n} decks (popularity_rank {rank_span})")
    click.echo(
        f"  creator-tag coverage: {len(tagged)}/{n} ({round(100 * len(tagged) / n)}%)"
        f"  |  bracket coverage: {len(bracketed)}/{n} "
        f"({round(100 * len(bracketed) / n)}%)"
    )
    if tag_counter:
        top = ", ".join(f"{t}({c})" for t, c in tag_counter.most_common(8))
        click.echo(f"  top creator tags: {top}")


@cli.command(name="pull-decks")
@click.argument("commander_names", nargs=-1, required=True)
@click.option("--target", type=int, default=100, help="Verified decks per commander.")
@click.option(
    "--sort",
    type=click.Choice(["-favorites", "-viewCount", "-createdAt", "-numFollowers"]),
    default="-favorites",
    help="Popularity sort (a proxy, not power).",
)
@click.option(
    "--max-candidates", type=int, default=600,
    help="Cost cap: candidate decks examined per commander.",
)
@click.option("--full", is_flag=True, help="Re-fetch decks already stored.")
def pull_decks(
    commander_names: tuple[str, ...],
    target: int,
    sort: str,
    max_candidates: int,
    full: bool,
) -> None:
    """Pull a commander's most-popular Archidekt decks into the corpus (Phase 2).

    Stops at TARGET verified decks or MAX_CANDIDATES examined, whichever first.
    Each deck is verified to actually be commanded by the named commander.
    """
    import logging

    from sabermetrics.ingestion.archidekt import ArchidektIngestion

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db_path = _default_db_path()
    ingestion = ArchidektIngestion(db_path)

    click.echo(
        f"Pulling up to {target} decks per commander, sorted by '{sort}' "
        f"(cap {max_candidates} candidates).\n"
        "NOTE: sort order is a POPULARITY PROXY, not a power or correctness\n"
        "signal. It biases toward early-posted decks, established creators, and\n"
        "decks with writeups. popularity_rank is stored per deck so this bias\n"
        "stays visible to every downstream consumer."
    )

    for name in commander_names:
        click.echo(f"\n=== {name} ===")
        try:
            result = ingestion.ingest_commander(
                name, target=target, sort=sort, full=full,
                max_candidates=max_candidates,
            )
        except Exception as e:  # noqa: BLE001 — one bad commander shouldn't halt a batch
            click.echo(f"  FAILED: {e}")
            continue

        if result.items_ingested == 0 and not result.success:
            click.echo(f"  {result.errors[0] if result.errors else 'no decks stored'}")
            continue

        click.echo(
            f"  stored={result.items_ingested}  "
            f"rejected/failed={result.items_failed}"
        )
        if result.items_ingested < target:
            click.echo(
                f"  (fewer than {target} — commander likely lacks that many "
                "distinct popular decks; see logged warning)"
            )
        _report_pulled_corpus(db_path, name)


@cli.command(name="cluster-decks")
@click.argument("commander_name")
@click.option("--k", type=int, default=None, help="Cluster count (default: data-driven).")
@click.option("--bootstrap", type=int, default=100, help="Bootstrap resamples for ARI stability.")
@click.option("--floor", type=int, default=20, help="Min decks/cluster for validity (plan: 20-40).")
@click.option("--no-normalize", is_flag=True, help="Cluster on raw scores, not archetype profile.")
def cluster_decks_cmd(
    commander_name: str,
    k: int | None,
    bootstrap: int,
    floor: int,
    no_normalize: bool,
) -> None:
    """Cluster a commander's decks into macro-archetypes (Phase 3).

    Runs on the commander's pulled Archidekt corpus. Reports cluster sizes vs
    the validity floor and a bootstrap adjusted-Rand-index stability verdict —
    if the split isn't stable at this sample size, it says so.
    """
    from sabermetrics.analytics.deck_clustering import format_report, run_clustering

    db_path = _default_db_path()
    report = run_clustering(
        db_path, commander_name, k=k, n_bootstrap=bootstrap,
        floor=floor, normalize=not no_normalize,
    )
    click.echo(format_report(report))


@cli.command(name="value-cards")
@click.argument("commander_name")
@click.option("--k", type=int, default=None, help="Cluster count (default: floor-aware auto).")
@click.option("--floor", type=int, default=20, help="Min decks/cluster for validity.")
@click.option("--top", type=int, default=12, help="Cards shown per list.")
def value_cards_cmd(
    commander_name: str, k: int | None, floor: int, top: int
) -> None:
    """Per-cluster card valuation with confidence bands (Phase 4).

    Reports each sub-archetype cluster's confident staples (tight CI) and the
    cards that distinguish it from the commander's other clusters. Mid-range
    inclusion rates are flagged low-confidence — only reliable rates are shown
    as staples.
    """
    from sabermetrics.analytics.cluster_valuation import (
        compute_cluster_valuation,
        format_valuation,
    )

    db_path = _default_db_path()
    valuation = compute_cluster_valuation(db_path, commander_name, k=k, floor=floor)
    click.echo(format_valuation(valuation, top_n=top))


@cli.command(name="characterize-variants")
@click.argument("commander_name")
@click.option("--sample-decks", type=int, default=1, help="Sample decklists per cluster sent to the LLM.")
def characterize_variants_cmd(commander_name: str, sample_decks: int) -> None:
    """LLM variant characterization over a commander's clusters (Phase 4b).

    Reads each cluster's Phase 4 statistics + a representative decklist and asks
    the model to name and contrast the sub-variants. Output is an explicit
    HYPOTHESIS to sanity-check, not a statistic. Incurs a small LLM cost.
    """
    from sabermetrics.reasoning.variant_characterization import (
        characterize_variants,
        format_variants,
    )

    db_path = _default_db_path()
    try:
        response, cost, valuation = characterize_variants(
            db_path, commander_name, sample_decks=sample_decks
        )
    except Exception as e:  # noqa: BLE001 — surface API/key errors cleanly
        click.echo(f"Variant characterization failed: {e}")
        return
    click.echo(format_variants(response, valuation))
    click.echo(f"\nLLM cost: ${cost:.4f}")


@cli.command(name="validate-clusters")
@click.argument("commander_name")
@click.option("--test-frac", type=float, default=0.2, help="Held-out fraction.")
@click.option("--splits", type=int, default=25, help="Random splits to average.")
@click.option("--top", type=int, default=45, help="Consensus-decklist cards per cluster.")
def validate_clusters_cmd(
    commander_name: str, test_frac: float, splits: int, top: int
) -> None:
    """Held-out validation + consensus decklists for a commander (Phase 5).

    Repeated 80/20 splits check whether the clustering and its predicted staples
    generalize to unseen decks, then the inclusion-ranked consensus decklist per
    cluster is printed for manual (domain-expert) review.
    """
    from sabermetrics.analytics.cluster_validation import (
        aggregate_decklist,
        format_aggregate,
        format_holdout,
        holdout_validation,
    )

    db_path = _default_db_path()
    report = holdout_validation(
        db_path, commander_name, test_frac=test_frac, n_splits=splits
    )
    click.echo(format_holdout(report))
    click.echo("")
    click.echo(format_aggregate(aggregate_decklist(db_path, commander_name, top_n=top)))


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


if __name__ == "__main__":
    cli()
