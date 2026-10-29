# Lead demonstration: local PostgreSQL evidence

This procedure proves what is actually persisted. It never inserts synthetic
Frigate events and never touches the protected training-image directories.

## 1. Service and migration health

Run from the repository root:

```sh
docker compose ps -a
docker compose exec -T postgres pg_isready
docker compose exec -T postgres psql -U home_server -d home_server -P pager=off \
  -c "select version_num from alembic_version;"
docker compose exec -T ingestor python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health/live").read().decode())'
```

`/health/ready` may intentionally return `503` when the root filesystem is
below the configured safety reserve. That is a storage safety failure, not a
database connectivity failure.

## 2. Show persisted records

```sh
docker compose exec -T postgres psql -U home_server -d home_server -P pager=off -c \
"select 'HA states' as item, count(*) from home_assistant_state_changes
 union all select 'Frigate events', count(*) from frigate_events
 union all select 'Face results', count(*) from face_recognition_results
 union all select 'Media assets', count(*) from media_assets;"

docker compose exec -T postgres psql -U home_server -d home_server -P pager=off -c \
"select event_id,camera,label,start_time,end_time,event_type
 from frigate_events order by start_time desc nulls last limit 10;"

docker compose exec -T postgres psql -U home_server -d home_server -P pager=off -c \
"select event_id,identity,result_status,recognized_at,camera,match_score,
 source_system,media_reference
 from face_recognition_results order by recognized_at desc limit 10;"

docker compose exec -T postgres psql -U home_server -d home_server -P pager=off -c \
"select entity_id,new_state,source_event_at,last_changed,last_updated
 from home_assistant_state_changes order by source_event_at desc limit 10;"
```

`source_system=frigate_sqlite` means a real event was imported from Frigate's
read-only SQLite history. `source_system=frigate_mqtt` is the live MQTT path.
Do not present the former as a live-camera pass.

To show runtime health without exposing credentials:

```sh
curl -sS http://127.0.0.1:8000/health/services
```

The Frigate heartbeat must be `healthy`, with nonzero camera/process FPS and a
usable connection quality, before calling the live-camera portion a pass.

## 3. Face-recognition test images

The authorized source images are the existing files under:

```text
frigate/storage/clips/faces/Zain/
frigate/storage/clips/faces/Mehtab/
```

Run the real Frigate API test without copying or modifying the images:

```sh
for image in frigate/storage/clips/faces/Zain/*; do
  printf '%s: ' "$image"
  curl --max-time 45 -sS -F "file=@$image" \
    http://127.0.0.1:5000/api/faces/recognize
done
for image in frigate/storage/clips/faces/Mehtab/*; do
  printf '%s: ' "$image"
  curl --max-time 45 -sS -F "file=@$image" \
    http://127.0.0.1:5000/api/faces/recognize
done
```

Record the actual HTTP result for every file. A 500, timeout, or uncertain
identity is a failed test; historical labels in PostgreSQL do not substitute
for a live API result.

## 4. Media evidence

PostgreSQL stores references, sizes, MIME types, and SHA-256 checksums. The
binary files are separate:

```sh
find media_archive -type f -printf '%s %p\n' | sort -n | tail -20
sha256sum media_archive/frigate/<event-id>/recording.mp4
docker compose exec -T postgres psql -U home_server -d home_server -P pager=off -c \
"select event_id,media_type,copy_status,archive_path,size_bytes,sha256,mime_type
 from media_assets where copy_status='archived' order by completed_at desc;"
```

To prove API retrieval, use the local API token without printing it:

```sh
docker compose exec -T ingestor python -c '
import hashlib, os, urllib.request
asset_id = "<asset-uuid>"
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/media/" + asset_id,
    headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
)
data = urllib.request.urlopen(request, timeout=15).read()
print(len(data), hashlib.sha256(data).hexdigest())
'
```

The reported length and checksum must match the SQL row and the host file.

## 5. Idempotency and restart checks

For historical Frigate verification only, rerun the importer. It reads the
source SQLite file read-only and must report `inserted=0` and all rows as
duplicates on a second run:

```sh
# Substitute the PostgreSQL container IP for the internal `postgres` hostname
# when running this host-side script, as described in the main runbook.
DATABASE_URL='<resolved-local-database-url>' \
  venv/bin/python backend/scripts/import_frigate_sqlite.py \
  --sqlite frigate/config/frigate.db
```

Then capture counts, restart only the ingestor, wait for it to reconnect, and
capture the same counts again:

```sh
docker compose restart ingestor
docker compose exec -T postgres psql -U home_server -d home_server -P pager=off -c \
"select count(*) from frigate_events;
 select count(*) from face_recognition_results;
 select count(*) from media_assets;"
```

Home Assistant may add legitimate state rows during recovery. Verify semantic
duplicate protection with:

```sh
docker compose exec -T postgres psql -U home_server -d home_server -P pager=off -c \
"select entity_id,source_event_at,new_state,last_changed,last_updated,count(*)
 from home_assistant_state_changes
 group by entity_id,source_event_at,new_state,last_changed,last_updated
 having count(*) > 1;"
```

Rows from before the semantic-dedupe fix are preserved for auditability; new
same-semantic seed/live/history records must not create additional groups.

## 6. Live Frigate gate

A live PASS requires all of the following actual evidence:

1. Frigate stats show nonzero `camera_fps` and `process_fps`.
2. A real person movement produces a message on `frigate/events`.
3. The ingestor stores that event with `source_system=frigate_mqtt`.
4. A completed event clip or snapshot exists in `media_archive` and matches
   its PostgreSQL checksum.

If Frigate reports `camera_fps=0` or `connection_quality=unusable`, do not
claim the live path passed. The verified active configuration uses software
decode after the VAAPI path produced repeated surface-sync errors. The current
post-fix acceptance evidence is a five-minute window with approximately
5.8–6.1 FPS, excellent quality, zero reconnects, zero stalls, and zero skipped
FPS. Never guess a camera address or print the private endpoint.
