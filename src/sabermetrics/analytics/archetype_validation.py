"""Validate the macro-archetype signature library against creator tags.

Phase 1 acceptance: rather than trusting the hand-curated signature cards
blindly, we score decks whose creators assigned archetype tags (the Phase 0.3
ground truth) and measure how well signature-based classification recovers
those labels (precision / recall / F1 per archetype).

The labeled corpus is pooled across MANY commanders — it is built from
Archidekt's *global* deck search (no commander filter), keeping only decks that
carry creator tags, then fetching each such deck's decklist. It is cached to
disk so re-runs don't re-hit the API.

Caveats surfaced in the report, not hidden:
  - Creator tags are INCOMPLETE (~15-19% of decks tagged, and a tagged deck may
    omit applicable tags). Recall (did we catch what the creator labeled?) is
    therefore the more trustworthy metric; precision is a LOWER BOUND, because a
    deck we call "tokens" may genuinely be tokens yet simply lack the tag.
  - "Control" / "Combo" tags are unrecognized by design (no signature set); the
    report shows what fraction of tagged decks fall outside the modeled set.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from sabermetrics.analytics.archetype_signatures import (
    ArchetypeLibrary,
    classify_deck,
    tags_to_archetypes,
)
from sabermetrics.ingestion.archidekt import (
    ARCHIDEKT_API_URL,
    COMMANDER_FORMAT_ID,
    USER_AGENT,
    extract_summary_metadata,
    parse_deck_detail,
)
from sabermetrics.utils.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

# Archidekt's global search is capped at 1000 results (~17 pages of 60); sweep
# multiple sorts to diversify the labeled pool.
_CORPUS_SORTS = ("-viewCount", "-createdAt")
_MAX_PAGES_PER_SORT = 17


# ---------------------------------------------------------------------------
# Corpus construction (network + cache)
# ---------------------------------------------------------------------------


def build_labeled_corpus(
    cache_path: Path,
    target: int = 200,
    rate_per_second: float = 1.0,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Build (or load) a pooled, tag-labeled decklist corpus from Archidekt.

    Args:
        cache_path: JSON file to read/write the corpus.
        target: Number of tagged decks (with decklists) to collect.
        rate_per_second: Politeness rate limit for API calls.
        force: If True, rebuild even if the cache exists.

    Returns:
        List of ``{"deck_id", "name", "tags": [...], "cards": [...]}`` records.
    """
    if cache_path.exists() and not force:
        with open(cache_path) as f:
            cached: list[dict[str, Any]] = json.load(f)
        logger.info("Loaded %d cached labeled decks from %s", len(cached), cache_path)
        if len(cached) >= target:
            return cached[:target]
        logger.info("Cache has %d < target %d; rebuilding", len(cached), target)

    limiter = RateLimiter(requests_per_second=rate_per_second)
    headers = {"User-Agent": USER_AGENT}
    corpus: list[dict[str, Any]] = []
    seen: set[Any] = set()

    for sort in _CORPUS_SORTS:
        if len(corpus) >= target:
            break
        for page in range(1, _MAX_PAGES_PER_SORT + 1):
            if len(corpus) >= target:
                break
            limiter.wait()
            try:
                resp = httpx.get(
                    f"{ARCHIDEKT_API_URL}/decks/v3/",
                    params={
                        "deckFormat": COMMANDER_FORMAT_ID,
                        "orderBy": sort,
                        "page": page,
                    },
                    headers=headers,
                    timeout=20,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    break
                results = resp.json().get("results") or []
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                logger.warning("corpus search %s p%d failed: %s", sort, page, e)
                break
            if not results:
                break

            for summary in results:
                if len(corpus) >= target:
                    break
                deck_id = summary.get("id")
                tags = extract_summary_metadata(summary)["tags"]
                if not deck_id or deck_id in seen or not tags:
                    continue
                seen.add(deck_id)

                cards = _fetch_deck_cards(deck_id, headers, limiter)
                if not cards:
                    continue
                corpus.append({
                    "deck_id": deck_id,
                    "name": summary.get("name"),
                    "tags": tags,
                    "cards": cards,
                })

            logger.info(
                "[%s] page %d: %d labeled decks collected", sort, page, len(corpus)
            )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(corpus, f)
    logger.info("Wrote %d labeled decks to %s", len(corpus), cache_path)
    return corpus


def _fetch_deck_cards(
    deck_id: Any, headers: dict[str, str], limiter: RateLimiter
) -> list[str]:
    """Fetch a deck's card names from the Archidekt detail endpoint."""
    limiter.wait()
    try:
        resp = httpx.get(
            f"{ARCHIDEKT_API_URL}/decks/{deck_id}/",
            headers=headers,
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        _commanders, cards = parse_deck_detail(resp.json())
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.debug("detail fetch for %s failed: %s", deck_id, e)
        return []
    return [name for name, _qty, _is_cmdr in cards]


# ---------------------------------------------------------------------------
# Evaluation (pure)
# ---------------------------------------------------------------------------


def evaluate(
    corpus: list[dict[str, Any]], library: ArchetypeLibrary
) -> dict[str, Any]:
    """Compute per-archetype precision/recall/F1 against creator tags.

    Only decks with at least one *recognized* creator tag contribute to
    precision/recall (a deck we cannot label from tags is not ground truth).

    Args:
        corpus: Labeled decks from :func:`build_labeled_corpus`.
        library: A loaded :class:`ArchetypeLibrary`.

    Returns:
        A report dict with ``per_archetype`` metrics, ``macro`` / ``micro``
        averages, and coverage counts.
    """
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)

    total = len(corpus)
    with_recognized_gold = 0
    tagged_but_unrecognized = 0

    for deck in corpus:
        gold = tags_to_archetypes(deck.get("tags", []), library)
        if not gold:
            tagged_but_unrecognized += 1
            continue
        with_recognized_gold += 1

        pred = set(classify_deck(deck.get("cards", []), library).labels)

        for arch in library.archetypes:
            in_gold = arch in gold
            in_pred = arch in pred
            if in_gold and in_pred:
                tp[arch] += 1
            elif in_pred and not in_gold:
                fp[arch] += 1
            elif in_gold and not in_pred:
                fn[arch] += 1

    per_archetype: dict[str, dict[str, float | int]] = {}
    for arch in library.archetypes:
        precision = _safe_div(tp[arch], tp[arch] + fp[arch])
        recall = _safe_div(tp[arch], tp[arch] + fn[arch])
        f1 = _safe_div(2 * precision * recall, precision + recall)
        per_archetype[arch] = {
            "support": tp[arch] + fn[arch],
            "tp": tp[arch],
            "fp": fp[arch],
            "fn": fn[arch],
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    sup_arch = [a for a, m in per_archetype.items() if m["support"] > 0]
    macro = {
        "precision": round(
            _safe_div(
                sum(per_archetype[a]["precision"] for a in sup_arch), len(sup_arch)
            ), 3,
        ),
        "recall": round(
            _safe_div(
                sum(per_archetype[a]["recall"] for a in sup_arch), len(sup_arch)
            ), 3,
        ),
        "f1": round(
            _safe_div(
                sum(per_archetype[a]["f1"] for a in sup_arch), len(sup_arch)
            ), 3,
        ),
    }
    tt, tfp, tfn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    micro_p = _safe_div(tt, tt + tfp)
    micro_r = _safe_div(tt, tt + tfn)
    micro = {
        "precision": round(micro_p, 3),
        "recall": round(micro_r, 3),
        "f1": round(_safe_div(2 * micro_p * micro_r, micro_p + micro_r), 3),
    }

    return {
        "total_decks": total,
        "decks_with_recognized_gold": with_recognized_gold,
        "tagged_but_unrecognized": tagged_but_unrecognized,
        "per_archetype": per_archetype,
        "macro": macro,
        "micro": micro,
    }


def _safe_div(num: float, denom: float) -> float:
    return num / denom if denom else 0.0


def format_report(report: dict[str, Any]) -> str:
    """Render an evaluation report as a readable table."""
    lines = [
        "=== Macro-archetype signature validation ===",
        f"labeled decks: {report['total_decks']}  "
        f"(recognized gold: {report['decks_with_recognized_gold']}, "
        f"tagged-but-unrecognized: {report['tagged_but_unrecognized']})",
        "",
        f"{'archetype':<14}{'support':>8}{'prec':>7}{'recall':>8}{'f1':>7}"
        f"{'tp':>5}{'fp':>5}{'fn':>5}",
        "-" * 63,
    ]
    for arch, m in sorted(
        report["per_archetype"].items(),
        key=lambda kv: kv[1]["support"], reverse=True,
    ):
        lines.append(
            f"{arch:<14}{m['support']:>8}{m['precision']:>7}{m['recall']:>8}"
            f"{m['f1']:>7}{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}"
        )
    lines.append("-" * 63)
    ma, mi = report["macro"], report["micro"]
    lines.append(
        f"{'MACRO avg':<14}{'':>8}{ma['precision']:>7}{ma['recall']:>8}{ma['f1']:>7}"
    )
    lines.append(
        f"{'MICRO avg':<14}{'':>8}{mi['precision']:>7}{mi['recall']:>8}{mi['f1']:>7}"
    )
    lines.append("")
    lines.append(
        "note: precision is a LOWER BOUND — creator tags are incomplete, so a "
        "'false positive' may be a correct label the creator simply omitted."
    )
    return "\n".join(lines)
