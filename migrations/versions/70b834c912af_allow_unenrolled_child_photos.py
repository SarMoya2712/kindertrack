"""Allow registration photos before recognition enrollment."""
from alembic import op
import sqlalchemy as sa

revision = '70b834c912af'
down_revision = '4fe4b10b75b2'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('photos', 'embedding', existing_type=sa.LargeBinary(), nullable=True)
    op.alter_column('photos', 'model', existing_type=sa.String(80), nullable=True)


def downgrade():
    # PostgreSQL refuses this change while unenrolled photos exist, preserving data.
    op.alter_column('photos', 'embedding', existing_type=sa.LargeBinary(), nullable=False)
    op.alter_column('photos', 'model', existing_type=sa.String(80), nullable=False)
