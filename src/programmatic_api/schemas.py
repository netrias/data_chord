"""External request and response models for the versioned API."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_COLUMNS = 500
_MAX_TOTAL_VALUES = 25_000
_MAX_VALUES_PER_COLUMN = 5000
_MAX_VALUE_CHARACTERS = 4096
_MAX_REQUEST_CHARACTERS = 1_000_000

SampleValue = Annotated[str, Field(max_length=_MAX_VALUE_CHARACTERS)]
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


__all__ = [
    "HarmonizationKind",
    "RecommendationColumnRequest",
    "RecommendationColumnResponse",
    "RecommendationMatchResponse",
    "RecommendationRequest",
    "RecommendationResponse",
]
