"""Request-scoped ORM criteria for Cloud Brain resources and their child records.

These guards complement service action checks. They do not authorize arbitrary
SQL against external data sources, which must use the analysis/query gateway.

职责：把"当前用户能看到哪些数据"从散落在各服务层的 where 条件，收敛成一套**查询级兜底**条件。
绑定一次之后，本次请求内该 Session 上所有对已登记模型的 SELECT / UPDATE / DELETE 都会被自动附加
可见性约束（含延迟加载的关联对象），服务层即使忘记过滤也不会越权读出/改到别人的数据。

系统位置：判定链的第三段（认证 → 权限 → **数据范围** → 资源归属）。它复用
``common.authorization`` 的结论（access_policy / allowed_owner_ids / resource_grant_ids /
private_visibility_predicate），再由 ``common/owned_records`` 对单条记录做显式过滤与复核。

关键机制：
- **默认拒绝**：某动作不在当前策略的权限集合里时，对应模型的条件是 ``false()``（查询恒空、
  批量更新/删除影响 0 行），而不是"不加条件"。因此漏配权限不会变成放行。
- 条件按模型登记在三张表里：ROOT_RESOURCES（直接判定）、CONFIG_RESOURCES（仅当请求动作落在该域下
  才纳入，用于避免普通用户因执行连接器/模型而获得密钥列表权限）、CHILD_RESOURCES（子表继承父表条件，
  防止通过子记录绕过父记录的可见性）。
- 唯一一处刻意放宽：知识库请求期间，被授权知识库引用的源文件也可读（见 bind_data_scope 内的说明），
  这不等于文件系统权限。

约束：
- 必须在加载业务数据之前调用（common.deps 在依赖阶段调用）；绑定后条件不可撤销、不可更换动作，
  同一请求内若要按另一个动作放宽必须显式再调 ``bind_data_scope(ctx, permission)``（analytics 等
  模块即这样按动作重建）。
- 只对 **ORM** 语句生效：``text()`` / 原生 SQL 不受约束，因此对外部数据源的任意查询必须走
  analysis/query 网关，不能靠本模块兜底。
- 未认证路径（bootstrap、登录前接口）不使用本模块——它们的会话 ``info`` 中没有 criteria，
  事件监听器即空转；反过来说，新增加密/引导类接口时不要给它们绑定数据范围。
"""
from __future__ import annotations

from sqlalchemy import and_, event, false, or_, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app import models
from app.common.authorization import access_policy, allowed_owner_ids, private_visibility_predicate, resource_grant_ids
from app.common.deps import AuthContext

# 根资源登记表：(模型, 权限域, 创建人列名)。域必须与权限码前缀一致（同时用作显式资源授权的
# resource_type），创建人列名按模型实际列名填（Run 用 started_by_user_id，其余多为 owner_user_id）。
# 漏登记一张表 = 该表没有任何查询级兜底，只能靠服务层自觉；新增业务表时必须在这里登记。
ROOT_RESOURCES = (
    (models.File, "files", "owner_user_id"),
    (models.Artifact, "files", "owner_user_id"),
    (models.KnowledgeBase, "knowledge", "owner_user_id"),
    (models.DataSource, "datasources", "owner_user_id"),
    (models.Skill, "skills", "owner_user_id"),
    (models.Plugin, "plugins", "owner_user_id"),
    (models.Connector, "connectors", "owner_user_id"),
    (models.Conversation, "chat", "owner_user_id"),
    (models.Run, "runs", "started_by_user_id"),
)
# Configuration inspection is scoped at its own API boundary. A user who may
# execute an authorized connector/model may use its referenced encrypted secret
# without being granted permission to list or manage secrets themselves.
# 中文补充：models / secrets 只在"请求动作本身就属于该域"时才纳入过滤（见 bind_data_scope 的
# startswith 判断）。否则普通用户一执行连接器就会顺带拿到密钥与模型配置的可见范围，等于绕过
# models.view / secrets.view 的授权。
CONFIG_RESOURCES = (
    (models.ModelProfile, "models", None),
    (models.Secret, "secrets", "owner_user_id"),
)
# 子资源登记表：(模型, 指向父表的外键列, 父模型)。父模型必须已出现在 ROOT_RESOURCES 中，
# 否则计算条件时会 KeyError——这是登记错误，必须补登父表而不是加兜底分支。
# 子表条件 = "外键属于可见父记录"（模型自身带 workspace_id 时再 AND 一次），
# 因此父记录的可见性变化会立即传导到消息、分块、版本、运行步骤等子记录。
CHILD_RESOURCES = (
    (models.Message, "conversation_id", models.Conversation),
    (models.KnowledgeDocument, "knowledge_base_id", models.KnowledgeBase),
    (models.KnowledgeChunk, "knowledge_base_id", models.KnowledgeBase),
    (models.SkillVersion, "skill_id", models.Skill),
    (models.PluginVersion, "plugin_id", models.Plugin),
    (models.ConnectorVersion, "connector_id", models.Connector),
    (models.Tool, "connector_id", models.Connector),
    (models.ToolVersion, "tool_id", models.Tool),
    (models.RunStep, "run_id", models.Run),
    (models.ToolCall, "run_id", models.Run),
    (models.RunEvent, "run_id", models.Run),
    (models.RunResourceSnapshot, "run_id", models.Run),
)


def bind_data_scope(ctx: AuthContext, permission: str | None = None) -> None:
    """Bind immutable criteria to this request's Session before loading business data.

    中文补充：把本次请求的可见性条件写进 ``ctx.db.info``，事件监听器再把它附加到此后所有 ORM 查询。

    参数：
    - ``ctx``：认证上下文（提供 Session、工作区、用户与策略缓存）；
    - ``permission``：本次请求的动作码。省略时取 ``ctx.request_permission``（由
      ``enforce_request_permission`` 在旧路径上写好）；两者都没有时按每个域的 ``<域>.view`` 兜底。

    返回：无。

    业务异常：PermissionErrorEx(403) —— allowed_owner_ids 内部会调用 require_permission，
    因此动作本身未授权时这里就会失败（有权限校验的路径上是冗余的二次确认，属预期）。

    副作用（都是本次请求内的内存状态，不写库、不写审计）：
    - ``ctx.db.info["resource_access_criteria"]``：with_loader_criteria 选项元组；
    - ``ctx.db.info["actor_user_id"] / ["actor_workspace_id"]``：操作者标识，供服务层记录/审计取值，
      不要用它替代 AuthContext 做权限判断。

    调用时机约束：必须在加载业务数据**之前**（依赖阶段）调用；绑定只做一次性计算，之后无法收窄或
    撤销，需要换动作必须重新调用本函数（不要直接改 info 里的选项）。

    计算顺序约束（改动时注意）：先算根资源 → 再应用知识库对源文件的例外 → 最后用父条件派生
    子表条件，因为子表条件直接引用 ``predicates[父模型]``，顺序颠倒会拿到未过滤（或缺失）的条件。
    """
    policy = access_policy(ctx)
    request_permission = permission or getattr(ctx, "request_permission", None)
    predicates = {}
    scopes = {}
    resources = ROOT_RESOURCES + tuple(item for item in CONFIG_RESOURCES
                                      if request_permission and request_permission.startswith(item[1] + "."))
    for model, domain, owner_field in resources:
        action = request_permission if request_permission and request_permission.startswith(domain + ".") else domain + ".view"
        if action not in policy.permissions:
            # 默认拒绝：没有该动作权限时给恒假条件（查询恒空、批量更新/删除影响 0 行），
            # 绝不能"跳过过滤"——跳过等于把整表暴露给未授权用户。
            predicates[model] = false()
            continue
        if action not in scopes:
            # 同一动作只解析一次：allowed_owner_ids / resource_grant_ids 各自都要查库。
            scopes[action] = (allowed_owner_ids(ctx, action), resource_grant_ids(ctx, domain, action.rsplit(".", 1)[-1]))
        owners, granted_ids = scopes[action]
        predicate = model.workspace_id == ctx.workspace_id
        if owners is not None:
            # 工作区级配置没有个人所有者，个人范围只能读取显式授权的记录。
            owner_match = getattr(model, owner_field).in_(owners) if owner_field else false()
            predicate = and_(predicate, or_(owner_match, model.id.in_(granted_ids)))
        visibility = private_visibility_predicate(ctx, model)
        if visibility is not None:
            predicate = and_(predicate, visibility)
        predicates[model] = predicate
    # A knowledge grant includes the source files only while resolving a
    # knowledge request. It does not grant /api/files listing or download.
    # 中文补充：例外只在本请求动作属于 knowledge 域时生效，且只放宽到"被可见知识库引用的文件"，
    # 因此它既不能让用户列出文件，也不能下载未被任何可见知识库引用的文件。
    if request_permission and request_permission.startswith("knowledge."):
        allowed_kb_ids = select(models.KnowledgeBase.id).where(predicates[models.KnowledgeBase])
        referenced_file_ids = select(models.KnowledgeDocument.file_id).where(
            models.KnowledgeDocument.workspace_id == ctx.workspace_id,
            models.KnowledgeDocument.knowledge_base_id.in_(allowed_kb_ids),
        )
        predicates[models.File] = or_(
            predicates[models.File],
            and_(models.File.workspace_id == ctx.workspace_id, models.File.id.in_(referenced_file_ids)),
        )
    for model, parent_field, parent in CHILD_RESOURCES:
        # 子表条件直接引用父表条件：父模型必须先于本循环写入 predicates（见函数 docstring 的顺序约束）。
        predicate = getattr(model, parent_field).in_(select(parent.id).where(predicates[parent]))
        if hasattr(model, "workspace_id"):
            predicate = and_(model.workspace_id == ctx.workspace_id, predicate)
        predicates[model] = predicate
    # include_aliases 与 propagate_to_loaders 都不能去掉：前者让带别名（自连接/联表）的查询同样受限，
    # 后者让 relationship 的延迟加载也带上条件，否则关联对象会成为绕过过滤的侧门。
    ctx.db.info["resource_access_criteria"] = tuple(
        with_loader_criteria(model, predicate, include_aliases=True, propagate_to_loaders=True)
        for model, predicate in predicates.items()
    )
    ctx.db.info["actor_user_id"] = ctx.user_id
    ctx.db.info["actor_workspace_id"] = ctx.workspace_id


@event.listens_for(Session, "do_orm_execute")
def _guard_orm_access(state: ORMExecuteState) -> None:
    """Apply scope to ORM reads and bulk writes; unauthenticated bootstrap is separate.

    中文补充：进程级的 Session 事件监听器（导入本模块即注册，对所有 Session 生效），在语句执行前
    附加可见性条件。

    为什么能同时管住写：is_update / is_delete 也一并处理，因此 bulk update/delete 只会命中当前
    可见范围内的行——未授权动作的恒假条件会让它影响 0 行，而不是报错，服务层需要自己判断影响行数。

    约束：
    - 只有会话 ``info`` 里存在 criteria 时才动手（即已通过 bind_data_scope 的请求），其它会话
      （worker、定时任务、bootstrap）不受影响但也不受保护，需要时各自显式绑定；
    - 对原生 SQL / text() 语句不生效（没有可附加条件的 ORM 实体），所以外部数据源查询必须走
      analysis/query 网关；
    - 本函数必须保持幂等且无副作用，事件在每条语句上都会触发。
    """
    options = state.session.info.get("resource_access_criteria")
    if options and (state.is_select or state.is_update or state.is_delete):
        state.statement = state.statement.options(*options)
