"""Load versioned, packaged legacy designs without executing legacy Python at runtime."""

from importlib.resources import files
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from enterprise_platform.application.contracts import canonical_hash
from enterprise_platform.application.errors import NotFound, PersistenceError
from enterprise_platform.domain.office_content import OfficeContent
from enterprise_platform.domain.office_templates import OfficeTemplate

_TEMPLATE_DIGEST = "e372960988f564cd7fa7b72b768df94912df3fc03acb2d2361fe65d84e799454"


class _BuiltinCatalog(OfficeContent):
    catalog_version: Literal["legacy-premium-v1"]
    source_member: Literal["the_company_agent-main/backend/app/modules/presentation/premium_catalog.py"]
    source_sha256: Literal["1c4f91170388c2d47724dbf4b8ab7f8306a9f100eef33bf368e7069bc0bdb68a"]
    templates: tuple[OfficeTemplate, ...] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def original_designs(self) -> Self:
        if len({item.id for item in self.templates}) != 20:
            raise ValueError("Duplicate template identity")
        if canonical_hash([item.model_dump(mode="json") for item in self.templates]) != _TEMPLATE_DIGEST:
            raise ValueError("Original template content changed without a new catalog revision")
        return self


def builtin_templates() -> tuple[OfficeTemplate, ...]:
    try:
        data = files("enterprise_platform").joinpath("resources/office_builtin_templates.json").read_bytes()
        if len(data) > 262144:
            raise ValueError("Template catalog exceeds limit")
        return _BuiltinCatalog.model_validate_json(data).templates
    except (OSError, ValueError, ValidationError):
        raise PersistenceError("office_template_catalog_invalid") from None


def get_builtin_template(template_id: str, revision: int) -> OfficeTemplate:
    if type(revision) is not int or revision != 1:
        raise NotFound("office_template_not_found")
    for item in builtin_templates():
        if item.id == template_id:
            return item
    raise NotFound("office_template_not_found")
