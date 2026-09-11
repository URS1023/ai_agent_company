import asyncio
from threading import Event
from unittest.mock import patch

import pytest
from test_dashboard_refresh_service import principal
from test_dashboard_schema_resolver import setup as schema_setup
from test_dashboard_sql_trial import setup as trial_setup

from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.dashboard_sql_trial_executor import RegisteredSqlTrialExecutor
from enterprise_platform.application.errors import AccessDenied
from enterprise_platform.domain.data_sources import DataSourceError, ReadLimits, SqlRead


def setup():
    resolver, _, view, sources = schema_setup()
    parts = trial_setup()
    draft = parts[2].get.return_value.model_copy(update={"source": resolver._grants.get.return_value.source})
    parts[2].get.return_value = draft
    permit = parts[4].authorize.return_value.model_copy(
        update={"source": draft.source, "draft_hash": canonical_hash(draft.model_dump(mode="json"))}
    )
    parts[4].authorize.return_value = permit
    read = SqlRead(
        source=draft.source, read_id=draft.draft_id, revision=permit.draft_hash, sql=draft.proposals.slots[0].sql
    )
    executor = RegisteredSqlTrialExecutor(sources, resolver._reads, parts[2], parts[4])
    return executor, permit, read, parts[4]


def test_executes_only_saved_sql_with_budget_and_closes_reader():
    executor, permit, read, _ = setup()
    with patch("enterprise_platform.application.dashboard_sql_trial_executor.DatabaseSourceReader") as reader:
        result = asyncio.run(executor.execute(principal(), permit, read))
    assert result is reader.return_value.read.return_value
    config = reader.call_args.args[0]
    assert config.limits.max_rows <= permit.limits.max_rows
    assert config.limits.max_bytes <= permit.limits.max_bytes
    call = reader.return_value.read.call_args
    assert call.args == (read, {})
    assert call.kwargs["plan_budget"] == permit.plan_budget
    assert isinstance(call.kwargs["deadline"], float)
    reader.return_value.close.assert_called_once()


def test_changed_sql_never_constructs_a_reader():
    executor, permit, read, _ = setup()
    with patch("enterprise_platform.application.dashboard_sql_trial_executor.DatabaseSourceReader") as reader:
        with pytest.raises(AccessDenied):
            asyncio.run(executor.execute(principal(), permit, read.model_copy(update={"sql": "SELECT 1"})))
        reader.assert_not_called()


def test_revoked_grant_never_constructs_a_reader():
    executor, permit, read, authorizer = setup()
    authorizer.authorize.side_effect = AccessDenied()
    with patch("enterprise_platform.application.dashboard_sql_trial_executor.DatabaseSourceReader") as reader:
        with pytest.raises(AccessDenied):
            asyncio.run(executor.execute(principal(), permit, read))
        reader.assert_not_called()


def test_reader_failure_still_closes_connection_without_retry():
    executor, permit, read, _ = setup()
    with patch("enterprise_platform.application.dashboard_sql_trial_executor.DatabaseSourceReader") as reader:
        reader.return_value.read.side_effect = DataSourceError("timeout")
        with pytest.raises(DataSourceError):
            asyncio.run(executor.execute(principal(), permit, read))
        reader.return_value.read.assert_called_once()
        reader.return_value.close.assert_called_once()


def test_revoke_during_read_discards_result_and_closes_reader():
    executor, permit, read, authorizer = setup()
    with patch("enterprise_platform.application.dashboard_sql_trial_executor.DatabaseSourceReader") as reader:

        def execute(*args, **kwargs):
            authorizer.authorize.side_effect = AccessDenied()
            return reader.return_value

        reader.return_value.read.side_effect = execute
        with pytest.raises(AccessDenied):
            asyncio.run(executor.execute(principal(), permit, read))
        reader.return_value.close.assert_called_once()


def test_source_limits_cannot_be_relaxed_by_a_larger_trial_grant():
    executor, permit, read, _ = setup()
    view = executor._sources.get(principal().workspace_id, read.source.source_id).view
    entry = executor._reads.resolve(
        view.workspace_id, view.source_id, view.source_revision, view.read_id, view.read_revision
    )
    limits = ReadLimits(max_rows=1, max_bytes=16, timeout_seconds=0.1)
    entry = entry.model_copy(update={"connection": entry.connection.model_copy(update={"limits": limits})})
    with (
        patch.object(executor._reads, "resolve", return_value=entry),
        patch("enterprise_platform.application.dashboard_sql_trial_executor.DatabaseSourceReader") as reader,
    ):
        asyncio.run(executor.execute(principal(), permit, read))
    assert reader.call_args.args[0].limits == limits


def test_cross_actor_permit_fails_before_loading_source_credentials():
    executor, permit, read, _ = setup()
    with patch.object(executor._reads, "resolve") as resolve:
        with pytest.raises(AccessDenied):
            asyncio.run(executor.execute(principal().model_copy(update={"actor_id": "other"}), permit, read))
        resolve.assert_not_called()


def test_cancellation_does_not_skip_reader_cleanup_when_driver_returns():
    executor, permit, read, _ = setup()
    started, release, closed = Event(), Event(), Event()
    with patch("enterprise_platform.application.dashboard_sql_trial_executor.DatabaseSourceReader") as reader:

        def execute(*args, **kwargs):
            started.set()
            assert release.wait(3)
            return reader.return_value

        reader.return_value.read.side_effect = execute
        reader.return_value.close.side_effect = closed.set

        async def cancel():
            task = asyncio.create_task(executor.execute(principal(), permit, read))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            assert await asyncio.to_thread(closed.wait, 3)

        asyncio.run(cancel())
        reader.return_value.close.assert_called_once()
