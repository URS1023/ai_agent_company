# -*- coding: utf-8 -*-
"""Backfill: message file parts with placeholder name 'file' -> real filename."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.db import new_session
from app.models import Message, File, Workspace, User
from sqlalchemy import select

db = new_session()
ws = db.query(Workspace).first()
files = {f.id: f.name for f in db.execute(select(File)).scalars().all()}
rows = db.execute(select(Message).where(Message.role == "user", Message.parts_json.isnot(None))).scalars().all()
updated = 0
for m in rows:
    changed = False
    parts = m.parts_json
    if isinstance(parts, list):
        for p in parts:
            if isinstance(p, dict) and p.get("type") == "file":
                f = p.get("file") or {}
                fid = f.get("id") or ""
                if f.get("name") in ("file", "", None) and fid in files:
                    f["name"] = files[fid]
                    f["mime_type"] = f.get("mime_type") or ""
                    changed = True
    if changed:
        m.parts_json = parts
        updated += 1
db.commit()
print("messages backfilled:", updated)
db.close()
