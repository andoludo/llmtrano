"""phase 2 scenarios: runs.scenario and house_briefs.scenario (baseline or renovation)

Revision ID: a3f2c9d1e7b4
Revises: dd2f8b16e186
Create Date: 2026-10-28 22:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f2c9d1e7b4"
down_revision: str | Sequence[str] | None = "dd2f8b16e186"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "runs", sa.Column("scenario", sa.String(length=16), server_default="baseline", nullable=False)
    )
    op.add_column(
        "house_briefs",
        sa.Column("scenario", sa.String(length=16), server_default="baseline", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("house_briefs", "scenario")
    op.drop_column("runs", "scenario")
