from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .actor_names import ActorNames
from .config import SOURCE_INTERVALS, Settings
from .directories import Directories, DirectoryRequest
from .sources import SOURCES, source_origins
from .storage import atomic_write
from .tasks import TaskRequest, TaskRunner
from .writer import nfo_paths, target_folder


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***" if key in {"api_key", "cookie", "signature_prefix"} and item else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def toml_text(data: dict) -> str:
    lines = []

    def table(value: dict, prefix: list[str]) -> None:
        if prefix:
            lines.append("[" + ".".join(json.dumps(p) for p in prefix) + "]")
        for key, item in value.items():
            if isinstance(item, dict):
                continue
            if item is None:
                if not prefix and key in {
                    "flaresolverr_url",
                    "rules_file",
                    "actor_names_file",
                    "metadata_root",
                }:
                    item = ""
                else:
                    continue
            lines.append(json.dumps(key) + " = " + json.dumps(item, ensure_ascii=False))
        lines.append("")
        for key, item in value.items():
            if isinstance(item, dict):
                table(item, [*prefix, key])

    table(data, [])
    return "\n".join(lines)


class ScanRequest(BaseModel):
    roots: list[Path] | None = None


def create_app(
    settings: Settings,
    *,
    runner_factory=TaskRunner,
    config_path: Path | None = None,
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
        if settings.media_roots and settings.metadata_root:
            app.state.runner = runner_factory(settings)
            app.state.watcher = asyncio.create_task(app.state.runner.watch())
        try:
            yield
        finally:
            if app.state.runner:
                await app.state.runner.close()
                await app.state.watcher

    app = FastAPI(title="Eros", lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "web/assets"), name="assets")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home():
        from importlib.resources import files

        return files("eros_scraper").joinpath("web/index.html").read_text(encoding="utf-8")

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

    def runner() -> TaskRunner:
        if app.state.configuring:
            raise ValueError("configuration change in progress")
        if app.state.runner is None:
            raise ValueError("configure media and metadata directories first")
        return app.state.runner

    def current_settings():
        return app.state.runner.settings if app.state.runner else app.state.settings

    def public_config():
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
        return {"status": "ok", "version": "0.2.0", "storage": "files"}

    @app.get("/api/config")
    async def config():
        return public_config()

    @app.put("/api/config")
    async def update_config(value: dict):
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
        ready = bool(updated.media_roots and updated.metadata_root)
        path = config_path or Path("/data/config.toml")
        same_output = bool(service and ready and updated.metadata_root.resolve() == service.output)
        app.state.configuring = True
        candidate = None
        try:
            if ready:
                if same_output:
                    preview = await service.scan_async(settings=updated, persist=False)
                else:
                    candidate = runner_factory(updated)
                    await candidate.scan_async()
            old_movies = service.state.movies_data if same_output else None
            scan_path = service.state.root / "scan.json" if same_output else None
            old_scan = scan_path.read_bytes() if scan_path and scan_path.is_file() else None
            scan_saved = False
            try:
                if same_output:
                    from .scanner import Movie

                    service.state.save_scan([Movie.model_validate(row) for row in preview])
                    scan_saved = True
                atomic_write(path, toml_text(updated.model_dump(mode="json")))
            except OSError, ValueError:
                if scan_saved:
                    if old_scan is not None:
                        atomic_write(scan_path, old_scan)
                    else:
                        scan_path.unlink(missing_ok=True)
                    service.state.movies_data = old_movies
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
                    from .logging_setup import configure

                    configure()
                candidate = None
        finally:
            if candidate:
                await candidate.close()
                if service:
                    from .logging_setup import configure

                    configure()
            app.state.configuring = False
        return public_config()

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
            raise ValueError("configure media_roots through /api/config before scanning")
        return await service.scan_async()

    @app.get("/api/movies")
    async def movies(status: str | None = None):
        return runner().state.movies(status) if app.state.runner else []

    @app.get("/api/movies/detail")
    async def movie_detail(path: str):
        service = runner()
        row = next((m for m in service.state.movies() if m["path"] == path), None)
        if row is None:
            raise HTTPException(404, "movie not found")
        return {**row, "last_result": service.state.results.get(row["key"])}

    @app.get("/api/movies/{key}")
    async def movie(key: str):
        value = runner().state.movie(key).model_dump(mode="json")
        value["nfo_exists"] = all(p.is_file() for p in nfo_paths(runner().output, runner().state.movie(key)))
        value["last_result"] = runner().state.results.get(key)
        return value

    @app.post("/api/tasks")
    async def create_task(request: TaskRequest):
        return runner().create(request)

    @app.get("/api/tasks")
    async def tasks():
        return runner().state.tasks() if app.state.runner else []

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str, page: int | None = None, status: str = "all", query: str = ""):
        record = runner().state.task(task_id)
        if page is None:
            return record
        if page < 1 or status not in ("all", "success", "failed", "pending", "running"):
            raise HTTPException(422, "invalid task result filter")
        items = sorted(record.pop("items"), key=lambda item: (item["status"] != "failed", item["key"]))
        items = [
            item
            for item in items
            if (status == "all" or item["status"] == status) and query.casefold() in item["key"].casefold()
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

    @app.post("/api/tasks/{task_id}/retry")
    async def retry(task_id: str):
        return runner().retry(task_id)

    @app.get("/api/conflicts")
    async def conflicts():
        return runner().state.movies("conflict")

    @app.get("/api/unknown-actors")
    async def actor_issues():
        return runner().state.actor_issues() if app.state.runner else []

    @app.get("/api/actors")
    async def actors():
        path = runner().settings.actor_names_file or runner().state.root / "actor_names.json"
        names = ActorNames.load(path if path.is_file() else None)
        return {"version": 1, "actors": names.actors, "ambiguous_aliases": names.ambiguous}

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
                raise HTTPException(422, "actor records require a name, aliases and homepages string lists")
        names = ActorNames(actors)
        path = service.settings.actor_names_file or service.state.root / "actor_names.json"
        pending = names.pending(service.state.issues)
        from .storage import write_json

        old_mapping = path.read_bytes() if path.is_file() else None
        try:
            atomic_write(path, json.dumps({"version": 1, "actors": actors}, ensure_ascii=False, indent=2))
            write_json(service.state.root / "actors-unknown.json", pending)
        except OSError, ValueError:
            if old_mapping is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old_mapping)
            raise
        service.settings.actor_names_file = path
        service.state.issues = pending
        return {"names": len(actors), "ambiguous_aliases": names.ambiguous}

    def orphan_keys():
        service = runner()
        if not (service.state.root / "scan.json").is_file():
            raise ValueError("scan the media library before listing orphan metadata")
        known = {m["key"] for m in service.state.movies()}
        return sorted(
            p.name
            for p in service.output.iterdir()
            if p.is_dir()
            and not p.is_symlink()
            and not p.name.startswith(".")
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
