"""External request and response models for the versioned API."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from netrias_client import SUPPORTED_TABULAR_SUFFIXES
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_COLUMNS = 500
_MAX_TOTAL_VALUES = 25_000
_MAX_VALUES_PER_COLUMN = 5000
_MAX_VALUE_CHARACTERS = 4096
_MAX_REQUEST_CHARACTERS = 1_000_000

SampleValue = Annotated[str, Field(max_length=_MAX_VALUE_CHARACTERS)]
DocumentCell = Annotated[str, Field(max_length=1_000_000)]
DocumentRow = Annotated[list[DocumentCell], Field(max_length=_MAX_COLUMNS)]
HarmonizationKind = Literal["harmonizable", "no_permissible_values", "numeric"]


class RecommendationColumnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_name: str = Field(max_length=1024)
    values: list[SampleValue] = Field(max_length=_MAX_VALUES_PER_COLUMN)


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_model_key: str = Field(max_length=256)
    external_version_number: str = Field(max_length=256)
    columns: list[RecommendationColumnRequest] = Field(
        min_length=1,
        max_length=_MAX_COLUMNS,
    )
    top_k: int = Field(default=3, ge=1, le=10, strict=True)

    @field_validator("data_model_key", "external_version_number")
    @classmethod
    def _normalize_required_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("external_version_number")
    @classmethod
    def _require_concrete_version(cls, value: str) -> str:
        if value.lower() == "latest":
            raise ValueError("external_version_number must be concrete")
        return value

    @model_validator(mode="after")
    def _limit_total_sample_size(self) -> Self:
        value_count = sum(len(column.values) for column in self.columns)
        if value_count > _MAX_TOTAL_VALUES:
            raise ValueError("column samples exceed the request value limit")
        character_count = sum(
            len(column.column_name) + sum(len(value) for value in column.values)
            for column in self.columns
        )
        if character_count > _MAX_REQUEST_CHARACTERS:
            raise ValueError("column samples exceed the request character limit")
        return self


class RecommendationMatchResponse(BaseModel):
    target: str
    target_cde_id: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    harmonization: HarmonizationKind


class RecommendationColumnResponse(BaseModel):
    column_name: str
    matches: list[RecommendationMatchResponse]


class RecommendationResponse(BaseModel):
    target_schema: str
    results: list[RecommendationColumnResponse]


class HarmonizationAlternativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=1024)
    confidence: float = Field(ge=0.0, le=1.0)
    harmonization: HarmonizationKind
    cde_id: int | None = Field(default=None, strict=True)


class HarmonizationMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_name: str = Field(max_length=1024)
    cde_key: str = Field(min_length=1, max_length=1024)
    cde_id: int = Field(strict=True)
    harmonization: HarmonizationKind
    alternatives: list[HarmonizationAlternativeRequest] = Field(max_length=10)


class HarmonizationDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=1024)
    sheet_name: str | None = Field(default=None, alias="sheetName", max_length=31)
    header: list[Annotated[str, Field(max_length=1024)]] = Field(
        min_length=1,
        max_length=_MAX_COLUMNS,
    )
    rows: list[DocumentRow]

    @field_validator("name")
    @classmethod
    def _require_safe_supported_name(cls, value: str) -> str:
        if value != Path(value).name or "/" in value or "\\" in value:
            raise ValueError("document name must be a file name")
        if Path(value).suffix.lower() not in SUPPORTED_TABULAR_SUFFIXES:
            raise ValueError("document name must use a supported tabular suffix")
        return value

    @model_validator(mode="after")
    def _require_rectangular_document(self) -> Self:
        width = len(self.header)
        if any(len(row) != width for row in self.rows):
            raise ValueError("document rows must match the header width")
        is_xlsx = Path(self.name).suffix.lower() == ".xlsx"
        if is_xlsx and not self.sheet_name:
            raise ValueError("XLSX documents require sheetName")
        if not is_xlsx and self.sheet_name is not None:
            raise ValueError("sheetName is only valid for XLSX documents")
        return self


class HarmonizationSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0"] = Field(alias="schemaVersion")
    data_model_key: str = Field(alias="data_commons_key", max_length=256)
    external_version_number: str = Field(max_length=256)
    use_cache: bool = Field(default=True, strict=True)
    document: HarmonizationDocumentRequest
    column_mappings: list[HarmonizationMappingRequest | None] = Field(
        default_factory=list,
        max_length=_MAX_COLUMNS,
    )

    @field_validator("data_model_key", "external_version_number")
    @classmethod
    def _normalize_harmonization_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("external_version_number")
    @classmethod
    def _require_harmonization_concrete_version(cls, value: str) -> str:
        if value.lower() == "latest":
            raise ValueError("external_version_number must be concrete")
        return value

    @model_validator(mode="after")
    def _require_mapping_position_count(self) -> Self:
        if self.column_mappings and len(self.column_mappings) != len(self.document.header):
            raise ValueError("column_mappings must match the document width")
        return self


class HarmonizationSubmitResponse(BaseModel):
    job_id: str


class HarmonizationJobResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["QUEUED", "SUCCEEDED", "FAILED"]
    final_url: str | None = None
    manifest_url: str | None = None
    error_message: str | None = Field(default=None, alias="errorMessage")


__all__ = [
    "HarmonizationKind",
    "HarmonizationJobResponse",
    "HarmonizationSubmissionRequest",
    "HarmonizationSubmitResponse",
    "RecommendationColumnRequest",
    "RecommendationColumnResponse",
    "RecommendationMatchResponse",
    "RecommendationRequest",
    "RecommendationResponse",
]
