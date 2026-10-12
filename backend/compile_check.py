# -*- coding: utf-8 -*-
import ast, os, sys
root = os.path.dirname(os.path.abspath(__file__))
errors = []
parsed = 0
for dirpath, dirnames, filenames in os.walk(os.path.join(root, "app")):
    for fn in filenames:
        if fn.endswith(".py"):
            p = os.path.join(dirpath, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    ast.parse(f.read())
                parsed += 1
            except SyntaxError as e:
                errors.append((p, f"{e.lineno}:{e.offset} {e.msg}"))
if errors:
    for e in errors:
        print("SYNTAX ERR:", e)
    sys.exit(1)
print("compile check OK —", parsed, "files parsed")
