"""增加流程运行预约、输入指纹和任务投影；旧历史保持未知来源字段为空。"""

from alembic import op
import sqlalchemy as sa

revision = '20261021_08'
down_revision = '20261021_07'
branch_labels = None
depends_on = None

NEW_COLUMNS = ('idempotency_key', 'request_hash', 'workflow_revision', 'definition_snapshot',
    'task_id', 'current_step', 'deadline_at')
KEY_COLUMNS = ('workspace_id', 'created_by', 'workflow_id', 'idempotency_key')
CONSTRAINT = 'uq_flow_run_request'


def definition():
    """固定08结构，不从会继续演进的ORM模型推导生产迁移。"""
    metadata = sa.MetaData()
    def column(name, type_, nullable=False, **kwargs):
        return sa.Column(name, type_, nullable=nullable, **kwargs)
    return sa.Table('flow_runs', metadata,
        column('id', sa.String(64), primary_key=True), column('workspace_id', sa.String(64), index=True),
        column('workflow_id', sa.String(64), index=True), column('status', sa.String(30)),
        column('input', sa.Text()), column('steps', sa.JSON()), column('output', sa.Text()),
        column('error', sa.Text(), nullable=True), column('knowledge_sources', sa.JSON(), nullable=True),
        column('idempotency_key', sa.String(160), nullable=True), column('request_hash', sa.String(64), nullable=True),
        column('workflow_revision', sa.Integer(), nullable=True), column('definition_snapshot', sa.JSON(), nullable=True),
        column('task_id', sa.String(36), nullable=True), column('current_step', sa.Integer(), nullable=True),
        column('deadline_at', sa.DateTime(timezone=True), nullable=True), column('created_by', sa.String(64)),
        column('started_at', sa.DateTime(timezone=True)), column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(*KEY_COLUMNS, name=CONSTRAINT))


def type_signature(type_):
    if isinstance(type_, sa.Text):
        return ('text',)
    if isinstance(type_, sa.String):
        return ('string', type_.length)
    for name, class_ in [('json', sa.JSON), ('datetime', sa.DateTime), ('integer', sa.Integer)]:
        if isinstance(type_, class_):
            return (name,)
    return (str(type_),)


def preflight(connection):
    """先核全部既有列、键和重复编号，再进行任何DDL；不接管陌生开发结构。"""
    inspector = sa.inspect(connection)
    if 'flow_runs' not in inspector.get_table_names():
        raise RuntimeError('flow_reservation_table_missing')
    table = definition()
    expected = {column.name: column for column in table.columns}
    observed = {column['name']: column for column in inspector.get_columns(table.name)}
    missing = set(expected) - set(observed)
    if set(observed) - set(expected) or missing - set(NEW_COLUMNS):
        raise RuntimeError('flow_reservation_schema_mismatch')
    for name, column in observed.items():
        if (type_signature(column['type']) != type_signature(expected[name].type)
                or bool(column['nullable']) != expected[name].nullable):
            raise RuntimeError('flow_reservation_column_mismatch:' + name)
    if inspector.get_pk_constraint(table.name).get('constrained_columns') != ['id']:
        raise RuntimeError('flow_reservation_primary_key_mismatch')
    observed_indices = {item['name']: (tuple(item['column_names']), bool(item['unique']))
        for item in inspector.get_indexes(table.name)}
    expected_indices = {item.name: (tuple(column.name for column in item.columns), item.unique) for item in table.indexes}
    if observed_indices != expected_indices or inspector.get_foreign_keys(table.name):
        raise RuntimeError('flow_reservation_index_or_foreign_key_mismatch')
    uniques = {(item['name'], tuple(item['column_names'])) for item in inspector.get_unique_constraints(table.name)}
    if uniques not in (set(), {(CONSTRAINT, KEY_COLUMNS)}):
        raise RuntimeError('flow_reservation_unique_constraint_mismatch')
    if 'idempotency_key' not in missing:
        duplicate = connection.execute(sa.select(*[table.c[name] for name in KEY_COLUMNS]).where(
            table.c.idempotency_key.is_not(None)).group_by(*[table.c[name] for name in KEY_COLUMNS])
            .having(sa.func.count() > 1).limit(1)).first()
        if duplicate is not None:
            raise RuntimeError('flow_reservation_duplicate_request_key')
    return missing, bool(uniques)


def upgrade():
    missing, has_unique = preflight(op.get_bind())
    if not missing and has_unique:
        return
    expected = definition()
    # SQLite需通过batch保留旧数据并增唯一键，其他方言由Alembic使用原生ALTER。
    with op.batch_alter_table('flow_runs') as batch:
        for name in NEW_COLUMNS:
            if name in missing:
                batch.add_column(sa.Column(name, expected.c[name].type, nullable=True))
        if not has_unique:
            batch.create_unique_constraint(CONSTRAINT, KEY_COLUMNS)


def downgrade():
    """降级前先停入口和备份；不修改旧运行正文及来源证据。"""
    preflight(op.get_bind())
    with op.batch_alter_table('flow_runs') as batch:
        batch.drop_constraint(CONSTRAINT, type_='unique')
        for name in reversed(NEW_COLUMNS):
            batch.drop_column(name)
