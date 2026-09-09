"""The ``mana:*`` family: sources, bursts, fixing, and land development.

These tags stay atomic. In particular, ``mana:mana_rock`` does not claim that a
rock is net-positive: that is arithmetic between its printed mana value and the
amount matched by ``mana:adds_two_or_more`` and belongs in the query layer.
Likewise, a land tutor is separated by destination because a land in hand and a
land on the battlefield answer different deck-building questions.
"""

from __future__ import annotations

from sabermetrics.mechanics.tags.definitions import TagDefinition
from sabermetrics.mechanics.tags.predicates import (
    AllOf,
    AnyOf,
    Not,
    Text,
    TypeLine,
)

_ADDS_MANA = Text(
    r"\badd (?:\{[^}\n]+\})+|\badd (?:one|two|three|four|five|six|x) mana"
)

# Cost-mechanic cards make adjacent negatives for mana predicates and keep the
# fixture pool from becoming a stack of vanilla cards.
_MANA_NEGATIVES = (
    "Force of Will",
    "Gitaxian Probe",
    "Gut Shot",
    "Mental Misstep",
    "Mulldrifter",
    "Treasure Cruise",
    "Chord of Calling",
    "Faithless Looting",
    "Underworld Breach",
    "Exalted Angel",
    "Fireblast",
    "Daze",
    "Snuff Out",
    "Dig Through Time",
    "Thoughtcast",
)

MANA_TAGS: tuple[TagDefinition, ...] = (
    TagDefinition(
        id="mana:produces_mana",
        version="1.0",
        description=(
            "The card's own non-reminder rules text contains an instruction to "
            "add mana."
        ),
        limitations=(
            "This is deliberately broad: lands, activated sources, triggered "
            "sources, rituals, and granted abilities can all match. It does not "
            "say when the mana is available, whether an activation has a cost, "
            "or whether the card produces more mana than it consumes."
        ),
        predicate=_ADDS_MANA,
        positive_fixtures=(
            "Sol Ring",
            "Arcane Signet",
            "Llanowar Elves",
            "Birds of Paradise",
            "Dark Ritual",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:mana_rock",
        version="1.0",
        description=(
            "The card is a noncreature artifact whose own rules text produces " "mana."
        ),
        limitations=(
            "Artifact creatures are excluded even when they tap for mana. The "
            "tag does not distinguish immediate from delayed access, net-positive "
            "from net-neutral rocks, restricted mana, or an activation that "
            "requires another resource; those are query-layer compositions."
        ),
        predicate=AllOf(
            _ADDS_MANA,
            TypeLine(r"\bArtifact\b"),
            Not(TypeLine(r"\bCreature\b")),
        ),
        positive_fixtures=(
            "Sol Ring",
            "Arcane Signet",
            "Fellwar Stone",
            "Mana Vault",
            "Grim Monolith",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:mana_dork",
        version="1.0",
        description=(
            "The card is a creature with a printed tap ability that adds mana."
        ),
        limitations=(
            "Matches the conventional '{T}: Add' shape only. Creatures that make "
            "mana from attacks, death triggers, counters, or a non-tap activated "
            "ability are real mana creatures but are outside this narrower tag. "
            "It does not account for summoning sickness."
        ),
        predicate=AllOf(
            Text(r"\{T\}[^:\n]{0,40}:\s*Add " r"(?:(?:\{[^}\n]+\})+|(?:one )?mana)"),
            TypeLine(r"\bCreature\b"),
        ),
        positive_fixtures=(
            "Llanowar Elves",
            "Elvish Mystic",
            "Fyndhorn Elves",
            "Birds of Paradise",
            "Devoted Druid",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:ritual",
        version="1.0",
        description=(
            "The card is an instant or sorcery whose resolution directly adds "
            "mana as a one-shot effect."
        ),
        limitations=(
            "This is a text-and-type classification, not a profitability test. "
            "A spell that replaces exactly the mana spent can match, while a "
            "permanent with a ritual-like enter trigger does not. Delayed mana "
            "and Treasure creation are represented separately."
        ),
        predicate=AllOf(
            _ADDS_MANA,
            TypeLine(r"\b(?:Instant|Sorcery)\b"),
        ),
        positive_fixtures=(
            "Dark Ritual",
            "Cabal Ritual",
            "Pyretic Ritual",
            "Seething Song",
            "Culling the Weak",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:treasure_producer",
        version="1.0",
        description=(
            "The card's own non-reminder rules text creates one or more Treasure "
            "tokens."
        ),
        limitations=(
            "Matches creation, not cards that merely sacrifice, count, copy, or "
            "reward Treasures. It does not establish when the trigger occurs, "
            "whether the token enters tapped, or how many tokens a conditional "
            "or variable instruction will create."
        ),
        predicate=Text(r"\bcreate [^.\n]{0,100}\bTreasure tokens?\b"),
        positive_fixtures=(
            "Dockside Extortionist",
            "Ragavan, Nimble Pilferer",
            "Professional Face-Breaker",
            "Smothering Tithe",
            "Spell Swindle",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:land_to_battlefield",
        version="1.0",
        description=(
            "The card searches its controller's library for a land and puts that "
            "card onto the battlefield."
        ),
        limitations=(
            "Matches explicit library-search wording and does not cover lands "
            "played from the top, returned from a graveyard, or put from hand "
            "onto the battlefield. It does not distinguish tapped from untapped "
            "arrival or count the activation cost."
        ),
        predicate=Text(
            r"search your library for [^.\n]{0,140}"
            r"\b(?:land|Plains|Island|Swamp|Mountain|Forest) cards?\b"
            r"[^.\n]{0,180}\bput (?:it|that card|those cards|them|one) "
            r"onto the battlefield"
        ),
        positive_fixtures=(
            "Rampant Growth",
            "Nature's Lore",
            "Three Visits",
            "Farseek",
            "Sakura-Tribe Elder",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:land_to_hand",
        version="1.0",
        description=(
            "The card searches its controller's library for a land and puts or "
            "reveals that card into their hand."
        ),
        limitations=(
            "Matches explicit library-search wording only. It does not include "
            "lands drawn or returned from another zone, and it does not imply "
            "mana acceleration because the found land still has to be played."
        ),
        predicate=Text(
            r"search your library for [^.\n]{0,140}"
            r"\b(?:land|Plains|Island|Swamp|Mountain|Forest) cards?\b"
            r"[^.\n]{0,180}\b(?:put (?:it|that card|those cards|them|one)|"
            r"the rest) into your hand"
        ),
        positive_fixtures=(
            "Sylvan Scrying",
            "Expedition Map",
            "Lay of the Land",
            "Attune with Aether",
            "Nissa's Pilgrimage",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:any_color",
        version="1.0",
        description=(
            "The card's rules text can add one mana of any color or a chosen "
            "combination of colors."
        ),
        limitations=(
            "Matches the broad printed phrase and does not inspect restrictions "
            "such as commander identity, a chosen color, opponents' lands, or "
            "what the mana may be spent on. Colorless mana is not itself a "
            "color and does not satisfy this tag."
        ),
        predicate=AnyOf(
            Text(r"\badd (?:one )?mana of any color\b"),
            Text(r"\badd [^. \n]* mana in any combination of colors\b"),
        ),
        positive_fixtures=(
            "Birds of Paradise",
            "Arcane Signet",
            "Mana Confluence",
            "City of Brass",
            "Paradise Mantle",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:adds_two_or_more",
        version="1.0",
        description=(
            "One printed mana-production instruction on the card can add at "
            "least two mana."
        ),
        limitations=(
            "This is an output-shape tag, not a net-mana calculation. It does "
            "not subtract casting or activation costs, prove that a variable "
            "amount is at least two in a game state, or combine repeated "
            "single-mana triggers."
        ),
        predicate=AnyOf(
            Text(r"\badd (?:\{[^}\n]+\}){2,}"),
            Text(r"\badd (?:two|three|four|five|six|x) mana\b"),
        ),
        positive_fixtures=(
            "Sol Ring",
            "Mana Vault",
            "Grim Monolith",
            "Dark Ritual",
            "Seething Song",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:untaps_land",
        version="1.0",
        description=(
            "The card's rules text can untap one or more lands, making their "
            "mana abilities available again."
        ),
        limitations=(
            "Matches explicit land or Forest targets. It does not infer that an "
            "untapped generic permanent will be a land, account for activation "
            "costs, or claim the effect is net-positive after those costs."
        ),
        predicate=Text(
            r"\buntap (?:up to )?(?:(?:one|two|three) )?"
            r"(?:target )?(?:land|lands|Forest|two target lands|"
            r"up to three target lands)\b"
        ),
        positive_fixtures=(
            "Arbor Elf",
            "Voyaging Satyr",
            "Deserted Temple",
            "Krosan Restorer",
            "Ley Druid",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
    TagDefinition(
        id="mana:mana_filter",
        version="1.0",
        description=(
            "The card has a mana ability that requires a mana payment before it "
            "adds its output mana."
        ),
        limitations=(
            "Matches the conventional printed payment-before-'Add' shape. It "
            "does not compare input and output colors, subtract non-mana costs, "
            "or include replacement effects that change how another source "
            "produces mana."
        ),
        predicate=Text(
            r"\{(?:\d+|[WUBRGC])\}(?:, \{T\})?[^:\n]{0,20}:\s*" r"Add (?:\{[^}\n]+\})+"
        ),
        positive_fixtures=(
            "Boros Signet",
            "Azorius Signet",
            "Dimir Signet",
            "Rakdos Signet",
            "Manaforge Cinder",
        ),
        negative_fixtures=_MANA_NEGATIVES,
        confidence=1.0,
    ),
)
