"""Assemble a lab from configuration and environment.

Each part independently falls back to its fixture implementation when its live
backing is not configured, and the mode it landed in is reported rather than
inferred. That reporting is the point: a page showing a deck built from fixture
card data must say so, or the fixture becomes indistinguishable from the corpus
the moment someone screenshots it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from sabermetrics.cedh.adapters_fixture import (
    FixtureCardRepository,
    FixtureMetaRepository,
)
from sabermetrics.cedh.errors import ModelConfigurationError
from sabermetrics.cedh.lab import CedhDeckLab
from sabermetrics.cedh.model_gateway import ModelGateway
from sabermetrics.cedh.packs import PackRegistry
from sabermetrics.cedh.repositories import CardRepository, MetaRepository
from sabermetrics.cedh.settings import CedhSettings, load_cedh_settings
from sabermetrics.cedh.simulator import SimulatorClient, build_simulator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LabModes:
    """Which implementation each part of the lab is actually running."""

    cards: str
    meta: str
    model: str
    simulator: str

    @property
    def any_fixture(self) -> bool:
        return "fixture" in (self.cards, self.meta) or self.simulator == "fixture"

    @property
    def notices(self) -> list[str]:
        """Human-readable warnings about non-production modes."""
        out: list[str] = []
        if self.cards == "fixture":
            out.append(
                "Card data is a synthetic fixture, not the mtg_v1 corpus. Set "
                "MTG_V1_DSN to read real card facts."
            )
        if self.meta == "fixture":
            out.append(
                "Tournament data is a fixture. Set MTG_V1_DSN once the "
                "mtg_v1 tournament views exist."
            )
        if self.model == "none":
            out.append(
                "No model provider is configured, so there is no narrative. "
                "The deck itself is deterministic and unaffected."
            )
        if self.simulator == "fixture":
            out.append(
                "Simulation figures come from a checked-in fixture, not a run "
                "of commander_simulator against this list."
            )
        return out


def build_card_repository(
    settings: CedhSettings,
) -> tuple[CardRepository, str]:
    """Return the card repository and the mode it is in."""
    dsn = os.environ.get("MTG_V1_DSN", "").strip()
    if dsn:
        from sabermetrics.cedh.adapters_postgres import PostgresCardRepository

        return PostgresCardRepository(dsn), "mtg_v1"
    return FixtureCardRepository(Path(settings.fixtures_dir)), "fixture"


def build_meta_repository(
    settings: CedhSettings,
) -> tuple[MetaRepository, str]:
    """Return the meta repository and the mode it is in."""
    dsn = os.environ.get("MTG_V1_DSN", "").strip()
    if dsn:
        from sabermetrics.cedh.adapters_postgres import PostgresMetaRepository

        return PostgresMetaRepository(dsn), "mtg_v1"
    return FixtureMetaRepository(Path(settings.fixtures_dir)), "fixture"


def build_model_gateway(
    settings: CedhSettings,
    *,
    db_path: str | None = None,
    user_id: str | None = None,
    deck_id: str | None = None,
) -> tuple[ModelGateway | None, str]:
    """Return the model gateway, or ``None`` when no credential is configured.

    A missing credential is not an error. The deck is deterministic, so running
    with no model is a supported mode rather than a broken one — it costs the
    narrative and nothing else.
    """
    if not os.environ.get(settings.model.credential_env, "").strip():
        return None, "none"
    try:
        from sabermetrics.cedh.provider_deepseek import build_gateway

        gateway = build_gateway(
            settings, db_path=db_path, user_id=user_id, deck_id=deck_id
        )
    except ModelConfigurationError as exc:
        logger.warning("cEDH model gateway is misconfigured: %s", exc)
        return None, "none"
    return gateway, f"{gateway.provider}:{gateway.model_id}"


def build_simulator_client(
    settings: CedhSettings,
) -> tuple[SimulatorClient, str]:
    """Return the simulator client and the mode it is in."""
    return build_simulator(settings.simulator), settings.simulator.mode


def build_default_lab(
    *,
    db_path: str | None = None,
    user_id: str | None = None,
    deck_id: str | None = None,
    settings: CedhSettings | None = None,
) -> tuple[CedhDeckLab, LabModes]:
    """Build the configured lab and report the mode each part is in."""
    settings = settings or load_cedh_settings()
    cards, cards_mode = build_card_repository(settings)
    meta, meta_mode = build_meta_repository(settings)
    gateway, model_mode = build_model_gateway(
        settings, db_path=db_path, user_id=user_id, deck_id=deck_id
    )
    simulator, sim_mode = build_simulator_client(settings)

    lab = CedhDeckLab(
        cards=cards,
        meta=meta,
        registry=PackRegistry(cards, Path(settings.packs_dir)),
        simulator=simulator,
        gateway=gateway,
        settings=settings,
    )
    return lab, LabModes(
        cards=cards_mode,
        meta=meta_mode,
        model=model_mode,
        simulator=sim_mode,
    )
