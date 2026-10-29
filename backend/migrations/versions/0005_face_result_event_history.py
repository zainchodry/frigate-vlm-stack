"""preserve event context when recognition event rows expire

Revision ID: 0005_face_result_event_history
Revises: 0004_face_result_metadata_name
Create Date: 2026-10-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_face_result_event_history"
down_revision = "0004_face_result_metadata_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "face_recognition_results",
        sa.Column("event_reference", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "face_recognition_results",
        sa.Column("event_camera", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "face_recognition_results",
        sa.Column("event_label", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "face_recognition_results",
        sa.Column("event_start_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "face_recognition_results",
        sa.Column("event_end_time", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "face_recognition_results",
        sa.Column("event_type", sa.String(length=64), nullable=True),
    )
    op.execute(
        "UPDATE face_recognition_results AS face "
        "SET event_reference = face.event_id, "
        "event_camera = event.camera, event_label = event.label, "
        "event_start_time = event.start_time, event_end_time = event.end_time, "
        "event_type = event.event_type "
        "FROM frigate_events AS event "
        "WHERE face.event_id = event.event_id"
    )
    op.execute(
        "UPDATE face_recognition_results "
        "SET event_reference = event_id "
        "WHERE event_reference IS NULL AND event_id IS NOT NULL"
    )
    op.create_index(
        "ix_face_recognition_results_event_reference",
        "face_recognition_results",
        ["event_reference", "recognized_at"],
    )

    # Replace the original restrictive FK with a preserving relationship. The
    # source ID remains in event_reference when an event row is later removed.
    op.execute(
        "DO $$ DECLARE constraint_name text; BEGIN "
        "SELECT conname INTO constraint_name FROM pg_constraint "
        "WHERE conrelid = 'face_recognition_results'::regclass "
        "AND contype = 'f' AND conkey = ARRAY[ "
        "(SELECT attnum FROM pg_attribute WHERE attrelid = 'face_recognition_results'::regclass "
        "AND attname = 'event_id') ]; "
        "IF constraint_name IS NOT NULL THEN "
        "EXECUTE format('ALTER TABLE face_recognition_results DROP CONSTRAINT %I', constraint_name); "
        "END IF; END $$;"
    )
    op.create_foreign_key(
        "face_recognition_results_event_id_fkey",
        "face_recognition_results",
        "frigate_events",
        ["event_id"],
        ["event_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "face_recognition_results_event_id_fkey",
        "face_recognition_results",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "face_recognition_results_event_id_fkey",
        "face_recognition_results",
        "frigate_events",
        ["event_id"],
        ["event_id"],
    )
    op.drop_index(
        "ix_face_recognition_results_event_reference",
        table_name="face_recognition_results",
    )
    for column in (
        "event_type",
        "event_end_time",
        "event_start_time",
        "event_label",
        "event_camera",
        "event_reference",
    ):
        op.drop_column("face_recognition_results", column)
