"""Explicit quality contracts; exact values use decimal strings, never implicit rounding.

职责：质检业务域的形状契约 —— 指标、模板及其修订快照、检测观察值（含外观缺陷）、
检验记录与人工复核请求、检测节点配置，以及各类视图。

位置与依赖：被 ``quality.registry`` / ``inspections`` / ``detection`` /
``workflow_builder`` 与 ``quality.router`` 共用；``Observation`` 同时是检测证据的落库形状，
被 ``QualityDetection.results`` 直接引用。

核心约定：
- **精确数值一律用字符串**（``decimal_text`` 校验）：有限、有效数字 ≤ 100 位、
  指数绝对值 ≤ 100。不使用 float —— 结算/判定不接受隐式舍入。
- ``Editable`` 子类修改时必须带 ``version``（乐观锁，过期 409）；
  视图类（``*View``）与 ``Assessment`` 等为只读输出，不参与入参校验。
- 单位不自动换算：实测单位必须与指标单位一致，比对由上层负责。
- 本模块只做形状与跨字段一致性校验，资源存在性与权限由服务层负责。
"""
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel


def decimal_text(value: str) -> str:
    """Validate a finite bounded decimal without changing the user's precision.

    （中文补充）校验"精确数值文本"并**原样返回**（不归一化、不去尾零），
    因为质检判定与让步结算要比对文本精度：把 ``"1.50"`` 变成 ``"1.5"`` 会改变证据形态。
    范围上限（有效数字 ≤ 100、指数 ≤ 100）是为了配合结算时放大到 512 位精度的计算上下文。
    异常：非法小数、无穷/NaN、超范围时抛 ``ValueError``（由 pydantic 转成 422）。
    """
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("请输入有效的精确小数字符串") from exc
    if not number.is_finite() or len(number.as_tuple().digits) > 100 or abs(number.adjusted()) > 100:
        raise ValueError("数值必须有限且不超过 100 位有效数字或指数范围")
    return value


class Editable(ApiModel):
    """可编辑对象的基类：修改必填 ``version``，且所有字符串字段先去首尾空白。

    与 ``alerts.schemas.Editable`` 语义一致，但这是质检域的独立实现 ——
    两处若有行为差异（例如是否允许空字符串）需分别评估，不要互相假定。
    """
    version: int | None = Field(default=None, ge=1, description="修改必填当前版本，过期返回 409")

    @field_validator("*", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class MetricInput(Editable):
    """质检指标：数值（numeric）、指定成分（component）或外观缺陷（appearance）。

    类别决定字段语义（``standards`` 校验器强制）：数值/成分必须至少给一个标准界限；
    外观指标**不接受数值范围**，改用缺陷级别（``blockingSeverities``）与
    最低置信度（``minimumConfidence``，低于该值需复核）。单位不自动换算，
    实测单位必须与这里的 ``unit`` 一致。修改后已保存的模板修订不受影响（模板保存时固化标准）。
    """
    name: str = Field(min_length=1, max_length=160, description="质检指标名称")
    category: Literal["numeric", "component", "appearance"] = Field(description="数值、指定成分、外观缺陷")
    unit: str = Field(default="", max_length=40, description="明确计量单位，实测单位必须一致，不自动换算")
    standard_min: str | None = Field(default=None, alias="standardMin", max_length=120, description="包含端点的标准下限，精确文本")
    standard_max: str | None = Field(default=None, alias="standardMax", max_length=120, description="包含端点的标准上限，精确文本")
    component_name: str = Field(default="", alias="componentName", max_length=120, description="成分指标必须指定唯一成分名称")
    minimum_confidence: str = Field(default="0.8", alias="minimumConfidence", max_length=120, description="外观缺陷低于此置信度需复核，0 到 1")
    blocking_severities: list[Literal["minor", "moderate", "severe"]] = Field(default_factory=lambda: ["moderate", "severe"], alias="blockingSeverities", max_length=3, description="出现即不合格的缺陷级别")
    description: str = Field(default="", max_length=3000, description="标准说明")
    status: Literal["active", "inactive"] = Field(default="active", description="是否启用")

    @field_validator("standard_min", "standard_max", "minimum_confidence")
    @classmethod
    def finite_decimal(cls, value):
        return decimal_text(value) if value is not None else None

    @model_validator(mode="after")
    def standards(self):
        if not 0 <= Decimal(self.minimum_confidence) <= 1:
            raise ValueError("最低置信度必须介于 0 和 1")
        if self.standard_min is not None and self.standard_max is not None and Decimal(self.standard_min) > Decimal(self.standard_max):
            raise ValueError("标准下限不得高于上限")
        if self.category != "appearance" and self.standard_min is None and self.standard_max is None:
            raise ValueError("数值与成分指标至少配置一个标准界限")
        if self.category == "component" and not self.component_name:
            raise ValueError("成分指标必须填写成分名称")
        if self.category == "appearance" and (self.standard_min is not None or self.standard_max is not None):
            raise ValueError("外观指标使用缺陷级别与置信度，不接受数值范围")
        if len(set(self.blocking_severities)) != len(self.blocking_severities):
            raise ValueError("缺陷级别不能重复")
        return self


class MetricView(MetricInput):
    """指标的输出视图：在入参之上补 id 与时间戳（``status`` 可为 deleted，历史仍可读）。"""
    id: str = Field(description="指标标识")
    status: str = Field(description="当前状态，历史可为 deleted")
    createTime: str = Field(description="创建时间 UTC")
    updateTime: str = Field(description="更新时间 UTC")


class TemplateInput(Editable):
    """质检模板：一组**有序**指标的显式选择（无默认模板、无隐式补齐）。

    ``metricIds`` 顺序即报告逐项展示顺序；保存时会**固化当时的指标标准**成模板修订，
    因此之后修改指标不会改变既有模板修订的判定口径。``deviceType`` 为空表示不限设备类别。
    """
    name: str = Field(min_length=1, max_length=160, description="质检模板名称")
    device_type: str = Field(default="", alias="deviceType", max_length=100, description="适用设备类别；空表示不限制类别")
    description: str = Field(default="", max_length=3000, description="检验规范与用途")
    metric_ids: list[str] = Field(alias="metricIds", min_length=1, max_length=100, description="显式选择有序指标，保存时固定当时标准")
    status: Literal["active", "inactive"] = Field(default="active", description="是否允许新质检")

    @field_validator("metric_ids")
    @classmethod
    def distinct_metrics(cls, values):
        if len(set(values)) != len(values) or any(not 1 <= len(value) <= 26 for value in values):
            raise ValueError("指标标识无效或重复")
        return values


class TemplateView(TemplateInput):
    """模板的输出视图（补 id 与时间戳）。"""
    id: str = Field(description="模板标识")
    status: str = Field(description="当前状态")
    createTime: str = Field(description="创建时间 UTC")
    updateTime: str = Field(description="更新时间 UTC")


class TemplateSnapshot(TemplateView):
    """模板修订快照：模板信息 + **该修订固定的完整指标标准**。

    质检记录与报告都引用这个形状，因此历史判定只依赖快照，不依赖指标当前值。
    """
    metrics: list[MetricView] = Field(description="此修订不可变的完整指标标准")


class RevisionView(ApiModel):
    """模板修订列表项：明确的修订号 + 快照 + 创建时间。

    修订号是"跑哪一版标准"的唯一依据（质检记录里固定保存），不接受"用最新版"的隐式行为。
    """
    revision: int = Field(description="明确修订号")
    snapshot: TemplateSnapshot = Field(description="固定模板和指标")
    createTime: str = Field(description="修订创建时间 UTC")


class Defect(ApiModel):
    """检测服务返回的**单个**缺陷：类型、置信度、严重度，可选归一化框。

    ``confidence`` 用精确文本且限定 0–1（不做四舍五入）；``bbox`` 为归一化
    x/y/宽/高（0–1，且右下不得越界），坐标相对**按 EXIF 方向纠正后**的图片尺寸
    （见 ``QualityImageView`` 说明），检测服务必须按同一方向解释。
    ``severity='review'`` 表示无法自动定级、需要人工复核。
    """
    defectType: str = Field(min_length=1, max_length=160, description="实际缺陷类型")
    confidence: str = Field(max_length=120, description="精确置信度文本，0 至 1")
    severity: Literal["minor", "moderate", "severe", "review"] = Field(description="严重度或待复核")
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description="可选归一化坐标 x/y/宽/高，范围 0 至 1")
    suggest: str = Field(default="", max_length=2000, description="实际处置建议")

    @field_validator("confidence")
    @classmethod
    def probability(cls, value):
        decimal_text(value)
        if not 0 <= Decimal(value) <= 1:
            raise ValueError("置信度范围必须为 0 至 1")
        return value

    @field_validator("bbox")
    @classmethod
    def coordinates(cls, value):
        if value is not None and (any(not 0 <= item <= 1 for item in value) or value[0] + value[2] > 1.00000001 or value[1] + value[3] > 1.00000001):
            raise ValueError("缺陷框必须为有效归一化坐标")
        return value


class Observation(ApiModel):
    """一条指标的实测观察值（数值/成分）或外观检测结论（``defects``）。

    它是检测证据的落库形状，因此字段语义严格：
    - ``value`` 缺失**不是合格**（判定层会把它算作未检测/需复核），数值必须是精确小数字符串；
    - ``defects`` 必须由检测服务显式给出：**空数组与"未检测"含义不同**，
      外观指标缺失该字段时上层会拒绝证据（见 ``quality.detection``）；
    - ``imageFileId`` 是平台内的授权原图文件标识，来源文字（``imageRef``）不能替代它，
      缺陷框与图片的关联靠该标识；
    - ``unit`` 必须与指标单位一致，系统不做换算。
    """
    metricId: str = Field(min_length=1, max_length=26, description="所选模板中的指标")
    value: str | None = Field(default=None, max_length=120, description="数值/成分实测值，精确文本；缺失不是合格")
    unit: str = Field(default="", max_length=40, description="实际单位")
    imageRef: str = Field(default="", max_length=1000, description="原始图像资料标识或来源 URI，仅作追溯，不自动抓取")
    imageFileId: str | None = Field(default=None, min_length=1, max_length=26, description="授权原图文件标识；平台校验内容并固定哈希，来源文字不能替代此关联")
    defects: list[Defect] | None = Field(default=None, max_length=300, description="外观检测必须显式提供数组；空数组与未检测不同")
    note: str = Field(default="", max_length=2000, description="人工录入说明或采样备注")

    @field_validator("value")
    @classmethod
    def finite_value(cls, value):
        return decimal_text(value) if value is not None else None


class InspectionInput(ApiModel):
    """手工创建质检记录（普通页面/接口）的入参。

    ``observations`` 是**人工测量结果**，平台不会把它标为自动设备证据；
    ``detectionIds`` 只能引用真实存在的专用节点检测（自动从证据读取，
    不接受客户端填数字来"伪造检测"）。``templateRevision`` 必须明确：质检记录固定标准版本。
    ``requestKey`` 用于防止网络重试造成重复记录（同键不同参数返回 409）。
    """
    templateId: str = Field(min_length=1, max_length=26, description="明确选择模板，无默认模板")
    templateRevision: int = Field(ge=1, description="明确选择不可变模板修订")
    deviceId: str = Field(min_length=1, max_length=26, description="启用质检用途的设备")
    sampleId: str = Field(min_length=1, max_length=160, description="样本/批次标识")
    requestKey: str = Field(min_length=8, max_length=160, description="防重复提交键，相同键参数不同返回 409")
    observations: list[Observation] = Field(default_factory=list, max_length=100, description="人工测量结果；不会标为自动设备证据")
    detectionIds: list[str] = Field(default_factory=list, max_length=100, description="实际专用节点检测标识；自动从证据读取，不接受客户端篡改")

    @field_validator("sampleId", "requestKey")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("标识不能为空白")
        return value.strip()


class QualityConfig(ApiModel):
    """质检工作流节点配置：模板修订 + 设备 + 样本模式。

    两种样本模式互斥（``sample_selection`` 强制）：``fixed`` 需给出固定样本编号（整批一份报告），
    ``input`` 则按上游检测引用逐样本分组、为每个样本生成独立报告，因此**不接受**固定样本编号。
    """
    templateId: str = Field(min_length=1, max_length=26, description="所选模板")
    templateRevision: int = Field(ge=1, description="固定修订号")
    deviceId: str = Field(min_length=1, max_length=26, description="质检设备")
    sampleId: str = Field(default="", max_length=160, description="固定模式的当前样本；逐样本模式不填写")
    sampleMode: Literal["fixed", "input"] = Field(default="fixed", description="固定样本，或按上游检测引用分组生成每个样本的独立报告")

    @model_validator(mode="after")
    def sample_selection(self):
        if self.sampleMode == "fixed" and not self.sampleId.strip():
            raise ValueError("固定样本模式必须填写样本编号")
        if self.sampleMode == "input" and self.sampleId:
            raise ValueError("逐样本模式不接受固定样本编号")
        return self


class DetectorConfig(ApiModel):
    """检测节点配置：指标（含版本）+ 设备 + HTTP 检测服务（含配置版本）。

    指标版本与数据源版本都必须明确固定 —— 运行时会与实际版本比对，不一致就拒绝执行，
    避免"改了服务配置后旧流程按新配置跑"。
    ``requestMode='saved'`` 直接执行已保存的请求体；``'input'`` 把上游样本逐条绑定到
    已授权的 POST 模板（此时 ``sampleField`` / ``imageField`` 指定直接字段名，不做深层猜测）。
    """
    metricId: str = Field(min_length=1, max_length=26, description="所选外观或成分指标")
    metricVersion: int = Field(ge=1, description="明确指标版本")
    deviceId: str = Field(min_length=1, max_length=26, description="检测设备")
    datasourceId: str = Field(min_length=1, max_length=26, description="设备关联 HTTP 视觉或成分服务")
    datasourceVersion: int = Field(ge=1, description="固定连接/请求配置版本")
    requestMode: Literal["saved", "input"] = Field(default="saved", description="saved 执行已保存请求；input 将上游样本逐条绑定到已授权的 POST 模板")
    sampleField: str = Field(default="sampleId", min_length=1, max_length=120, description="输入模式中样本编号所在的直接字段名")
    imageField: str = Field(default="imageFileId", min_length=1, max_length=120, description="外观输入模式中授权图片标识所在的直接字段名")


class QualityImageView(ApiModel):
    """质检图片的只读视图（与文件资料模块共用同一文件资源）。

    ``mimeType`` 由**实际解码**确定，不信任扩展名；``sha256`` 用于核验报告证据未被替换；
    ``width`` / ``height`` 是**按 EXIF 方向显示后**的尺寸，``orientation`` 保留原始方向值，
    检测服务与缺陷框都以该显示方向为基准，否则框会错位。
    """
    id: str = Field(description="文件标识，与文件资料模块使用同一资源")
    name: str = Field(description="关联时的原始文件名称")
    mimeType: Literal["image/png", "image/jpeg", "image/webp"] = Field(description="依据实际解码确定的类型，不信任扩展名")
    size: int = Field(description="原始文件字节数")
    sha256: str = Field(description="原始内容 SHA-256，用于核验报告证据")
    width: int = Field(description="按 EXIF 方向显示后的像素宽度；缺陷框相对此方向")
    height: int = Field(description="按 EXIF 方向显示后的像素高度")
    orientation: int = Field(description="原图 EXIF 方向 1–8；检测服务应按此方向解释缺陷框")


class ReviewInput(ApiModel):
    """人工复核请求：只针对某个模板指标给出结论，必须写明依据。

    ``decision`` 为 passed/failed/retest（合格/不合格/要求复检）；
    复核**不覆盖机器原始判断**，只追加到复核历史并生成"考虑复核后的当前结论"。
    ``version`` 是记录的乐观锁版本，过期返回 409。
    """
    version: int = Field(ge=1, description="当前检验记录版本")
    metricId: str = Field(min_length=1, max_length=26, description="复核的模板指标")
    decision: Literal["passed", "failed", "retest"] = Field(description="人工合格、不合格或要求复检")
    note: str = Field(min_length=1, max_length=3000, description="必填人工复核依据，不覆盖机器原始判断")

    @field_validator("note")
    @classmethod
    def required_reason(cls, value):
        if not value.strip():
            raise ValueError("请输入复核依据")
        return value.strip()


class Assessment(ApiModel):
    """单指标的原始判定（逐项结果，含未检测项）。

    ``outcome``：passed / failed / review（需人工复核） / incomplete（未检测、证据不足）。
    ``observation`` 为 null 表示**没有实测或观察**（不是合格）；``provenance`` 说明
    结论来自 manual 还是 detector（含检测标识与查询来源），审计与报告据此区分人工与自动证据。
    """
    metricId: str = Field(description="指标标识")
    name: str = Field(description="固定指标名称")
    category: str = Field(description="指标类别")
    outcome: Literal["passed", "failed", "review", "incomplete"] = Field(description="原始逐项判定")
    reason: str = Field(description="真实判定理由")
    observation: Observation | None = Field(description="实际检测或人工观察，未检测为 null")
    provenance: dict = Field(description="manual 或 detector，以及检测标识和查询来源")


class InspectionView(ApiModel):
    """质检记录输出视图：原始结论与人工复核后的有效结论并列。

    关键约束：``outcome`` 是**不可变**的原始结论，``effectiveOutcome`` 是考虑复核后的当前结论，
    两者必须分别展示，禁止用后者覆盖前者；``results`` 是完整模板逐项判定（含未检测项），
    ``reviews`` 是追加式人工复核历史。``executionId`` / ``nodeId`` 为空表示人工录入记录；
    ``fromQualityForm`` 为真可通过所属批次追溯；``writeOperations`` 是该流程的实际写库凭据。
    """
    id: str = Field(description="质检记录标识")
    templateId: str = Field(description="模板标识")
    deviceId: str = Field(description="设备标识")
    sampleId: str = Field(description="样本标识")
    outcome: str = Field(description="不可变原始结论")
    effectiveOutcome: str = Field(description="考虑人工复核后的当前结论，原始结论不变")
    status: str = Field(description="active/archived")
    version: int = Field(description="复核/归档乐观锁版本")
    snapshot: dict = Field(description="原设备/模板修订/检测来源快照")
    results: list[Assessment] = Field(description="完整模板逐项原始判定，含未检测项")
    reviews: list[dict] = Field(description="追加式人工复核历史，含操作者与时间")
    executionId: str | None = Field(description="来源工作流运行；人工录入为空")
    workflowId: str | None = Field(default=None, description="来源共享工作流标识，用于普通页面追溯")
    fromQualityForm: bool = Field(default=False, description="是否由普通多样本运行表单创建，可打开所属批次")
    nodeId: str = Field(description="来源节点；人工录入为空")
    createTime: str = Field(description="记录创建时间 UTC")
    writeOperations: list[dict] = Field(default_factory=list, description="关联工作流的实际目标数据库写入凭据")
