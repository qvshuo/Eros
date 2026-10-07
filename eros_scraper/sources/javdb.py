from __future__ import annotations

from urllib.parse import urlencode, urljoin

from ..models import Actor, Candidate, Metadata, RawDocument, ScrapeRequest, SourceId, SourceResult, Status
from ..numbers import normalize_number
from ..parsing import artwork, first, minutes, parse_date, text, texts
from ..transport import FetchError, Page
from .base import Source


class JavDBWeb(Source):
    id = SourceId.JAVDB
    default_url = "https://javdb.com"

    async def _fetch(self, request: ScrapeRequest, raw: list[RawDocument]) -> SourceResult:
        if request.source_url:
            url = self.checked_url(request.source_url)
        elif request.external_id:
            if not request.external_id.isalnum():
                raise FetchError(Status.UNSUPPORTED, "invalid JavDB movie ID")
            url = self.base + "/v/" + request.external_id
        else:
            search = await self.client.get(
                self.id,
                self.base
                + "/search?"
                + urlencode({"q": self.rules.query_number(request.number), "f": "all", "locale": "zh"}),
            )
            raw.append(search.snapshot())
            selector = search.selector()
            empty = selector.css(".empty-message")
            if any(
                str(node.get_all_text()).strip() in ("暫無內容", "暂无内容", "No results", "No results found")
                for node in empty
            ):
                return self.missed(request)
            if not selector.css(".movie-list"):
                raise FetchError(Status.PARSE_ERROR, "JavDB search container missing")
            candidates = [
                Candidate(
                    number=code,
                    title=text(node, './/div[contains(@class,"video-title")]//text()'),
                    url=urljoin(self.base, node.attrib["href"]),
                )
                for node in selector.css("a.box[href]")
                if (code := text(node, './/div[contains(@class,"video-title")]/strong/text()'))
            ]
            selected = self.select(request, candidates)
            if isinstance(selected, SourceResult):
                return selected
            if not selected:
                return self.missed(request)
            url = self.checked_url(selected.url)
        page = await self.client.get(self.id, url + ("&" if "?" in url else "?") + "locale=zh")
        raw.append(page.snapshot())
        return self.parse(request, page)

    def parse(self, request: ScrapeRequest, page: Page) -> SourceResult:
        selector = page.selector()

        def row(label: str):
            return selector.xpath(
                f'//nav[contains(@class,"movie-panel-info")]//div[contains(@class,"panel-block")][strong[contains(text(),"{label}")]]/span[contains(@class,"value")]'
            )

        number = first(selector, '//a[contains(@class,"copy-to-clipboard")]/@data-clipboard-text')
        original = text(selector, '//h2//span[contains(@class,"origin-title")]//text()')
        title = original or text(selector, '//h2//strong[contains(@class,"current-title")]//text()')
        if not number or not title or not selector.css("nav.movie-panel-info"):
            raise FetchError(Status.PARSE_ERROR, "JavDB detail markers missing")

        def value(label: str) -> str | None:
            nodes = row(label)
            return str(nodes[0].get_all_text()).strip() if nodes else None

        actors = []
        nodes = row("演員")
        if nodes:
            for node in nodes[0].css('a[href*="/actors/"]'):
                actors.append(
                    Actor(
                        name=str(node.get_all_text()).strip(),
                        gender="female" if "actor-female" in node.attrib.get("class", "") else "unknown",
                        source_id=node.attrib["href"].rsplit("/", 1)[-1],
                    )
                )
        canonical = normalize_number(number)
        metadata = Metadata(
            number=canonical,
            title=title,
            original_title=original,
            release_date=parse_date(value("日期")),
            release_kind="release",
            runtime_minutes=minutes(value("時長")),
            studio=value("片商"),
            publisher=value("發行"),
            series=value("系列"),
            directors=[value("導演")] if value("導演") else [],
            actors=actors,
            tags=texts(
                selector, '//nav[contains(@class,"movie-panel-info")]//a[contains(@href,"/tags/")]/text()'
            ),
            artwork=artwork(
                self.id,
                page.url,
                texts(selector, '//img[contains(@class,"video-cover")]/@src'),
                texts(
                    selector, '//*[contains(@class,"preview-images")]//a[contains(@class,"tile-item")]/@href'
                ),
            ),
            classification=self.rules.classify(canonical),
            external_id=page.url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1],
            source_url=page.url,
        )
        return self.found(request, metadata)
