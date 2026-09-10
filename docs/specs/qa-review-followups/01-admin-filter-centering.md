# QA follow-up 1: Center Users category labels

Owner feedback: on /admin/users?status=active, All / Invited / Active / Disabled labels sit above the vertical center of their segmented buttons.

Scope: fix the actual text alignment, not merely equal container heights. All four links, including the selected state and counts, must use equal vertical padding and center their text within the hit area. Preserve status URLs, active indication, search query, and admin authorization. Scope styles to the Users controls to avoid changing other segmented navigation.

Acceptance: verify All, Invited, Active and Disabled at 320, 430 and 1280 CSS pixels, normal and 200% text size, Chromium and WebKit. Text line-box center must match button center within 2px. Buttons remain keyboard accessible, at least 44px high, and do not cause page overflow. A horizontally scrollable table is acceptable.

Implementation ownership: admin_users.html and the .dl-user-controls CSS rules only; add a focused regression only if it verifies behavior. Do not modify commander or range styles. No user data changes.
