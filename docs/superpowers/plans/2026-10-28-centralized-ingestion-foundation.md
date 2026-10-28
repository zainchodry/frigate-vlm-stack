# Centralized Ingestion Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax (- [ ]) for tracking.

**Goal:** Build a local FastAPI ingestion service and PostgreSQL data plane that durably captures Frigate MQTT lifecycle/alert metadata and Home Assistant state/event history without changing the existing surveillance or automation stack.

**Architecture:** Add PostgreSQL and one FastAPI/worker service to the existing Docker network. The FastAPI process owns read-only health/query routes plus supervised Frigate MQTT and Home Assistant REST/WebSocket connectors. Every source message crosses a PostgreSQL idempotency boundary and updates normalized current-state/history rows in one transaction.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, Pydantic Settings, SQLAlchemy 2 async ORM, asyncpg, Alembic, Paho MQTT 2, httpx, websockets, pytest, pytest-asyncio, PostgreSQL 16, Docker Compose.

## Global Constraints

- Preserve existing Frigate, Home Assistant, Mosquitto, camera, AI-model, recording, retention, and automation configuration.
- Do not delete existing data, change media paths, or recreate existing services unnecessarily.
- Do not copy, move, delete, or re-encode snapshots or recordings in phase 1.
- PostgreSQL is never published on the host; the API binds to 127.0.0.1 by default.
- Credentials and Home Assistant tokens live only in ignored .env; commit .env.example with non-secret defaults.
- Store source and receipt timestamps as PostgreSQL timestamptz normalized to UTC; Asia/Karachi is display-only.
- Frigate MQTT uses QoS 1, a stable persistent session, and acknowledgement only after commit.
- Home Assistant recovery is bounded/best-effort, checkpointed, and records gaps instead of claiming complete replay.
- Disk warning is at <=20% free or <4 GiB; critical is at <=10% free or <2 GiB; thresholds are configurable.
- No phase-one process runs a deletion command or automatically removes Frigate, Home Assistant, or media data.
- Protect these trees from all cleanup:
  - /home/enigmatix/home-server/frigate/storage/clips/faces/Zain/
  - /home/enigmatix/home-server/frigate/storage/clips/faces/Mehtab/
  - /home/enigmatix/home-server/frigate/storage/clips/Zain/
  - /home/enigmatix/home-server/frigate/storage/clips/mehtab/
  - /home/enigmatix/home-server/frigate/storage/clips/faces/train/ until verified.
- Before any future cleanup, show protected paths, verified backup manifest, and exact deletion manifest and obtain explicit approval.
- Do not deploy or start containers until the user explicitly authorizes deployment after implementation and test review.

## File Map

Create:

- backend/pyproject.toml and backend/Dockerfile.
- backend/app/config.py, logging.py, db.py, models.py, main.py, and api.py.
- backend/app/domain/types.py, idempotency.py, and normalizers.py.
- backend/app/services/ingestion.py and repositories.py.
- backend/app/connectors/retry.py, mqtt.py, and home_assistant.py.
- backend/app/monitoring/disk.py and state.py.
- backend/alembic.ini, backend/migrations/env.py, and backend/migrations/versions/001_initial_ingestion_schema.py.
- backend/tests/conftest.py plus unit, integration, connector, API, and optional e2e tests.
- backend/tests/api/test_configuration_docs.py — environment/runbook safety checks.
- .env.example, postgres/.gitkeep, and docs/centralized-ingestion.md.

Modify only docker-compose.yml by appending postgres/ingestor blocks and .gitignore for data/cache files. Do not modify Frigate, Home Assistant, MQTT, model, media, or existing database files.

---

### Task 1: Backend skeleton, settings, and redacted logging

**Files:** Create backend/pyproject.toml, backend/app/__init__.py, config.py, logging.py, backend/tests/conftest.py, backend/tests/unit/test_config.py, and test_logging.py.

**Interfaces:** Produce AppSettings, get_settings(), configure_logging(), and redact_mapping().

- [ ] Step 1: Write failing tests.

@@@
def test_settings_load_token_and_redact_summary(monkeypatch):
    monkeypatch.setenv("HA_TOKEN", "secret-token")
    settings = AppSettings(
        database_url="postgresql+asyncpg://u:p@db/app",
        ha_url="http://homeassistant:8123",
        ha_token="secret-token",
    )
    assert settings.ha_token == "secret-token"
    assert settings.safe_summary()["ha_token"] == "[redacted]"
    assert "secret-token" not in repr(settings.safe_summary())

def test_redact_mapping_handles_nested_passwords():
    assert redact_mapping({"password": "p", "nested": {"authorization": "Bearer x"}}) == {
        "password": "[redacted]",
        "nested": {"authorization": "[redacted]"},
    }
@@@

- [ ] Step 2: Run python3 -m pytest backend/tests/unit/test_config.py backend/tests/unit/test_logging.py -q. Expected: FAIL because the package does not exist.
- [ ] Step 3: Implement Pydantic Settings. Require DATABASE_URL; permit empty HA_TOKEN only for tests, while runtime status marks Home Assistant unavailable. Defaults: MQTT_HOST=mqtt, MQTT_PORT=1883, MQTT_CLIENT_ID=home-server-ingestor, HA_URL=http://homeassistant:8123, HA_EVENT_TYPES=state_changed,automation_triggered,call_service, HA_HISTORY_MAX_LOOKBACK_SECONDS=900, API_HOST=127.0.0.1, API_PORT=8000, retry max attempts 8, warning/critical free bytes 4 GiB/2 GiB, and warning/critical ratios 0.20/0.10. Redact keys containing password, token, secret, authorization, or api_key and remove URL user-info before logging.
- [ ] Step 4: Run the focused tests; expected: PASS with no secret in captured logs.
- [ ] Step 5: Commit with git add backend && git commit -m "feat: scaffold ingestion backend configuration".

### Task 2: PostgreSQL service, models, and migration

**Files:** Create backend/app/db.py, models.py, backend/alembic.ini, backend/migrations/env.py, backend/migrations/versions/001_initial_ingestion_schema.py, backend/tests/integration/test_schema.py, and postgres/.gitkeep. Modify docker-compose.yml by appending only postgres and .gitignore for postgres/data/.

**Interfaces:** Produce Base, async_engine, AsyncSessionFactory, get_session(), and models IngestMessage, FrigateEvent, FrigateReview, HomeAssistantEntity, HomeAssistantStateChange, HomeAssistantEvent, MediaAsset, ConnectorCheckpoint, and ServiceHeartbeat.

- [ ] Step 1: Write failing tests asserting all nine tables and unique source/idempotency constraints.

@@@
async def test_initial_schema_has_all_phase_one_tables(db_connection):
    assert await list_tables(db_connection) >= {
        "ingest_messages", "frigate_events", "frigate_reviews",
        "home_assistant_entities", "home_assistant_state_changes",
        "home_assistant_events", "media_assets",
        "connector_checkpoints", "service_heartbeats",
    }

async def test_duplicate_source_key_is_rejected(db_session):
    db_session.add_all([
        IngestMessage(source="frigate_mqtt", idempotency_key="same", payload={}),
        IngestMessage(source="frigate_mqtt", idempotency_key="same", payload={}),
    ])
    with pytest.raises(IntegrityError):
        await db_session.commit()
@@@

- [ ] Step 2: Run the integration test before migration; expected: FAIL. Do not start Docker here without deployment authorization.
- [ ] Step 3: Implement PostgreSQL JSONB/timestamptz/UUID models and the migration. Add foreign keys from normalized rows to ingest_messages; unique source/idempotency; unique envelope references for append-only HA rows; indexes on source/time, processing status, Frigate camera/time, review camera/time, HA entity/time, and checkpoint name.
- [ ] Step 4: Append this service without changing existing Compose blocks:

@@@
  postgres:
    image: postgres:16-alpine
    container_name: home-server-postgres
    restart: unless-stopped
    env_file: .env
    environment:
      POSTGRES_DB: home_events
      POSTGRES_USER: home_events
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}
    volumes:
      - ./postgres/data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 6
    networks:
      - home-server
@@@

- [ ] Step 5: After explicit deployment authorization, run docker compose up -d postgres; load the ignored .env; run alembic upgrade head from the backend project; then run integration tests. Expected: healthy Postgres, idempotent migration, passing schema tests.
- [ ] Step 6: Commit with git add backend/app/db.py backend/app/models.py backend/alembic.ini backend/migrations backend/tests/integration docker-compose.yml .gitignore postgres/.gitkeep && git commit -m "feat: add PostgreSQL ingestion schema".

### Task 3: Canonical source keys, timestamps, and normalizers

**Files:** Create backend/app/domain/types.py, idempotency.py, normalizers.py, backend/tests/unit/test_idempotency.py, and test_normalizers.py.

**Interfaces:** canonical_json(value) -> str; sha256_payload(value) -> str; derive_frigate_key(topic, payload) -> str; derive_home_assistant_key(event_type, event) -> str; parse_source_timestamp(value) -> datetime | None; normalize_frigate(topic, payload, received_at) -> FrigateNormalized; normalize_home_assistant(event_type, event, received_at) -> HomeAssistantNormalized. types.py also defines SourceEnvelope, FrigateEventRecord, FrigateReviewRecord, HAStateRecord, HAEventRecord, and ConnectorStatus.

- [ ] Step 1: Write failing tests that exact Frigate duplicates share keys, new/update/end share event ID but have distinct payload keys, HA state rows use entity ID and source time, and malformed timestamps return None.
- [ ] Step 2: Run the unit tests; expected: FAIL.
- [ ] Step 3: Implement sorted compact canonical JSON and SHA-256. Frigate keys include topic and payload hash. HA state keys use entity ID, new-state last_updated, state, and canonical attributes so live WebSocket and REST history overlap deduplicates; generic events use context ID or payload hash. Convert Unix seconds/ISO-8601 to aware UTC and preserve unknown fields in JSONB.
- [ ] Step 4: Run the unit tests; expected: PASS.
- [ ] Step 5: Commit with git add backend/app/domain backend/tests/unit/test_idempotency.py backend/tests/unit/test_normalizers.py && git commit -m "feat: normalize source events with deterministic keys".

### Task 4: Transactional ingestion and query repositories

**Files:** Create backend/app/services/repositories.py, ingestion.py, backend/tests/unit/test_ingestion_service.py, and backend/tests/integration/test_ingestion_transactions.py.

**Interfaces:** insert_ingest_message(session, envelope) -> InsertResult; upsert_frigate_event; upsert_frigate_review; upsert_home_assistant_entity; append_home_assistant_state; append_home_assistant_event; process_normalized_message(session_factory, envelope, normalized) -> ProcessResult; query functions list_frigate_events, list_frigate_reviews, get_entity, list_entity_states, list_home_assistant_events, ingest_stats. Define InsertResult, ProcessStatus, and ProcessResult here for connector callers.

- [ ] Step 1: Write failing tests for duplicate processing, Frigate lifecycle/review upserts, HA append plus current entity update, rollback, and redacted failed envelopes.
- [ ] Step 2: Run the focused unit/integration tests; expected: FAIL.
- [ ] Step 3: Implement one transaction: insert source envelope with ON CONFLICT DO NOTHING; return DUPLICATE without normalized writes; normalize and write current/append-only rows; set processed_at only after all writes succeed; rollback on transient errors; retain malformed payloads as failed envelopes.
- [ ] Step 4: Bound query limits and filters by time/camera/entity. Run tests; expected: PASS.
- [ ] Step 5: Commit with git add backend/app/services backend/tests/unit/test_ingestion_service.py backend/tests/integration/test_ingestion_transactions.py && git commit -m "feat: persist source events transactionally".

### Task 5: Frigate MQTT connector

**Files:** Create backend/app/connectors/retry.py, mqtt.py, backend/tests/connectors/test_retry.py, and test_mqtt.py.

**Interfaces:** backoff_delays(settings) -> Iterator[float]; MqttConnector.start(); stop() -> Awaitable[None]; status() -> ConnectorStatus; process callback accepts SourceEnvelope and FrigateNormalized.

- [ ] Step 1: Write failing tests for bounded jittered delays, topic subscriptions, reconnect, duplicate delivery, malformed payload persistence, and ack ordering.
- [ ] Step 2: Run connector tests; expected: FAIL.
- [ ] Step 3: Implement Paho MQTT 2 MQTT 3.1.1 with stable client ID, persistent session, manual_ack=True, QoS 1, and subscriptions frigate/events, frigate/reviews, frigate/tracked_object_update, frigate/available, and frigate/+/status/#. Exclude binary snapshot topics. Bridge callbacks to an asyncio queue; call message.ack() only after PROCESSED, DUPLICATE, or durably recorded FAILED result. Leave transient failures unacknowledged. Track status and redact payload/credentials in logs.
- [ ] Step 4: Run connector tests; expected: PASS.
- [ ] Step 5: Commit with git add backend/app/connectors backend/tests/connectors && git commit -m "feat: ingest Frigate MQTT events with manual acknowledgements".

### Task 6: Home Assistant REST/WebSocket connector and recovery

**Files:** Create backend/app/connectors/home_assistant.py, backend/tests/connectors/test_home_assistant.py, and JSON fixtures for states, state_changed, and history.

**Interfaces:** HomeAssistantRestClient.get_states(); get_history(start, end, entity_ids); HomeAssistantWebSocket.connect(); HomeAssistantConnector.start(); stop(); status(); recover_history(checkpoint, now) -> RecoveryResult.

- [ ] Step 1: Write failing fixture tests for /api/states entity seeding, state_changed timestamp storage, WebSocket subscriptions, reconnect, overlap deduplication, 401/403 handling, and incomplete history recovery.
- [ ] Step 2: Run connector tests; expected: FAIL.
- [ ] Step 3: Implement httpx Bearer authentication without logging headers. Seed /api/states into entities without fabricating history. Authenticate WebSocket, subscribe to configured state_changed/automation_triggered/call_service events, and route events through shared ingestion.
- [ ] Step 4: Store checkpoint timestamps. On reconnect, request a bounded history window with overlap, batch entity IDs, process recovered rows through the same keys, and record requested/recovered windows, entity count, result, and gap reason. Keep live ingestion active but readiness degraded on incomplete recovery. Avoid tight retry loops for 401/403.
- [ ] Step 5: Run tests; expected: PASS. Commit with git add backend/app/connectors/home_assistant.py backend/tests/connectors && git commit -m "feat: ingest Home Assistant states and recover gaps".

### Task 7: Disk monitoring, health routes, and lifecycle

**Files:** Create backend/app/monitoring/disk.py, state.py, api.py, main.py, backend/tests/unit/test_disk.py, backend/tests/api/test_health.py, and test_queries.py.

**Interfaces:** DiskMonitor.snapshot(path) -> DiskSnapshot; classify(snapshot) -> DiskStatus; GET /health/live; GET /health/ready; GET /health/connectors; GET /api/ingest/stats; GET /api/frigate/events; GET /api/frigate/reviews; GET /api/home-assistant/entities/{entity_id}; GET /api/home-assistant/states/{entity_id}; GET /api/home-assistant/events.

- [ ] Step 1: Write failing tests for stricter disk thresholds, live/ready separation, connector status, degraded HA recovery, bounded queries, and lifecycle cancellation.
- [ ] Step 2: Run tests; expected: FAIL.
- [ ] Step 3: Implement os.statvfs disk snapshots plus a PostgreSQL database-size query. Log threshold transitions; never prune. At critical, block only optional backfill/maintenance and expose status.
- [ ] Step 4: Implement FastAPI lifespan: create engine, start connectors, update heartbeats, cancel cleanly on shutdown. Live only proves process responsiveness. Ready checks DB, disk, fresh heartbeats, MQTT connection, and HA recovery. Query routes enforce limits and return UTC timestamps.
- [ ] Step 5: Run tests; expected: PASS. Commit with git add backend/app/monitoring backend/app/api.py backend/app/main.py backend/tests/unit/test_disk.py backend/tests/api && git commit -m "feat: add ingestion health and read APIs".

### Task 8: Image, Compose wiring, environment template, and runbook

**Files:** Create backend/Dockerfile, .env.example, docs/centralized-ingestion.md, and backend/tests/api/test_configuration_docs.py. Modify docker-compose.yml by appending ingestor and .gitignore as needed.

- [ ] Step 1: Write tests that .env.example contains replacement values rather than secrets, does not contain existing RTSP credentials, and the runbook contains every protected path and no destructive cleanup command. Expected: FAIL.
- [ ] Step 2: Implement a Python 3.11 slim image and append only:

@@@
  ingestor:
    build:
      context: ./backend
    container_name: home-server-ingestor
    restart: unless-stopped
    env_file: .env
    ports:
      - "127.0.0.1:8080:8000"
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - home-server
@@@

Do not depend on Frigate/Home Assistant in Compose; connectors retry independently. PostgreSQL remains unexposed.
- [ ] Step 3: Document .env variables, read-only preflight, migrations, startup/shutdown of only new services, health/query examples, pg_dump/pg_restore requirements, token setup, MQTT test, recovery limitations, disk thresholds, and the no-deletion/protected-training workflow.
- [ ] Step 4: Run docker compose config --quiet and documentation tests; expected: PASS, localhost-only API, no PostgreSQL host port, and no existing service block changes.
- [ ] Step 5: Commit with git add backend/Dockerfile .env.example docs/centralized-ingestion.md docker-compose.yml .gitignore && git commit -m "feat: wire ingestion service configuration".

### Task 9: Controlled verification and evidence

**Files:** Modify docs/centralized-ingestion.md with actual results only after tests run; create backend/tests/e2e/test_mqtt_ingestion.py only if needed.

- [ ] Step 1: Before deployment, capture git status, docker compose config --services, docker compose config --volumes, df -hT, Docker volume locations, and SHA-256 manifests for all protected trees.
- [ ] Step 2: Run python3 -m pytest backend/tests/unit backend/tests/connectors backend/tests/api -q. Expected: PASS without a HA token or running Frigate.
- [ ] Step 3: Stop and present changed files, preflight evidence, host port, PostgreSQL path, and free space. Obtain explicit deployment confirmation.
- [ ] Step 4: After confirmation, start only postgres and ingestor, run migration and integration tests. Do not start Frigate/Home Assistant.
- [ ] Step 5: If MQTT is required, start only mqtt and publish synthetic new, duplicate new, and end messages. Verify one normalized Frigate event, distinct envelopes, stored end timestamp, and no media change.
- [ ] Step 6: Run HA fixture tests without a token; with a supplied token, capture one permitted live state/event and verify entity ID/source timestamps. Never claim complete replay when recovery reports a gap.
- [ ] Step 7: Recheck df -hT, PostgreSQL data size, protected checksums, and git status. Expected: no protected changes, no media copy/delete, and only test database growth.
- [ ] Step 8: Record actual results and stop only ingestor/postgres and any mqtt service started solely for testing. Do not stop/recreate Frigate or Home Assistant without explicit instruction. Commit verification notes.

## Plan self-review

- Schema coverage: source envelopes, Frigate tracked events/reviews, HA entities/state/events, media references, checkpoints, and heartbeats are assigned to Tasks 2–4.
- Reliability coverage: transactions, idempotency, MQTT manual acknowledgement, retry, reconnect backfill, incomplete recovery, and health are assigned to Tasks 4–7.
- Safety coverage: service isolation, no media operations, disk thresholds, protected training trees, preflight manifests, explicit deployment gate, and no deletion commands are global and assigned to Tasks 8–9.
- Testing coverage: unit, PostgreSQL, MQTT, HA fixture, API, Compose, and controlled end-to-end tests are assigned.
- Documentation coverage: installation, environment, migrations, startup/shutdown, backup/restore requirements, health, recovery limits, troubleshooting, and future cleanup approval are assigned.
- No task modifies Frigate, Home Assistant, Mosquitto, camera, model, recording, retention, or automation configuration.
- All interfaces used by later tasks are defined before they are consumed.
