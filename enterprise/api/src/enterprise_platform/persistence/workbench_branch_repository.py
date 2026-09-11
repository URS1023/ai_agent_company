"""Transactional branch lifecycle; native reconstruction and authorization stay outside.

Forks lock and revision-check their parent before inserting a pending child. Binding
accepts only externally verified reconstruction output, never creates a native
conversation. All results are returned after commit. This repository does not yet
coordinate branch-head advancement with message dispatch claims.
"""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.errors import Conflict, NotFound, PersistenceError
from enterprise_platform.application.workbench_branch_listing import BranchListQuery, BranchPage
from enterprise_platform.application.workbench_branches import (
    BranchContext,
    bind_fork_context,
    create_root_branch,
    fork_branch,
)
from enterprise_platform.application.workbench_messages import MessageScope

from .mapping import audit, transaction, utc_now, validate_revision
from .workbench_branches import BranchContextRow, as_branch_context, branch_context_row


def branch_query(scope: MessageScope) -> Select[tuple[BranchContextRow]]:
    return (
        select(BranchContextRow)
        .where(
            BranchContextRow.workspace_id == scope.workspace_id,
            BranchContextRow.actor_id == scope.actor_id,
            BranchContextRow.installed_app_id == str(scope.installed_app_id),
            BranchContextRow.branch_id == scope.branch_id,
        )
        .execution_options(populate_existing=True)
    )


def _audit(session: Session, branch: BranchContext, event: str) -> None:
    audit(
        session,
        branch.scope.workspace_id,
        "chat_branch",
        branch.scope.branch_id,
        branch.scope.actor_id,
        event,
        utc_now(),
        {"revision": branch.revision, "state": branch.state, "installed_app_id": str(branch.scope.installed_app_id)},
    )


def _locked(session: Session, scope: MessageScope, expected_revision: int) -> tuple[BranchContextRow, BranchContext]:
    row = session.scalar(branch_query(scope).with_for_update())
    if row is None:
        raise NotFound("branch_not_found")
    current = as_branch_context(row, scope)
    if current.revision != expected_revision:
        raise Conflict("branch_revision_conflict")
    return row, current


def _insert(session: Session, requested: BranchContext) -> BranchContext:
    candidate = branch_context_row(requested)
    inserted = session.scalar(
        insert(BranchContextRow)
        .values(
            workspace_id=candidate.workspace_id,
            actor_id=candidate.actor_id,
            installed_app_id=candidate.installed_app_id,
            branch_id=candidate.branch_id,
            revision=candidate.revision,
            state=candidate.state,
            conversation_id=candidate.conversation_id,
            head_message_id=candidate.head_message_id,
            document_json=candidate.document_json,
            document_hash=candidate.document_hash,
        )
        .on_conflict_do_nothing(
            index_elements=[
                BranchContextRow.workspace_id,
                BranchContextRow.actor_id,
                BranchContextRow.installed_app_id,
                BranchContextRow.branch_id,
            ]
        )
        .returning(BranchContextRow.branch_id)
    )
    if inserted is not None and inserted != requested.scope.branch_id:
        raise PersistenceError("branch_insert_identity_invalid")
    row = session.scalar(branch_query(requested.scope).with_for_update())
    if row is None:
        raise PersistenceError("branch_insert_not_visible")
    current = as_branch_context(row, requested.scope)
    if current.origin != requested.origin:
        raise Conflict("branch_creation_conflict")
    if inserted is not None:
        if current != requested:
            raise PersistenceError("branch_insert_state_invalid")
        _audit(session, current, "chat_branch_created")
    return current


class SqlAlchemyBranchRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, scope: MessageScope) -> BranchContext:
        with transaction(self._sessions) as session:
            row = session.scalar(branch_query(scope))
            if row is None:
                raise NotFound("branch_not_found")
            result = as_branch_context(row, scope)
        return result

    def list_branches(self, query: BranchListQuery) -> BranchPage:
        """Read an app-authorized actor directory without locks, dispatch or mutation.

        Every row, including the continuation sentinel, must pass canonical storage
        and expected-scope validation. Page ordering is stable by branch identifier.
        """
        statement = select(BranchContextRow).where(
            BranchContextRow.workspace_id == query.workspace_id,
            BranchContextRow.actor_id == query.actor_id,
            BranchContextRow.installed_app_id == str(query.installed_app_id),
        )
        if query.after is not None:
            statement = statement.where(BranchContextRow.branch_id > query.after)
        statement = statement.order_by(BranchContextRow.branch_id).limit(query.limit + 1)
        with transaction(self._sessions) as session:
            rows = session.scalars(statement).all()
            branches = tuple(
                as_branch_context(
                    row,
                    MessageScope(
                        workspace_id=query.workspace_id,
                        actor_id=query.actor_id,
                        installed_app_id=query.installed_app_id,
                        branch_id=row.branch_id,
                    ),
                )
                for row in rows
            )
            items = branches[: query.limit]
            result = BranchPage(
                items=items,
                next_after=items[-1].scope.branch_id if len(branches) > query.limit else None,
            )
        return result

    def create_root(self, scope: MessageScope) -> BranchContext:
        requested = create_root_branch(scope)
        with transaction(self._sessions) as session:
            result = _insert(session, requested)
        return result

    def fork(
        self, parent_scope: MessageScope, branch_id: str, message_id: UUID, *, expected_revision: int
    ) -> BranchContext:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            _, parent = _locked(session, parent_scope, expected_revision)
            result = _insert(session, fork_branch(parent, branch_id, message_id))
        return result

    def bind_fork(
        self, scope: MessageScope, conversation_id: UUID, head_message_id: UUID, *, expected_revision: int
    ) -> BranchContext:
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            row, current = _locked(session, scope, expected_revision)
            result = bind_fork_context(current, conversation_id, head_message_id)
            updated = branch_context_row(result)
            row.revision, row.state = updated.revision, updated.state
            row.conversation_id, row.head_message_id = updated.conversation_id, updated.head_message_id
            row.document_json, row.document_hash = updated.document_json, updated.document_hash
            _audit(session, result, "chat_branch_context_bound")
            session.flush()
        return result
