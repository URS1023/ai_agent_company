import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from test_business_service import binding
from test_schedule_service import fixture as service_fixture

from enterprise_platform.adapters.schedule_authority import FileScheduleAuthority
from enterprise_platform.application.errors import AccessDenied, DependencyUnavailable


def fixture(tmp_path):
    service, _, _, authority, _ = service_fixture()
    grant = authority.get_grant.return_value
    path = tmp_path / "grants.json"
    path.write_text(json.dumps({"grants": [grant.model_dump(mode="json")]}))
    probe = AsyncMock(return_value=False)
    source = FileScheduleAuthority(path, probe)
    return source, path, probe, grant, service


def test_grants_are_exactly_scoped_and_reloaded_after_revocation(tmp_path) -> None:
    source, path, probe, grant, _ = fixture(tmp_path)
    assert source.get_grant("w", "service") == grant
    assert source.get_grant("other", "service") is None
    assert source.get_grant("w", "other") is None
    path.write_text('{"grants": []}')
    assert source.get_grant("w", "service") is None
    probe.assert_not_awaited()


def test_duplicate_actor_scope_is_rejected_not_first_match_wins(tmp_path) -> None:
    source, path, _, grant, _ = fixture(tmp_path)
    path.write_text(json.dumps({"grants": [grant.model_dump(mode="json")] * 2}))
    with pytest.raises(DependencyUnavailable):
        source.get_grant("w", "service")


@pytest.mark.parametrize(
    "content", ["broken", '{"grants": [], "unknown": 1}', "x" * (1024 * 1024 + 1)], ids=["json", "schema", "size"]
)
def test_bad_grant_document_has_opaque_failure_without_cached_grant(tmp_path, content) -> None:
    source, path, _, _, _ = fixture(tmp_path)
    assert source.get_grant("w", "service") is not None
    path.write_text(content)
    with pytest.raises(DependencyUnavailable) as error:
        source.get_grant("w", "service")
    assert str(error.value) == "schedule_grant_source_unavailable"


def test_non_boolean_grant_enable_is_rejected(tmp_path) -> None:
    source, path, _, grant, _ = fixture(tmp_path)
    path.write_text(json.dumps({"grants": [grant.model_dump(mode="json") | {"enabled": "true"}]}))
    with pytest.raises(DependencyUnavailable):
        source.get_grant("w", "service")


def test_native_probe_is_called_from_thread_without_false_fallback(tmp_path) -> None:
    source, _, probe, _, _ = fixture(tmp_path)
    value = binding()
    assert asyncio.run(asyncio.to_thread(source.has_native_timer, value)) is False
    probe.assert_awaited_once_with(value)


def test_unknown_native_probe_result_is_not_treated_as_false(tmp_path) -> None:
    source, _, probe, _, _ = fixture(tmp_path)
    probe.return_value = None
    with pytest.raises(DependencyUnavailable):
        source.has_native_timer(binding())


def test_service_rejects_removed_file_grant_before_native_probe(tmp_path) -> None:
    source, path, probe, _, service = fixture(tmp_path)
    service._authority = source
    path.write_text('{"grants": []}')
    with pytest.raises(AccessDenied):
        service._authorize_service(binding(), "service")
    probe.assert_not_awaited()


def test_service_rejects_grant_removed_while_native_inspection_is_in_flight(tmp_path) -> None:
    source, path, probe, _, service = fixture(tmp_path)
    service._authority = source

    async def revoke_then_respond(value):
        path.write_text('{"grants": []}')
        return False

    probe.side_effect = revoke_then_respond
    with pytest.raises(AccessDenied):
        service._authorize_service(binding(), "service")


def test_authority_rejects_accidental_event_loop_blocking_call(tmp_path) -> None:
    source, _, probe, _, _ = fixture(tmp_path)

    async def scenario():
        with pytest.raises(DependencyUnavailable):
            source.has_native_timer(binding())

    asyncio.run(scenario())
    probe.assert_not_awaited()


def test_expiry_during_native_inspection_is_rechecked_before_success(tmp_path) -> None:
    source, _, _, grant, service = fixture(tmp_path)
    service._authority = source
    times = iter([grant.expires_at - timedelta(seconds=1), grant.expires_at])
    service._clock = lambda: next(times)
    with pytest.raises(AccessDenied):
        service._authorize_service(binding(), "service")
