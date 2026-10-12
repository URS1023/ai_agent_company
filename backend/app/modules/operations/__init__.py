"""Authorized run tracing, health, recovery and audit operations.

中文补充：运维中心模块。对外只挂载 `/api/v1/operations` 一个路由前缀（见
`router.py`），数据全部来自当前工作区持久化记录（runs / run_steps / tool_calls /
model_calls / run_events / artifacts / audit_logs，以及知识库、Bot、应用三张业务表），
不引入独立缓存或外部系统。除 `service.recover` 在 `dryRun=false` 时改写运行状态并写审计外，
其余查询接口均为只读，且每个视图都按角色权限与其数据范围二次过滤。
"""
