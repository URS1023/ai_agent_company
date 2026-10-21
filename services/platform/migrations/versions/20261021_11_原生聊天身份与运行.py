"""原生聊天平台映射、幂等运行与动作预约；原生消息正文继续存放在Open WebUI。"""

from alembic import op
import sqlalchemy as sa

revision = '20261021_11'
down_revision = '20261021_10'
branch_labels = None
depends_on = None

# 冻结11结构，不能导入后续业务模型而悄悄改变旧迁移。
COMMON = [('id', 'str', 64, False), ('workspace_id', 'str', 64, False), ('user_id', 'str', 64, False)]
TIMES = [('created_at', 'datetime', None, False), ('updated_at', 'datetime', None, False)]
COLUMNS = {
    'native_chat_identities': COMMON + [
        ('integration_id', 'str', 64, False), ('integration_revision', 'int', None, False),
        ('integration_url', 'str', 500, False), ('native_user_id', 'str', 200, True),
        ('credential_ref', 'str', 64, True), ('status', 'str', 30, False), ('error_code', 'str', 120, True),
        ('revision', 'int', None, False)] + TIMES,
    'native_chat_conversations': COMMON + [
        ('integration_id', 'str', 64, False), ('identity_id', 'str', 64, False),
        ('native_chat_id', 'str', 200, True), ('title', 'str', 240, False), ('creation_key', 'str', 160, True),
        ('request_hash', 'str', 64, False), ('status', 'str', 30, False), ('active_turn_id', 'str', 64, True),
        ('inherited_sources', 'json', None, False), ('revision', 'int', None, False)] + TIMES,
    'native_chat_turns': COMMON + [
        ('integration_id', 'str', 64, False), ('identity_id', 'str', 64, False),
        ('conversation_id', 'str', 64, False), ('native_chat_id', 'str', 200, False),
        ('user_message_id', 'str', 100, False), ('assistant_message_id', 'str', 100, False),
        ('parent_message_id', 'str', 100, True), ('model_id', 'str', 200, False),
        ('idempotency_key', 'str', 160, False), ('request_hash', 'str', 64, False),
        ('request_metadata', 'json', None, False), ('status', 'str', 30, False), ('task_id', 'str', 64, True),
        ('native_task_ids', 'json', None, False), ('sources', 'json', None, False),
        ('error_code', 'str', 120, True), ('error', 'text', None, True), ('revision', 'int', None, False),
        ('deadline_at', 'datetime', None, False)] + TIMES,
    'native_chat_turn_actions': COMMON + [
        ('turn_id', 'str', 64, False), ('kind', 'str', 30, False), ('idempotency_key', 'str', 160, False),
        ('request_hash', 'str', 64, False), ('request_metadata', 'json', None, False),
        ('status', 'str', 30, False), ('result', 'json', None, False)] + TIMES,
}
INDEXES = {
    'native_chat_identities': ['workspace_id', 'user_id', 'integration_id'],
    'native_chat_conversations': ['workspace_id', 'user_id', 'integration_id', 'identity_id'],
    'native_chat_turns': ['workspace_id', 'user_id', 'integration_id', 'identity_id', 'conversation_id'],
    'native_chat_turn_actions': ['workspace_id', 'turn_id'],
}
UNIQUES = {
    'native_chat_identities': [('uq_chat_identity_actor_instance', ['workspace_id', 'user_id', 'integration_id']),
        ('uq_chat_identity_native_user', ['integration_id', 'native_user_id'])],
    'native_chat_conversations': [('uq_chat_conversation_native', ['integration_id', 'native_chat_id']),
        ('uq_chat_conversation_intent', ['workspace_id', 'user_id', 'integration_id', 'creation_key'])],
    'native_chat_turns': [('uq_chat_turn_intent', ['conversation_id', 'idempotency_key']),
        ('uq_chat_turn_native_message', ['identity_id', 'assistant_message_id'])],
    'native_chat_turn_actions': [('uq_chat_turn_action_intent', ['turn_id', 'idempotency_key'])],
}


def definitions():
    metadata = sa.MetaData()
    types = {'str': sa.String, 'text': sa.Text, 'int': sa.Integer, 'json': sa.JSON,
        'datetime': lambda: sa.DateTime(timezone=True)}
    tables = []
    for name, columns in COLUMNS.items():
        table = sa.Table(name, metadata, *[sa.Column(key, types[kind](length) if kind == 'str' else types[kind](),
            primary_key=key == 'id', nullable=nullable, index=key in INDEXES[name])
            for key, kind, length, nullable in columns],
            *[sa.UniqueConstraint(*keys, name=constraint) for constraint, keys in UNIQUES[name]])
        tables.append(table)
    return tables


def signature(type_):
    for cls, label in ((sa.Text, 'text'), (sa.String, 'str'), (sa.Integer, 'int'), (sa.JSON, 'json'), (sa.DateTime, 'datetime')):
        if isinstance(type_, cls):
            return (label, type_.length) if label == 'str' else (label,)
    return (str(type_),)


def upgrade():
    """开发create_all已有同表时严格核对；先检查所有表，陌生结构不做任何DDL。"""
    connection, tables = op.get_bind(), definitions()
    inspector = sa.inspect(connection)
    existing = set(inspector.get_table_names())
    for table in tables:
        if table.name not in existing:
            continue
        columns = {column['name']: column for column in inspector.get_columns(table.name)}
        if set(columns) != set(table.c.keys()) or any(signature(columns[c.name]['type']) != signature(c.type)
                or bool(columns[c.name]['nullable']) != c.nullable for c in table.c):
            raise RuntimeError('native_chat_schema_mismatch:' + table.name)
        if inspector.get_pk_constraint(table.name).get('constrained_columns') != ['id']:
            raise RuntimeError('native_chat_primary_key_mismatch:' + table.name)
        indices = {item['name']: (tuple(item['column_names']), bool(item['unique'])) for item in inspector.get_indexes(table.name)}
        expected = {item.name: (tuple(c.name for c in item.columns), item.unique) for item in table.indexes}
        uniques = {item['name']: tuple(item['column_names']) for item in inspector.get_unique_constraints(table.name)}
        if (indices != expected or uniques != {name: tuple(keys) for name, keys in UNIQUES[table.name]}
                or inspector.get_foreign_keys(table.name)):
            raise RuntimeError('native_chat_constraint_mismatch:' + table.name)
    for table in tables:
        if table.name not in existing:
            table.create(connection)


def downgrade():
    raise RuntimeError('native_chat_history_requires_explicit_export_before_downgrade')
