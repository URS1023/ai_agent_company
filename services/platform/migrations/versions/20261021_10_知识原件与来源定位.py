"""知识文档增加原件固定来源，切块保留页/表/段落定位；旧粘贴数据保持不变。"""

from alembic import op
import sqlalchemy as sa

revision = '20261021_10'
down_revision = '20261021_09'
branch_labels = None
depends_on = None

ADDITIONS = {
    'kb_documents': {'file_id': sa.String(36), 'source_sha256': sa.String(64),
        'source_revision': sa.Integer(), 'parser_metadata': sa.JSON()},
    'kb_chunks': {'source_location': sa.JSON()},
}
OLD_COLUMNS = {
    'kb_documents': {'id', 'workspace_id', 'space_id', 'name', 'source', 'char_count',
        'chunk_count', 'status', 'error', 'created_by', 'created_at'},
    'kb_chunks': {'id', 'workspace_id', 'space_id', 'document_id', 'ordinal', 'content', 'vector', 'created_at'},
}


def _signature(type_):
    if isinstance(type_, sa.Text):
        return ('text',)
    if isinstance(type_, sa.String):
        return ('string', type_.length)
    if isinstance(type_, sa.Integer):
        return ('integer',)
    if isinstance(type_, sa.JSON):
        return ('json',)
    if isinstance(type_, sa.DateTime):
        return ('datetime',)
    return (str(type_),)


def definitions():
    """冻结原有两表和10新增列，后续模型变化不得影响此迁移的接管范围。"""
    metadata = sa.MetaData()
    def column(name, type_, **kwargs):
        return sa.Column(name, type_, nullable=False, **kwargs)
    documents = sa.Table('kb_documents', metadata,
        column('id', sa.String(64), primary_key=True), column('workspace_id', sa.String(64), index=True),
        column('space_id', sa.String(64), index=True), column('name', sa.String(240)),
        column('source', sa.String(40)), column('char_count', sa.Integer()), column('chunk_count', sa.Integer()),
        column('status', sa.String(30)), sa.Column('error', sa.Text(), nullable=True),
        column('created_by', sa.String(64)), column('created_at', sa.DateTime(timezone=True)))
    chunks = sa.Table('kb_chunks', metadata,
        column('id', sa.String(64), primary_key=True), column('workspace_id', sa.String(64), index=True),
        column('space_id', sa.String(64), index=True), column('document_id', sa.String(64), index=True),
        column('ordinal', sa.Integer()), column('content', sa.Text()), column('vector', sa.JSON()),
        column('created_at', sa.DateTime(timezone=True)))
    for table in (documents, chunks):
        for name, type_ in ADDITIONS[table.name].items():
            table.append_column(sa.Column(name, type_, nullable=True))
    return (documents, chunks)


def preflight(connection):
    """先核两表全部新列，无陌生列/不兼容新列时才接管；不覆盖已有字段。"""
    inspector = sa.inspect(connection)
    missing = {}
    for definition in definitions():
        table, additions = definition.name, ADDITIONS[definition.name]
        if table not in inspector.get_table_names():
            raise RuntimeError('knowledge_original_table_missing:' + table)
        columns = {column['name']: column for column in inspector.get_columns(table)}
        if set(columns) - OLD_COLUMNS[table] - set(additions) or OLD_COLUMNS[table] - set(columns):
            raise RuntimeError('knowledge_original_schema_mismatch:' + table)
        for name, observed in columns.items():
            expected = definition.c[name]
            if (_signature(observed['type']) != _signature(expected.type)
                    or bool(observed['nullable']) != expected.nullable):
                raise RuntimeError('knowledge_original_column_mismatch:' + name)
        if inspector.get_pk_constraint(table).get('constrained_columns') != ['id']:
            raise RuntimeError('knowledge_original_primary_key_mismatch:' + table)
        observed_indices = {item['name']: (tuple(item['column_names']), bool(item['unique']))
            for item in inspector.get_indexes(table)}
        expected_indices = {item.name: (tuple(column.name for column in item.columns), item.unique)
            for item in definition.indexes}
        if (observed_indices != expected_indices or inspector.get_foreign_keys(table)
                or inspector.get_unique_constraints(table)):
            raise RuntimeError('knowledge_original_constraint_mismatch:' + table)
        missing[table] = set(additions) - set(columns)
    return missing


def upgrade():
    missing = preflight(op.get_bind())
    for table, names in missing.items():
        for name in ADDITIONS[table]:
            if name in names:
                op.add_column(table, sa.Column(name, ADDITIONS[table][name], nullable=True))


def downgrade():
    """仅显式降级移除新增来源列；执行前须备份原件与来源关系。"""
    preflight(op.get_bind())
    for table, additions in reversed(list(ADDITIONS.items())):
        with op.batch_alter_table(table) as batch:
            for name in reversed(list(additions)):
                batch.drop_column(name)
