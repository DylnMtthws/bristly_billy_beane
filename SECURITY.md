# Security review — multi-user beta

Reviewed before the first internet-exposed (Cloudflare Tunnel) release. The app
went from single-user/localhost to an authenticated, publicly reachable web app
(ADR-015..018), so the whole auth + exposure surface was audited.

## Controls verified

**Authentication & accounts**
- Passwords hashed with **argon2id** (`db.hash_password` / `verify_password`); no plaintext, no reversible storage.
- **Admin-provisioned only** — no self-registration route. Accounts are created by an admin; the invitee sets their own password via a **single-use, expiring** invite token (`secrets.token_urlsafe(32)`, `InviteRepo.get_valid` enforces unused + unexpired).
- Login is **rate-limited** (10/min, 50/hr) keyed on the real client IP (`CF-Connecting-IP`).
- Login timing is **equalized** — one argon2 verify always runs (dummy hash for unknown/password-less emails), so response time can't enumerate valid users. The error message is generic ("Invalid email or password").
- Disabled/invited accounts cannot hold a session (`AuthUser.is_active` gates on `status == 'active'`).

**Sessions & CSRF**
- Signed session cookies with `HttpOnly`, `SameSite=Lax`, and `Secure` (default on; `SABER_COOKIE_SECURE=0` only for local http).
- `SECRET_KEY` from `SABER_SECRET_KEY`; a random fallback is used with a loud warning (must be set in production).
- **CSRF protection (Flask-WTF) is global** on all state-changing requests. Server-rendered forms include `csrf_token`; async POSTs (favorites, feedback, generate) send `X-CSRFToken`. No endpoint is CSRF-exempt.

**Authorization (no IDOR)**
- Every user route is behind a login `before_request`; the admin blueprint is behind an admin-role `before_request`.
- Decks are owner-scoped: viewing/deleting a deck and per-deck favorite/feedback all require `owner_id == current_user` (admins may *view/delete* but not author feedback on others' decks). Cross-user access returns **403**.
- Admin-only data (feedback export, per-user stats, cost) is reachable only through the admin gate.

**Injection & output**
- All SQL is parameterized; the Explore filter builder uses `?` placeholders and a fixed ORDER BY allowlist (no string interpolation of user input).
- Jinja autoescaping is on; user-supplied content (comments, display names, deck names) is escaped. JS data is passed via `|tojson`.

**Exposure**
- App binds **127.0.0.1 only**; public access is via the Cloudflare Tunnel (`cloudflared` connects locally). The port is never exposed directly.
- `ProxyFix` trusts exactly one proxy hop (Cloudflare). Served by **waitress**, not the Flask dev server; `debug=False`.
- Generation errors are logged server-side but returned to clients as a generic message (no stack/path disclosure).

## Residual risks (accepted for the closed beta)
- **No Content-Security-Policy header.** The UI uses the Tailwind Play CDN + inline scripts, so a strict CSP isn't trivial. XSS risk is mitigated by Jinja autoescaping. Revisit (nonce-based CSP + self-hosted Tailwind build) before any open/public launch.
- **ProxyFix trusts forwarded headers.** Correct only because the tunnel is the sole ingress and the app binds localhost. Do not bind `0.0.0.0` or add other ingress without revisiting.
- **In-memory rate-limit + session store.** Fine for a single-process waitress deployment; would need shared storage if ever scaled out.

## Release gate — before sharing the tunnel URL
1. `SABER_SECRET_KEY` set to a strong random value in `.env`.
2. `SABER_COOKIE_SECURE` unset or `1` (never `0` in production).
3. App bound to `127.0.0.1`; Cloudflare Tunnel is the only ingress.
4. `ANTHROPIC_API_KEY` set; monthly cost ceiling configured (`settings.llm.monthly_cost_ceiling_usd`).
5. Only intended testers invited; no stray `admin`-role accounts.
