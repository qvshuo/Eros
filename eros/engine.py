from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from .catalog import CatalogRules
from .http import Client, WebClient
from .logging import report_progress
from .models import (
    Category,
    Classification,
    Evidence,
    Metadata,
    ScrapeRequest,
    ScrapeResult,
    SourceId,
    SourceResult,
    Status,
)
from .scanner import validate_code
from .settings import Settings
from .sources import SOURCES

FIELDS = (
    "title",
    "original_title",
    "plot",
    "release_date",
    "runtime_minutes",
    "studio",
    "publisher",
    "label",
    "series",
    "directors",
    "actors",
    "tags",
    "genres",
    "artwork",
)


def title_identity(value: str, code: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace(code.casefold(), "")
    return re.sub(r"[\W_]+", "", value)


class Scraper:
    def __init__(self, settings: Settings | None = None, *, client: Client | None = None):
        self.settings = settings or Settings()
        self.rules = CatalogRules.load(self.settings.catalog_rules_file)
        self.client = client or WebClient(self.settings)
        self._owns_client = client is None
        self.sources = {
            source: factory(self.client, self.settings, self.rules) for source, factory in SOURCES.items()
        }
        self._inflight: dict[str, asyncio.Task[SourceResult]] = {}

    async def __aenter__(self) -> Scraper:
        if self._owns_client:
            await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_client:
            await self.client.__aexit__(*exc)

    def classify(self, code: str) -> Classification:
        return self.rules.classify(validate_code(code))

    async def _fetch(self, source: SourceId, request: ScrapeRequest) -> SourceResult:
        key = f"{source}:" + request.model_dump_json(exclude={"enrich", "category"})
        if key not in self._inflight:
            task = asyncio.create_task(self.sources[source].fetch(request))
            self._inflight[key] = task

            def done(completed):
                if self._inflight.get(key) is completed:
                    self._inflight.pop(key, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(done)
        return (await asyncio.shield(self._inflight[key])).model_copy(deep=True)

    async def scrape(self, request: ScrapeRequest | str) -> ScrapeResult:
        request = ScrapeRequest(code=request) if isinstance(request, str) else request.model_copy(deep=True)
        validate_code(request.code)
        if (request.external_id or request.source_url) and not request.source:
            raise ValueError("explicit movie ID or URL requires a source")
        classification = self.rules.classify(request.code)
        if request.category:
            if classification.category == Category.FC2 and request.category != Category.FC2:
                raise ValueError("category contradicts FC2 number")
            classification.category = request.category
            classification.evidence.append(Evidence(basis="user_category", value=request.category))
        route = [request.source] if request.source else self.settings.routes[classification.category.value]
        results, fields, warnings = [], {}, []
        metadata: Metadata | None = None
        for source in route:
            report_progress("source", source=source)
            result = await self._fetch(source, request)
            report_progress("source", result.status.value, source)
            results.append(result)
            if result.status == Status.AMBIGUOUS and metadata is None:
                return ScrapeResult(
                    code=request.code,
                    status=Status.AMBIGUOUS,
                    classification=classification,
                    results=results,
                    warnings=["select a source movie ID or URL"],
                )
            if result.status != Status.FOUND or result.metadata is None:
                continue
            incoming = result.metadata
            warnings.extend(result.warnings)
            if metadata is None:
                metadata = incoming.model_copy(deep=True, update={"code": request.code})
                fields = {name: source for name in FIELDS if getattr(metadata, name)}
            elif (
                incoming.original_title
                and metadata.original_title
                and title_identity(incoming.original_title, incoming.code)
                != title_identity(metadata.original_title, metadata.code)
            ):
                warnings.append(f"{source}: original title differs; enrichment skipped")
                continue
            else:
                for name in FIELDS:
                    release = (
                        name == "release_date"
                        and metadata.release_kind != "release"
                        and incoming.release_kind == "release"
                    )
                    if (not getattr(metadata, name) or release) and getattr(incoming, name):
                        setattr(metadata, name, getattr(incoming, name))
                        fields[name] = source
                        if name == "release_date":
                            metadata.release_kind = incoming.release_kind
            if incoming.classification.category == Category.FC2:
                classification.category = Category.FC2
            elif not request.category and any(
                e.basis == "catalog_type" for e in incoming.classification.evidence
            ):
                classification.category = incoming.classification.category
            classification.evidence.extend(
                e for e in incoming.classification.evidence if e not in classification.evidence
            )
            report_progress(
                "metadata_found",
                "found",
                source,
                message="已解析作品信息",
                metadata=metadata.model_dump(mode="json"),
            )
            if not request.enrich or all(
                (
                    metadata.plot,
                    metadata.actors,
                    metadata.artwork,
                    metadata.release_date,
                    metadata.release_kind != "delivery",
                    metadata.runtime_minutes,
                )
            ):
                break
        if metadata:
            metadata.classification = classification
        failures = [r for r in results if r.status not in (Status.NOT_FOUND, Status.UNSUPPORTED)]
        status = Status.FOUND if metadata else failures[0].status if failures else Status.NOT_FOUND
        return ScrapeResult(
            code=request.code,
            status=status,
            classification=classification,
            metadata=metadata,
            field_sources=fields,
            results=results,
            warnings=warnings,
        )
