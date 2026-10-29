"""avoid SQLAlchemy's reserved metadata attribute name

Revision ID: 0004_face_result_metadata_name
Revises: 0003_face_recognition_results
Create Date: 2026-10-29
"""

from alembic import op


revision = "0004_face_result_metadata_name"
down_revision = "0003_face_recognition_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'face_recognition_results' "
        "AND column_name = 'metadata') THEN "
        "ALTER TABLE face_recognition_results RENAME COLUMN metadata TO result_metadata; "
        "END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'face_recognition_results' "
        "AND column_name = 'result_metadata') THEN "
        "ALTER TABLE face_recognition_results RENAME COLUMN result_metadata TO metadata; "
        "END IF; END $$;"
    )
