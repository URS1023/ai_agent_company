"""Shared Python workflows for analytics, device alerts and quality inspection.

中文补充：本包是"通用分析 / 设备预警 / 质量检查"三类业务共用的可视化工作流引擎。
它在系统中的位置与边界如下：

- 对外接口只有 ``router.py`` 暴露的 ``/api/v1/workflows/*`` 路由；本包其余模块都是内部实现，
  不应被其他模块直接当作数据访问层调用。
- 关键依赖：``schemas.py`` 定义图与每个节点的配置契约，``catalog.py`` 维护节点目录与默认配置，
  ``graph.py`` 负责发布/执行前的图校验与当前资源授权，``engine.py`` 按拓扑序执行 DAG，
  ``nodes.py`` 提供具体节点执行器，``execution.py`` 负责幂等启动、恢复、取消与历史查询。
- 重要约束：节点执行一律通过 ``workflows.execute`` 权限访问数据源；执行结果同时写入统一运行中心
  （``runs``/``run_steps``）和 ``workflow_executions``；已发布版本不可变，草稿编辑不影响正在运行的历史。
"""
