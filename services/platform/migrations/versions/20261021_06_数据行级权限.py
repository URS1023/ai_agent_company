"""只新增表级行策略及查询授权快照，不改写既有数据源、查询或业务事实。"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_06"
down_revision = "20261021_05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("data_row_policies",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("datasource_id", sa.String(36), nullable=False),
        sa.Column("table_name", sa.String(160), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["datasource_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "datasource_id", "table_name", name="uq_data_row_policy_table"))
    op.create_index("ix_data_row_policies_workspace_id", "data_row_policies", ["workspace_id"])
    op.create_index("ix_data_row_policies_datasource_id", "data_row_policies", ["datasource_id"])
    op.create_table("data_query_access_snapshots",
        sa.Column("query_id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("subject_user_id", sa.String(36), nullable=False),
        sa.Column("used_tables", sa.JSON(), nullable=False),
        sa.Column("field_scope", sa.JSON(), nullable=False),
        sa.Column("row_scope", sa.JSON(), nullable=False),
        sa.Column("policy_versions", sa.JSON(), nullable=False),
        sa.Column("scope_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["query_id"], ["data_queries.id"]),
        sa.PrimaryKeyConstraint("query_id"))
    op.create_index("ix_data_query_access_snapshots_workspace_id", "data_query_access_snapshots", ["workspace_id"])


def downgrade():
    """只撤销本版新增表；降级前须先停用依赖本版行权限的入口并备份授权证据。"""
    op.drop_index("ix_data_query_access_snapshots_workspace_id", table_name="data_query_access_snapshots")
    op.drop_table("data_query_access_snapshots")
    op.drop_index("ix_data_row_policies_datasource_id", table_name="data_row_policies")
    op.drop_index("ix_data_row_policies_workspace_id", table_name="data_row_policies")
    op.drop_table("data_row_policies")
