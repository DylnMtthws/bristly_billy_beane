# QA follow-up 2: Build / Favorite and retire Compare

Owner feedback: the Commanders catalog currently places Start a build above Compare and a heart. Replace this with one compact row: [Build] [Favorite]. Remove the Compare button and page because it is unused and slow.

Scope: catalog cards display exactly Build and Favorite actions side by side, with visible labels and an optional heart. Preserve CSRF-protected build creation and single-click favorite toggling, selected/aria-pressed state and favorites filtering. Apply the Build label and remove Compare on commander detail and any other Research entry points for consistency. Preserve representative lists, individual commander details, Meta, and card discovery.

Remove the comparison route, its unused template and comparison-only backend work when safely unused; old /research/compare URLs should redirect directly to /research/ (no expensive comparison computation). Remove every link to the retired page. Update tests that expected it; include a regression that the old URL redirects without calculating comparisons.

Acceptance: catalog has one row with Build and Favorite at 320, 375, 430 and desktop widths, useful >=44px touch targets, visible keyboard focus and no overflow. Build opens a new owner deck for the selected commander; Favorite toggles exactly once without navigation and survives refresh. No Compare link anywhere in Research. Direct legacy URL redirects promptly. No schema or infrastructure change.

Ownership: Research routes/templates, comparison-only repository method if unused, commander catalog/action CSS, relevant tests. Do not edit range-control styles/JS or admin controls.
