"""Tests for EDHREC-aware Pareto filter protection.

Validates that cards with strong EDHREC inclusion survive Pareto
filtering even when dominated on raw CVAR + price by cards the
community doesn't use for this commander.
"""

import json


from sabermetrics.pipeline.trace import GenerationTracer


def _make_card(
    name: str,
    cvar: float,
    price: float,
    edhrec_pct: float = 0.0,
    role: str = "utility",
) -> dict:
    """Create a minimal card dict for Pareto filter testing."""
    return {
        "id": f"id-{name.lower().replace(' ', '-')}",
        "name": name,
        "price_usd": str(price),
        "_cvar_score": cvar,
        "edhrec_inclusion_pct": edhrec_pct,
        "role_tags": json.dumps([role]),
    }


def _run_pareto(cards: list[dict], watchlist: set[str] | None = None) -> tuple[list[dict], list]:
    """Run the Pareto filter logic and return (kept, trace_events)."""
    if watchlist is None:
        watchlist = {c["name"] for c in cards}
    tracer = GenerationTracer(generation_id="test", watchlist=watchlist)

    # Replicate _pareto_filter logic (without DeckBuilder instantiation)
    role_groups: dict[str, list[dict]] = {}
    for card in cards:
        role_tags_raw = card.get("role_tags", '["utility"]')
        if isinstance(role_tags_raw, str):
            try:
                role_tags = json.loads(role_tags_raw)
            except (json.JSONDecodeError, TypeError):
                role_tags = ["utility"]
        else:
            role_tags = role_tags_raw or ["utility"]
        primary_role = role_tags[0] if role_tags else "utility"
        role_groups.setdefault(primary_role, []).append(card)

    kept = []
    for role, group in role_groups.items():
        if role == "land":
            kept.extend(group)
            continue
        group.sort(key=lambda c: c.get("_cvar_score", 0), reverse=True)
        frontier = []
        for card in group:
            card_name = card.get("name", "")
            cvar = card.get("_cvar_score", 0)
            price = float(card.get("price_usd", 0) or 0)
            dominated = False
            edhrec_saved = False
            card_edhrec = card.get("edhrec_inclusion_pct", 0.0)
            for f_card in frontier:
                f_cvar = f_card.get("_cvar_score", 0)
                f_price = float(f_card.get("price_usd", 0) or 0)
                if f_cvar >= cvar and f_price <= price and (f_cvar > cvar or f_price < price):
                    f_edhrec = f_card.get("edhrec_inclusion_pct", 0.0)
                    if card_edhrec >= 30.0 and (card_edhrec - f_edhrec) >= 25.0:
                        edhrec_saved = True
                        continue
                    dominated = True
                    break
            if not dominated:
                frontier.append(card)
                if edhrec_saved:
                    tracer.record(
                        card_name=card_name, stage="pareto", action="protected",
                        card_id=card.get("id"), score=cvar,
                        reason=f"EDHREC protected ({card_edhrec:.0f}% inclusion)",
                    )
                else:
                    tracer.record(
                        card_name=card_name, stage="pareto", action="considered",
                        card_id=card.get("id"), score=cvar,
                        reason="survived Pareto",
                    )
            else:
                tracer.record(
                    card_name=card_name, stage="pareto", action="rejected",
                    card_id=card.get("id"), score=cvar,
                    reason="dominated",
                )
        kept.extend(frontier)
    return kept, tracer.events


class TestEDHRECParetoProtection:
    """Tests for EDHREC-aware dominance override in Pareto filter."""

    def test_high_edhrec_protected_from_zero_edhrec_dominator(self) -> None:
        """A 100% EDHREC card survives dominance by a 0% EDHREC card."""
        cards = [
            _make_card("Spectral Adversary", cvar=0.836, price=0.15, edhrec_pct=0.0),
            _make_card("Wall of Denial", cvar=0.600, price=0.43, edhrec_pct=100.0),
        ]
        kept, events = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Wall of Denial" in kept_names
        protected = [e for e in events if e.card_name == "Wall of Denial" and e.action == "protected"]
        assert len(protected) == 1
        assert "EDHREC protected" in protected[0].reason

    def test_all_four_arcades_staples_protected(self) -> None:
        """Wall of Denial, High Alert, Assault Formation, Axebane Guardian all survive."""
        targets = [
            _make_card("Wall of Denial", cvar=0.60, price=0.43, edhrec_pct=100.0, role="wincon"),
            _make_card("High Alert", cvar=0.60, price=0.42, edhrec_pct=100.0, role="wincon"),
            _make_card("Assault Formation", cvar=0.60, price=0.33, edhrec_pct=100.0, role="wincon"),
            _make_card("Axebane Guardian", cvar=0.55, price=0.18, edhrec_pct=100.0, role="wincon"),
        ]
        dominators = [
            _make_card("Generic Beater A", cvar=0.85, price=0.10, edhrec_pct=0.0, role="wincon"),
            _make_card("Generic Beater B", cvar=0.75, price=0.08, edhrec_pct=0.0, role="wincon"),
        ]
        kept, events = _run_pareto(targets + dominators)
        kept_names = {c["name"] for c in kept}
        for t in targets:
            assert t["name"] in kept_names, f"{t['name']} missing from kept"

    def test_below_floor_not_protected(self) -> None:
        """A card with 15% EDHREC (below 30% floor) is not protected."""
        cards = [
            _make_card("Dominator", cvar=0.90, price=0.50, edhrec_pct=0.0),
            _make_card("Niche Card", cvar=0.60, price=1.00, edhrec_pct=15.0),
        ]
        kept, events = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Niche Card" not in kept_names

    def test_small_gap_not_protected(self) -> None:
        """55% vs 45% (gap=10 < 25) — NOT protected, CVAR breaks tie."""
        cards = [
            _make_card("Popular A", cvar=0.90, price=0.50, edhrec_pct=45.0),
            _make_card("Popular B", cvar=0.70, price=1.00, edhrec_pct=55.0),
        ]
        kept, events = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Popular B" not in kept_names

    def test_large_gap_protected(self) -> None:
        """80% vs 0% (gap=80 >= 25) — PROTECTED."""
        cards = [
            _make_card("Generic", cvar=0.90, price=0.50, edhrec_pct=0.0),
            _make_card("Community Favorite", cvar=0.70, price=1.00, edhrec_pct=80.0),
        ]
        kept, events = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Community Favorite" in kept_names

    def test_exactly_at_thresholds(self) -> None:
        """30% EDHREC and exactly 25pp gap — should be protected."""
        cards = [
            _make_card("Dominator", cvar=0.90, price=0.50, edhrec_pct=5.0),
            _make_card("Threshold Card", cvar=0.70, price=1.00, edhrec_pct=30.0),
        ]
        kept, _ = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Threshold Card" in kept_names

    def test_just_below_thresholds(self) -> None:
        """29% EDHREC (just below floor) — NOT protected."""
        cards = [
            _make_card("Dominator", cvar=0.90, price=0.50, edhrec_pct=0.0),
            _make_card("Almost Card", cvar=0.70, price=1.00, edhrec_pct=29.0),
        ]
        kept, _ = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Almost Card" not in kept_names

    def test_gap_just_below_threshold(self) -> None:
        """40% vs 16% (gap=24 < 25) — NOT protected."""
        cards = [
            _make_card("Competitor", cvar=0.90, price=0.50, edhrec_pct=16.0),
            _make_card("Slightly More Popular", cvar=0.70, price=1.00, edhrec_pct=40.0),
        ]
        kept, _ = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Slightly More Popular" not in kept_names

    def test_zero_edhrec_never_protected(self) -> None:
        """Cards with 0% EDHREC are never EDHREC-protected."""
        cards = [
            _make_card("Better Card", cvar=0.90, price=0.50, edhrec_pct=0.0),
            _make_card("Worse Card", cvar=0.70, price=1.00, edhrec_pct=0.0),
        ]
        kept, events = _run_pareto(cards)
        protected = [e for e in events if e.action == "protected"]
        assert len(protected) == 0

    def test_both_100_pct_cvar_breaks_tie(self) -> None:
        """Two cards both at 100% EDHREC — gap is 0, CVAR decides."""
        cards = [
            _make_card("Negate", cvar=0.82, price=0.30, edhrec_pct=100.0),
            _make_card("High Alert", cvar=0.60, price=0.42, edhrec_pct=100.0),
        ]
        kept, _ = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        # Both at 100%, gap=0 < 25, so normal dominance applies
        assert "High Alert" not in kept_names

    def test_multiple_dominators_all_checked(self) -> None:
        """Card survives if EDHREC-protected against one dominator but
        then dominated by another with high EDHREC."""
        cards = [
            _make_card("Zero EDHREC Dominator", cvar=0.90, price=0.10, edhrec_pct=0.0),
            _make_card("High EDHREC Dominator", cvar=0.85, price=0.20, edhrec_pct=90.0),
            _make_card("Target", cvar=0.60, price=0.50, edhrec_pct=100.0),
        ]
        kept, events = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        # Target is protected from Zero EDHREC (100-0=100 ≥ 25)
        # But High EDHREC Dominator also dominates: 100-90=10 < 25, so NOT protected
        assert "Target" not in kept_names

    def test_protected_from_all_dominators_survives(self) -> None:
        """Card protected from ALL dominators on frontier survives."""
        cards = [
            _make_card("Zero A", cvar=0.90, price=0.10, edhrec_pct=0.0),
            _make_card("Zero B", cvar=0.85, price=0.20, edhrec_pct=0.0),
            _make_card("Target", cvar=0.60, price=0.50, edhrec_pct=100.0),
        ]
        kept, events = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Target" in kept_names
        protected = [e for e in events if e.card_name == "Target" and e.action == "protected"]
        assert len(protected) == 1

    def test_trace_records_correct_action(self) -> None:
        """Trace event for EDHREC-protected card has action='protected'."""
        cards = [
            _make_card("Dominator", cvar=0.90, price=0.50, edhrec_pct=0.0),
            _make_card("Protected", cvar=0.70, price=1.00, edhrec_pct=80.0),
        ]
        _, events = _run_pareto(cards)
        protected = [e for e in events if e.card_name == "Protected"]
        assert len(protected) == 1
        assert protected[0].action == "protected"
        assert "EDHREC protected (80% inclusion)" in protected[0].reason

    def test_different_roles_independent(self) -> None:
        """EDHREC protection works independently per role."""
        cards = [
            _make_card("Ramp Dominator", cvar=0.90, price=0.10, edhrec_pct=0.0, role="ramp"),
            _make_card("Ramp Target", cvar=0.60, price=0.50, edhrec_pct=100.0, role="ramp"),
            _make_card("Draw Dominator", cvar=0.90, price=0.10, edhrec_pct=80.0, role="draw"),
            _make_card("Draw Target", cvar=0.60, price=0.50, edhrec_pct=100.0, role="draw"),
        ]
        kept, _ = _run_pareto(cards)
        kept_names = {c["name"] for c in kept}
        assert "Ramp Target" in kept_names  # 100 vs 0, gap=100 ≥ 25
        assert "Draw Target" not in kept_names  # 100 vs 80, gap=20 < 25
