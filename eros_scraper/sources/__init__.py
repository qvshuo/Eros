from urllib.parse import urlparse

from ..models import SourceId
from .avsox import AVSOX
from .dmm import DMM
from .fc2 import FC2
from .javbus import JavBus
from .javdb_source import JavDB

SOURCES = {source.id: source for source in (DMM, JavDB, JavBus, FC2, AVSOX)}


def source_origins(settings) -> dict[str, list[str]]:
    origins = {
        source.value: [
            (settings.sites[source].base_url if source in settings.sites else None) or factory.default_url
        ]
        for source, factory in SOURCES.items()
    }
    origins[SourceId.JAVDB].append(settings.javdb_api.base_url)
    origins[SourceId.DMM].extend(["https://video.dmm.co.jp", "https://www.dmm.co.jp"])
    return {
        source: sorted({value for url in urls if (value := origin(url))}) for source, urls in origins.items()
    }


def origin(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return "https://" + parsed.netloc.lower().removesuffix(":443")


def source_for_url(url: str, settings) -> SourceId | None:
    matches = [
        source for source, urls in source_origins(settings).items() if origin(url) in urls and origin(url)
    ]
    return SourceId(matches[0]) if len(matches) == 1 else None
