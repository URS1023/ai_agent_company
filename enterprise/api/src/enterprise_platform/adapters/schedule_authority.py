"""Operator-configured grants and live native-owner probe for scheduling.

The explicit file is reloaded on every lookup; it is never created, updated or
discovered here. Operators restrict access and replace it atomically. This adapter
is for the service's synchronous authority port, called in worker threads. It does
not provide a durable grant revision/lease or replace native authentication.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from enterprise_platform.application.contracts import Binding, Contract
from enterprise_platform.application.errors import DependencyUnavailable
from enterprise_platform.application.schedule_service import ServiceGrant


class _GrantDocument(Contract):
    grants: tuple[ServiceGrant, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def unique_actor_scopes(self) -> Self:
        keys = [(grant.principal.workspace_id, grant.principal.actor_id) for grant in self.grants]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate scheduler grant scope")
        return self


class FileScheduleAuthority:
    def __init__(self, path: Path, probe: Callable[[Binding], Awaitable[bool]]) -> None:
        self._path, self._probe = path, probe

    def get_grant(self, workspace_id: str, actor_id: str) -> ServiceGrant | None:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError("Grant document exceeds limit")
            document = _GrantDocument.model_validate_json(content, strict=True)
        except (OSError, ValueError):
            raise DependencyUnavailable("schedule_grant_source_unavailable") from None
        return next(
            (
                grant
                for grant in document.grants
                if (grant.principal.workspace_id, grant.principal.actor_id) == (workspace_id, actor_id)
            ),
            None,
        )

    def has_native_timer(self, binding: Binding) -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise DependencyUnavailable("schedule_authority_requires_worker_thread")
        binding = Binding.model_validate(binding.model_dump())

        async def inspect() -> bool:
            result = await self._probe(binding)
            if type(result) is not bool:
                raise DependencyUnavailable("schedule_native_owner_unavailable")
            return result

        return asyncio.run(inspect())
