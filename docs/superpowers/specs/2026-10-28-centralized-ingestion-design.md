# Centralized Ingestion Foundation Design

**Date:** 2026-10-28  
**Status:** Approved for implementation planning  
**Scope:** Phase 1 — independent database ingestion and metadata storage

## Goal

Add a durable, independently queryable data plane for Frigate and Home Assistant without changing the existing surveillance or home-automation behavior. The first phase stores Frigate event metadata, Home Assistant state history, and selected Home Assistant events in PostgreSQL. It does not copy, move, delete, or re-encode snapshots or recordings.

## Existing environment and constraints

The repository currently contains one Docker Compose stack with these existing services:

- Frigate, with its configuration mounted at `./frigate/config` and its media mounted at `./frigate/storage:/media/frigate`.
- Home Assistant, with its configuration mounted at `./homeassistant/config`.
- Mosquitto, with configuration, persistence, and logs mounted below `./mqtt`.

The existing Frigate configuration enables MQTT, recording, snapshots, object detection, and face recognition. The trained face data is under `frigate/storage/clips/faces`; this directory and all current media remain outside the phase-one change surface. Home Assistant is configured for the `Asia/Karachi` timezone and exposes its HTTP/API integration. Existing automations consume Frigate MQTT events.

The repository has one initial commit and no backend or PostgreSQL service. At inspection time the Compose project had no running containers, although the Docker daemon was available. The host has 8 CPUs and approximately 31 GiB RAM. The root filesystem is approximately 234 GiB total, 210 GiB used, and 12 GiB available (95% used). The checkout consumes approximately 15 GiB, almost all of it under `frigate/storage/recordings`; individual recording segments are approximately 30 MiB. This makes the root filesystem unsuitable for media duplication and unsuitable for an unbounded database.

The implementation must therefore:

1. Preserve the existing Frigate, Home Assistant, Mosquitto, camera, AI-model, recording, retention, and automation configuration.
2. Avoid deleting data, changing media paths, or recreating existing services unnecessarily.
3. Avoid copying or moving media in phase 1.
4. Monitor database growth and filesystem headroom, warning before storage becomes critical.
5. Keep all credentials and tokens outside tracked files.

## Architecture

### Services

Add two Compose services on the existing `home-server` network:

1. `postgres`: PostgreSQL with its data directory mounted at a clearly visible phase-one path. PostgreSQL is not published on the host; only the ingestor can reach it through the Compose network.
2. `ingestor`: one FastAPI application containing the HTTP API and supervised asynchronous ingestion tasks.

The ingestor process has three independent responsibilities:

- FastAPI endpoints for liveness, readiness, connector status, and read-only query smoke tests.
- A Frigate MQTT connector using the existing Mosquitto service.
- A Home Assistant REST/WebSocket connector using a long-lived token supplied through `.env`.

The database contract is deliberately independent of this process layout. If load or operational requirements later justify separate API and worker containers, the connectors can be moved without changing the schema or source idempotency keys.

### Source data flow

```text
Frigate -> Mosquitto MQTT -> Frigate connector -> transactional PostgreSQL write
Home Assistant REST snapshot -------------------> entity baseline
Home Assistant WebSocket -> HA connector -------> transactional PostgreSQL write
Home Assistant REST history --------------------> bounded reconnect recovery
```

The Frigate connector subscribes at QoS 1 to:

- `frigate/events` for tracked-object lifecycle messages.
- `frigate/reviews` for alert/review lifecycle messages.
- `frigate/tracked_object_update` for face-recognition and description updates.
- `frigate/available` and camera status topics for source availability and camera-role health.

It does not subscribe to binary snapshot topics. Phase 1 stores source paths and URL references where available; it never writes media bytes to PostgreSQL or to a second filesystem.

The Home Assistant connector first reads `/api/states` to establish the current entity baseline. It then creates WebSocket subscriptions for `state_changed`, `automation_triggered`, and `call_service`. The event allowlist is configurable, but the defaults are intentionally limited to the state and automation/service events needed by this phase instead of subscribing to every event on the bus.

Home Assistant does not provide a universal replay cursor for WebSocket events. The connector stores its last committed source timestamp and, after reconnect, requests a bounded history window through the REST history API for known entities. Recovered rows use the same idempotency path as live rows. Recovery is explicitly best-effort: the system records the requested window, the recovered window, the number of entities queried, and any failure or gap. If Home Assistant’s recorder retention is shorter than the outage, or an entity is unavailable from the history endpoint, the missing interval cannot be reconstructed and remains visible in connector status/logs rather than being presented as complete.

## Database design

All source event times and database receipt times use PostgreSQL `timestamptz` and are normalized to UTC. `Asia/Karachi` is retained as the application display timezone only. A source timestamp is never replaced by the local process clock; the process clock is stored separately as `received_at`.

### `ingest_messages`

This is the durable source envelope and idempotency boundary.

- `id` UUID primary key.
- `source` (`frigate_mqtt` or `home_assistant`).
- `source_topic` or WebSocket event type.
- `idempotency_key` text, unique with `source`.
- `source_event_at` nullable `timestamptz`.
- `received_at` `timestamptz` not null.
- `payload` JSONB not null.
- `payload_sha256` text not null.
- `processing_status` (`processed`, `failed`, or `pending`).
- `attempt_count`, `last_attempt_at`, `last_error`.
- `processed_at` nullable `timestamptz`.

Indexes cover `(source, source_event_at)`, `(processing_status, last_attempt_at)`, and the unique idempotency boundary. Exact redelivery is ignored after the first committed insert. A legitimate Frigate update uses a distinct payload-derived key even when its tracked-object ID is unchanged.

### `frigate_events`

One current lifecycle row per Frigate tracked-object event ID.

- `event_id` text primary key.
- Camera, label, sub-label, score, zones, start/end/frame timestamps.
- Current event type and latest source timestamp.
- Snapshot/recording path or URL references, nullable.
- Raw `before` and `after` JSONB for forward compatibility.
- `first_seen_at`, `last_seen_at`, and `ingest_message_id`.

An update or end message upserts this row. The source envelope remains the audit history, so lifecycle changes are not lost while duplicate MQTT deliveries do not create duplicate event rows.

### `home_assistant_entities`

One row per entity ID, seeded by REST and updated on every state change.

- `entity_id` text primary key.
- Domain, friendly name, device/area identifiers when available.
- Current state and attributes JSONB.
- `last_changed`, `last_updated`, and `last_seen_at` source timestamps.

### `home_assistant_state_changes`

Append-only history for `state_changed` events.

- Entity ID and source event time.
- Old and new state values.
- Old and new attributes JSONB.
- `last_changed` and `last_updated` source timestamps.
- Home Assistant context ID, parent context ID, and user ID.
- Unique reference to the originating `ingest_messages` row.

### `home_assistant_events`

Append-only selected event history for automation and service activity.

- Event type and source event time.
- Event data JSONB.
- Context ID, parent context ID, and user ID.
- Unique reference to the originating `ingest_messages` row.

### `media_assets`

Future-compatible metadata only. It contains the source system, event association, media type, source path/URL, optional checksum, storage backend, retention metadata, and availability status. Phase 1 may populate references discovered in metadata, but it never copies media bytes, changes Frigate paths, or deletes source files.

### `connector_checkpoints` and `service_heartbeats`

These tables record last successful source receipt, connector state, reconnect count, retry count, checkpoint timestamps, recovery windows, gap/incomplete-recovery markers, and heartbeat times. They drive readiness checks and operational troubleshooting.

## Reliability and failure handling

### Transaction boundary

Each accepted source message is processed in one database transaction:

1. Insert the source envelope using its unique idempotency key.
2. If it is new, validate and normalize it.
3. Upsert the current Frigate/entity row or append the HA history row.
4. Update the connector checkpoint and heartbeat.
5. Commit.

If any step fails, the transaction rolls back. The source is retried according to its connector policy; malformed messages are retained as failed envelopes with a redacted error message and are not silently dropped.

### MQTT delivery

The MQTT client uses a stable client ID, QoS 1, a persistent session, and acknowledgement only after the database transaction commits. Reconnect uses bounded exponential backoff with jitter. PostgreSQL idempotency makes redelivery safe. The implementation must not include message payloads containing credentials in logs.

### Home Assistant reconnects

The WebSocket connector reconnects with exponential backoff. On a successful reconnect it runs the bounded history recovery procedure described above before marking the connector fully recovered. If the REST recovery fails, the connector remains connected for live events but readiness reports incomplete recovery until the condition is resolved or explicitly acknowledged. The checkpoint and gap record make the limitation queryable.

### Database outages

Transient connection errors are retried with a bounded backoff. MQTT messages remain unacknowledged until their transaction succeeds. Home Assistant events are recovered from the REST history window after reconnect; an in-memory queue is not treated as durable storage. This is an at-least-once design with database idempotency, not an exactly-once transport.

## Disk protection and operational limits

Phase 1 must not allocate a large pre-sized database file or reserve space by copying media. PostgreSQL uses normal growth on a visible data path. The ingestor periodically checks the filesystem containing PostgreSQL and reports:

- `healthy` above 20% free space.
- `warning` at or below 20% free or below 4 GiB free.
- `critical` at or below 10% free or below 2 GiB free.

The lower threshold wins. Readiness becomes degraded at `warning` and blocks new nonessential maintenance work at `critical`; ingestion remains observable and never deletes Frigate, Home Assistant, or media data automatically. The service emits structured warnings with current free bytes and database size. A configurable maximum database directory size is enforced by refusing optional raw-payload backfill/maintenance work before the critical threshold; core live ingestion failures are surfaced rather than causing silent data loss.

The exact limit is an environment setting documented in `.env.example`, with a conservative phase-one default sized for metadata testing rather than production history. A production deployment must move PostgreSQL and media to a dedicated filesystem before long-term retention is enabled. The database and media storage plans remain separate: media archival will require a separate mounted volume, capacity measured from actual recording bitrate, a reserve threshold, and independent retention/cleanup policy.

No phase-one migration may run `rm`, delete old database rows, alter Frigate retention, alter Home Assistant recorder settings, or move files.

## Security

- `.env` is ignored and contains database credentials, the Home Assistant token, MQTT credentials if authentication is later enabled, and local endpoint settings.
- `.env.example` documents variable names and safe non-secret defaults only.
- PostgreSQL has no host port mapping.
- The API binds to `127.0.0.1` by default; changing the bind address is an explicit deployment choice.
- Logs use structured fields but redact token/password keys and never print authorization headers or full connection URLs containing credentials.
- The API exposes read-only health/status and query endpoints in phase 1; write operations remain internal to the connectors.

## Testing and validation

The implementation plan must include these independent gates:

1. Unit tests for Frigate payload normalization, Home Assistant event normalization, timestamp conversion, idempotency-key derivation, redaction, and retry/backoff classification.
2. PostgreSQL integration tests for migrations, unique idempotency constraints, Frigate lifecycle upserts, HA append-only state history, and transactional rollback.
3. MQTT connector tests for QoS/ack ordering, reconnect, duplicate delivery, malformed payload retention, and Frigate `new`/`update`/`end` lifecycle behavior.
4. Home Assistant fixture tests for initial `/api/states`, WebSocket subscription/reconnect, history recovery, incomplete recovery reporting, and entity IDs/timestamps.
5. FastAPI health endpoint tests for liveness, database readiness, connector heartbeats, and disk warning/critical thresholds.
6. Controlled Compose verification using only the new PostgreSQL/ingestor services first, followed by an explicitly reviewed end-to-end test that publishes a simulated Frigate message to the existing broker. Live Home Assistant ingestion remains gated on the token being supplied.
7. Documentation verification for installation, `.env` configuration, migrations, startup/shutdown, backup/restore requirements, health interpretation, recovery limitations, and troubleshooting.

Before any deployment test, capture `git status`, Compose configuration, filesystem usage, and Docker volume locations. Tests must not recreate or alter the existing Frigate/Home Assistant/Mosquitto service definitions beyond adding the new services and network dependencies required for the ingestor.

## Planned phase-one boundaries

Included:

- PostgreSQL service and migrations.
- FastAPI service and read-only operational endpoints.
- Frigate MQTT event/alert/status ingestion.
- Home Assistant baseline, live state/event ingestion, and bounded reconnect recovery.
- Idempotency, transactional writes, retries, logging, health checks, disk warnings, and tests.
- Documentation and a future-compatible media-reference schema.

Explicitly deferred:

- Copying or moving snapshots and recordings.
- Independent media filesystem provisioning.
- Media hashing, deduplication, quota enforcement, and cleanup.
- Database backup jobs and restore automation beyond documenting the required PostgreSQL backup/restore procedure.
- Search UI, dashboard, authentication for external users, and analytics.

These deferred items form phase 2 and must be planned separately after storage capacity and retention requirements are confirmed.
