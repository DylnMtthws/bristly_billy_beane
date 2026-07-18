"""Phase 4b: LLM variant characterization over deck clusters.

Frequentist sub-variant detection is unreliable at per-commander N (the plan is
explicit about this), so instead an LLM reasoning pass reads each macro-archetype
cluster's distilled card statistics (Phase 4) plus a representative decklist and
names the variant, its game plan, and what distinguishes it from the others.

The output is explicitly a HYPOTHESIS for a human to sanity-check — never a
statistic to trust. The prompt says so, the response carries per-variant
confidence caveats, and the formatter banners it.

All LLM access goes through the cost-tracked :class:`AnthropicClient` wrapper.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sabermetrics.analytics.archetype_signatures import load_library
from sabermetrics.analytics.cluster_valuation import (
    CommanderValuation,
    compute_cluster_valuation,
)
from sabermetrics.analytics.deck_clustering import (
    DeckRecord,
    build_feature_matrix,
    cluster_decks,
    load_commander_decks,
)
from sabermetrics.models.llm_responses import ClusterVariantsResponse

logger = logging.getLogger(__name__)


def _cluster_members(
    db_path: Path, commander: str, k: int
) -> dict[int, list[DeckRecord]]:
    """Re-derive cluster membership (deterministic) to pull sample decklists.

    Uses the same load/feature/cluster pipeline and seed as the valuation, so
    labels and cluster ids match Phase 4 exactly.
    """
    library = load_library()
    decks = load_commander_decks(db_path, commander)
    features, _names = build_feature_matrix(decks, library, normalize=True)
    labels, _model = cluster_decks(features, k, seed=0)
    members: dict[int, list[DeckRecord]] = {}
    for deck, lbl in zip(decks, labels):
        members.setdefault(int(lbl), []).append(deck)
    return members


def build_clusters_block(
    valuation: CommanderValuation,
    members: dict[int, list[DeckRecord]],
    top_stats: int = 10,
    sample_decks: int = 1,
) -> str:
    """Render the per-cluster evidence block for the prompt (pure, testable).

    Args:
        valuation: The Phase 4 per-cluster valuation.
        members: Cluster id -> member decks (for sample decklists).
        top_stats: Max staples / distinctive cards to include per cluster.
        sample_decks: Number of representative decklists per cluster.

    Returns:
        A formatted text block describing every cluster's statistics and a
        representative (most-popular) decklist.
    """
    lines: list[str] = []
    for c in valuation.clusters:
        floor_note = "" if c.meets_floor else " [BELOW validity floor — weak/uncertain]"
        lines.append(
            f"CLUSTER {c.cluster_id}: dominant archetype '{c.dominant_archetype}', "
            f"{c.size} decks{floor_note}"
        )
        if c.staples:
            lines.append("  Confident staples (inclusion):")
            for s in c.staples[:top_stats]:
                lines.append(f"    {int(s.inclusion_rate * 100)}%  {s.card_name}")
        if c.distinctive:
            lines.append("  Distinctive vs other clusters (inclusion, +lift):")
            for d in c.distinctive[:top_stats]:
                flag = "" if d.reliable else " (low-confidence)"
                lines.append(
                    f"    {int(d.inclusion_rate * 100)}% "
                    f"(+{int(d.lift_vs_rest * 100)}){flag}  {d.card_name}"
                )
        ranked = sorted(
            members.get(c.cluster_id, []),
            key=lambda r: (r.popularity_rank if r.popularity_rank is not None else 10**9),
        )
        for i, deck in enumerate(ranked[:sample_decks]):
            lines.append(
                f"  Sample decklist #{i + 1} (popularity rank {deck.popularity_rank}): "
                + ", ".join(deck.card_names)
            )
        lines.append("")
    return "\n".join(lines).strip()


def _strip_json_fences(text: str) -> str:
    """Strip a leading ```json / ``` fence if the model wrapped its output."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    out: list[str] = []
    in_block = False
    for line in text.split("\n"):
        if line.startswith("```") and not in_block:
            in_block = True
            continue
        if line.startswith("```") and in_block:
            break
        if in_block:
            out.append(line)
    return "\n".join(out)


def characterize_variants(
    db_path: Path,
    commander: str,
    top_stats: int = 10,
    sample_decks: int = 1,
) -> tuple[ClusterVariantsResponse, float, CommanderValuation]:
    """Run the LLM variant-characterization pass for a commander's clusters.

    Args:
        db_path: SQLite database path.
        commander: Commander id or (partial) name.
        top_stats: Staples / distinctive cards per cluster passed to the model.
        sample_decks: Representative decklists per cluster passed to the model.

    Returns:
        ``(response, cost_usd, valuation)``. The response is a hypothesis.
    """
    valuation = compute_cluster_valuation(db_path, commander)
    if not valuation.clusters:
        return (
            ClusterVariantsResponse(overall_note="No clusters to characterize."),
            0.0,
            valuation,
        )

    members = _cluster_members(db_path, commander, valuation.k)
    clusters_block = build_clusters_block(
        valuation, members, top_stats=top_stats, sample_decks=sample_decks
    )

    from sabermetrics.config import settings
    from sabermetrics.reasoning.client import AnthropicClient
    from sabermetrics.reasoning.prompts import load_prompt

    template = load_prompt("cluster_variants")
    prompt_text = template.format(
        commander=commander,
        n_decks=valuation.n_decks,
        clusters_block=clusters_block,
    )
    system = (
        "You are an expert Magic: the Gathering Commander strategist. Always "
        "output valid JSON matching the requested schema."
    )

    client = AnthropicClient.get_instance(db_path)
    result = client.call_with_cache(
        model=settings.llm.synthesis_model,
        system=system,
        messages=[{"role": "user", "content": prompt_text}],
        cache_breakpoints=[],
        max_tokens=2000,
        temperature=0.2,
        call_type="cluster_variants",
    )

    data = json.loads(_strip_json_fences(result.content))
    response = ClusterVariantsResponse(**data)
    logger.info(
        "Variant characterization: %d variants, $%.4f",
        len(response.variants), result.cost_usd,
    )
    return response, result.cost_usd, valuation


def format_variants(
    response: ClusterVariantsResponse, valuation: CommanderValuation
) -> str:
    """Render the characterization with an explicit hypothesis banner."""
    lines = [
        f"=== Cluster variant characterization (HYPOTHESIS): {valuation.commander} ===",
        "These are LLM hypotheses over statistical clusters — sanity-check, "
        "do not treat as fact.",
    ]
    size_by_id = {c.cluster_id: c.size for c in valuation.clusters}
    for v in response.variants:
        n = size_by_id.get(v.cluster_id, "?")
        lines.append("")
        lines.append(f"── cluster {v.cluster_id}: {v.variant_name} (n={n} decks)")
        lines.append(f"   game plan: {v.game_plan}")
        if v.key_cards:
            lines.append(f"   key cards: {', '.join(v.key_cards)}")
        if v.differentiators:
            lines.append(f"   differs by: {v.differentiators}")
        if v.confidence:
            lines.append(f"   ⚠ verify: {v.confidence}")
    if response.overall_note:
        lines.append("")
        lines.append(f"overall: {response.overall_note}")
    return "\n".join(lines)
