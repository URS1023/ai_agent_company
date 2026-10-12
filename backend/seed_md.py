# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime
from app.db import new_session
from app.models import Workspace, User, Conversation, Message
from app.ids import new_id
db = new_session(); ws = db.query(Workspace).first(); user = db.query(User).first()
conv = Conversation(id=new_id(), workspace_id=ws.id, owner_user_id=user.id, title="markdown渲染验证",
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow())
db.add(conv); db.flush()
md = "### 要点\n\n- 第一项**加粗关键点**\n- 第二项[点此下载](/api/artifacts/x/download)\n\n| 项目 | 金额 |\n|---|---|\n| 建行 | 206,753 |\n| 合计 | 440,478 |\n\n\u0060\u0060\u0060python\nprint(1+1)\n\u0060\u0060\u0060"
db.add(Message(id=new_id(), workspace_id=ws.id, conversation_id=conv.id, role="user", sequence=0,
               content="请展示表格与代码块", status="completed", created_at=datetime.utcnow(),
               parts_json=[{"type":"text","text":"请展示表格与代码块"}]))
db.add(Message(id=new_id(), workspace_id=ws.id, conversation_id=conv.id, role="assistant", sequence=1,
               content=md, status="completed", created_at=datetime.utcnow(),
               parts_json=[{"type":"text","text":md}]))
db.commit()
print("conv:", conv.id)
db.close();
