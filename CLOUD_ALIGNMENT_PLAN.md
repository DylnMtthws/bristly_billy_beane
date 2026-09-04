# Cloud alignment plan: bristly_billy_beane (cEDH Deck Lab)

Purpose: make the Deck Lab deployable as a containerized web app on a single Linux x86-64 machine with a persistent volume, reachable by invited testers over the public internet, reading `mtg_v1` from a managed Postgres over TLS, and calling the simulator over HTTP instead of a local subprocess. This plan is self-contained. Do not read or modify the sibling repositories (`ingestion_pipeline_mtg`, `commander_simulator`); integrate only through the contracts pinned in section 2, which were written against their plans.

Scope boundary: **code, tests, image and documentation only.** Do not create cloud accounts, deploy, or point anything at a remote database. Deployment happens in a later phase using the artifacts this plan produces.

---

## 1. Context the agent needs

Facts established on 2026-09-04:

- Process: `sabermetrics serve` → `src/sabermetrics/main.py:204` → `src/sabermetrics/ui/app.py:154 run_server`. The host is **force-rewritten to `127.0.0.1`** at `app.py:167-172` regardless of `--host`. WSGI is waitress, `threads=8`, `trusted_proxy="127.0.0.1"` (`app.py:194-201`). `ProxyFix` for one hop.
- Ingress today is `tailscale serve`; `SABER_AUTH_MODE=tailscale` derives identity from proxy headers and **has no login form**. `hybrid` and `password` modes exist and are tested; ADR-027 (`CLAUDE.md:407`) already defines the public-exposure playbook: `SABER_PUBLIC=1` forces hybrid, requires a stable `SABER_SECRET_KEY`, enables HSTS and per-account lockout.
- `CLAUDE.md:51` states "never bind 0.0.0.0, never port-forward" and ADR-008 rejected cloud hosting on cost. **This plan supersedes both**; W9 records the decision.
- App state is SQLite at a hardcoded relative path `data/sabermetrics.db` (`main.py:8-10`, `cedh/cost_ledger.py:26`): `users`, `invite_tokens`, `card_feedback`, `deck_feedback`, `cedh_candidates`, `cost_log`, favorites, plus the legacy corpus tables. WAL is set once in `scripts/setup_db.py:540`; **no `busy_timeout`** is set on per-request connections (`db.py:71-97`). Sessions are signed cookies. Rate limiting is `memory://` (`ui/extensions.py:32`), which is fine for one process.
- Postgres: one env var `MTG_V1_DSN` (`cedh/adapters_postgres.py:54-65`, `cedh/factory.py:76,88`), role `mtg_consumer`, `assert_v1_only` guards every statement. Driver is the optional extra `postgres` (`pyproject.toml:31`). The tournament-side views probed at `adapters_postgres.py:46-51` (`tournament`, `tournament_entry`, `commander_identity`, `commander_card_inclusion`) are handled as unavailable when absent; the last two are **not part of the upstream v1 contract** and will stay absent.
- Simulator: `cedh/simulator.py` has `FixtureSimulatorClient`, `SubprocessSimulatorClient`, `DisabledSimulatorClient`, selected by `config/cedh.yaml:69-77` (`mode: fixture` today). The subprocess client invokes `--candidate … --format json` (`simulator.py:308-331`), which **the real binary does not implement**; the binary's interface is `--request … --cards … --output-json`. `CEDH_SIMULATOR_BIN` is test-only. Result timeout 120 s.
- `POST /lab/build` (`ui/cedh_routes.py:134-141` → `cedh/lab.py:204-209`) runs the simulator and then the model call **synchronously inside the request**: worst case 120 s + 60 s × up to 4 model attempts. No queue, no threads.
- LLM: `httpx` against `https://router.huggingface.co/v1`, provider pinned to DeepInfra, key in `HF_TOKEN` (`config/cedh.yaml:10-21`, `cedh/provider_deepseek.py`). Global ceiling `config/settings.yaml:22`. `HF_TOKEN` is currently commented out in `.env`, so narratives are off; unrelated to this plan.
- `sentence-transformers` is a **required** dependency (`pyproject.toml:24`) but is imported only by the legacy path (`analytics/embeddings.py:67`, `reference_layer/indexer.py:39`); nothing under `src/sabermetrics/cedh/` imports it, and tests assert that import graph. `.venv` is 1.2 GB of which torch is 533 MB.
- The four launchd jobs (`launchd/`) refresh only the legacy SQLite corpus. The cEDH path does not use them.
- Tests: 77 files, ~1,022 functions, offline by construction; markers `postgres`, `model`, `simulator` are opt-in. `tests/test_tailscale_auth.py:598` asserts the `127.0.0.1` bind; `tests/test_cedh_lab.py:363` hardcodes a Mac path and skips when absent.
- No Dockerfile, Procfile, or platform config exists. CI is `ubuntu-latest`, Python 3.11, `pip install -e ".[dev]"`, `pytest -q`.

---

## 2. Contracts this repo must honor (pinned, do not change unilaterally)

### 2.1 Postgres (`mtg_v1`)
Unchanged: connect as `mtg_consumer`, read `mtg_v1` only. The production DSN **carries `?sslmode=require`**; psycopg passes it through. Add a unit test that a DSN with a query string reaches `psycopg.connect` untouched. Ids in `mtg_v1` are deterministic uuid5, so nothing cached by oracle id needs invalidating when the upstream database is rebuilt.

### 2.2 Simulation service HTTP contract (the simulator repo is being built to exactly this)

Base URL from `CEDH_SIMULATOR_URL` (e.g. `http://sim-worker.flycast:8080`). Plain HTTP on a private network.

`GET /healthz` → `200 {"status":"ok","cs_version":"<semver>","strategy_packs":[...]}`

`POST /simulate`, JSON:
```json
{
  "candidate": { ...cedh-deck-candidate.v1 document, verbatim... },
  "games": 20000, "turn": 3, "seed": 12345,
  "scenario": "goldfish_assembly.v1",
  "sweep": false, "ablate": []
}
```
Only `candidate` is required. `games` above the service cap is **rejected with 400**, never clamped.

- `200`: body is byte-for-byte the simulator's `--output-json` document. Headers `X-Sim-Version`, `X-Sim-Result-Schema` (e.g. `cedh-simulation-result.v2`), `X-Cards-Sha256`, `X-Sim-Threads`.
- Errors carry `{"error": "<code>", "detail": "<text>", "stderr": "<tail>"}`:
  `400 invalid_request` · `422 unsupported` (pack/commander/quantity rejected) · `429 busy` with `Retry-After` · `503 card_data_unavailable` · `504 timeout` · `500 simulator_failed`.
- Platform behavior the client must tolerate: the service machine is **stopped when idle and started by the first inbound request**, so the first connection can take 2 to 10 s to be accepted. A single simulation takes 1 to 5 s once running; a sweep 20 to 60 s. One simulation runs at a time; a second concurrent request gets 429.

Mapping to this repo's domain: every non-200 becomes `NotSimulated` with a reason code that mirrors the service error code, and `absence_is_visible` (`CLAUDE.md`) applies: the UI renders "not simulated (<reason>)", never a neutral score. A 200 whose `X-Sim-Result-Schema` is not one the app supports is also `NotSimulated(unsupported_schema)`. The result schema file is vendored into `fixtures/cedh/contracts/` from the simulator's published `contracts/` directory (versioned JSON is the sanctioned integration path per `CLAUDE.md`); validate every 200 body against it before use.

### 2.3 Platform assumptions
- Image platform `linux/amd64`, Python 3.11 (current CI version; do not bump in this plan).
- One machine, one process, waitress with 8 threads. No horizontal scaling. The persistent volume is mounted at `/data`.
- The platform's proxy terminates TLS and forwards `X-Forwarded-For`, `X-Forwarded-Proto`, and `Fly-Client-IP`. The app binds `0.0.0.0:8080` **inside the container only**; the container has no public interface except through the proxy.
- Listen port 8080. `GET /healthz` must return 200 without touching Postgres or the simulator (SQLite `SELECT 1` is fine).

---

## 3. Work items, in order

### W1. Configuration surface for hosting (`ui/app.py`, `main.py`, `cedh/settings.py`)
Add, all read from the environment with the current behavior as default:

| Variable | Default | Effect |
|---|---|---|
| `SABER_BIND_HOST` | `127.0.0.1` | replaces the forced rewrite at `app.py:167-172`; log the effective bind |
| `SABER_PORT` | `5000` | |
| `SABER_TRUSTED_PROXY` | `127.0.0.1` | waitress `trusted_proxy`; accept `*` to trust the immediate hop only when the container has no public interface |
| `SABER_DB_PATH` | `data/sabermetrics.db` | replaces both hardcoded paths (`main.py:8`, `cedh/cost_ledger.py:26`); the cost ledger must use the same resolver |
| `CEDH_SIMULATOR_URL` | unset | selects the HTTP simulator client (W4) |
| `CEDH_SIMULATOR_TIMEOUT` | `180` | seconds, total per request including cold start |

Rules: `SABER_PUBLIC=1` with `SABER_BIND_HOST=0.0.0.0` and `SABER_AUTH_MODE=tailscale` is a **fatal startup error** (Tailscale headers cannot arrive through a public proxy; the request would be anonymous). Update `tests/test_tailscale_auth.py:598` to assert the default, and add tests for the new variables and the fatal combination.

Add `GET /healthz` (no auth, no CSRF, not rate-limited) returning `{"status":"ok","version":<package version>}`.

### W2. SQLite hardening
- Set `PRAGMA busy_timeout=5000` and `PRAGMA journal_mode=WAL` on every connection in `db.py:71-97` (WAL is persistent per file, but setting it is idempotent and protects a freshly copied file).
- Add a `sabermetrics db-backup <dest>` CLI command using `sqlite3.Connection.backup()`; the deployment phase uses it for the one-time copy and for scheduled snapshots.
- Ensure `scripts/setup_db.py` and first-run initialization work when `SABER_DB_PATH` points at an empty directory on a mounted volume (create parent dirs, apply schema).
- Test: 8 threads writing feedback rows concurrently for two seconds must produce zero `database is locked` errors.

### W3. Trim the image: make the legacy stack optional
- Move `sentence-transformers` (and `pandas` if only legacy code imports it; verify by grep) to `[project.optional-dependencies] legacy`. Keep `numpy` if `cedh/` or `ui/` import it.
- The lazy imports at `analytics/embeddings.py:67` and `reference_layer/indexer.py:39` must raise a clear `RuntimeError("install sabermetrics[legacy]")` rather than `ImportError` deep in a request.
- CI installs `.[dev,postgres,legacy]` so the legacy tests still run; the Dockerfile installs `.[postgres]` only.
- Confirm via the existing import-graph tests that `cedh/` still imports none of it.

### W4. `HttpSimulatorClient` (`cedh/simulator.py`)
- New client beside the three existing ones, selected when `CEDH_SIMULATOR_URL` is set (env wins over `config/cedh.yaml` `mode`), or by `mode: http` with `url:` in the YAML.
- Uses `httpx` (already a dependency): connect timeout 15 s (cold start), read timeout from `CEDH_SIMULATOR_TIMEOUT`, one retry on connection error or 429 (honor `Retry-After`, cap 10 s), no retry on 4xx/5xx otherwise.
- Sends the candidate document verbatim, `games` and `turn` from `config/cedh.yaml` (`games: 20000`, `objective_turn: 3`), a deterministic `seed` derived from the candidate hash so reruns are reproducible.
- Validates the 200 body against the vendored schema; records `X-Sim-Version`, `X-Cards-Sha256`, `X-Sim-Result-Schema` and `X-Sim-Threads` in the candidate's provenance so the UI can show what simulated it.
- Maps every failure per section 2.2 to `NotSimulated(reason)`.
- Fix or delete `SubprocessSimulatorClient`'s argument list so it matches the real binary (`--request … --cards … --output-json -`), or mark it deprecated in favor of the HTTP client. Do not leave a client that cannot possibly work.
- Tests: `respx` or a local `http.server` stub covering 200, each error code, 429-then-200, cold-start connect delay, schema mismatch, and provenance capture. Add a `simulator` marker test that hits a real `CEDH_SIMULATOR_URL` when set.

### W5. Asynchronous deck build
Replace the synchronous `POST /lab/build` with a job:

- Table `build_jobs(id, user_id, status, created_at, started_at, finished_at, candidate_id, error_code, error_detail)` in SQLite; status in `queued | running | simulating | explaining | done | failed`.
- `POST /lab/build` validates quota and ceiling exactly as today (`cedh_routes.py:99-115`), inserts a job, submits `lab.run(...)` to a process-wide `ThreadPoolExecutor(max_workers=2)`, and returns 202 with the job id (HTML: redirect to `/lab/build/<id>`).
- `GET /lab/build/<id>` renders status and, when done, the deck page; the page polls every 3 s (a `<meta refresh>` or minimal fetch is fine, no framework). JSON variant at `/lab/build/<id>.json` for tests.
- Per-user authorization on the job as on decks: a user never sees another user's job.
- On process restart, any job still `queued`/`running` is marked `failed(interrupted)` at startup; the user can rebuild. Quota is charged only on `done`.
- Keep `lab.run` itself unchanged in signature; the executor wraps it. `max_workers=2` is a deliberate ceiling: the simulator accepts one job at a time.
- Tests: full flow with the fixture simulator and a stub model; failure paths; authorization; interrupted-job cleanup.

### W6. Dockerfile and `.dockerignore`
- `python:3.11-slim-bookworm`, non-root uid 1001, `pip install --no-cache-dir .[postgres]`, copy `config/`, `fixtures/` and `src/`, `EXPOSE 8080`, `CMD ["sabermetrics","serve"]` with `SABER_BIND_HOST=0.0.0.0 SABER_PORT=8080 SABER_DB_PATH=/data/sabermetrics.db` set as image env. Bake the package version into the image label.
- `.dockerignore`: `.venv`, `data/`, `.env*`, `tests/`, `build/`, `*.db`.
- CI job: build for `linux/amd64` with `push: false`, start the container with `SABER_AUTH_MODE=password` and a tmpfs `/data`, curl `/healthz` and the login page. Image publishing is **not** in this plan.

### W7. Public-mode hardening check
`SABER_PUBLIC=1` is the intended production setting. Audit and test that under it: `SABER_SECRET_KEY` missing is fatal (`app.py:71-86`), cookies are `Secure` and `SameSite=Lax`, HSTS is sent, CSRF is enforced on every POST including the new job endpoint, lockout is per account, and `flask-limiter` keys on the proxy-supplied client IP (not the proxy's own address) when `SABER_TRUSTED_PROXY` is set. Remove the stale Cloudflare Tunnel comments at `app.py:159-160,170`.

### W8. `deploy/fly.toml` draft (do not deploy)
```toml
app = "decklab"
primary_region = "ord"
[build]
  dockerfile = "Dockerfile"
[env]
  SABER_PUBLIC = "1"
  SABER_AUTH_MODE = "hybrid"
  SABER_BIND_HOST = "0.0.0.0"
  SABER_PORT = "8080"
  SABER_TRUSTED_PROXY = "*"
  SABER_DB_PATH = "/data/sabermetrics.db"
[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = "off"
  min_machines_running = 1
  [http_service.checks.healthz]
    path = "/healthz"
    interval = "30s"
[mounts]
  source = "decklab_data"
  destination = "/data"
[[vm]]
  size = "shared-cpu-1x"
  memory = "1gb"
```
Header comment: secrets are `SABER_SECRET_KEY`, `MTG_V1_DSN`, `HF_TOKEN`, `CEDH_SIMULATOR_URL`, set on the platform, never in this file. Exactly one machine because SQLite lives on the volume.

### W9. Charter and documentation
- ADRs in this repo are rows in the table at `CLAUDE.md:382-409`; there is no `docs/adr/` directory and this plan does not create one. Add a row for ADR-028 there, and put the full rationale in a new "ADR-028: cloud hosting" section of `docs/deployment.md`. ADR-028 supersedes ADR-008 (cost rejection) and ADR-026 (tailnet-only). Records: one container on a managed platform, SQLite on a volume for the beta, `hybrid` auth with admin-issued invites (ADR-015 unchanged), simulator over private HTTP, `mtg_v1` over TLS, and that the annual cost target in `CLAUDE.md:57-58` now covers LLM spend only, with hosting budgeted separately at about $15 to $25 per month. State the follow-up: move app state from SQLite to Postgres when a second machine is ever needed.
- Update `CLAUDE.md`: the `hosting` and `auth` constraints, the ADR table, the technology stack `binding` line, and the "Explicitly Excluded" strike-throughs. Keep the `tailscale` auth mode documented as valid for local/tailnet use.
- `docs/deployment.md`: add a "Container deployment" section with the env table from W1, the secret list, the backup command from W2, and the tester onboarding flow under `hybrid` mode (admin creates account → invite link → password). Mark the `tailscale serve` section as the local alternative.
- `launchd/` and the four refresh scripts: add a header comment marking them legacy-path only; do not delete.
- `tests/test_cedh_lab.py:363`: replace the hardcoded Mac path with an env var (`CEDH_KINNAN_DECK_TOML`) and keep the skip.

---

## 4. Do not

- Do not add price, budget, or collection inputs anywhere (ADR-025). Do not touch the cEDH data boundary (`assert_v1_only`, `card_any_medium`).
- Do not import anything from the sibling repositories or copy their code; the vendored schema file is the only artifact that crosses.
- Do not add a message broker, Redis, Celery, or a second process. One process, one thread pool.
- Do not enable self-registration. Accounts remain admin-provisioned (ADR-015).
- Do not bump Python or swap waitress for gunicorn.
- Do not delete the SQLite legacy tables, the launchd files, or the Tailscale auth mode.
- Do not deploy, publish images, create accounts, or set a real `MTG_V1_DSN` anywhere in the repository.

---

## 5. Definition of done

- [x] `SABER_BIND_HOST`, `SABER_PORT`, `SABER_TRUSTED_PROXY`, `SABER_DB_PATH`, `CEDH_SIMULATOR_URL`, `CEDH_SIMULATOR_TIMEOUT` implemented and tested; the public+tailscale combination is fatal; `/healthz` exists.
- [x] `busy_timeout` on every connection; `db-backup` command; concurrent-write test passes.
- [x] `sentence-transformers` is an optional `legacy` extra; `pip install .[postgres]` image is under 400 MB.
- [x] `HttpSimulatorClient` implements section 2.2 with tests for every status code; the subprocess client either matches the real binary or is removed.
- [x] `/lab/build` returns immediately with a job; status page and JSON endpoint work; interrupted jobs are cleaned up; quota charged only on success.
- [x] `docker build --platform linux/amd64 .` succeeds; container serves `/healthz` and the login page in CI.
- [x] `deploy/fly.toml`, ADR-028, `CLAUDE.md`, `docs/deployment.md` updated.
- [x] Full suite green (`pytest`), including the new tests; `ruff`, `black`, `mypy` clean as before.
- [x] Hand-off note in `docs/deployment.md`: the env and secret tables, the backup/restore commands, the vendored schema version, and the exact `sabermetrics` CLI commands to create the first admin account under `hybrid` mode.
