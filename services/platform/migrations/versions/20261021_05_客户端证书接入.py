"""新增客户端公钥证书、重放账本及短令牌关联，保留现有身份数据。"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_05"
down_revision = "20261021_04"
branch_labels = None
depends_on = None

def upgrade():
    """只保存公钥证书和断言摘要，调用方私钥不进入平台。"""
    op.create_table('service_client_certificates',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('identity_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('certificate_pem', sa.Text(), nullable=False),
    sa.Column('fingerprint_sha256', sa.String(length=64), nullable=False),
    sa.Column('subject_text', sa.Text(), nullable=False),
    sa.Column('issuer_text', sa.Text(), nullable=False),
    sa.Column('serial_number', sa.String(length=80), nullable=False),
    sa.Column('algorithm', sa.String(length=12), nullable=False),
    sa.Column('not_before', sa.String(length=40), nullable=False),
    sa.Column('not_after', sa.String(length=40), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('revoked_at', sa.String(length=40), nullable=True),
    sa.Column('revocation_reason', sa.String(length=500), nullable=False),
    sa.Column('replaced_by_id', sa.String(length=36), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['identity_id'], ['service_identities.id'], ),
    sa.ForeignKeyConstraint(['replaced_by_id'], ['service_client_certificates.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identity_id', 'fingerprint_sha256', name='uq_client_certificate_fingerprint')
    )
    op.create_index(op.f('ix_service_client_certificates_identity_id'), 'service_client_certificates', ['identity_id'], unique=False)
    op.create_index(op.f('ix_service_client_certificates_workspace_id'), 'service_client_certificates', ['workspace_id'], unique=False)
    op.create_table('service_client_assertions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('identity_id', sa.String(length=36), nullable=False),
    sa.Column('certificate_id', sa.String(length=36), nullable=False),
    sa.Column('jti_sha256', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.String(length=40), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['certificate_id'], ['service_client_certificates.id'], ),
    sa.ForeignKeyConstraint(['identity_id'], ['service_identities.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identity_id', 'jti_sha256', name='uq_client_assertion_replay')
    )
    op.create_index(op.f('ix_service_client_assertions_identity_id'), 'service_client_assertions', ['identity_id'], unique=False)
    op.create_index(op.f('ix_service_client_assertions_workspace_id'), 'service_client_assertions', ['workspace_id'], unique=False)
    op.create_table('service_certificate_token_bindings',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('certificate_id', sa.String(length=36), nullable=False),
    sa.Column('service_access_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['certificate_id'], ['service_client_certificates.id'], ),
    sa.ForeignKeyConstraint(['service_access_id'], ['service_access_tokens.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('service_access_id')
    )
    op.create_index(op.f('ix_service_certificate_token_bindings_certificate_id'), 'service_certificate_token_bindings', ['certificate_id'], unique=False)
    op.create_index(op.f('ix_service_certificate_token_bindings_workspace_id'), 'service_certificate_token_bindings', ['workspace_id'], unique=False)

def downgrade():
    """须先撤销本版接入令牌并备份证书配置，再回退关联表。"""
    op.drop_index(op.f('ix_service_certificate_token_bindings_workspace_id'), table_name='service_certificate_token_bindings')
    op.drop_index(op.f('ix_service_certificate_token_bindings_certificate_id'), table_name='service_certificate_token_bindings')
    op.drop_table('service_certificate_token_bindings')
    op.drop_index(op.f('ix_service_client_assertions_workspace_id'), table_name='service_client_assertions')
    op.drop_index(op.f('ix_service_client_assertions_identity_id'), table_name='service_client_assertions')
    op.drop_table('service_client_assertions')
    op.drop_index(op.f('ix_service_client_certificates_workspace_id'), table_name='service_client_certificates')
    op.drop_index(op.f('ix_service_client_certificates_identity_id'), table_name='service_client_certificates')
    op.drop_table('service_client_certificates')
