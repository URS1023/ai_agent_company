"""新增系统辅助资源与不可变修订，原业务结构保持不变。"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_04"
down_revision = "20261021_03"
branch_labels = None
depends_on = None

def upgrade():
    """配置资源与修订独立存储，作用域和业务校验由服务层保证。"""
    op.create_table('operation_support_resources',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('scope', sa.String(length=20), nullable=False),
    sa.Column('scope_owner', sa.String(length=80), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.Column('updated_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'kind', 'scope_owner', 'code', name='uq_operation_support_key')
    )
    op.create_index(op.f('ix_operation_support_resources_workspace_id'), 'operation_support_resources', ['workspace_id'], unique=False)
    op.create_index('ix_operation_support_scope', 'operation_support_resources', ['workspace_id', 'kind', 'scope'], unique=False)
    op.create_table('operation_support_revisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('resource_id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['resource_id'], ['operation_support_resources.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('resource_id', 'number', name='uq_operation_support_revision')
    )
    op.create_index(op.f('ix_operation_support_revisions_resource_id'), 'operation_support_revisions', ['resource_id'], unique=False)
    op.create_index(op.f('ix_operation_support_revisions_workspace_id'), 'operation_support_revisions', ['workspace_id'], unique=False)

def downgrade():
    """回退前须备份本版配置及修订记录。"""
    op.drop_index(op.f('ix_operation_support_revisions_resource_id'), table_name='operation_support_revisions')
    op.drop_index(op.f('ix_operation_support_revisions_workspace_id'), table_name='operation_support_revisions')
    op.drop_table('operation_support_revisions')
    op.drop_index(op.f('ix_operation_support_resources_workspace_id'), table_name='operation_support_resources')
    op.drop_index('ix_operation_support_scope', table_name='operation_support_resources')
    op.drop_table('operation_support_resources')
