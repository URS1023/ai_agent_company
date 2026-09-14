import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_source_contracts import db_draft
from test_source_service import principal, service

from enterprise_platform.application.errors import AccessDenied, PersistenceError
from enterprise_platform.application.source_contracts import SourceDraft
from enterprise_platform.persistence.source_models import SourceBase, SourceHeadRow
from enterprise_platform.persistence.sources import require_enabled_source, source_version_row


@pytest.mark.parametrize("state", ["enabled", "disabled", "tampered", "missing", "other_workspace"])
def test_transaction_source_gate_authenticates_current_persisted_head(state):
    app, repository, _, cipher = service()
    view = app.create_source(
        principal(), SourceDraft.model_validate({**db_draft(), "enabled": state != "disabled"}), request_key="r"
    )
    engine = create_engine("sqlite://")
    SourceBase.metadata.create_all(engine)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                SourceHeadRow(
                    workspace_id=view.workspace_id,
                    source_id=view.source_id,
                    read_id=view.read_id,
                    revision=1,
                    request_key="r",
                )
            )
            session.flush()
            row = source_version_row(repository.versions[0])
            if state == "tampered":
                row.public_json = view.model_copy(update={"enabled": False}).model_dump_json()
            session.add(row)
            session.flush()
            workspace = "other" if state == "other_workspace" else view.workspace_id
            source_id = "missing" if state == "missing" else view.source_id
            if state == "enabled":
                require_enabled_source(session, cipher, workspace, source_id)
            else:
                error = PersistenceError if state == "tampered" else AccessDenied
                with pytest.raises(error):
                    require_enabled_source(session, cipher, workspace, source_id)
    finally:
        engine.dispose()
