"""初始化平台身份、制造、数据和报表结构。"""
from alembic import op
import sqlalchemy as sa


revision = '20261020_01'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    """执行本版本明确记录的结构变化。"""
    # 初始结构快照，后续变更另建迁移，不修改本版本。
    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=160), nullable=False),
    sa.Column('resource_type', sa.String(length=100), nullable=False),
    sa.Column('resource_id', sa.String(length=100), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_events_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('data_sources',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=False),
    sa.Column('managed_filename', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('schema_snapshot', sa.JSON(), nullable=False),
    sa.Column('max_rows', sa.Integer(), nullable=False),
    sa.Column('connection_config', sa.JSON(), nullable=False),
    sa.Column('credential_ref', sa.String(length=36), nullable=True),
    sa.Column('target_approval', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('data_sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_sources_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_devices',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('device_type', sa.String(length=100), nullable=False),
    sa.Column('location', sa.String(length=160), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_devices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_devices_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_inspection_templates',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('target_type', sa.String(length=20), nullable=False),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_inspection_templates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_inspection_templates_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_products',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('specification', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_products_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_simulation_activations',
    sa.Column('generator_id', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('dataset_id', sa.String(length=36), nullable=True),
    sa.Column('sample_count', sa.Integer(), nullable=False),
    sa.Column('device_mapping', sa.JSON(), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_simulation_activations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_simulation_activations_generator_id'), ['generator_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_simulation_activations_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('native_callback_receipts',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('integration_id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=64), nullable=False),
    sa.Column('event_id', sa.String(length=160), nullable=False),
    sa.Column('body_hash', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('disposition', sa.String(length=30), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('integration_id', 'event_id', name='uq_native_callback_event')
    )
    with op.batch_alter_table('native_callback_receipts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_native_callback_receipts_integration_id'), ['integration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_native_callback_receipts_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('native_runs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('workspace_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=False),
    sa.Column('integration_id', sa.String(length=64), nullable=False),
    sa.Column('binding_id', sa.String(length=64), nullable=False),
    sa.Column('binding_kind', sa.String(length=50), nullable=False),
    sa.Column('binding_revision', sa.Integer(), nullable=False),
    sa.Column('release_id', sa.String(length=200), nullable=True),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('native_run_id', sa.String(length=200), nullable=True),
    sa.Column('native_task_id', sa.String(length=200), nullable=True),
    sa.Column('platform_task_id', sa.String(length=64), nullable=True),
    sa.Column('external_user_id', sa.String(length=300), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'binding_id', 'idempotency_key', name='uq_native_run_request')
    )
    with op.batch_alter_table('native_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_native_runs_binding_id'), ['binding_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_native_runs_integration_id'), ['integration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_native_runs_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('organizations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('report_templates',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('layout', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('report_templates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_report_templates_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('reports',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('layout', sa.JSON(), nullable=False),
    sa.Column('template_id', sa.String(length=100), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('published_revision', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reports_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('system_state',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('data_datasets',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('datasource_id', sa.String(length=36), nullable=False),
    sa.Column('table_name', sa.String(length=100), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('columns', sa.JSON(), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('source_kind', sa.String(length=30), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['datasource_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('data_datasets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_datasets_datasource_id'), ['datasource_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_data_datasets_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('data_queries',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('datasource_id', sa.String(length=36), nullable=False),
    sa.Column('sql', sa.Text(), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['datasource_id'], ['data_sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('data_queries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_queries_datasource_id'), ['datasource_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_data_queries_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_analyses',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('request_snapshot', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('provenance', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['manufacturing_devices.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_analyses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_analyses_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_analyses_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_batches',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('product_id', sa.String(length=36), nullable=False),
    sa.Column('external_ref', sa.String(length=300), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['manufacturing_products.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_batches_product_id'), ['product_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_batches_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_points',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('unit', sa.String(length=40), nullable=False),
    sa.Column('datasource_id', sa.String(length=36), nullable=True),
    sa.Column('mapping', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['manufacturing_devices.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_points', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_points_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_points_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_template_revisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('template_id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['template_id'], ['manufacturing_inspection_templates.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('template_id', 'number')
    )
    with op.batch_alter_table('manufacturing_template_revisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_template_revisions_template_id'), ['template_id'], unique=False)

    op.create_table('report_revisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('report_id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('report_id', 'number')
    )
    with op.batch_alter_table('report_revisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_report_revisions_report_id'), ['report_id'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('disabled', sa.Boolean(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('workspaces',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('factory_id', sa.String(length=80), nullable=False),
    sa.Column('industry', sa.String(length=80), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('data_processing_jobs',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('query_id', sa.String(length=36), nullable=False),
    sa.Column('operations', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['query_id'], ['data_queries.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('data_processing_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_processing_jobs_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('files',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('visibility', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_key', sa.String(length=160), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_files_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('login_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('csrf_token', sa.String(length=80), nullable=False),
    sa.Column('expires_at', sa.String(length=40), nullable=False),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_table('manufacturing_alert_rules',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('point_id', sa.String(length=36), nullable=True),
    sa.Column('operator', sa.String(length=10), nullable=False),
    sa.Column('threshold', sa.String(length=120), nullable=False),
    sa.Column('unit', sa.String(length=40), nullable=False),
    sa.Column('dedupe_minutes', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['manufacturing_devices.id'], ),
    sa.ForeignKeyConstraint(['point_id'], ['manufacturing_points.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_alert_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_alert_rules_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_alert_rules_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_inspections',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('target_type', sa.String(length=20), nullable=False),
    sa.Column('template_id', sa.String(length=36), nullable=False),
    sa.Column('template_revision', sa.Integer(), nullable=False),
    sa.Column('standard_snapshot', sa.JSON(), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=True),
    sa.Column('product_id', sa.String(length=36), nullable=True),
    sa.Column('batch_id', sa.String(length=36), nullable=True),
    sa.Column('expected_samples', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('outcome', sa.String(length=30), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['manufacturing_batches.id'], ),
    sa.ForeignKeyConstraint(['device_id'], ['manufacturing_devices.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['manufacturing_products.id'], ),
    sa.ForeignKeyConstraint(['template_id'], ['manufacturing_inspection_templates.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_inspections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_inspections_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('memberships',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role_ids', sa.JSON(), nullable=False),
    sa.Column('department_id', sa.String(length=36), nullable=True),
    sa.Column('disabled', sa.Boolean(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'user_id')
    )
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memberships_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memberships_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('notifications',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('resource_type', sa.String(length=80), nullable=False),
    sa.Column('resource_id', sa.String(length=80), nullable=False),
    sa.Column('read_at', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('resource_grants',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('resource_type', sa.String(length=100), nullable=False),
    sa.Column('resource_id', sa.String(length=80), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('actions', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resource_grants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resource_grants_resource_id'), ['resource_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_resource_grants_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('resource_records',
    sa.Column('id', sa.String(length=80), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('owner_id', sa.String(length=36), nullable=False),
    sa.Column('visibility', sa.String(length=20), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.Column('updated_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resource_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resource_records_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_resource_records_workspace_id'), ['workspace_id'], unique=False)
        batch_op.create_index('ix_resources_workspace_kind', ['workspace_id', 'kind'], unique=False)

    op.create_table('resource_references',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('source_type', sa.String(length=100), nullable=False),
    sa.Column('source_id', sa.String(length=80), nullable=False),
    sa.Column('target_type', sa.String(length=100), nullable=False),
    sa.Column('target_id', sa.String(length=80), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('resource_references', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_resource_references_source_id'), ['source_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_resource_references_target_id'), ['target_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_resource_references_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('roles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('permissions', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'name')
    )
    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_roles_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('secret_entries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('kind', sa.String(length=80), nullable=False),
    sa.Column('encrypted_value', sa.Text(), nullable=False),
    sa.Column('fingerprint', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('deleted', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('secret_entries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_secret_entries_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('tasks',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('engine', sa.String(length=60), nullable=False),
    sa.Column('access_permission', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('inputs', sa.JSON(), nullable=False),
    sa.Column('outputs', sa.JSON(), nullable=False),
    sa.Column('error', sa.JSON(), nullable=True),
    sa.Column('source_run_id', sa.String(length=160), nullable=True),
    sa.Column('idempotency_key', sa.String(length=160), nullable=True),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.Column('updated_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workspace_id', 'user_id', 'idempotency_key')
    )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tasks_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('graph_entities',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('graph_id', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('type', sa.String(length=120), nullable=False),
    sa.Column('properties', sa.JSON(), nullable=False),
    sa.Column('source_refs', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['resource_records.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('graph_entities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_graph_entities_graph_id'), ['graph_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_graph_entities_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_incidents',
    sa.Column('rule_id', sa.String(length=36), nullable=False),
    sa.Column('device_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('first_observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['manufacturing_devices.id'], ),
    sa.ForeignKeyConstraint(['rule_id'], ['manufacturing_alert_rules.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_incidents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_incidents_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_incidents_rule_id'), ['rule_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_incidents_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_inspection_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('inspection_id', sa.String(length=36), nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['inspection_id'], ['manufacturing_inspections.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_inspection_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_inspection_runs_inspection_id'), ['inspection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_inspection_runs_task_id'), ['task_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_inspection_runs_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_reviews',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('inspection_id', sa.String(length=36), nullable=False),
    sa.Column('outcome', sa.String(length=30), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['inspection_id'], ['manufacturing_inspections.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_reviews', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_reviews_inspection_id'), ['inspection_id'], unique=False)

    op.create_table('manufacturing_samples',
    sa.Column('inspection_id', sa.String(length=36), nullable=False),
    sa.Column('external_ref', sa.String(length=160), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['inspection_id'], ['manufacturing_inspections.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('inspection_id', 'external_ref')
    )
    with op.batch_alter_table('manufacturing_samples', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_samples_inspection_id'), ['inspection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_samples_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('secret_rotation_tickets',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('secret_id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=False),
    sa.Column('expected_revision', sa.Integer(), nullable=False),
    sa.Column('encrypted_value', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.String(length=40), nullable=False),
    sa.Column('consumed_at', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['secret_id'], ['secret_entries.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('token_hash')
    )
    with op.batch_alter_table('secret_rotation_tickets', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_secret_rotation_tickets_secret_id'), ['secret_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_secret_rotation_tickets_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('secret_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('secret_id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('encrypted_value', sa.Text(), nullable=False),
    sa.Column('fingerprint', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['secret_id'], ['secret_entries.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('secret_id', 'revision')
    )
    with op.batch_alter_table('secret_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_secret_versions_secret_id'), ['secret_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_secret_versions_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('service_identities',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.String(length=2000), nullable=False),
    sa.Column('caller_kind', sa.String(length=40), nullable=False),
    sa.Column('permissions', sa.JSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    with op.batch_alter_table('service_identities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_service_identities_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('task_events',
    sa.Column('sequence', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('task_id', sa.String(length=36), nullable=False),
    sa.Column('type', sa.String(length=100), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('sequence')
    )
    with op.batch_alter_table('task_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_events_task_id'), ['task_id'], unique=False)

    op.create_table('graph_relations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('graph_id', sa.String(length=80), nullable=False),
    sa.Column('source', sa.String(length=36), nullable=False),
    sa.Column('target', sa.String(length=36), nullable=False),
    sa.Column('type', sa.String(length=120), nullable=False),
    sa.Column('properties', sa.JSON(), nullable=False),
    sa.Column('source_refs', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['graph_id'], ['resource_records.id'], ),
    sa.ForeignKeyConstraint(['source'], ['graph_entities.id'], ),
    sa.ForeignKeyConstraint(['target'], ['graph_entities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('graph_relations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_graph_relations_graph_id'), ['graph_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_graph_relations_workspace_id'), ['workspace_id'], unique=False)

    op.create_table('manufacturing_incident_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('incident_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['manufacturing_incidents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_incident_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_incident_events_incident_id'), ['incident_id'], unique=False)

    op.create_table('manufacturing_sample_evidence',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('inspection_id', sa.String(length=36), nullable=False),
    sa.Column('sample_id', sa.String(length=36), nullable=False),
    sa.Column('observations', sa.JSON(), nullable=False),
    sa.Column('provenance', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['inspection_id'], ['manufacturing_inspections.id'], ),
    sa.ForeignKeyConstraint(['sample_id'], ['manufacturing_samples.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('manufacturing_sample_evidence', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_manufacturing_sample_evidence_inspection_id'), ['inspection_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_manufacturing_sample_evidence_sample_id'), ['sample_id'], unique=False)

    op.create_table('service_access_tokens',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('identity_id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('scopes', sa.JSON(), nullable=False),
    sa.Column('expires_at', sa.String(length=40), nullable=False),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['identity_id'], ['service_identities.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['login_sessions.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id')
    )
    with op.batch_alter_table('service_access_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_service_access_tokens_identity_id'), ['identity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_service_access_tokens_workspace_id'), ['workspace_id'], unique=False)

    # 本版本结构操作结束。

def downgrade():
    """按本版本逆序回退；使用前须检查数据保留要求。"""
    # 初始结构快照，后续变更另建迁移，不修改本版本。
    with op.batch_alter_table('service_access_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_service_access_tokens_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_service_access_tokens_identity_id'))

    op.drop_table('service_access_tokens')
    with op.batch_alter_table('manufacturing_sample_evidence', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_sample_evidence_sample_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_sample_evidence_inspection_id'))

    op.drop_table('manufacturing_sample_evidence')
    with op.batch_alter_table('manufacturing_incident_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_incident_events_incident_id'))

    op.drop_table('manufacturing_incident_events')
    with op.batch_alter_table('graph_relations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_graph_relations_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_graph_relations_graph_id'))

    op.drop_table('graph_relations')
    with op.batch_alter_table('task_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_events_task_id'))

    op.drop_table('task_events')
    with op.batch_alter_table('service_identities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_service_identities_workspace_id'))

    op.drop_table('service_identities')
    with op.batch_alter_table('secret_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_secret_versions_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_secret_versions_secret_id'))

    op.drop_table('secret_versions')
    with op.batch_alter_table('secret_rotation_tickets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_secret_rotation_tickets_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_secret_rotation_tickets_secret_id'))

    op.drop_table('secret_rotation_tickets')
    with op.batch_alter_table('manufacturing_samples', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_samples_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_samples_inspection_id'))

    op.drop_table('manufacturing_samples')
    with op.batch_alter_table('manufacturing_reviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_reviews_inspection_id'))

    op.drop_table('manufacturing_reviews')
    with op.batch_alter_table('manufacturing_inspection_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_inspection_runs_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_inspection_runs_task_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_inspection_runs_inspection_id'))

    op.drop_table('manufacturing_inspection_runs')
    with op.batch_alter_table('manufacturing_incidents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_incidents_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_incidents_rule_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_incidents_device_id'))

    op.drop_table('manufacturing_incidents')
    with op.batch_alter_table('graph_entities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_graph_entities_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_graph_entities_graph_id'))

    op.drop_table('graph_entities')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_workspace_id'))

    op.drop_table('tasks')
    with op.batch_alter_table('secret_entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_secret_entries_workspace_id'))

    op.drop_table('secret_entries')
    with op.batch_alter_table('roles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_roles_workspace_id'))

    op.drop_table('roles')
    with op.batch_alter_table('resource_references', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resource_references_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_resource_references_target_id'))
        batch_op.drop_index(batch_op.f('ix_resource_references_source_id'))

    op.drop_table('resource_references')
    with op.batch_alter_table('resource_records', schema=None) as batch_op:
        batch_op.drop_index('ix_resources_workspace_kind')
        batch_op.drop_index(batch_op.f('ix_resource_records_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_resource_records_kind'))

    op.drop_table('resource_records')
    with op.batch_alter_table('resource_grants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_resource_grants_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_resource_grants_resource_id'))

    op.drop_table('resource_grants')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))

    op.drop_table('notifications')
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memberships_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_memberships_user_id'))

    op.drop_table('memberships')
    with op.batch_alter_table('manufacturing_inspections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_inspections_workspace_id'))

    op.drop_table('manufacturing_inspections')
    with op.batch_alter_table('manufacturing_alert_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_alert_rules_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_alert_rules_device_id'))

    op.drop_table('manufacturing_alert_rules')
    op.drop_table('login_sessions')
    with op.batch_alter_table('files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_files_workspace_id'))

    op.drop_table('files')
    with op.batch_alter_table('data_processing_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_processing_jobs_workspace_id'))

    op.drop_table('data_processing_jobs')
    op.drop_table('workspaces')
    op.drop_table('users')
    with op.batch_alter_table('report_revisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_report_revisions_report_id'))

    op.drop_table('report_revisions')
    with op.batch_alter_table('manufacturing_template_revisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_template_revisions_template_id'))

    op.drop_table('manufacturing_template_revisions')
    with op.batch_alter_table('manufacturing_points', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_points_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_points_device_id'))

    op.drop_table('manufacturing_points')
    with op.batch_alter_table('manufacturing_batches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_batches_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_batches_product_id'))

    op.drop_table('manufacturing_batches')
    with op.batch_alter_table('manufacturing_analyses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_analyses_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_analyses_device_id'))

    op.drop_table('manufacturing_analyses')
    with op.batch_alter_table('data_queries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_queries_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_data_queries_datasource_id'))

    op.drop_table('data_queries')
    with op.batch_alter_table('data_datasets', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_datasets_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_data_datasets_datasource_id'))

    op.drop_table('data_datasets')
    op.drop_table('system_state')
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reports_workspace_id'))

    op.drop_table('reports')
    with op.batch_alter_table('report_templates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_report_templates_workspace_id'))

    op.drop_table('report_templates')
    op.drop_table('organizations')
    with op.batch_alter_table('native_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_native_runs_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_native_runs_integration_id'))
        batch_op.drop_index(batch_op.f('ix_native_runs_binding_id'))

    op.drop_table('native_runs')
    with op.batch_alter_table('native_callback_receipts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_native_callback_receipts_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_native_callback_receipts_integration_id'))

    op.drop_table('native_callback_receipts')
    with op.batch_alter_table('manufacturing_simulation_activations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_simulation_activations_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_manufacturing_simulation_activations_generator_id'))

    op.drop_table('manufacturing_simulation_activations')
    with op.batch_alter_table('manufacturing_products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_products_workspace_id'))

    op.drop_table('manufacturing_products')
    with op.batch_alter_table('manufacturing_inspection_templates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_inspection_templates_workspace_id'))

    op.drop_table('manufacturing_inspection_templates')
    with op.batch_alter_table('manufacturing_devices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_manufacturing_devices_workspace_id'))

    op.drop_table('manufacturing_devices')
    with op.batch_alter_table('data_sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_data_sources_workspace_id'))

    op.drop_table('data_sources')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_events_workspace_id'))
        batch_op.drop_index(batch_op.f('ix_audit_events_user_id'))

    op.drop_table('audit_events')
    # 本版本结构操作结束。
