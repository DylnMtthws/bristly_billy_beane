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

## Pick a shape first

| | Private (tailnet only) | **Public (Funnel)** |
|---|---|---|
| Who can reach it | Devices on your tailnet | Anyone with the URL |
| Testers must install Tailscale | Yes | No |
| `SABER_AUTH_MODE` | `tailscale` | `hybrid` |
| How people sign in | Tailscale identity, no password | You: identity. Them: password |
| Public attack surface | None | The login page |

Both are below. Private is stronger and is the default; public is what you want
if testers should be able to open a link and nothing else.

---

## 1. Start the app

```bash
export SABER_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export SABER_AUTH_MODE=tailscale     # or `hybrid` for a public deployment
sabermetrics serve                   # binds 127.0.0.1:5000, always
```

`SABER_SECRET_KEY` must be set and stable — it signs session cookies and CSRF
tokens, so a fresh key on every restart logs everyone out. On a public
deployment the app **refuses to start** without one, because that failure
otherwise shows up as "logins are flaky" rather than as a misconfiguration.

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

If you want a public URL instead, skip to
[Going public with Funnel](#going-public-with-funnel).

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

## Going public with Funnel

`tailscale funnel` publishes the same port to the public internet, on the same
`.ts.net` hostname, with the same certificate. Testers open a link; they install
nothing.

**Funnel traffic is anonymous.** Tailscale does not set identity headers on it,
so a public visitor can never arrive already authenticated. That is why a public
deployment runs in `hybrid` mode: your tailnet requests still authenticate by
identity, and everyone else gets the password form.

```bash
# One-time: enable Funnel for the tailnet in the admin console
#   https://login.tailscale.com/admin/acls  →  add "funnel" to nodeAttrs

export SABER_AUTH_MODE=hybrid
export SABER_PUBLIC=1                # required; turns on the public posture
export SABER_SECRET_KEY="<a real, stable 64-char hex string>"
sabermetrics serve

tailscale funnel --bg 5000
tailscale funnel status              # prints the public URL
```

Then give each tester a password account the ordinary way:

```bash
sabermetrics invite-user --email alice@example.com
# → prints a one-time link; send it to her
```

You keep passwordless access over the tailnet; they sign in with a password.

### What `SABER_PUBLIC=1` changes

It does not change routing. It tightens the posture, and it is opt-in so a
private deployment is never held to a public policy or the reverse.

* **A stable `SABER_SECRET_KEY` becomes mandatory** — the app refuses to start
  without one.
* **HSTS is sent.** Only here: over plain http it is meaningless, and on a
  local preview it would pin a stale policy into your browser.

Baseline headers (`X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy`, `Permissions-Policy`) are always sent, public or not — a
header that is only correct sometimes is one nobody can reason about.

### What protects the login page

It is on the internet now, so:

| Control | Behaviour |
|---|---|
| Rate limiting | 10 sign-ins/minute, 50/hour per source address |
| Account lockout | 5 failures locks the account for 15 minutes |
| Locked + correct password | Still refused — otherwise the lock is decorative against someone who guesses right |
| Unknown email vs wrong password | Identical message, so the form cannot enumerate accounts |
| Hashing | argon2id |
| Registration | None. Invite-only, admin-issued |

Lockout is per **account**, not only per address, because an attacker rotates
IPs and Funnel traffic may share one.

### Turning it off

```bash
tailscale funnel --bg off       # back to tailnet-only, instantly
```

Then drop `SABER_PUBLIC` and set `SABER_AUTH_MODE=tailscale` to remove the
password path entirely.

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
| Funnel enabled, `tailscale` mode, request from the internet | 403, anonymous requests are refused |
| Funnel enabled, `hybrid` mode, request from the internet | The password login page |
| Identity headers on a Funnel request | Refused — Tailscale never sets them there, so their presence is a forgery |
| Not on the tailnet at all | Connection refused — nothing is listening publicly |

---

## The three modes

| Mode | Tailnet identity | Password form | Use |
|---|---|---|---|
| `tailscale` | Yes | No | Private tailnet deployment |
| `hybrid` | Yes | Yes | Public Funnel deployment |
| `password` | No | Yes | Local development; the test suite default |

`password` ignores identity headers entirely, so a password deployment can
never silently become header-authenticated.

```bash
sabermetrics create-admin --email you@example.com
sabermetrics invite-user --email tester@example.com
```

## Keeping it running

`sabermetrics serve` runs under `waitress`. To survive reboots, run it from a
launchd job in the style of the ones in `launchd/`, and set `tailscale serve`
to persist with `--bg` (it already does; check with `tailscale serve status`).
