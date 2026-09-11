import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from test_workflow_activation_contracts import create

from enterprise_platform.application.errors import PersistenceError
from enterprise_platform.persistence.workflow_activation_mapping import activation_row, as_activation
from enterprise_platform.persistence.workflow_activation_models import ActivationBase, WorkflowActivationRow


def test_activation_roundtrip_and_unique_version_key():
    value = create()
    assert as_activation(activation_row(value)) == value
    assert set(ActivationBase.metadata.tables) == {"enterprise_workflow_activations"}
    ddl = str(CreateTable(WorkflowActivationRow.__table__).compile(dialect=postgresql.dialect()))
    assert "UNIQUE (key_id, workflow_id)" in ddl
    assert "UNIQUE (workspace_id, enrollment_id)" in ddl
    assert "secret TEXT" not in ddl


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "other"),
        ("app_id", "other"),
        ("enrollment_id", "other"),
        ("workflow_id", "other"),
        ("binding_id", "other"),
        ("binding_revision", 9),
        ("key_id", "other"),
        ("active", False),
        ("revision", 8),
        ("public_json", "{}"),
    ],
)
def test_mirrored_authority_corruption_rejected(field, value):
    row = activation_row(create())
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        as_activation(row)
