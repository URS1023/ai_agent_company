"""Exercise native input validation, not a parallel workbench type system."""

from importlib import import_module
from unittest.mock import patch

import pytest

from core.app.entities.app_invoke_entities import InvokeFrom, UserFrom
from core.app.file_access import get_current_file_access_scope
from graphon.variables.input_entities import VariableEntity, VariableEntityType
from models import Account


def variable(kind, **kwargs):
    return VariableEntity(variable="value", label="Value", type=kind, **kwargs)


def check(inputs, variables):
    module = import_module("services.workbench_input_service")
    user = Account(name="User", email="user@example.test")
    user.id = "actor"
    return module.require_workbench_inputs(tenant_id="tenant", user=user, inputs=inputs, variables=variables)


@pytest.mark.parametrize("value", [0, "0", "12.5"])
def test_native_numeric_conversion_is_accepted_without_rewriting_snapshot(value):
    inputs = {"value": value}
    assert check(inputs, [variable(VariableEntityType.NUMBER, required=True)]) is None
    assert inputs == {"value": value}


@pytest.mark.parametrize("inputs", [{}, {"value": "not-a-number"}, {"value": []}])
def test_missing_or_invalid_native_number_has_opaque_error(inputs):
    with pytest.raises(ValueError, match="^Workbench inputs unavailable$"):
        check(inputs, [variable(VariableEntityType.NUMBER, required=True)])


def test_native_option_membership_is_preserved():
    selected = variable(VariableEntityType.SELECT, required=True, options=["A", "B"])
    assert check({"value": "A"}, [selected]) is None
    with pytest.raises(ValueError):
        check({"value": "C"}, [selected])


def test_file_input_uses_fresh_native_account_scope_and_restores_it():
    before = get_current_file_access_scope()
    mapping = {"transfer_method": "local_file", "upload_file_id": "selected"}

    def build(**kwargs):
        scope = kwargs["access_controller"].current_scope()
        assert scope.tenant_id == "tenant"
        assert scope.user_id == "actor"
        assert scope.user_from == UserFrom.ACCOUNT
        assert scope.invoke_from == InvokeFrom.EXPLORE
        assert scope.granted_upload_file_ids == frozenset()
        assert kwargs["mapping"] == mapping
        raise ValueError("private file metadata")

    with patch("core.app.apps.base_app_generator.file_factory.build_from_mapping", side_effect=build) as resolve:
        with pytest.raises(ValueError, match="^Workbench inputs unavailable$"):
            check({"value": mapping}, [variable(VariableEntityType.FILE, required=True)])
    resolve.assert_called_once()
    assert get_current_file_access_scope() is before


def test_native_batch_dropped_files_are_not_reported_as_fully_checked():
    with patch("core.app.apps.base_app_generator.file_factory.build_from_mappings", return_value=[]):
        with pytest.raises(ValueError, match="^Workbench inputs unavailable$"):
            check({"value": [{}]}, [variable(VariableEntityType.FILE_LIST, required=True)])


def test_optional_default_is_checked_without_filling_original_inputs():
    inputs = {}
    assert check(inputs, [variable(VariableEntityType.NUMBER, required=False, default="0")]) is None
    assert inputs == {}


def test_file_list_max_length_fails_before_file_resolution():
    with patch("core.app.apps.base_app_generator.file_factory.build_from_mappings") as resolve:
        with pytest.raises(ValueError, match="^Workbench inputs unavailable$"):
            check({"value": [{}, {}]}, [variable(VariableEntityType.FILE_LIST, required=True, max_length=1)])
    resolve.assert_not_called()


def test_optional_empty_file_placeholder_remains_valid():
    inputs = {"value": ""}
    with patch("core.app.apps.base_app_generator.file_factory.build_from_mapping") as resolve:
        assert check(inputs, [variable(VariableEntityType.FILE, required=False)]) is None
    resolve.assert_not_called()
    assert inputs == {"value": ""}
