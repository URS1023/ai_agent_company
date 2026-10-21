"""校验后台可信工具绑定；只保存引擎User与Vault引用，不回显签名密钥。"""

from sqlalchemy import select

from agent_platform.core.errors import DomainError
from agent_platform.core.models import Membership, ResourceRecord, User, Workspace
from ..identity.models import ServiceIdentity

FIELDS = {"delegation_service_identity_id", "delegation_jwt_secret_ref"}


def validate_delegation_config(session, actor, data, *, vault):
    """校验创建或合并后的完整配置，不修改记录、不执行网络请求。

    两字段都为null表示显式关闭；配置与清除均需集成管理权限。更新调用方必须先
    与旧记录合并，防止只修改一个引用时将仍有效的另一半误判为缺失。
    """
    if not FIELDS.intersection(data):
        return
    actor.require("m15.admin")
    user_id, ref = (data.get(key) for key in ("delegation_service_identity_id", "delegation_jwt_secret_ref"))
    if user_id is None and ref is None:
        return
    if (not isinstance(user_id, str) or not user_id or not isinstance(ref, str) or not ref):
        raise DomainError(422, "delegation_pair_required", "请同时选择引擎身份与签名凭据",
            {key: "请完整选择或同时清除" for key in FIELDS})
    if data.get("engine") != "openwebui":
        raise DomainError(422, "delegation_engine_invalid", "该委托配置仅适用于Open WebUI", {"engine": "请选择Open WebUI"})
    identity = session.scalar(select(ServiceIdentity).where(ServiceIdentity.user_id == user_id,
        ServiceIdentity.workspace_id == actor.workspace_id, ServiceIdentity.enabled.is_(True)))
    user = session.get(User, user_id)
    workspace = session.get(Workspace, actor.workspace_id)
    member = session.scalar(select(Membership).where(Membership.user_id == user_id,
        Membership.workspace_id == actor.workspace_id, Membership.disabled.is_(False)))
    if (identity is None or user is None or user.disabled or workspace is None or workspace.archived
            or user.organization_id != workspace.organization_id or member is None):
        raise DomainError(422, "delegation_identity_invalid", "请选择当前工作区有效的服务身份",
            {"delegation_service_identity_id": "服务身份不存在或已停用"})
    duplicate = session.scalar(select(ResourceRecord.id).where(ResourceRecord.kind == "integration_instance",
        ResourceRecord.workspace_id == actor.workspace_id, ResourceRecord.deleted.is_(False),
        ResourceRecord.archived.is_(False), ResourceRecord.id != data.get("id", ""),
        ResourceRecord.data["delegation_service_identity_id"].as_string() == user_id).limit(1))
    if duplicate is not None:
        raise DomainError(409, "delegation_identity_already_bound", "该引擎身份已绑定其他实例",
            {"delegation_service_identity_id": "请为不同实例使用独立服务身份"})
    if vault is None:
        raise DomainError(503, "secret_store_unavailable", "凭据存储尚未就绪")
    try:
        secret = vault.get(ref, actor.workspace_id, session=session)
    except DomainError as error:
        if error.status == 404:
            raise DomainError(422, "delegation_secret_invalid", "签名凭据不存在或已撤销",
                {"delegation_jwt_secret_ref": "请选择当前工作区的有效凭据"}) from None
        raise
    if not isinstance(secret, str) or len(secret) < 32:
        raise DomainError(422, "delegation_secret_invalid", "签名凭据长度不足",
            {"delegation_jwt_secret_ref": "请更换至少32个字符的签名凭据"})
