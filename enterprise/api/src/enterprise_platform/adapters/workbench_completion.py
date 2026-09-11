"""Chat completion proof from a fully consumed trusted stream plus native state.

Plain chat requires server-persisted terminal metadata bound to this task.
This does not classify unmarked pre-workflow moderation/stop, abandoned streams or
paused/resumed tasks. Those paths retain occupancy for a separate reconciler. A caller
must invoke reconcile only after clean stream exhaustion, not a client completion claim.
"""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import TypeAdapter

from enterprise_platform.application.contracts import JsonObject, Principal
from enterprise_platform.application.errors import AccessDenied
from enterprise_platform.application.workbench_messages import NativeGenerationTerminal, NativeMessageReceipt
from enterprise_platform.application.workflow_setup_execution import NativeSetupSession

from .workbench_context_client import GenerationStateObservation
from .workbench_stream import ChatStreamInvalid

type WorkflowOutcome = Literal["succeeded", "partial-succeeded", "failed", "stopped"]
_OUTCOME: TypeAdapter[WorkflowOutcome] = TypeAdapter(WorkflowOutcome)


class NativeGenerationReader(Protocol):
    async def read_generation_state(
        self,
        principal: Principal,
        session: NativeSetupSession,
        receipt: NativeMessageReceipt,
    ) -> GenerationStateObservation: ...


class ChatStreamCompletion:
    def __init__(self, receipt: NativeMessageReceipt) -> None:
        self._receipt = NativeMessageReceipt.model_validate_json(receipt.model_dump_json())
        self._run: str | None = None
        self._started = False
        self._outcome: WorkflowOutcome | None = None
        self._message_end = False
        self._paused = False
        self._error = False

    def observe(self, event: JsonObject) -> None:
        receipt = self._receipt
        try:
            kind = event["event"]
            if event.get("conversation_id") != str(receipt.conversation_id) or event.get("message_id") != str(
                receipt.message_id
            ):
                raise ValueError("Message identity changed")
            if event.get("task_id") != receipt.task_id and not (kind == "error" and "task_id" not in event):
                raise ValueError("Task identity changed")
            if kind == "message_end":
                self._message_end = True
            elif kind == "workflow_paused":
                self._paused = True
            elif kind == "error":
                self._error = True
            elif kind in {"workflow_started", "workflow_finished"}:
                run = event.get("workflow_run_id")
                data = event.get("data")
                if not isinstance(run, str) or not isinstance(data, dict) or data.get("id") != run:
                    raise ValueError("Workflow identity missing")
                parsed = UUID(run)
                if not parsed.int or str(parsed) != run or (self._run is not None and self._run != run):
                    raise ValueError("Workflow identity changed")
                outcome: WorkflowOutcome | None = None
                if kind == "workflow_finished":
                    outcome = _OUTCOME.validate_python(data.get("status"))
                    finished = data.get("finished_at")
                    if type(finished) is not int or finished < 0:
                        raise ValueError("Workflow finish time missing")
                    if self._outcome is not None and self._outcome != outcome:
                        raise ValueError("Conflicting completion")
                self._run = run
                if kind == "workflow_started":
                    self._started = True
                else:
                    self._outcome = outcome
        except (ValueError, TypeError, KeyError):
            raise ChatStreamInvalid("Native workflow completion unavailable") from None

    async def reconcile(
        self,
        principal: Principal,
        session: NativeSetupSession,
        reader: NativeGenerationReader,
    ) -> NativeGenerationTerminal | None:
        scope = self._receipt.scope
        if (principal.workspace_id, principal.actor_id) != (scope.workspace_id, scope.actor_id):
            raise AccessDenied()
        outcome = self._outcome
        plain = self._run is None
        if self._paused:
            return None
        if plain:
            if not self._message_end and not self._error:
                return None
        else:
            if not self._started or outcome is None:
                return None
            if outcome != "failed" and (not self._message_end or self._error):
                return None
        observed = await reader.read_generation_state(principal, session, self._receipt)
        observed = GenerationStateObservation.model_validate_json(observed.model_dump_json())
        if (
            observed.workspace_id != scope.workspace_id
            or observed.actor_id != scope.actor_id
            or observed.installed_app_id != str(scope.installed_app_id)
            or observed.conversation_id != str(self._receipt.conversation_id)
            or observed.message_id != str(self._receipt.message_id)
        ):
            return None
        if plain:
            marker = observed.message_terminal
            if marker is None or marker.task_id != self._receipt.task_id or observed.workflow_run_id is not None:
                return None
            if (marker.outcome == "failed") != self._error:
                return None
            return NativeGenerationTerminal(receipt=self._receipt, outcome=marker.outcome)
        if (
            observed.workflow_run_id != self._run
            or observed.workflow_status != outcome
            or not observed.workflow_finished
            or observed.message_status != ("error" if outcome == "failed" else "normal")
            or outcome is None
        ):
            return None
        return NativeGenerationTerminal(receipt=self._receipt, outcome=outcome)
