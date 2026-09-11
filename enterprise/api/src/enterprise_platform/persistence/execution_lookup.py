"""Resolve only the native execution association recorded by streaming dispatch.

No fallback to model-supplied business IDs. Missing association is distinguishable
from ambiguous/corrupted storage; a native execution must identify just one run
inside a workspace. The caller verifies workflow, state and attested node policy.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.adapters.dify_workflows import validate_native_run_id
from enterprise_platform.application.contracts import Run
from enterprise_platform.application.errors import InvalidState

from .mapping import as_run, transaction
from .models import RunRow


class SqlAlchemyExecutionLookup:
    _sessions: sessionmaker[Session]

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def find_native_run(self, workspace_id: str, app_id: str, native_run_id: str) -> Run | None:
        validate_native_run_id(native_run_id)
        with transaction(self._sessions) as session:
            rows = tuple(
                session.scalars(
                    select(RunRow)
                    .where(
                        RunRow.workspace_id == workspace_id,
                        RunRow.dify_run_id == native_run_id,
                    )
                    .limit(2)
                )
            )
            if len(rows) > 1:
                raise InvalidState("ambiguous_native_execution")
            if not rows:
                return None
            run = as_run(rows[0])
            if run.workspace_id != workspace_id or run.spec.app_id != app_id or run.dify_run_id != native_run_id:
                raise InvalidState("native_execution_scope_mismatch")
            return run
