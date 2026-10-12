# -*- coding: utf-8 -*-
import httpx
BASE="http://127.0.0.1:8000"
c=httpx.Client(timeout=httpx.Timeout(30.0))
t=c.post(BASE+"/api/auth/login", json={"username":"workspace-admin","password":"admin123"}).json()["token"]
H={"Authorization":"Bearer "+t}
import json
for path in ["/api/presentation-templates","/api/models/profiles","/api/chat/conversations","/api/runs","/api/workspace/members","/api/audit?limit=3","/api/webinfo"]:
    try:
        r=c.get(BASE+path, headers=H)
        body = r.json()
        if path=="/api/presentation-templates": print(path, r.status_code, "templates:", len(body.get("items",[])))
        elif path=="/api/models/profiles": print(path, r.status_code, "profiles:", len(body.get("items",[])))
        elif path=="/api/chat/conversations": print(path, r.status_code, "convs:", len(body.get("items",[])))
        elif path=="/api/workspace/members": print(path, r.status_code, "members:", len(body.get("items",[])))
        else: print(path, r.status_code, "ok" if r.status_code==200 else body)
    except Exception as e:
        print(path, "ERR", str(e)[:80])
