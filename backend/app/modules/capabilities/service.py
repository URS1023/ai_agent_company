"""Action-scoped capability catalog and immutable connector version lifecycle.

中文补充（工具目录与连接器服务）：
- 职责：① 向管理台提供"当前账号可见工具"的目录视图（内置 + 连接器工具，含治理元数据）；
  ② 管理连接器与不可变版本的完整生命周期（创建草稿 → 新版本 → 发布 → 复制/共享/归档）；
  ③ 以生产同款网关执行"工具试运行/参数校验/参数编译"，并完整落 Run/Step/ToolCall 追踪。
- 权限：按动作拆分（capabilities.view/execute/create/edit/delete/export、
  connectors.view/create/edit/execute/publish/delete），并对连接器、数据源、密钥
  叠加 require_resource 数据范围校验；跨工作区资源一律表现为"不存在"。
- 关键约束：
  - 发布是原子动作，发布后工具网关只解析该版本（旧运行仍保留原版本标识）；
    已发布版本不可覆盖，修改必须新建版本（based_on_version_id 记录来源）。
  - 配置在入库前必须通过 normalize_configuration 的白名单校验：HTTP 路径不能换主机、
    明文凭据一律拒绝；只有 system.admin 才能授权 allow_private（本机/内网目标）。
  - 工具名与内置工具及其它连接器冲突时直接拒绝（避免遮蔽已发布调用契约）。
- 依赖：SQLAlchemy 模型（Connector/ConnectorVersion/Tool/ToolVersion/DataSource/Secret）、
  collection.transport 的地址与请求头校验、providers.tool 的网关与校验器。
"""
from __future__ import annotations

import json
import ipaddress
import re
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select

from app.common.audit import audit
from app.common.authorization import access_policy, require_permission, require_resource
from app.common.owned_records import visible_records
from app.common.data_scope import bind_data_scope
from app.common.deps import AuthContext
from app.errors import ConflictError, NotFoundError, ValidationError
from app.ids import new_id
from app.models import (
    Connector,
    ConnectorVersion,
    DataSource,
    Run,
    Secret,
    Tool,
    ToolVersion,
)
from app.modules.collection.transport import sensitive_name, validate_address, validate_headers, validate_url
from app.modules.runtime import service as runtime
from app.modules.system.permissions import ALL_PERMISSIONS
from app.modules.system.service_helpers import commit_change
from app.providers.tool import builtin_provider
from app.providers.tool.base import ToolSpec
from app.providers.tool.gateway import ToolGateway, build_context
from app.providers.tool.validator import rule_validators, validate_arguments, validate_schema

from .schemas import (
    ConnectorCloneInput, ConnectorInput, ConnectorShareInput, ConnectorVersionInput, ToolDefinition,
)
from .models import ToolCategory, ToolMethodMetadata


PROVIDER_LABELS = {"builtin": "平台内置", "http": "HTTP", "database": "数据库", "mcp": "MCP"}
_CONFIG_KEYS = {
    "http": {"baseUrl", "base_url", "path", "method", "headers", "auth", "timeoutSeconds", "timeout_seconds", "allowPrivate", "allow_private", "bodyMode", "body_mapping"},
    "database": {"dataSourceId", "data_source_id"},
    "mcp": {"transport", "url", "headers", "auth", "secretId", "secret_id", "timeoutSeconds", "timeout_seconds", "allowPrivate", "allow_private"},
}


def _now() -> datetime:
    """统一的写入时间（UTC naive），保证各表时间字段口径一致。"""
    return datetime.utcnow()


def _value(data: dict, camel: str, snake: str, default=None):
    """按"前端 camelCase 优先、后端 snake_case 兼容"的顺序取值。

    连接器配置同时要被前端（camelCase）和 provider（snake_case）消费，
    历史数据两种写法都存在，因此统一从这里取，不要在各处各写一遍。
    """
    return data[camel] if camel in data else data.get(snake, default)


def _schema_contract(schema: dict, *, input_schema: bool) -> dict:
    """入库前使用执行器的同一契约检查，不能保存运行期不执行的约束。"""
    if not isinstance(schema, dict):
        raise ValidationError("工具 Schema 必须是 JSON 对象")
    if input_schema and schema.get("type", "object") != "object":
        raise ValidationError("工具输入 Schema 根节点必须是 object")
    return validate_schema(schema, rule_validators)


def _tool_payload(item: ToolDefinition, provider: str) -> dict:
    """把接口层的工具定义转成持久化 spec（字段名用 snake_case 与 provider 约定一致）。

    权限码必须存在于 ALL_PERMISSIONS 白名单，否则拒绝——防止注册出永远不会有人能
    通过校验的"死工具"，也防止自定义权限名绕过网关的权限判定。
    """
    if any(permission not in ALL_PERMISSIONS for permission in item.permissions):
        raise ValidationError("工具声明了不存在的权限", tool=item.name)
    return {
        "name": item.name,
        "description": item.description.strip(),
        "provider": provider,
        "input_schema": _schema_contract(item.input_schema, input_schema=True),
        "output_schema": _schema_contract(item.output_schema, input_schema=False) if item.output_schema else {},
        "permissions": item.permissions,
        "timeout_seconds": item.timeout_seconds,
        "risk_level": item.risk_level,
        "requires_approval": item.requires_approval,
        "retry_policy": {},
    }


def _secret_reference(ctx: AuthContext, secret_id: str | None) -> str | None:
    """校验密钥引用：必须属于当前工作区、状态 active、未删除，且当前账号有 secrets.view。

    只返回 secret_id（引用），不读取也不返回明文；明文只在网关执行时解密注入。
    异常：不存在/已停用抛 NotFoundError；无权限由 require_resource 抛出。
    """
    if not secret_id:
        return None
    secret = ctx.db.get(Secret, secret_id)
    if secret is None or secret.workspace_id != ctx.workspace_id or secret.status != "active" or secret.deleted_at is not None:
        raise NotFoundError("选择的密钥不存在或已停用")
    require_resource(ctx, "secrets.view", secret)
    return secret.id


def _auth_config(ctx: AuthContext, raw) -> dict:
    """规范化认证配置：只允许保存密钥引用，禁止写入 token/密码/请求头明文。

    字段白名单为 type/secretId/secret_id/username/headerName/header_name；
    支持 none/bearer/basic/header 四种类型；非 none 时必须选择有效密钥记录，
    basic 必须有用户名（密码从密钥取），header 的自定义头名需符合 HTTP 头命名规则。
    返回统一 snake_case 结构 {"type","secret_id","username","header_name"}。
    """
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValidationError("认证配置必须是对象")
    allowed = {"type", "secretId", "secret_id", "username", "headerName", "header_name"}
    if set(raw) - allowed:
        raise ValidationError("认证配置只能保存密钥引用，不能保存 token、密码或请求头原文")
    auth_type = str(raw.get("type") or "none").lower()
    if auth_type not in {"none", "bearer", "basic", "header"}:
        raise ValidationError("认证类型只支持 none、bearer、basic 或 header")
    secret_id = _secret_reference(ctx, _value(raw, "secretId", "secret_id"))
    if auth_type != "none" and not secret_id:
        raise ValidationError("启用认证时必须选择密钥记录")
    username = str(raw.get("username") or "").strip()
    header_name = str(_value(raw, "headerName", "header_name", "X-API-Key") or "").strip()
    if auth_type == "basic" and not username:
        raise ValidationError("Basic 认证必须填写用户名；密码从密钥记录读取")
    if auth_type == "header" and not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}", header_name):
        raise ValidationError("自定义认证请求头名称不正确")
    return {"type": auth_type, "secret_id": secret_id, "username": username, "header_name": header_name}


def _public_headers(raw) -> dict[str, str]:
    """校验连接器的固定请求头：必须是字符串键值，且不能保存敏感头明文。

    敏感头（Authorization/Api-Key 等）必须改为"认证类型 + 密钥记录"配置，
    因此这里命中 sensitive_name 且非空即拒绝；其余合法性交给 validate_headers
    （数量、大小、禁用 Host/Content-Length 等传输层管辖字段）。
    """
    if not isinstance(raw or {}, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in (raw or {}).items()):
        raise ValidationError("固定请求头必须是字符串键值对象")
    headers = {key.strip(): value for key, value in (raw or {}).items()}
    if any(sensitive_name(key) and value for key, value in headers.items()):
        raise ValidationError("认证请求头请使用认证类型和密钥记录配置，不能保存明文")
    validate_headers(headers)
    return headers


def _validate_literal_target(url: str, allow_private: bool) -> None:
    """校验"字面量地址"目标：localhost/内网 IP 必须显式授权（allow_private）。

    域名不做解析（真正的解析与私网拒绝在运行期由 transport.resolve_destination
    逐次完成，避免配置校验通过、解析后指向内网）；此处只拦下写死的主机名与 IP。
    异常：未授权访问本机/内网时抛 ValidationError。
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        if not allow_private:
            raise ValidationError("本机和内网目标需要管理员明确授权")
        return
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    validate_address(host, allow_private)


def normalize_configuration(ctx: AuthContext, payload: ConnectorInput | ConnectorVersionInput) -> tuple[dict, list[dict]]:
    """把连接器配置与工具定义规范化为可持久化的 (config, tools)。

    这是连接器入库前的唯一校验关口，按 provider 分支：
    - http：只允许 GET/POST 与 json/query；路径必须是固定站内绝对路径且不能改变
      base_url 的主机（防 SSRF 换目标）；超时 1–120 秒；启用 allow_private 需 system.admin。
    - database：数据源必须存在、启用、属本工作区且不是 HTTP 数据源，并要求
      datasources.view 资源权限；工具名限定为 list_tables/describe_table/query，
      且强制追加 datasources.execute 权限。
    - mcp：只支持 streamable HTTP（禁止网页配置本机命令）；公网必须 https；
      超时 1–120 秒；allow_private 需 system.admin。
    另外拒绝未登记字段（防止把任意配置偷渡进版本 spec）。
    返回：规范化的 config 与工具 spec 列表（字段名 snake_case）。
    异常：配置越界/缺字段抛 ValidationError，资源不存在或无权限抛 NotFoundError。
    """
    raw = payload.config
    if payload.provider == "mcp" and str(raw.get("transport") or "http").lower() not in {"http", "streamable_http"}:
        raise ValidationError("网页管理的 MCP 仅支持 streamable HTTP；不允许配置任意本机命令")
    unknown = set(raw) - _CONFIG_KEYS[payload.provider]
    if unknown:
        raise ValidationError("连接配置包含未登记字段", fields=sorted(unknown))
    tools = [_tool_payload(item, payload.provider) for item in payload.tools]
    if not tools:
        raise ValidationError("连接器至少需要一个可测试的工具")
    if payload.provider == "http":
        base_url = str(_value(raw, "baseUrl", "base_url", "") or "").strip().rstrip("/")
        path = str(raw.get("path") or "/").strip()
        method = str(raw.get("method") or "POST").upper()
        body_mode = str(_value(raw, "bodyMode", "body_mapping", "json") or "json").lower()
        timeout = int(_value(raw, "timeoutSeconds", "timeout_seconds", 30) or 30)
        allow_private = bool(_value(raw, "allowPrivate", "allow_private", False))
        if method not in {"GET", "POST"} or body_mode not in {"json", "query"}:
            raise ValidationError("HTTP 工具只支持 GET/POST 与 JSON/查询参数映射")
        if not path.startswith("/") or path.startswith("//") or "\\" in path or "#" in path or "://" in path:
            raise ValidationError("HTTP 接口路径必须是固定的站内绝对路径")
        full_url = base_url + path
        parsed = validate_url(full_url)
        base = validate_url(base_url)
        if (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port):
            raise ValidationError("HTTP 接口路径不能改变目标主机")
        if allow_private:
            require_permission(ctx, "system.admin")
        _validate_literal_target(full_url, allow_private)
        if not 1 <= timeout <= 120:
            raise ValidationError("HTTP 超时必须为 1–120 秒")
        config = {
            "base_url": base_url,
            "path": path,
            "method": method,
            "body_mapping": body_mode,
            "headers": _public_headers(raw.get("headers")),
            "auth": _auth_config(ctx, raw.get("auth")),
            "timeout_seconds": timeout,
            "allow_private": allow_private,
        }
    elif payload.provider == "database":
        source_id = str(_value(raw, "dataSourceId", "data_source_id", "") or "").strip()
        source = ctx.db.get(DataSource, source_id) if source_id else None
        if source is None or source.workspace_id != ctx.workspace_id or source.status != "active" or source.provider == "http":
            raise NotFoundError("选择的数据库数据源不存在或未启用")
        require_resource(ctx, "datasources.view", source)
        allowed_names = {"database.list_tables", "database.describe_table", "database.query"}
        if any(tool["name"] not in allowed_names for tool in tools):
            raise ValidationError("数据库连接器只开放表清单、字段查看和只读查询工具")
        for tool in tools:
            tool["permissions"] = list(dict.fromkeys([*tool["permissions"], "datasources.execute"]))
        config = {"data_source_id": source.id}
    else:
        transport = str(raw.get("transport") or "http").lower()
        if transport not in {"http", "streamable_http"}:
            raise ValidationError("网页管理的 MCP 仅支持 streamable HTTP；不允许配置任意本机命令")
        url = str(raw.get("url") or "").strip()
        parsed = validate_url(url)
        if parsed.scheme != "https" and not bool(_value(raw, "allowPrivate", "allow_private", False)):
            raise ValidationError("公网 MCP 必须使用 HTTPS；内网 HTTP 需要管理员明确授权")
        allow_private = bool(_value(raw, "allowPrivate", "allow_private", False))
        if allow_private:
            require_permission(ctx, "system.admin")
        _validate_literal_target(url, allow_private)
        timeout = int(_value(raw, "timeoutSeconds", "timeout_seconds", 30) or 30)
        if not 1 <= timeout <= 120:
            raise ValidationError("MCP 超时必须为 1–120 秒")
        auth = raw.get("auth") or ({"type": "bearer", "secretId": _value(raw, "secretId", "secret_id")} if _value(raw, "secretId", "secret_id") else {})
        config = {
            "transport": "streamable_http",
            "url": url,
            "headers": _public_headers(raw.get("headers")),
            "auth": _auth_config(ctx, auth),
            "timeout_seconds": timeout,
            "allow_private": allow_private,
        }
    return config, tools


def _redacted_config(config: dict) -> dict:
    """返回可对外展示的连接配置：把 secret_id 换成 hasSecret 标记（有引用才回显 ID）。

    深拷贝后再改写，避免污染数据库里不可变的版本 spec；任何情况下都不返回密钥明文。
    """
    result = json.loads(json.dumps(config or {}))
    auth = result.get("auth")
    if isinstance(auth, dict):
        secret_id = auth.pop("secret_id", None)
        auth["hasSecret"] = bool(secret_id)
        if secret_id:
            auth["secretId"] = secret_id
    return result


def _connector(ctx: AuthContext, connector_id: str, permission: str) -> Connector:
    """按 ID 取连接器，同时绑定数据范围并做资源级鉴权（越权/不存在统一报 NotFoundError）。

    已归档的连接器对调用方表现为不存在；任何读写连接器的入口都必须经由本函数，
    以保证"数据范围 + 资源权限"两步校验不会被某个新入口漏掉。
    """
    bind_data_scope(ctx, permission)
    row = ctx.db.scalar(visible_records(ctx, Connector, permission).where(
        Connector.id == connector_id, Connector.status != "archived",
    ))
    if row is None:
        raise NotFoundError("连接器不存在或不在当前操作的数据范围内")
    require_resource(ctx, permission, row)
    return row


def _latest_version(ctx: AuthContext, connector_id: str) -> ConnectorVersion:
    """取连接器的最新版本（按 version_number 倒序取第一条）。

    所有"改配置"的动作都以最新版本号做乐观锁基准，因此必须用同一口径取号；
    没有版本时抛 ValidationError（正常流程不会出现，属数据异常兜底）。
    """
    row = ctx.db.scalar(select(ConnectorVersion).where(ConnectorVersion.connector_id == connector_id)
                        .order_by(ConnectorVersion.version_number.desc()).limit(1))
    if row is None:
        raise ValidationError("连接器没有可用版本")
    return row


def _tool_view(spec: ToolSpec, *, status: str = "published", connector_name: str = "") -> dict:
    """工具声明的对外视图（camelCase），供管理台工具列表/版本详情使用。

    只暴露声明类信息，不含连接配置与密钥；内置工具没有 connector_id，前端据此区分来源。
    """
    return {
        "name": spec.name,
        "description": spec.description,
        "provider": spec.provider,
        "providerLabel": PROVIDER_LABELS.get(spec.provider, spec.provider),
        "inputSchema": spec.input_schema,
        "outputSchema": spec.output_schema,
        "permissions": spec.permissions,
        "timeoutSeconds": spec.timeout_seconds,
        "riskLevel": spec.risk_level,
        "requiresApproval": spec.requires_approval,
        "connectorId": spec.connector_id,
        "connectorName": connector_name,
        "status": status,
    }


def _apply_catalog_metadata(ctx: AuthContext, items: list[dict], *, include_disabled: bool, categories=None) -> list[dict]:
    """把治理元数据（分类、展示名、启停、标签）合并进工具视图。

    参数：``include_disabled=False``（模型/普通用户场景）会剔除已停用方法；
    管理台传 True 以看到并重新启用它们。``categories`` 允许调用方批量复用分类缓存，
    避免在分页循环里重复查库。
    未治理过的工具（无 metadata 行）按"启用 + 名称即展示名"处理，保持向后兼容。
    分类路径带环保护（历史上可能被人工改库破坏），遇到环即停止向上追溯。
    """
    names = [item["name"] for item in items]
    metadata = {
        row.tool_name: row for row in ctx.db.scalars(select(ToolMethodMetadata).where(
            ToolMethodMetadata.workspace_id == ctx.workspace_id,
            ToolMethodMetadata.tool_name.in_(names),
        )).all()
    } if names else {}
    if categories is None:
        categories = {
            row.id: row for row in ctx.db.scalars(select(ToolCategory).where(
                ToolCategory.workspace_id == ctx.workspace_id,
                ToolCategory.status == "active",
            )).all()
        }

    def category_path(category_id: str | None) -> str:
        names_: list[str] = []
        seen: set[str] = set()
        current = categories.get(category_id or "")
        while current and current.id not in seen:
            seen.add(current.id)
            names_.append(current.name)
            current = categories.get(current.parent_id or "")
        return " / ".join(reversed(names_))

    result = []
    for item in items:
        row = metadata.get(item["name"])
        enabled = bool(row.enabled) if row else True
        if not enabled and not include_disabled:
            continue
        category = categories.get(row.category_id) if row and row.category_id else None
        result.append({
            **item,
            "methodId": row.id if row else None,
            "methodVersion": row.version if row else 0,
            "code": row.code if row else item["name"],
            "displayName": (row.display_name if row else "") or item["name"],
            "description": (row.description if row else "") or item.get("description", ""),
            "categoryId": category.id if category else None,
            "categoryName": category.name if category else "",
            "categoryPath": category_path(category.id) if category else "",
            "enabled": enabled,
            "tags": list(row.tags or []) if row else [],
        })
    return result


def _iter_visible_tools(ctx: AuthContext, *, include_disabled: bool = False, provider: str = ""):
    """Read fixed-size connector batches; never truncate a catalog at 5000 tools.

    Permission arrays remain validated with the same ToolSpec contract. Keyset
    pages finish before metadata queries, avoiding nested unbuffered cursors.

    中文补充：生成器形式产出"当前账号可见工具"。可见 = 已启用（可选含停用）
    + 已发布 + 权限码命中 access_policy + 连接器落在角色数据范围内。
    分页用 keyset（Tool.id > last_id，每次 100 条）而非 offset，避免工具很多时
    变慢；必须整表遍历，所以不要为了"快"截断成前 5000 条。调用前要求
    capabilities.view；无 connectors.view 时只产出内置工具。
    """
    require_permission(ctx, "capabilities.view")
    granted = access_policy(ctx).permissions
    categories = {
        row.id: row for row in ctx.db.scalars(select(ToolCategory).where(
            ToolCategory.workspace_id == ctx.workspace_id, ToolCategory.status == "active",
        )).all()
    }
    if not provider or provider == "builtin":
        builtin = [_tool_view(spec) for spec in builtin_provider.builtin_specs()
                   if all(permission in granted for permission in spec.permissions)]
        yield from _apply_catalog_metadata(ctx, builtin, include_disabled=include_disabled, categories=categories)
    if "connectors.view" not in granted or provider == "builtin":
        return
    bind_data_scope(ctx, "connectors.view")
    visible_ids = visible_records(ctx, Connector, "connectors.view").with_only_columns(Connector.id)
    query = (
        select(Tool, ToolVersion, Connector, ConnectorVersion)
        .join(ToolVersion, ToolVersion.id == Tool.published_version_id)
        .join(Connector, Connector.id == Tool.connector_id)
        .join(ConnectorVersion, ConnectorVersion.id == Connector.published_version_id)
        .where(Tool.connector_id.in_(visible_ids), Tool.status == "published",
               ToolVersion.status == "published", Connector.status == "published",
               ConnectorVersion.status == "published")
    )
    if provider:
        query = query.where(Connector.provider == provider)
    last_id = None
    while True:
        page = query.where(Tool.id > last_id) if last_id else query
        rows = ctx.db.execute(page.order_by(Tool.id).limit(100)).all()
        if not rows:
            return
        items = []
        for tool, version, connector, connector_version in rows:
            spec = ToolSpec.from_dict(version.spec)
            spec.connector_id, spec.connector_version_id = connector.id, connector_version.id
            spec.tool_id, spec.provider = tool.id, connector.provider
            if all(permission in granted for permission in spec.permissions):
                items.append(_tool_view(spec, connector_name=connector.name))
        yield from _apply_catalog_metadata(ctx, items, include_disabled=include_disabled, categories=categories)
        if len(rows) < 100:
            return
        last_id = rows[-1][0].id


def list_tools(
    ctx: AuthContext,
    *,
    keyword: str = "",
    provider: str = "",
    category_id: str = "",
    limit: int = 200,
    offset: int = 0,
    include_disabled: bool = False,
) -> dict:
    """工具列表（管理台与普通查询共用）；权限 capabilities.view（由 _iter_visible_tools 校验）。

    筛选在内存中完成：keyword 命中名称/展示名/描述/连接器名/code/分类路径/标签，
    provider 与 category_id 为精确匹配；排序把内置工具置顶，再按分类路径与展示名。
    返回 items（已分页）+ total（筛选后的总数）。
    """
    items = list(_iter_visible_tools(ctx, include_disabled=include_disabled, provider=provider))
    keyword = keyword.strip().lower()
    if keyword:
        items = [item for item in items if keyword in " ".join([
            item["name"], item["displayName"], item["description"], item["connectorName"],
            item["code"], item["categoryPath"], " ".join(item["tags"]),
        ]).lower()]
    if provider:
        items = [item for item in items if item["provider"] == provider]
    if category_id:
        items = [item for item in items if item["categoryId"] == category_id]
    items.sort(key=lambda item: (item["provider"] != "builtin", item["categoryPath"] or "~", item["displayName"]))
    return {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}


def resolve_visible_tool(ctx: AuthContext, name: str, permission: str = "capabilities.view") -> ToolSpec:
    """按名称解析"当前账号确实可用"的工具，返回可直接执行的 ToolSpec。

    这是管理台侧（校验/编译/试运行）的统一解析入口，与运行期网关的 resolve 保持
    同样的判定：治理停用 → 不存在；权限码不满足 → 不存在；连接器越范围 → 不存在
    （统一用"不存在"而非 403，避免通过错误码探测他人资源）。连接器工具会回填
    connector 的版本信息与 ``_connector_spec``，供试运行执行使用。
    异常：无权限或工具不可用抛 NotFoundError/由 require_permission 抛 AppError。
    """
    require_permission(ctx, permission)
    method = ctx.db.scalar(select(ToolMethodMetadata).where(
        ToolMethodMetadata.workspace_id == ctx.workspace_id,
        ToolMethodMetadata.tool_name == name,
    ))
    if method is not None and not method.enabled:
        raise NotFoundError("工具不存在或当前账号不可用")
    granted = access_policy(ctx).permissions
    for spec in builtin_provider.builtin_specs():
        if spec.name == name:
            if not all(item in granted for item in spec.permissions):
                raise NotFoundError("工具不存在或当前账号不可用")
            return spec
    require_permission(ctx, "connectors.view" if permission.endswith("view") else "connectors.execute")
    connector_permission = "connectors.view" if permission.endswith("view") else "connectors.execute"
    bind_data_scope(ctx, connector_permission)
    visible_ids = visible_records(ctx, Connector, connector_permission).with_only_columns(Connector.id)
    row = ctx.db.execute(
        select(Tool, ToolVersion, Connector, ConnectorVersion)
        .join(ToolVersion, ToolVersion.id == Tool.published_version_id)
        .join(Connector, Connector.id == Tool.connector_id)
        .join(ConnectorVersion, ConnectorVersion.id == Connector.published_version_id)
        .where(Tool.name == name, Tool.connector_id.in_(visible_ids), Tool.status == "published", Connector.status == "published")
    ).first()
    if row is None:
        raise NotFoundError("工具不存在或当前账号不可用")
    tool, version, connector, connector_version = row
    require_resource(ctx, connector_permission, connector)
    spec = ToolSpec.from_dict(version.spec)
    if any(item not in granted for item in spec.permissions):
        raise NotFoundError("工具不存在或当前账号不可用")
    spec.connector_id = connector.id
    spec.connector_version_id = connector_version.id
    spec.tool_id = tool.id
    spec.provider = connector.provider
    spec._connector_spec = connector_version.spec
    return spec


def validate_tool_arguments(ctx: AuthContext, name: str, arguments: dict) -> dict:
    """确定性校验工具参数（不调用模型、不执行工具）；权限 capabilities.execute。

    返回 {"tool","valid","arguments","schema"}；校验失败由 validate_arguments 抛
    SchemaValidationError，前端据此高亮字段。
    """
    spec = resolve_visible_tool(ctx, name, "capabilities.execute")
    return {"tool": name, "valid": True, "arguments": validate_arguments(arguments, spec.input_schema), "schema": spec.input_schema}


def _check_tool_name_conflicts(ctx: AuthContext, tools: list[dict], connector_id: str | None = None) -> None:
    """拒绝与内置工具或同工作区其它连接器重名的工具名。

    ``connector_id`` 用于"自身版本升级"场景：同连接器内的旧工具名不算冲突，
    否则任何改名/新增都会被已有记录挡住。冲突抛 ConflictError 并带上冲突名单。
    """
    builtin_names = {item.name for item in builtin_provider.builtin_specs()}
    names = {item["name"] for item in tools}
    conflict = names & builtin_names
    tool_table = Tool.__table__
    statement = select(tool_table.c.name).where(
        tool_table.c.workspace_id == ctx.workspace_id, tool_table.c.name.in_(names),
    )
    if connector_id:
        statement = statement.where(tool_table.c.connector_id != connector_id)
    conflict.update(ctx.db.scalars(statement).all())
    if conflict:
        raise ConflictError("工具名已被平台或其他连接器使用", names=sorted(conflict))


def _create_connector_record(ctx: AuthContext, payload: ConnectorInput) -> Connector:
    """通过同一权限、配置和重名检查创建草稿记录；事务由公开服务统一提交。"""
    require_permission(ctx, "connectors.create")
    config, tools = normalize_configuration(ctx, payload)
    _check_tool_name_conflicts(ctx, tools)
    row = Connector(
        id=new_id(), workspace_id=ctx.workspace_id, owner_user_id=ctx.user_id,
        name=payload.name, provider=payload.provider, visibility=payload.visibility,
        status="draft", created_at=_now(), updated_at=_now(),
    )
    ctx.db.add(row)
    ctx.db.flush()
    version = ConnectorVersion(
        id=new_id(), connector_id=row.id, version_number=1, status="draft", spec=config,
        created_by=ctx.user_id, created_at=_now(),
    )
    ctx.db.add(version)
    ctx.db.flush()
    for item in tools:
        tool = Tool(
            id=new_id(), workspace_id=ctx.workspace_id, connector_id=row.id,
            name=item["name"], description=item["description"], status="draft",
            created_at=_now(), updated_at=_now(),
        )
        ctx.db.add(tool)
        ctx.db.flush()
        ctx.db.add(ToolVersion(
            id=new_id(), tool_id=tool.id, connector_version_id=version.id, version_number=1,
            status="draft", spec=item, created_by=ctx.user_id, created_at=_now(),
        ))
    # 会话关闭了自动 flush；后续同事务发布必须能查询到最后一条工具版本。
    ctx.db.flush()
    return row


def create_connector(ctx: AuthContext, payload: ConnectorInput) -> dict:
    """创建并提交连接器草稿；管理台仍需单独授权并显式发布才能启用。"""
    row = _create_connector_record(ctx, payload)
    commit_change(ctx, "connector.create", "connector", row.id, {"provider": row.provider, "version": 1})
    return connector_view(ctx, row, include_versions=True)


def create_and_publish_connector(ctx: AuthContext, payload: ConnectorInput) -> dict:
    """对话注册的原子入口：创建和发布分别鉴权，两条审计与全部记录一次提交。

    不向请求模型增加自动发布开关；发布校验或提交失败时回滚整次注册，
    防止失败回执背后留下草稿、重名占用或已发布的半成品。
    """
    require_permission(ctx, "connectors.create")
    require_permission(ctx, "connectors.publish")
    try:
        row = _create_connector_record(ctx, payload)
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="connector.create", resource_type="connector", resource_id=row.id,
              metadata={"provider": row.provider, "version": 1})
        row, latest = _publish_connector_record(ctx, row.id, 1)
        # 在提交前构造回执，避免提交成功后才发现版本视图无法读取而误报失败。
        result = connector_view(ctx, row, include_versions=True)
        commit_change(ctx, "connector.publish", "connector", row.id, {"version": latest.version_number})
        return result
    except Exception:
        ctx.db.rollback()
        raise


def list_connectors(ctx: AuthContext, *, keyword: str = "", provider: str = "", limit: int = 100, offset: int = 0) -> dict:
    """连接器列表（按 connectors.view 数据范围过滤，已归档的不返回）；权限 connectors.view。

    返回 items + total（去掉分页的匹配总数）；列表视图含最新版本号与发布版本 ID，
    但不含连接配置明细（配置只在 get_connector / 版本详情里以脱敏形式出现）。
    """
    query = visible_records(ctx, Connector, "connectors.view").where(Connector.status != "archived")
    if keyword.strip():
        query = query.where(Connector.name.ilike(f"%{keyword.strip()}%"))
    if provider:
        query = query.where(Connector.provider == provider)
    total = ctx.db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = ctx.db.scalars(query.order_by(Connector.updated_at.desc(), Connector.id).limit(limit).offset(offset)).all()
    return {"items": [connector_view(ctx, row) for row in rows], "total": total, "limit": limit, "offset": offset}


def connector_view(ctx: AuthContext, row: Connector, *, include_versions: bool = False) -> dict:
    """连接器对外视图；``include_versions=True`` 时附带每个版本及其工具清单。

    版本配置经 _redacted_config 脱敏（只回显 secretId 与 hasSecret），
    因此本函数可以安全地用于接口返回，无需调用方再脱敏。
    """
    latest = ctx.db.scalar(select(ConnectorVersion).where(ConnectorVersion.connector_id == row.id)
                           .order_by(ConnectorVersion.version_number.desc()).limit(1))
    result = {
        "id": row.id, "name": row.name, "provider": row.provider,
        "providerLabel": PROVIDER_LABELS.get(row.provider, row.provider),
        "visibility": row.visibility, "status": row.status,
        "version": latest.version_number if latest else 0,
        "publishedVersionId": row.published_version_id,
        "updateTime": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_versions:
        versions = ctx.db.scalars(select(ConnectorVersion).where(ConnectorVersion.connector_id == row.id)
                                  .order_by(ConnectorVersion.version_number.desc())).all()
        tool_rows = ctx.db.execute(
            select(ToolVersion, Tool).join(Tool, Tool.id == ToolVersion.tool_id)
            .where(Tool.connector_id == row.id)
            .order_by(ToolVersion.version_number.desc(), Tool.name)
        ).all()
        by_version: dict[str, list[dict]] = {}
        for tool_version, tool in tool_rows:
            by_version.setdefault(tool_version.connector_version_id, []).append({
                "id": tool.id, "versionId": tool_version.id, "name": tool.name,
                "description": tool.description, "status": tool_version.status,
                "spec": _tool_view(ToolSpec.from_dict(tool_version.spec), status=tool_version.status),
            })
        result["versions"] = [{
            "id": version.id, "version": version.version_number, "status": version.status,
            "basedOnVersionId": version.based_on_version_id,
            "config": _redacted_config(version.spec), "tools": by_version.get(version.id, []),
            "createTime": version.created_at.isoformat(),
            "publishTime": version.published_at.isoformat() if version.published_at else None,
        } for version in versions]
    return result


def get_connector(ctx: AuthContext, connector_id: str) -> dict:
    """连接器详情（含全部版本与脱敏配置）；权限 connectors.view 且需在数据范围内。"""
    return connector_view(ctx, _connector(ctx, connector_id, "connectors.view"), include_versions=True)


def create_version(ctx: AuthContext, connector_id: str, payload: ConnectorVersionInput) -> dict:
    """基于当前最新版本创建新草稿版本（不可变版本链）；权限 connectors.edit。

    约束：``payload.version`` 必须等于当前最新版本号（乐观锁，冲突提示重新加载）；
    连接器类型不可在新版本中变更；工具名冲突检查会忽略本连接器自身的旧名。
    已存在同名工具则复用该 Tool 行并推进其版本号，新工具则新建（并触发发布后
    归档旧工具的规则）。副作用：写版本与工具版本表并提交，记录审计日志。
    """
    row = _connector(ctx, connector_id, "connectors.edit")
    latest = _latest_version(ctx, connector_id)
    if payload.version != latest.version_number:
        raise ConflictError("连接配置已被修改，请重新加载", current_version=latest.version_number)
    if payload.provider != row.provider:
        raise ValidationError("不能在新版本中改变连接器类型；请新建连接器")
    config, tools = normalize_configuration(ctx, payload)
    _check_tool_name_conflicts(ctx, tools, connector_id)
    next_number = latest.version_number + 1
    version = ConnectorVersion(
        id=new_id(), connector_id=row.id, version_number=next_number,
        based_on_version_id=latest.id, status="draft", spec=config,
        created_by=ctx.user_id, created_at=_now(),
    )
    ctx.db.add(version)
    ctx.db.flush()
    existing = {item.name: item for item in ctx.db.scalars(select(Tool).where(Tool.connector_id == row.id)).all()}
    for item in tools:
        tool = existing.get(item["name"])
        if tool is None:
            tool = Tool(
                id=new_id(), workspace_id=ctx.workspace_id, connector_id=row.id,
                name=item["name"], description=item["description"], status="draft",
                created_at=_now(), updated_at=_now(),
            )
            ctx.db.add(tool)
            ctx.db.flush()
            tool_number = 1
        else:
            tool.description = item["description"]
            tool.status = "draft"
            tool.updated_at = _now()
            tool_number = (ctx.db.scalar(select(func.max(ToolVersion.version_number)).where(ToolVersion.tool_id == tool.id)) or 0) + 1
        ctx.db.add(ToolVersion(
            id=new_id(), tool_id=tool.id, connector_version_id=version.id,
            version_number=tool_number, status="draft", spec=item,
            created_by=ctx.user_id, created_at=_now(),
        ))
    row.name = payload.name
    row.visibility = payload.visibility
    row.status = "draft"
    row.updated_at = _now()
    commit_change(ctx, "connector.version.create", "connector", row.id, {"version": next_number})
    return connector_view(ctx, row, include_versions=True)


def _publish_connector_record(ctx: AuthContext, connector_id: str, version_number: int) -> tuple[Connector, ConnectorVersion]:
    """发布指定版本的记录状态，事务由公开服务提交；权限 connectors.publish。

    发布后：连接器 published_version_id 与每个工具 published_version_id 全部指向
    该版本，工具网关只解析这一版本；本次版本里已移除的工具会被置为 archived，
    从而立即从模型工具列表消失（历史运行仍保留其原版本标识，可追溯）。
    版本号不匹配抛 ConflictError（要求重新加载），无工具则抛 ValidationError。
    """
    row = _connector(ctx, connector_id, "connectors.publish")
    latest = _latest_version(ctx, connector_id)
    if latest.version_number != version_number:
        raise ConflictError("只能发布当前最新草稿，请重新加载", current_version=latest.version_number)
    tool_versions = ctx.db.scalars(select(ToolVersion).where(ToolVersion.connector_version_id == latest.id)).all()
    if not tool_versions:
        raise ValidationError("当前版本没有工具，不能发布")
    latest.status = "published"
    latest.published_at = _now()
    row.status = "published"
    row.published_version_id = latest.id
    row.updated_at = _now()
    for tool_version in tool_versions:
        tool_version.status = "published"
        tool_version.published_at = _now()
        tool = ctx.db.get(Tool, tool_version.tool_id)
        if tool:
            tool.status = "published"
            tool.published_version_id = tool_version.id
            tool.updated_at = _now()
    active_tool_ids = {item.tool_id for item in tool_versions}
    for tool in ctx.db.scalars(select(Tool).where(Tool.connector_id == row.id, Tool.id.notin_(active_tool_ids))):
        tool.status = "archived"
        tool.published_version_id = None
        tool.updated_at = _now()
    return row, latest


def publish_connector(ctx: AuthContext, connector_id: str, version_number: int) -> dict:
    """管理台单独发布最新草稿，保持资源范围校验与独立审计、提交行为。"""
    row, latest = _publish_connector_record(ctx, connector_id, version_number)
    commit_change(ctx, "connector.publish", "connector", row.id, {"version": latest.version_number})
    return connector_view(ctx, row, include_versions=True)


def clone_connector(ctx: AuthContext, connector_id: str, payload: ConnectorCloneInput) -> dict:
    """把连接器复制为独立草稿（复制当前版本的配置与工具 Schema）；权限 connectors.create。

    工具名统一加 ``tool_prefix`` 前缀并截断到 100 字符，既保证不与源连接器冲突，
    也保证与其它连接器重名时被冲突检查拦住；副本可见性固定为 private（共享范围
    不随复制扩散），状态为 draft，需重新发布才会启用。
    异常：源连接器不可见抛 NotFoundError；当前版本无工具抛 ValidationError。
    """
    source = _connector(ctx, connector_id, "connectors.view")
    require_permission(ctx, "connectors.create")
    latest = _latest_version(ctx, source.id)
    records = ctx.db.execute(
        select(ToolVersion, Tool).join(Tool, Tool.id == ToolVersion.tool_id)
        .where(ToolVersion.connector_version_id == latest.id)
        .order_by(Tool.name)
    ).all()
    if not records:
        raise ValidationError("连接器当前版本没有可复制的工具")
    prefix = payload.tool_prefix.strip().rstrip(".") or ("copy_" + new_id()[-6:].lower())
    definitions = []
    for version, tool in records:
        raw = dict(version.spec or {})
        suffix = tool.name
        candidate = f"{prefix}.{suffix}"
        if len(candidate) > 100:
            candidate = candidate[:100].rstrip(".-_")
        definitions.append(ToolDefinition(
            name=candidate,
            description=str(raw.get("description") or tool.description or ""),
            inputSchema=dict(raw.get("input_schema") or raw.get("inputSchema") or {}),
            outputSchema=dict(raw.get("output_schema") or raw.get("outputSchema") or {}),
            permissions=list(raw.get("permissions") or []),
            timeoutSeconds=int(raw.get("timeout_seconds") or raw.get("timeoutSeconds") or 30),
            riskLevel=str(raw.get("risk_level") or raw.get("riskLevel") or "low"),
            requiresApproval=bool(raw.get("requires_approval") or raw.get("requiresApproval") or False),
        ))
    copied = create_connector(ctx, ConnectorInput(
        name=payload.name.strip() or (source.name + " 副本"),
        description="",
        provider=source.provider,
        visibility="private",
        config=dict(latest.spec or {}),
        tools=definitions,
    ))
    commit_change(ctx, "connector.clone", "connector", copied["id"], {
        "sourceId": source.id, "sourceVersion": latest.version_number,
    })
    return copied


def share_connector(ctx: AuthContext, connector_id: str, payload: ConnectorShareInput) -> dict:
    """修改连接器共享范围；权限 connectors.publish（共享属于发布级变更）。

    同样要求版本号匹配以做乐观锁：避免"共享范围"被基于旧视图的请求悄悄改回。
    共享只影响"谁能看到/使用"，不改变连接器绑定数据源自身的数据范围。
    """
    row = _connector(ctx, connector_id, "connectors.publish")
    latest = _latest_version(ctx, connector_id)
    if latest.version_number != payload.version:
        raise ConflictError("连接配置已被修改，请重新加载", current_version=latest.version_number)
    row.visibility = payload.visibility
    row.updated_at = _now()
    commit_change(ctx, "connector.share", "connector", row.id, {"visibility": row.visibility})
    return connector_view(ctx, row, include_versions=True)


def archive_connector(ctx: AuthContext, connector_id: str, version_number: int) -> None:
    """归档连接器（软删除，同时归档其全部工具）；权限 connectors.delete。

    不做物理删除：版本 spec 与历史 ToolCall 都保留，便于回溯旧运行的调用契约；
    归档后该连接器的工具立刻不可再被解析/执行（网关只认已发布状态）。
    返回 None（路由层用 MutationResult 包装响应）。
    """
    row = _connector(ctx, connector_id, "connectors.delete")
    latest = _latest_version(ctx, connector_id)
    if latest.version_number != version_number:
        raise ConflictError("连接配置已被修改，请重新加载", current_version=latest.version_number)
    row.status = "archived"
    row.updated_at = _now()
    for tool in ctx.db.scalars(select(Tool).where(Tool.connector_id == row.id)):
        tool.status = "archived"
        tool.updated_at = _now()
    commit_change(ctx, "connector.archive", "connector", row.id, {"retainedHistory": True})


def test_connector(ctx: AuthContext, connector_id: str, *, version_number: int, tool_name: str, arguments: dict) -> dict:
    """试运行连接器指定版本中的某个工具；权限 connectors.execute（+ 资源数据范围）。

    与正式调用完全同构：走同一个工具网关与同一份版本 spec（含已授权的 allow_private），
    因此测试结果对线上行为有参考价值；但不会改变任何已发布状态。
    返回：网关结果 + runId/stepId/toolCallId + 版本号（便于前端关联追踪）。
    异常：版本或工具不存在抛 NotFoundError，参数校验失败由网关抛 SchemaValidationError。
    """
    row = _connector(ctx, connector_id, "connectors.execute")
    version = ctx.db.scalar(select(ConnectorVersion).where(
        ConnectorVersion.connector_id == row.id, ConnectorVersion.version_number == version_number,
    ))
    if version is None:
        raise NotFoundError("指定连接器版本不存在")
    record = ctx.db.execute(
        select(ToolVersion, Tool).join(Tool, Tool.id == ToolVersion.tool_id)
        .where(ToolVersion.connector_version_id == version.id, Tool.name == tool_name)
    ).first()
    if record is None:
        raise NotFoundError("指定版本中不存在该工具")
    tool_version, tool = record
    spec = ToolSpec.from_dict(tool_version.spec)
    spec.provider = row.provider
    spec.connector_id = row.id
    spec.connector_version_id = version.id
    spec.tool_id = tool.id
    result = _execute_tool_test(
        ctx,
        spec=spec,
        arguments=arguments,
        connector_spec=version.spec,
        tool_version_id=tool_version.id,
        purpose="connector_test",
        audit_action="connector.test",
        audit_entity_type="connector",
        audit_entity_id=row.id,
        audit_metadata={"version": version.version_number},
    )
    return {**result, "version": version.version_number}


def _execute_tool_test(
    ctx: AuthContext,
    *,
    spec: ToolSpec,
    arguments: dict,
    connector_spec: dict,
    tool_version_id: str | None,
    purpose: str,
    audit_action: str,
    audit_entity_type: str,
    audit_entity_id: str,
    audit_metadata: dict | None = None,
) -> dict:
    """Execute one governed method through the same gateway used by production runs.

    Network transport is enabled only for providers that require it.  Private
    address access remains controlled by the administrator-approved
    ``allow_private`` value persisted in the immutable connector version.

    中文补充：试运行的公共实现（连接器测试与目录方法测试共用）。每次测试都会
    真实创建 Run/Step/ToolCall 记录并在结束时收口（complete/fail + finalize），
    因此"试运行"在监控与审计里与正式调用一样可见。
    网络开关只对 http/mcp 打开；本机/内网访问权限来自版本 spec 里管理员已批准的
    ``allow_private``，测试接口不能自行放宽这一授权。
    参数：``purpose``/``audit_action``/``audit_entity_*`` 决定落库的用途标记与审计条目。
    返回：网关结果 dict 增强 runId/stepId/toolCallId；业务失败以结果形式返回，不抛异常。
    """
    validated = validate_arguments(arguments, spec.input_schema)
    run = runtime.create_run(
        ctx.db, workspace_id=ctx.workspace_id, conversation_id="", user_id=ctx.user_id,
        context_snapshot={
            "purpose": purpose,
            "tool": spec.name,
            "connectorId": spec.connector_id,
            "connectorVersionId": spec.connector_version_id,
        },
        model_profile="none",
    )
    run.status = "running"
    run.started_at = _now()
    step = runtime.start_step(
        ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, sequence=1,
        step_type="tool", name=spec.name, input_data=validated,
    )
    call = runtime.record_tool_call(
        ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, step_id=step.id,
        tool_name=spec.name, tool_version_id=tool_version_id, arguments=validated,
    )
    # 每次目录试运行都有独立 Run；同一 Run 内的传输重试必须复用该键。
    call.idempotency_key = "capability-" + sha256(
        f"{run.id}:{spec.connector_version_id}:{spec.name}".encode("utf-8")
    ).hexdigest()
    ctx.db.commit()
    gateway = ToolGateway(ctx.db)
    execution = build_context(
        db=ctx.db, workspace_id=ctx.workspace_id, user_id=ctx.user_id,
        run_id=run.id, allow_network=spec.provider in {"http", "mcp"},
    )
    execution["idempotency_key"] = call.idempotency_key
    result = gateway.execute_spec(spec, validated, connector_spec, execution)
    output = result.to_dict()
    failed = result.status != "success"
    runtime.finish_tool_call(ctx.db, call, output, result.error_code if failed else None, result.error_summary if failed else None)
    if failed:
        runtime.fail_step(ctx.db, step, result.error_code or "tool_failed", result.error_summary or "工具测试失败")
    else:
        runtime.complete_step(ctx.db, step, output)
    runtime.finalize_run(
        ctx.db, run, status="failed" if failed else "completed",
        error_code=result.error_code if failed else None,
        error_summary=result.error_summary if failed else None,
    )
    commit_change(ctx, audit_action, audit_entity_type, audit_entity_id, {
        **(audit_metadata or {}), "runId": run.id, "status": result.status,
    })
    return {**output, "runId": run.id, "stepId": step.id, "toolCallId": call.id}


def test_method(ctx: AuthContext, tool_name: str, arguments: dict) -> dict:
    """Test a published builtin or connector method from the method catalog.

    中文补充：方法目录里的"试运行"，内置工具与已发布连接器工具都支持；
    权限 capabilities.execute，工具不可用（停用/越权/未发布）统一报 NotFoundError。
    返回网关结果 + provider/connectorId + runId 等追踪字段。
    """
    spec = resolve_visible_tool(ctx, tool_name, "capabilities.execute")
    connector_spec = dict(getattr(spec, "_connector_spec", None) or {})
    tool_version_id = None
    if spec.tool_id:
        tool = ctx.db.get(Tool, spec.tool_id)
        tool_version_id = tool.published_version_id if tool else None
    result = _execute_tool_test(
        ctx,
        spec=spec,
        arguments=arguments,
        connector_spec=connector_spec,
        tool_version_id=tool_version_id,
        purpose="capability_method_test",
        audit_action="capability.method.test",
        audit_entity_type="tool_method",
        audit_entity_id=tool_name,
        audit_metadata={"provider": spec.provider, "connectorId": spec.connector_id},
    )
    return {**result, "provider": spec.provider, "connectorId": spec.connector_id}


def compile_tool_arguments(ctx: AuthContext, name: str, intent: str, references: dict) -> dict:
    """把自然语言需求编译为该工具的参数；权限 capabilities.execute。

    使用当前工作区的模型配置（profile=reasoning）生成参数，随后由 validator 做
    确定性校验；全过程记录为一次真实 Run（step_type=model），成功/失败都会
    finalize，便于在运行记录里复盘"模型为什么产出这个参数"。
    ``references`` 是调用方提供的、已授权的引用数据（不再访问数据库）。
    异常：编译或校验失败会先收口 Run 再原样抛出（含错误码）。
    """
    require_permission(ctx, "capabilities.execute")
    spec = resolve_visible_tool(ctx, name, "capabilities.execute")
    from app.providers.tool.compiler import compile_arguments, reference_resolver
    run = runtime.create_run(
        ctx.db, workspace_id=ctx.workspace_id, conversation_id="", user_id=ctx.user_id,
        context_snapshot={"purpose": "tool_argument_compile", "tool": name}, model_profile="reasoning",
    )
    run.status = "running"
    run.started_at = _now()
    step = runtime.start_step(
        ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, sequence=1,
        step_type="model", name="编译工具参数", input_data={"tool": name, "intent": intent[:2000]},
    )
    ctx.db.commit()
    try:
        arguments = compile_arguments(
            spec, intent, context={"references": references},
            resolver=reference_resolver(references),
            workspace_id=ctx.workspace_id,
        )
        runtime.complete_step(ctx.db, step, {"arguments": arguments})
        runtime.finalize_run(ctx.db, run, status="completed")
        ctx.db.commit()
        return {"tool": name, "arguments": arguments, "schema": spec.input_schema, "runId": run.id}
    except Exception as exc:
        runtime.fail_step(ctx.db, step, getattr(exc, "code", "compile_failed"), str(exc))
        runtime.finalize_run(ctx.db, run, status="failed", error_code=getattr(exc, "code", "compile_failed"), error_summary=str(exc))
        ctx.db.commit()
        raise


def generate_from_run(ctx: AuthContext, run_id: str, target: str) -> dict:
    """从一次运行记录沉淀出 Skill 或 Plugin 草稿（target 决定类型）。

    权限：capabilities.create + runs.view + ``{target}s.create``，并对运行记录做
    资源级校验（只允许读取自己发起的运行，owner_field=started_by_user_id）。
    生成结果保持草稿状态，需人工确认后启用；实际生成逻辑复用 capability/service
    的 legacy_generate，本函数只负责权限与资源范围的把关。
    异常：运行不存在或不在数据范围内抛 NotFoundError。
    """
    require_permission(ctx, "capabilities.create")
    require_permission(ctx, "runs.view")
    require_permission(ctx, f"{target}s.create")
    run = ctx.db.get(Run, run_id)
    if run is None:
        raise NotFoundError("运行记录不存在")
    require_resource(ctx, "runs.view", run, owner_field="started_by_user_id")
    from app.modules.capability.service import generate_from_run as legacy_generate
    return legacy_generate(
        ctx.db, workspace_id=ctx.workspace_id, run_id=run.id, want=target,
        owner_user_id=ctx.user_id,
    )
