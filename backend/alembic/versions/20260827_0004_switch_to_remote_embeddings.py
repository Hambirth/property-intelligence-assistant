"""Switch derived vectors to the reviewed free remote embedding model.

Revision ID: 20260827_0004
Revises: 20260825_0003
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from pgvector.sqlalchemy import VECTOR

from alembic import op

revision: str = "20260827_0004"
down_revision: str | None = "20260825_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Chunks are deterministic derived data. They must be regenerated with the
    # new model; keeping vectors from two embedding spaces would corrupt search.
    op.execute("DELETE FROM document_chunks")
    op.alter_column(
        "document_chunks",
        "embedding",
        type_=VECTOR(1024),
        postgresql_using="embedding::vector(1024)",
    )


def downgrade() -> None:
    op.execute("DELETE FROM document_chunks")
    op.alter_column(
        "document_chunks",
        "embedding",
        type_=VECTOR(384),
        postgresql_using="embedding::vector(384)",
    )
