"""Retired database benchmark entry point. This file never opens a connection.

The incident snapshot remains locally in:
.implementation/sql-benchmark-20260911/benchmark_workflow_reads.pre_retirement.py.txt

Use benchmark_local_szh.py for the bounded, explicitly local comparison.
Non-local database testing requires the user's explicit authorization for that task.
"""

if __name__ == '__main__':
    raise SystemExit('This benchmark is retired. No database connection was opened. Use benchmark_local_szh.py for local szh_new only.')
