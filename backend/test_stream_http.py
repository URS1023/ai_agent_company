# -*- coding: utf-8 -*-
import os, sys, time, json
import httpx
BASE = "http://127.0.0.1:8000"
c = httpx.Client(timeout=httpx.Timeout(400.0, connect=10.0))
H = {"Authorization": "Bearer " + c.post(BASE+"/api/auth/login", json={"username":"workspace-admin","password":"admin123"}).json()["token"]}
conv = c.post(BASE + "/api/chat/conversations", headers=H, json={"title":"流式测试"}).json()["id"]
events = []
t_start = time.time()
with c.stream("POST", BASE + f"/api/chat/conversations/{conv}/send", headers={**H,"Content-Type":"application/json"},
              json={"content":"请用两句话介绍贵公司AI工作平台的功能"}) as resp:
    for line in resp.iter_lines():
        if line.startswith("event:"): ev = line[6:].strip()
        elif line.startswith("data:"):
            try:
                d = json.loads(line[5:])
                if ev == "message.delta" and d.get("delta"):
                    events.append((round(time.time()-t_start,1), d["delta"]))
                elif ev in ("tool.started", "tool.completed"):
                    events.append((round(time.time()-t_start,1), "[" + ev + "] " + (d.get("tool") or "")))
                elif ev == "run.completed":
                    events.append((round(time.time()-t_start,1), "[run.completed]"))
            except Exception: pass
print("total frames:", len(events))
# show growth: content length over successive deltas for first text burst
lens = []
text = ""
for (tstamp, frag) in events:
    if isinstance(frag, str) and not frag.startswith("["):
        text += frag
        lens.append((tstamp, len(text)))
print("delta count (text):", len([e for e in events if isinstance(e[1],str) and not e[1].startswith('[')]))
print("sample growth:", lens[:8], "...", lens[-3:] if len(lens)>3 else "")
print("final text len:", len(text))
print("all events sample:", events[:14])
