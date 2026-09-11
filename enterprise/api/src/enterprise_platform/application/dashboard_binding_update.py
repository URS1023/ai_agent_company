"""Data-binding document transition; callers authorize queries before committing.

Partial configurations may be saved as drafts. Refresh requires complete mandatory
slot coverage. Changing queries invalidates the old result batch rather than
presenting data from a previous query as the new configuration's output.
"""

from dataclasses import replace

from enterprise_platform.domain import dashboard as d

from .dashboard_refresh_service import DashboardRecord
from .errors import InvalidInput


def replace_dashboard_bindings(record: DashboardRecord, bindings: tuple[d.ExecutionBinding, ...]) -> DashboardRecord:
    slots = {slot.slot_id: slot for slot in record.design.slots}
    seen: set[str] = set()
    for binding in bindings:
        slot = slots.get(binding.slot_id)
        if slot is None or binding.slot_id in seen:
            raise InvalidInput("invalid_dashboard_bindings")
        seen.add(binding.slot_id)
        mapped = set(binding.proposal.field_map)
        if mapped - {column.name for column in slot.columns} or any(
            column.required and column.name not in mapped for column in slot.columns
        ):
            raise InvalidInput("invalid_dashboard_bindings")
    if d.binding_set_identity(bindings) == d.binding_set_identity(record.bindings):
        return record
    return replace(
        record, revision=record.revision + 1, bindings=bindings, state=d.RefreshState(record.design.identity)
    )
