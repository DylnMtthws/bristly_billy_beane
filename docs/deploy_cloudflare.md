# Deploy: Mac mini + Cloudflare Tunnel

The app runs on the Mac mini bound to `127.0.0.1` and is published over a
Cloudflare Tunnel (ADR-016). The port is never exposed directly; `cloudflared`
makes an outbound connection to Cloudflare and forwards HTTPS traffic to
`localhost:5000`.

```
Tester's browser  ──HTTPS──▶  Cloudflare edge  ──tunnel──▶  cloudflared (Mac mini)  ──▶  127.0.0.1:5000 (waitress)
```

## 1. One-time setup

### Environment
```bash
cp .env.example .env
# edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...
#   SABER_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
# leave SABER_COOKIE_SECURE unset (defaults to Secure/HTTPS-only)
```

### Database + admin account
```bash
python scripts/setup_db.py                       # idempotent; safe on an existing DB
python -m sabermetrics create-admin --email you@example.com
```

### Install cloudflared
```bash
brew install cloudflared
cloudflared tunnel login                         # opens browser; pick your domain
cloudflared tunnel create saber
# Route a hostname to the tunnel (needs a domain on Cloudflare):
cloudflared tunnel route dns saber saber.yourdomain.com
```

Create `~/.cloudflared/config.yml`:
```yaml
tunnel: saber
credentials-file: /Users/you/.cloudflared/<TUNNEL-UUID>.json
ingress:
  - hostname: saber.yourdomain.com
    service: http://127.0.0.1:5000
  - service: http_status:404
```

> No domain? `cloudflared tunnel --url http://127.0.0.1:5000` gives a temporary
> `*.trycloudflare.com` URL — fine for quick testing, not stable.

## 2. Run

Two processes: the app and the tunnel.

```bash
# App (waitress, bound to localhost):
python -m sabermetrics serve --port 5000

# Tunnel (separate terminal):
cloudflared tunnel run saber
```

For always-on, run each under `launchd` (see `scripts/` launchd plists as a
pattern) or `cloudflared service install`.

## 3. Invite testers
- In the app: **Admin → Users → Add User** → copy the one-time invite link and send it. The invitee sets their own password.
- Or CLI: `python -m sabermetrics invite-user --email friend@example.com --base-url https://saber.yourdomain.com`

Each user is capped at 20 generated decks/month (admin-overridable); the global
monthly cost ceiling is the hard stop.

## 4. Before you share the URL — release gate
Complete the checklist in [`SECURITY.md`](../SECURITY.md):
`SABER_SECRET_KEY` set, cookies Secure, localhost-only bind, cost ceiling
configured, only intended testers invited. Re-run the security review after any
change to auth, sessions, or exposure.

## Ops
- **Logs:** `data/logs/` (JSON, rotated).
- **Cost/usage:** Admin → Costs (per user + call type).
- **Feedback:** Admin → Feedback (+ CSV/JSON export) — the Phase-1 dataset.
- **Backups:** copy `data/sabermetrics.db` (use `sqlite3 ... ".backup"` for a
  consistent snapshot while running).
