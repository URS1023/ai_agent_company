"""增加质检复检、处置与设备维修闭环；保留原判定记录和历史标准。"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_02"
down_revision = "20261020_01"
branch_labels = None
depends_on = None

def upgrade():
    """只新增制造业务关联表，不修改旧检查、样本和判定结果。"""
# 本版本结构快照，后续变更使用新的迁移。
    op.create_table('manufacturing_quality_categories',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('target_type', sa.String(length=20), nullable=False),
    sa.Column('parent_id', sa.String(length=36), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['manufacturing_quality_categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_manufacturing_quality_categories_workspace_id'), 'manufacturing_quality_categories', ['workspace_id'], unique=False)
    op.create_table('manufacturing_category_bindings',
    sa.Column('category_id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=True),
    sa.Column('product_id', sa.String(length=36), nullable=True),
    sa.Column('template_id', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['manufacturing_quality_categories.id'], ),
    sa.ForeignKeyConstraint(['device_id'], ['manufacturing_devices.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['manufacturing_products.id'], ),
    sa.ForeignKeyConstraint(['template_id'], ['manufacturing_inspection_templates.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'device_id'),
    sa.UniqueConstraint('workspace_id', 'product_id'),
    sa.UniqueConstraint('workspace_id', 'template_id')
    )
    op.create_index(op.f('ix_manufacturing_category_bindings_category_id'), 'manufacturing_category_bindings', ['category_id'], unique=False)
    op.create_index(op.f('ix_manufacturing_category_bindings_workspace_id'), 'manufacturing_category_bindings', ['workspace_id'], unique=False)
    op.create_table('manufacturing_quality_cases',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('inspection_id', sa.String(length=36), nullable=False),
    sa.Column('source_run_id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('disposition', sa.String(length=30), nullable=False),
    sa.Column('assignee_id', sa.String(length=36), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('root_cause', sa.Text(), nullable=False),
    sa.Column('corrective_action', sa.Text(), nullable=False),
    sa.Column('performed_by', sa.String(length=36), nullable=True),
    sa.Column('reinspection_id', sa.String(length=36), nullable=True),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('closed_by', sa.String(length=36), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['inspection_id'], ['manufacturing_inspections.id'], ),
    sa.ForeignKeyConstraint(['reinspection_id'], ['manufacturing_inspections.id'], ),
    sa.ForeignKeyConstraint(['source_run_id'], ['manufacturing_inspection_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'source_run_id')
    )
    op.create_index(op.f('ix_manufacturing_quality_cases_inspection_id'), 'manufacturing_quality_cases', ['inspection_id'], unique=False)
    op.create_index(op.f('ix_manufacturing_quality_cases_workspace_id'), 'manufacturing_quality_cases', ['workspace_id'], unique=False)
    op.create_table('manufacturing_reinspections',
    sa.Column('parent_inspection_id', sa.String(length=36), nullable=False),
    sa.Column('source_run_id', sa.String(length=36), nullable=False),
    sa.Column('child_inspection_id', sa.String(length=36), nullable=False),
    sa.Column('root_inspection_id', sa.String(length=36), nullable=False),
    sa.Column('selected_sample_refs', sa.JSON(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['child_inspection_id'], ['manufacturing_inspections.id'], ),
    sa.ForeignKeyConstraint(['parent_inspection_id'], ['manufacturing_inspections.id'], ),
    sa.ForeignKeyConstraint(['root_inspection_id'], ['manufacturing_inspections.id'], ),
    sa.ForeignKeyConstraint(['source_run_id'], ['manufacturing_inspection_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('child_inspection_id')
    )
    op.create_index(op.f('ix_manufacturing_reinspections_child_inspection_id'), 'manufacturing_reinspections', ['child_inspection_id'], unique=False)
    op.create_index(op.f('ix_manufacturing_reinspections_parent_inspection_id'), 'manufacturing_reinspections', ['parent_inspection_id'], unique=False)
    op.create_index(op.f('ix_manufacturing_reinspections_root_inspection_id'), 'manufacturing_reinspections', ['root_inspection_id'], unique=False)
    op.create_index(op.f('ix_manufacturing_reinspections_workspace_id'), 'manufacturing_reinspections', ['workspace_id'], unique=False)
    op.create_table('manufacturing_work_orders',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('incident_id', sa.String(length=36), nullable=True),
    sa.Column('analysis_id', sa.String(length=36), nullable=True),
    sa.Column('source_snapshot', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('assignee_id', sa.String(length=36), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('root_cause', sa.Text(), nullable=False),
    sa.Column('repair_note', sa.Text(), nullable=False),
    sa.Column('repaired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('performed_by', sa.String(length=36), nullable=True),
    sa.Column('verified_by', sa.String(length=36), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verification_snapshot', sa.JSON(), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['analysis_id'], ['manufacturing_analyses.id'], ),
    sa.ForeignKeyConstraint(['device_id'], ['manufacturing_devices.id'], ),
    sa.ForeignKeyConstraint(['incident_id'], ['manufacturing_incidents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('analysis_id'),
    sa.UniqueConstraint('incident_id')
    )
    op.create_index(op.f('ix_manufacturing_work_orders_device_id'), 'manufacturing_work_orders', ['device_id'], unique=False)
    op.create_index(op.f('ix_manufacturing_work_orders_workspace_id'), 'manufacturing_work_orders', ['workspace_id'], unique=False)
    op.create_table('manufacturing_quality_case_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['manufacturing_quality_cases.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_manufacturing_quality_case_events_case_id'), 'manufacturing_quality_case_events', ['case_id'], unique=False)
    op.create_table('manufacturing_work_order_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['manufacturing_work_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_manufacturing_work_order_events_order_id'), 'manufacturing_work_order_events', ['order_id'], unique=False)


def downgrade():
    """回退本版新增结构；执行前须备份本版处置与维修记录。"""
# 本版本结构快照，后续变更使用新的迁移。
    op.drop_index(op.f('ix_manufacturing_work_order_events_order_id'), table_name='manufacturing_work_order_events')
    op.drop_table('manufacturing_work_order_events')
    op.drop_index(op.f('ix_manufacturing_quality_case_events_case_id'), table_name='manufacturing_quality_case_events')
    op.drop_table('manufacturing_quality_case_events')
    op.drop_index(op.f('ix_manufacturing_work_orders_workspace_id'), table_name='manufacturing_work_orders')
    op.drop_index(op.f('ix_manufacturing_work_orders_device_id'), table_name='manufacturing_work_orders')
    op.drop_table('manufacturing_work_orders')
    op.drop_index(op.f('ix_manufacturing_reinspections_workspace_id'), table_name='manufacturing_reinspections')
    op.drop_index(op.f('ix_manufacturing_reinspections_root_inspection_id'), table_name='manufacturing_reinspections')
    op.drop_index(op.f('ix_manufacturing_reinspections_parent_inspection_id'), table_name='manufacturing_reinspections')
    op.drop_index(op.f('ix_manufacturing_reinspections_child_inspection_id'), table_name='manufacturing_reinspections')
    op.drop_table('manufacturing_reinspections')
    op.drop_index(op.f('ix_manufacturing_quality_cases_workspace_id'), table_name='manufacturing_quality_cases')
    op.drop_index(op.f('ix_manufacturing_quality_cases_inspection_id'), table_name='manufacturing_quality_cases')
    op.drop_table('manufacturing_quality_cases')
    op.drop_index(op.f('ix_manufacturing_category_bindings_workspace_id'), table_name='manufacturing_category_bindings')
    op.drop_index(op.f('ix_manufacturing_category_bindings_category_id'), table_name='manufacturing_category_bindings')
    op.drop_table('manufacturing_category_bindings')
    op.drop_index(op.f('ix_manufacturing_quality_categories_workspace_id'), table_name='manufacturing_quality_categories')
    op.drop_table('manufacturing_quality_categories')
