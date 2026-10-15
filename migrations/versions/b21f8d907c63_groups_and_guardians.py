"""Groups and multiple guardians; preserve legacy parent information.

Revision ID: b21f8d907c63
Revises: a047eced711a
"""
from alembic import op
import sqlalchemy as sa

revision = 'b21f8d907c63'
down_revision = 'a047eced711a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('groups',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.String(1000)),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('length(trim(name)) > 0', name='group_name_required'))
    for column in ('is_active', 'created_at'):
        op.create_index('ix_groups_'+column, 'groups', [column])
    op.add_column('children', sa.Column('group_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_children_group_id_groups', 'children', 'groups', ['group_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_children_group_id', 'children', ['group_id'])
    # A real editable migration group, only when legacy children exist. No default
    # group is silently assigned to new children by the application.
    op.execute("""INSERT INTO groups(name, description, is_active, created_at)
        SELECT 'Без группы (перенесено)', 'Укажите фактические группы для перенесённых детей.', true, CURRENT_TIMESTAMP
        WHERE EXISTS (SELECT 1 FROM children)""")
    op.execute('UPDATE children SET group_id = (SELECT min(id) FROM groups)')
    op.alter_column('children', 'group_id', nullable=False)
    op.create_table('guardians',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('child_id', sa.Integer(), sa.ForeignKey('children.id', ondelete='CASCADE'), nullable=False),
        sa.Column('relation_type', sa.String(16), nullable=False),
        sa.Column('first_name', sa.String(80)), sa.Column('last_name', sa.String(80)),
        sa.Column('middle_name', sa.String(80)), sa.Column('phone', sa.String(30)),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("relation_type IN ('father','mother','grandmother','grandfather','guardian','other')", name='guardian_relation'))
    for column in ('child_id', 'created_at'):
        op.create_index('ix_guardians_'+column, 'guardians', [column])
    op.create_index('uq_guardians_primary_child', 'guardians', ['child_id'], unique=True, postgresql_where=sa.text('is_primary'))
    op.execute("""INSERT INTO guardians(child_id, relation_type, first_name, last_name, middle_name, phone, is_primary, created_at)
        SELECT id, 'other', parent_first_name, parent_last_name, parent_middle_name, parent_phone, true, created_at
        FROM children WHERE coalesce(parent_first_name,'') <> '' OR coalesce(parent_last_name,'') <> ''
            OR coalesce(parent_middle_name,'') <> '' OR coalesce(parent_phone,'') <> ''""")
    for name in ('parent_first_name','parent_last_name','parent_middle_name','parent_phone'):
        op.drop_column('children', name)


def downgrade():
    # Prevent destructive downgrade when multiple guardians could be lost.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT child_id FROM guardians GROUP BY child_id HAVING count(*)>1)
        THEN RAISE EXCEPTION 'Export multiple guardians before downgrade'; END IF; END $$""")
    for name in ('parent_first_name','parent_last_name','parent_middle_name','parent_phone'):
        op.add_column('children', sa.Column(name, sa.String(30 if name=='parent_phone' else 80)))
    op.execute('''UPDATE children c SET parent_first_name=g.first_name, parent_last_name=g.last_name,
        parent_middle_name=g.middle_name, parent_phone=g.phone FROM guardians g WHERE g.child_id=c.id''')
    op.drop_table('guardians')
    op.drop_column('children', 'group_id')
    op.drop_table('groups')
