from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class SourceId(StrEnum):
    DMM = "dmm"
    JAVDB = "javdb"
    JAVBUS = "javbus"
    FC2 = "fc2"
    AVSOX = "avsox"


class Category(StrEnum):
    CENSORED = "censored"
    UNCENSORED = "uncensored"
    FC2 = "fc2"
    UNKNOWN = "unknown"


class Status(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    LOGIN_REQUIRED = "login_required"
    REGION_BLOCKED = "region_blocked"
    IP_BLOCKED = "ip_blocked"
    BLOCKED = "blocked"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNSUPPORTED = "unsupported"


class Evidence(BaseModel):
    basis: str
    value: str
    source: SourceId | None = None


class Classification(BaseModel):
    category: Category = Category.UNKNOWN
    evidence: list[Evidence] = Field(default_factory=list)


class Actor(BaseModel):
    name: str
    gender: Literal["female", "male", "unknown"] = "unknown"
    source_id: str | None = None


class Artwork(BaseModel):
    url: str
    role: Literal["cover", "poster", "sample"]
    source: SourceId
    referer: str


class Metadata(BaseModel):
    number: str
    title: str
    original_title: str | None = None
    plot: str | None = None
    release_date: date | None = None
    release_kind: Literal["release", "delivery", "unknown"] = "unknown"
    runtime_minutes: int | None = Field(default=None, ge=0)
    studio: str | None = None
    publisher: str | None = None
    label: str | None = None
    series: str | None = None
    directors: list[str] = Field(default_factory=list)
    actors: list[Actor] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    genres: list[str] | None = None
    artwork: list[Artwork] = Field(default_factory=list)
    classification: Classification = Field(default_factory=Classification)
    external_id: str | None = None
    source_url: str


class Candidate(BaseModel):
    number: str
    url: str
    title: str | None = None
    external_id: str | None = None


class RawDocument(BaseModel):
    url: str
    status: int
    transport: Literal["scrapling", "flaresolverr"]
    content: str = Field(repr=False)


class SourceResult(BaseModel):
    schema_version: int = 1
    source: SourceId
    requested_number: str
    status: Status
    metadata: Metadata | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    raw: list[RawDocument] = Field(default_factory=list, repr=False)
    source_data: JsonValue = Field(default=None, repr=False)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: str
    source: SourceId | None = None
    source_url: str | None = None
    external_id: str | None = None
    category: Category | None = None
    enrich: bool = True


class ScrapeResult(BaseModel):
    number: str
    status: Status
    classification: Classification
    metadata: Metadata | None = None
    field_sources: dict[str, SourceId] = Field(default_factory=dict)
    results: list[SourceResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
