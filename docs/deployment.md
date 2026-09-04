# Deployment

One machine, one command, no public surface.

The app binds `127.0.0.1` and **is never exposed by a port forward**.
`tailscale serve` terminates TLS on your tailnet and proxies to that local
port, so the only people who can reach it are devices on your tailnet — and
Tailscale has already authenticated every one of them.

This replaces the Cloudflare Tunnel design (ADR-016), which was specified but
never deployed. Tailscale is a strictly better position for this app: the
tunnel would have put a login page on the public internet, where anyone could
knock on it. A tailnet has no public surface to knock on at all. It is also one
command instead of a daemon, a config file, a DNS record and an account.

---

## 1. Start the app

```bash
export SABER_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export SABER_AUTH_MODE=tailscale
sabermetrics serve            # binds 127.0.0.1:5000, always
```

`SABER_SECRET_KEY` must be set and stable. Without it the app generates a random
key at startup, which signs CSRF tokens — every restart invalidates them.

## 2. Put it on the tailnet

```bash
tailscale serve --bg 5000
tailscale serve status
```

That publishes `https://<machine>.<tailnet>.ts.net` with a real certificate,
reachable only from your tailnet. On this machine that is:

```
https://macmini.tail8c92e6.ts.net
```

**Do not run `tailscale funnel`.** Funnel exposes the same port to the public
internet *and does not set identity headers*, so every request arrives
anonymous. The app refuses anonymous requests, so Funnel would not leak data —
but it would put the origin on the public internet for no benefit.

## 3. Give yourself an account

Tailscale says who you are; the database says what you may do. Find your login:

```bash
tailscale status --json | python3 -c \
  "import json,sys; print(list(json.load(sys.stdin)['User'].values())[0]['LoginName'])"
# → DylnMtthws@github
```

Then:

```bash
sabermetrics grant-access DylnMtthws@github --admin
```

## 4. Add a tester

Two steps, neither of which involves a password, an invite link or an email.

1. **Share the machine with them in Tailscale.** In the admin console, share
   the `macmini` node with their Tailscale account, or invite them to the
   tailnet. They install Tailscale (free) and accept.
2. **Give them an account:**

   ```bash
   sabermetrics grant-access alice@github --name "Alice"
   ```

   If you do not know their login yet, let them open the URL first — the page
   they land on prints the exact command, with their login already filled in.

Then send them the `.ts.net` URL. That is the whole flow.

```bash
sabermetrics list-access                    # who has access
sabermetrics revoke-access alice@github     # takes effect on their next request
```

Revocation is immediate. Identity is re-checked on every request, so there is
no session to expire and no token to wait out.

---

## How the identity actually works

`tailscale serve` sets three headers on every proxied request:

| Header | Example |
|---|---|
| `Tailscale-User-Login` | `alice@github` |
| `Tailscale-User-Name` | `Alice Example` |
| `Tailscale-User-Profile-Pic` | `https://...` |

It **strips any client-supplied `Tailscale-*` headers before setting its own**,
which is what makes them trustworthy: a remote caller cannot inject an identity.

The app adds a second check. `ProxyFix` rewrites `remote_addr` from the single
`X-Forwarded-For` hop the proxy sets, and
`sabermetrics.ui.tailscale_auth.identity_from_headers` refuses any identity
whose source address is outside Tailscale's CGNAT range (`100.64.0.0/10`). That
is defence against a misconfiguration rather than against an attacker: if the
app is ever bound to a LAN interface, a request from `192.168.x.x` with
hand-set headers is refused instead of believed.

### What this does not protect against

**A process already running on the Mac mini can forge an identity** by
connecting to `127.0.0.1:5000` and setting the headers itself. That is not
closable at this layer — a local process can also just read
`data/sabermetrics.db`. The host is the trust boundary, and it is your own
machine. Stated here rather than left implicit, because unstated assumptions
about header trust are how this class of auth goes wrong.

### Failure modes, and what each looks like

| Situation | Result |
|---|---|
| Opened `localhost:5000` directly | 403, "did not arrive through the tailnet" |
| On the tailnet, no account | 403, page prints the `grant-access` command |
| Account disabled | 403, "this account has been disabled" |
| Funnel enabled, request from the internet | 403, anonymous requests are refused |
| Not on the tailnet at all | Connection refused — nothing is listening publicly |

---

## Password mode

`SABER_AUTH_MODE=password` (the default) keeps the original email + argon2id
flow with admin-issued invite links, and is what the test suite runs against.
Use it for local development. It is still a supported mode, not dead code — but
it is not how this is deployed, because it needs the app to be reachable
somewhere a password can be typed.

```bash
sabermetrics create-admin --email you@example.com
sabermetrics invite-user --email tester@example.com
```

## Keeping it running

`sabermetrics serve` runs under `waitress`. To survive reboots, run it from a
launchd job in the style of the ones in `launchd/`, and set `tailscale serve`
to persist with `--bg` (it already does; check with `tailscale serve status`).
