"""Immutable original PPT template definitions; content generation cannot change their design."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from .office_content import OfficeContent

Text = Annotated[str, StringConstraints(min_length=1, max_length=512)]
Color = Annotated[str, StringConstraints(pattern=r"^[0-9A-F]{6}$")]
PreviewColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-F]{6}$")]


class TemplateTheme(OfficeContent):
    background: Color
    surface: Color
    accent: Color
    accent2: Color
    text: Color
    muted: Color
    line: Color


class TemplateTypography(OfficeContent):
    title_font: Text
    body_font: Text
    title_size: int = Field(ge=1, le=144)
    body_size: int = Field(ge=1, le=144)


class TemplatePreview(OfficeContent):
    background: PreviewColor
    accent: PreviewColor
    text: PreviewColor
    layout: Text


class TemplateDesign(OfficeContent):
    cover: Text
    chrome: Text
    background: Text
    cards: Text
    metrics: Text
    comparison: Text
    timeline: Text


class OfficeTemplate(OfficeContent):
    id: Text
    name: Text
    category: Text
    description: Text
    recommended_for: tuple[Text, ...]
    layout_family: Literal["premium"]
    style_profile: Text
    density: Text
    layouts: tuple[Text, ...]
    preview: TemplatePreview
    typography: TemplateTypography
    theme: TemplateTheme
    design: TemplateDesign

    @model_validator(mode="after")
    def consistent_identity(self) -> Self:
        if self.id != self.style_profile or len(set(self.layouts)) != len(self.layouts):
            raise ValueError("Template identity or layouts mismatch")
        return self
