# Remaining Linear issues: QA batch

Owner requested all remaining issues read, specified, implemented and deployed
for QA review. Base production: 4a66f56ac1d5d6b72c385a0efa171d2c0589fbd6.
Production promotion is not authorized for this new batch. Automatic agent
intake and publication remain off. Do not publish source or contact users.

## DYL-19: card discovery and Commander legality

Research Cards must support Oracle text; separate supertype, type and subtype
with IS/NOT operators; colors Include/Exclude/Exactly; mana value, power and
toughness accessible range controls with labeled bounds 0 through 9 and 10+;
and common/uncommon/rare/mythic rarity. Preserve URL state, progressive fragments,
history, pagination and no-JS GET fallback. A full-range slider means unfiltered;
10+ is an open upper bucket, not exactly ten. Nonnumeric power/toughness must not
silently become zero. Use actual imported card attributes, no invented values.
Audit existing schema/ingestion; add backward-compatible fields only if needed.
Do not download a corpus or contact new services; report existing-data coverage.

Only cards with affirmative existing Commander-format legality may appear in
Research searches, commander choices, builder search or be newly added/imported
into a deck. Distinguish format legality from eligibility to be a commander.
Enforce server-side, including direct IDs and imports. Preserve existing stored
decks and allow owners to inspect/remove old illegal cards; never delete data.
Avoid failing previously valid legal fixture tests by using appropriate fixtures.

## DYL-20: distinct Commanders and Meta

Commanders is an alphabetical catalog of all legal commander-eligible cards,
including cards without tournament entries. Provide images and links to detail,
search, relevant filters and pagination. Meta remains the observed tournament
archetype/commander-pair ranking with real statistics and existing prepared
snapshot speed. Do not turn missing tournament evidence into zero. Default
Commanders must stay fast without running Meta aggregation just to list cards.

## DYL-21: discover explicitly public user decks

Add a fourth Research tab, Decks, with paginated searchable public deck cards,
commander images, title and display name (never email). Add clear private/public
controls in library deck options and builder deck options, owner-only and CSRF
protected. All existing/new/duplicated/imported decks default private. Existing
unlisted share links do not opt decks into discovery. Public means readable by
other signed-in app users through Research; no anonymous indexing is introduced.
Use a read-only public detail page, never another user's editable builder.

Dedicated public projections must exclude owner IDs, emails, notes, mutation/
undo history, tokens and other private metadata. Query current visibility on
every view; unpublishing or deleting immediately removes listing and access,
including guessed IDs and stale page links. Public views may not invoke editing
APIs or expose writable credentials. Existing owner access/share revocation
continues to work. Additive migration only, no new external services.

## DYL-25: representative recorded decklist

Replace the long inclusion-only list on commander detail with one real recorded
deck for the selected commander/pair/window, chosen deterministically by closest
overlap to the cohort's common cards, with a stable tie-break. Prefer complete
records, but label partial lists honestly if the corpus only has partial lists.
Do not fabricate a 100-card playable deck from frequency rankings. Show provenance
as a recorded example/representative sample (not a guaranteed optimal list),
counts, images grouped by primary type and quantities; show commander(s) apart
from the main deck. Keep missing-image/name fallbacks, lazy loading and relevant
detail links. The removed Add top 40 button must remain absent. Empty corpus:
helpful no-recorded-list state, not invented cards. Keep query cost bounded.

## DYL-23, DYL-28, DYL-31: mobile usability

Keep removed Tournament evidence paragraph absent. On Research use readable
body/filter/result text (16px body/input, at least 14px supporting labels), clear
contrast, visible focus and practical 44px touch controls. This is a scoped
readability improvement, not a claim of complete WCAG conformance.
Commander Start a build must fit its mobile grid without clipping at 320/375/430
pixels, alongside working Compare/Favorite controls. Admin Users selection/action
controls must align vertically with consistent heights and spacing; preserve
permissions and form behavior, allow intentional table scrolling without page
overflow. Verify Chromium and WebKit at 430px plus desktop and 200% text zoom.

## DYL-30: deliberate secure password change

Existing current-password check and session-version invalidation are present;
preserve and test them. Replace always-exposed fields with a deliberate Account
security entry and focused change-password screen or accessible disclosure.
Current password, matching confirmation, CSRF and authenticated active account
are mandatory. Apply existing password policy consistently; reject unchanged
password and bound input length before expensive hashing. Add durable per-account
attempt throttling using existing security infrastructure, including incorrect
current-password attempts. Do not let another account's failure lock this user.
On success revoke previous sessions/remember cookies/reset tokens and require a
fresh login with clear confirmation. Never log or reflect passwords. No MFA or
email delivery integration is introduced; QA must not send email. Test stale
sessions, missing/wrong current password, CSRF, throttling, reuse, mismatches,
successful fresh login and another account's isolation. Preserve admin recovery.

## Implementation and acceptance

Cursor owns implementation under src/sabermetrics and tests, and this spec only.
Read relevant code first. No dependencies, CI, deployment, secret, production-data
or provider changes. No commits, publishing or subagents. Additive schema and
ingestion changes needed for these features are allowed and must be documented.
Keep recently approved navigation/Research behavior and account isolation intact.
Write focused meaningful regression tests for data semantics and authorization;
update existing tests when their intentional UX contract changes. Run focused
tests, canonical Ruff/Black and mypy. Coordinator independently reviews full diff,
validates security/privacy boundaries and data availability, runs full suite,
native image smokes and real QA browser acceptance. Only after this does the
candidate go to owner review; issues remain Ready for Review until production.

## Coordinator review

Card eligibility is checked separately from affirmative format legality. Reference:
[Wizards Comprehensive Rules, 903.3 and 903.3a](https://media.wizards.com/2026/downloads/MagicCompRules%2020260619.pdf).
Source sync now carries actual power/toughness text; QA can be enriched from its
existing read-only reference database. Current application data has 31,830
format-legal cards; no new service is needed. The public projection omits draft,
notes and sideboard zones as well as private owner metadata.
