from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from .config import Settings
from .logging_setup import report_progress
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
from .numbers import NumberRules
from .scanner import validate_key
from .sources import SOURCES
from .transport import Client, WebClient

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


def title_key(value: str, number: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace(number.casefold(), "")
    return re.sub(r"[\W_]+", "", value)


class Scraper:
    def __init__(self, settings: Settings | None = None, *, client: Client | None = None):
        self.settings = settings or Settings()
        self.rules = NumberRules.load(self.settings.rules_file)
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

    def classify(self, number: str) -> Classification:
        return self.rules.classify(validate_key(number))

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
        request = ScrapeRequest(number=request) if isinstance(request, str) else request.model_copy(deep=True)
        validate_key(request.number)
        if (request.external_id or request.source_url) and not request.source:
            raise ValueError("explicit movie ID or URL requires a source")
        classification = self.rules.classify(request.number)
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
                    number=request.number,
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
                metadata = incoming.model_copy(deep=True, update={"number": request.number})
                fields = {name: source for name in FIELDS if getattr(metadata, name)}
            elif (
                incoming.original_title
                and metadata.original_title
                and title_key(incoming.original_title, incoming.number)
                != title_key(metadata.original_title, metadata.number)
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
            number=request.number,
            status=status,
            classification=classification,
            metadata=metadata,
            field_sources=fields,
            results=results,
            warnings=warnings,
        )
