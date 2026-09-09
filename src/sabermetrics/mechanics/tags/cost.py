"""The ``cost:*`` family: how a card can be paid for, and what else it demands.

This is the family the plan's driving example needs — *"which cards can be cast
without paying mana, and which of those are legal in this identity"* — and it is
first for that reason.

Two distinctions run through the whole family and are worth stating once:

**A cost the card imposes on itself is not a cost it imposes on other spells.**
"You may pay {1} rather than pay this spell's mana cost" is an alternative cost;
"Spells you cast cost {1} less" is a cost *modifier*. They read alike and answer
different questions, so they are different tags and each names the other in its
limitations.

**An alternative cost is not a free cast.** Force of Will has an alternative
cost that happens to contain no mana; Bringer of the Black Dawn has one that
contains five. ``cost:alternative_cast_cost`` covers both, and
``cost:free_alternative_cost`` is the strictly narrower claim.
"""

from __future__ import annotations

from sabermetrics.mechanics.tags.definitions import TagDefinition
from sabermetrics.mechanics.tags.predicates import AnyOf, Cost, Keyword, Text

#: The standard wording, plus the graveyard-recursion phrasing that says "its"
#: but has already named the card it means. "rather than pay **its** mana cost"
#: on its own is excluded on purpose: on Bolas's Citadel and Valgavoth it
#: describes a cost the card imposes on *other* spells, which is a different
#: claim and gets a different tag.
_ALTERNATIVE_COST = AnyOf(
    Text(r"rather than pay this spell's mana cost"),
    Text(
        r"cast this (?:card|creature|spell|permanent)\b[^.]{0,120}"
        r"rather than paying its mana cost"
    ),
)

#: The same offer with no mana in it. Excluding ``{`` and ``}`` from the run
#: between "you may" and the clause is what separates Force of Will from
#: Bringer of the Black Dawn without enumerating either, in both word orders.
#: ``{0}`` is admitted explicitly: a Trap that offers "pay {0}" is free, and a
#: definition of free that excluded it would be about braces rather than mana.
_FREE_ALTERNATIVE_COST = AnyOf(
    Text(r"you may [^.{}]*rather than pay this spell's mana cost"),
    Text(r"rather than pay this spell's mana cost, you may [^.{}]*\."),
    Text(r"you may pay \{0\} rather than pay this spell's mana cost"),
)

# Mechanically adjacent negatives shared by the exact-keyword definitions.
# Each carries a shipped mana tag once both R1 families are loaded, but none has
# one of the named alternative-cost keywords.
_KEYWORD_NEGATIVES = (
    "Sol Ring",
    "Arcane Signet",
    "Fellwar Stone",
    "Mana Vault",
    "Grim Monolith",
    "Llanowar Elves",
    "Birds of Paradise",
    "Elvish Mystic",
    "Dark Ritual",
    "Cabal Ritual",
    "Dockside Extortionist",
    "Smothering Tithe",
    "Rampant Growth",
    "Nature's Lore",
    "Deserted Temple",
)


def _keyword_cost_tag(
    *,
    name: str,
    keyword: str,
    description: str,
    limitations: str,
    positives: tuple[str, ...],
) -> TagDefinition:
    """Build one exact-keyword cost tag with the shared negative pool."""
    return TagDefinition(
        id=f"cost:{name}",
        version="1.0",
        description=description,
        limitations=limitations,
        predicate=Keyword(keyword),
        positive_fixtures=positives,
        negative_fixtures=_KEYWORD_NEGATIVES,
        confidence=1.0,
    )


COST_TAGS: tuple[TagDefinition, ...] = (
    TagDefinition(
        id="cost:phyrexian_mana",
        version="1.0",
        description=(
            "The card's printed mana cost contains a Phyrexian symbol, so part "
            "of casting it may be paid with 2 life instead of mana."
        ),
        limitations=(
            "Reads the printed cast cost only. A card whose *activated ability* "
            "costs Phyrexian mana (Blinding Souleater, Hex Parasite) is not "
            "tagged, because it is not cast for that cost. Says nothing about "
            "how much life the substitution costs in total, nor whether paying "
            "it is ever correct."
        ),
        predicate=Cost(r"\{[^{}]*/P\}"),
        positive_fixtures=(
            "Gitaxian Probe",
            "Gut Shot",
            "Mental Misstep",
            "Mutagenic Growth",
            "Dismember",
            "Surgical Extraction",
            "Noxious Revival",
            "Apostle's Blessing",
            "Vault Skirge",
            "Porcelain Legionnaire",
            "Birthing Pod",
            "Phyrexian Metamorph",
            "K'rrik, Son of Yawgmoth",
            "Postmortem Lunge",
            "Act of Aggression",
        ),
        negative_fixtures=(
            "Blinding Souleater",
            "Hex Parasite",
            "Lashwrithe",
            "Skrelv, Defector Mite",
            "Kitchen Finks",
            "Boros Charm",
            "Force of Will",
            "Lightning Bolt",
            "Sol Ring",
            "Brainstorm",
        ),
        confidence=1.0,
    ),
    TagDefinition(
        id="cost:alternative_cast_cost",
        version="1.0",
        description=(
            "The card offers an alternative cost for casting itself, in place "
            "of its printed mana cost."
        ),
        limitations=(
            "Matches the standard wording only. Keyword alternative costs that "
            "never spell the clause out — evoke, dash, madness, flashback, "
            "foretell, escape, overload — carry their own tags instead, and a "
            "card with one of those is not tagged here. Cards that let *other* "
            "spells be cast for an alternative cost (Omniscience, Jodah) are "
            "cost modifiers, not this. Says nothing about whether the "
            "alternative is cheaper: several are strictly worse."
        ),
        predicate=_ALTERNATIVE_COST,
        positive_fixtures=(
            "Force of Will",
            "Force of Negation",
            "Force of Vigor",
            "Fireblast",
            "Daze",
            "Gush",
            "Foil",
            "Commandeer",
            "Bringer of the Black Dawn",
            "Blazing Shoal",
            "Invigorate",
            "Land Grant",
            "Allosaurus Rider",
            "Archive Trap",
            "Flare of Denial",
            "Snuff Out",
            "Squee, Dubious Monarch",
            "Glimpse the Cosmos",
        ),
        negative_fixtures=(
            "Omniscience",
            "Jodah, Archmage Eternal",
            "Bolas's Citadel",
            "Valgavoth, Terror Eater",
            "The Infamous Cruelclaw",
            "Lightning Bolt",
            "Sol Ring",
            "Brainstorm",
            "Grim Monolith",
            "Mana Crypt",
        ),
        confidence=1.0,
    ),
    TagDefinition(
        id="cost:free_alternative_cost",
        version="1.0",
        description=(
            "The card's alternative cost contains no mana at all, so it can be "
            "cast with an empty board and an untapped nothing."
        ),
        limitations=(
            "Strictly narrower than cost:alternative_cast_cost and detected the "
            "same way, so it inherits that tag's wording limitation. 'Free' "
            "means free of mana, not free of cost: Force of Will exiles a card "
            "and pays life, Fireblast sacrifices two lands. Cards that are free "
            "for a reason other than an alternative cost — cost reduction to "
            "zero, a zero printed cost, cascade — are not tagged here."
        ),
        predicate=_FREE_ALTERNATIVE_COST,
        positive_fixtures=(
            "Force of Will",
            "Force of Negation",
            "Force of Vigor",
            "Fireblast",
            "Daze",
            "Gush",
            "Foil",
            "Commandeer",
            "Invigorate",
            "Land Grant",
            "Flare of Denial",
            "Flare of Cultivation",
            "Blazing Shoal",
            "Snuff Out",
            "Abolish",
            "Allosaurus Rider",
            "Archive Trap",
            "Mindbreak Trap",
        ),
        negative_fixtures=(
            "Bringer of the Black Dawn",
            "Baleful Mastery",
            "Ingenious Mastery",
            "Fieldmist Borderpost",
            "Admiral's Order",
            "Omniscience",
            "Lightning Bolt",
            "Sol Ring",
            "Mana Crypt",
            "Brainstorm",
        ),
        confidence=1.0,
    ),
    TagDefinition(
        id="cost:additional_nonmana_cost",
        version="1.0",
        description=(
            "Casting the spell can require a named non-mana action or resource "
            "in addition to its ordinary mana payment."
        ),
        limitations=(
            "Matches explicit additional-cost wording only, and only when the "
            "clause names sacrifice, discard, exile, reveal, return, tap, life, "
            "or counters. Keyword costs such as kicker are separate mechanics. "
            "An optional additional cost still matches; this tag does not say "
            "that the option must be paid."
        ),
        predicate=Text(
            r"as an additional cost to cast this spell,[^.\n]{0,180}"
            r"(?:sacrifice|discard|exile|reveal|return|tap|pay \w+ life|"
            r"put [^.]* counter)"
        ),
        positive_fixtures=(
            "Altar's Reap",
            "Deadly Dispute",
            "Firestorm",
            "Neoform",
            "Shrapnel Blast",
            "Village Rites",
            "Hatred",
            "Scarscale Ritual",
        ),
        negative_fixtures=(
            "Force of Will",
            "Force of Negation",
            "Thalia, Guardian of Thraben",
            "Sphere of Resistance",
            "Sol Ring",
            "Dark Ritual",
            "Treasure Cruise",
            "Mulldrifter",
            "Faithless Looting",
            "Lightning Bolt",
            "Brainstorm",
            "Arcane Signet",
        ),
        confidence=1.0,
    ),
    TagDefinition(
        id="cost:cost_reduction",
        version="1.0",
        description=(
            "The card's rules text reduces mana required to cast itself or a "
            "declared class of spells."
        ),
        limitations=(
            "Matches explicit 'costs ... less to cast' wording. It does not "
            "infer reductions from convoke, delve, improvise, affinity, or an "
            "alternative cost. It also does not calculate the resulting amount "
            "or whether the reduction's condition can be met."
        ),
        predicate=AnyOf(
            Text(r"\bthis spell costs [^.\n]{0,100} less to cast"),
            Text(r"\bspells you cast cost [^.\n]{0,100} less to cast"),
            Text(r"\b[A-Za-z -]+ spells you cast cost [^.\n]{0,100} less to cast"),
        ),
        positive_fixtures=(
            "Etherium Sculptor",
            "Goblin Electromancer",
            "Starnheim Aspirant",
            "Avatar of Fury",
            "Of One Mind",
            "Thunderclap Drake",
            "The Pride of Hull Clade",
            "Draco",
        ),
        negative_fixtures=(
            "Force of Will",
            "Chord of Calling",
            "Treasure Cruise",
            "Whir of Invention",
            "Dark Ritual",
            "Sol Ring",
            "Lightning Bolt",
            "Brainstorm",
            "Arcane Signet",
            "Mana Vault",
            "Dockside Extortionist",
            "Rampant Growth",
        ),
        confidence=1.0,
    ),
    _keyword_cost_tag(
        name="affinity",
        keyword="Affinity",
        description=(
            "The spell has affinity, reducing its generic casting cost for each "
            "permanent of the quality named by that ability."
        ),
        limitations=(
            "Uses the published keyword array and does not parse which quality "
            "the affinity ability counts. It says nothing about the number of "
            "qualifying permanents or the spell's final payable cost."
        ),
        positives=(
            "Frogmite",
            "Thoughtcast",
            "Myr Enforcer",
            "Sojourner's Companion",
            "Thought Monitor",
        ),
    ),
    _keyword_cost_tag(
        name="convoke",
        keyword="Convoke",
        description=(
            "The spell has convoke, allowing untapped creatures to help pay its "
            "casting cost."
        ),
        limitations=(
            "Uses the published keyword array. It does not establish that a deck "
            "has creatures available to tap, distinguish competing tap uses, or "
            "calculate how much mana convoke will save in a game state."
        ),
        positives=(
            "Chord of Calling",
            "Sprout Swarm",
            "Stoke the Flames",
            "March of the Multitudes",
            "Nissa's Expedition",
        ),
    ),
    _keyword_cost_tag(
        name="delve",
        keyword="Delve",
        description=(
            "The spell has delve, allowing cards exiled from its controller's "
            "graveyard to pay generic casting cost."
        ),
        limitations=(
            "Uses the published keyword array. It does not count available "
            "graveyard cards, value those cards as resources, or calculate the "
            "spell's effective cost in a particular turn."
        ),
        positives=(
            "Dig Through Time",
            "Treasure Cruise",
            "Tasigur, the Golden Fang",
            "Gurmag Angler",
            "Murderous Cut",
        ),
    ),
    _keyword_cost_tag(
        name="improvise",
        keyword="Improvise",
        description=(
            "The spell has improvise, allowing untapped artifacts to help pay "
            "its generic casting cost."
        ),
        limitations=(
            "Uses the published keyword array. It does not count artifacts, "
            "distinguish artifacts needed for other activations, or calculate "
            "the resulting cost for any board state."
        ),
        positives=(
            "Whir of Invention",
            "Metallic Rebuke",
            "Herald of Anguish",
            "Reverse Engineer",
            "Battle at the Bridge",
        ),
    ),
    _keyword_cost_tag(
        name="emerge",
        keyword="Emerge",
        description=(
            "The creature spell has emerge, an alternative cost reduced by the "
            "mana value of a creature sacrificed while casting it."
        ),
        limitations=(
            "Uses the published keyword array. It does not identify a sacrifice "
            "candidate, calculate the resulting payment, or claim sacrificing "
            "that creature is strategically favorable."
        ),
        positives=(
            "Elder Deep-Fiend",
            "Distended Mindbender",
            "Wretched Gryff",
            "Lashweed Lurker",
            "Decimator of the Provinces",
        ),
    ),
    _keyword_cost_tag(
        name="evoke",
        keyword="Evoke",
        description=(
            "The permanent spell has evoke, an alternative casting cost that "
            "causes it to be sacrificed after it enters."
        ),
        limitations=(
            "Uses the published keyword array and does not parse the printed "
            "evoke payment. It identifies the casting option, not whether the "
            "entering or leaving triggers make that option useful."
        ),
        positives=("Mulldrifter", "Shriekmaw", "Solitude", "Endurance", "Grief"),
    ),
    _keyword_cost_tag(
        name="dash",
        keyword="Dash",
        description=(
            "The creature spell has dash, an alternative cost that grants haste "
            "and returns it to its owner's hand at the next end step."
        ),
        limitations=(
            "Uses the published keyword array and does not parse the dash "
            "payment. It does not compare that payment with the printed cost or "
            "determine whether returning the creature is beneficial."
        ),
        positives=(
            "Ragavan, Nimble Pilferer",
            "Zurgo Bellstriker",
            "Kolaghan, the Storm's Fury",
            "Mardu Scout",
            "Lightning Berserker",
        ),
    ),
    _keyword_cost_tag(
        name="madness",
        keyword="Madness",
        description=(
            "The card has madness, allowing it to be cast for its madness cost "
            "when it is discarded into exile."
        ),
        limitations=(
            "Uses the published keyword array and does not parse the madness "
            "payment. It does not establish that a discard outlet is available "
            "or that casting the card is better than leaving it discarded."
        ),
        positives=(
            "Anje's Ravager",
            "Big Game Hunter",
            "Fiery Temper",
            "Basking Rootwalla",
            "Alms of the Vein",
        ),
    ),
    _keyword_cost_tag(
        name="flashback",
        keyword="Flashback",
        description=(
            "The instant or sorcery has flashback, allowing it to be cast from "
            "the graveyard for the printed flashback cost."
        ),
        limitations=(
            "Uses the published keyword array and does not parse or compare the "
            "flashback payment. Cards that grant flashback to other cards may "
            "also publish the keyword, so this tag does not prove the tagged "
            "card itself is castable from the graveyard."
        ),
        positives=(
            "Faithless Looting",
            "Dread Return",
            "Deep Analysis",
            "Sevinne's Reclamation",
            "Firebolt",
        ),
    ),
    _keyword_cost_tag(
        name="escape",
        keyword="Escape",
        description=(
            "The card has escape, allowing it to be cast from the graveyard by "
            "paying its escape cost and exiling other cards."
        ),
        limitations=(
            "Uses the published keyword array and does not parse the mana or "
            "exile payment. It does not count graveyard resources or assess "
            "competition with other cards that consume the graveyard."
        ),
        positives=(
            "Phoenix of Ash",
            "Uro, Titan of Nature's Wrath",
            "Kroxa, Titan of Death's Hunger",
            "Ox of Agonas",
            "Cling to Dust",
        ),
    ),
    _keyword_cost_tag(
        name="foretell",
        keyword="Foretell",
        description=(
            "The card has foretell, allowing it to be exiled face down and cast "
            "on a later turn for its foretell cost."
        ),
        limitations=(
            "Uses the published keyword array and does not parse either payment. "
            "It does not account for the setup turn, hidden-information value, "
            "or effects that change the generic foretell setup cost."
        ),
        positives=(
            "Saw It Coming",
            "Doomskar",
            "Behold the Multiverse",
            "Alrund's Epiphany",
            "Mystic Reflection",
        ),
    ),
    _keyword_cost_tag(
        name="morph",
        keyword="Morph",
        description=(
            "The permanent has morph, allowing it to be cast face down as a "
            "colorless 2/2 creature for three generic mana."
        ),
        limitations=(
            "Uses the published keyword array. Megamorph and disguise are "
            "distinct keywords and are not folded into this tag. It does not "
            "parse the turn-face-up cost or value hidden information."
        ),
        positives=(
            "Willbender",
            "Vesuvan Shapeshifter",
            "Exalted Angel",
            "Akroma, Angel of Fury",
            "Bane of the Living",
        ),
    ),
)
