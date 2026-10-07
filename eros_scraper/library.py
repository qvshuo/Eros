import logging
from pathlib import Path

from .actor_names import ActorNames, comparison_name
from .engine import FIELDS, Scraper, title_key
from .images import save_images
from .models import Metadata, ScrapeRequest, Status
from .scanner import Movie
from .translation import Translator
from .writer import existing_nfo, media_stems, nfo_bytes, read_nfo, target_folder, write_nfos

logger = logging.getLogger(__name__)


def normalize_actors(metadata: Metadata, names: ActorNames) -> list[dict]:
    actors, issues, seen = [], [], set()
    for actor in metadata.actors:
        primary = names.resolve(actor.name)
        if primary:
            actor = actor.model_copy(update={"name": primary})
        else:
            candidates = names.ambiguous.get(comparison_name(actor.name), [])
            issues.append(
                {
                    "name": actor.name,
                    "status": "ambiguous" if candidates else "unknown",
                    "candidates": candidates,
                }
            )
            if candidates:
                logger.warning(
                    "演员名称存在歧义，保留来源名字：%s；候选日文主名：%s", actor.name, "、".join(candidates)
                )
            else:
                logger.warning("演员名称未收录，保留来源名字：%s", actor.name)
        if actor.name not in seen:
            actors.append(actor)
            seen.add(actor.name)
    metadata.actors = actors
    return issues


async def scrape_movie(
    scraper: Scraper,
    movie: Movie,
    output: Path,
    names: ActorNames,
    translator: Translator,
    *,
    source=None,
    source_url=None,
    external_id=None,
    enrich=True,
    images=True,
    missing_only=False,
):
    result = await scraper.scrape(
        ScrapeRequest(
            number=movie.key,
            source=source,
            source_url=source_url,
            external_id=external_id,
            enrich=enrich,
        )
    )
    report = {
        "key": movie.key,
        "status": result.status.value,
        "category": result.classification.category,
        "warnings": result.warnings,
        "sources": [
            {
                "source": r.source,
                "status": r.status,
                "message": r.message,
                "candidates": [c.model_dump() for c in r.candidates],
            }
            for r in result.results
        ],
    }
    if result.status != Status.FOUND or result.metadata is None:
        return report
    target = target_folder(output, movie.key)
    metadata = result.metadata
    previous = None
    path = existing_nfo(output, movie)
    if path.is_file():
        try:
            previous = read_nfo(path, movie.key)
        except ValueError:
            pass
    same_movie = previous is not None and title_key(
        previous.original_title or previous.title, previous.number
    ) == title_key(metadata.original_title or metadata.title, metadata.number)
    if same_movie:
        for field in FIELDS:
            if (missing_only or not getattr(metadata, field)) and getattr(previous, field):
                setattr(metadata, field, getattr(previous, field))
    if not metadata.original_title:
        metadata.original_title = metadata.title
    report["actor_issues"] = normalize_actors(metadata, names)
    report["translation_errors"] = await translator.metadata(metadata)
    if same_movie:
        # A translation outage must not replace a verified previous translation.
        for error in report["translation_errors"]:
            old_value = getattr(previous, error["field"])
            if old_value:
                setattr(metadata, error["field"], old_value)
    content = nfo_bytes(metadata)
    write_nfos(output, movie, content)
    report["images"] = (
        await save_images(
            scraper.client,
            metadata.artwork,
            target,
            media_stems(movie),
            missing_only=missing_only,
        )
        if images
        else []
    )
    return report
