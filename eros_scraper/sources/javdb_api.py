from __future__ import annotations

import hashlib
import re
import time
import uuid
from urllib.parse import urlencode

from ..models import (
    Actor,
    Candidate,
    Category,
    Classification,
    Evidence,
    Metadata,
    RawDocument,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..numbers import normalize_number
from ..parsing import artwork, minutes, parse_date
from ..transport import FetchError
from .base import Source


class JavDBApi(Source):
    id = SourceId.JAVDB
    default_url = "https://jdforrepam.com"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.base = self.settings.javdb_api.base_url.rstrip("/")
        self.device_uuid = str(uuid.uuid4())

    async def api(self, path: str, raw: list[RawDocument], **query: str | int) -> dict:
        config = self.settings.javdb_api
        stamp = str(int(time.time()))
        signature = hashlib.md5((stamp + config.signature_prefix).encode(), usedforsecurity=False).hexdigest()
        params = {
            "app_channel": "official",
            "app_version": config.app_version,
            "app_version_number": config.app_version_number,
            "platform": "android",
            "system_version": "13",
            "device_model": "Pixel 6",
            "device_name": "Pixel",
            "device_uuid": self.device_uuid,
            **query,
        }
        page = await self.client.get(
            self.id,
            self.base + path + "?" + urlencode(params),
            headers={
                "jdsignature": f"{stamp}.{config.signature_version}.{signature}",
                "User-Agent": "Dart/3.4 (dart:io)",
                "Accept": "application/json",
                "Accept-Language": "zh-TW",
            },
        )
        raw.append(page.snapshot())
        payload = page.json()
        if not isinstance(payload, dict):
            raise FetchError(Status.PARSE_ERROR, "JavDB API envelope changed")
        if payload.get("success") not in (1, True):
            raise FetchError(Status.BLOCKED, "JavDB API rejected the signed request")
        if not isinstance(payload.get("data"), dict):
            raise FetchError(Status.PARSE_ERROR, "JavDB API returned no data object")
        return payload["data"]

    async def _fetch(self, request: ScrapeRequest, raw: list[RawDocument]) -> SourceResult:
        movie_id = request.external_id
        if request.source_url:
            self.checked_url(request.source_url)
            match = re.search(r"/api/v4/movies/([A-Za-z0-9]+)(?:$|\?)", request.source_url)
            if not match:
                raise FetchError(Status.UNSUPPORTED, "expected a JavDB API movie URL")
            movie_id = movie_id or match[1]
        if not movie_id:
            data = await self.api(
                "/api/v2/search", raw, q=self.rules.query_number(request.number), page=1, limit=100
            )
            movies = data.get("movies")
            if not isinstance(movies, list):
                raise FetchError(Status.PARSE_ERROR, "JavDB API search schema changed")
            candidates = [
                Candidate(
                    number=item["number"],
                    title=item.get("origin_title") or item.get("title"),
                    external_id=str(item["id"]),
                    url=self.base + f"/api/v4/movies/{item['id']}",
                )
                for item in movies
                if isinstance(item, dict) and item.get("id") and item.get("number")
            ]
            selected = self.select(request, candidates)
            if isinstance(selected, SourceResult):
                return selected
            if not selected:
                if len(movies) >= 100:
                    raise FetchError(Status.PARSE_ERROR, "search truncated; supply movie ID to disambiguate")
                return self.missed(request)
            movie_id = selected.external_id
        if not movie_id or not re.fullmatch(r"[A-Za-z0-9]{1,40}", movie_id):
            raise FetchError(Status.UNSUPPORTED, "invalid JavDB movie ID")
        data = await self.api(f"/api/v4/movies/{movie_id}", raw)
        movie = data.get("movie")
        if not isinstance(movie, dict) or not movie.get("number") or not movie.get("title"):
            raise FetchError(Status.PARSE_ERROR, "JavDB API detail schema changed")
        if str(movie.get("id")) != movie_id:
            raise FetchError(Status.IDENTITY_MISMATCH, "JavDB API returned a different movie ID")
        number = normalize_number(movie["number"])
        category = movie.get("type")
        classification = self.rules.classify(number)
        if category in (0, 1, 3, "0", "1", "3"):
            category = int(category)
            classification = Classification(
                category={0: Category.CENSORED, 1: Category.UNCENSORED, 3: Category.FC2}[category],
                evidence=[Evidence(basis="catalog_type", value=str(category), source=self.id)],
            )
        if number.startswith("FC2PPV-") and classification.category != Category.FC2:
            raise FetchError(Status.PARSE_ERROR, "API type contradicts FC2 number family")
        title = movie.get("origin_title") or movie["title"]
        source_url = self.base + f"/api/v4/movies/{movie_id}"
        actors = [
            Actor(
                name=item["name"],
                gender={0: "female", 1: "male"}.get(item.get("gender"), "unknown"),
                source_id=str(item["id"]) if item.get("id") else None,
            )
            for item in movie.get("actors") or []
            if isinstance(item, dict) and item.get("name")
        ]
        metadata = Metadata(
            number=number,
            title=title,
            original_title=movie.get("origin_title") or None,
            # Live App summaries sometimes describe a different movie despite a
            # correct ID and origin_title. Preserve the value in source_data only.
            plot=None,
            release_date=parse_date(movie.get("release_date")),
            release_kind="release",
            runtime_minutes=minutes(movie.get("duration")),
            studio=movie.get("maker_name") or None,
            publisher=movie.get("publisher_name") or None,
            series=movie.get("series_name") or None,
            directors=[movie["director_name"]] if movie.get("director_name") else [],
            actors=actors,
            tags=[
                item["name"]
                for item in movie.get("tags") or []
                if isinstance(item, dict) and item.get("name")
            ],
            artwork=artwork(
                self.id,
                self.base + "/",
                [movie["cover_url"]] if movie.get("cover_url") else [],
                [item["large_url"] for item in movie.get("preview_images") or [] if item.get("large_url")],
                [movie["thumb_url"]] if movie.get("thumb_url") else [],
            ),
            classification=classification,
            external_id=movie_id,
            source_url=source_url,
        )
        warnings = (
            [
                "App summary retained in source_data; excluded from plot because live samples contain misassociated translations"
            ]
            if movie.get("summary")
            else []
        )
        return self.found(request, metadata, source_data=movie, warnings=warnings)
