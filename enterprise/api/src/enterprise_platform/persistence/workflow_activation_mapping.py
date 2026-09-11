"""Validate mirrored activation authority against its complete frozen receipt."""

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workflow_activation_contracts import ActivationView

from .mapping import decode, serialized, utc
from .workflow_activation_models import WorkflowActivationRow


def activation_row(value: ActivationView) -> WorkflowActivationRow:
    value = ActivationView.model_validate(value.model_dump())
    return WorkflowActivationRow(
        workspace_id=value.binding.workspace_id,
        activation_id=value.id,
        enrollment_id=value.enrollment.id,
        app_id=value.binding.app_id,
        workflow_id=str(value.binding.workflow_id),
        key_id=value.key_id,
        binding_id=value.binding.id,
        binding_revision=value.binding.revision,
        revision=value.revision,
        active=value.active,
        public_json=serialized(value),
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def as_activation(row: WorkflowActivationRow) -> ActivationView:
    value = decode(ActivationView, row.public_json)
    try:
        expected = activation_row(value)
        for column in WorkflowActivationRow.__table__.columns:
            key = column.key
            if key not in {"public_json", "created_at", "updated_at"} and getattr(row, key) != getattr(expected, key):
                raise PersistenceError("stored_activation_scope_invalid")
        if utc(row.created_at) != value.created_at or utc(row.updated_at) != value.updated_at:
            raise PersistenceError("stored_activation_timestamp_invalid")
        return value
    except (ValueError, TypeError, AttributeError):
        raise PersistenceError("stored_activation_invalid") from None
