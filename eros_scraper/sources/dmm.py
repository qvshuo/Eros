from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, quote, urljoin, urlparse

from ..models import (
    Actor,
    Evidence,
    Metadata,
    RawDocument,
    ScrapeRequest,
    SourceId,
    SourceResult,
    Status,
)
from ..parsing import artwork, parse_date
from ..transport import FetchError
from .base import Source

QUERY = """query FetchDigitalContent($id: ID!) {
 ppvContent(id: $id) { id title description deliveryStartDate duration
 actresses { name } directors { name } series { name } maker { name } label { name }
 genres { name } packageImage { largeUrl mediumUrl } sampleImages { imageUrl largeImageUrl } }
}"""


def cid_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("video.dmm.co.jp", "www.dmm.co.jp"):
        return None
    query = parse_qs(parsed.query)
    match = re.search(r"(?:cid=|/id=)([a-z0-9_]+)", parsed.path, re.IGNORECASE)
    cid = query.get("id", query.get("cid", [None]))[0] or (match[1] if match else None)
    return cid.lower() if cid and re.fullmatch(r"[a-z0-9_]{3,80}", cid, re.IGNORECASE) else None


def cid_matches_number(cid: str, number: str, catalog_prefixes=()) -> bool:
    prefix, _, digits = number.rpartition("-")
    if not digits.isdigit():
        return False
    # A known numeric catalog prefix is not a manufacturer namespace.
    for known in catalog_prefixes:
        if re.fullmatch(r"\d+" + re.escape(prefix), known) and cid_matches_number(cid, known + "-" + digits):
            return False
    # Namespace prefixes and digital edition suffixes occur in official IDs.
    # Match the full catalog prefix; never shorten 300MIUM to MIUM.
    match = re.fullmatch(
        r"(?:[a-z]_\d+|\d+)?" + re.escape(prefix.lower()) + r"(\d+)(?:so|sp|tk|hd|hhb|hib)?", cid.lower()
    )
    return bool(match and int(match[1]) == int(digits))


class DMM(Source):
    id = SourceId.DMM
    default_url = "https://api.video.dmm.co.jp"

    async def content(self, cid: str, raw: list[RawDocument]) -> dict | None:
        page = await self.client.post_json(
            self.id,
            self.base + "/graphql",
            {
                "operationName": "FetchDigitalContent",
                "variables": {"id": cid},
                "query": QUERY,
            },
            headers={"Origin": "https://video.dmm.co.jp", "Referer": "https://video.dmm.co.jp/"},
        )
        raw.append(page.snapshot())
        document = page.json()
        if not isinstance(document, dict) or not isinstance(document.get("data"), dict):
            raise FetchError(Status.PARSE_ERROR, "DMM GraphQL returned no data")
        movie = document["data"].get("ppvContent")
        if movie is None and not document.get("errors"):
            return None
        if not isinstance(movie, dict) or not movie.get("title"):
            raise FetchError(Status.PARSE_ERROR, "DMM GraphQL content schema changed")
        if movie.get("id") != cid:
            raise FetchError(Status.IDENTITY_MISMATCH, "DMM returned a different CID")
        return movie

    async def search_cids(self, request: ScrapeRequest, raw: list[RawDocument]) -> list[str]:
        page = await self.client.get(
            self.id,
            "https://www.dmm.co.jp/search/=/searchstr=" + quote(request.number) + "/",
            cookies={"age_check_done": "1"},
        )
        raw.append(page.snapshot())
        selector = page.selector()
        urls = selector.css("a[href]::attr(href)").getall()
        # Modern DMM embeds detailUrl strings in its search-state scripts.
        for escaped in re.findall(r'"detailUrl"\s*:\s*("(?:\\.|[^"\\])*")', page.text):
            urls.append(json.loads(escaped))
        candidates = set()
        for value in urls:
            url = urljoin(page.url, value)
            parsed = urlparse(url)
            if parsed.hostname == "video.dmm.co.jp" or "/digital/" in parsed.path:
                if (cid := cid_from_url(url)) and cid_matches_number(
                    cid, request.number, [*self.rules.censored_prefixes, *self.rules.uncensored_prefixes]
                ):
                    candidates.add(cid)
        if not candidates and not selector.css('input[name="searchstr"], input[name="keyword"], #searchstr'):
            raise FetchError(Status.PARSE_ERROR, "DMM search contains no verified content links or search UI")
        return sorted(candidates)

    async def _fetch(self, request: ScrapeRequest, raw: list[RawDocument]) -> SourceResult:
        if request.number.startswith("FC2PPV-"):
            raise FetchError(Status.UNSUPPORTED, "DMM does not resolve FC2 article IDs")
        cid = request.external_id
        if request.source_url:
            from_url = cid_from_url(request.source_url)
            if not from_url:
                raise FetchError(Status.UNSUPPORTED, "expected an official DMM content URL with CID")
            if cid and cid.lower() != from_url:
                raise FetchError(Status.IDENTITY_MISMATCH, "explicit CID differs from URL CID")
            cid = from_url
        explicit = bool(cid)
        movie = None
        basis = "validated_cid_candidate"
        if cid:
            cid = cid.lower()
            if not re.fullmatch(r"[a-z0-9_]{3,80}", cid):
                raise FetchError(Status.UNSUPPORTED, "invalid DMM CID")
            if not cid_matches_number(
                cid, request.number, [*self.rules.censored_prefixes, *self.rules.uncensored_prefixes]
            ):
                raise FetchError(
                    Status.IDENTITY_MISMATCH, "CID does not encode the full requested catalog number"
                )
            movie = await self.content(cid, raw)
        else:
            cid = self.rules.dmm_cid(request.number)
            if not cid:
                raise FetchError(Status.UNSUPPORTED, "DMM needs an alphanumeric catalog or explicit CID")
            movie = await self.content(cid, raw)
            if movie is None:
                candidates = await self.search_cids(request, raw)
                candidates = [value for value in candidates if value != cid]
                if len(candidates) > 1:
                    from ..models import Candidate

                    return SourceResult(
                        source=self.id,
                        requested_number=request.number,
                        status=Status.AMBIGUOUS,
                        candidates=[
                            Candidate(
                                number=request.number,
                                external_id=value,
                                url=f"https://video.dmm.co.jp/av/content/?id={value}",
                            )
                            for value in candidates
                        ],
                        message="multiple official DMM CID candidates; select a content URL",
                    )
                if candidates:
                    cid = candidates[0]
                    movie = await self.content(cid, raw)
                    basis = "official_search_cid"
        if movie is None:
            return self.missed(request)
        classification = self.rules.classify(request.number)
        # Adult PPV identifies the catalog, but this response has no censorship field.
        # Preserve maintained number hints rather than inventing native censorship.
        classification.evidence.append(
            Evidence(basis="official_adult_ppv_content", value=cid, source=self.id)
        )
        classification.evidence.append(
            Evidence(basis=basis if not explicit else "explicit_verified_cid", value=cid, source=self.id)
        )
        package = movie.get("packageImage") or {}
        source_url = f"https://video.dmm.co.jp/av/content/?id={cid}"

        def names(key: str) -> list[str]:
            value = movie.get(key) or []
            value = [value] if isinstance(value, dict) else value
            return [item["name"] for item in value if isinstance(item, dict) and item.get("name")]

        metadata = Metadata(
            number=request.number,
            title=movie["title"],
            original_title=movie["title"],
            plot=movie.get("description") or None,
            release_date=parse_date(movie.get("deliveryStartDate")),
            release_kind="delivery",
            runtime_minutes=int(movie["duration"]) // 60 if movie.get("duration") else None,
            studio=next(iter(names("maker")), None),
            label=next(iter(names("label")), None),
            series=next(iter(names("series")), None),
            directors=names("directors"),
            actors=[Actor(name=name, gender="female") for name in names("actresses")],
            tags=names("genres"),
            artwork=artwork(
                self.id,
                source_url,
                [package.get("largeUrl") or package.get("mediumUrl")] if package else [],
                [
                    item["largeImageUrl"] if item.get("largeImageUrl") else item["imageUrl"]
                    for item in movie.get("sampleImages") or []
                    if item.get("largeImageUrl") or item.get("imageUrl")
                ],
            ),
            classification=classification,
            external_id=cid,
            source_url=source_url,
        )
        return self.found(request, metadata, source_data=movie)
