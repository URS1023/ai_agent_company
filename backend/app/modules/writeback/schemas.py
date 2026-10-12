"""Documented allowlist, field mapping and receipt contracts.

中文补充（写回契约层）：
- 字段约束即安全边界的一部分，改动前请确认执行层仍然依赖：columns 非空且唯一
  （空列表不等于"全部字段"）、modes 只能 insert/update、update 必须给出完整唯一键
  且至少有一个非键更新字段、insert 不允许带 keys。
- ``target_version`` 是"钉住的白名单版本"：管理员编辑目标后，老工作流不会自动跟随，
  必须显式重新选择，避免写入范围被静默扩大。
- status 字段说明中特别区分 uncertain（提交结果未知）与 failed（已确认回滚），
  对外文案不得把未确认描述成失败或可重试。
"""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.common.contracts import ApiModel


class TargetInput(ApiModel):
    """建立/修改写入目标的请求体（白名单 + 乐观锁 version）。

    不变式：表名与字段名都必须是已存在的真实业务对象；保存时服务层会用只读连接复核，
    因此"保存成功"代表当时确实可写。
    """

    name: str = Field(min_length=1, max_length=160, description="业务写入目标名称")
    datasource_id: str = Field(alias="datasourceId", min_length=1, max_length=26, description="已授权的目标关系数据库；保存目标不会写业务数据")
    source_version: int = Field(alias="sourceVersion", ge=1, description="用户确认的连接配置版本；配置变化后必须重新确认")
    table_name: str = Field(alias="tableName", min_length=1, max_length=128, description="已存在的普通业务表，不创建、删除表或写视图")
    columns: list[str] = Field(min_length=1, max_length=200, description="允许写入的真实字段白名单，空列表不代表全部")
    modes: list[Literal["insert", "update"]] = Field(min_length=1, max_length=2, description="明确允许追加或按唯一键更新，不默认启用")
    status: Literal["active", "disabled"] = Field(default="active", description="停用后禁止新写入，保留历史")
    version: int | None = Field(default=None, ge=1, description="编辑时必须提交当前目标版本")

    @field_validator("name", "table_name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """名称/表名去空白，且不能含控制字符（控制字符会污染 SQL 标识符与日志）。"""
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("名称不能为空或包含控制字符")
        return value.strip()

    @model_validator(mode="after")
    def unique_fields(self):
        """字段名非空、唯一且 ≤128 字符；写入方式不重复（重复即配置错误）。"""
        if len(set(self.columns)) != len(self.columns) or any(not field or len(field) > 128 for field in self.columns):
            raise ValueError("字段名必须非空、唯一且不超过 128 字符")
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("写入方式重复")
        return self


class TargetView(BaseModel):
    """写入目标的对外视图（只含配置，绝不含数据库凭据）。"""

    id: str = Field(description="目标标识")
    name: str = Field(description="名称")
    datasourceId: str = Field(description="关联数据源")
    sourceVersion: int = Field(description="已确认的数据源版本")
    tableName: str = Field(description="目标表")
    columns: list[str] = Field(description="允许写入的字段")
    modes: list[str] = Field(description="允许的写入方式")
    status: str = Field(description="active/disabled/deleted")
    version: int = Field(description="目标配置版本")


class WriteField(ApiModel):
    """单字段映射：目标列 ← 当前行 JSONPath / 完整上游输入 / 常量。

    origin=row 时 path 相对当前记录求值；origin=input 时相对完整上游数据求值
    （适合取批次级字段）；literal 直接使用 value。三种来源都必须恰好命中一个值。
    """

    column: str = Field(min_length=1, max_length=128, description="明确选择的目标字段")
    origin: Literal["row", "input", "literal"] = Field(default="row", description="从当前行、完整上游输入或常量读取")
    path: str = Field(default="$", min_length=1, max_length=500, description="JSONPath，必须准确匹配一个值；不隐式取首项")
    value: Any = Field(default=None, description="origin=literal 时的常量；精确小数请使用字符串")


class OutputTableConfig(ApiModel):
    """工作流"写回目标表"节点的配置（保存在节点上，执行时逐项校验）。"""

    target_id: str = Field(alias="targetId", min_length=1, max_length=26, description="明确选择的授权写入目标")
    target_version: int = Field(alias="targetVersion", ge=1, description="固定目标配置版本；不会跟随管理员编辑自动更新")
    mode: Literal["insert", "update"] = Field(description="追加新记录或按唯一键更新已有记录")
    record_path: str = Field(default="$.data", alias="recordPath", min_length=1, max_length=500, description="上游中的记录数组，例 $.data 或 $.issues")
    fields: list[WriteField] = Field(min_length=1, max_length=200, description="目标字段与实际行/输入/常量的逐项映射")
    keys: list[str] = Field(default_factory=list, max_length=30, description="update 必选：完整主键或唯一键，必须也在映射中；不允许无条件更新")

    @model_validator(mode="after")
    def unique_mapping(self):
        """映射一致性校验：字段不重复；keys 必须来自映射内字段；update 必须有非键字段。

        这些约束在执行层之外先拦一次，避免出现"定位键没映射"或"整行都是键"导致的
        无意义/危险写入（后者等价于无条件更新）。
        """
        columns = [field.column for field in self.fields]
        if len(set(columns)) != len(columns):
            raise ValueError("目标字段不能重复映射")
        if len(set(self.keys)) != len(self.keys) or not set(self.keys) <= set(columns):
            raise ValueError("唯一键字段不能重复且必须包含在映射中")
        if self.mode == "update" and (not self.keys or not set(columns) - set(self.keys)):
            raise ValueError("更新必须选择唯一键和至少一个非键更新字段")
        if self.mode == "insert" and self.keys:
            raise ValueError("追加方式不使用更新键，请清空键字段")
        return self


class OperationView(BaseModel):
    """写入回执的对外视图（读历史不会重发写入）。"""

    id: str = Field(description="持久化写入凭据标识")
    executionId: str = Field(description="来源工作流执行")
    nodeId: str = Field(description="来源节点")
    targetId: str = Field(description="授权目标")
    status: str = Field(description="succeeded/failed/uncertain/unconfirmed；未确认不等于失败或可以重试")
    requestedRows: int = Field(description="本批映射行数")
    affectedRows: int | None = Field(description="已确认提交行数；未知为 null")
    errorMessage: str = Field(description="脱敏错误与核对建议")
    snapshot: dict = Field(description="实际目标、配置版本、映射与数据摘要，不含凭据")
    createTime: str = Field(description="创建时间 UTC")
