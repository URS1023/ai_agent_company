"""Exports use saved revisions and current permissions, not caller-supplied documents."""

from dataclasses import replace
from io import BytesIO
from unittest.mock import Mock
from uuid import UUID
from zipfile import ZipFile

import pytest

from enterprise_platform.adapters.office_docx import render_office_docx
from enterprise_platform.application.contracts import Principal
from enterprise_platform.application.errors import AccessDenied, Conflict, InvalidInput
from enterprise_platform.application.office_edits import OfficeEditService, OfficeFileRecord, OfficeGrant
from enterprise_platform.application.office_export import OfficeExportService
from enterprise_platform.domain.office_revision import OfficeRevision, OfficeText, OfficeUnit

FILE = UUID(int=1)


def setup():
    principal = Principal(actor_id="actor", workspace_id="workspace", workspace_role="normal", display_name="User")
    record = OfficeFileRecord(
        workspace_id="workspace",
        template_id="document-default",
        template_revision=1,
        source_snapshot_ids=(),
        content=OfficeRevision(
            file_id=FILE,
            revision=1,
            kind="document",
            units=(OfficeUnit(unit_id=UUID(int=2), kind="paragraph", content=(OfficeText(text="Quality report"),)),),
        ),
    )
    repository, policy = Mock(), Mock()
    repository.get.return_value = record
    policy.authorize.return_value = OfficeGrant("workspace", "actor", FILE, "read", 1)
    renderer = Mock(wraps=render_office_docx)
    service = OfficeExportService(OfficeEditService(repository, policy), renderer)
    return service, principal, repository, policy, renderer, record


def test_export_produces_real_docx_from_saved_record_and_rechecks_access():
    service, principal, repository, policy, renderer, record = setup()
    result = service.export_document(principal, FILE, expected_revision=1)
    with ZipFile(BytesIO(result.content)) as package:
        assert package.testzip() is None
        assert b"Quality report" in package.read("word/document.xml")
    assert result.filename == f"office-{FILE}-r1.docx"
    assert result.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert result.revision == 1
    renderer.assert_called_once_with(record)
    assert policy.authorize.call_count == 2
    assert repository.get.call_count == 2
    repository.commit.assert_not_called()


def test_denied_access_does_not_render():
    service, principal, repository, policy, renderer, _ = setup()
    policy.authorize.side_effect = AccessDenied()
    with pytest.raises(AccessDenied):
        service.export_document(principal, FILE, expected_revision=1)
    renderer.assert_not_called()
    repository.get.assert_not_called()


def test_stale_revision_does_not_render():
    service, principal, _, _, renderer, _ = setup()
    with pytest.raises(Conflict, match="office_revision_conflict"):
        service.export_document(principal, FILE, expected_revision=2)
    renderer.assert_not_called()


def test_permission_revoked_during_render_prevents_returning_bytes():
    service, principal, _, policy, renderer, _ = setup()
    policy.authorize.side_effect = [policy.authorize.return_value, AccessDenied()]
    with pytest.raises(AccessDenied):
        service.export_document(principal, FILE, expected_revision=1)
    renderer.assert_called_once()


@pytest.mark.parametrize("change", ["revision", "template", "sources", "text"])
def test_change_during_render_prevents_returning_stale_content(change):
    service, principal, repository, _, renderer, record = setup()
    if change == "revision":
        changed = replace(record, content=record.content.model_copy(update={"revision": 2}))
    elif change == "template":
        changed = replace(record, template_revision=2)
    elif change == "sources":
        changed = replace(record, source_snapshot_ids=("new-snapshot",))
    else:
        unit = record.content.units[0].model_copy(update={"content": (OfficeText(text="Changed"),)})
        changed = replace(record, content=record.content.model_copy(update={"units": (unit,)}))
    repository.get.side_effect = [record, changed]
    with pytest.raises(Conflict, match="office_revision_conflict"):
        service.export_document(principal, FILE, expected_revision=1)
    renderer.assert_called_once()


@pytest.mark.parametrize("revision", [True, 1.0, 0, -1, "1"])
def test_invalid_expected_revision_does_not_read_or_render(revision):
    service, principal, repository, policy, renderer, _ = setup()
    with pytest.raises(InvalidInput):
        service.export_document(principal, FILE, expected_revision=revision)
    policy.authorize.assert_not_called()
    repository.get.assert_not_called()
    renderer.assert_not_called()


def test_invalid_metadata_reaches_renderer_domain_validation():
    service, principal, repository, _, _, record = setup()
    repository.get.return_value = replace(record, source_snapshot_ids=("snapshot\ud800",))
    with pytest.raises(InvalidInput, match="office_document_text_invalid"):
        service.export_document(principal, FILE, expected_revision=1)


def test_presentation_is_rejected_before_document_renderer():
    service, principal, repository, _, renderer, record = setup()
    slide = record.content.units[0].model_copy(update={"kind": "slide"})
    repository.get.return_value = replace(
        record, content=record.content.model_copy(update={"kind": "presentation", "units": (slide,)})
    )
    with pytest.raises(InvalidInput, match="office_document_required"):
        service.export_document(principal, FILE, expected_revision=1)
    renderer.assert_not_called()
