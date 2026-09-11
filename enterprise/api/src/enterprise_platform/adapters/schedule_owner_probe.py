"""Resolve exact active publication before and after a native ownership snapshot.

This reduces stale receipt use but is not a distributed lease. The inspection
session is a server-supplied read credential, never the execution actor authority.
No signing key, graph hash override, or latest-version fallback is accepted.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from enterprise_platform.application.contracts import Binding, Principal
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable
from enterprise_platform.application.workflow_publication_read import NativePublicationMetadata
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .dify_identity import DifyIdentityClient
from .schedule_owner_client import DifyScheduleOwnerClient


class ActivePublicationLookup(Protocol):
    def find_registration(
        self, workspace_id: str, app_id: str, *, workflow_id: UUID, expected_binding: Binding | None = None
    ) -> NativePublicationMetadata | None: ...


class ActivationScheduleOwnerProbe:
    def __init__(
        self,
        lookup: ActivePublicationLookup,
        identity: DifyIdentityClient,
        client: DifyScheduleOwnerClient,
        sessions: Callable[[], Awaitable[NativeSetupSession]],
    ) -> None:
        self._lookup, self._identity, self._client, self._sessions = lookup, identity, client, sessions

    async def _publication(self, binding: Binding) -> NativePublicationMetadata:
        value = await asyncio.to_thread(
            self._lookup.find_registration,
            binding.workspace_id,
            binding.app_id,
            workflow_id=binding.workflow_id,
            expected_binding=binding,
        )
        if value is None:
            raise DependencyUnavailable("schedule_active_publication_unavailable")
        value = NativePublicationMetadata.model_validate(value.model_dump())
        if (value.workspace_id, value.app_id, value.workflow_id) != (
            binding.workspace_id,
            binding.app_id,
            str(binding.workflow_id),
        ):
            raise DependencyUnavailable("schedule_active_publication_mismatch")
        return value

    async def __call__(self, binding: Binding) -> bool:
        binding = Binding.model_validate(binding.model_dump())
        before = await self._publication(binding)
        session = await self._sessions()
        principal = await self._identity.resolve(
            cookie_header=session.cookie_header,
            authorization=session.authorization,
            csrf_token=session.csrf_token,
        )
        principal = Principal.model_validate(principal.model_dump())
        if principal.workspace_id != binding.workspace_id:
            raise AccessDenied("schedule_inspection_workspace_mismatch")
        owner = await self._client.read(
            principal,
            session,
            app_id=before.app_id,
            workflow_id=before.workflow_id,
            expected_hash=before.graph_hash,
        )
        if type(owner) is not bool or await self._publication(binding) != before:
            raise DependencyUnavailable("schedule_ownership_snapshot_changed")
        return owner
