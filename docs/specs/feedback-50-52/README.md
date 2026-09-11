# Feedback 50–52: QA-only batch
Baseline: production/main 095c9a030b5f5e635ebd811db93e565dfcfc8df4. Owner requests individual specs, Cursor implementation, coordinator review and QA deployment. No production deployment in this batch.

Two source-only Cursor tasks: Search owns DYL-50 and DYL-52, including search_cards, lightweight owned-deck color lookup, Research search and search-related JS only. Validation owns DYL-51: validate/get entry annotations, builder count/row/card rendering, scoped validation CSS. Both may edit deck_documents.py and builder JS in distinct sections; coordinator merges the changes. Do not rewrite unrelated sections.

Preserve authorization, public/private boundaries, saved decks, undo/redo, imported cards and existing layout. No external calls in search. Do not hardcode a ban list. Use imported affirmative legality. No credentials, user data or original issue metadata are included.

Rules reference: https://magic.wizards.com/en/formats/commander ; https://magic.wizards.com/en/news/feature/introduction-commander-2016-10-28 . Color identity is a subset of the combined commander identities, not equality: colorless and single-color cards remain valid within a multicolor deck.
