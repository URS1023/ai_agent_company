"""Model profile governance: configuration, connectivity probes and the usage ledger.

中文补充（模型与用量治理包）：本包负责"模型角色（profile）怎么配、能不能通、花了多少"，
不含模型调用的实际实现。包入口不导出任何符号，调用方按 ``model.router`` / ``model.service``
等具体模块导入。

包内分工：``router.py`` 是对外 HTTP 入口（路由前缀 ``/api/models``，tags=["模型与用量治理"]），
``service.py`` 承担配置读写、密钥引用、连通性探测、计价归一化、用量汇总与预算闸门，
``models.py`` 存放本包自有的两张表（``environment_model_probes``、``model_usage_records``）。

关键依赖与位置（跨模块契约，改动前先确认对方）：
- 上游调用方：``providers/model.py``（Model Gateway）在每次真实调用前调 ``service.enforce_budget``、
  调用后调 ``service.record_usage``，并用 ``_profile_config`` 解析 profile；因此本包的表结构与
  这两个函数签名是对外契约，不能随意改语义。
- 同层依赖：密钥只存引用，明文交给 ``modules.secret``（本包落在 ``capabilities_json.secret_id``）；
  预算策略复用 ``modules.system`` 的 ``SystemSetting``（key = ``models.cost_policy``）。
- 下游消费方：``modules/chat/model_selection.py`` 读 ``service.list_effective_profiles``
  组装对话模型选择器。

约束：模型配置、密钥引用、单价与用量账本全部按 ``workspace_id`` 隔离；列表接口只回显
"是否已配置密钥"，绝不返回密文。本包路由属于平台管理域（``models.*``），内置"业务成员"
角色默认不含这些权限，只由管理员显式授予。
"""
