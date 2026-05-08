"""Pairwise synergy matrix for candidate cards.

Combines three signals to score how well any two cards work together:
1. Rule matching — hand-curated trigger/payoff pairs (config/synergy_rules.yaml)
2. Co-occurrence — empirical data from tracked decklists
3. Embedding similarity — semantic similarity of oracle text (cross-role only)

Matrix is symmetric, computed once per commander, and cacheable.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path

import numpy as np
import yaml

from sabermetrics.analytics.embeddings import get_embedding_service

logger = logging.getLogger(__name__)

# Signal weights for hybrid score
RULE_WEIGHT = 0.40
COOCCURRENCE_WEIGHT = 0.35
EMBEDDING_WEIGHT = 0.25


class SynergyMatrix:
    """Precomputed pairwise synergy scores for candidate cards."""

    def __init__(
        self,
        matrix: np.ndarray,
        card_id_to_index: dict[str, int],
        index_to_card_id: dict[int, str],
    ) -> None:
        self.matrix = matrix
        self.card_id_to_index = card_id_to_index
        self.index_to_card_id = index_to_card_id

    def get_synergy(self, card_id_a: str, card_id_b: str) -> float:
        """Get synergy score between two cards."""
        idx_a = self.card_id_to_index.get(card_id_a)
        idx_b = self.card_id_to_index.get(card_id_b)
        if idx_a is None or idx_b is None:
            return 0.0
        return float(self.matrix[idx_a, idx_b])


def build_synergy_matrix(
    candidates: list[dict],
    commander_id: str,
    db_path: Path,
) -> SynergyMatrix:
    """Build hybrid synergy matrix from three signal sources.

    Args:
        candidates: List of candidate card dicts (must have 'id', 'oracle_text',
            'role_tags' or inferred roles).
        commander_id: Scryfall ID of the commander.
        db_path: Path to SQLite database for co-occurrence lookup.

    Returns:
        SynergyMatrix with N×N float32 scores.
    """
    n = len(candidates)
    if n == 0:
        return SynergyMatrix(
            matrix=np.zeros((0, 0), dtype=np.float32),
            card_id_to_index={},
            index_to_card_id={},
        )

    # Build index mappings
    card_id_to_index: dict[str, int] = {}
    index_to_card_id: dict[int, str] = {}
    for i, card in enumerate(candidates):
        cid = card.get("id", str(i))
        card_id_to_index[cid] = i
        index_to_card_id[i] = cid

    candidate_ids = [candidates[i].get("id", str(i)) for i in range(n)]

    # Signal 1: Rule matching
    rules = _load_synergy_rules()
    rule_matrix = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            score = _match_rules(candidates[i], candidates[j], rules)
            rule_matrix[i, j] = score
            rule_matrix[j, i] = score

    # Signal 2: Co-occurrence
    cooccurrence_rates = _batch_cooccurrence(candidate_ids, commander_id, db_path)
    cooccurrence_matrix = np.zeros((n, n), dtype=np.float32)
    max_rate = max(cooccurrence_rates.values()) if cooccurrence_rates else 1.0
    if max_rate == 0:
        max_rate = 1.0
    for (id_a, id_b), rate in cooccurrence_rates.items():
        idx_a = card_id_to_index.get(id_a)
        idx_b = card_id_to_index.get(id_b)
        if idx_a is not None and idx_b is not None:
            normalized = rate / max_rate
            cooccurrence_matrix[idx_a, idx_b] = normalized
            cooccurrence_matrix[idx_b, idx_a] = normalized

    # Signal 3: Embedding similarity (cross-role only)
    embedding_matrix = _compute_embedding_matrix(candidates)

    # Zero out same-role pairs for embedding signal
    primary_roles = _get_primary_roles(candidates)
    for i in range(n):
        for j in range(i + 1, n):
            if primary_roles[i] == primary_roles[j]:
                embedding_matrix[i, j] = 0.0
                embedding_matrix[j, i] = 0.0

    # Hybrid combination
    hybrid = (
        RULE_WEIGHT * rule_matrix
        + COOCCURRENCE_WEIGHT * cooccurrence_matrix
        + EMBEDDING_WEIGHT * embedding_matrix
    )

    logger.info(
        "Synergy matrix built: %dx%d, rule_max=%.3f, cooc_pairs=%d, emb_mean=%.3f",
        n, n,
        float(rule_matrix.max()) if n > 0 else 0,
        len(cooccurrence_rates),
        float(embedding_matrix.mean()) if n > 0 else 0,
    )

    return SynergyMatrix(
        matrix=hybrid,
        card_id_to_index=card_id_to_index,
        index_to_card_id=index_to_card_id,
    )


def _load_synergy_rules() -> list[dict]:
    """Load and parse config/synergy_rules.yaml."""
    config_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "config" / "synergy_rules.yaml"
    )
    if not config_path.exists():
        logger.warning("synergy_rules.yaml not found at %s", config_path)
        return []
    with open(config_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("rules", [])


def _match_rules(
    card_a: dict, card_b: dict, rules: list[dict],
) -> float:
    """Check if card pair matches any synergy rules. Returns max strength."""
    max_strength = 0.0
    for rule in rules:
        # Check A=trigger, B=payoff
        s1 = _single_rule_match(card_a, card_b, rule)
        # Check B=trigger, A=payoff
        s2 = _single_rule_match(card_b, card_a, rule)
        max_strength = max(max_strength, s1, s2)
    return max_strength


def _single_rule_match(
    trigger_card: dict, payoff_card: dict, rule: dict,
) -> float:
    """Check if trigger_card matches rule trigger and payoff_card matches payoff.

    Returns rule strength if matched, 0.0 otherwise.
    """
    trigger = rule.get("trigger", {})
    payoff = rule.get("payoff", {})
    strength = rule.get("strength", 0.5)

    if not _card_matches_clause(trigger_card, trigger):
        return 0.0
    if not _card_matches_clause(payoff_card, payoff):
        return 0.0

    return strength


def _card_matches_clause(card: dict, clause: dict) -> bool:
    """Check if a card matches a rule clause (trigger or payoff).

    Clause fields (all must match if present):
    - text_contains: list[str] — all must appear in oracle text
    - keywords: list[str] — any must appear in card keywords
    - type_includes: list[str] — any must appear in type line
    - cmc_range: [min, max] — card CMC must be in range
    """
    if not clause:
        return False

    oracle = (card.get("oracle_text") or "").lower()
    type_line = (card.get("type_line") or "").lower()

    # Parse keywords from card
    kw_raw = card.get("keywords", "[]")
    if isinstance(kw_raw, str):
        try:
            keywords = [k.lower() for k in json.loads(kw_raw)]
        except (json.JSONDecodeError, TypeError):
            keywords = []
    else:
        keywords = [k.lower() for k in (kw_raw or [])]

    # text_contains: ALL must match
    text_contains = clause.get("text_contains", [])
    if text_contains:
        for text in text_contains:
            if text.lower() not in oracle:
                return False

    # keywords: ANY must match
    rule_keywords = clause.get("keywords", [])
    if rule_keywords:
        if not any(kw.lower() in keywords for kw in rule_keywords):
            return False

    # type_includes: ANY must match
    type_includes = clause.get("type_includes", [])
    if type_includes:
        if not any(t.lower() in type_line for t in type_includes):
            return False

    # cmc_range: card CMC must be in range
    cmc_range = clause.get("cmc_range")
    if cmc_range and len(cmc_range) == 2:
        cmc = float(card.get("cmc", 0) or 0)
        if cmc < cmc_range[0] or cmc > cmc_range[1]:
            return False

    return True


def _batch_cooccurrence(
    candidate_ids: list[str],
    commander_id: str,
    db_path: Path,
) -> dict[tuple[str, str], float]:
    """Load all cooccurrence rates for candidate pairs in one query.

    Args:
        candidate_ids: List of card IDs to look up.
        commander_id: Commander context for co-occurrence data.
        db_path: Path to SQLite database.

    Returns:
        Dict mapping (card_a_id, card_b_id) to cooccurrence_rate.
    """
    if not candidate_ids:
        return {}

    conn = sqlite3.connect(str(db_path))
    try:
        # Check if table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='card_cooccurrence'"
        )
        if not cursor.fetchone():
            return {}

        # Build IN clause with placeholders
        id_set = set(candidate_ids)
        placeholders = ",".join("?" * len(id_set))
        id_list = list(id_set)

        query = (
            f"SELECT card_a_id, card_b_id, cooccurrence_rate "
            f"FROM card_cooccurrence "
            f"WHERE commander_id = ? "
            f"AND card_a_id IN ({placeholders}) "
            f"AND card_b_id IN ({placeholders})"
        )
        params = [commander_id] + id_list + id_list

        cursor = conn.execute(query, params)
        results: dict[tuple[str, str], float] = {}
        for row in cursor:
            results[(row[0], row[1])] = row[2]
        return results

    except sqlite3.OperationalError:
        logger.debug("card_cooccurrence table query failed, skipping")
        return {}
    finally:
        conn.close()


def _compute_embedding_matrix(candidates: list[dict]) -> np.ndarray:
    """Compute pairwise cosine similarity from oracle text embeddings.

    Args:
        candidates: List of card dicts with 'oracle_text'.

    Returns:
        N×N float32 matrix of cosine similarities (0-1 clamped).
    """
    n = len(candidates)
    if n == 0:
        return np.zeros((0, 0), dtype=np.float32)

    texts = [
        (c.get("oracle_text") or c.get("name") or "unknown card")
        for c in candidates
    ]

    try:
        service = get_embedding_service()
        embeddings = service.embed_batch(texts)
        emb_matrix = np.array(embeddings, dtype=np.float32)  # N x dim

        # Normalize rows
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        emb_matrix = emb_matrix / norms

        # Cosine similarity via matmul
        sim = emb_matrix @ emb_matrix.T

        # Clamp to [0, 1] and zero diagonal
        sim = np.clip(sim, 0.0, 1.0)
        np.fill_diagonal(sim, 0.0)

        return sim.astype(np.float32)

    except Exception as e:
        logger.warning("Embedding computation failed, using zeros: %s", e)
        return np.zeros((n, n), dtype=np.float32)


def _get_primary_roles(candidates: list[dict]) -> list[str]:
    """Extract the primary role tag for each candidate card.

    Args:
        candidates: List of card dicts with 'role_tags' field.

    Returns:
        List of primary role strings (one per candidate).
    """
    roles: list[str] = []
    for card in candidates:
        rt_raw = card.get("role_tags", "[]")
        if isinstance(rt_raw, str):
            try:
                rt = json.loads(rt_raw)
            except (json.JSONDecodeError, TypeError):
                rt = []
        else:
            rt = rt_raw or []

        roles.append(rt[0] if rt else "utility")
    return roles
