"""新增只归本人及当前工作区的手工记忆，不迁移或自动采集对话。"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_09"
down_revision = "20261021_08"
branch_labels = None
depends_on = None


def definition():
    """静态冻结09结构，避免后续ORM变化重写历史迁移。"""
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(36), primary_key=True))
    return sa.Table("chat_user_memories", metadata,
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False), sa.Column("type", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False), sa.Column("updated_at", sa.String(40), nullable=False),
        sa.Index("ix_chat_user_memories_scope", "workspace_id", "user_id", "updated_at", "id"))


def signature(kind):
    if isinstance(kind, sa.Text):
        return ("text",)
    if isinstance(kind, sa.String):
        return ("string", kind.length)
    if isinstance(kind, sa.Integer):
        return ("integer",)
    return (str(kind),)


def preflight(connection):
    """仅接纳准确匹配的开发结构，陌生同名表不改动、不覆盖。"""
    inspector = sa.inspect(connection)
    table = definition()
    if table.name not in inspector.get_table_names():
        return False
    expected = {column.name: column for column in table.columns}
    actual = {column["name"]: column for column in inspector.get_columns(table.name)}
    if set(actual) != set(expected):
        raise RuntimeError("memory_migration_columns_mismatch")
    for name, column in actual.items():
        if signature(column["type"]) != signature(expected[name].type) or bool(column["nullable"]) != expected[name].nullable:
            raise RuntimeError("memory_migration_type_mismatch:" + name)
    if inspector.get_pk_constraint(table.name).get("constrained_columns") != ["id"]:
        raise RuntimeError("memory_migration_key_mismatch")
    indices = {item["name"]: (tuple(item["column_names"]), bool(item["unique"])) for item in inspector.get_indexes(table.name)}
    if indices != {"ix_chat_user_memories_scope": (("workspace_id", "user_id", "updated_at", "id"), False)}:
        raise RuntimeError("memory_migration_index_mismatch")
    foreign = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"])) for item in inspector.get_foreign_keys(table.name)}
    if foreign != {(("workspace_id",), "workspaces", ("id",)), (("user_id",), "users", ("id",))} or inspector.get_unique_constraints(table.name):
        raise RuntimeError("memory_migration_foreign_key_mismatch")
    return True


def upgrade():
    if not preflight(op.get_bind()):
        definition().create(op.get_bind())


def downgrade():
    """仅显式降级时删除该表；执行前应备份需要保留的个人记录。"""
    if preflight(op.get_bind()):
        op.drop_table("chat_user_memories")
