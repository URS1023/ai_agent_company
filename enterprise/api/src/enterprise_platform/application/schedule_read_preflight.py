"""Check configured window mappings without querying the device data source."""

from .contracts import Binding
from .errors import InvalidInput
from .input_capture import RegisteredRead, RegisteredReadRegistry


def validate_schedule_read(binding: Binding, registry: RegisteredReadRegistry) -> None:
    binding = Binding.model_validate(binding.model_dump())
    window_keys = {"window_start", "window_end"}
    allowed = binding.manifest.get("input_keys", [])
    if (
        not isinstance(allowed, list)
        or any(not isinstance(key, str) for key in allowed)
        or not window_keys.issubset(allowed)
    ):
        raise InvalidInput("schedule_window_inputs_not_registered")
    entry = registry.resolve(
        binding.workspace_id, binding.source_id, binding.source_revision, binding.read_id, binding.read_revision
    )
    entry = RegisteredRead.model_validate(entry.model_dump())
    source = entry.read.source
    if (source.workspace_id, source.source_id, source.revision, entry.read.read_id, entry.read.revision) != (
        binding.workspace_id,
        binding.source_id,
        binding.source_revision,
        binding.read_id,
        binding.read_revision,
    ) or binding.device_id not in entry.device_ids:
        raise InvalidInput("schedule_read_scope_mismatch")
    # The interval configuration currently supplies only these two inputs. Extra
    # required read inputs would otherwise fail later in InputCaptureService._bind.
    if {item.input_key for item in entry.parameters} != window_keys or any(
        item.kind != "datetime" or item.nullable for item in entry.parameters
    ):
        raise InvalidInput("schedule_read_window_mapping_invalid")
