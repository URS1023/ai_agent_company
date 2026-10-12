"""工作区（workspace）模块包：当前工作区信息与工作区成员 / 用户管理。

对外路由前缀 ``/api/workspace``（见 ``router.py``），供前端工作区设置页与后台用户管理页调用。
所有读写都限定在 ``ctx.workspace_id``（组织归属判断用 ``ctx.workspace.organization_id``）内。

约束：成员与用户的写操作属于管理动作，除 ``require_admin``（即 ``system.admin``）外还叠加
``system.users.assign_roles`` 等细粒度权限，并要求业务写入与 ``workspace.add_member`` 审计
记录同事务提交（``commit_change``），不允许只改数据不落审计。
"""
