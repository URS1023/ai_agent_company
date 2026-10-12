"""Strict request contracts for the operations center.

中文补充：运维中心的请求契约层。这里只放"写操作"的入参（当前仅中断运行恢复），
所有查询接口的筛选条件直接以 FastAPI `Query` 声明在 `router.py` 上，不在此重复建模。
基类 `ApiModel` 为 `extra="forbid"` 且开启 `populate_by_name`，因此字段别名（camelCase）
与下划线名都可接收，但任何未声明字段都会直接 422，不会静默丢弃。
"""
from __future__ import annotations

from pydantic import Field

from app.common.contracts import ApiModel


class RecoverStaleInput(ApiModel):
    """POST /api/v1/operations/runs/recover 的入参：中断运行恢复的两段式开关。

    生命周期只有一次调用：先以 `dry_run=True`（默认）拿到候选清单与跳过原因，
    确认无误后再以 `dry_run=False` 提交；服务端不保存任何中间状态，两次调用之间
    出现的运行变化会重新判定。字段级约束以 `Field` 声明为准（阈值 60–86400 秒，
    未显式传 `dryRun` 时恒为预览），因此缺省值本身即"不会误改数据"的安全默认。
    """

    threshold_seconds: int = Field(
        default=900, alias="thresholdSeconds", ge=60, le=86400,
        description="超过该秒数才检查执行者；仅确认本机执行者已退出的聊天可清理，等待审批及归属不明的运行保留",
    )
    dry_run: bool = Field(
        default=True, alias="dryRun",
        description="true 只预览；false 才把已授权的中断运行标记为失败",
    )
