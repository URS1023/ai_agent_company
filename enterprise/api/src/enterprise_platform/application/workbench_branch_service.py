"""Native app-authorized creation and reads of actor-owned workbench branches.

Creating a root allocates only enterprise context. Native conversations are created
by their first real send. Existing roots are never reset; forks require a separate
history-reconstruction operation and are not accepted as root-creation requests.
"""

import asyncio
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from .contracts import Principal
from .errors import Conflict, InvalidInput, PersistenceError
from .workbench_branch_listing import BranchListQuery, BranchPage
from .workbench_branches import BranchContext
from .workbench_messages import MessageScope
from .workflow_setup_execution import NativeSetupSession


class BranchRepository(Protocol):
    def list_branches(self, query: BranchListQuery) -> BranchPage: ...

    def create_root(self, scope: MessageScope) -> BranchContext: ...

    def get(self, scope: MessageScope) -> BranchContext: ...


class NativeBranchAccess(Protocol):
    async def check_listing_access(
        self, principal: Principal, session: NativeSetupSession, query: BranchListQuery
    ) -> None: ...

    async def check_branch_access(
        self, principal: Principal, session: NativeSetupSession, scope: MessageScope
    ) -> None: ...


class WorkbenchBranchService:
    def __init__(self, repository: BranchRepository, access: NativeBranchAccess) -> None:
        self._repository = repository
        self._access = access

    async def list_branches(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        installed_app_id: UUID,
        after: str | None = None,
        limit: int = 50,
    ) -> BranchPage:
        """Authorize the native app before a bounded off-loop actor directory read."""
        try:
            query = BranchListQuery(
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                installed_app_id=installed_app_id,
                after=after,
                limit=limit,
            )
        except ValueError:
            raise InvalidInput("branch_list_query_invalid") from None
        await self._access.check_listing_access(principal, session, query)
        page = await asyncio.to_thread(self._repository.list_branches, query)
        try:
            page = BranchPage.model_validate_json(page.model_dump_json())
            if len(page.items) > query.limit:
                raise ValueError("Oversized page")
            ids = set()
            for branch in page.items:
                scope = branch.scope
                if (
                    (scope.workspace_id, scope.actor_id, scope.installed_app_id)
                    != (query.workspace_id, query.actor_id, query.installed_app_id)
                    or scope.branch_id in ids
                    or scope.branch_id == query.after
                ):
                    raise ValueError("Unexpected branch scope/cursor")
                ids.add(scope.branch_id)
            if page.next_after is not None and (
                len(page.items) != query.limit or page.next_after != page.items[-1].scope.branch_id
            ):
                raise ValueError("Invalid continuation")
        except (ValueError, TypeError, RecursionError):
            raise PersistenceError("branch_list_storage_invalid") from None
        return page

    async def _run(
        self,
        principal: Principal,
        session: NativeSetupSession,
        installed_app_id: UUID,
        branch_id: str,
        operation: Callable[[MessageScope], BranchContext],
    ) -> BranchContext:
        try:
            if installed_app_id.int == 0:
                raise ValueError("App identifier required")
            scope = MessageScope(
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                installed_app_id=installed_app_id,
                branch_id=branch_id,
            )
        except ValueError:
            raise InvalidInput("branch_scope_invalid") from None
        await self._access.check_branch_access(principal, session, scope)
        result = await asyncio.to_thread(operation, scope)
        try:
            result = BranchContext.model_validate_json(result.model_dump_json())
            if result.scope != scope:
                raise ValueError("Unexpected branch scope")
        except (ValueError, TypeError, RecursionError):
            raise PersistenceError("branch_context_storage_invalid") from None
        return result

    async def create_root(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        installed_app_id: UUID,
        branch_id: str,
    ) -> BranchContext:
        branch = await self._run(principal, session, installed_app_id, branch_id, self._repository.create_root)
        if branch.origin is not None:
            raise Conflict("branch_creation_conflict")
        return branch

    async def get(
        self,
        principal: Principal,
        session: NativeSetupSession,
        *,
        installed_app_id: UUID,
        branch_id: str,
    ) -> BranchContext:
        return await self._run(principal, session, installed_app_id, branch_id, self._repository.get)
