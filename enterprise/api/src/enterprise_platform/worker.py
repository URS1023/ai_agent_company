"""Explicit single-run worker command using the verified deployment composition.

An operator selects dispatch or reconcile and an exact workspace/run. This command
does not serve HTTP, migrate schemas, scan queues, retry, or run a scheduler.
Runtime cleanup finishes before public output; credentials, inputs and nonce stay private.

Exit codes: 0 means this operation completed, not that a business assessment passed;
1 means configuration/operation failure; 2 means invalid arguments; 3 means the run
is uncertain and needs reconciliation; 4 means the run is failed or cancelled.
The explicit status remains authoritative for nonterminal outcomes such as dispatched.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from typing import NoReturn

from enterprise_platform.application.contracts import Run
from enterprise_platform.application.errors import EnterpriseError, InvalidState
from enterprise_platform.bootstrap import Runtime, Settings, create_runtime


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        """Avoid argparse echoing accidental credentials or control characters."""
        self.exit(2, '{"code":"worker_invalid_arguments"}\n')


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise argparse.ArgumentTypeError("Invalid identifier")
    return value


def _failure(code: str) -> int:
    sys.stderr.write(json.dumps({"code": code}, separators=(",", ":")) + "\n")
    return 1


def _execute(runtime: Runtime, action: str, workspace_id: str, run_id: str) -> Run:
    try:
        run = (
            runtime.dispatcher.dispatch(workspace_id, run_id)
            if action == "dispatch"
            else runtime.dispatcher.reconcile(workspace_id, run_id)
        )
        if run.workspace_id != workspace_id or run.id != run_id:
            raise InvalidState("worker_run_identity_mismatch")
        return run
    finally:
        runtime.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        description="Operate on one enterprise run; no automatic retry or queue scan", allow_abbrev=False
    )
    parser.add_argument("action", choices=("dispatch", "reconcile"))
    parser.add_argument("--workspace-id", required=True, type=_identifier)
    parser.add_argument("--run-id", required=True, type=_identifier)
    args = parser.parse_args(argv)

    try:
        runtime = create_runtime(Settings.from_environment())
    except KeyboardInterrupt:
        return _failure("worker_interrupted")
    except Exception:
        return _failure("worker_configuration_invalid")

    try:
        run = _execute(runtime, args.action, args.workspace_id, args.run_id)
    except EnterpriseError as error:
        return _failure(error.code)
    except KeyboardInterrupt:
        return _failure("worker_interrupted")
    except Exception:
        return _failure("worker_operation_failed")

    sys.stdout.write(json.dumps({"run_id": run.id, "status": run.status}, separators=(",", ":")) + "\n")
    if run.status == "uncertain":
        return 3
    if run.status in {"failed", "cancelled"}:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
