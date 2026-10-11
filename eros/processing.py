import logging
from pathlib import Path

from .actors import ActorAliases, comparison_name
from .engine import FIELDS, Scraper, title_identity
from .images import save_images
from .logging import report_progress
from .models import Metadata, ScrapeRequest, Status
from .nfo import (
    existing_nfo,
    media_stems,
    read_nfo,
    serialize_nfo,
    target_folder,
    write_nfo_files,
)
from .scanner import Work
from .translation import Translator

logger = logging.getLogger(__name__)


def resolve_actor_names(metadata: Metadata, names: ActorAliases) -> list[dict]:
    actors, issues, seen = [], [], set()
    for actor in metadata.actors:
        primary = names.resolve(actor.name) if actor.gender != "male" else None
        if primary:
            actor = actor.model_copy(update={"name": primary})
        elif actor.gender != "male":
            candidates = names.ambiguous.get(comparison_name(actor.name), [])
            if candidates:
                issues.append(
                    {
                        "name": actor.name,
                        "status": "ambiguous",
                        "candidates": candidates,
                    }
                )
                logger.warning(
                    "演员名称存在歧义，保留来源名字：%s；候选日文主名：%s",
                    actor.name,
                    "、".join(candidates),
                )
        if actor.name not in seen:
            actors.append(actor)
            seen.add(actor.name)
    metadata.actors = actors
    return issues


async def process_work(
    scraper: Scraper,
    work: Work,
    output: Path,
    names: ActorAliases,
    translator: Translator,
    *,
    source=None,
    source_url=None,
    external_id=None,
    enrich=True,
    images=True,
    missing_only=False,
    portraits=None,
):
    result = await scraper.scrape(
        ScrapeRequest(
            code=work.code,
            source=source,
            source_url=source_url,
            external_id=external_id,
            enrich=enrich,
        )
    )
    report = {
        "code": work.code,
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
    target = target_folder(output, work.code)
    metadata = result.metadata
    previous = None
    path = existing_nfo(output, work)
    if path.is_file():
        try:
            previous = read_nfo(path, work.code)
        except ValueError:
            pass
    same_movie = previous is not None and title_identity(
        previous.original_title or previous.title, previous.code
    ) == title_identity(metadata.original_title or metadata.title, metadata.code)
    if same_movie:
        for field in FIELDS:
            if (missing_only or not getattr(metadata, field)) and getattr(previous, field):
                setattr(metadata, field, getattr(previous, field))
    if not metadata.original_title:
        metadata.original_title = metadata.title
    report["actor_issues"] = resolve_actor_names(metadata, names)
    report_progress("translation" if translator.settings.enabled else "metadata")
    report["translation_errors"] = await translator.metadata(metadata)
    if same_movie:
        # A translation outage must not replace a verified previous translation.
        for error in report["translation_errors"]:
            old_value = getattr(previous, error["field"])
            if old_value:
                setattr(metadata, error["field"], old_value)
    content = serialize_nfo(metadata)
    report_progress("metadata")
    write_nfo_files(output, work, content)
    report["images"] = (
        await save_images(
            scraper.client,
            metadata.artwork,
            target,
            media_stems(work),
            missing_only=missing_only,
        )
        if images
        else []
    )
    if portraits is not None:
        report["portraits"] = await portraits.save(metadata.actors)
    return report
