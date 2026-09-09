# Production storage and recovery

Production keeps one SQLite writer on machine `2872537a9e3348` and its original encrypted volume `vol_vp2q3wklozkm6z24`. The volume was expanded from 1 GB to 5 GB after backups exhausted it. `fly.production.toml` requests automatic growth at 70% utilization, in 1 GB increments, capped at 10 GB. Growth does not replace retention or monitoring.

## Backups

`scripts/storage_control.py` orchestrates backups to the private Tigris bucket `decklab-production-backups-dylnmtthws`. Credentials live in the protected GitHub production environment and the Mac mini's private dispatcher secret store. They are never written into receipts or Linear.

The production helper uses SQLite's online backup API to stage a consistent snapshot in temporary storage off `/data`. It compresses the snapshot, restores and checks SQLite integrity, uploads it, downloads it again, and compares SHA-256 hashes. Only then does it record a verification manifest and retain a local copy. Remote jobs have a ten-minute execution limit and return durable receipts; a lost Fly exec connection does not imply success.

Every deployment requires a fresh verified private backup and a completed Fly volume snapshot before image promotion. Hourly maintenance archives any older local backups before pruning. Local deletion requires the exact verification marker and an unchanged checksum. Only the two newest verified local backups remain. Remote retention keeps one snapshot for each of the newest 24 populated hourly buckets, 7 daily buckets and 4 weekly buckets. Predeployment and migrated legacy backups are additionally pinned for 30 days. Unreferenced uploads older than 48 hours are removed.

An isolated restore runs weekly on the Mac mini. It downloads an archive into a private temporary directory, verifies compressed and restored checksums, runs SQLite integrity validation, and checks the schema table count. It never writes into production. This verifies database recovery, not a full application disaster-recovery cutover.

## Capacity and SQLite

The release controller checks storage before merging and before deployment dispatch. Backup staging and deployment require at least 1 GB free on `/data` and temporary free space of three database sizes plus 100 MB. Failures stop the release with a reason.

SQLite connections in document, research and research-sync paths close explicitly. Writers use the standard 1,000-page automatic checkpoint interval and a 64 MiB retained journal limit. The latter is not a hard bound during an active transaction. Every five minutes the monitor attempts a passive checkpoint; a truncate checkpoint is attempted only after all frames were checkpointed, with a one-second SQLite busy timeout. A log remaining above 128 MiB requires investigation of active readers or writers. Never delete SQLite WAL or SHM files to free space.

## Operations and Linear

The dispatcher integration `integrations/deck-lab/storage.py` runs every minute through the `com.sdlc-dispatcher.deck-lab-storage-monitor` login service. DYL-16 is the single storage-health issue; the monitor updates its existing comment and changes it between healthy/Done and Blocked. It records private backup, checkpoint and restore receipts locally. Hourly backups and Linear reporting depend on the Mac mini being online and logged in; Fly automatic growth and GitHub's required predeployment backup do not.

For a failed deployment after a successful merge, move the issue through review to Ready to Deploy again. The controller reuses the recorded approved merge only if the approval revision, PR head, merge SHA and current main still match. It does not merge twice. A changed main requires a fresh review.

Backend maintenance uses the reviewed deployment workflow with temporary repository variables `RELEASE_MAINTENANCE_BASE_SHA` and `RELEASE_MAINTENANCE_HEAD_SHA`, both exact full SHAs. This permits that single reviewed transition while retaining CI, artifact provenance, owner approval, backup and health checks. Remove both variables after rollout. Never use a broad permanent deployment-scope exemption.

## Validation

Tests cover failed uploads, corrupted downloads, changed legacy backups, low capacity, twenty repeated backup/prune cycles, retention pins, connection closure and rollback, isolated restoration, lost job acknowledgements, and deployment retry without a second merge. Production validation additionally requires a real private backup, isolated restore, bounded local retention, successful deployment of the approved commit, and verification of the live volume and growth settings.
