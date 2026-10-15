"""Structured audit entities and before/after values, preserving old events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = 'a047eced711a'
down_revision = '8c14297ad613'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('audit', sa.Column('entity_type', sa.String(24), nullable=False, server_default='system'))
    op.add_column('audit', sa.Column('entity_id', sa.Integer(), nullable=True))
    op.add_column('audit', sa.Column('old_value', JSONB(), nullable=True))
    op.add_column('audit', sa.Column('new_value', JSONB(), nullable=True))
    op.execute("UPDATE audit SET entity_type='child', entity_id=child_id WHERE child_id IS NOT NULL")
    op.execute("UPDATE audit SET old_value=details->'old', new_value=details->'new' WHERE action='tariff_changed'")
    op.create_index('ix_audit_action', 'audit', ['action'])
    op.create_index('ix_audit_entity', 'audit', ['entity_type', 'entity_id'])


def downgrade():
    op.drop_index('ix_audit_entity', table_name='audit')
    op.drop_index('ix_audit_action', table_name='audit')
    for column in ('new_value','old_value','entity_id','entity_type'):
        op.drop_column('audit', column)
