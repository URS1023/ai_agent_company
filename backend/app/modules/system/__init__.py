"""Users, organization structure, roles and authorization management.

职责：system（系统管理）模块的包入口，聚合成员与组织架构、角色与授权、资源授权、
菜单/参数/字典、组织与工作区生命周期、工作区安全策略与 OIDC 单点登录。

包内约定：
- 路由：router.py 与 governance_router.py 是唯一对外出口，统一挂在 /api/v1/system；
  本包 __init__ 刻意不导入任何子模块，避免启动时的循环导入（service 之间互相引用较密）。
- 分层：*_service.py 负责业务与事务，service_helpers.py 提供跨服务的并发/审计约束，
  models.py / schemas.py / *_schemas.py 只描述数据结构，permissions.py 是权限编码的唯一来源。
- 约束：所有表带 workspace_id，读写一律以工作区为界；敏感凭据不落本模块的表（走密钥模块）。
"""
