"""新增原件到普通原生用户副本的预约表；不修改原生数据库或既有文件。"""

from alembic import op
import sqlalchemy as sa

revision = '20261021_12'
down_revision = '20261021_11'
branch_labels = None
depends_on = None

STRINGS = {'id': 64, 'workspace_id': 64, 'user_id': 64, 'identity_id': 64, 'integration_id': 64,
    'file_id': 36, 'source_sha256': 64, 'source_name': 240, 'source_content_type': 160,
    'request_key': 160, 'request_hash': 64, 'native_file_id': 200, 'status': 30,
    'native_status': 30, 'error_code': 120, 'cleanup_status': 30}
NULLABLE = {'native_file_id', 'native_status', 'error_code', 'cleanup_status'}
INDEXES = ('workspace_id', 'user_id', 'identity_id', 'integration_id', 'file_id')


def upgrade():
    op.create_table('native_chat_files',
        *[sa.Column(name, sa.String(length), primary_key=name == 'id', nullable=name in NULLABLE)
          for name, length in STRINGS.items()],
        *[sa.Column(name, sa.Integer(), nullable=False) for name in ('source_revision', 'source_size', 'generation', 'revision')],
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=False) for name in ('deadline_at', 'created_at', 'updated_at')],
        sa.UniqueConstraint('identity_id', 'request_key', name='uq_native_chat_file_intent'),
        sa.UniqueConstraint('identity_id', 'file_id', 'source_revision', 'source_sha256', 'generation', name='uq_native_chat_file_generation'),
        sa.UniqueConstraint('identity_id', 'native_file_id', name='uq_native_chat_file_copy'))
    for name in INDEXES:
        op.create_index('ix_native_chat_files_' + name, 'native_chat_files', [name])


def downgrade():
    for name in reversed(INDEXES):
        op.drop_index('ix_native_chat_files_' + name, table_name='native_chat_files')
    op.drop_table('native_chat_files')
