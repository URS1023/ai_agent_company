# -*- coding: utf-8 -*-
"""Tool Gateway — the single execution gateway for every tool in the system.

Responsibilities: lookup → permission → secret → schema validation →
provider dispatch → result normalization. (§29)

中文补充（治理与安全约束，改动前必读）：
- 全系统唯一的工具执行入口：Chat/工作流/定时任务/测试调用都必须走
  ``ToolGateway.execute``，不得绕过网关直接调用 provider 的 ``invoke``。
- 权限链：工具发现（list_available）与执行（execute）都会校验
  ``ToolSpec.permissions``。执行期用 require_permission 逐项校验并叠加
  require_resource 的数据范围绑定（bind_data_scope），因此租户/数据源越权
  在这里被拦截，而不是在 provider 内部。
- ``enabled`` 治理：操作台停用某个工具方法后（capabilities.models
  .ToolMethodMetadata.enabled=False），该方法立即从 list_available 的模型
  工具列表中消失，并且 resolve 返回 None / 已解析的 spec 执行时报错；
  检查时直接 select 列值而不复用 ORM 缓存，保证已打开的会话在下一轮就能生效。
- 密钥：连接器/数据源引用的 Secret 在执行前集中解密注入 ``ctx["secret_values"]``，
  并校验密钥归属工作区且状态为 active；工具代码拿不到明文，只在网关内注入。
- 结果归一化：所有 provider 返回值经 normalizer 统一为 ToolResult，
  失败以 error_code/error_summary 形式返回模型，不向上抛异常（参数校验失败、
  用户取消除外）。
"""
from __future__ import annotations

from app.core.observability import log_event, observe

import logging
import json
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import AppError, SchemaValidationError, ToolExecutionError
from ...modules.secret.service import decrypt_many
from ...modules.runtime.task_control import ChatCancelled, ChatTaskTimeout
from ...modules.capabilities.models import ToolMethodMetadata
from ...models import Connector, ConnectorVersion, DataSource, Secret, Tool as ToolRow, ToolVersion
from . import builtin_provider, database_provider, http_provider, mcp_provider, sandbox_provider
from .base import ToolSpec, ToolResult
from .normalizer import make_error_result, normalize
from .validator import validate_arguments

logger = logging.getLogger("tool_gateway")

_PROVIDERS = {
    # provider 名称 → 实现模块；键必须与 ToolSpec.provider 的取值集合一致，
    # 新增实现时同步在本表注册，否则执行期会返回 unsupported_provider。
    "http": http_provider,
    "database": database_provider,
    "mcp": mcp_provider,
    "builtin": builtin_provider,
    "sandbox": sandbox_provider,
}


def _normalize_model_arguments(name: str, arguments):
    """兼容模型常见的别名写法，再交给同一份严格 schema 校验。

    原生工具循环中，模型有时会把 OpenAI 函数参数写成 camelCase，或把文档规格
    写成通用的 ``spec``/``data``。这些写法语义明确且不会扩大权限；在校验前统一
    成业务工具的正式字段，可避免已经有数据时因一个字段名差异丢失整轮交付。
    """
    value = arguments
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return value
        if isinstance(parsed, dict):
            value = parsed
        else:
            return arguments
    if not isinstance(value, dict):
        return arguments
    result = dict(value)

    aliases = {
        "render_document": {"document_spec": ("spec", "data", "document", "documentSpec")},
        "render_chart_image": {"query_id": ("queryId",), "chart_type": ("chartType", "type")},
        "export_query_spreadsheet": {"query_id": ("queryId",)},
        "export_query_presentation": {"query_ids": ("queryIds",)},
        "create_dashboard_from_queries": {"query_ids": ("queryIds",)},
        "query_inventory_data": {"datasource_id": ("datasourceId",), "warehouse_id": ("warehouseId",),
                                 "material_id": ("materialId",), "batch_number": ("batchNumber",),
                                 "page_size": ("pageSize",)},
        "render_dashboard_image": {"report_id": ("reportId",)},
    }.get(name, {})
    for canonical, names in aliases.items():
        if canonical not in result:
            for alias in names:
                if alias in result:
                    result[canonical] = result[alias]
                    break
        for alias in names:
            result.pop(alias, None)
    return result


def build_context(*, db: Session, workspace_id: str, user_id: str, run_id: str,
                  conversation_id: str = None, attached_file_ids: List[str] | None = None,
                  selected_datasource_ids: List[str] | None = None,
                  selected_dashboard_template_id: str | None = None,
                  selected_dashboard_template_mode: str = "",
                  allow_network: bool = False) -> dict:
    """构造工具执行上下文（ctx），是 provider 能读到的全部运行期信息的白名单。

    关键参数：``workspace_id`` / ``user_id`` / ``run_id`` 用于鉴权与审计归属；
    ``attached_file_ids`` 限定本次会话可见的附件；``selected_datasource_ids``
    是用户在前端显式勾选的数据源，数据库类工具只能在其范围内选源；
    ``allow_network`` 会传递给沙箱 provider 决定是否放行网络。

    返回的 dict 同时是一个受控的"传递容器"：网关后续会把解密后的
    ``secret_values``、解析出的 ``data_source_config``、``auth_context`` 等
    写入其中，provider 只读不写。
    """
    return {
        "db": db,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "attached_file_ids": attached_file_ids or [],
        "selected_datasource_ids": selected_datasource_ids or [],
        "selected_dashboard_template_id": selected_dashboard_template_id,
        "selected_dashboard_template_mode": selected_dashboard_template_mode,
        "allow_network": allow_network,
        "secret_values": {},
    }


class ToolGateway:
    """工具网关：工具发现、鉴权、密钥注入、参数校验、provider 分发、结果归一化。

    生命周期：每个请求/每个执行步骤用同一个 SQLAlchemy ``Session`` 构造一次，
    不做跨请求复用；会话内的 ORM 身份映射不参与治理判定（见 ``_method_enabled``）。

    关键不变式：
    - 只暴露"已发布"的工具：内置工具来自代码内的 builtin_specs()，连接器工具
      必须是 Tool/Connector 及各自 published 版本四者同时 published，且
      ToolVersion.connector_version_id 指向已发布的 ConnectorVersion。
    - 任何执行都要经过权限校验，权限不足抛 AppError（403 语义），不会降级放行。
    - provider 内部崩溃被转为 tool_internal_error 结果，只记录日志向外返回摘要，
      不把堆栈透给模型；但 ChatCancelled / ChatTaskTimeout 必须向上抛出，
      以便上层终止本轮对话。
    """

    def __init__(self, db: Session):
        """绑定当前请求的数据库会话；网关持有的只是会话，不缓存工具定义。"""
        self.db = db

    def _method_enabled(self, workspace_id: str, name: str) -> bool:
        """判断工具方法是否被治理开关启用（未配置视为启用）。

        权限：仅做状态读取，不做鉴权，调用方必须先通过身份校验。
        返回 True 表示可用；只有显式写入 enabled=False 才会返回 False，
        这样历史数据（无该行）保持原有可用性。
        """
        # Select the column, not a cached ORM entity: an already-open chat must
        # observe governance changes before its next provider invocation.
        enabled = self.db.scalar(select(ToolMethodMetadata.enabled).where(
            ToolMethodMetadata.workspace_id == workspace_id,
            ToolMethodMetadata.tool_name == name,
        ))
        return enabled is not False

    # ------------------------------------------------------------------
    def list_available(self, workspace_id: str, include_builtin: bool = True,
                       execution_context: dict | None = None) -> List[ToolSpec]:
        """列出当前身份在该工作区可用的工具，返回的是模型工具列表的唯一来源。

        权限：身份解析失败（未登录/成员被移除等 AppError）直接返回空列表而不是
        抛错，避免把鉴权细节泄露给模型；随后按 access_policy 的权限集合逐项过滤，
        缺少 ``connectors.execute`` 时只返回内置工具。
        数据范围：对 ``connectors.execute`` 调用 bind_data_scope，使后续
        require_resource 能按用户的可访问范围过滤连接器。
        治理：``enabled=False`` 的工具方法在这里被剔除——"停用即从模型工具列表
        移除"，模型不会再看到它，从而不会尝试调用。
        """
        auth_ctx = None
        granted = None
        if execution_context is not None:
            try:
                auth_ctx = self._authorize_identity(execution_context)
                from ...common.authorization import access_policy
                granted = access_policy(auth_ctx).permissions
            except AppError:
                return []
        disabled = set(self.db.scalars(select(ToolMethodMetadata.tool_name).where(
            ToolMethodMetadata.workspace_id == workspace_id,
            ToolMethodMetadata.enabled.is_(False),
        )).all())
        # 只查被显式停用的名称集合：未配置记录的工具保持历史可用性。
        specs: List[ToolSpec] = [
            spec for spec in (builtin_provider.builtin_specs() if include_builtin else [])
            if spec.name not in disabled and (granted is None or all(permission in granted for permission in spec.permissions))
        ]
        if granted is not None and "connectors.execute" not in granted:
            return specs
        if auth_ctx is not None:
            from ...common.data_scope import bind_data_scope
            bind_data_scope(auth_ctx, "connectors.execute")
        rows = self.db.execute(
            select(ToolRow, ToolVersion, Connector, ConnectorVersion)
            .join(ToolVersion, ToolVersion.id == ToolRow.published_version_id)
            .join(Connector, Connector.id == ToolRow.connector_id)
            .join(ConnectorVersion, ConnectorVersion.id == Connector.published_version_id)
            .where(ToolRow.workspace_id == workspace_id,
                   ToolRow.status == "published",
                   ToolVersion.status == "published",
                   Connector.status == "published",
                   ConnectorVersion.status == "published",
                   ToolVersion.connector_version_id == ConnectorVersion.id)
        ).all()
        names = {s.name for s in specs}
        for tool, tv, conn, cv in rows:
            try:
                spec = ToolSpec.from_dict(tv.spec)
                spec.connector_id = conn.id
                spec.connector_version_id = cv.id
                spec.provider = conn.provider
                spec._connector_spec = cv.spec  # type: ignore[attr-defined]
                spec._tool_id = tool.id  # type: ignore[attr-defined]
                spec._tool_version_id = tv.id  # 缓存回放必须绑定首次执行的工具版本。
                if spec.name not in names and spec.name not in disabled and (granted is None or all(permission in granted for permission in spec.permissions)):
                    if auth_ctx is not None:
                        from ...common.authorization import require_resource
                        require_resource(auth_ctx, "connectors.execute", conn)
                    specs.append(spec)
                    names.add(spec.name)
            except Exception as exc:
                logger.warning("skip tool %s: %s", tool.name, exc)
        return specs

    def _authorize_identity(self, execution_context: dict):
        """Resolve live account/membership state and attach no additional authority.

        中文补充：实时读取账号/成员关系（不信任会话中缓存的角色），并把结果写回
        ``execution_context["auth_context"]`` 供后续 require_resource / data_scope
        复用；本方法只做身份解析，不授予任何额外权限。
        """
        from ...modules.business_ai.context import auth_context_from_tool
        auth_ctx = auth_context_from_tool(execution_context)
        execution_context["auth_context"] = auth_ctx
        return auth_ctx

    def _authorize_spec(self, spec: ToolSpec, execution_context: dict) -> None:
        """ToolSpec.permissions is an execution constraint, not descriptive metadata.

        中文补充：执行任何工具前的最后一道鉴权闸门，逐项 require_permission；
        随后再校验治理开关 ``enabled``，若方法已被操作台停用，抛
        ToolExecutionError（业务错误，会回给模型）而不是静默跳过，
        以保证"停用"对已在进行中的会话同样生效。
        异常：权限不足时由 require_permission 抛 AppError（403 语义）。
        """
        auth_ctx = self._authorize_identity(execution_context)
        from ...common.authorization import require_permission
        for permission in spec.permissions:
            require_permission(auth_ctx, permission)
        if not self._method_enabled(auth_ctx.workspace_id, spec.name):
            raise ToolExecutionError("工具方法已停用，不能发起新的执行")

    def resolve(self, workspace_id: str, name: str, execution_context: dict | None = None) -> Optional[ToolSpec]:
        """按名称解析工具定义，返回 None 表示不存在或已被治理停用。

        权限：内置工具命中时立即做 ``_authorize_spec``（已知但无权调用应报 403，
        与"不存在"区分）；连接器工具要求 ``connectors.execute`` 权限并绑定数据范围，
        再对具体连接器做 require_resource。无执行上下文（如平台内部巡检）时跳过鉴权。
        异常：鉴权失败抛 AppError 并向上传递；spec 反序列化失败只记 warning 返回 None。
        """
        if not self._method_enabled(workspace_id, name):
            return None
        for s in builtin_provider.builtin_specs():
            if s.name == name:
                if execution_context is not None:
                    # A known tool that the caller cannot execute is forbidden,
                    # not missing.  Keeping the authorization error lets the
                    # execution gateway return the correct security contract.
                    self._authorize_spec(s, execution_context)
                return s
        auth_ctx = None
        if execution_context is not None:
            auth_ctx = self._authorize_identity(execution_context)
            from ...common.authorization import require_permission
            from ...common.data_scope import bind_data_scope
            require_permission(auth_ctx, "connectors.execute")
            bind_data_scope(auth_ctx, "connectors.execute")
        row = self.db.execute(
            select(ToolRow, ToolVersion, Connector, ConnectorVersion)
            .join(ToolVersion, ToolVersion.id == ToolRow.published_version_id)
            .join(Connector, Connector.id == ToolRow.connector_id)
            .join(ConnectorVersion, ConnectorVersion.id == Connector.published_version_id)
            .where(ToolRow.workspace_id == workspace_id, ToolRow.name == name,
                   ToolRow.status == "published", ToolVersion.status == "published",
                   Connector.status == "published", ConnectorVersion.status == "published",
                   ToolVersion.connector_version_id == ConnectorVersion.id)
        ).first()
        if not row:
            return None
        tool, tv, conn, cv = row
        try:
            if auth_ctx is not None:
                from ...common.authorization import require_resource
                require_resource(auth_ctx, "connectors.execute", conn)
            spec = ToolSpec.from_dict(tv.spec)
            spec.connector_id = conn.id
            spec.connector_version_id = cv.id
            spec.provider = conn.provider
            spec._connector_spec = cv.spec
            spec._tool_id = tool.id
            spec._tool_version_id = tv.id
            return spec
        except AppError:
            raise
        except Exception as exc:
            logger.warning("resolve tool %s failed: %s", name, exc)
            return None

    def _prepare_secrets(self, spec: ToolSpec, ctx: dict, conn_spec: dict = None) -> None:
        """集中解析并注入连接器/数据源所需密钥与数据源配置。

        密钥来源三处且会去重：连接器 spec 的 ``secret_id``、``auth.secret_id``、
        以及其绑定数据源的 ``secret_id``。每个密钥都必须属于当前工作区、
        状态为 active 且未删除，否则抛 ToolExecutionError（不泄露密钥是否存在
        以外的信息）。解密失败同样按配置错误处理，提示管理员重新配置。

        副作用：写入 ``ctx["secret_values"]``（按 secret_id 建索引），单密钥时
        额外别名 token/password/api_key 以兼容各类 provider；若绑定了数据源，
        还会注入 ``ctx["data_source_config"]``（含明文密码）与 ``data_source_secret_id``，
        并对该数据源做 require_resource 数据范围校验。
        """
        conn_spec = conn_spec or (getattr(spec, "_connector_spec", None) or {})
        secret_ids = []
        if conn_spec.get("secret_id"):
            secret_ids.append(conn_spec["secret_id"])
        auth = conn_spec.get("auth") or {}
        if auth.get("secret_id"):
            secret_ids.append(auth["secret_id"])
        ds_id = conn_spec.get("data_source_id")
        if ds_id:
            ds = self.db.get(DataSource, ds_id)
            if ds and ds.secret_id:
                secret_ids.append(ds.secret_id)
        secret_ids = list(dict.fromkeys(s for s in secret_ids if s))
        for secret_id in secret_ids:
            secret = self.db.get(Secret, secret_id)
            if not secret or secret.workspace_id != ctx.get("workspace_id") or secret.status != "active" or secret.deleted_at is not None:
                raise ToolExecutionError("连接器引用的密钥不存在、已停用或不属于当前工作区")
        values = decrypt_many(self.db, secret_ids)
        if len(values) != len(secret_ids):
            raise ToolExecutionError("连接器密钥无法解密，请由管理员重新配置")
        ctx["secret_values"] = values
        if len(values) == 1:
            only = next(iter(values.values()))
            ctx["secret_values"].setdefault("token", only)
            ctx["secret_values"].setdefault("password", only)
            ctx["secret_values"].setdefault("api_key", only)
        # inject resolved datasource config (with decrypted password) for DB tools
        if ds_id:
            ds = self.db.get(DataSource, ds_id)
            if not ds or ds.workspace_id != ctx.get("workspace_id") or ds.status != "active":
                raise ToolExecutionError("连接器引用的数据源不存在、未启用或不属于当前工作区")
            auth_ctx = ctx.get("auth_context")
            if auth_ctx is not None:
                from ...common.authorization import require_resource
                require_resource(auth_ctx, "datasources.execute", ds)
            if ds:
                ds_cfg = dict(ds.connection_config or {})
                if ds.secret_id and ds.secret_id in values:
                    ds_cfg["password"] = values[ds.secret_id]
                elif ds_cfg.get("password") is None:
                    ds_cfg["password"] = ""
                ctx["data_source_config"] = ds_cfg
                ctx["data_source_secret_id"] = ds.secret_id if ds.secret_id else None

    @observe("tool.execute", "工具调用", fields={"workspace_id": "workspace_id", "run_id": "ctx.run_id", "tool_name": "name"})
    def execute(self, workspace_id: str, name: str, args_raw, ctx: dict) -> ToolResult:
        """按名称执行工具（运行期主入口，模型/工作流/定时任务共用）。

        流程：checkpoint() 响应取消 → resolve（含 enabled 治理与鉴权）→
        ``_authorize_spec`` 二次鉴权 → validate_arguments 入参校验 → 注入密钥与
        连接器 spec → ``_dispatch`` 分发到 provider → 结果归一化。

        参数：``args_raw`` 为模型给出的原始参数（可能是 JSON 字符串，交给 validator
        兼容处理）；``ctx`` 由 ``build_context`` 创建，本方法会往里补 auth_context、
        connector_spec、secret_values 等。
        返回：ToolResult；业务失败（工具不存在/鉴权失败/密钥配置错误）以
        error_code 形式返回而不抛错，便于模型看到原因后调整。
        异常：SchemaValidationError 会包装成中文提示后抛出（属于调用方缺陷，
        不作为工具结果返回）；ChatCancelled / ChatTaskTimeout 继续向上抛。
        """
        from app.modules.runtime.task_control import checkpoint
        checkpoint()
        try:
            spec = self.resolve(workspace_id, name, execution_context=ctx)
            if spec is None:
                return make_error_result("tool_not_found", f"未找到工具 {name}")
            self._authorize_spec(spec, ctx)
            args = validate_arguments(_normalize_model_arguments(name, args_raw), spec.input_schema, ctx)
        except SchemaValidationError as exc:
            raise SchemaValidationError(f"工具 {name} 参数校验失败: {exc}")
        except AppError as exc:
            return self._error_result(name, exc)
        conn_spec = getattr(spec, "_connector_spec", None)
        if conn_spec is not None:
            ctx["connector_spec"] = conn_spec
            ctx["connector_version_id"] = spec.connector_version_id
        self._prepare_secrets(spec, ctx)
        if spec.provider not in {"http", "mcp"} and callable(ctx.get("_before_tool_dispatch")):
            # 外部 HTTP/MCP 在各自目标与请求预检完成后登记；其余工具在网关派发前登记。
            ctx["_before_tool_dispatch"](provider=spec.provider, external_side_effect=False)
        return self._dispatch(spec, args, name, ctx)

    def authorize_replay(self, workspace_id: str, name: str, arguments: dict, ctx: dict,
                         *, tool_version_id: str | None = None, saved_result=None) -> ToolResult | None:
        """只校验缓存回执的当前授权，不解密密钥、不调用模型/数据源或重发业务操作。

        覆盖工具治理、连接器及绑定数据源、显式数据源查询和单文件读取。
        未建立资源来源校验的聚合/生成工具暂不回放，不能用一次工具权限检查冒充逐行授权。
        """
        denied = make_error_result("replay_authorization_required",
            "已有操作不会重复执行，但当前无法完整核验这份历史回执的资源授权。请在对应业务页面查看已授权的结果。")
        denied.metadata = {"outcome": "blocked", "recovery": "review_saved_result", "operation_reexecuted": False}
        try:
            spec = self.resolve(workspace_id, name, execution_context=ctx)
            if spec is None:
                return make_error_result("tool_not_available", "当前工具已停用或不再可用，不能回放历史回执。")
            self._authorize_spec(spec, ctx)
            if not isinstance(arguments, dict):
                return denied
            # 沿用正式参数校验；带 query_id/report_id 等未知间接资源参数的旧回执不能套用单源证明。
            args = validate_arguments(_normalize_model_arguments(name, arguments), spec.input_schema, ctx)
            actor = ctx["auth_context"]
            from ...modules.analytics.sources import get_source

            if spec.provider in {"http", "mcp", "database"}:
                # 版本改变后连接器可能已换绑数据源，不能用新资源授权放行旧资源结果。
                if not tool_version_id or tool_version_id != getattr(spec, "_tool_version_id", None):
                    return denied
                # 连接器权限与资源范围由 resolve 检查；绑定数据源仍须独立验证。
                source_id = (getattr(spec, "_connector_spec", None) or {}).get("data_source_id")
                if source_id:
                    source = get_source(actor, source_id, "datasources.execute", active=True, provider="any")
                    if spec.provider == "database":
                        from ...modules.analytics.query_authority import authorize_query_replay
                        if not authorize_query_replay(actor, source, saved_result,
                                source_permission="datasources.execute", run_id=ctx.get("run_id"), spec=spec):
                            return denied
                elif spec.provider == "database":
                    return denied
            elif name in {"query_business_data", "query_inventory_data", "collect_device_monitoring_snapshot"}:
                selected = list(dict.fromkeys(ctx.get("selected_datasource_ids") or []))
                source_id = args.get("datasource_id") or (selected[0] if len(selected) == 1 else None)
                if not source_id or (selected and source_id not in selected):
                    return denied
                source = get_source(actor, source_id, active=True, provider="any")
                if source.provider != "http" and str(args.get("mode") or "natural_language") not in {"schema", "profile"}:
                    from ...modules.analytics.query_authority import authorize_query_replay
                    if not authorize_query_replay(actor, source, saved_result, run_id=ctx.get("run_id"), spec=spec):
                        return denied
            elif name == "read_file":
                from ...common.authorization import require_resource
                from ...modules.files.service import get_file
                file_id = args.get("file_id")
                if not file_id:
                    return denied
                row = get_file(self.db, actor.workspace_id, file_id)
                require_resource(actor, "files.view", row)
            elif name not in {"current_datetime", "list_business_skills", "read_business_skill_resource"}:
                return denied
        except SchemaValidationError:
            return denied
        except AppError as exc:
            result = self._error_result(name, exc)
            result.metadata = {**result.metadata, "outcome": "blocked", "recovery": "review_saved_result", "operation_reexecuted": False}
            return result
        return None

    @observe("tool.test", "工具试运行", fields={"workspace_id": "ctx.workspace_id", "run_id": "ctx.run_id", "tool_name": "spec.name", "provider": "spec.provider"})
    def execute_spec(self, spec: ToolSpec, args_raw, connector_spec: dict, ctx: dict) -> ToolResult:
        """Execute an explicit ToolSpec + connector spec (used for test invoke).

        中文补充：供"连接器工具试运行"使用——此时 spec 可能尚未落库发布，
        因此由调用方直接传入，跳过基于名称的解析但仍执行完整鉴权与入参校验，
        避免试运行接口成为越权通道。业务类错误以 ToolResult 返回，不抛异常。
        """
        try:
            self._authorize_spec(spec, ctx)
            args = validate_arguments(args_raw, spec.input_schema, ctx)
        except SchemaValidationError as exc:
            raise SchemaValidationError(f"工具 {spec.name} 参数校验失败: {exc}")
        except AppError as exc:
            return make_error_result(exc.code, exc.message)
        spec._connector_spec = connector_spec
        ctx["connector_spec"] = connector_spec
        self._prepare_secrets(spec, ctx, connector_spec)
        return self._dispatch(spec, args, spec.name, ctx)

    def _dispatch(self, spec: ToolSpec, args: dict, name: str, ctx: dict) -> ToolResult:
        """把请求分发到 spec.provider 对应的 provider 模块并归一化返回。

        未知 provider 返回 unsupported_provider 错误（不是异常），避免因配置错误
        打断整轮对话。provider 抛出的业务异常统一转成失败结果；provider 内部
        未预期异常记录堆栈日志后返回 tool_internal_error，只把摘要给模型，
        但取消/超时异常必须继续上抛以终止本轮执行。
        """
        log_event("tool.dispatch", "工具已通过权限与参数校验，进入执行器", tool_name=name, provider=spec.provider)
        provider_mod = _PROVIDERS.get(spec.provider)
        if provider_mod is None:
            return make_error_result("unsupported_provider", f"不支持的 Provider: {spec.provider}")
        try:
            result = provider_mod.invoke(spec, args, ctx)
            return normalize(result, tool_name=name)
        except ToolExecutionError as exc:
            return self._error_result(name, exc)
        except AppError as exc:
            return self._error_result(name, exc)
        except (SchemaValidationError, ChatCancelled, ChatTaskTimeout):
            raise
        except Exception as exc:
            logger.exception("tool %s crashed", name)
            # 详细异常只进入受控日志；工具回执不能把 SQL、路径或连接参数直接交给模型。
            return make_error_result("tool_internal_error", "工具执行未完成，请查看日志后重试。")

    @staticmethod
    def _error_result(name: str, error: AppError) -> ToolResult:
        """把业务异常转为失败结果；对业务查询工具额外附加澄清语义。

        特殊处理原因：业务取数工具在口径不明确时需要向用户追问，因此当异常
        extra 中带 ``clarification`` 时，把结果标记为 requires_user_input 并把
        候选口径回填到 data 中，前端据此渲染追问卡片；其他工具保持原样返回。
        """
        result = make_error_result(error.code, error.message)
        if error.extra.get("failure_domain") == "datasource":
            result.metadata["failure_domain"] = "datasource"
        if isinstance(error.extra.get("presentation_progress"), dict):
            result.metadata["presentation_progress"] = error.extra["presentation_progress"]
        if name in {"query_business_data", "query_inventory_data"}:
            clarification = str(error.extra.get("clarification") or "").strip()[:1000]
            result.metadata.update(retryable=False, requires_user_input=bool(clarification))
            if clarification:
                result.data = {"clarification": clarification}
                if isinstance(error.extra.get("options"), list):
                    result.data["options"] = error.extra["options"][:6]
                result.error_summary = f"需要补充业务口径：{clarification}"
        return result
