# Partner commander support

Research uses the complete commander set recorded for each submitted tournament deck. Rograkh + Silas and Rograkh + Tymna have independent entries, finishes, trends, color identities and card inclusion denominators. A tournament entry contributes once to its cohort and once to the field denominator. Recorded solo decks remain separate; a member of a recorded pair does not acquire the pair's results as a solo profile.

The additive migration reconstructs the commander sets from existing namespaced source rows and adds stable IDs based on sorted Oracle IDs. The six-hour corpus refresh updates these identities in the same snapshot transaction. Legacy per-card source records, card IDs, user documents and existing favorites remain intact. Pair favorites have their own foreign-key-backed table. Migration resumes if an earlier attempt left unassigned source rows.

Starting a build from a pair creates both commanders. New-deck and editor dialogs offer compatible partner selection, using Oracle text rather than the current commander's colors to find a partner. Commander replacement uses the existing owner, revision and mutation checks and updates both cards atomically. Library color restrictions use both commanders, and two commanders require 98 library cards (100 total).

Compatibility follows [Comprehensive Rules 702.124](https://media.wizards.com/2026/downloads/MagicCompRules%2020260819.txt): ordinary Partner, matching named variants, reciprocal Partner with, Choose a Background and Doctor's companion are distinct. Original Friends forever wording is accepted alongside its updated named variant. A solo Partner card is permitted by the rules and can remain an unfinished draft; the app does not invent a tournament pairing for it.

Validation includes exact-cohort and migration regressions, partner variant restrictions, owner isolation, atomic replacement, 98-card validation, pair favorites, Research-to-build routes, installed-image smoke checks, and desktop/mobile browser acceptance. Production-corpus rehearsal recovered 88 pairings without changing source row counts; unfiltered Research took approximately 0.4 seconds locally.
