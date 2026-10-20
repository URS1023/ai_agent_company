"""新增固定输入、来源和结果的设备能耗分析记录。"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_03"
down_revision = "20261021_02"
branch_labels = None
depends_on = None


def upgrade():
    """计算结果独立保存，不改变原设备、数据查询和维修记录。"""
    op.create_table(
        "manufacturing_energy_analyses",
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("query_id", sa.String(36), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["manufacturing_devices.id"]),
        sa.ForeignKeyConstraint(["query_id"], ["data_queries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for field in ("device_id", "query_id", "task_id", "workspace_id"):
        op.create_index(
            f"ix_manufacturing_energy_analyses_{field}",
            "manufacturing_energy_analyses",
            [field],
        )


def downgrade():
    """回退前应备份该版本生成的能耗分析记录。"""
    for field in ("workspace_id", "task_id", "query_id", "device_id"):
        op.drop_index(
            f"ix_manufacturing_energy_analyses_{field}",
            table_name="manufacturing_energy_analyses",
        )
    op.drop_table("manufacturing_energy_analyses")
