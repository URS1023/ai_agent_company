"""Documented envelopes shared by migrated business APIs and their UI clients.

已迁移业务接口与其 UI 客户端共享的统一响应契约。

系统位置：路由层用 ``Envelope``/``PagedEnvelope`` 包装成功结果（``response_model`` 同时
承担 OpenAPI 文档职责），变更类操作把 ``MutationResult`` 放进 ``data``。

错误分类：**失败不走这套结构**。业务异常由 ``app.errors.AppError`` 及其子类表达，
经 ``main.py`` 注册的异常处理器转成带 HTTP 状态码与稳定 ``code`` 的统一错误体，
前端按 ``code``（而非文案）分支。因此这里的 ``success`` 只描述"业务结果是否完整成功"，
不用于表达错误原因；需要逐项状态时用 207 + 逐项状态（见 ``Envelope.success`` 字段说明）。

约束：这是对外契约，新增字段必须是**向后兼容的补充**（老客户端应能继续读 ``data``），
不要改字段语义或删字段；字段名与前端约定保持 camelCase（如 ``hasMore``）。
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    """Accept documented field names only; never silently discard configuration.

    请求模型的基类：``extra="forbid"`` 让多传或拼错的字段直接 422，而不是被静默丢弃
    （历史事故类型：前端字段名漂移后配置看着"已保存"其实从未生效）。

    不要为了兼容前端而放开 ``extra``；``populate_by_name=True`` 已允许按别名填充，
    需要 camelCase 入参时用 ``alias``，而不是放宽未知字段。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Envelope(BaseModel, Generic[T]):
    """Successful business result; errors use the platform's typed HTTP errors.

    成功响应的统一外壳：``data`` 为当前用户已授权的业务结果（越权数据不应出现在这里，
    由服务层的数据范围过滤保证），``message`` 仅承载可展示文案。

    注意：仅用于成功（含批量部分成功，此时用 207 + ``data`` 内逐项状态）；
    失败必须抛 ``AppError`` 子类，不要用 ``success=False`` 表达错误。
    """

    success: bool = Field(default=True, description="操作是否完整成功；批量部分完成可返回 207 与逐项状态，全部失败返回对应的非 2xx 状态")
    data: T = Field(description="当前用户已授权的业务结果")
    message: str = Field(default="", description="用于界面展示的操作说明，不包含凭据")


class MutationResult(BaseModel):
    """变更类操作的载荷，通常放在 ``Envelope[MutationResult]`` 的 ``data`` 里。

    ``version`` 为资源自身的乐观锁版本（资源未启用乐观锁时为 None，前端不应据此判断失败）；
    ``deleted`` 表示资源已从正常业务列表移除（多为软删除，历史追溯仍保留）。
    删除接口返回它而不是空体，便于前端直接更新本地列表项。
    """

    id: str = Field(description="发生变更的资源标识")
    version: int | None = Field(default=None, description="变更后的乐观锁版本")
    deleted: bool = Field(default=False, description="是否已从正常业务列表移除")


class PagedEnvelope(Envelope[list[T]], Generic[T]):
    """Additive paging metadata; existing clients can still read the data array.

    在 ``Envelope`` 之外附加分页元数据的分页响应契约。

    约束：字段名必须与 ``common.pagination.page_records`` 返回的 dict 键保持一致
    （``data``/``total``/``limit``/``offset``/``hasMore``），两者是同一契约的两侧，
    改一侧必须同步改另一侧；``offset`` 必须是服务端实际生效的值（``page_records`` 会把
    越界 offset 回收到最后一页），否则前端翻页游标会与真实数据错位。
    """
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    hasMore: bool
