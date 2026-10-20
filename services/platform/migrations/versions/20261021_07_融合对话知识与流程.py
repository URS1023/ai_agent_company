"""新增融合知识、Agent和流程六表；兼容经精确结构核验的本轮开发建表。"""

from alembic import op
import sqlalchemy as sa

revision = '20261021_07'
down_revision = '20261021_06'
branch_labels = None
depends_on = None


def definitions():
    """固定本版结构，不导入会随业务迭代变化的运行模型。"""
    metadata = sa.MetaData()
    def column(name, type_, nullable=False, **kwargs):
        return sa.Column(name, type_, nullable=nullable, **kwargs)
    def identity():
        return [column('id', sa.String(64), primary_key=True), column('workspace_id', sa.String(64), index=True)]
    def created():
        return [column('created_by', sa.String(64)), column('created_at', sa.DateTime(timezone=True))]
    sa.Table('kb_spaces', metadata, *identity(), column('name', sa.String(160)),
        column('description', sa.Text()), *created(), column('updated_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('workspace_id', 'name', name='uq_kb_space_name'))
    sa.Table('kb_documents', metadata, *identity(), column('space_id', sa.String(64), index=True),
        column('name', sa.String(240)), column('source', sa.String(40)), column('char_count', sa.Integer()),
        column('chunk_count', sa.Integer()), column('status', sa.String(30)), column('error', sa.Text(), nullable=True),
        *created())
    sa.Table('kb_chunks', metadata, *identity(), column('space_id', sa.String(64), index=True),
        column('document_id', sa.String(64), index=True), column('ordinal', sa.Integer()),
        column('content', sa.Text()), column('vector', sa.JSON()), column('created_at', sa.DateTime(timezone=True)))
    sa.Table('flow_workflows', metadata, *identity(), column('name', sa.String(160)),
        column('description', sa.Text()), column('definition', sa.JSON()), column('enabled', sa.Boolean()),
        column('revision', sa.Integer()), *created(), column('updated_at', sa.DateTime(timezone=True)))
    sa.Table('flow_runs', metadata, *identity(), column('workflow_id', sa.String(64), index=True),
        column('status', sa.String(30)), column('input', sa.Text()), column('steps', sa.JSON()),
        column('output', sa.Text()), column('error', sa.Text(), nullable=True),
        column('knowledge_sources', sa.JSON(), nullable=True), column('created_by', sa.String(64)),
        column('started_at', sa.DateTime(timezone=True)), column('finished_at', sa.DateTime(timezone=True), nullable=True))
    sa.Table('agent_apps', metadata, *identity(), column('name', sa.String(160)),
        column('description', sa.Text()), column('prompt', sa.Text()), column('space_ids', sa.JSON()),
        column('top_k', sa.Integer()), column('max_tokens', sa.Integer()), column('temperature', sa.Double()),
        column('enabled', sa.Boolean()), column('revision', sa.Integer()), *created(),
        column('updated_at', sa.DateTime(timezone=True)))
    return list(metadata.sorted_tables)


def type_signature(type_):
    """比较数据库反射能稳定保留的类型；SQLite不保留DateTime的timezone标记。"""
    if isinstance(type_, sa.Text):
        return ('text',)
    if isinstance(type_, sa.String):
        return ('string', type_.length)
    for name, class_ in [('json', sa.JSON), ('datetime', sa.DateTime), ('boolean', sa.Boolean), ('double', sa.Double),
            ('float', sa.Float), ('integer', sa.Integer)]:
        if isinstance(type_, class_):
            return (name,)
    return (str(type_),)


def verify_existing(inspector, table):
    """仅接受本次create_all生成的已知结构；陌生列、类型或键不静默接管。"""
    existing = {column['name']: column for column in inspector.get_columns(table.name)}
    expected = {column.name: column for column in table.columns}
    optional = {'knowledge_sources'} if table.name == 'flow_runs' else set()
    if set(existing) - set(expected) or set(expected) - set(existing) - optional:
        raise RuntimeError('fusion_migration_schema_mismatch:' + table.name)
    for name, observed in existing.items():
        wanted = expected[name]
        if (type_signature(observed['type']) != type_signature(wanted.type)
                or bool(observed['nullable']) != wanted.nullable):
            raise RuntimeError('fusion_migration_column_mismatch:' + table.name + '.' + name)
    if inspector.get_pk_constraint(table.name).get('constrained_columns') != ['id']:
        raise RuntimeError('fusion_migration_primary_key_mismatch:' + table.name)
    indices = {item['name']: (tuple(item['column_names']), bool(item['unique'])) for item in inspector.get_indexes(table.name)}
    expected_indices = {index.name: (tuple(column.name for column in index.columns), index.unique) for index in table.indexes}
    if indices != expected_indices:
        raise RuntimeError('fusion_migration_index_mismatch:' + table.name)
    unique = {tuple(item['column_names']) for item in inspector.get_unique_constraints(table.name)}
    expected_unique = {tuple(column.name for column in constraint.columns) for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)}
    if unique != expected_unique or inspector.get_foreign_keys(table.name):
        raise RuntimeError('fusion_migration_constraint_mismatch:' + table.name)
    return set(expected) - set(existing)


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = definitions()
    present = set(inspector.get_table_names())
    # 全组检查先于任何DDL，避免发现第六张表陌生时前五张已被修改。
    missing_columns = {table.name: verify_existing(inspector, table) for table in tables if table.name in present}
    for table in tables:
        if table.name not in present:
            op.create_table(table.name, *[sa.Column(column.name, column.type,
                    nullable=column.nullable, primary_key=column.primary_key) for column in table.columns],
                *[sa.UniqueConstraint(*[column.name for column in item.columns], name=item.name)
                    for item in table.constraints if isinstance(item, sa.UniqueConstraint)])
            for index in sorted(table.indexes, key=lambda item: item.name):
                op.create_index(index.name, table.name, [column.name for column in index.columns], unique=index.unique)
        elif missing_columns[table.name]:
            # 唯一允许的已有表变更；旧行保留null，不能把当前绑定伪造为历史来源。
            op.add_column(table.name, sa.Column('knowledge_sources', sa.JSON(), nullable=True))


def downgrade():
    """仅删除本版六表；实际降级前必须停用融合入口并独立备份其业务记录。"""
    for table in reversed(definitions()):
        for index in sorted(table.indexes, key=lambda item: item.name):
            op.drop_index(index.name, table_name=table.name)
        op.drop_table(table.name)
