"""Small, visual read/calculate/output contracts; no user-authored code.

中文补充：读取值 / 计算值 / 输出结果三个节点的配置契约。它们的共同前提是
**配置里不能出现用户编写的 SQL、JSONPath 或代码**：

- ``ReadValuesConfig`` 两种模式都不接受 SQL 文本：选表模式的 SQL 由 ``table_values`` 生成，
  数据集模式的 SQL 存在分析模块的查询模板里（本节点只引用模板 id 与版本）。
  需要自定义 SQL、JSONPath 或脚本时，应使用 ``database-query``（只读 SQL）、
  ``jsonpath-extract``（JSONPath + 字段类型）或 ``code`` 节点，而不是扩展本文件。
- ``ValueCalculation.operation`` 是封闭枚举，新增运算必须同时改这里与
  ``value_nodes.calculate_one``，否则会保存成功但执行时无对应分支。
- 字段名一律经 ``validate_named_fields`` 校验：非空、不重复、不含原型污染名，
  避免输出时覆盖 ``__proto__`` 之类的键。

向后兼容：``ReadValuesConfig.legacy_dataset`` 保证旧配置（没有 readMode）仍可解析，
按是否带 templateId 推断模式，不要删除。
"""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.common.contracts import ApiModel
from app.modules.workflows.schemas import validate_named_fields


class ValueOperand(ApiModel):
    """计算操作数：引用当前行字段，或使用固定常量。

    ``source="field"`` 时 ``field`` 必填且必须是安全字段名；``source="constant"`` 时取值来自 ``value``。
    """

    source: Literal["field", "constant"] = "field"
    field: str = Field(default="", max_length=120)
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def valid_field(self):
        """引用字段时必须填写字段名，且名字要安全（不能是 __proto__ 之类）。"""
        if self.source == "field" and not self.field.strip():
            raise ValueError("请选择参与计算的字段")
        if self.source == "field":
            validate_named_fields([self.field], "计算字段")
        return self


class TableValueFilter(ApiModel):
    """选表读取的一条筛选条件。

    运算符包含 ``is_null``/``not_null``（此时不需要取值）；``in`` 可接受多个值。
    ``skip_empty`` 表示"运行表单留空时不限制"——注意它只作用于上游提供的筛选值，
    空记录批次仍返回空集（不会反过来变成无条件全表读取，见 ``table_values.table_query``）。
    """

    field: str = Field(min_length=1, max_length=120)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null", "in"] = "eq"
    value_source: Literal["fixed", "input", "device"] = Field(default="fixed", alias="valueSource")
    value: str | int | float | bool | None = None
    input_field: str = Field(default="", alias="inputField", max_length=120)
    skip_empty: bool = Field(default=False, alias="skipEmpty", description="运行表单的可选筛选值留空时不限制；空记录批次仍返回空集")

    @model_validator(mode="after")
    def valid_value(self):
        """非空值判断类运算符不需要取值；其他运算符必须给出来源（上游字段名或固定值）。

        注意固定值用 None 表示"没填"，判断空值请用"为空"运算符而不是填 null。
        """
        if self.operator not in {"is_null", "not_null"}:
            if self.value_source == "input" and not self.input_field.strip():
                raise ValueError("请选择上游字段")
            if self.value_source == "fixed" and self.value is None:
                raise ValueError("请填写筛选值；判断空值请使用“为空”")
        return self


class TableValueMatch(ApiModel):
    """关联读取的字段对应关系：本表字段 <- 上游字段。

    ``field`` 是所选表的列，``input_field`` 是上游记录里的字段名；两者的取值类型
    都必须是单值（文本/数字），嵌套对象会在关联时被拒绝。
    """

    field: str = Field(min_length=1, max_length=120)
    input_field: str = Field(alias="inputField", min_length=1, max_length=120)


class TableValueOrder(ApiModel):
    """关联读取的排序项：用于"取最近一条"（match_mode=latest）等场景。"""

    field: str = Field(min_length=1, max_length=120)
    direction: Literal["asc", "desc"] = "desc"


class ReadValuesConfig(ApiModel):
    """读取值节点配置：选表模式（table）与数据集模式（dataset）共用一份契约。

    选表模式用 ``table_name``/``fields``/``filters``/``matches``/``order_by``/``take``；
    数据集模式用 ``template_id``/``template_version``/``parameters``/``parameter_sources``。
    两种模式下 ``fields`` 都是"要输出的列"，``field_aliases`` 只允许为已选字段改名，
    输出名要保证互不重复且安全（否则会覆盖上游字段）。

    校验要点：table 模式下必须选表并至少勾一个字段；用设备筛选必须绑定设备；
    ``match_mode != off`` 必须给出关联字段；``latest`` 必须有排序；
    ``take`` 只能用于非关联读取且必须配合排序（否则"前 N 条"不确定）。
    """

    datasource_id: str = Field(alias="datasourceId", min_length=1, max_length=26)
    datasource_version: int | None = Field(default=None, alias="datasourceVersion", ge=1)
    read_mode: Literal["table", "dataset"] = Field(default="table", alias="readMode")
    table_name: str = Field(default="", alias="tableName", max_length=128)
    filters: list[TableValueFilter] = Field(default_factory=list, max_length=20)
    field_aliases: dict[str, str] = Field(default_factory=dict, alias="fieldAliases", description="所选字段的输出名称")
    match_mode: Literal["off", "all", "latest", "unique"] = Field(default="off", alias="matchMode")
    matches: list[TableValueMatch] = Field(default_factory=list, max_length=8)
    order_by: list[TableValueOrder] = Field(default_factory=list, alias="orderBy", max_length=5)
    match_count_field: str = Field(default="匹配记录数", alias="matchCountField", min_length=1, max_length=120)
    keep_input_fields: list[str] = Field(default_factory=list, alias="keepInputFields", max_length=100)
    take: int | None = Field(default=None, ge=1, le=10000, description="明确只取排序后的前 N 条，留空读取全部符合条件的记录")
    template_id: str = Field(default="", alias="templateId", max_length=26)
    template_version: int = Field(default=1, alias="templateVersion", ge=1)
    device_id: str | None = Field(default=None, alias="deviceId", max_length=26)
    fields: list[str] = Field(default_factory=list, max_length=100)
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    parameter_sources: dict[str, Literal["input", "fixed"]] = Field(default_factory=dict, alias="parameterSources")
    max_rows: int = Field(default=10000, alias="maxRows", ge=1, le=10000)

    @model_validator(mode="before")
    @classmethod
    def legacy_dataset(cls, values):
        """兼容旧配置：没有 readMode 时按是否带 templateId 推断模式（dataset / table）。

        这是升级期的一次性推断，保留以免历史节点在保存时报"缺少读取模式"。
        """
        if isinstance(values, dict) and "readMode" not in values and "read_mode" not in values:
            values = {**values, "readMode": "dataset" if values.get("templateId", values.get("template_id")) else "table"}
        return values

    @model_validator(mode="after")
    def valid_read(self):
        """两种读取模式的完整交叉校验。

        dataset 必须有数据集；table 必须选表且至少一个字段；用设备筛选必须先绑定设备；
        输出名只能给已选字段改名且互不重复；关联读取必须有字段对应关系；
        "取最近"必须有排序；``take`` 只能用于非关联读取且必须配合排序（否则"前 N 条"不确定）。
        """
        if self.read_mode == "dataset" and not self.template_id:
            raise ValueError("请选择读取数据集")
        if self.read_mode == "table" and (not self.table_name.strip() or not self.fields):
            raise ValueError("请选择数据表并至少勾选一个字段")
        if self.read_mode == "table" and any(f.value_source == "device" for f in self.filters) and not self.device_id:
            raise ValueError("当前流程没有绑定设备，请使用固定值或上游字段筛选")
        if self.read_mode == "table":
            if set(self.field_aliases) - set(self.fields):
                raise ValueError("只能为已选择的字段设置输出名称")
            validate_named_fields([self.field_aliases.get(f, f) for f in self.fields], "读取输出名称")
            validate_named_fields([self.match_count_field], "匹配数量字段")
            if self.match_mode != "off" and not self.matches:
                raise ValueError("请配置当前表字段与上游字段的对应关系")
            if self.match_mode == "latest" and not self.order_by:
                raise ValueError("取最近记录需要选择排序字段")
            if self.keep_input_fields:
                validate_named_fields(self.keep_input_fields, "保留上游字段")
            if self.take is not None and (not self.order_by or self.match_mode != "off"):
                raise ValueError("只取前 N 条需选择排序，且不能用于关联读取")
        return self

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, values):
        """读取字段名必须非空、不重复且安全；空列表保持原样（由 valid_read 判定是否允许）。"""
        return validate_named_fields(values, "读取字段") if values else values


class ValueCalculation(ApiModel):
    """计算值节点的一项计算：输出名 + 封闭枚举的运算 + 操作数（+ 可选映射表）。

    ``right`` 为空的规则：需要两个值的运算（加减乘除、天数/小时差、contains、coalesce、
    add_hours/add_months）必须提供，否则配置报错；``mapping`` 只用于 lookup/mapped_field，
    上限 100 项。
    """

    name: str = Field(min_length=1, max_length=120)
    operation: Literal["copy", "add", "subtract", "multiply", "divide", "days_between", "hours_between",
                       "number", "lower_limit", "upper_limit", "unit", "lookup", "contains",
                       "range_lower", "range_upper", "range_unit", "coalesce", "now", "add_hours", "add_months", "mapped_field"]
    left: ValueOperand
    right: ValueOperand | None = None
    mapping: dict[str, str | int | float | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_operands(self):
        """结果字段名安全且不能占用业务标识保留名；需要两个值的运算必须给出 right；映射上限 100 项。

        保留 ``recordKey``/``deviceKey``/``planId``/``itemId`` 是为了让计算不破坏业务关联标识。
        """
        validate_named_fields([self.name], "计算结果字段", reserved={"recordKey", "deviceKey", "planId", "itemId"})
        if self.operation in {"add", "subtract", "multiply", "divide", "days_between", "hours_between", "contains", "coalesce", "add_hours", "add_months"} and self.right is None:
            raise ValueError("此计算需要选择两个值")
        if len(self.mapping) > 100:
            raise ValueError("映射最多100项")
        return self


class CalculateValuesConfig(ApiModel):
    """计算值节点配置：至少一项计算，输出名在同一节点内不能重复。"""

    calculations: list[ValueCalculation] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_names(self):
        """计算结果名不能重复：同名会让前一项结果被后一项覆盖，无法追溯。"""
        if len({c.name for c in self.calculations}) != len(self.calculations):
            raise ValueError("计算结果名称不能重复")
        return self


class OutputValuesConfig(ApiModel):
    """输出结果节点配置：选择输出字段并给出统一结论与说明。

    ``mode`` 决定结论文案（alert 用正常/预警，quality 用合格/不合格），
    ``conclusion`` 是逐行写死的结论（该节点不做条件判断——判断应由上游规则节点完成）。
    """

    mode: Literal["alert", "quality"] = "alert"
    fields: list[str] = Field(default_factory=list, max_length=100)
    conclusion: Literal["normal", "warning", "pending"] = "normal"
    message: str = Field(default="", max_length=1000)

    @field_validator("fields")
    @classmethod
    def valid_fields(cls, values):
        """输出字段名必须非空、不重复且安全；空列表表示保留全部原始字段。"""
        return validate_named_fields(values, "输出字段") if values else values
