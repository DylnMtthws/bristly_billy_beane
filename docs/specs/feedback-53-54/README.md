# DYL-53–54: Export and menu dismissal

Baseline: production/main 7345357db1e16a18f5dc3ae8dca129543b07ed85. Owner requests Cursor implementation, coordinator review, and QA only.

Session 1 owns DYL-53: builder header template, new export JS/CSS if useful, export functions and old share handler in deck-lab-builder.js, tests/test_deck_export_menu.py. Session 2 owns DYL-54: shared transient-menu dismissal in deck-lab-shell.js and necessary builder menu/search hooks, tests/test_menu_dismissal.py. Avoid changing each other's sections. Export details must use data-dismiss-menu so the second session's delegated handler includes it. No production data/credentials, backend/schema changes, dependency installs, deployments, git commits, or external browsing. Keep existing authorizations and persistence unchanged.

Coordinator verified Mana Pool's public mass entry page with synthetic input on 2026-09-11: GET https://manapool.com/add-deck?deck=MSBTb2wgUmluZwoyIElzbGFuZA%3D%3D prefilled textarea#decklist with `1 Sol Ring` and `2 Island`. Contract is URL-encoded standard base64 of UTF-8 plain quantity/name lines in deck parameter. No API key or purchase action. Reference https://manapool.com/mass-entry-info .
