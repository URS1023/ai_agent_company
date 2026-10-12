# -*- coding: utf-8 -*-
"""Deterministic UI artifact verification: real docx + chart artifacts -> message with artifact parts."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.db import new_session
from app.models import Conversation, Run, User, Workspace, Message, Artifact
from app.ids import new_id
from datetime import datetime
from sqlalchemy import select
from app.providers.tool.gateway import ToolGateway, build_context
from app.providers.tool.sandbox_provider import invoke as sandbox_invoke
from app.providers.tool.base import ToolSpec
from app.modules.artifact.service import create_artifact

db = new_session()
ws = db.query(Workspace).first()
user = db.query(User).first()
conv = Conversation(id=new_id(), workspace_id=ws.id, owner_user_id=user.id, title="UI工件预览示例",
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow())
db.add(conv); db.flush()
run = Run(id=new_id(), workspace_id=ws.id, conversation_id=conv.id, started_by_user_id=user.id,
          status="completed", created_at=datetime.utcnow(), started_at=datetime.utcnow(),
          completed_at=datetime.utcnow(), model_profile="reasoning")
db.add(run); db.flush(); db.commit()

gw = ToolGateway(db)
ctx = build_context(db=db, workspace_id=ws.id, user_id=user.id, run_id=run.id, conversation_id=conv.id)
spec_doc = {"title": "UI 工件预览示例文档",
            "sections": [{"title": "项目实施方案", "blocks": [
                {"type": "p", "text": "本项目为北京园区智能楼宇项目实施，含弱电、安防与 BA 自控。"},
                {"type": "table", "rows": [["阶段", "时间", "交付物"], ["方案设计", "第1-2周", "设计文档"], ["实施", "第3-12周", "现场交付"]]}
            ]}]}
res = gw.execute(ws.id, "render_document", {"document_spec": spec_doc}, ctx)
doc_art_id = res.data.get("artifact_id") if res.data else None

code = """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.figure(figsize=(6,4))
plt.bar(["华北","华东","华南"],[1200,980,860])
plt.title("区域销售(万元)")
plt.savefig("chart.png")
result = "ok"
"""
spec_py = ToolSpec(name="python_execute", description="", provider="sandbox",
                   input_schema={"type": "object", "properties": {"code": {"type": "string"}}}, timeout_seconds=60)
sres = sandbox_invoke(spec_py, {"code": code}, ctx)
chart_art = None
if sres.artifacts:
    from pathlib import Path
    data = Path(sres.artifacts[0]["path"]).read_bytes()
    chart_art = create_artifact(db, workspace_id=ws.id, run_id=run.id, owner_user_id=user.id,
                                type="chart", name="区域销售图表.png", data=data, metadata={"tool": "python_execute"})
db.commit()
print("doc artifact:", doc_art_id, "| chart artifact:", chart_art.id if chart_art else None)

parts = [
    {"type": "text", "text": "以下两份工件可点击“下载”或“在网页预览”查看（图表可内联预览）。"},
    {"type": "artifact", "artifact": {"id": doc_art_id, "type": "document", "name": "UI 工件预览示例文档.docx",
                                      "download_url": "/api/artifacts/" + str(doc_art_id) + "/download"}},
    {"type": "artifact", "artifact": {"id": chart_art.id, "type": "chart", "name": "区域销售图表.png",
                                      "download_url": "/api/artifacts/" + chart_art.id + "/download"}}
]
db.add(Message(id=new_id(), workspace_id=ws.id, conversation_id=conv.id, role="user", sequence=0,
               content="请生成本项目实施方案文档与销售图表", status="completed",
               created_at=datetime.utcnow(), parts_json=[{"type": "text", "text": "请生成本项目实施方案文档与销售图表"}]))
db.add(Message(id=new_id(), workspace_id=ws.id, conversation_id=conv.id, role="assistant", sequence=1,
               content="以下两份工件可点击下载或预览。", status="completed",
               created_at=datetime.utcnow(), parts_json=parts))
db.commit()

run_arts = db.execute(select(Artifact).where(Artifact.run_id == run.id)).scalars().all()
print("run artifacts in DB:", [(a.id[:8], a.type, a.name) for a in run_arts])
print("conversation:", conv.id, "run:", run.id)
db.close()
