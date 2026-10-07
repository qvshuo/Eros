from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from ..models import Actor, Candidate, Metadata, RawDocument, ScrapeRequest, SourceId, SourceResult, Status
from ..numbers import normalize_number
from ..parsing import artwork, minutes, parse_date, text, texts
from ..transport import FetchError, Page
from .base import Source


class AVSOX(Source):
    id = SourceId.AVSOX
    default_url = "https://avsox.click"

    async def _fetch(self, request: ScrapeRequest, raw: list[RawDocument]) -> SourceResult:
        if request.source_url:
            url = self.checked_url(request.source_url)
        elif request.external_id:
            if not request.external_id.isalnum():
                raise FetchError(Status.UNSUPPORTED, "invalid AVSOX movie ID")
            url = self.base + "/cn/movies/" + request.external_id
        else:
            search = await self.document(
                self.base + "/cn/search/" + quote(self.rules.query_number(request.number)), raw, "search"
            )
            candidates = []
            for node in search.selector().css('a[href*="/movies/"]'):
                title = str(node.get_all_text()).strip()
                code = text(node, './/*[contains(@class,"movie-meta")]/span[1]/text()')
                if code:
                    candidates.append(
                        Candidate(number=code, title=title, url=urljoin(self.base, node.attrib["href"]))
                    )
            selected = self.select(request, candidates)
            if isinstance(selected, SourceResult):
                return selected
            if not selected:
                if self.empty(search):
                    return self.missed(request)
                raise FetchError(
                    Status.PARSE_ERROR,
                    "AVSOX search exposes neither exact results nor an explicit empty state",
                )
            url = self.checked_url(selected.url)
        page = await self.document(url, raw, "detail")
        return self.parse(request, page)

    async def document(self, url: str, raw: list[RawDocument], mode: str) -> Page:
        try:
            page = await self.client.get(self.id, url)
            raw.append(page.snapshot())
            selector = page.selector()
            if (
                selector.css(".detail-row")
                if mode == "detail"
                else selector.css('a[href*="/movies/"]') or self.empty(page)
            ):
                return page
        except FetchError as exc:
            if exc.status not in (Status.NETWORK_ERROR, Status.BLOCKED):
                raise
            if exc.page:
                raw.append(exc.page.snapshot())
        # Current AVSOX is a Vue SPA. The remote browser renders its JSON-driven UI;
        # request.post cannot impersonate its JSON-array API with a form body.
        page = await self.client.get(self.id, url, rendered=True)
        raw.append(page.snapshot())
        return page

    @staticmethod
    def empty(page: Page) -> bool:
        return any(
            str(node.get_all_text()).strip() in ("没有结果。", "沒有結果。", "No results.")
            for node in page.selector().css(".q-banner")
        )

    def parse(self, request: ScrapeRequest, page: Page) -> SourceResult:
        selector = page.selector()

        def row(label: str) -> str | None:
            return text(
                selector,
                f'//*[contains(@class,"detail-row")][span[contains(@class,"detail-label")][contains(text(),"{label}")]]/*[contains(@class,"detail-value")]//text()',
            )

        code = row("识别码")
        title = text(selector, "//h1/text()")
        if not code or not title:
            raise FetchError(Status.PARSE_ERROR, "AVSOX detail markers missing after rendering")
        title = re.sub(r"^" + re.escape(code) + r"\s*", "", title).strip()
        number = normalize_number(code)
        metadata = Metadata(
            number=number,
            title=title,
            original_title=title,
            release_date=parse_date(row("发行时间")),
            release_kind="release",
            runtime_minutes=minutes(row("长度")),
            studio=row("制作商"),
            publisher=row("发行商"),
            series=row("系列"),
            actors=[
                Actor(
                    name=str(node.get_all_text()).strip(),
                    gender="female",
                    source_id=node.attrib["href"].rsplit("/", 1)[-1],
                )
                for node in selector.css("a.actress-card[href]")
                if str(node.get_all_text()).strip()
            ],
            tags=texts(selector, '//a[contains(@href,"/genres/")]/text()'),
            artwork=artwork(
                self.id,
                page.url,
                texts(selector, '//img[@alt and @src][starts-with(@alt,"' + code + '")]/@src'),
                texts(selector, '//*[contains(@class,"sample")]//img/@src'),
            ),
            classification=self.rules.classify(number),
            external_id=page.url.rstrip("/").rsplit("/", 1)[-1],
            source_url=page.url,
        )
        return self.found(request, metadata)
