# Centralized ingestion phase one — implementation report

Date: 2026-10-28 (Asia/Karachi)

Status: implementation prepared and offline-validated; live deployment intentionally not performed.

## Scope completed

The repository now contains one FastAPI service, an independent PostgreSQL
schema/migration, and two connectors:

* Frigate MQTT consumes events, reviews, notifications, and availability
  messages using a stable client ID, persistent QoS 1 subscriptions, manual
  acknowledgement, and exponential reconnect delay.
* Home Assistant seeds current state through REST, subscribes to configured
  WebSocket event types, reconnects with exponential backoff, and performs a
  bounded REST state-history recovery after a later disconnect.
* All accepted messages are stored in `ingest_messages` with canonical SHA-256
  payload hashes and unique `(source, idempotency_key)` protection.
* Frigate lifecycle projections upsert by event/review ID while retaining raw
  before/after payloads and source timestamps.
* Home Assistant entities, state changes, automation/service events, and
  connector checkpoints are stored independently of the native HA database.
* A future-ready `media_assets` table records references and retention metadata,
  but phase one performs no media copy, move, or delete operation.
* FastAPI liveness/readiness endpoints expose database and disk status without
  exposing secrets. The API's default application bind is localhost; Compose
  exposes only a host loopback port.

## Database objects

`ingest_messages`, `frigate_events`, `frigate_reviews`,
`home_assistant_entities`, `home_assistant_state_changes`,
`home_assistant_events`, `media_assets`, `connector_checkpoints`, and
`service_heartbeats` are created by migration `0001_initial_ingestion_schema`.
Timestamps use timezone-aware PostgreSQL values. Projection writes and the
envelope insert happen in one transaction; a failed projection leaves the
message uncommitted so the connector can retry.

## Existing-system inspection

The existing Compose file was reviewed before adding anything. Existing
services remain unchanged:

* Frigate uses `./frigate/config:/config` and `./frigate/storage:/media/frigate`.
* Home Assistant uses `./homeassistant/config:/config`.
* Mosquitto uses `./mqtt/config`, `./mqtt/data`, and `./mqtt/log` mounts.
* The new PostgreSQL service uses only `${POSTGRES_DATA_PATH:-./postgres/data}`
  and has no host port.
* The new ingestor mounts only that PostgreSQL path read-only for disk
  monitoring and has no Frigate, model, recording, or media mount.

The current Docker status check showed no running Compose containers. No
container was started, recreated, stopped, or deleted during this work.

## Storage evidence

Read-only inspection reported:

* Root filesystem: 234 GB total, 210 GB used, 12.17 GB available, 95% used.
* Repository: approximately 15 GB, of which `frigate/storage` is approximately
  15 GB.
* `frigate/storage/recordings`: approximately 15 GB (the largest directories
  observed were 8.5 GB and 6.2 GB).
* `frigate/storage/clips`: approximately 65 MB.
* Docker: 22.24 GB images (16.57 GB reported reclaimable), 11.63 GB build cache
  (3.274 GB reported reclaimable), and 900.9 MB local volumes.

No Docker prune, database allocation, media cleanup, or backup operation was
performed. The phase-one monitor warns at 4 GiB/20% free, becomes critical at
2 GiB/10% free, and warns when the database directory reaches 80% of the 2 GiB
soft limit. It never cleans automatically.

## Protected training media

The following exact paths were verified and excluded from all cleanup logic:

* `/home/enigmatix/home-server/frigate/storage/clips/faces/Zain/` — 5 files,
  approximately 732 KiB.
* `/home/enigmatix/home-server/frigate/storage/clips/faces/Mehtab/` — 5 files,
  approximately 1.2 MiB.
* `/home/enigmatix/home-server/frigate/storage/clips/Zain/` — 103 files,
  approximately 11 MiB, including `.training_metadata.json` and `train/`.
* `/home/enigmatix/home-server/frigate/storage/clips/mehtab/` — 161 files,
  approximately 18 MiB, including `.training_metadata.json` and `train/`.
* `/home/enigmatix/home-server/frigate/storage/clips/faces/train/` — 100 files,
  approximately 1.5 MiB, including Zain/Mehtab-labelled training examples.

These paths must never be modified, moved, overwritten, or deleted. A future
media cleanup requires a separate checksum-backed backup, a complete exact
deletion manifest, and explicit approval immediately before deletion.

## Verification performed

All of the following completed without starting the live stack:

* `19 passed` Python unit, connector fixture, schema, API, retry, and ingestion
  tests.
* Python bytecode compilation passed for application, migrations, and tests.
* `docker compose config --quiet` passed with validation-only environment
  values.
* Alembic offline SQL generation passed for migration `0001`.
* `git diff --check` passed.
* The FastAPI application imported successfully.

Live PostgreSQL connectivity, MQTT delivery, Home Assistant WebSocket delivery,
and end-to-end retrieval remain intentionally untested until `.env` is filled
with real credentials and explicit deployment approval is given. The controlled
startup and rollback/troubleshooting procedure is in
[`docs/runbooks/centralized-ingestion.md`](../runbooks/centralized-ingestion.md).
