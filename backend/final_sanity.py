# -*- coding: utf-8 -*-
import os, sys, json
import httpx
BASE = "http://127.0.0.1:8000"
c = httpx.Client(timeout=httpx.Timeout(60.0))
H = {"Authorization": "Bearer " + c.post(BASE+"/api/auth/login", json={"username":"workspace-admin","password":"admin123"}).json()["token"]}
def check(name, fn):
    try:
        r = fn()
        ok = "OK" if 200 == r.status_code or 201 == r.status_code else "FAIL"
        print(f"{ok} {name} -> {r.status_code}")
    except Exception as e:
        print(f"FAIL {name} -> ERR {str(e)[:80]}")
check("health", lambda: c.get(BASE+"/api/health"))
check("me", lambda: c.get(BASE+"/api/auth/me", headers=H))
check("conversations", lambda: c.get(BASE+"/api/chat/conversations", headers=H))
check("files", lambda: c.get(BASE+"/api/files", headers=H))
check("knowledge", lambda: c.get(BASE+"/api/knowledge", headers=H))
check("knowledge.search", lambda: c.post(BASE+"/api/knowledge/search", headers=H, json={"query":"资质","top_k":3}))
check("skills", lambda: c.get(BASE+"/api/skills", headers=H))
check("plugins", lambda: c.get(BASE+"/api/plugins", headers=H))
check("connectors", lambda: c.get(BASE+"/api/connectors", headers=H))
check("datasources", lambda: c.get(BASE+"/api/datasources", headers=H))
check("runs", lambda: c.get(BASE+"/api/runs", headers=H))
check("artifacts", lambda: c.get(BASE+"/api/artifacts", headers=H))
check("audit", lambda: c.get(BASE+"/api/audit", headers=H))
check("secrets", lambda: c.get(BASE+"/api/secrets", headers=H))
check("workspace.members", lambda: c.get(BASE+"/api/workspace/members", headers=H))
check("models.profiles", lambda: c.get(BASE+"/api/models/profiles", headers=H))
check("presentation.templates", lambda: c.get(BASE+"/api/presentation-templates", headers=H))
check("presentation.resources", lambda: c.get(BASE+"/api/presentation-templates/resources", headers=H))
check("webinfo", lambda: c.get(BASE+"/api/webinfo"))
