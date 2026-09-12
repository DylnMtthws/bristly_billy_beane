# DYL-53 — Export menu with copy and Mana Pool handoff

Problem: the builder header has separate download Export and Share buttons. Share creates a link; the requested actions are decklist copy and mass-entry handoff.

Acceptance:
- Replace the builder Share button and redundant separate Export link with one Export disclosure containing exactly Copy list and Buy deck. Keep readable accessible labels and touch targets. Available on desktop and mobile, in Decklist and Playmat, without header overflow at 390/430px. Preserve existing share API and library sharing; no new share token is created by export.
- Export current successfully saved document state, including current commanders/partners and all main-deck/custom main zones; exclude sideboard/maybeboard. Aggregate identical names across printings/zones. Plain `quantity name` lines, commanders first then sorted remaining cards, no private title, IDs, tags, comments, zone headings or other metadata. Unicode/apostrophes/split-card names must survive. No legal-card filtering of actual deck contents.
- Prevent stale export while pending/failed saves; disable actions with concise status until saves complete/retry. Changes already saved after opening menu must appear in export.
- Copy list writes text to clipboard directly from user activation; show concise accessible success/error. Handle clipboard denial/unavailability (including Safari) with selectable read-only text fallback, never claim success on failure. Dismissing fallback does not alter deck.
- Buy deck opens https://manapool.com/add-deck?deck=<encoded-base64-UTF8> in a new tab with noopener/noreferrer and no referrer. Prepare href synchronously from saved state; no async popup opening. No auto-submit, cart, affiliate parameters, API credentials or checkout. No external requests before explicit click. Empty list disables actions. Do not send metadata. Preserve deck tab.
- Mark export details with data-dismiss-menu for DYL-54. Keyboard activation, Escape and outside dismissal work. Read-only shared deck controls retain existing restrictions.
- Focused tests: quantities, partners, duplicates, Unicode, exclusions, pending/error/empty states, clipboard failure fallback, exact URL payload and no share mutation. Reuse existing JS harness patterns.

Implementation: UI-only. deck-lab-builder.js holds authoritative state and pendingSaves/failedSave; use it instead of stale initial JSON or additional endpoint. A small helper JS module is acceptable. Existing download route remains intact for library consumers.
