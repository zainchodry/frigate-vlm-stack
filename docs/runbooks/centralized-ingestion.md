# Centralized ingestion phase-one runbook

This phase adds an independent PostgreSQL database and one FastAPI service for
Frigate MQTT events and Home Assistant state/event ingestion. It does not copy,
move, delete, or retain media files. Existing Frigate, Home Assistant,
Mosquitto, camera, model, recording, and automation configuration remains
owned by the existing services.

## Storage safety

The root filesystem was observed at approximately 95% used with about 12 GiB
free. The repository's Frigate recording tree is the main large consumer. No
cleanup was performed during phase one.

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
directory size. Defaults warn at 4 GiB/20% free, become critical at
2 GiB/10% free, and warn when the database reaches 80% of its 2 GiB configured
soft limit. The monitor never deletes data automatically.

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
Uvicorn. The migration is additive and creates only the centralized database
tables.

## Operation and health

```sh
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
docker compose logs -f --no-log-prefix ingestor
```

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

## Phase-two gate

Media archival, retention, quotas, cleanup, and backups require a separate plan.
Before implementation, inspect the exact current media paths, confirm the
protected training trees, create/verify their backup, show the full proposed
deletion manifest, and obtain explicit approval.
