# QA follow-up 3: Single-track two-handle numeric ranges

Owner feedback: mana value, power and toughness currently each have two separate slider rows. Replace each with one horizontal track containing a minimum and maximum handle, following the supplied reference's geometry while retaining Deck Lab styling.

Use the shared _bound_range.html component so every occurrence is consistent. Keep existing query parameter names, integer buckets 0 through 9 and open-ended 10+, filtering semantics and Apply/Clear flow. Show a concise current selection (e.g. 1–5), endpoints/ticks as space permits, and a per-range Clear action resetting to 0–10+. Full range includes all values; unknown printed stats remain excluded only when a stat range is active. No backend semantics changes.

Both handles must support pointer/touch drag and keyboard input, have independent accessible minimum/maximum names and aria-valuetext including 10+, visible focus, and maintain min <= max. Overlap and coincident values must remain recoverable: either handle can move away from the other, including at both endpoints. Keep two native range inputs overlaid on a single visual track where practical; provide no-JS usable form controls. Avoid dependencies.

Acceptance: single visible track per stat, handles share the same vertical center; submit/Back/pagination restore both values; Clear resets one range only; 0..0 and 10+..10+ work; dragging both directions and coincident handles work in Chromium/WebKit at 320, 430 and desktop widths; keyboard arrows/Home/End work; no horizontal page overflow. Do not label 10+ as exactly 10.

Ownership: _bound_range.html, only range-control CSS/JS in deck-lab.css and deck-lab-research.js, focused range tests if needed. Do not change commander actions or Users controls.
