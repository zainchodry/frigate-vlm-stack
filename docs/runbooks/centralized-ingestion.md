# Centralized ingestion and media archive runbook

The project has an independent PostgreSQL database and one FastAPI service for
Frigate MQTT events and Home Assistant state/event ingestion. The optional
archive worker stores binary media separately under `media_archive` and keeps
searchable metadata, checksums, and copy status in PostgreSQL. PostgreSQL does
not contain the media binaries. Existing Frigate, Home Assistant, Mosquitto,
camera, model, recording, and automation configuration remains owned by the
existing services.

## Storage safety

The current root filesystem is approximately 98% used with about 5.4 GiB free.
The repository's Frigate recording tree is the main consumer at roughly 18 GiB;
the independent media archive is roughly 417 MiB and PostgreSQL is roughly
76 MiB. The only writable filesystem currently available is the root
partition. No recordings, snapshots, databases, or training data were
deleted. Only superseded, untagged, non-running ingestor image layers were
removed after exact image-ID verification.

The following paths are protected training data and must never be deleted,
moved, modified, or overwritten:

* `/home/enigmatix/home-server/frigate/storage/clips/faces/Zain/`
* `/home/enigmatix/home-server/frigate/storage/clips/faces/Mehtab/`
* `/home/enigmatix/home-server/frigate/storage/clips/Zain/`
* `/home/enigmatix/home-server/frigate/storage/clips/mehtab/`
* `/home/enigmatix/home-server/frigate/storage/clips/faces/train/`

Before any future media cleanup, verify these exact paths, create and verify a
separate checksum-backed backup, produce an exact deletion manifest, and obtain
explicit approval. Cleanup must use an allowlist of regular recordings and
snapshots and fail closed if any protected path is selected.

PostgreSQL is bind-mounted at `POSTGRES_DATA_PATH` and is not allocated a large
pre-sized file. The service monitors filesystem free space and database
directory size. The current deployment uses a 2 GiB database soft limit, a
512 MiB media-archive quota, and a 6 GiB filesystem reserve. Readiness becomes
critical before the reserve is breached; the monitor never deletes data
automatically.

For long-term media archival, use a separate filesystem or NAS mount with an
explicit quota. Keep the database and media archival roots separate, reserve
at least 20% free space, and define media retention before adding copy jobs.

## Configuration

1. Copy `.env.example` to `.env`.
2. Generate a strong PostgreSQL password and set `POSTGRES_PASSWORD`.
3. Set `DATABASE_URL` with a URL-encoded password.
4. Create a Home Assistant long-lived access token and set `HA_TOKEN`.
5. Keep `.env` mode 0600 and do not commit it.

The Compose API binding is `127.0.0.1:${API_PORT:-8000}`. PostgreSQL has no
published host port and is reachable only on the Compose network.

## Installation and controlled startup

Inspect first:

```sh
df -h /
docker system df
docker compose config
docker compose ps
```

The first `docker compose config` must be run after `.env` is populated. Review
that the existing `frigate`, `homeassistant`, and `mqtt` blocks and mounts are
unchanged. Then, only after explicit deployment approval:

```sh
docker compose build ingestor
docker compose up -d postgres
docker compose logs --no-log-prefix postgres
docker compose run --rm ingestor alembic upgrade head
docker compose up -d ingestor
```

The normal ingestor entrypoint runs `alembic upgrade head` before starting
Uvicorn. The current production migration is
`0005_face_result_event_history`; it preserves a durable event reference and
event context when a future event-row expiry sets the relational `event_id` to
NULL.

## Operation and health

```sh
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
curl -sS http://127.0.0.1:8000/health/services
docker compose logs -f --no-log-prefix ingestor
```

`/health/services` is the durable service-heartbeat view. It reports MQTT,
Home Assistant, Frigate API/camera quality, media archiving, retention scans,
and the ingestor disk state. A reachable Frigate API with an unusable camera
is reported as `degraded`; it is not treated as healthy.

The MQTT connector uses a stable client ID, persistent QoS 1 subscriptions,
manual acknowledgements, and exponential reconnect delay. A message is
acknowledged only after the envelope and projection commit. Home Assistant
seeds current states through `/api/states`, then subscribes over WebSocket.
Reconnect recovery uses a bounded REST history window with overlap. Because
Home Assistant history cannot recover every automation/service event and may
already have purged data, the checkpoint records incomplete recovery explicitly.

## Backup and restore requirements

Phase-one deployment should not be considered protected until PostgreSQL backup
is configured on a separate filesystem or host. Use a version-compatible
`pg_dump`/`pg_restore` process, verify the dump by listing/restoring it into a
temporary database, and retain multiple dated copies. Do not back up by copying
an active PostgreSQL data directory.

The verified deployment command is:

```sh
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > /tmp/home-server-postgres-YYYYMMDD.dump
docker cp /tmp/home-server-postgres-YYYYMMDD.dump \
  home-server-postgres:/tmp/home-server-postgres-YYYYMMDD.dump
docker compose exec -T postgres pg_restore --list \
  /tmp/home-server-postgres-YYYYMMDD.dump
```

This validates the dump catalog; a full restore drill still requires a separate
temporary PostgreSQL instance or filesystem with enough free space.

## Retention and archive safety

The archive worker assigns a seven-day `retention_until` to new archived
snapshots, recordings, and Home Assistant images. The scheduled retention scan
is enabled but is currently dry-run only (`RETENTION_APPLY=false`) because the
root filesystem is critically full and no deletion was approved. It considers
only assets already marked `archived`, verifies the path is inside
`MEDIA_ARCHIVE_ROOT`, and preserves metadata rows when an operator explicitly
enables expiry. Failed and quota-blocked assets remain visible and retryable;
they are never reported as archived.

Frigate's effective policy is now verified at seven days for alert recordings,
detection recordings, motion recordings, and snapshots. Continuous recording
remains disabled (`0` days) to preserve the existing storage behavior; the
policy therefore retains event/motion recordings rather than creating a new
continuous stream. No direct Frigate media deletion was performed.

The active Frigate configuration explicitly uses software decode
(`hwaccel_args: []`). The prior VAAPI path produced repeated surface-sync and
`hwdownload` failures on the active Intel/Frigate runtime. After a backed-up
supported restart, the camera sustained approximately 5.8–6.1 FPS for five
minutes with excellent quality, zero reconnects, zero stalls, and no matching
FFmpeg/VAAPI errors.

## Runtime ownership

The Snap-backed Docker Compose project owns `home-server-ingestor`,
`home-server-postgres`, and `mqtt`. The Compose `homeassistant` entry is only
`Created`; it must not be started from this project. The live Frigate and Home
Assistant services are owned by the separate system Docker daemon
(`/usr/bin/dockerd`, `/run/containerd`) and use the repository's Frigate/HA
bind mounts. Do not use this Compose file to recreate them. Operate the local
project with:

```sh
docker compose build ingestor
docker compose up -d --no-deps ingestor
docker compose ps -a
```

Do not run `docker compose up` for Frigate or Home Assistant until their actual
owner, configuration path, network, and rollback procedure are identified.

## Troubleshooting

* `postgres` fails healthcheck: inspect `POSTGRES_PASSWORD`, the bind path,
  permissions, and disk free space; do not remove the data directory.
* `ingestor` restarts: inspect migration output and connector exception logs;
  secrets are redacted and should never be pasted into reports.
* MQTT events are delayed: verify the broker is reachable on the Compose
  network and that the stable client ID is not used by another consumer.
* Home Assistant recovery is degraded: inspect the checkpoint warning; the
  system continues live WebSocket ingestion while recording the recovery gap.
* Disk warning/critical: stop adding data, take a backup, and follow the
  separately approved media cleanup process. Never select the protected
  training paths above.

* Camera heartbeat degraded: inspect the active Frigate owner's go2rtc and
  FFmpeg logs, current stats, and hardware device mapping. The verified
  current configuration uses software decode; a supported `POST /api/restart`
  is allowed only after backing up and validating the owner configuration.

## Future cleanup and retention changes

Any cleanup, retention-policy change, quota increase, or archival migration
requires a separate plan. Before changing storage, inspect the exact current
media paths, confirm the protected training trees, create/verify their backup,
show the full proposed deletion manifest, and obtain explicit approval. The
current worker fails closed when the quota or filesystem reserve is reached.

See `docs/runbooks/lead-demo.md` for the repeatable evidence workflow and the
known live-camera limitations.
