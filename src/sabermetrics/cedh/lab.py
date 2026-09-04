"""The cEDH Deck Lab: the vertical slice, wired end to end.

Order matters and is the design. The deck is built **before** the model is
called and does not depend on it: if the provider is down, over budget, or
returns something that will not validate, the user still gets the deck and a
visible note that the prose is missing. The model writes about the deck; it
does not decide it.

Every model output is cross-checked against what was actually built before it
is shown:

* an explanation may only cite oracle_ids that are in the candidate;
* an evidence summary may only cite chunk ids that were sent, and may not
  claim more events or decks than the package contained;
* a pack id from intent classification is looked up in the registry, and an
  unknown one means "unsupported", never "build something else".

A claim that fails its check is dropped and recorded, not quietly trusted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from sabermetrics.cedh.builder import build_candidate
from sabermetrics.cedh.candidate import DeckCandidate
from sabermetrics.cedh.domain import BuildConstraints, LabRequest, MetagameWindow
from sabermetrics.cedh.errors import (
    CandidateConstraintViolation,
    CedhError,
    UnsupportedCommander,
)
from sabermetrics.cedh.evidence import EvidencePackage, EvidenceService
from sabermetrics.cedh.model_gateway import (
    ModelGateway,
    ReasoningMode,
    StructuredRequest,
    UsageRecord,
)
from sabermetrics.cedh.packs import PackRegistry, PackSummary, summarize
from sabermetrics.cedh.prompts import (
    EVIDENCE_SYSTEM,
    EVIDENCE_VERSION,
    EXPLAIN_SYSTEM,
    EXPLAIN_VERSION,
    INTENT_SYSTEM,
    INTENT_VERSION,
    evidence_user,
    explain_user,
    intent_user,
)
from sabermetrics.cedh.repositories import CardRepository, MetaRepository
from sabermetrics.cedh.responses import (
    DeckExplanation,
    EvidenceSummary,
    IntentClassification,
)
from sabermetrics.cedh.settings import CedhSettings, load_cedh_settings
from sabermetrics.cedh.simulator import (
    NotSimulated,
    SimulationOutcome,
    SimulatorClient,
)
from sabermetrics.errors import LLMCostCeilingExceeded

logger = logging.getLogger(__name__)


class LabResult(BaseModel):
    """Everything one lab run produced, including what it could not produce."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    supported: bool
    pack_id: str = ""
    pack_name: str = ""
    unsupported_detail: str = ""
    candidate: DeckCandidate | None = None
    evidence: EvidencePackage | None = None
    simulation: SimulationOutcome | None = None
    evidence_summary: EvidenceSummary | None = None
    explanation: DeckExplanation | None = None
    intent: IntentClassification | None = None
    #: Every model call this run made, successful or not.
    usages: list[UsageRecord] = []
    #: Things that did not work, in the user's terms. Never silently empty.
    warnings: list[str] = []

    @property
    def total_cost_usd(self) -> float:
        return round(sum(u.cost_usd for u in self.usages), 6)

    @property
    def simulated(self) -> bool:
        return self.simulation is not None and self.simulation.status == "simulated"


@dataclass
class CedhDeckLab:
    """Orchestrates one cEDH deck-lab request.

    Args:
        cards: Card facts and legality.
        meta: Tournament facts. May report itself unavailable.
        registry: Strategy pack registry.
        simulator: Simulator client.
        gateway: Model gateway. ``None`` runs the whole slice with no model at
            all, which is a supported mode: the deck is deterministic.
        settings: cEDH settings.
    """

    cards: CardRepository
    meta: MetaRepository
    registry: PackRegistry
    simulator: SimulatorClient
    gateway: ModelGateway | None = None
    settings: CedhSettings = field(default_factory=load_cedh_settings)

    # -- public API -------------------------------------------------------

    def pack_summaries(self) -> list[PackSummary]:
        """Every authored pack, supported or not, with simulator support."""
        return summarize(self.registry, self.simulator.supported_commander_keys())

    def run(
        self,
        request: LabRequest,
        *,
        candidate_id: str | None = None,
        generated_at: datetime | None = None,
    ) -> LabResult:
        """Run the slice: resolve, retrieve, build, simulate, explain."""
        usages: list[UsageRecord] = []
        warnings: list[str] = []

        pack_id, intent = self._resolve_pack(request, usages, warnings)
        if pack_id is None:
            return LabResult(
                supported=False,
                unsupported_detail=(
                    "No supported strategy pack matches this request. "
                    "Supported packs: "
                    + (
                        ", ".join(self.registry.supported_pack_ids())
                        or "none are currently resolvable"
                    )
                ),
                intent=intent,
                usages=usages,
                warnings=warnings,
            )

        try:
            pack = self.registry.get(pack_id)
        except UnsupportedCommander as exc:
            return LabResult(
                supported=False,
                pack_id=pack_id,
                unsupported_detail=str(exc),
                intent=intent,
                usages=usages,
                warnings=warnings,
            )

        window = request.constraints.metagame or MetagameWindow()
        evidence_service = EvidenceService(
            self.meta,
            settings=self.settings.evidence,
            card_snapshot=self.cards.snapshot(),
        )
        evidence = evidence_service.build(pack.commander, window)
        if not evidence.meta_available:
            warnings.append(
                "No tournament evidence is available for this build: "
                f"{evidence.meta_detail}. Nothing below is corroborated by "
                "event results."
            )

        try:
            candidate = build_candidate(
                pack,
                self.cards,
                constraints=request.constraints,
                evidence=evidence,
                candidate_id=candidate_id,
                generated_at=generated_at,
            )
        except CandidateConstraintViolation as exc:
            return LabResult(
                supported=True,
                pack_id=pack.pack_id,
                pack_name=pack.name,
                unsupported_detail=str(exc),
                evidence=evidence,
                intent=intent,
                usages=usages,
                warnings=[*warnings, str(exc)],
            )

        simulation = self.simulator.simulate(candidate)
        if isinstance(simulation, NotSimulated):
            warnings.append(f"Not simulated ({simulation.reason}): {simulation.detail}")

        summary = self._summarise_evidence(evidence, usages, warnings)
        explanation = self._explain(
            pack, candidate, evidence, simulation, usages, warnings
        )

        return LabResult(
            supported=True,
            pack_id=pack.pack_id,
            pack_name=pack.name,
            candidate=candidate,
            evidence=evidence,
            simulation=simulation,
            evidence_summary=summary,
            explanation=explanation,
            intent=intent,
            usages=usages,
            warnings=warnings,
        )

    # -- steps ------------------------------------------------------------

    def _resolve_pack(
        self,
        request: LabRequest,
        usages: list[UsageRecord],
        warnings: list[str],
    ) -> tuple[str | None, IntentClassification | None]:
        """Pick the pack: explicit id, then classification, then unsupported."""
        supported = set(self.registry.supported_pack_ids())
        if request.pack_id:
            if request.pack_id in supported:
                return request.pack_id, None
            # An explicit id that does not resolve is still reported through
            # the registry, so the user sees *why* rather than "not found".
            return request.pack_id, None

        if not request.raw_intent:
            if len(supported) == 1:
                return next(iter(supported)), None
            return None, None

        if self.gateway is None:
            warnings.append(
                "No model gateway is configured, so free-text intent could "
                "not be classified. Choose a strategy pack explicitly."
            )
            return None, None

        summaries = [
            {
                "pack_id": s.pack_id,
                "commander_names": list(s.commander_names),
                "summary": s.summary,
            }
            for s in self.pack_summaries()
            if s.supported
        ]
        result = self._call(
            StructuredRequest(
                prompt_version=INTENT_VERSION,
                system=INTENT_SYSTEM,
                user=intent_user(request.raw_intent, summaries),
                schema=IntentClassification,
                mode=ReasoningMode.CLASSIFY,
                call_type="cedh:intent",
            ),
            usages,
            warnings,
        )
        if result is None:
            return None, None
        intent = result
        if intent.pack_id and intent.pack_id in supported:
            return intent.pack_id, intent
        if intent.pack_id:
            warnings.append(
                f"Intent classification returned pack {intent.pack_id!r}, "
                "which is not a supported pack. Treating the request as "
                "unsupported rather than substituting another pack."
            )
        return None, intent

    def _summarise_evidence(
        self,
        evidence: EvidencePackage,
        usages: list[UsageRecord],
        warnings: list[str],
    ) -> EvidenceSummary | None:
        if self.gateway is None or not evidence.chunks:
            return None
        result = self._call(
            StructuredRequest(
                prompt_version=EVIDENCE_VERSION,
                system=EVIDENCE_SYSTEM,
                user=evidence_user(evidence.render()),
                schema=EvidenceSummary,
                mode=ReasoningMode.EXPLAIN,
                call_type="cedh:evidence_summary",
                evidence_hash=evidence.evidence_hash,
            ),
            usages,
            warnings,
        )
        if result is None:
            return None
        return self._check_summary(result, evidence, warnings)

    def _explain(
        self,
        pack: object,
        candidate: DeckCandidate,
        evidence: EvidencePackage,
        simulation: SimulationOutcome,
        usages: list[UsageRecord],
        warnings: list[str],
    ) -> DeckExplanation | None:
        if self.gateway is None:
            return None
        card_lines = [
            f"- {c.oracle_id} — {c.name} — {c.role}"
            for c in sorted(candidate.cards, key=lambda c: (c.role, c.name))
        ]
        win_lines = [
            f"{w.name} ({w.kind.value}): {w.converts_via}"
            + ("" if w.complete else " [INCOMPLETE in this list]")
            for w in candidate.win_packages
        ]
        result = self._call(
            StructuredRequest(
                prompt_version=EXPLAIN_VERSION,
                system=EXPLAIN_SYSTEM,
                user=explain_user(
                    commander=candidate.commander.display_name,
                    pack_summary=getattr(pack, "summary", ""),
                    card_lines=card_lines,
                    role_counts=candidate.role_counts,
                    win_packages=win_lines,
                    simulation_note=_simulation_note(simulation),
                    evidence_block=evidence.render(),
                ),
                schema=DeckExplanation,
                mode=ReasoningMode.EXPLAIN,
                call_type="cedh:explanation",
                evidence_hash=evidence.evidence_hash,
                max_output_tokens=1600,
            ),
            usages,
            warnings,
        )
        if result is None:
            return None
        return self._check_explanation(result, candidate, warnings)

    # -- cross-checks -----------------------------------------------------

    @staticmethod
    def _check_summary(
        summary: EvidenceSummary,
        evidence: EvidencePackage,
        warnings: list[str],
    ) -> EvidenceSummary:
        """Drop citations the package does not support."""
        known = {c.chunk_id for c in evidence.chunks}
        cited = [cid for cid in summary.chunk_ids if cid in known]
        invented = sorted(set(summary.chunk_ids) - known)
        if invented:
            warnings.append(
                "The evidence summary cited chunk ids that were not sent to "
                f"it ({', '.join(invented)}); those citations were dropped."
            )
        updates: dict[str, object] = {"chunk_ids": cited}
        if summary.events_cited > evidence.events_seen:
            warnings.append(
                f"The evidence summary claimed {summary.events_cited} events "
                f"but the package contained {evidence.events_seen}; the count "
                "was corrected downward."
            )
            updates["events_cited"] = evidence.events_seen
        if summary.decks_cited > evidence.decks_seen:
            warnings.append(
                f"The evidence summary claimed {summary.decks_cited} decks "
                f"but the package contained {evidence.decks_seen}; the count "
                "was corrected downward."
            )
            updates["decks_cited"] = evidence.decks_seen
        return summary.model_copy(update=updates)

    @staticmethod
    def _check_explanation(
        explanation: DeckExplanation,
        candidate: DeckCandidate,
        warnings: list[str],
    ) -> DeckExplanation:
        """Drop card references that are not in the built deck."""
        in_deck = {c.oracle_id for c in candidate.cards} | set(
            candidate.commander.oracle_ids
        )
        kept = [oid for oid in explanation.key_oracle_ids if oid in in_deck]
        invented = sorted(set(explanation.key_oracle_ids) - in_deck)
        if invented:
            warnings.append(
                "The explanation referenced cards that are not in this deck "
                f"({len(invented)} oracle_id(s)); those references were "
                "dropped. The prose may mention a card the list does not run."
            )
        return explanation.model_copy(update={"key_oracle_ids": kept})

    # -- gateway ----------------------------------------------------------

    def _call(
        self,
        request: StructuredRequest,
        usages: list[UsageRecord],
        warnings: list[str],
    ):
        """Run one model call, degrading to ``None`` on any failure.

        The deck is already built by the time any of these run, so a provider
        failure costs prose and nothing else. It is recorded as a warning
        rather than raised: a cEDH list with no narrative is a usable answer,
        and a 500 page is not.
        """
        assert self.gateway is not None
        try:
            result = self.gateway.generate(request)
        except LLMCostCeilingExceeded as exc:
            warnings.append(
                f"Monthly cost ceiling reached; {request.call_type} was not "
                f"run ({exc})."
            )
            return None
        except CedhError as exc:
            logger.warning("cEDH %s failed: %s", request.call_type, exc)
            warnings.append(
                f"The model could not produce {request.call_type} "
                f"({type(exc).__name__}). The deck below is unaffected — it is "
                "built deterministically."
            )
            return None
        usages.append(result.usage)
        return result.value


def _simulation_note(simulation: SimulationOutcome) -> str:
    """Render the simulation state for the model, honesty header included."""
    if isinstance(simulation, NotSimulated):
        return (
            f"NOT SIMULATED ({simulation.reason}): {simulation.detail}. "
            "Do not estimate, invent, or imply a simulation figure."
        )
    point = simulation.headline
    probability = f"{point.probability:.2%}" if point else "n/a"
    return (
        f"{simulation.metric}: P(assembled by turn "
        f"{simulation.objective_turn}) = {probability} over "
        f"{simulation.games:,} games. This measures {simulation.measures}. "
        f"It does not measure {simulation.does_not_measure} "
        f"The model could not see {simulation.unseen_card_count} of the 99."
    )


def build_lab(
    *,
    cards: CardRepository,
    meta: MetaRepository,
    simulator: SimulatorClient,
    gateway: ModelGateway | None = None,
    settings: CedhSettings | None = None,
) -> CedhDeckLab:
    """Assemble a lab from its parts, resolving the pack registry."""
    settings = settings or load_cedh_settings()
    return CedhDeckLab(
        cards=cards,
        meta=meta,
        registry=PackRegistry(cards),
        simulator=simulator,
        gateway=gateway,
        settings=settings,
    )


def default_constraints() -> BuildConstraints:
    """Proxy-unrestricted, default metagame window. The cEDH default."""
    settings = load_cedh_settings()
    return BuildConstraints(
        metagame=MetagameWindow(
            days=settings.meta.window_days,
            min_event_size=settings.meta.min_event_size,
        )
    )
