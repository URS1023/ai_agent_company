"""${message}。"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

def upgrade():
    """执行本版本明确记录的结构变化。"""
    ${upgrades if upgrades else "pass"}

def downgrade():
    """按本版本逆序回退；使用前须检查数据保留要求。"""
    ${downgrades if downgrades else "pass"}
