"""add durable media archive state and deduplication

Revision ID: 0002_media_archive
Revises: 0001_initial_ingestion_schema
Create Date: 2026-10-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_media_archive"
down_revision = "0001_initial_ingestion_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS asset_key VARCHAR(512)")
    op.execute("UPDATE media_assets SET asset_key = id::text WHERE asset_key IS NULL")
    op.alter_column("media_assets", "asset_key", nullable=False)
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS entity_id VARCHAR(255)")
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS source_event_at TIMESTAMPTZ")
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS archive_path TEXT")
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS size_bytes INTEGER")
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS mime_type VARCHAR(128)")
    op.execute(
        "ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS copy_status VARCHAR(32) "
        "NOT NULL DEFAULT 'pending'"
    )
    op.execute(
        "ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS attempt_count INTEGER "
        "NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS last_error TEXT")
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ")
    op.execute("ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_media_assets_asset_key') THEN "
        "ALTER TABLE media_assets ADD CONSTRAINT uq_media_assets_asset_key UNIQUE (asset_key); "
        "END IF; END $$;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_media_assets_copy_status "
        "ON media_assets (copy_status, last_attempt_at)"
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_copy_status", table_name="media_assets")
    op.drop_constraint("uq_media_assets_asset_key", "media_assets", type_="unique")
    for name in (
        "completed_at",
        "last_attempt_at",
        "last_error",
        "attempt_count",
        "copy_status",
        "mime_type",
        "size_bytes",
        "archive_path",
        "entity_id",
        "source_event_at",
        "asset_key",
    ):
        op.drop_column("media_assets", name)
