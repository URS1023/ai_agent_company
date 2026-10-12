# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.providers.tool.database_provider import _is_read_only_sql
cases = [
  ("SELECT * FROM sales", True),
  ("SELECT 1", True),
  ("INSERT INTO sales VALUES (1)", False),
  ("DELETE FROM sales", False),
  ("SELECT * FROM sales INTO OUTFILE '/tmp/x.csv'", True),  # filesystem write via SELECT
  ("SELECT * FROM sales FOR UPDATE", True),                  # write-lock via SELECT
  ("WITH d AS (SELECT 1) SELECT * FROM d", True),
  ("SELECT 1; DROP TABLE sales", False),
]
for sql, expect in cases:
    got = _is_read_only_sql(sql)
    flag = "OK " if got == expect else "!! "
    print(flag, repr(sql), "-> allowed:", got, "(expected:", expect, ")")
