"""Transactional live scanner stability and short-lived duplicate suppression.

Revision ID: c32a91df087e
Revises: b21f8d907c63
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'c32a91df087e'
down_revision = 'b21f8d907c63'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('live_scanner_states',
        sa.Column('session_hash', sa.String(64), sa.ForeignKey('admin_sessions.token_hash', ondelete='CASCADE'), primary_key=True),
        sa.Column('state', postgresql.JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_live_scanner_states_updated_at', 'live_scanner_states', ['updated_at'])


def downgrade():
    op.drop_table('live_scanner_states')
