# Architecture and tradeoffs

Deck Lab separates user-owned editable state from externally sourced research.
Flask serves the UI and APIs; SQLite stores accounts, documents, preferences,
and cached public facts. Research ingestion consumes the versioned `mtg_v1`
PostgreSQL contract through a read-only role. Refreshes preserve account/deck
state, and failed refreshes retain the last complete snapshot with visible
freshness limits.

## Decisions

| Decision | Benefit | Cost / boundary |
| --- | --- | --- |
| Flask + server-rendered HTML with progressive JS | One deployable application; ordinary HTTP debugging | Rich interactions require deliberate client state/history handling |
| SQLite for app state | Simple persistence and consistent online backups | One writable host; write concurrency needs care |
| Versioned external data views | Ingestion can evolve behind a stable consumer contract | Consumer must represent unavailable/stale source data |
| Separate simulator service | Independent versioning and schema validation | Availability and model fidelity are explicit product limits |
| Deterministic selection, optional model narrative | Reproducible candidates despite provider failures | Curated strategy material constrains supported builds |
| Isolated QA on the same host | Reviewable releases within a small hosting budget | Shared host failure remains possible |

## Release and recovery

CI tests a Linux container and retains its exact image as a release artifact.
The operator checks provenance, image identity, and installed-package smokes.
An owner reviews QA before approving a production application change. Promotion
uses current production data and a pre-release encrypted backup; image rollback
does not overwrite recent data with a stale database.

Production backup jobs and restore checks run on the host. Credentials and
private operational receipts are intentionally absent from public GitHub.
