"""Equipment and alert records; original evidence is separate from issue handling.

职责：设备预警业务域的持久化模型（SQLAlchemy 2.0 声明式）—— 设备台账、测点、专家经验、
预警策略与其不可变修订、报告、问题与处置事件，以及向导的幂等回执。

位置与依赖：所有服务层（``registry`` / ``reports`` / ``workflow_builder`` / ``device_catalog``）
共用这一组表；``Device`` 同时被质检（quality）业务复用，因此设备字段变更会影响两个业务域。
表结构由 ``Base.metadata`` 建表，列注释即业务语义说明。

关键不变式（模型层面保证）：
- 证据不可变：``AlertRuleRevision.snapshot`` 与 ``AlertProblem.finding`` 一旦写入不再修改，
  人工处置只改 ``AlertProblem`` 的 ``status`` / ``note`` / ``version``，历史在
  ``AlertProblemEvent`` 中只增不改。
- 唯一约束即业务约束：设备编码工作区内唯一、测点编码设备内唯一、规则修订
  ``(rule_id, revision)`` 唯一、报告 ``(execution_id, node_id)`` 唯一（一次执行一个节点只出一份报告）、
  向导回执 ``(workspace_id, owner_user_id, request_key)`` 唯一（防重复创建）。
- 删除一律软删（status='deleted'），以便历史报告与处置记录仍可追溯。
"""
from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.owned_records import OwnedRecord
from app.models import Base


class Device(OwnedRecord, Base):
    """One equipment registry shared by alerts and quality inspections.

    （中文补充）设备台账主表，**预警与质检共用**：``domains`` 决定其可参与的业务
    （``alert`` / ``quality``，至少一个），``datasource_id`` 是采集源，质检另用
    ``DeviceQualitySource`` 关联多个 HTTP 检测服务。
    ``status`` 取值 active/inactive/maintenance/deleted：后三者都不允许开始新的检测/预警，
    但历史报告仍按原快照可读。``code`` 在工作区内唯一且删除后不再复用（保留编码供追溯）。
    """

    __tablename__ = "equipment_devices"
    __table_args__ = (UniqueConstraint("workspace_id", "code", name="uq_equipment_code"),)
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="工作区内设备唯一编码")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="设备名称")
    category: Mapped[str] = mapped_column(String(100), default="", nullable=False, comment="设备类型")
    model: Mapped[str] = mapped_column(String(100), default="", nullable=False, comment="型号")
    manufacturer: Mapped[str] = mapped_column(String(160), default="", nullable=False, comment="生产商")
    location: Mapped[str] = mapped_column(String(300), default="", nullable=False, comment="安装位置")
    protocol: Mapped[str] = mapped_column(String(40), default="HTTP", nullable=False, comment="协议标识，不代表适配器已连接")
    datasource_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("data_sources.id"), nullable=True, comment="共享采集源，凭据由数据源管理")
    domains: Mapped[list] = mapped_column(JSON, default=list, nullable=False, comment="适用业务 alert/quality")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="备注")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/maintenance/deleted")


class DeviceQualitySource(OwnedRecord, Base):
    """Additional HTTP detector services authorized for one physical device.

    （中文补充）设备 ↔ 质检服务的**授权白名单**（一设备多服务），只允许 HTTP 数据源
    （保存时由 registry 校验 provider）。记录顺序按 ``created_at, id`` 稳定输出，
    设备视图中的 ``qualityDatasourceIds`` 即来源于此。
    约束：某服务从该表移除即表示设备不再获准使用它；仅"设备主数据源"可见并不等于这些服务可见，
    设备整体可见性要求所有关联服务都在调用者权限范围内。
    """

    __tablename__ = "equipment_device_quality_sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "device_id", "datasource_id", name="uq_equipment_quality_source"),
    )
    device_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("equipment_devices.id"), nullable=False, index=True, comment="所属设备",
    )
    datasource_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("data_sources.id"), nullable=False, index=True, comment="获准用于质检节点的 HTTP 服务",
    )


class BusinessCardImage(OwnedRecord, Base):
    """Optional catalogue photo; independent of source data and inspection evidence.

    （中文补充）设备/产品名片图与"图片文件"的关联行。``kind`` 取 device/product，
    ``target_id`` 分别是设备 id 或产品目录中的 ``productId``（产品不是库表，故此处是弱引用，
    不设外键）。``file_id`` 置空表示已解除关联（文件本身保留），``version`` 用于上传/删除的
    乐观锁（未上传过时前端应传 0）。``(workspace_id, kind, target_id)`` 唯一：一个对象一张图。
    本表只存图片引用，与数据源、检测证据无关。
    """

    __tablename__ = "business_card_images"
    __table_args__ = (UniqueConstraint("workspace_id", "kind", "target_id", name="uq_business_card_image"),)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(80), nullable=False)
    file_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("files.id"), nullable=True)


class MeasurementPoint(OwnedRecord, Base):
    """Named equipment field and source mapping, without pretending to schedule it.

    （中文补充）测点 = "设备上的一个具名字段"与"采集结果里的字段名"的映射，
    向导据此生成 JSONPath 提取字段。``field_name`` 在**所选测点集合内不得重复**（否则提取会覆盖）；
    同一设备内 ``code`` 唯一，且测点不能移动到另一设备（只能新建）。
    ``frequency_minutes`` 只是期望间隔，保存台账**不会**自动创建定时任务；
    真正排期由调度任务负责。
    """

    __tablename__ = "equipment_measurement_points"
    __table_args__ = (UniqueConstraint("device_id", "code", name="uq_equipment_point_code"),)
    device_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_devices.id"), nullable=False, index=True, comment="所属设备")
    code: Mapped[str] = mapped_column(String(80), nullable=False, comment="设备内测点号")
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="测点名称")
    field_name: Mapped[str] = mapped_column(String(120), nullable=False, comment="采集后记录中的完整字段名")
    unit: Mapped[str] = mapped_column(String(40), default="", nullable=False, comment="计量单位")
    point_type: Mapped[str] = mapped_column(String(40), default="number", nullable=False, comment="测点类型标识")
    device_key: Mapped[str] = mapped_column(String(120), default="", nullable=False, comment="来源设备 key")
    prefix: Mapped[str] = mapped_column(String(120), default="", nullable=False, comment="来源前缀")
    frequency_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="期望采集间隔，不自动创建定时任务")
    failure_diagnosis: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否用于故障诊断")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="测点说明")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")


class ExpertAdvice(OwnedRecord, Base):
    """Reusable operating-condition knowledge linked by explicit revision snapshots.

    （中文补充）专家经验（工况 + 告警条件 + 处置预案）是可复用知识，**不是判定依据本身**：
    规则修订时会把经验内容快照进 ``AlertRuleRevision.snapshot["advice"]``，
    因此事后编辑经验不会改变已发布流程的判据与预案文本。
    """

    __tablename__ = "equipment_expert_advice"
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="专家经验名称")
    condition_code: Mapped[str] = mapped_column(String(80), default="", nullable=False, comment="工况编码")
    operating_condition: Mapped[str] = mapped_column(String(300), default="", nullable=False, comment="适用工况")
    alert_type: Mapped[str] = mapped_column(String(80), default="", nullable=False, comment="告警类型")
    condition_description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="告警条件说明")
    content: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="经验内容")
    response_plan: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="处置预案")
    plan_short: Mapped[str] = mapped_column(String(200), default="", nullable=False, comment="预案简称")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")


class AlertRule(OwnedRecord, Base):
    """User-maintained alert policy; graphs pin one immutable rule revision.

    （中文补充）预警策略的"当前版本"。``rules_json`` 是逐项异常条件（条件/级别/说明），
    保存时校验表达式合法性与字段引用；``record_key_field`` 用于把问题定位到源记录，
    留空则退化为报告中的行号。停用后不能开始新的执行，但历史报告与修订仍可追溯。
    每次保存都会生成一条 ``AlertRuleRevision``（修订号 = 保存后的 ``version``）。
    """

    __tablename__ = "equipment_alert_rules"
    name: Mapped[str] = mapped_column(String(160), nullable=False, comment="规则名称")
    description: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="业务用途")
    device_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_devices.id"), nullable=False, index=True, comment="受检设备")
    advice_id: Mapped[str | None] = mapped_column(String(26), ForeignKey("equipment_expert_advice.id"), nullable=True, comment="关联专家经验")
    record_key_field: Mapped[str] = mapped_column(String(120), default="", nullable=False, comment="问题定位记录字段；空则使用行号")
    rules_json: Mapped[list] = mapped_column(JSON, nullable=False, comment="逐项异常条件、级别及说明")
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, comment="active/inactive/deleted")


class AlertRuleRevision(OwnedRecord, Base):
    """Immutable policy and advice contents retained for published workflows.

    （中文补充）策略 + 关联专家经验的**不可变快照**，是"跑哪一套判据"的唯一依据：
    已发布流程固定 ``revision``，因此后续编辑策略不会改变既有流程的判定口径。
    ``(rule_id, revision)`` 唯一；``revision`` 取自规则保存时的 ``version``，
    所以修订号可能因删除/停用等操作跳号，不保证连续。
    """

    __tablename__ = "equipment_alert_rule_revisions"
    __table_args__ = (UniqueConstraint("rule_id", "revision", name="uq_equipment_alert_revision"),)
    rule_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_alert_rules.id"), nullable=False, index=True, comment="规则标识")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="固定修订号")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="规则与专家经验的不可变快照")


class AlertReport(OwnedRecord, Base):
    """One actual node evaluation; execution completion is reported separately.

    （中文补充）一次预警节点判定的完整证据：``snapshot`` 存规则修订、设备与工况快照，
    ``result_json`` 存实际数据、逐条判定、问题与摘要（导出报告直接取用）。
    ``outcome`` 取值 issues/normal/no_data，是**本次判定**的结论，
    与 ``executionStatus``（整个流程是否跑完）必须分开解释：流程失败不代表判定正常，
    ``no_data`` 表示无参与记录、不做正常判定。
    ``(execution_id, node_id)`` 唯一，保证同一执行同一节点只落一份报告。
    """

    __tablename__ = "equipment_alert_reports"
    __table_args__ = (UniqueConstraint("execution_id", "node_id", name="uq_equipment_alert_evaluation"),)
    rule_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_alert_rules.id"), nullable=False, index=True, comment="规则标识")
    device_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_devices.id"), nullable=False, index=True, comment="设备标识")
    execution_id: Mapped[str] = mapped_column(String(26), ForeignKey("workflow_executions.id"), nullable=False, index=True, comment="实际工作流执行")
    node_id: Mapped[str] = mapped_column(String(80), nullable=False, comment="实际预警节点")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="报告标题")
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, comment="issues/normal/no_data，不等于整个流程状态")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="规则修订、设备与工况快照")
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False, comment="实际数据、逐条判断、问题和摘要")


class AlertProblem(OwnedRecord, Base):
    """Original findings never change when a human handles a problem.

    （中文补充）报告中的一条原始发现。``finding``（规则项、级别、说明）与 ``row_index`` /
    ``record_key`` 都不可变，人工处置只更新 ``status`` / ``note`` / ``version``：
    - ``status`` 取值 ``open``（待处理，初始值）/ ``acknowledged``（已知悉）/ ``resolved``（已解决）
      / ``ignored``（已忽略）；后端不校验迁移合法性，任意状态可互转，业务以必填理由约束；
    - ``record_key`` 来自策略配置的 ``recordKeyField``，缺失时用行号（从 1 起）作为定位键，
      因此它与 ``row_index``（从 0 起）口径不同，展示时不要混用。
    同名设备/记录可能在不同轮次产生多条问题，本表**不做跨报告去重**。
    """

    __tablename__ = "equipment_alert_problems"
    report_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_alert_reports.id"), nullable=False, index=True, comment="来源报告")
    row_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="报告中的原始行号，从 0 起")
    record_key: Mapped[str] = mapped_column(String(500), nullable=False, comment="来源记录定位键")
    finding: Mapped[dict] = mapped_column(JSON, nullable=False, comment="原始规则项、级别、说明，不受人工处理影响")
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, comment="open/acknowledged/resolved/ignored")
    note: Mapped[str] = mapped_column(Text, default="", nullable=False, comment="最新处置说明")


class AlertProblemEvent(OwnedRecord, Base):
    """Append-only review history including reopen/ignore reasons.

    （中文补充）问题处置的审计流水：只追加、不修改、不删除，``previous_status`` →
    ``next_status`` 配 ``note``（必填理由）记录每一次人工动作，含"重新打开"与"忽略"。
    ``owner_user_id`` 即处置人；查询只按 ``problem_id`` 取，权限经父问题/报告链校验。
    """

    __tablename__ = "equipment_alert_problem_events"
    problem_id: Mapped[str] = mapped_column(String(26), ForeignKey("equipment_alert_problems.id"), nullable=False, index=True, comment="问题标识")
    previous_status: Mapped[str] = mapped_column(String(20), nullable=False, comment="变更前状态")
    next_status: Mapped[str] = mapped_column(String(20), nullable=False, comment="变更后状态")
    note: Mapped[str] = mapped_column(Text, nullable=False, comment="人工处置理由")


class AlertWorkflowCreation(OwnedRecord, Base):
    """Idempotency receipt for one ordinary-page alert workflow publication.

    （中文补充）向导的防重复回执：``(workspace_id, owner_user_id, request_key)`` 唯一，
    ``request_hash`` 是除该键外整份配置的摘要 —— 同键同摘要返回既有结果，
    同键不同摘要报 409，同键但流程后来被改动/停用也报 409（提示直接打开既有流程）。
    ``published_revision`` 记录首次发布的不可变修订（当前恒为 1，与新流程创建时的
    ``Workflow.published_revision`` 一致；见 ``workflow_builder`` 关于硬编码 1 的说明）。
    ``workflow_id`` / ``schedule_id`` 各自唯一：一个流程/调度只对应一次向导创建。
    """

    __tablename__ = "equipment_alert_workflow_creations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "owner_user_id", "request_key", name="uq_alert_workflow_request"),
    )
    workflow_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("workflow_definitions.id"), nullable=False, unique=True,
        comment="由普通业务向导创建并发布的共享工作流",
    )
    schedule_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("system_scheduled_jobs.id"), nullable=True, unique=True,
        comment="可选的固定调度任务",
    )
    request_key: Mapped[str] = mapped_column(String(160), nullable=False, comment="客户端防重复创建键")
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="除防重复键外的完整配置摘要")
    published_revision: Mapped[int] = mapped_column(Integer, nullable=False, comment="首次发布的不可变修订")
