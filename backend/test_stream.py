# -*- coding: utf-8 -*-
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.providers import model
t0 = time.time()
deltas = []
def on_delta(d):
    deltas.append(d)
    if d:
        sys.stdout.write(d)
        sys.stdout.flush()
def on_done(p):
    print("")
    print("DONE tools:", p.get("tool_calls"), "usage:", bool(p.get("usage")))
r = model.stream_chat("reasoning", [{"role":"user","content":"请用一句话介绍你自己"}],
                      temperature=0.2, on_delta=on_delta, on_done=on_done)
print("elapsed", round(time.time()-t0,1), "token_frames:", len(deltas))
