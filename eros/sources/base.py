from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlparse

from pydantic import ValidationError

from ..catalog import CatalogRules
from ..http import Client, FetchError
from ..models import Candidate, Metadata, RawDocument, ScrapeRequest, SourceId, SourceResult, Status
from ..settings import Settings


class Source:
    id: ClassVar[SourceId]
    default_url: ClassVar[str]

    def __init__(self, client: Client, settings: Settings, rules: CatalogRules) -> None:
        self.client = client
        self.settings = settings
        self.rules = rules
        settings = settings.sites.get(self.id)
        self.base = (settings.base_url if settings and settings.base_url else self.default_url).rstrip("/")

    def checked_url(self, value: str) -> str:
        if urlparse(value).scheme != "https" or urlparse(value).netloc.lower().removesuffix(
            ":443"
        ) != urlparse(self.base).netloc.lower().removesuffix(":443"):
            raise FetchError(Status.UNSUPPORTED, f"URL must belong to the configured {self.id} origin")
        return value

    async def fetch(self, request: ScrapeRequest) -> SourceResult:
        raw: list[RawDocument] = []
        try:
            result = await self._fetch(request, raw)
            if result.metadata and not self.rules.matches(request.code, result.metadata.code):
                raise FetchError(Status.IDENTITY_MISMATCH, "detail number differs from requested number")
            result.raw = raw
            return result
        except FetchError as exc:
            if exc.page and (not raw or raw[-1].url != exc.page.url or raw[-1].content != exc.page.text):
                raw.append(exc.page.snapshot())
            return SourceResult(
                source=self.id, requested_code=request.code, status=exc.status, message=str(exc), raw=raw
            )
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            return SourceResult(
                source=self.id,
                requested_code=request.code,
                status=Status.PARSE_ERROR,
                message=f"source document changed ({type(exc).__name__})",
                raw=raw,
            )

    def found(self, request: ScrapeRequest, metadata: Metadata, **kwargs: object) -> SourceResult:
        return SourceResult(
            source=self.id, requested_code=request.code, status=Status.FOUND, metadata=metadata, **kwargs
        )

    def missed(self, request: ScrapeRequest) -> SourceResult:
        return SourceResult(source=self.id, requested_code=request.code, status=Status.NOT_FOUND)

    def select(self, request: ScrapeRequest, candidates: list[Candidate]) -> Candidate | SourceResult | None:
        unique = {
            candidate.url: candidate
            for candidate in candidates
            if self.rules.matches(request.code, candidate.code)
        }
        if len(unique) > 1:
            return SourceResult(
                source=self.id,
                requested_code=request.code,
                status=Status.AMBIGUOUS,
                candidates=list(unique.values()),
                message="multiple exact catalog candidates",
            )
        return next(iter(unique.values()), None)

    async def _fetch(self, request: ScrapeRequest, raw: list[RawDocument]) -> SourceResult:
        raise NotImplementedError
