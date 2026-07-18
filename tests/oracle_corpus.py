"""Shared card corpus for oracle-pattern classification snapshots.

Used by the oracle-pattern unification snapshot test (and the before/after
review harness). Spans every functional category that components.py and
role_tagger.py classify, plus negative/edge cases. Not a test module itself
(no ``test_`` prefix), so pytest does not collect it.
"""

from __future__ import annotations

# Each entry: (key, card_dict). Card fields are the ones the classifiers read.
CORPUS: list[tuple[str, dict]] = [
    # --- ramp ---
    ("sol_ring", {"name": "Sol Ring", "type_line": "Artifact", "cmc": 1.0,
                  "oracle_text": "{T}: Add {C}{C}."}),
    ("llanowar_elves", {"name": "Llanowar Elves", "type_line": "Creature — Elf Druid",
                        "cmc": 1.0, "oracle_text": "{T}: Add {G}."}),
    ("arcane_signet", {"name": "Arcane Signet", "type_line": "Artifact", "cmc": 2.0,
                       "oracle_text": "{T}: Add one mana of any color in your commander's color identity."}),
    ("rampant_growth", {"name": "Rampant Growth", "type_line": "Sorcery", "cmc": 2.0,
                        "oracle_text": "Search your library for a basic land card, put it onto the battlefield tapped, then shuffle."}),
    ("cultivate", {"name": "Cultivate", "type_line": "Sorcery", "cmc": 3.0,
                   "oracle_text": "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle."}),
    ("dark_ritual", {"name": "Dark Ritual", "type_line": "Instant", "cmc": 1.0,
                     "oracle_text": "Add {B}{B}{B}."}),
    ("smothering_tithe", {"name": "Smothering Tithe", "type_line": "Enchantment", "cmc": 4.0,
                          "oracle_text": "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token."}),
    ("worn_powerstone", {"name": "Worn Powerstone", "type_line": "Artifact", "cmc": 3.0,
                         "oracle_text": "Worn Powerstone enters the battlefield tapped. {T}: Add {C}{C}."}),
    # --- fixing ---
    ("chromatic_lantern", {"name": "Chromatic Lantern", "type_line": "Artifact", "cmc": 3.0,
                           "oracle_text": "Lands you control have \"{T}: Add one mana of any color.\" {T}: Add one mana of any color."}),
    ("fellwar_stone", {"name": "Fellwar Stone", "type_line": "Artifact", "cmc": 2.0,
                       "oracle_text": "{T}: Add one mana of any color that a land an opponent controls could produce."}),
    # --- draw ---
    ("divination", {"name": "Divination", "type_line": "Sorcery", "cmc": 3.0,
                    "oracle_text": "Draw two cards."}),
    ("sign_in_blood", {"name": "Sign in Blood", "type_line": "Sorcery", "cmc": 2.0,
                       "oracle_text": "Target player draws two cards and loses 2 life."}),
    ("brainstorm", {"name": "Brainstorm", "type_line": "Instant", "cmc": 1.0,
                    "oracle_text": "Draw three cards, then put two cards from your hand on top of your library in any order."}),
    ("phyrexian_arena", {"name": "Phyrexian Arena", "type_line": "Enchantment", "cmc": 3.0,
                         "oracle_text": "At the beginning of your upkeep, you draw a card and you lose 1 life."}),
    ("senseis_top", {"name": "Sensei's Divining Top", "type_line": "Artifact", "cmc": 1.0,
                     "oracle_text": "{T}: Look at the top three cards of your library, then put them back in any order. {1}, {T}: Draw a card, then put Sensei's Divining Top on top of its owner's library."}),
    # --- removal ---
    ("swords", {"name": "Swords to Plowshares", "type_line": "Instant", "cmc": 1.0,
                "oracle_text": "Exile target creature. Its controller gains life equal to its power."}),
    ("murder", {"name": "Murder", "type_line": "Instant", "cmc": 3.0,
                "oracle_text": "Destroy target creature."}),
    ("lightning_bolt", {"name": "Lightning Bolt", "type_line": "Instant", "cmc": 1.0,
                        "oracle_text": "Lightning Bolt deals 3 damage to any target."}),
    ("counterspell", {"name": "Counterspell", "type_line": "Instant", "cmc": 2.0,
                      "oracle_text": "Counter target spell."}),
    ("pongify", {"name": "Pongify", "type_line": "Instant", "cmc": 1.0,
                 "oracle_text": "Destroy target creature. It can't be regenerated. Its controller creates a 3/3 green Ape creature token."}),
    ("cyclonic_rift", {"name": "Cyclonic Rift", "type_line": "Instant", "cmc": 2.0,
                       "oracle_text": "Return target nonland permanent you don't control to its owner's hand. Overload {6}{U}."}),
    # --- board wipe ---
    ("wrath_of_god", {"name": "Wrath of God", "type_line": "Sorcery", "cmc": 4.0,
                      "oracle_text": "Destroy all creatures. They can't be regenerated."}),
    ("toxic_deluge", {"name": "Toxic Deluge", "type_line": "Sorcery", "cmc": 3.0,
                      "oracle_text": "As an additional cost to cast this spell, pay X life. All creatures get -X/-X until end of turn."}),
    ("evacuation", {"name": "Evacuation", "type_line": "Instant", "cmc": 5.0,
                    "oracle_text": "Return all creatures to their owners' hands."}),
    # --- tutor ---
    ("demonic_tutor", {"name": "Demonic Tutor", "type_line": "Sorcery", "cmc": 2.0,
                       "oracle_text": "Search your library for a card, put that card into your hand, then shuffle."}),
    ("worldly_tutor", {"name": "Worldly Tutor", "type_line": "Instant", "cmc": 1.0,
                       "oracle_text": "Search your library for a creature card, reveal it, then shuffle and put that card on top."}),
    # --- recursion ---
    ("regrowth", {"name": "Regrowth", "type_line": "Sorcery", "cmc": 2.0,
                  "oracle_text": "Return target card from your graveyard to your hand."}),
    ("animate_dead", {"name": "Animate Dead", "type_line": "Enchantment — Aura", "cmc": 2.0,
                      "oracle_text": "Return target creature card from your graveyard to the battlefield under your control."}),
    # --- protection ---
    ("heroic_intervention", {"name": "Heroic Intervention", "type_line": "Instant", "cmc": 2.0,
                             "oracle_text": "Permanents you control gain hexproof and indestructible until end of turn."}),
    ("swiftfoot_boots", {"name": "Swiftfoot Boots", "type_line": "Artifact — Equipment", "cmc": 2.0,
                         "oracle_text": "Equipped creature has hexproof and haste. Equip {1}."}),
    # --- wincon ---
    ("expropriate", {"name": "Expropriate", "type_line": "Sorcery", "cmc": 9.0,
                     "oracle_text": "Council's dilemma — Take an extra turn after this one for each time players chose time."}),
    ("laboratory_maniac", {"name": "Laboratory Maniac", "type_line": "Creature — Human Wizard", "cmc": 2.0,
                           "oracle_text": "If you would draw a card while your library has no cards in it, you win the game instead."}),
    # --- threat ---
    ("combat_beater", {"name": "Generic Beater", "type_line": "Creature — Beast", "cmc": 5.0,
                       "oracle_text": "Whenever this creature deals combat damage to a player, draw a card."}),
    # --- land / utility / negative ---
    ("command_tower", {"name": "Command Tower", "type_line": "Land", "cmc": 0.0,
                       "oracle_text": "{T}: Add one mana of any color in your commander's color identity."}),
    ("reliquary_tower", {"name": "Reliquary Tower", "type_line": "Land", "cmc": 0.0,
                         "oracle_text": "You have no maximum hand size. {T}: Add {C}."}),
    ("vanilla_bear", {"name": "Grizzly Bears", "type_line": "Creature — Bear", "cmc": 2.0,
                      "oracle_text": ""}),
    ("opp_treasure", {"name": "Pirate Captain", "type_line": "Creature — Pirate", "cmc": 3.0,
                      "oracle_text": "When this creature enters, each opponent creates a Treasure token."}),
]
