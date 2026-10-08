from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import SourceId

SOURCE_INTERVALS = {
    SourceId.DMM: 5,
    SourceId.JAVDB: 60,
    SourceId.JAVBUS: 15,
    SourceId.FC2: 10,
    SourceId.AVSOX: 30,
}


def seconds(value: float) -> float:
    if value < 0 or (value != 1 and value % 5 != 0):
        raise ValueError("seconds must be 1 or a multiple of 5 (0 disables an interval)")
    return value


class SiteSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None
    cookie: str = Field(default="", repr=False)
    interval_seconds: float | None = Field(default=None, ge=0)

    @field_validator("interval_seconds")
    @classmethod
    def interval(cls, value: float | None) -> float | None:
        return seconds(value) if value is not None else None

    @field_validator("cookie")
    @classmethod
    def validate_cookie(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value or value.lower().startswith("cookie:"):
            raise ValueError("paste only the single-line Cookie value: name=value; name2=value2")
        seen = set()
        for part in value.split(";"):
            if not part.strip():
                continue
            name, separator, _ = part.strip().partition("=")
            if not separator or not re.fullmatch(r"[^\s=;]+", name) or name in seen:
                raise ValueError("cookie must contain unique name=value pairs")
            if name.lower() in {"path", "domain", "expires", "max-age", "samesite", "secure", "httponly"}:
                raise ValueError("paste the request Cookie value, not Set-Cookie")
            seen.add(name)
        return value

    def cookie_values(self) -> dict[str, str]:
        return dict(part.strip().split("=", 1) for part in self.cookie.split(";") if part.strip())


class ApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str = "https://jdforrepam.com"
    app_version: str = "1.9.28"
    app_version_number: str = "10928"
    signature_prefix: str = Field(
        default="71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa",
        repr=False,
    )
    signature_version: str = "lpw6vgqzsp"


class TranslationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    endpoint: str = "https://api.openai.com/v1"
    model: str = ""
    api_key: str = Field(default="", repr=False)
    timeout_seconds: float = Field(default=30, gt=0)
    interval_seconds: float = Field(default=1, ge=0)
    _seconds = field_validator("timeout_seconds", "interval_seconds")(seconds)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flaresolverr_url: str | None = "http://flaresolverr:8191/v1"
    timeout_seconds: float = Field(default=25, gt=0)
    retry_delay_seconds: int = Field(default=5, ge=1)
    retries: int = Field(default=1, ge=0, le=3)
    sites: dict[SourceId, SiteSettings] = Field(default_factory=dict)
    javdb_api: ClassVar[ApiSettings] = ApiSettings()
    rules_file: Path | None = Field(default=None, exclude=True)
    actor_names_file: Path | None = Field(default=None, exclude=True)
    actor_images: bool = True
    media_roots: list[Path] = Field(default_factory=list)
    metadata_root: Path | None = Path("/data/metadata")
    watch_interval_seconds: int = Field(default=30, ge=5)
    video_extensions: ClassVar[tuple[str, ...]] = (
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".m4v",
        ".ts",
        ".strm",
    )
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    item_retries: int = Field(default=1, ge=0, le=3)
    routes: dict[str, list[SourceId]] = Field(
        default_factory=lambda: {
            "censored": [SourceId.DMM, SourceId.JAVDB, SourceId.JAVBUS],
            "uncensored": [SourceId.JAVDB, SourceId.JAVBUS, SourceId.AVSOX],
            "fc2": [SourceId.FC2, SourceId.JAVDB],
            "unknown": [SourceId.JAVDB, SourceId.JAVBUS, SourceId.DMM, SourceId.AVSOX],
        }
    )
    _seconds = field_validator("timeout_seconds", "retry_delay_seconds", "watch_interval_seconds")(seconds)

    @field_validator("flaresolverr_url")
    @classmethod
    def solver_address(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        if not value:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("FlareSolverr requires an HTTP or HTTPS address")
        return value

    @model_validator(mode="after")
    def validate_routes(self) -> Settings:
        if any(site != SourceId.JAVDB and config.cookie for site, config in self.sites.items()):
            raise ValueError("only JavDB supports a configured Cookie")
        for name in ("censored", "uncensored", "fc2", "unknown"):
            route = self.routes.get(name, [])
            if not route or len(set(route)) != len(route):
                raise ValueError(f"route {name} must be nonempty without duplicate sources")
            if (
                SourceId.JAVDB in route
                and SourceId.JAVBUS in route
                and route.index(SourceId.JAVDB) > route.index(SourceId.JAVBUS)
            ):
                raise ValueError("JavDB must precede JavBus")
        return self

    def source_interval(self, source: SourceId) -> float:
        configured = self.sites.get(source)
        return (
            configured.interval_seconds
            if configured and configured.interval_seconds is not None
            else SOURCE_INTERVALS[source]
        )

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        if path is None:
            return cls()
        return cls.model_validate_json(path.read_bytes())
