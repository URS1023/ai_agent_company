"""Create one real chart artifact in an isolated UI-acceptance database.

中文说明（聊天产物夹具播入器，非测试用例）：
用途：在隔离验收库里为 workspace-admin 生成一张真实的柱状图产物（PNG，类型 chart），
供 tests/ui/chat_artifact_markdown.js 校验 markdown 中的产物预览/下载渲染。
运行方式：在已切换到隔离库（APP_ENV=test 等）的环境下 python tests/ui/seed_chat_artifact.py，
标准输出为 JSON（workspace_id 与产物引用）。
影响范围：写库（新增产物记录）并占用存储；只允许在隔离库运行——脚本会校验管理员与成员关系，
找不到时直接报错，不会向生产库写入。
"""
from __future__ import annotations

import io
import json

from PIL import Image, ImageDraw
from sqlalchemy import select

from app.db import new_session
from app.models import AuthIdentity, WorkspaceMember
from app.modules.artifact.service import artifact_reference, create_artifact


def main() -> None:
    db = new_session()
    try:
        identity = db.scalar(select(AuthIdentity).where(
            AuthIdentity.provider == "local",
            AuthIdentity.username_normalized == "workspace-admin",
        ))
        if identity is None:
            raise RuntimeError("isolated workspace administrator has not been bootstrapped")
        membership = db.scalar(select(WorkspaceMember).where(
            WorkspaceMember.user_id == identity.user_id,
        ).order_by(WorkspaceMember.created_at))
        if membership is None:
            raise RuntimeError("isolated workspace membership is missing")

        image = Image.new("RGB", (720, 360), "#f7f9fc")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 36, 680, 316), fill="#ffffff", outline="#cbd6e4", width=2)
        bars = [118, 184, 146, 232, 268]
        colors = ["#2463eb", "#3a75ef", "#5087f1", "#6799f4", "#7dabf6"]
        for index, height in enumerate(bars):
            left = 92 + index * 108
            draw.rectangle((left, 286 - height, left + 58, 286), fill=colors[index])
        draw.line((72, 286, 646, 286), fill="#7d8da5", width=2)
        draw.line((72, 72, 72, 286), fill="#7d8da5", width=2)
        output = io.BytesIO()
        image.save(output, format="PNG")
        artifact = create_artifact(
            db,
            workspace_id=membership.workspace_id,
            run_id="isolated-ui-chart-preview",
            owner_user_id=identity.user_id,
            type="chart",
            name="north_china_analysis.png",
            data=output.getvalue(),
            metadata={"fixture": "chat-markdown-preview"},
        )
        db.commit()
        print(json.dumps({
            "workspace_id": membership.workspace_id,
            "artifact": artifact_reference(artifact),
        }, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
