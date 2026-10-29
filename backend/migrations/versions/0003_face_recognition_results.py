"""store Frigate face recognition projections

Revision ID: 0003_face_recognition_results
Revises: 0002_media_archive
Create Date: 2026-10-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_face_recognition_results"
down_revision = "0002_media_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "face_recognition_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=True),
        sa.Column("identity", sa.String(length=256), nullable=True),
        sa.Column("result_status", sa.String(length=32), nullable=False),
        sa.Column("recognized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("camera", sa.String(length=128), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("media_reference", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("ingest_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["frigate_events.event_id"]),
        sa.ForeignKeyConstraint(["ingest_message_id"], ["ingest_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key", name="uq_face_recognition_results_source_key"),
    )
    op.create_index(
        "ix_face_recognition_results_event",
        "face_recognition_results",
        ["event_id", "recognized_at"],
    )
    op.create_index(
        "ix_face_recognition_results_identity",
        "face_recognition_results",
        ["identity", "recognized_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_face_recognition_results_identity", table_name="face_recognition_results")
    op.drop_index("ix_face_recognition_results_event", table_name="face_recognition_results")
    op.drop_table("face_recognition_results")
