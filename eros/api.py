from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shutil
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import __version__
from .actors import ActorAliases, comparison_name
from .directories import Directories, DirectoryRequest
from .images import actor_image, crop_poster
from .models import Metadata
from .nfo import (
    existing_nfo,
    media_stems,
    nfo_paths,
    read_nfo,
    serialize_nfo,
    target_folder,
    write_nfo_files,
)
from .processing import resolve_actor_names
from .settings import SOURCE_INTERVALS, Settings
from .sources import SOURCES, source_origins
from .storage import atomic_write
from .tasks import TaskQueue, TaskRequest


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***" if key in {"api_key", "cookie", "signature_prefix"} and item else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class ScanRequest(BaseModel):
    roots: list[Path] | None = None


class CropRequest(BaseModel):
    file: str
    box: tuple[int, int, int, int] | None = None
    center: bool = False
    fingerprint: str | None = None


def create_app(
    settings: Settings,
    *,
    runner_factory=TaskQueue,
    settings_path: Path | None = None,
    directories: Directories | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.configuring = False
        app.state.runner = None
        app.state.watcher = None
        if directories:
            directories.validate(settings)
        if settings.media_directories and settings.metadata_directory:
            app.state.runner = runner_factory(settings)
            app.state.watcher = asyncio.create_task(app.state.runner.watch())
        try:
            yield
        finally:
            if app.state.runner:
                await app.state.runner.close()
                await app.state.watcher

    app = FastAPI(title="Eros", lifespan=lifespan)
    app.mount(
        "/assets",
        StaticFiles(directory=Path(__file__).parent / "web/assets"),
        name="assets",
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home():
        from importlib.resources import files

        return files("eros").joinpath("web/index.html").read_text(encoding="utf-8")

    @app.exception_handler(ValidationError)
    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: item[key] for key in ("loc", "msg", "type") if key in item} for item in exc.errors()
                ]
            },
        )

    def runner() -> TaskQueue:
        if app.state.configuring:
            raise ValueError("configuration change in progress")
        if app.state.runner is None:
            raise ValueError("configure media and metadata directories first")
        return app.state.runner

    def current_settings():
        return app.state.runner.settings if app.state.runner else app.state.settings

    def public_settings():
        return {
            **redact(current_settings().model_dump(mode="json")),
            "configured": app.state.runner is not None,
            "source_origins": source_origins(current_settings()),
            "source_defaults": {source.value: factory.default_url for source, factory in SOURCES.items()},
            "source_intervals": {source.value: interval for source, interval in SOURCE_INTERVALS.items()},
        }

    @app.exception_handler(OSError)
    async def filesystem_error(_request, _exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": "filesystem access failed"})

    @app.exception_handler(sqlite3.Error)
    async def database_error(_request, _exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"detail": "runtime storage unavailable"})

    @app.exception_handler(ValueError)
    async def invalid_request(_request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def not_found(_request, _exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": "not found"})

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__, "storage": "sqlite"}

    @app.get("/api/settings")
    async def read_settings():
        return public_settings()

    @app.put("/api/settings")
    async def save_settings(value: dict):
        if app.state.configuring:
            raise ValueError("configuration change in progress")
        service = app.state.runner
        if service and service.active():
            raise ValueError("cannot change configuration while tasks are active")

        def merge(old, new):
            if new == "***":
                return old
            if isinstance(old, dict) and isinstance(new, dict):
                return {**old, **{k: merge(old.get(k), v) for k, v in new.items()}}
            return new

        previous = current_settings()
        updated = Settings.model_validate(merge(previous.model_dump(mode="json"), value))
        if directories:
            directories.validate(updated)
        ready = bool(updated.media_directories and updated.metadata_directory)
        path = settings_path or Path("/data/settings.json")
        same_output = bool(service and ready and updated.metadata_directory.resolve() == service.output)
        if same_output:
            updated.actor_aliases_file = previous.actor_aliases_file
            updated.catalog_rules_file = previous.catalog_rules_file
        app.state.configuring = True
        candidate = None
        try:
            if ready:
                if same_output:
                    preview = await service.scan_async(settings=updated, persist=False)
                else:
                    candidate = runner_factory(updated)
                    await candidate.scan_async()
            previous_catalog = service.runtime.works_data if same_output else None
            had_scan = service.runtime.has_scan() if same_output else False
            scan_saved = False
            try:
                if same_output:
                    from .scanner import Work

                    service.runtime.save_scan([Work.model_validate(row) for row in preview])
                    scan_saved = True
                atomic_write(path, updated.model_dump_json(indent=2))
            except OSError, ValueError:
                if scan_saved:
                    if had_scan:
                        service.runtime.save_catalog(previous_catalog)
                    else:
                        service.runtime.clear_scan()
                raise
            if same_output:
                from .translation import Translator

                old_translator = service.translator
                service.settings = updated
                service.translator = Translator(updated.translation)
                app.state.settings = updated
                await old_translator.close()
            else:
                if service:
                    await service.close()
                    await app.state.watcher
                app.state.runner = candidate
                app.state.settings = updated
                app.state.watcher = asyncio.create_task(candidate.watch()) if candidate else None
                if candidate:
                    from .logging import configure

                    configure()
                candidate = None
        finally:
            if candidate:
                await candidate.close()
                if service:
                    from .logging import configure

                    configure()
            app.state.configuring = False
        return public_settings()

    @app.get("/api/directories")
    async def directory_list(kind: str = "media", path: Path | None = None):
        if directories is None:
            raise ValueError("directory browser is not configured")
        return directories.list(kind, path)

    @app.post("/api/directories")
    async def directory_create(value: DirectoryRequest):
        if directories is None:
            raise ValueError("directory browser is not configured")
        return directories.create(value)

    @app.post("/api/scan")
    async def scan(request: ScanRequest | None = None):
        service = runner()
        if request and request.roots:
            raise ValueError("configure media_directories through /api/settings before scanning")
        return await service.scan_async()

    @app.get("/api/works")
    async def works(status: str | None = None):
        return runner().runtime.works(status) if app.state.runner else []

    @app.get("/api/works/detail")
    async def work_details(path: str):
        service = runner()
        row = next((m for m in service.runtime.works() if m["path"] == path), None)
        if row is None:
            raise HTTPException(404, "work not found")
        metadata, error, artwork = None, None, []
        editable = row["status"] not in ("invalid_key", "conflict")
        if editable:
            work = service.runtime.work(row["code"])
            nfo = existing_nfo(service.output, work)
            if nfo.is_file():
                try:
                    metadata = read_nfo(nfo, work.code).model_dump(mode="json")
                except ValueError as exc:
                    error = str(exc)
            folder = target_folder(service.output, work.code)
            if folder.is_dir():
                artwork = [p.name for p in folder.iterdir() if image_file(work, p)]
        history = [
            {
                "task_id": task["id"],
                "created_at": task["created_at"],
                "status": item["status"],
                "stage": item.get("stage"),
                "events": item.get("events", []),
            }
            for task in service.runtime.task_data.values()
            for item in task["items"]
            if item["code"] == row["code"]
        ]
        return {
            **row,
            "metadata": metadata,
            "preview": service.previews.get(row["code"]),
            "artwork": sorted(artwork),
            "editable": editable,
            "history": sorted(history, key=lambda item: item["created_at"], reverse=True),
            "metadata_error": error,
            "last_result": service.runtime.results.get(row["code"]),
        }

    def image_file(work, path):
        return (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            and any(path.stem.startswith(stem + "-") for stem in media_stems(work))
        )

    def artwork_file(service, key, file):
        work = service.runtime.work(key)
        folder = target_folder(service.output, key)
        path = folder / file
        if Path(file).name != file or not image_file(work, path):
            raise HTTPException(404, "image not found")
        return work, path

    @app.put("/api/works/{key}/metadata")
    async def save_metadata(key: str, value: dict):
        service = runner()
        fields = {
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
        }
        if set(value) - fields:
            raise HTTPException(422, "unknown or read-only metadata field")
        async with service.task_lock:
            if service.active():
                raise ValueError("cannot edit metadata while tasks are active")
            work = service.runtime.work(key)
            path = existing_nfo(service.output, work)
            previous = {"code": key, "title": key, "source_url": ""}
            if path.is_file():
                try:
                    previous = read_nfo(path, key).model_dump()
                except ValueError:
                    pass
            metadata = Metadata.model_validate({**previous, **value})
            if not metadata.title.strip():
                raise HTTPException(422, "title must not be empty")
            translation_errors = []
            for field in ("title", "plot"):
                text = getattr(metadata, field)
                if field in value and text and text != previous.get(field):
                    try:
                        setattr(metadata, field, await service.translator.text(text))
                    except ValueError as exc:
                        translation_errors.append({"field": field, "error": str(exc)})
            if service.active():
                raise ValueError("cannot edit metadata while tasks are active")
            issues = resolve_actor_names(metadata, ActorAliases.load(service.settings.actor_aliases_file))
            write_nfo_files(service.output, work, serialize_nfo(metadata))
            report = {
                **service.runtime.results.get(key, {}),
                "status": "found",
                "actor_issues": issues,
                "translation_errors": translation_errors,
            }
            report.pop("error", None)
            service.runtime.record_result(key, report)
        await service.scan_async()
        return await work_details(str(work.path))

    @app.get("/api/works/{key}/artwork")
    async def artwork(key: str, file: str):
        _, path = artwork_file(runner(), key, file)
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    async def cropped_image(service, key, request):
        work, path = artwork_file(service, key, request.file)
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("artwork exceeds 32 MiB")
        data = path.read_bytes()
        fingerprint = hashlib.sha256(data).hexdigest()
        if request.fingerprint and request.fingerprint != fingerprint:
            raise HTTPException(409, "source image changed; preview again")
        result = await asyncio.to_thread(crop_poster, data, request.box, request.center)
        return work, data, fingerprint, result

    @app.post("/api/works/{key}/crop")
    async def preview_crop(key: str, request: CropRequest):
        _, _, fingerprint, result = await cropped_image(runner(), key, request)
        return {
            **{name: result[name] for name in ("box", "faces", "width", "height")},
            "fingerprint": fingerprint,
            "preview": "data:image/png;base64," + base64.b64encode(result["preview"]).decode(),
        }

    @app.put("/api/works/{key}/crop")
    async def save_crop(key: str, request: CropRequest):
        service = runner()
        async with service.task_lock:
            if service.active():
                raise ValueError("cannot edit metadata while tasks are active")
            work, data, _, result = await cropped_image(service, key, request)
            if service.active():
                raise ValueError("cannot edit metadata while tasks are active")
            folder = target_folder(service.output, key)
            for stem in media_stems(work):
                # Fanart keeps the original if this work only had a poster.
                if not any(p.stem == stem + "-fanart" for p in folder.iterdir()):
                    atomic_write(folder / (stem + "-fanart" + Path(request.file).suffix), data)
                poster = folder / (stem + "-poster" + result["extension"])
                atomic_write(poster, result["content"])
                for old in list(folder.iterdir()):
                    if old.stem == stem + "-poster" and old != poster and image_file(work, old):
                        old.unlink()
            report = {**service.runtime.results.get(key, {})}
            if report:
                report["images"] = [
                    item
                    for item in report.get("images", [])
                    if not (item.get("status") == "error" and item.get("file") == "poster")
                ]
                service.runtime.record_result(key, report)
        await service.scan_async()
        return await work_details(str(work.path))

    @app.get("/api/works/{key}")
    async def work(key: str):
        value = runner().runtime.work(key).model_dump(mode="json")
        value["nfo_exists"] = all(p.is_file() for p in nfo_paths(runner().output, runner().runtime.work(key)))
        value["last_result"] = runner().runtime.results.get(key)
        return value

    @app.post("/api/tasks")
    async def create_task(request: TaskRequest):
        return runner().create(request)

    @app.get("/api/tasks")
    async def tasks():
        return runner().runtime.tasks() if app.state.runner else []

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str, page: int | None = None, status: str = "all", query: str = ""):
        record = runner().runtime.task(task_id)
        if page is None:
            return record
        if page < 1 or status not in (
            "all",
            "success",
            "failed",
            "pending",
            "running",
            "cancelled",
            "skipped",
        ):
            raise HTTPException(422, "invalid task result filter")
        items = sorted(
            record.pop("items"),
            key=lambda item: (item["status"] != "failed", item["code"]),
        )
        items = [
            item
            for item in items
            if (status == "all" or item["status"] == status) and query.casefold() in item["code"].casefold()
        ]
        pages = max(1, (len(items) + 99) // 100)
        page = min(page, pages)
        return {
            **record,
            "items": items[(page - 1) * 100 : page * 100],
            "page": page,
            "pages": pages,
            "matched": len(items),
        }

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str):
        return runner().delete(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        return await runner().cancel(task_id)

    @app.post("/api/tasks/{task_id}/retry")
    async def retry(task_id: str):
        return runner().retry(task_id)

    @app.get("/api/conflicts")
    async def conflicts():
        return runner().runtime.works("conflict")

    @app.get("/api/actors/library")
    async def actor_library(query: str = ""):
        if not app.state.runner:
            return []
        service = runner()

        def collect():
            names = ActorAliases.load(service.settings.actor_aliases_file)
            rows = {}
            for record in service.runtime.works():
                if record["status"] in ("invalid_key", "conflict"):
                    continue
                work = service.runtime.work(record["code"])
                try:
                    metadata = read_nfo(existing_nfo(service.output, work), work.code)
                except (ValueError, OSError):
                    continue
                for actor in metadata.actors:
                    resolved = names.resolve(actor.name)
                    primary = actor.name
                    if primary not in rows:
                        candidates = names.ambiguous.get(comparison_name(actor.name), [])
                        try:
                            portrait = actor_image(service.output, primary) is not None
                        except ValueError:
                            portrait = False
                        rows[primary] = {
                            "name": primary,
                            "actors": [primary],
                            "portrait_name": primary,
                            "codes": [],
                            "count": 0,
                            "portrait": portrait,
                            "status": "ambiguous" if candidates else "known" if resolved else "original",
                            "portrait_status": "ready" if portrait else "missing",
                            "candidates": candidates,
                        }
                    row = rows[primary]
                    if work.code not in row["codes"]:
                        row["codes"].append(work.code)
                        row["count"] += 1
            if query.strip():
                needle = comparison_name(query)
                matched = {}
                for primary, record in names.actors.items():
                    if any(needle in comparison_name(value) for value in [primary, *record["aliases"]]):
                        existing = rows.get(primary)
                        portrait = actor_image(service.output, primary) is not None
                        matched[primary] = existing or {
                            "name": primary,
                            "actors": [],
                            "portrait_name": primary,
                            "codes": [],
                            "count": 0,
                            "portrait": portrait,
                            "portrait_status": "ready" if portrait else "missing",
                            "status": "known",
                            "candidates": [],
                        }
                for name, row in rows.items():
                    primary = names.resolve(name) or name
                    if comparison_name(name) in names.ambiguous:
                        continue
                    if primary not in matched and needle not in comparison_name(name):
                        continue
                    if primary not in matched:
                        matched[primary] = {**row, "name": primary}
                        continue
                    target = matched[primary]
                    if not target["actors"]:
                        target.update(portrait=False, portrait_status="missing", portrait_name=name)
                    target["codes"] = sorted(set(target["codes"]) | set(row["codes"]))
                    target["count"] = len(target["codes"])
                    if name not in target["actors"]:
                        target["actors"].append(name)
                    if row["portrait"]:
                        target.update(portrait=True, portrait_status="ready", portrait_name=name)
                rows = matched
            return sorted(
                rows.values(), key=lambda row: (row["status"] == "known", row["portrait"], row["name"])
            )

        return await asyncio.to_thread(collect)

    @app.get("/api/actors/{name}/portrait")
    async def actor_portrait(name: str):
        path = actor_image(runner().output, name)
        if path is None:
            raise HTTPException(404, "portrait not found")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.get("/api/actors")
    async def actors():
        path = runner().settings.actor_aliases_file or runner().runtime.root / "actor_aliases.json"
        names = ActorAliases.load(path if path.is_file() else None)
        return {
            "version": 1,
            "actors": names.actors,
            "ambiguous_aliases": names.ambiguous,
        }

    @app.put("/api/actors")
    async def update_actors(value: dict):
        service = runner()
        if service.active():
            raise ValueError("cannot update actor mapping while tasks are active")
        actors = value.get("actors")
        if value.get("version") != 1 or not isinstance(actors, dict):
            raise HTTPException(422, "expected version=1 and an actors object")
        for name, record in actors.items():
            if (
                not name.strip()
                or not isinstance(record, dict)
                or any(
                    not isinstance(record.get(field), list)
                    or any(not isinstance(s, str) for s in record[field])
                    for field in ("aliases", "homepages")
                )
            ):
                raise HTTPException(
                    422,
                    "actor records require a name, aliases and homepages string lists",
                )
        names = ActorAliases(actors)
        path = service.settings.actor_aliases_file or service.runtime.root / "actor_aliases.json"
        pending = names.pending(service.runtime.issues)
        old_mapping = path.read_bytes() if path.is_file() else None
        try:
            atomic_write(
                path,
                json.dumps({"version": 1, "actors": actors}, ensure_ascii=False, indent=2),
            )
            service.runtime.save_issues(pending)
        except OSError, ValueError, sqlite3.Error:
            if old_mapping is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old_mapping)
            raise
        service.settings.actor_aliases_file = path
        return {"names": len(actors), "ambiguous_aliases": names.ambiguous}

    def orphan_keys():
        service = runner()
        if not service.runtime.has_scan():
            raise ValueError("scan the media library before listing orphan metadata")
        known = {m["code"] for m in service.runtime.works()}
        return sorted(
            p.name
            for p in service.output.iterdir()
            if p.is_dir()
            and not p.is_symlink()
            and not p.name.startswith(".")
            and p.name != "actors"
            and p.name not in known
            and any(p.glob("*.nfo"))
        )

    @app.get("/api/orphans")
    async def orphans():
        return orphan_keys()

    @app.delete("/api/orphans/{key}")
    async def delete_orphan(key: str):
        if runner().active():
            raise ValueError("cannot delete metadata while tasks are active")
        if key not in orphan_keys():
            raise HTTPException(404, "orphan metadata not found")
        shutil.rmtree(target_folder(runner().output, key))
        return {"deleted": key}

    return app
