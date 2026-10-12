"""Persistent, provider-neutral model usage ledger.

中文补充（模型治理自有表）：本文件只声明本包自己拥有的两张表，通用领域表
（``ModelProfile`` / ``ModelProbeResult`` / ``Secret`` / ``Workspace``）在 ``app/models.py`` 中。

- ``EnvironmentModelProbe``：给"没有数据库 profile 行"的环境默认角色留探测历史；
- ``ModelUsageRecord``：每次模型调用一行事实账本，带调用当时的计价快照。

职责边界与关键约束：
- 表结构由 ``app/schema.py`` 启动建表/迁移，改字段会影响既有部署，新增字段需保证可空或有默认值；
- 写入方是 ``service.record_usage``（独立短事务，不参与业务事务），本文件不做任何业务校验；
- 供应商中立：这里只存 provider/model 字符串与 token/费用，不存请求或回答正文；
- ``priced=False`` 的行没有任何费用信息（``cost`` 为 NULL），预算硬限制不统计这些行，
  详见 ``service.enforce_budget`` 的说明。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.ids import new_id
from app.models import Base, now


class EnvironmentModelProbe(Base):
    """Probe history for deployment profiles without a ModelProfile row.

    中文补充：环境默认角色（reasoning/embedding/vision，见 ``service._environment_defaults``）
    在被管理员保存成数据库 profile 之前没有 ``ModelProfile`` 行可挂，因此探测结果单独落这张表，
    靠 ``(workspace_id, profile_name, config_fingerprint)`` 定位"当前这套环境配置下的最后一次探测"。
    生命周期：只追加、不更新；环境变量或默认模型变更会让 fingerprint 变化，旧记录自然失效（不被展示），
    无需清理。本表不存密钥，``details_json`` 里只有回复摘要与耗时。
    """

    __tablename__ = "environment_model_probes"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    profile_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # 环境默认配置的 sha256（service._environment_fingerprint）；与当前配置不一致的历史探测不再回显。
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # passed | failed
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class ModelUsageRecord(Base):
    """One completed or failed model call with an immutable pricing snapshot.

    中文补充：模型调用用量账本。每次调用（含失败）写一行，字段是"事实 + 计价快照"，
    写入后不可变、不更新：管理员事后改价不会改写历史费用，也不会让本月的预算口径前后跳变。

    关键不变式：
    - ``input_price_per_million`` / ``output_price_per_million`` 是调用发生时的单价快照，
      ``priced=False`` 表示当时没有任何单价（``cost`` 必为 NULL），用量页把这类调用单独统计为
      "未计价调用/未计价 Token"，而不是按 0 元计入费用；
    - ``cost`` 只在两档单价至少有一个时才计算（缺失的一档按 0 参与计算），币种取快照的 currency；
    - 预算硬限制只累加"``priced=True`` 且 ``currency`` 等于策略币种"的行，所以未配置价格的调用
      永远不会让预算被判定为超额（约束详见 ``service.enforce_budget``）。
    """

    __tablename__ = "model_usage_records"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    profile_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # chat | embedding | vision
    source: Mapped[str] = mapped_column(String(64), default="model_gateway", nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # completed | failed
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 单价快照：NULL 表示该档未配置价格，与"价格为 0"是两种不同事实，不可用 0 代替。
    input_price_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_price_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)
    # 是否已计价：预算统计与"未计价调用"提示都以此列为准，不要用 cost is not None 反推。
    priced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 自由上下文：source/source_id 之外的 usage_context 键（run_id、purpose 等）落这里，便于按需追溯。
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # started_at 由 completed_at 减 latency 反推（见 service.record_usage），仅用于展示与排序。
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
