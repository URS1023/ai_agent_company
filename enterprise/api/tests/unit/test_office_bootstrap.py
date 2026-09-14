"""Production assembly routes with persistence and native identity explicitly isolated."""

from io import BytesIO
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from test_bootstrap import settings
from test_office_export import setup
from test_source_encryption import keyring

from enterprise_platform.application.office_directory import OfficeCandidates
from enterprise_platform.bootstrap import Settings, create_runtime
from enterprise_platform.persistence.office_creation import SqlAlchemyOfficeFileCreator
from enterprise_platform.persistence.office_directory import SqlAlchemyOfficeDirectory
from enterprise_platform.persistence.office_repository import SqlAlchemyOfficeEditRepository


def test_configured_runtime_wires_office_list_read_and_real_docx_export():
    _, principal, _, policy, _, record = setup()
    config = Settings.model_validate({**settings().model_dump(), "source_keyring": keyring()})
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No database in composition test")),
        patch(
            "enterprise_platform.adapters.dify_identity.DifyIdentityClient.resolve",
            new=AsyncMock(return_value=principal),
        ),
        patch.object(SqlAlchemyOfficeEditRepository, "authorize", return_value=policy.authorize.return_value),
        patch.object(SqlAlchemyOfficeEditRepository, "get", return_value=record),
        patch.object(SqlAlchemyOfficeFileCreator, "create", return_value=record) as creator,
        patch.object(
            SqlAlchemyOfficeDirectory, "candidates", return_value=OfficeCandidates((record.content.file_id,), False)
        ),
    ):
        runtime = create_runtime(config)
        try:
            with TestClient(runtime.app) as client:
                created = client.post(
                    "/enterprise/api/v1/office/files",
                    headers={"Origin": config.allowed_origins[0]},
                    json={
                        "expected_actor_id": principal.actor_id,
                        "expected_workspace_id": principal.workspace_id,
                        "file_id": str(record.content.file_id),
                        "kind": "document",
                        "template_id": record.template_id,
                        "template_revision": "1",
                        "units": [unit.model_dump(mode="json") for unit in record.content.units],
                    },
                )
                assert created.status_code == 201
                assert creator.call_args.args[1].workspace_id == principal.workspace_id
                path = f"/enterprise/api/v1/office/files/{record.content.file_id}"
                assert client.get("/enterprise/api/v1/office/files").status_code == 200
                assert client.get(path).status_code == 200
                response = client.get(f"{path}/document?expected_revision=1")
                assert response.status_code == 200
                with ZipFile(BytesIO(response.content)) as package:
                    assert package.testzip() is None
                    assert b"Quality report" in package.read("word/document.xml")
        finally:
            runtime.close()
