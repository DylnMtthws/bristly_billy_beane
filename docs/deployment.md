# Deployment

Production is one Python 3.11 container on one managed machine, behind the
platform TLS proxy, with SQLite app state on a persistent volume at `/data`.
The image binds `0.0.0.0:8080` **inside the container only**. Do not expose that
port directly and do not start a second app machine while SQLite is the state
store. The checked-in `deploy/fly.toml` is a draft; deployment and account
creation remain separate, deliberate operations.

## ADR-028: cloud hosting

ADR-028 supersedes ADR-008's rejection of cloud hosting on cost and ADR-026's
tailnet-only production posture. The closed beta needs a public URL that does
not require every invited tester to install Tailscale. The chosen shape is one
container on a managed platform, still one Python process, with waitress and
one process-wide build thread pool. SQLite remains appropriate only because
there is exactly one machine and one attached volume.

Production uses `hybrid` auth. Public requests use the password and one-time
invite path; trusted Tailscale identity headers can still provide passwordless
access where that proxy is actually present. Accounts remain admin-issued and
there is no registration route (ADR-015 is unchanged). The cEDH app reads
`mtg_v1` from managed Postgres over TLS with `sslmode=require`, and calls the
simulator over plain HTTP on the platform's private network. Its response is
accepted only after validating `cedh-simulation-result.v3`.

The annual `$30` target and `$100` ceiling in `CLAUDE.md` now describe LLM
spend only. Hosting is budgeted separately at approximately $15–$25 per month.
If a second app machine is ever needed, migrate users, jobs, feedback,
candidates and the other app-state tables from SQLite to Postgres first; do
not attach two writers to this volume.

## Container deployment

Build and smoke the artifact locally without publishing it:

```bash
docker build --platform linux/amd64 -t decklab .
docker run --rm --tmpfs /data:rw,uid=1001,gid=1001 \
  -e SABER_AUTH_MODE=password -e SABER_SECRET_KEY=local-smoke-only \
  -p 127.0.0.1:8080:8080 decklab
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/login
```

The final verification produced a 91,467,622-byte (about 91.5 MB) amd64 image,
running as uid/gid `1001:1001`. CI rebuilds it, enforces the 400 MB ceiling,
and smokes both endpoints on an empty tmpfs `/data`.

### Environment

| Variable | Default | Effect |
|---|---|---|
| `SABER_BIND_HOST` | `127.0.0.1` | Waitress bind host; production sets `0.0.0.0` inside the container |
| `SABER_PORT` | `5000` | Listen port; production sets `8080` |
| `SABER_TRUSTED_PROXY` | `127.0.0.1` | Immediate proxy trusted by waitress; production sets `*`, still limited to one hop |
| `SABER_DB_PATH` | `data/sabermetrics.db` | SQLite app-state path; production sets `/data/sabermetrics.db` |
| `CEDH_SIMULATOR_URL` | unset | Selects the HTTP simulator client and supplies its private base URL |
| `CEDH_SIMULATOR_TIMEOUT` | `330` | Simulator read/write timeout in seconds; outlives the server's 300 s ceiling |
| `SABER_EMAIL_FROM` | unset | Verified Resend sender, e.g. `Deck Lab <accounts@decklab.studio>` |
| `SABER_PUBLIC_URL` | unset | Trusted HTTPS website origin for reset links; use the current public Fly URL until a custom website domain is configured |

Production additionally sets `SABER_PUBLIC=1` and `SABER_AUTH_MODE=hybrid` as
shown in `deploy/fly.toml`. `SABER_PUBLIC=1` requires a stable secret,
`MTG_V1_DSN`, and `CEDH_SIMULATOR_URL`; missing values are fatal at startup.
public `0.0.0.0` binding with `tailscale`-only auth is also fatal.

### Secrets

Set these in the platform secret store, never in `fly.toml`, `.env`, an image
layer, a log, or a commit:

| Secret | Required | Purpose |
|---|---|---|
| `SABER_SECRET_KEY` | Yes | Stable session and CSRF signing key; generate 32 random bytes or more |
| `MTG_V1_DSN` | Yes for production card data | `mtg_consumer` Postgres DSN, including `?sslmode=require` |
| `HF_TOKEN` | Optional | Enables DeepSeek narrative/explanation calls; builds remain deterministic without it |
| `CEDH_SIMULATOR_URL` | Yes for production simulation | Private simulator base URL; also selects HTTP mode |
| `RESEND_API_KEY` | For password recovery | Resend sending-only key restricted to the verified sending domain |

### Password recovery

Set `RESEND_API_KEY`, `SABER_EMAIL_FROM`, and `SABER_PUBLIC_URL` together to
enable **Forgot password?** in password/hybrid mode. Partial configuration
fails at startup; with all three unset recovery is
disabled and the existing login/invite flow works normally. Store the API key
using the platform CLI's stdin secret import, never as a command argument or
in repository files. The website origin must be HTTPS; request Host/proxy
headers are never used to construct email links. The verified email domain
does not also have to host the website.

Use Resend's free transactional tier for this low-volume deployment and leave
email click/open tracking disabled for recovery messages. Resend accepts the
email via its HTTPS API; API acceptance does not prove inbox delivery. After
deployment, test actual delivery to the administrator and complete a reset
in their browser before calling the feature operational.

Recovery applies only to active accounts with passwords; it cannot create an
account, accept an invitation, enable a disabled account, or change its role.
The browser receives the same response regardless of account eligibility or
email delivery. Mail runs on a bounded background worker (eight tasks maximum).
Pending messages may be lost on restart; retry after one minute. Failures are
logged without provider response bodies, email contents, tokens or API keys.
There is no automatic email retry that could duplicate sends.

Reset links expire after 30 minutes, are stored only as SHA-256 hashes in
SQLite, and are consumed in the same transaction as the password change.
The token is in a URL fragment, moved by first-party JavaScript into the
CSRF-protected POST body and removed from browser history. Recovery pages load
no third-party scripts, set a strict CSP, forbid caching and send no referrer.
JavaScript is required; scanners opening links do not consume them.

Limits: requests 5/minute and 20/hour per IP; submissions 10/minute and 50/hour
per IP. SQLite enforces one email per account per minute, three per hour, and
40 reset emails total per rolling 24 hours, even across restarts or rotating
IPs. The total reserves room for up to 40 password-change notifications within
Resend's 100/day free allowance; other applications sharing the account also
consume its allowance. Failed delivery attempts count toward these limits.

Successful recovery preserves all profile, role, ownership and quota fields,
clears login lockout, and invalidates previous password sessions, other reset
links, and outstanding invite/setup links. A notification is sent and the user
signs in normally. Trusted Tailscale identity remains a separate login method.

Before deployment, take an online SQLite backup. Normal `sabermetrics serve`
startup adds a `session_version` column and `password_reset_tokens` table
idempotently. Legacy version-zero cookies work until that account changes its
password. **Do not roll back to an older auth implementation after a reset**:
it would accept legacy cookies without the version check. Disable recovery by
removing all three email settings together while retaining this auth version.

### First admin and tester onboarding under `hybrid`

Run the first command in the production container/console. It prompts for the
password rather than putting a credential in shell history:

```bash
SABER_DB_PATH=/data/sabermetrics.db sabermetrics create-admin \
  --email admin@example.com --display-name "Deck Lab Admin"
```

Then sign in as that admin, or provision each tester from the console. The
admin creates an inactive account and a one-time invite; the tester opens the
link, chooses their own password, and becomes active. There is no
self-registration.

With the Resend settings above configured, **Create & send invite** automatically
emails the link from `SABER_EMAIL_FROM`. **Resend invite** sends a fresh link to
an existing invited user without creating another account. Links use the
trusted `SABER_PUBLIC_URL`, never the request's Host header, and expire after
seven days. The admin request waits for the email API response (10-second HTTP
timeouts); success means the provider accepted the email, not proof of inbox
delivery. A failed or unconfirmed send leaves the account invited and displays
a retry message. No invite link is flashed into the admin session in email mode.
Disabled/active accounts cannot be reinvited; accepting an invite still preserves
the role and quota the admin assigned. Invitations share Resend's sending quota
with password recovery. No new secrets or services are needed.

Private/local deployments without email settings retain manual links. A public
deployment without email configuration reports email as unavailable instead of
silently requiring manual delivery. Console `invite-user` remains a manual
operator workflow.

```bash
SABER_DB_PATH=/data/sabermetrics.db sabermetrics invite-user \
  --email alice@example.com --display-name "Alice" \
  --base-url https://decklab.example.com
```

### Backup and restore

`db-backup` uses SQLite's online backup API, so snapshots are consistent even
while the single app process is serving requests:

```bash
SABER_DB_PATH=/data/sabermetrics.db sabermetrics db-backup \
  /data/backups/sabermetrics-2026-09-04.db
```

To restore, first stop the app process, preserve the current database, restore
through the same SQLite backup API, then restart:

```bash
SABER_DB_PATH=/data/sabermetrics.db sabermetrics db-backup \
  /data/backups/pre-restore.db
SABER_DB_PATH=/data/backups/sabermetrics-2026-09-04.db \
  sabermetrics db-backup /data/sabermetrics.db
```

The schema is created automatically on an empty mounted volume. Any build job
left nonterminal by a restart is surfaced as `failed(interrupted)` and can be
rebuilt; only a completed candidate counts against quota.

### Integration hand-off

- Postgres stays behind `mtg_v1` and `assert_v1_only`; the production DSN must
  retain `sslmode=require`.
- The simulator request contract is the frozen contract in
  `CLOUD_ALIGNMENT_PLAN.md` section 2.2.
- The vendored response schema is
  `fixtures/cedh/contracts/cedh-simulation-result.v3.schema.json`; its schema id
  is `cedh-simulation-result.v3` and every HTTP 200 is validated against it.
- Platform secrets are exactly the four entries in the table above. No real
  secret or remote DSN is checked into this repository.

## Local/tailnet alternative

The app still defaults to `127.0.0.1:5000`. `tailscale serve` can terminate TLS
on a private tailnet and proxy to that local port, so devices on the tailnet
can use passwordless `tailscale` auth. This is a supported local/private shape,
not the ADR-028 production deployment.

---

### Pick a local shape

| | Private (tailnet only) | **Public (local Funnel alternative)** |
|---|---|---|
| Who can reach it | Devices on your tailnet | Anyone with the URL |
| Testers must install Tailscale | Yes | No |
| `SABER_AUTH_MODE` | `tailscale` | `hybrid` |
| How people sign in | Tailscale identity, no password | You: identity. Them: password |
| Public attack surface | None | The login page |

Both local alternatives are below. The managed container above is the intended
production shape for testers who should be able to open a link and nothing else.

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

**HTTPS must be enabled for the tailnet first**, or this hangs while it waits
for a certificate it cannot get. Turn it on once at
<https://login.tailscale.com/admin/dns> → *Enable HTTPS*. Check with:

```bash
tailscale status --json | python3 -c "import json,sys; print(json.load(sys.stdin).get('CertDomains'))"
# null  → not enabled yet
```

Until it is enabled you can still run tailnet-only over plain HTTP, which is
encrypted by WireGuard anyway:

```bash
SABER_COOKIE_SECURE=0 sabermetrics serve    # Secure cookies are not sent over http
tailscale serve --bg --http=8080 5000
```

Drop `SABER_COOKIE_SECURE=0` the moment HTTPS is on — without it, sessions and
CSRF tokens do not persist, and POSTs fail with a 400 that looks like a bug.

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

## Local public alternative: Funnel

`tailscale funnel` publishes the same port to the public internet, on the same
`.ts.net` hostname, with the same certificate. Testers open a link; they install
nothing.

**Funnel traffic is anonymous.** Tailscale does not set identity headers on it,
so a public visitor can never arrive already authenticated. That is why a public
deployment runs in `hybrid` mode: your tailnet requests still authenticate by
identity, and everyone else gets the password form.

Funnel needs two one-time console changes, and `tailscale funnel` fails
without them:

1. **HTTPS certificates** — <https://login.tailscale.com/admin/dns> → *Enable HTTPS*
2. **The funnel node attribute** — <https://login.tailscale.com/admin/acls>:

   ```jsonc
   "nodeAttrs": [
     { "target": ["autogroup:member"], "attr": ["funnel"] }
   ]
   ```

Verify both before trying:

```bash
tailscale status --json | python3 -c "import json,sys; d=json.load(sys.stdin); \
  print('https:', d.get('CertDomains')); \
  print('funnel:', 'funnel' in str(d['Self'].get('CapMap', {})))"
```

```bash

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
| `hybrid` | Yes | Yes | Managed public deployment, or local Funnel alternative |
| `password` | No | Yes | Local development; the test suite default |

`password` ignores identity headers entirely, so a password deployment can
never silently become header-authenticated.

```bash
sabermetrics create-admin --email you@example.com
sabermetrics invite-user --email tester@example.com
```

## Keeping the local alternative running

`sabermetrics serve` runs under `waitress`. To survive reboots, run it from a
launchd job in the style of the ones in `launchd/`, and set `tailscale serve`
to persist with `--bg` (it already does; check with `tailscale serve status`).
