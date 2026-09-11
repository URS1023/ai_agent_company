"""Validated storage projection; no token plaintext belongs in enrollment rows."""

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.application.workflow_enrollment_contracts import EnrollmentView, StoredEnrollment

from .mapping import decode, serialized, utc
from .workflow_enrollment_models import WorkflowEnrollmentRow


def enrollment_row(value: StoredEnrollment) -> WorkflowEnrollmentRow:
    value = StoredEnrollment.model_validate(value)
    view = value.view
    return WorkflowEnrollmentRow(
        workspace_id=view.provisioning.workspace_id,
        enrollment_id=view.id,
        provisioning_id=view.provisioning.id,
        app_id=view.provisioning.app_id,
        actor_id=view.provisioning.actor_id,
        revision=view.revision,
        state=view.state,
        secret_ref=value.secret_ref,
        claim_nonce=value.claim_nonce,
        public_json=serialized(view),
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def as_enrollment(row: WorkflowEnrollmentRow) -> StoredEnrollment:
    view = decode(EnrollmentView, row.public_json)
    try:
        value = StoredEnrollment(view=view, secret_ref=row.secret_ref, claim_nonce=row.claim_nonce)
        expected = enrollment_row(value)
        for column in WorkflowEnrollmentRow.__table__.columns:
            key = column.key
            if key not in {"public_json", "created_at", "updated_at"} and getattr(row, key) != getattr(expected, key):
                raise PersistenceError("stored_enrollment_scope_invalid")
        if utc(row.created_at) != view.created_at or utc(row.updated_at) != view.updated_at:
            raise PersistenceError("stored_enrollment_timestamp_invalid")
        return value
    except (ValueError, TypeError, AttributeError):
        raise PersistenceError("stored_enrollment_internal_invalid") from None
