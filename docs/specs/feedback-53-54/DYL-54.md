# DYL-54 — Dismiss transient menus outside their selection area

Reported in builder on iOS Safari 26 at 430x775. Deck options and generated zone-action details lack outside dismissal; custom selects and account menus have partial independent behavior.

Acceptance:
- Tapping/clicking outside an open Deck options, zone actions, Export (data-dismiss-menu), account menu, custom select or card-search suggestions closes that transient menu. A touch/pointer event must work even when playmat drag handlers stop bubbling. Delegate in capture phase where appropriate and avoid preventing default/stealing normal target actions.
- Inside presses remain usable: selecting options, clipboard/export actions, nested form inputs, scroll, and search results must not be swallowed or closed before selection. Opening a different transient menu closes the prior menu; trigger toggling works.
- Escape closes the relevant menu and returns focus to its trigger where appropriate; outside pointer dismissal does not steal focus from the tapped target. Preserve select keyboard navigation and search aria-expanded state.
- Dynamic zones/menus created after builder render inherit the behavior without duplicate event listeners. Menus fit the viewport. Do not treat all details as menus: preserve legitimate accordions. Do not silently dismiss unsaved edit/confirmation dialogs. Existing modal cancel/close remains separate.
- Export session marks its menu data-dismiss-menu. You may use class-based known menu selectors for existing dynamic menus, avoiding changes to session 1 template sections.
- Focused tests and browser acceptance on desktop Chromium and mobile WebKit: outside, inside, toggle, Escape, dynamic zone menu, custom select, autocomplete, and no accidental deck command or lost selection.

Implementation: shared deck-lab-shell.js owns menu dismissal; deck-lab-builder.js may need narrow search-hook changes. Do not rewrite export/share handler or header owned by DYL-53.
