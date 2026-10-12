"""查询准备快照与派发前授权重验，只读平台状态，不连接业务源或重新规划 SQL。"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.authorization import require_permission
from app.common.deps import AuthContext, _workspace_is_active, resolve_service_principal
from app.errors import AuthError, ConflictError, PermissionErrorEx
from app.models import Run, User, Workspace, WorkspaceMember
from app.modules.analytics.connections import source_version
from app.modules.analytics.models import SourceMetadata
from app.modules.analytics.profile_updates import PROFILE_FIELDS
from app.modules.analytics.sources import get_source
from app.modules.runtime.task_control import checkpoint


def _digest(value) -> str:
    """只在内存比较规范快照；哈希及底层连接配置不进入面向用户的错误。"""
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                             default=str).encode("utf-8")).hexdigest()


def _source_contract(source) -> str:
    """派生画像不改变执行授权；连接、凭据引用、业务范围和预算均必须保持一致。"""
    config = {key: value for key, value in (source.connection_config or {}).items() if key not in PROFILE_FIELDS}
    return _digest([source.workspace_id, source.id, source.provider, source.secret_id,
                    source.owner_user_id, source.status, config])


class QueryAuthority:
    """持有准备时不可变的来源基线，每次检查使用独立平台读事务和新权限上下文。"""

    def __init__(self, ctx, source, *, permission="analytics.execute", source_permission="datasources.view", run_id=None):
        self.bind = ctx.db.get_bind()
        self.workspace_id, self.user_id, self.source_id = ctx.workspace_id, ctx.user_id, source.id
        self.version, self.contract = source_version(source), _source_contract(source)
        self.permission, self.source_permission = permission, source_permission
        self.client_id, self.client_scopes = ctx.client_id, ctx.client_scopes
        self.token_digest = sha256(ctx.token.encode()).hexdigest() if ctx.token else None
        self.run_id = run_id
        self.schema_digest = None
        self.tool_name = None
        self.tool_permissions = ()
        self.tool_binding = None

    def bind_tool(self, spec) -> None:
        """记住已解析的工具约束，不能在准备期间悄悄切换连接器发布版本。"""
        self.tool_name = spec.name
        self.tool_permissions = tuple(spec.permissions or ())
        if getattr(spec, "connector_id", None) and getattr(spec, "_tool_version_id", None):
            self.tool_binding = (spec.connector_id, spec.connector_version_id, spec._tool_version_id)

    def _check_tool(self, ctx):
        """只读工具治理/绑定；不解析密钥，不执行 provider，不把角色撤权当连接失败。"""
        if self.tool_name is None:
            return
        for permission in self.tool_permissions:
            require_permission(ctx, permission)
        from app.providers.tool.gateway import ToolGateway
        gateway = ToolGateway(ctx.db)
        if not gateway._method_enabled(ctx.workspace_id, self.tool_name):
            raise PermissionErrorEx("查询工具已停用，不能继续执行")
        if self.tool_binding is not None:
            context = {"db": ctx.db, "workspace_id": ctx.workspace_id, "user_id": ctx.user_id,
                       "auth_context": ctx}
            current = gateway.resolve(ctx.workspace_id, self.tool_name, execution_context=context)
            binding = ((current.connector_id, current.connector_version_id, current._tool_version_id)
                       if current is not None else None)
            if binding != self.tool_binding:
                raise ConflictError("查询工具的发布版本或来源绑定已改变，尚未继续执行", retryable=False)

    def bind_schema(self, tables) -> None:
        """在等待模型前记住结构，不能随后刷新原 ORM 对象使旧新版本被一起覆盖。"""
        digest = _digest(tables)
        if self.schema_digest is not None and self.schema_digest != digest:
            self._changed()
        self.schema_digest = digest

    @staticmethod
    def _changed():
        raise ConflictError("数据源配置或授权结构在查询准备期间已改变，尚未继续执行；请重新取得当前范围后发起查询",
                            source_changed=True, retryable=False, business_query_reexecuted=False)

    def _actor(self, db):
        """重新检查主体与客户端当前上限，不复用请求策略或 Session 的数据范围缓存。"""
        user = db.get(User, self.user_id)
        workspace = db.get(Workspace, self.workspace_id)
        member = db.scalar(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == self.workspace_id, WorkspaceMember.user_id == self.user_id))
        if user is None or user.status != "active" or workspace is None or not _workspace_is_active(db, workspace):
            raise AuthError("查询执行身份或工作区已不可用")
        if member is None:
            raise PermissionErrorEx("用户已不属于当前工作区")
        scopes = self.client_scopes
        if self.client_id:
            from app.modules.system_tools.models import IntegrationAccessToken, IntegrationClient
            client = db.scalar(select(IntegrationClient).where(
                IntegrationClient.client_id == self.client_id, IntegrationClient.workspace_id == self.workspace_id,
                IntegrationClient.created_by == self.user_id, IntegrationClient.enabled.is_(True)))
            if client is None:
                raise AuthError("查询服务客户端已停用或不可用")
            scopes = (scopes or frozenset()).intersection(client.scopes or [])
            token = None
            identified = False
            if self.token_digest:
                identified = True
                token = db.scalar(select(IntegrationAccessToken).where(
                    IntegrationAccessToken.token_digest == self.token_digest))
            elif self.run_id:
                run = db.get(Run, self.run_id)
                if run is None or run.workspace_id != self.workspace_id or run.started_by_user_id != self.user_id:
                    raise AuthError("查询运行身份不一致")
                actor = (run.context_snapshot or {}).get("actor") or {}
                if actor.get("client_token_id"):
                    identified = True
                    token = db.get(IntegrationAccessToken, actor["client_token_id"])
            if identified:
                principal = resolve_service_principal(db, token)
                if (principal.workspace_id != self.workspace_id or principal.user.id != self.user_id
                        or principal.client_id != self.client_id):
                    raise AuthError("查询服务令牌身份不一致")
                scopes = scopes.intersection(principal.client_scopes)
            # 旧工具上下文若没有原令牌标识，只能证明当前客户端状态和原范围上限。
        return AuthContext(db, user, workspace, member.role, "", scopes, self.client_id)

    @contextmanager
    def authorized_source(self, *, check_schema=True):
        """短事务核对已提交状态；不持平台锁等待模型/驱动，不提交调用者的待保存记录。"""
        checkpoint()
        with Session(bind=self.bind, autoflush=False, expire_on_commit=False) as db:
            ctx = self._actor(db)
            require_permission(ctx, self.permission)
            self._check_tool(ctx)
            source = get_source(ctx, self.source_id, self.source_permission, active=True)
            if source_version(source) != self.version or _source_contract(source) != self.contract:
                self._changed()
            if check_schema and self.schema_digest is not None:
                record = db.scalar(select(SourceMetadata).where(
                    SourceMetadata.workspace_id == self.workspace_id, SourceMetadata.datasource_id == self.source_id))
                if (record is None or record.status != "ready" or record.source_version != self.version
                        or _digest(record.tables_json) != self.schema_digest):
                    self._changed()
            checkpoint()
            yield ctx, source

    def check(self) -> None:
        """连接前/派发前调用；已发生的变更明确拒绝，不重取租约或重发查询。"""
        with self.authorized_source():
            pass

    def check_result(self, sql, result) -> None:
        """缓存返回独立检查真实投影，不把旧成功回执当成当前字段权限。"""
        with self.authorized_source() as (ctx, source):
            from app.modules.analytics.snapshot_access import SnapshotAccess
            SnapshotAccess(ctx).query_result(source, sql, result)


def authorize_query_replay(ctx, source, saved_result, *, source_permission="datasources.view", run_id=None, spec=None) -> bool:
    """持久工具回放仅验证既有查询证明与当前列范围，不调用执行器或增加历史查看权限。"""
    from app.common.json_numbers import preserve_json_integers
    from app.modules.analytics.models import QueryHistory
    from app.modules.analytics.snapshot_access import SnapshotAccess
    authority = QueryAuthority(ctx, source, permission=("datasources.execute" if source_permission == "datasources.execute" else "analytics.execute"),
                               source_permission=source_permission, run_id=run_id)
    if spec is not None:
        authority.bind_tool(spec)
    with authority.authorized_source() as (fresh, current_source):
        payload = saved_result.get("data") if isinstance(saved_result, dict) else None
        payload = payload if isinstance(payload, dict) else {}
        query_id = payload.get("queryId")
        if not query_id:
            # 旧无查询证明回执仅保留未限制字段的绑定源合同；一旦收窄，不能猜测其数据列。
            return not SnapshotAccess.restricted(current_source)
        record = fresh.db.scalar(select(QueryHistory).where(
            QueryHistory.id == str(query_id), QueryHistory.workspace_id == fresh.workspace_id,
            QueryHistory.owner_user_id == fresh.user_id, QueryHistory.datasource_id == current_source.id,
            QueryHistory.status == "success"))
        if record is None:
            return False
        canonical = record.result_json or {}
        # 异常历史只失去回放资格，不覆盖原证据，也不能让结构错误穿透成 500。
        if (not isinstance(canonical, dict) or not isinstance(canonical.get("data"), list)
                or not isinstance(canonical.get("columns"), list)
                or not all(isinstance(column, dict) for column in canonical["columns"])
                or not isinstance(canonical.get("parameters") or {}, dict)):
            return False
        supplied = {"data": payload.get("data", payload.get("rows")),
                    "columns": payload.get("column_metadata", payload.get("columns")),
                    "parameters": canonical.get("parameters") or {}}
        if (payload.get("datasourceId", payload.get("datasource_id")) != current_source.id
                or payload.get("generatedSql", payload.get("sql")) != record.generated_sql
                or preserve_json_integers(supplied["data"]) != preserve_json_integers(canonical.get("data"))):
            return False
        column_names = lambda columns: [item.get("columnName") for item in columns or [] if isinstance(item, dict)]
        if (not isinstance(supplied["columns"], list)
                or len(column_names(supplied["columns"])) != len(supplied["columns"])
                or column_names(supplied["columns"]) != column_names(canonical.get("columns"))):
            return False
        SnapshotAccess(fresh).query_result(current_source, record.generated_sql, supplied)
        return True
