"""Store unknown-face rejection reasons without guessing historical reasons."""
from alembic import op
import sqlalchemy as sa

revision = '8c14297ad613'
down_revision = '70b834c912af'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('unknown_faces', sa.Column('reason', sa.String(24), nullable=False, server_default='unspecified'))
    op.create_check_constraint(op.f('ck_unknown_faces_valid_reason'), 'unknown_faces',
                               "reason IN ('not_found', 'low_confidence', 'unspecified')")


def downgrade():
    op.drop_constraint(op.f('ck_unknown_faces_valid_reason'), 'unknown_faces', type_='check')
    op.drop_column('unknown_faces', 'reason')
